"""Decision engine rules, risk scoring, and photo diagnosis normalisation."""

from __future__ import annotations

import asyncio

import pytest

from app import crop_doctor
from app import decision_engine as de

DRY_WEATHER = {
    "temperature": 29, "humidity": 60, "description": "clear sky",
    "rainfall": 0, "wind_speed": 3, "location": "Kalaburagi",
}


def _market(decision: str = "WAIT", **overrides):
    payload = {
        "available": True, "decision": decision, "current_price": 2760.0, "market": "Kalaburagi",
        "data_quality": "LIVE", "confidence": "HIGH", "recommended_days": 10,
        "reasoning": ["price is below its 30-day average"], "risks": [],
        "spoken": "My recommendation is to wait about 10 days before selling your Onion.",
    }
    payload.update(overrides)
    return payload


def _diagnosis(**overrides):
    payload = {
        "id": "dx1", "is_plant": True, "crop": "Onion", "category": "DISEASE",
        "condition": "Purple blotch", "confidence": 0.85, "severity": "HIGH",
        "treatment": ["Spray mancozeb 2.5 g per litre of water"], "prevention": [],
        "summary": "Purple blotch is spreading on the leaves.",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def inputs(monkeypatch):
    state = {"market": _market(), "weather": dict(DRY_WEATHER)}

    async def fake_weather(location):
        return state["weather"]

    monkeypatch.setattr(de, "sale_window", lambda *args, **kwargs: state["market"])
    monkeypatch.setattr(de, "get_weather", fake_weather)
    monkeypatch.setattr(de, "rag_retrieve", lambda *args, **kwargs: [])
    return state


def _decide(**kwargs):
    return asyncio.run(de.decide("Onion", "Kalaburagi", **kwargs))


def test_severe_disease_overrides_waiting(inputs):
    result = _decide(diagnosis=_diagnosis(), storage_days=15)

    assert result["market_decision"] == "WAIT"
    assert result["decision"] == "SELL_PARTIAL"
    assert "SEVERE_PROBLEM_OVERRIDES_WAIT" in result["rules_fired"]
    assert result["actions"][0]["kind"] == "TREAT"
    assert result["treat_first"] is True


def test_low_confidence_diagnosis_does_not_override_market(inputs):
    result = _decide(diagnosis=_diagnosis(confidence=0.3), storage_days=15)

    assert result["decision"] == "WAIT"
    assert "SEVERE_PROBLEM_OVERRIDES_WAIT" not in result["rules_fired"]


def test_rain_blocks_spraying(inputs):
    inputs["weather"]["rainfall"] = 2.5
    result = _decide(diagnosis=_diagnosis(severity="MEDIUM"))

    spray = next(a for a in result["actions"] if a["kind"] == "SPRAY_TIMING")
    assert spray["title"] == "Do not spray today"
    assert spray["priority"] == 1
    assert "WEATHER_BLOCKS_SPRAYING" in result["rules_fired"]


def test_no_photo_scores_no_crop_risk_and_asks_for_one(inputs):
    result = _decide(storage_days=15)

    health = next(f for f in result["risk"]["factors"] if f["name"] == "Crop health")
    assert health["points"] == 0
    assert result["decision"] == "WAIT"
    assert any(a["kind"] == "PHOTO" for a in result["actions"])


def test_risk_score_is_sum_of_attributed_factors(inputs):
    inputs["market"] = _market(data_quality="MOCK")
    inputs["weather"].update(humidity=90, rainfall=1)
    result = _decide(diagnosis=_diagnosis(), needs_cash=True)

    assert result["risk"]["score"] == sum(f["points"] for f in result["risk"]["factors"])
    assert result["risk"]["band"] in ("HIGH", "SEVERE")
    assert any(a["kind"] == "INSURANCE" for a in result["actions"])


def test_missing_market_data_still_gives_crop_advice(inputs):
    inputs["market"] = {"available": False, "reason": "No price history for Onion here."}
    result = _decide(diagnosis=_diagnosis(severity="LOW"))

    assert result["decision"] == "INSUFFICIENT_DATA"
    kinds = [a["kind"] for a in result["actions"]]
    assert "MARKET" in kinds and "TREAT" in kinds


def test_normalise_rejects_values_it_cannot_score():
    out = crop_doctor._normalise(
        {"category": "fungus", "severity": "very bad", "confidence": "85", "is_plant": "false"}, "Onion",
    )
    assert out["category"] == "UNCLEAR"
    assert out["severity"] == "NONE"
    assert out["is_plant"] is False
    assert out["confidence"] == 0.85


def test_normalise_healthy_crop_gets_no_treatment():
    out = crop_doctor._normalise({"category": "HEALTHY", "severity": "HIGH", "confidence": 0.9, "treatment": ["spray"]}, "")
    assert out["severity"] == "NONE"
    assert out["treatment"] == []
    assert out["condition"] == "Healthy"


def test_parse_json_strips_code_fence():
    assert crop_doctor._parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_diagnose_route_rejects_non_image_bytes():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routes.advisor_routes import router

    api = FastAPI()
    api.include_router(router)
    res = TestClient(api).post(
        "/api/advisor/diagnose", files={"file": ("leaf.jpg", b"not really a jpeg", "image/jpeg")},
    )
    assert res.status_code == 415
