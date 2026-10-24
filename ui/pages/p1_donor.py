"""Donor Portal & Food Passport Generator page for TickTock Meals.

Empowers anyone, anywhere—from households, wedding halls, and bakeries to
corporate canteens—to post surplus food and generate an instant verifiable
AI Food Passport with nutritional estimation and automated hunger hotspot dispatch.
"""

from __future__ import annotations

import io
import random
import uuid
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from PIL import Image
import streamlit as st

from app.audit import record_event
from app.clock import now as get_clock_now
from app.db import (
    create_donor,
    create_listing,
    create_match,
    get_connection,
    list_donors,
    list_listings,
    list_recipients,
    list_sos_requests,
    list_volunteers,
    update_listing,
    update_sos_request,
)
from app.handover import generate_passport_qr
from app.llm.vision import extract_passport
from app.matching import find_matches
from app.models import (
    ContainerType,
    Dish,
    Donor,
    DonorForm,
    DonorKind,
    FoodPassport,
    Listing,
    ListingStatus,
    Match,
    SOSRequest,
    SOSStatus,
    SOSUrgency,
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

CITY_AREAS = [
    "Indiranagar",
    "Koramangala",
    "Yelahanka New Town",
    "Whitefield",
    "Hebbal",
    "Malleshwaram",
    "Jayanagar",
    "Rajajinagar",
    "Frazer Town",
    "Bellary Road",
    "Thanisandra",
    "HSR Layout",
    "BTM Layout",
]


def render_donor_page(lang: str = "en") -> None:
    """Render the Universal Food Donor Portal and Passport Generator."""
    st.title("🍲 " + t("donor_header", lang))
    st.markdown(
        "**Universal Hyperlocal Rescue:** Connect surplus food from *any household, banquet, restaurant, or bakery* "
        "directly to people in need, children's homes, and hunger hotspots."
    )

    conn = get_connection()
    donors = list_donors(conn=conn)
    clock_now = get_clock_now()

    col_form, col_preview = st.columns([1, 1], gap="large")

    with col_form:
        st.subheader("1. Surplus Food Origin (Post From Anywhere)")
        donor_origin_type = st.radio(
            "Donor Origin",
            options=["📍 Post From Anywhere (Household, Banquet, Bakery, Restaurant)", "🏢 Select Registered Facility"],
            index=0,
            horizontal=True,
            key="donor_origin_radio",
        )

        selected_donor: Optional[Donor] = None

        if donor_origin_type.startswith("📍"):
            c_name, c_kind = st.columns([3, 2])
            with c_name:
                custom_name = st.text_input(
                    "Donor / Organization Name",
                    value="Anand Family Gathering",
                    placeholder="e.g. Anand Family, Royal Palace Banquet, Daily Bakes",
                    key="custom_donor_name",
                )
            with c_kind:
                custom_kind = st.selectbox(
                    "Donor Category",
                    options=[
                        ("household", "Household / Family"),
                        ("event", "Event / Banquet / Wedding"),
                        ("restaurant", "Restaurant / Cloud Kitchen"),
                        ("bakery", "Bakery / Confectionery"),
                        ("supermarket", "Supermarket / Grocery"),
                        ("corporate", "Corporate Cafeteria"),
                        ("individual", "Individual Citizen"),
                    ],
                    format_func=lambda x: x[1],
                    index=0,
                    key="custom_donor_kind",
                )

            c_area, c_addr = st.columns([2, 3])
            with c_area:
                custom_area = st.selectbox(
                    "Neighborhood / Area",
                    options=CITY_AREAS,
                    index=0,
                    key="custom_donor_area",
                )
            with c_addr:
                custom_address = st.text_input(
                    "Pickup Street Address",
                    value="14/2, 12th Main, HAL 2nd Stage",
                    placeholder="e.g. 14/2, 12th Main, HAL 2nd Stage",
                    key="custom_donor_addr",
                )

            custom_phone = st.text_input(
                "Contact Phone (Concealed until volunteer dispatch)",
                value="+91 98451 90876",
                key="custom_donor_phone",
            )
        else:
            donor_names = {f"{d.name} ({d.area})": d for d in donors}
            if not donor_names:
                st.warning("No registered donors found. Please use 'Post From Anywhere' or load seed data.")
                return
            chosen_name = st.selectbox(
                t("select_donor", lang),
                options=list(donor_names.keys()),
                key="donor_select_box",
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
                value=45,
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
            value="Freshly prepared lunch buffet surplus; packed in food-grade covered steel vessels",
            placeholder="e.g. Prepared for lunch; kept in warm food-grade vessel",
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

        with st.spinner("Analyzing surplus with Gemini Vision, estimating nutrition, & assessing safety..."):
            # 1. Resolve Donor
            if donor_origin_type.startswith("📍"):
                donor_id = f"dn_{uuid.uuid4().hex[:8]}"
                # Generate realistic coordinate offset within Bengaluru
                lat_base = 13.0000 + (hash(custom_area) % 100) / 1000.0
                lon_base = 77.5800 + (hash(custom_name) % 100) / 1000.0
                new_donor = Donor(
                    id=donor_id,
                    name=custom_name or "Generous Donor",
                    kind=DonorKind(custom_kind[0]),
                    lat=lat_base,
                    lon=lon_base,
                    area=custom_area,
                    typical_surplus_portions=int(est_portions),
                    active_hours="08:00-22:00",
                )
                create_donor(new_donor, conn=conn)
                selected_donor = new_donor

            # 2. Build DonorForm
            cooked_at_str = cooked_time.strftime("%H:%M:%S")
            donor_form = DonorForm(
                cooked_at=cooked_at_str,
                storage=StorageCondition(storage_cond),
                est_portions=int(est_portions),
                container=ContainerType(container),
                notes=notes or None,
            )

            # 3. Extract Passport via Gemini Vision
            temp_img_path = FIXTURE_DIR / selected_image_filename
            if not temp_img_path.exists():
                temp_img_path = Path(".cache") / f"temp_{selected_image_filename}"
                temp_img_path.parent.mkdir(parents=True, exist_ok=True)
                temp_img_path.write_bytes(selected_image_bytes)

            passport = extract_passport(photos=[str(temp_img_path)], form=donor_form)

            # 4. Grounded Safety Assessment
            safety = assess_safety(passport=passport)

            # 5. Triage & Rescue Cascade Tier
            triage_dec = evaluate_triage(passport, safety, reference_time=clock_now)

            # 6. Create Listing
            listing_id = f"list_{datetime.now().strftime('%m%d_%H%M%S')}"
            listing = Listing(
                id=listing_id,
                donor_id=selected_donor.id if selected_donor else "donor_01",
                created_at=clock_now,
                photo_path=str(temp_img_path),
                passport=passport.model_dump(mode="json"),
                safety=safety.model_dump(mode="json"),
                tier=triage_dec.tier,
                status=ListingStatus.LISTED,
                expires_at=safety.safe_until,
            )
            create_listing(listing, conn=conn)

            # Record audit event
            record_event(
                action="LISTING_CREATED",
                entity_id=listing_id,
                actor=selected_donor.name if selected_donor else "Donor",
                details={
                    "donor_id": listing.donor_id,
                    "portions": int(est_portions),
                    "tier": triage_dec.tier.value,
                },
                conn=conn,
            )

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
            st.info("Submit the donor form on the left or click 'Run Demo Scenario' in the sidebar to view a live Passport card.")
            return

        target_listing = next((l for l in all_listings if l.id == active_lid), None)
        if not target_listing or not target_listing.passport:
            st.info("No passport data available.")
            return

        p_data = target_listing.passport
        s_data = target_listing.safety or {}
        donor_rec = next((d for d in donors if d.id == target_listing.donor_id), None)
        donor_name = donor_rec.name if donor_rec else "Surplus Food Donor"
        donor_area = donor_rec.area if donor_rec else "Bengaluru"

        # Passport Card
        with st.container(border=True):
            st.markdown(f"### 📋 Food Passport: `{target_listing.id}`")
            st.caption(f"Donor Origin: **{donor_name}** ({donor_area})")

            # Dietary & Dish Information
            dishes = p_data.get("dishes", [])
            total_portions = sum(d.get("est_portions", 0) for d in dishes) or 50

            for d in dishes:
                v_badge = "🟢 Pure Veg" if d.get("veg_status") == "veg" else ("🔴 Non-Veg" if d.get("veg_status") == "non_veg" else "🟡 Contains Egg")
                conf_pct = int(d.get("confidence", 0.9) * 100)
                st.markdown(f"- **{d.get('name', 'Surplus Meal')}** — {d.get('est_portions', 0)} portions `{v_badge}` *(AI Confidence: {conf_pct}%)*")

            # Nutritional Rescued Estimation Block
            cal_est = total_portions * 380
            protein_est = (total_portions * 14.5)
            person_days_est = cal_est / 2000.0

            st.markdown("---")
            st.markdown("#### ⚡ AI Rescued Nutritional Value")
            n1, n2, n3 = st.columns(3)
            with n1:
                st.metric("Total Rescued Energy", f"{cal_est:,} kcal")
            with n2:
                st.metric("Rescued Protein", f"{protein_est:,.1f} g")
            with n3:
                st.metric("Human Daily Needs", f"~{person_days_est:.1f} adult-days")

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

            # Scannable QR Code Display
            st.markdown("---")
            c_qr, c_dispatch = st.columns([1, 2])
            with c_qr:
                st.markdown(f"**{t('qr_code_title', lang)}:**")
                qr_bytes = generate_passport_qr(target_listing.id)
                st.image(qr_bytes, caption=f"Passport: ticktock://passport/{target_listing.id}", width=150)

            with c_dispatch:
                st.markdown("#### 🚀 Direct Hunger Dispatch")
                st.caption("Instantly match this surplus to the nearest hunger hotspot or critical shelter.")

                # Check if already matched
                if target_listing.status == ListingStatus.MATCHED:
                    st.success("✅ **Listing is already matched with an active courier mission.**")
                else:
                    auto_match_btn = st.button(
                        "🚨 1-Click Auto-Match to Urgent SOS Hotspot",
                        type="primary",
                        use_container_width=True,
                        key=f"btn_auto_match_{target_listing.id}",
                    )

                    if auto_match_btn:
                        with st.spinner("Finding nearest critical hunger deficit and dispatching courier..."):
                            # Check open SOS requests
                            open_sos = [s for s in list_sos_requests(conn=conn) if s.status == SOSStatus.OPEN]
                            volunteers = list_volunteers(conn=conn)
                            recipients = list_recipients(conn=conn)

                            chosen_vol = volunteers[0] if volunteers else None
                            otp_code = f"{random.randint(1000, 9999)}"

                            if open_sos:
                                # Top priority SOS
                                urgent_sos = open_sos[0]
                                match_id = f"m_{target_listing.id}"

                                # Match with recipient closest to SOS or first human recipient
                                cand_recip = recipients[0] if recipients else None
                                cand_recip_id = cand_recip.id if cand_recip else "recip_01"

                                new_match = Match(
                                    id=match_id,
                                    listing_id=target_listing.id,
                                    recipient_id=cand_recip_id,
                                    volunteer_id=chosen_vol.id if chosen_vol else "vol_01",
                                    score=0.98,
                                    why_this_match=(
                                        f"Direct SOS dispatch to fulfill urgent food need at {urgent_sos.requester_name}. "
                                        f"Volunteer {chosen_vol.name if chosen_vol else 'Courier'} dispatched with 4-digit handover OTP."
                                    ),
                                    status="PROPOSED",
                                    created_at=clock_now,
                                    otp_code=otp_code,
                                    otp_verified=False,
                                    sos_id=urgent_sos.id,
                                )
                                create_match(new_match, conn=conn)

                                # Update SOS status
                                update_sos_request(
                                    sos_id=urgent_sos.id,
                                    status=SOSStatus.MATCHED,
                                    conn=conn,
                                )

                                # Update Listing status
                                update_listing(target_listing.id, status=ListingStatus.MATCHED, conn=conn)

                                record_event(
                                    action="SOS_MATCHED",
                                    entity_id=target_listing.id,
                                    actor="Auto-Dispatch Engine",
                                    details={
                                        "sos_id": urgent_sos.id,
                                        "requester": urgent_sos.requester_name,
                                        "volunteer": chosen_vol.name if chosen_vol else "Courier",
                                        "otp": otp_code,
                                    },
                                    conn=conn,
                                )

                                st.success(
                                    f"🎉 **Dispatched!** Matched with **{urgent_sos.requester_name}** ({urgent_sos.portions_needed} meals needed).\n\n"
                                    f"🛵 **Courier:** {chosen_vol.name if chosen_vol else 'Courier'} is en route.\n\n"
                                    f"🔑 **Recipient Handover OTP:** `{otp_code}` (Shelter coordinator will provide this to courier upon delivery)."
                                )
                                st.rerun()
                            else:
                                # Standard match engine fallback
                                match_res = find_matches(target_listing, now=clock_now, conn=conn)
                                if match_res.candidates:
                                    cand = match_res.candidates[0]
                                    m_id = f"m_{target_listing.id}"
                                    std_match = Match(
                                        id=m_id,
                                        listing_id=target_listing.id,
                                        recipient_id=cand.recipient_id,
                                        volunteer_id=cand.volunteer_id,
                                        score=cand.score,
                                        score_breakdown=cand.score_breakdown,
                                        why_this_match=cand.why_this_match,
                                        status="PROPOSED",
                                        created_at=clock_now,
                                        otp_code=otp_code,
                                        otp_verified=False,
                                    )
                                    create_match(std_match, conn=conn)
                                    update_listing(target_listing.id, status=ListingStatus.MATCHED, conn=conn)

                                    st.success(
                                        f"🎉 **Matched!** Assigned to recipient `{cand.recipient_id}`.\n\n"
                                        f"🔑 **Handover OTP Code:** `{otp_code}`"
                                    )
                                    st.rerun()
                                else:
                                    st.warning("No time-feasible recipient found within the safe holding window.")
