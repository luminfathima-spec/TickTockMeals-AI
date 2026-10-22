"""Tests for Phase 3: Grounded Safety Engine (RAG plus rules).

Verifies deterministic guardrails, RAG retrieval accuracy, and the asymmetric
safety principle (rules can downgrade, but LLM can never upgrade risk).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from app.clock import SimClock
from app.models import (
    AllergenHint,
    ContainerType,
    Dish,
    DishCategory,
    DonorForm,
    FoodPassport,
    StorageCondition,
    VegStatus,
    VisibleFlag,
)
from app.safety.assess import LLMSafetyOutput, SafetyVerdict, assess_safety
from app.safety.rag import index_knowledge_base, retrieve
from app.safety.rules import RiskLevel, evaluate_rules, parse_cooked_at


@pytest.fixture
def base_clock() -> datetime:
    return datetime(2026, 10, 22, 14, 0, 0)


def create_test_passport(
    category: DishCategory = DishCategory.COOKED_RICE,
    storage: StorageCondition = StorageCondition.HOT_HELD,
    container: ContainerType = ContainerType.COVERED,
    cooked_hours_ago: float = 1.0,
    visible_flags: list[VisibleFlag] | None = None,
    ref_time: datetime | None = None,
    dish_name: str = "Test Food Item",
) -> FoodPassport:
    ref = ref_time or datetime(2026, 10, 22, 14, 0, 0)
    cooked_dt = ref - timedelta(hours=cooked_hours_ago)
    flags = visible_flags or [VisibleFlag.NONE]

    dish = Dish(
        name=dish_name,
        category=category,
        veg_status=VegStatus.VEG,
        est_portions=30,
        visible_flags=flags,
        confidence=0.92,
    )
    form = DonorForm(
        cooked_at=cooked_dt.strftime("%Y-%m-%d %H:%M:%S"),
        storage=storage,
        container=container,
        est_portions=30,
        notes="Automated test item",
    )
    return FoodPassport(
        dishes=[dish],
        allergen_hints=[AllergenHint.UNKNOWN],
        photo_quality_ok=True,
        photo_quality_note="Acceptable quality",
        needs_human_check=False,
        handling=form,
    )


# ---------------- Table-Driven Safety Rules Tests (15+ Cases) ----------------

TABLE_TEST_CASES = [
    # 1. Hot-held rice 1 hour old -> LOW risk, positive minutes left
    (
        create_test_passport(
            category=DishCategory.COOKED_RICE,
            storage=StorageCondition.HOT_HELD,
            cooked_hours_ago=1.0,
        ),
        RiskLevel.LOW,
        True,   # human_ok
        False,  # requires_human_check
    ),
    # 2. Hot-held rice 6 hours old -> HIGH risk (base safe hours for hot-held rice is 5h)
    (
        create_test_passport(
            category=DishCategory.COOKED_RICE,
            storage=StorageCondition.HOT_HELD,
            cooked_hours_ago=6.0,
        ),
        RiskLevel.HIGH,
        False,
        False,
    ),
    # 3. Ambient cooked rice 1 hour old -> LOW risk
    (
        create_test_passport(
            category=DishCategory.COOKED_RICE,
            storage=StorageCondition.AMBIENT,
            cooked_hours_ago=1.0,
        ),
        RiskLevel.LOW,
        True,
        False,
    ),
    # 4. Ambient cooked rice 4.5 hours old -> HIGH risk (ambient rice strict threshold is 3.5h)
    (
        create_test_passport(
            category=DishCategory.COOKED_RICE,
            storage=StorageCondition.AMBIENT,
            cooked_hours_ago=4.5,
        ),
        RiskLevel.HIGH,
        False,
        False,
    ),
    # 5. Mold flag present -> HIGH risk, human_ok=False
    (
        create_test_passport(
            category=DishCategory.COOKED_RICE,
            storage=StorageCondition.HOT_HELD,
            cooked_hours_ago=0.5,
            visible_flags=[VisibleFlag.MOLD],
        ),
        RiskLevel.HIGH,
        False,
        True,
    ),
    # 6. Biological contamination risk flag -> HIGH risk, human_ok=False
    (
        create_test_passport(
            category=DishCategory.CURRY_GRAVY,
            storage=StorageCondition.HOT_HELD,
            cooked_hours_ago=0.5,
            visible_flags=[VisibleFlag.CONTAMINATION_RISK],
        ),
        RiskLevel.HIGH,
        False,
        True,
    ),
    # 7. Discoloration flag -> at least MEDIUM risk, requires_human_check=True
    (
        create_test_passport(
            category=DishCategory.DAIRY,
            storage=StorageCondition.REFRIGERATED,
            cooked_hours_ago=1.0,
            visible_flags=[VisibleFlag.DISCOLORATION],
        ),
        RiskLevel.MEDIUM,
        True,
        True,
    ),
    # 8. Off_visual flag -> at least MEDIUM risk, requires_human_check=True
    (
        create_test_passport(
            category=DishCategory.DRY_VEG,
            storage=StorageCondition.HOT_HELD,
            cooked_hours_ago=1.0,
            visible_flags=[VisibleFlag.OFF_VISUAL],
        ),
        RiskLevel.MEDIUM,
        True,
        True,
    ),
    # 9. Storage unknown -> enforces conservative row, requires_human_check=True
    (
        create_test_passport(
            category=DishCategory.COOKED_RICE,
            storage=StorageCondition.UNKNOWN,
            cooked_hours_ago=1.0,
        ),
        RiskLevel.LOW,
        True,
        True,  # unknown storage mandates human check
    ),
    # 10. Storage unknown 3 hours old -> HIGH risk (conservative base safe hours is 2.0h)
    (
        create_test_passport(
            category=DishCategory.COOKED_RICE,
            storage=StorageCondition.UNKNOWN,
            cooked_hours_ago=3.0,
        ),
        RiskLevel.HIGH,
        False,
        True,
    ),
    # 11. Refrigerated dairy 4 hours old -> LOW risk, human_ok=True (refrigerated dairy safe up to 18h)
    (
        create_test_passport(
            category=DishCategory.DAIRY,
            storage=StorageCondition.REFRIGERATED,
            cooked_hours_ago=4.0,
            dish_name="Paneer Butter Masala",
        ),
        RiskLevel.LOW,
        True,
        False,
    ),
    # 12. Ambient dairy 3 hours old -> HIGH risk (strict ambient dairy limit is 2.5h)
    (
        create_test_passport(
            category=DishCategory.DAIRY,
            storage=StorageCondition.AMBIENT,
            cooked_hours_ago=3.0,
            dish_name="Curd Rice with Tadka",
        ),
        RiskLevel.HIGH,
        False,
        False,
    ),
    # 13. Open container -> MEDIUM risk, requires_human_check=True
    (
        create_test_passport(
            category=DishCategory.DRY_VEG,
            storage=StorageCondition.HOT_HELD,
            container=ContainerType.OPEN,
            cooked_hours_ago=1.0,
        ),
        RiskLevel.MEDIUM,
        True,
        True,
    ),
    # 14. Urgent window (less than 45 min left) -> MEDIUM risk
    (
        create_test_passport(
            category=DishCategory.COOKED_RICE,
            storage=StorageCondition.AMBIENT,  # 3.5h safe window
            cooked_hours_ago=3.1,             # 24 minutes left
        ),
        RiskLevel.MEDIUM,
        True,
        False,
    ),
    # 15. Packaged food within safe window -> requires_human_check=True for date verification
    (
        create_test_passport(
            category=DishCategory.PACKAGED,
            storage=StorageCondition.AMBIENT,
            cooked_hours_ago=2.0,
            dish_name="Packaged Biscuits",
        ),
        RiskLevel.LOW,
        True,
        True,
    ),
]


@pytest.mark.parametrize(
    "passport,expected_risk,expected_human_ok,expected_human_check",
    TABLE_TEST_CASES,
)
def test_table_driven_safety_rules(
    passport: FoodPassport,
    expected_risk: RiskLevel,
    expected_human_ok: bool,
    expected_human_check: bool,
    base_clock: datetime,
):
    """Verify table-driven test cases covering all guardrail rules."""
    risk, reasons, safe_until, human_ok, requires_check, _ = evaluate_rules(
        passport, reference_time=base_clock
    )
    assert risk == expected_risk
    assert human_ok == expected_human_ok
    assert requires_check == expected_human_check


def test_hot_held_rice_1hr_vs_6hr(base_clock: datetime):
    """Assert 1h old hot-held rice is LOW risk with positive minutes_left; 6h old is HIGH."""
    # 1 hour old
    p_fresh = create_test_passport(
        category=DishCategory.COOKED_RICE,
        storage=StorageCondition.HOT_HELD,
        cooked_hours_ago=1.0,
        ref_time=base_clock,
    )
    verdict_fresh = assess_safety(p_fresh, reference_time=base_clock, mode="mock")
    assert verdict_fresh.risk_level == RiskLevel.LOW
    assert verdict_fresh.human_ok is True
    assert verdict_fresh.minutes_left > 0

    # 6 hours old
    p_old = create_test_passport(
        category=DishCategory.COOKED_RICE,
        storage=StorageCondition.HOT_HELD,
        cooked_hours_ago=6.0,
        ref_time=base_clock,
    )
    verdict_old = assess_safety(p_old, reference_time=base_clock, mode="mock")
    assert verdict_old.risk_level == RiskLevel.HIGH
    assert verdict_old.human_ok is False
    assert verdict_old.minutes_left <= 0


def test_mold_flag_always_high_regardless_of_llm(base_clock: datetime):
    """Mold flag is always HIGH and human_ok false even if LLM claims it is safe."""
    p_mold = create_test_passport(
        category=DishCategory.BAKERY,
        storage=StorageCondition.AMBIENT,
        cooked_hours_ago=0.5,
        visible_flags=[VisibleFlag.MOLD],
        ref_time=base_clock,
    )

    # Mock an LLM that attempts to declare the moldy bread LOW risk
    mock_client = MagicMock()
    mock_client.mode = "live"
    mock_client.generate_structured.return_value = LLMSafetyOutput(
        llm_risk_level=RiskLevel.LOW,
        explanation="The model believes this bread looks completely safe to consume.",
        extra_caution_flags=[],
    )

    verdict = assess_safety(p_mold, reference_time=base_clock, client=mock_client)
    assert verdict.risk_level == RiskLevel.HIGH
    assert verdict.human_ok is False
    assert any("llm_override_rejected" in r for r in verdict.reasons)


def test_mocked_llm_cannot_lower_risk_and_logged(base_clock: datetime):
    """If deterministic rules assign HIGH risk, LLM trying to return LOW is rejected and logged."""
    p_expired = create_test_passport(
        category=DishCategory.COOKED_RICE,
        storage=StorageCondition.AMBIENT,
        cooked_hours_ago=6.0,  # clearly expired
        ref_time=base_clock,
    )

    mock_client = MagicMock()
    mock_client.mode = "live"
    mock_client.generate_structured.return_value = LLMSafetyOutput(
        llm_risk_level=RiskLevel.LOW,
        explanation="Model says it looks fine.",
        extra_caution_flags=[],
    )

    verdict = assess_safety(p_expired, reference_time=base_clock, client=mock_client)
    assert verdict.risk_level == RiskLevel.HIGH
    assert verdict.human_ok is False
    assert any("llm_override_rejected" in r for r in verdict.reasons)


def test_retrieval_returns_dairy_note_for_curd_query():
    """RAG retrieval returns the dairy guidance note when queried for curd."""
    results = retrieve("curd rice temperature abuse and whey separation", k=3)
    assert len(results) > 0
    top_chunk_id = results[0]["id"]
    top_chunk_source = results[0]["source"]
    assert "dairy" in top_chunk_id or "dairy" in top_chunk_source.lower()


def test_every_verdict_has_citation_or_explicit_rules_only(base_clock: datetime):
    """Every verdict contains at least one citation or an explicit rules-only explanation."""
    passport = create_test_passport(
        category=DishCategory.COOKED_RICE,
        storage=StorageCondition.HOT_HELD,
        cooked_hours_ago=1.0,
        ref_time=base_clock,
    )
    verdict = assess_safety(passport, reference_time=base_clock, mode="mock")
    has_citations = len(verdict.citations) > 0
    has_rules_only = any("rules only" in r.lower() for r in verdict.reasons)
    assert has_citations or has_rules_only
