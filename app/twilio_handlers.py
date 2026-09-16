"""
Twilio webhooks for the voice agent: Kannada, Hindi and English.

    POST /twilio/voice          call answered: language menu or greeting, then listen
    POST /twilio/language       menu answered: remember the language, greet
    POST /twilio/gather         farmer spoke: start the turn, hold the line
    POST /twilio/answer         hold loop: play the answer once it is ready
    POST /twilio/status         call lifecycle
    POST /api/call              outbound "call me"
    POST /api/process-speech    text in, reply and audio out (integrations)

Choosing a language
-------------------
Speech recognition needs the language up front, so it cannot be detected from
the first sentence. A first-time caller hears a short menu in all three
languages (press 1/2/3, or say the language). The choice is stored against the
phone number, so later calls go straight to the greeting. A caller can switch
at any time by asking ("ಹಿಂದಿಯಲ್ಲಿ ಮಾತಾಡಿ", "English please"), and an outbound
call can set the language directly.

How a turn is delivered
-----------------------
A turn takes a few seconds. Twilio allows a webhook 15 seconds and a trial
account may not modify a live call, so /twilio/gather starts the turn on a
worker thread and returns a spoken hold plus a redirect; /twilio/answer is
polled until the reply is ready and plays it inside a <Gather>, so the farmer
can interrupt it. The worker must be a real thread: FastAPI BackgroundTasks run
inside the response cycle and would leave the caller in silence until Twilio
gave up with "an application error has occurred".
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
import urllib.parse
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response
from twilio.twiml.voice_response import Gather, VoiceResponse

from app.audio_manager import get_audio_url
from app.config import get_settings, get_twilio_client, twilio_configured
from app.security import (
    rate_limit_ai,
    rate_limit_calls,
    require_api_key,
    verify_twilio_signature,
)
from app.voice import call_log, dialogue, speech
from app.voice.language import (
    DEFAULT_LANGUAGE,
    MENU_HINTS,
    PROFILES,
    SUPPORTED,
    greeting_text,
    language_from_choice,
    normalise_language,
    speech_hints,
)
from app.voice.pipeline import run_turn

logger = logging.getLogger(__name__)

router = APIRouter()

TERMINAL_CALL_STATUSES = ("completed", "failed", "busy", "no-answer", "canceled")

# After this many silent listens in a row the agent says goodbye and hangs up,
# rather than looping forever on a phone left off the hook.
MAX_SILENT_TURNS = 2

# The language menu is repeated once; after that the default language is used.
MAX_MENU_ATTEMPTS = 2

# A caller parked longer than this hears an apology and is asked again.
ANSWER_MAX_WAIT_SECONDS = 40


def _is_real_twilio_call_sid(call_sid: str) -> bool:
    return bool(re.fullmatch(r"CA[0-9a-fA-F]{32}", (call_sid or "").strip()))


def _resolve_farmer_phone(from_number: str, to_number: str) -> str:
    """For outbound calls the farmer is `To`; for inbound calls, `From`."""
    from_n = (from_number or "").strip()
    to_n = (to_number or "").strip()
    if to_n.startswith("+91"):
        return to_n
    if from_n.startswith("+91"):
        return from_n
    settings = get_settings()
    if settings.TWILIO_PHONE_NUMBER and from_n == settings.TWILIO_PHONE_NUMBER:
        return to_n
    return from_n or to_n


def _twiml(response: VoiceResponse) -> Response:
    return Response(content=str(response), media_type="application/xml")


def _prompt_url(key: str, language: str) -> Optional[str]:
    name = speech.system_prompt_file(key, language)
    return get_audio_url(name) if name else None


def _listen(response: VoiceResponse, language: str, play_url: Optional[str] = None) -> VoiceResponse:
    """Play `play_url` (interruptible) and listen for speech in `language`."""
    settings = get_settings()
    language = normalise_language(language)
    gather = Gather(
        input="speech",
        action=f"{settings.BASE_URL}/twilio/gather",
        method="POST",
        language=PROFILES[language].stt_code,
        speech_timeout="auto",
        timeout=8,
        hints=speech_hints(language),
        action_on_empty_result=True,
    )
    if play_url:
        gather.play(play_url)
    response.append(gather)
    # Only reached if Twilio skips the action; treated as a silent turn.
    response.redirect(f"{settings.BASE_URL}/twilio/gather", method="POST")
    return response


def _language_menu(response: VoiceResponse, name: str = "") -> VoiceResponse:
    """Ask for a language: a key press or the spoken name, in all three languages."""
    settings = get_settings()
    action = f"{settings.BASE_URL}/twilio/language"
    if name:
        action += f"?name={urllib.parse.quote(name)}"
    gather = Gather(
        input="dtmf speech",
        num_digits=1,
        action=action,
        method="POST",
        language="en-IN",
        hints=MENU_HINTS,
        speech_timeout="auto",
        timeout=6,
        action_on_empty_result=True,
    )
    for language in SUPPORTED:
        url = _prompt_url("menu", language)
        if url:
            gather.play(url)
    response.append(gather)
    response.redirect(action, method="POST")
    return response


def _greet(response: VoiceResponse, language: str, name: str = "") -> VoiceResponse:
    greeting = None
    if name:
        # Only a greeting synthesised in advance (by /api/call) is used here;
        # generating one now would be seconds of dead air.
        cached = speech.cached(greeting_text(language, name), language, prefix="greet_")
        greeting = get_audio_url(cached) if cached else None
    return _listen(response, language, greeting or _prompt_url("greeting", language))


# ---------------------------------------------------------------------------
# Pending answers
# ---------------------------------------------------------------------------

_PENDING: Dict[str, Dict[str, Any]] = {}
_PENDING_LOCK = threading.Lock()


def _begin_answer(call_sid: str) -> None:
    with _PENDING_LOCK:
        _PENDING[call_sid] = {"status": "working", "created_at": time.time()}


def _update_answer(call_sid: str, **fields: Any) -> None:
    with _PENDING_LOCK:
        entry = _PENDING.setdefault(call_sid, {"created_at": time.time()})
        entry.update(fields)


def _peek_answer(call_sid: str) -> Optional[Dict[str, Any]]:
    with _PENDING_LOCK:
        entry = _PENDING.get(call_sid)
        return dict(entry) if entry else None


def _take_answer(call_sid: str) -> Optional[Dict[str, Any]]:
    with _PENDING_LOCK:
        return _PENDING.pop(call_sid, None)


def _answer_in_background(call_sid: str, phone: str, transcript: str) -> None:
    def work() -> None:
        try:
            result = run_turn(call_sid, phone, transcript)
            if not result.audio_url:
                raise RuntimeError("the reply could not be synthesised")
            _update_answer(
                call_sid, status="ready", audio_url=result.audio_url,
                hangup=result.hangup, language=result.language,
            )
        except Exception:
            logger.exception("Turn failed for %s", call_sid)
            _update_answer(call_sid, status="error")

    threading.Thread(target=work, name=f"turn-{call_sid[-6:]}", daemon=True).start()


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

@router.post("/twilio/voice", dependencies=[Depends(verify_twilio_signature)])
async def handle_incoming_call(
    request: Request,
    CallSid: str = Form(default=""),
    From: str = Form(default=""),
    To: str = Form(default=""),
):
    """Call connected: greet in the known language, or ask which language."""
    phone = _resolve_farmer_phone(From, To)
    state = dialogue.get_state(CallSid, phone)
    name = request.query_params.get("name", "").strip()

    # A caller's saved language used to skip the menu on repeat calls. Every
    # call now asks explicitly, English/Kannada/Hindi, so the caller always
    # confirms rather than being defaulted silently. An explicit `lang` query
    # param (operator-dialled outbound calls) still bypasses the menu.
    requested = normalise_language(request.query_params.get("lang"), default=None)
    language = requested
    logger.info("Call connected: %s (farmer %s, language %s)", CallSid, phone, language or "not chosen")

    # Preflight and launcher checks use made-up call SIDs; they are not calls.
    if _is_real_twilio_call_sid(CallSid):
        await asyncio.to_thread(call_log.start_call, CallSid, phone, "phone", language)

    if language:
        state.language = language
        return _twiml(_greet(VoiceResponse(), language, name))
    return _twiml(_language_menu(VoiceResponse(), name))


@router.post("/twilio/language", dependencies=[Depends(verify_twilio_signature)])
async def handle_language_choice(
    request: Request,
    CallSid: str = Form(default=""),
    Digits: str = Form(default=""),
    SpeechResult: str = Form(default=""),
    From: str = Form(default=""),
    To: str = Form(default=""),
):
    """The caller answered the language menu (or said nothing)."""
    phone = _resolve_farmer_phone(From, To)
    state = dialogue.get_state(CallSid, phone)
    name = request.query_params.get("name", "").strip()

    choice = language_from_choice(Digits, SpeechResult)
    if choice is None:
        state.menu_attempts += 1
        if state.menu_attempts < MAX_MENU_ATTEMPTS:
            return _twiml(_language_menu(VoiceResponse(), name))
        choice = DEFAULT_LANGUAGE
        logger.info("No language chosen on %s; using %s", CallSid, choice)
    else:
        logger.info("Caller on %s chose %s", CallSid, choice)
        if phone:
            await asyncio.to_thread(call_log.set_language_preference, phone, choice)

    state.language = choice
    if _is_real_twilio_call_sid(CallSid):
        await asyncio.to_thread(call_log.set_call_language, CallSid, choice)
    return _twiml(_greet(VoiceResponse(), choice, name))


@router.post("/twilio/gather", dependencies=[Depends(verify_twilio_signature)])
async def handle_gather(
    CallSid: str = Form(default=""),
    SpeechResult: str = Form(default=""),
    Confidence: str = Form(default=""),
    From: str = Form(default=""),
    To: str = Form(default=""),
):
    """The farmer spoke (or did not). Start the turn and hold the line."""
    started = time.time()
    phone = _resolve_farmer_phone(From, To)
    state = dialogue.get_state(CallSid, phone)
    language = state.language
    transcript = (SpeechResult or "").strip()
    settings = get_settings()

    if not transcript:
        state.silent_turns += 1
        logger.info("Silent turn %d on %s", state.silent_turns, CallSid)
        response = VoiceResponse()
        if state.silent_turns > MAX_SILENT_TURNS:
            goodbye = _prompt_url("goodbye", language)
            if goodbye:
                response.play(goodbye)
            response.hangup()
            return _twiml(response)
        return _twiml(_listen(response, language, _prompt_url("reprompt", language)))

    logger.info("Heard on %s [%s] (confidence %s): %s", CallSid, language, Confidence or "n/a", transcript)
    _begin_answer(CallSid)
    _answer_in_background(CallSid, phone, transcript)

    response = VoiceResponse()
    hold = _prompt_url("hold", language)
    if hold:
        response.play(hold)
    response.redirect(f"{settings.BASE_URL}/twilio/answer?attempt=1", method="POST")
    logger.info("Gather returned in %.3fs; caller on hold", time.time() - started)
    return _twiml(response)


@router.post("/twilio/answer", dependencies=[Depends(verify_twilio_signature)])
async def handle_answer(request: Request):
    """Hold loop. Plays the answer when ready, otherwise waits a moment and asks again."""
    settings = get_settings()
    form = await request.form()
    call_sid = str(form.get("CallSid", ""))
    try:
        attempt = int(request.query_params.get("attempt", "1"))
    except ValueError:
        attempt = 1

    entry = _peek_answer(call_sid)
    status = (entry or {}).get("status")
    language = dialogue.get_state(call_sid).language
    response = VoiceResponse()

    if status == "ready":
        _take_answer(call_sid)
        waited = time.time() - entry["created_at"]
        logger.info("Answer delivered to %s after %.1fs (poll %d)", call_sid, waited, attempt)
        # The turn may have switched language; listen in the one it ended in.
        language = entry.get("language") or language
        if entry.get("hangup"):
            response.play(entry["audio_url"])
            response.hangup()
            return _twiml(response)
        return _twiml(_listen(response, language, entry["audio_url"]))

    waited = time.time() - (entry or {}).get("created_at", time.time())
    if entry is None or status == "error" or waited > ANSWER_MAX_WAIT_SECONDS:
        _take_answer(call_sid)
        key = "timeout" if status == "working" else "error"
        logger.warning("No answer for %s (%s after %.0fs)", call_sid, status or "missing", waited)
        return _twiml(_listen(response, language, _prompt_url(key, language)))

    # Silence for several seconds reads as a dropped call, so a short
    # reassurance plays every other attempt instead of bare dead air.
    more = _prompt_url("hold_more", language) if attempt % 2 == 0 else None
    if more:
        response.play(more)
    else:
        response.pause(length=1)
    response.redirect(
        f"{settings.BASE_URL}/twilio/answer?attempt={attempt + 1}", method="POST"
    )
    return _twiml(response)


@router.post("/twilio/status", dependencies=[Depends(verify_twilio_signature)])
async def handle_call_status(
    CallSid: str = Form(default=""),
    CallStatus: str = Form(default=""),
    CallDuration: str = Form(default=""),
):
    logger.info("Call %s is %s%s", CallSid, CallStatus,
                f" ({CallDuration}s)" if CallDuration else "")
    if CallStatus in TERMINAL_CALL_STATUSES:
        dialogue.end_state(CallSid)
        _take_answer(CallSid)
        await asyncio.to_thread(call_log.end_call, CallSid, CallStatus)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Outbound call
# ---------------------------------------------------------------------------

@router.post("/api/call", dependencies=[Depends(require_api_key), Depends(rate_limit_calls)])
async def initiate_call(request: Request):
    """
    Call a farmer.

    Body: {"phone_number": "+91XXXXXXXXXX", "name": "Rajesh", "language": "kn" | "hi" | "en"}.
    `language` is optional: without it the farmer's saved language is used, or
    the language menu is played. On a Twilio trial account the number must be
    verified in the Twilio console.
    """
    settings = get_settings()
    body = await request.json()
    phone_number = str(body.get("phone_number") or body.get("phone") or "").strip()
    name = str(body.get("name") or "").strip()
    raw_language = body.get("language")
    language = normalise_language(raw_language, default=None)

    if not phone_number:
        raise HTTPException(status_code=400, detail="phone_number is required")
    if raw_language and language is None:
        raise HTTPException(status_code=400, detail="language must be kn, hi or en")
    if not phone_number.startswith("+"):
        phone_number = f"+91{phone_number[-10:]}"
    if not twilio_configured():
        raise HTTPException(status_code=500, detail="Twilio credentials are not configured")

    if language:
        await asyncio.to_thread(call_log.set_language_preference, phone_number, language)
    greeting_language = language or await asyncio.to_thread(call_log.get_language_preference, phone_number)

    params: Dict[str, str] = {}
    if name:
        params["name"] = name
    if language:
        params["lang"] = language
    voice_url = f"{settings.BASE_URL}/twilio/voice"
    if params:
        voice_url += "?" + urllib.parse.urlencode(params)

    if name and greeting_language:
        # Synthesised now, while the phone rings, so the greeting is instant.
        try:
            await speech.synthesize(greeting_text(greeting_language, name), greeting_language, prefix="greet_")
        except Exception as exc:
            logger.warning("Personal greeting unavailable for %s: %s", name, exc)

    try:
        client = get_twilio_client()
        call = await asyncio.to_thread(
            client.calls.create,
            to=phone_number,
            from_=settings.TWILIO_PHONE_NUMBER,
            url=voice_url,
            status_callback=f"{settings.BASE_URL}/twilio/status",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
        )
    except Exception as exc:
        logger.error("Could not place call to %s: %s", phone_number, exc)
        raise HTTPException(status_code=502, detail=f"Twilio refused the call: {exc}") from exc

    logger.info("Calling %s in %s: %s", phone_number, greeting_language or "menu", call.sid)
    return {
        "success": True, "call_sid": call.sid, "phone_number": phone_number,
        "language": greeting_language, "status": call.status,
    }


# ---------------------------------------------------------------------------
# Integrations
# ---------------------------------------------------------------------------

@router.post(
    "/api/process-speech",
    dependencies=[Depends(require_api_key), Depends(rate_limit_ai)],
)
async def process_speech_api(request: Request):
    """
    Run one turn from text, for integrations such as n8n.

    Body: {"text": "...", "language": "kn" | "hi" | "en", "phone": "+91...", "session_id": "optional"}.
    Turns with the same session_id share context, exactly like a phone call.
    """
    body = await request.json()
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    phone = str(body.get("phone") or "").strip()
    session = str(body.get("session_id") or "default")
    language = normalise_language(body.get("language"), default=None)

    result = await asyncio.to_thread(
        run_turn, f"API-{session}", phone, text, channel="api", language=language,
    )
    return {
        "success": True,
        "language": result.language,
        "input_text": result.farmer_text,
        "input_english": result.farmer_en,
        "intent": result.intent,
        "response_text": result.reply_text,
        "response_english": result.reply_en,
        "data_quality": result.data_quality,
        "audio_url": result.audio_url,
        "timings": result.timings,
    }
