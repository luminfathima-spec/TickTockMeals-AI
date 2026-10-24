"""Impact analytics dashboard and source-level prevention insights page."""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from app.db import get_connection, list_donors
from app.impact import compute_impact_summary, get_impact_dataframes
from app.insights import generate_prevention_insights
from ui.i18n import t


def render_impact_page(lang: str = "en") -> None:
    """Render the Impact Analytics KPIs, Plotly charts, and Prevention Insights."""
    st.title(t("impact_header", lang))
    st.caption(t("impact_sub", lang))

    conn = get_connection()
    summary = compute_impact_summary(conn=conn, include_history=True)
    dfs = get_impact_dataframes(conn=conn, include_history=True)

    # Top KPI Metrics
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric(t("kpi_portions", lang), f"{summary['total_portions_rescued']:,}")
    k2.metric(t("kpi_kg", lang), f"{summary['total_kg_rescued']:,.1f} kg")
    k3.metric(t("kpi_co2e", lang), f"{summary['total_co2e_avoided_kg']:,.1f} kg")
    k4.metric(t("kpi_cascade_saved", lang), f"{summary['cascade_saved_pct']:.1f}%")
    k5.metric(t("kpi_avg_delivery", lang), f"{summary['avg_time_to_delivery_minutes']:.0f} mins")

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

    # Transparent Assumptions Expander
    with st.expander("ℹ️ " + t("assumptions_title", lang), expanded=False):
        assumptions = summary["assumptions"]
        st.markdown(f"- **Portion Weight Assumption:** `{assumptions['kg_per_meal']} kg / portion` — *{assumptions['source_kg_per_meal']}*")
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
