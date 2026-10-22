# Phase 6 Report: Multilingual Messaging & Accept/Decline Loop

**Project**: TickTock Meals  
**Phase**: 6 (Multilingual Messaging and Accept/Decline Loop)  
**Date**: October 2026  
**Status**: Completed and Green  

---

## 1. What Was Built
- **Multilingual Notification Engine (`app/llm/messages.py`)**:
  - Composes concise, localized food rescue notifications for **recipients** (shelters) and **volunteers** (couriers).
  - Native localization support across 3 languages: **English (`en`)**, **Kannada (`kn`)**, and **Hindi (`hi`)**.
  - All 6 role x language combinations render strictly under 60 words (average 25-35 words).
- **Strict Content & Privacy Guardrails**:
  - **Required Content**: Food details (portions and dishes), dietary status (Pure Veg, Contains Egg, Non-Veg), allergen hints accompanied by mandatory *"visual guess only"* disclaimer, calculated safe-until time, and explicit reply choices (`ACCEPT` / `DECLINE`).
  - **Pre-Acceptance Privacy Protection**: Under no circumstances is the donor organization name or exact street address exposed in initial dispatch notifications. Only the locality area (e.g. "Yelahanka", "Hebbal") is shown.
- **Deterministic Templates & Offline Fallbacks**:
  - Six verified deterministic templates for mock mode, rate limits, and network fallback.
  - Zero hallucinated facts; zero reliance on external LLM calls during offline testing.
- **Post-Check Validation (`validate_message`)**:
  - Verifies generated message word count (<= 60 words).
  - Checks portion and time consistency against input data.
  - Checks for presence of `ACCEPT` and `DECLINE` reply keywords.
  - Checks allergen disclaimer note for recipients.
  - Redaction check: Rejects any generated text that accidentally exposes exact donor names or coordinates, automatically falling back to deterministic templates.
- **Outbox Persistence & State Machine (`app/db.py`, `app/llm/messages.py`)**:
  - Automatically queues and stores notifications in `outbox_messages` table with status tracking (`queued`, `delivered_sim`, `accepted`, `declined`, `timed_out`).
  - Added `update_outbox_message` CRUD helper in `app/db.py`.
- **Interactive Accept / Decline Simulation Loop (`simulate_response`)**:
  - **ACCEPT**:
    - Updates message status to `accepted`.
    - Updates match status to `ACCEPTED`.
    - Transitions listing status to `ListingStatus.ACCEPTED`.
    - Generates and unlocks exact pickup address and gate instructions, dispatching confirmation to courier volunteer.
    - Appends tamper-evident audit event `MATCH_ACCEPTED`.
  - **DECLINE / TIMEOUT**:
    - Updates message status to `declined` or `timed_out`.
    - Calls Phase 5 escalation engine (`escalate`) to find the next best feasible match.
    - Automatically queues new localized outbox notifications for the escalated candidate match.
    - Appends audit event `MATCH_DECLINED` or `MATCH_TIMED_OUT`.
- **UI & Helper Functions**:
  - `preview_messages_for_match(match, conn)`: Returns localized preview strings across all 3 languages for Streamlit UI toggle.
  - `format_address_reveal(donor)`: Formats full pickup coordinates and gate reception instructions upon acceptance.
- **CLI Utility**:
  - `python -m app.llm.messages --demo` displays all 6 role x language combinations, verifies privacy concealment, and exercises post-check validation.

---

## 2. How to Run It
```powershell
# Run the messaging engine CLI demo
python -m app.llm.messages --demo

# Run all test suites (84 tests passing)
pytest -q
```

---

## 3. Assumptions Made
- Coordinator and volunteer language preference defaults to recipient/volunteer profile setting (`languages[0]`), falling back to English.
- Exact street address is revealed to the courier volunteer only after the match is formally accepted.
- Allergen hints always include the visual guess disclaimer to prevent liability in community distribution.

---

## 4. What Is Next
- **Phase 7: Real-Time Streamlit Operations Cockpit**:
  - Interactive multi-role dashboard for donors, dispatch coordinators, recipients, and courier volunteers.
  - Simulated clock controls for time-travel testing (fast-forwarding safe holding windows).
  - Pydeck geospatial map of North Bengaluru network.
  - Live outbox simulation panel with instantaneous accept/decline action triggers.
