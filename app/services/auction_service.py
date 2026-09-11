"""Auction engine service for reverse bidding flow."""

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from app.activity import publish_activity_event
from app.farmer_store import get_or_create_farmer, normalize_phone
from app.services.db import DB_LOCK, get_connection, init_extension_tables


VALID_STATUSES = {"OPEN", "CLOSED", "SOLD"}


def _now() -> int:
    return int(time.time())


def _as_bid(row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "listing_id": row["listing_id"],
        "buyer_id": row["buyer_id"],
        "bid_price_per_unit": float(row["bid_price_per_unit"]),
        "quantity": float(row["quantity"]),
        "created_at": int(row["created_at"]),
    }


def _get_highest_bid(conn, listing_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        """
        SELECT id, listing_id, buyer_id, bid_price_per_unit, quantity, created_at
        FROM bids
        WHERE listing_id = ?
        ORDER BY bid_price_per_unit DESC, created_at ASC
        LIMIT 1
        """,
        (listing_id,),
    ).fetchone()
    return _as_bid(row) if row else None


def _as_listing(conn, row, include_bids: bool = False) -> Dict[str, Any]:
    listing = {
        "id": row["id"],
        "farmer_phone": row["farmer_phone"],
        "crop_name": row["crop_name"],
        "quantity": float(row["quantity"]),
        "base_price_per_unit": float(row["base_price_per_unit"]),
        "status": row["status"],
        "expires_at": int(row["expires_at"]),
        "created_at": int(row["created_at"]),
        "accepted_bid_id": row["accepted_bid_id"],
        "buyer_id": row["buyer_id"],
        "sold_price_per_unit": float(row["sold_price_per_unit"]) if row["sold_price_per_unit"] is not None else None,
        "sold_quantity": float(row["sold_quantity"]) if row["sold_quantity"] is not None else None,
        "order_status": row["order_status"],
        "highest_bid": _get_highest_bid(conn, row["id"]),
    }
    if include_bids:
        rows = conn.execute(
            """
            SELECT id, listing_id, buyer_id, bid_price_per_unit, quantity, created_at
            FROM bids
            WHERE listing_id = ?
            ORDER BY bid_price_per_unit DESC, created_at DESC
            """,
            (row["id"],),
        ).fetchall()
        listing["bids"] = [_as_bid(r) for r in rows]
    return listing


def close_expired_listings() -> int:
    init_extension_tables()
    now = _now()

    with DB_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id FROM listings WHERE status = 'OPEN' AND expires_at <= ?",
                (now,),
            ).fetchall()
            if not rows:
                return 0

            conn.execute(
                "UPDATE listings SET status = 'CLOSED' WHERE status = 'OPEN' AND expires_at <= ?",
                (now,),
            )
            conn.commit()

            for row in rows:
                publish_activity_event(
                    event_type="listing_closed",
                    title="Listing Auto Closed",
                    details=f"Listing {row['id']} expired and was closed automatically.",
                    meta={"listing_id": row["id"]},
                )
            return len(rows)
        finally:
            conn.close()


def create_listing(
    farmer_phone: str,
    crop_name: str,
    quantity: float,
    base_price_per_unit: float,
    expires_in_minutes: int = 30,
) -> Dict[str, Any]:
    init_extension_tables()
    close_expired_listings()

    phone = normalize_phone(farmer_phone)
    if not phone:
        raise ValueError("Valid farmer_phone is required")
    if quantity <= 0:
        raise ValueError("quantity must be greater than zero")
    if base_price_per_unit <= 0:
        raise ValueError("base_price_per_unit must be greater than zero")

    get_or_create_farmer(name="Farmer", phone=phone, city="Kalaburgi")

    listing_id = str(uuid.uuid4())
    now = _now()
    expires_at = now + (int(expires_in_minutes) * 60)

    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO listings(
                    id, farmer_phone, crop_name, quantity, base_price_per_unit,
                    status, expires_at, created_at, order_status
                ) VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?, 'PENDING')
                """,
                (
                    listing_id,
                    phone,
                    crop_name.strip() or "Unknown",
                    float(quantity),
                    float(base_price_per_unit),
                    expires_at,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
            conn.commit()
            listing = _as_listing(conn, row, include_bids=True)
        finally:
            conn.close()

    publish_activity_event(
        event_type="listing_created",
        title="Auction Listing Created",
        details=f"{listing['crop_name']} {listing['quantity']:.0f} kg @ INR {listing['base_price_per_unit']:.2f}/kg",
        meta={"listing_id": listing_id, "farmer_phone": phone},
    )
    return listing


def get_listing_by_id(listing_id: str) -> Optional[Dict[str, Any]]:
    init_extension_tables()
    close_expired_listings()

    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
        if not row:
            return None
        return _as_listing(conn, row, include_bids=True)
    finally:
        conn.close()


def get_listings(status: Optional[str] = None, farmer_phone: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    init_extension_tables()
    close_expired_listings()

    where = []
    params: List[Any] = []

    if status:
        status = status.upper().strip()
        if status not in VALID_STATUSES:
            raise ValueError("status must be OPEN, CLOSED, or SOLD")
        where.append("status = ?")
        params.append(status)

    if farmer_phone:
        where.append("farmer_phone = ?")
        params.append(normalize_phone(farmer_phone))

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(max(1, min(limit, 200)))

    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT * FROM listings {where_sql} ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [_as_listing(conn, row, include_bids=False) for row in rows]
    finally:
        conn.close()


def place_bid(listing_id: str, buyer_id: str, bid_price_per_unit: float, quantity: float) -> Dict[str, Any]:
    init_extension_tables()
    close_expired_listings()

    if bid_price_per_unit <= 0:
        raise ValueError("bid_price_per_unit must be greater than zero")
    if quantity <= 0:
        raise ValueError("quantity must be greater than zero")

    buyer_identity = normalize_phone(buyer_id) or buyer_id.strip()
    if normalize_phone(buyer_identity):
        get_or_create_farmer(name="Buyer", phone=buyer_identity, city="Kalaburgi")

    bid_id = str(uuid.uuid4())
    now = _now()

    with DB_LOCK:
        conn = get_connection()
        try:
            listing = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
            if not listing:
                raise ValueError("Listing not found")
            if listing["status"] != "OPEN":
                raise ValueError("Listing is not open for bidding")
            if int(listing["expires_at"]) <= now:
                raise ValueError("Listing has already expired")
            if quantity > float(listing["quantity"]):
                raise ValueError("Bid quantity cannot exceed listing quantity")
            if bid_price_per_unit < float(listing["base_price_per_unit"]):
                raise ValueError("Bid must be greater than or equal to base price")

            conn.execute(
                """
                INSERT INTO bids(id, listing_id, buyer_id, bid_price_per_unit, quantity, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (bid_id, listing_id, buyer_identity, float(bid_price_per_unit), float(quantity), now),
            )

            bid_row = conn.execute("SELECT * FROM bids WHERE id = ?", (bid_id,)).fetchone()
            conn.commit()
            bid = _as_bid(bid_row)
        finally:
            conn.close()

    publish_activity_event(
        event_type="new_bid",
        title="New Bid Received",
        details=f"Listing {listing_id} got bid INR {bid_price_per_unit:.2f}/kg by {buyer_identity}",
        meta={"listing_id": listing_id, "bid_id": bid_id, "buyer_id": buyer_identity},
    )
    publish_activity_event(
        event_type="bid_placed",
        title="Bid Placed",
        details=f"Buyer {buyer_identity} bid INR {bid_price_per_unit:.2f}/kg on {listing_id}",
        meta={"listing_id": listing_id, "bid_id": bid_id, "buyer_id": buyer_identity},
    )

    return bid


def accept_bid(listing_id: str, bid_id: str, farmer_phone: Optional[str] = None) -> Dict[str, Any]:
    init_extension_tables()
    close_expired_listings()

    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")

            listing = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
            if not listing:
                raise ValueError("Listing not found")
            if listing["status"] != "OPEN":
                raise ValueError("Listing is not open")

            listing_farmer = listing["farmer_phone"]
            if farmer_phone and normalize_phone(farmer_phone) != listing_farmer:
                raise ValueError("Only listing owner can accept a bid")

            bid = conn.execute(
                "SELECT * FROM bids WHERE id = ? AND listing_id = ?",
                (bid_id, listing_id),
            ).fetchone()
            if not bid:
                raise ValueError("Bid not found for this listing")

            order_amount = float(bid["bid_price_per_unit"]) * float(bid["quantity"])
            buyer_id = bid["buyer_id"]

            buyer = conn.execute("SELECT wallet_balance FROM farmers WHERE phone = ?", (normalize_phone(buyer_id),)).fetchone()
            if not buyer:
                raise ValueError("Buyer wallet not found")

            current_balance = float(buyer["wallet_balance"])
            if current_balance < order_amount:
                raise ValueError(
                    f"Insufficient buyer wallet balance. Needed INR {order_amount:.2f}, available INR {current_balance:.2f}"
                )

            next_balance = round(current_balance - order_amount, 2)
            now = _now()
            order_id = f"AUC-{uuid.uuid4().hex[:10].upper()}"

            conn.execute(
                "UPDATE farmers SET wallet_balance = ?, updated_at = ? WHERE phone = ?",
                (next_balance, now, normalize_phone(buyer_id)),
            )
            conn.execute(
                """
                INSERT INTO transactions(phone, type, amount, description, timestamp, meta_json)
                VALUES (?, 'debit', ?, ?, ?, ?)
                """,
                (
                    normalize_phone(buyer_id),
                    order_amount,
                    f"Auction purchase for listing {listing_id}",
                    now,
                    json.dumps({"listing_id": listing_id, "bid_id": bid_id, "order_id": order_id}),
                ),
            )
            conn.execute(
                """
                INSERT INTO orders(phone, order_id, product, quantity, amount, dealer_name, status, timestamp, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalize_phone(buyer_id),
                    order_id,
                    listing["crop_name"],
                    f"{float(bid['quantity']):.2f} kg",
                    order_amount,
                    "AI Krishi Auction",
                    "placed",
                    now,
                    json.dumps(
                        {
                            "order_id": order_id,
                            "product": listing["crop_name"],
                            "quantity": f"{float(bid['quantity']):.2f} kg",
                            "amount": order_amount,
                            "dealer_name": "AI Krishi Auction",
                            "status": "placed",
                            "timestamp": now,
                            "listing_id": listing_id,
                            "bid_id": bid_id,
                        }
                    ),
                ),
            )

            conn.execute(
                """
                UPDATE listings
                SET status = 'SOLD', accepted_bid_id = ?, buyer_id = ?, sold_price_per_unit = ?, sold_quantity = ?, order_status = 'PENDING'
                WHERE id = ?
                """,
                (
                    bid_id,
                    normalize_phone(buyer_id),
                    float(bid["bid_price_per_unit"]),
                    float(bid["quantity"]),
                    listing_id,
                ),
            )

            row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
            conn.commit()
            listing_view = _as_listing(conn, row, include_bids=True)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    publish_activity_event(
        event_type="bid_accepted",
        title="Bid Accepted",
        details=f"Listing {listing_id} sold at INR {listing_view.get('sold_price_per_unit', 0):.2f}/kg",
        meta={
            "listing_id": listing_id,
            "bid_id": bid_id,
            "buyer_id": listing_view.get("buyer_id", ""),
            "farmer_phone": listing_view.get("farmer_phone", ""),
        },
    )
    return listing_view


def close_listing(listing_id: str, farmer_phone: Optional[str] = None) -> Dict[str, Any]:
    init_extension_tables()

    with DB_LOCK:
        conn = get_connection()
        try:
            row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
            if not row:
                raise ValueError("Listing not found")
            if farmer_phone and normalize_phone(farmer_phone) != row["farmer_phone"]:
                raise ValueError("Only listing owner can close the listing")
            if row["status"] == "SOLD":
                raise ValueError("Sold listing cannot be manually closed")

            conn.execute("UPDATE listings SET status = 'CLOSED' WHERE id = ?", (listing_id,))
            row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
            conn.commit()
            listing = _as_listing(conn, row, include_bids=True)
        finally:
            conn.close()

    publish_activity_event(
        event_type="listing_closed",
        title="Listing Closed",
        details=f"Listing {listing_id} was closed by farmer action.",
        meta={"listing_id": listing_id, "farmer_phone": listing.get("farmer_phone", "")},
    )
    return listing


def complete_order(listing_id: str, farmer_phone: Optional[str] = None) -> Dict[str, Any]:
    init_extension_tables()

    with DB_LOCK:
        conn = get_connection()
        try:
            row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
            if not row:
                raise ValueError("Listing not found")
            if row["status"] != "SOLD":
                raise ValueError("Only sold listings can be completed")
            if farmer_phone and normalize_phone(farmer_phone) != row["farmer_phone"]:
                raise ValueError("Only listing owner can complete the order")

            conn.execute(
                "UPDATE listings SET order_status = 'COMPLETED' WHERE id = ?",
                (listing_id,),
            )
            row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
            conn.commit()
            listing = _as_listing(conn, row, include_bids=True)
        finally:
            conn.close()

    publish_activity_event(
        event_type="order_completed",
        title="Auction Order Completed",
        details=f"Listing {listing_id} marked as completed.",
        meta={"listing_id": listing_id, "farmer_phone": listing.get("farmer_phone", "")},
    )
    return listing


def get_seller_listings(phone: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
    return get_listings(status=status, farmer_phone=phone, limit=200)


def get_seller_dashboard(phone: str) -> Dict[str, Any]:
    init_extension_tables()
    close_expired_listings()
    norm_phone = normalize_phone(phone)

    conn = get_connection()
    try:
        counts = conn.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'OPEN' THEN 1 ELSE 0 END) AS active_count,
                SUM(CASE WHEN status = 'CLOSED' THEN 1 ELSE 0 END) AS expired_count,
                SUM(CASE WHEN status = 'SOLD' THEN 1 ELSE 0 END) AS sold_count,
                COALESCE(SUM(CASE WHEN status = 'SOLD' THEN sold_price_per_unit * sold_quantity ELSE 0 END), 0) AS total_earnings,
                COALESCE(SUM(CASE WHEN status = 'SOLD' AND order_status = 'PENDING' THEN 1 ELSE 0 END), 0) AS pending_orders,
                COALESCE(SUM(CASE WHEN status = 'SOLD' AND order_status = 'COMPLETED' THEN 1 ELSE 0 END), 0) AS completed_sales
            FROM listings
            WHERE farmer_phone = ?
            """,
            (norm_phone,),
        ).fetchone()

        recent_rows = conn.execute(
            "SELECT * FROM listings WHERE farmer_phone = ? ORDER BY created_at DESC LIMIT 20",
            (norm_phone,),
        ).fetchall()
        recent = [_as_listing(conn, row, include_bids=False) for row in recent_rows]

        top_bids = []
        for row in recent_rows:
            highest = _get_highest_bid(conn, row["id"])
            if highest:
                top_bids.append({
                    "listing_id": row["id"],
                    "crop_name": row["crop_name"],
                    "highest_bid": highest,
                })

        return {
            "phone": norm_phone,
            "kpis": {
                "active_listings": int(counts["active_count"] or 0),
                "expired_listings": int(counts["expired_count"] or 0),
                "sold_listings": int(counts["sold_count"] or 0),
                "total_earnings": round(float(counts["total_earnings"] or 0), 2),
                "pending_orders": int(counts["pending_orders"] or 0),
                "completed_sales": int(counts["completed_sales"] or 0),
            },
            "top_bids": top_bids,
            "recent_listings": recent,
        }
    finally:
        conn.close()


def get_seller_revenue(phone: str) -> Dict[str, Any]:
    norm_phone = normalize_phone(phone)
    init_extension_tables()

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, crop_name, sold_price_per_unit, sold_quantity, order_status, created_at
            FROM listings
            WHERE farmer_phone = ? AND status = 'SOLD'
            ORDER BY created_at DESC
            """,
            (norm_phone,),
        ).fetchall()
        sales = []
        total = 0.0
        for row in rows:
            amount = float(row["sold_price_per_unit"] or 0) * float(row["sold_quantity"] or 0)
            total += amount
            sales.append(
                {
                    "listing_id": row["id"],
                    "crop_name": row["crop_name"],
                    "sold_price_per_unit": float(row["sold_price_per_unit"] or 0),
                    "sold_quantity": float(row["sold_quantity"] or 0),
                    "order_status": row["order_status"] or "PENDING",
                    "amount": round(amount, 2),
                    "created_at": int(row["created_at"]),
                }
            )

        return {
            "phone": norm_phone,
            "total_earnings": round(total, 2),
            "sales": sales,
        }
    finally:
        conn.close()


def get_buyer_bids(phone: str) -> List[Dict[str, Any]]:
    norm_phone = normalize_phone(phone)
    init_extension_tables()

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT b.id, b.listing_id, b.buyer_id, b.bid_price_per_unit, b.quantity, b.created_at,
                   l.crop_name, l.status, l.base_price_per_unit, l.sold_price_per_unit, l.order_status
            FROM bids b
            JOIN listings l ON l.id = b.listing_id
            WHERE b.buyer_id = ? OR b.buyer_id = ?
            ORDER BY b.created_at DESC
            LIMIT 200
            """,
            (norm_phone, phone.strip()),
        ).fetchall()

        result = []
        for row in rows:
            result.append(
                {
                    "bid_id": row["id"],
                    "listing_id": row["listing_id"],
                    "buyer_id": row["buyer_id"],
                    "crop_name": row["crop_name"],
                    "bid_price_per_unit": float(row["bid_price_per_unit"]),
                    "quantity": float(row["quantity"]),
                    "listing_status": row["status"],
                    "base_price_per_unit": float(row["base_price_per_unit"]),
                    "sold_price_per_unit": float(row["sold_price_per_unit"] or 0),
                    "order_status": row["order_status"] or "PENDING",
                    "created_at": int(row["created_at"]),
                }
            )
        return result
    finally:
        conn.close()


def get_buyer_dashboard(phone: str) -> Dict[str, Any]:
    norm_phone = normalize_phone(phone)
    bids = get_buyer_bids(norm_phone or phone)
    open_listings = get_listings(status="OPEN", limit=100)

    total_bids = len(bids)
    active_bids = sum(1 for b in bids if str(b.get("listing_status", "")).upper() == "OPEN")
    won_bids = sum(1 for b in bids if str(b.get("listing_status", "")).upper() == "SOLD")
    max_bid = max([float(b.get("bid_price_per_unit", 0)) for b in bids], default=0.0)

    return {
        "phone": norm_phone or phone,
        "kpis": {
            "total_bids": total_bids,
            "active_bids": active_bids,
            "won_bids": won_bids,
            "highest_bid_value": round(max_bid, 2),
            "open_listings": len(open_listings),
        },
        "open_listings": open_listings,
        "recent_bids": bids[:20],
    }
