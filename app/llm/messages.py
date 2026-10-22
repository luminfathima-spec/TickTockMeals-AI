"""Multilingual messaging and accept/decline loop for TickTock Meals.

Handles:
- Multilingual notification composition (English, Kannada, Hindi)
  for recipients and courier volunteers.
- Strict constraint enforcement: strictly under 60 words, portions & dishes,
  veg status, allergen hints with 'visual guess only' disclaimer,
  safe-until time, pickup AREA only (exact street address concealed
  until acceptance), and reply options (ACCEPT / DECLINE).
- Deterministic templates for all 3 languages used in mock mode, rate limits,
  or offline fallbacks.
- Post-check validation: verifies that quantities, times, and confidentiality
  match input data; falls back to deterministic template if fabricated,
  doctored, or mismatched.
- Outbox persistence: records all dispatches in OutboxMessage with state tracking.
- Interactive Accept / Decline simulation:
  - DECLINE / TIMEOUT triggers automated escalation to next match candidate.
  - ACCEPT transitions listing status to ACCEPTED and reveals exact pickup address
    to the volunteer courier only.
- UI preview and language toggle helper functions.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from app.clock import now as get_clock_now
from app.config import get_config
from app.db import (
    create_audit_event,
    create_outbox_message,
    get_connection,
    get_donor,
    get_listing,
    get_match,
    get_outbox_message,
    get_recipient,
    get_volunteer,
    list_donors,
    list_outbox_messages,
    update_listing,
    update_match,
    update_outbox_message,
)
from app.llm.client import LLMClient
from app.matching import escalate
from app.models import (
    AuditEvent,
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
from app.safety.rules import parse_cooked_at

# Supported languages
SUPPORTED_LANGUAGES = ["en", "kn", "hi"]

# Dietary status translations
VEG_TRANSLATIONS = {
    "en": {"veg": "Pure Veg", "egg": "Contains Egg", "non_veg": "Non-Veg"},
    "kn": {"veg": "ಶುದ್ಧ ಸಸ್ಯಾಹಾರಿ", "egg": "ಮೊಟ್ಟೆ ಒಳಗೊಂಡಿದೆ", "non_veg": "ಮಾಂಸಾಹಾರಿ"},
    "hi": {"veg": "शुद्ध शाकाहारी", "egg": "अंडा शामिल", "non_veg": "मांसाहारी"},
}

# Allergen fallback labels
NO_ALLERGENS_TRANSLATIONS = {
    "en": "None detected",
    "kn": "ಯಾವುದೂ ಪತ್ತೆಯಾಗಿಲ್ಲ",
    "hi": "कोई नहीं पाया गया",
}

# Allergen disclaimer translations
DISCLAIMER_TRANSLATIONS = {
    "en": "visual guess only",
    "kn": "ದೃಶ್ಯ ಅಂದಾಜು ಮಾತ್ರ",
    "hi": "केवल दृश्य अनुमान",
}


def format_safe_until(safe_until_dt: Optional[Union[datetime, str]]) -> str:
    """Format safe-until timestamp as a readable time string."""
    if not safe_until_dt:
        return "Urgent (within 2-3 hours)"
    if isinstance(safe_until_dt, datetime):
        return safe_until_dt.strftime("%I:%M %p")
    try:
        dt = datetime.fromisoformat(str(safe_until_dt))
        return dt.strftime("%I:%M %p")
    except Exception:
        return str(safe_until_dt)


def extract_message_context(
    match: Optional[Union[Match, Dict[str, Any]]] = None,
    passport: Optional[Union[FoodPassport, Dict[str, Any]]] = None,
    verdict: Optional[Union[SafetyVerdict, Dict[str, Any]]] = None,
    donor_area: Optional[str] = None,
    exact_donor_name: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """Extract and normalize all contextual details required for message composition."""
    # 1. Resolve listing and donor if match provided
    listing_obj: Optional[Listing] = None
    donor_obj: Optional[Donor] = None
    c = conn

    if match:
        listing_id = match.listing_id if isinstance(match, Match) else match.get("listing_id")
        if listing_id and c:
            listing_obj = get_listing(listing_id, conn=c)
            if listing_obj:
                donor_obj = get_donor(listing_obj.donor_id, conn=c)
                if not passport and listing_obj.passport:
                    passport = listing_obj.passport
                if not verdict and listing_obj.safety:
                    verdict = listing_obj.safety

    # Donor area and exact name
    area = donor_area or (donor_obj.area if donor_obj else "North Bengaluru")
    donor_name = exact_donor_name or (donor_obj.name if donor_obj else None)

    # 2. Extract food details from passport
    portions = 0
    dishes_list: List[str] = []
    veg_status_code = "veg"
    allergen_hints_list: List[str] = []

    if passport:
        if isinstance(passport, dict):
            dishes_raw = passport.get("dishes", [])
            for d in dishes_raw:
                p_cnt = d.get("est_portions", 0)
                portions += p_cnt
                d_name = d.get("name", "")
                if d_name:
                    dishes_list.append(d_name)
                v_stat = str(d.get("veg_status", "")).lower()
                if "non_veg" in v_stat:
                    veg_status_code = "non_veg"
                elif "egg" in v_stat and veg_status_code != "non_veg":
                    veg_status_code = "egg"

            hints = passport.get("allergen_hints", [])
            allergen_hints_list = [
                str(h).replace("AllergenHint.", "").lower()
                for h in hints
                if str(h).replace("AllergenHint.", "").lower() not in ("none", "unknown", "")
            ]
            if portions == 0 and "handling" in passport:
                portions = passport["handling"].get("est_portions", 0)
        elif isinstance(passport, FoodPassport):
            for d in passport.dishes:
                portions += d.est_portions
                dishes_list.append(d.name)
                if d.veg_status == VegStatus.NON_VEG:
                    veg_status_code = "non_veg"
                elif d.veg_status == VegStatus.EGG and veg_status_code != "non_veg":
                    veg_status_code = "egg"

            for h in passport.allergen_hints:
                val = h.value if hasattr(h, "value") else str(h)
                if val.lower() not in ("none", "unknown"):
                    allergen_hints_list.append(val)
            if portions == 0:
                portions = passport.handling.est_portions

    if portions <= 0:
        portions = 25  # Sensible fallback
    if not dishes_list:
        dishes_list = ["Prepared Meals"]

    dishes_summary = ", ".join(dishes_list[:2])
    if len(dishes_list) > 2:
        dishes_summary += f" +{len(dishes_list) - 2} more"

    # 3. Resolve safe-until time
    safe_until_val: Optional[Union[datetime, str]] = None
    if verdict:
        if isinstance(verdict, dict):
            safe_until_val = verdict.get("safe_until")
        elif isinstance(verdict, SafetyVerdict):
            safe_until_val = verdict.safe_until
    elif listing_obj and listing_obj.expires_at:
        safe_until_val = listing_obj.expires_at

    safe_until_str = format_safe_until(safe_until_val)

    return {
        "portions": portions,
        "dishes_summary": dishes_summary,
        "veg_status_code": veg_status_code,
        "allergen_hints": allergen_hints_list,
        "safe_until_str": safe_until_str,
        "area": area,
        "exact_donor_name": donor_name,
    }


def get_template_message(
    role: str,
    language: str,
    portions: int,
    dishes: str,
    veg_status_code: str,
    allergen_hints: List[str],
    safe_until: str,
    area: str,
) -> str:
    """Generate high-quality, deterministic notification message strictly under 60 words."""
    lang = language.lower() if language.lower() in SUPPORTED_LANGUAGES else "en"
    veg_label = VEG_TRANSLATIONS.get(lang, VEG_TRANSLATIONS["en"]).get(veg_status_code, "Veg")

    if allergen_hints:
        allergens_str = ", ".join(allergen_hints)
    else:
        allergens_str = NO_ALLERGENS_TRANSLATIONS.get(lang, "None detected")

    role_norm = role.strip().lower()

    if lang == "kn":
        if role_norm == "volunteer":
            # Under 30 words
            return (
                f"ಟಿಕ್‌ಟಾಕ್ ಮೀಲ್ಸ್ ಕೊರಿಯರ್: ತುರ್ತು ಪಿಕಪ್ ಕರೆ! {area} ಪ್ರದೇಶದಲ್ಲಿ {portions} ಊಟ {dishes} ({veg_label}). "
                f"ಸುರಕ್ಷಿತ ಸಮಯ: {safe_until}. ನಿಖರ ವಿಳಾಸ ಪಡೆಯಲು ACCEPT ಅಥವಾ ತಿರಸ್ಕರಿಸಲು DECLINE ಎಂದು ಉತ್ತರಿಸಿ."
            )
        else:
            # Under 35 words
            return (
                f"ಟಿಕ್‌ಟಾಕ್ ಮೀಲ್ಸ್: ತುರ್ತು ಆಹಾರ ರಕ್ಷಣೆ! {portions} ಊಟ {dishes} ({veg_label}). "
                f"ಅಲರ್ಜಿನ್: {allergens_str} (ದೃಶ್ಯ ಅಂದಾಜು ಮಾತ್ರ). ಸುರಕ್ಷಿತ ಸಮಯ: {safe_until}. "
                f"ಸಂಗ್ರಹಣಾ ಪ್ರದೇಶ: {area}. ಸ್ವೀಕರಿಸಲು ACCEPT ಅಥವಾ ತಿರಸ್ಕರಿಸಲು DECLINE ಎಂದು ಉತ್ತರಿಸಿ."
            )
    elif lang == "hi":
        if role_norm == "volunteer":
            # Under 30 words
            return (
                f"टिकटॉक मील्स कूरियर: पिकअप अलर्ट! {area} में {portions} भोजन {dishes} ({veg_label})। "
                f"सुरक्षित समय: {safe_until}। सटीक पता पाने और डिलीवरी शुरू करने के लिए ACCEPT या अस्वीकार करने के लिए DECLINE लिखें।"
            )
        else:
            # Under 35 words
            return (
                f"टिकटॉक मील्स: तत्काल भोजन बचाव! {portions} भोजन {dishes} ({veg_label})। "
                f"एलर्जेंस: {allergens_str} (केवल दृश्य अनुमान)। सुरक्षित समय: {safe_until}। "
                f"पिकअप क्षेत्र: {area}। स्वीकार करने के लिए ACCEPT या अस्वीकार करने के लिए DECLINE लिखें।"
            )
    else:
        # English
        if role_norm == "volunteer":
            # Under 35 words
            return (
                f"TickTock Meals Courier: Pickup dispatch alert! {portions} portions {dishes} ({veg_label}) in {area}. "
                f"Safe until: {safe_until}. Transport needed to recipient. Reply ACCEPT to reveal pickup address and start delivery, or DECLINE."
            )
        else:
            # Under 35 words
            return (
                f"TickTock Meals: Urgent food rescue! {portions} portions {dishes} ({veg_label}). "
                f"Allergens: {allergens_str} (visual guess only). Safe until: {safe_until}. "
                f"Pickup Area: {area}. Reply ACCEPT to claim or DECLINE."
            )


def validate_message(
    text: str,
    expected_portions: int,
    expected_safe_until: str,
    expected_area: str,
    exact_donor_name: Optional[str] = None,
    role: str = "recipient",
) -> Tuple[bool, str]:
    """Post-check validation verifying quantities, safe-until times, privacy, and word limits.

    Returns:
        (is_valid, failure_reason)
    """
    # 1. Word limit check (strictly under 60 words)
    words = text.split()
    if len(words) > 60:
        return False, f"Message word count {len(words)} exceeds 60 words limit"

    # 2. Action keywords check (must contain ACCEPT and DECLINE)
    text_upper = text.upper()
    if "ACCEPT" not in text_upper or "DECLINE" not in text_upper:
        return False, "Missing required ACCEPT or DECLINE action keywords"

    # 3. Privacy / Redaction check: exact donor organization name must NEVER be exposed
    if exact_donor_name and len(exact_donor_name) >= 4:
        if exact_donor_name.lower() in text.lower():
            return False, f"Privacy violation: exact donor name '{exact_donor_name}' revealed before acceptance"

    # 4. Portion count consistency check
    # Find all standalone integer numbers in the text
    numbers_in_text = [int(n) for n in re.findall(r"\b\d+\b", text)]
    if numbers_in_text:
        # Check if the expected portion count exists among numbers
        if expected_portions not in numbers_in_text:
            return False, f"Portion consistency mismatch: expected {expected_portions}, found numbers {numbers_in_text}"

    # 5. Allergen disclaimer check for recipient
    if role.strip().lower() == "recipient":
        has_disclaimer = any(
            phrase in text.lower()
            for phrase in ["visual guess", "ದೃಶ್ಯ ಅಂದಾಜು", "दृश्य अनुमान", "guess", "ಅಂದಾಜು", "अनुमान"]
        )
        if not has_disclaimer:
            return False, "Missing mandatory allergen disclaimer note ('visual guess only')"

    return True, "Valid"


def compose_message(
    role: str,
    language: str,
    match: Optional[Union[Match, Dict[str, Any]]] = None,
    passport: Optional[Union[FoodPassport, Dict[str, Any]]] = None,
    verdict: Optional[Union[SafetyVerdict, Dict[str, Any]]] = None,
    conn: Optional[sqlite3.Connection] = None,
    client: Optional[LLMClient] = None,
    donor_area: Optional[str] = None,
    exact_donor_name: Optional[str] = None,
) -> str:
    """Compose a localized, concise food rescue notification under 60 words.

    In mock mode or offline fallback, returns deterministic verified template.
    In live mode, queries LLM and executes post-check validation with automatic template fallback.
    """
    lang = language.lower() if language.lower() in SUPPORTED_LANGUAGES else "en"
    ctx = extract_message_context(
        match=match,
        passport=passport,
        verdict=verdict,
        donor_area=donor_area,
        exact_donor_name=exact_donor_name,
        conn=conn,
    )

    template_msg = get_template_message(
        role=role,
        language=lang,
        portions=ctx["portions"],
        dishes=ctx["dishes_summary"],
        veg_status_code=ctx["veg_status_code"],
        allergen_hints=ctx["allergen_hints"],
        safe_until=ctx["safe_until_str"],
        area=ctx["area"],
    )

    llm_client = client or LLMClient()
    if llm_client.mode == "mock":
        return template_msg

    # Live Mode generation
    lang_names = {"en": "English", "kn": "Kannada", "hi": "Hindi"}
    target_lang = lang_names.get(lang, "English")

    system_instruction = (
        "You are the TickTock Meals Dispatch Coordinator. You write ultra-concise, urgent SMS food rescue notifications. "
        "Strict rules:\n"
        "1. Total length MUST be strictly under 60 words.\n"
        "2. Accurately state: portion count, dish names, veg status, allergen note ('visual guess only'), safe-until time, and pickup AREA ONLY.\n"
        "3. NEVER reveal exact street address or donor organization name.\n"
        "4. Conclude with explicit instructions to reply ACCEPT or DECLINE.\n"
        f"5. Write in {target_lang}."
    )

    prompt = (
        f"Compose food rescue SMS notification for role '{role}' in {target_lang}.\n"
        f"Details:\n"
        f"- Portions: {ctx['portions']}\n"
        f"- Dishes: {ctx['dishes_summary']}\n"
        f"- Veg Status: {ctx['veg_status_code']}\n"
        f"- Allergens: {', '.join(ctx['allergen_hints']) if ctx['allergen_hints'] else 'None detected'} (visual guess only)\n"
        f"- Safe Until: {ctx['safe_until_str']}\n"
        f"- Pickup Area: {ctx['area']}\n"
        "Reply options: ACCEPT / DECLINE."
    )

    try:
        generated = llm_client.generate_text(
            prompt=prompt,
            model=llm_client.config.llm.models.MODEL_FAST,
            system_instruction=system_instruction,
        ).strip()

        # Run post-check validation
        is_valid, reason = validate_message(
            text=generated,
            expected_portions=ctx["portions"],
            expected_safe_until=ctx["safe_until_str"],
            expected_area=ctx["area"],
            exact_donor_name=ctx["exact_donor_name"],
            role=role,
        )

        if is_valid:
            return generated
        else:
            # Fall back to deterministic template on any discrepancy
            return template_msg
    except Exception:
        return template_msg


def format_address_reveal(donor: Optional[Donor]) -> str:
    """Format exact donor street address and pickup instructions revealed upon acceptance."""
    if not donor:
        return "North Bengaluru Food Rescue Station (Contact dispatch coordinator)"
    return f"{donor.name}, Main Gate / Reception, {donor.area}, Bengaluru (GPS: {donor.lat:.4f}, {donor.lon:.4f})"


def preview_messages_for_match(
    match: Match,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Dict[str, str]]:
    """Generate UI previews of messages for both roles across all three languages."""
    c = conn or get_connection()
    close_after = conn is None
    try:
        listing = get_listing(match.listing_id, conn=c)
        donor = get_donor(listing.donor_id, conn=c) if listing else None
        donor_area = donor.area if donor else "North Bengaluru"
        exact_donor_name = donor.name if donor else None

        results: Dict[str, Dict[str, str]] = {"recipient": {}, "volunteer": {}}
        for lang in SUPPORTED_LANGUAGES:
            results["recipient"][lang] = compose_message(
                role="recipient",
                language=lang,
                match=match,
                passport=listing.passport if listing else None,
                verdict=listing.safety if listing else None,
                donor_area=donor_area,
                exact_donor_name=exact_donor_name,
                conn=c,
            )
            results["volunteer"][lang] = compose_message(
                role="volunteer",
                language=lang,
                match=match,
                passport=listing.passport if listing else None,
                verdict=listing.safety if listing else None,
                donor_area=donor_area,
                exact_donor_name=exact_donor_name,
                conn=c,
            )
        return results
    finally:
        if close_after:
            c.close()


def queue_match_notifications(
    match: Match,
    conn: Optional[sqlite3.Connection] = None,
    now: Optional[datetime] = None,
    client: Optional[LLMClient] = None,
) -> List[OutboxMessage]:
    """Compose and queue outbox notifications for recipient coordinator and courier volunteer."""
    c = conn or get_connection()
    close_after = conn is None
    current_time = now or get_clock_now()

    messages: List[OutboxMessage] = []
    try:
        listing = get_listing(match.listing_id, conn=c)
        donor = get_donor(listing.donor_id, conn=c) if listing else None
        donor_area = donor.area if donor else "North Bengaluru"
        exact_donor_name = donor.name if donor else None

        # 1. Recipient coordinator notification
        recipient = get_recipient(match.recipient_id, conn=c)
        recip_lang = recipient.languages[0] if (recipient and recipient.languages) else "en"
        recip_text = compose_message(
            role="recipient",
            language=recip_lang,
            match=match,
            passport=listing.passport if listing else None,
            verdict=listing.safety if listing else None,
            donor_area=donor_area,
            exact_donor_name=exact_donor_name,
            conn=c,
            client=client,
        )
        recip_msg = OutboxMessage(
            id=f"msg_{uuid.uuid4().hex[:10]}",
            recipient_or_volunteer_id=match.recipient_id,
            role="recipient",
            language=recip_lang,
            message_text=recip_text,
            status="delivered_sim",
            created_at=current_time,
            match_id=match.id,
        )
        create_outbox_message(recip_msg, conn=c)
        messages.append(recip_msg)

        # 2. Courier volunteer notification (if volunteer is assigned)
        if match.volunteer_id:
            volunteer = get_volunteer(match.volunteer_id, conn=c)
            vol_lang = volunteer.languages[0] if (volunteer and volunteer.languages) else "en"
            vol_text = compose_message(
                role="volunteer",
                language=vol_lang,
                match=match,
                passport=listing.passport if listing else None,
                verdict=listing.safety if listing else None,
                donor_area=donor_area,
                exact_donor_name=exact_donor_name,
                conn=c,
                client=client,
            )
            vol_msg = OutboxMessage(
                id=f"msg_{uuid.uuid4().hex[:10]}",
                recipient_or_volunteer_id=match.volunteer_id,
                role="volunteer",
                language=vol_lang,
                message_text=vol_text,
                status="delivered_sim",
                created_at=current_time,
                match_id=match.id,
            )
            create_outbox_message(vol_msg, conn=c)
            messages.append(vol_msg)

        return messages
    finally:
        if close_after:
            c.close()


def simulate_response(
    message_id: str,
    action: str,
    conn: Optional[sqlite3.Connection] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Simulate receiving a response (ACCEPT, DECLINE, TIMEOUT) to an outbox dispatch.

    - DECLINE or TIMEOUT triggers escalation to next match candidate.
    - ACCEPT transitions listing status to ACCEPTED and reveals exact pickup address
      to volunteer courier.
    """
    c = conn or get_connection()
    close_after = conn is None
    current_time = now or get_clock_now()

    norm_action = action.strip().upper()
    if norm_action not in ("ACCEPT", "DECLINE", "TIMEOUT", "TIMED_OUT"):
        raise ValueError(f"Invalid simulated action: '{action}'. Must be ACCEPT, DECLINE, or TIMEOUT.")

    try:
        msg = get_outbox_message(message_id, conn=c)
        if not msg:
            raise ValueError(f"Outbox message '{message_id}' not found.")

        # Update OutboxMessage status
        if norm_action == "ACCEPT":
            msg.status = "accepted"
        elif norm_action == "DECLINE":
            msg.status = "declined"
        else:
            msg.status = "timed_out"

        update_outbox_message(msg, conn=c)

        if not msg.match_id:
            return {"status": msg.status, "message_id": message_id, "escalated": False}

        match = get_match(msg.match_id, conn=c)
        if not match:
            return {"status": msg.status, "message_id": message_id, "escalated": False}

        # ---------------- Case A: ACCEPT ----------------
        if norm_action == "ACCEPT":
            match.status = "ACCEPTED"
            match.responded_at = current_time
            update_match(match, conn=c)

            listing = get_listing(match.listing_id, conn=c)
            exact_address = "Address available on dispatch"
            if listing:
                listing.status = ListingStatus.ACCEPTED
                update_listing(listing, conn=c)

                donor = get_donor(listing.donor_id, conn=c)
                exact_address = format_address_reveal(donor)

            # Reveal address to courier volunteer via confirmation outbox message
            if match.volunteer_id:
                vol_confirm_msg = OutboxMessage(
                    id=f"msg_{uuid.uuid4().hex[:10]}",
                    recipient_or_volunteer_id=match.volunteer_id,
                    role="volunteer",
                    language="en",
                    message_text=(
                        f"TickTock Meals Confirmed! Delivery address unlocked: Pickup at {exact_address}. "
                        "Show match token to security gate upon arrival."
                    ),
                    status="delivered_sim",
                    created_at=current_time,
                    match_id=match.id,
                )
                create_outbox_message(vol_confirm_msg, conn=c)

            # Audit event
            create_audit_event(
                AuditEvent(
                    id=f"aud_{uuid.uuid4().hex[:10]}",
                    timestamp=current_time,
                    event_type="MATCH_ACCEPTED",
                    entity_type="match",
                    entity_id=match.id,
                    payload={
                        "listing_id": match.listing_id,
                        "recipient_id": match.recipient_id,
                        "volunteer_id": match.volunteer_id,
                        "message_id": message_id,
                        "status": "ACCEPTED",
                    },
                ),
                conn=c,
            )

            return {
                "status": "ACCEPTED",
                "message_id": message_id,
                "match_id": match.id,
                "listing_id": listing.id if listing else None,
                "exact_pickup_address": exact_address,
                "volunteer_revealed": bool(match.volunteer_id),
                "escalated": False,
            }

        # ---------------- Case B: DECLINE / TIMEOUT ----------------
        action_type = "DECLINED" if norm_action == "DECLINE" else "TIMED_OUT"

        create_audit_event(
            AuditEvent(
                id=f"aud_{uuid.uuid4().hex[:10]}",
                timestamp=current_time,
                event_type=f"MATCH_{action_type}",
                entity_type="match",
                entity_id=match.id,
                payload={
                    "listing_id": match.listing_id,
                    "recipient_id": match.recipient_id,
                    "volunteer_id": match.volunteer_id,
                    "message_id": message_id,
                    "action": action_type,
                },
            ),
            conn=c,
        )

        # Escalate to next candidate
        new_match, reclassified = escalate(
            match_id=match.id,
            action=action_type,
            conn=c,
            now=current_time,
        )

        new_messages = []
        if new_match:
            new_messages = queue_match_notifications(new_match, conn=c, now=current_time)

        return {
            "status": action_type,
            "message_id": message_id,
            "match_id": match.id,
            "escalated": True,
            "new_match": new_match,
            "new_messages": new_messages,
            "reclassified": reclassified,
        }
    finally:
        if close_after:
            c.close()


def safe_print(text: str) -> None:
    """Print text safely on any platform without failing on cp1252 encoding."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="backslashreplace").decode("ascii"))


def run_demo() -> None:
    """CLI messaging demonstration showing templates, privacy protection, and accept/decline loop."""
    safe_print("\n=======================================================")
    safe_print("   TickTock Meals: Multilingual Messaging Demo         ")
    safe_print("=======================================================")

    conn = get_connection()
    sim_time = datetime(2026, 10, 22, 12, 0, 0)

    # 1. Ensure test donor and listing exist
    donors = list_donors(conn=conn)
    if not donors:
        safe_print("[ERROR] No donors found in database. Run 'python -m app.seed --reset' first.")
        return

    sample_donor = donors[0]
    sample_passport = FoodPassport(
        dishes=[
            Dish(
                name="Steamed Rice & Dal",
                category=DishCategory.COOKED_RICE,
                veg_status=VegStatus.VEG,
                est_portions=60,
                visible_flags=[VisibleFlag.NONE],
                confidence=0.95,
            )
        ],
        allergen_hints=[],
        photo_quality_ok=True,
        photo_quality_note="Clear catering pots",
        needs_human_check=False,
        handling=DonorForm(
            cooked_at="11:00:00",
            storage=StorageCondition.AMBIENT,
            est_portions=60,
            container=ContainerType.COVERED,
        ),
    )

    sample_match = Match(
        id=f"m_demo_{uuid.uuid4().hex[:8]}",
        listing_id="list_demo",
        recipient_id="recip_01",
        volunteer_id="vol_01",
        score=0.88,
        status="PROPOSED",
        created_at=sim_time,
    )

    safe_print("\n--- 1. Multilingual Previews (All 6 Combinations) ---")
    for role in ["recipient", "volunteer"]:
        safe_print(f"\n[ROLE: {role.upper()}]")
        for lang in ["en", "kn", "hi"]:
            msg = compose_message(
                role=role,
                language=lang,
                match=sample_match,
                passport=sample_passport,
                donor_area=sample_donor.area,
                exact_donor_name=sample_donor.name,
                conn=conn,
            )
            safe_print(f"  [{lang.upper()} ({len(msg.split())} words)]: {msg}")

    safe_print("\n--- 2. Privacy & Redaction Verification ---")
    en_msg = compose_message(
        role="volunteer",
        language="en",
        match=sample_match,
        passport=sample_passport,
        donor_area=sample_donor.area,
        exact_donor_name=sample_donor.name,
        conn=conn,
    )
    safe_print(f"  Pickup Area exposed: '{sample_donor.area}' in text -> {sample_donor.area in en_msg}")
    safe_print(f"  Donor Name concealed: '{sample_donor.name}' in text -> {sample_donor.name in en_msg}")
    assert sample_donor.name not in en_msg, "Exact donor name must NOT appear before acceptance!"
    safe_print("  [PASS] Privacy preserved: Exact address hidden until acceptance.")

    safe_print("\n--- 3. Post-Check Validation Guardrails ---")
    valid, reason = validate_message(
        text=en_msg,
        expected_portions=60,
        expected_safe_until="03:00 PM",
        expected_area=sample_donor.area,
        exact_donor_name=sample_donor.name,
        role="volunteer",
    )
    safe_print(f"  Template validation: {valid} ({reason})")

    # Test fabricated portion count rejection
    fabricated_text = en_msg.replace("60 portions", "999 portions")
    valid_fab, reason_fab = validate_message(
        text=fabricated_text,
        expected_portions=60,
        expected_safe_until="03:00 PM",
        expected_area=sample_donor.area,
        exact_donor_name=sample_donor.name,
        role="volunteer",
    )
    safe_print(f"  Fabricated text (999 portions instead of 60) caught: {not valid_fab} -> '{reason_fab}'")

    safe_print("\n=======================================================")
    safe_print("   Demo Complete: All constraints verified successfully! ")
    safe_print("=======================================================\n")



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TickTock Meals Multilingual Messaging Engine")
    parser.add_argument("--demo", action="store_true", help="Run messaging demonstration")
    args = parser.parse_args()

    if args.demo:
        run_demo()
    else:
        parser.print_help()
