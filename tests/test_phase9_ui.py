"""Tests for Phase 9: Streamlit UI and Demo Time-Machine.

Verifies:
1. Internationalization dictionary completeness and interpolation fallback.
2. Headless Streamlit AppTest rendering for initial load and all 6 navigation tabs.
3. Time Machine controls (+15 min, +1 hr, +3 hr, reset) and clock advance integration.
4. One-click demo scenario generation and database seeding integration.
5. Multi-language UI switching (English, Kannada, Hindi).
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest

from app.clock import now as get_clock_now, reset as clock_reset, set_fixed as clock_set_fixed
from app.db import get_connection, get_listing, init_db, list_matches, list_outbox_messages
from app.seed import seed_database
from ui.i18n import TRANSLATIONS, UI_LANGUAGES, t
from ui.streamlit_app import run_demo_scenario

APP_SCRIPT_PATH = str((Path(__file__).resolve().parent.parent / "ui" / "streamlit_app.py").resolve())


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Ensure a clean, isolated SQLite DB and deterministic clock for each test."""
    test_db = tmp_path / "test_ui.db"
    monkeypatch.setenv("DATABASE_URL", str(test_db))
    monkeypatch.setenv("LLM_MODE", "mock")
    clock_reset()
    conn = get_connection(str(test_db))
    init_db(conn)
    seed_database(conn_or_path=conn, reset=True, seed=42)
    conn.close()
    yield
    clock_reset()


# ==============================================================================
# 1. Internationalization (i18n) Tests
# ==============================================================================

def test_i18n_supported_languages():
    """Verify supported language codes and labels."""
    assert "en" in UI_LANGUAGES
    assert "kn" in UI_LANGUAGES
    assert "hi" in UI_LANGUAGES
    assert "Kannada" in UI_LANGUAGES["kn"]
    assert "Hindi" in UI_LANGUAGES["hi"]


def test_i18n_key_completeness():
    """Verify that all keys in English have corresponding entries in Kannada and Hindi."""
    en_keys = set(TRANSLATIONS["en"].keys())
    kn_keys = set(TRANSLATIONS["kn"].keys())
    hi_keys = set(TRANSLATIONS["hi"].keys())

    missing_in_kn = en_keys - kn_keys
    missing_in_hi = en_keys - hi_keys

    assert not missing_in_kn, f"Missing Kannada keys: {missing_in_kn}"
    assert not missing_in_hi, f"Missing Hindi keys: {missing_in_hi}"
    assert len(en_keys) >= 40, "Translation catalog must cover all core UI elements"


def test_i18n_translations_content():
    """Verify proper localization values for primary branding and headers."""
    assert t("app_title", "en") == "TickTock Meals"
    assert t("app_title", "kn") == "ಟಿಕ್‌ಟಾಕ್ ಮೀಲ್ಸ್"
    assert t("app_title", "hi") == "टिकटॉक मील्स"

    # Verify fallback for unknown language code
    assert t("app_title", "es") == "TickTock Meals"

    # Verify fallback for non-existent key
    assert t("non_existent_key_xyz", "en") == "non_existent_key_xyz"


def test_i18n_interpolation():
    """Verify keyword interpolation and graceful fallback when formatting fails."""
    # Test valid interpolation
    TRANSLATIONS["en"]["_test_greeting"] = "Hello, {name}!"
    assert t("_test_greeting", "en", name="Bengaluru") == "Hello, Bengaluru!"

    # Test missing format key does not raise exception
    assert t("_test_greeting", "en") == "Hello, {name}!"


# ==============================================================================
# 2. Streamlit UI AppTest Headless Smoke Tests
# ==============================================================================

def test_streamlit_initial_load():
    """Verify main Streamlit app loads cleanly without unhandled exceptions."""
    at = AppTest.from_file(APP_SCRIPT_PATH)
    at.run()
    assert len(at.exception) == 0, f"App raised exceptions on load: {at.exception}"


def test_streamlit_navigation_all_pages():
    """Verify user can navigate to each of the 6 roles/pages without errors."""
    at = AppTest.from_file(APP_SCRIPT_PATH)
    at.run()
    assert len(at.exception) == 0

    page_keys = [
        "page_donor",
        "page_map",
        "page_messages",
        "page_volunteer",
        "page_impact",
        "page_audit",
    ]

    for pkey in page_keys:
        label = t(pkey, "en")
        at.radio(key="main_nav_radio").set_value(label).run()
        assert len(at.exception) == 0, f"Exception rendering page '{pkey}': {at.exception}"


def test_streamlit_language_selector():
    """Verify switching languages updates session state without error."""
    at = AppTest.from_file(APP_SCRIPT_PATH)
    at.run()
    assert len(at.exception) == 0

    # Select Kannada
    at.selectbox(key="ui_language_selector").set_value("kn").run()
    assert len(at.exception) == 0
    assert at.session_state["lang"] == "kn"

    # Select Hindi
    at.selectbox(key="ui_language_selector").set_value("hi").run()
    assert len(at.exception) == 0
    assert at.session_state["lang"] == "hi"


# ==============================================================================
# 3. Demo Time-Machine & Quick-Trigger Tests
# ==============================================================================

def test_streamlit_time_machine_buttons():
    """Verify Time Machine sidebar buttons advance the simulated clock and rerun cleanly."""
    at = AppTest.from_file(APP_SCRIPT_PATH)
    at.run()
    assert len(at.exception) == 0

    # Test +15 min
    before_15 = get_clock_now()
    at.button(key="tm_add_15").click().run()
    assert len(at.exception) == 0
    after_15 = get_clock_now()
    assert (after_15 - before_15).total_seconds() == 900

    # Test +1 hr
    before_1h = get_clock_now()
    at.button(key="tm_add_1h").click().run()
    assert len(at.exception) == 0
    after_1h = get_clock_now()
    assert (after_1h - before_1h).total_seconds() == 3600

    # Test Reset
    at.button(key="tm_reset").click().run()
    assert len(at.exception) == 0


def test_run_demo_scenario_logic(monkeypatch, tmp_path):
    """Verify the one-click demo scenario creates listing, match, and queued notifications."""
    test_db = tmp_path / "scenario_db.db"
    monkeypatch.setenv("DATABASE_URL", str(test_db))
    conn = get_connection(str(test_db))
    init_db(conn)

    listing_id = run_demo_scenario(conn=conn)
    assert listing_id.startswith("list_demo_")

    # Verify listing was persisted
    listing = get_listing(listing_id, conn=conn)
    assert listing is not None
    assert listing.id == listing_id
    assert listing.tier.value == "HUMAN_RESCUE"
    assert listing.status.value == "LISTED"

    # Verify match was created
    matches = list_matches(listing_id=listing_id, conn=conn)
    assert len(matches) >= 1
    assert matches[0].status == "PROPOSED"
    assert matches[0].score > 0.0

    # Verify messages were queued in outbox
    msgs = list_outbox_messages(conn=conn)
    listing_msgs = [m for m in msgs if listing_id in m.message_text or m.recipient_or_volunteer_id == matches[0].recipient_id or m.match_id == matches[0].id]
    assert len(listing_msgs) >= 1
    conn.close()
