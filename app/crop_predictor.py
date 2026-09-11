"""Crop prediction and yield estimation using Gemini API + weather context."""

import json
import logging
from typing import Any, Dict

import httpx

from app.agriculture import get_soil_info, get_weather

logger = logging.getLogger(__name__)
GEMINI_TIMEOUT_SECONDS = 20


async def _call_gemini_json(prompt: str, fallback_data: Dict[str, Any]) -> Dict[str, Any]:
    """Helper to call Gemini API and parse JSON response."""
    try:
        from app.config import get_settings

        settings = get_settings()
        if not settings.GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY not set; using fallback prediction data")
            return fallback_data

        full_prompt = (
            f"{prompt}\n\n"
            "Strictly return ONLY a valid JSON object. "
            "Do not include markdown blocks or any other explanation text."
        )

        endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.5-flash:generateContent"
        )
        payload = {
            "contents": [{"parts": [{"text": full_prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.3,
            },
        }

        async with httpx.AsyncClient(timeout=GEMINI_TIMEOUT_SECONDS) as client:
            response = await client.post(
                endpoint,
                params={"key": settings.GEMINI_API_KEY},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        text = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )
        text = str(text).strip()
        if not text:
            logger.warning("Gemini returned empty response text; using fallback prediction data")
            return fallback_data

        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]

        parsed = json.loads(text.strip())
        if isinstance(parsed, dict):
            return parsed

        logger.warning("Gemini response was not a JSON object; using fallback prediction data")
        return fallback_data
    except Exception as e:
        logger.error(f"Gemini API error in prediction: {e}")
        error_msg = str(e).lower()
        timeout_types = tuple(
            err for err in (
                getattr(httpx, "TimeoutException", None),
                getattr(httpx, "TimeoutError", None),
            )
            if err is not None
        )
        if timeout_types and isinstance(e, timeout_types):
            logger.warning("Gemini request timed out; using fallback prediction data")
            return fallback_data
        if "429" in error_msg or "quota" in error_msg or "resourceexhausted" in error_msg:
            logger.warning("Gemini rate limited; using fallback prediction data")
            return fallback_data
        if "api_key" in error_msg or "unauthorized" in error_msg:
            logger.warning("Gemini API key invalid/unauthorized; using fallback prediction data")
            return fallback_data
        return fallback_data


async def predict_crops(
    soil_type: str = "",
    location: str = "Kalaburgi",
    season: str = "",
) -> Dict[str, Any]:
    """Predict best crops using Gemini based on soil, weather, and location."""
    weather = await get_weather(location)
    soil_info = get_soil_info(location)
    effective_soil = soil_type or soil_info.get("soil_type", "Red laterite")
    temp = weather.get("temperature", 28)
    humidity = weather.get("humidity", 65)
    rainfall = weather.get("rainfall", 0)
    effective_season = season or "Kharif"

    prompt = f"""
You are an expert agricultural AI. Recommend the top 4 crops for a farmer in India with the following conditions:
Location: {location}
Soil Type: {effective_soil}
Current Weather: {temp}°C, {humidity}% humidity, {rainfall}mm recent rainfall
Season: {effective_season}

Return a JSON object matching this exact schema:
{{
  "predictions": [
    {{
      "crop": "Crop Name",
      "confidence": 95, 
      "season": "Suggested Season",
      "water_need": "Low/Medium/High",
      "base_yield_per_acre": "Expected yield in quintals (number)",
      "notes": "Short sentence explaining why it's a good fit."
    }}
  ]
}}
"""
    fallback = {
        "predictions": [
            {"crop": "Ragi", "confidence": 92, "season": "Kharif", "water_need": "Low", "base_yield_per_acre": 10, "notes": f"Great for {effective_soil} soil in {location}."},
            {"crop": "Maize", "confidence": 88, "season": "Kharif", "water_need": "Medium", "base_yield_per_acre": 22, "notes": "Requires moderate watering but yields well here."},
            {"crop": "Tomato", "confidence": 85, "season": "Kharif", "water_need": "Medium", "base_yield_per_acre": 120, "notes": "High yield potential if temperature stays below 35°C."},
            {"crop": "Jowar", "confidence": 80, "season": "Rabi", "water_need": "Low", "base_yield_per_acre": 8, "notes": "Drought resistant alternative."}
        ]
    }

    ai_data = await _call_gemini_json(prompt, fallback)
    predictions = ai_data.get("predictions", fallback["predictions"])

    return {
        "location": location,
        "soil_type": effective_soil,
        "temperature": temp,
        "humidity": humidity,
        "rainfall": rainfall,
        "season": effective_season,
        "predictions": predictions,
    }


async def predict_yield(
    crop: str,
    area_acres: float = 1.0,
    soil_type: str = "",
    rainfall_mm: float = 0,
    season: str = "kharif",
) -> Dict[str, Any]:
    """Predict expected yield using Gemini given area and conditions."""
    effective_soil = soil_type or "Loamy"
    
    prompt = f"""
You are an expert agricultural AI. Estimate the crop yield for a farmer in India.
Crop: {crop}
Farm Area: {area_acres} acres
Soil Type: {effective_soil}
Recent Rainfall: {rainfall_mm} mm
Season: {season}

Return a JSON object matching this exact schema:
{{
  "base_yield_per_acre": "Base expected yield in quintals under normal conditions (number)",
  "multiplier": "A float between 0.5 and 1.5 representing the impact of the specific soil/weather (number)",
  "estimated_yield_quintals": "Final calculated yield (base * area * multiplier) (number)",
  "confidence": "Low/Medium/High",
  "notes": "Short sentence explaining the estimate."
}}
"""
    fallback = {
        "base_yield_per_acre": 15,
        "multiplier": 1.0,
        "estimated_yield_quintals": round(15 * area_acres, 1),
        "confidence": "Medium",
        "notes": f"Standard estimate for {crop} on {area_acres} acres."
    }

    ai_data = await _call_gemini_json(prompt, fallback)
    
    # Ensure all required fields are present
    for key in fallback:
        if key not in ai_data:
            ai_data[key] = fallback[key]

    return {
        "crop": crop.title(),
        "area_acres": area_acres,
        "soil_type": effective_soil,
        "season": season.title(),
        "base_yield_per_acre": ai_data["base_yield_per_acre"],
        "multiplier": ai_data["multiplier"],
        "estimated_yield_quintals": ai_data["estimated_yield_quintals"],
        "confidence": ai_data["confidence"],
        "notes": ai_data["notes"],
    }
