"""
The languages the voice agent speaks: Kannada, Hindi and English.

Everything that differs by language lives here: speech-recognition codes, crop
and market names, how numbers are phrased for a text-to-speech voice, the
fixed prompts, and the words that help recognition.

Replies are never machine-translated. Each reply is composed in every language
from the same engine numbers (see `dialogue.Line`), so a Hindi caller and a
Kannada caller hear the same figures, each in natural phrasing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional

from app.voice.kannada import COMMODITY_KN, KN_TO_MARKET, MARKET_KN

SUPPORTED = ("kn", "hi", "en")
DEFAULT_LANGUAGE = "kn"


@dataclass(frozen=True)
class Profile:
    code: str
    native_name: str
    english_name: str
    stt_code: str          # Twilio <Gather language>
    dtmf: str              # key on the language menu


PROFILES: Dict[str, Profile] = {
    "kn": Profile("kn", "ಕನ್ನಡ", "Kannada", "kn-IN", "1"),
    "hi": Profile("hi", "हिंदी", "Hindi", "hi-IN", "2"),
    "en": Profile("en", "English", "English", "en-IN", "3"),
}

# Every way a caller or a form might name a language.
_LANGUAGE_NAMES: Dict[str, str] = {
    "kn": "kn", "kannada": "kn", "kanada": "kn", "ಕನ್ನಡ": "kn", "कन्नड़": "kn", "कन्नड": "kn",
    "hi": "hi", "hindi": "hi", "ಹಿಂದಿ": "hi", "हिंदी": "hi", "हिन्दी": "hi",
    "en": "en", "english": "en", "angrezi": "en", "ಇಂಗ್ಲಿಷ್": "en", "ಇಂಗ್ಲೀಷ್": "en",
    "इंग्लिश": "en", "अंग्रेज़ी": "en", "अंग्रेजी": "en",
}


def normalise_language(value: Optional[str], default: Optional[str] = DEFAULT_LANGUAGE) -> Optional[str]:
    """`kn`, `hi` or `en` for a code or a language name in any script; else `default`."""
    if not value:
        return default
    return _LANGUAGE_NAMES.get(str(value).strip().lower(), default)


def language_from_choice(digits: Optional[str], speech: Optional[str]) -> Optional[str]:
    """Interpret an answer to the language menu: a key press or a spoken language name."""
    key = (digits or "").strip()[:1]
    for profile in PROFILES.values():
        if key and key == profile.dtmf:
            return profile.code
    said = (speech or "").strip().lower()
    if not said:
        return None
    for name, code in sorted(_LANGUAGE_NAMES.items(), key=lambda kv: -len(kv[0])):
        if len(name) > 2 and name in said:
            return code
    for word, code in (("one", "kn"), ("two", "hi"), ("three", "en"), ("ek", "kn"), ("do", "hi"), ("teen", "en")):
        if re.search(rf"\b{word}\b", said):
            return code
    return None


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

COMMODITY_HI: Dict[str, str] = {
    "Onion": "प्याज", "Tomato": "टमाटर", "Potato": "आलू", "Ragi": "रागी",
    "Maize": "मक्का", "Wheat": "गेहूं", "Paddy": "धान", "Jowar": "ज्वार",
    "Bajra": "बाजरा", "Cotton": "कपास", "Sugarcane": "गन्ना", "Chilli": "मिर्च",
    "Turmeric": "हल्दी", "Groundnut": "मूंगफली", "Soyabean": "सोयाबीन", "Tur": "तुअर",
    "Bengal Gram": "चना", "Green Gram": "मूंग", "Black Gram": "उड़द", "Banana": "केला",
    "Grapes": "अंगूर", "Pomegranate": "अनार", "Ginger": "अदरक", "Garlic": "लहसुन",
}

MARKET_HI: Dict[str, str] = {
    "Kalaburagi": "कलबुर्गी", "Sedam": "सेडम", "Chittapur": "चित्तापुर", "Aland": "आलंद",
    "Afzalpur": "अफ़ज़लपुर", "Jevargi": "जेवरगी", "Yadgir": "यादगीर", "Shahapur": "शाहपुर",
    "Surapura": "सुरपुर", "Bidar": "बीदर", "Basavakalyan": "बसवकल्याण", "Humnabad": "हुमनाबाद",
    "Bhalki": "भालकी", "Raichur": "रायचूर", "Sindhanur": "सिंधनूर", "Manvi": "मानवी",
    "Koppal": "कोप्पल", "Gangavathi": "गंगावती", "Ballari": "बल्लारी", "Vijayapura": "विजयपुरा",
    "Bagalkot": "बागलकोट", "Bengaluru": "बेंगलुरु", "Mysuru": "मैसूरु", "Hubballi": "हुब्बली",
    "Belagavi": "बेलगावी", "Davangere": "दावणगेरे", "Shivamogga": "शिवमोग्गा", "Tumakuru": "तुमकुरु",
    "Hassan": "हासन", "Mandya": "मंड्या", "Chitradurga": "चित्रदुर्ग", "Gadag": "गदग",
    "Haveri": "हावेरी", "Lasalgaon": "लासलगांव", "Pimpalgaon Baswant": "पिंपलगांव बसवंत",
    "Nashik": "नासिक", "Yeola": "येवला", "Pune": "पुणे", "Solapur": "सोलापुर",
    "Ahmednagar": "अहमदनगर", "Chhatrapati Sambhajinagar": "छत्रपति संभाजीनगर", "Latur": "लातूर",
    "Nagpur": "नागपुर", "Amravati": "अमरावती", "Akola": "अकोला", "Jalgaon": "जलगांव",
    "Kolhapur": "कोल्हापुर", "Sangli": "सांगली", "Satara": "सातारा", "Beed": "बीड",
    "Nanded": "नांदेड़", "Dharashiv": "धाराशिव", "Parbhani": "परभणी", "Jalna": "जालना",
    "Yavatmal": "यवतमाल", "Dhule": "धुले", "Mumbai": "मुंबई",
}

_SPOKEN_MARKET_VARIANTS_HI: Dict[str, str] = {
    "गुलबर्गा": "Kalaburagi", "कलबुरगी": "Kalaburagi", "कलबुर्गि": "Kalaburagi",
    "सोलापूर": "Solapur", "नाशिक": "Nashik", "बैंगलोर": "Bengaluru", "बेंगलोर": "Bengaluru",
    "हुबली": "Hubballi", "बेलगाम": "Belagavi", "बीजापुर": "Vijayapura", "नांदेड": "Nanded",
}

# Native-script spellings of every market, for finding a place in a transcript.
NATIVE_TO_MARKET: Dict[str, str] = dict(KN_TO_MARKET)
NATIVE_TO_MARKET.update({hi: en for en, hi in MARKET_HI.items()})
NATIVE_TO_MARKET.update(_SPOKEN_MARKET_VARIANTS_HI)

_CROP_NAMES = {"kn": COMMODITY_KN, "hi": COMMODITY_HI}
_MARKET_NAMES = {"kn": MARKET_KN, "hi": MARKET_HI}

GRADES: Dict[str, Dict[str, str]] = {
    "kn": {"A": "ಉತ್ತಮ ದರ್ಜೆ", "B": "ಮಧ್ಯಮ ದರ್ಜೆ", "C": "ಸಾಧಾರಣ ದರ್ಜೆ"},
    "hi": {"A": "अच्छी क्वालिटी", "B": "मध्यम क्वालिटी", "C": "साधारण क्वालिटी"},
    "en": {"A": "grade A", "B": "grade B", "C": "grade C"},
}

COMPLAINTS: Dict[str, Dict[str, str]] = {
    "kn": {
        "PAYMENT": "ಹಣ ಪಾವತಿ ಆಗಿಲ್ಲ", "WEIGHT": "ತೂಕದಲ್ಲಿ ಮೋಸ",
        "QUALITY": "ಗುಣಮಟ್ಟದ ಹೆಸರಿನಲ್ಲಿ ತಿರಸ್ಕಾರ", "QUANTITY": "ಪ್ರಮಾಣದಲ್ಲಿ ವ್ಯತ್ಯಾಸ",
        "LOGISTICS": "ಸಾಗಣೆ ತೊಂದರೆ", "DAMAGE": "ಸರಕು ಹಾಳಾಗಿದೆ", "OTHER": "ಇತರ ತೊಂದರೆ",
    },
    "hi": {
        "PAYMENT": "भुगतान नहीं मिला", "WEIGHT": "तौल में धोखा",
        "QUALITY": "क्वालिटी के नाम पर माल लौटाया", "QUANTITY": "मात्रा में फ़र्क",
        "LOGISTICS": "ढुलाई की दिक्कत", "DAMAGE": "माल ख़राब हुआ", "OTHER": "दूसरी दिक्कत",
    },
    "en": {
        "PAYMENT": "payment not received", "WEIGHT": "weighing fraud",
        "QUALITY": "rejected on quality", "QUANTITY": "quantity mismatch",
        "LOGISTICS": "transport problem", "DAMAGE": "produce damaged", "OTHER": "other problem",
    },
}

CONFIDENCE: Dict[str, Dict[str, str]] = {
    "kn": {"HIGH": "ಹೆಚ್ಚು", "MEDIUM": "ಮಧ್ಯಮ", "LOW": "ಕಡಿಮೆ"},
    "hi": {"HIGH": "ज़्यादा", "MEDIUM": "मध्यम", "LOW": "कम"},
    "en": {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"},
}


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------
# Neural voices read a plain integer well in every language. They read "2,760",
# "2480-3040" and "16.0%" badly, so spoken text never contains those forms.

def approx(value: float) -> int:
    """Round a total the way a person says it: 10,588 is 'about 10,600'."""
    value = abs(float(value))
    if value >= 100_000:
        return int(round(value / 1000.0) * 1000)
    if value >= 1_000:
        return int(round(value / 100.0) * 100)
    return int(round(value / 10.0) * 10)


def _whole(value: float) -> int:
    return int(round(float(value)))


def _trim(q: float) -> str:
    return str(int(q)) if abs(q - round(q)) < 0.05 else f"{q:.1f}"


_UNITS = {
    "kn": {"rupees": "ರೂಪಾಯಿ", "about": "ಸುಮಾರು", "quintal": "ಕ್ವಿಂಟಲ್", "kg": "ಕೆಜಿ",
           "km": "ಕಿಲೋಮೀಟರ್", "percent": "ಶೇಕಡಾ", "days": "ದಿನ"},
    "hi": {"rupees": "रुपये", "about": "लगभग", "quintal": "क्विंटल", "kg": "किलो",
           "km": "किलोमीटर", "percent": "प्रतिशत", "days": "दिन"},
}


class Speaker:
    """Names and numbers phrased for one language."""

    def __init__(self, code: str):
        self.code = code

    def crop(self, name: Optional[str]) -> str:
        return _CROP_NAMES.get(self.code, {}).get(name or "", name or "")

    def market(self, name: Optional[str]) -> str:
        return _MARKET_NAMES.get(self.code, {}).get(name or "", name or "")

    def rupees(self, value: float) -> str:
        if self.code == "en":
            return f"₹{_whole(value):,}"
        return f"{_whole(value)} {_UNITS[self.code]['rupees']}"

    def about(self, value: float) -> str:
        if self.code == "en":
            return f"about ₹{approx(value):,}"
        units = _UNITS[self.code]
        return f"{units['about']} {approx(value)} {units['rupees']}"

    def qty(self, kg: Optional[float]) -> str:
        if kg is None:
            return ""
        kg = float(kg)
        if self.code == "en":
            return f"{_trim(kg / 100.0)} quintal" if kg >= 100 else f"{_whole(kg)} kg"
        units = _UNITS[self.code]
        return f"{_trim(kg / 100.0)} {units['quintal']}" if kg >= 100 else f"{_whole(kg)} {units['kg']}"

    def km(self, value: float) -> str:
        return f"{_whole(value)} km" if self.code == "en" else f"{_whole(value)} {_UNITS[self.code]['km']}"

    def pct(self, value: float) -> str:
        n = abs(_whole(value))
        return f"{n} percent" if self.code == "en" else f"{n} {_UNITS[self.code]['percent']}"

    def days(self, value: int) -> str:
        n = int(value)
        if self.code == "en":
            return f"{n} day" if n == 1 else f"{n} days"
        return f"{n} {_UNITS[self.code]['days']}"

    def grade(self, grade: Optional[str]) -> str:
        return GRADES[self.code].get(grade or "", "")

    def complaint(self, category: str) -> str:
        return COMPLAINTS[self.code].get(category, COMPLAINTS[self.code]["OTHER"])

    def confidence(self, level: Optional[str]) -> Optional[str]:
        return CONFIDENCE[self.code].get(level or "")


_SPEAKERS = {code: Speaker(code) for code in SUPPORTED}


def speaker(code: Optional[str]) -> Speaker:
    return _SPEAKERS[normalise_language(code)]


# ---------------------------------------------------------------------------
# Fixed prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPTS: Dict[str, Dict[str, str]] = {
    "kn": {
        "greeting": (
            "ನಮಸ್ಕಾರ! ನಾನು ಕೃಷಿ ಮಾರುಕಟ್ಟೆ ಸಹಾಯಕ. ನಿಮ್ಮ ಬೆಳೆಯ ಇಂದಿನ ಬೆಲೆ, ಯಾವ "
            "ಮಾರುಕಟ್ಟೆಯಲ್ಲಿ ಹೆಚ್ಚು ಲಾಭ, ಈಗ ಮಾರಬೇಕಾ ಕಾಯಬೇಕಾ, ಅಥವಾ ಖರೀದಿಸುವ "
            "ವ್ಯಾಪಾರಿಗಳು, ಯಾವುದರ ಬಗ್ಗೆ ಬೇಕಾದರೂ ಕೇಳಿ."
        ),
        "hold": "ಒಂದು ಕ್ಷಣ, ನೋಡುತ್ತಿದ್ದೇನೆ.",
        "hold_more": "ಇನ್ನೇನು ಬಂತು.",
        "reprompt": "ಕ್ಷಮಿಸಿ, ನನಗೆ ಸರಿಯಾಗಿ ಕೇಳಿಸಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಇನ್ನೊಮ್ಮೆ ಹೇಳಿ.",
        "goodbye": "ಧನ್ಯವಾದಗಳು. ನಿಮ್ಮ ಬೆಳೆಗೆ ಒಳ್ಳೆಯ ಬೆಲೆ ಸಿಗಲಿ. ಮತ್ತೆ ಬೇಕಾದಾಗ ಕರೆ ಮಾಡಿ.",
        "error": "ಕ್ಷಮಿಸಿ, ಈಗ ಮಾಹಿತಿ ಪಡೆಯಲು ತೊಂದರೆ ಆಯಿತು. ದಯವಿಟ್ಟು ಇನ್ನೊಮ್ಮೆ ಕೇಳಿ.",
        "timeout": "ಕ್ಷಮಿಸಿ, ಉತ್ತರ ಸಿದ್ಧವಾಗಲು ತಡವಾಯಿತು. ದಯವಿಟ್ಟು ನಿಮ್ಮ ಪ್ರಶ್ನೆಯನ್ನು ಇನ್ನೊಮ್ಮೆ ಕೇಳಿ.",
        "menu": "ಕನ್ನಡಕ್ಕಾಗಿ ಒಂದು ಒತ್ತಿ, ಅಥವಾ ಕನ್ನಡ ಅಂತ ಹೇಳಿ.",
    },
    "hi": {
        "greeting": (
            "नमस्ते! मैं कृषि मंडी सहायक हूँ। अपनी फसल का आज का भाव, किस मंडी में ज़्यादा "
            "फ़ायदा, अभी बेचें या रुकें, या ख़रीदने वाले व्यापारी, किसी के भी बारे में पूछिए।"
        ),
        "hold": "एक पल, देख रहा हूँ।",
        "hold_more": "बस, आ ही गया।",
        "reprompt": "माफ़ कीजिए, ठीक से सुनाई नहीं दिया। कृपया एक बार फिर बोलिए।",
        "goodbye": "धन्यवाद। आपकी फसल को अच्छा भाव मिले। फिर ज़रूरत हो तो कॉल कीजिए।",
        "error": "माफ़ कीजिए, अभी जानकारी लेने में दिक्कत आई। कृपया दोबारा पूछिए।",
        "timeout": "माफ़ कीजिए, जवाब तैयार होने में देर हो गई। कृपया अपना सवाल फिर से पूछिए।",
        "menu": "हिंदी के लिए दो दबाइए, या हिंदी बोलिए।",
    },
    "en": {
        "greeting": (
            "Hello! I am your farm market assistant. Ask me about today's crop price, which "
            "market pays best, whether to sell now or wait, or buyers for your produce."
        ),
        "hold": "One moment, let me check.",
        "hold_more": "Almost there.",
        "reprompt": "Sorry, I did not catch that. Please say it again.",
        "goodbye": "Thank you. I hope you get a good price. Call again whenever you need.",
        "error": "Sorry, I had trouble getting that information. Please ask again.",
        "timeout": "Sorry, the answer took too long. Please ask your question again.",
        "menu": "For English, press three, or say English.",
    },
}

DEMO_CAVEAT: Dict[str, str] = {
    "kn": (
        "ಒಂದು ಮುಖ್ಯ ವಿಷಯ: ಈಗ ಹೇಳುತ್ತಿರುವ ಬೆಲೆ ಮತ್ತು ವ್ಯಾಪಾರಿಗಳ ಮಾಹಿತಿ "
        "ಪ್ರಾಯೋಗಿಕ ಮಾಹಿತಿ, ನಿಜವಾದ ಮಾರುಕಟ್ಟೆ ದರ ಅಲ್ಲ."
    ),
    "hi": "एक ज़रूरी बात: अभी बताए जा रहे भाव और व्यापारियों की जानकारी डेमो डेटा है, असली मंडी भाव नहीं।",
    "en": (
        "One important note: the prices and buyer details I am giving are "
        "demonstration data, not real market rates."
    ),
}


def greeting_text(language: str = DEFAULT_LANGUAGE, name: Optional[str] = None) -> str:
    language = normalise_language(language)
    base = SYSTEM_PROMPTS[language]["greeting"]
    name = (name or "").strip()
    if not name:
        return base
    opener = {"kn": "ನಮಸ್ಕಾರ!", "hi": "नमस्ते!", "en": "Hello!"}[language]
    return base.replace(opener, f"{opener[:-1]} {name}!", 1)


# Words that help recognition. Crop and market names are the terms it most
# often mishears, and they are the ones every answer depends on.
_HINT_WORDS = {
    "kn": ["ಬೆಲೆ", "ಮಾರುಕಟ್ಟೆ", "ಕ್ವಿಂಟಲ್", "ಚೀಲ", "ವ್ಯಾಪಾರಿ", "ಆಫರ್", "ಹಣ", "ದೂರು",
           "ಹೌದು", "ಬೇಡ", "ಮಾರಾಟ", "ಖರೀದಿ", "ಹಿಂದಿ", "ಇಂಗ್ಲಿಷ್"],
    "hi": ["भाव", "मंडी", "क्विंटल", "बोरी", "व्यापारी", "ऑफ़र", "पैसा", "भुगतान", "शिकायत",
           "हाँ", "नहीं", "बेचना", "ख़रीदार", "कन्नड़", "इंग्लिश"],
    "en": ["price", "market", "mandi", "quintal", "bags", "buyer", "offer", "payment",
           "complaint", "yes", "no", "sell", "FPO", "Kannada", "Hindi"],
}

MENU_HINTS = "Kannada, Hindi, English, one, two, three"


def speech_hints(language: str = DEFAULT_LANGUAGE) -> str:
    language = normalise_language(language)
    if language == "en":
        crops, markets = list(COMMODITY_KN.keys()), list(MARKET_KN.keys())
    else:
        crops = list(_CROP_NAMES[language].values())
        markets = list(_MARKET_NAMES[language].values())
    return ", ".join(crops + markets[:40] + _HINT_WORDS[language])


# ---------------------------------------------------------------------------
# Hygiene
# ---------------------------------------------------------------------------

_SPOKEN_RUPEES = {"kn": "ರೂಪಾಯಿ", "hi": "रुपये", "en": "rupees"}
_SPOKEN_PERCENT = {"kn": "ಶೇಕಡಾ", "hi": "प्रतिशत", "en": "percent"}


def clean_for_speech(text: str, language: str = DEFAULT_LANGUAGE) -> str:
    """Strip anything a TTS voice would read as a symbol rather than a word."""
    language = normalise_language(language)
    spoken = str(text or "")
    spoken = re.sub(r"[*#_`>|~]", " ", spoken)
    rupees = _SPOKEN_RUPEES[language]
    spoken = re.sub(r"₹\s?(\d[\d,]*)", lambda m: f"{m.group(1).replace(',', '')} {rupees}", spoken)
    spoken = spoken.replace("₹", "")
    while re.search(r"(\d),(\d{2,3})", spoken):
        spoken = re.sub(r"(\d),(\d{2,3})", r"\1\2", spoken)
    spoken = spoken.replace("%", f" {_SPOKEN_PERCENT[language]}")
    if language == "en":
        spoken = re.sub(r"\b(\d+)\s?km\b", r"\1 kilometres", spoken)
        spoken = re.sub(r"\bqtl\b", "quintal", spoken)
    spoken = re.sub(r"[()\[\]{}]", " ", spoken)
    spoken = re.sub(r"\s+", " ", spoken)
    return spoken.strip()
