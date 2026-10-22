"""Unit tests for Phase 6: Multilingual Messaging and Accept/Decline Loop."""

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
    create_match,
    create_recipient,
    create_volunteer,
    get_connection,
    get_listing,
    get_match,
    get_outbox_message,
    init_db,
    list_matches,
    list_outbox_messages,
)
from app.llm.client import LLMClient
from app.llm.messages import (
    SUPPORTED_LANGUAGES,
    compose_message,
    extract_message_context,
    format_address_reveal,
    get_template_message,
    preview_messages_for_match,
    queue_match_notifications,
    simulate_response,
    validate_message,
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
    Match,
    OutboxMessage,
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
def messaging_db(tmp_path: Path) -> sqlite3.Connection:
    """Create fresh isolated SQLite database with test network."""
    db_file = tmp_path / "test_messaging.db"
    conn = get_connection(db_file)
    init_db(conn)

    # 1. Test Donor in Yelahanka
    donor = Donor(
        id="d_mess_01",
        name="Presidency Canteen North",
        kind=DonorKind.CANTEEN,
        lat=13.1000,
        lon=77.5950,
        area="Yelahanka",
        typical_surplus_portions=50,
        active_hours="07:00-22:00",
    )
    create_donor(donor, conn=conn)

    # 2. Test Volunteer (Kannada speaker)
    volunteer = Volunteer(
        id="v_courier_01",
        name="Suresh Volunteer",
        lat=13.0980,
        lon=77.5920,
        vehicle=VehicleType.BIKE,
        max_portions_carry=60,
        languages=["kn", "en"],
        available=True,
        reliability=0.95,
    )
    create_volunteer(volunteer, conn=conn)

    # 3. Test Recipient (Hindi speaker)
    recipient = Recipient(
        id="r_shelter_01",
        name="Ashraya Care Center",
        tier=Tier.HUMAN,
        lat=13.1050,
        lon=77.5980,
        area="Yelahanka",
        capacity_portions_per_day=100,
        veg_only=True,
        open_from="08:00",
        open_to="21:00",
        languages=["hi", "en"],
        accepts_cooked=True,
    )
    create_recipient(recipient, conn=conn)

    # 4. Secondary Recipient for Escalation
    recipient2 = Recipient(
        id="r_shelter_02",
        name="Sneha Seva Home",
        tier=Tier.HUMAN,
        lat=13.1080,
        lon=77.6000,
        area="Yelahanka",
        capacity_portions_per_day=80,
        veg_only=False,
        open_from="08:00",
        open_to="21:00",
        languages=["en"],
        accepts_cooked=True,
    )
    create_recipient(recipient2, conn=conn)

    return conn


def create_sample_listing(
    donor_id: str = "d_mess_01",
    portions: int = 45,
    safe_hours: int = 3,
    created_at: Optional[datetime] = None,
) -> Listing:
    """Helper to create test food listing with valid FoodPassport and SafetyVerdict."""
    sim_now = created_at or datetime(2026, 10, 22, 12, 0, 0)
    passport = FoodPassport(
        dishes=[
            Dish(
                name="Sambar Rice",
                category=DishCategory.COOKED_RICE,
                veg_status=VegStatus.VEG,
                est_portions=portions,
                visible_flags=[VisibleFlag.NONE],
                confidence=0.96,
            )
        ],
        allergen_hints=[AllergenHint.DAIRY],
        photo_quality_ok=True,
        photo_quality_note="High clarity",
        needs_human_check=False,
        handling=DonorForm(
            cooked_at="11:30:00",
            storage=StorageCondition.AMBIENT,
            est_portions=portions,
            container=ContainerType.COVERED,
        ),
    )
    verdict = SafetyVerdict(
        safe_until=sim_now + timedelta(hours=safe_hours),
        minutes_left=safe_hours * 60,
        risk_level=RiskLevel.LOW,
        human_ok=True,
        reasons=["Safe food holding parameters satisfied"],
        citations=[],
        requires_human_check=False,
    )
    return Listing(
        id=f"list_{uuid.uuid4().hex[:8]}",
        donor_id=donor_id,
        created_at=sim_now,
        passport=passport.model_dump(),
        safety=verdict.model_dump(),
        tier=Tier.HUMAN_RESCUE,
        status=ListingStatus.LISTED,
        expires_at=verdict.safe_until,
    )


# ---------------- 1. Multilingual Combinations & Word Limits ----------------

def test_all_six_role_language_combinations():
    """Verify all 6 role x language combinations render and strictly adhere to word limits."""
    roles = ["recipient", "volunteer"]
    languages = ["en", "kn", "hi"]

    for role in roles:
        for lang in languages:
            msg = get_template_message(
                role=role,
                language=lang,
                portions=45,
                dishes="Sambar Rice",
                veg_status_code="veg",
                allergen_hints=["dairy"],
                safe_until="03:00 PM",
                area="Yelahanka",
            )
            assert isinstance(msg, str)
            word_count = len(msg.split())
            # Strictly under 60 words
            assert word_count < 60, f"Role {role} in {lang} exceeded 60 words: {word_count} words"
            # Must contain reply action keywords
            assert "ACCEPT" in msg
            assert "DECLINE" in msg
            # Must mention safe-until time and portions
            assert "03:00 PM" in msg
            assert "45" in msg
            # Must mention area
            assert "Yelahanka" in msg

            # Recipient role must contain allergen disclaimer
            if role == "recipient":
                assert any(
                    disclaimer in msg
                    for disclaimer in ["visual guess only", "ದೃಶ್ಯ ಅಂದಾಜು ಮಾತ್ರ", "केवल दृश्य अनुमान"]
                )


# ---------------- 2. Privacy & Address Concealment ----------------

def test_privacy_preservation_pre_acceptance():
    """Verify exact donor organization name is concealed and only locality area is revealed."""
    donor_name = "Presidency Canteen North"
    donor_area = "Yelahanka"

    for role in ["recipient", "volunteer"]:
        for lang in SUPPORTED_LANGUAGES:
            msg = compose_message(
                role=role,
                language=lang,
                donor_area=donor_area,
                exact_donor_name=donor_name,
            )
            # Area must be visible
            assert donor_area in msg
            # Exact donor name must NOT appear
            assert donor_name not in msg


# ---------------- 3. Post-Check Validation Guardrails ----------------

def test_post_check_validation_catches_doctored_content():
    """Verify post-check catches fabricated portions, excessive length, privacy leaks, and missing actions."""
    valid_text = (
        "TickTock Meals: Urgent food rescue! 45 portions Sambar Rice (Pure Veg). "
        "Allergens: dairy (visual guess only). Safe until: 03:00 PM. Pickup Area: Yelahanka. Reply ACCEPT or DECLINE."
    )

    # 1. Valid text passes
    valid, reason = validate_message(
        valid_text,
        expected_portions=45,
        expected_safe_until="03:00 PM",
        expected_area="Yelahanka",
        exact_donor_name="Presidency Canteen North",
        role="recipient",
    )
    assert valid is True
    assert reason == "Valid"

    # 2. Fabricated portion count rejected
    fake_portion = valid_text.replace("45 portions", "999 portions")
    valid_p, reason_p = validate_message(
        fake_portion,
        expected_portions=45,
        expected_safe_until="03:00 PM",
        expected_area="Yelahanka",
        role="recipient",
    )
    assert valid_p is False
    assert "Portion consistency mismatch" in reason_p

    # 3. Privacy leak (exposed donor name) rejected
    leaked_text = valid_text.replace("Pickup Area: Yelahanka", "Pickup Area: Yelahanka at Presidency Canteen North")
    valid_l, reason_l = validate_message(
        leaked_text,
        expected_portions=45,
        expected_safe_until="03:00 PM",
        expected_area="Yelahanka",
        exact_donor_name="Presidency Canteen North",
        role="recipient",
    )
    assert valid_l is False
    assert "Privacy violation" in reason_l

    # 4. Missing action keywords rejected
    no_actions = valid_text.replace("Reply ACCEPT or DECLINE.", "Thank you.")
    valid_a, reason_a = validate_message(
        no_actions,
        expected_portions=45,
        expected_safe_until="03:00 PM",
        expected_area="Yelahanka",
        role="recipient",
    )
    assert valid_a is False
    assert "Missing required ACCEPT or DECLINE" in reason_a

    # 5. Over 60 words rejected
    long_text = valid_text + " " + "extra " * 50
    valid_len, reason_len = validate_message(
        long_text,
        expected_portions=45,
        expected_safe_until="03:00 PM",
        expected_area="Yelahanka",
        role="recipient",
    )
    assert valid_len is False
    assert "exceeds 60 words" in reason_len


def test_compose_message_falls_back_to_template_on_invalid_llm():
    """Verify compose_message automatically falls back to template when LLM hallucinates."""
    class BadLLMClient:
        mode = "live"
        config = LLMClient().config

        def generate_text(self, *args, **kwargs) -> str:
            # Hallucinates wrong portions and misses ACCEPT/DECLINE
            return "Here is 888 portions of food. Pickup now."

    bad_client = BadLLMClient()
    fallback = compose_message(
        role="recipient",
        language="en",
        donor_area="Yelahanka",
        client=bad_client,  # type: ignore
    )
    # Must have rejected hallucination and returned verified template with ACCEPT and DECLINE
    assert "ACCEPT" in fallback
    assert "DECLINE" in fallback
    assert "888" not in fallback


# ---------------- 4. Outbox Queueing & Persistence ----------------

def test_queue_match_notifications(messaging_db: sqlite3.Connection):
    """Verify outbox messages are queued for coordinator and courier in their preferred languages."""
    listing = create_sample_listing()
    create_listing(listing, conn=messaging_db)

    match = Match(
        id="m_queue_test",
        listing_id=listing.id,
        recipient_id="r_shelter_01",  # Prefers Hindi
        volunteer_id="v_courier_01",  # Prefers Kannada
        score=0.92,
        status="PROPOSED",
        created_at=datetime(2026, 10, 22, 12, 0, 0),
    )
    create_match(match, conn=messaging_db)

    queued = queue_match_notifications(match, conn=messaging_db)
    assert len(queued) == 2

    # Check recipient message (Hindi)
    recip_msg = next(m for m in queued if m.role == "recipient")
    assert recip_msg.language == "hi"
    assert recip_msg.recipient_or_volunteer_id == "r_shelter_01"
    assert recip_msg.status == "delivered_sim"
    assert "ACCEPT" in recip_msg.message_text

    # Check volunteer message (Kannada)
    vol_msg = next(m for m in queued if m.role == "volunteer")
    assert vol_msg.language == "kn"
    assert vol_msg.recipient_or_volunteer_id == "v_courier_01"
    assert vol_msg.status == "delivered_sim"
    assert "ACCEPT" in vol_msg.message_text

    # Verify retrievable via DB
    loaded = get_outbox_message(recip_msg.id, conn=messaging_db)
    assert loaded is not None
    assert loaded.id == recip_msg.id


# ---------------- 5. Accept Loop & Address Unlock ----------------

def test_simulate_response_accept_unlocks_address(messaging_db: sqlite3.Connection):
    """Accepting a match transitions listing to ACCEPTED and unlocks exact address for courier."""
    listing = create_sample_listing()
    create_listing(listing, conn=messaging_db)

    match = Match(
        id="m_accept_test",
        listing_id=listing.id,
        recipient_id="r_shelter_01",
        volunteer_id="v_courier_01",
        score=0.90,
        status="PROPOSED",
        created_at=datetime(2026, 10, 22, 12, 0, 0),
    )
    create_match(match, conn=messaging_db)

    messages = queue_match_notifications(match, conn=messaging_db)
    recip_msg = next(m for m in messages if m.role == "recipient")

    # Recipient clicks ACCEPT
    res = simulate_response(
        message_id=recip_msg.id,
        action="ACCEPT",
        conn=messaging_db,
        now=datetime(2026, 10, 22, 12, 5, 0),
    )

    assert res["status"] == "ACCEPTED"
    assert res["match_id"] == match.id
    assert "Presidency Canteen North" in res["exact_pickup_address"]
    assert "Yelahanka" in res["exact_pickup_address"]

    # Verify listing status transitioned to ACCEPTED
    updated_listing = get_listing(listing.id, conn=messaging_db)
    assert updated_listing is not None
    assert updated_listing.status == ListingStatus.ACCEPTED

    # Verify match status transitioned to ACCEPTED
    updated_match = get_match(match.id, conn=messaging_db)
    assert updated_match is not None
    assert updated_match.status == "ACCEPTED"
    assert updated_match.responded_at is not None

    # Verify confirmation address dispatch message was created for the courier volunteer
    all_outbox = list_outbox_messages(conn=messaging_db)
    vol_unlock_msgs = [
        m for m in all_outbox
        if m.role == "volunteer" and "unlocked" in m.message_text.lower()
    ]
    assert len(vol_unlock_msgs) == 1
    assert "Presidency Canteen North" in vol_unlock_msgs[0].message_text


# ---------------- 6. Decline Loop & Candidate Escalation ----------------

def test_simulate_response_decline_triggers_escalation(messaging_db: sqlite3.Connection):
    """Declining a dispatch escalates to the next candidate and queues new notifications."""
    sim_now = datetime(2026, 10, 22, 12, 0, 0)
    listing = create_sample_listing(created_at=sim_now)
    create_listing(listing, conn=messaging_db)

    # Initial match with r_shelter_01
    match = Match(
        id="m_decline_test",
        listing_id=listing.id,
        recipient_id="r_shelter_01",
        volunteer_id="v_courier_01",
        score=0.91,
        status="PROPOSED",
        created_at=sim_now,
    )
    create_match(match, conn=messaging_db)

    messages = queue_match_notifications(match, conn=messaging_db, now=sim_now)
    recip_msg = next(m for m in messages if m.role == "recipient")

    # Recipient DECLINES
    res = simulate_response(
        message_id=recip_msg.id,
        action="DECLINE",
        conn=messaging_db,
        now=sim_now + timedelta(minutes=5),
    )

    assert res["status"] == "DECLINED"
    assert res["escalated"] is True
    # Verify escalated match was generated for secondary recipient
    new_match = res["new_match"]
    assert new_match is not None
    assert new_match.recipient_id == "r_shelter_02"
    assert new_match.id != match.id

    # Verify new notifications were queued
    assert len(res["new_messages"]) >= 1


# ---------------- 7. Timeout Response & UI Preview Helper ----------------

def test_simulate_response_timeout(messaging_db: sqlite3.Connection):
    """Timeout response marks outbox as timed_out and escalates."""
    sim_now = datetime(2026, 10, 22, 12, 0, 0)
    listing = create_sample_listing(created_at=sim_now)
    create_listing(listing, conn=messaging_db)

    match = Match(
        id="m_timeout_test",
        listing_id=listing.id,
        recipient_id="r_shelter_01",
        volunteer_id="v_courier_01",
        score=0.89,
        status="PROPOSED",
        created_at=sim_now,
    )
    create_match(match, conn=messaging_db)

    messages = queue_match_notifications(match, conn=messaging_db, now=sim_now)
    recip_msg = next(m for m in messages if m.role == "recipient")

    res = simulate_response(
        message_id=recip_msg.id,
        action="TIMEOUT",
        conn=messaging_db,
        now=sim_now + timedelta(minutes=15),
    )
    assert res["status"] == "TIMED_OUT"
    assert res["escalated"] is True


def test_preview_messages_helper(messaging_db: sqlite3.Connection):
    """Verify preview_messages_for_match returns all languages for both roles."""
    listing = create_sample_listing()
    create_listing(listing, conn=messaging_db)

    match = Match(
        id="m_preview_test",
        listing_id=listing.id,
        recipient_id="r_shelter_01",
        volunteer_id="v_courier_01",
        score=0.88,
        status="PROPOSED",
        created_at=datetime(2026, 10, 22, 12, 0, 0),
    )
    create_match(match, conn=messaging_db)

    previews = preview_messages_for_match(match, conn=messaging_db)
    assert "recipient" in previews
    assert "volunteer" in previews
    for lang in ["en", "kn", "hi"]:
        assert lang in previews["recipient"]
        assert lang in previews["volunteer"]
        assert len(previews["recipient"][lang]) > 0
        assert len(previews["volunteer"][lang]) > 0
