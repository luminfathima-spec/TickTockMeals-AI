"""Volunteer verification gate, state machine handover, and QR passport for TickTock Meals.

Handles:
- Strict state machine for ListingStatus (illegal transitions raise ValueError).
- Human verification gate: HUMAN_RESCUE cannot move to PICKED_UP without a complete
  VerificationChecklist (smell/appearance, temperature/hot-holding, clean/covered container,
  labelled with time). A failed check immediately triggers Phase 4 reclassification.
- Animal feed lighter checklist: does not enforce hot-holding or time labelling.
- QR PNG generator for passport verification.
- Public read-only passport page renderer (dishes, veg status, allergen disclaimer,
  countdown, tier, verification status; never includes donor contact details).
- Delivery confirmation storing actual_portions for impact metrics.
- Tamper-evident audit event recording on every step.
"""

from __future__ import annotations

import io
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import qrcode

from app.audit import record_event
from app.clock import now as get_clock_now
from app.db import (
    get_connection,
    get_donor,
    get_listing,
    get_match,
    list_matches,
    update_listing,
    update_match,
)
from app.models import (
    Dish,
    FoodPassport,
    Listing,
    ListingStatus,
    Tier,
    VegStatus,
    VerificationChecklist,
)
from app.triage import reclassify

# Strict state machine transition rules
ALLOWED_TRANSITIONS: Dict[ListingStatus, Set[ListingStatus]] = {
    ListingStatus.LISTED: {
        ListingStatus.VERIFIED,
        ListingStatus.MATCHED,
        ListingStatus.RECLASSIFIED,
        ListingStatus.EXPIRED,
    },
    ListingStatus.VERIFIED: {
        ListingStatus.MATCHED,
        ListingStatus.RECLASSIFIED,
        ListingStatus.EXPIRED,
    },
    ListingStatus.MATCHED: {
        ListingStatus.ACCEPTED,
        ListingStatus.MATCHED,
        ListingStatus.RECLASSIFIED,
        ListingStatus.EXPIRED,
    },
    ListingStatus.ACCEPTED: {
        ListingStatus.PICKED_UP,
        ListingStatus.RECLASSIFIED,
        ListingStatus.EXPIRED,
    },
    ListingStatus.PICKED_UP: {
        ListingStatus.DELIVERED,
        ListingStatus.RECLASSIFIED,
        ListingStatus.EXPIRED,
    },
    ListingStatus.DELIVERED: set(),  # Terminal state
    ListingStatus.EXPIRED: {
        ListingStatus.RECLASSIFIED,
    },
    ListingStatus.RECLASSIFIED: {
        ListingStatus.MATCHED,
        ListingStatus.ACCEPTED,
        ListingStatus.PICKED_UP,
        ListingStatus.EXPIRED,
    },
}


def validate_transition(current: ListingStatus, target: ListingStatus) -> None:
    """Validate that transition from current state to target state is legally allowed."""
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise ValueError(
            f"Illegal listing transition: cannot move from '{current.value}' to '{target.value}'. "
            f"Allowed next states: {[s.value for s in allowed]}"
        )


def validate_checklist_for_tier(
    checklist: VerificationChecklist,
    tier: Union[Tier, str],
) -> Tuple[bool, List[str]]:
    """Validate physical verification checklist against tier-specific requirements.

    - HUMAN_RESCUE requires: smell_appearance_ok, temperature_ok, container_clean_covered,
      and labelled_with_time.
    - ANIMAL_FEED requires a lighter checklist: smell_appearance_ok and container_clean_covered.
    """
    tier_str = tier.value if isinstance(tier, Tier) else str(tier)
    errors: List[str] = []

    # 1. Base sensory and hygiene requirements (all tiers)
    if not checklist.smell_appearance_ok:
        errors.append("Visual & sensory check failed: off-odor, discoloration, or foreign matter observed")

    if not checklist.container_clean_covered:
        errors.append("Container hygiene check failed: container is unclean, open, or non-food-grade")

    # 2. Strict Human Rescue requirements
    if "HUMAN" in tier_str:
        if not checklist.temperature_ok:
            errors.append(
                "Temperature check failed: food is not within safe hot-holding range (>=60C or hot to touch)"
            )
        if not checklist.labelled_with_time:
            errors.append("Labelling check failed: container missing preparation and pickup timestamp label")

    return (len(errors) == 0, errors)


def verify_and_pickup(
    listing_id: str,
    checklist: VerificationChecklist,
    conn: Optional[sqlite3.Connection] = None,
    now: Optional[datetime] = None,
) -> Tuple[Listing, bool, Optional[str]]:
    """Execute physical volunteer verification gate and transition listing to PICKED_UP.

    If any mandatory item fails:
    - The listing is blocked from moving to PICKED_UP.
    - An immediate cascade reclassification is triggered through Phase 4 policy.
    - An audit event 'VERIFICATION_FAILED' is recorded with exact failure reasons.
    - Returns (reclassified_listing, False, failure_explanation).

    If all items pass:
    - The listing transitions to PICKED_UP.
    - An audit event 'LISTING_PICKED_UP' is recorded.
    - Returns (updated_listing, True, None).
    """
    c = conn or get_connection()
    close_after = conn is None
    current_time = now or get_clock_now()

    try:
        listing = get_listing(listing_id, conn=c)
        if not listing:
            raise ValueError(f"Listing '{listing_id}' not found.")

        # State machine transition check
        validate_transition(listing.status, ListingStatus.PICKED_UP)

        # Evaluate tier-specific verification requirements
        passed, failure_reasons = validate_checklist_for_tier(checklist, listing.tier)

        if not passed:
            reason_text = "; ".join(failure_reasons)

            # Record failed check in audit chain
            record_event(
                event_type="VERIFICATION_FAILED",
                entity_type="listing",
                entity_id=listing.id,
                payload={
                    "failed_tier": listing.tier.value if isinstance(listing.tier, Tier) else str(listing.tier),
                    "failed_reasons": failure_reasons,
                    "volunteer_id": checklist.volunteer_id,
                    "temp_reading_c": checklist.temp_reading_c,
                    "notes": checklist.notes,
                },
                conn=c,
                timestamp=current_time,
            )

            # Update safety assessment on listing reflecting physical inspection failure
            if listing.safety:
                listing.safety["human_ok"] = False
                reasons_list = listing.safety.get("reasons", [])
                reasons_list.extend(failure_reasons)
                listing.safety["reasons"] = reasons_list

                # If sensory / smell check failed, food is spoiled: mark HIGH risk
                if not checklist.smell_appearance_ok:
                    listing.safety["risk_level"] = "HIGH"

            # If sensory/hygiene check failed, add contamination flag so animal feed excludes it
            if not checklist.smell_appearance_ok and listing.passport:
                dishes = listing.passport.get("dishes", [])
                for dish in dishes:
                    flags = dish.get("visible_flags", [])
                    if "contamination_risk" not in flags:
                        flags.append("contamination_risk")
                    dish["visible_flags"] = flags
                listing.passport["dishes"] = dishes

            update_listing(listing, conn=c)

            # Immediately reclassify food via Phase 4 rescue cascade
            reclassified_listing, _ = reclassify(listing, now=current_time, conn=c)
            return reclassified_listing, False, reason_text


        # All checks passed: transition to PICKED_UP
        listing.status = ListingStatus.PICKED_UP
        update_listing(listing, conn=c)

        # Record successful pickup in audit chain
        record_event(
            event_type="LISTING_PICKED_UP",
            entity_type="listing",
            entity_id=listing.id,
            payload={
                "tier": listing.tier.value if isinstance(listing.tier, Tier) else str(listing.tier),
                "volunteer_id": checklist.volunteer_id,
                "checklist": checklist.model_dump(mode="json"),
            },
            conn=c,
            timestamp=current_time,
        )

        return listing, True, None
    finally:
        if close_after:
            c.close()


def confirm_delivery(
    listing_id: str,
    actual_portions: int,
    recipient_notes: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
    now: Optional[datetime] = None,
) -> Listing:
    """Recipient confirms food arrival and records verified portion count received.

    Requires listing to be in PICKED_UP state.
    Stores actual_portions for impact analytics and records audit event.
    """
    c = conn or get_connection()
    close_after = conn is None
    current_time = now or get_clock_now()

    if actual_portions <= 0:
        raise ValueError(f"Actual portions received must be at least 1 (got {actual_portions}).")

    try:
        listing = get_listing(listing_id, conn=c)
        if not listing:
            raise ValueError(f"Listing '{listing_id}' not found.")

        # State machine transition check
        validate_transition(listing.status, ListingStatus.DELIVERED)

        listing.status = ListingStatus.DELIVERED
        listing.actual_portions = actual_portions
        update_listing(listing, conn=c)

        # Audit trail
        record_event(
            event_type="LISTING_DELIVERED",
            entity_type="listing",
            entity_id=listing.id,
            payload={
                "tier": listing.tier.value if isinstance(listing.tier, Tier) else str(listing.tier),
                "actual_portions": actual_portions,
                "recipient_notes": recipient_notes,
            },
            conn=c,
            timestamp=current_time,
        )

        return listing
    finally:
        if close_after:
            c.close()


def generate_passport_qr(passport_id: str, output_path: Optional[Union[str, Path]] = None) -> bytes:
    """Generate QR PNG bytes encoding a passport ID for public verification."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(f"ticktock://passport/{passport_id}")
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    if output_path:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with open(out_p, "wb") as f:
            f.write(png_bytes)

    return png_bytes


def render_passport_page(
    listing_id: str,
    conn: Optional[sqlite3.Connection] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Return public read-only passport details for community inspection.

    IMPORTANT PRIVACY RULE: Never include donor organization contact details,
    phone numbers, or exact street address.
    """
    c = conn or get_connection()
    close_after = conn is None
    current_time = now or get_clock_now()

    try:
        listing = get_listing(listing_id, conn=c)
        if not listing:
            raise ValueError(f"Listing '{listing_id}' not found.")

        # 1. Parse food items
        dishes: List[Dict[str, Any]] = []
        allergen_hints: List[str] = []
        cooked_at_str = "Not recorded"
        container_state = "covered"
        est_portions = 0

        if listing.passport:
            p_data = listing.passport
            dishes_raw = p_data.get("dishes", [])
            for d in dishes_raw:
                p_cnt = d.get("est_portions", 0)
                est_portions += p_cnt
                dishes.append({
                    "name": d.get("name", "Surplus Dish"),
                    "category": d.get("category", "cooked_rice"),
                    "veg_status": d.get("veg_status", "veg"),
                    "portions": p_cnt,
                    "confidence": d.get("confidence", 1.0),
                })
            hints = p_data.get("allergen_hints", [])
            allergen_hints = [
                str(h).replace("AllergenHint.", "").lower()
                for h in hints
                if str(h).replace("AllergenHint.", "").lower() not in ("none", "unknown", "")
            ]
            if "handling" in p_data:
                cooked_at_str = str(p_data["handling"].get("cooked_at", cooked_at_str))
                container_state = str(p_data["handling"].get("container", container_state))
                if est_portions == 0:
                    est_portions = p_data["handling"].get("est_portions", 0)

        # 2. Overall dietary status
        veg_status = "Pure Veg"
        if any(d["veg_status"] == "non_veg" for d in dishes):
            veg_status = "Non-Veg"
        elif any(d["veg_status"] == "egg" for d in dishes):
            veg_status = "Contains Egg"

        # 3. Live safe holding countdown
        minutes_left = 0
        if listing.expires_at:
            delta = listing.expires_at - current_time
            minutes_left = max(0, int(delta.total_seconds() / 60))

        safe_until_str = listing.expires_at.strftime("%I:%M %p") if listing.expires_at else "Urgent"

        # 4. Current tier display
        tier_val = listing.tier.value if isinstance(listing.tier, Tier) else str(listing.tier)

        # 5. Verification status
        if listing.status == ListingStatus.DELIVERED:
            verification_status = "DELIVERED_AND_CONFIRMED"
        elif listing.status == ListingStatus.PICKED_UP:
            verification_status = "VERIFIED_AND_PICKED_UP"
        elif listing.status == ListingStatus.RECLASSIFIED:
            verification_status = "RECLASSIFIED_DOWNGRADE"
        else:
            verification_status = "PENDING_VERIFICATION"

        return {
            "passport_id": listing.id,
            "dishes": dishes,
            "total_portions": est_portions,
            "veg_status": veg_status,
            "allergen_hints": allergen_hints,
            "allergen_note": "Allergen hints are visual estimates only and must be verified by volunteers.",
            "cooked_at": cooked_at_str,
            "container": container_state,
            "safe_until": safe_until_str,
            "minutes_left": minutes_left,
            "current_tier": tier_val,
            "status": listing.status.value,
            "verification_status": verification_status,
            "actual_portions": listing.actual_portions,
            "disclaimer": "Advisory only. Mandatory human volunteer verification required before distribution.",
        }
    finally:
        if close_after:
            c.close()
