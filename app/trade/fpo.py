"""
Farmer Producer Organisations and lot aggregation.

The problem statement names the pain directly: farmers sell small fragmented
volumes, while buyers "struggle to aggregate consistent volumes". This module
is the bridge. It pools compatible member lots into a single buyer-ready
consignment large enough to meet a processor's or exporter's minimum order.

The economics are already proven elsewhere in this codebase: freight per
quintal falls sharply with load size (see
`tests/test_logistics.py::test_freight_per_quintal_falls_as_load_grows`), so
aggregation raises every member's net realisation even at an unchanged price.
`estimate_aggregation_benefit` quantifies that for a specific pool.

The hard rule is compatibility. Lots are only pooled when commodity, grade and
variety match and their availability windows overlap. Mixing a Grade A lot
into a Grade C pool would drag the whole consignment down to the worst grade
present and quietly rob the farmer who brought the good produce.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.market.logistics import compute_net_realisation
from app.market.normalization import normalize_market_name, resolve_commodity
from app.services.db import DB_LOCK, get_connection
from app.trade import audit
from app.trade.lots import LotError, LotStatus, get_lot, lot_to_dict
from app.trade.schema import now_ts

logger = logging.getLogger(__name__)

# Minimum pooled weight worth creating an aggregate lot for. Below this the
# handling overhead outweighs the freight saving.
MIN_AGGREGATE_KG = 500.0


class FpoError(ValueError):
    """Invalid FPO operation."""


@dataclass
class AggregationGroup:
    """A set of member lots that can legitimately be pooled together."""

    commodity: str
    grade: str
    variety: Optional[str]
    lots: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def total_kg(self) -> float:
        return sum(float(l["quantity_kg"]) for l in self.lots)

    @property
    def farmer_count(self) -> int:
        return len({l["farmer_phone"] for l in self.lots if l["farmer_phone"]})

    @property
    def key(self) -> Tuple[str, str, Optional[str]]:
        return (self.commodity, self.grade, self.variety)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "commodity": self.commodity,
            "grade": self.grade,
            "variety": self.variety,
            "total_kg": round(self.total_kg, 1),
            "total_quintal": round(self.total_kg / 100.0, 2),
            "lot_count": len(self.lots),
            "farmer_count": self.farmer_count,
            "viable": self.total_kg >= MIN_AGGREGATE_KG,
            "lots": [
                {
                    "id": l["id"],
                    "farmer_phone": l["farmer_phone"],
                    "quantity_kg": l["quantity_kg"],
                    "pickup_village": l["pickup_village"],
                }
                for l in self.lots
            ],
        }


# ---------------------------------------------------------------------------
# FPO registry
# ---------------------------------------------------------------------------

def create_fpo(
    name: str,
    *,
    registration_no: Optional[str] = None,
    district: Optional[str] = None,
    state: Optional[str] = None,
    contact_phone: Optional[str] = None,
    actor: str = "admin",
) -> Dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise FpoError("FPO name is required")

    fpo_id = str(uuid.uuid4())
    ts = now_ts()

    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO fpos(id, name, registration_no, district, state,
                                 contact_phone, verification_status, created_at, updated_at)
                VALUES (?,?,?,?,?,?, 'UNVERIFIED', ?,?)
                """,
                (
                    fpo_id, name, registration_no,
                    normalize_market_name(district) if district else None,
                    state, contact_phone, ts, ts,
                ),
            )
            audit.record(
                audit.EntityType.FPO, fpo_id, audit.Action.CREATED,
                actor=actor, actor_role="admin",
                new_value={"name": name, "registration_no": registration_no},
                conn=conn,
            )
            conn.commit()
        finally:
            conn.close()

    logger.info("FPO registered: %s (%s)", name, fpo_id)
    return get_fpo(fpo_id)


def fpo_to_dict(row, member_count: int = 0) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "registration_no": row["registration_no"],
        "district": row["district"],
        "state": row["state"],
        "contact_phone": row["contact_phone"],
        "verification_status": row["verification_status"],
        "verified_at": row["verified_at"],
        "is_active": bool(row["is_active"]),
        "member_count": member_count,
        "created_at": row["created_at"],
    }


def get_fpo(fpo_id: str) -> Optional[Dict[str, Any]]:
    with DB_LOCK:
        conn = get_connection()
        try:
            row = conn.execute("SELECT * FROM fpos WHERE id = ?", (fpo_id,)).fetchone()
            if not row:
                return None
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM fpo_members WHERE fpo_id = ? AND is_active = 1",
                (fpo_id,),
            ).fetchone()["n"]
            return fpo_to_dict(row, member_count=count)
        finally:
            conn.close()


def list_fpos(district: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM fpos WHERE is_active = 1"
    params: List[Any] = []
    if district:
        sql += " AND LOWER(district) = LOWER(?)"
        params.append(district)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with DB_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(sql, params).fetchall()
            out = []
            for r in rows:
                count = conn.execute(
                    "SELECT COUNT(*) AS n FROM fpo_members WHERE fpo_id = ? AND is_active = 1",
                    (r["id"],),
                ).fetchone()["n"]
                out.append(fpo_to_dict(r, member_count=count))
            return out
        finally:
            conn.close()


def verify_fpo(fpo_id: str, verified_by: str = "admin") -> Dict[str, Any]:
    with DB_LOCK:
        conn = get_connection()
        try:
            if conn.execute("SELECT 1 FROM fpos WHERE id = ?", (fpo_id,)).fetchone() is None:
                raise FpoError(f"Unknown FPO {fpo_id}")
            ts = now_ts()
            conn.execute(
                "UPDATE fpos SET verification_status = 'VERIFIED', verified_at = ?, "
                "updated_at = ? WHERE id = ?",
                (ts, ts, fpo_id),
            )
            audit.record(
                audit.EntityType.FPO, fpo_id, audit.Action.VERIFIED,
                actor=verified_by, actor_role="admin",
                new_value={"verification_status": "VERIFIED"}, conn=conn,
            )
            conn.commit()
        finally:
            conn.close()
    return get_fpo(fpo_id)


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------

def add_member(
    fpo_id: str, farmer_phone: str, role: str = "MEMBER", actor: str = "admin"
) -> Dict[str, Any]:
    if not get_fpo(fpo_id):
        raise FpoError(f"Unknown FPO {fpo_id}")
    farmer_phone = (farmer_phone or "").strip()
    if not farmer_phone:
        raise FpoError("farmer_phone is required")

    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO fpo_members(fpo_id, farmer_phone, role, joined_at, is_active)
                VALUES (?,?,?,?,1)
                ON CONFLICT(fpo_id, farmer_phone) DO UPDATE SET
                    is_active = 1, role = excluded.role
                """,
                (fpo_id, farmer_phone, role, now_ts()),
            )
            audit.record(
                audit.EntityType.FPO, fpo_id, audit.Action.MEMBER_ADDED,
                actor=actor, actor_role="admin",
                new_value={"farmer_phone": farmer_phone, "role": role},
                conn=conn,
            )
            conn.commit()
        finally:
            conn.close()

    return {"fpo_id": fpo_id, "farmer_phone": farmer_phone, "role": role, "is_active": True}


def remove_member(fpo_id: str, farmer_phone: str, actor: str = "admin") -> Dict[str, Any]:
    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE fpo_members SET is_active = 0 WHERE fpo_id = ? AND farmer_phone = ?",
                (fpo_id, farmer_phone),
            )
            audit.record(
                audit.EntityType.FPO, fpo_id, audit.Action.MEMBER_REMOVED,
                actor=actor, actor_role="admin",
                old_value={"farmer_phone": farmer_phone}, conn=conn,
            )
            conn.commit()
        finally:
            conn.close()
    return {"fpo_id": fpo_id, "farmer_phone": farmer_phone, "is_active": False}


def list_members(fpo_id: str) -> List[Dict[str, Any]]:
    with DB_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM fpo_members WHERE fpo_id = ? AND is_active = 1 ORDER BY joined_at",
                (fpo_id,),
            ).fetchall()
        finally:
            conn.close()
    return [
        {"farmer_phone": r["farmer_phone"], "role": r["role"], "joined_at": r["joined_at"]}
        for r in rows
    ]


def get_farmer_fpos(farmer_phone: str) -> List[Dict[str, Any]]:
    with DB_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT f.* FROM fpos f
                JOIN fpo_members m ON m.fpo_id = f.id
                WHERE m.farmer_phone = ? AND m.is_active = 1 AND f.is_active = 1
                """,
                (farmer_phone,),
            ).fetchall()
            return [fpo_to_dict(r) for r in rows]
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _windows_overlap(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """
    Whether two lots are available at a common time.

    Absent windows are treated as always-available: a farmer who did not
    state a window should not be excluded from pooling.
    """
    a_start, a_end = a.get("available_from"), a.get("available_until")
    b_start, b_end = b.get("available_from"), b.get("available_until")
    if a_end is not None and b_start is not None and a_end < b_start:
        return False
    if b_end is not None and a_start is not None and b_end < a_start:
        return False
    return True


def find_aggregation_groups(
    fpo_id: str,
    commodity: Optional[str] = None,
) -> List[AggregationGroup]:
    """
    Group an FPO's published member lots into compatible pools.

    Grouping key is (commodity, grade, variety). Incompatible produce is
    never combined, and each group reports whether it clears the minimum
    worth aggregating.
    """
    if not get_fpo(fpo_id):
        raise FpoError(f"Unknown FPO {fpo_id}")

    members = {m["farmer_phone"] for m in list_members(fpo_id)}
    if not members:
        return []

    placeholders = ",".join("?" for _ in members)
    sql = f"""
        SELECT * FROM lots
        WHERE status = ?
          AND is_aggregate = 0
          AND parent_lot_id IS NULL
          AND (farmer_phone IN ({placeholders}) OR fpo_id = ?)
    """
    params: List[Any] = [LotStatus.PUBLISHED, *members, fpo_id]

    if commodity:
        match = resolve_commodity(commodity)
        sql += " AND LOWER(commodity) = LOWER(?)"
        params.append(match.canonical if match else commodity)

    with DB_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    lots = [lot_to_dict(r) for r in rows]

    buckets: Dict[Tuple[str, str, Optional[str]], List[Dict[str, Any]]] = defaultdict(list)
    for lot in lots:
        key = (lot["commodity"], lot["grade"] or "UNGRADED", lot["variety"])
        buckets[key].append(lot)

    groups: List[AggregationGroup] = []
    for (commodity_name, grade, variety), bucket in buckets.items():
        # Within a compatible bucket, split further on availability so a lot
        # that is only free next month does not block one ready today.
        remaining = list(bucket)
        while remaining:
            seed = remaining.pop(0)
            cluster = [seed]
            still: List[Dict[str, Any]] = []
            for candidate in remaining:
                if all(_windows_overlap(candidate, member) for member in cluster):
                    cluster.append(candidate)
                else:
                    still.append(candidate)
            remaining = still
            groups.append(AggregationGroup(commodity_name, grade, variety, cluster))

    groups.sort(key=lambda g: g.total_kg, reverse=True)
    return groups


def estimate_aggregation_benefit(
    group: AggregationGroup,
    price_per_quintal: float,
    distance_km: float,
    perishable: bool = False,
) -> Dict[str, Any]:
    """
    Quantify what pooling is worth, in rupees.

    Compares each member shipping alone against one pooled consignment, using
    the same net-realisation model the market engine uses for everything else.
    The gain is real: freight is charged per vehicle, so small separate loads
    each pay a minimum fare that a single larger load pays once.
    """
    if not group.lots:
        raise FpoError("Cannot estimate benefit for an empty group")

    separate_total = 0.0
    for lot in group.lots:
        qty = float(lot["quantity_kg"])
        if qty <= 0:
            continue
        alone = compute_net_realisation(
            market="aggregate-comparison",
            gross_price_per_quintal=price_per_quintal,
            distance_km=distance_km,
            quantity_kg=qty,
            perishable=perishable,
        )
        separate_total += alone.total_net

    pooled = compute_net_realisation(
        market="aggregate-comparison",
        gross_price_per_quintal=price_per_quintal,
        distance_km=distance_km,
        quantity_kg=group.total_kg,
        perishable=perishable,
    )

    gain = pooled.total_net - separate_total
    quintals = group.total_kg / 100.0

    return {
        "total_kg": round(group.total_kg, 1),
        "farmer_count": group.farmer_count,
        "lot_count": len(group.lots),
        "price_per_quintal": price_per_quintal,
        "distance_km": round(distance_km, 1),
        "net_if_sold_separately": round(separate_total, 2),
        "net_if_aggregated": round(pooled.total_net, 2),
        "total_gain": round(gain, 2),
        "gain_per_quintal": round(gain / quintals, 2) if quintals else 0.0,
        "gain_per_farmer": (
            round(gain / group.farmer_count, 2) if group.farmer_count else 0.0
        ),
        "vehicle_when_aggregated": pooled.vehicle,
        "explanation": (
            "Freight is charged per vehicle, so several small loads each pay a minimum "
            "fare. Pooling them into one consignment pays that once."
        ),
    }


def create_aggregate_lot(
    fpo_id: str,
    lot_ids: Sequence[str],
    *,
    expected_price: Optional[float] = None,
    pickup_village: Optional[str] = None,
    pickup_district: Optional[str] = None,
    actor: str = "fpo",
) -> Dict[str, Any]:
    """
    Pool member lots into one FPO consignment.

    Member lots move to AGGREGATED and point at the parent, so nothing is
    lost and each farmer's contribution stays traceable for settlement.
    Refuses to mix incompatible produce.
    """
    if not get_fpo(fpo_id):
        raise FpoError(f"Unknown FPO {fpo_id}")
    if len(lot_ids) < 2:
        raise FpoError("Aggregation needs at least two lots")

    lots = [get_lot(lid) for lid in lot_ids]
    missing = [lid for lid, lot in zip(lot_ids, lots) if lot is None]
    if missing:
        raise FpoError(f"Unknown lot(s): {', '.join(missing)}")

    wrong_status = [l["id"] for l in lots if l["status"] != LotStatus.PUBLISHED]
    if wrong_status:
        raise FpoError(
            f"Only PUBLISHED lots can be aggregated; these are not: {', '.join(wrong_status)}"
        )

    already = [l["id"] for l in lots if l["parent_lot_id"] or l["is_aggregate"]]
    if already:
        raise FpoError(f"These lots are already part of an aggregate: {', '.join(already)}")

    # Compatibility: one commodity, one grade, one variety.
    commodities = {l["commodity"] for l in lots}
    grades = {(l["grade"] or "UNGRADED") for l in lots}
    varieties = {l["variety"] for l in lots}
    if len(commodities) > 1:
        raise FpoError(f"Cannot aggregate different commodities: {', '.join(sorted(commodities))}")
    if len(grades) > 1:
        raise FpoError(
            f"Cannot aggregate different grades: {', '.join(sorted(grades))}. "
            "Pooling them would drag the whole consignment down to the lowest grade."
        )
    if len(varieties) > 1:
        raise FpoError("Cannot aggregate different varieties")

    total_kg = sum(float(l["quantity_kg"]) for l in lots)
    commodity = commodities.pop()
    grade = grades.pop()
    variety = varieties.pop()

    # The pooled claim is only as strong as its weakest member's evidence.
    basis_rank = ["SELF_DECLARED", "AI_ESTIMATED", "BUYER_ACCEPTED", "INSPECTED", "VERIFIED"]
    weakest_basis = min(
        (l["grade_basis"] for l in lots), key=lambda b: basis_rank.index(b)
    )

    parent_id = str(uuid.uuid4())
    ts = now_ts()
    first = lots[0]

    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO lots(
                    id, farmer_phone, fpo_id, is_aggregate, commodity, variety, grade,
                    grade_basis, quantity_kg, expected_price, pickup_village,
                    pickup_district, pickup_state, status, created_at, updated_at
                ) VALUES (?, NULL, ?, 1, ?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    parent_id, fpo_id, commodity, variety, grade, weakest_basis,
                    total_kg, expected_price,
                    pickup_village or first["pickup_village"],
                    normalize_market_name(pickup_district) if pickup_district
                    else first["pickup_district"],
                    first["pickup_state"], LotStatus.PUBLISHED, ts, ts,
                ),
            )

            for lot in lots:
                conn.execute(
                    "UPDATE lots SET parent_lot_id = ?, status = ?, updated_at = ? WHERE id = ?",
                    (parent_id, LotStatus.AGGREGATED, ts, lot["id"]),
                )
                audit.record(
                    audit.EntityType.LOT, lot["id"], audit.Action.AGGREGATED,
                    actor=actor, actor_role="fpo",
                    old_value={"status": lot["status"]},
                    new_value={"status": LotStatus.AGGREGATED, "parent_lot_id": parent_id},
                    conn=conn,
                )

            audit.record(
                audit.EntityType.LOT, parent_id, audit.Action.CREATED,
                actor=actor, actor_role="fpo",
                new_value={
                    "is_aggregate": True, "commodity": commodity, "grade": grade,
                    "quantity_kg": total_kg, "member_lots": list(lot_ids),
                },
                metadata={"fpo_id": fpo_id, "farmer_count": len(
                    {l["farmer_phone"] for l in lots if l["farmer_phone"]}
                )},
                conn=conn,
            )
            conn.commit()
        finally:
            conn.close()

    logger.info(
        "FPO %s aggregated %d lots into %s (%.0f kg of %s)",
        fpo_id, len(lot_ids), parent_id, total_kg, commodity,
    )
    return get_lot(parent_id, include_children=True)


def split_settlement(parent_lot_id: str, total_amount_paise: int) -> List[Dict[str, Any]]:
    """
    Apportion an aggregate sale back to contributing farmers, by weight.

    Works in integer paise and gives any rounding remainder to the largest
    contributor, so the parts always sum exactly to the total. A settlement
    that loses a paisa to rounding is a settlement that cannot be audited.
    """
    children = [c for c in _child_rows(parent_lot_id) if float(c["quantity_kg"]) > 0]
    if not children:
        raise FpoError(f"Lot {parent_lot_id} has no member lots to settle")

    total_kg = sum(float(c["quantity_kg"]) for c in children)
    shares: List[Dict[str, Any]] = []
    allocated = 0

    for child in children:
        qty = float(child["quantity_kg"])
        amount = int(total_amount_paise * qty / total_kg)
        allocated += amount
        shares.append({
            "lot_id": child["id"],
            "farmer_phone": child["farmer_phone"],
            "quantity_kg": qty,
            "share_pct": round(qty / total_kg * 100.0, 4),
            "amount_paise": amount,
            "amount_rupees": round(amount / 100.0, 2),
        })

    remainder = total_amount_paise - allocated
    if remainder:
        biggest = max(shares, key=lambda s: s["quantity_kg"])
        biggest["amount_paise"] += remainder
        biggest["amount_rupees"] = round(biggest["amount_paise"] / 100.0, 2)
        biggest["rounding_remainder_paise"] = remainder

    return shares


def _child_rows(parent_lot_id: str):
    with DB_LOCK:
        conn = get_connection()
        try:
            return conn.execute(
                "SELECT * FROM lots WHERE parent_lot_id = ? ORDER BY quantity_kg DESC",
                (parent_lot_id,),
            ).fetchall()
        finally:
            conn.close()
