# Phase 3 Report: Grounded Safety Engine (RAG plus Rules)

**Project**: TickTock Meals  
**Phase**: 3 (Grounded Safety Engine: Deterministic Guardrails & RAG)  
**Date**: October 2026  
**Status**: Completed and Green  

---

## 1. What Was Built
- **Authoritative Knowledge Base (`app/safety/kb/*.md`)**:
  - 10 research-grounded markdown guidance notes sourced from official regulatory standards (FSSAI Regulations, WHO Five Keys to Safer Food, FAO Feed Safety, and Codex Alimentarius).
  - Each note is structured with a machine-parseable `source:` header and section headings.
- **RAG Vector Storage & Retrieval Engine (`app/safety/rag.py`)**:
  - Automatically parses markdown notes, splits into 150–250 word logical chunks, and embeds them into SQLite (`kb_chunks` table).
  - Change detection via SHA-256 file hashing avoids redundant re-indexing.
  - Cosine similarity retrieval over float32 embedding vectors returning top-k relevant chunks with source citations and similarity scores.
  - Deterministic subword/keyword-aware hashing pseudo-embeddings in `MOCK` mode for 100% offline accuracy.
- **Shelf Life Matrix (`app/safety/shelf_life.yaml`)**:
  - Comprehensive (category × storage condition) lookup table mapping food types to base safe holding hours (`hot_held`, `ambient`, `refrigerated`, `unknown`).
  - Strict conservative limits enforced for high-risk categories like `cooked_rice` (3.5h ambient max) and `dairy` (2.5h ambient max).
- **Deterministic Guardrails Engine (`app/safety/rules.py`)**:
  - Pure Python deterministic rule logic completely immune to LLM hallucinations or adversarial prompt injection.
  - Immediate `HIGH` risk escalation and `human_ok=False` upon detecting visible mold or contamination.
  - `MEDIUM` risk enforcement and volunteer inspection triggers for discoloration, off-visual characteristics, open packaging, or urgent expiration windows (< 45 minutes).
  - Time arithmetic powered by `app.clock.now()` to support simulated time-travel.
- **Safety Assessment Coordinator (`app/safety/assess.py`)**:
  - Orchestrates deterministic rules evaluation, guideline retrieval, and advisory LLM synthesis.
  - **Asymmetric Guardrail Principle**: The LLM can only upgrade risk (making decisions safer); if the model attempts to downgrade risk or mark unsafe food as safe, the override is rejected, recorded in the audit reasons as `llm_override_rejected`, and overridden by the rules engine.
- **CLI Utility**:
  - `python -m app.safety.assess <fixture>` prints formatted safety verdicts with risk levels, countdowns, cited regulatory sources, and mandatory disclaimers.

---

## 2. Authoritative Knowledge Base Sources Table

| KB File | Topic | Official Regulatory Source & Section |
| :--- | :--- | :--- |
| `danger_zone_and_time_limits.md` | Temperature Danger Zone (5°C–60°C) & 2h/4h Rule | FSSAI Catering Services Sec. 4.1; WHO Five Keys to Safer Food, Key 3 |
| `hot_holding.md` | Active Hot Holding (>= 60°C) | FSSAI Food Safety Management System (FSMS) Guidelines, Food Service Sec. 5.2 |
| `cooling_and_refrigeration.md` | Two-Stage Cooling & Cold Storage (<= 5°C) | WHO Five Keys to Safer Food, Key 3; FSSAI Schedule 4 Part II Sec. 6.3 |
| `cooked_rice_safety.md` | Bacillus cereus Endospores & Ambient Rice Limits | FSSAI Guidance Note on Cooked Cereals & Rice Sec. 3.2; UK FSA Advisory |
| `dairy_and_curd_safety.md` | Perishable Dairy, Paneer, & Curd Spoilage | FSSAI Dairy Products Safety Standards Sec. 2.1; ICMR Food Safety Manual Sec. 6 |
| `gravy_dishes_and_curries.md` | High-Water-Activity Curries, Dals, & Meat Dishes | FSSAI Mass Catering Guidelines Sec. 4.4; US FDA Food Code Sec. 3-501.16 |
| `packaged_food_and_expiry.md` | Package Integrity, Use-By vs Best-Before Dates | FSSAI Packaging & Labelling Regulations 2020, Reg. 2.4; Codex Standard 1-1985 |
| `cross_contamination_and_containers.md` | Clean Containers, Lids, & Cross-Contamination | WHO Five Keys to Safer Food, Keys 2 & 4; FSSAI Schedule 4 Sec. 3 |
| `animal_feed_exclusions.md` | Toxic Animal Ingredients (Allium, Theobromine, Xylitol) | FAO/WHO National Feed Safety Legislation Sec. 5.1; Vet Toxicology Guide |
| `transport_and_handover.md` | Courier Temperature Checks & Transit Limits | FSSAI Surplus Food Redistribution Guidelines Sec. 7.1; Codex Food Hygiene |

---

## 3. How to Run It
```powershell
# Run safety assessment on a food fixture
python -m app.safety.assess tests/fixtures/cooked_rice_and_sambar.json

# Run all test suites
pytest -q
```

---

## 4. Assumptions Made
- The safety engine acts as an advisory decision-support system and does not replace statutory food-safety certification.
- All human consumption handovers require a volunteer physical inspection step prior to transfer.
- Ambient room temperatures reflect representative tropical conditions (25°C–35°C), necessitating strict 3.5h/2.5h thresholds for ambient rice and dairy.

---

## 5. What Is Next
- **Phase 4: Triage and the Rescue Cascade**:
  - Automated tiering: `HUMAN_RESCUE` -> `ANIMAL_FEED` -> `COMPOST_BIOGAS` -> `DISCARD_UNSAFE`.
  - Continuous simulated clock ticks to automatically trigger cascading reclassification.
  - Audit trail event logging and cancellation of expired candidate matches.
  - CLI `python -m app.triage --demo` simulating a 6-hour rescue cascade in the terminal.
