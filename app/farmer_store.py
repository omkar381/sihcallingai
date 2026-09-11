"""Persistent farmer profile and wallet store using SQLite."""

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from typing import Dict, Any, Optional

_lock = threading.Lock()
_db_initialized = False

_DEFAULT_WALLET = 20000.0
_DB_PATH = os.path.join("data", "krishi.sqlite3")

_PRICE_CATALOG = {
    "urea": {"unit_price": 295.0, "unit": "bag"},
    "dap": {"unit_price": 1350.0, "unit": "bag"},
    "hybrid seeds": {"unit_price": 1800.0, "unit": "packet"},
    "pesticides": {"unit_price": 650.0, "unit": "litre"},
    "neem oil": {"unit_price": 420.0, "unit": "litre"},
}


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return ""
    if len(digits) == 10:
        return f"+91{digits}"
    if digits.startswith("91") and len(digits) == 12:
        return f"+{digits}"
    if phone.startswith("+"):
        return phone
    return f"+{digits}"


def _now() -> int:
    return int(time.time())

def generate_ugfid(state: Optional[str], district: Optional[str], phone: str) -> str:
    st = (state or "KA")[:2].upper().ljust(2, 'A')
    dt = (district or "KAL")[:3].upper().ljust(3, 'A')
    hsh = hashlib.sha256(phone.encode()).hexdigest()[:6].upper()
    return f"KR-IND-{st}-{dt}-{hsh}"


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    global _db_initialized
    if _db_initialized:
        return

    with _lock:
        if _db_initialized:
            return

        conn = _connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS farmers (
                    phone TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    city TEXT NOT NULL,
                    wallet_balance REAL NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone TEXT NOT NULL,
                    order_id TEXT,
                    product TEXT,
                    quantity TEXT,
                    amount REAL,
                    dealer_name TEXT,
                    status TEXT,
                    timestamp INTEGER NOT NULL,
                    raw_json TEXT,
                    FOREIGN KEY(phone) REFERENCES farmers(phone)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone TEXT NOT NULL,
                    type TEXT NOT NULL,
                    amount REAL NOT NULL,
                    description TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    meta_json TEXT,
                    FOREIGN KEY(phone) REFERENCES farmers(phone)
                )
                """
            )
            try:
                conn.execute("ALTER TABLE farmers ADD COLUMN ugfid TEXT")
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ugfid ON farmers(ugfid)")
                conn.execute("ALTER TABLE farmers ADD COLUMN village TEXT")
                conn.execute("ALTER TABLE farmers ADD COLUMN taluk TEXT")
                conn.execute("ALTER TABLE farmers ADD COLUMN district TEXT")
                conn.execute("ALTER TABLE farmers ADD COLUMN state TEXT")
                conn.execute("ALTER TABLE farmers ADD COLUMN land_size TEXT")
                conn.execute("ALTER TABLE farmers ADD COLUMN crop_types TEXT")
            except sqlite3.OperationalError:
                pass
            conn.commit()
            _db_initialized = True
        finally:
            conn.close()


def _load_farmer(conn: sqlite3.Connection, phone: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM farmers WHERE phone = ?",
        (phone,),
    ).fetchone()
    if not row:
        return None

    orders_rows = conn.execute(
        "SELECT raw_json FROM orders WHERE phone = ? ORDER BY timestamp DESC, id DESC",
        (phone,),
    ).fetchall()
    tx_rows = conn.execute(
        "SELECT type, amount, description, timestamp, meta_json FROM transactions WHERE phone = ? ORDER BY timestamp DESC, id DESC",
        (phone,),
    ).fetchall()

    orders = []
    for r in orders_rows:
        try:
            orders.append(json.loads(r["raw_json"] or "{}"))
        except Exception:
            pass

    transactions = []
    for r in tx_rows:
        meta = {}
        try:
            meta = json.loads(r["meta_json"] or "{}")
        except Exception:
            meta = {}
        transactions.append(
            {
                "type": r["type"],
                "amount": r["amount"],
                "description": r["description"],
                "timestamp": r["timestamp"],
                "meta": meta,
            }
        )

    row_dict = dict(row)
    return {
        "name": row_dict["name"],
        "phone": row_dict["phone"],
        "city": row_dict["city"],
        "wallet_balance": row_dict["wallet_balance"],
        "created_at": row_dict["created_at"],
        "updated_at": row_dict["updated_at"],
        "ugfid": row_dict.get("ugfid"),
        "village": row_dict.get("village"),
        "taluk": row_dict.get("taluk"),
        "district": row_dict.get("district"),
        "state": row_dict.get("state"),
        "land_size": row_dict.get("land_size"),
        "crop_types": row_dict.get("crop_types"),
        "orders": orders,
        "transactions": transactions,
    }


def get_or_create_farmer(name: str, phone: str, city: str = "Kalaburgi") -> Dict[str, Any]:
    _init_db()
    norm_phone = normalize_phone(phone)
    if not norm_phone:
        raise ValueError("Valid phone number required")

    with _lock:
        conn = _connect()
        try:
            farmer = _load_farmer(conn, norm_phone)
            now = _now()
            if not farmer:
                ugfid = generate_ugfid("Karnataka", city, norm_phone)
                conn.execute(
                    "INSERT INTO farmers(phone, name, city, wallet_balance, created_at, updated_at, ugfid, district, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (norm_phone, name or "Farmer", city or "Kalaburgi", _DEFAULT_WALLET, now, now, ugfid, city or "Kalaburgi", "Karnataka"),
                )
                conn.execute(
                    "INSERT INTO transactions(phone, type, amount, description, timestamp, meta_json) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        norm_phone,
                        "credit",
                        _DEFAULT_WALLET,
                        "Initial wallet balance",
                        now,
                        json.dumps({}),
                    ),
                )
                conn.commit()
            else:
                conn.execute(
                    "UPDATE farmers SET name = ?, city = ?, updated_at = ? WHERE phone = ?",
                    (
                        name or farmer.get("name", "Farmer"),
                        city or farmer.get("city", "Kalaburgi"),
                        now,
                        norm_phone,
                    ),
                )
                conn.commit()

            latest = _load_farmer(conn, norm_phone)
            return dict(latest or {})
        finally:
            conn.close()


def get_farmer(phone: str) -> Optional[Dict[str, Any]]:
    _init_db()
    norm_phone = normalize_phone(phone)
    if not norm_phone:
        return None

    conn = _connect()
    try:
        farmer = _load_farmer(conn, norm_phone)
        return dict(farmer) if farmer else None
    finally:
        conn.close()


def update_farmer_profile(
    phone: str, 
    name: Optional[str] = None, 
    city: Optional[str] = None,
    village: Optional[str] = None,
    taluk: Optional[str] = None,
    district: Optional[str] = None,
    state: Optional[str] = None,
    land_size: Optional[str] = None,
    crop_types: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    _init_db()
    norm_phone = normalize_phone(phone)
    if not norm_phone:
        return None

    with _lock:
        conn = _connect()
        try:
            farmer = _load_farmer(conn, norm_phone)
            if not farmer:
                return None

            next_name = (name or "").strip() or farmer.get("name", "Farmer")
            next_city = (city or "").strip() or farmer.get("city", "Kalaburgi")
            next_village = (village or "").strip() or farmer.get("village", "")
            next_taluk = (taluk or "").strip() or farmer.get("taluk", "")
            next_district = (district or "").strip() or farmer.get("district", "")
            next_state = (state or "").strip() or farmer.get("state", "")
            next_land_size = (land_size or "").strip() or farmer.get("land_size", "")
            next_crop_types = (crop_types or "").strip() or farmer.get("crop_types", "")
            
            # If ugfid is somehow missing (e.g. from existing DB before migration), generate it now
            next_ugfid = farmer.get("ugfid")
            if not next_ugfid:
                next_ugfid = generate_ugfid(next_state, next_district or next_city, norm_phone)

            conn.execute(
                """
                UPDATE farmers 
                SET name = ?, city = ?, updated_at = ?, ugfid = ?, village = ?, taluk = ?, district = ?, state = ?, land_size = ?, crop_types = ?
                WHERE phone = ?
                """,
                (next_name, next_city, _now(), next_ugfid, next_village, next_taluk, next_district, next_state, next_land_size, next_crop_types, norm_phone),
            )
            conn.commit()
            return _load_farmer(conn, norm_phone)
        finally:
            conn.close()


def parse_quantity(quantity: str) -> Dict[str, Any]:
    text = (quantity or "1").lower().strip()
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]+)?", text)
    if not match:
        return {"value": 1.0, "unit": "unit"}

    value = float(match.group(1))
    unit = (match.group(2) or "unit").lower()

    aliases = {
        "kgs": "kg",
        "kilogram": "kg",
        "kilograms": "kg",
        "quintals": "quintal",
        "bags": "bag",
        "packets": "packet",
        "liters": "litre",
        "litres": "litre",
        "bottles": "bottle",
    }
    unit = aliases.get(unit, unit)

    return {"value": value, "unit": unit}


def estimate_order_amount(product: str, quantity: str) -> Dict[str, Any]:
    product_key = (product or "").strip().lower()
    parsed = parse_quantity(quantity)
    qty_value = parsed["value"]
    qty_unit = parsed["unit"]

    pricing = _PRICE_CATALOG.get(product_key)
    if not pricing:
        # Generic fallback price.
        pricing = {"unit_price": 100.0, "unit": "kg"}

    unit_price = pricing["unit_price"]
    price_unit = pricing["unit"]

    # If unit mismatches, still use numeric qty with catalog unit price.
    amount = round(qty_value * unit_price, 2)

    return {
        "product": product,
        "quantity": quantity,
        "qty_value": qty_value,
        "qty_unit": qty_unit,
        "price_unit": price_unit,
        "unit_price": unit_price,
        "amount": amount,
    }


def debit_wallet(phone: str, amount: float, description: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    _init_db()
    norm_phone = normalize_phone(phone)
    if not norm_phone:
        return {"success": False, "reason": "invalid_phone"}

    with _lock:
        conn = _connect()
        try:
            farmer = conn.execute(
                "SELECT wallet_balance FROM farmers WHERE phone = ?",
                (norm_phone,),
            ).fetchone()
            if not farmer:
                return {"success": False, "reason": "farmer_not_found"}

            balance = float(farmer["wallet_balance"])
            if balance < amount:
                return {
                    "success": False,
                    "reason": "insufficient_balance",
                    "wallet_balance": balance,
                    "required": amount,
                }

            next_balance = round(balance - amount, 2)
            now = _now()
            conn.execute(
                "UPDATE farmers SET wallet_balance = ?, updated_at = ? WHERE phone = ?",
                (next_balance, now, norm_phone),
            )

            tx = {
                "type": "debit",
                "amount": amount,
                "description": description,
                "timestamp": now,
                "meta": metadata or {},
            }
            conn.execute(
                "INSERT INTO transactions(phone, type, amount, description, timestamp, meta_json) VALUES (?, ?, ?, ?, ?, ?)",
                (norm_phone, "debit", amount, description, now, json.dumps(tx["meta"], ensure_ascii=False)),
            )
            conn.commit()

            return {
                "success": True,
                "wallet_balance": next_balance,
                "transaction": tx,
            }
        finally:
            conn.close()


def record_order(phone: str, order_data: Dict[str, Any]) -> None:
    _init_db()
    norm_phone = normalize_phone(phone)
    if not norm_phone:
        return

    with _lock:
        conn = _connect()
        try:
            farmer = conn.execute(
                "SELECT phone FROM farmers WHERE phone = ?",
                (norm_phone,),
            ).fetchone()
            if not farmer:
                return

            ts = int(order_data.get("timestamp") or _now())
            conn.execute(
                """
                INSERT INTO orders(phone, order_id, product, quantity, amount, dealer_name, status, timestamp, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    norm_phone,
                    str(order_data.get("order_id", "")),
                    str(order_data.get("product", "")),
                    str(order_data.get("quantity", "")),
                    float(order_data.get("amount", 0) or 0),
                    str(order_data.get("dealer_name", "")),
                    str(order_data.get("status", "placed")),
                    ts,
                    json.dumps(order_data, ensure_ascii=False),
                ),
            )
            conn.execute(
                "UPDATE farmers SET updated_at = ? WHERE phone = ?",
                (_now(), norm_phone),
            )
            conn.commit()
        finally:
            conn.close()
