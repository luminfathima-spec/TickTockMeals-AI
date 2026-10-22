# Phase 0 Report: Scaffold and Config

**Project**: TickTock Meals  
**Phase**: 0 (Scaffold, Config, SimClock, LLM Client Shell)  
**Date**: October 2026  
**Status**: Completed and Green  

---

## 1. What Was Built
- **Project Folder Structure**:
  - `app/`, `app/llm/`, `app/safety/`, `app/safety/kb/`
  - `ui/`, `eval/`, `tests/`, `tests/fixtures/`, `docs/`, `docs/phase_reports/`, `data/`
- **Environment & Dependencies**:
  - `requirements.txt` configured with Python 3.13 verified packages (`google-genai`, `pydantic>=2`, `python-dotenv`, `pyyaml`, `numpy`, `scipy`, `streamlit`, `pydeck`, `pandas`, `plotly`, `qrcode[pil]`, `pytest`, `tenacity`).
  - Virtual environment `.venv` created and all dependencies successfully installed.
- **Rules & Safety**:
  - `PROJECT_RULES.md` created with engineering guidelines, zero-leak credential requirements, simulated time control, and deterministic test requirements.
  - `.gitignore` and `.env.example` prepared.
- **Configuration Engine (`app/config.py` & `config.yaml`)**:
  - Strict Pydantic v2 typed schema loader for application configuration.
  - Validates model keys, transit speeds, scoring weights, impact factors, and rate limiter.
  - Environment variable override support (`LLM_MODE`).
  - Zero hardcoding of model names in Python source files.
- **Simulated Clock (`app/clock.py`)**:
  - `SimClock` class with `now()`, `advance(minutes)`, `reset()`, and `set_fixed(dt)` functions.
  - Enables simulated time-machine jumps for triage and deterministic testing.
- **Unified LLM Client Shell (`app/llm/client.py`)**:
  - Single gateway for all LLM calls.
  - Sliding-window per-minute rate limiter.
  - Disk-backed response cache (`.cache/`) keyed by SHA-256 of `(model, prompt, image bytes)`.
  - Exponential backoff retry via `tenacity`.
  - Deterministic offline `MOCK` mode returning consistent fixtures and pseudo-embeddings without touching the network.
  - Isolated live integration with `google-genai`.

---

## 2. How to Run It
```powershell
# Activate environment
.\.venv\Scripts\Activate.ps1

# Run tests
pytest -q
```

---

## 3. Assumptions Made
- Default `LLM_MODE` is `"mock"` to ensure complete offline safety and protect API quota.
- Configurable impact factors (`kg_per_meal: 0.4`, `co2e_kg_per_kg_food: 2.5`) and average transit speeds (`bike: 22.0`, `car: 25.0`) are documented assumptions subject to bibliography references in `README.md`.
- No Gemini API key is stored or committed.

---

## 4. What Is Next
- **Phase 1: Domain models, SQLite database, and seeded Bengaluru network**:
  - Define Pydantic domain models: `Donor`, `Recipient`, `Volunteer`, `Listing`, `Match`, `OutboxMessage`, `AuditEvent`.
  - SQLite database layer with parameterized queries.
  - Seed script generating realistic synthetic data around north Bengaluru (Yelahanka, Hebbal, Rajanukunte, Jakkur) with 30 days of historical patterns.
