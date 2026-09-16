"""
Farmer portal API.

Authentication is a session cookie (HttpOnly, SameSite=Lax). Every request
that changes state must also carry the X-Portal-Client header: a cross-site
form cannot set a custom header, so this closes CSRF without a token dance.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from app.config import get_settings
from app.portal import auth, service
from app.portal.service import PortalError
from app.security import SlidingWindowRateLimiter, require_api_key

router = APIRouter(prefix="/api/portal", tags=["farmer-portal"])
pages = APIRouter(include_in_schema=False)

_login_limiter = SlidingWindowRateLimiter(max_requests=20, window_seconds=60.0)
_register_limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=600.0)

_SHELL = os.path.join("static", "portal", "index.html")
_PAGE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    # The verification token is in the URL; keep it off outbound Referer headers.
    "Referrer-Policy": "same-origin",
}
_FILE_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "sandbox",
}


async def portal_error_handler(request: Request, exc: PortalError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"detail": {"message": exc.message, "code": exc.code, "fields": exc.fields}},
    )


def install(app) -> None:
    app.add_exception_handler(PortalError, portal_error_handler)
    app.include_router(router)
    app.include_router(pages)


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------

def _client_ip(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("X-Forwarded-For", "")
    # Only the local tunnel (ngrok) is trusted to report the real client.
    if forwarded and host in ("127.0.0.1", "::1", "localhost"):
        return forwarded.split(",")[0].strip()
    return host


def _limit(limiter: SlidingWindowRateLimiter, request: Request, what: str) -> None:
    if not get_settings().ENABLE_RATE_LIMIT:
        return
    key = _client_ip(request)
    if not limiter.check(key):
        raise PortalError(
            f"Too many {what} attempts from this network. Try again in {limiter.retry_after(key)} seconds.",
            429, "RATE_LIMITED",
        )


def _require_client_header(request: Request) -> None:
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(auth.CLIENT_HEADER) != "web":
        raise PortalError("This request was blocked. Reload the page and try again.", 403, "CSRF")


def _is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("X-Forwarded-Proto", "").lower() == "https"


def _public_base(request: Request) -> str:
    configured = (get_settings().BASE_URL or "").rstrip("/")
    if configured and "localhost" not in configured and "127.0.0.1" not in configured:
        return configured
    proto = request.headers.get("X-Forwarded-Proto") or request.url.scheme
    host = request.headers.get("X-Forwarded-Host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


async def session(request: Request) -> Dict[str, Any]:
    _require_client_header(request)
    farmer = await asyncio.to_thread(service.session_farmer, request.cookies.get(auth.SESSION_COOKIE))
    if farmer is None:
        raise PortalError("Your session has ended. Sign in again.", 401, "UNAUTHENTICATED")
    return farmer


async def active_farmer(farmer: Dict[str, Any] = Depends(session)) -> Dict[str, Any]:
    if farmer["must_change_password"]:
        raise PortalError("Set a new password before continuing.", 403, "PASSWORD_CHANGE_REQUIRED")
    return farmer


async def _json(request: Request) -> Dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        raise PortalError("The request body must be JSON.", 400, "BAD_REQUEST")
    if not isinstance(body, dict):
        raise PortalError("The request body must be a JSON object.", 400, "BAD_REQUEST")
    return body


async def _read_upload(upload: UploadFile, limit: int) -> bytes:
    return await upload.read(limit + 1)


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------

@router.get("/meta")
async def meta():
    return service.meta()


@router.post("/register")
async def register(request: Request):
    _require_client_header(request)
    _limit(_register_limiter, request, "registration")
    body = await _json(request)
    return await asyncio.to_thread(service.register_farmer, body)


@router.post("/login")
async def login(request: Request):
    _require_client_header(request)
    _limit(_login_limiter, request, "sign-in")
    body = await _json(request)
    token, farmer = await asyncio.to_thread(
        service.authenticate, str(body.get("identifier") or ""), str(body.get("password") or ""),
        request.headers.get("User-Agent", ""),
    )
    response = JSONResponse({"farmer": farmer})
    response.set_cookie(
        auth.SESSION_COOKIE, token, max_age=auth.SESSION_SECONDS, httponly=True,
        samesite="lax", secure=_is_https(request), path="/",
    )
    return response


@router.get("/verify/{farmer_id}")
async def verify(farmer_id: str, t: str = Query("")):
    return await asyncio.to_thread(service.verify_card, farmer_id, t)


@router.get("/verify/{farmer_id}/photo")
async def verify_photo(farmer_id: str, t: str = Query("")):
    found = await asyncio.to_thread(service.verified_photo, farmer_id, t)
    if not found:
        return Response(status_code=404)
    return FileResponse(found[0], media_type=found[1], headers=_FILE_HEADERS)


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

@router.get("/session")
async def current_session(farmer: Dict[str, Any] = Depends(session)):
    unread = 0 if farmer["must_change_password"] else await asyncio.to_thread(service.unread_count, farmer["farmer_id"])
    return {"farmer": farmer, "unread_notifications": unread}


@router.post("/logout")
async def logout(request: Request):
    _require_client_header(request)
    await asyncio.to_thread(service.end_session, request.cookies.get(auth.SESSION_COOKIE))
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return response


@router.post("/password")
async def change_password(request: Request, farmer: Dict[str, Any] = Depends(session)):
    body = await _json(request)
    updated = await asyncio.to_thread(
        service.change_password, farmer["farmer_id"], str(body.get("current_password") or ""),
        str(body.get("new_password") or ""), request.cookies.get(auth.SESSION_COOKIE),
    )
    return {"farmer": updated}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

async def _refresh_weather(farmer: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not service.weather_check_due(farmer["farmer_id"]):
        return None
    from app.agriculture import get_weather

    try:
        reading = await asyncio.wait_for(get_weather(farmer["district"]), timeout=6)
    except Exception:
        reading = {}
    return await asyncio.to_thread(service.record_weather, farmer["farmer_id"], farmer["district"], reading)


_last_weather: Dict[str, Dict[str, Any]] = {}


@router.get("/overview")
async def overview(farmer: Dict[str, Any] = Depends(active_farmer)):
    reading = await _refresh_weather(farmer)
    if reading:
        _last_weather[farmer["farmer_id"]] = reading
    data = await asyncio.to_thread(service.overview, farmer["farmer_id"])
    data["weather"] = _last_weather.get(farmer["farmer_id"])
    return data


@router.get("/profile")
async def profile(farmer: Dict[str, Any] = Depends(active_farmer)):
    return {"farmer": await asyncio.to_thread(service.get_profile, farmer["farmer_id"])}


@router.put("/profile")
async def update_profile(request: Request, farmer: Dict[str, Any] = Depends(active_farmer)):
    body = await _json(request)
    return {"farmer": await asyncio.to_thread(service.update_profile, farmer["farmer_id"], body)}


@router.post("/profile/photo")
async def upload_photo(photo: UploadFile = File(...), farmer: Dict[str, Any] = Depends(active_farmer)):
    data = await _read_upload(photo, service.MAX_PHOTO_BYTES)
    return {"farmer": await asyncio.to_thread(service.save_photo, farmer["farmer_id"], data)}


@router.delete("/profile/photo")
async def delete_photo(farmer: Dict[str, Any] = Depends(active_farmer)):
    return {"farmer": await asyncio.to_thread(service.remove_photo, farmer["farmer_id"])}


@router.get("/photo")
async def photo(farmer: Dict[str, Any] = Depends(session)):
    found = await asyncio.to_thread(service.photo_file, farmer["farmer_id"])
    if not found:
        return Response(status_code=404)
    return FileResponse(found[0], media_type=found[1], headers=_FILE_HEADERS)


@router.get("/farm")
async def farm(farmer: Dict[str, Any] = Depends(active_farmer)):
    return await asyncio.to_thread(service.farm_summary, farmer["farmer_id"])


@router.post("/plots")
async def create_plot(request: Request, farmer: Dict[str, Any] = Depends(active_farmer)):
    body = await _json(request)
    return {"plot": await asyncio.to_thread(service.create_plot, farmer["farmer_id"], body)}


@router.put("/plots/{plot_id}")
async def update_plot(plot_id: str, request: Request, farmer: Dict[str, Any] = Depends(active_farmer)):
    body = await _json(request)
    return {"plot": await asyncio.to_thread(service.update_plot, farmer["farmer_id"], plot_id, body)}


@router.delete("/plots/{plot_id}")
async def delete_plot(plot_id: str, farmer: Dict[str, Any] = Depends(active_farmer)):
    await asyncio.to_thread(service.delete_plot, farmer["farmer_id"], plot_id)
    return {"ok": True}


@router.get("/crops")
async def crops(farmer: Dict[str, Any] = Depends(active_farmer)):
    return {"crops": await asyncio.to_thread(service.list_crops, farmer["farmer_id"])}


@router.post("/crops")
async def create_crop(request: Request, farmer: Dict[str, Any] = Depends(active_farmer)):
    body = await _json(request)
    return {"crop": await asyncio.to_thread(service.create_crop, farmer["farmer_id"], body)}


@router.put("/crops/{crop_id}")
async def update_crop(crop_id: str, request: Request, farmer: Dict[str, Any] = Depends(active_farmer)):
    body = await _json(request)
    return {"crop": await asyncio.to_thread(service.update_crop, farmer["farmer_id"], crop_id, body)}


@router.delete("/crops/{crop_id}")
async def delete_crop(crop_id: str, farmer: Dict[str, Any] = Depends(active_farmer)):
    await asyncio.to_thread(service.delete_crop, farmer["farmer_id"], crop_id)
    return {"ok": True}


@router.post("/crops/{crop_id}/harvests")
async def add_harvest(crop_id: str, request: Request, farmer: Dict[str, Any] = Depends(active_farmer)):
    body = await _json(request)
    return {"crop": await asyncio.to_thread(service.add_harvest, farmer["farmer_id"], crop_id, body)}


@router.delete("/harvests/{harvest_id}")
async def delete_harvest(harvest_id: str, farmer: Dict[str, Any] = Depends(active_farmer)):
    await asyncio.to_thread(service.delete_harvest, farmer["farmer_id"], harvest_id)
    return {"ok": True}


@router.get("/documents")
async def documents(farmer: Dict[str, Any] = Depends(active_farmer)):
    return await asyncio.to_thread(service.list_documents, farmer["farmer_id"])


@router.post("/documents")
async def upload_document(
    file: UploadFile = File(...),
    category: str = Form(""),
    title: str = Form(""),
    reference_number: str = Form(""),
    farmer: Dict[str, Any] = Depends(active_farmer),
):
    data = await _read_upload(file, service.MAX_DOCUMENT_BYTES)
    document = await asyncio.to_thread(
        service.upload_document, farmer["farmer_id"], category=category, title=title,
        reference_number=reference_number, original_name=file.filename or "", data=data,
    )
    return {"document": document}


@router.get("/documents/{doc_id}/file")
async def document_file(doc_id: str, download: bool = False, farmer: Dict[str, Any] = Depends(active_farmer)):
    path, mime, name = await asyncio.to_thread(service.document_file, farmer["farmer_id"], doc_id)
    return FileResponse(
        path, media_type=mime, filename=name, headers=_FILE_HEADERS,
        content_disposition_type="attachment" if download else "inline",
    )


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, farmer: Dict[str, Any] = Depends(active_farmer)):
    await asyncio.to_thread(service.delete_document, farmer["farmer_id"], doc_id)
    return {"ok": True}


@router.get("/notifications")
async def notifications(category: Optional[str] = None, farmer: Dict[str, Any] = Depends(active_farmer)):
    await _refresh_weather(farmer)
    return await asyncio.to_thread(service.list_notifications, farmer["farmer_id"], category or None)


@router.post("/notifications/read-all")
async def read_all(farmer: Dict[str, Any] = Depends(active_farmer)):
    return {"marked": await asyncio.to_thread(service.mark_read, farmer["farmer_id"], None)}


@router.post("/notifications/{notification_id}/read")
async def read_one(notification_id: str, farmer: Dict[str, Any] = Depends(active_farmer)):
    return {"marked": await asyncio.to_thread(service.mark_read, farmer["farmer_id"], notification_id)}


_call_limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=300.0)


@router.post("/call")
async def call_now(farmer: Dict[str, Any] = Depends(active_farmer)):
    """Place an AI Krishi voice call. Always dials the configured portal number."""
    from app.config import get_twilio_client, twilio_configured

    settings = get_settings()
    to_number = (settings.PORTAL_CALL_NUMBER or "").strip()
    if not to_number:
        raise PortalError("Calling is not set up on this server.", 503, "CALL_UNAVAILABLE")
    if not twilio_configured() or not settings.TWILIO_PHONE_NUMBER:
        raise PortalError("Calling is not available right now. Please try again later.", 503, "CALL_UNAVAILABLE")
    if settings.ENABLE_RATE_LIMIT and not _call_limiter.check(farmer["farmer_id"]):
        raise PortalError(
            f"A call was requested recently. Try again in {_call_limiter.retry_after(farmer['farmer_id'])} seconds.",
            429, "RATE_LIMITED",
        )

    base = settings.BASE_URL.rstrip("/")

    def _dial():
        return get_twilio_client().calls.create(
            to=to_number,
            from_=settings.TWILIO_PHONE_NUMBER,
            url=f"{base}/twilio/voice",
            status_callback=f"{base}/twilio/status",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
        )

    try:
        call = await asyncio.to_thread(_dial)
    except Exception as exc:
        raise PortalError(f"The call could not be placed: {str(exc)[:160]}", 502, "CALL_FAILED")

    await asyncio.to_thread(service.log_call_request, farmer["farmer_id"], call.sid, to_number)
    return {"call_sid": call.sid, "status": call.status, "to": to_number}


@router.get("/card")
async def card(request: Request, farmer: Dict[str, Any] = Depends(active_farmer)):
    return await asyncio.to_thread(service.card, farmer["farmer_id"], _public_base(request))


# ---------------------------------------------------------------------------
# Registry office (operator key)
# ---------------------------------------------------------------------------

@router.post("/admin/farmers/{farmer_id}/verification", dependencies=[Depends(require_api_key)])
async def admin_verification(farmer_id: str, request: Request):
    body = await _json(request)
    return {"farmer": await asyncio.to_thread(
        service.set_verification, auth.canonical_farmer_id(farmer_id),
        str(body.get("status") or "").upper(), str(body.get("verified_by") or "Registry operator"),
    )}


@router.post("/admin/farmers/{farmer_id}/reset-password", dependencies=[Depends(require_api_key)])
async def admin_reset_password(farmer_id: str):
    return {"credentials": await asyncio.to_thread(service.reset_password, auth.canonical_farmer_id(farmer_id))}


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def _shell() -> FileResponse:
    return FileResponse(_SHELL, headers=_PAGE_HEADERS)


@pages.get("/portal")
@pages.get("/portal/{path:path}")
async def portal_page(path: str = ""):
    return _shell()


@pages.get("/verify/{farmer_id}")
async def verify_page(farmer_id: str):
    return _shell()
