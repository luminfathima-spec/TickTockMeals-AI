"""Donor Portal & Food Passport Generator page for TickTock Meals."""

from __future__ import annotations

import io
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from PIL import Image
import streamlit as st

from app.clock import now as get_clock_now
from app.db import create_listing, get_connection, list_donors, list_listings
from app.handover import generate_passport_qr
from app.llm.vision import extract_passport
from app.models import (
    ContainerType,
    DonorForm,
    Listing,
    ListingStatus,
    StorageCondition,
    Tier,
)
from app.safety.assess import assess_safety
from app.triage import cascade_timeline, evaluate_triage
from ui.i18n import t

FIXTURE_DIR = Path("tests/fixtures")
SAMPLE_FIXTURES: Dict[str, str] = {
    "Steamed Rice & Sambar (Fresh, Low Risk)": "cooked_rice_and_sambar.jpg",
    "Chicken Dum Biryani (Non-Veg, Fresh)": "biryani.jpg",
    "Packaged Whole Wheat Bread (Packaged)": "packaged_bread.jpg",
    "Curd Rice (Discolored, High Risk)": "curd_rice_discoloration.jpg",
    "Fresh Fruit Salad (Perishable)": "fruit_salad.jpg",
    "Blurry Catering Shot (Low Quality)": "blurry_photo.jpg",
}


def render_donor_page(lang: str = "en") -> None:
    """Render the Food Donor Portal and Passport Generator."""
    st.title(t("donor_header", lang))
    st.caption(t("donor_sub", lang))

    conn = get_connection()
    donors = list_donors(conn=conn)
    clock_now = get_clock_now()

    if not donors:
        st.warning("No donors found in the network. Please click 'Load Seed Data' in the sidebar.")
        return

    col_form, col_preview = st.columns([1, 1], gap="large")

    with col_form:
        st.subheader("1. " + t("select_donor", lang))
        donor_names = {f"{d.name} ({d.area})": d for d in donors}
        chosen_name = st.selectbox(
            t("select_donor", lang),
            options=list(donor_names.keys()),
            key="donor_select_box",
            label_visibility="collapsed",
        )
        selected_donor = donor_names[chosen_name]

        st.markdown("---")
        st.subheader("2. Surplus Food Photograph")
        photo_mode = st.radio(
            "Image Source",
            options=["Select Pre-Recorded Fixture", "Upload New Photo"],
            horizontal=True,
            key="photo_mode_radio",
        )

        selected_image_bytes: Optional[bytes] = None
        selected_image_filename = "upload.jpg"

        if photo_mode == "Select Pre-Recorded Fixture":
            fixture_choice = st.selectbox(
                t("select_fixture", lang),
                options=list(SAMPLE_FIXTURES.keys()),
                key="fixture_select",
            )
            rel_path = FIXTURE_DIR / SAMPLE_FIXTURES[fixture_choice]
            if rel_path.exists():
                selected_image_bytes = rel_path.read_bytes()
                selected_image_filename = SAMPLE_FIXTURES[fixture_choice]
                st.image(selected_image_bytes, caption=fixture_choice, width=320)
        else:
            uploaded_file = st.file_uploader(
                t("upload_photos", lang),
                type=["jpg", "jpeg", "png"],
                key="donor_file_uploader",
            )
            if uploaded_file is not None:
                selected_image_bytes = uploaded_file.getvalue()
                selected_image_filename = uploaded_file.name
                st.image(selected_image_bytes, caption="Uploaded Surplus Photo", width=320)

        st.markdown("---")
        st.subheader("3. Handling & Cooking Metadata")

        c1, c2 = st.columns(2)
        with c1:
            # Default cooked_at to 1 hour prior to simulated clock
            default_cooked_time = (clock_now - timedelta(hours=1)).time()
            cooked_time = st.time_input(
                t("cooked_at", lang),
                value=default_cooked_time,
                key="donor_cooked_time",
            )
            storage_cond = st.selectbox(
                t("storage_condition", lang),
                options=[s.value for s in StorageCondition],
                index=0,
                key="donor_storage_cond",
            )

        with c2:
            est_portions = st.number_input(
                t("est_portions", lang),
                min_value=5,
                max_value=1000,
                value=selected_donor.typical_surplus_portions or 50,
                step=5,
                key="donor_est_portions",
            )
            container = st.selectbox(
                t("container_type", lang),
                options=[c.value for c in ContainerType if c.value != "unknown"],
                index=0,
                key="donor_container",
            )

        notes = st.text_input(
            t("donor_notes", lang),
            placeholder="e.g., Prepared for hostel lunch; kept in warm food-grade vessel",
            key="donor_notes_input",
        )

        submit_btn = st.button(
            t("btn_create_passport", lang),
            type="primary",
            use_container_width=True,
            key="btn_create_passport_trigger",
        )

    # Submission Action
    if submit_btn:
        if not selected_image_bytes:
            st.error("Please select a sample photo or upload an image.")
            return

        with st.spinner("Analyzing surplus with Gemini Vision & assessing safety guidelines..."):
            # 1. Build DonorForm
            cooked_at_str = cooked_time.strftime("%H:%M:%S")
            donor_form = DonorForm(
                cooked_at=cooked_at_str,
                storage=StorageCondition(storage_cond),
                est_portions=int(est_portions),
                container=ContainerType(container),
                notes=notes or None,
            )

            # 2. Extract Passport via Gemini Vision
            # Save temporary image file for vision extractor
            temp_img_path = FIXTURE_DIR / selected_image_filename
            if not temp_img_path.exists():
                temp_img_path = Path(".cache") / f"temp_{selected_image_filename}"
                temp_img_path.parent.mkdir(parents=True, exist_ok=True)
                temp_img_path.write_bytes(selected_image_bytes)

            passport = extract_passport(photos=[str(temp_img_path)], form=donor_form)

            # 3. Grounded Safety Assessment
            safety = assess_safety(passport=passport)

            # 4. Triage & Rescue Cascade Tier
            triage_dec = evaluate_triage(passport, safety, reference_time=clock_now)

            # 5. Create Listing
            listing_id = f"list_{datetime.now().strftime('%m%d_%H%M%S')}"
            listing = Listing(
                id=listing_id,
                donor_id=selected_donor.id,
                created_at=clock_now,
                photo_path=str(temp_img_path),
                passport=passport.model_dump(mode="json"),
                safety=safety.model_dump(mode="json"),
                tier=triage_dec.tier,
                status=ListingStatus.LISTED,
                expires_at=safety.safe_until,
            )
            create_listing(listing, conn=conn)

            # Store in session state for instant view
            st.session_state["active_listing_id"] = listing_id
            st.success(f"Food Passport '{listing_id}' created and verified successfully!")

    # Display Preview Column
    with col_preview:
        st.subheader(t("passport_card_title", lang))

        # Check if an active listing is selected
        active_lid = st.session_state.get("active_listing_id")
        all_listings = list_listings(conn=conn)

        if not active_lid and all_listings:
            active_lid = all_listings[0].id

        if not active_lid:
            st.info("Submit the donor form on the left or click 'Run Demo Scenario' to view a live Passport card.")
            return

        target_listing = next((l for l in all_listings if l.id == active_lid), None)
        if not target_listing or not target_listing.passport:
            st.info("No passport data available.")
            return

        p_data = target_listing.passport
        s_data = target_listing.safety or {}

        # Passport Card
        with st.container(border=True):
            st.markdown(f"### 📋 Food Passport: `{target_listing.id}`")
            st.caption(f"Donor Facility: **{selected_donor.name}** ({selected_donor.area})")

            # Dietary & Dish Information
            dishes = p_data.get("dishes", [])
            for d in dishes:
                v_badge = "🟢 Pure Veg" if d.get("veg_status") == "veg" else ("🔴 Non-Veg" if d.get("veg_status") == "non_veg" else "🟡 Contains Egg")
                st.markdown(f"- **{d.get('name', 'Surplus Meal')}** — {d.get('est_portions', 0)} portions `{v_badge}`")

            # Allergens & Disclaimers
            hints = p_data.get("allergen_hints", [])
            hints_str = ", ".join(hints) if hints else "None detected"
            st.info(f"**Allergens (visual estimate):** {hints_str}\n\n*{p_data.get('allergen_note', '')}*")

            # Safe Window Countdown & Tier Badge
            c_left, c_right = st.columns(2)
            with c_left:
                risk_lvl = s_data.get("risk_level", "LOW")
                risk_color = "🟢" if risk_lvl == "LOW" else ("🟡" if risk_lvl == "MEDIUM" else "🔴")
                st.metric(
                    label=t("safe_window_title", lang),
                    value=f"{target_listing.expires_at.strftime('%I:%M %p') if target_listing.expires_at else 'Urgent'}",
                    delta=f"{risk_color} Risk: {risk_lvl}",
                )

            with c_right:
                tier_badge = target_listing.tier.value if isinstance(target_listing.tier, Tier) else str(target_listing.tier)
                st.metric(
                    label=t("triage_tier_title", lang),
                    value=tier_badge.replace("_", " "),
                    delta="State: " + target_listing.status.value,
                )

            # Cascade Timeline Bar
            st.markdown("---")
            st.markdown(f"**{t('cascade_timeline_title', lang)}:**")
            timeline_str = cascade_timeline(target_listing)
            st.code(timeline_str, language="markdown")

            # Expandable Citations
            citations = s_data.get("citations", [])
            with st.expander(t("citations_title", lang), expanded=False):
                if citations:
                    for cite in citations:
                        st.markdown(f"**[{cite.get('chunk_id')}]** *{cite.get('source')}*")
                        st.markdown(f"> {cite.get('text', '')}")
                else:
                    reasons = s_data.get("reasons", ["Deterministic safety rule limits applied."])
                    for r in reasons:
                        st.markdown(f"- {r}")

            # QR Code Display
            st.markdown("---")
            st.markdown(f"**{t('qr_code_title', lang)}:**")
            qr_bytes = generate_passport_qr(target_listing.id)
            st.image(qr_bytes, caption=f"Passport Token: ticktock://passport/{target_listing.id}", width=180)
