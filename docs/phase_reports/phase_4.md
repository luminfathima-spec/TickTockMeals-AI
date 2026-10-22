# Phase 4 Report: Triage and the Rescue Cascade

**Project**: TickTock Meals  
**Phase**: 4 (Triage and the Rescue Cascade)  
**Date**: October 2026  
**Status**: Completed and Green  

---

## 1. What Was Built
- **Rescue Cascade Tier Policy Engine (`app/triage.py`)**:
  - Implements dynamic four-stage triage: `HUMAN_RESCUE` ➡️ `ANIMAL_FEED` ➡️ `COMPOST_BIOGAS` ➡️ `DISCARD_UNSAFE`.
  - Configurable policy thresholds in `config.yaml` (`min_human_window_minutes: 45`, `animal_window_hours: 2.0`, `compost_window_hours: 24.0`).
  - Strict toxic ingredient and category exclusions for animal shelters (Alliums like onion/garlic, chocolate, sweets, spicy gravies, sambars, and biryanis).
  - Biosecurity guardrail: Visible mold or biological contamination completely bars food from entering animal feed, routing directly to compost/biogas.
- **Idempotent Scheduler Tick (`tick`)**:
  - Automatically queries all open surplus listings (`LISTED`, `VERIFIED`, `MATCHED`, `RECLASSIFIED`).
  - Evaluates remaining time envelopes against the simulated timekeeper (`app.clock.now()`).
  - Reclassifies listings whose safe holding windows transition to the next recovery tier.
  - Fully idempotent: multiple executions at the identical timestamp produce zero redundant database writes or event log emissions.
- **Downgrade Cascade & Audit Trail Integration**:
  - State transition updates listing status to `RECLASSIFIED`.
  - Appends an audit event (`RECLASSIFIED`) capturing previous tier, new tier, remaining minutes, and regulatory policy rule IDs.
  - Automatically cancels all pending `PROPOSED` human-tier matches when food downgrades to animal feed or compost.
- **Cascade Timeline Visualizer (`cascade_timeline`)**:
  - Human-friendly milestone summarizer (e.g. `"Human rescue until 02:15 PM, animal feed until 05:00 PM, then compost"`).
- **CLI Terminal Simulation**:
  - `python -m app.triage --demo` simulates a live 6-hour timeline, demonstrating step-by-step automatic cascade transitions from steamed rice to animal feed to compost with live match cancellation.

---

## 2. Decision Policy Summary

| Tier | Eligibility Criteria | Next Downgrade Milestone | Action on Downgrade |
| :--- | :--- | :--- | :--- |
| **HUMAN_RESCUE** | `human_ok=True`, risk not `HIGH`, `minutes_left >= 45m` | `safe_until - 45m` | Cancels pending human matches; reclassifies to animal feed or compost |
| **ANIMAL_FEED** | Human window closed, no mold/contamination, no toxic exclusions | `safe_until + 2.0h` | Downgrades to compost/biogas; alerts animal couriers |
| **COMPOST_BIOGAS** | Organic matter not eligible for humans or animals | `safe_until + 26.0h` | Re-routes to biomethanation or composting yards |
| **DISCARD_UNSAFE** | Packaging contamination, non-organic hazard, or expired compost staging | Terminal | Discarded under sanitary protocol |

---

## 3. How to Run It
```powershell
# Run the 6-hour rescue cascade terminal simulation
python -m app.triage --demo

# Run all test suites
pytest -q
```

---

## 4. Assumptions Made
- The human recovery threshold is parameterized at 45 minutes to guarantee adequate buffer for courier dispatch, transit, and volunteer inspection before the safe threshold expires.
- Plain cooked rice, plain chapatis, and raw vegetable trimmings are eligible for animal feed, while seasoned dishes with Alliums (onions/garlic) or spices are routed directly to compost.
- Animal feed safe holding is modeled as extending 2 hours beyond the conservative human safe-until limit.

---

## 5. What Is Next
- **Phase 5: Time-Feasible, Explainable Matching**:
  - Haversine distance and transit ETA calculation using vehicle average speeds (`bike: 22 km/h`, `car: 25 km/h`) plus handover buffers.
  - Hard constraint feasibility filter: recipient tier match, opening hours check at arrival ETA, dietary constraints (`veg_only`), volunteer capacity and availability.
  - Explainable multi-factor scoring (urgency, proximity, capacity, diet, volunteer reliability).
  - Split listings for oversized donations, batch assignment without double-booking, and candidate escalation loop.
