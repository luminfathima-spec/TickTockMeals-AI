"""Cryptographic audit chain verification and tamper demonstration page."""

from __future__ import annotations

import json
import pandas as pd
import streamlit as st

from app.audit import verify_chain
from app.db import get_connection, list_audit_events
from ui.i18n import t


def render_audit_page(lang: str = "en") -> None:
    """Render the Cryptographic Audit Chain verification and interactive tamper demo."""
    st.title(t("audit_header", lang))
    st.caption(t("audit_sub", lang))

    conn = get_connection()
    valid, message, broken_row = verify_chain(conn=conn)

    # Status Badge
    st.markdown(f"### {t('chain_status', lang)}")
    if valid:
        st.success(f"{t('status_verified', lang)} — {message}")
    else:
        st.error(f"{t('status_broken', lang)} — {message} (Broken at row #{broken_row})")

    st.markdown("---")

    # Interactive Tamper Demonstration
    st.subheader("🧪 Interactive Cryptographic Tamper Demonstration")
    st.markdown(
        "Demonstrate for expo judges how SHA-256 hash chaining instantly exposes unauthorized database tampering. "
        "Click the button below to simulate malicious row modification."
    )

    col_tamper, col_restore = st.columns(2)

    with col_tamper:
        if st.button("🚨 " + t("btn_tamper", lang), type="secondary", use_container_width=True, key="btn_tamper_demo"):
            cursor = conn.execute("SELECT rowid, id, payload FROM audit_events ORDER BY rowid DESC LIMIT 1")
            row = cursor.fetchone()
            if row:
                st.session_state["tampered_rowid"] = row["rowid"]
                st.session_state["original_payload"] = row["payload"]
                with conn:
                    conn.execute(
                        "UPDATE audit_events SET payload = ? WHERE rowid = ?",
                        (json.dumps({"tampered": True, "malicious_edit": "Bypassed volunteer inspection"}), row["rowid"]),
                    )
                st.warning(f"Tampered with event '{row['id']}'! Hash chain is now invalid.")
                st.rerun()
            else:
                st.info("No audit events to tamper with.")

    with col_restore:
        if st.button("🔄 " + t("btn_restore", lang), type="primary", use_container_width=True, key="btn_restore_demo"):
            t_rowid = st.session_state.get("tampered_rowid")
            orig_p = st.session_state.get("original_payload")
            if t_rowid is not None:
                with conn:
                    conn.execute("UPDATE audit_events SET payload = ? WHERE rowid = ?", (orig_p, t_rowid))
                st.success("Original payload restored! Integrity verified.")
                st.rerun()
            else:
                # Re-verify clean
                st.info("Chain is already in original state.")
                st.rerun()

    st.markdown("---")

    # Sequential Audit Event Log Table
    st.subheader("📜 " + t("event_log_title", lang))
    events = list_audit_events(conn=conn)

    if events:
        event_rows = []
        for idx, e in enumerate(events, start=1):
            prev_short = (e.prev_hash[:12] + "...") if e.prev_hash else "GENESIS"
            hash_short = (e.hash[:12] + "...") if e.hash else "NONE"
            event_rows.append({
                "Seq #": idx,
                "Event Type": e.event_type,
                "Entity": f"{e.entity_type}:{e.entity_id}",
                "Timestamp": e.timestamp.strftime("%Y-%m-%d %I:%M:%S %p"),
                "Previous Hash": prev_short,
                "Event Hash (SHA-256)": hash_short,
            })
        st.dataframe(pd.DataFrame(event_rows), use_container_width=True, hide_index=True)
    else:
        st.info("No audit events recorded yet.")
