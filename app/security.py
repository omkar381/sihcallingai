"""
Authentication, webhook verification and rate limiting.

Addresses the three findings that let anyone with the public URL spend the
project's money:

  * /api/call had no authentication at all - a stranger could make the
    service dial any number in the world on the account's balance.
  * Twilio webhooks were never signature-verified, so /twilio/gather could be
    driven by forged requests, burning LLM quota and writing to the database.
  * There was no rate limiting on any expensive path.

Design choices worth knowing:

  * API keys are compared with `secrets.compare_digest` to avoid leaking key
    material through response timing.
  * If no API key is configured, one is generated at startup and logged once,
    rather than defaulting to "unprotected". Failing open silently is how the
    original hole survived.
  * Twilio signature validation needs the account's Auth Token. When only an
    API key is configured the validator cannot run, so it logs a loud warning
    on every request instead of pretending to have verified anything.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

from fastapi import HTTPException, Request, status

from app.config import get_settings

logger = logging.getLogger(__name__)

API_KEY_HEADER = "X-API-Key"
TWILIO_SIGNATURE_HEADER = "X-Twilio-Signature"

# Generated once per process when the operator has not configured a key.
_ephemeral_api_key: Optional[str] = None
_warned_missing_validation = False


# ---------------------------------------------------------------------------
# API key auth
# ---------------------------------------------------------------------------

def get_api_key() -> str:
    """
    The configured API key, or a generated one for this process.

    Generating rather than disabling auth means an unconfigured deployment is
    locked by default instead of open by default.
    """
    global _ephemeral_api_key

    configured = (get_settings().API_KEY or "").strip()
    if configured:
        return configured

    if _ephemeral_api_key is None:
        _ephemeral_api_key = secrets.token_urlsafe(32)
        logger.warning(
            "API_KEY is not set. Generated an ephemeral key for this process only:\n"
            "    %s\n"
            "Protected endpoints require header '%s: <key>'. "
            "Set API_KEY in .env to make this stable across restarts.",
            _ephemeral_api_key, API_KEY_HEADER,
        )
    return _ephemeral_api_key


def _extract_api_key(request: Request) -> Optional[str]:
    header = request.headers.get(API_KEY_HEADER)
    if header:
        return header.strip()

    # Accept a bearer token too, which is what most dashboards send.
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()

    return None


async def require_api_key(request: Request) -> str:
    """
    FastAPI dependency guarding endpoints that spend money or move data.

    Usage:  @router.post("/api/call", dependencies=[Depends(require_api_key)])
    """
    settings = get_settings()
    if not settings.REQUIRE_API_KEY:
        return "auth-disabled"

    presented = _extract_api_key(request)
    if not presented:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing API key. Send it in the '{API_KEY_HEADER}' header.",
        )

    # Constant-time comparison: a plain `==` leaks the key one byte at a time.
    if not secrets.compare_digest(presented, get_api_key()):
        logger.warning(
            "Rejected request to %s with an invalid API key (client=%s)",
            request.url.path, request.client.host if request.client else "unknown",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key."
        )

    return presented


# ---------------------------------------------------------------------------
# Twilio webhook signature verification
# ---------------------------------------------------------------------------

async def verify_twilio_signature(request: Request) -> bool:
    """
    Verify that a webhook genuinely came from Twilio.

    Twilio signs the full request URL plus the sorted POST body with the
    account's Auth Token. Returns True when verified.

    Raises 403 when validation is enabled, possible, and fails. When the Auth
    Token is unavailable the request is allowed through but loudly logged -
    the alternative is taking the phone system down, and a warning that names
    the gap is more useful than a silent pretence of security.
    """
    global _warned_missing_validation
    settings = get_settings()

    if not settings.VALIDATE_TWILIO_SIGNATURE:
        return False

    auth_token = (settings.TWILIO_AUTH_TOKEN or "").strip()
    if not auth_token:
        if not _warned_missing_validation:
            logger.error(
                "Twilio webhook signature validation is ON but TWILIO_AUTH_TOKEN is not set "
                "(API-key auth does not carry the signing secret). Webhooks are NOT being "
                "verified. Set TWILIO_AUTH_TOKEN for the account that owns the number."
            )
            _warned_missing_validation = True
        return False

    signature = request.headers.get(TWILIO_SIGNATURE_HEADER)
    if not signature:
        logger.warning("Twilio webhook to %s carried no signature header", request.url.path)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Missing Twilio signature."
        )

    try:
        from twilio.request_validator import RequestValidator
    except ImportError:  # pragma: no cover
        logger.error("twilio package missing RequestValidator; cannot verify webhooks")
        return False

    # Twilio signs the URL it was configured with. Behind ngrok or a proxy the
    # scheme/host seen by the app differs, so prefer the public BASE_URL.
    public_base = (settings.BASE_URL or "").rstrip("/")
    if public_base:
        url = f"{public_base}{request.url.path}"
        if request.url.query:
            url = f"{url}?{request.url.query}"
    else:
        url = str(request.url)

    form = await request.form()
    params = {key: str(value) for key, value in form.items()}

    validator = RequestValidator(auth_token)
    if not validator.validate(url, params, signature):
        logger.warning(
            "Rejected forged Twilio webhook to %s (client=%s)",
            request.url.path, request.client.host if request.client else "unknown",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid Twilio signature."
        )

    return True


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class SlidingWindowRateLimiter:
    """
    In-process sliding-window limiter.

    Adequate for a single-instance deployment. A multi-instance deployment
    needs a shared store (Redis); this class is deliberately small so that
    swap is contained.
    """

    def __init__(self, max_requests: int, window_seconds: float):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)

    def check(self, key: str) -> bool:
        """True when the call is allowed; records the hit."""
        now = time.monotonic()
        window = self._hits[key]

        cutoff = now - self.window_seconds
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) >= self.max_requests:
            return False

        window.append(now)
        return True

    def retry_after(self, key: str) -> int:
        window = self._hits.get(key)
        if not window:
            return 0
        return max(1, int(self.window_seconds - (time.monotonic() - window[0])) + 1)

    def reset(self, key: Optional[str] = None) -> None:
        if key is None:
            self._hits.clear()
        else:
            self._hits.pop(key, None)


# Outbound calls cost money on every request, so they are limited hardest.
call_limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=60.0)
# LLM-backed endpoints burn quota.
ai_limiter = SlidingWindowRateLimiter(max_requests=60, window_seconds=60.0)
# Read-only market queries are cheap but still worth bounding.
query_limiter = SlidingWindowRateLimiter(max_requests=240, window_seconds=60.0)


def _client_key(request: Request) -> str:
    presented = _extract_api_key(request)
    if presented:
        # Bucket by key prefix so one caller's limit is not shared globally,
        # without putting the whole secret into memory keys or logs.
        return f"key:{presented[:12]}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


def _enforce(limiter: SlidingWindowRateLimiter, request: Request, label: str) -> None:
    if not get_settings().ENABLE_RATE_LIMIT:
        return
    key = _client_key(request)
    if not limiter.check(key):
        retry = limiter.retry_after(key)
        logger.warning("Rate limit hit on %s by %s", label, key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded for {label}. Retry in {retry}s.",
            headers={"Retry-After": str(retry)},
        )


async def rate_limit_calls(request: Request) -> None:
    _enforce(call_limiter, request, "outbound calling")


async def rate_limit_ai(request: Request) -> None:
    _enforce(ai_limiter, request, "AI processing")


async def rate_limit_queries(request: Request) -> None:
    _enforce(query_limiter, request, "market queries")


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

def resolve_cors_origins() -> list:
    """
    Explicit origin allowlist.

    `allow_origins=["*"]` together with `allow_credentials=True` is rejected
    by browsers and signals an undesigned auth model, so a wildcard is
    downgraded to credential-less mode by the caller.
    """
    raw = (get_settings().ALLOWED_ORIGINS or "").strip()
    if not raw:
        return [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://localhost:8000",
            "http://localhost:8001",
            "http://127.0.0.1:8001",
        ]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def cors_allows_credentials(origins: list) -> bool:
    return "*" not in origins
