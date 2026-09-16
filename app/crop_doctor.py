"""
Crop photo diagnosis.

Gemini vision reads one leaf or crop photo and returns a structured finding:
what the crop is, what is wrong, how sure it is and what to do. The output is
normalised before anyone sees it, because the decision engine scores risk from
these fields and must never receive a severity or confidence it cannot parse.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)

VISION_MODEL = "gemini-2.5-flash"
CATEGORIES = ("DISEASE", "PEST", "NUTRIENT", "ABIOTIC", "HEALTHY", "UNCLEAR")
SEVERITIES = ("NONE", "LOW", "MEDIUM", "HIGH")
REFERENCE_MIN_SCORE = 0.62

# Recent diagnoses, so the decision engine can use one by id without the photo
# being uploaded twice. In memory only: after a restart the farmer re-uploads.
_MAX_KEPT = 200
_TTL_SECONDS = 6 * 3600
_store: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_store_lock = threading.Lock()


class DiagnosisUnavailable(RuntimeError):
    """The photo could not be analysed; the message is safe to show the farmer."""


_PROMPT = """You are a plant pathologist advising smallholder farmers in Karnataka, India.
Examine the photo. The farmer says the crop is: {crop}. Location: {location}.

Reply with JSON only, using exactly these keys:
{{
  "is_plant": true if the photo shows a crop plant, leaf, fruit, grain or harvested produce, else false,
  "crop": the crop you see, common English name,
  "category": one of DISEASE, PEST, NUTRIENT, ABIOTIC, HEALTHY, UNCLEAR,
  "condition": the specific disease, pest or deficiency, or "Healthy", or "Unclear photo",
  "confidence": number from 0 to 1,
  "severity": one of NONE, LOW, MEDIUM, HIGH,
  "affected_area_pct": estimated percent of visible plant tissue affected, 0 to 100,
  "symptoms": list of what is visible in this photo,
  "treatment": ordered list of practical steps,
  "prevention": list,
  "summary": two plain sentences for the farmer
}}

Rules:
- If the photo is blurry, too far away, or the symptoms fit several problems, use category UNCLEAR with confidence below 0.4, and say in the summary what photo would help (close-up of one affected leaf, both sides, daylight).
- Never invent a problem. A healthy crop is HEALTHY with severity NONE and an empty treatment list.
- Treatment: name active ingredients, not brands, with the usual dose per litre of water where one is standard. Include at least one non-chemical step.
- ABIOTIC covers water stress, heat, cold, herbicide or sun damage.
- Each list item under 25 words, at most 5 items per list."""


def diagnose(image_bytes: bytes, mime_type: str, crop_hint: str = "", location: str = "") -> Dict[str, Any]:
    """Analyse one photo. Raises DiagnosisUnavailable with a farmer-safe message on any failure."""
    settings = get_settings()
    if not settings.GEMINI_API_KEY:
        raise DiagnosisUnavailable("Photo checks are not configured on this server.")

    import google.generativeai as genai

    genai.configure(api_key=settings.GEMINI_API_KEY)
    model = genai.GenerativeModel(VISION_MODEL)
    prompt = _PROMPT.format(crop=crop_hint or "not stated", location=location or "not stated")

    started = time.time()
    try:
        response = model.generate_content(
            [prompt, {"mime_type": mime_type, "data": image_bytes}],
            generation_config={"response_mime_type": "application/json", "temperature": 0.2},
            request_options={"timeout": 60},
        )
        raw = _parse_json(response.text)
    except Exception as exc:
        logger.warning("Crop photo diagnosis failed: %s", exc)
        text = str(exc).lower()
        if "429" in text or "quota" in text or "exhausted" in text:
            raise DiagnosisUnavailable("The photo service is at its usage limit. Try again in a minute.") from exc
        raise DiagnosisUnavailable("The photo could not be analysed. Try a clear close-up in daylight.") from exc

    result = _normalise(raw, crop_hint)
    result.update(
        id=uuid.uuid4().hex[:16],
        crop_hint=crop_hint or None,
        location=location or None,
        model=VISION_MODEL,
        created_at=int(time.time()),
        analysis_ms=int((time.time() - started) * 1000),
    )
    result["references"] = _references(result)
    _remember(result)
    logger.info(
        "Crop diagnosis %s: %s / %s (%s, %.0f%%) in %dms",
        result["id"], result["crop"], result["condition"], result["severity"],
        result["confidence"] * 100, result["analysis_ms"],
    )
    return result


def get_diagnosis(diagnosis_id: str) -> Optional[Dict[str, Any]]:
    now = time.time()
    with _store_lock:
        item = _store.get(diagnosis_id)
        if item is None:
            return None
        if now - item["created_at"] > _TTL_SECONDS:
            _store.pop(diagnosis_id, None)
            return None
        return item


def _remember(result: Dict[str, Any]) -> None:
    with _store_lock:
        _store[result["id"]] = result
        while len(_store) > _MAX_KEPT:
            _store.popitem(last=False)


def _parse_json(text: str) -> Dict[str, Any]:
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text or "")
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("Diagnosis response was not a JSON object")
    return data


def _truthy(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return bool(value)


def _clean_list(value: Any, limit: int = 5) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()][:limit]


def _normalise(raw: Dict[str, Any], crop_hint: str) -> Dict[str, Any]:
    category = str(raw.get("category") or "UNCLEAR").strip().upper()
    if category not in CATEGORIES:
        category = "UNCLEAR"

    severity = str(raw.get("severity") or "NONE").strip().upper()
    if severity not in SEVERITIES:
        # A named problem with an unreadable severity is scored as medium, not ignored.
        severity = "MEDIUM" if category in ("DISEASE", "PEST", "NUTRIENT", "ABIOTIC") else "NONE"

    try:
        confidence = float(raw.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.0
    if 1.0 < confidence <= 100.0:
        confidence /= 100.0
    confidence = min(max(confidence, 0.0), 1.0)

    try:
        area = float(raw.get("affected_area_pct"))
    except (TypeError, ValueError):
        area = None
    if area is not None:
        area = min(max(area, 0.0), 100.0)

    is_plant = _truthy(raw.get("is_plant"), True)
    if not is_plant:
        category = "UNCLEAR"
    if category in ("HEALTHY", "UNCLEAR"):
        severity = "NONE"

    condition = str(raw.get("condition") or "").strip()
    if not condition or category in ("HEALTHY", "UNCLEAR"):
        condition = condition if condition and category not in ("HEALTHY", "UNCLEAR") else (
            "Healthy" if category == "HEALTHY" else "Unclear photo"
        )

    return {
        "is_plant": is_plant,
        "crop": str(raw.get("crop") or crop_hint or "").strip() or None,
        "category": category,
        "condition": condition or "Unidentified problem",
        "healthy": category == "HEALTHY",
        "confidence": round(confidence, 2),
        "severity": severity,
        "affected_area_pct": area,
        "symptoms": _clean_list(raw.get("symptoms")),
        "treatment": [] if category == "HEALTHY" else _clean_list(raw.get("treatment")),
        "prevention": _clean_list(raw.get("prevention")),
        "summary": str(raw.get("summary") or "").strip(),
    }


def _references(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    if result["category"] in ("HEALTHY", "UNCLEAR"):
        return []
    try:
        from app.rag import retrieve

        query = " ".join(filter(None, [result["crop"], result["condition"], *result["symptoms"][:3]]))
        return [d.to_dict() for d in retrieve(query, k=3) if d.score >= REFERENCE_MIN_SCORE]
    except Exception as exc:
        logger.warning("Knowledge references skipped for diagnosis: %s", exc)
        return []
