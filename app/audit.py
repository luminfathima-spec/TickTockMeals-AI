"""Tamper-evident audit trail with SHA-256 hash chaining for TickTock Meals.

Every state change, triage decision, match, message, and verification checklist
writes an append-only AuditEvent.
Verification detects any modified, corrupted, or deleted records.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from app.clock import now as get_clock_now
from app.db import create_audit_event, get_connection
from app.models import AuditEvent


def compute_event_hash(
    prev_hash: Optional[str],
    event_id: str,
    timestamp: Union[datetime, str],
    event_type: str,
    entity_type: str,
    entity_id: str,
    payload: Optional[Union[Dict[str, Any], str]] = None,
) -> str:
    """Compute deterministic SHA-256 digest of an audit event linked to prev_hash."""
    prev_hash_str = prev_hash if prev_hash else "GENESIS"
    ts_str = timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp)
    payload_str = (
        json.dumps(payload, sort_keys=True, default=str)
        if isinstance(payload, dict)
        else (str(payload) if payload else "")
    )

    raw_canonical = (
        f"{prev_hash_str}::{event_id}::{ts_str}::{event_type}::"
        f"{entity_type}::{entity_id}::{payload_str}"
    )
    return hashlib.sha256(raw_canonical.encode("utf-8")).hexdigest()


def get_latest_event_hash(conn: Optional[sqlite3.Connection] = None) -> Optional[str]:
    """Retrieve the hash of the most recent audit event in the database."""
    c = conn or get_connection()
    close_after = conn is None
    try:
        cursor = c.execute("SELECT hash FROM audit_events ORDER BY rowid DESC LIMIT 1")
        row = cursor.fetchone()
        return row["hash"] if row else None
    finally:
        if close_after:
            c.close()


def record_event(
    event_type: str,
    entity_type: str,
    entity_id: str,
    payload: Optional[Dict[str, Any]] = None,
    conn: Optional[sqlite3.Connection] = None,
    timestamp: Optional[datetime] = None,
    event_id: Optional[str] = None,
) -> AuditEvent:
    """Append a new tamper-evident event to the hash chain and persist it in SQLite."""
    c = conn or get_connection()
    close_after = conn is None
    current_time = timestamp or get_clock_now()
    eid = event_id or f"aud_{uuid.uuid4().hex[:12]}"

    try:
        prev_hash = get_latest_event_hash(conn=c) or "GENESIS"
        event_hash = compute_event_hash(
            prev_hash=prev_hash,
            event_id=eid,
            timestamp=current_time,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
        )

        event = AuditEvent(
            id=eid,
            timestamp=current_time,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
            prev_hash=prev_hash,
            hash=event_hash,
        )
        create_audit_event(event, conn=c)
        return event
    finally:
        if close_after:
            c.close()


def verify_chain(conn: Optional[sqlite3.Connection] = None) -> Tuple[bool, str, Optional[int]]:
    """Verify integrity of the audit event chain.

    Recomputes every event hash and verifies previous-hash links.
    Returns:
        (is_valid, report_message, first_broken_row_index_or_none)
    """
    c = conn or get_connection()
    close_after = conn is None

    try:
        cursor = c.execute(
            """
            SELECT rowid, id, timestamp, event_type, entity_type, entity_id, payload, prev_hash, hash
            FROM audit_events ORDER BY rowid ASC
            """
        )
        rows = cursor.fetchall()
        if not rows:
            return True, "OK: Audit chain is empty (0 events).", None

        expected_prev_hash: Optional[str] = None

        for idx, row in enumerate(rows, start=1):
            stored_prev_hash = row["prev_hash"]
            stored_hash = row["hash"]

            # Genesis link check
            if idx == 1:
                if stored_prev_hash not in (None, "GENESIS"):
                    return (
                        False,
                        f"Broken chain link at row 1 (event id '{row['id']}'): invalid genesis prev_hash '{stored_prev_hash}'",
                        1,
                    )
            else:
                if stored_prev_hash != expected_prev_hash:
                    return (
                        False,
                        f"Broken chain link at row {idx} (event id '{row['id']}'): prev_hash mismatch. "
                        f"Expected '{expected_prev_hash}', found '{stored_prev_hash}'",
                        idx,
                    )

            # Recompute and compare hash
            payload_data = json.loads(row["payload"]) if row["payload"] else None
            computed_hash = compute_event_hash(
                prev_hash=stored_prev_hash,
                event_id=row["id"],
                timestamp=row["timestamp"],
                event_type=row["event_type"],
                entity_type=row["entity_type"],
                entity_id=row["entity_id"],
                payload=payload_data,
            )

            if computed_hash != stored_hash:
                return (
                    False,
                    f"Tampered audit entry at row {idx} (event id '{row['id']}'): hash mismatch. "
                    f"Computed '{computed_hash}', stored '{stored_hash}'",
                    idx,
                )

            expected_prev_hash = stored_hash

        return True, f"Audit chain integrity verified ({len(rows)} events, 0 errors).", None
    finally:
        if close_after:
            c.close()


def print_verify_status() -> int:
    """CLI handler for audit chain integrity verification."""
    conn = get_connection()
    valid, message, broken_row = verify_chain(conn=conn)
    conn.close()

    if valid:
        print(f"OK: {message}")
        return 0
    else:
        print(f"FAIL: {message} (Broken row index: {broken_row})")
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TickTock Meals Audit Trail Verification")
    parser.add_argument("--verify", action="store_true", help="Verify hash-chain integrity of audit log")
    args = parser.parse_args()

    if args.verify:
        sys.exit(print_verify_status())
    else:
        parser.print_help()
