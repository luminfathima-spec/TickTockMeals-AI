"""SQLite database repository for TickTock Meals.

Provides zero-ORM lightweight persistence with parameterized queries,
schema initialization, and clean CRUD operations for all domain models.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from app.config import get_config
from app.models import (
    AuditEvent,
    Donor,
    DonorKind,
    HistoricalListing,
    Listing,
    ListingStatus,
    Match,
    OutboxMessage,
    Recipient,
    Tier,
    VehicleType,
    Volunteer,
)


def get_connection(db_path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
    """Create and return a configured SQLite connection."""
    target_path = Path(db_path) if db_path else Path(get_config().db_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(conn_or_path: Optional[Union[sqlite3.Connection, str, Path]] = None) -> None:
    """Create database tables if they do not already exist."""
    close_after = False
    if isinstance(conn_or_path, sqlite3.Connection):
        conn = conn_or_path
    else:
        conn = get_connection(conn_or_path)
        close_after = True

    try:
        with conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS donors (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    lat REAL NOT NULL,
                    lon REAL NOT NULL,
                    area TEXT NOT NULL,
                    typical_surplus_portions INTEGER NOT NULL,
                    active_hours TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS recipients (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    lat REAL NOT NULL,
                    lon REAL NOT NULL,
                    area TEXT NOT NULL,
                    capacity_portions_per_day INTEGER NOT NULL,
                    veg_only INTEGER NOT NULL,
                    open_from TEXT NOT NULL,
                    open_to TEXT NOT NULL,
                    languages TEXT NOT NULL,
                    accepts_cooked INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS volunteers (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    lat REAL NOT NULL,
                    lon REAL NOT NULL,
                    vehicle TEXT NOT NULL,
                    max_portions_carry INTEGER NOT NULL,
                    languages TEXT NOT NULL,
                    available INTEGER NOT NULL,
                    reliability REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS listings (
                    id TEXT PRIMARY KEY,
                    donor_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    photo_path TEXT,
                    passport TEXT,
                    safety TEXT,
                    tier TEXT NOT NULL,
                    status TEXT NOT NULL,
                    expires_at TEXT,
                    FOREIGN KEY (donor_id) REFERENCES donors(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS matches (
                    id TEXT PRIMARY KEY,
                    listing_id TEXT NOT NULL,
                    recipient_id TEXT NOT NULL,
                    volunteer_id TEXT,
                    score REAL NOT NULL,
                    score_breakdown TEXT,
                    why_this_match TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    responded_at TEXT,
                    FOREIGN KEY (listing_id) REFERENCES listings(id) ON DELETE CASCADE,
                    FOREIGN KEY (recipient_id) REFERENCES recipients(id) ON DELETE CASCADE,
                    FOREIGN KEY (volunteer_id) REFERENCES volunteers(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS outbox_messages (
                    id TEXT PRIMARY KEY,
                    recipient_or_volunteer_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    language TEXT NOT NULL,
                    message_text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    match_id TEXT
                );

                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    payload TEXT,
                    prev_hash TEXT,
                    hash TEXT
                );

                CREATE TABLE IF NOT EXISTS historical_listings (
                    id TEXT PRIMARY KEY,
                    donor_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    weekday TEXT NOT NULL,
                    category TEXT NOT NULL,
                    dish_name TEXT NOT NULL,
                    portions INTEGER NOT NULL,
                    tier_rescued TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    FOREIGN KEY (donor_id) REFERENCES donors(id) ON DELETE CASCADE
                );
                """
            )
    finally:
        if close_after:
            conn.close()


# ---------------- Donor CRUD ----------------

def create_donor(donor: Donor, conn: Optional[sqlite3.Connection] = None) -> Donor:
    c = conn or get_connection()
    try:
        with c:
            c.execute(
                """
                INSERT INTO donors (id, name, kind, lat, lon, area, typical_surplus_portions, active_hours)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (donor.id, donor.name, donor.kind.value, donor.lat, donor.lon, donor.area,
                 donor.typical_surplus_portions, donor.active_hours),
            )
        return donor
    finally:
        if conn is None:
            c.close()


def get_donor(donor_id: str, conn: Optional[sqlite3.Connection] = None) -> Optional[Donor]:
    c = conn or get_connection()
    try:
        cursor = c.execute("SELECT * FROM donors WHERE id = ?", (donor_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return Donor(
            id=row["id"],
            name=row["name"],
            kind=DonorKind(row["kind"]),
            lat=row["lat"],
            lon=row["lon"],
            area=row["area"],
            typical_surplus_portions=row["typical_surplus_portions"],
            active_hours=row["active_hours"],
        )
    finally:
        if conn is None:
            c.close()


def list_donors(conn: Optional[sqlite3.Connection] = None) -> List[Donor]:
    c = conn or get_connection()
    try:
        cursor = c.execute("SELECT * FROM donors ORDER BY id ASC")
        return [
            Donor(
                id=r["id"],
                name=r["name"],
                kind=DonorKind(r["kind"]),
                lat=r["lat"],
                lon=r["lon"],
                area=r["area"],
                typical_surplus_portions=r["typical_surplus_portions"],
                active_hours=r["active_hours"],
            )
            for r in cursor.fetchall()
        ]
    finally:
        if conn is None:
            c.close()


def delete_donor(donor_id: str, conn: Optional[sqlite3.Connection] = None) -> bool:
    c = conn or get_connection()
    try:
        with c:
            cur = c.execute("DELETE FROM donors WHERE id = ?", (donor_id,))
            return cur.rowcount > 0
    finally:
        if conn is None:
            c.close()


# ---------------- Recipient CRUD ----------------

def create_recipient(recipient: Recipient, conn: Optional[sqlite3.Connection] = None) -> Recipient:
    c = conn or get_connection()
    try:
        with c:
            c.execute(
                """
                INSERT INTO recipients (
                    id, name, tier, lat, lon, area, capacity_portions_per_day,
                    veg_only, open_from, open_to, languages, accepts_cooked
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recipient.id,
                    recipient.name,
                    recipient.tier.value,
                    recipient.lat,
                    recipient.lon,
                    recipient.area,
                    recipient.capacity_portions_per_day,
                    1 if recipient.veg_only else 0,
                    recipient.open_from,
                    recipient.open_to,
                    json.dumps(recipient.languages),
                    1 if recipient.accepts_cooked else 0,
                ),
            )
        return recipient
    finally:
        if conn is None:
            c.close()


def get_recipient(recipient_id: str, conn: Optional[sqlite3.Connection] = None) -> Optional[Recipient]:
    c = conn or get_connection()
    try:
        cursor = c.execute("SELECT * FROM recipients WHERE id = ?", (recipient_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return Recipient(
            id=row["id"],
            name=row["name"],
            tier=Tier(row["tier"]),
            lat=row["lat"],
            lon=row["lon"],
            area=row["area"],
            capacity_portions_per_day=row["capacity_portions_per_day"],
            veg_only=bool(row["veg_only"]),
            open_from=row["open_from"],
            open_to=row["open_to"],
            languages=json.loads(row["languages"]) if row["languages"] else ["en"],
            accepts_cooked=bool(row["accepts_cooked"]),
        )
    finally:
        if conn is None:
            c.close()


def list_recipients(tier: Optional[Tier] = None, conn: Optional[sqlite3.Connection] = None) -> List[Recipient]:
    c = conn or get_connection()
    try:
        if tier:
            cursor = c.execute("SELECT * FROM recipients WHERE tier = ? ORDER BY id ASC", (tier.value,))
        else:
            cursor = c.execute("SELECT * FROM recipients ORDER BY id ASC")
        return [
            Recipient(
                id=r["id"],
                name=r["name"],
                tier=Tier(r["tier"]),
                lat=r["lat"],
                lon=r["lon"],
                area=r["area"],
                capacity_portions_per_day=r["capacity_portions_per_day"],
                veg_only=bool(r["veg_only"]),
                open_from=r["open_from"],
                open_to=r["open_to"],
                languages=json.loads(r["languages"]) if r["languages"] else ["en"],
                accepts_cooked=bool(r["accepts_cooked"]),
            )
            for r in cursor.fetchall()
        ]
    finally:
        if conn is None:
            c.close()


def delete_recipient(recipient_id: str, conn: Optional[sqlite3.Connection] = None) -> bool:
    c = conn or get_connection()
    try:
        with c:
            cur = c.execute("DELETE FROM recipients WHERE id = ?", (recipient_id,))
            return cur.rowcount > 0
    finally:
        if conn is None:
            c.close()


# ---------------- Volunteer CRUD ----------------

def create_volunteer(volunteer: Volunteer, conn: Optional[sqlite3.Connection] = None) -> Volunteer:
    c = conn or get_connection()
    try:
        with c:
            c.execute(
                """
                INSERT INTO volunteers (
                    id, name, lat, lon, vehicle, max_portions_carry, languages, available, reliability
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    volunteer.id,
                    volunteer.name,
                    volunteer.lat,
                    volunteer.lon,
                    volunteer.vehicle.value,
                    volunteer.max_portions_carry,
                    json.dumps(volunteer.languages),
                    1 if volunteer.available else 0,
                    volunteer.reliability,
                ),
            )
        return volunteer
    finally:
        if conn is None:
            c.close()


def get_volunteer(volunteer_id: str, conn: Optional[sqlite3.Connection] = None) -> Optional[Volunteer]:
    c = conn or get_connection()
    try:
        cursor = c.execute("SELECT * FROM volunteers WHERE id = ?", (volunteer_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return Volunteer(
            id=row["id"],
            name=row["name"],
            lat=row["lat"],
            lon=row["lon"],
            vehicle=VehicleType(row["vehicle"]),
            max_portions_carry=row["max_portions_carry"],
            languages=json.loads(row["languages"]) if row["languages"] else ["en"],
            available=bool(row["available"]),
            reliability=row["reliability"],
        )
    finally:
        if conn is None:
            c.close()


def list_volunteers(only_available: bool = False, conn: Optional[sqlite3.Connection] = None) -> List[Volunteer]:
    c = conn or get_connection()
    try:
        if only_available:
            cursor = c.execute("SELECT * FROM volunteers WHERE available = 1 ORDER BY id ASC")
        else:
            cursor = c.execute("SELECT * FROM volunteers ORDER BY id ASC")
        return [
            Volunteer(
                id=r["id"],
                name=r["name"],
                lat=r["lat"],
                lon=r["lon"],
                vehicle=VehicleType(r["vehicle"]),
                max_portions_carry=r["max_portions_carry"],
                languages=json.loads(r["languages"]) if r["languages"] else ["en"],
                available=bool(r["available"]),
                reliability=r["reliability"],
            )
            for r in cursor.fetchall()
        ]
    finally:
        if conn is None:
            c.close()


def update_volunteer(volunteer: Volunteer, conn: Optional[sqlite3.Connection] = None) -> Volunteer:
    c = conn or get_connection()
    try:
        with c:
            c.execute(
                """
                UPDATE volunteers SET
                    name = ?, lat = ?, lon = ?, vehicle = ?,
                    max_portions_carry = ?, languages = ?, available = ?, reliability = ?
                WHERE id = ?
                """,
                (
                    volunteer.name,
                    volunteer.lat,
                    volunteer.lon,
                    volunteer.vehicle.value,
                    volunteer.max_portions_carry,
                    json.dumps(volunteer.languages),
                    1 if volunteer.available else 0,
                    volunteer.reliability,
                    volunteer.id,
                ),
            )
        return volunteer
    finally:
        if conn is None:
            c.close()


# ---------------- Listing CRUD ----------------

def create_listing(listing: Listing, conn: Optional[sqlite3.Connection] = None) -> Listing:
    c = conn or get_connection()
    try:
        with c:
            c.execute(
                """
                INSERT INTO listings (
                    id, donor_id, created_at, photo_path, passport, safety, tier, status, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    listing.id,
                    listing.donor_id,
                    listing.created_at.isoformat(),
                    listing.photo_path,
                    json.dumps(listing.passport) if listing.passport else None,
                    json.dumps(listing.safety) if listing.safety else None,
                    listing.tier.value if isinstance(listing.tier, Tier) else str(listing.tier),
                    listing.status.value,
                    listing.expires_at.isoformat() if listing.expires_at else None,
                ),
            )
        return listing
    finally:
        if conn is None:
            c.close()


def get_listing(listing_id: str, conn: Optional[sqlite3.Connection] = None) -> Optional[Listing]:
    c = conn or get_connection()
    try:
        cursor = c.execute("SELECT * FROM listings WHERE id = ?", (listing_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return Listing(
            id=row["id"],
            donor_id=row["donor_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            photo_path=row["photo_path"],
            passport=json.loads(row["passport"]) if row["passport"] else None,
            safety=json.loads(row["safety"]) if row["safety"] else None,
            tier=Tier(row["tier"]) if row["tier"] in Tier.__members__ else Tier.HUMAN,
            status=ListingStatus(row["status"]),
            expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
        )
    finally:
        if conn is None:
            c.close()


def list_listings(
    status: Optional[ListingStatus] = None,
    tier: Optional[Tier] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> List[Listing]:
    c = conn or get_connection()
    try:
        query = "SELECT * FROM listings WHERE 1=1"
        params: List[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status.value)
        if tier:
            query += " AND tier = ?"
            params.append(tier.value)
        query += " ORDER BY created_at DESC"

        cursor = c.execute(query, params)
        results = []
        for row in cursor.fetchall():
            results.append(
                Listing(
                    id=row["id"],
                    donor_id=row["donor_id"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    photo_path=row["photo_path"],
                    passport=json.loads(row["passport"]) if row["passport"] else None,
                    safety=json.loads(row["safety"]) if row["safety"] else None,
                    tier=Tier(row["tier"]) if row["tier"] in Tier.__members__ else Tier.HUMAN,
                    status=ListingStatus(row["status"]),
                    expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
                )
            )
        return results
    finally:
        if conn is None:
            c.close()


def update_listing(listing: Listing, conn: Optional[sqlite3.Connection] = None) -> Listing:
    c = conn or get_connection()
    try:
        with c:
            c.execute(
                """
                UPDATE listings SET
                    donor_id = ?, created_at = ?, photo_path = ?, passport = ?,
                    safety = ?, tier = ?, status = ?, expires_at = ?
                WHERE id = ?
                """,
                (
                    listing.donor_id,
                    listing.created_at.isoformat(),
                    listing.photo_path,
                    json.dumps(listing.passport) if listing.passport else None,
                    json.dumps(listing.safety) if listing.safety else None,
                    listing.tier.value if isinstance(listing.tier, Tier) else str(listing.tier),
                    listing.status.value,
                    listing.expires_at.isoformat() if listing.expires_at else None,
                    listing.id,
                ),
            )
        return listing
    finally:
        if conn is None:
            c.close()


# ---------------- Match CRUD ----------------

def create_match(match: Match, conn: Optional[sqlite3.Connection] = None) -> Match:
    c = conn or get_connection()
    try:
        with c:
            c.execute(
                """
                INSERT INTO matches (
                    id, listing_id, recipient_id, volunteer_id, score,
                    score_breakdown, why_this_match, status, created_at, responded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match.id,
                    match.listing_id,
                    match.recipient_id,
                    match.volunteer_id,
                    match.score,
                    json.dumps(match.score_breakdown) if match.score_breakdown else None,
                    match.why_this_match,
                    match.status,
                    match.created_at.isoformat(),
                    match.responded_at.isoformat() if match.responded_at else None,
                ),
            )
        return match
    finally:
        if conn is None:
            c.close()


def get_match(match_id: str, conn: Optional[sqlite3.Connection] = None) -> Optional[Match]:
    c = conn or get_connection()
    try:
        cursor = c.execute("SELECT * FROM matches WHERE id = ?", (match_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return Match(
            id=row["id"],
            listing_id=row["listing_id"],
            recipient_id=row["recipient_id"],
            volunteer_id=row["volunteer_id"],
            score=row["score"],
            score_breakdown=json.loads(row["score_breakdown"]) if row["score_breakdown"] else None,
            why_this_match=row["why_this_match"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            responded_at=datetime.fromisoformat(row["responded_at"]) if row["responded_at"] else None,
        )
    finally:
        if conn is None:
            c.close()


def list_matches(listing_id: Optional[str] = None, conn: Optional[sqlite3.Connection] = None) -> List[Match]:
    c = conn or get_connection()
    try:
        if listing_id:
            cursor = c.execute("SELECT * FROM matches WHERE listing_id = ? ORDER BY score DESC", (listing_id,))
        else:
            cursor = c.execute("SELECT * FROM matches ORDER BY created_at DESC")
        return [
            Match(
                id=r["id"],
                listing_id=r["listing_id"],
                recipient_id=r["recipient_id"],
                volunteer_id=r["volunteer_id"],
                score=r["score"],
                score_breakdown=json.loads(r["score_breakdown"]) if r["score_breakdown"] else None,
                why_this_match=r["why_this_match"],
                status=r["status"],
                created_at=datetime.fromisoformat(r["created_at"]),
                responded_at=datetime.fromisoformat(r["responded_at"]) if r["responded_at"] else None,
            )
            for r in cursor.fetchall()
        ]
    finally:
        if conn is None:
            c.close()


# ---------------- Outbox Message CRUD ----------------

def create_outbox_message(msg: OutboxMessage, conn: Optional[sqlite3.Connection] = None) -> OutboxMessage:
    c = conn or get_connection()
    try:
        with c:
            c.execute(
                """
                INSERT INTO outbox_messages (
                    id, recipient_or_volunteer_id, role, language, message_text, status, created_at, match_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    msg.id,
                    msg.recipient_or_volunteer_id,
                    msg.role,
                    msg.language,
                    msg.message_text,
                    msg.status,
                    msg.created_at.isoformat(),
                    msg.match_id,
                ),
            )
        return msg
    finally:
        if conn is None:
            c.close()


def get_outbox_message(msg_id: str, conn: Optional[sqlite3.Connection] = None) -> Optional[OutboxMessage]:
    c = conn or get_connection()
    try:
        cursor = c.execute("SELECT * FROM outbox_messages WHERE id = ?", (msg_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return OutboxMessage(
            id=row["id"],
            recipient_or_volunteer_id=row["recipient_or_volunteer_id"],
            role=row["role"],
            language=row["language"],
            message_text=row["message_text"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            match_id=row["match_id"],
        )
    finally:
        if conn is None:
            c.close()


def list_outbox_messages(conn: Optional[sqlite3.Connection] = None) -> List[OutboxMessage]:
    c = conn or get_connection()
    try:
        cursor = c.execute("SELECT * FROM outbox_messages ORDER BY created_at DESC")
        return [
            OutboxMessage(
                id=r["id"],
                recipient_or_volunteer_id=r["recipient_or_volunteer_id"],
                role=r["role"],
                language=r["language"],
                message_text=r["message_text"],
                status=r["status"],
                created_at=datetime.fromisoformat(r["created_at"]),
                match_id=r["match_id"],
            )
            for r in cursor.fetchall()
        ]
    finally:
        if conn is None:
            c.close()


# ---------------- Audit Event CRUD ----------------

def create_audit_event(event: AuditEvent, conn: Optional[sqlite3.Connection] = None) -> AuditEvent:
    c = conn or get_connection()
    try:
        with c:
            c.execute(
                """
                INSERT INTO audit_events (
                    id, timestamp, event_type, entity_type, entity_id, payload, prev_hash, hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.timestamp.isoformat(),
                    event.event_type,
                    event.entity_type,
                    event.entity_id,
                    json.dumps(event.payload) if event.payload else None,
                    event.prev_hash,
                    event.hash,
                ),
            )
        return event
    finally:
        if conn is None:
            c.close()


def list_audit_events(conn: Optional[sqlite3.Connection] = None) -> List[AuditEvent]:
    c = conn or get_connection()
    try:
        cursor = c.execute("SELECT * FROM audit_events ORDER BY timestamp ASC")
        return [
            AuditEvent(
                id=r["id"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                event_type=r["event_type"],
                entity_type=r["entity_type"],
                entity_id=r["entity_id"],
                payload=json.loads(r["payload"]) if r["payload"] else None,
                prev_hash=r["prev_hash"],
                hash=r["hash"],
            )
            for r in cursor.fetchall()
        ]
    finally:
        if conn is None:
            c.close()


# ---------------- Historical Listing CRUD ----------------

def create_historical_listing(item: HistoricalListing, conn: Optional[sqlite3.Connection] = None) -> HistoricalListing:
    c = conn or get_connection()
    try:
        with c:
            c.execute(
                """
                INSERT INTO historical_listings (
                    id, donor_id, date, weekday, category, dish_name, portions, tier_rescued, outcome
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.donor_id,
                    item.date,
                    item.weekday,
                    item.category,
                    item.dish_name,
                    item.portions,
                    item.tier_rescued,
                    item.outcome,
                ),
            )
        return item
    finally:
        if conn is None:
            c.close()


def list_historical_listings(
    donor_id: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> List[HistoricalListing]:
    c = conn or get_connection()
    try:
        if donor_id:
            cursor = c.execute("SELECT * FROM historical_listings WHERE donor_id = ? ORDER BY date ASC", (donor_id,))
        else:
            cursor = c.execute("SELECT * FROM historical_listings ORDER BY date ASC")
        return [
            HistoricalListing(
                id=r["id"],
                donor_id=r["donor_id"],
                date=r["date"],
                weekday=r["weekday"],
                category=r["category"],
                dish_name=r["dish_name"],
                portions=r["portions"],
                tier_rescued=r["tier_rescued"],
                outcome=r["outcome"],
            )
            for r in cursor.fetchall()
        ]
    finally:
        if conn is None:
            c.close()
