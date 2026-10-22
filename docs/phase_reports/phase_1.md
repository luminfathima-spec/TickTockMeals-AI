# Phase 1 Report: Domain Models, Database, and Seed Data

**Project**: TickTock Meals  
**Phase**: 1 (Domain Models, SQLite Repository, Seeded Bengaluru Network)  
**Date**: October 2026  
**Status**: Completed and Green  

---

## 1. What Was Built
- **Pydantic v2 Domain Models (`app/models.py`)**:
  - `Donor`: Models campus messes, wedding halls, restaurants, canteens, and events with coordinates, typical surplus portions, and active hours.
  - `Recipient`: Models Human NGOs (with dietary rules like `veg_only`), Animal Shelters, and Compost/Biogas plants, with operating hours, capacities, and languages.
  - `Volunteer`: Models couriers with vehicle types (`bike`, `car`), maximum carry capacity, languages (`en`, `kn`, `hi`), and reliability scores.
  - `Listing`: Structured surplus listing with JSON slots for `FoodPassport` and `SafetyVerdict`, status state machine, and expiry countdown.
  - `Match`, `OutboxMessage`, `AuditEvent`: Operational models supporting match scoring, multilingual outbox dispatch, and tamper-evident audit trails.
  - `HistoricalListing`: 30-day historical log table powering source-level prevention insights.
- **SQLite Database Repository (`app/db.py`)**:
  - Zero-ORM lightweight repository layer with parameterized queries, schema creation, foreign key constraints (`PRAGMA foreign_keys = ON;`), and CRUD functions for all domain entities.
- **North Bengaluru Seed Network (`app/seed.py`)**:
  - 8 realistic synthetic donors (mix of hostel messes, convention halls, canteens, restaurants).
  - 12 synthetic recipients: 6 Human NGOs (including `Karuna Sneha Care Home` as strict `veg_only`), 3 Animal Shelters, and 3 Compost/Biogas processors.
  - 10 courier volunteers with realistic vehicle mixes, languages, and capacity profiles.
  - Realistic North Bengaluru coordinates (Yelahanka, Hebbal, Jakkur, Rajanukunte) bounded strictly within 13.00°N–13.25°N and 77.50°E–77.70°E.
  - 122 historical listings over 30 days generated with a deterministic fixed random seed (`42`).
  - Planted pattern: Sunday rice overproduction at Yelahanka Campus Mess (`donor_01`) yields a Sunday average of ~110 portions versus weekday average of ~35 portions.

---

## 2. How to Run It
```powershell
# Reset and seed the database
python -m app.seed --reset

# Run tests
pytest -q
```

---

## 3. Assumptions Made
- All entities, personal names, phone numbers, and organizations are 100% synthetic for demo and evaluation safety.
- North Bengaluru geographic coordinates reflect representative operational clusters between Hebbal and Rajanukunte.
- Tiers are normalized as `HUMAN`, `ANIMAL`, and `COMPOST` across listings and recipients.

---

## 4. What Is Next
- **Phase 2: Gemini Vision to Food Passport**:
  - Transform food photos and donor handling notes into validated structured `FoodPassport` models using Gemini Vision with structured outputs.
  - EXIF/metadata stripping and image resizing.
  - 6 offline test fixtures (cooked rice and sambar, biryani, packaged bread, curd rice with discoloration, fruit salad, blurry photo).
  - CLI `python -m app.llm.vision <image> --mode mock`.
