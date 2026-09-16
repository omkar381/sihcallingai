"""
Audio file manager.
Serves MP3 files and auto-cleans old files.
"""

import os
import time
import asyncio
import logging
from typing import Optional

from app.config import get_settings

logger = logging.getLogger(__name__)


def get_audio_url(filename: str) -> str:
    """
    Get the full public URL for an audio file.
    This URL is what Twilio uses in <Play> tags.
    """
    settings = get_settings()
    base = settings.BASE_URL.rstrip("/")
    return f"{base}/audio/{filename}"


def audio_file_exists(filename: str) -> bool:
    """Check if an audio file exists in the audio directory."""
    settings = get_settings()
    path = os.path.join(settings.AUDIO_DIR, filename)
    return os.path.isfile(path)


def delete_audio_file(filename: str) -> bool:
    """Delete a specific audio file. Returns True if deleted."""
    settings = get_settings()
    path = os.path.join(settings.AUDIO_DIR, filename)
    try:
        if os.path.isfile(path):
            os.unlink(path)
            logger.debug(f"Deleted audio file: {filename}")
            return True
    except OSError as e:
        logger.warning(f"Failed to delete audio file {filename}: {e}")
    return False


def cleanup_old_audio_files(max_age_minutes: int = 60) -> int:
    """
    Delete audio files older than max_age_minutes.
    Skips system files (greeting.mp3, wait.mp3, error.mp3).
    Returns number of files deleted.
    """
    settings = get_settings()
    audio_dir = settings.AUDIO_DIR

    if not os.path.isdir(audio_dir):
        return 0

    system_files = {"greeting.mp3", "wait.mp3", "error.mp3"}
    cutoff_time = time.time() - (max_age_minutes * 60)
    deleted = 0

    try:
        for filename in os.listdir(audio_dir):
            if filename in system_files or filename.startswith(("sys_", "greet_")):
                continue  # Never delete system prompts; they are fixed text

            filepath = os.path.join(audio_dir, filename)
            if not os.path.isfile(filepath):
                continue

            if os.path.getmtime(filepath) < cutoff_time:
                try:
                    os.unlink(filepath)
                    deleted += 1
                except OSError:
                    pass
    except Exception as e:
        logger.warning(f"Audio cleanup error: {e}")

    if deleted > 0:
        logger.info(f"Cleaned up {deleted} old audio files")
    return deleted


async def periodic_audio_cleanup(interval_minutes: int = 15) -> None:
    """
    Background task that periodically cleans up old audio files.
    Run as an asyncio task during app lifespan.
    """
    settings = get_settings()
    while True:
        await asyncio.sleep(interval_minutes * 60)
        try:
            cleanup_old_audio_files(max_age_minutes=settings.AUDIO_CLEANUP_MINUTES)
        except Exception as e:
            logger.error(f"Periodic audio cleanup failed: {e}")
