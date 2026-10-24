"""Geospatial network map and time-feasible routing page for TickTock Meals."""

from __future__ import annotations

import pandas as pd
import pydeck as pdk
import streamlit as st

from app.clock import now as get_clock_now
from app.db import (
    get_connection,
    get_donor,
    list_donors,
    list_listings,
    list_recipients,
    list_volunteers,
)
from app.matching import find_matches
from app.models import ListingStatus, Tier
from ui.i18n import t

# North Bengaluru center coordinates
DEFAULT_LAT = 13.0850
DEFAULT_LON = 77.5920


def render_map_page(lang: str = "en") -> None:
    """Render the Pydeck geospatial network map and explainable routing breakdown."""
    st.title(t("map_header", lang))
    st.caption(t("map_sub", lang))

    conn = get_connection()
    donors = list_donors(conn=conn)
    recipients = list_recipients(conn=conn)
    volunteers = list_volunteers(conn=conn)
    live_listings = list_listings(conn=conn)
    clock_now = get_clock_now()

    if not donors or not recipients:
        st.warning("Database empty. Click 'Load Seed Data' in the sidebar.")
        return

    # Filter for open / active listings
    active_listings = [
        l for l in live_listings
        if l.status in (ListingStatus.LISTED, ListingStatus.VERIFIED, ListingStatus.MATCHED, ListingStatus.ACCEPTED)
    ]

    selected_listing = None
    if active_listings:
        l_options = {
            f"Listing {l.id} ({l.tier.value if isinstance(l.tier, Tier) else l.tier}) - Expires {l.expires_at.strftime('%I:%M %p') if l.expires_at else 'N/A'}": l
            for l in active_listings
        }
        chosen_key = st.selectbox(
            t("select_listing", lang),
            options=list(l_options.keys()),
            key="map_listing_select",
        )
        selected_listing = l_options[chosen_key]
    else:
        st.info(t("no_listings", lang))

    # Evaluate matches for selected listing
    match_result = None
    route_lines = []
    top_candidate = None

    if selected_listing:
        match_result = find_matches(selected_listing, now=clock_now, conn=conn)
        if match_result.candidates:
            top_candidate = match_result.candidates[0]
            # Route line: Volunteer -> Donor -> Recipient
            d_obj = get_donor(selected_listing.donor_id, conn=conn)
            v_obj = next((v for v in volunteers if v.id == top_candidate.volunteer_id), None)
            r_obj = next((r for r in recipients if r.id == top_candidate.recipient_id), None)

            if d_obj and v_obj and r_obj:
                # Leg 1: Volunteer to Donor
                route_lines.append({
                    "start": [v_obj.lon, v_obj.lat],
                    "end": [d_obj.lon, d_obj.lat],
                    "color": [0, 200, 255, 200],  # Cyan
                    "label": f"Courier Leg: {top_candidate.volunteer_name}",
                })
                # Leg 2: Donor to Recipient
                route_lines.append({
                    "start": [d_obj.lon, d_obj.lat],
                    "end": [r_obj.lon, r_obj.lat],
                    "color": [50, 205, 50, 220],  # Lime Green
                    "label": f"Delivery Leg: {top_candidate.recipient_name}",
                })

    # Build Map Data
    donors_data = [
        {"name": d.name, "area": d.area, "lat": d.lat, "lon": d.lon, "color": [255, 215, 0, 220], "radius": 180}
        for d in donors
    ]

    recipients_data = []
    for r in recipients:
        t_val = r.tier.value if isinstance(r.tier, Tier) else str(r.tier)
        color = [46, 204, 113, 200] if "HUMAN" in t_val else ([155, 89, 182, 200] if "ANIMAL" in t_val else [230, 126, 34, 200])
        recipients_data.append({
            "name": r.name,
            "area": r.area,
            "tier": t_val,
            "capacity": r.capacity_portions_per_day,
            "lat": r.lat,
            "lon": r.lon,
            "color": color,
            "radius": 160,
        })

    volunteers_data = [
        {"name": v.name, "vehicle": v.vehicle.value, "lat": v.lat, "lon": v.lon, "color": [52, 152, 219, 220], "radius": 130}
        for v in volunteers
    ]

    # Pydeck Layers
    layers = [
        pdk.Layer(
            "ScatterplotLayer",
            data=pd.DataFrame(donors_data),
            get_position=["lon", "lat"],
            get_color="color",
            get_radius="radius",
            pickable=True,
            auto_highlight=True,
        ),
        pdk.Layer(
            "ScatterplotLayer",
            data=pd.DataFrame(recipients_data),
            get_position=["lon", "lat"],
            get_color="color",
            get_radius="radius",
            pickable=True,
            auto_highlight=True,
        ),
        pdk.Layer(
            "ScatterplotLayer",
            data=pd.DataFrame(volunteers_data),
            get_position=["lon", "lat"],
            get_color="color",
            get_radius="radius",
            pickable=True,
            auto_highlight=True,
        ),
    ]

    if route_lines:
        layers.append(
            pdk.Layer(
                "LineLayer",
                data=pd.DataFrame(route_lines),
                get_source_position="start",
                get_target_position="end",
                get_color="color",
                get_width=5,
                pickable=True,
            )
        )

    # Render Pydeck Map
    deck = pdk.Deck(
        layers=layers,
        initial_view_state=pdk.ViewState(
            latitude=DEFAULT_LAT,
            longitude=DEFAULT_LON,
            zoom=12.2,
            pitch=35,
        ),
        tooltip={"text": "{name}\n{area}"},
        map_style="light",
    )

    st.pydeck_chart(deck, use_container_width=True)

    # Legend
    st.markdown(
        """
        **Map Legend:**
        🟡 **Donors (Canteens/Halls)** |
        🟢 **Human NGOs** |
        🟣 **Animal Shelters** |
        🟠 **Compost / Biogas Units** |
        🔵 **Volunteers** |
        ── **Optimal Dispatch Route**
        """
    )

    # Explainable Matching Panel
    if top_candidate and match_result:
        st.markdown("---")
        st.subheader("⚡ " + t("why_this_match_title", lang))

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Selected Recipient", top_candidate.recipient_name, f"Area: {top_candidate.recipient_area}")
        c2.metric("Courier Volunteer", top_candidate.volunteer_name, f"Transport: {top_candidate.volunteer_vehicle.upper()}")
        c3.metric("Estimated Arrival", top_candidate.estimated_arrival.strftime("%I:%M %p"), f"{top_candidate.total_eta_minutes:.0f} mins total ETA")
        c4.metric("Aggregate Score", f"{top_candidate.score:.2f} / 1.00", f"Distance: {top_candidate.total_distance_km:.1f} km")

        st.info(f"**Dispatch Explanation:** {top_candidate.why_this_match}")

        # Score Breakdown Bar Chart
        st.markdown(f"**{t('score_breakdown_title', lang)}:**")
        breakdown_df = pd.DataFrame([
            {"Factor": k.replace("_", " ").title(), "Score Weight": v}
            for k, v in top_candidate.score_breakdown.items()
        ])
        st.bar_chart(breakdown_df.set_index("Factor"))

        # Disqualified Candidates / Hard Constraint Rejections
        if match_result.rejections:
            st.markdown("---")
            with st.expander(t("rejected_recipients_title", lang), expanded=False):
                rej_rows = [
                    {"Recipient": rej.recipient_name, "Tier": rej.recipient_tier, "Area": rej.recipient_area, "Constraint Failed": rej.reason}
                    for rej in match_result.rejections
                ]
                st.dataframe(pd.DataFrame(rej_rows), use_container_width=True)
