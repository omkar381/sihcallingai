"""
Google Cloud Text-to-Speech for generating Kannada audio.
Pre-generates greeting and failsafe audio at startup.
"""

import os
import uuid
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Pre-generated audio file paths
GREETING_AUDIO: Optional[str] = None
WAIT_AUDIO: Optional[str] = None
ERROR_AUDIO: Optional[str] = None


def _generate_audio_gcloud(text: str, output_path: str) -> bool:
    """
    Generate Kannada MP3 audio using Google Cloud TTS.
    Returns True on success.
    """
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        # Fast fail if no credentials provided, prevents 12s metadata server timeout
        logger.debug("Skipping Google Cloud TTS (GOOGLE_APPLICATION_CREDENTIALS not set)")
        return False

    try:
        from google.cloud import texttospeech

        client = texttospeech.TextToSpeechClient()

        synthesis_input = texttospeech.SynthesisInput(text=text)

        voice = texttospeech.VoiceSelectionParams(
            language_code="kn-IN",
            name="kn-IN-Wavenet-A",
            ssml_gender=texttospeech.SsmlVoiceGender.FEMALE,
        )

        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=0.9,   # Slightly slower for clarity
            pitch=0.0,
        )

        response = client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config,
        )

        with open(output_path, "wb") as f:
            f.write(response.audio_content)

        logger.info(f"Generated audio (Google Cloud TTS): {output_path}")
        return True

    except Exception as e:
        logger.warning(f"Google Cloud TTS failed: {e}")
        return False


def _generate_audio_edge_tts(text: str, output_path: str) -> bool:
    """
    Fallback: Generate Kannada MP3 using edge-tts (fast, free, neural voices).
    """
    try:
        import subprocess
        import sys
        # kn-IN-GaganNeural (Male)
        voice = "kn-IN-GaganNeural"
        cmd = [
            sys.executable, "-m", "edge_tts",
            "--voice", voice,
            "--rate", "-5%",      # Slightly slower for natural feel
            "--pitch", "+0Hz",    # Reset pitch for male voice
            "--text", text,
            "--write-media", output_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)

        logger.info(f"Generated audio (edge-tts fallback): {output_path}")
        return True

    except subprocess.CalledProcessError as e:
        logger.warning(f"edge-tts process failed: {e.stderr}")
        return False
    except Exception as e:
        logger.warning(f"edge-tts fallback also failed: {e}")
        return False


def generate_kannada_audio(text: str, audio_dir: str = "audio") -> Optional[str]:
    """
    Generate Kannada speech audio from text.
    Tries Google Cloud TTS first, falls back to edge-tts.

    Args:
        text: Kannada text to convert to speech
        audio_dir: Directory to save audio files

    Returns:
        Filename of generated audio (relative to audio_dir), or None on failure
    """
    if not text or not text.strip():
        logger.warning("Empty text passed to TTS")
        return None

    os.makedirs(audio_dir, exist_ok=True)

    filename = f"{uuid.uuid4().hex}.mp3"
    output_path = os.path.join(audio_dir, filename)

    # Try Google Cloud TTS first
    if _generate_audio_gcloud(text, output_path):
        return filename

    # Fallback to edge-tts
    if _generate_audio_edge_tts(text, output_path):
        return filename

    logger.error("All TTS methods failed")
    return None


def pre_generate_system_audio(audio_dir: str = "audio") -> None:
    """
    Pre-generate greeting and failsafe audio at startup.
    These are cached so they don't need to be generated during calls.
    """
    global GREETING_AUDIO, WAIT_AUDIO, ERROR_AUDIO

    os.makedirs(audio_dir, exist_ok=True)

    # --- Greeting ---
    greeting_text = "ನಮಸ್ಕಾರ, AI ಕೃಷಿ ಸಹಾಯಕಕ್ಕೆ ಸ್ವಾಗತ. ದಯವಿಟ್ಟು ನಿಮ್ಮ ಕೃಷಿ ಸಮಸ್ಯೆಯನ್ನು ಹೇಳಿ."
    greeting_path = os.path.join(audio_dir, "greeting.mp3")
    if not os.path.exists(greeting_path):
        if _generate_audio_gcloud(greeting_text, greeting_path) or \
           _generate_audio_edge_tts(greeting_text, greeting_path):
            GREETING_AUDIO = "greeting.mp3"
            logger.info("Pre-generated greeting audio")
        else:
            logger.error("Failed to pre-generate greeting audio")
    else:
        GREETING_AUDIO = "greeting.mp3"
        logger.info("Greeting audio already exists")

    # --- Wait message ---
    wait_text = "ದಯವಿಟ್ಟು ಕಾಯಿರಿ, ಮಾಹಿತಿ ಪಡೆಯಲಾಗುತ್ತಿದೆ"
    wait_path = os.path.join(audio_dir, "wait.mp3")
    if not os.path.exists(wait_path):
        if _generate_audio_gcloud(wait_text, wait_path) or \
           _generate_audio_edge_tts(wait_text, wait_path):
            WAIT_AUDIO = "wait.mp3"
            logger.info("Pre-generated wait audio")
        else:
            logger.error("Failed to pre-generate wait audio")
    else:
        WAIT_AUDIO = "wait.mp3"
        logger.info("Wait audio already exists")

    # --- Error message ---
    error_text = "ಕ್ಷಮಿಸಿ, ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ"
    error_path = os.path.join(audio_dir, "error.mp3")
    if not os.path.exists(error_path):
        if _generate_audio_gcloud(error_text, error_path) or \
           _generate_audio_edge_tts(error_text, error_path):
            ERROR_AUDIO = "error.mp3"
            logger.info("Pre-generated error audio")
        else:
            logger.error("Failed to pre-generate error audio")
    else:
        ERROR_AUDIO = "error.mp3"
        logger.info("Error audio already exists")


def get_greeting_audio() -> Optional[str]:
    """Get the pre-generated greeting audio filename."""
    return GREETING_AUDIO


def get_wait_audio() -> Optional[str]:
    """Get the pre-generated wait audio filename."""
    return WAIT_AUDIO


def get_error_audio() -> Optional[str]:
    """Get the pre-generated error audio filename."""
    return ERROR_AUDIO
