# Phase 9 Report: Streamlit UI with the Demo Time-Machine

**Project**: TickTock Meals  
**Phase**: 9 (Streamlit UI with the Demo Time-Machine)  
**Date**: October 2026  
**Status**: Completed and Green (109 Tests Passing)  

---

## 1. What Was Built

### A. Main Application & Time-Machine (`ui/streamlit_app.py`)
- **Expo-Ready Sidebar Controls**:
  - **Simulated Clock Display**: Live display of simulated datetime (`%Y-%m-%d %I:%M %p`).
  - **Demo Time-Machine**: Interactive controls (`+15 min`, `+1 hr`, `+3 hr`, `Reset Clock`) coupled directly with `app.triage.tick(now)` to allow judges to witness real-time cascade tier degradation (`HUMAN_RESCUE` $\to$ `ANIMAL_FEED` $\to$ `COMPOST_BIOGAS`) in under 2 minutes.
  - **1-Click Demo Scenario Trigger**: Instantly loads seed data, sets the clock to lunch surplus time (12:00 PM), generates a fresh surplus listing with verified Food Passport, computes the top explainable match, and queues localized WhatsApp/SMS notifications.
  - **1-Click Seed Data Loader**: Repopulates the SQLite database with deterministic synthetic North Bengaluru donors, recipients, and couriers.
  - **Language Selector**: Live multilingual UI switching between English, Kannada, and Hindi.
  - **AI Mode Indicator**: Visual indicator showing whether the platform is running in live Gemini API mode (`🟢 LIVE`) or deterministic mock mode (`🔵 MOCK`).

### B. Six Multi-Role Pages (`ui/pages/`)
1. **Donor Portal (`p1_donor.py`)**:
   - Sample fixture photo selector (`cooked_rice_and_sambar.jpg`, `paneer_butter_masala.jpg`, etc.) or user photo upload (1–3 files).
   - Preparation time, storage condition, estimated portions, and packaging container inputs.
   - Live **Verifiable Food Passport Card** displaying dishes recognized by Gemini Vision, allergen indicators, safe holding countdown, assigned rescue tier, cascade timeline bar, grounded FSSAI regulation citations, and cryptographically signed QR code.
2. **Network Map & Routing (`p2_map.py`)**:
   - High-contrast **Pydeck Geospatial 3D Map** plotting donors (orange circles), recipients color-coded by tier (green for human shelters, yellow for gaushalas, purple for compost plants), volunteers (blue markers), and multi-leg transit route arcs.
   - Active listing selector with dynamic countdown banner.
   - **"Why this match?"** transparent score breakdown horizontal bar chart (`urgency`, `proximity`, `capacity`, `diet`, `hours`).
   - Disqualified candidate rejection explanations table (e.g., "Transit ETA exceeds safe holding window").
3. **Messages & Outbox (`p3_messages.py`)**:
   - Outbox feed displaying generated messages in recipient-preferred languages (English, Kannada, Hindi).
   - Interactive simulation buttons (`Accept`, `Decline (Escalate)`, `Simulate Timeout`) to demonstrate automated tier cascade escalation live.
4. **Volunteer & Verification Gate (`p4_volunteer.py`)**:
   - Courier task card with privacy guard (broad pickup area only before acceptance; exact street address and contact revealed only upon acceptance).
   - **Physical Verification Gate Checklist**: Mandatory checks (container integrity, temperature check, sensory odor check, visual insect/spoilage check).
   - Automated failsafe cascade reclassification if any check fails during pickup.
   - Recipient handover verification with tamper-evident audit logging.
5. **Impact & Prevention Insights (`p5_impact.py`)**:
   - 5 KPI summary tiles: Total Portions Rescued, Landfill Weight Diverted (kg), Avoided Emissions ($\text{kg CO}_2\text{e}$), Cascade Salvage Efficiency (%), and Average Response Turnaround.
   - Interactive Plotly visualizations: Donut chart of recovery by tier, daily 30-day rescue volume trend.
   - Shelter capacity utilization progress bars and donor recognition leaderboard.
   - Grounded calculation assumptions expander (FAO Food Waste Index, UNEP factors).
   - Source-level waste prevention report generator powered by Gemini with strict quantitative consistency guardrails.
6. **Tamper-Evident Audit Chain (`p6_audit.py`)**:
   - Real-time cryptographic chain integrity status badge (`✓ VERIFIED` or `⚠ TAMPER DETECTED`).
   - Interactive **"Demonstrate Tamper Detection"** button that alters a past database row to prove instant cryptographic invalidation of the SHA-256 hash chain to expo judges.
   - **"Restore Chain Integrity"** button to recompute and re-verify hashes.
   - Full chronological audit event log table with expandable hash payload inspection.

### C. Internationalization (`ui/i18n.py`)
- Full triple-language dictionary covering all navigation titles, card headers, form labels, buttons, metric labels, and system status messages:
  - English (`en`)
  - Kannada (`kn` — ಕನ್ನಡ)
  - Hindi (`hi` — हिन्दी)
- Missing-key fallback to English and keyword string interpolation helper `t(key, lang, **kwargs)`.

---

## 2. Example Outputs & UI State

### Demo Scenario Run Payload
```json
{
  "listing_id": "list_demo_120000",
  "donor": "Presidency Campus Central Mess",
  "dish": "Steamed Sona Masoori Rice & Sambar (65 portions)",
  "storage": "AMBIENT (covered pots)",
  "safety_verdict": {
    "safe_until": "2026-10-24T14:45:00",
    "risk_level": "LOW",
    "human_ok": true,
    "minutes_left": 165
  },
  "match_candidate": {
    "recipient": "Shanti Bhavan Children Home (Yelahanka)",
    "volunteer": "Suresh Kumar (Hero Splendor)",
    "composite_score": 0.885,
    "eta_minutes": 22.4
  },
  "notifications_queued": 2
}
```

### Headless AppTest Test Coverage
- Verified clean page rendering without exceptions across all 6 roles.
- Verified interactive language selection persistence across state.
- Verified Time Machine buttons advance simulated time by exact intervals (`+15 min`, `+1 hr`).
- Verified end-to-end scenario generation logic with database persistence and notification queueing.

---

## 3. How to Run It

```powershell
# Launch the interactive Streamlit UI in browser
streamlit run ui/streamlit_app.py

# Run all 109 test suites offline
pytest -q

# Run Phase 9 UI test suite specifically
pytest tests/test_phase9_ui.py -v
```

---

## 4. Assumptions Made
- Default UI language initializes to English, allowing instant runtime switching via the sidebar.
- Pydeck map defaults center point to Presidency University, Rajanukunte, North Bengaluru (`lat: 13.168, lon: 77.534`).
- Streamlit headless tests utilize `streamlit.testing.v1.AppTest` for automated CI/regression testing.

---

## 5. What Is Next
- **Phase 10: Free-Tier Resilience & Offline Demo Mode**:
  - Gemini free-tier quota tracking and adaptive rate-limiter guard.
  - 4-level graceful degradation matrix (Live Gemini $\to$ Cached JSON $\to$ Deterministic Rules $\to$ Offline Synthetic Demo).
  - Pre-recorded 90-second deterministic demo scenario replay for fail-safe expo booth presentations.
  - Automated pre-commit secrets scanner to ensure zero API keys or credentials can ever be committed.
