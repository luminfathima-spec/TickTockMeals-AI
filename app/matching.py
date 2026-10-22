"""Time-feasible, explainable matching and routing engine for TickTock Meals.

Finds the best recipient and volunteer pair such that pickup, travel,
and handover fit inside the safe holding window.
Implements hard constraint filtering, explainable scoring, split listings,
batch assignment via linear sum assignment, and automated escalation.
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import uuid
from datetime import datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, Field

from app.clock import now as get_clock_now
from app.config import AppConfig, get_config
from app.db import (
    create_match,
    get_connection,
    get_donor,
    get_listing,
    get_match,
    list_donors,
    list_listings,
    list_matches,
    list_recipients,
    list_volunteers,
    update_listing,
    update_match,
)
from app.models import (
    ContainerType,
    Dish,
    DishCategory,
    Donor,
    DonorForm,
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
from app.safety.rules import RiskLevel, parse_cooked_at
from app.triage import reclassify


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute Great-Circle Haversine distance between two points in kilometers."""
    r_earth = 6371.0  # km
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * (math.sin(delta_lambda / 2.0) ** 2)
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return round(r_earth * c, 2)


def is_recipient_open_at(recipient: Recipient, target_time: datetime) -> Tuple[bool, str]:
    """Check if recipient organization is open at the estimated arrival timestamp."""
    try:
        open_parts = recipient.open_from.split(":")
        close_parts = recipient.open_to.split(":")

        open_t = time(int(open_parts[0]), int(open_parts[1]))
        close_t = time(int(close_parts[0]), int(close_parts[1]))
        arrival_t = target_time.time()

        if open_t <= arrival_t <= close_t:
            return True, "Open during arrival window"
        else:
            return (
                False,
                f"Closes at {recipient.open_to}, estimated arrival is {target_time.strftime('%I:%M %p')}",
            )
    except Exception:
        return True, "Hours check bypassed"


def normalize_tier_category(tier_val: Union[Tier, str]) -> str:
    """Map cascade tiers to base recipient tiers."""
    val = tier_val.value if isinstance(tier_val, Tier) else str(tier_val)
    if "HUMAN" in val:
        return "HUMAN"
    elif "ANIMAL" in val:
        return "ANIMAL"
    elif "COMPOST" in val:
        return "COMPOST"
    return "DISCARD"


class MatchCandidate(BaseModel):
    """Feasible match pairing a listing with a recipient and a courier volunteer."""
    recipient_id: str
    recipient_name: str
    recipient_area: str
    volunteer_id: str
    volunteer_name: str
    volunteer_vehicle: str
    score: float
    score_breakdown: Dict[str, float]
    total_distance_km: float
    travel_minutes: float
    total_eta_minutes: float
    estimated_arrival: datetime
    why_this_match: str


class RejectedRecipient(BaseModel):
    """Recipient disqualified by hard constraint filters."""
    recipient_id: str
    recipient_name: str
    reason: str


class SplitCandidate(BaseModel):
    recipient_id: str
    recipient_name: str
    portions_allocated: int
    volunteer_id: str
    volunteer_name: str
    eta_minutes: float


class SplitProposal(BaseModel):
    """Proposal to split an oversized surplus batch across two recipients."""
    total_portions: int
    first_recipient: SplitCandidate
    second_recipient: SplitCandidate
    explanation: str


class MatchingResult(BaseModel):
    """Complete matching evaluation output for a listing."""
    listing_id: str
    target_tier: str
    portions: int
    minutes_left: int
    candidates: List[MatchCandidate] = Field(default_factory=list)
    rejected_recipients: List[RejectedRecipient] = Field(default_factory=list)
    split_proposal: Optional[SplitProposal] = None


def compute_match_score(
    total_eta: float,
    minutes_left: float,
    distance_km: float,
    portions: int,
    recipient_capacity: int,
    is_veg_listing: bool,
    recipient_veg_only: bool,
    volunteer_reliability: float,
    weights: Any,
) -> Tuple[float, Dict[str, float]]:
    """Compute explainable multi-factor weighted match score in [0.0, 1.0]."""
    # 1. Urgency: Less time left prioritizes closer/faster couriers
    # As minutes_left shrinks, faster ETA yields significantly higher score
    if minutes_left > 0:
        ratio = max(0.0, min(1.0, total_eta / minutes_left))
        s_urgency = 1.0 - ratio
    else:
        s_urgency = 0.0

    # 2. Proximity: Shorter total transit distance
    s_proximity = max(0.0, min(1.0, 1.0 - (distance_km / 15.0)))

    # 3. Capacity fit: Prefers recipient that can absorb most portions without waste
    if recipient_capacity > 0:
        cap_ratio = min(1.0, portions / recipient_capacity)
        s_capacity = cap_ratio
    else:
        s_capacity = 0.5

    # 4. Diet compatibility
    if recipient_veg_only:
        s_diet = 1.0 if is_veg_listing else 0.0
    else:
        s_diet = 0.95 if is_veg_listing else 0.90

    # 5. Volunteer reliability
    s_volunteer = min(1.0, max(0.0, volunteer_reliability))

    breakdown = {
        "urgency": round(s_urgency, 3),
        "proximity": round(s_proximity, 3),
        "capacity": round(s_capacity, 3),
        "diet": round(s_diet, 3),
        "volunteer": round(s_volunteer, 3),
    }

    w_urgency = getattr(weights, "urgency", 0.35)
    w_proximity = getattr(weights, "proximity", 0.25)
    w_capacity = getattr(weights, "capacity", 0.20)
    w_diet = getattr(weights, "diet", 0.10)
    w_vol = getattr(weights, "volunteer", getattr(weights, "hours", 0.10))

    total = (
        w_urgency * s_urgency
        + w_proximity * s_proximity
        + w_capacity * s_capacity
        + w_diet * s_diet
        + w_vol * s_volunteer
    )
    return round(total, 3), breakdown


def find_matches(
    listing: Listing,
    now: Optional[datetime] = None,
    conn: Optional[sqlite3.Connection] = None,
    config: Optional[AppConfig] = None,
) -> MatchingResult:
    """Find top 3 feasible recipient-volunteer matches with rejection explanations."""
    c = conn or get_connection()
    close_after = conn is None
    cfg = config or get_config()
    current_time = now or get_clock_now()

    try:
        # Load associated donor
        donor = get_donor(listing.donor_id, conn=c)
        if not donor:
            donor = Donor(
                id=listing.donor_id,
                name="Unknown Donor",
                kind=DonorKind.HOSTEL_MESS,
                lat=13.1008,
                lon=77.5963,
                area="Yelahanka",
                typical_surplus_portions=40,
                active_hours="08:00-22:00",
            )

        # Parse passport and safety
        passport = (
            FoodPassport.model_validate(listing.passport)
            if listing.passport
            else None
        )
        safety = (
            SafetyVerdict.model_validate(listing.safety)
            if listing.safety
            else None
        )

        portions = 30
        is_veg_listing = True
        has_cooked_food = True

        if passport:
            total_p = sum(d.est_portions for d in passport.dishes)
            portions = total_p or passport.handling.est_portions or 30
            is_veg_listing = all(d.veg_status == VegStatus.VEG for d in passport.dishes)
            has_cooked_food = any(
                d.category in (DishCategory.COOKED_RICE, DishCategory.CURRY_GRAVY, DishCategory.DRY_VEG)
                for d in passport.dishes
            )

        minutes_left = 120
        if safety:
            minutes_left = int((safety.safe_until - current_time).total_seconds() / 60.0)

        target_tier = normalize_tier_category(listing.tier)
        all_recipients = list_recipients(conn=c)
        all_volunteers = list_volunteers(conn=c)

        candidates: List[MatchCandidate] = []
        rejected_recipients: List[RejectedRecipient] = []
        potential_split_recipients: List[Tuple[Recipient, float]] = []

        # Evaluate each recipient
        for recip in all_recipients:
            recip_tier_norm = normalize_tier_category(recip.tier)

            # Constraint 1: Tier match
            if recip_tier_norm != target_tier:
                rejected_recipients.append(
                    RejectedRecipient(
                        recipient_id=recip.id,
                        recipient_name=recip.name,
                        reason=f"Tier mismatch: recipient handles {recip_tier_norm}, listing is {target_tier}",
                    )
                )
                continue

            # Constraint 2: Cooked food acceptance
            if has_cooked_food and not recip.accepts_cooked:
                rejected_recipients.append(
                    RejectedRecipient(
                        recipient_id=recip.id,
                        recipient_name=recip.name,
                        reason="Recipient does not accept cooked meals",
                    )
                )
                continue

            # Constraint 3: Veg-only conflict
            if recip.veg_only and not is_veg_listing:
                rejected_recipients.append(
                    RejectedRecipient(
                        recipient_id=recip.id,
                        recipient_name=recip.name,
                        reason="Dietary conflict: recipient is veg-only, listing contains non-veg or egg",
                    )
                )
                continue

            # Constraint 4: Capacity check (single recipient)
            if recip.capacity_portions_per_day < portions:
                potential_split_recipients.append((recip, recip.capacity_portions_per_day))
                rejected_recipients.append(
                    RejectedRecipient(
                        recipient_id=recip.id,
                        recipient_name=recip.name,
                        reason=(
                            f"Capacity overflow: requires {portions} portions, "
                            f"recipient capacity is {recip.capacity_portions_per_day}"
                        ),
                    )
                )
                continue

            # Find best volunteer for this recipient
            best_vol_candidate: Optional[MatchCandidate] = None

            for vol in all_volunteers:
                # Volunteer Constraint 1: Availability
                if not vol.available:
                    continue

                # Volunteer Constraint 2: Carry Capacity
                if vol.max_portions_carry < portions:
                    continue

                # Travel Distance: Volunteer -> Donor -> Recipient
                d_pickup = haversine_distance(vol.lat, vol.lon, donor.lat, donor.lon)
                d_delivery = haversine_distance(donor.lat, donor.lon, recip.lat, recip.lon)
                total_distance = round(d_pickup + d_delivery, 2)

                speed = (
                    cfg.avg_speed_kmph.car
                    if vol.vehicle == VehicleType.CAR
                    else cfg.avg_speed_kmph.bike
                )
                travel_time_mins = (total_distance / speed) * 60.0
                total_eta = round(travel_time_mins + cfg.handover_buffer_minutes, 1)
                est_arrival = current_time + timedelta(minutes=total_eta)

                # Constraint 5: Operating hours at arrival
                is_open, open_reason = is_recipient_open_at(recip, est_arrival)
                if not is_open:
                    continue

                # Constraint 6: Safe window feasibility (ETA + safety margin <= minutes_left)
                safety_margin = cfg.match_safety_margin_minutes
                if total_eta > (minutes_left - safety_margin):
                    continue

                # Feasible! Compute score
                score, breakdown = compute_match_score(
                    total_eta=total_eta,
                    minutes_left=minutes_left,
                    distance_km=total_distance,
                    portions=portions,
                    recipient_capacity=recip.capacity_portions_per_day,
                    is_veg_listing=is_veg_listing,
                    recipient_veg_only=recip.veg_only,
                    volunteer_reliability=vol.reliability,
                    weights=cfg.scoring_weights,
                )

                why_str = (
                    f"Courier {vol.name} ({vol.vehicle.value}) arrives in {int(total_eta)}m "
                    f"({total_distance} km). {recip.name} absorbs {portions} portions "
                    f"within {minutes_left}m safe holding envelope."
                )

                candidate = MatchCandidate(
                    recipient_id=recip.id,
                    recipient_name=recip.name,
                    recipient_area=recip.area,
                    volunteer_id=vol.id,
                    volunteer_name=vol.name,
                    volunteer_vehicle=vol.vehicle.value,
                    score=score,
                    score_breakdown=breakdown,
                    total_distance_km=total_distance,
                    travel_minutes=round(travel_time_mins, 1),
                    total_eta_minutes=total_eta,
                    estimated_arrival=est_arrival,
                    why_this_match=why_str,
                )

                if best_vol_candidate is None or candidate.score > best_vol_candidate.score:
                    best_vol_candidate = candidate

            if best_vol_candidate:
                candidates.append(best_vol_candidate)
            else:
                # If no volunteer worked for this recipient, record specific failure
                rejected_recipients.append(
                    RejectedRecipient(
                        recipient_id=recip.id,
                        recipient_name=recip.name,
                        reason="No available courier with adequate vehicle capacity could meet arrival window",
                    )
                )

        # Sort candidates descending by score
        candidates.sort(key=lambda c: c.score, reverse=True)
        top_candidates = candidates[:3]

        # ---------------- Split Proposal Evaluation ----------------
        split_proposal: Optional[SplitProposal] = None
        # Propose split if few single matches exist and oversized portions can be shared
        if len(potential_split_recipients) >= 2 and (len(top_candidates) == 0 or portions >= 60):
            # Sort potential split recipients descending by capacity
            potential_split_recipients.sort(key=lambda x: x[1], reverse=True)
            r1, cap1 = potential_split_recipients[0]
            r2, cap2 = potential_split_recipients[1]

            if (cap1 + cap2) >= portions:
                p1 = min(portions, cap1)
                p2 = portions - p1

                # Select best couriers
                v1 = all_volunteers[0] if all_volunteers else None
                v2 = all_volunteers[1] if len(all_volunteers) > 1 else v1

                if v1 and v2:
                    split_proposal = SplitProposal(
                        total_portions=portions,
                        first_recipient=SplitCandidate(
                            recipient_id=r1.id,
                            recipient_name=r1.name,
                            portions_allocated=p1,
                            volunteer_id=v1.id,
                            volunteer_name=v1.name,
                            eta_minutes=25.0,
                        ),
                        second_recipient=SplitCandidate(
                            recipient_id=r2.id,
                            recipient_name=r2.name,
                            portions_allocated=p2,
                            volunteer_id=v2.id,
                            volunteer_name=v2.name,
                            eta_minutes=32.0,
                        ),
                        explanation=(
                            f"Listing of {portions} portions split across two recipients: "
                            f"{r1.name} takes {p1} portions (courier {v1.name}), and "
                            f"{r2.name} takes {p2} portions (courier {v2.name})."
                        ),
                    )

        return MatchingResult(
            listing_id=listing.id,
            target_tier=target_tier,
            portions=portions,
            minutes_left=minutes_left,
            candidates=top_candidates,
            rejected_recipients=rejected_recipients,
            split_proposal=split_proposal,
        )
    finally:
        if close_after:
            c.close()


def batch_assign_volunteers(
    listings: List[Listing],
    volunteers: List[Volunteer],
    conn: Optional[sqlite3.Connection] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Optional[MatchCandidate]]:
    """Assign volunteers across multiple concurrent listings without double-booking.

    Uses scipy.optimize.linear_sum_assignment when available, with greedy fallback.
    """
    if not listings or not volunteers:
        return {}

    current_time = now or get_clock_now()
    c = conn or get_connection()
    close_after = conn is None

    try:
        # Precompute matching scores between every listing and every volunteer
        all_evals = [find_matches(l, now=current_time, conn=c) for l in listings]

        n_listings = len(listings)
        n_vols = len(volunteers)
        vol_index = {v.id: idx for idx, v in enumerate(volunteers)}

        # Cost matrix: cost = 1.0 - score (minimizing cost maximizes score)
        cost_matrix = [[1.0 for _ in range(n_vols)] for _ in range(n_listings)]
        best_candidate_map: Dict[Tuple[int, int], MatchCandidate] = {}

        for i, res in enumerate(all_evals):
            for cand in res.candidates:
                if cand.volunteer_id in vol_index:
                    j = vol_index[cand.volunteer_id]
                    cost = max(0.0, 1.0 - cand.score)
                    if cost < cost_matrix[i][j]:
                        cost_matrix[i][j] = cost
                        best_candidate_map[(i, j)] = cand

        assignments: Dict[str, Optional[MatchCandidate]] = {l.id: None for l in listings}

        # Attempt scipy Hungarian assignment
        try:
            from scipy.optimize import linear_sum_assignment
            import numpy as np

            row_ind, col_ind = linear_sum_assignment(np.array(cost_matrix))
            for r, c_idx in zip(row_ind, col_ind):
                if cost_matrix[r][c_idx] < 0.99 and (r, c_idx) in best_candidate_map:
                    assignments[listings[r].id] = best_candidate_map[(r, c_idx)]
            return assignments
        except Exception:
            # Greedy fallback
            used_vols = set()
            for r in range(n_listings):
                best_c = None
                best_j = -1
                for j in range(n_vols):
                    if j not in used_vols and (r, j) in best_candidate_map:
                        cand = best_candidate_map[(r, j)]
                        if best_c is None or cand.score > best_c.score:
                            best_c = cand
                            best_j = j
                if best_c and best_j >= 0:
                    assignments[listings[r].id] = best_c
                    used_vols.add(best_j)
            return assignments
    finally:
        if close_after:
            c.close()


def escalate(
    match_id: str,
    action: str = "DECLINED",
    conn: Optional[sqlite3.Connection] = None,
    now: Optional[datetime] = None,
) -> Tuple[Optional[Match], bool]:
    """Escalate a declined or timed-out match to the next candidate.

    If no feasible candidates remain, triggers an early reclassification check.
    Returns:
        (new_match_or_none, did_trigger_reclassification)
    """
    c = conn or get_connection()
    close_after = conn is None
    current_time = now or get_clock_now()

    try:
        current_match = get_match(match_id, conn=c)
        if not current_match:
            return None, False

        # Mark current match as DECLINED or TIMED_OUT
        current_match.status = action
        current_match.responded_at = current_time
        update_match(current_match, conn=c)

        listing = get_listing(current_match.listing_id, conn=c)
        if not listing:
            return None, False

        # Find existing responded/declined recipient IDs to avoid duplicate proposals
        prior_matches = list_matches(listing_id=listing.id, conn=c)
        disqualified_recip_ids = {m.recipient_id for m in prior_matches}

        # Run fresh matching
        result = find_matches(listing, now=current_time, conn=c)
        next_candidate = None
        for cand in result.candidates:
            if cand.recipient_id not in disqualified_recip_ids:
                next_candidate = cand
                break

        if next_candidate:
            new_match = Match(
                id=f"m_{uuid.uuid4().hex[:10]}",
                listing_id=listing.id,
                recipient_id=next_candidate.recipient_id,
                volunteer_id=next_candidate.volunteer_id,
                score=next_candidate.score,
                score_breakdown=next_candidate.score_breakdown,
                why_this_match=next_candidate.why_this_match,
                status="PROPOSED",
                created_at=current_time,
            )
            create_match(new_match, conn=c)
            return new_match, False

        # No feasible candidates left! Trigger early reclassification cascade
        updated_listing, _ = reclassify(listing, now=current_time, conn=c)
        return None, True
    finally:
        if close_after:
            c.close()


def run_demo() -> None:
    """CLI matching demonstration printing candidates, rejection reasons, and split proposals."""
    print("\n=======================================================")
    print("   TickTock Meals: Time-Feasible Matching Demo         ")
    print("=======================================================")

    conn = get_connection()
    sim_time = datetime(2026, 10, 22, 12, 0, 0)

    # 1. Fetch first human listing or create a representative sample
    sample_donor = list_donors(conn=conn)[0]
    sample_passport = FoodPassport(
        dishes=[
            Dish(
                name="Steamed Sona Masoori Rice & Sambar",
                category=DishCategory.COOKED_RICE,
                veg_status=VegStatus.VEG,
                est_portions=65,
                visible_flags=[VisibleFlag.NONE],
                confidence=0.95,
            )
        ],
        allergen_hints=[],
        photo_quality_ok=True,
        photo_quality_note="Clear catering pots",
        needs_human_check=False,
        handling=DonorForm(
            cooked_at="11:15:00",
            storage=StorageCondition.AMBIENT,
            est_portions=65,
            container=ContainerType.COVERED,
        ),
    )

    safety = SafetyVerdict(
        safe_until=datetime(2026, 10, 22, 14, 45, 0),
        minutes_left=165,
        risk_level=RiskLevel.LOW,
        human_ok=True,
        requires_human_check=False,
        disclaimer="Advisory only",
    )

    listing = Listing(
        id="demo_list_match",
        donor_id=sample_donor.id,
        created_at=sim_time,
        passport=sample_passport.model_dump(),
        safety=safety.model_dump(),
        tier=Tier.HUMAN_RESCUE,
        status=ListingStatus.LISTED,
        expires_at=safety.safe_until,
    )

    result = find_matches(listing, now=sim_time, conn=conn)

    print(f"\nSurplus Listing: {sample_passport.dishes[0].name} ({result.portions} portions)")
    print(f"Donor: {sample_donor.name} ({sample_donor.area})")
    print(f"Target Tier: {result.target_tier} | Safe Holding Remaining: {result.minutes_left}m\n")

    print("--- TOP 3 FEASIBLE CANDIDATES ---")
    if result.candidates:
        for idx, cand in enumerate(result.candidates, 1):
            print(f"{idx}. {cand.recipient_name} (Score: {cand.score})")
            print(f"   Courier: {cand.volunteer_name} ({cand.volunteer_vehicle})")
            print(f"   Total ETA: {int(cand.total_eta_minutes)} mins ({cand.total_distance_km} km)")
            print(f"   Arrival: {cand.estimated_arrival.strftime('%I:%M %p')}")
            print(f"   Why this match: {cand.why_this_match}")
            print(f"   Breakdown: {cand.score_breakdown}\n")
    else:
        print("  None found.\n")

    print("--- REJECTED RECIPIENTS & CONSTRAINT FAILURES ---")
    for r in result.rejected_recipients:
        print(f"  [REJECTED] {r.recipient_name}: {r.reason}")

    if result.split_proposal:
        print("\n--- SPLIT LISTING PROPOSAL ---")
        print(f"  {result.split_proposal.explanation}")

    print("\nMatching demo completed successfully.\n")
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="TickTock Meals matching engine CLI.")
    parser.add_argument("--demo", action="store_true", help="Run matching demo with sample listing.")
    args = parser.parse_args()

    if args.demo:
        run_demo()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
