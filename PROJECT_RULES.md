# PROJECT RULES - TickTock Meals

You are my senior pair-programmer on a project called TickTock Meals: a generative-AI food-rescue triage and routing platform for my final-year MCA expo (MERNOVA X 2026, Presidency University, Bangalore).

## ENVIRONMENT
- Windows, PowerShell, Python 3.13, pytest, git. Give PowerShell commands only. Use a virtual environment (.venv).
- Project root folder: ticktockmeals. Follow the folder layout given per phase.

## PROJECT RULES (apply to every phase)
1. Work ONLY on the phase I name. Do not start later phases or add unrequested features.
2. FREE TIER ONLY. Use Gemini through the google-genai SDK. Read GEMINI_API_KEY from .env using python-dotenv. Never hardcode, log or print the key. Make sure .env is in .gitignore.
3. Every LLM call goes through ONE module, app/llm/client.py, which provides: retry with exponential backoff, a per-minute rate limiter, an on-disk response cache keyed by a hash of (model, prompt, image bytes), call counters, and a MOCK mode. LLM_MODE=mock must return deterministic fixtures so the whole test-suite runs offline with no network.
4. Model names live ONLY in config.yaml (MODEL_FAST, MODEL_VISION, MODEL_EMBED). No model name may appear in code.
5. Use Pydantic v2 models for all structured data, type hints everywhere, short docstrings, small functions, no global mutable state except the config and the DB connection factory.
6. All time logic uses app/clock.py (a simulated clock that can be advanced) instead of calling datetime.now() directly. This powers the demo time-machine.
7. Synthetic data only. No real people, phone numbers, organisations or addresses. Use clearly fictional names around north Bengaluru.
8. SAFETY PRINCIPLE: AI output is advisory. Deterministic rules may DOWNGRADE a safety verdict, but the LLM may never upgrade a verdict that the rules marked unsafe. Any HUMAN_RESCUE handover requires a human verification step.
9. Each phase must end with: (a) pytest passing, (b) a short PHASE REPORT in docs/phase_reports/phase_N.md (what was built, how to run it, assumptions made, what is next), and (c) one git commit with message "phase N: <summary>".
10. If something is ambiguous, choose the simplest reasonable option, state the assumption in the phase report, and continue. Only stop to ask me if you are truly blocked.
11. Write meaningful tests, not placeholders. Prefer table-driven tests for decision logic.
