# Phase 7 Report: Verification Gate, Passport QR, Tamper-Evident Audit Chain

**Project**: TickTock Meals  
**Phase**: 7 (Verification Gate, QR Passport, Tamper-Evident Audit Chain)  
**Date**: October 2026  
**Status**: Completed and Green  

---

## 1. What Was Built
- **Strict State Machine for Listing Lifecycle (`app/handover.py`)**:
  - Enforces explicit legal transitions across `ListingStatus` states:
    `LISTED` ➡️ `VERIFIED` ➡️ `MATCHED` ➡️ `ACCEPTED` ➡️ `PICKED_UP` ➡️ `DELIVERED`.
  - Terminal states (`DELIVERED`) and invalid skips (e.g. `LISTED` ➡️ `DELIVERED`, or `ACCEPTED` ➡️ `DELIVERED` skipping `PICKED_UP`) raise clear `ValueError` exceptions via `validate_transition()`.
- **Physical Verification Gate (`VerificationChecklist`)**:
  - **`HUMAN_RESCUE` Pickup Gate**:
    - Mandatory physical checklist completed by volunteer at donor site:
      1. Smell & appearance check (fresh, no off-odors, discoloration, or foreign matter).
      2. Temperature check ($\ge 60^\circ\text{C}$ or confirmed "still hot to touch").
      3. Container hygiene check (clean, food-grade, covered).
      4. Time labelling check (marked with preparation/pickup time).
    - **Failsafe Reclassification**: If any check fails, the listing is blocked from moving to `PICKED_UP`. The safety assessment is updated (`human_ok = False`), an audit event `VERIFICATION_FAILED` is logged, and the listing is immediately reclassified down the cascade (to animal feed or compost).
  - **`ANIMAL_FEED` Lighter Verification Gate**:
    - Enforces sensory freshness and clean container hygiene, but does not block on hot temperature or time labelling.
    - If food has spoiled or smells sour, it is immediately reclassified down to compost or discard.
- **Delivery Confirmation & Actual Portions Tracking (`confirm_delivery`)**:
  - Transitions listing from `PICKED_UP` to `DELIVERED` upon recipient arrival.
  - Enforces recipient confirmation of `actual_portions` received ($\ge 1$).
  - Persists `actual_portions` in the database and audit trail for Phase 8 impact analytics.
- **Tamper-Evident Hash-Chained Audit Trail (`app/audit.py`)**:
  - Append-only event logging where each `AuditEvent` stores `prev_hash` and `hash = SHA-256(prev_hash, event_id, timestamp, event_type, entity_type, entity_id, payload)`.
  - Genesis event starts the chain with `prev_hash = "GENESIS"`.
  - `verify_chain()` recomputes SHA-256 digests and link continuity across the entire table, pinpointing the exact row index of any modified payload, tampered timestamp, or deleted event.
  - CLI command: `python -m app.audit --verify` exits 0 on valid chains.
- **Scannable Passport QR Code Generator (`generate_passport_qr`)**:
  - Uses `qrcode` library to generate standard PNG bytes encoding `ticktock://passport/{passport_id}`.
- **Public Read-Only Passport Data View (`render_passport_page`)**:
  - Structured view for community and recipient verification: dishes, portion count, dietary status, allergen hints with visual-guess disclaimer, safe-until countdown, current tier, verification gate status, and safety disclaimer.
  - **Privacy Guarantee**: Under no circumstances are donor organization contact names, phone numbers, or exact street addresses exposed on public read-only passport views.

---

## 2. How to Run It
```powershell
# Verify audit chain integrity
python -m app.audit --verify

# Run the complete test suite (94 passing tests)
pytest -q
```

---

## 3. Assumptions Made
- Human consumption pickup is strictly contingent on physical inspection at handover; software cannot certify biological safety without a human in the loop.
- Courier volunteers carry a basic probe thermometer or confirm hot-to-touch status.
- Once a listing is confirmed `DELIVERED`, its state is terminal and cannot be transitioned back to pickup or listed.
- Audit chain links are strictly sequential by `rowid`.

---

## 4. What Is Next
- **Phase 8: Impact Analytics & Prevention Insights (`app/impact.py`, `app/insights.py`)**:
  - Impact metrics: rescued portions by tier, kilograms of food diverted, estimated $\text{CO}_2\text{e}$ avoided, cascade rescue efficiency, average rescue turnaround time, donor leaderboard.
  - Prevention insights: 30-day historical analysis detecting overproduction patterns (e.g. Sunday mess rice) and generating actionable Gemini advice for donors to eliminate surplus at source.
