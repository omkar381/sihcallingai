"""
Google Translate integration for Kannada ↔ English translation.
Uses googletrans (free) with fallback to Google Cloud Translation API.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ---------- googletrans (free, no API key) ----------

def _translate_with_googletrans(text: str, src: str, dest: str) -> Optional[str]:
    """Translate using the free googletrans library."""
    try:
        from googletrans import Translator
        translator = Translator()
        result = translator.translate(text, src=src, dest=dest)
        if result and result.text:
            return result.text
    except Exception as e:
        logger.warning(f"googletrans failed ({src}→{dest}): {e}")
    return None


# ---------- Google Cloud Translation (paid, reliable) ----------

def _translate_with_gcloud(text: str, src: str, dest: str) -> Optional[str]:
    """Translate using Google Cloud Translation API."""
    try:
        from google.cloud import translate_v2 as translate
        client = translate.Client()
        result = client.translate(text, source_language=src, target_language=dest)
        if result and result.get("translatedText"):
            return result["translatedText"]
    except Exception as e:
        logger.warning(f"Google Cloud Translate failed ({src}→{dest}): {e}")
    return None


# ---------- Public API ----------

def translate_to_english(kannada_text: str, source: str = "kn") -> str:
    """
    Translate Kannada (or `source`) text to English.
    Tries googletrans first, falls back to Google Cloud Translation.
    """
    if not kannada_text or not kannada_text.strip():
        return ""

    text = kannada_text.strip()

    # Try free API first
    result = _translate_with_googletrans(text, src=source, dest="en")
    if result:
        logger.info(f"Translated KN→EN (googletrans): '{result[:80]}...'")
        return result

    # Fallback to Google Cloud
    result = _translate_with_gcloud(text, src=source, dest="en")
    if result:
        logger.info(f"Translated KN→EN (gcloud): '{result[:80]}...'")
        return result

    # Last resort: return original text (Gemini can sometimes handle Kannada)
    logger.error("All translation methods failed for KN→EN — returning original text")
    return text


def translate_to_kannada(english_text: str) -> str:
    """
    Translate English text to Kannada.
    Tries googletrans first, falls back to Google Cloud Translation.
    """
    if not english_text or not english_text.strip():
        return ""

    text = english_text.strip()

    # Try free API first
    result = _translate_with_googletrans(text, src="en", dest="kn")
    if result:
        logger.info(f"Translated EN→KN (googletrans): '{result[:80]}...'")
        return result

    # Fallback to Google Cloud
    result = _translate_with_gcloud(text, src="en", dest="kn")
    if result:
        logger.info(f"Translated EN→KN (gcloud): '{result[:80]}...'")
        return result

    # Last resort
    logger.error("All translation methods failed for EN→KN — returning original text")
    return text
