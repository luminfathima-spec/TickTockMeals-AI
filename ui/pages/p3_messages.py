"""Multilingual outbox viewer and simulated acceptance loop for TickTock Meals."""

from __future__ import annotations

import streamlit as st

from app.clock import now as get_clock_now
from app.db import get_connection, list_outbox_messages
from app.llm.messages import simulate_response
from ui.i18n import t


def render_messages_page(lang: str = "en") -> None:
    """Render the Outbox dispatch viewer and Accept/Decline action loop."""
    st.title(t("messages_header", lang))
    st.caption(t("messages_sub", lang))

    conn = get_connection()
    messages = list_outbox_messages(conn=conn)
    clock_now = get_clock_now()

    if not messages:
        st.info(t("no_messages", lang))
        st.markdown(
            "💡 *Tip: Head to the **Donor** page to create a listing or click **'Run Demo Scenario'** in the sidebar to auto-generate matches and outbox messages.*"
        )
        return

    # Language Filter Toggle
    lang_filter = st.radio(
        t("toggle_language", lang),
        options=["All", "English (en)", "Kannada (kn)", "Hindi (hi)"],
        horizontal=True,
        key="outbox_lang_filter",
    )

    filtered_messages = messages
    if lang_filter == "English (en)":
        filtered_messages = [m for m in messages if m.language == "en"]
    elif lang_filter == "Kannada (kn)":
        filtered_messages = [m for m in messages if m.language == "kn"]
    elif lang_filter == "Hindi (hi)":
        filtered_messages = [m for m in messages if m.language == "hi"]

    st.markdown(f"**Total Messages ({len(filtered_messages)}):**")

    # Render each outbox message card
    for msg in filtered_messages:
        with st.container(border=True):
            col_info, col_actions = st.columns([3, 1])

            with col_info:
                role_icon = "🏢" if msg.role == "recipient" else "🛵"
                role_label = "Recipient Coordinator" if msg.role == "recipient" else "Courier Volunteer"
                status_color = "🟢" if msg.status == "accepted" else ("🔴" if msg.status == "declined" else ("🟡" if msg.status == "timed_out" else "🔵"))

                st.markdown(f"#### {role_icon} {role_label} (`{msg.recipient_or_volunteer_id}`)")
                st.caption(f"Status: **{status_color} {msg.status.upper()}** | Language: `{msg.language.upper()}` | Queued: {msg.created_at.strftime('%I:%M %p')}")
                st.info(msg.message_text)

            with col_actions:
                st.markdown("**Action Simulation:**")
                if msg.status in ("queued", "delivered_sim"):
                    if st.button(t("btn_accept", lang), key=f"accept_{msg.id}", type="primary", use_container_width=True):
                        res = simulate_response(message_id=msg.id, action="ACCEPT", conn=conn, now=clock_now)
                        st.success("Accepted! Address unlocked for courier.")
                        st.rerun()

                    if st.button(t("btn_decline", lang), key=f"decline_{msg.id}", type="secondary", use_container_width=True):
                        res = simulate_response(message_id=msg.id, action="DECLINE", conn=conn, now=clock_now)
                        st.warning("Declined! Escalated to next match candidate.")
                        st.rerun()

                    if st.button(t("btn_timeout", lang), key=f"timeout_{msg.id}", use_container_width=True):
                        res = simulate_response(message_id=msg.id, action="TIMEOUT", conn=conn, now=clock_now)
                        st.warning("Timeout simulated! Escalated to next candidate.")
                        st.rerun()
                else:
                    st.write(f"Response recorded: **{msg.status.upper()}**")
