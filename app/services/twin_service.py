"""Digital twin service for personalized farmer intelligence."""

import json
import time
from typing import Any, Dict, List

from app.activity import publish_activity_event
from app.farmer_store import get_or_create_farmer, normalize_phone
from app.services.db import DB_LOCK, get_connection, init_extension_tables


def _now() -> int:
    return int(time.time())


def _json_list(text: str) -> List[str]:
    try:
        data = json.loads(text or "[]")
        if isinstance(data, list):
            return [str(x) for x in data if str(x).strip()]
    except Exception:
        return []
    return []


def _risk_score(avg_yield: float, unique_crops: int, sold_count: int) -> float:
    score = 35.0
    if avg_yield < 1.0:
        score += 25.0
    elif avg_yield < 2.5:
        score += 12.0

    if unique_crops <= 1:
        score += 15.0
    elif unique_crops == 2:
        score += 8.0

    if sold_count == 0:
        score += 12.0

    return max(0.0, min(100.0, round(score, 2)))


def _to_twin(row) -> Dict[str, Any]:
    return {
        "farmer_phone": row["farmer_phone"],
        "crops_grown": _json_list(row["crops_grown"]),
        "land_size": float(row["land_size"]) if row["land_size"] is not None else None,
        "soil_type": row["soil_type"] or "Unknown",
        "water_source": row["water_source"] or "Unknown",
        "avg_yield": float(row["avg_yield"] or 0),
        "preferred_crops": _json_list(row["preferred_crops"]),
        "risk_score": float(row["risk_score"] or 0),
        "last_updated": int(row["last_updated"]),
    }


def _ensure_twin_row(phone: str) -> None:
    init_extension_tables()
    norm_phone = normalize_phone(phone)
    if not norm_phone:
        raise ValueError("invalid phone")

    get_or_create_farmer(name="Farmer", phone=norm_phone, city="Kalaburgi")

    with DB_LOCK:
        conn = get_connection()
        try:
            row = conn.execute("SELECT farmer_phone FROM farmer_twin WHERE farmer_phone = ?", (norm_phone,)).fetchone()
            if row:
                return
            now = _now()
            conn.execute(
                """
                INSERT INTO farmer_twin(
                    farmer_phone, crops_grown, land_size, soil_type, water_source,
                    avg_yield, preferred_crops, risk_score, last_updated
                ) VALUES (?, '[]', NULL, 'Unknown', 'Unknown', 0, '[]', 50, ?)
                """,
                (norm_phone, now),
            )
            conn.commit()
        finally:
            conn.close()


def refresh_twin_from_system(phone: str) -> Dict[str, Any]:
    _ensure_twin_row(phone)
    norm_phone = normalize_phone(phone)

    with DB_LOCK:
        conn = get_connection()
        try:
            twin_row = conn.execute("SELECT * FROM farmer_twin WHERE farmer_phone = ?", (norm_phone,)).fetchone()

            sold_rows = conn.execute(
                """
                SELECT crop_name, sold_quantity
                FROM listings
                WHERE farmer_phone = ? AND status = 'SOLD'
                """,
                (norm_phone,),
            ).fetchall()
            order_rows = conn.execute(
                "SELECT product FROM orders WHERE phone = ?",
                (norm_phone,),
            ).fetchall()

            sold_crops = [str(r["crop_name"]).strip() for r in sold_rows if str(r["crop_name"]).strip()]
            order_crops = [str(r["product"]).strip() for r in order_rows if str(r["product"]).strip()]
            all_crops = sorted({*sold_crops, *order_crops})
            preferred = sorted(set(sold_crops))[:5]

            total_qty = sum(float(r["sold_quantity"] or 0) for r in sold_rows)
            sold_count = len(sold_rows)
            avg_yield = round(total_qty / sold_count, 2) if sold_count else float(twin_row["avg_yield"] or 0)
            risk = _risk_score(avg_yield=avg_yield, unique_crops=len(all_crops), sold_count=sold_count)

            now = _now()
            conn.execute(
                """
                UPDATE farmer_twin
                SET crops_grown = ?, preferred_crops = ?, avg_yield = ?, risk_score = ?, last_updated = ?
                WHERE farmer_phone = ?
                """,
                (
                    json.dumps(all_crops, ensure_ascii=False),
                    json.dumps(preferred, ensure_ascii=False),
                    avg_yield,
                    risk,
                    now,
                    norm_phone,
                ),
            )
            conn.commit()

            row = conn.execute("SELECT * FROM farmer_twin WHERE farmer_phone = ?", (norm_phone,)).fetchone()
            return _to_twin(row)
        finally:
            conn.close()


def get_twin(phone: str) -> Dict[str, Any]:
    return refresh_twin_from_system(phone)


def patch_twin(phone: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    _ensure_twin_row(phone)
    norm_phone = normalize_phone(phone)

    updates = []
    params = []

    if "crops_grown" in payload and payload["crops_grown"] is not None:
        updates.append("crops_grown = ?")
        params.append(json.dumps(payload["crops_grown"], ensure_ascii=False))

    if "land_size" in payload and payload["land_size"] is not None:
        updates.append("land_size = ?")
        params.append(float(payload["land_size"]))

    if "soil_type" in payload and payload["soil_type"] is not None:
        updates.append("soil_type = ?")
        params.append(str(payload["soil_type"]).strip() or "Unknown")

    if "water_source" in payload and payload["water_source"] is not None:
        updates.append("water_source = ?")
        params.append(str(payload["water_source"]).strip() or "Unknown")

    if "avg_yield" in payload and payload["avg_yield"] is not None:
        updates.append("avg_yield = ?")
        params.append(float(payload["avg_yield"]))

    if "preferred_crops" in payload and payload["preferred_crops"] is not None:
        updates.append("preferred_crops = ?")
        params.append(json.dumps(payload["preferred_crops"], ensure_ascii=False))

    if "risk_score" in payload and payload["risk_score"] is not None:
        updates.append("risk_score = ?")
        params.append(float(payload["risk_score"]))

    with DB_LOCK:
        conn = get_connection()
        try:
            if updates:
                updates.append("last_updated = ?")
                params.append(_now())
                params.append(norm_phone)
                conn.execute(
                    f"UPDATE farmer_twin SET {', '.join(updates)} WHERE farmer_phone = ?",
                    tuple(params),
                )
                conn.commit()

            row = conn.execute("SELECT * FROM farmer_twin WHERE farmer_phone = ?", (norm_phone,)).fetchone()
            twin = _to_twin(row)
        finally:
            conn.close()

    publish_activity_event(
        event_type="twin_updated",
        title="Digital Twin Updated",
        details=f"Twin profile updated for {norm_phone}",
        meta={"farmer_phone": norm_phone},
    )
    return twin


async def get_recommendations(phone: str) -> Dict[str, Any]:
    from app.gemini_ai import get_farming_advice

    twin = refresh_twin_from_system(phone)

    recommendations: List[str] = []
    top_crop = twin["preferred_crops"][0] if twin["preferred_crops"] else "tomato"

    if twin["risk_score"] >= 65:
        recommendations.append("Your risk score is high. Diversify crops and split sales into smaller batches.")
    elif twin["risk_score"] >= 45:
        recommendations.append("Your risk score is moderate. Lock one crop contract and keep one crop for spot market.")
    else:
        recommendations.append("Your risk score is healthy. You can target higher-margin crops this season.")

    if twin["avg_yield"] < 2.0:
        recommendations.append("Your yield is below average. Consider soil test and micronutrient correction before next sowing.")
    else:
        recommendations.append("Your yield trend is stable. Focus on market timing to improve profits.")

    recommendations.append(f"Based on your profile, {top_crop} is a strong candidate for your next cycle.")

    ai_prompt = (
        "Use this digital twin context and provide personalized farmer advisory in 3 concise sentences. "
        "Include one crop suggestion, one risk mitigation action, and one market timing suggestion. "
        f"Twin Data: {json.dumps(twin, ensure_ascii=False)}"
    )

    ai_summary = await get_farming_advice(
        call_sid=f"twin-{twin['farmer_phone']}",
        farmer_query=ai_prompt,
        crop=top_crop,
        soil=twin["soil_type"],
        location="Kalaburgi",
        weather="Local seasonal",
    )

    return {
        "farmer_phone": twin["farmer_phone"],
        "generated_at": _now(),
        "recommendations": recommendations,
        "ai_summary": ai_summary,
    }
