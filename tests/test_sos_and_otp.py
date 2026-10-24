"""Unit and integration tests for Community SOS hunger requests and OTP handover verification."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
import pytest

from app.db import (
    create_donor,
    create_listing,
    create_match,
    create_sos_request,
    get_connection,
    get_match,
    get_sos_request,
    init_db,
    list_sos_requests,
    update_match,
    update_sos_request,
)
from app.models import (
    Dish,
    DishCategory,
    Donor,
    DonorKind,
    Listing,
    ListingStatus,
    Match,
    SOSRequest,
    SOSStatus,
    SOSUrgency,
    Tier,
    VegStatus,
)
from app.seed import get_seed_sos_requests, seed_database


@pytest.fixture
def memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    yield conn
    conn.close()


def test_sos_request_creation_and_retrieval(memory_db):
    now = datetime(2026, 10, 24, 12, 0, 0)
    sos = SOSRequest(
        id="sos_test_01",
        requester_name="Hope Destitute Center",
        requester_type="shelter",
        portions_needed=50,
        veg_only=True,
        address="Sector 2, Yelahanka",
        lat=13.1005,
        lon=77.5950,
        needed_by=now + timedelta(hours=2),
        urgency=SOSUrgency.CRITICAL,
        status=SOSStatus.OPEN,
        contact_phone="9845011111",
        notes="High protein preferred",
        created_at=now,
    )

    created = create_sos_request(sos, conn=memory_db)
    assert created.id == "sos_test_01"

    fetched = get_sos_request("sos_test_01", conn=memory_db)
    assert fetched is not None
    assert fetched.requester_name == "Hope Destitute Center"
    assert fetched.portions_needed == 50
    assert fetched.urgency == SOSUrgency.CRITICAL
    assert fetched.status == SOSStatus.OPEN


def test_sos_request_update_status(memory_db):
    now = datetime(2026, 10, 24, 12, 0, 0)
    sos = SOSRequest(
        id="sos_test_02",
        requester_name="Sneha Orphanage",
        requester_type="orphanage",
        portions_needed=35,
        veg_only=False,
        address="Hebbal Ring Road",
        lat=13.0350,
        lon=77.5970,
        needed_by=now + timedelta(hours=3),
        urgency=SOSUrgency.HIGH,
        status=SOSStatus.OPEN,
        contact_phone="9845022222",
        created_at=now,
    )
    create_sos_request(sos, conn=memory_db)

    # Update via string ID and status enum
    updated = update_sos_request("sos_test_02", status=SOSStatus.MATCHED, conn=memory_db)
    assert updated is not None
    assert updated.status == SOSStatus.MATCHED

    # Re-fetch
    refetched = get_sos_request("sos_test_02", conn=memory_db)
    assert refetched.status == SOSStatus.MATCHED


def test_match_with_otp_and_sos_link(memory_db):
    now = datetime(2026, 10, 24, 12, 0, 0)
    match = Match(
        id="m_test_otp",
        listing_id="list_test",
        recipient_id="recip_01",
        volunteer_id="vol_01",
        score=0.95,
        status="PROPOSED",
        otp_code="7890",
        otp_verified=False,
        sos_id="sos_test_01",
        created_at=now,
    )

    create_match(match, conn=memory_db)

    fetched = get_match("m_test_otp", conn=memory_db)
    assert fetched is not None
    assert fetched.otp_code == "7890"
    assert fetched.otp_verified is False
    assert fetched.sos_id == "sos_test_01"

    # Simulate OTP verification
    fetched.otp_verified = True
    fetched.status = "DELIVERED"
    update_match(fetched, conn=memory_db)

    verified = get_match("m_test_otp", conn=memory_db)
    assert verified.otp_verified is True
    assert verified.status == "DELIVERED"


def test_seed_includes_sos_hotspots(memory_db):
    seed_database(conn_or_path=memory_db, reset=True, seed=42)
    sos_items = list_sos_requests(conn=memory_db)
    assert len(sos_items) >= 5

    names = [s.requester_name for s in sos_items]
    assert "Shanti Bhavan Children Home" in names
    assert any("Worker Camp" in n for n in names)


def test_dish_nutritional_attributes():
    dish = Dish(
        name="Lentil Khichdi & Mixed Veg",
        category=DishCategory.COOKED_RICE,
        veg_status=VegStatus.VEG,
        est_portions=40,
        est_calories_per_portion=380,
        est_protein_g=14.5,
        confidence=0.98,
    )
    assert dish.est_calories_per_portion == 380
    assert dish.est_protein_g == 14.5
    assert dish.est_portions * dish.est_calories_per_portion == 15200
