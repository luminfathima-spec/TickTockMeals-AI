"""TickTock Meals: Main Streamlit Application & Demo Time-Machine.

Expo-ready interface for MERNOVA X 2026:
- Multi-role application (Donor, Map & Routing, Messages, Volunteer, Impact, Audit)
- Simulated Time Machine controls (+15m, +1h, +3h, reset) triggering automatic Rescue Cascade
- Multilingual support (English, Kannada, Hindi) via ui/i18n.py
- 1-Click 'Run Demo Scenario' and 'Load Seed Data' buttons
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import streamlit as st

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.clock import advance as clock_advance, now as get_clock_now, reset as clock_reset, set_fixed as clock_set_fixed
from app.config import get_config
from app.db import (
    create_listing,
    create_match,
    get_connection,
    init_db,
    list_donors,
    list_listings,
    list_recipients,
    list_volunteers,
)
from app.llm.client import LLMClient
from app.llm.messages import queue_match_notifications
from app.matching import find_matches
from app.models import (
    AllergenHint,
    ContainerType,
    Dish,
    DishCategory,
    DonorForm,
    FoodPassport,
    Listing,
    ListingStatus,
    Match,
    StorageCondition,
    Tier,
    VegStatus,
    VisibleFlag,
)
from app.safety.assess import SafetyVerdict
from app.safety.rules import RiskLevel
from app.seed import seed_database
from app.triage import tick
from ui.i18n import UI_LANGUAGES, t
from ui.pages.p1_donor import render_donor_page
from ui.pages.p2_map import render_map_page
from ui.pages.p3_messages import render_messages_page
from ui.pages.p4_volunteer import render_volunteer_page
from ui.pages.p5_impact import render_impact_page
from ui.pages.p6_audit import render_audit_page

# Streamlit Page Config
st.set_page_config(
    page_title="TickTock Meals | AI Food Rescue",
    page_icon="⏱️",
    layout="wide",
    initial_sidebar_state="expanded",
)


def run_demo_scenario(conn: Optional[sqlite3.Connection] = None) -> str:
    """Run an end-to-end demo scenario in one click for expo judges."""
    if conn is None:
        conn = get_connection()
        init_db(conn)

    # 1. Ensure seed data exists
    donors = list_donors(conn=conn)
    if not donors:
        seed_database(conn_or_path=conn, reset=True, seed=42)
        donors = list_donors(conn=conn)

    sample_donor = donors[0]

    # 2. Set clock to realistic lunch surplus hour
    sim_time = datetime(2026, 10, 24, 12, 0, 0)
    clock_set_fixed(sim_time)

    # 3. Create fresh representative surplus listing
    passport = FoodPassport(
        dishes=[
            Dish(
                name="Steamed Sona Masoori Rice & Sambar",
                category=DishCategory.COOKED_RICE,
                veg_status=VegStatus.VEG,
                est_portions=65,
                visible_flags=[VisibleFlag.NONE],
                confidence=0.96,
            )
        ],
        allergen_hints=[AllergenHint.DAIRY],
        photo_quality_ok=True,
        photo_quality_note="Clear catering pots",
        needs_human_check=False,
        handling=DonorForm(
            cooked_at="11:15:00",
            storage=StorageCondition.AMBIENT,
            est_portions=65,
            container=ContainerType.COVERED,
        ),
    )

    verdict = SafetyVerdict(
        safe_until=sim_time + timedelta(hours=2, minutes=45),
        minutes_left=165,
        risk_level=RiskLevel.LOW,
        human_ok=True,
        reasons=["Safe holding window: within 4 hours ambient limit."],
        citations=[],
        requires_human_check=False,
    )

    listing_id = f"list_demo_{datetime.now().strftime('%H%M%S')}"
    listing = Listing(
        id=listing_id,
        donor_id=sample_donor.id,
        created_at=sim_time,
        photo_path="tests/fixtures/cooked_rice_and_sambar.jpg",
        passport=passport.model_dump(mode="json"),
        safety=verdict.model_dump(mode="json"),
        tier=Tier.HUMAN_RESCUE,
        status=ListingStatus.LISTED,
        expires_at=verdict.safe_until,
    )
    create_listing(listing, conn=conn)

    # 4. Generate explainable match
    result = find_matches(listing, now=sim_time, conn=conn)
    if result.candidates:
        cand = result.candidates[0]
        match = Match(
            id=f"m_{listing_id}",
            listing_id=listing.id,
            recipient_id=cand.recipient_id,
            volunteer_id=cand.volunteer_id,
            score=cand.score,
            score_breakdown=cand.score_breakdown,
            why_this_match=cand.why_this_match,
            status="PROPOSED",
            created_at=sim_time,
        )
        create_match(match, conn=conn)

        # 5. Queue multilingual outbox messages
        queue_match_notifications(match, conn=conn, now=sim_time)

    st.session_state["active_listing_id"] = listing_id
    return listing_id


def main() -> None:
    """Main application layout, sidebar time-machine, and multi-page routing."""
    # Ensure database tables exist
    init_db()

    # ---------------- SIDEBAR ----------------
    with st.sidebar:
        st.markdown("## ⏱️ **TickTock Meals**")
        st.caption("Time-Critical Food Rescue Triage & Routing Platform")
        st.markdown(f"`{t('demo_badge')}`")

        st.markdown("---")

        # 1. Language Selector
        lang_code = st.selectbox(
            t("lang_label"),
            options=list(UI_LANGUAGES.keys()),
            format_func=lambda x: UI_LANGUAGES[x],
            key="ui_language_selector",
        )
        st.session_state["lang"] = lang_code

        # 2. LLM Engine Mode & Quota Health
        cfg = get_config()
        llm_client = LLMClient()
        health = llm_client.get_health_status()
        eff_mode = health["effective_mode"].upper()

        if health["quota_exhausted"]:
            st.warning("⚠️ Free-Tier Quota Reached: Operating in offline fallback mode.")
        elif health["near_quota"]:
            st.info("ℹ️ Free-Tier Quota Soft Limit: Operating in cache-only mode.")

        if eff_mode == "LIVE":
            st.success("🟢 AI Mode: LIVE (Gemini API)")
        elif eff_mode == "REPLAY":
            st.info("🟣 AI Mode: REPLAY (Offline Fixtures)")
        elif eff_mode == "CACHE_ONLY":
            st.warning("🟡 AI Mode: CACHE-ONLY (Quota Guard)")
        elif eff_mode == "FALLBACK":
            st.warning("🟠 AI Mode: FALLBACK (Deterministic)")
        else:
            st.info("🔵 AI Mode: MOCK (Deterministic)")

        with st.expander("🩺 System Health & Quota", expanded=False):
            st.markdown(f"**Calls Today:** `{health['daily_calls']} / {health['daily_budget']}`")
            st.markdown(f"**Total Invocations:** `{health['total_calls']}`")
            st.markdown(f"**Cache Hit Rate:** `{health['cache_hit_rate_pct']}%`")
            st.markdown(f"**Avg Latency:** `{health['avg_latency_ms']} ms`")
            if health.get("last_error"):
                st.caption(f"Last error: {health['last_error']}")
            else:
                st.caption("Status: Healthy (zero recent errors)")

        st.markdown("---")

        # 3. Simulated Clock Display
        clock_now = get_clock_now()
        st.markdown(f"**🕒 {t('clock_label', lang_code)}:**")
        st.code(clock_now.strftime("%Y-%m-%d  %I:%M %p"), language="markdown")

        # 4. TIME MACHINE Controls
        st.markdown(f"**⏳ {t('time_machine_label', lang_code)}:**")
        t_col1, t_col2 = st.columns(2)

        with t_col1:
            if st.button(t("btn_add_15m", lang_code), use_container_width=True, key="tm_add_15"):
                clock_advance(15)
                tick(get_clock_now())
                st.rerun()

            if st.button(t("btn_add_3h", lang_code), use_container_width=True, key="tm_add_3h"):
                clock_advance(180)
                tick(get_clock_now())
                st.rerun()

        with t_col2:
            if st.button(t("btn_add_1h", lang_code), use_container_width=True, key="tm_add_1h"):
                clock_advance(60)
                tick(get_clock_now())
                st.rerun()

            if st.button(t("btn_reset_clock", lang_code), use_container_width=True, key="tm_reset"):
                clock_reset()
                st.rerun()

        st.markdown("---")

        # 5. Expo Demo Quick-Triggers
        if st.button("🚀 " + t("btn_run_scenario", lang_code), type="primary", use_container_width=True, key="sidebar_run_scenario"):
            lid = run_demo_scenario()
            st.success(f"Demo scenario active! Listing '{lid}' created, matched, and queued.")
            st.rerun()

        if st.button("📦 " + t("btn_load_demo", lang_code), use_container_width=True, key="sidebar_load_seed"):
            counts = seed_database(reset=True, seed=42)
            st.success(f"Seeded {counts['donors']} donors, {counts['recipients']} recipients, {counts['volunteers']} volunteers.")
            st.rerun()

        st.markdown("---")

        # 6. Page Navigation Radio
        page_options = {
            t("page_donor", lang_code): "donor",
            t("page_map", lang_code): "map",
            t("page_messages", lang_code): "messages",
            t("page_volunteer", lang_code): "volunteer",
            t("page_impact", lang_code): "impact",
            t("page_audit", lang_code): "audit",
        }

        selected_page_label = st.radio(
            t("nav_label", lang_code),
            options=list(page_options.keys()),
            key="main_nav_radio",
        )
        selected_page = page_options[selected_page_label]

    # ---------------- MAIN CONTENT ROUTING ----------------
    if selected_page == "donor":
        render_donor_page(lang=lang_code)
    elif selected_page == "map":
        render_map_page(lang=lang_code)
    elif selected_page == "messages":
        render_messages_page(lang=lang_code)
    elif selected_page == "volunteer":
        render_volunteer_page(lang=lang_code)
    elif selected_page == "impact":
        render_impact_page(lang=lang_code)
    elif selected_page == "audit":
        render_audit_page(lang=lang_code)


if __name__ == "__main__":
    main()
