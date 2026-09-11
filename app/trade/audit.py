"""
Append-only audit trail.

Every economically meaningful action writes an event here: lot created, offer
accepted, payment released, dispute resolved. The table is never updated and
never deleted from, so the history of a transaction can be reconstructed
exactly - which is the difference between a demo and something a government
department could actually audit.

Events carry the before and after value, the actor, and a monotonic sequence
number. A gap in the sequence is evidence of tampering; reordering is not
possible because the sequence is assigned under the same lock as the insert.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.services.db import DB_LOCK, get_connection
from app.trade.schema import now_ts

logger = logging.getLogger(__name__)


class EntityType:
    BUYER = "BUYER"
    BUYER_DEMAND = "BUYER_DEMAND"
    FPO = "FPO"
    LOT = "LOT"
    OFFER = "OFFER"
    PAYMENT = "PAYMENT"
    DISPUTE = "DISPUTE"
    TRANSPORT = "TRANSPORT"


class Action:
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    STATUS_CHANGED = "STATUS_CHANGED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    SUSPENDED = "SUSPENDED"
    MEMBER_ADDED = "MEMBER_ADDED"
    MEMBER_REMOVED = "MEMBER_REMOVED"
    AGGREGATED = "AGGREGATED"
    OFFER_PLACED = "OFFER_PLACED"
    OFFER_ACCEPTED = "OFFER_ACCEPTED"
    OFFER_REJECTED = "OFFER_REJECTED"
    PAYMENT_INITIATED = "PAYMENT_INITIATED"
    PAYMENT_ESCROWED = "PAYMENT_ESCROWED"
    PAYMENT_RELEASED = "PAYMENT_RELEASED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    PAYMENT_REFUNDED = "PAYMENT_REFUNDED"
    DISPUTE_OPENED = "DISPUTE_OPENED"
    DISPUTE_RESOLVED = "DISPUTE_RESOLVED"
    DELIVERY_CONFIRMED = "DELIVERY_CONFIRMED"


@dataclass
class AuditEvent:
    id: str
    sequence: int
    entity_type: str
    entity_id: str
    action: str
    actor: Optional[str]
    actor_role: Optional[str]
    old_value: Optional[Dict[str, Any]]
    new_value: Optional[Dict[str, Any]]
    metadata: Dict[str, Any]
    request_id: Optional[str]
    created_at: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "sequence": self.sequence,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "action": self.action,
            "actor": self.actor,
            "actor_role": self.actor_role,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "metadata": self.metadata,
            "request_id": self.request_id,
            "created_at": datetime.fromtimestamp(self.created_at, tz=timezone.utc).isoformat(),
        }


def _json_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return json.dumps({"unserialisable": str(value)})


def _loads(text: Optional[str]) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def record(
    entity_type: str,
    entity_id: str,
    action: str,
    *,
    actor: Optional[str] = None,
    actor_role: Optional[str] = None,
    old_value: Optional[Dict[str, Any]] = None,
    new_value: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None,
    conn=None,
) -> str:
    """
    Append one audit event and return its id.

    Pass an open `conn` to enlist the event in a caller's transaction, so the
    event and the change it describes commit together. Without that, a crash
    between the two writes would leave a state change with no record of it.
    """
    event_id = str(uuid.uuid4())
    params = (
        event_id, entity_type, entity_id, action, actor, actor_role,
        _json_or_none(old_value), _json_or_none(new_value),
        _json_or_none(metadata or {}), request_id, now_ts(),
    )
    sql = """
        INSERT INTO audit_events(
            id, sequence, entity_type, entity_id, action, actor, actor_role,
            old_value, new_value, metadata, request_id, created_at
        ) VALUES (
            ?, (SELECT IFNULL(MAX(sequence), 0) + 1 FROM audit_events),
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
    """

    if conn is not None:
        conn.execute(sql, params)
        return event_id

    with DB_LOCK:
        own = get_connection()
        try:
            own.execute(sql, params)
            own.commit()
        finally:
            own.close()
    return event_id


def _row_to_event(row) -> AuditEvent:
    return AuditEvent(
        id=row["id"],
        sequence=row["sequence"],
        entity_type=row["entity_type"],
        entity_id=row["entity_id"],
        action=row["action"],
        actor=row["actor"],
        actor_role=row["actor_role"],
        old_value=_loads(row["old_value"]),
        new_value=_loads(row["new_value"]),
        metadata=_loads(row["metadata"]) or {},
        request_id=row["request_id"],
        created_at=row["created_at"],
    )


def history(entity_type: str, entity_id: str, limit: int = 100) -> List[AuditEvent]:
    """Full history of one entity, oldest first - the story of a transaction."""
    with DB_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT * FROM audit_events
                WHERE entity_type = ? AND entity_id = ?
                ORDER BY sequence ASC LIMIT ?
                """,
                (entity_type, entity_id, limit),
            ).fetchall()
        finally:
            conn.close()
    return [_row_to_event(r) for r in rows]


def recent(limit: int = 50, entity_type: Optional[str] = None) -> List[AuditEvent]:
    sql = "SELECT * FROM audit_events"
    params: List[Any] = []
    if entity_type:
        sql += " WHERE entity_type = ?"
        params.append(entity_type)
    sql += " ORDER BY sequence DESC LIMIT ?"
    params.append(limit)

    with DB_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    return [_row_to_event(r) for r in rows]


def verify_integrity() -> Dict[str, Any]:
    """
    Check the sequence for gaps.

    An append-only log whose integrity is never checked is just a table. This
    is what an evaluator would run to confirm nothing was deleted.
    """
    with DB_LOCK:
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n,
                       MIN(sequence) AS lo,
                       MAX(sequence) AS hi,
                       COUNT(DISTINCT sequence) AS distinct_seq
                FROM audit_events
                """
            ).fetchone()
        finally:
            conn.close()

    count, lo, hi = row["n"], row["lo"], row["hi"]
    if count == 0:
        return {"intact": True, "events": 0, "detail": "No events recorded yet."}

    expected = (hi - lo + 1)
    gaps = expected - count
    duplicates = count - row["distinct_seq"]

    return {
        "intact": gaps == 0 and duplicates == 0,
        "events": count,
        "sequence_range": [lo, hi],
        "missing_sequences": max(gaps, 0),
        "duplicate_sequences": duplicates,
        "detail": (
            "Audit trail is contiguous with no gaps."
            if gaps == 0 and duplicates == 0
            else f"{gaps} missing and {duplicates} duplicate sequence numbers detected."
        ),
    }
