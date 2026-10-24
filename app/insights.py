"""Source-level prevention insights engine for TickTock Meals.

Analyzes 30-day historical surplus data to identify overproduction patterns
(e.g., hostel mess Sunday rice overproduction) and delivers actionable advice
to reduce food waste at the source.
Includes strict number-consistency post-check guardrails and deterministic fallback.
"""

from __future__ import annotations

import math
import re
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import numpy as np

from app.config import AppConfig, get_config
from app.db import get_connection, get_donor, list_historical_listings
from app.llm.client import LLMClient
from app.models import HistoricalListing


def analyze_donor_history(
    donor_id: str,
    conn: Optional[sqlite3.Connection] = None,
    config: Optional[AppConfig] = None,
) -> Dict[str, Any]:
    """Analyze historical surplus listings for a donor to detect overproduction patterns.

    Computes weekday/category statistics (mean, deviation, weekday effect)
    and estimates projected waste avoided.
    """
    c = conn or get_connection()
    close_after = conn is None
    cfg = config or get_config()

    kg_per_meal = cfg.impact_factors.kg_per_meal
    co2e_factor = cfg.impact_factors.co2e_kg_per_kg_food

    try:
        donor = get_donor(donor_id, conn=c)
        donor_name = donor.name if donor else f"Donor {donor_id}"
        history = list_historical_listings(donor_id=donor_id, conn=c)

        if not history:
            return {
                "donor_id": donor_id,
                "donor_name": donor_name,
                "total_events": 0,
                "total_portions": 0,
                "mean_portions": 0.0,
                "top_pattern": None,
                "summary_table": [],
                "allowed_numbers": set(),
                "projection": {
                    "reduction_target_pct": 15.0,
                    "projected_portions_month": 0,
                    "projected_kg_month": 0.0,
                    "projected_co2e_month": 0.0,
                    "label": "No historical data available",
                },
            }

        # 1. Group portions by (weekday, category)
        group_data: Dict[Tuple[str, str], List[int]] = {}
        category_portions: Dict[str, List[int]] = {}
        category_weekday_portions: Dict[str, Dict[str, List[int]]] = {}

        for h in history:
            key = (h.weekday, h.category)
            group_data.setdefault(key, []).append(h.portions)
            category_portions.setdefault(h.category, []).append(h.portions)
            category_weekday_portions.setdefault(h.category, {}).setdefault(h.weekday, []).append(h.portions)

        # 2. Build summary table of statistical metrics
        summary_table: List[Dict[str, Any]] = []
        for (w_day, cat), portions in group_data.items():
            arr = np.array(portions)
            summary_table.append({
                "weekday": w_day,
                "category": cat,
                "count": len(portions),
                "total_portions": int(np.sum(arr)),
                "avg_portions": round(float(np.mean(arr)), 1),
                "std_portions": round(float(np.std(arr)), 1),
            })

        summary_table.sort(key=lambda x: x["avg_portions"], reverse=True)

        # 3. Detect strongest weekday effect pattern
        top_pattern: Optional[Dict[str, Any]] = None
        max_delta = -1.0

        for cat, w_dict in category_weekday_portions.items():
            for w_day, portions in w_dict.items():
                spike_avg = float(np.mean(portions))
                # Average of other days for this category
                other_portions: List[int] = []
                for other_day, o_list in w_dict.items():
                    if other_day != w_day:
                        other_portions.extend(o_list)

                other_avg = float(np.mean(other_portions)) if other_portions else spike_avg
                diff = spike_avg - other_avg

                if diff > max_delta and len(portions) >= 2:
                    max_delta = diff
                    pct_increase = round((diff / other_avg) * 100.0, 1) if other_avg > 0 else 0.0
                    top_pattern = {
                        "weekday": w_day,
                        "category": cat,
                        "spike_avg": round(spike_avg, 1),
                        "weekday_avg": round(other_avg, 1),
                        "difference": round(diff, 1),
                        "pct_increase": pct_increase,
                        "total_spike_portions": sum(portions),
                        "spike_occurrences": len(portions),
                    }

        # 4. Compute projected monthly savings (assuming 15% reduction in top spike category)
        rec_pct = 15.0
        projected_portions = 0
        if top_pattern:
            # 15% reduction on top spike category volume
            projected_portions = int(round(top_pattern["total_spike_portions"] * (rec_pct / 100.0)))

        projected_kg = round(projected_portions * kg_per_meal, 1)
        projected_co2e = round(projected_kg * co2e_factor, 1)

        projection = {
            "reduction_target_pct": rec_pct,
            "projected_portions_month": projected_portions,
            "projected_kg_month": projected_kg,
            "projected_co2e_month": projected_co2e,
            "label": (
                f"ESTIMATED PROJECTION (not a promise): Implementing recommended {int(rec_pct)}% batch "
                f"reductions can prevent approximately {projected_portions} portions ({projected_kg} kg) "
                f"of food surplus and avoid {projected_co2e} kg CO2e monthly."
            ),
        }

        # 5. Extract set of allowed numbers for post-check validation
        allowed_nums: Set[float] = {
            float(rec_pct),
            float(projected_portions),
            float(projected_kg),
            float(projected_co2e),
            float(kg_per_meal),
            float(co2e_factor),
        }
        if top_pattern:
            allowed_nums.update([
                float(top_pattern["spike_avg"]),
                float(top_pattern["weekday_avg"]),
                float(top_pattern["difference"]),
                float(top_pattern["pct_increase"]),
                float(top_pattern["total_spike_portions"]),
                float(top_pattern["spike_occurrences"]),
            ])

        for row in summary_table:
            allowed_nums.update([
                float(row["count"]),
                float(row["total_portions"]),
                float(row["avg_portions"]),
                float(row["std_portions"]),
            ])

        all_portions = [h.portions for h in history]
        total_portions_all = sum(all_portions)
        mean_portions_all = round(float(np.mean(all_portions)), 1) if all_portions else 0.0

        allowed_nums.add(float(total_portions_all))
        allowed_nums.add(float(mean_portions_all))
        allowed_nums.add(float(len(history)))

        return {
            "donor_id": donor_id,
            "donor_name": donor_name,
            "total_events": len(history),
            "total_portions": total_portions_all,
            "mean_portions": mean_portions_all,
            "top_pattern": top_pattern,
            "summary_table": summary_table,
            "allowed_numbers": allowed_nums,
            "projection": projection,
        }
    finally:
        if close_after:
            c.close()


def validate_insights_report(report_text: str, allowed_numbers: Set[float]) -> Tuple[bool, List[str]]:
    """Verify that every quantitative number cited in the report exists in the allowed set.

    Rejects LLM hallucinations where fabricated percentages or metrics are introduced.
    """
    # Exclude common formatting numbers (list items: 1., 2., 3.; 30 days)
    exempt_numbers = {1.0, 2.0, 3.0, 4.0, 5.0, 30.0, 2026.0}

    # Find numbers and percentages (e.g. 115.5, 35, 15%, 80.3)
    tokens = re.findall(r"\b\d+(?:\.\d+)?%?\b", report_text)
    unapproved: List[str] = []

    for token in tokens:
        clean_num_str = token.rstrip("%")
        try:
            val = float(clean_num_str)
        except ValueError:
            continue

        if val in exempt_numbers:
            continue

        # Check if number matches any allowed number within a small rounding tolerance
        matched = any(math.isclose(val, allowed, abs_tol=0.6) for allowed in allowed_numbers)
        if not matched:
            unapproved.append(token)

    return (len(unapproved) == 0, unapproved)


def get_deterministic_insights_report(analysis: Dict[str, Any]) -> str:
    """Generate high-quality verified template report strictly adhering to verified metrics."""
    donor_name = analysis["donor_name"]
    pattern = analysis.get("top_pattern")
    proj = analysis["projection"]

    if not pattern:
        return (
            f"TickTock Meals Source Prevention Report for {donor_name}:\n\n"
            "Key Waste Finding:\n"
            "Historical production logs show stable distribution across weekdays with no severe overproduction spikes.\n\n"
            "Recommendations:\n"
            "1. Continue standardized daily portion batching.\n"
            "2. Audit daily plate waste after high-occupancy events.\n"
            "3. Maintain rescue readiness for unforeseen attendance shifts.\n\n"
            f"{proj['label']}"
        )

    w_day = pattern["weekday"]
    cat = pattern["category"].replace("_", " ")
    spike_avg = pattern["spike_avg"]
    w_avg = pattern["weekday_avg"]
    pct_inc = pattern["pct_increase"]
    rec_pct = int(proj["reduction_target_pct"])

    return (
        f"TickTock Meals Source Prevention Report for {donor_name}:\n\n"
        f"Key Waste Finding:\n"
        f"Analysis of 30-day production history reveals a significant surplus spike on {w_day}s. "
        f"{cat.title()} surplus averages {spike_avg} portions on {w_day}s compared to a weekday average of "
        f"{w_avg} portions (a {pct_inc}% increase).\n\n"
        "Concrete Operational Recommendations:\n"
        f"1. Target Weekend Batch Reduction: Scale back {w_day} {cat} preparation volume by {rec_pct}% to align with reduced resident attendance.\n"
        f"2. Attendance Forecasting: Implement pre-weekend sign-outs to capture resident departure trends before preparation begins.\n"
        "3. Menu Repurposing: Introduce planned recipes on subsequent mornings that safely incorporate leftover grain portions.\n\n"
        f"{proj['label']}"
    )


def generate_prevention_insights(
    donor_id: str,
    conn: Optional[sqlite3.Connection] = None,
    client: Optional[LLMClient] = None,
    config: Optional[AppConfig] = None,
) -> Dict[str, Any]:
    """Generate source prevention insights report for a donor with strict number guardrails."""
    c = conn or get_connection()
    close_after = conn is None
    cfg = config or get_config()

    try:
        analysis = analyze_donor_history(donor_id, conn=c, config=cfg)
        template_report = get_deterministic_insights_report(analysis)

        llm_client = client or LLMClient()

        if llm_client.mode == "mock":
            return {
                "donor_id": donor_id,
                "donor_name": analysis["donor_name"],
                "analysis": analysis,
                "report_text": template_report,
                "is_template_fallback": False,
                "projection": analysis["projection"],
            }

        # Live Mode Generation
        system_instruction = (
            "You are the TickTock Meals Prevention Insights Analyst. You analyze historical food waste patterns "
            "to recommend production changes that reduce surplus at source.\n"
            "STRICT GUARDRAIL:\n"
            "You may ONLY cite numbers (counts, averages, percentages, kilograms) that appear in the provided summary statistics table. "
            "Do NOT invent or extrapolate any unapproved numbers.\n"
            "Provide a concise plain-English report with 3 concrete operational recommendations."
        )

        table_lines = [
            f"- {r['weekday']} {r['category']}: count={r['count']}, avg={r['avg_portions']} portions, total={r['total_portions']}"
            for r in analysis["summary_table"][:8]
        ]
        table_str = "\n".join(table_lines)

        prompt = (
            f"Generate source-level waste reduction insights report for donor '{analysis['donor_name']}'.\n"
            f"Summary Statistics:\n{table_str}\n\n"
            f"Identified Pattern: {analysis['top_pattern']}\n"
            f"Projection Target: {analysis['projection']['reduction_target_pct']}% reduction\n"
            "Format with: Key Finding, 3 Recommendations, and Projected Savings."
        )

        try:
            generated = llm_client.generate_text(
                prompt=prompt,
                model=cfg.llm.models.MODEL_FAST,
                system_instruction=system_instruction,
            ).strip()

            is_valid, unapproved = validate_insights_report(generated, analysis["allowed_numbers"])

            if is_valid:
                return {
                    "donor_id": donor_id,
                    "donor_name": analysis["donor_name"],
                    "analysis": analysis,
                    "report_text": generated,
                    "is_template_fallback": False,
                    "projection": analysis["projection"],
                }
            else:
                # LLM cited unapproved numbers: fall back to deterministic template
                return {
                    "donor_id": donor_id,
                    "donor_name": analysis["donor_name"],
                    "analysis": analysis,
                    "report_text": template_report,
                    "is_template_fallback": True,
                    "rejected_numbers": unapproved,
                    "projection": analysis["projection"],
                }
        except Exception:
            return {
                "donor_id": donor_id,
                "donor_name": analysis["donor_name"],
                "analysis": analysis,
                "report_text": template_report,
                "is_template_fallback": True,
                "projection": analysis["projection"],
            }
    finally:
        if close_after:
            c.close()
