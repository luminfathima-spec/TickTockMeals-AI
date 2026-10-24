"""Community SOS & Hunger Hotspot Requests for TickTock Meals.

Allows shelters, orphanages, slum coordinators, and community volunteers
to broadcast real-time meal deficits so donors and cloud kitchens
can fulfill them directly before hunger strikes.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Optional
import streamlit as st

from app.audit import record_event
from app.clock import now as get_clock_now
from app.db import (
    create_match,
    create_sos_request,
    get_connection,
    list_listings,
    list_recipients,
    list_sos_requests,
    list_volunteers,
    update_listing,
    update_sos_request,
)
from app.models import (
    ListingStatus,
    Match,
    SOSRequest,
    SOSStatus,
    SOSUrgency,
)
from ui.i18n import t


def render_sos_page(lang: str = "en") -> None:
    """Render the community hunger hotspots and meal requests view."""
    conn = get_connection()
    clock_now = get_clock_now()

    st.markdown("## 🆘 **Community Hunger Hotspots & Meal Requests (SOS)**")
    st.markdown(
        "Real-time food needs broadcast by local shelters, orphanages, and community coordinators. "
        "Donors and kitchens can directly pledge surplus meals to fulfill these urgent requests."
    )

    # Top KPI Metrics Bar
    all_sos = list_sos_requests(conn=conn)
    open_sos = [s for s in all_sos if s.status == SOSStatus.OPEN]
    total_portions_needed = sum(s.portions_needed for s in open_sos)
    critical_count = sum(1 for s in open_sos if s.urgency == SOSUrgency.CRITICAL)

    k1, k2, k3 = st.columns(3)
    with k1:
        st.metric("Open Food Requests", len(open_sos))
    with k2:
        st.metric("Total Meals Needed Tonight", f"{total_portions_needed} portions")
    with k3:
        st.metric("Critical Urgency", f"{critical_count} requests", delta="Action Required", delta_color="inverse")

    st.markdown("---")

    # Broadcast New Request Section
    with st.expander("📢 **Broadcast an Urgent Meal Need (Shelter / Coordinator Form)**", expanded=False):
        st.caption("Are you managing a shelter, orphanage, or community in need of food? Broadcast your requirements here.")
        with st.form("broadcast_sos_form", clear_on_submit=True):
            col1, col2 = st.columns(2)
            with col1:
                requester_name = st.text_input("Organization / Community Name", placeholder="e.g. Hope Orphanage")
                req_type = st.selectbox(
                    "Requester Type",
                    options=["orphanage", "shelter", "slum_cluster", "hospital_attendants", "daily_wage_camp", "individual"],
                    format_func=lambda x: x.replace("_", " ").title(),
                )
                portions = st.number_input("Meals / Portions Needed", min_value=5, max_value=500, value=40, step=5)
                veg_only = st.checkbox("Strict Vegetarian Meals Only", value=True)

            with col2:
                address = st.text_input("Delivery Address / Neighborhood", placeholder="e.g. Yelahanka New Town, Sector 1")
                urgency = st.selectbox("Urgency Level", options=["CRITICAL", "HIGH", "NORMAL"], index=1)
                hours_needed = st.slider("Needed Within (Hours)", min_value=1, max_value=8, value=3)
                contact_phone = st.text_input("Coordinator Contact Phone", value="9845012345")
                notes = st.text_area("Special Dietary / Handling Notes (Optional)", placeholder="e.g. Soft cooked food preferred for children.")

            submitted = st.form_submit_button("🚨 Broadcast SOS Request Now", type="primary", use_container_width=True)

            if submitted:
                if not requester_name or not address:
                    st.error("Please provide both an organization name and delivery address.")
                else:
                    new_sos = SOSRequest(
                        id=f"sos_{uuid.uuid4().hex[:8]}",
                        requester_name=requester_name,
                        requester_type=req_type,
                        portions_needed=int(portions),
                        veg_only=veg_only,
                        address=address,
                        lat=13.0800 + (hash(address) % 50) / 1000.0,
                        lon=77.5900 + (hash(requester_name) % 50) / 1000.0,
                        needed_by=clock_now + timedelta(hours=hours_needed),
                        urgency=SOSUrgency(urgency),
                        status=SOSStatus.OPEN,
                        contact_phone=contact_phone,
                        notes=notes or None,
                        created_at=clock_now,
                    )
                    create_sos_request(new_sos, conn=conn)
                    record_event(
                        event_type="SOS_REQUEST_CREATED",
                        entity_type="sos_request",
                        entity_id=new_sos.id,
                        payload=new_sos.model_dump(mode="json"),
                        conn=conn,
                    )
                    st.success(f"Urgent hunger request broadcast successfully! Reference ID: `{new_sos.id}`")
                    st.rerun()

    # Active SOS Feed
    st.markdown("### 📋 **Active Community Meal Requests**")

    # Filter controls
    f_col1, f_col2 = st.columns([2, 1])
    with f_col1:
        urgency_filter = st.selectbox("Filter by Urgency", options=["ALL", "CRITICAL", "HIGH", "NORMAL"])
    with f_col2:
        veg_filter = st.checkbox("Show Vegetarian Requests Only", value=False)

    filtered_sos = open_sos
    if urgency_filter != "ALL":
        filtered_sos = [s for s in filtered_sos if s.urgency.value == urgency_filter]
    if veg_filter:
        filtered_sos = [s for s in filtered_sos if s.veg_only]

    if not filtered_sos:
        st.info("🎉 No active hunger requests matching your filter. All local shelters are currently supported!")
        return

    # Check for available surplus listings to propose matches
    available_listings = [
        l for l in list_listings(conn=conn)
        if l.status in (ListingStatus.LISTED, ListingStatus.VERIFIED)
    ]
    volunteers = list_volunteers(only_available=True, conn=conn) or list_volunteers(conn=conn)

    for item in filtered_sos:
        urgency_color = "red" if item.urgency == SOSUrgency.CRITICAL else "orange" if item.urgency == SOSUrgency.HIGH else "blue"
        mins_remaining = int((item.needed_by - clock_now).total_seconds() / 60.0)
        time_str = f"{mins_remaining} minutes remaining" if mins_remaining > 0 else "Overdue / Immediate"

        with st.container(border=True):
            c_header, c_action = st.columns([3, 1])
            with c_header:
                st.markdown(
                    f"#### :{urgency_color}[{item.urgency.value} URGENCY] — **{item.requester_name}** "
                    f"(`{item.portions_needed} meals needed`)"
                )
                st.markdown(
                    f"📍 **Location**: `{item.address}` | 🏷️ **Type**: `{item.requester_type.replace('_', ' ').title()}` | "
                    f"🥗 **Diet**: `{'Pure Veg Only' if item.veg_only else 'Any Safe Food'}`"
                )
                st.caption(f"🕒 **Needed By**: {item.needed_by.strftime('%I:%M %p')} ({time_str}) | 📞 Contact: `{item.contact_phone}`")
                if item.notes:
                    st.markdown(f"> *{item.notes}*")

            with c_action:
                st.markdown("<br>", unsafe_allow_html=True)
                if available_listings and volunteers:
                    candidate_listing = available_listings[0]
                    btn_key = f"fulfill_{item.id}_{candidate_listing.id}"
                    if st.button(
                        f"🤝 Fulfill Need\n({candidate_listing.id})",
                        key=btn_key,
                        type="primary",
                        use_container_width=True,
                    ):
                        courier = volunteers[0]
                        new_match = Match(
                            id=f"m_{item.id}_{candidate_listing.id}",
                            listing_id=candidate_listing.id,
                            recipient_id=item.requester_name,
                            volunteer_id=courier.id,
                            score=0.95,
                            score_breakdown={"urgency": 0.4, "proximity": 0.3, "capacity": 0.2, "diet": 0.1},
                            why_this_match=f"Direct fulfillment of {item.urgency.value} hunger request for {item.requester_name}.",
                            status="ACCEPTED",
                            otp_code="4218",
                            otp_verified=False,
                            sos_id=item.id,
                            created_at=clock_now,
                        )
                        create_match(new_match, conn=conn)

                        # Update listing and SOS status
                        candidate_listing.status = ListingStatus.MATCHED
                        update_listing(candidate_listing, conn=conn)

                        item.status = SOSStatus.MATCHED
                        update_sos_request(item, conn=conn)

                        record_event(
                            event_type="SOS_FULFILLMENT_MATCHED",
                            entity_type="match",
                            entity_id=new_match.id,
                            payload={"sos_id": item.id, "listing_id": candidate_listing.id, "volunteer": courier.name},
                            conn=conn,
                        )
                        st.success(f"Matched with {candidate_listing.id}! Courier {courier.name} dispatched.")
                        st.rerun()
                else:
                    if st.button("Pledge Meals", key=f"pledge_{item.id}", use_container_width=True):
                        st.info("Pledge noted! Post a surplus donation in the Donor Portal to dispatch courier.")
