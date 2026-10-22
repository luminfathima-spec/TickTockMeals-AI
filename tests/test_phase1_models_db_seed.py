"""Tests for Phase 1: Domain models, SQLite database, and seeded Bengaluru network."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
import pytest

from app.db import (
    create_audit_event,
    create_donor,
    create_historical_listing,
    create_listing,
    create_match,
    create_outbox_message,
    create_recipient,
    create_volunteer,
    get_connection,
    get_donor,
    get_listing,
    get_match,
    get_outbox_message,
    get_recipient,
    get_volunteer,
    init_db,
    list_donors,
    list_historical_listings,
    list_recipients,
    list_volunteers,
)
from app.models import (
    AuditEvent,
    Donor,
    DonorKind,
    HistoricalListing,
    Listing,
    ListingStatus,
    Match,
    OutboxMessage,
    Recipient,
    Tier,
    VehicleType,
    Volunteer,
)
from app.seed import get_seed_donors, get_seed_recipients, get_seed_volunteers, seed_database


@pytest.fixture
def test_db(tmp_path: Path) -> sqlite3.Connection:
    """Create a fresh in-memory or temporary SQLite database."""
    db_file = tmp_path / "test_ticktock.db"
    conn = get_connection(db_file)
    init_db(conn)
    yield conn
    conn.close()


def test_roundtrip_all_models_through_db(test_db: sqlite3.Connection):
    """Verify that every domain model can round-trip through SQLite cleanly."""
    # 1. Donor
    donor = Donor(
        id="d_test",
        name="Test Kitchen",
        kind=DonorKind.HOSTEL_MESS,
        lat=13.1000,
        lon=77.5900,
        area="Yelahanka",
        typical_surplus_portions=50,
        active_hours="08:00-20:00",
    )
    create_donor(donor, conn=test_db)
    loaded_donor = get_donor("d_test", conn=test_db)
    assert loaded_donor is not None
    assert loaded_donor.id == donor.id
    assert loaded_donor.kind == DonorKind.HOSTEL_MESS
    assert loaded_donor.typical_surplus_portions == 50

    # 2. Recipient
    recip = Recipient(
        id="r_test",
        name="Test Shelter",
        tier=Tier.HUMAN,
        lat=13.1050,
        lon=77.5950,
        area="Yelahanka",
        capacity_portions_per_day=75,
        veg_only=True,
        open_from="09:00",
        open_to="18:00",
        languages=["en", "kn"],
        accepts_cooked=True,
    )
    create_recipient(recip, conn=test_db)
    loaded_recip = get_recipient("r_test", conn=test_db)
    assert loaded_recip is not None
    assert loaded_recip.veg_only is True
    assert loaded_recip.languages == ["en", "kn"]
    assert loaded_recip.tier == Tier.HUMAN

    # 3. Volunteer
    vol = Volunteer(
        id="v_test",
        name="Test Rider",
        lat=13.0900,
        lon=77.5800,
        vehicle=VehicleType.BIKE,
        max_portions_carry=30,
        languages=["kn", "hi"],
        available=True,
        reliability=0.95,
    )
    create_volunteer(vol, conn=test_db)
    loaded_vol = get_volunteer("v_test", conn=test_db)
    assert loaded_vol is not None
    assert loaded_vol.vehicle == VehicleType.BIKE
    assert loaded_vol.reliability == 0.95

    # 4. Listing
    now_dt = datetime(2026, 10, 22, 14, 0, 0)
    listing = Listing(
        id="list_test",
        donor_id=donor.id,
        created_at=now_dt,
        photo_path="data/test_photo.jpg",
        passport={"dishes": [{"name": "Rice", "category": "cooked_rice"}]},
        safety={"risk_level": "LOW", "safe_until": "2026-10-22T18:00:00"},
        tier=Tier.HUMAN,
        status=ListingStatus.LISTED,
        expires_at=datetime(2026, 10, 22, 18, 0, 0),
    )
    create_listing(listing, conn=test_db)
    loaded_listing = get_listing("list_test", conn=test_db)
    assert loaded_listing is not None
    assert loaded_listing.status == ListingStatus.LISTED
    assert loaded_listing.passport["dishes"][0]["name"] == "Rice"
    assert loaded_listing.safety["risk_level"] == "LOW"

    # 5. Match
    match_obj = Match(
        id="m_test",
        listing_id=listing.id,
        recipient_id=recip.id,
        volunteer_id=vol.id,
        score=0.88,
        score_breakdown={"urgency": 0.35, "proximity": 0.25},
        why_this_match="Optimal courier proximity and high recipient capacity",
        status="PROPOSED",
        created_at=now_dt,
    )
    create_match(match_obj, conn=test_db)
    loaded_match = get_match("m_test", conn=test_db)
    assert loaded_match is not None
    assert loaded_match.score == 0.88
    assert loaded_match.score_breakdown["urgency"] == 0.35

    # 6. OutboxMessage
    msg = OutboxMessage(
        id="msg_test",
        recipient_or_volunteer_id=vol.id,
        role="volunteer",
        language="kn",
        message_text="Pickup ready at Yelahanka Campus Mess",
        status="queued",
        created_at=now_dt,
        match_id=match_obj.id,
    )
    create_outbox_message(msg, conn=test_db)
    loaded_msg = get_outbox_message("msg_test", conn=test_db)
    assert loaded_msg is not None
    assert loaded_msg.role == "volunteer"
    assert loaded_msg.language == "kn"

    # 7. AuditEvent
    audit = AuditEvent(
        id="aud_test",
        timestamp=now_dt,
        event_type="LISTING_CREATED",
        entity_type="listing",
        entity_id=listing.id,
        payload={"tier": "HUMAN"},
        prev_hash="0000000000000000000000000000000000000000000000000000000000000000",
        hash="abcd1234ef5678",
    )
    create_audit_event(audit, conn=test_db)

    # 8. HistoricalListing
    hist = HistoricalListing(
        id="h_test",
        donor_id=donor.id,
        date="2026-10-18",
        weekday="Sunday",
        category="cooked_rice",
        dish_name="Steamed Sona Masoori Rice",
        portions=115,
        tier_rescued="HUMAN",
        outcome="DELIVERED",
    )
    create_historical_listing(hist, conn=test_db)
    hist_list = list_historical_listings(donor_id=donor.id, conn=test_db)
    assert len(hist_list) == 1
    assert hist_list[0].portions == 115


def test_seed_is_deterministic(tmp_path: Path):
    """Assert that running seed with the same fixed seed produces identical databases."""
    db1 = tmp_path / "seed1.db"
    db2 = tmp_path / "seed2.db"

    seed_database(conn_or_path=str(db1), reset=True, seed=42)
    seed_database(conn_or_path=str(db2), reset=True, seed=42)

    conn1 = get_connection(db1)
    conn2 = get_connection(db2)

    donors1 = list_donors(conn1)
    donors2 = list_donors(conn2)
    assert [d.model_dump() for d in donors1] == [d.model_dump() for d in donors2]

    recips1 = list_recipients(conn=conn1)
    recips2 = list_recipients(conn=conn2)
    assert [r.model_dump() for r in recips1] == [r.model_dump() for r in recips2]

    vols1 = list_volunteers(conn=conn1)
    vols2 = list_volunteers(conn=conn2)
    assert [v.model_dump() for v in vols1] == [v.model_dump() for v in vols2]

    hist1 = list_historical_listings(conn=conn1)
    hist2 = list_historical_listings(conn=conn2)
    assert [h.model_dump() for h in hist1] == [h.model_dump() for h in hist2]

    conn1.close()
    conn2.close()


def test_coordinates_in_bengaluru_bounding_box():
    """Assert all synthetic donors, recipients, and volunteers fall within North Bengaluru bounds."""
    # North Bengaluru bounding envelope:
    min_lat, max_lat = 13.00, 13.25
    min_lon, max_lon = 77.50, 77.70

    for d in get_seed_donors():
        assert min_lat <= d.lat <= max_lat, f"Donor {d.name} lat {d.lat} outside bounds"
        assert min_lon <= d.lon <= max_lon, f"Donor {d.name} lon {d.lon} outside bounds"

    for r in get_seed_recipients():
        assert min_lat <= r.lat <= max_lat, f"Recipient {r.name} lat {r.lat} outside bounds"
        assert min_lon <= r.lon <= max_lon, f"Recipient {r.name} lon {r.lon} outside bounds"

    for v in get_seed_volunteers():
        assert min_lat <= v.lat <= max_lat, f"Volunteer {v.name} lat {v.lat} outside bounds"
        assert min_lon <= v.lon <= max_lon, f"Volunteer {v.name} lon {v.lon} outside bounds"


def test_planted_weekday_pattern_hostel_sunday_rice(tmp_path: Path):
    """Assert that Sunday hostel rice surplus portions exceed the weekday mean."""
    db_file = tmp_path / "pattern_test.db"
    seed_database(conn_or_path=str(db_file), reset=True, seed=42)

    conn = get_connection(db_file)
    hist = list_historical_listings(donor_id="donor_01", conn=conn)
    conn.close()

    # Filter for cooked_rice entries from donor_01
    rice_entries = [h for h in hist if h.category == "cooked_rice"]
    assert len(rice_entries) > 0

    sunday_portions = [h.portions for h in rice_entries if h.weekday == "Sunday"]
    weekday_portions = [
        h.portions for h in rice_entries
        if h.weekday in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
    ]

    assert len(sunday_portions) > 0
    assert len(weekday_portions) > 0

    sunday_mean = sum(sunday_portions) / len(sunday_portions)
    weekday_mean = sum(weekday_portions) / len(weekday_portions)

    # Sunday rice portions must distinctly exceed weekday average
    assert sunday_mean > weekday_mean * 1.5, (
        f"Planted pattern check failed: Sunday mean ({sunday_mean:.1f}) "
        f"not significantly greater than weekday mean ({weekday_mean:.1f})"
    )
    assert sunday_mean >= 90.0
    assert weekday_mean <= 50.0


def test_recipient_composition(tmp_path: Path):
    """Verify composition: 6 Human (at least 1 veg_only), 3 Animal, 3 Compost."""
    recipients = get_seed_recipients()
    assert len(recipients) == 12

    human = [r for r in recipients if r.tier == Tier.HUMAN]
    animal = [r for r in recipients if r.tier == Tier.ANIMAL]
    compost = [r for r in recipients if r.tier == Tier.COMPOST]

    assert len(human) == 6
    assert len(animal) == 3
    assert len(compost) == 3

    # Check for veg_only recipient
    veg_only_recipients = [r for r in human if r.veg_only]
    assert len(veg_only_recipients) >= 1
