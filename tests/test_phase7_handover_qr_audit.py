"""Unit tests for Phase 7: Verification Gate, Passport QR, and Tamper-Evident Audit Chain."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
import pytest

from app.audit import compute_event_hash, record_event, verify_chain
from app.db import (
    create_donor,
    create_listing,
    create_recipient,
    create_volunteer,
    get_connection,
    get_donor,
    get_listing,
    init_db,
    update_listing,
)
from app.handover import (
    ALLOWED_TRANSITIONS,
    confirm_delivery,
    generate_passport_qr,
    render_passport_page,
    validate_checklist_for_tier,
    validate_transition,
    verify_and_pickup,
)
from app.models import (
    AllergenHint,
    ContainerType,
    Dish,
    DishCategory,
    Donor,
    DonorForm,
    DonorKind,
    FoodPassport,
    Listing,
    ListingStatus,
    Recipient,
    StorageCondition,
    Tier,
    VegStatus,
    VehicleType,
    VisibleFlag,
    VerificationChecklist,
    Volunteer,
)
from app.safety.assess import SafetyVerdict
from app.safety.rules import RiskLevel


@pytest.fixture
def handover_db(tmp_path: Path) -> sqlite3.Connection:
    """Create fresh isolated SQLite database with test network."""
    db_file = tmp_path / "test_handover.db"
    conn = get_connection(db_file)
    init_db(conn)

    # 1. Test Donor
    donor = Donor(
        id="d_handover_01",
        name="Secret Hostel Mess",
        kind=DonorKind.HOSTEL_MESS,
        lat=13.1000,
        lon=77.5950,
        area="Yelahanka",
        typical_surplus_portions=50,
        active_hours="07:00-22:00",
    )
    create_donor(donor, conn=conn)

    # 2. Test Volunteer
    vol = Volunteer(
        id="v_handover_01",
        name="Ramesh Courier",
        lat=13.0980,
        lon=77.5920,
        vehicle=VehicleType.BIKE,
        max_portions_carry=60,
        languages=["en"],
        available=True,
        reliability=0.95,
    )
    create_volunteer(vol, conn=conn)

    # 3. Test Recipient
    recip = Recipient(
        id="r_handover_01",
        name="Hope Children Home",
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

    return conn


def create_sample_listing(
    conn: sqlite3.Connection,
    tier: Tier = Tier.HUMAN_RESCUE,
    status: ListingStatus = ListingStatus.ACCEPTED,
    portions: int = 50,
    created_at: Optional[datetime] = None,
) -> Listing:
    """Helper to insert test listing."""
    sim_now = created_at or datetime(2026, 10, 22, 12, 0, 0)
    passport = FoodPassport(
        dishes=[
            Dish(
                name="Sambar Rice",
                category=DishCategory.COOKED_RICE,
                veg_status=VegStatus.VEG,
                est_portions=portions,
                visible_flags=[VisibleFlag.NONE],
                confidence=0.95,
            )
        ],
        allergen_hints=[AllergenHint.DAIRY],
        photo_quality_ok=True,
        photo_quality_note="Clear catering container",
        needs_human_check=False,
        handling=DonorForm(
            cooked_at="11:00:00",
            storage=StorageCondition.AMBIENT,
            est_portions=portions,
            container=ContainerType.COVERED,
        ),
    )
    verdict = SafetyVerdict(
        safe_until=sim_now + timedelta(hours=3),
        minutes_left=180,
        risk_level=RiskLevel.LOW,
        human_ok=True,
        reasons=["Safe holding parameters"],
        citations=[],
        requires_human_check=False,
    )
    listing = Listing(
        id=f"list_ho_{uuid.uuid4().hex[:8]}",
        donor_id="d_handover_01",
        created_at=sim_now,
        passport=passport.model_dump(),
        safety=verdict.model_dump(),
        tier=tier,
        status=status,
        expires_at=verdict.safe_until,
    )
    create_listing(listing, conn=conn)
    return listing


# ---------------- 1. State Machine Transitions ----------------

def test_state_machine_legal_and_illegal_transitions():
    """Verify illegal transitions raise ValueError and legal transitions are allowed."""
    # Legal paths
    validate_transition(ListingStatus.LISTED, ListingStatus.VERIFIED)
    validate_transition(ListingStatus.LISTED, ListingStatus.MATCHED)
    validate_transition(ListingStatus.MATCHED, ListingStatus.ACCEPTED)
    validate_transition(ListingStatus.ACCEPTED, ListingStatus.PICKED_UP)
    validate_transition(ListingStatus.PICKED_UP, ListingStatus.DELIVERED)

    # Illegal paths raise ValueError
    with pytest.raises(ValueError, match="Illegal listing transition"):
        validate_transition(ListingStatus.LISTED, ListingStatus.DELIVERED)

    with pytest.raises(ValueError, match="Illegal listing transition"):
        validate_transition(ListingStatus.ACCEPTED, ListingStatus.DELIVERED)

    with pytest.raises(ValueError, match="Illegal listing transition"):
        validate_transition(ListingStatus.DELIVERED, ListingStatus.PICKED_UP)

    with pytest.raises(ValueError, match="Illegal listing transition"):
        validate_transition(ListingStatus.LISTED, ListingStatus.PICKED_UP)


# ---------------- 2. Human Verification Gate ----------------

def test_human_rescue_pickup_requires_full_checklist(handover_db: sqlite3.Connection):
    """HUMAN_RESCUE cannot be picked up if temperature or hygiene checks fail."""
    listing = create_sample_listing(conn=handover_db, tier=Tier.HUMAN_RESCUE, status=ListingStatus.ACCEPTED)
    sim_time = datetime(2026, 10, 22, 12, 15, 0)

    # Incomplete / Failed checklist (food is cold / temperature failed)
    bad_checklist = VerificationChecklist(
        volunteer_id="v_handover_01",
        timestamp=sim_time,
        smell_appearance_ok=True,
        temperature_ok=False,  # FAILED
        container_clean_covered=True,
        labelled_with_time=True,
        notes="Food felt room temp and cold",
    )

    reclassified, passed, error = verify_and_pickup(
        listing_id=listing.id,
        checklist=bad_checklist,
        conn=handover_db,
        now=sim_time,
    )

    assert passed is False
    assert error is not None
    assert "Temperature check failed" in error
    # Must have triggered Phase 4 reclassification away from HUMAN_RESCUE
    assert reclassified.tier != Tier.HUMAN_RESCUE

    # Verify audit event logged
    cursor = handover_db.execute("SELECT * FROM audit_events WHERE event_type = 'VERIFICATION_FAILED'")
    events = cursor.fetchall()
    assert len(events) >= 1


def test_human_rescue_pickup_succeeds_when_checklist_complete(handover_db: sqlite3.Connection):
    """HUMAN_RESCUE transitions to PICKED_UP when all mandatory checklist items pass."""
    listing = create_sample_listing(conn=handover_db, tier=Tier.HUMAN_RESCUE, status=ListingStatus.ACCEPTED)
    sim_time = datetime(2026, 10, 22, 12, 15, 0)

    good_checklist = VerificationChecklist(
        volunteer_id="v_handover_01",
        timestamp=sim_time,
        smell_appearance_ok=True,
        temperature_ok=True,
        temp_reading_c=68.5,
        container_clean_covered=True,
        labelled_with_time=True,
        notes="Steam visible, clean stainless containers",
    )

    updated_listing, passed, error = verify_and_pickup(
        listing_id=listing.id,
        checklist=good_checklist,
        conn=handover_db,
        now=sim_time,
    )

    assert passed is True
    assert error is None
    assert updated_listing.status == ListingStatus.PICKED_UP

    # Verify database state
    db_listing = get_listing(listing.id, conn=handover_db)
    assert db_listing.status == ListingStatus.PICKED_UP

    # Verify audit event
    cursor = handover_db.execute("SELECT * FROM audit_events WHERE event_type = 'LISTING_PICKED_UP'")
    assert len(cursor.fetchall()) == 1


# ---------------- 3. Animal Feed Lighter Checklist ----------------

def test_animal_feed_allowed_with_lighter_checklist(handover_db: sqlite3.Connection):
    """ANIMAL_FEED does not require hot-holding temperature or time label."""
    listing = create_sample_listing(conn=handover_db, tier=Tier.ANIMAL_FEED, status=ListingStatus.ACCEPTED)
    sim_time = datetime(2026, 10, 22, 12, 15, 0)

    lighter_checklist = VerificationChecklist(
        volunteer_id="v_handover_01",
        timestamp=sim_time,
        smell_appearance_ok=True,
        temperature_ok=False,  # Allowed for animal feed
        container_clean_covered=True,
        labelled_with_time=False,  # Allowed for animal feed
        notes="Plain cooked rice for gaushala",
    )

    updated_listing, passed, error = verify_and_pickup(
        listing_id=listing.id,
        checklist=lighter_checklist,
        conn=handover_db,
        now=sim_time,
    )

    assert passed is True
    assert error is None
    assert updated_listing.status == ListingStatus.PICKED_UP


def test_animal_feed_fails_on_smell_or_hygiene(handover_db: sqlite3.Connection):
    """ANIMAL_FEED is rejected if food smells spoiled or container is contaminated."""
    listing = create_sample_listing(conn=handover_db, tier=Tier.ANIMAL_FEED, status=ListingStatus.ACCEPTED)
    sim_time = datetime(2026, 10, 22, 12, 15, 0)

    spoiled_checklist = VerificationChecklist(
        volunteer_id="v_handover_01",
        timestamp=sim_time,
        smell_appearance_ok=False,  # Fails sensory check
        temperature_ok=False,
        container_clean_covered=True,
        labelled_with_time=False,
        notes="Sour odor detected",
    )

    reclassified, passed, error = verify_and_pickup(
        listing_id=listing.id,
        checklist=spoiled_checklist,
        conn=handover_db,
        now=sim_time,
    )

    assert passed is False
    assert error is not None
    assert "Visual & sensory check failed" in error
    # Reclassified to compost or discard
    assert reclassified.tier in (Tier.COMPOST_BIOGAS, Tier.DISCARD_UNSAFE)


# ---------------- 4. Delivery Confirmation & Portions ----------------

def test_delivery_confirmation_stores_actual_portions(handover_db: sqlite3.Connection):
    """Recipient confirms food arrival, storing actual_portions for impact metrics."""
    listing = create_sample_listing(conn=handover_db, status=ListingStatus.PICKED_UP)
    sim_time = datetime(2026, 10, 22, 13, 0, 0)

    delivered = confirm_delivery(
        listing_id=listing.id,
        actual_portions=48,
        recipient_notes="All 48 portions served to residents",
        conn=handover_db,
        now=sim_time,
    )

    assert delivered.status == ListingStatus.DELIVERED
    assert delivered.actual_portions == 48

    db_listing = get_listing(listing.id, conn=handover_db)
    assert db_listing.status == ListingStatus.DELIVERED
    assert db_listing.actual_portions == 48

    # Verify audit event
    cursor = handover_db.execute("SELECT * FROM audit_events WHERE event_type = 'LISTING_DELIVERED'")
    events = cursor.fetchall()
    assert len(events) == 1


def test_delivery_confirmation_rejects_illegal_state(handover_db: sqlite3.Connection):
    """Confirming delivery fails if listing was never picked up or portion count is invalid."""
    # Listing in ACCEPTED status (not yet PICKED_UP)
    listing = create_sample_listing(conn=handover_db, status=ListingStatus.ACCEPTED)

    with pytest.raises(ValueError, match="Illegal listing transition"):
        confirm_delivery(listing_id=listing.id, actual_portions=50, conn=handover_db)

    # Valid state, but negative portions
    listing.status = ListingStatus.PICKED_UP
    update_listing(listing, conn=handover_db)

    with pytest.raises(ValueError, match="Actual portions received must be at least 1"):
        confirm_delivery(listing_id=listing.id, actual_portions=0, conn=handover_db)


# ---------------- 5. Audit Chain Integrity & Tamper Detection ----------------

def test_audit_chain_verification_and_tamper_detection(handover_db: sqlite3.Connection):
    """Verify audit chain passes when clean, and detects modified or deleted rows."""
    now = datetime(2026, 10, 22, 12, 0, 0)

    # Record 4 clean chained events
    e1 = record_event("EVENT_1", "listing", "l_01", {"action": "create"}, conn=handover_db, timestamp=now)
    e2 = record_event("EVENT_2", "match", "m_01", {"action": "propose"}, conn=handover_db, timestamp=now + timedelta(minutes=1))
    e3 = record_event("EVENT_3", "match", "m_01", {"action": "accept"}, conn=handover_db, timestamp=now + timedelta(minutes=2))
    e4 = record_event("EVENT_4", "listing", "l_01", {"action": "pickup"}, conn=handover_db, timestamp=now + timedelta(minutes=3))

    # 1. Clean verification passes
    valid, msg, broken_row = verify_chain(conn=handover_db)
    assert valid is True
    assert "Audit chain integrity verified" in msg
    assert broken_row is None

    # 2. Tamper Test: Modify payload in e2
    with handover_db:
        handover_db.execute(
            "UPDATE audit_events SET payload = ? WHERE id = ?",
            ('{"action": "propose", "fraud": true}', e2.id),
        )

    tampered, msg, broken_row = verify_chain(conn=handover_db)
    assert tampered is False
    assert "Tampered audit entry" in msg

    # Restore e2 payload
    with handover_db:
        handover_db.execute(
            "UPDATE audit_events SET payload = ? WHERE id = ?",
            ('{"action": "propose"}', e2.id),
        )

    # Verify restored passes
    valid, _, _ = verify_chain(conn=handover_db)
    assert valid is True

    # 3. Tamper Test: Delete an intermediate row (e3)
    with handover_db:
        handover_db.execute("DELETE FROM audit_events WHERE id = ?", (e3.id,))

    broken, msg, broken_row = verify_chain(conn=handover_db)
    assert broken is False
    assert "Broken chain link" in msg


# ---------------- 6. QR Code Generation ----------------

def test_passport_qr_generation(tmp_path: Path):
    """Generate QR PNG bytes encoding passport ID."""
    out_file = tmp_path / "test_qr.png"
    png_bytes = generate_passport_qr(passport_id="list_qr_999", output_path=out_file)

    assert isinstance(png_bytes, bytes)
    assert len(png_bytes) > 0
    # PNG standard header magic bytes
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert out_file.exists()
    assert out_file.stat().st_size > 0


# ---------------- 7. Public Passport Read-Only Page & Privacy ----------------

def test_render_passport_page_structure_and_privacy(handover_db: sqlite3.Connection):
    """Public passport data must include dishes, countdown, and disclaimer, and NEVER donor contact details."""
    listing = create_sample_listing(conn=handover_db, portions=55)
    page_data = render_passport_page(
        listing_id=listing.id,
        conn=handover_db,
        now=datetime(2026, 10, 22, 12, 30, 0),
    )

    # Required public data present
    assert page_data["passport_id"] == listing.id
    assert page_data["total_portions"] == 55
    assert len(page_data["dishes"]) == 1
    assert page_data["dishes"][0]["name"] == "Sambar Rice"
    assert page_data["veg_status"] == "Pure Veg"
    assert "dairy" in page_data["allergen_hints"]
    assert "visual estimates only" in page_data["allergen_note"]
    assert page_data["safe_until"] is not None
    assert page_data["minutes_left"] > 0
    assert "HUMAN_RESCUE" in page_data["current_tier"]
    assert page_data["verification_status"] == "PENDING_VERIFICATION"
    assert "Advisory only" in page_data["disclaimer"]

    # CRITICAL PRIVACY TEST: Donor details must never leak into public read-only page
    donor = get_donor("d_handover_01", conn=handover_db)
    page_str = str(page_data)
    assert donor.name not in page_str
    assert "Secret Hostel Mess" not in page_str
    assert "phone" not in page_str.lower()
