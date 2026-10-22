"""Tests for Phase 4: Triage and the Rescue Cascade.

Verifies automatic tier assignment, cascading downgrades over time,
idempotency of scheduler ticks, animal feed exclusions, and match cancellations.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
import pytest

from app.config import get_config
from app.db import (
    create_donor,
    create_listing,
    create_match,
    get_connection,
    get_listing,
    init_db,
    list_matches,
)
from app.models import (
    ContainerType,
    Dish,
    DishCategory,
    Donor,
    DonorForm,
    DonorKind,
    FoodPassport,
    Listing,
    ListingStatus,
    Match,
    StorageCondition,
    Tier,
    VegStatus,
    VisibleFlag,
)
from app.safety.assess import Citation, SafetyVerdict
from app.safety.rules import RiskLevel
from app.triage import (
    TriageDecision,
    cascade_timeline,
    evaluate_triage,
    is_animal_feed_eligible,
    reclassify,
    tick,
)


@pytest.fixture
def test_db(tmp_path: Path) -> sqlite3.Connection:
    """Create a temporary SQLite database initialized with tables."""
    db_file = tmp_path / "test_triage.db"
    conn = get_connection(db_file)
    init_db(conn)
    yield conn
    conn.close()


def create_mock_passport(
    dish_name: str = "Plain Steamed Rice",
    category: DishCategory = DishCategory.COOKED_RICE,
    visible_flags: list[VisibleFlag] | None = None,
    storage: StorageCondition = StorageCondition.AMBIENT,
    cooked_at: str = "12:00:00",
) -> FoodPassport:
    flags = visible_flags or [VisibleFlag.NONE]
    return FoodPassport(
        dishes=[
            Dish(
                name=dish_name,
                category=category,
                veg_status=VegStatus.VEG,
                est_portions=30,
                visible_flags=flags,
                confidence=0.95,
            )
        ],
        allergen_hints=[],
        photo_quality_ok=True,
        photo_quality_note="Clear test image",
        needs_human_check=False,
        handling=DonorForm(
            cooked_at=cooked_at,
            storage=storage,
            est_portions=30,
            container=ContainerType.COVERED,
        ),
    )


def create_mock_safety(
    safe_until: datetime,
    human_ok: bool = True,
    risk_level: RiskLevel = RiskLevel.LOW,
    now_ref: datetime | None = None,
) -> SafetyVerdict:
    ref = now_ref or datetime(2026, 10, 22, 12, 0, 0)
    mins_left = int((safe_until - ref).total_seconds() / 60.0)
    return SafetyVerdict(
        safe_until=safe_until,
        minutes_left=mins_left,
        risk_level=risk_level,
        human_ok=human_ok,
        reasons=["Mock safety assessment."],
        citations=[Citation(id="test_chunk_1", source="FSSAI Guidelines")],
        requires_human_check=False,
    )


# ---------------- 1. Table-Driven Policy Tests (15+ Combinations) ----------------

TRIAGE_POLICY_CASES = [
    # 1. Fresh human-safe plain rice with 120 mins remaining -> HUMAN_RESCUE
    (
        create_mock_passport("Plain Steamed Rice", DishCategory.COOKED_RICE),
        datetime(2026, 10, 22, 14, 0, 0),  # safe until
        datetime(2026, 10, 22, 12, 0, 0),  # now (120m left)
        True,                              # human_ok
        RiskLevel.LOW,
        Tier.HUMAN_RESCUE,
    ),
    # 2. Human-safe plain rice with 50 mins left (> 45 min threshold) -> HUMAN_RESCUE
    (
        create_mock_passport("Plain Steamed Rice", DishCategory.COOKED_RICE),
        datetime(2026, 10, 22, 12, 50, 0),
        datetime(2026, 10, 22, 12, 0, 0),  # 50m left
        True,
        RiskLevel.LOW,
        Tier.HUMAN_RESCUE,
    ),
    # 3. Plain rice with 40 mins left (< 45 min threshold) -> Cascades to ANIMAL_FEED
    (
        create_mock_passport("Plain Steamed Rice", DishCategory.COOKED_RICE),
        datetime(2026, 10, 22, 12, 40, 0),
        datetime(2026, 10, 22, 12, 0, 0),  # 40m left
        True,
        RiskLevel.LOW,
        Tier.ANIMAL_FEED,
    ),
    # 4. Plain rice with 10 mins left -> ANIMAL_FEED
    (
        create_mock_passport("Plain Steamed Rice", DishCategory.COOKED_RICE),
        datetime(2026, 10, 22, 12, 10, 0),
        datetime(2026, 10, 22, 12, 0, 0),
        True,
        RiskLevel.LOW,
        Tier.ANIMAL_FEED,
    ),
    # 5. Sambar (onion/garlic gravy) with 40 mins left -> skips animal feed, goes to COMPOST_BIOGAS
    (
        create_mock_passport("Vegetable Drumstick Sambar", DishCategory.CURRY_GRAVY),
        datetime(2026, 10, 22, 12, 40, 0),
        datetime(2026, 10, 22, 12, 0, 0),
        True,
        RiskLevel.LOW,
        Tier.COMPOST_BIOGAS,
    ),
    # 6. Chicken Dum Biryani with 35 mins left -> skips animal, goes to COMPOST_BIOGAS
    (
        create_mock_passport("Chicken Dum Biryani", DishCategory.COOKED_RICE),
        datetime(2026, 10, 22, 12, 35, 0),
        datetime(2026, 10, 22, 12, 0, 0),
        True,
        RiskLevel.LOW,
        Tier.COMPOST_BIOGAS,
    ),
    # 7. Mysore Pak (heavy sweet) with 40 mins left -> skips animal, goes to COMPOST_BIOGAS
    (
        create_mock_passport("Mysore Pak", DishCategory.SWEET),
        datetime(2026, 10, 22, 12, 40, 0),
        datetime(2026, 10, 22, 12, 0, 0),
        True,
        RiskLevel.LOW,
        Tier.COMPOST_BIOGAS,
    ),
    # 8. Garlic Dal Tadka with 30 mins left -> skips animal (garlic), goes to COMPOST_BIOGAS
    (
        create_mock_passport("Garlic Dal Tadka", DishCategory.CURRY_GRAVY),
        datetime(2026, 10, 22, 12, 30, 0),
        datetime(2026, 10, 22, 12, 0, 0),
        True,
        RiskLevel.LOW,
        Tier.COMPOST_BIOGAS,
    ),
    # 9. Rice with visible mold -> skips animal feed, goes directly to COMPOST_BIOGAS
    (
        create_mock_passport(
            "Steamed Rice with Mold",
            DishCategory.COOKED_RICE,
            visible_flags=[VisibleFlag.MOLD],
        ),
        datetime(2026, 10, 22, 14, 0, 0),
        datetime(2026, 10, 22, 12, 0, 0),
        False,  # human_ok False
        RiskLevel.HIGH,
        Tier.COMPOST_BIOGAS,
    ),
    # 10. Rice with contamination risk -> skips animal feed, goes directly to COMPOST_BIOGAS
    (
        create_mock_passport(
            "Rice with Contamination",
            DishCategory.COOKED_RICE,
            visible_flags=[VisibleFlag.CONTAMINATION_RISK],
        ),
        datetime(2026, 10, 22, 14, 0, 0),
        datetime(2026, 10, 22, 12, 0, 0),
        False,
        RiskLevel.HIGH,
        Tier.COMPOST_BIOGAS,
    ),
    # 11. Plain wheat chapati / bread with 30 mins left -> ANIMAL_FEED (safe for animals)
    (
        create_mock_passport("Plain Whole Wheat Chapati", DishCategory.BAKERY),
        datetime(2026, 10, 22, 12, 30, 0),
        datetime(2026, 10, 22, 12, 0, 0),
        True,
        RiskLevel.LOW,
        Tier.ANIMAL_FEED,
    ),
    # 12. Fresh cut fruit salad with 35 mins left -> ANIMAL_FEED
    (
        create_mock_passport("Fresh Apple and Melon Slices", DishCategory.FRUIT_VEG_RAW),
        datetime(2026, 10, 22, 12, 35, 0),
        datetime(2026, 10, 22, 12, 0, 0),
        True,
        RiskLevel.LOW,
        Tier.ANIMAL_FEED,
    ),
    # 13. Plain boiled potato with 40 mins left -> ANIMAL_FEED
    (
        create_mock_passport("Boiled Potatoes Plain", DishCategory.DRY_VEG),
        datetime(2026, 10, 22, 12, 40, 0),
        datetime(2026, 10, 22, 12, 0, 0),
        True,
        RiskLevel.LOW,
        Tier.ANIMAL_FEED,
    ),
    # 14. Rice held 4 hours past safe_until (animal window 2h expired) -> COMPOST_BIOGAS
    (
        create_mock_passport("Plain Steamed Rice", DishCategory.COOKED_RICE),
        datetime(2026, 10, 22, 12, 0, 0),  # safe until was 12:00
        datetime(2026, 10, 22, 15, 0, 0),  # now is 15:00 (+3h, past animal window)
        False,
        RiskLevel.HIGH,
        Tier.COMPOST_BIOGAS,
    ),
    # 15. Rice held 30 hours past safe_until (compost window 24h expired) -> DISCARD_UNSAFE
    (
        create_mock_passport("Plain Steamed Rice", DishCategory.COOKED_RICE),
        datetime(2026, 10, 22, 12, 0, 0),
        datetime(2026, 10, 23, 20, 0, 0),  # +32h
        False,
        RiskLevel.HIGH,
        Tier.DISCARD_UNSAFE,
    ),
]


@pytest.mark.parametrize(
    "passport,safe_until,now_dt,human_ok,risk_level,expected_tier",
    TRIAGE_POLICY_CASES,
)
def test_triage_decision_policy_table(
    passport: FoodPassport,
    safe_until: datetime,
    now_dt: datetime,
    human_ok: bool,
    risk_level: RiskLevel,
    expected_tier: Tier,
):
    """Verify table-driven triage decisions for all 15+ combinations."""
    safety = create_mock_safety(
        safe_until=safe_until,
        human_ok=human_ok,
        risk_level=risk_level,
        now_ref=now_dt,
    )
    decision = evaluate_triage(passport, safety, reference_time=now_dt)
    assert decision.tier == expected_tier
    assert len(decision.policy_rule_ids) > 0


# ---------------- 2. Clock Advancement Tier Sequence Test ----------------

def test_advance_clock_tier_sequence_rice_listing(test_db: sqlite3.Connection):
    """Advance the clock in steps and assert the exact tier sequence:
    HUMAN_RESCUE -> ANIMAL_FEED -> COMPOST_BIOGAS.
    """
    donor = Donor(
        id="d_seq",
        name="Hostel Mess",
        kind=DonorKind.HOSTEL_MESS,
        lat=13.1000,
        lon=77.5900,
        area="Yelahanka",
        typical_surplus_portions=40,
        active_hours="08:00-22:00",
    )
    create_donor(donor, conn=test_db)

    # Safe until 15:00 (human window closes at 14:15, animal window closes at 17:00)
    t0 = datetime(2026, 10, 22, 12, 0, 0)
    passport = create_mock_passport("Plain Steamed Rice", DishCategory.COOKED_RICE)
    safety = create_mock_safety(safe_until=datetime(2026, 10, 22, 15, 0, 0), now_ref=t0)

    listing = Listing(
        id="list_seq",
        donor_id=donor.id,
        created_at=t0,
        passport=passport.model_dump(),
        safety=safety.model_dump(),
        tier=Tier.HUMAN_RESCUE,
        status=ListingStatus.LISTED,
        expires_at=safety.safe_until,
    )
    create_listing(listing, conn=test_db)

    # Step 1: At 12:00 (T0) -> HUMAN_RESCUE
    reclassify(listing, now=t0, conn=test_db)
    l1 = get_listing("list_seq", conn=test_db)
    assert l1.tier == Tier.HUMAN_RESCUE

    # Step 2: At 14:10 (50 mins left > 45) -> Still HUMAN_RESCUE
    t_1410 = datetime(2026, 10, 22, 14, 10, 0)
    reclassify(listing, now=t_1410, conn=test_db)
    l2 = get_listing("list_seq", conn=test_db)
    assert l2.tier == Tier.HUMAN_RESCUE

    # Step 3: At 14:20 (40 mins left < 45) -> Downgraded to ANIMAL_FEED
    t_1420 = datetime(2026, 10, 22, 14, 20, 0)
    reclassify(listing, now=t_1420, conn=test_db)
    l3 = get_listing("list_seq", conn=test_db)
    assert l3.tier == Tier.ANIMAL_FEED
    assert l3.status == ListingStatus.RECLASSIFIED

    # Step 4: At 16:30 (past safe_until 15:00, but within animal window ending at 17:00) -> Still ANIMAL_FEED
    t_1630 = datetime(2026, 10, 22, 16, 30, 0)
    reclassify(listing, now=t_1630, conn=test_db)
    l4 = get_listing("list_seq", conn=test_db)
    assert l4.tier == Tier.ANIMAL_FEED

    # Step 5: At 17:15 (past 17:00 animal window) -> Downgraded to COMPOST_BIOGAS
    t_1715 = datetime(2026, 10, 22, 17, 15, 0)
    reclassify(listing, now=t_1715, conn=test_db)
    l5 = get_listing("list_seq", conn=test_db)
    assert l5.tier == Tier.COMPOST_BIOGAS


# ---------------- 3. Scheduler Tick Idempotency ----------------

def test_tick_is_idempotent(test_db: sqlite3.Connection):
    """Running tick twice with the same timestamp produces identical state and zero redundant changes."""
    donor = Donor(
        id="d_idem",
        name="Tech Canteen",
        kind=DonorKind.CANTEEN,
        lat=13.1100,
        lon=77.5950,
        area="Yelahanka",
        typical_surplus_portions=30,
        active_hours="08:00-20:00",
    )
    create_donor(donor, conn=test_db)

    t0 = datetime(2026, 10, 22, 12, 0, 0)
    # Safe until 12:40 (40 mins left -> requires reclassification to ANIMAL_FEED)
    passport = create_mock_passport("Plain Steamed Rice", DishCategory.COOKED_RICE)
    safety = create_mock_safety(safe_until=datetime(2026, 10, 22, 12, 40, 0), now_ref=t0)

    listing = Listing(
        id="list_idem",
        donor_id=donor.id,
        created_at=t0,
        passport=passport.model_dump(),
        safety=safety.model_dump(),
        tier=Tier.HUMAN_RESCUE,
        status=ListingStatus.LISTED,
        expires_at=safety.safe_until,
    )
    create_listing(listing, conn=test_db)

    # First tick at t0: reclassifies to ANIMAL_FEED
    first_run_changes = tick(now=t0, conn=test_db)
    assert len(first_run_changes) == 1
    assert first_run_changes[0] == ("list_idem", "HUMAN_RESCUE", "ANIMAL_FEED")

    # Second tick at identical t0: must produce NO reclassifications
    second_run_changes = tick(now=t0, conn=test_db)
    assert len(second_run_changes) == 0

    # Third tick at identical t0: still no changes
    third_run_changes = tick(now=t0, conn=test_db)
    assert len(third_run_changes) == 0


# ---------------- 4. Mold & Animal Exclusions ----------------

def test_mold_never_reaches_animal_feed():
    """Moldy food must skip the animal feed tier completely."""
    passport = create_mock_passport(
        dish_name="Spoiled Rice with Mold",
        category=DishCategory.COOKED_RICE,
        visible_flags=[VisibleFlag.MOLD],
    )
    safety = create_mock_safety(
        safe_until=datetime(2026, 10, 22, 13, 0, 0),
        human_ok=False,
        risk_level=RiskLevel.HIGH,
    )
    decision = evaluate_triage(passport, safety, reference_time=datetime(2026, 10, 22, 12, 0, 0))
    assert decision.tier != Tier.ANIMAL_FEED
    assert decision.tier == Tier.COMPOST_BIOGAS


def test_excluded_foods_skip_animal_tier():
    """Allium and spicy curries must skip animal feed and cascade directly to compost."""
    sambar_passport = create_mock_passport("South Indian Onion Sambar", DishCategory.CURRY_GRAVY)
    eligible, reasons, _ = is_animal_feed_eligible(sambar_passport)
    assert eligible is False
    assert any("onion" in r.lower() or "curry" in r.lower() for r in reasons)

    safety = create_mock_safety(
        safe_until=datetime(2026, 10, 22, 12, 30, 0),  # expired for human
        now_ref=datetime(2026, 10, 22, 12, 0, 0),
    )
    decision = evaluate_triage(
        sambar_passport, safety, reference_time=datetime(2026, 10, 22, 12, 0, 0)
    )
    assert decision.tier == Tier.COMPOST_BIOGAS


# ---------------- 5. Match Cancellation on Downgrade ----------------

def test_downgrade_cancels_outstanding_human_matches(test_db: sqlite3.Connection):
    """When a listing downgrades from human tier, all pending PROPOSED human matches must be cancelled."""
    donor = Donor(
        id="d_cancel",
        name="Grand Banquet Hall",
        kind=DonorKind.WEDDING_HALL,
        lat=13.0800,
        lon=77.6000,
        area="Jakkur",
        typical_surplus_portions=100,
        active_hours="10:00-23:00",
    )
    create_donor(donor, conn=test_db)

    t0 = datetime(2026, 10, 22, 12, 0, 0)
    # Safe until 12:40 (40 mins left -> forces downgrade to ANIMAL_FEED)
    passport = create_mock_passport("Plain Steamed Rice", DishCategory.COOKED_RICE)
    safety = create_mock_safety(safe_until=datetime(2026, 10, 22, 12, 40, 0), now_ref=t0)

    listing = Listing(
        id="list_cancel",
        donor_id=donor.id,
        created_at=t0,
        passport=passport.model_dump(),
        safety=safety.model_dump(),
        tier=Tier.HUMAN_RESCUE,
        status=ListingStatus.LISTED,
        expires_at=safety.safe_until,
    )
    create_listing(listing, conn=test_db)

    # Create a recipient for the foreign key
    from app.db import create_recipient
    from app.models import Recipient
    recip = Recipient(
        id="recip_01",
        name="Ashraya Shelter",
        tier=Tier.HUMAN,
        lat=13.1000,
        lon=77.5900,
        area="Yelahanka",
        capacity_portions_per_day=50,
        veg_only=False,
        open_from="08:00",
        open_to="21:00",
        languages=["en"],
        accepts_cooked=True,
    )
    create_recipient(recip, conn=test_db)

    # Add a proposed human match
    human_match = Match(
        id="m_human_pending",
        listing_id=listing.id,
        recipient_id=recip.id,
        score=0.91,
        status="PROPOSED",
        created_at=t0,
    )
    create_match(human_match, conn=test_db)

    # Reclassify triggers downgrade from HUMAN_RESCUE to ANIMAL_FEED
    reclassify(listing, now=t0, conn=test_db)

    # Verify match status
    matches = list_matches(listing_id="list_cancel", conn=test_db)
    assert len(matches) == 1
    assert matches[0].status == "CANCELLED"


def test_cascade_timeline_helper():
    """Verify cascade_timeline formats readable milestones."""
    t0 = datetime(2026, 10, 22, 12, 0, 0)
    passport = create_mock_passport("Plain Steamed Rice", DishCategory.COOKED_RICE)
    safety = create_mock_safety(safe_until=datetime(2026, 10, 22, 15, 0, 0), now_ref=t0)

    listing = Listing(
        id="list_tl",
        donor_id="d1",
        created_at=t0,
        passport=passport.model_dump(),
        safety=safety.model_dump(),
        tier=Tier.HUMAN_RESCUE,
        status=ListingStatus.LISTED,
        expires_at=safety.safe_until,
    )
    timeline_str = cascade_timeline(listing, reference_time=t0)
    assert "Human rescue until 02:15 PM" in timeline_str
    assert "animal feed until 05:00 PM" in timeline_str
    assert "then compost" in timeline_str
