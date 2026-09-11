"""
Agriculture data integrations:
- Weather API (OpenWeatherMap)
- Soil dataset (static fallback)
- Crop recommendation dataset (static fallback)
"""

import json
import os
import logging
from typing import Dict, List, Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# Paths to static data files
STATIC_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static_data")


# ============================================
# Weather API (OpenWeatherMap)
# ============================================

# City name mapping for OpenWeatherMap compatibility
_CITY_NAME_MAP = {
    "kalaburgi": "Kalaburagi",
    "kalaburagi": "Kalaburagi",
    "gulbarga": "Kalaburagi",
    "bengaluru": "Bangalore",
    "mysuru": "Mysore",
    "belagavi": "Belgaum",
    "hubballi": "Hubli",
    "dharwad": "Dharwad",
    "mangaluru": "Mangalore",
    "shivamogga": "Shimoga",
    "vijayapura": "Bijapur",
    "ballari": "Bellary",
    "raichur": "Raichur",
}

async def get_weather(location: str = "Bangalore") -> Dict:
    """
    Get current weather data from OpenWeatherMap.
    Falls back to static data if API fails.
    """
    settings = get_settings()
    # Normalize city name for API compatibility
    api_location = _CITY_NAME_MAP.get(location.lower().strip(), location)

    if not settings.OPENWEATHER_API_KEY:
        logger.warning("OPENWEATHER_API_KEY not set — using fallback weather data")
        return _get_fallback_weather(location)

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                "https://api.openweathermap.org/data/2.5/weather",
                params={
                    "q": f"{api_location},IN",
                    "appid": settings.OPENWEATHER_API_KEY,
                    "units": "metric",
                },
            )
            response.raise_for_status()
            data = response.json()

            weather = {
                "temperature": data["main"]["temp"],
                "humidity": data["main"]["humidity"],
                "description": data["weather"][0]["description"],
                "rainfall": data.get("rain", {}).get("1h", 0),
                "wind_speed": data["wind"]["speed"],
                "location": location,
            }
            logger.info(f"Weather for {location}: {weather['temperature']}°C, {weather['description']}")
            return weather

    except Exception as e:
        logger.warning(f"Weather API failed for {location}: {e} — using fallback")
        return _get_fallback_weather(location)


def _get_fallback_weather(location: str) -> Dict:
    """Static fallback weather data for Karnataka regions."""
    return {
        "temperature": 28,
        "humidity": 65,
        "description": "partly cloudy",
        "rainfall": 0,
        "wind_speed": 10,
        "location": location,
        "source": "fallback",
    }


def format_weather_string(weather: Dict) -> str:
    """Format weather data into a readable string for Gemini."""
    return (
        f"Temperature: {weather.get('temperature', 'N/A')}°C, "
        f"Humidity: {weather.get('humidity', 'N/A')}%, "
        f"Conditions: {weather.get('description', 'N/A')}, "
        f"Rainfall: {weather.get('rainfall', 0)}mm"
    )


# ============================================
# Soil Dataset (Static)
# ============================================

_soil_data = None


def _load_soil_data() -> Dict:
    """Load soil data from static JSON file."""
    global _soil_data
    if _soil_data is not None:
        return _soil_data

    soil_file = os.path.join(STATIC_DATA_DIR, "soil_data.json")
    try:
        with open(soil_file, "r", encoding="utf-8") as f:
            _soil_data = json.load(f)
            logger.info(f"Loaded soil data: {len(_soil_data.get('regions', []))} regions")
            return _soil_data
    except FileNotFoundError:
        logger.warning(f"Soil data file not found: {soil_file}")
        _soil_data = {"regions": []}
        return _soil_data
    except Exception as e:
        logger.error(f"Failed to load soil data: {e}")
        _soil_data = {"regions": []}
        return _soil_data


def get_soil_info(location: str = "Karnataka") -> Dict:
    """
    Get soil information for a given location.
    Uses static dataset with fuzzy matching on district/region names.
    """
    data = _load_soil_data()
    location_lower = location.lower()

    # Search for matching region
    for region in data.get("regions", []):
        if location_lower in region.get("name", "").lower() or \
           region.get("name", "").lower() in location_lower:
            logger.info(f"Found soil data for {location}: {region.get('soil_type')}")
            return region

    # Default Karnataka soil info
    return {
        "name": location,
        "soil_type": "Red laterite soil",
        "ph_range": "5.5-7.0",
        "characteristics": "Well-drained, moderately fertile",
        "suitable_crops": ["Ragi", "Jowar", "Groundnut", "Sunflower"],
    }


# ============================================
# Crop Recommendations (Static)
# ============================================

_crop_data = None


def _load_crop_data() -> Dict:
    """Load crop data from static JSON file."""
    global _crop_data
    if _crop_data is not None:
        return _crop_data

    crop_file = os.path.join(STATIC_DATA_DIR, "crop_data.json")
    try:
        with open(crop_file, "r", encoding="utf-8") as f:
            _crop_data = json.load(f)
            logger.info(f"Loaded crop data: {len(_crop_data.get('crops', []))} crops")
            return _crop_data
    except FileNotFoundError:
        logger.warning(f"Crop data file not found: {crop_file}")
        _crop_data = {"crops": []}
        return _crop_data
    except Exception as e:
        logger.error(f"Failed to load crop data: {e}")
        _crop_data = {"crops": []}
        return _crop_data


def get_crop_recommendations(soil_type: str = "", season: str = "kharif") -> List[Dict]:
    """
    Get crop recommendations based on soil type and season.
    """
    data = _load_crop_data()
    soil_lower = soil_type.lower()
    season_lower = season.lower()

    recommendations = []
    for crop in data.get("crops", []):
        soil_match = not soil_lower or soil_lower in crop.get("suitable_soil", "").lower()
        season_match = not season_lower or season_lower in crop.get("season", "").lower()
        if soil_match or season_match:
            recommendations.append(crop)

    if not recommendations:
        # Default recommendations
        recommendations = [
            {"name": "Ragi", "season": "Kharif", "suitable_soil": "Red soil"},
            {"name": "Rice", "season": "Kharif", "suitable_soil": "Clay soil"},
            {"name": "Jowar", "season": "Rabi", "suitable_soil": "Black soil"},
        ]

    logger.info(f"Crop recommendations for soil='{soil_type}', season='{season}': {len(recommendations)} crops")
    return recommendations
