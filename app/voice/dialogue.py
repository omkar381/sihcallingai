"""
The conversation manager, scoped to PS 26132, in Kannada, Hindi and English.

What a caller can do, in the order the problem statement puts it:

    price discovery    PRICE, PRICE_TREND
    market choice      COMPARE_MARKETS (ranked on what the farmer keeps after transport)
    timing             SELL_OR_WAIT (depends on storage and cash need, so it asks)
    market linkage     FIND_BUYERS (verified demand only), LIST_FOR_SALE, MY_OFFERS
    settlement         PAYMENT_STATUS, RAISE_COMPLAINT
    aggregation        FPO_POOLING

Principles:
- Every figure comes from an engine. Each reply is composed natively in every
  language from those figures (a `Line`); nothing is machine-translated and
  nothing is estimated here.
- Anything that commits the farmer (listing produce, accepting an offer,
  filing a complaint) is read back and needs an explicit yes.
- When information is missing the agent asks for it instead of assuming.
- Each answer ends by offering the natural next step, so the call moves
  toward a sale rather than stopping at a number.
- The caller can switch language at any point by asking for it.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.config import get_settings
from app.voice.language import DEFAULT_LANGUAGE, DEMO_CAVEAT, SYSTEM_PROMPTS, normalise_language, speaker
from app.voice.nlu import Understanding, compose_open_reply

logger = logging.getLogger(__name__)

K, H, E = speaker("kn"), speaker("hi"), speaker("en")


@dataclass
class Line:
    """One thing to say, written in every language the agent speaks."""

    kn: str = ""
    hi: str = ""
    en: str = ""

    def __add__(self, other: "Line") -> "Line":
        return Line(_cat(self.kn, other.kn), _cat(self.hi, other.hi), _cat(self.en, other.en))

    def get(self, language: str) -> str:
        return getattr(self, normalise_language(language))


def _cat(a: str, b: str) -> str:
    return f"{a} {b}".strip() if a and b else (a or b)


def _join(parts: List[Line]) -> Line:
    out = Line()
    for part in parts:
        out = out + part
    return out


def _prompt(key: str) -> Line:
    return Line(SYSTEM_PROMPTS["kn"][key], SYSTEM_PROMPTS["hi"][key], SYSTEM_PROMPTS["en"][key])


@dataclass
class Reply:
    text: str               # what is spoken, in `language`
    en: str                 # English, for the transcript view
    language: str
    intent: str
    data_quality: Optional[str] = None
    uses_market_data: bool = False
    hangup: bool = False


@dataclass
class Pending:
    """Something the farmer has been asked to say yes to."""

    kind: str                 # "confirm" commits an action; "suggest" offers a next step
    action: str
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DialogueState:
    call_sid: str
    phone: str = ""
    language: str = DEFAULT_LANGUAGE
    commodity: Optional[str] = None
    commodity_raw: Optional[str] = None
    location: Optional[str] = None
    quantity_kg: Optional[float] = None
    storage_days: Optional[int] = None
    needs_cash_urgently: Optional[bool] = None
    grade: Optional[str] = None
    complaint_category: Optional[str] = None
    complaint_text: Optional[str] = None
    awaiting: Optional[str] = None
    pending: Optional[Pending] = None
    last_question_en: Optional[str] = None
    last_reply: Optional[Reply] = None
    caveat_spoken: bool = False
    turn_no: int = 0
    silent_turns: int = 0
    menu_attempts: int = 0
    history: List[Dict[str, str]] = field(default_factory=list)
    last_activity: float = field(default_factory=time.time)


_STATES: Dict[str, DialogueState] = {}
_STATES_LOCK = threading.Lock()


def _default_location(phone: str) -> str:
    settings = get_settings()
    try:
        from app.farmer_store import get_farmer

        farmer = get_farmer(phone) if phone else None
        city = (farmer or {}).get("city") or (farmer or {}).get("district")
        if city:
            from app.market.normalization import normalize_market_name

            return normalize_market_name(city) or settings.VOICE_DEFAULT_LOCATION
    except Exception:
        pass
    return settings.VOICE_DEFAULT_LOCATION


def get_state(call_sid: str, phone: str = "") -> DialogueState:
    with _STATES_LOCK:
        state = _STATES.get(call_sid)
        if state is None:
            state = DialogueState(call_sid=call_sid, phone=phone)
            _STATES[call_sid] = state
        elif phone and not state.phone:
            state.phone = phone
        state.last_activity = time.time()
        return state


def end_state(call_sid: str) -> None:
    with _STATES_LOCK:
        _STATES.pop(call_sid, None)


def cleanup_states(max_idle_seconds: int = 1800) -> int:
    cutoff = time.time() - max_idle_seconds
    with _STATES_LOCK:
        stale = [sid for sid, s in _STATES.items() if s.last_activity < cutoff]
        for sid in stale:
            _STATES.pop(sid, None)
    return len(stale)


def _reply(state: DialogueState, line: Line, intent: str, data_quality: Optional[str] = None,
           uses_market_data: bool = False, hangup: bool = False) -> Reply:
    return Reply(
        text=line.get(state.language), en=line.en, language=state.language, intent=intent,
        data_quality=data_quality, uses_market_data=uses_market_data, hangup=hangup,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def respond(state: DialogueState, u: Understanding) -> Reply:
    """Decide and phrase the answer to one utterance."""
    state.turn_no += 1
    state.silent_turns = 0
    _absorb(state, u)
    intent = u.intent

    if intent == "CHANGE_LANGUAGE":
        state.awaiting = None
        return _finish(state, _change_language(state, u))

    if intent == "REPEAT" and state.last_reply:
        last = state.last_reply
        return Reply(last.text, last.en, last.language, "REPEAT", last.data_quality)

    if intent == "GOODBYE":
        state.pending = None
        return _finish(state, _reply(state, _prompt("goodbye"), "GOODBYE", hangup=True))

    if state.pending:
        pending = state.pending
        if intent == "YES":
            state.pending = None
            return _finish(state, _run_pending(state, u, pending))
        if intent == "NO":
            state.pending = None
            state.awaiting = None
            return _finish(state, _reply(state, Line(
                "ಸರಿ, ಆಗಲಿ. ಬೇರೆ ಏನು ಸಹಾಯ ಬೇಕು?",
                "ठीक है। और क्या मदद चाहिए?",
                "All right. What else can I help with?",
            ), "NO"))
        # A new request replaces an unanswered suggestion or confirmation.
        state.pending = None

    if state.awaiting and intent in ("PROVIDE_DETAILS", "YES", "OTHER"):
        intent = state.awaiting
    elif intent == "PROVIDE_DETAILS":
        intent = "PRICE" if u.commodity else "HELP"
    elif intent in ("YES", "NO"):
        intent = "HELP"

    if intent != state.awaiting:
        state.awaiting = None

    handler = _HANDLERS.get(intent, _other)
    try:
        reply = handler(state, u)
    except Exception:
        logger.exception("Dialogue handler for %s failed", intent)
        reply = _reply(state, _prompt("error"), intent)
    return _finish(state, reply)


def _absorb(state: DialogueState, u: Understanding) -> None:
    """Carry slots forward: a crop mentioned once applies until another is named."""
    if u.commodity:
        state.commodity = u.commodity
        state.commodity_raw = u.commodity_raw
    elif u.commodity_raw:
        # A crop was named that we hold no data for. Keeping the previous crop
        # would answer about the wrong one ("dragon fruit price" -> tur price).
        state.commodity = None
        state.commodity_raw = u.commodity_raw
    if u.location:
        state.location = u.location
    if u.quantity_kg:
        state.quantity_kg = u.quantity_kg
    if u.storage_days is not None:
        state.storage_days = u.storage_days
    if u.needs_cash_urgently is not None:
        state.needs_cash_urgently = u.needs_cash_urgently
    if u.grade:
        state.grade = u.grade
    if u.complaint_category:
        state.complaint_category = u.complaint_category
    if u.intent == "RAISE_COMPLAINT" and u.english:
        state.complaint_text = u.english


def _finish(state: DialogueState, reply: Reply) -> Reply:
    settings = get_settings()
    if (
        reply.uses_market_data
        and reply.data_quality == "MOCK"
        and settings.MARKET_DEMO_MODE
        and not state.caveat_spoken
    ):
        reply.text = _insert_before_question(reply.text, DEMO_CAVEAT[reply.language])
        reply.en = _insert_before_question(reply.en, DEMO_CAVEAT["en"])
        state.caveat_spoken = True

    state.last_reply = reply
    state.last_question_en = reply.en if reply.en.rstrip().endswith("?") else None
    return reply


def _insert_before_question(text: str, note: str) -> str:
    """Put `note` ahead of a closing question, so the turn still ends on the question."""
    match = re.match(r"^(.*[.!।])\s+([^.!?।]*\?)\s*$", text, flags=re.DOTALL)
    if match:
        return f"{match.group(1)} {note} {match.group(2)}"
    return f"{text} {note}"


def _suggest(state: DialogueState, action: str, **data: Any) -> None:
    state.pending = Pending("suggest", action, data)


def _confirm(state: DialogueState, action: str, **data: Any) -> None:
    state.pending = Pending("confirm", action, data)


def _run_pending(state: DialogueState, u: Understanding, pending: Pending) -> Reply:
    if pending.kind == "suggest":
        handler = _HANDLERS.get(pending.action, _help)
        return handler(state, u)
    runner = _CONFIRMED_ACTIONS.get(pending.action)
    if runner is None:
        return _help(state, u)
    return runner(state, pending.data)


def _demo_quality() -> Optional[str]:
    """Trade records carry no per-row provenance; demo mode marks them as fixtures."""
    return "MOCK" if get_settings().MARKET_DEMO_MODE else None


# ---------------------------------------------------------------------------
# Language
# ---------------------------------------------------------------------------

def _change_language(state: DialogueState, u: Understanding) -> Reply:
    choice = normalise_language(u.language_choice, default=None)
    if choice is None:
        return _reply(state, Line(
            "ಯಾವ ಭಾಷೆಯಲ್ಲಿ ಮಾತನಾಡಲಿ? ಕನ್ನಡ, ಹಿಂದಿ ಅಥವಾ ಇಂಗ್ಲಿಷ್?",
            "किस भाषा में बात करूँ? कन्नड़, हिंदी या इंग्लिश?",
            "Which language should I speak: Kannada, Hindi or English?",
        ), "CHANGE_LANGUAGE")
    state.language = choice
    return _reply(state, Line(
        "ಸರಿ, ಈಗಿನಿಂದ ಕನ್ನಡದಲ್ಲಿ ಮಾತನಾಡುತ್ತೇನೆ. ಹೇಳಿ, ಯಾವ ಬೆಳೆಯ ಬಗ್ಗೆ ಮಾಹಿತಿ ಬೇಕು?",
        "ठीक है, अब से हिंदी में बात करूँगा। बताइए, किस फसल की जानकारी चाहिए?",
        "Sure, I will speak English from now on. Which crop do you need information about?",
    ), "CHANGE_LANGUAGE")


# ---------------------------------------------------------------------------
# Shared questions
# ---------------------------------------------------------------------------

def _location(state: DialogueState) -> str:
    if not state.location:
        state.location = _default_location(state.phone)
    return state.location


def _ask_commodity(state: DialogueState, intent: str) -> Reply:
    state.awaiting = intent
    if state.commodity_raw and not state.commodity:
        raw = state.commodity_raw
        state.commodity_raw = None
        return _reply(state, Line(
            f"ಕ್ಷಮಿಸಿ, {raw} ಬೆಳೆಯ ಮಾರುಕಟ್ಟೆ ಮಾಹಿತಿ ನನ್ನ ಬಳಿ ಇಲ್ಲ. ಈರುಳ್ಳಿ, ತೊಗರಿ, ಹತ್ತಿ, "
            "ಮೆಕ್ಕೆಜೋಳ, ಟೊಮೆಟೊ ಇಂತಹ ಬೆಳೆಗಳ ಬಗ್ಗೆ ಕೇಳಬಹುದು. ಯಾವ ಬೆಳೆ?",
            f"माफ़ कीजिए, {raw} का मंडी भाव मेरे पास नहीं है। आप प्याज, तुअर, कपास, मक्का, "
            "टमाटर जैसी फसलों के बारे में पूछ सकते हैं। कौन सी फसल?",
            f"Sorry, I do not have market data for {raw}. I can help with crops like onion, tur, "
            "cotton, maize and tomato. Which crop?",
        ), intent)
    return _reply(state, Line(
        "ಯಾವ ಬೆಳೆಯ ಬಗ್ಗೆ ಕೇಳುತ್ತಿದ್ದೀರಿ?",
        "किस फसल के बारे में पूछ रहे हैं?",
        "Which crop are you asking about?",
    ), intent)


def _ask_quantity(state: DialogueState, intent: str) -> Reply:
    state.awaiting = intent
    c = state.commodity
    return _reply(state, Line(
        f"ನಿಮ್ಮ ಬಳಿ ಎಷ್ಟು {K.crop(c)} ಇದೆ? ಕ್ವಿಂಟಲ್ ಅಥವಾ ಚೀಲದಲ್ಲಿ ಹೇಳಿದರೂ ಸಾಕು.",
        f"आपके पास {H.crop(c)} कितनी मात्रा में है? क्विंटल या बोरी में बता दीजिए।",
        f"How much {c} do you have? Quintals or bags is fine.",
    ), intent)


def _market_unavailable(state: DialogueState, intent: str) -> Reply:
    place, c = state.location, state.commodity
    # Forget the place so the next question does not fail the same way.
    state.location = None
    return _reply(state, Line(
        f"ಕ್ಷಮಿಸಿ, {K.market(place)} ಹತ್ತಿರ {K.crop(c)} ಬೆಲೆ ಮಾಹಿತಿ ಸಿಗಲಿಲ್ಲ. ಹತ್ತಿರದ ಜಿಲ್ಲಾ ಕೇಂದ್ರದ "
        "ಹೆಸರು ಹೇಳಿದರೆ ಅಲ್ಲಿ ನೋಡುತ್ತೇನೆ.",
        f"माफ़ कीजिए, {H.market(place)} के पास {H.crop(c)} का भाव नहीं मिला। पास के ज़िला "
        "मुख्यालय का नाम बताइए, मैं वहाँ देखता हूँ।",
        f"Sorry, I could not find {c} prices near {place or 'there'}. "
        "Tell me the nearest district town and I will check there.",
    ), intent)


def _date_note(payload: Dict[str, Any]) -> Line:
    quality = payload.get("data_quality")
    if quality in ("STALE", "DEGRADED") and payload.get("arrival_date"):
        date = payload["arrival_date"]
        return Line(
            f"ಈ ಬೆಲೆ {date} ದಿನಾಂಕದ್ದು, ಮಾರುವ ಮೊದಲು ಮಾರುಕಟ್ಟೆಯಲ್ಲಿ ಖಚಿತಪಡಿಸಿಕೊಳ್ಳಿ.",
            f"यह भाव {date} का है, बेचने से पहले मंडी में पक्का कर लीजिए।",
            f"This price is from {date}; confirm at the market before selling.",
        )
    return Line()


# ---------------------------------------------------------------------------
# Price discovery
# ---------------------------------------------------------------------------

def _price(state: DialogueState, u: Understanding) -> Reply:
    from app.market import service

    if not state.commodity:
        return _ask_commodity(state, "PRICE")
    payload = service.get_price(state.commodity, _location(state))
    if not payload.get("available"):
        return _market_unavailable(state, "PRICE")

    m, c = payload["market"], state.commodity
    modal, low, high = payload["modal_price"], payload["min_price"], payload["max_price"]
    per_kg = int(round(modal / 100))

    parts = [Line(
        f"{K.market(m)} ಮಾರುಕಟ್ಟೆಯಲ್ಲಿ {K.crop(c)} ಬೆಲೆ ಕ್ವಿಂಟಲ್‌ಗೆ {K.rupees(modal)} ಇದೆ, "
        f"ಅಂದರೆ ಕೆಜಿಗೆ ಸುಮಾರು {per_kg} ರೂಪಾಯಿ.",
        f"{H.market(m)} मंडी में {H.crop(c)} का भाव {H.rupees(modal)} प्रति क्विंटल है, "
        f"यानी लगभग {per_kg} रुपये किलो।",
        f"At {m} market, {c} is {E.rupees(modal)} per quintal, about ₹{per_kg} a kilo.",
    )]
    if low and high and high > low:
        lo, hi_ = int(round(low)), int(round(high))
        parts.append(Line(
            f"ಕನಿಷ್ಠ {lo}, ಗರಿಷ್ಠ {hi_} ರೂಪಾಯಿ ತನಕ ವ್ಯಾಪಾರ ಆಗಿದೆ.",
            f"कम से कम {lo} और ज़्यादा से ज़्यादा {hi_} रुपये तक सौदा हुआ है।",
            f"Trades ranged from {E.rupees(low)} to {E.rupees(high)}.",
        ))
    parts.append(_date_note(payload))
    parts.append(Line(
        "ಖರ್ಚು ಕಳೆದು ಬೇರೆ ಹತ್ತಿರದ ಮಾರುಕಟ್ಟೆಯಲ್ಲಿ ಹೆಚ್ಚು ಸಿಗುತ್ತಾ ಅಂತ ನೋಡಬೇಕಾ?",
        "क्या देखूँ कि ख़र्च काटकर पास की किसी और मंडी में ज़्यादा मिलेगा?",
        "Shall I check whether a nearby market pays more after costs?",
    ))
    _suggest(state, "COMPARE_MARKETS")
    return _reply(state, _join(parts), "PRICE", payload.get("data_quality"), uses_market_data=True)


def _trend(state: DialogueState, u: Understanding) -> Reply:
    from app.market import service

    if not state.commodity:
        return _ask_commodity(state, "PRICE_TREND")
    payload = service.get_trend(state.commodity, _location(state))
    if not payload.get("available"):
        return _market_unavailable(state, "PRICE_TREND")

    c, m = state.commodity, payload.get("market") or _location(state)
    windows = payload.get("windows", {})
    week, month = windows.get("7") or {}, windows.get("30") or {}
    arrivals = (payload.get("arrivals") or {}).get("30") or {}

    parts: List[Line] = []
    if payload.get("current_price"):
        p = payload["current_price"]
        parts.append(Line(
            f"{K.market(m)} ಮಾರುಕಟ್ಟೆಯಲ್ಲಿ {K.crop(c)} ಈಗ ಕ್ವಿಂಟಲ್‌ಗೆ {K.rupees(p)}.",
            f"{H.market(m)} मंडी में {H.crop(c)} अभी {H.rupees(p)} प्रति क्विंटल है।",
            f"{c} at {m} is now {E.rupees(p)} per quintal.",
        ))

    change = week.get("change_pct")
    if week.get("sufficient_data") and change is not None:
        if abs(change) < 1:
            parts.append(Line(
                "ಕಳೆದ ಒಂದು ವಾರದಲ್ಲಿ ಬೆಲೆ ಹೆಚ್ಚು ಕಡಿಮೆ ಸ್ಥಿರವಾಗಿದೆ.",
                "पिछले एक हफ़्ते में भाव लगभग स्थिर रहा है।",
                "Over the past week the price has been roughly steady.",
            ))
        else:
            up = change > 0
            parts.append(Line(
                f"ಕಳೆದ ಒಂದು ವಾರದಲ್ಲಿ ಬೆಲೆ {K.pct(change)} {'ಏರಿದೆ' if up else 'ಇಳಿದಿದೆ'}.",
                f"पिछले एक हफ़्ते में भाव {H.pct(change)} {'बढ़ा है' if up else 'घटा है'}।",
                f"Over the past week it has {'risen' if up else 'fallen'} {E.pct(change)}.",
            ))

    if month.get("sufficient_data") and month.get("average"):
        avg = month["average"]
        parts.append(Line(
            f"ಕಳೆದ ಒಂದು ತಿಂಗಳ ಸರಾಸರಿ {K.rupees(avg)}.",
            f"पिछले एक महीने का औसत {H.rupees(avg)} है।",
            f"The one-month average is {E.rupees(avg)}.",
        ))

    arrival_change = arrivals.get("change_pct")
    if arrival_change is not None and abs(arrival_change) >= 5:
        down = arrival_change < 0
        parts.append(Line(
            f"ಮಾರುಕಟ್ಟೆಗೆ ಬರುತ್ತಿರುವ ಸರಕಿನ ಪ್ರಮಾಣ {K.pct(arrival_change)} {'ಕಡಿಮೆಯಾಗಿದೆ' if down else 'ಹೆಚ್ಚಾಗಿದೆ'}.",
            f"मंडी में आवक {H.pct(arrival_change)} {'कम हुई है' if down else 'बढ़ी है'}।",
            f"Arrivals into the market have {'fallen' if down else 'risen'} {E.pct(arrival_change)}.",
        ))

    if len(parts) <= 1:
        parts.append(Line(
            "ಬೆಲೆಯ ಏರಿಳಿತ ಹೇಳುವಷ್ಟು ಹಳೆಯ ಮಾಹಿತಿ ಇನ್ನೂ ಸಾಕಾಗುವಷ್ಟು ಇಲ್ಲ.",
            "भाव का रुझान बताने लायक पुराने आँकड़े अभी काफ़ी नहीं हैं।",
            "There is not yet enough history to describe a trend.",
        ))

    parts.append(Line(
        "ಈಗ ಮಾರಬೇಕಾ ಕಾಯಬೇಕಾ ಅಂತ ಸಲಹೆ ಬೇಕಾ?",
        "क्या अभी बेचने या रुकने की सलाह चाहिए?",
        "Would you like advice on whether to sell now or wait?",
    ))
    _suggest(state, "SELL_OR_WAIT")
    return _reply(state, _join(parts), "PRICE_TREND", payload.get("data_quality"), uses_market_data=True)


# ---------------------------------------------------------------------------
# Market choice
# ---------------------------------------------------------------------------

def _compare(state: DialogueState, u: Understanding) -> Reply:
    from app.market import service

    if not state.commodity:
        return _ask_commodity(state, "COMPARE_MARKETS")
    if not state.quantity_kg:
        return _ask_quantity(state, "COMPARE_MARKETS")

    payload = service.compare(state.commodity, _location(state), quantity=f"{state.quantity_kg} kg")
    options = payload.get("options") or []
    if not payload.get("available") or not options:
        return _market_unavailable(state, "COMPARE_MARKETS")

    c, qty = state.commodity, state.quantity_kg
    origin = next((o for o in options if o.get("is_origin")), None)
    best = next((o for o in options if o["market"] == payload.get("best_market")), options[0])

    parts: List[Line] = []
    if origin:
        om, net = origin["market"], origin["net_price_per_quintal"]
        parts.append(Line(
            f"ನಿಮ್ಮ {K.qty(qty)} {K.crop(c)} {K.market(om)} ಮಾರುಕಟ್ಟೆಯಲ್ಲಿ ಮಾರಿದರೆ, ಸಾಗಣೆ ಮತ್ತು "
            f"ಕಮಿಷನ್ ಕಳೆದು ಕ್ವಿಂಟಲ್‌ಗೆ ಸುಮಾರು {K.rupees(net)} ಕೈಗೆ ಬರುತ್ತದೆ.",
            f"{H.qty(qty)} {H.crop(c)} अगर {H.market(om)} मंडी में बेचेंगे, तो भाड़ा और कमीशन "
            f"काटकर लगभग {H.rupees(net)} प्रति क्विंटल हाथ में आएँगे।",
            f"Selling your {E.qty(qty)} of {c} at {om}, you would keep about {E.rupees(net)} "
            "per quintal after transport and commission.",
        ))

    if origin is None or best["market"] != origin["market"]:
        bm, dist, net = best["market"], best["distance_km"], best["net_price_per_quintal"]
        parts.append(Line(
            f"{K.market(bm)} ಮಾರುಕಟ್ಟೆ {K.km(dist)} ದೂರ ಇದೆ, ಆದರೆ ಅಲ್ಲಿ ಎಲ್ಲಾ ಖರ್ಚು ಕಳೆದು "
            f"ಕ್ವಿಂಟಲ್‌ಗೆ {K.rupees(net)} ಸಿಗುತ್ತದೆ.",
            f"{H.market(bm)} मंडी {H.km(dist)} दूर है, लेकिन वहाँ सारा ख़र्च काटकर "
            f"{H.rupees(net)} प्रति क्विंटल मिलेंगे।",
            f"{bm} is {E.km(dist)} away, but after all costs it leaves you {E.rupees(net)} per quintal.",
        ))
        total = payload.get("advantage_total")
        if total and total > 0:
            parts.append(Line(
                f"ಅಂದರೆ ನಿಮ್ಮ ಪೂರ್ತಿ ಸರಕಿಗೆ {K.about(total)} ಹೆಚ್ಚು.",
                f"यानी आपके पूरे माल पर {H.about(total)} ज़्यादा।",
                f"That is {E.about(total)} more for your whole lot.",
            ))
    else:
        parts.append(Line(
            "ಖರ್ಚು ಕಳೆದು ನೋಡಿದರೆ ನಿಮ್ಮ ಹತ್ತಿರದ ಮಾರುಕಟ್ಟೆಯೇ ಉತ್ತಮ. ದೂರದ ಮಾರುಕಟ್ಟೆಗೆ ಹೋದರೆ "
            "ಸಾಗಣೆ ಖರ್ಚೇ ಹೆಚ್ಚಾಗುತ್ತದೆ.",
            "ख़र्च काटकर देखें तो आपकी पास की मंडी ही सबसे अच्छी है। दूर की मंडी जाने पर "
            "भाड़ा ही ज़्यादा लग जाएगा।",
            "After costs, your nearby market is the best option; a distant market loses the "
            "difference to transport.",
        ))

    parts.append(Line(
        "ಈ ಬೆಳೆ ಖರೀದಿಸುವ ಪರಿಶೀಲಿತ ವ್ಯಾಪಾರಿಗಳನ್ನು ಹುಡುಕಬೇಕಾ?",
        "क्या इस फसल के सत्यापित ख़रीदार ढूँढूँ?",
        "Shall I look for verified buyers for this crop?",
    ))
    _suggest(state, "FIND_BUYERS")
    return _reply(state, _join(parts), "COMPARE_MARKETS", payload.get("data_quality"), uses_market_data=True)


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------

def _decision_line(decision: str, c: str, days: int) -> Line:
    if decision == "SELL_NOW":
        return Line(
            f"ನನ್ನ ಸಲಹೆ: ನಿಮ್ಮ {K.crop(c)} ಈಗಲೇ ಮಾರಿ.",
            f"मेरी सलाह: {H.crop(c)} अभी बेच दीजिए।",
            f"My advice: sell your {c} now.",
        )
    if decision == "WAIT":
        return Line(
            f"ನನ್ನ ಸಲಹೆ: ಸುಮಾರು {K.days(days)} ಕಾದು ನಂತರ ಮಾರಿ.",
            f"मेरी सलाह: लगभग {H.days(days)} रुककर बेचिए।",
            f"My advice: wait about {E.days(days)}, then sell.",
        )
    if decision == "SELL_PARTIAL":
        return Line(
            "ನನ್ನ ಸಲಹೆ: ಒಂದು ಭಾಗ ಈಗ ಮಾರಿ, ಉಳಿದದ್ದನ್ನು ಸ್ವಲ್ಪ ದಿನ ಇಟ್ಟುಕೊಳ್ಳಿ.",
            "मेरी सलाह: कुछ हिस्सा अभी बेचिए, बाकी कुछ दिन रखिए।",
            "My advice: sell part now and hold the rest for a few days.",
        )
    return Line(
        "ನನ್ನ ಸಲಹೆ: ಈಗಲೇ ಮಾರಿ, ಆದರೆ ನಿಮ್ಮ ಹತ್ತಿರದ ಮಾರುಕಟ್ಟೆಯಲ್ಲಿ ಅಲ್ಲ.",
        "मेरी सलाह: अभी बेचिए, लेकिन अपनी पास की मंडी में नहीं।",
        "My advice: sell now, but not at your nearest market.",
    )


_DECISIONS = ("SELL_NOW", "WAIT", "SELL_PARTIAL", "COMPARE_MARKETS")


def _sell_or_wait(state: DialogueState, u: Understanding) -> Reply:
    from app.market import service

    if not state.commodity:
        return _ask_commodity(state, "SELL_OR_WAIT")

    missing_storage = state.storage_days is None
    missing_urgency = state.needs_cash_urgently is None
    if missing_storage or missing_urgency:
        state.awaiting = "SELL_OR_WAIT"
        if missing_storage and missing_urgency:
            line = Line(
                "ಸರಿಯಾದ ಸಲಹೆ ಕೊಡಲು ಎರಡು ವಿಷಯ ಹೇಳಿ. ಬೆಳೆಯನ್ನು ಎಷ್ಟು ದಿನ ಸುರಕ್ಷಿತವಾಗಿ "
                "ಇಡಬಹುದು? ಮತ್ತು ನಿಮಗೆ ತಕ್ಷಣ ಹಣದ ಅವಶ್ಯಕತೆ ಇದೆಯಾ?",
                "सही सलाह देने के लिए दो बातें बताइए। फसल को कितने दिन सुरक्षित रख सकते हैं? "
                "और क्या आपको तुरंत पैसों की ज़रूरत है?",
                "To advise you properly, tell me two things: how many days can you safely store "
                "the crop, and do you need money urgently?",
            )
        elif missing_storage:
            line = Line(
                "ಬೆಳೆಯನ್ನು ಎಷ್ಟು ದಿನ ಸುರಕ್ಷಿತವಾಗಿ ಇಡಬಹುದು?",
                "फसल को कितने दिन सुरक्षित रख सकते हैं?",
                "How many days can you safely store the crop?",
            )
        else:
            line = Line(
                "ನಿಮಗೆ ತಕ್ಷಣ ಹಣದ ಅವಶ್ಯಕತೆ ಇದೆಯಾ?",
                "क्या आपको तुरंत पैसों की ज़रूरत है?",
                "Do you need money urgently?",
            )
        return _reply(state, line, "SELL_OR_WAIT")

    location = _location(state)
    quantity = f"{state.quantity_kg} kg" if state.quantity_kg else None
    payload = service.sale_window(
        state.commodity, location, quantity=quantity,
        storage_days=int(state.storage_days or 0),
        needs_cash_urgently=bool(state.needs_cash_urgently),
    )
    decision = payload.get("decision")
    if not payload.get("available") or decision not in _DECISIONS:
        return _reply(state, Line(
            "ಸರಿಯಾದ ಸಲಹೆ ಕೊಡುವಷ್ಟು ನಂಬಲರ್ಹ ಮಾರುಕಟ್ಟೆ ಮಾಹಿತಿ ಈಗ ನನ್ನ ಬಳಿ ಇಲ್ಲ. "
            "ದಯವಿಟ್ಟು ನಿಮ್ಮ ಹತ್ತಿರದ ಎಪಿಎಂಸಿಯಲ್ಲಿ ವಿಚಾರಿಸಿ.",
            "सही सलाह देने लायक भरोसेमंद मंडी आँकड़े अभी मेरे पास नहीं हैं। "
            "कृपया अपनी पास की एपीएमसी मंडी में पूछ लीजिए।",
            "I do not have reliable enough market data to advise you. Please check with your nearest APMC.",
        ), "SELL_OR_WAIT", payload.get("data_quality"))

    c = state.commodity
    days = int(payload.get("recommended_days") or 0)
    parts = [_decision_line(decision, c, days)]

    # The reasons are rebuilt from the farmer's own constraints and the trend
    # numbers, because the engine's reasoning strings are English audit text.
    reasons: List[Line] = []
    if state.needs_cash_urgently:
        reasons.append(Line("ನಿಮಗೆ ತಕ್ಷಣ ಹಣ ಬೇಕು ಅಂತ ಹೇಳಿದ್ದೀರಿ", "आपको तुरंत पैसे चाहिए", "you need money urgently"))
    if not state.storage_days:
        reasons.append(Line("ಬೆಳೆ ಇಟ್ಟುಕೊಳ್ಳುವ ವ್ಯವಸ್ಥೆ ಇಲ್ಲ", "रखने की व्यवस्था नहीं है", "you have no storage"))

    trend = service.get_trend(c, location)
    windows = trend.get("windows", {}) if trend.get("available") else {}
    quarter, week = windows.get("90") or {}, windows.get("7") or {}
    current = payload.get("current_price") or trend.get("current_price")
    if current and quarter.get("sufficient_data") and quarter.get("average"):
        gap = (current - quarter["average"]) / quarter["average"] * 100
        if abs(gap) >= 5:
            above = gap > 0
            reasons.append(Line(
                f"ಈಗಿನ ಬೆಲೆ ಕಳೆದ ಮೂರು ತಿಂಗಳ ಸರಾಸರಿಗಿಂತ {K.pct(gap)} {'ಹೆಚ್ಚಿದೆ' if above else 'ಕಡಿಮೆ ಇದೆ'}",
                f"आज का भाव पिछले तीन महीने के औसत से {H.pct(gap)} {'ज़्यादा' if above else 'कम'} है",
                f"today's price is {E.pct(gap)} {'above' if above else 'below'} the three-month average",
            ))
    if week.get("sufficient_data") and week.get("change_pct") is not None and abs(week["change_pct"]) >= 3:
        rising = week["change_pct"] > 0
        reasons.append(Line(
            f"ಕಳೆದ ವಾರದಿಂದ ಬೆಲೆ {'ಏರುತ್ತಿದೆ' if rising else 'ಇಳಿಯುತ್ತಿದೆ'}",
            f"पिछले हफ़्ते से भाव {'बढ़ रहा' if rising else 'घट रहा'} है",
            f"prices have been {'rising' if rising else 'falling'} this past week",
        ))

    if reasons:
        top = reasons[:3]
        parts.append(Line(
            "ಕಾರಣ, " + ", ".join(r.kn for r in top) + ".",
            "क्योंकि " + ", ".join(r.hi for r in top) + "।",
            "Because " + ", ".join(r.en for r in top) + ".",
        ))

    alt = payload.get("alternative_market")
    if decision == "COMPARE_MARKETS" and alt:
        am, adv = alt["market"], int(round(alt["advantage_per_quintal"]))
        parts.append(Line(
            f"{K.market(am)} ಮಾರುಕಟ್ಟೆಯಲ್ಲಿ ಸಾಗಣೆ ಖರ್ಚು ಕಳೆದೂ ಕ್ವಿಂಟಲ್‌ಗೆ ಸುಮಾರು {adv} ರೂಪಾಯಿ ಹೆಚ್ಚು ಸಿಗುತ್ತದೆ.",
            f"{H.market(am)} मंडी में भाड़ा काटकर भी लगभग {adv} रुपये प्रति क्विंटल ज़्यादा मिलेंगे।",
            f"{am} would give about ₹{adv} more per quintal even after transport.",
        ))

    price_range = payload.get("expected_price_range")
    if decision == "WAIT" and price_range:
        lo, hi_ = int(round(price_range["low"])), int(round(price_range["high"]))
        parts.append(Line(
            f"ಬೆಲೆ ನಿರೀಕ್ಷೆಯಂತೆ ಹೋದರೆ ಕ್ವಿಂಟಲ್‌ಗೆ {lo} ರಿಂದ {hi_} ರೂಪಾಯಿ ಸಿಗಬಹುದು, ಆದರೆ ಇದು ಖಚಿತವಲ್ಲ.",
            f"भाव उम्मीद के मुताबिक़ चला तो {lo} से {hi_} रुपये प्रति क्विंटल मिल सकते हैं, लेकिन यह पक्का नहीं है।",
            f"If prices move as expected you may get {E.rupees(lo)} to {E.rupees(hi_)} per quintal, "
            "but that is not certain.",
        ))

    confidence = payload.get("confidence")
    if K.confidence(confidence):
        parts.append(Line(
            f"ಈ ಸಲಹೆಯ ಖಚಿತತೆ {K.confidence(confidence)}.",
            f"इस सलाह पर भरोसा {H.confidence(confidence)} है।",
            f"My confidence in this is {E.confidence(confidence)}.",
        ))

    if decision in ("SELL_NOW", "COMPARE_MARKETS", "SELL_PARTIAL"):
        parts.append(Line(
            "ಖರೀದಿಸುವ ಪರಿಶೀಲಿತ ವ್ಯಾಪಾರಿಗಳನ್ನು ಹುಡುಕಬೇಕಾ?",
            "क्या सत्यापित ख़रीदार ढूँढूँ?",
            "Shall I find verified buyers?",
        ))
        _suggest(state, "FIND_BUYERS")

    return _reply(state, _join(parts), "SELL_OR_WAIT", payload.get("data_quality"), uses_market_data=True)


# ---------------------------------------------------------------------------
# Market linkage
# ---------------------------------------------------------------------------

def _trust_line(badge: Dict[str, Any]) -> Line:
    band = (badge or {}).get("trust_band")
    if band == "STRONG":
        return Line("ಇವರ ಹಣ ಪಾವತಿ ದಾಖಲೆ ಚೆನ್ನಾಗಿದೆ.", "इनका भुगतान रिकॉर्ड अच्छा है।",
                    "They have a strong payment record.")
    if band == "NEW":
        return Line("ಇವರು ಪರಿಶೀಲಿತರು, ಆದರೆ ಇಲ್ಲಿ ಇನ್ನೂ ವ್ಯಾಪಾರ ಮಾಡಿಲ್ಲ.",
                    "ये सत्यापित हैं, लेकिन यहाँ अभी तक कोई सौदा नहीं किया है।",
                    "They are verified but have not traded here before.")
    if band in ("WEAK", "POOR"):
        return Line("ಇವರ ಪಾವತಿ ದಾಖಲೆ ದುರ್ಬಲ, ಮುಂಗಡ ಹಣ ಕೇಳಿ.",
                    "इनका भुगतान रिकॉर्ड कमज़ोर है, पहले से पैसे माँगिए।",
                    "Their payment record is weak; ask for advance payment.")
    return Line("ಇವರು ಪರಿಶೀಲಿತ ವ್ಯಾಪಾರಿ.", "ये सत्यापित व्यापारी हैं।", "They are a verified buyer.")


_OFFER_TO_LIST = Line(
    "ನಿಮ್ಮ ಸರಕನ್ನು ಮಾರಾಟಕ್ಕೆ ಪಟ್ಟಿ ಮಾಡಿದರೆ ವ್ಯಾಪಾರಿಗಳು ನೋಡಿ ಆಫರ್ ಕಳುಹಿಸಬಹುದು. ಪಟ್ಟಿ ಮಾಡಬೇಕಾ?",
    "अपना माल बिक्री के लिए डालेंगे तो ख़रीदार देखकर ऑफ़र भेज सकते हैं। डाल दूँ?",
    "If you list your produce, buyers can see it and send offers. Shall I list it?",
)


def _buyers(state: DialogueState, u: Understanding) -> Reply:
    from app.trade import buyers, lots

    if not state.commodity:
        return _ask_commodity(state, "FIND_BUYERS")

    c = state.commodity
    demand = []
    for row in buyers.list_open_demand(commodity=c, verified_only=True, limit=50):
        buyer = row.get("buyer") or buyers.get_buyer(row["buyer_id"]) or {}
        badge = buyer.get("badge") or {}
        if badge.get("safe_to_recommend"):
            demand.append((row, buyer, badge))
    demand.sort(key=lambda item: item[0].get("price_offered") or 0, reverse=True)

    has_listing = bool(state.phone) and any(
        l["status"] in ("PUBLISHED", "MATCHED", "OFFER_RECEIVED")
        for l in lots.list_lots(commodity=c, farmer_phone=state.phone, limit=20)
    )

    if not demand:
        parts = [Line(
            f"ಈಗ {K.crop(c)} ಖರೀದಿಸಲು ಯಾವುದೇ ಪರಿಶೀಲಿತ ವ್ಯಾಪಾರಿಯ ಬೇಡಿಕೆ ಇಲ್ಲ.",
            f"अभी {H.crop(c)} के लिए किसी सत्यापित ख़रीदार की माँग नहीं है।",
            f"There is no open demand for {c} from verified buyers right now.",
        )]
        if not has_listing:
            parts.append(_OFFER_TO_LIST)
            _suggest(state, "LIST_FOR_SALE")
        return _reply(state, _join(parts), "FIND_BUYERS")

    row, buyer, badge = demand[0]
    need_kg = row.get("quantity_remaining_kg") or row.get("quantity_needed_kg")
    place = row.get("delivery_district") or buyer.get("district") or ""
    name = buyer.get("name", "")
    count = len(demand)
    terms = row.get("payment_terms_days") or 7

    parts = [Line(
        f"{K.crop(c)} ಖರೀದಿಸಲು {count} ಪರಿಶೀಲಿತ ವ್ಯಾಪಾರಿಗಳ ಬೇಡಿಕೆ ಇದೆ.",
        f"{H.crop(c)} ख़रीदने के लिए " + (
            "एक सत्यापित ख़रीदार की माँग है।" if count == 1 else f"{count} सत्यापित ख़रीदारों की माँग है।"
        ),
        f"{count} verified buyer{'s' if count != 1 else ''} want {c}.",
    ), Line(
        f"ಅತಿ ಹೆಚ್ಚು ಬೆಲೆ ಕೊಡುವವರು {name}" + (f", {K.market(place)}" if place else "") + ".",
        f"सबसे अच्छा भाव {name}" + (f", {H.market(place)}" if place else "") + " दे रहे हैं।",
        f"The best price is from {name}" + (f" in {place}" if place else "") + ".",
    )]

    need = Line(f"ಅವರಿಗೆ {K.qty(need_kg)} ಬೇಕು", f"उन्हें {H.qty(need_kg)} चाहिए", f"They need {E.qty(need_kg)}")
    if row.get("price_offered"):
        p = row["price_offered"]
        need = need + Line(f", ಕ್ವಿಂಟಲ್‌ಗೆ {K.rupees(p)} ಕೊಡುತ್ತಾರೆ", f", {H.rupees(p)} प्रति क्विंटल देंगे",
                           f" at {E.rupees(p)} per quintal")
    if row.get("min_grade") in ("A", "B", "C"):
        g = row["min_grade"]
        need = need + Line(f", {K.grade(g)} ಸರಕು ಬೇಕು", f", {H.grade(g)} का माल चाहिए", f", {E.grade(g)} or better")
    need = need + Line(f", {K.days(terms)}ದಲ್ಲಿ ಹಣ ಪಾವತಿ.", f", {H.days(terms)} में भुगतान।",
                       f", paying within {E.days(terms)}.")
    # Pieces above begin with a comma; rejoin them without the separating space.
    need = Line(need.kn.replace(" ,", ","), need.hi.replace(" ,", ","), need.en.replace(" ,", ","))
    parts.extend([need, _trust_line(badge)])

    if has_listing:
        parts.append(Line(
            "ನಿಮ್ಮ ಸರಕು ಈಗಾಗಲೇ ಪಟ್ಟಿಯಲ್ಲಿದೆ. ಬಂದಿರುವ ಆಫರ್‌ಗಳನ್ನು ಕೇಳಬೇಕಾ?",
            "आपका माल पहले से सूची में है। आए हुए ऑफ़र सुनाऊँ?",
            "Your produce is already listed. Shall I read your offers?",
        ))
        _suggest(state, "MY_OFFERS")
    else:
        parts.append(Line(
            "ನಿಮ್ಮ ಸರಕನ್ನು ಮಾರಾಟಕ್ಕೆ ಪಟ್ಟಿ ಮಾಡಿದರೆ ಈ ವ್ಯಾಪಾರಿಗಳು ನಿಮಗೆ ಆಫರ್ ಕಳುಹಿಸಬಹುದು. ಪಟ್ಟಿ ಮಾಡಬೇಕಾ?",
            "अपना माल बिक्री के लिए डालेंगे तो ये ख़रीदार आपको ऑफ़र भेज सकते हैं। डाल दूँ?",
            "If you list your produce these buyers can send you offers. Shall I list it?",
        ))
        _suggest(state, "LIST_FOR_SALE")
    return _reply(state, _join(parts), "FIND_BUYERS", _demo_quality(), uses_market_data=True)


_NO_PHONE = Line(
    "ಕ್ಷಮಿಸಿ, ನಿಮ್ಮ ಮೊಬೈಲ್ ಸಂಖ್ಯೆ ಸಿಗಲಿಲ್ಲ.",
    "माफ़ कीजिए, आपका मोबाइल नंबर नहीं मिला।",
    "Sorry, your phone number is not available.",
)


def _list_for_sale(state: DialogueState, u: Understanding) -> Reply:
    if not state.phone:
        return _reply(state, _NO_PHONE, "LIST_FOR_SALE")
    if not state.commodity:
        return _ask_commodity(state, "LIST_FOR_SALE")
    if not state.quantity_kg:
        return _ask_quantity(state, "LIST_FOR_SALE")
    c = state.commodity
    if not state.grade:
        state.awaiting = "LIST_FOR_SALE"
        return _reply(state, Line(
            f"ನಿಮ್ಮ {K.crop(c)} ಗುಣಮಟ್ಟ ಹೇಗಿದೆ? ಉತ್ತಮ, ಮಧ್ಯಮ, ಅಥವಾ ಸಾಧಾರಣ?",
            f"आपके {H.crop(c)} की क्वालिटी कैसी है? अच्छी, मध्यम या साधारण?",
            f"How is the quality of your {c}: good, medium or ordinary?",
        ), "LIST_FOR_SALE")

    location = _location(state)
    qty, g = state.quantity_kg, state.grade
    _confirm(state, "LIST_FOR_SALE", commodity=c, quantity_kg=qty, grade=g, location=location)
    return _reply(state, Line(
        f"ಖಚಿತಪಡಿಸಿ: {K.qty(qty)} {K.grade(g)} {K.crop(c)}, ಸ್ಥಳ {K.market(location)}. "
        "ಮಾರಾಟಕ್ಕೆ ಪಟ್ಟಿ ಮಾಡಲೇ? ಹೌದು ಅಥವಾ ಬೇಡ ಅಂತ ಹೇಳಿ.",
        f"पक्का कीजिए: {H.qty(qty)} {H.grade(g)} {H.crop(c)}, जगह {H.market(location)}। "
        "बिक्री के लिए डाल दूँ? हाँ या नहीं बोलिए।",
        f"To confirm: list {E.qty(qty)} of {E.grade(g)} {c} from {location} for sale? Say yes or no.",
    ), "LIST_FOR_SALE")


def _do_list_for_sale(state: DialogueState, data: Dict[str, Any]) -> Reply:
    from app.activity import publish_activity_event
    from app.market.repository import resolve_origin
    from app.trade import lots, matching

    origin = resolve_origin(data["location"])
    lot = lots.create_lot(
        data["commodity"], float(data["quantity_kg"]),
        farmer_phone=state.phone, grade=data["grade"],
        quality_notes="Grade declared by the farmer on a voice call",
        pickup_district=origin.district if origin else data["location"],
        pickup_state=origin.state if origin else None,
        publish=True, actor=state.phone,
    )
    matches = matching.find_matches(lot, limit=5)
    publish_activity_event(
        "lot_listed_by_voice", "Produce listed by phone",
        f"{data['commodity']} | {E.qty(data['quantity_kg'])} | grade {data['grade']}",
        meta={"lot_id": lot["id"], "farmer_phone": state.phone, "matches": len(matches)},
    )

    c, qty = data["commodity"], data["quantity_kg"]
    parts = [Line(
        f"ಆಯಿತು. ನಿಮ್ಮ {K.qty(qty)} {K.crop(c)} ಮಾರಾಟಕ್ಕೆ ಪಟ್ಟಿ ಆಗಿದೆ, ನಿಮ್ಮ ಮೊಬೈಲ್ ಸಂಖ್ಯೆಗೆ ಜೋಡಿಸಲಾಗಿದೆ.",
        f"हो गया। आपका माल, {H.qty(qty)} {H.crop(c)}, बिक्री के लिए डाल दिया गया है और आपके मोबाइल नंबर से जुड़ा है।",
        f"Done. Your {E.qty(qty)} of {c} is listed for sale against your phone number.",
    )]
    if matches:
        n = len(matches)
        parts.append(Line(
            f"ಇದು {n} ಪರಿಶೀಲಿತ ವ್ಯಾಪಾರಿಗಳ ಬೇಡಿಕೆಗೆ ಹೊಂದುತ್ತದೆ.",
            f"यह {n} सत्यापित ख़रीदारों की माँग से मेल खाता है।",
            f"It matches demand from {n} verified buyer{'s' if n != 1 else ''}.",
        ))
        top = max((m for m in matches if m.price_offered), key=lambda m: m.price_offered, default=None)
        if top:
            p = top.price_offered
            parts.append(Line(
                f"ಅವರಲ್ಲಿ ಅತಿ ಹೆಚ್ಚು ಬೇಡಿಕೆ ಬೆಲೆ ಕ್ವಿಂಟಲ್‌ಗೆ {K.rupees(p)}.",
                f"उनमें सबसे ऊँचा भाव {H.rupees(p)} प्रति क्विंटल है।",
                f"The highest asking price among them is {E.rupees(p)} per quintal.",
            ))
    else:
        parts.append(Line(
            "ಈಗ ಹೊಂದುವ ಬೇಡಿಕೆ ಇಲ್ಲ, ಆದರೆ ಹೊಸ ವ್ಯಾಪಾರಿಗಳು ಇದನ್ನು ನೋಡಬಹುದು.",
            "अभी कोई माँग मेल नहीं खाती, लेकिन नए ख़रीदार इसे देख सकेंगे।",
            "No demand matches yet, but new buyers will be able to see it.",
        ))
    parts.append(Line(
        "ಆಫರ್ ಬಂದಿದೆಯಾ ತಿಳಿಯಲು ಈ ಸಂಖ್ಯೆಗೆ ಕರೆ ಮಾಡಿ, ನನ್ನ ಆಫರ್‌ಗಳು ಅಂತ ಕೇಳಿ.",
        "ऑफ़र आया या नहीं, जानने के लिए इसी नंबर पर कॉल करके पूछिए।",
        "To hear offers, call this number and ask for your offers.",
    ))
    state.grade = None
    return _reply(state, _join(parts), "LIST_FOR_SALE", _demo_quality(), uses_market_data=bool(matches))


def _my_offers(state: DialogueState, u: Understanding) -> Reply:
    from app.market import service
    from app.trade import lots, offers

    if not state.phone:
        return _reply(state, _NO_PHONE, "MY_OFFERS")

    my_lots = lots.list_lots(farmer_phone=state.phone, limit=50)
    if not my_lots:
        return _reply(state, Line(
            "ನಿಮ್ಮ ಹೆಸರಿನಲ್ಲಿ ಯಾವುದೇ ಸರಕು ಮಾರಾಟಕ್ಕೆ ಪಟ್ಟಿ ಆಗಿಲ್ಲ. ಪಟ್ಟಿ ಮಾಡಲು, ಉದಾಹರಣೆಗೆ "
            "ಇಪ್ಪತ್ತು ಕ್ವಿಂಟಲ್ ಈರುಳ್ಳಿ ಮಾರಬೇಕು ಅಂತ ಹೇಳಿ.",
            "आपके नाम पर कोई माल बिक्री के लिए नहीं डाला गया है। डालने के लिए बोलिए, जैसे "
            "बीस क्विंटल प्याज बेचना है।",
            "You have no produce listed. To list, say something like: I want to sell twenty quintal onion.",
        ), "MY_OFFERS")

    lot_by_id = {l["id"]: l for l in my_lots}
    open_offers = []
    for lot in my_lots:
        if state.commodity and lot["commodity"] != state.commodity:
            continue
        open_offers.extend(offers.list_offers(lot_id=lot["id"], status="OPEN"))
    if not open_offers and state.commodity:
        for lot in my_lots:
            open_offers.extend(offers.list_offers(lot_id=lot["id"], status="OPEN"))

    if not open_offers:
        return _reply(state, Line(
            "ನಿಮ್ಮ ಪಟ್ಟಿ ಮಾಡಿದ ಸರಕಿಗೆ ಇನ್ನೂ ಯಾವುದೇ ಹೊಸ ಆಫರ್ ಬಂದಿಲ್ಲ.",
            "आपके डाले हुए माल पर अभी तक कोई नया ऑफ़र नहीं आया है।",
            "No new offers have come in for your listed produce yet.",
        ), "MY_OFFERS", _demo_quality())

    best = max(open_offers, key=lambda o: o["price_per_quintal"])
    lot = lot_by_id.get(best["lot_id"], {})
    c = lot.get("commodity") or state.commodity or ""
    buyer = best.get("buyer") or {}
    name = buyer.get("name") or ""
    count = len(open_offers)
    p, qty, total, terms = best["price_per_quintal"], best["quantity_kg"], best["gross_value"], best["payment_terms_days"]

    parts = [Line(
        ("ನಿಮ್ಮ ಸರಕಿಗೆ ಒಂದು ಆಫರ್ ಬಂದಿದೆ." if count == 1 else f"ನಿಮ್ಮ ಸರಕಿಗೆ {count} ಆಫರ್ ಬಂದಿವೆ.")
        + f" ಅತಿ ಹೆಚ್ಚಿನದು {name or 'ಒಬ್ಬ ವ್ಯಾಪಾರಿ'} ಅವರದು: {K.qty(qty)} {K.crop(c)}, ಕ್ವಿಂಟಲ್‌ಗೆ "
        f"{K.rupees(p)}, ಒಟ್ಟು {K.about(total)}, {K.days(terms)}ದಲ್ಲಿ ಪಾವತಿ.",
        ("आपके माल पर एक ऑफ़र आया है।" if count == 1 else f"आपके माल पर {count} ऑफ़र आए हैं।")
        + f" सबसे ऊँचा {name or 'एक व्यापारी'} का है: {H.qty(qty)} {H.crop(c)}, {H.rupees(p)} प्रति "
        f"क्विंटल, कुल {H.about(total)}, {H.days(terms)} में भुगतान।",
        f"You have {count} offer{'s' if count != 1 else ''}. The highest is from {name or 'a buyer'}: "
        f"{E.rupees(p)} per quintal for {E.qty(qty)} of {c}, {E.about(total)} in total, paid within "
        f"{E.days(terms)}.",
    ), _trust_line(buyer.get("badge") or {})]

    quality = _demo_quality()
    market = service.get_price(c, lot.get("pickup_district") or _location(state)) if c else {}
    if market.get("available"):
        quality = market.get("data_quality") or quality
        gap = p - market["modal_price"]
        if abs(gap) >= 20:
            n, mm, above = int(round(abs(gap))), market["market"], gap > 0
            parts.append(Line(
                f"ಇದು {K.market(mm)} ಮಾರುಕಟ್ಟೆಯ ಬೆಲೆಗಿಂತ ಕ್ವಿಂಟಲ್‌ಗೆ {n} ರೂಪಾಯಿ {'ಹೆಚ್ಚು' if above else 'ಕಡಿಮೆ'}.",
                f"यह {H.market(mm)} मंडी के भाव से {n} रुपये प्रति क्विंटल {'ज़्यादा' if above else 'कम'} है।",
                f"That is ₹{n} per quintal {'above' if above else 'below'} the {mm} market price.",
            ))

    parts.append(Line("ಈ ಆಫರ್ ಒಪ್ಪಿಕೊಳ್ಳಬೇಕಾ?", "क्या यह ऑफ़र मंज़ूर करूँ?", "Do you want to accept this offer?"))
    _confirm(state, "ACCEPT_OFFER", offer_id=best["id"], commodity=c)
    return _reply(state, _join(parts), "MY_OFFERS", quality, uses_market_data=True)


def _do_accept_offer(state: DialogueState, data: Dict[str, Any]) -> Reply:
    from app.activity import publish_activity_event
    from app.trade import offers

    try:
        result = offers.accept_offer(data["offer_id"], actor=state.phone, actor_role="farmer")
    except offers.OfferError as exc:
        logger.info("Voice acceptance refused: %s", exc)
        return _reply(state, Line(
            "ಕ್ಷಮಿಸಿ, ಈ ಆಫರ್ ಈಗ ಲಭ್ಯವಿಲ್ಲ. ಅದರ ಅವಧಿ ಮುಗಿದಿರಬಹುದು. ಬೇರೆ ಆಫರ್‌ಗಳನ್ನು ಕೇಳಬೇಕಾ?",
            "माफ़ कीजिए, यह ऑफ़र अब उपलब्ध नहीं है, शायद इसकी मियाद ख़त्म हो गई। दूसरे ऑफ़र सुनाऊँ?",
            "Sorry, that offer is no longer available; it may have expired. Shall I check other offers?",
        ), "ACCEPT_OFFER")

    payment = result.get("payment") or {}
    offer = result.get("offer") or {}
    publish_activity_event(
        "offer_accepted_by_voice", "Offer accepted by phone",
        f"{data.get('commodity', '')} | ₹{offer.get('price_per_quintal')}/qtl",
        meta={"offer_id": data["offer_id"], "payment_id": payment.get("id"), "farmer_phone": state.phone},
    )
    amount = payment.get("amount_rupees") or offer.get("gross_value") or 0
    return _reply(state, Line(
        f"ಆಫರ್ ಒಪ್ಪಿಕೊಳ್ಳಲಾಗಿದೆ. ಒಟ್ಟು {K.about(amount)} ನಿಮಗೆ ಬರಬೇಕು. ಒಂದು ಮುಖ್ಯ ಸಲಹೆ: "
        "ವ್ಯಾಪಾರಿ ಹಣವನ್ನು ಸುರಕ್ಷಿತ ಖಾತೆಯಲ್ಲಿ ಜಮಾ ಮಾಡಿದ ನಂತರವೇ ಸರಕು ಕಳುಹಿಸಿ. ಹಣ ಜಮಾ ಆಗಿದೆಯಾ ಅಂತ "
        "ಈ ಸಂಖ್ಯೆಗೆ ಕರೆ ಮಾಡಿ ಕೇಳಬಹುದು.",
        f"ऑफ़र मंज़ूर हो गया। आपको कुल {H.about(amount)} मिलने हैं। एक ज़रूरी सलाह: व्यापारी जब "
        "पैसा सुरक्षित खाते में जमा कर दे, तभी माल भेजिए। पैसा जमा हुआ या नहीं, इसी नंबर पर कॉल "
        "करके पूछ सकते हैं।",
        f"Offer accepted. You are due {E.about(amount)}. Important: send the produce only after the "
        "buyer has deposited the money in the secure escrow account. You can call this number to "
        "check whether it has been deposited.",
    ), "ACCEPT_OFFER", _demo_quality())


# ---------------------------------------------------------------------------
# Settlement
# ---------------------------------------------------------------------------

def _payment_line(status: str, amount: float) -> Line:
    ka, ha, ea = K.about(amount), H.about(amount), E.about(amount)
    lines = {
        "PENDING": Line(
            "ವ್ಯಾಪಾರಿ ಇನ್ನೂ ಹಣ ಕಳುಹಿಸಿಲ್ಲ. ಹಣ ಸುರಕ್ಷಿತ ಖಾತೆಗೆ ಬರುವವರೆಗೆ ಸರಕು ಕಳುಹಿಸಬೇಡಿ.",
            "व्यापारी ने अभी पैसा नहीं भेजा है। पैसा सुरक्षित खाते में आने तक माल मत भेजिए।",
            "The buyer has not sent the money yet. Do not dispatch until it reaches escrow."),
        "INITIATED": Line(
            "ವ್ಯಾಪಾರಿ ಹಣ ಕಳುಹಿಸಲು ಶುರು ಮಾಡಿದ್ದಾರೆ, ಇನ್ನೂ ಸುರಕ್ಷಿತ ಖಾತೆಗೆ ಜಮಾ ಆಗಿಲ್ಲ.",
            "व्यापारी ने पैसा भेजना शुरू किया है, अभी सुरक्षित खाते में जमा नहीं हुआ है।",
            "The buyer has started the payment, but it has not reached escrow yet."),
        "ESCROW": Line(
            f"{ka} ಸುರಕ್ಷಿತ ಖಾತೆಯಲ್ಲಿ ಜಮಾ ಆಗಿದೆ. ಈಗ ಸರಕು ಕಳುಹಿಸಬಹುದು. ಸರಕು ತಲುಪಿದ ಮೇಲೆ ಹಣ ನಿಮ್ಮ ಖಾತೆಗೆ ಬರುತ್ತದೆ.",
            f"{ha} सुरक्षित खाते में जमा हो गए हैं। अब माल भेज सकते हैं। माल पहुँचने पर पैसा आपके खाते में आएगा।",
            f"{ea[0].upper() + ea[1:]} is held in escrow. You can dispatch now; it is released to you on delivery."),
        "DELIVERY_CONFIRMED": Line(
            f"ಸರಕು ತಲುಪಿರುವುದು ದೃಢಪಟ್ಟಿದೆ. {ka} ಬಿಡುಗಡೆ ಪ್ರಕ್ರಿಯೆಯಲ್ಲಿದೆ.",
            f"माल पहुँचना पक्का हो गया है। {ha} जारी होने की प्रक्रिया में हैं।",
            f"Delivery is confirmed. {ea[0].upper() + ea[1:]} is being released."),
        "RELEASE_PENDING": Line(
            f"ಸರಕು ತಲುಪಿದೆ. {ka} ಬಿಡುಗಡೆ ಪ್ರಕ್ರಿಯೆಯಲ್ಲಿದೆ.",
            f"माल पहुँच गया है। {ha} जारी होने की प्रक्रिया में हैं।",
            f"Delivered. {ea[0].upper() + ea[1:]} is being released."),
        "RELEASED": Line(
            f"{ka} ನಿಮ್ಮ ಖಾತೆಗೆ ಬಿಡುಗಡೆ ಆಗಿದೆ.",
            f"{ha} आपके खाते में जारी हो गए हैं।",
            f"{ea[0].upper() + ea[1:]} has been released to you."),
        "DISPUTED": Line(
            "ಈ ಪಾವತಿಯ ಮೇಲೆ ದೂರು ಇದೆ, ಹಣ ತಡೆಹಿಡಿಯಲಾಗಿದೆ. ದೂರು ಬಗೆಹರಿದ ನಂತರ ಬಿಡುಗಡೆ ಆಗುತ್ತದೆ.",
            "इस भुगतान पर शिकायत है, पैसा रोका गया है। शिकायत सुलझने के बाद जारी होगा।",
            "This payment is under dispute and frozen until it is resolved."),
        "FAILED": Line(
            "ಪಾವತಿ ವಿಫಲವಾಗಿದೆ. ವ್ಯಾಪಾರಿ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಬೇಕು. ಅಲ್ಲಿಯವರೆಗೆ ಸರಕು ಕಳುಹಿಸಬೇಡಿ.",
            "भुगतान नाकाम रहा। व्यापारी को दोबारा कोशिश करनी होगी। तब तक माल मत भेजिए।",
            "The payment failed and the buyer must retry. Do not dispatch until then."),
        "REFUNDED": Line(
            "ಈ ವ್ಯವಹಾರ ರದ್ದಾಗಿದೆ, ಹಣ ವ್ಯಾಪಾರಿಗೆ ಹಿಂತಿರುಗಿಸಲಾಗಿದೆ.",
            "यह सौदा रद्द हो गया, पैसा व्यापारी को लौटा दिया गया।",
            "This deal was cancelled and the money refunded to the buyer."),
    }
    return lines.get(status, lines["PENDING"])


def _payment(state: DialogueState, u: Understanding) -> Reply:
    from app.trade import buyers, lots, payments

    records = payments.list_payments(payee_phone=state.phone, limit=20) if state.phone else []
    if not records:
        return _reply(state, Line(
            "ನಿಮ್ಮ ಹೆಸರಿನಲ್ಲಿ ಯಾವುದೇ ಹಣ ಪಾವತಿ ದಾಖಲೆ ಇಲ್ಲ. ಆಫರ್ ಒಪ್ಪಿಕೊಂಡ ನಂತರ ಪಾವತಿ ಇಲ್ಲಿ ಕಾಣಿಸುತ್ತದೆ.",
            "आपके नाम पर कोई भुगतान रिकॉर्ड नहीं है। ऑफ़र मंज़ूर करने के बाद भुगतान यहाँ दिखेगा।",
            "There are no payments in your name. A payment appears here once you accept an offer.",
        ), "PAYMENT_STATUS")

    all_active = [p for p in records if not p["is_terminal"]]
    if u.commodity:
        named = [p for p in records if (lots.get_lot(p["lot_id"]) or {}).get("commodity") == u.commodity]
        records = named or records
    active = [p for p in records if not p["is_terminal"]]
    record = active[0] if active else records[0]
    lot = lots.get_lot(record["lot_id"]) or {}
    buyer = buyers.get_buyer(record["buyer_id"]) or {}
    c = lot.get("commodity") or ""
    name = buyer.get("name")

    parts = [Line(
        f"{name or 'ವ್ಯಾಪಾರಿ'} ಅವರೊಂದಿಗಿನ {K.crop(c)} ವ್ಯವಹಾರ:",
        f"{name or 'व्यापारी'} के साथ {H.crop(c)} का सौदा:",
        f"Your {c} deal with {name or 'the buyer'}:",
    ), _payment_line(record["status"], record["amount_rupees"])]

    if record.get("is_overdue"):
        days = record.get("days_overdue") or 0
        parts.append(Line(
            f"ಪಾವತಿ {K.days(days)} ತಡವಾಗಿದೆ. ದೂರು ದಾಖಲಿಸಬೇಕಾ?",
            f"भुगतान {H.days(days)} देर से है। शिकायत दर्ज करूँ?",
            f"The payment is {E.days(days)} overdue. Do you want to file a complaint?",
        ))
        state.complaint_category = "PAYMENT"
        _suggest(state, "RAISE_COMPLAINT")

    others = sum(1 for p in all_active if p["id"] != record["id"])
    if others == 1:
        parts.append(Line("ಇನ್ನೂ ಒಂದು ಪಾವತಿ ಬಾಕಿ ಇದೆ.", "एक और भुगतान बाकी है।", "One more payment is pending."))
    elif others > 1:
        parts.append(Line(f"ಇನ್ನೂ {others} ಪಾವತಿಗಳು ಬಾಕಿ ಇವೆ.", f"{others} और भुगतान बाकी हैं।",
                          f"{others} more payments are pending."))

    return _reply(state, _join(parts), "PAYMENT_STATUS", _demo_quality())


def _complaint(state: DialogueState, u: Understanding) -> Reply:
    from app.trade import lots, payments

    if not state.phone:
        return _reply(state, _NO_PHONE, "RAISE_COMPLAINT")

    records = payments.list_payments(payee_phone=state.phone, limit=20)
    committed = [
        l for l in lots.list_lots(farmer_phone=state.phone, exclude_aggregated=False, limit=50)
        if l.get("accepted_offer_id")
    ]
    if not records and not committed:
        return _reply(state, Line(
            "ದೂರು ದಾಖಲಿಸಲು ನಿಮ್ಮ ಹೆಸರಿನಲ್ಲಿ ಯಾವುದೇ ವ್ಯವಹಾರ ಸಿಗಲಿಲ್ಲ. ಮಂಡಿಯಲ್ಲಿ ನೇರವಾಗಿ ನಡೆದ "
            "ವ್ಯವಹಾರಕ್ಕೆ ನಿಮ್ಮ ಎಪಿಎಂಸಿ ಕಾರ್ಯದರ್ಶಿಗೆ ದೂರು ನೀಡಿ.",
            "शिकायत दर्ज करने के लिए आपके नाम पर कोई सौदा नहीं मिला। मंडी में सीधे हुए सौदे के लिए "
            "अपने एपीएमसी सचिव से शिकायत कीजिए।",
            "I found no deal in your name to complain about. For a sale made directly at the mandi, "
            "complain to your APMC secretary.",
        ), "RAISE_COMPLAINT")

    if not state.complaint_category:
        state.awaiting = "RAISE_COMPLAINT"
        return _reply(state, Line(
            "ಏನು ತೊಂದರೆ ಆಗಿದೆ? ಹಣ ಬಂದಿಲ್ಲವೇ, ತೂಕದಲ್ಲಿ ಮೋಸವೇ, ಅಥವಾ ಗುಣಮಟ್ಟದ ಹೆಸರಿನಲ್ಲಿ ತಿರಸ್ಕಾರವೇ?",
            "क्या दिक्कत हुई? पैसा नहीं आया, तौल में धोखा हुआ, या क्वालिटी के नाम पर माल लौटाया गया?",
            "What went wrong: payment not received, weighing fraud, or rejection on quality?",
        ), "RAISE_COMPLAINT")

    category = state.complaint_category

    def lot_crop(payment: Dict[str, Any]) -> Optional[str]:
        return (lots.get_lot(payment["lot_id"]) or {}).get("commodity")

    candidates = [p for p in records if not p["is_terminal"]] or list(records)
    if u.commodity:
        candidates = [p for p in candidates if lot_crop(p) == u.commodity] or candidates
    # A payment complaint is about money that has not arrived; a weight or
    # quality complaint is about produce already handed over, where money is held.
    held = ("ESCROW", "DELIVERY_CONFIRMED", "RELEASE_PENDING")
    if category == "PAYMENT":
        candidates.sort(key=lambda p: (not p.get("is_overdue"), p["status"] in held))
    else:
        candidates.sort(key=lambda p: p["status"] not in held)
    target_payment = candidates[0] if candidates else None
    target_lot = lots.get_lot(target_payment["lot_id"]) if target_payment else committed[0]

    _confirm(
        state, "RAISE_COMPLAINT",
        lot_id=target_lot["id"], payment_id=target_payment["id"] if target_payment else None,
        payment_status=target_payment["status"] if target_payment else None,
        offer_id=target_lot.get("accepted_offer_id"), category=category,
        commodity=target_lot.get("commodity"),
    )
    c = target_lot.get("commodity") or ""
    return _reply(state, Line(
        f"ನಿಮ್ಮ {K.crop(c)} ವ್ಯವಹಾರದ ಬಗ್ಗೆ, {K.complaint(category)} ಎಂದು ದೂರು ದಾಖಲಿಸಲೇ? "
        "ಹೌದು ಅಥವಾ ಬೇಡ ಅಂತ ಹೇಳಿ.",
        f"आपके {H.crop(c)} के सौदे पर, {H.complaint(category)} की शिकायत दर्ज करूँ? हाँ या नहीं बोलिए।",
        f"File a complaint of {E.complaint(category)} on your {c} deal? Say yes or no.",
    ), "RAISE_COMPLAINT")


def _do_complaint(state: DialogueState, data: Dict[str, Any]) -> Reply:
    from app.activity import publish_activity_event
    from app.trade import disputes

    description = state.complaint_text or f"Complaint raised by phone: {E.complaint(data['category'])}"
    try:
        dispute = disputes.open_dispute(
            data["lot_id"], state.phone, "farmer", data["category"], description,
            offer_id=data.get("offer_id"), payment_id=data.get("payment_id"),
        )
    except disputes.DisputeError as exc:
        logger.info("Voice complaint refused: %s", exc)
        return _reply(state, Line(
            "ಈ ವ್ಯವಹಾರದ ಮೇಲೆ ಈಗಾಗಲೇ ದೂರು ದಾಖಲಾಗಿದೆ ಅಥವಾ ಈಗ ದೂರು ಸ್ವೀಕರಿಸಲು ಆಗುತ್ತಿಲ್ಲ.",
            "इस सौदे पर पहले से शिकायत दर्ज है, या अभी शिकायत नहीं ली जा सकती।",
            f"A complaint could not be filed on this deal: {exc}",
        ), "RAISE_COMPLAINT")

    publish_activity_event(
        "dispute_raised_by_voice", "Complaint filed by phone",
        f"{data.get('commodity', '')} | {data['category']}",
        meta={"dispute_id": dispute["id"], "lot_id": data["lot_id"], "farmer_phone": state.phone},
    )
    state.complaint_category = None
    state.complaint_text = None
    if data.get("payment_status") in ("ESCROW", "DELIVERY_CONFIRMED", "RELEASE_PENDING"):
        held = Line(
            "ಬಗೆಹರಿಯುವವರೆಗೆ ಹಣ ಸುರಕ್ಷಿತ ಖಾತೆಯಲ್ಲಿಯೇ ತಡೆಹಿಡಿಯಲಾಗುತ್ತದೆ.",
            "सुलझने तक पैसा सुरक्षित खाते में ही रोका जाएगा।",
            "The money is held in escrow until it is resolved.",
        )
    else:
        held = Line(
            "ಬಗೆಹರಿಯುವವರೆಗೆ ಸರಕು ಕಳುಹಿಸಬೇಡಿ.",
            "सुलझने तक माल मत भेजिए।",
            "Do not dispatch any produce until it is resolved.",
        )
    return _reply(state, _join([
        Line("ನಿಮ್ಮ ದೂರು ದಾಖಲಾಗಿದೆ. ಅಧಿಕಾರಿ ಪರಿಶೀಲಿಸುತ್ತಾರೆ.", "आपकी शिकायत दर्ज हो गई है। अधिकारी जाँच करेंगे।",
             "Your complaint is filed and an officer will review it."),
        held,
        Line("ದೂರಿನ ಸ್ಥಿತಿ ತಿಳಿಯಲು ಈ ಸಂಖ್ಯೆಗೆ ಮತ್ತೆ ಕರೆ ಮಾಡಬಹುದು.",
             "शिकायत की स्थिति जानने के लिए इसी नंबर पर फिर कॉल कर सकते हैं।",
             "You can call this number to check on it."),
    ]), "RAISE_COMPLAINT")


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _fpo(state: DialogueState, u: Understanding) -> Reply:
    from app.trade import fpo

    memberships = fpo.get_farmer_fpos(state.phone) if state.phone else []
    if not memberships:
        return _reply(state, Line(
            "ನೀವು ಇನ್ನೂ ಯಾವುದೇ ರೈತ ಉತ್ಪಾದಕ ಸಂಸ್ಥೆಯ ಸದಸ್ಯರಲ್ಲ. ಹತ್ತಿರದ ಎಫ್‌ಪಿಒ ಸೇರಿದರೆ, ಇತರ ರೈತರ "
            "ಸರಕಿನೊಂದಿಗೆ ಒಂದೇ ಲಾರಿಯಲ್ಲಿ ಕಳುಹಿಸಿ ಸಾಗಣೆ ಖರ್ಚು ಉಳಿಸಬಹುದು ಮತ್ತು ದೊಡ್ಡ ವ್ಯಾಪಾರಿಗಳನ್ನು ತಲುಪಬಹುದು.",
            "आप अभी किसी किसान उत्पादक संगठन के सदस्य नहीं हैं। पास के एफ़पीओ से जुड़ेंगे तो दूसरे "
            "किसानों के माल के साथ एक ही गाड़ी में भेजकर भाड़ा बचा सकते हैं और बड़े ख़रीदारों तक पहुँच सकते हैं।",
            "You are not a member of any farmer producer organisation yet. Joining a nearby FPO lets you "
            "ship with other farmers in one truck, cutting transport cost and reaching bigger buyers.",
        ), "FPO_POOLING")

    def mine(group) -> bool:
        return any(l.get("farmer_phone") == state.phone for l in group.lots)

    # Across every FPO the farmer belongs to, prefer a pool that contains the
    # farmer's own produce and actually has something to pool with.
    best = None
    for membership in memberships:
        org_id = membership.get("id")
        groups = fpo.find_aggregation_groups(org_id, u.commodity) if u.commodity else []
        groups = groups or fpo.find_aggregation_groups(org_id)
        for group in groups:
            rank = (mine(group), len(group.lots) > 1, group.total_kg)
            if best is None or rank > best[0]:
                best = (rank, membership, group)

    if best is None or len(best[2].lots) < 2:
        org = (best[1] if best else memberships[0]).get("name") or "FPO"
        return _reply(state, Line(
            f"{org} ಸಂಸ್ಥೆಯಲ್ಲಿ ನಿಮ್ಮ ಸರಕಿನೊಂದಿಗೆ ಒಟ್ಟುಗೂಡಿಸಲು ಈಗ ಬೇರೆ ಸದಸ್ಯರ ಹೊಂದುವ ಸರಕು ಇಲ್ಲ. "
            "ಅದೇ ಬೆಳೆ ಮತ್ತು ದರ್ಜೆಯ ಸರಕನ್ನು ಇತರರು ಪಟ್ಟಿ ಮಾಡಿದಾಗ ಒಟ್ಟಾಗಿ ಮಾರಬಹುದು.",
            f"{org} में आपके माल के साथ मिलाने के लिए अभी दूसरे सदस्यों का मेल खाता माल नहीं है। जब दूसरे "
            "सदस्य उसी फसल और क्वालिटी का माल डालेंगे, तब साथ बेच सकते हैं।",
            f"{org} has no other member produce to pool with yours right now. Pooling becomes "
            "possible when others list the same crop and grade.",
        ), "FPO_POOLING")

    _, org_row, group = best
    org = org_row.get("name") or "FPO"
    c = group.commodity
    benefit = fpo.estimate_pool_benefit_at_market(group, _location(state))
    if benefit is None:
        return _market_unavailable(state, "FPO_POOLING")
    gain, per_farmer = benefit["total_gain"], benefit["gain_per_farmer"]
    farmers, includes = group.farmer_count, mine(group)

    def produce(s) -> str:
        return " ".join(p for p in (s.qty(group.total_kg), s.grade(group.grade), s.crop(c)) if p)

    parts = [Line(
        f"{org} ಸಂಸ್ಥೆಯಲ್ಲಿ {'ನೀವೂ ಸೇರಿ ' if includes else ''}{farmers} ರೈತರು ಒಟ್ಟು {produce(K)} ಮಾರಾಟಕ್ಕೆ ಇಟ್ಟಿದ್ದಾರೆ.",
        f"{org} में {'आपको मिलाकर ' if includes else ''}"
        + ("एक किसान ने" if farmers == 1 else f"{farmers} किसानों ने")
        + f" कुल {produce(H)} बिक्री के लिए डाला है।",
        f"In {org}, {'including you, ' if includes else ''}{farmers} farmer{'s' if farmers != 1 else ''} "
        f"have listed {E.qty(group.total_kg)} of {' '.join(p for p in (E.grade(group.grade), c) if p)} in total.",
    )]
    if gain > 0:
        parts.append(Line(
            f"ಎಲ್ಲರೂ ಒಂದೇ ವಾಹನದಲ್ಲಿ ಕಳುಹಿಸಿದರೆ, ಬೇರೆ ಬೇರೆಯಾಗಿ ಕಳುಹಿಸುವುದಕ್ಕಿಂತ ಒಟ್ಟು {K.about(gain)} "
            f"ಹೆಚ್ಚು ಉಳಿಯುತ್ತದೆ, ಅಂದರೆ ಪ್ರತಿ ರೈತನಿಗೆ {K.about(per_farmer)}.",
            f"सब मिलकर एक ही गाड़ी में भेजें तो अलग-अलग भेजने के मुक़ाबले कुल {H.about(gain)} ज़्यादा "
            f"बचेंगे, यानी हर किसान को {H.about(per_farmer)}।",
            f"Shipping together in one vehicle keeps {E.about(gain)} more than shipping separately, "
            f"{E.about(per_farmer)} per farmer.",
        ))
    else:
        parts.append(Line(
            "ಈ ಪ್ರಮಾಣಕ್ಕೆ ಒಟ್ಟಾಗಿ ಕಳುಹಿಸಿದರೂ ಹೆಚ್ಚು ಉಳಿತಾಯ ಆಗುವುದಿಲ್ಲ.",
            "इतनी मात्रा पर साथ भेजने से ख़ास बचत नहीं होगी।",
            "At this volume, pooling would not save much.",
        ))
    parts.append(Line(
        "ಒಟ್ಟುಗೂಡಿಸುವ ನಿರ್ಧಾರವನ್ನು ನಿಮ್ಮ ಸಂಸ್ಥೆಯ ವ್ಯವಸ್ಥಾಪಕರು ಮಾಡುತ್ತಾರೆ.",
        "माल मिलाने का फ़ैसला आपके संगठन के प्रबंधक करते हैं।",
        "Your FPO manager makes the pooling decision.",
    ))
    return _reply(state, _join(parts), "FPO_POOLING", benefit.get("data_quality") or _demo_quality(),
                  uses_market_data=True)


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------

_HELP = Line(
    "ನಾನು ಈ ವಿಷಯಗಳಲ್ಲಿ ಸಹಾಯ ಮಾಡಬಲ್ಲೆ: ಬೆಳೆಯ ಇಂದಿನ ಬೆಲೆ, ಖರ್ಚು ಕಳೆದು ಯಾವ ಮಾರುಕಟ್ಟೆ ಉತ್ತಮ, "
    "ಈಗ ಮಾರಬೇಕಾ ಕಾಯಬೇಕಾ, ಪರಿಶೀಲಿತ ವ್ಯಾಪಾರಿಗಳು, ಸರಕನ್ನು ಮಾರಾಟಕ್ಕೆ ಪಟ್ಟಿ ಮಾಡುವುದು, ಮತ್ತು ನಿಮ್ಮ "
    "ಆಫರ್ ಹಾಗೂ ಹಣ ಪಾವತಿಯ ಸ್ಥಿತಿ. ಉದಾಹರಣೆಗೆ, ಕಲಬುರಗಿಯಲ್ಲಿ ಈರುಳ್ಳಿ ಬೆಲೆ ಎಷ್ಟು ಅಂತ ಕೇಳಿ.",
    "मैं इन बातों में मदद कर सकता हूँ: फसल का आज का भाव, ख़र्च काटकर कौन सी मंडी सबसे अच्छी, "
    "अभी बेचें या रुकें, सत्यापित ख़रीदार, माल बिक्री के लिए डालना, और आपके ऑफ़र व भुगतान की "
    "स्थिति। जैसे पूछिए, कलबुर्गी में प्याज का भाव क्या है।",
    "I can help with today's crop price, which market is best after costs, whether to sell now or "
    "wait, verified buyers, listing your produce for sale, and the status of your offers and "
    "payments. For example, ask: what is the onion price in Kalaburagi?",
)


def _help(state: DialogueState, u: Understanding) -> Reply:
    return _reply(state, _HELP, "HELP")


def _greeting(state: DialogueState, u: Understanding) -> Reply:
    return _reply(state, Line(
        "ನಮಸ್ಕಾರ! ಹೇಳಿ, ಯಾವ ಬೆಳೆಯ ಬಗ್ಗೆ ಮಾಹಿತಿ ಬೇಕು?",
        "नमस्ते! बताइए, किस फसल की जानकारी चाहिए?",
        "Hello! Tell me, which crop do you need information about?",
    ), "GREETING")


def _other(state: DialogueState, u: Understanding) -> Reply:
    composed = (
        compose_open_reply(u.text, u.english, state.history, state.language)
        if (u.text or u.english) else None
    )
    if composed:
        return Reply(composed[0], composed[1], state.language, "OTHER")
    return _reply(state, Line(
        "ಕ್ಷಮಿಸಿ, ಅದು ನನಗೆ ಸರಿಯಾಗಿ ಅರ್ಥವಾಗಲಿಲ್ಲ.", "माफ़ कीजिए, मैं ठीक से समझ नहीं पाया।",
        "Sorry, I did not quite understand that.",
    ) + _HELP, "OTHER")


_HANDLERS: Dict[str, Callable[[DialogueState, Understanding], Reply]] = {
    "PRICE": _price,
    "PRICE_TREND": _trend,
    "COMPARE_MARKETS": _compare,
    "SELL_OR_WAIT": _sell_or_wait,
    "FIND_BUYERS": _buyers,
    "LIST_FOR_SALE": _list_for_sale,
    "MY_OFFERS": _my_offers,
    "PAYMENT_STATUS": _payment,
    "RAISE_COMPLAINT": _complaint,
    "FPO_POOLING": _fpo,
    "HELP": _help,
    "GREETING": _greeting,
    "OTHER": _other,
}

_CONFIRMED_ACTIONS: Dict[str, Callable[[DialogueState, Dict[str, Any]], Reply]] = {
    "LIST_FOR_SALE": _do_list_for_sale,
    "ACCEPT_OFFER": _do_accept_offer,
    "RAISE_COMPLAINT": _do_complaint,
}


def record_history(state: DialogueState, farmer_text: str, farmer_en: str, reply: Reply) -> None:
    state.history.append({
        "farmer_text": farmer_text, "farmer_en": farmer_en,
        "reply_text": reply.text, "reply_en": reply.en,
        "intent": reply.intent, "language": reply.language,
    })
    del state.history[:-12]
