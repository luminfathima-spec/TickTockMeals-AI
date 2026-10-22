# Phase 5 Report: Time-Feasible, Explainable Matching

**Project**: TickTock Meals  
**Phase**: 5 (Time-Feasible, Explainable Matching and Routing)  
**Date**: October 2026  
**Status**: Completed and Green  

---

## 1. What Was Built
- **Zero-API Haversine Distance & Honest ETA Engine (`app/matching.py`)**:
  - Computes great-circle distance between couriers, donors, and recipients with zero external maps API calls or privacy tracking.
  - Multi-leg route estimation: Courier ➡️ Donor (pickup) + Donor ➡️ Recipient (delivery).
  - Vehicle speed matrix from `config.yaml` (`bike: 22 km/h`, `car: 25 km/h`) plus 15-minute handover buffer.
- **Hard Feasibility Constraint Filters**:
  - **Tier Alignment**: Enforces matching tier (`HUMAN_RESCUE` to `HUMAN`, `ANIMAL_FEED` to `ANIMAL`, `COMPOST_BIOGAS` to `COMPOST`).
  - **Operating Hours**: Validates that recipient shelter is physically open at estimated courier arrival timestamp (`arrival_time`).
  - **Cooked Food Acceptance**: Verifies recipient accepts prepared cooked meals.
  - **Dietary Safeguards**: Strictly prevents non-veg or egg dishes from being routed to `veg_only` recipients.
  - **Recipient Capacity**: Ensures surplus volume does not exceed daily intake capacity.
  - **Courier Suitability**: Enforces courier availability, vehicle type, and carry capacity limits.
  - **Time Budget Envelope**: Rejects matches where total ETA exceeds safe holding time minus configured safety margin (`match_safety_margin_minutes: 15`).
- **Explainable Multi-Factor Scoring**:
  - Transparent weighted scoring across 5 factors: `urgency` (0.35), `proximity` (0.25), `capacity_fit` (0.20), `diet_compatibility` (0.10), and `volunteer_reliability` (0.10).
  - Produces human-readable `"why this match"` explanations detailing courier transport, transit duration, distance, and recipient capacity absorption.
  - Exposes comprehensive rejection logs detailing the exact hard constraint failed for each disqualified recipient.
- **Split Listing Generator**:
  - Automatically proposes an optimal 2-recipient allocation when surplus batches exceed single shelter intake limits.
- **Batch Assignment (Scipy Linear Sum Assignment)**:
  - Solves the bipartite assignment problem across multiple simultaneous open listings and courier volunteers, guaranteeing zero double-booking.
  - Includes robust greedy fallback for constrained environments.
- **Automated Escalation Loop (`escalate`)**:
  - Handles candidate declines or response timeouts by proposing the next best feasible match.
  - If all candidates are exhausted, triggers immediate early reclassification into subsequent cascade tiers.
- **CLI Utility**:
  - `python -m app.matching --demo` demonstrates match ranking, rejection tracking, and split generation.

---

## 2. How to Run It
```powershell
# Run the matching engine CLI demo
python -m app.matching --demo

# Run all test suites
pytest -q
```

---

## 3. Assumptions Made
- Courier transit speeds assume representative North Bengaluru urban road conditions (bike 22 km/h, car 25 km/h).
- Turnaround and inspection handover buffer is fixed at 15 minutes.
- Matching safety margin is maintained at 15 minutes before safe-until expiry to prevent transit expiration.

---

## 4. What Is Next
- **Phase 6: Multilingual Messaging and Accept/Decline Loop**:
  - Short, clear pickup dispatch requests for recipients and couriers in English, Kannada, and Hindi (`app/llm/messages.py`).
  - Strict under-60-words rule with number/time verification checks falling back to deterministic templates.
  - Outbox persistence (`app.models.OutboxMessage`) with privacy protection (area-only address until acceptance).
  - Driving the accept/decline/escalate loop and volunteer address reveals.
