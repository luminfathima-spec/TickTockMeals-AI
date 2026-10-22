"""Triage and Rescue Cascade engine for TickTock Meals.

Implements automatic hierarchical surplus tiering:
HUMAN_RESCUE -> ANIMAL_FEED -> COMPOST_BIOGAS -> DISCARD_UNSAFE.

Driven by simulated clock ticks, with idempotent reclassification,
audit logging, and match cancellations upon downgrade.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, Field

from app.clock import advance as advance_clock, now as get_clock_now, set_fixed as set_clock_fixed
from app.config import AppConfig, get_config
from app.db import (
    create_audit_event,
    create_donor,
    create_listing,
    create_match,
    get_connection,
    get_listing,
    list_listings,
    list_matches,
    update_listing,
)
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
    StorageCondition,
    Tier,
    VegStatus,
    VisibleFlag,
)
from app.safety.assess import SafetyVerdict, assess_safety


class TriageDecision(BaseModel):
    """Structured decision output from the Rescue Cascade policy engine."""
    tier: Tier = Field(..., description="Assigned cascade tier")
    next_tier: Optional[Tier] = Field(default=None, description="Subsequent fallback tier upon downgrade")
    next_downgrade_at: Optional[datetime] = Field(default=None, description="Timestamp when current tier expires")
    minutes_left_in_tier: int = Field(default=0, description="Minutes remaining in current tier")
    explanation: List[str] = Field(default_factory=list, description="Reasoning for tier assignment")
    policy_rule_ids: List[str] = Field(default_factory=list, description="Audit rule references for decision")


def is_animal_feed_eligible(
    passport: FoodPassport,
    config: Optional[AppConfig] = None,
) -> Tuple[bool, List[str], List[str]]:
    """Determine whether surplus food is suitable for animal shelters.

    Excludes toxic ingredients (onion, garlic, chocolate, sweets, excessive spices),
    curry gravies, and any visible mold or biological contamination.
    """
    cfg = config or get_config()
    reasons: List[str] = []
    rule_ids: List[str] = []

    # 1. Biological Hazard Check: Mold or Contamination strictly excluded
    for dish in passport.dishes:
        for flag in dish.visible_flags:
            if flag in (VisibleFlag.MOLD, VisibleFlag.CONTAMINATION_RISK):
                reasons.append("Visible mold or biological contamination detected; excluded from animal feed.")
                rule_ids.append("RULE_ANIMAL_NO_MOLD")
                return False, reasons, rule_ids

    # 2. Ingredient & Category Exclusion Checks
    exclusions = [e.lower() for e in cfg.animal_exclusions]

    for dish in passport.dishes:
        dish_name_lower = dish.name.lower()
        cat_val = dish.category.value if isinstance(dish.category, DishCategory) else str(dish.category)

        for exc in exclusions:
            if exc in dish_name_lower:
                reasons.append(f"Dish '{dish.name}' contains toxic animal ingredient: '{exc}'.")
                rule_ids.append("RULE_ANIMAL_EXCLUSION_INGREDIENT")
                return False, reasons, rule_ids

        # Exclude rich curries, heavy sweets, and beverages from animal feed
        if cat_val in ("curry_gravy", "sweet", "beverage"):
            reasons.append(f"Category '{cat_val}' unsuitable for animal shelter feed.")
            rule_ids.append("RULE_ANIMAL_EXCLUSION_CATEGORY")
            return False, reasons, rule_ids

    reasons.append("Eligible for animal feed: free of mold, toxic Alliums, sweets, and heavy gravies.")
    rule_ids.append("RULE_ANIMAL_ELIGIBLE")
    return True, reasons, rule_ids


def evaluate_triage(
    passport: FoodPassport,
    safety: SafetyVerdict,
    reference_time: Optional[datetime] = None,
    config: Optional[AppConfig] = None,
) -> TriageDecision:
    """Evaluate current tier for a FoodPassport based on safety verdict and clock."""
    now = reference_time or get_clock_now()
    cfg = config or get_config()

    min_human_win = cfg.min_human_window_minutes
    safe_until = safety.safe_until
    minutes_left = int((safe_until - now).total_seconds() / 60.0)

    animal_eligible, animal_reasons, animal_rules = is_animal_feed_eligible(passport, config=cfg)

    # ---------------- 1. HUMAN RESCUE TIER ----------------
    # Eligible if safety cleared, risk is not HIGH, and minutes_left exceeds minimum useful window
    if safety.human_ok and safety.risk_level.value != "HIGH" and minutes_left >= min_human_win:
        human_end = safe_until - timedelta(minutes=min_human_win)
        tier_mins_left = max(0, int((human_end - now).total_seconds() / 60.0))
        next_tier = Tier.ANIMAL_FEED if animal_eligible else Tier.COMPOST_BIOGAS

        return TriageDecision(
            tier=Tier.HUMAN_RESCUE,
            next_tier=next_tier,
            next_downgrade_at=human_end,
            minutes_left_in_tier=tier_mins_left,
            explanation=[
                f"Cleared for human consumption: {minutes_left} minutes safe holding left (minimum window: {min_human_win}m)."
            ],
            policy_rule_ids=["RULE_HUMAN_WINDOW_OK"],
        )

    # ---------------- 2. ANIMAL FEED TIER ----------------
    # Eligible if animal safe (no mold, no Allium/chocolate/sweets) and animal window has not expired
    has_mold = any(
        VisibleFlag.MOLD in d.visible_flags or VisibleFlag.CONTAMINATION_RISK in d.visible_flags
        for d in passport.dishes
    )

    if animal_eligible and not has_mold:
        # Animal feed remains safe for an additional duration past the human safe threshold
        animal_end = safe_until + timedelta(hours=cfg.animal_window_hours)
        animal_mins_left = int((animal_end - now).total_seconds() / 60.0)

        if animal_mins_left > 0:
            return TriageDecision(
                tier=Tier.ANIMAL_FEED,
                next_tier=Tier.COMPOST_BIOGAS,
                next_downgrade_at=animal_end,
                minutes_left_in_tier=animal_mins_left,
                explanation=[
                    "Human consumption window closed; routed to animal feed.",
                    *animal_reasons,
                ],
                policy_rule_ids=["RULE_CASCADE_ANIMAL_FEED", *animal_rules],
            )

    # ---------------- 3. COMPOST & BIOGAS TIER ----------------
    # Organic surplus not fit for humans or animals is routed to bio-methanation / composting
    compost_end = safe_until + timedelta(hours=cfg.animal_window_hours + cfg.compost_window_hours)
    compost_mins_left = int((compost_end - now).total_seconds() / 60.0)

    if compost_mins_left > 0:
        return TriageDecision(
            tier=Tier.COMPOST_BIOGAS,
            next_tier=Tier.DISCARD_UNSAFE,
            next_downgrade_at=compost_end,
            minutes_left_in_tier=compost_mins_left,
            explanation=[
                "Surplus routed to bio-methanation or organic composting.",
                *animal_reasons,
            ],
            policy_rule_ids=["RULE_CASCADE_COMPOST"],
        )

    # ---------------- 4. DISCARD UNSAFE TIER ----------------
    return TriageDecision(
        tier=Tier.DISCARD_UNSAFE,
        next_tier=None,
        next_downgrade_at=None,
        minutes_left_in_tier=0,
        explanation=["Exceeded holding time limit for all recovery channels; discard required."],
        policy_rule_ids=["RULE_DISCARD_EXPIRED"],
    )


def cascade_timeline(listing: Listing, reference_time: Optional[datetime] = None) -> str:
    """Return human-readable summary of future tier downgrade milestones."""
    now = reference_time or get_clock_now()
    cfg = get_config()

    if not listing.passport or not listing.safety:
        return "Timeline unavailable: listing requires passport and safety assessment."

    passport = FoodPassport.model_validate(listing.passport)
    safety = SafetyVerdict.model_validate(listing.safety)
    safe_until = safety.safe_until

    human_end = safe_until - timedelta(minutes=cfg.min_human_window_minutes)
    animal_end = safe_until + timedelta(hours=cfg.animal_window_hours)
    animal_eligible, _, _ = is_animal_feed_eligible(passport, config=cfg)

    human_str = human_end.strftime("%I:%M %p")
    animal_str = animal_end.strftime("%I:%M %p")

    if animal_eligible:
        return f"Human rescue until {human_str}, animal feed until {animal_str}, then compost"
    else:
        return f"Human rescue until {human_str}, then compost (animal exclusions apply)"


def reclassify(
    listing: Listing,
    now: datetime,
    conn: Optional[sqlite3.Connection] = None,
) -> Tuple[Listing, Optional[TriageDecision]]:
    """Reclassify a listing based on the simulated clock.

    If the tier changes:
    1. Updates listing.tier and listing.status to RECLASSIFIED.
    2. Writes an AuditEvent.
    3. Cancels any pending PROPOSED human-tier matches.
    """
    if not listing.passport or not listing.safety:
        return listing, None

    c = conn or get_connection()
    close_after = conn is None

    try:
        passport = FoodPassport.model_validate(listing.passport)
        safety = SafetyVerdict.model_validate(listing.safety)

        decision = evaluate_triage(passport, safety, reference_time=now)
        current_tier_val = listing.tier.value if isinstance(listing.tier, Tier) else str(listing.tier)
        new_tier_val = decision.tier.value

        if current_tier_val != new_tier_val:
            old_tier = listing.tier
            listing.tier = decision.tier
            listing.status = ListingStatus.RECLASSIFIED

            # 1. Update listing in DB
            update_listing(listing, conn=c)

            # 2. Write AuditEvent
            event = AuditEvent(
                id=f"aud_{uuid.uuid4().hex[:12]}",
                timestamp=now,
                event_type="RECLASSIFIED",
                entity_type="listing",
                entity_id=listing.id,
                payload={
                    "prev_tier": old_tier.value if isinstance(old_tier, Tier) else str(old_tier),
                    "new_tier": new_tier_val,
                    "reasons": decision.explanation,
                    "rule_ids": decision.policy_rule_ids,
                    "minutes_left_in_new_tier": decision.minutes_left_in_tier,
                },
            )
            create_audit_event(event, conn=c)

            # 3. Cancel outstanding human matches if downgraded from human
            if old_tier in (Tier.HUMAN, Tier.HUMAN_RESCUE):
                with c:
                    c.execute(
                        """
                        UPDATE matches
                        SET status = 'CANCELLED'
                        WHERE listing_id = ? AND status = 'PROPOSED'
                        """,
                        (listing.id,),
                    )

            return listing, decision

        return listing, decision
    finally:
        if close_after:
            c.close()


def tick(
    now: Optional[datetime] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> List[Tuple[str, str, str]]:
    """Periodic scheduler tick: re-triages all active listings.

    Idempotent: Running multiple times with identical `now` produces zero redundant state changes.
    Returns: List of (listing_id, old_tier, new_tier) reclassification events.
    """
    current_time = now or get_clock_now()
    c = conn or get_connection()
    close_after = conn is None

    reclassified_list: List[Tuple[str, str, str]] = []

    try:
        # Fetch all open listings
        open_statuses = [
            ListingStatus.LISTED,
            ListingStatus.VERIFIED,
            ListingStatus.MATCHED,
            ListingStatus.RECLASSIFIED,
        ]

        active_listings: List[Listing] = []
        for s in open_statuses:
            active_listings.extend(list_listings(status=s, conn=c))

        # Reclassify each active listing
        for lst in active_listings:
            old_tier_val = lst.tier.value if isinstance(lst.tier, Tier) else str(lst.tier)
            updated_listing, decision = reclassify(lst, now=current_time, conn=c)
            new_tier_val = (
                updated_listing.tier.value
                if isinstance(updated_listing.tier, Tier)
                else str(updated_listing.tier)
            )

            if old_tier_val != new_tier_val:
                reclassified_list.append((lst.id, old_tier_val, new_tier_val))

        return reclassified_list
    finally:
        if close_after:
            c.close()


def run_demo() -> None:
    """CLI 6-hour rescue cascade simulation in the terminal."""
    print("\n=======================================================")
    print("   TickTock Meals: 6-Hour Rescue Cascade Simulation    ")
    print("=======================================================")

    conn = get_connection()
    sim_start = datetime(2026, 10, 22, 12, 0, 0)
    set_clock_fixed(sim_start)

    # 1. Setup donor and listing
    donor = Donor(
        id="donor_demo",
        name="Central Campus Mess",
        kind=DonorKind.HOSTEL_MESS,
        lat=13.1000,
        lon=77.5900,
        area="Yelahanka",
        typical_surplus_portions=50,
        active_hours="08:00-22:00",
    )
    with conn:
        conn.execute("DELETE FROM donors WHERE id = 'donor_demo'")
        conn.execute("DELETE FROM listings WHERE id = 'list_demo'")
        conn.execute("DELETE FROM matches WHERE listing_id = 'list_demo'")

    create_donor(donor, conn=conn)

    # Food: Plain Steamed Sona Masoori Rice cooked at 11:30 (ambient storage: 3.5h safe window -> safe until 15:00)
    # Minimum human window: 45m -> Human window closes at 14:15.
    # Animal window: +2h -> Animal window closes at 17:00.
    # Compost window: closes afterwards.
    cooked_time = sim_start - timedelta(minutes=30)
    passport = FoodPassport(
        dishes=[
            Dish(
                name="Steamed Sona Masoori Rice",
                category=DishCategory.COOKED_RICE,
                veg_status=VegStatus.VEG,
                est_portions=40,
                visible_flags=[VisibleFlag.NONE],
                confidence=0.96,
            )
        ],
        allergen_hints=[],
        photo_quality_ok=True,
        photo_quality_note="Clear catering container",
        needs_human_check=False,
        handling=DonorForm(
            cooked_at=cooked_time.strftime("%Y-%m-%d %H:%M:%S"),
            storage=StorageCondition.AMBIENT,
            est_portions=40,
            container=ContainerType.COVERED,
        ),
    )

    safety = assess_safety(passport, reference_time=sim_start, mode="mock")

    listing = Listing(
        id="list_demo",
        donor_id=donor.id,
        created_at=sim_start,
        passport=passport.model_dump(),
        safety=safety.model_dump(),
        tier=Tier.HUMAN_RESCUE,
        status=ListingStatus.LISTED,
        expires_at=safety.safe_until,
    )
    create_listing(listing, conn=conn)

    # Propose a human match to test cancellation
    match = Match(
        id="match_demo",
        listing_id=listing.id,
        recipient_id="recip_01",
        score=0.92,
        status="PROPOSED",
        created_at=sim_start,
    )
    create_match(match, conn=conn)

    print(f"\nListing Registered at: {sim_start.strftime('%I:%M %p')}")
    print(f"Food: {passport.dishes[0].name} (40 portions)")
    print(f"Safe until: {safety.safe_until.strftime('%I:%M %p')}")
    print(f"Cascade Timeline: {cascade_timeline(listing, sim_start)}\n")

    steps = [
        ("T+0h (12:00 PM)", 0),
        ("T+1h (01:00 PM)", 60),
        ("T+2.5h (02:30 PM)", 90),   # Past 02:15 PM -> Downgrade to ANIMAL_FEED!
        ("T+4h (04:00 PM)", 90),
        ("T+5.5h (05:30 PM)", 90),   # Past 05:00 PM -> Downgrade to COMPOST_BIOGAS!
        ("T+6h (06:00 PM)", 30),
    ]

    for label, advance_mins in steps:
        if advance_mins > 0:
            current_clock = advance_clock(advance_mins)
        else:
            current_clock = get_clock_now()

        # Run tick
        reclassifications = tick(now=current_clock, conn=conn)
        updated_listing = get_listing("list_demo", conn=conn)
        matches = list_matches(listing_id="list_demo", conn=conn)
        human_match_status = matches[0].status if matches else "NONE"

        print(f"--- Clock: {current_clock.strftime('%I:%M %p')} [{label}] ---")
        print(f"  Current Tier:        {updated_listing.tier.value}")
        print(f"  Status:              {updated_listing.status.value}")
        print(f"  Human Match Status:  {human_match_status}")
        if reclassifications:
            for l_id, old_t, new_t in reclassifications:
                print(f"  [CASCADE EVENT] {old_t} -> {new_t}")
        print()

    print("Cascade simulation finished successfully.\n")
    conn.close()


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Triage and Rescue Cascade controller.")
    parser.add_argument("--demo", action="store_true", help="Run 6-hour cascade simulation in terminal.")
    args = parser.parse_args()

    if args.demo:
        run_demo()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
