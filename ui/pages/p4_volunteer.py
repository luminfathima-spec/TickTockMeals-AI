"""Courier volunteer task card, verification gate, and delivery confirmation page."""

from __future__ import annotations

from datetime import datetime
import streamlit as st

from app.clock import now as get_clock_now
from app.db import (
    get_connection,
    get_donor,
    list_listings,
    list_volunteers,
    update_listing,
)
from app.handover import confirm_delivery, verify_and_pickup
from app.models import (
    ListingStatus,
    Tier,
    VerificationChecklist,
)
from ui.i18n import t


def render_volunteer_page(lang: str = "en") -> None:
    """Render the Courier Volunteer verification gate and delivery confirmation."""
    st.title(t("volunteer_header", lang))
    st.caption(t("volunteer_sub", lang))

    conn = get_connection()
    volunteers = list_volunteers(conn=conn)
    live_listings = list_listings(conn=conn)
    clock_now = get_clock_now()

    if not volunteers:
        st.warning("No volunteers found. Click 'Load Seed Data' in the sidebar.")
        return

    # Volunteer Profile Selector
    vol_dict = {f"{v.name} ({v.vehicle.value.upper()})": v for v in volunteers}
    selected_vol_name = st.selectbox("Select Active Volunteer", options=list(vol_dict.keys()), key="vol_profile_select")
    current_vol = vol_dict[selected_vol_name]

    # Find listings assigned or in progress (ACCEPTED or PICKED_UP)
    actionable_listings = [
        l for l in live_listings
        if l.status in (ListingStatus.ACCEPTED, ListingStatus.PICKED_UP)
    ]

    if not actionable_listings:
        st.info("No active accepted assignments awaiting pickup or delivery. Accept a match in the Messages outbox or run the demo scenario.")
        return

    listing_options = {
        f"Listing {l.id} - Tier: {l.tier.value if isinstance(l.tier, Tier) else l.tier} (Status: {l.status.value})": l
        for l in actionable_listings
    }
    chosen_listing_key = st.selectbox("Select Assigned Listing", options=list(listing_options.keys()), key="vol_active_listing")
    active_listing = listing_options[chosen_listing_key]
    donor_obj = get_donor(active_listing.donor_id, conn=conn)

    # Task Card
    with st.container(border=True):
        st.subheader("📋 " + t("active_task", lang))
        c1, c2, c3 = st.columns(3)
        c1.metric("Listing ID", active_listing.id)
        c2.metric("Current Tier", active_listing.tier.value if isinstance(active_listing.tier, Tier) else str(active_listing.tier))
        c3.metric("State", active_listing.status.value)

        # Address reveal logic
        st.markdown(f"#### 📍 {t('pickup_address_title', lang)}")
        if active_listing.status in (ListingStatus.ACCEPTED, ListingStatus.PICKED_UP, ListingStatus.DELIVERED):
            exact_addr = (
                f"{donor_obj.name}, Main Gate / Reception, {donor_obj.area}, Bengaluru (GPS: {donor_obj.lat:.4f}, {donor_obj.lon:.4f})"
                if donor_obj else "North Bengaluru Food Station"
            )
            st.success(t("address_revealed", lang, address=exact_addr))
        else:
            area_str = donor_obj.area if donor_obj else "North Bengaluru"
            st.warning(t("address_concealed", lang, area=area_str))

    # SECTION 1: Verification Gate Checklist (only if status == ACCEPTED)
    if active_listing.status == ListingStatus.ACCEPTED:
        st.markdown("---")
        st.subheader("🔬 " + t("checklist_header", lang))
        st.markdown(
            "⚠️ **Human Consumption Verification Gate:** In accordance with food safety standards, "
            "all 4 items must be verified physically before loading. Any check failure triggers immediate reclassification down the cascade."
        )

        with st.form(key="verification_gate_form"):
            chk_smell = st.checkbox(t("chk_smell", lang), value=True, key="chk_smell_val")
            chk_temp = st.checkbox(t("chk_temp", lang), value=True, key="chk_temp_val")
            chk_container = st.checkbox(t("chk_container", lang), value=True, key="chk_container_val")
            chk_label = st.checkbox(t("chk_label", lang), value=True, key="chk_label_val")

            c_t1, c_t2 = st.columns(2)
            with c_t1:
                temp_val = st.number_input(t("temp_reading", lang), min_value=10.0, max_value=100.0, value=65.0, step=0.5, key="vol_temp_input")
            with c_t2:
                notes = st.text_input(t("volunteer_notes", lang), placeholder="e.g., Food steaming hot, packaging intact", key="vol_field_notes")

            submit_pickup = st.form_submit_button(t("btn_verify_pickup", lang), type="primary", use_container_width=True)

        if submit_pickup:
            checklist = VerificationChecklist(
                volunteer_id=current_vol.id,
                timestamp=clock_now,
                smell_appearance_ok=chk_smell,
                temperature_ok=chk_temp,
                temp_reading_c=float(temp_val),
                container_clean_covered=chk_container,
                labelled_with_time=chk_label,
                notes=notes or None,
            )

            updated_l, passed, err_msg = verify_and_pickup(
                listing_id=active_listing.id,
                checklist=checklist,
                conn=conn,
                now=clock_now,
            )

            if passed:
                st.success("Verification complete! Listing marked PICKED_UP. Proceed with delivery.")
                st.rerun()
            else:
                st.error(f"Verification Failed: {err_msg}")
                st.warning(f"Food has been automatically reclassified down the cascade to {updated_l.tier.value}!")
                st.rerun()

    # SECTION 2: Recipient Handover & Delivery Confirmation (only if status == PICKED_UP)
    elif active_listing.status == ListingStatus.PICKED_UP:
        st.markdown("---")
        st.subheader("📦 " + t("delivery_section", lang))

        with st.form(key="delivery_confirmation_form"):
            default_portions = active_listing.actual_portions or 50
            if active_listing.passport:
                dishes = active_listing.passport.get("dishes", [])
                p_sum = sum(d.get("est_portions", 0) for d in dishes)
                if p_sum > 0:
                    default_portions = p_sum

            actual_portions = st.number_input(
                t("actual_portions_label", lang),
                min_value=1,
                max_value=1000,
                value=int(default_portions),
                step=1,
                key="deliv_portions_input",
            )
            recip_notes = st.text_input(
                t("recipient_notes_label", lang),
                placeholder="e.g., Received in good order, served immediately to residents",
                key="deliv_recip_notes",
            )

            submit_delivery = st.form_submit_button(t("btn_confirm_delivery", lang), type="primary", use_container_width=True)

        if submit_delivery:
            deliv_l = confirm_delivery(
                listing_id=active_listing.id,
                actual_portions=int(actual_portions),
                recipient_notes=recip_notes or None,
                conn=conn,
                now=clock_now,
            )
            st.success(f"Delivery of {deliv_l.actual_portions} portions confirmed! Recorded in audit chain.")
            st.rerun()
