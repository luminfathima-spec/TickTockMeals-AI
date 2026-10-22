"""Unit tests for Phase 5: Time-feasible, explainable matching and routing."""

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
    create_recipient,
    create_volunteer,
    get_connection,
    get_listing,
    get_match,
    init_db,
    list_matches,
    list_volunteers,
)
from app.matching import (
    batch_assign_volunteers,
    escalate,
    find_matches,
    haversine_distance,
    is_recipient_open_at,
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
    Recipient,
    StorageCondition,
    Tier,
    VegStatus,
    VehicleType,
    VisibleFlag,
    Volunteer,
)
from app.safety.assess import SafetyVerdict
from app.safety.rules import RiskLevel


@pytest.fixture
def match_db(tmp_path: Path) -> sqlite3.Connection:
    """Create fresh isolated SQLite database with test network."""
    db_file = tmp_path / "test_match.db"
    conn = get_connection(db_file)
    init_db(conn)

    # Standard Donor in Yelahanka
    donor = Donor(
        id="d_yelahanka",
        name="Yelahanka Mess",
        kind=DonorKind.HOSTEL_MESS,
        lat=13.1000,
        lon=77.5950,
        area="Yelahanka",
        typical_surplus_portions=50,
        active_hours="07:00-22:00",
    )
    create_donor(donor, conn=conn)

    # Standard Volunteers
    vol_bike = Volunteer(
        id="v_bike",
        name="Biker Bob",
        lat=13.0980,
        lon=77.5920,
        vehicle=VehicleType.BIKE,
        max_portions_carry=35,
        languages=["en"],
        available=True,
        reliability=0.92,
    )
    vol_car = Volunteer(
        id="v_car",
        name="Driver Dave",
        lat=13.0950,
        lon=77.5900,
        vehicle=VehicleType.CAR,
        max_portions_carry=120,
        languages=["en"],
        available=True,
        reliability=0.95,
    )
    create_volunteer(vol_bike, conn=conn)
    create_volunteer(vol_car, conn=conn)

    yield conn
    conn.close()


def create_test_listing(
    donor_id: str = "d_yelahanka",
    portions: int = 30,
    is_veg: bool = True,
    safe_until: datetime | None = None,
    created_at: datetime | None = None,
    tier: Tier = Tier.HUMAN_RESCUE,
) -> Listing:
    t_create = created_at or datetime(2026, 10, 22, 12, 0, 0)
    t_safe = safe_until or (t_create + timedelta(hours=3))

    veg_stat = VegStatus.VEG if is_veg else VegStatus.NON_VEG
    dish_name = "Veg Rice & Dal" if is_veg else "Chicken Biryani"

    passport = FoodPassport(
        dishes=[
            Dish(
                name=dish_name,
                category=DishCategory.COOKED_RICE,
                veg_status=veg_stat,
                est_portions=portions,
                visible_flags=[VisibleFlag.NONE],
                confidence=0.95,
            )
        ],
        allergen_hints=[],
        photo_quality_ok=True,
        photo_quality_note="Acceptable",
        needs_human_check=False,
        handling=DonorForm(
            cooked_at=t_create.strftime("%H:%M:%S"),
            storage=StorageCondition.AMBIENT,
            est_portions=portions,
            container=ContainerType.COVERED,
        ),
    )

    safety = SafetyVerdict(
        safe_until=t_safe,
        minutes_left=int((t_safe - t_create).total_seconds() / 60.0),
        risk_level=RiskLevel.LOW,
        human_ok=True,
        requires_human_check=False,
        disclaimer="Advisory only",
    )

    return Listing(
        id=f"list_{portions}_{'veg' if is_veg else 'nonveg'}",
        donor_id=donor_id,
        created_at=t_create,
        passport=passport.model_dump(),
        safety=safety.model_dump(),
        tier=tier,
        status=ListingStatus.LISTED,
        expires_at=t_safe,
    )


# ---------------- 1. Hard Constraint Filter Tests ----------------

def test_constraint_closed_recipient(match_db: sqlite3.Connection):
    """Recipient closing before courier arrival is rejected."""
    # Recipient closes at 12:15 PM
    recip_early_close = Recipient(
        id="r_closed",
        name="Early Bird Shelter",
        tier=Tier.HUMAN,
        lat=13.1050,
        lon=77.6000,
        area="Yelahanka",
        capacity_portions_per_day=50,
        veg_only=False,
        open_from="08:00",
        open_to="12:15",
        languages=["en"],
        accepts_cooked=True,
    )
    create_recipient(recip_early_close, conn=match_db)

    now = datetime(2026, 10, 22, 12, 0, 0)
    listing = create_test_listing(portions=30, created_at=now)
    create_listing(listing, conn=match_db)

    result = find_matches(listing, now=now, conn=match_db)

    # ETA will be >= 15 min buffer -> arrival at >= 12:15:30 -> closed
    rejected_ids = [r.recipient_id for r in result.rejected_recipients]
    assert "r_closed" in rejected_ids

    reason = next(r.reason for r in result.rejected_recipients if r.recipient_id == "r_closed")
    assert "Closes at" in reason or "courier" in reason.lower()


def test_constraint_veg_only_conflict(match_db: sqlite3.Connection):
    """Veg-only recipient is disqualified when surplus contains non-veg."""
    recip_veg = Recipient(
        id="r_veg_strict",
        name="Strict Veg Home",
        tier=Tier.HUMAN,
        lat=13.1020,
        lon=77.5960,
        area="Yelahanka",
        capacity_portions_per_day=80,
        veg_only=True,
        open_from="08:00",
        open_to="21:00",
        languages=["en"],
        accepts_cooked=True,
    )
    create_recipient(recip_veg, conn=match_db)

    now = datetime(2026, 10, 22, 12, 0, 0)
    non_veg_listing = create_test_listing(portions=30, is_veg=False, created_at=now)
    create_listing(non_veg_listing, conn=match_db)

    result = find_matches(non_veg_listing, now=now, conn=match_db)
    rejected_ids = [r.recipient_id for r in result.rejected_recipients]
    assert "r_veg_strict" in rejected_ids

    reason = next(r.reason for r in result.rejected_recipients if r.recipient_id == "r_veg_strict")
    assert "Dietary conflict" in reason


def test_constraint_capacity_overflow(match_db: sqlite3.Connection):
    """Recipient with capacity less than total portions is rejected."""
    recip_small = Recipient(
        id="r_small",
        name="Tiny Shelter",
        tier=Tier.HUMAN,
        lat=13.1010,
        lon=77.5940,
        area="Yelahanka",
        capacity_portions_per_day=20,  # only 20
        veg_only=False,
        open_from="08:00",
        open_to="21:00",
        languages=["en"],
        accepts_cooked=True,
    )
    create_recipient(recip_small, conn=match_db)

    now = datetime(2026, 10, 22, 12, 0, 0)
    listing = create_test_listing(portions=50, created_at=now)
    create_listing(listing, conn=match_db)

    result = find_matches(listing, now=now, conn=match_db)
    rejected_ids = [r.recipient_id for r in result.rejected_recipients]
    assert "r_small" in rejected_ids

    reason = next(r.reason for r in result.rejected_recipients if r.recipient_id == "r_small")
    assert "Capacity overflow" in reason


def test_constraint_eta_too_long(match_db: sqlite3.Connection):
    """When safe window remaining is too small for courier transit, listing is non-viable."""
    recip = Recipient(
        id="r_far",
        name="Far North Care",
        tier=Tier.HUMAN,
        lat=13.1800,  # ~10 km away
        lon=77.6200,
        area="Rajanukunte",
        capacity_portions_per_day=50,
        veg_only=False,
        open_from="08:00",
        open_to="21:00",
        languages=["en"],
        accepts_cooked=True,
    )
    create_recipient(recip, conn=match_db)

    now = datetime(2026, 10, 22, 12, 0, 0)
    # Safe until in only 20 minutes (handover buffer alone is 15 min, plus 15 min safety margin = 30m required)
    listing = create_test_listing(
        portions=25,
        safe_until=now + timedelta(minutes=20),
        created_at=now,
    )
    create_listing(listing, conn=match_db)

    result = find_matches(listing, now=now, conn=match_db)
    # Far recipient should be rejected because ETA cannot fit in window
    assert len(result.candidates) == 0


def test_constraint_volunteer_bike_capacity_exceeded(match_db: sqlite3.Connection):
    """Volunteer on a bike cannot be assigned a load exceeding carry limit."""
    # Only bike volunteer is available (disable car)
    with match_db:
        match_db.execute("UPDATE volunteers SET available = 0 WHERE id = 'v_car'")

    recip = Recipient(
        id="r_capacity_ok",
        name="Big Shelter",
        tier=Tier.HUMAN,
        lat=13.1020,
        lon=77.5960,
        area="Yelahanka",
        capacity_portions_per_day=100,
        veg_only=False,
        open_from="08:00",
        open_to="21:00",
        languages=["en"],
        accepts_cooked=True,
    )
    create_recipient(recip, conn=match_db)

    now = datetime(2026, 10, 22, 12, 0, 0)
    # Listing has 70 portions (bike capacity is only 35)
    listing = create_test_listing(portions=70, created_at=now)
    create_listing(listing, conn=match_db)

    result = find_matches(listing, now=now, conn=match_db)
    assert len(result.candidates) == 0


# ---------------- 2. Urgency Shift Test ----------------

def test_best_match_changes_appropriately_when_urgency_changes(match_db: sqlite3.Connection):
    """When safe window becomes tight, urgency score elevates closer/faster couriers."""
    recip = Recipient(
        id="r_urgency_test",
        name="Community Shelter",
        tier=Tier.HUMAN,
        lat=13.1010,
        lon=77.5960,
        area="Yelahanka",
        capacity_portions_per_day=50,
        veg_only=False,
        open_from="08:00",
        open_to="21:00",
        languages=["en"],
        accepts_cooked=True,
    )
    create_recipient(recip, conn=match_db)

    now = datetime(2026, 10, 22, 12, 0, 0)

    # Case A: Relaxed safe window (180 minutes left)
    listing_relaxed = create_test_listing(
        portions=25,
        safe_until=now + timedelta(minutes=180),
        created_at=now,
    )
    res_relaxed = find_matches(listing_relaxed, now=now, conn=match_db)
    assert len(res_relaxed.candidates) > 0

    # Case B: Urgent safe window (50 minutes left)
    listing_urgent = create_test_listing(
        portions=25,
        safe_until=now + timedelta(minutes=50),
        created_at=now,
    )
    res_urgent = find_matches(listing_urgent, now=now, conn=match_db)
    assert len(res_urgent.candidates) > 0

    # Check that score breakdown urgency changed between relaxed and urgent
    cand_relaxed = res_relaxed.candidates[0]
    cand_urgent = res_urgent.candidates[0]
    assert cand_relaxed.score_breakdown["urgency"] != cand_urgent.score_breakdown["urgency"]


# ---------------- 3. Batch Assignment No Double Booking ----------------

def test_batch_assignment_never_double_books_volunteers(match_db: sqlite3.Connection):
    """Batch assignment matches multiple listings without assigning any volunteer twice."""
    recip1 = Recipient(
        id="r1",
        name="Shelter One",
        tier=Tier.HUMAN,
        lat=13.1020,
        lon=77.5950,
        area="Yelahanka",
        capacity_portions_per_day=50,
        veg_only=False,
        open_from="08:00",
        open_to="21:00",
        languages=["en"],
        accepts_cooked=True,
    )
    recip2 = Recipient(
        id="r2",
        name="Shelter Two",
        tier=Tier.HUMAN,
        lat=13.1040,
        lon=77.5970,
        area="Yelahanka",
        capacity_portions_per_day=50,
        veg_only=False,
        open_from="08:00",
        open_to="21:00",
        languages=["en"],
        accepts_cooked=True,
    )
    create_recipient(recip1, conn=match_db)
    create_recipient(recip2, conn=match_db)

    now = datetime(2026, 10, 22, 12, 0, 0)
    l1 = create_test_listing(portions=25, created_at=now)
    l2 = create_test_listing(portions=25, created_at=now)
    l1.id = "l1"
    l2.id = "l2"
    create_listing(l1, conn=match_db)
    create_listing(l2, conn=match_db)

    volunteers = list_volunteers(conn=match_db)
    assignments = batch_assign_volunteers(
        listings=[l1, l2],
        volunteers=volunteers,
        conn=match_db,
        now=now,
    )

    assigned_vols = [cand.volunteer_id for cand in assignments.values() if cand is not None]
    # Assert no volunteer is double-booked
    assert len(assigned_vols) == len(set(assigned_vols))


# ---------------- 4. Escalation Loop Test ----------------

def test_escalation_walks_candidate_list_and_triggers_reclassification(match_db: sqlite3.Connection):
    """Declining a candidate moves to the next, and exhausting candidates triggers cascade reclassification."""
    # Add two recipients
    r1 = Recipient(
        id="r_esc_1",
        name="Candidate One",
        tier=Tier.HUMAN,
        lat=13.1010,
        lon=77.5950,
        area="Yelahanka",
        capacity_portions_per_day=50,
        veg_only=False,
        open_from="08:00",
        open_to="21:00",
        languages=["en"],
        accepts_cooked=True,
    )
    r2 = Recipient(
        id="r_esc_2",
        name="Candidate Two",
        tier=Tier.HUMAN,
        lat=13.1030,
        lon=77.5970,
        area="Yelahanka",
        capacity_portions_per_day=50,
        veg_only=False,
        open_from="08:00",
        open_to="21:00",
        languages=["en"],
        accepts_cooked=True,
    )
    create_recipient(r1, conn=match_db)
    create_recipient(r2, conn=match_db)

    now = datetime(2026, 10, 22, 12, 0, 0)
    listing = create_test_listing(portions=25, created_at=now)
    listing.id = "list_esc"
    create_listing(listing, conn=match_db)

    # Create initial match with r1
    initial_match = Match(
        id="m_esc_0",
        listing_id=listing.id,
        recipient_id=r1.id,
        score=0.85,
        status="PROPOSED",
        created_at=now,
    )
    create_match(initial_match, conn=match_db)

    # 1. Decline first match -> Escalates to r2
    new_match, did_cascade = escalate(initial_match.id, action="DECLINED", conn=match_db, now=now)
    assert new_match is not None
    assert did_cascade is False
    assert new_match.recipient_id == r2.id
    assert new_match.status == "PROPOSED"

    # 2. Decline second match -> No more candidates! Triggers reclassification
    final_match, did_cascade_2 = escalate(new_match.id, action="DECLINED", conn=match_db, now=now)
    assert final_match is None
    assert did_cascade_2 is True


# ---------------- 5. Rejection Reason Details & Distance ----------------

def test_every_rejection_includes_specific_reason_string(match_db: sqlite3.Connection):
    """Every rejected recipient includes an explicit informative rejection reason."""
    now = datetime(2026, 10, 22, 12, 0, 0)
    listing = create_test_listing(portions=30, created_at=now)
    create_listing(listing, conn=match_db)

    result = find_matches(listing, now=now, conn=match_db)
    for rej in result.rejected_recipients:
        assert rej.reason
        assert len(rej.reason.strip()) > 5


def test_haversine_distance():
    """Haversine distance calculates accurate geographic distances."""
    # Yelahanka (13.1008, 77.5963) to Hebbal (13.0358, 77.5880) is ~7.3 km
    dist = haversine_distance(13.1008, 77.5963, 13.0358, 77.5880)
    assert 6.5 <= dist <= 8.5
