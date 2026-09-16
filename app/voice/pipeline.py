"""
One conversational turn, end to end.

    transcript -> understand -> respond -> synthesise -> record

Blocking by design. It runs on a worker thread (the Twilio webhook hands it off
and returns TwiML immediately; the browser simulator runs it via
asyncio.to_thread), so network calls here never stall the server's event loop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.audio_manager import get_audio_url
from app.voice import call_log, dialogue, speech
from app.voice.language import normalise_language
from app.voice.nlu import understand

logger = logging.getLogger(__name__)


@dataclass
class TurnResult:
    call_sid: str
    turn_no: int
    language: str
    farmer_text: str
    farmer_en: str
    intent: str
    slots: Dict[str, Any]
    reply_text: str
    reply_en: str
    data_quality: Optional[str]
    hangup: bool
    audio_file: Optional[str]
    audio_url: Optional[str]
    nlu_source: str
    timings: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "call_sid": self.call_sid, "turn_no": self.turn_no, "language": self.language,
            "farmer_text": self.farmer_text, "farmer_en": self.farmer_en,
            "intent": self.intent, "slots": self.slots,
            "reply_text": self.reply_text, "reply_en": self.reply_en,
            "data_quality": self.data_quality, "hangup": self.hangup,
            "audio_url": self.audio_url, "nlu_source": self.nlu_source,
            "timings": self.timings,
        }


def run_turn(
    call_sid: str,
    phone: str,
    transcript: str,
    *,
    channel: str = "phone",
    language: Optional[str] = None,
    synthesize_audio: bool = True,
) -> TurnResult:
    """Understand, answer and voice one utterance. Never raises."""
    started = time.time()
    state = dialogue.get_state(call_sid, phone)
    if language:
        state.language = normalise_language(language)

    t0 = time.time()
    u = understand(transcript, context_question=state.last_question_en, language=state.language)
    nlu_ms = int((time.time() - t0) * 1000)
    logger.info(
        "[turn] %s [%s] intent=%s via %s in %dms slots=%s",
        call_sid, state.language, u.intent, u.source, nlu_ms, u.slots(),
    )

    t1 = time.time()
    reply = dialogue.respond(state, u)
    dialogue.record_history(state, transcript, u.english, reply)
    logic_ms = int((time.time() - t1) * 1000)

    # A farmer who switches language on a phone call keeps it for next time.
    if reply.intent == "CHANGE_LANGUAGE" and channel == "phone" and phone:
        try:
            call_log.set_language_preference(phone, reply.language)
            call_log.set_call_language(call_sid, reply.language)
        except Exception:
            logger.exception("Could not store language preference for %s", phone)

    audio_file = None
    tts_ms = 0
    if synthesize_audio:
        t2 = time.time()
        try:
            audio_file = asyncio.run(speech.synthesize(reply.text, reply.language))
        except Exception:
            logger.exception("Reply synthesis failed for %s", call_sid)
        tts_ms = int((time.time() - t2) * 1000)

    timings = {
        "nlu_ms": nlu_ms, "logic_ms": logic_ms, "tts_ms": tts_ms,
        "total_ms": int((time.time() - started) * 1000),
    }
    logger.info("[turn] %s answered %s in %dms (nlu %d, logic %d, tts %d)",
                call_sid, reply.intent, timings["total_ms"], nlu_ms, logic_ms, tts_ms)

    try:
        call_log.record_turn(
            call_sid=call_sid, phone=phone, turn_no=state.turn_no, language=reply.language,
            farmer_text=transcript, farmer_en=u.english, intent=reply.intent,
            slots=u.slots(), reply_text=reply.text, reply_en=reply.en,
            data_quality=reply.data_quality, nlu_source=u.source,
            timings=timings, audio_file=audio_file, channel=channel,
        )
    except Exception:
        logger.exception("Could not record turn for %s", call_sid)

    return TurnResult(
        call_sid=call_sid, turn_no=state.turn_no, language=reply.language,
        farmer_text=transcript, farmer_en=u.english, intent=reply.intent, slots=u.slots(),
        reply_text=reply.text, reply_en=reply.en, data_quality=reply.data_quality,
        hangup=reply.hangup, audio_file=audio_file,
        audio_url=get_audio_url(audio_file) if audio_file else None,
        nlu_source=u.source, timings=timings,
    )
