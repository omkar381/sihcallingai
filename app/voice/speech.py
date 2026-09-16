"""
Text to speech for the voice agent, in Kannada, Hindi and English.

1. edge-tts runs in-process. It used to be launched as `python -m edge_tts` in
   a subprocess for every reply, paying interpreter start-up on each turn.

2. Output is content-addressed. The file name is a hash of voice, rate and
   text, so an identical sentence is synthesised once and then served from
   disk. Fixed prompts (menu, greeting, hold, re-prompt, goodbye) and repeated
   answers become instant.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Dict, Optional, Tuple

from app.config import get_settings
from app.voice.language import DEFAULT_LANGUAGE, SUPPORTED, SYSTEM_PROMPTS, clean_for_speech, normalise_language

logger = logging.getLogger(__name__)

# Fixed prompts are kept by the audio cleaner; per-turn replies are not.
SYSTEM_PREFIX = "sys_"
REPLY_PREFIX = "tts_"
_SYSTEM_FILES: Dict[Tuple[str, str], str] = {}


def voice_for(language: str) -> str:
    settings = get_settings()
    return {
        "kn": settings.VOICE_TTS_VOICE,
        "hi": settings.VOICE_TTS_VOICE_HI,
        "en": settings.VOICE_TTS_VOICE_EN,
    }[normalise_language(language)]


def _file_name(prefix: str, text: str, language: str) -> str:
    key = f"{voice_for(language)}|{get_settings().VOICE_TTS_RATE}|{text}"
    return f"{prefix}{hashlib.sha256(key.encode('utf-8')).hexdigest()[:24]}.mp3"


async def _edge_tts(spoken: str, path: str, language: str) -> None:
    import edge_tts

    partial = path + ".part"
    try:
        communicate = edge_tts.Communicate(
            spoken, voice_for(language), rate=get_settings().VOICE_TTS_RATE
        )
        await communicate.save(partial)
        if os.path.getsize(partial) == 0:
            raise RuntimeError("edge-tts produced an empty file")
        os.replace(partial, path)
    finally:
        if os.path.exists(partial):
            try:
                os.unlink(partial)
            except OSError:
                pass


async def synthesize(
    text: str, language: str = DEFAULT_LANGUAGE, prefix: str = REPLY_PREFIX,
) -> Optional[str]:
    """Audio file name (inside AUDIO_DIR) for `text` spoken in `language`."""
    settings = get_settings()
    language = normalise_language(language)
    spoken = clean_for_speech(text, language)
    if not spoken:
        return None

    name = _file_name(prefix, spoken, language)
    path = os.path.join(settings.AUDIO_DIR, name)
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        try:
            os.utime(path, None)   # a cache hit is a use; keep it from being cleaned
        except OSError:
            pass
        return name

    os.makedirs(settings.AUDIO_DIR, exist_ok=True)
    # One request per reply. Splitting into sentences and synthesising them
    # concurrently was measured slower (5.9s against 3.9s for the same reply):
    # the service throttles parallel requests from one client.
    try:
        await _edge_tts(spoken, path, language)
        return name
    except Exception as exc:
        logger.warning("edge-tts failed for %s (%s)", language, exc)

    if language != "kn":
        return None
    from app.tts import generate_kannada_audio

    generated = generate_kannada_audio(spoken, audio_dir=settings.AUDIO_DIR)
    if not generated:
        return None
    try:
        os.replace(os.path.join(settings.AUDIO_DIR, generated), path)
        return name
    except OSError:
        return generated


def cached(text: str, language: str = DEFAULT_LANGUAGE, prefix: str = REPLY_PREFIX) -> Optional[str]:
    """File name if this exact text is already synthesised, without generating it."""
    language = normalise_language(language)
    spoken = clean_for_speech(text, language)
    if not spoken:
        return None
    name = _file_name(prefix, spoken, language)
    return name if os.path.isfile(os.path.join(get_settings().AUDIO_DIR, name)) else None


async def prepare_system_prompts() -> Dict[str, str]:
    """Synthesise every fixed prompt in every language once. Safe to call repeatedly."""
    for language in SUPPORTED:
        for key, text in SYSTEM_PROMPTS[language].items():
            name = await synthesize(text, language, prefix=SYSTEM_PREFIX)
            if name:
                _SYSTEM_FILES[(language, key)] = name
            else:
                logger.error("Could not prepare the %s '%s' voice prompt", language, key)
    logger.info("Voice prompts ready: %d across %s", len(_SYSTEM_FILES), ", ".join(SUPPORTED))
    return {f"{lang}:{key}": name for (lang, key), name in _SYSTEM_FILES.items()}


def system_prompt_file(key: str, language: str = DEFAULT_LANGUAGE) -> Optional[str]:
    """File name of a prepared prompt, recovering it from disk after a restart."""
    language = normalise_language(language)
    if (language, key) in _SYSTEM_FILES:
        return _SYSTEM_FILES[(language, key)]
    text = SYSTEM_PROMPTS[language].get(key)
    if not text:
        return None
    name = cached(text, language, prefix=SYSTEM_PREFIX)
    if name:
        _SYSTEM_FILES[(language, key)] = name
    return name
