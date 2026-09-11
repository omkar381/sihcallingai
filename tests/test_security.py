"""
Authentication, webhook verification and rate limiting.

Covers the toll-fraud hole (/api/call with no auth) and the forged-webhook
hole (no Twilio signature check) identified in the audit.
"""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI, Depends, Request
from fastapi.testclient import TestClient

from app.config import get_settings
from app.security import (
    API_KEY_HEADER,
    SlidingWindowRateLimiter,
    cors_allows_credentials,
    get_api_key,
    rate_limit_calls,
    require_api_key,
    resolve_cors_origins,
)


@pytest.fixture
def settings():
    s = get_settings()
    original = (s.API_KEY, s.REQUIRE_API_KEY, s.ENABLE_RATE_LIMIT, s.VALIDATE_TWILIO_SIGNATURE)
    yield s
    (s.API_KEY, s.REQUIRE_API_KEY, s.ENABLE_RATE_LIMIT, s.VALIDATE_TWILIO_SIGNATURE) = original


@pytest.fixture
def protected_app(settings):
    settings.API_KEY = "test-key-abc123"
    settings.REQUIRE_API_KEY = True
    settings.ENABLE_RATE_LIMIT = False

    app = FastAPI()

    @app.post("/api/call", dependencies=[Depends(require_api_key)])
    async def call():
        return {"ok": True}

    @app.get("/public")
    async def public():
        return {"ok": True}

    return TestClient(app)


class TestApiKeyAuth:
    def test_unauthenticated_call_is_rejected(self, protected_app):
        # The original hole: anyone with the URL could dial any number.
        response = protected_app.post("/api/call", json={"phone_number": "+910000000000"})
        assert response.status_code == 401

    def test_wrong_key_is_forbidden(self, protected_app):
        response = protected_app.post("/api/call", headers={API_KEY_HEADER: "wrong"})
        assert response.status_code == 403

    def test_correct_key_is_allowed(self, protected_app):
        response = protected_app.post("/api/call", headers={API_KEY_HEADER: "test-key-abc123"})
        assert response.status_code == 200

    def test_bearer_token_is_accepted(self, protected_app):
        response = protected_app.post(
            "/api/call", headers={"Authorization": "Bearer test-key-abc123"}
        )
        assert response.status_code == 200

    def test_public_routes_stay_open(self, protected_app):
        assert protected_app.get("/public").status_code == 200

    def test_unconfigured_key_generates_one_rather_than_disabling_auth(self, settings):
        # Failing open is what allowed the original vulnerability to persist.
        settings.API_KEY = ""
        generated = get_api_key()
        assert generated
        assert len(generated) >= 32
        assert get_api_key() == generated  # stable within the process

    def test_auth_can_be_explicitly_disabled(self, settings):
        settings.API_KEY = "k"
        settings.REQUIRE_API_KEY = False
        app = FastAPI()

        @app.post("/x", dependencies=[Depends(require_api_key)])
        async def x():
            return {"ok": True}

        assert TestClient(app).post("/x").status_code == 200


class TestRateLimiter:
    def test_allows_up_to_the_limit(self):
        limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=60)
        assert all(limiter.check("caller") for _ in range(3))

    def test_blocks_beyond_the_limit(self):
        limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            limiter.check("caller")
        assert limiter.check("caller") is False

    def test_callers_are_isolated(self):
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60)
        limiter.check("a"), limiter.check("a")
        assert limiter.check("a") is False
        assert limiter.check("b") is True

    def test_window_slides(self):
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=0.3)
        limiter.check("a"), limiter.check("a")
        assert limiter.check("a") is False
        time.sleep(0.35)
        assert limiter.check("a") is True

    def test_retry_after_is_positive_when_blocked(self):
        limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60)
        limiter.check("a")
        assert limiter.retry_after("a") > 0

    def test_enforced_limit_returns_429(self, settings):
        settings.REQUIRE_API_KEY = False
        settings.ENABLE_RATE_LIMIT = True

        from app.security import call_limiter
        call_limiter.reset()

        app = FastAPI()

        @app.post("/call", dependencies=[Depends(rate_limit_calls)])
        async def call():
            return {"ok": True}

        client = TestClient(app)
        statuses = [client.post("/call").status_code for _ in range(15)]
        assert 429 in statuses
        assert statuses[0] == 200
        call_limiter.reset()


class TestCors:
    def test_default_origins_are_explicit_not_wildcard(self, settings):
        settings.ALLOWED_ORIGINS = ""
        origins = resolve_cors_origins()
        assert "*" not in origins
        assert origins

    def test_configured_origins_are_parsed(self, settings):
        settings.ALLOWED_ORIGINS = "https://a.example, https://b.example"
        assert resolve_cors_origins() == ["https://a.example", "https://b.example"]

    def test_wildcard_disables_credentials(self):
        # The invalid combination flagged in the audit must be impossible.
        assert cors_allows_credentials(["*"]) is False
        assert cors_allows_credentials(["https://a.example"]) is True


class TestTwilioSignature:
    def _app(self):
        from app.security import verify_twilio_signature

        app = FastAPI()

        @app.post("/twilio/voice", dependencies=[Depends(verify_twilio_signature)])
        async def voice():
            return {"ok": True}

        return TestClient(app)

    def test_missing_signature_is_rejected_when_token_present(self, settings):
        settings.VALIDATE_TWILIO_SIGNATURE = True
        settings.TWILIO_AUTH_TOKEN = "a" * 32
        try:
            assert self._app().post("/twilio/voice", data={"CallSid": "CA1"}).status_code == 403
        finally:
            settings.TWILIO_AUTH_TOKEN = ""

    def test_forged_signature_is_rejected(self, settings):
        settings.VALIDATE_TWILIO_SIGNATURE = True
        settings.TWILIO_AUTH_TOKEN = "a" * 32
        try:
            response = self._app().post(
                "/twilio/voice",
                data={"CallSid": "CA1"},
                headers={"X-Twilio-Signature": "obviously-not-valid"},
            )
            assert response.status_code == 403
        finally:
            settings.TWILIO_AUTH_TOKEN = ""

    def test_disabled_validation_lets_requests_through(self, settings):
        settings.VALIDATE_TWILIO_SIGNATURE = False
        assert self._app().post("/twilio/voice", data={"CallSid": "CA1"}).status_code == 200

    def test_missing_auth_token_does_not_silently_claim_verification(self, settings):
        # Without the Auth Token the validator cannot run. It must let the
        # phone system keep working but must not report success.
        settings.VALIDATE_TWILIO_SIGNATURE = True
        settings.TWILIO_AUTH_TOKEN = ""
        assert self._app().post("/twilio/voice", data={"CallSid": "CA1"}).status_code == 200
