"""Unit tests for Phase 8: Impact Analytics and Source-Level Prevention Insights."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
import pytest

from app.db import (
    create_donor,
    create_listing,
    create_recipient,
    create_volunteer,
    get_connection,
    init_db,
    update_listing,
)
from app.handover import confirm_delivery, verify_and_pickup
from app.impact import compute_impact_summary, get_impact_dataframes
from app.insights import (
    analyze_donor_history,
    generate_prevention_insights,
    validate_insights_report,
)
from app.llm.client import LLMClient
from app.models import (
    AllergenHint,
    ContainerType,
    Dish,
    DishCategory,
    Donor,
    DonorForm,
    DonorKind,
    FoodPassport,
    HistoricalListing,
    Listing,
    ListingStatus,
    Recipient,
    StorageCondition,
    Tier,
    VegStatus,
    VerificationChecklist,
    VehicleType,
    VisibleFlag,
    Volunteer,
)
from app.safety.assess import SafetyVerdict
from app.safety.rules import RiskLevel
from app.seed import seed_database
from app.triage import reclassify


@pytest.fixture
def isolated_db(tmp_path: Path) -> sqlite3.Connection:
    """Create fresh isolated SQLite database."""
    db_file = tmp_path / "test_impact_isolated.db"
    conn = get_connection(db_file)
    init_db(conn)

    donor = Donor(
        id="donor_test_01",
        name="Campus Dining Facility",
        kind=DonorKind.HOSTEL_MESS,
        lat=13.1000,
        lon=77.5950,
        area="Yelahanka",
        typical_surplus_portions=50,
        active_hours="07:00-22:00",
    )
    create_donor(donor, conn=conn)

    recip = Recipient(
        id="recip_test_01",
        name="Compassion Shelter",
        tier=Tier.HUMAN,
        lat=13.1050,
        lon=77.5980,
        area="Yelahanka",
        capacity_portions_per_day=100,
        veg_only=False,
        open_from="08:00",
        open_to="21:00",
        languages=["en"],
        accepts_cooked=True,
    )
    create_recipient(recip, conn=conn)

    vol = Volunteer(
        id="vol_test_01",
        name="Vijay Volunteer",
        lat=13.0980,
        lon=77.5920,
        vehicle=VehicleType.BIKE,
        max_portions_carry=60,
        languages=["en"],
        available=True,
        reliability=0.95,
    )
    create_volunteer(vol, conn=conn)

    return conn


def make_test_listing(
    conn: sqlite3.Connection,
    donor_id: str = "donor_test_01",
    tier: Tier = Tier.HUMAN_RESCUE,
    status: ListingStatus = ListingStatus.DELIVERED,
    actual_portions: int = 50,
) -> Listing:
    """Helper to create and insert a test listing."""
    now = datetime(2026, 10, 24, 12, 0, 0)
    passport = FoodPassport(
        dishes=[
            Dish(
                name="Rice & Dal",
                category=DishCategory.COOKED_RICE,
                veg_status=VegStatus.VEG,
                est_portions=actual_portions,
                visible_flags=[VisibleFlag.NONE],
                confidence=0.95,
            )
        ],
        allergen_hints=[],
        photo_quality_ok=True,
        photo_quality_note="Clear",
        needs_human_check=False,
        handling=DonorForm(
            cooked_at="11:00:00",
            storage=StorageCondition.AMBIENT,
            est_portions=actual_portions,
            container=ContainerType.COVERED,
        ),
    )
    verdict = SafetyVerdict(
        safe_until=now + timedelta(hours=3),
        minutes_left=180,
        risk_level=RiskLevel.LOW,
        human_ok=True,
        reasons=["Safe holding parameters"],
        citations=[],
        requires_human_check=False,
    )
    listing = Listing(
        id=f"list_{uuid.uuid4().hex[:8]}",
        donor_id=donor_id,
        created_at=now,
        passport=passport.model_dump(),
        safety=verdict.model_dump(),
        tier=tier,
        status=status,
        expires_at=verdict.safe_until,
        actual_portions=actual_portions,
    )
    create_listing(listing, conn=conn)
    return listing


# ---------------- 1. Hand-Computed Values on Tiny Dataset ----------------

def test_impact_figures_match_hand_computed_values_on_tiny_dataset(isolated_db: sqlite3.Connection):
    """Verify that impact metrics match exact hand-computed values on a tiny dataset.

    Hand dataset:
    - 1 Human rescue: 50 portions
    - 1 Animal feed rescue: 30 portions
    - 1 Compost rescue: 20 portions
    Expected:
    - Total portions = 100
    - Total kg = 100 * 0.4 = 40.0 kg
    - Total CO2e = 40.0 * 2.5 = 100.0 kg CO2e
    - Cascade-saved portions = 30 + 20 = 50 portions
    - Cascade-saved percentage = (50 / 100) * 100 = 50.0%
    """
    make_test_listing(conn=isolated_db, tier=Tier.HUMAN_RESCUE, actual_portions=50)
    make_test_listing(conn=isolated_db, tier=Tier.ANIMAL_FEED, actual_portions=30)
    make_test_listing(conn=isolated_db, tier=Tier.COMPOST_BIOGAS, actual_portions=20)

    summary = compute_impact_summary(conn=isolated_db, include_history=False)

    assert summary["portions_by_tier"]["HUMAN_RESCUE"] == 50
    assert summary["portions_by_tier"]["ANIMAL_FEED"] == 30
    assert summary["portions_by_tier"]["COMPOST_BIOGAS"] == 20

    assert summary["total_portions_rescued"] == 100
    assert summary["total_kg_rescued"] == 40.0
    assert summary["total_co2e_avoided_kg"] == 100.0

    assert summary["cascade_saved_portions"] == 50
    assert summary["cascade_saved_pct"] == 50.0

    # Verify assumptions are transparently included in output
    assert summary["assumptions"]["kg_per_meal"] == 0.4
    assert summary["assumptions"]["co2e_kg_per_kg_food"] == 2.5
    assert "FAO" in summary["assumptions"]["source_kg_per_meal"]


# ---------------- 2. Cascade Saved with Reclassified Listing ----------------

def test_cascade_saved_percentage_with_reclassified_listing(isolated_db: sqlite3.Connection):
    """Verify cascade-saved percentage correctly accounts for reclassified listings."""
    # Create listing initially HUMAN_RESCUE
    listing = make_test_listing(
        conn=isolated_db,
        tier=Tier.HUMAN_RESCUE,
        status=ListingStatus.ACCEPTED,
        actual_portions=40,
    )

    sim_time = datetime(2026, 10, 24, 12, 15, 0)
    # Volunteer inspection fails temperature check -> triggers reclassification to ANIMAL_FEED
    failed_checklist = VerificationChecklist(
        volunteer_id="vol_test_01",
        timestamp=sim_time,
        smell_appearance_ok=True,
        temperature_ok=False,  # Fails human requirement
        container_clean_covered=True,
        labelled_with_time=True,
        notes="Food lukewarm",
    )

    reclassified_listing, passed, _ = verify_and_pickup(
        listing_id=listing.id,
        checklist=failed_checklist,
        conn=isolated_db,
        now=sim_time,
    )
    assert passed is False
    assert reclassified_listing.tier == Tier.ANIMAL_FEED

    # Courier proceeds with animal feed pickup
    reclassified_listing.status = ListingStatus.PICKED_UP
    update_listing(reclassified_listing, conn=isolated_db)

    # Delivery confirmation for 40 actual portions
    confirm_delivery(
        listing_id=reclassified_listing.id,
        actual_portions=40,
        recipient_notes="Received at animal shelter",
        conn=isolated_db,
        now=sim_time + timedelta(minutes=30),
    )

    summary = compute_impact_summary(conn=isolated_db, include_history=False)
    assert summary["total_portions_rescued"] == 40
    assert summary["portions_by_tier"]["ANIMAL_FEED"] == 40
    assert summary["cascade_saved_portions"] == 40
    # 100% of this rescued surplus was saved by the cascade!
    assert summary["cascade_saved_pct"] == 100.0


# ---------------- 3. Post-Check Validation & Hallucination Rejection ----------------

def test_insights_post_check_rejects_invented_numbers():
    """Verify that validate_insights_report catches hallucinated numbers and triggers fallback."""
    allowed = {115.5, 35.2, 80.3, 228.1, 15.0, 462.0}

    # 1. Clean report with valid numbers passes
    valid_text = (
        "Key Waste Finding: Sunday surplus averages 115.5 portions compared to 35.2 on weekdays (a 228.1% increase).\n"
        "Recommendations:\n"
        "1. Reduce Sunday rice preparation by 15%.\n"
        "2. Adjust forecasting.\n"
        "3. Repurpose remaining food."
    )
    valid, unapproved = validate_insights_report(valid_text, allowed)
    assert valid is True
    assert len(unapproved) == 0

    # 2. Fabricated report citing unapproved numbers is rejected
    fake_text = (
        "We observed that 888 kg of food was wasted and 77% was discarded on Tuesdays.\n"
        "Reduce batch size by 42%."
    )
    valid_f, unapproved_f = validate_insights_report(fake_text, allowed)
    assert valid_f is False
    assert any("888" in s for s in unapproved_f)
    assert any("77" in s for s in unapproved_f)
    assert any("42" in s for s in unapproved_f)


def test_generate_prevention_insights_falls_back_on_hallucinated_llm(isolated_db: sqlite3.Connection):
    """Verify generate_prevention_insights falls back to template when LLM invents facts."""
    from app.db import create_historical_listing

    for i in range(10):
        h = HistoricalListing(
            id=f"h_test_{i}",
            donor_id="donor_test_01",
            date="2026-10-01",
            weekday="Sunday" if i % 2 == 0 else "Monday",
            category="cooked_rice",
            dish_name="Steamed Rice",
            portions=100 if i % 2 == 0 else 30,
            tier_rescued="HUMAN",
            outcome="DELIVERED",
        )
        create_historical_listing(h, conn=isolated_db)

    class HallucinatingLLMClient:
        mode = "live"
        config = LLMClient().config

        def generate_text(self, *args, **kwargs) -> str:
            # Hallucinated response citing invented metrics
            return (
                "Our AI analysis detected 999 kg of unnecessary waste!\n"
                "1. Cut production by 67% on Thursdays.\n"
                "2. Throw away 84 portions.\n"
                "3. Stop serving rice."
            )

    hallucinating_client = HallucinatingLLMClient()
    result = generate_prevention_insights(
        donor_id="donor_test_01",
        conn=isolated_db,
        client=hallucinating_client,  # type: ignore
    )

    # Must fall back to deterministic template report
    assert result["is_template_fallback"] is True
    assert "999" not in result["report_text"]
    assert "Source Prevention Report" in result["report_text"]


# ---------------- 4. Planted Sunday Rice Pattern Detection ----------------

def test_planted_sunday_rice_pattern_detected_for_hostel_donor(tmp_path: Path):
    """Verify that analyze_donor_history detects the planted Sunday rice surplus pattern."""
    db_file = tmp_path / "test_seeded_impact.db"
    seed_database(conn_or_path=str(db_file), reset=True, seed=42)

    conn = get_connection(db_file)
    try:
        analysis = analyze_donor_history("donor_01", conn=conn)

        assert analysis["donor_id"] == "donor_01"
        assert analysis["total_events"] > 0
        assert analysis["top_pattern"] is not None

        top = analysis["top_pattern"]
        assert top["weekday"] == "Sunday"
        assert top["category"] == "cooked_rice"
        # Sunday average should be ~3x the weekday average (>100% increase)
        assert top["spike_avg"] > 90.0
        assert top["weekday_avg"] < 50.0
        assert top["pct_increase"] > 100.0

        # Generate prevention report in mock mode
        insights = generate_prevention_insights("donor_01", conn=conn)
        assert insights["is_template_fallback"] is False
        report = insights["report_text"]

        assert "Sunday" in report
        assert "rice" in report.lower()
        assert "15%" in report
        assert "ESTIMATED PROJECTION" in report
    finally:
        conn.close()


# ---------------- 5. Impact DataFrames Output Structure ----------------

def test_impact_dataframes_output_structure(tmp_path: Path):
    """Verify get_impact_dataframes produces valid pandas DataFrames for UI visualization."""
    db_file = tmp_path / "test_seeded_dfs.db"
    seed_database(conn_or_path=str(db_file), reset=True, seed=42)

    conn = get_connection(db_file)
    try:
        dfs = get_impact_dataframes(conn=conn, include_history=True)

        assert "by_tier" in dfs
        assert "donors" in dfs
        assert "recipients" in dfs
        assert "daily_trend" in dfs

        # Check by_tier DataFrame
        df_tier = dfs["by_tier"]
        assert not df_tier.empty
        assert "Tier" in df_tier.columns
        assert "Portions" in df_tier.columns
        assert "Kilograms (kg)" in df_tier.columns

        # Check donors leaderboard DataFrame
        df_donors = dfs["donors"]
        assert not df_donors.empty
        assert "Donor Name" in df_donors.columns
        assert "Portions Rescued" in df_donors.columns

        # Check recipients DataFrame
        df_recipients = dfs["recipients"]
        assert not df_recipients.empty
        assert "Recipient NGO / Shelter" in df_recipients.columns

        # Check daily trend DataFrame
        df_trend = dfs["daily_trend"]
        assert not df_trend.empty
        assert "Date" in df_trend.columns
        assert "Weekday" in df_trend.columns
    finally:
        conn.close()
