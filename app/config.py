"""
Application configuration loaded from environment variables.
Uses pydantic-settings for validation and type coercion.
"""

import os
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """All configuration for the AI Krishi Voice Assistant."""

    # --- Server ---
    BASE_URL: str = Field(default="http://localhost:8000", description="Public base URL for callbacks")
    HOST: str = Field(default="0.0.0.0")
    PORT: int = Field(default=8000)

    # --- Twilio ---
    TWILIO_ACCOUNT_SID: str = Field(default="")
    TWILIO_AUTH_TOKEN: str = Field(default="")
    TWILIO_PHONE_NUMBER: str = Field(default="")
    # Optional API Key auth (preferred when set; works when no Auth Token is available)
    TWILIO_API_KEY_SID: str = Field(default="")
    TWILIO_API_KEY_SECRET: str = Field(default="")

    # --- Google Gemini API ---
    GEMINI_API_KEY: str = Field(default="")

    # --- Google Cloud ---
    GOOGLE_APPLICATION_CREDENTIALS: str = Field(default="")
    GCP_PROJECT_ID: str = Field(default="")

    # --- OpenWeatherMap ---
    OPENWEATHER_API_KEY: str = Field(default="")

    # --- data.gov.in ---
    DATA_GOV_API_KEY: str = Field(default="579b464db66ec23bdd0000013ea9573582a742454b843e9f3a92140b")

    # --- Whisper ---
    WHISPER_MODEL_SIZE: str = Field(default="base")

    # --- Audio ---
    AUDIO_DIR: str = Field(default="audio")
    AUDIO_CLEANUP_MINUTES: int = Field(default=60)

    # --- Security ---
    # Protects endpoints that spend money or move data. When API_KEY is blank
    # an ephemeral key is generated at startup and logged, so an unconfigured
    # deployment is locked rather than open.
    API_KEY: str = Field(default="")
    REQUIRE_API_KEY: bool = Field(default=True)
    VALIDATE_TWILIO_SIGNATURE: bool = Field(default=True)
    ENABLE_RATE_LIMIT: bool = Field(default=True)
    # Comma-separated. Never use "*" together with credentials.
    ALLOWED_ORIGINS: str = Field(default="")

    # --- Market intelligence ---
    # Allows MOCK-tagged demo data to reach the recommendation engine. Off by
    # default so a real call can never be answered from fixture data.
    MARKET_DEMO_MODE: bool = Field(default=False)
    MARKET_DEFAULT_RADIUS_KM: float = Field(default=150.0)

    # --- Voice agent ---
    # Intent parsing runs on every turn, so it uses the fastest model that
    # reads Kannada reliably (~1.7s measured, against ~2.7s for 2.5 Flash).
    VOICE_NLU_MODEL: str = Field(default="gemini-3.5-flash-lite")
    VOICE_NLU_TIMEOUT_SECONDS: float = Field(default=6.0)
    VOICE_REPLY_MODEL: str = Field(default="gemini-3.5-flash-lite")
    VOICE_REPLY_TIMEOUT_SECONDS: float = Field(default=6.0)
    VOICE_TTS_VOICE: str = Field(default="kn-IN-GaganNeural")
    VOICE_TTS_VOICE_HI: str = Field(default="hi-IN-MadhurNeural")
    VOICE_TTS_VOICE_EN: str = Field(default="en-IN-PrabhatNeural")
    VOICE_TTS_RATE: str = Field(default="-4%")
    VOICE_DEFAULT_LOCATION: str = Field(default="Kalaburagi")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


# Singleton instance
_settings = None


def get_settings() -> Settings:
    """Get or create the singleton Settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
        # Ensure audio directory exists
        os.makedirs(_settings.AUDIO_DIR, exist_ok=True)
    return _settings


# ============================================
# TWILIO CLIENT FACTORY
# ============================================

def twilio_configured() -> bool:
    """True when we have enough credentials to talk to Twilio."""
    s = get_settings()
    if not s.TWILIO_ACCOUNT_SID:
        return False
    return bool(s.TWILIO_AUTH_TOKEN) or bool(s.TWILIO_API_KEY_SID and s.TWILIO_API_KEY_SECRET)


def get_twilio_client():
    """
    Build a Twilio REST client.

    Prefers API Key auth (TWILIO_API_KEY_SID/SECRET) when present, since an
    account may expose an API key without a usable Auth Token. Falls back to
    Account SID + Auth Token.
    """
    from twilio.rest import Client as _TwilioClient

    s = get_settings()
    if not s.TWILIO_ACCOUNT_SID:
        raise RuntimeError("TWILIO_ACCOUNT_SID is not configured")

    if s.TWILIO_API_KEY_SID and s.TWILIO_API_KEY_SECRET:
        return _TwilioClient(s.TWILIO_API_KEY_SID, s.TWILIO_API_KEY_SECRET, s.TWILIO_ACCOUNT_SID)

    if s.TWILIO_AUTH_TOKEN:
        return _TwilioClient(s.TWILIO_ACCOUNT_SID, s.TWILIO_AUTH_TOKEN)

    raise RuntimeError("No Twilio credentials: set TWILIO_AUTH_TOKEN or TWILIO_API_KEY_SID/SECRET")
