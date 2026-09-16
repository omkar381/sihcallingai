"""
AI Krishi Voice Assistant — FastAPI Application Entry Point.

Lifespan: preloads Whisper model, initializes Gemini, pre-generates system audio.
Mounts static audio serving and includes all route handlers.
"""

import os

# faiss/numpy's bundled OpenBLAS fails to allocate per-thread buffers on this
# machine with its default thread count; must be set before numpy is imported.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import asyncio
import logging
import json
import urllib.parse
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, StreamingResponse
from twilio.rest import Client as TwilioClient

from app.config import get_settings, get_twilio_client, twilio_configured
from app.security import resolve_cors_origins, cors_allows_credentials, require_api_key, rate_limit_calls
from app.gemini_ai import initialize_gemini
from app.tts import pre_generate_system_audio
from app.audio_manager import periodic_audio_cleanup
from app.call_session import cleanup_expired_sessions
from app.agent_tools import warm_market_price_cache
from app.agriculture import get_weather, get_soil_info, get_crop_recommendations
from app.farmer_store import get_or_create_farmer, get_farmer, normalize_phone, update_farmer_profile
from app.activity import get_recent_activity_events
from app.twilio_handlers import router as twilio_router
from app.routes.auction_routes import router as auction_router
from app.routes.seller_routes import router as seller_router
from app.routes.buyer_routes import router as buyer_router
from app.routes.twin_routes import router as twin_router
from app.routes.market_routes import router as market_router
from app.routes.trade_routes import router as trade_router
from app.routes.voice_routes import router as voice_router
from app.routes.advisor_routes import router as advisor_router
from app.services.db import init_extension_tables
from app.services.auction_service import close_expired_listings
from app.marketplace import (
    create_listing, get_listings, get_listing, update_listing,
    delete_listing, contact_seller, get_marketplace_stats,
)
from app.crop_predictor import predict_crops, predict_yield
from app.chatbot import chat as chatbot_chat, upload_document as chatbot_upload, get_history as chatbot_history, analyze_image as chatbot_analyze_image, analyze_pdf as chatbot_analyze_pdf

# ============================================
# Logging Configuration
# ============================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ============================================
# Background Tasks
# ============================================

async def _background_session_cleanup():
    """Periodically clean up expired call sessions."""
    while True:
        await asyncio.sleep(300)  # Every 5 minutes
        try:
            cleanup_expired_sessions(timeout_seconds=1800)
            from app.voice.dialogue import cleanup_states
            cleanup_states(max_idle_seconds=1800)
        except Exception as e:
            logger.error(f"Session cleanup error: {e}")


async def _background_market_cache_refresh():
    """Refresh market cache periodically so voice and dashboard stay fast."""
    while True:
        await asyncio.sleep(300)  # Every 5 minutes
        try:
            await asyncio.to_thread(warm_market_price_cache)
        except Exception as e:
            logger.error(f"Market cache refresh error: {e}")


async def _background_listing_expiry_refresh():
    """Close expired auction listings periodically."""
    while True:
        await asyncio.sleep(30)
        try:
            closed = await asyncio.to_thread(close_expired_listings)
            if closed:
                logger.info(f"Auto-closed {closed} expired listing(s)")
        except Exception as e:
            logger.error(f"Listing expiry refresh error: {e}")


# ============================================
# Application Lifespan
# ============================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan handler.
    - Startup: load models, initialize APIs, pre-generate audio
    - Shutdown: graceful cleanup
    """
    settings = get_settings()
    logger.info("=" * 60)
    logger.info("AI Krishi Voice Assistant — Starting Up")
    logger.info("=" * 60)

    # Ensure audio directory exists
    os.makedirs(settings.AUDIO_DIR, exist_ok=True)

    # 1. Skip Whisper model load
    logger.info("Using Twilio native speech-to-text (Whisper disabled)")

    # 2. Initialize Gemini
    try:
        initialize_gemini()
    except Exception as e:
        logger.error(f"Gemini initialization failed: {e}")
        logger.warning("Continuing without Gemini — responses will use fallback")

    # 3. Pre-generate system audio (greeting, wait, error)
    try:
        pre_generate_system_audio(audio_dir=settings.AUDIO_DIR)
    except Exception as e:
        logger.error(f"System audio pre-generation failed: {e}")

    # 3b. Voice agent: call transcript tables and the fixed Kannada prompts
    # (greeting, hold, re-prompt, goodbye). Prompts are content-addressed, so
    # after the first start this is a no-op that just finds them on disk.
    try:
        from app.voice.call_log import init_call_tables
        from app.voice.speech import prepare_system_prompts
        await asyncio.to_thread(init_call_tables)
        await prepare_system_prompts()
    except Exception as e:
        logger.error(f"Voice prompt preparation failed: {e}")

    # 3c. Build/load the RAG knowledge index (farming schemes, pest/disease,
    # selling mechanics) so the first chatbot question isn't slowed by it.
    try:
        from app.rag import ensure_ready as rag_ensure_ready
        rag_ok = await asyncio.to_thread(rag_ensure_ready)
        logger.info("RAG knowledge index ready" if rag_ok else "RAG knowledge index unavailable")
    except Exception as e:
        logger.error(f"RAG index preparation failed: {e}")

    # 4. Warm market cache at startup to reduce first-request latency
    try:
        await asyncio.to_thread(warm_market_price_cache)
        logger.info("Market price cache warmup completed")
    except Exception as e:
        logger.error(f"Market cache warmup failed: {e}")

    # 4b2. Ensure market intelligence tables and the market registry exist.
    try:
        from app.market.schema import init_market_tables
        from app.trade.schema import init_trade_tables
        await asyncio.to_thread(init_market_tables)
        await asyncio.to_thread(init_trade_tables)
        from app.market import repository as _market_repo
        _coverage = await asyncio.to_thread(_market_repo.get_data_coverage)
        _total = sum(c["records"] for c in _coverage)
        _mock = sum(c["mock_records"] for c in _coverage)
        logger.info(
            "Market intelligence ready: %d price records (%d demo/MOCK) across %d commodities",
            _total, _mock, len(_coverage),
        )
        if _total and _mock == _total:
            logger.warning(
                "All stored market data is DEMO (MOCK) fixture data. Register a personal "
                "data.gov.in key and set DATA_GOV_API_KEY to ingest real mandi prices."
            )
    except Exception as _exc:
        logger.error("Market intelligence tables failed to initialise: %s", _exc)

    # 4b. Ensure auction and digital twin tables exist.
    try:
        await asyncio.to_thread(init_extension_tables)
        logger.info("Auction/twin extension tables initialized")
    except Exception as e:
        logger.error(f"Extension table initialization failed: {e}")

    # 5. Start background cleanup tasks
    cleanup_task = asyncio.create_task(periodic_audio_cleanup(interval_minutes=15))
    session_cleanup_task = asyncio.create_task(_background_session_cleanup())
    market_cache_task = asyncio.create_task(_background_market_cache_refresh())
    listing_expiry_task = asyncio.create_task(_background_listing_expiry_refresh())

    logger.info("=" * 60)
    logger.info(f"Server ready at {settings.BASE_URL}")
    logger.info(f"Twilio webhook: {settings.BASE_URL}/twilio/voice")
    logger.info(f"Call API: {settings.BASE_URL}/api/call")
    logger.info("=" * 60)

    yield  # Application is running

    # Shutdown
    logger.info("Shutting down...")
    cleanup_task.cancel()
    session_cleanup_task.cancel()
    market_cache_task.cancel()
    listing_expiry_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    try:
        await session_cleanup_task
    except asyncio.CancelledError:
        pass
    try:
        await market_cache_task
    except asyncio.CancelledError:
        pass
    try:
        await listing_expiry_task
    except asyncio.CancelledError:
        pass
    logger.info("Shutdown complete.")


# ============================================
# FastAPI Application
# ============================================

app = FastAPI(
    title="AI Krishi Voice Assistant",
    description="Kannada-first agricultural voice assistant using Twilio + Gemini + Whisper",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware. An explicit allowlist replaces the previous
# allow_origins=["*"] + allow_credentials=True combination, which browsers
# reject outright and which signalled that no auth model had been designed.
_cors_origins = resolve_cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=cors_allows_credentials(_cors_origins),
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
)

# Mount audio static files
settings = get_settings()
os.makedirs(settings.AUDIO_DIR, exist_ok=True)
app.mount("/audio", StaticFiles(directory=settings.AUDIO_DIR), name="audio")
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include routers
app.include_router(twilio_router)
app.include_router(auction_router)
app.include_router(seller_router)
app.include_router(buyer_router)
app.include_router(twin_router)
app.include_router(market_router)
app.include_router(trade_router)
app.include_router(voice_router)
app.include_router(advisor_router)


# ============================================
# Health Check
# ============================================

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "AI Krishi Voice Assistant",
        "version": "1.0.0",
    }


@app.get("/api")
async def api_index():
    """API information."""
    return {
        "name": "AI Krishi Voice Assistant (Kannada-first)",
        "version": "1.0.0",
        "endpoints": {
            "frontend": "/",
            "console": "/console",
            "health": "/health",
            "twilio_voice": "/twilio/voice",
            "twilio_gather": "/twilio/gather",
            "twilio_status": "/twilio/status",
            "call_api": "/api/call",
            "process_speech": "/api/process-speech",
            "market_prices": "/api/market-prices",
            "activity_feed": "/api/activity-feed",
            "farmer_portal": "/farmer",
            "buyer_portal": "/buyer",
            "fpo_portal": "/fpo",
            "live_calls": "/calls",
        },
    }


@app.post("/api/farmer/session")
async def api_farmer_session(request: Request):
    """Create/get farmer profile by name and phone with default wallet."""
    body = await request.json()
    name = str(body.get("name", "Farmer")).strip()
    phone = str(body.get("phone", "")).strip()
    city = str(body.get("city", "Kalaburgi")).strip() or "Kalaburgi"

    if not phone:
        raise HTTPException(status_code=400, detail="phone is required")

    farmer = get_or_create_farmer(name=name or "Farmer", phone=phone, city=city)
    
    # We allow the session login to also update district, state, village via update_farmer_profile
    village = body.get("village")
    district = body.get("district")
    state = body.get("state")
    crop_types = body.get("crop_types")
    land_size = body.get("land_size")
    
    if any([village, district, state, crop_types, land_size]):
        farmer = update_farmer_profile(
            phone=phone,
            village=village,
            district=district,
            state=state,
            crop_types=crop_types,
            land_size=land_size
        )

    return {"success": True, "farmer": farmer}


@app.post("/api/farmer/create")
async def api_farmer_create(request: Request):
    """Explicit farmer creation endpoint backed by SQLite."""
    body = await request.json()
    name = str(body.get("name", "Farmer")).strip()
    phone = str(body.get("phone", "")).strip()
    city = str(body.get("city", "Kalaburgi")).strip() or "Kalaburgi"

    if not phone:
        raise HTTPException(status_code=400, detail="phone is required")

    farmer = get_or_create_farmer(name=name or "Farmer", phone=phone, city=city)
    return {"success": True, "farmer": farmer}


@app.get("/api/farmer/{phone}")
async def api_farmer_profile(phone: str):
    """Fetch farmer profile with live weather and soil context."""
    norm_phone = normalize_phone(phone)
    farmer = get_farmer(norm_phone)
    if not farmer:
        raise HTTPException(status_code=404, detail="Farmer not found")

    city = farmer.get("city", "Kalaburgi")
    weather = await get_weather(city)
    soil = get_soil_info(city)

    return {
        "success": True,
        "farmer": farmer,
        "context": {
            "city": city,
            "temperature": weather.get("temperature"),
            "weather_description": weather.get("description"),
            "soil_type": soil.get("soil_type", "Unknown"),
        },
    }


@app.patch("/api/farmer/{phone}")
async def api_farmer_update(phone: str, request: Request):
    """Update farmer profile fields like name/city/ugfid fields."""
    body = await request.json()
    name = str(body.get("name", "")).strip()
    city = str(body.get("city", "")).strip()

    updated = update_farmer_profile(
        phone=phone, 
        name=name or None, 
        city=city or None,
        village=body.get("village"),
        taluk=body.get("taluk"),
        district=body.get("district"),
        state=body.get("state"),
        land_size=body.get("land_size"),
        crop_types=body.get("crop_types")
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Farmer not found")

    return {"success": True, "farmer": updated}


@app.post(
    "/api/farmer/{phone}/call",
    dependencies=[Depends(require_api_key), Depends(rate_limit_calls)],
)
async def api_farmer_call(phone: str, request: Request):
    """Initiate call to farmer number from farmer dashboard."""
    settings = get_settings()
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    name = str(body.get("name", "")).strip()

    norm_phone = normalize_phone(phone)
    if not norm_phone:
        raise HTTPException(status_code=400, detail="Invalid phone number")

    if not twilio_configured():
        raise HTTPException(status_code=500, detail="Twilio credentials not configured")

    client = get_twilio_client()
    voice_url = f"{settings.BASE_URL}/twilio/voice"
    if name:
        voice_url += f"?name={urllib.parse.quote(name)}"

    call = client.calls.create(
        to=norm_phone,
        from_=settings.TWILIO_PHONE_NUMBER,
        url=voice_url,
        status_callback=f"{settings.BASE_URL}/twilio/status",
        status_callback_event=["initiated", "ringing", "answered", "completed"],
    )

    return {
        "success": True,
        "call_sid": call.sid,
        "phone_number": norm_phone,
        "status": call.status,
    }


@app.get("/api/activity-feed")
async def api_activity_feed(limit: int = 30):
    """Returns recent order/sell/system activity events for dashboard log."""
    events = get_recent_activity_events(limit=limit)
    return {"count": len(events), "events": events}


@app.get("/api/activity-stream")
async def api_activity_stream(limit: int = 20):
    """Server-sent events stream for real-time dashboard activity updates."""

    async def event_generator():
        last_signature = ""
        safe_limit = max(1, min(limit, 50))

        while True:
            events = get_recent_activity_events(limit=safe_limit)
            signature = json.dumps(events[:1], sort_keys=True, ensure_ascii=False)

            if signature != last_signature:
                payload = json.dumps({"count": len(events), "events": events}, ensure_ascii=False)
                yield f"data: {payload}\n\n"
                last_signature = signature

            await asyncio.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

# ============================================
# Marketplace API
# ============================================

@app.post("/api/marketplace/listings")
async def api_create_listing(request: Request):
    """Create a new marketplace listing."""
    body = await request.json()
    required = ["crop", "quantity", "price_per_unit"]
    for field in required:
        if not body.get(field):
            raise HTTPException(status_code=400, detail=f"{field} is required")
    listing = create_listing(
        seller_phone=str(body.get("seller_phone", "")).strip(),
        seller_name=str(body.get("seller_name", "Farmer")).strip(),
        crop=str(body["crop"]).strip(),
        quantity=float(body["quantity"]),
        unit=str(body.get("unit", "quintal")).strip(),
        price_per_unit=float(body["price_per_unit"]),
        description=str(body.get("description", "")).strip(),
        location=str(body.get("location", "Kalaburgi")).strip(),
    )
    return {"success": True, "listing": listing}


@app.get("/api/marketplace/listings")
async def api_get_listings(
    crop: str = "", location: str = "", status: str = "active", page: int = 1, limit: int = 20
):
    """Search and filter marketplace listings."""
    result = get_listings(crop_filter=crop, location_filter=location, status=status, page=page, limit=limit)
    return {"success": True, **result}


@app.get("/api/marketplace/listings/{listing_id}")
async def api_get_listing(listing_id: str):
    """Get a single marketplace listing."""
    listing = get_listing(listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    return {"success": True, "listing": listing}


@app.put("/api/marketplace/listings/{listing_id}")
async def api_update_listing(listing_id: str, request: Request):
    """Update a marketplace listing."""
    body = await request.json()
    updated = update_listing(listing_id, **body)
    if not updated:
        raise HTTPException(status_code=404, detail="Listing not found")
    return {"success": True, "listing": updated}


@app.delete("/api/marketplace/listings/{listing_id}")
async def api_delete_listing(listing_id: str):
    """Delete a marketplace listing."""
    deleted = delete_listing(listing_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Listing not found")
    return {"success": True}


@app.post("/api/marketplace/listings/{listing_id}/contact")
async def api_contact_seller(listing_id: str, request: Request):
    """Contact a seller about a listing."""
    body = await request.json()
    result = contact_seller(
        listing_id=listing_id,
        buyer_phone=str(body.get("buyer_phone", "")).strip(),
        buyer_name=str(body.get("buyer_name", "Buyer")).strip(),
        message=str(body.get("message", "")).strip(),
    )
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("reason", "Failed"))
    return result


@app.get("/api/marketplace/stats")
async def api_marketplace_stats():
    """Get marketplace statistics for admin dashboard."""
    return {"success": True, **get_marketplace_stats()}


# ============================================
# Crop Prediction & Yield API
# ============================================

@app.post("/api/predict/crops")
async def api_predict_crops(request: Request):
    """Predict best crops based on soil, weather, and location."""
    body = await request.json()
    try:
        result = await predict_crops(
            soil_type=str(body.get("soil_type", "")).strip(),
            location=str(body.get("location", "Kalaburgi")).strip(),
            season=str(body.get("season", "")).strip(),
        )
        return {"success": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/predict/yield")
async def api_predict_yield(request: Request):
    """Predict expected yield for a crop."""
    body = await request.json()
    crop = str(body.get("crop", "")).strip()
    if not crop:
        raise HTTPException(status_code=400, detail="crop is required")
    try:
        result = await predict_yield(
            crop=crop,
            area_acres=float(body.get("area_acres", 1.0)),
            soil_type=str(body.get("soil_type", "")).strip(),
            rainfall_mm=float(body.get("rainfall_mm", 0)),
            season=str(body.get("season", "kharif")).strip(),
        )
        return {"success": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================
# Chatbot API
# ============================================

@app.post("/api/chatbot/message")
async def api_chatbot_message(request: Request):
    """Send a chat message and get AI response."""
    body = await request.json()
    message = str(body.get("message", "")).strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    result = await chatbot_chat(
        message=message,
        session_id=str(body.get("session_id", "")).strip(),
        farmer_context=str(body.get("farmer_context", "")).strip(),
    )
    return {"success": True, **result}


@app.post("/api/chatbot/upload")
async def api_chatbot_upload(request: Request):
    """Upload a document for chatbot context."""
    body = await request.json()
    filename = str(body.get("filename", "document.txt")).strip()
    content = str(body.get("content", "")).strip()
    session_id = str(body.get("session_id", "")).strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    result = chatbot_upload(session_id=session_id, filename=filename, content=content)
    return {"success": True, **result}


@app.get("/api/chatbot/history/{session_id}")
async def api_chatbot_history(session_id: str):
    """Get chat history for a session."""
    result = chatbot_history(session_id)
    return {"success": True, **result}


@app.get("/api/chatbot/rag/search")
async def api_rag_search(q: str, k: int = 3):
    """Debug/inspection endpoint: raw top-k knowledge base matches for a query."""
    from app.rag import retrieve
    hits = await asyncio.to_thread(retrieve, q, k)
    return {"success": True, "query": q, "results": [h.to_dict() for h in hits]}


@app.post("/api/chatbot/upload-file")
async def api_chatbot_upload_file(
    file: UploadFile = File(...),
    session_id: str = Form(""),
    farmer_context: str = Form(""),
):
    """Upload an image or PDF for multimodal AI analysis."""
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    filename = file.filename.lower()
    content_type = file.content_type or ""
    file_bytes = await file.read()

    if not file_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    # File size limit: 10MB
    if len(file_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large. Maximum 10MB.")

    # Route to appropriate analyzer
    if content_type.startswith("image/") or filename.endswith((".jpg", ".jpeg", ".png", ".webp")):
        # Image analysis with Gemini Vision
        mime = content_type if content_type.startswith("image/") else "image/jpeg"
        result = await chatbot_analyze_image(
            image_bytes=file_bytes,
            filename=file.filename,
            mime_type=mime,
            session_id=session_id.strip(),
            farmer_context=farmer_context.strip(),
        )
        return {"success": True, **result}

    elif content_type == "application/pdf" or filename.endswith(".pdf"):
        # PDF extraction + analysis
        result = await chatbot_analyze_pdf(
            pdf_bytes=file_bytes,
            filename=file.filename,
            session_id=session_id.strip(),
            farmer_context=farmer_context.strip(),
        )
        return {"success": True, **result}

    else:
        # Fallback: treat as text document
        try:
            text_content = file_bytes.decode("utf-8", errors="replace")
        except Exception:
            text_content = str(file_bytes[:5000])
        result = chatbot_upload(
            session_id=session_id.strip(),
            filename=file.filename,
            content=text_content,
        )
        return {"success": True, **result}


# ============================================
# Call Agent Sync API
# ============================================

_sync_state = {"last_sync": 0, "sync_count": 0, "status": "idle"}


@app.get("/api/sync/status")
async def api_sync_status():
    """Get call agent sync health status."""
    return {
        "success": True,
        "last_sync": _sync_state["last_sync"],
        "sync_count": _sync_state["sync_count"],
        "status": _sync_state["status"],
        "dashboard_healthy": True,
        "call_agent_connected": _sync_state["last_sync"] > 0,
    }


@app.post("/api/sync/push")
async def api_sync_push(request: Request):
    """Call agent pushes updates to dashboard."""
    import time as _time
    body = await request.json()
    _sync_state["last_sync"] = int(_time.time())
    _sync_state["sync_count"] += 1
    _sync_state["status"] = "synced"

    event_type = str(body.get("event_type", "sync")).strip()
    data = body.get("data", {})

    # Process sync data based on event type
    if event_type == "farmer_update" and data.get("phone"):
        update_farmer_profile(
            phone=str(data["phone"]),
            name=data.get("name"),
            city=data.get("city"),
        )
    elif event_type == "marketplace_listing" and data.get("crop"):
        create_listing(
            seller_phone=str(data.get("seller_phone", "")).strip(),
            seller_name=str(data.get("seller_name", "Farmer")).strip(),
            crop=str(data["crop"]).strip(),
            quantity=float(data.get("quantity", 1)),
            unit=str(data.get("unit", "quintal")).strip(),
            price_per_unit=float(data.get("price_per_unit", 0)),
            description=str(data.get("description", "")).strip(),
            location=str(data.get("location", "Kalaburgi")).strip(),
        )

    return {"success": True, "sync_count": _sync_state["sync_count"]}


@app.get("/api/sync/pull")
async def api_sync_pull():
    """Call agent pulls latest data from dashboard."""
    import time as _time
    _sync_state["last_sync"] = int(_time.time())
    _sync_state["sync_count"] += 1
    _sync_state["status"] = "synced"

    from app.agent_tools import get_market_price
    market_data = []
    for crop in ["Onion", "Tomato", "Potato", "Chilli", "Maize"]:
        market_data.append(get_market_price(crop=crop, mandi_location="Kalaburgi"))

    listings = get_listings(limit=10)
    stats = get_marketplace_stats()
    events = get_recent_activity_events(limit=10)

    return {
        "success": True,
        "market_prices": market_data,
        "marketplace": listings,
        "marketplace_stats": stats,
        "recent_activity": events,
        "sync_count": _sync_state["sync_count"],
    }


@app.get("/api/market-prices")
async def api_market_prices(state: str = "Karnataka", district: str = "", commodity: str = ""):
    """Returns live mandi prices from data.gov.in with fallback to agent_tools."""
    from app.gov_data import fetch_mandi_prices

    records = await fetch_mandi_prices(
        state=state,
        district=district or "Kalaburgi",
        commodity=commodity,
        limit=20,
    )

    if records:
        live_data = []
        for r in records:
            crop = r.get("commodity", "Unknown")
            abbr = crop[:2].upper()
            modal = float(r.get("modal_price", 0))
            min_p = float(r.get("min_price", 0))
            max_p = float(r.get("max_price", 0))
            diff = modal - min_p
            trend_dir = "up" if diff >= 0 else "down"

            live_data.append({
                "crop": crop,
                "abbr": abbr,
                "price": f"{modal:.0f}",
                "price_unit": "/Qtl",
                "min_price": f"{min_p:.0f}",
                "max_price": f"{max_p:.0f}",
                "trend": "Live",
                "trend_dir": trend_dir,
                "diff": f"{abs(diff):.0f}",
                "market": r.get("market", ""),
                "variety": r.get("variety", ""),
                "date": r.get("arrival_date", ""),
                "district": r.get("district", ""),
                "state": r.get("state", ""),
            })
        return {"source": "data.gov.in", "data": live_data, "total": len(live_data)}

    # Fallback to old agent_tools
    from app.agent_tools import get_market_price
    crops = ["Onion", "Tomato", "Potato", "Chilli", "Maize"]
    fallback = []
    for crop in crops:
        info = get_market_price(crop=crop, mandi_location="Kalaburgi")
        fallback.append({
            "crop": crop,
            "abbr": crop[:2].upper(),
            "price": str(info.get("current_price", "N/A")).replace("INR/Quintal", ""),
            "price_unit": "/Qtl",
            "trend": "Estimated",
            "trend_dir": "up",
            "diff": "0",
            "market": "Kalaburgi APMC",
        })
    return {"source": "fallback", "data": fallback, "total": len(fallback)}


@app.get("/api/gov/mandi-prices")
async def api_gov_mandi_prices(state: str = "Karnataka", district: str = "", commodity: str = "", limit: int = 20):
    """Direct passthrough to data.gov.in mandi daily prices API."""
    from app.gov_data import fetch_mandi_prices
    records = await fetch_mandi_prices(state=state, district=district, commodity=commodity, limit=limit)
    return {"source": "data.gov.in", "records": records, "count": len(records)}


@app.get("/api/gov/variety-prices")
async def api_gov_variety_prices(state: str = "Karnataka", district: str = "Kalaburgi", commodity: str = "", limit: int = 20):
    """Variety-wise daily market prices from data.gov.in."""
    from app.gov_data import fetch_variety_prices
    records = await fetch_variety_prices(state=state, district=district, commodity=commodity, limit=limit)
    return {"source": "data.gov.in", "records": records, "count": len(records)}


@app.get("/api/gov/pmfby")
async def api_gov_pmfby(district: str = "", limit: int = 20):
    """PMFBY crop insurance enrollment and claims data from data.gov.in."""
    from app.gov_data import fetch_pmfby_data
    records = await fetch_pmfby_data(district=district, limit=limit)
    return {"source": "data.gov.in", "records": records, "count": len(records)}

# ============================================
# Frontend
# ============================================
# One product frontend for the current feature set. The earlier Stitch pages
# (wallet, input orders, auctions, crop prediction) describe a different
# product and are no longer served; their URLs redirect here.

_APP_SHELL = os.path.join("static", "app", "index.html")


def _app_shell():
    from fastapi.responses import FileResponse
    return FileResponse(_APP_SHELL, headers={"Cache-Control": "no-cache"})


@app.get("/", include_in_schema=False)
async def app_home():
    return _app_shell()


@app.get("/prices", include_in_schema=False)
@app.get("/advisor", include_in_schema=False)
@app.get("/farmer", include_in_schema=False)
@app.get("/buyer", include_in_schema=False)
@app.get("/fpo", include_in_schema=False)
@app.get("/calls", include_in_schema=False)
async def app_section():
    return _app_shell()


_LEGACY_PAGES = {
    "/app": "/", "/dashboard": "/console", "/stitch": "/", "/stitch/landing": "/",
    "/stitch/farmer": "/farmer", "/stitch/farmer/profile": "/farmer", "/stitch/admin": "/console",
    "/stitch/seller": "/farmer", "/stitch/buyer": "/buyer", "/stitch/marketplace": "/buyer",
    "/stitch/crop-prediction": "/", "/stitch/yield-prediction": "/", "/stitch/chatbot": "/calls",
    "/stitch/insurance": "/", "/stitch/gov-schemes": "/", "/stitch/signin": "/", "/stitch/404": "/",
}


def _legacy_redirect(target: str):
    from fastapi.responses import RedirectResponse

    async def handler():
        return RedirectResponse(target, status_code=308)
    return handler


for _path, _target in _LEGACY_PAGES.items():
    app.add_api_route(_path, _legacy_redirect(_target), methods=["GET"], include_in_schema=False)


@app.get("/farmer/profile/{ugfid}", include_in_schema=False)
async def legacy_farmer_profile(ugfid: str):
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/farmer", status_code=308)


@app.get("/api/ugfid/{ugfid}")
async def api_get_farmer_by_ugfid(ugfid: str):
    """Fetch farmer public profile by UGFID for digital identity display."""
    from app.farmer_store import _connect, _load_farmer
    conn = _connect()
    try:
        row = conn.execute("SELECT phone FROM farmers WHERE ugfid = ?", (str(ugfid).upper(),)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Identity not found")
        farmer = _load_farmer(conn, row["phone"])
        if not farmer:
            raise HTTPException(status_code=404, detail="Identity not found")
        
        # Omit sensitive data like wallet_balance, exact transactions, phone last 4 only
        safe_phone = farmer["phone"]
        masked_phone = safe_phone[:-4] + "****" if len(safe_phone) > 4 else safe_phone
        return {
            "success": True,
            "farmer": {
                "name": farmer["name"],
                "phone": masked_phone,
                "city": farmer["city"],
                "village": farmer.get("village", "N/A"),
                "taluk": farmer.get("taluk", "N/A"),
                "district": farmer.get("district", "N/A"),
                "state": farmer.get("state", "N/A"),
                "land_size": farmer.get("land_size", "N/A"),
                "crop_types": farmer.get("crop_types", "N/A"),
                "ugfid": farmer["ugfid"],
            }
        }
    finally:
        conn.close()

# ============================================
# Global Exception Handler
# ============================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all exception handler to prevent unhandled errors."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc)},
    )


@app.get("/console", include_in_schema=False)
async def market_console():
    """
    Market console: the operational UI for price discovery, the marketplace,
    settlement and the audit trail. Reads the same APIs the voice agent calls.
    """
    from fastapi.responses import FileResponse

    return FileResponse("static/console.html")
