"""
Whisper-based Speech-to-Text for Kannada audio.
Preloads model at startup for fast inference.
"""

import os
import io
import tempfile
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Global Whisper model — loaded once at startup
_whisper_model = None


def load_whisper_model(model_size: str = "base") -> None:
    """
    Load the Whisper model into memory.
    Called once during application startup (lifespan).
    """
    global _whisper_model
    try:
        import whisper
        logger.info(f"Loading Whisper model '{model_size}'...")
        _whisper_model = whisper.load_model(model_size)
        logger.info(f"Whisper model '{model_size}' loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to load Whisper model: {e}")
        raise RuntimeError(f"Whisper model failed to load: {e}")


def get_whisper_model():
    """Get the loaded Whisper model, raising an error if not loaded."""
    if _whisper_model is None:
        raise RuntimeError("Whisper model not loaded. Call load_whisper_model() first.")
    return _whisper_model


async def download_twilio_recording(recording_url: str, account_sid: str, auth_token: str) -> bytes:
    """
    Download audio recording from Twilio.
    Returns the raw audio bytes.
    """
    # Twilio recording URLs need .mp3 or .wav appended
    if not recording_url.endswith((".mp3", ".wav")):
        recording_url = recording_url + ".wav"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                recording_url,
                auth=(account_sid, auth_token),
                follow_redirects=True,
            )
            response.raise_for_status()
            logger.info(f"Downloaded Twilio recording: {len(response.content)} bytes")
            return response.content
    except Exception as e:
        logger.error(f"Failed to download Twilio recording: {e}")
        raise


def transcribe_audio(audio_bytes: bytes, language: str = "kn") -> str:
    """
    Transcribe audio bytes to text using Whisper.

    Args:
        audio_bytes: Raw audio data (WAV/MP3 format)
        language: Language code (kn = Kannada)

    Returns:
        Transcribed text string
    """
    model = get_whisper_model()

    # Write audio to a temp file (Whisper needs a file path)
    tmp_file = None
    try:
        tmp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_file.write(audio_bytes)
        tmp_file.flush()
        tmp_file.close()

        # Transcribe with Whisper
        result = model.transcribe(
            tmp_file.name,
            language=language,
            task="transcribe",
            fp16=False,  # Safe for CPU
        )

        transcribed_text = result.get("text", "").strip()
        logger.info(f"Whisper transcription ({language}): '{transcribed_text[:100]}...'")
        return transcribed_text

    except Exception as e:
        logger.error(f"Whisper transcription failed: {e}")
        raise
    finally:
        # Clean up temp file
        if tmp_file and os.path.exists(tmp_file.name):
            try:
                os.unlink(tmp_file.name)
            except OSError:
                pass


async def transcribe_from_url(
    recording_url: str,
    account_sid: str,
    auth_token: str,
    language: str = "kn",
) -> str:
    """
    Download audio from URL and transcribe it.
    Convenience function combining download + transcription.
    """
    audio_bytes = await download_twilio_recording(recording_url, account_sid, auth_token)
    return transcribe_audio(audio_bytes, language=language)
