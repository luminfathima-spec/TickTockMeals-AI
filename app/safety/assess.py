"""Grounded safety assessment engine for TickTock Meals.

Combines deterministic FSSAI/WHO rules with RAG retrieval over food safety guidance.
Guarantees that the LLM can only make decisions safer, never upgrade an unsafe verdict.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field

from app.clock import now as get_clock_now
from app.llm.client import LLMClient
from app.models import DonorForm, FoodPassport, StorageCondition
from app.safety.rag import retrieve
from app.safety.rules import RiskLevel, evaluate_rules


class Citation(BaseModel):
    """Reference to an authoritative food safety guideline chunk."""
    id: str = Field(..., description="Unique chunk identifier (e.g. 'cooked_rice_safety_1')")
    source: str = Field(..., description="Document title and regulatory section")


class SafetyVerdict(BaseModel):
    """Comprehensive grounded safety assessment for surplus food."""
    safe_until: datetime = Field(..., description="Calculated safe-until timestamp")
    minutes_left: int = Field(..., description="Minutes remaining until safe_until expires")
    risk_level: RiskLevel = Field(..., description="Aggregated risk level: LOW, MEDIUM, or HIGH")
    human_ok: bool = Field(..., description="True only if food is cleared for human consumption")
    reasons: List[str] = Field(default_factory=list, description="List of deterministic and advisory reasons")
    citations: List[Citation] = Field(default_factory=list, description="Citations to authoritative safety guidance")
    requires_human_check: bool = Field(..., description="Mandatory physical volunteer inspection flag")
    disclaimer: str = Field(
        default="Advisory only. Mandatory human volunteer verification required before distribution.",
        description="Fixed safety and legal disclaimer",
    )


class LLMSafetyOutput(BaseModel):
    """Structured response schema requested from Gemini."""
    llm_risk_level: RiskLevel = Field(default=RiskLevel.LOW)
    explanation: str = Field(..., description="2 to 4 sentence explanation citing chunk IDs")
    extra_caution_flags: List[str] = Field(default_factory=list)
    cited_chunk_ids: List[str] = Field(default_factory=list)


SAFETY_PROMPT_TEMPLATE = """You are a food safety evaluation expert working for a surplus rescue platform.
Analyze this Food Passport against the retrieved official food safety guidelines.

FOOD PASSPORT:
- Dishes: {dishes}
- Storage Condition: {storage}
- Cooked Time: {cooked_at}
- Container: {container}
- Deterministic Rule Verdict: {rules_risk}
- Safe Until: {safe_until} ({minutes_left} minutes remaining)
- Rule Reasons: {rule_reasons}

RETRIEVED GUIDELINES:
{guidelines}

CRITICAL RULES:
1. Explain the food safety factors in 2 to 4 sentences, explicitly citing chunk IDs (e.g., [cooked_rice_safety_1]).
2. You may suggest EXTRA caution flags or UPGRADE risk to a safer level (MEDIUM or HIGH).
3. You can NEVER upgrade food to safe if deterministic rules marked it unsafe or expired.
4. Output JSON strictly adhering to LLMSafetyOutput schema.
"""


def assess_safety(
    passport: FoodPassport,
    reference_time: Optional[datetime] = None,
    client: Optional[LLMClient] = None,
    mode: Optional[str] = None,
) -> SafetyVerdict:
    """Assess surplus food safety by running deterministic rules first, followed by RAG synthesis."""
    now = reference_time or get_clock_now()
    llm = client or LLMClient()
    active_mode = mode or llm.mode

    # 1. Deterministic Guardrails
    (
        rules_risk,
        rule_reasons,
        safe_until,
        human_ok,
        requires_human_check,
        base_hours,
    ) = evaluate_rules(passport, reference_time=now)

    minutes_left = int((safe_until - now).total_seconds() / 60.0)

    # 2. RAG Retrieval over Knowledge Base
    # Build query from dishes and storage
    dish_keywords = " ".join([d.name for d in passport.dishes] + [d.category.value for d in passport.dishes])
    query = f"{dish_keywords} storage {passport.handling.storage.value}"
    chunks = retrieve(query, k=4, client=llm)

    citations: List[Citation] = []
    chunk_text_block = ""
    for c in chunks:
        citations.append(Citation(id=c["id"], source=c["source"]))
        chunk_text_block += f"- [{c['id']}] (Source: {c['source']}):\n{c['text'][:300]}...\n\n"

    # 3. LLM Explanation / Advisory Synthesis
    reasons = list(rule_reasons)
    llm_risk = rules_risk
    extra_flags: List[str] = []

    if active_mode == "mock":
        # Deterministic mock response
        citation_ids = ", ".join([f"[{c.id}]" for c in citations[:2]]) if citations else "[rules only]"
        explanation = (
            f"Evaluated surplus with {len(passport.dishes)} dish(es) under {passport.handling.storage.value} conditions. "
            f"Based on guidance in {citation_ids}, holding window is active until {safe_until.strftime('%H:%M')}. "
            f"Mandatory temperature check recommended prior to handover."
        )
        reasons.append(explanation)
    else:
        # Live LLM execution
        dishes_desc = ", ".join(
            [f"{d.name} ({d.category.value}, flags: {[f.value for f in d.visible_flags]})" for d in passport.dishes]
        )
        prompt = SAFETY_PROMPT_TEMPLATE.format(
            dishes=dishes_desc or "Unspecified meal",
            storage=passport.handling.storage.value,
            cooked_at=passport.handling.cooked_at,
            container=passport.handling.container.value,
            rules_risk=rules_risk.value,
            safe_until=safe_until.isoformat(),
            minutes_left=minutes_left,
            rule_reasons="; ".join(rule_reasons),
            guidelines=chunk_text_block,
        )

        try:
            llm_res = llm.generate_structured(
                prompt=prompt,
                schema=LLMSafetyOutput,
            )
            llm_risk = llm_res.llm_risk_level
            reasons.append(llm_res.explanation)
            extra_flags = llm_res.extra_caution_flags
        except Exception:
            # Fallback to rules only
            reasons.append("Guidance advisory generated via deterministic rules (LLM synthesis unavailable).")

    # 4. Enforce Asymmetric Guardrail Merge
    # LLM can only make things SAFER (increase risk: LOW -> MEDIUM -> HIGH).
    # It can NEVER decrease risk or upgrade an unsafe verdict.
    if llm_risk < rules_risk:
        # LLM attempted to downgrade risk
        reasons.append("llm_override_rejected: LLM proposed lower risk level than deterministic safety rules.")
        final_risk = rules_risk
    elif llm_risk > rules_risk:
        # LLM flagged additional safety concerns; allow safe upgrade
        final_risk = llm_risk
        reasons.append(f"Risk elevated to {final_risk.value} based on safety guideline caution flags.")
    else:
        final_risk = rules_risk

    # If rules marked human_ok as False (e.g. mold, expired, biological risk),
    # the LLM can NEVER re-enable human consumption.
    if not human_ok or final_risk == RiskLevel.HIGH:
        final_human_ok = False
    else:
        final_human_ok = True

    if extra_flags:
        for f in extra_flags:
            reasons.append(f"Caution advisory: {f}")

    if not citations:
        reasons.append("rules only: No applicable knowledge base notes matched query.")

    return SafetyVerdict(
        safe_until=safe_until,
        minutes_left=minutes_left,
        risk_level=final_risk,
        human_ok=final_human_ok,
        reasons=reasons,
        citations=citations,
        requires_human_check=requires_human_check,
    )


def main() -> None:
    """CLI entrypoint for safety assessment."""
    parser = argparse.ArgumentParser(description="Run grounded safety assessment on a FoodPassport fixture.")
    parser.add_argument("fixture", type=str, help="Path to FoodPassport JSON or image fixture")
    args = parser.parse_args()

    fix_path = Path(args.fixture)
    if not fix_path.is_file():
        print(f"Error: Fixture file not found: {fix_path}")
        return

    if fix_path.suffix.lower() == ".json":
        with open(fix_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # Check if wrapped in FoodPassport or dishes
        if "handling" not in raw:
            raw["handling"] = {
                "cooked_at": "12:00",
                "storage": "ambient",
                "container": "covered",
                "est_portions": 30,
            }
        passport = FoodPassport.model_validate(raw)
    else:
        from app.llm.vision import extract_passport

        form = DonorForm(
            cooked_at="12:00",
            storage=StorageCondition.AMBIENT,
            est_portions=35,
        )
        passport = extract_passport([fix_path], form=form, mode="mock")

    verdict = assess_safety(passport, mode="mock")
    print("\n================ SAFETY VERDICT ================")
    print(f"Risk Level:            {verdict.risk_level.value}")
    print(f"Human Ok:              {verdict.human_ok}")
    print(f"Safe Until:            {verdict.safe_until.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Minutes Left:          {verdict.minutes_left} min")
    print(f"Requires Human Check:  {verdict.requires_human_check}")
    print("\nReasons:")
    for r in verdict.reasons:
        print(f"  - {r}")
    print("\nCitations:")
    for c in verdict.citations:
        print(f"  - [{c.id}] {c.source}")
    print(f"\nDisclaimer: {verdict.disclaimer}")
    print("================================================\n")


if __name__ == "__main__":
    main()
