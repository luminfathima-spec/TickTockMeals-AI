# Phase 8 Report: Impact Analytics & Prevention Insights

**Project**: TickTock Meals  
**Phase**: 8 (Impact Analytics and Source-Level Prevention Insights)  
**Date**: October 2026  
**Status**: Completed and Green (100 Tests Passing)  

---

## 1. What Was Built
- **Transparent Impact Engine (`app/impact.py`)**:
  - Computes multi-tier surplus recovery across delivered listings and historical logs:
    - **Portions Rescued by Tier**: `HUMAN_RESCUE`, `ANIMAL_FEED`, `COMPOST_BIOGAS`.
    - **Food Diverted from Landfill**: Kilograms calculated using configurable benchmark (`kg_per_meal: 0.40 kg`).
    - **Avoided Greenhouse Gas Emissions**: Kilograms of $\text{CO}_2\text{e}$ avoided using configurable factor (`co2e_kg_per_kg_food: 2.5`).
    - **Cascade Rescue Efficiency**: Percentage of food "saved by cascade" (diverted to animal feed or compost when human rescue window closed, rather than being discarded).
    - **Operational Turnaround Times**: Average duration from listing to match, and average duration from listing to completed delivery.
    - **Recipient Shelter Absorption**: Utilization tracking against daily capacity thresholds.
    - **Donor Leaderboard**: Transparent ranking of donors by volume rescued, weight diverted, and emissions avoided.
    - **DataFrames for Visualizations**: Generates clean pandas DataFrames (`by_tier`, `donors`, `recipients`, `daily_trend`) directly usable by Streamlit / Plotly.
    - **Assumptions Transparency**: Configurable impact factors and source citations (FAO Food Waste Index, UNEP) are explicitly embedded in every output dictionary.
- **Source-Level Prevention Insights Engine (`app/insights.py`)**:
  - Analyzes 30 days of historical donation logs by weekday and food category.
  - Computes descriptive statistics (count, mean portions, standard deviation, weekday effect vs category baseline).
  - **Planted Pattern Detection**: Automatically pinpoints the hostel mess (`donor_01`) overproduction pattern:
    - Sunday steamed rice surplus averages **115.5 portions** vs weekday average of **35.2 portions** (a **+228.1%** spike).
  - **Strict Number-Consistency Guardrail (`validate_insights_report`)**:
    - Post-check parser extracts all quantitative values in generated reports.
    - If any unapproved or hallucinated number is cited, the report is rejected and cleanly falls back to the deterministic template.
  - **Clearly Labelled Projections**:
    - Generates monthly waste reduction estimates (e.g., 15% reduction in weekend rice batches yields ~75 portions / 30.0 kg food / 75.0 kg $\text{CO}_2\text{e}$ saved monthly).
    - Explicitly labelled: `ESTIMATED PROJECTION (not a promise)`.

---

## 2. Example Outputs

### Example Impact Summary
```json
{
  "total_portions_rescued": 100,
  "total_kg_rescued": 40.0,
  "total_co2e_avoided_kg": 100.0,
  "portions_by_tier": {
    "HUMAN_RESCUE": 50,
    "ANIMAL_FEED": 30,
    "COMPOST_BIOGAS": 20
  },
  "cascade_saved_portions": 50,
  "cascade_saved_pct": 50.0,
  "avg_time_to_match_minutes": 14.5,
  "avg_time_to_delivery_minutes": 42.0,
  "assumptions": {
    "kg_per_meal": 0.4,
    "co2e_kg_per_kg_food": 2.5,
    "source_kg_per_meal": "FAO Food Waste Index (standard portion weight 0.40 kg / meal)",
    "source_co2e_factor": "UNEP / Our World in Data (2.5 kg CO2e avoided per kg food rescued)",
    "notes": "Configured in config.yaml under impact_factors; adjustable."
  }
}
```

### Example Prevention Insight Report (Hostel Mess `donor_01`)
```text
TickTock Meals Source Prevention Report for Presidency Campus Central Mess:

Key Waste Finding:
Analysis of 30-day production history reveals a significant surplus spike on Sundays. Cooked Rice surplus averages 115.5 portions on Sundays compared to a weekday average of 35.2 portions (a 228.1% increase).

Concrete Operational Recommendations:
1. Target Weekend Batch Reduction: Scale back Sunday cooked rice preparation volume by 15% to align with reduced resident attendance.
2. Attendance Forecasting: Implement pre-weekend sign-outs to capture resident departure trends before preparation begins.
3. Menu Repurposing: Introduce planned recipes on subsequent mornings that safely incorporate leftover grain portions.

ESTIMATED PROJECTION (not a promise): Implementing recommended 15% batch reductions can prevent approximately 75 portions (30.0 kg) of food surplus and avoid 75.0 kg CO2e monthly.
```

---

## 3. How to Run It
```powershell
# Run all test suites (100 passing tests)
pytest -q

# Run Phase 8 test suite specifically
pytest tests/test_phase8_impact_insights.py -v
```

---

## 4. Assumptions Made
- Standard portion weight is 0.40 kg (FAO Food Waste Index benchmark).
- Food rescue emission avoidance factor is 2.5 kg $\text{CO}_2\text{e}$ per kg food diverted from landfill/spoilage (UNEP / Our World in Data).
- Cascade savings metric accounts for surplus rescued through non-human tiers (animal feed and compost/biogas) that would otherwise have been discarded.

---

## 5. What Is Next
- **Phase 9: Streamlit UI with the Demo Time-Machine (`ui/streamlit_app.py`, `ui/pages/`, `ui/i18n.py`)**:
  - Full interactive multi-role application:
    1. Donor: photo upload, Food Passport card, safe-until countdown, citations, QR code.
    2. Network Map: Pydeck geospatial map with delivery routes, live listings, match scoring breakdowns, and rejected candidate explanations.
    3. Messages: localized outbox view with simulated `ACCEPT` / `DECLINE` buttons triggering real-time escalation.
    4. Volunteer: task card, physical verification gate checklist, and delivery confirmation.
    5. Impact & Insights: KPI tiles, Plotly charts by tier/week, and donor prevention report generator.
    6. Audit: tamper-evident hash-chain status badge with interactive "Tamper with row" demo button.
  - Interactive **Time Machine**: `+15 min`, `+1 hr`, `+3 hr`, and `reset` buttons to demonstrate the Rescue Cascade live in 2 minutes.
  - Multilingual support (English, Kannada, Hindi).
