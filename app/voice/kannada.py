"""
Kannada vocabulary for the voice agent: crop and market names as a Kannada
speaker says them, plus the spellings speech recognition actually returns.

Number phrasing, prompts and the other languages live in `app.voice.language`.
"""

from __future__ import annotations

from typing import Dict

COMMODITY_KN: Dict[str, str] = {
    "Onion": "ಈರುಳ್ಳಿ",
    "Tomato": "ಟೊಮೆಟೊ",
    "Potato": "ಆಲೂಗಡ್ಡೆ",
    "Ragi": "ರಾಗಿ",
    "Maize": "ಮೆಕ್ಕೆಜೋಳ",
    "Wheat": "ಗೋಧಿ",
    "Paddy": "ಭತ್ತ",
    "Jowar": "ಬಿಳಿಜೋಳ",
    "Bajra": "ಸಜ್ಜೆ",
    "Cotton": "ಹತ್ತಿ",
    "Sugarcane": "ಕಬ್ಬು",
    "Chilli": "ಮೆಣಸಿನಕಾಯಿ",
    "Turmeric": "ಅರಿಶಿನ",
    "Groundnut": "ಕಡಲೆಕಾಯಿ",
    "Soyabean": "ಸೋಯಾಬೀನ್",
    "Tur": "ತೊಗರಿ",
    "Bengal Gram": "ಕಡಲೆ",
    "Green Gram": "ಹೆಸರುಕಾಳು",
    "Black Gram": "ಉದ್ದಿನಕಾಳು",
    "Banana": "ಬಾಳೆಹಣ್ಣು",
    "Grapes": "ದ್ರಾಕ್ಷಿ",
    "Pomegranate": "ದಾಳಿಂಬೆ",
    "Ginger": "ಶುಂಠಿ",
    "Garlic": "ಬೆಳ್ಳುಳ್ಳಿ",
}

MARKET_KN: Dict[str, str] = {
    # Karnataka
    "Kalaburagi": "ಕಲಬುರಗಿ", "Sedam": "ಸೇಡಂ", "Chittapur": "ಚಿತ್ತಾಪುರ",
    "Aland": "ಆಳಂದ", "Afzalpur": "ಅಫಜಲಪುರ", "Jevargi": "ಜೇವರ್ಗಿ",
    "Yadgir": "ಯಾದಗಿರಿ", "Shahapur": "ಶಹಾಪುರ", "Surapura": "ಸುರಪುರ",
    "Bidar": "ಬೀದರ್", "Basavakalyan": "ಬಸವಕಲ್ಯಾಣ", "Humnabad": "ಹುಮನಾಬಾದ್",
    "Bhalki": "ಭಾಲ್ಕಿ", "Raichur": "ರಾಯಚೂರು", "Sindhanur": "ಸಿಂಧನೂರು",
    "Manvi": "ಮಾನ್ವಿ", "Koppal": "ಕೊಪ್ಪಳ", "Gangavathi": "ಗಂಗಾವತಿ",
    "Ballari": "ಬಳ್ಳಾರಿ", "Vijayapura": "ವಿಜಯಪುರ", "Bagalkot": "ಬಾಗಲಕೋಟೆ",
    "Bengaluru": "ಬೆಂಗಳೂರು", "Mysuru": "ಮೈಸೂರು", "Hubballi": "ಹುಬ್ಬಳ್ಳಿ",
    "Belagavi": "ಬೆಳಗಾವಿ", "Davangere": "ದಾವಣಗೆರೆ", "Shivamogga": "ಶಿವಮೊಗ್ಗ",
    "Tumakuru": "ತುಮಕೂರು", "Hassan": "ಹಾಸನ", "Mandya": "ಮಂಡ್ಯ",
    "Chitradurga": "ಚಿತ್ರದುರ್ಗ", "Gadag": "ಗದಗ", "Haveri": "ಹಾವೇರಿ",
    # Maharashtra
    "Lasalgaon": "ಲಾಸಲಗಾಂವ್", "Pimpalgaon Baswant": "ಪಿಂಪಳಗಾಂವ್ ಬಸವಂತ್",
    "Nashik": "ನಾಸಿಕ್", "Yeola": "ಯೇವಲಾ", "Pune": "ಪುಣೆ", "Solapur": "ಸೊಲ್ಲಾಪುರ",
    "Ahmednagar": "ಅಹಮದ್‌ನಗರ", "Chhatrapati Sambhajinagar": "ಛತ್ರಪತಿ ಸಂಭಾಜಿನಗರ",
    "Latur": "ಲಾತೂರು", "Nagpur": "ನಾಗಪುರ", "Amravati": "ಅಮರಾವತಿ", "Akola": "ಅಕೋಲಾ",
    "Jalgaon": "ಜಳಗಾಂವ್", "Kolhapur": "ಕೊಲ್ಹಾಪುರ", "Sangli": "ಸಾಂಗ್ಲಿ",
    "Satara": "ಸತಾರಾ", "Beed": "ಬೀಡ್", "Nanded": "ನಾಂದೇಡ್", "Dharashiv": "ಧಾರಾಶಿವ",
    "Parbhani": "ಪರಭಣಿ", "Jalna": "ಜಾಲ್ನಾ", "Yavatmal": "ಯವತ್ಮಾಳ", "Dhule": "ಧುಳೆ",
    "Mumbai": "ಮುಂಬೈ",
}

# How callers actually say some of these. Speech recognition returns what it
# heard, so "ಕಲ್ಬುರ್ಗಿ" and "ಗುಲ್ಬರ್ಗಾ" must land on the same market.
_SPOKEN_MARKET_VARIANTS: Dict[str, str] = {
    "ಕಲ್ಬುರ್ಗಿ": "Kalaburagi", "ಕಲಬುರ್ಗಿ": "Kalaburagi", "ಗುಲ್ಬರ್ಗಾ": "Kalaburagi",
    "ಗುಲ್ಬರ್ಗ": "Kalaburagi", "ಸೋಲಾಪುರ": "Solapur", "ಸೊಲಾಪುರ": "Solapur",
    "ಸೋಲಾಪೂರ": "Solapur", "ಬಿದರ್": "Bidar", "ಬಿಜಾಪುರ": "Vijayapura",
    "ಬೆಳಗಾಂ": "Belagavi", "ಹುಬ್ಬಳಿ": "Hubballi", "ಬೆಂಗಳೂರ": "Bengaluru",
    "ನಾಶಿಕ್": "Nashik", "ಲಾಸಲಗಾವ್": "Lasalgaon", "ರಾಯಚೂರ": "Raichur",
    "ಯಾದಗಿರ": "Yadgir",
}

KN_TO_MARKET: Dict[str, str] = {kn: en for en, kn in MARKET_KN.items()}
KN_TO_MARKET.update(_SPOKEN_MARKET_VARIANTS)
