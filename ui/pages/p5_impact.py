"""Impact analytics dashboard and source-level prevention insights page."""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from app.db import get_connection, list_donors, list_matches, list_recipients, list_sos_requests
from app.impact import compute_impact_summary, get_impact_dataframes
from app.insights import generate_prevention_insights
from app.models import SOSStatus
from ui.i18n import t


def render_impact_page(lang: str = "en") -> None:
    """Render the Impact Analytics KPIs, Plotly charts, and Prevention Insights."""
    st.title("📊 " + t("impact_header", lang))
    st.caption(
        "Quantifying hunger relief, nutritional rescue, CO2e diversion, and source-level waste reduction across Bengaluru."
    )

    conn = get_connection()
    summary = compute_impact_summary(conn=conn, include_history=True)
    dfs = get_impact_dataframes(conn=conn, include_history=True)
    all_sos = list_sos_requests(conn=conn)
    sos_active = sum(1 for s in all_sos if s.status in (SOSStatus.OPEN, SOSStatus.MATCHED))
    sos_relieved = sum(1 for s in all_sos if s.status == SOSStatus.FULFILLED)

    total_portions = summary["total_portions_rescued"]
    total_kcal = total_portions * 380
    total_protein_kg = (total_portions * 14.5) / 1000.0

    # Top KPI Metrics (Human & Environmental Rescue)
    st.subheader("⚡ Live Zero-Hunger Rescue Scoreboard")
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Portions Rescued", f"{total_portions:,}")
    k2.metric("Nutritional Energy", f"{total_kcal / 1_000_000:.2f} Mcal")
    k3.metric("Protein Rescued", f"{total_protein_kg:.1f} kg")
    k4.metric("Food Diverted", f"{summary['total_kg_rescued']:,.1f} kg")
    k5.metric("CO2e Avoided", f"{summary['total_co2e_avoided_kg']:,.1f} kg")
    k6.metric("SOS Hunger Deficits", f"{sos_relieved} Relieved", delta=f"{sos_active} Active")

    st.markdown("---")

    # Plotly Visualizations
    col_chart1, col_chart2 = st.columns([1, 1], gap="medium")

    with col_chart1:
        st.subheader("🥧 " + t("chart_tier_title", lang))
        df_tier = dfs["by_tier"]
        if not df_tier.empty:
            fig_tier = px.pie(
                df_tier,
                values="Portions",
                names="Tier",
                hole=0.45,
                color="Tier",
                color_discrete_map={
                    "Human Rescue": "#2ecc71",
                    "Animal Feed": "#9b59b6",
                    "Compost Biogas": "#e67e22",
                },
            )
            fig_tier.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=300)
            st.plotly_chart(fig_tier, use_container_width=True)
        else:
            st.info("No tier data available yet.")

    with col_chart2:
        st.subheader("📈 " + t("chart_trend_title", lang))
        df_trend = dfs["daily_trend"]
        if not df_trend.empty:
            fig_trend = px.bar(
                df_trend,
                x="Date",
                y="Portions",
                color="Weekday",
                title="Daily Recovered Volume",
            )
            fig_trend.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=300)
            st.plotly_chart(fig_trend, use_container_width=True)
        else:
            st.info("No trend data available yet.")

    # Leaderboard & Capacity Utilisation
    col_t1, col_t2 = st.columns([1, 1], gap="medium")

    with col_t1:
        st.subheader("🏆 " + t("leaderboard_title", lang))
        df_donors = dfs["donors"]
        if not df_donors.empty:
            st.dataframe(df_donors.head(8), use_container_width=True, hide_index=True)
        else:
            st.info("No donor entries available.")

    with col_t2:
        st.subheader("🏢 " + t("utilisation_title", lang))
        df_recip = dfs["recipients"]
        if not df_recip.empty:
            st.dataframe(df_recip.head(8), use_container_width=True, hide_index=True)
        else:
            st.info("No recipient entries available.")

    # Verified Deliveries & SOS Relief History
    st.markdown("---")
    st.subheader("🔒 Verified Handover & OTP Delivery Proof")
    matches = list_matches(conn=conn)
    recipients = {r.id: r.name for r in list_recipients(conn=conn)}

    if matches:
        delivery_records = []
        for m in matches:
            recip_name = recipients.get(m.recipient_id, m.recipient_id)
            otp_status = "✅ Verified" if getattr(m, "otp_verified", False) else f"🔑 Code: {getattr(m, 'otp_code', 'Pending')}"
            delivery_records.append({
                "Match ID": m.id,
                "Listing ID": m.listing_id,
                "Recipient Shelter": recip_name,
                "Status": m.status,
                "Verification Gate": otp_status,
                "Handover Time": m.created_at.strftime("%Y-%m-%d %H:%M") if m.created_at else "—",
            })
        st.dataframe(delivery_records, use_container_width=True, hide_index=True)
    else:
        st.info("No rescue matches recorded yet.")

    # Transparent Assumptions Expander
    with st.expander("ℹ️ " + t("assumptions_title", lang), expanded=False):
        assumptions = summary["assumptions"]
        st.markdown(f"- **Portion Weight Assumption:** `{assumptions['kg_per_meal']} kg / portion` — *{assumptions['source_kg_per_meal']}*")
        st.markdown(f"- **Nutritional Value Assumption:** `380 kcal / portion`, `14.5 g protein / portion` based on standard balanced Indian meal data.")
        st.markdown(f"- **Avoided Carbon Emissions:** `{assumptions['co2e_kg_per_kg_food']} kg CO2e / kg food` — *{assumptions['source_co2e_factor']}*")
        st.markdown(f"- **Configurable Settings:** *{assumptions['notes']}*")

    st.markdown("---")

    # Source-Level Waste Prevention Insights
    st.subheader("💡 " + t("insights_title", lang))
    st.markdown(
        "Gemini analyzes 30 days of kitchen production history to uncover recurring overproduction spikes and recommend "
        "operational changes that prevent food surplus at source."
    )

    donors = list_donors(conn=conn)
    if donors:
        donor_opts = {f"{d.name} ({d.area})": d.id for d in donors}
        chosen_d_name = st.selectbox("Select Facility for Waste Analysis", options=list(donor_opts.keys()), key="insights_donor_select")
        chosen_d_id = donor_opts[chosen_d_name]

        if st.button(t("btn_generate_insights", lang), type="primary", key="btn_run_insights"):
            with st.spinner("Analyzing historical production patterns and generating prevention advice..."):
                report_data = generate_prevention_insights(donor_id=chosen_d_id, conn=conn)

                with st.container(border=True):
                    st.markdown("### 📋 AI Prevention Advisory Report")
                    st.text(report_data["report_text"])

                    proj = report_data.get("projection", {})
                    st.success(f"🎯 **Projected Impact:** {proj.get('label', '')}")
