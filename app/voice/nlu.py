"""
Understanding what the farmer said, in Kannada, Hindi or English.

One Gemini call reads the transcript directly and returns the intent, the slots
(crop, market, quantity, storage days, urgency, grade, complaint type, a
requested language) and an English gloss for the transcript view.

This replaces two steps that each lost information: googletrans turned
"ಕಲ್ಬುರ್ಗಿಯಲ್ಲಿ ಈರುಳ್ಳಿ ಬೆಲೆ ಎಷ್ಟು" into "How much does Onion cost in Kalburgi?",
and substring matching on that English then decided the intent - so "should I
sell now or wait?" contained "sell" and was routed to an auction listing.

When Gemini is unavailable (quota, network) a keyword path takes over. It is
cruder, but it never stops the call from being answered.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import get_settings
from app.market.normalization import COMMODITY_MASTER, parse_quantity, resolve_commodity
from app.voice.kannada import MARKET_KN
from app.voice.language import (
    DEFAULT_LANGUAGE,
    NATIVE_TO_MARKET,
    PROFILES,
    SUPPORTED,
    language_from_choice,
    normalise_language,
)

logger = logging.getLogger(__name__)

INTENTS = (
    "PRICE", "COMPARE_MARKETS", "PRICE_TREND", "SELL_OR_WAIT", "FIND_BUYERS",
    "LIST_FOR_SALE", "MY_OFFERS", "PAYMENT_STATUS", "RAISE_COMPLAINT",
    "FPO_POOLING", "CHANGE_LANGUAGE", "PROVIDE_DETAILS", "YES", "NO", "REPEAT",
    "HELP", "GREETING", "GOODBYE", "OTHER",
)

COMPLAINT_CATEGORIES = ("PAYMENT", "QUALITY", "WEIGHT", "QUANTITY", "LOGISTICS", "DAMAGE", "OTHER")

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Set when Gemini answers 429; the keyword path serves until it expires.
_cooldown_until = 0.0


@dataclass
class Understanding:
    intent: str
    english: str = ""
    text: str = ""                           # the transcript, in whatever language was spoken
    commodity: Optional[str] = None          # canonical, when we hold data for it
    commodity_raw: Optional[str] = None      # what was said, even if unsupported
    location: Optional[str] = None
    quantity_kg: Optional[float] = None
    storage_days: Optional[int] = None
    needs_cash_urgently: Optional[bool] = None
    grade: Optional[str] = None
    complaint_category: Optional[str] = None
    language_choice: Optional[str] = None    # set for CHANGE_LANGUAGE
    source: str = "gemini"
    latency_ms: int = 0
    notes: List[str] = field(default_factory=list)

    def slots(self) -> Dict[str, Any]:
        data = asdict(self)
        for key in ("intent", "english", "text", "source", "latency_ms", "notes"):
            data.pop(key, None)
        return {k: v for k, v in data.items() if v is not None}


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

_NLU_INSTRUCTION = """You are the language-understanding step of a phone assistant that helps Indian farmers
sell their produce: market prices, the best market after transport costs, sell-or-wait advice,
verified buyers, listing produce for sale, offers, payments, complaints and farmer producer
organisations (FPOs). Farmers speak Kannada, Hindi or English, often mixed.

You receive a speech-to-text transcript. It may contain recognition errors, colloquial speech,
or English crop and place names. Return JSON only.

Intents:
- PRICE: today's price or rate of a crop at a market.
  e.g. "ಕಲಬುರಗಿಯಲ್ಲಿ ಈರುಳ್ಳಿ ಬೆಲೆ ಎಷ್ಟು", "प्याज का भाव क्या है", "what is the onion rate"
- COMPARE_MARKETS: which or where a market pays more, the best mandi, whether another market is better.
  e.g. "ಯಾವ ಮಾರುಕಟ್ಟೆಯಲ್ಲಿ ಹೆಚ್ಚು ಬೆಲೆ ಸಿಗುತ್ತೆ", "किस मंडी में ज़्यादा भाव मिलेगा", "which market pays best"
- PRICE_TREND: whether prices are rising or falling, last week or month, the average.
  e.g. "ಬೆಲೆ ಏರುತ್ತಿದೆಯಾ", "भाव बढ़ रहा है क्या", "are prices going up"
- SELL_OR_WAIT: should I sell now or wait, store or hold.
  e.g. "ಈಗ ಮಾರಬೇಕಾ ಕಾಯಬೇಕಾ", "अभी बेचूँ या रुकूँ", "should I sell now or wait"
- FIND_BUYERS: who will buy, traders or companies that want the crop.
  e.g. "ಯಾರು ಖರೀದಿ ಮಾಡ್ತಾರೆ", "मेरा प्याज कौन ख़रीदेगा", "who will buy my tur"
- LIST_FOR_SALE: the farmer wants to put produce up for sale.
  e.g. "ಇಪ್ಪತ್ತು ಕ್ವಿಂಟಲ್ ಈರುಳ್ಳಿ ಮಾರಾಟಕ್ಕೆ ಹಾಕಬೇಕು", "बीस क्विंटल प्याज बेचना है", "I want to list my onions"
- MY_OFFERS: offers or bids the farmer has received. e.g. "ನನಗೆ ಆಫರ್ ಬಂದಿದೆಯಾ", "कोई ऑफ़र आया क्या"
- PAYMENT_STATUS: whether their money or payment has come. e.g. "ನನ್ನ ಹಣ ಬಂತಾ", "मेरा पैसा आया क्या"
- RAISE_COMPLAINT: a complaint: not paid, cheated on weight, rejected on quality, damage.
  e.g. "ವ್ಯಾಪಾರಿ ಹಣ ಕೊಟ್ಟಿಲ್ಲ, ದೂರು ಕೊಡಬೇಕು", "तौल में धोखा हुआ, शिकायत करनी है"
- FPO_POOLING: farmer producer organisation, selling together, pooling with other farmers.
- CHANGE_LANGUAGE: the farmer asks the assistant to speak another language, or says only a
  language name. e.g. "ಹಿಂದಿಯಲ್ಲಿ ಮಾತಾಡಿ", "इंग्लिश में बोलो", "Kannada please", "हिंदी".
- PROVIDE_DETAILS: only answering the assistant's question with details (quantity, storage days,
  urgency, quality, place) and making no new request.
- YES: agreement ("ಹೌದು", "ಸರಿ", "ಆಗಲಿ", "हाँ", "ठीक है", "कर दो", "yes", "okay").
- NO: refusal ("ಬೇಡ", "ಇಲ್ಲ", "नहीं", "मत करो", "no").
- REPEAT: asks to repeat. HELP: asks what the assistant can do. GREETING: only a hello.
- GOODBYE: thanks, bye, or ending the call.
- OTHER: anything else.

Fields:
- commodity: English crop name (Onion, Tomato, Potato, Tur, Cotton, Maize, Jowar, Paddy, Chilli,
  Groundnut, Bengal Gram, Green Gram ...) or null. ಈರುಳ್ಳಿ/प्याज=Onion, ತೊಗರಿ/तुअर/अरहर=Tur,
  ಹತ್ತಿ/कपास=Cotton, ಮೆಕ್ಕೆಜೋಳ/मक्का=Maize, ಬಿಳಿಜೋಳ/ज्वार=Jowar, ಕಡಲೆ/चना=Bengal Gram.
- location: town, district or market in standard English spelling (Kalaburagi, Bidar, Solapur) or null.
- quantity: digits and unit: "30 quintal", "40 bag", "500 kg", "2 tonne". Convert number words in
  any language to digits. null if no quantity was said.
- storage_days: whole days the farmer can store the crop. 0 if they say they cannot store it.
  null if not mentioned.
- needs_cash_urgently: true or false only when mentioned, otherwise null.
- grade: "A" for good or best quality, "B" for medium, "C" for low. null if not mentioned.
- complaint_category: PAYMENT, QUALITY, WEIGHT, QUANTITY, LOGISTICS, DAMAGE or OTHER, only for
  RAISE_COMPLAINT, otherwise null.
- language: "kn", "hi" or "en", only for CHANGE_LANGUAGE, otherwise null.
- english: a faithful English translation of what the farmer said.

If the assistant has just asked a question, a bare answer to it is PROVIDE_DETAILS, YES or NO."""

_NLU_SCHEMA: Dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "intent": {"type": "STRING", "enum": list(INTENTS)},
        "commodity": {"type": "STRING", "nullable": True},
        "location": {"type": "STRING", "nullable": True},
        "quantity": {"type": "STRING", "nullable": True},
        "storage_days": {"type": "INTEGER", "nullable": True},
        "needs_cash_urgently": {"type": "BOOLEAN", "nullable": True},
        "grade": {"type": "STRING", "nullable": True, "enum": ["A", "B", "C"]},
        "complaint_category": {
            "type": "STRING", "nullable": True, "enum": list(COMPLAINT_CATEGORIES),
        },
        "language": {"type": "STRING", "nullable": True, "enum": list(SUPPORTED)},
        "english": {"type": "STRING"},
    },
    "required": ["intent", "english"],
}


def _gemini_json(
    model: str,
    system: str,
    user_text: str,
    schema: Dict[str, Any],
    timeout: float,
    temperature: float = 0.0,
) -> Dict[str, Any]:
    """One structured Gemini call. Raises on any failure; callers fall back."""
    global _cooldown_until
    settings = get_settings()
    if not settings.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")
    if time.time() < _cooldown_until:
        raise RuntimeError("Gemini is cooling down after a rate limit")

    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
            "responseSchema": schema,
        },
    }
    response = httpx.post(
        _GEMINI_URL.format(model=model),
        params={"key": settings.GEMINI_API_KEY},
        json=body,
        timeout=timeout,
    )
    if response.status_code == 429:
        _cooldown_until = time.time() + 30
        raise RuntimeError("Gemini rate limited (429)")
    response.raise_for_status()

    payload = response.json()
    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected Gemini response shape: {str(payload)[:200]}") from exc
    return json.loads(text)


# ---------------------------------------------------------------------------
# Slot normalisation, shared by both paths
# ---------------------------------------------------------------------------

def _script(text: str) -> str:
    """The language a transcript is written in, judged by its script."""
    if re.search(r"[ಀ-೿]", text or ""):
        return "kn"
    if re.search(r"[ऀ-ॿ]", text or ""):
        return "hi"
    return "en"


def _resolve_location(raw: Optional[str], transcript: str = "") -> Optional[str]:
    """A market or district name we can look up, or None."""
    from app.market.normalization import normalize_market_name

    if raw:
        canonical = normalize_market_name(raw)
        if canonical:
            return canonical
    return _scan_market(transcript)


def _scan_market(text: str) -> Optional[str]:
    if not text:
        return None
    lowered = text.lower()
    best: Tuple[int, Optional[str]] = (0, None)
    for spoken, canonical in NATIVE_TO_MARKET.items():
        if spoken in text and len(spoken) > best[0]:
            best = (len(spoken), canonical)
    for canonical in MARKET_KN:
        if re.search(rf"\b{re.escape(canonical.lower())}\b", lowered) and len(canonical) > best[0]:
            best = (len(canonical), canonical)
    for alias in ("gulbarga", "kalburgi", "kalaburgi"):
        if alias in lowered and best[1] is None:
            best = (len(alias), "Kalaburagi")
    return best[1]


def _scan_commodity(text: str) -> Optional[str]:
    """Longest alias present in the text, script-aware."""
    if not text:
        return None
    lowered = text.lower()
    best: Tuple[int, Optional[str]] = (0, None)
    for commodity in COMMODITY_MASTER:
        for alias in (commodity.canonical, *commodity.aliases):
            alias_l = alias.lower()
            if len(alias_l) < 3:
                continue
            if alias_l.isascii():
                hit = re.search(rf"\b{re.escape(alias_l)}s?\b", lowered)
            else:
                hit = alias_l in lowered
            if hit and len(alias_l) > best[0]:
                best = (len(alias_l), commodity.canonical)
    return best[1]


_EN_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
    "twenty": 20, "twenty five": 25, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
}


def _digits_for_words(text: str) -> str:
    out = text.lower()
    for word, value in sorted(_EN_NUMBER_WORDS.items(), key=lambda kv: -len(kv[0])):
        out = re.sub(rf"\b{word}\b", str(value), out)
    return out


def _quantity_kg(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    parsed = parse_quantity(_digits_for_words(str(raw)))
    return round(parsed.kg, 1) if parsed else None


def _finalise(u: Understanding, transcript: str, raw_commodity: Optional[str]) -> Understanding:
    if raw_commodity:
        match = resolve_commodity(raw_commodity)
        u.commodity_raw = raw_commodity
        u.commodity = match.canonical if match else None
    if not u.commodity and not u.commodity_raw:
        scanned = _scan_commodity(transcript) or _scan_commodity(u.english)
        if scanned:
            u.commodity = scanned
            u.commodity_raw = scanned
    if u.intent not in INTENTS:
        u.intent = "OTHER"
    if u.grade not in (None, "A", "B", "C"):
        u.grade = None
    if u.complaint_category and u.complaint_category not in COMPLAINT_CATEGORIES:
        u.complaint_category = "OTHER"
    if u.intent == "CHANGE_LANGUAGE" and not u.language_choice:
        u.language_choice = language_from_choice(None, transcript) or language_from_choice(None, u.english)
    if u.language_choice:
        u.language_choice = normalise_language(u.language_choice, default=None)
    return u


def _understand_with_gemini(transcript: str, context_question: Optional[str], language: str) -> Understanding:
    settings = get_settings()
    prompt = f"Call language: {PROFILES[language].english_name}\n"
    if context_question:
        prompt += f"Assistant's last question: {context_question}\n"
    prompt += f'Farmer said: "{transcript}"'

    started = time.time()
    data = _gemini_json(
        settings.VOICE_NLU_MODEL, _NLU_INSTRUCTION, prompt, _NLU_SCHEMA,
        timeout=settings.VOICE_NLU_TIMEOUT_SECONDS,
    )
    u = Understanding(
        intent=str(data.get("intent") or "OTHER").upper(),
        english=str(data.get("english") or "").strip(),
        location=_resolve_location(data.get("location"), transcript),
        quantity_kg=_quantity_kg(data.get("quantity")),
        storage_days=(
            int(data["storage_days"]) if isinstance(data.get("storage_days"), (int, float)) else None
        ),
        needs_cash_urgently=(
            data["needs_cash_urgently"] if isinstance(data.get("needs_cash_urgently"), bool) else None
        ),
        grade=data.get("grade"),
        complaint_category=data.get("complaint_category"),
        language_choice=data.get("language"),
        source="gemini",
        latency_ms=int((time.time() - started) * 1000),
    )
    return _finalise(u, transcript, data.get("commodity"))


# ---------------------------------------------------------------------------
# Keyword fallback
# ---------------------------------------------------------------------------

_LANGUAGE_WORDS = (
    r"kannada|hindi|english|ಕನ್ನಡ|ಹಿಂದಿ|ಇಂಗ್ಲಿಷ್|ಇಂಗ್ಲೀಷ್|कन्नड़|कन्नड|हिंदी|हिन्दी|इंग्लिश|अंग्रेज़ी|अंग्रेजी"
)

# Checked in order: the first rule that matches wins, so narrower requests
# ("should I sell now") sit above broader ones ("sell").
_RULES: Tuple[Tuple[str, str], ...] = (
    ("CHANGE_LANGUAGE",
     rf"^\W*({_LANGUAGE_WORDS})\W*(please)?\W*$"
     rf"|(speak|talk|ಮಾತನಾಡ|ಮಾತಾಡ|बात|बोल)\w*.{{0,24}}({_LANGUAGE_WORDS})"
     rf"|({_LANGUAGE_WORDS}).{{0,14}}(में|mein|\bme\b|ದಲ್ಲಿ|ನಲ್ಲಿ|please|bolo|बोलो|बोलिए|ಮಾತಾಡಿ|ಮಾತನಾಡಿ)"),
    ("GOODBYE", r"ಧನ್ಯವಾದ|ಬೈ\b|ಸಾಕು|धन्यवाद|शुक्रिया|बस इतना|\bthank|\bbye\b|that'?s all|dhanyavad|shukriya"),
    ("REPEAT", r"ಮತ್ತೆ ಹೇಳಿ|ಇನ್ನೊಮ್ಮೆ ಹೇಳಿ|फिर से बोल|दोबारा बोल|\brepeat\b|say (it )?again"),
    ("RAISE_COMPLAINT", r"ದೂರು|ಮೋಸ|शिकायत|धोखा|complain|cheat|fraud|not paid|didn'?t pay|did not pay|grievance|shikayat"),
    ("PAYMENT_STATUS", r"ಹಣ ಬಂತ|ಹಣ ಬಂದಿದ|ಪಾವತಿ|पैसा आया|पैसे आए|भुगतान|\bpayment\b|money (has )?(come|received|arrived)|got paid|paisa aaya"),
    ("MY_OFFERS", r"ಆಫರ್|ऑफ़र|ऑफर|\boffers?\b|\bbids?\b"),
    ("FPO_POOLING", r"ಎಫ್‌?ಪಿಒ|ಸಂಘ|ಒಟ್ಟಾಗಿ|एफ़?पीओ|मिलकर|\bfpo\b|producer (organi[sz]ation|company)|pool|together|group"),
    ("SELL_OR_WAIT", r"ಕಾಯ|ಈಗ ಮಾರ|अभी बेच|रुक(ूँ|ू|ना|ें)|sell now|\bwait\b|\bhold\b|store it|good time to sell|when (should|to) sell"),
    ("COMPARE_MARKETS", r"ಯಾವ ಮಾರುಕಟ್ಟೆ|ಯಾವ ಮಂಡಿ|ಹೆಚ್ಚು ಬೆಲೆ|कौन सी मंडी|किस मंडी|ज़्यादा भाव|ज्यादा भाव|best (market|mandi|price)|which (market|mandi)|where (should|can|to) (i )?sell|more price|higher price|other market"),
    ("PRICE_TREND", r"ಏರು|ಇಳಿ|बढ़ रहा|घट रहा|going (up|down)|rising|falling|\btrend|last (week|month)|average"),
    ("LIST_FOR_SALE", r"ಮಾರಾಟಕ್ಕೆ|ಮಾರಬೇಕು|बेचना है|बिक्री के लिए|want to sell|put .* for sale|list (my|it)|sell my"),
    ("FIND_BUYERS", r"ಖರೀದಿ|ಯಾರು|कौन ख़?रीद|ख़?रीदार|buyer|who (will|can) buy|trader|purchase"),
    ("PRICE", r"ಬೆಲೆ|ದರ|भाव|दाम|कीमत|\bprice\b|\brate\b|how much|bhav|mandi|apmc"),
    ("YES", r"^\s*(ಹೌದು|ಸರಿ|ಆಗಲಿ|ಓಕೆ|ಮಾಡಿ|हाँ|हां|ठीक है|कर दो|yes|ok|okay|sure|do it|go ahead|haan)\b"),
    ("NO", r"^\s*(ಬೇಡ|ಇಲ್ಲ|नहीं|मत|no|nope|cancel|don'?t|nahi)\b"),
    ("HELP", r"ಏನು ಮಾಡಬಹುದು|ಸಹಾಯ|मदद|\bhelp\b|what can you"),
    ("GREETING", r"^\s*(ನಮಸ್ಕಾರ|ಹಲೋ|ಹಾಯ್|नमस्ते|नमस्कार|हेलो|hello|hi|namaste|namaskara)\W*$"),
)


def _understand_with_keywords(transcript: str, context_question: Optional[str]) -> Understanding:
    from app.translation import translate_to_english

    started = time.time()
    script = _script(transcript)
    english = transcript if script == "en" else ""
    if script != "en":
        try:
            english = translate_to_english(transcript, source=script)
        except Exception as exc:  # pragma: no cover - network
            logger.warning("Fallback translation failed: %s", exc)
    combined = f"{transcript} || {english}"

    intent = "OTHER"
    for name, pattern in _RULES:
        if re.search(pattern, combined, flags=re.IGNORECASE):
            intent = name
            break

    quantity = _quantity_kg(english) or _quantity_kg(transcript)
    storage_days = None
    days = re.search(r"(\d+)\s*(?:days?|ದಿನ|दिन)", _digits_for_words(combined))
    if days:
        storage_days = int(days.group(1))
    elif re.search(r"can'?t store|cannot store|no storage|ಇಡಲು ಆಗ|नहीं रख सकते", combined, re.IGNORECASE):
        storage_days = 0

    urgent = None
    if re.search(r"not urgent|no hurry|ಅವಸರ ಇಲ್ಲ|जल्दी नहीं", combined, re.IGNORECASE):
        urgent = False
    elif re.search(r"urgent|immediately|need money|ತುರ್ತು|ತಕ್ಷಣ|तुरंत|फ़ौरन", combined, re.IGNORECASE):
        urgent = True

    grade = None
    if re.search(r"grade a|good quality|best quality|ಉತ್ತಮ|अच्छी", combined, re.IGNORECASE):
        grade = "A"
    elif re.search(r"medium|grade b|ಮಧ್ಯಮ|मध्यम", combined, re.IGNORECASE):
        grade = "B"
    elif re.search(r"grade c|low quality|ಸಾಧಾರಣ|साधारण", combined, re.IGNORECASE):
        grade = "C"

    category = None
    if intent == "RAISE_COMPLAINT":
        if re.search(r"weigh|ತೂಕ|तौल", combined, re.IGNORECASE):
            category = "WEIGHT"
        elif re.search(r"quality|reject|ಗುಣಮಟ್ಟ|क्वालिटी", combined, re.IGNORECASE):
            category = "QUALITY"
        elif re.search(r"pay|money|ಹಣ|पैस|भुगतान", combined, re.IGNORECASE):
            category = "PAYMENT"
        else:
            category = "OTHER"

    if intent == "OTHER" and context_question and (quantity or storage_days is not None or grade):
        intent = "PROVIDE_DETAILS"

    u = Understanding(
        intent=intent,
        english=english or transcript,
        location=_scan_market(transcript) or _scan_market(english),
        quantity_kg=quantity,
        storage_days=storage_days,
        needs_cash_urgently=urgent,
        grade=grade,
        complaint_category=category,
        source="keywords",
        latency_ms=int((time.time() - started) * 1000),
    )
    return _finalise(u, transcript, None)


def understand(
    transcript: str,
    context_question: Optional[str] = None,
    language: str = DEFAULT_LANGUAGE,
) -> Understanding:
    """Intent and slots for one utterance in any supported language. Never raises."""
    text = (transcript or "").strip()
    language = normalise_language(language)
    if not text:
        return Understanding(intent="OTHER", source="empty")
    try:
        u = _understand_with_gemini(text, context_question, language)
    except Exception as exc:
        logger.warning("Gemini NLU unavailable (%s); using keyword understanding", exc)
        u = _understand_with_keywords(text, context_question)
        u.notes.append(f"gemini unavailable: {exc}")
    u.text = text
    return u


# ---------------------------------------------------------------------------
# Open questions
# ---------------------------------------------------------------------------

_OPEN_REPLY_INSTRUCTION = """You are a phone assistant for Indian farmers, focused on selling produce: market prices,
choosing a market, when to sell, finding verified buyers, offers, payments and complaints.

Reply to the farmer in simple, warm, spoken {language}, the way a helpful person at a farmers'
helpline talks. Rules:
- At most three short sentences. No lists and no symbols. {script_rule}
- Never state a price, a buyer, a scheme amount or any figure. You have no data in this step.
- If the question is about farming practice, weather, pests, loans or schemes, give one line of
  general guidance and tell them the free Kisan Call Centre number 1800 180 1551 can help.
- End by offering what you can do: today's price, the best market, sell-or-wait advice,
  or verified buyers.
Return JSON with "reply" (in {language}) and "english" (its English translation)."""

_SCRIPT_RULES = {
    "kn": "Write in Kannada script, with no English words except names.",
    "hi": "Write in Devanagari Hindi, with no English words except names.",
    "en": "Use plain, simple Indian English.",
}

_OPEN_REPLY_SCHEMA = {
    "type": "OBJECT",
    "properties": {"reply": {"type": "STRING"}, "english": {"type": "STRING"}},
    "required": ["reply", "english"],
}


def compose_open_reply(
    transcript: str, english: str, history: List[Dict[str, str]], language: str = DEFAULT_LANGUAGE,
) -> Optional[Tuple[str, str]]:
    """A scoped conversational reply for questions no engine answers. None on failure."""
    settings = get_settings()
    language = normalise_language(language)
    recent = "\n".join(
        f"Farmer: {h.get('farmer_en', '')}\nAssistant: {h.get('reply_en', '')}"
        for h in history[-3:]
    )
    prompt = (
        (f"Conversation so far:\n{recent}\n\n" if recent else "")
        + f'Farmer now says: "{transcript}"\nEnglish gloss: "{english}"'
    )
    instruction = _OPEN_REPLY_INSTRUCTION.format(
        language=PROFILES[language].english_name, script_rule=_SCRIPT_RULES[language],
    )
    try:
        data = _gemini_json(
            settings.VOICE_REPLY_MODEL, instruction, prompt,
            _OPEN_REPLY_SCHEMA, timeout=settings.VOICE_REPLY_TIMEOUT_SECONDS,
            temperature=0.4,
        )
    except Exception as exc:
        logger.warning("Open reply generation failed: %s", exc)
        return None

    reply = str(data.get("reply") or "").strip()
    en = str(data.get("english") or "").strip()
    # A reply that slipped a number in is discarded: this step has no data, so
    # any figure in it is invented. The helpline number is the one exception.
    for text in (reply, en):
        if re.search(r"\d", text.replace("1800 180 1551", "")):
            return None
    if not reply:
        return None
    return reply, (reply if language == "en" else en)
