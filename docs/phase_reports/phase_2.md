# Phase 2 Report: Gemini Vision to Food Passport

**Project**: TickTock Meals  
**Phase**: 2 (Vision to Food Passport)  
**Date**: October 2026  
**Status**: Completed and Green  

---

## 1. What Was Built
- **FoodPassport Domain Schema (`app/models.py`)**:
  - `Dish`: Name, category (`cooked_rice`, `curry_gravy`, `dry_veg`, `dairy`, `bakery`, `fruit_veg_raw`, `packaged`, `sweet`, `beverage`, `other`), vegetarian status (`veg`, `non_veg`, `egg`, `unknown`), portion estimate, visual inspection flags (`spillage`, `discoloration`, `mold`, `off_visual`, `contamination_risk`, `none`), and model confidence score.
  - `FoodPassport`: Aggregated dishes, visual allergen guesses, mandatory volunteer verification note, photo quality status and notes, donor handling form copy, and automated `needs_human_check` indicator.
  - `DonorForm`: Self-reported cooked time, storage temperature (`hot_held`, `ambient`, `refrigerated`, `unknown`), container status (`open`, `covered`, `sealed`, `unknown`), and notes.
- **Image Pipeline & Privacy Protections (`app/llm/vision.py`)**:
  - `preprocess_image`: Automatically scales oversized images down to a maximum side of 1024 px preserving aspect ratio, saving quota and reducing latency.
  - Complete EXIF/GPS metadata stripping: Eliminates embedded camera info and location coordinates prior to downstream handling.
- **Structured Vision Extraction (`app/llm/vision.py`)**:
  - Objective prompt engineering preventing LLM hallucinations: strictly visual description only, forbidden from declaring food "safe", unknown fallback over guessing.
  - Structured output parsing with automatic single retry on malformed outputs, terminating in a fail-safe fallback (`needs_human_check=True`, empty dish list) ensuring the app never crashes.
  - Code-level deterministic `needs_human_check` evaluation: triggers whenever confidence < threshold (default 0.6), visible flags (`discoloration`, `mold`, etc.) exist, or image clarity is insufficient.
- **6 Offline Fixtures & Test Data (`tests/fixtures/`)**:
  - `cooked_rice_and_sambar`: Clear quality, veg, human check not required.
  - `biryani`: Non-veg, chicken biryani, clean containers.
  - `packaged_bread`: Sealed bakery items with gluten allergen tag.
  - `curd_rice_discoloration`: Dairy item with discoloration flag; triggers `needs_human_check = True`.
  - `fruit_salad`: Fresh raw cut fruit.
  - `blurry_photo`: Low lighting, unresolvable container; triggers `needs_human_check = True`.
- **CLI Utility**:
  - `python -m app.llm.vision <image_path> --mode mock`

---

## 2. How to Run It
```powershell
# Run vision extraction CLI on a test fixture
python -m app.llm.vision tests/fixtures/cooked_rice_and_sambar.jpg --mode mock

# Run all test suites
pytest -q
```

---

## 3. Assumptions Made
- Allergen detection from photographs is strictly treated as visual estimation and explicitly labeled with a disclaimer requiring physical on-site volunteer verification.
- Maximum allowable image dimension is capped at 1024 px to optimize Gemini free-tier quota and token consumption.
- If model output validation fails twice, the system safely marks the listing as requiring volunteer verification rather than halting execution.

---

## 4. What Is Next
- **Phase 3: Grounded Safety Engine (RAG plus rules)**:
  - Markdown knowledge base notes in `app/safety/kb/` synthesized from official guidelines (FSSAI, WHO Five Keys to Safer Food).
  - Deterministic rules in `app/safety/rules.py` (danger zone time limits, dairy/rice restrictions, storage rules).
  - RAG vector retrieval in `app/safety/rag.py` using SQLite vector storage and cosine similarity.
  - Guardrail synthesis in `app/safety/assess.py` ensuring deterministic rules can downgrade risk, but the LLM can never upgrade risk or extend safe windows.
