"""
Twilio webhook handlers for the AI Krishi Voice Assistant.

Endpoints:
- POST /twilio/voice      — Incoming call webhook (greeting + first Gather)
- POST /twilio/gather     — Process speech recording → AI → TTS → Play
- POST /twilio/status     — Call status callback
- POST /api/call          — Outbound "Call Me" endpoint
"""

import os
import hashlib
import asyncio
import time
import logging
import re
from typing import Optional

from fastapi import APIRouter, Request, Form, HTTPException, BackgroundTasks, Depends
from fastapi.responses import Response

from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import VoiceResponse, Gather

from app.config import get_settings, get_twilio_client, twilio_configured
from app.security import (
    require_api_key,
    verify_twilio_signature,
    rate_limit_calls,
    rate_limit_ai,
)
from app.translation import translate_to_english, translate_to_kannada
from app.gemini_ai import get_farming_advice
from app.tts import generate_kannada_audio, get_greeting_audio, get_wait_audio, get_error_audio
from app.agriculture import get_weather, get_soil_info, format_weather_string
from app.agent_tools import (
    get_market_price,
    find_dealers,
    place_order,
    negotiate_market_deal,
    sell_crop_autonomously,
)
from app.audio_manager import get_audio_url
from app.activity import publish_activity_event, get_recent_activity_events
from app.cache import get_cached_response, set_cached_response
from app.call_session import get_or_create_session, remove_session, CallSession
from app.farmer_store import estimate_order_amount, debit_wallet, record_order, get_or_create_farmer, get_farmer
from app.services.auction_service import create_listing as create_auction_listing
from app.services.auction_service import place_bid as place_auction_bid
from app.services.auction_service import get_listings as get_auction_listings
from app.services.auction_service import get_listing_by_id as get_auction_listing_by_id
from app.services.twin_service import get_twin

logger = logging.getLogger(__name__)

router = APIRouter()


def _is_real_twilio_call_sid(call_sid: str) -> bool:
    """True when SID looks like a real Twilio call SID (CA + 32 hex chars)."""
    return bool(re.fullmatch(r"CA[0-9a-fA-F]{32}", (call_sid or "").strip()))


def _resolve_farmer_phone(from_number: str, to_number: str) -> str:
    """
    Choose likely farmer phone from Twilio webhook fields.
    For outbound calls: To is usually farmer, From is Twilio number.
    For inbound calls: From is usually farmer.
    """
    from_n = (from_number or "").strip()
    to_n = (to_number or "").strip()

    if to_n.startswith("+91"):
        return to_n
    if from_n.startswith("+91"):
        return from_n
    if to_n:
        return to_n
    return from_n


def _is_market_price_query(english_query: str) -> bool:
    query = english_query.lower()
    return any(token in query for token in ["price", "rate", "market", "mandi", "apmc", "how much"])


def _is_sell_intent(english_query: str) -> bool:
    query = english_query.lower()
    return any(token in query for token in ["want to sell", "sell", "selling", "can you sell", "dispose"])


def _is_order_intent(english_query: str) -> bool:
    query = english_query.lower()
    return any(token in query for token in ["want to buy", "buy", "order", "book", "purchase"])


def _is_bid_intent(english_query: str) -> bool:
    query = english_query.lower()
    return any(token in query for token in ["bid", "place bid", "bidding", "offer price", "my offer", "quote price"])


def _is_affirmative(english_query: str) -> bool:
    query = english_query.lower().strip()
    positive_words = ["yes", "ok", "okay", "confirm", "do it", "proceed", "haan", "ha", "sari", "correct"]
    return any(word in query for word in positive_words)


def _is_negative(english_query: str) -> bool:
    query = english_query.lower().strip()
    negative_words = ["no", "cancel", "stop", "beda", "don't", "do not"]
    return any(word in query for word in negative_words)


def _extract_quantity(english_query: str, default_value: str) -> str:
    import re
    match = re.search(r"(\d+\s*(?:kg|quintal|quintals|ton|tons|bags|bag|litre|litres|bottles|bottle)?)", english_query.lower())
    if match:
        return match.group(1).strip()
    return default_value


def _detect_product_from_query(english_query: str, fallback_crop: str) -> str:
    query = english_query.lower()
    product_map = {
        "urea": "Urea",
        "dap": "DAP",
        "seed": "Hybrid Seeds",
        "seeds": "Hybrid Seeds",
        "pesticide": "Pesticides",
        "pesticides": "Pesticides",
        "neem": "Neem Oil",
        "spray": "Pesticide Spray",
        "fertilizer": "Fertilizer",
        "fertiliser": "Fertilizer",
    }
    for key, value in product_map.items():
        if key in query:
            return value
    if fallback_crop and fallback_crop != "Unknown":
        return f"{fallback_crop} Input Pack"
    return "Urea"


def _estimate_buy_price(product: str, quantity: str) -> str:
    estimate = estimate_order_amount(product, quantity)
    return (
        f"INR {estimate['unit_price']:.0f} per {estimate['price_unit']} "
        f"(estimated total INR {estimate['amount']:.0f})"
    )


def _quantity_to_kg(quantity_text: str) -> float:
    text = (quantity_text or "").lower().strip()
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(kg|quintal|quintals|ton|tons)?", text)
    if not match:
        return 50.0

    value = float(match.group(1))
    unit = (match.group(2) or "kg").lower()
    if unit in ("quintal", "quintals"):
        return value * 100.0
    if unit in ("ton", "tons"):
        return value * 1000.0
    return value


def _extract_bid_price_per_kg(english_query: str) -> Optional[float]:
    query = (english_query or "").lower()

    patterns = [
        r"(?:bid|offer|quote|at)\s*(?:inr|rs|rupees)?\s*([0-9]+(?:\.[0-9]+)?)",
        r"([0-9]+(?:\.[0-9]+)?)\s*(?:rupees|rs|inr)\s*(?:per\s*kg|kg)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, query)
        if match:
            return float(match.group(1))

    nums = re.findall(r"\b([0-9]+(?:\.[0-9]+)?)\b", query)
    if nums:
        try:
            return float(nums[0])
        except Exception:
            return None
    return None


def _extract_listing_id_from_query(english_query: str) -> str:
    match = re.search(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", (english_query or "").lower())
    return match.group(0) if match else ""


def _pick_open_listing_for_bid(english_query: str, session_crop: str) -> Optional[dict]:
    requested_listing_id = _extract_listing_id_from_query(english_query)
    if requested_listing_id:
        listing = get_auction_listing_by_id(requested_listing_id)
        if listing and str(listing.get("status", "")).upper() == "OPEN":
            return listing

    open_listings = get_auction_listings(status="OPEN", limit=100)
    if not open_listings:
        return None

    crop_hint = (session_crop or "").strip().lower()
    if crop_hint and crop_hint != "unknown":
        for item in open_listings:
            if crop_hint in str(item.get("crop_name", "")).lower():
                return item

    query = (english_query or "").lower()
    for item in open_listings:
        if str(item.get("crop_name", "")).lower() in query:
            return item

    return open_listings[0]


def _infer_base_price_per_kg(crop: str, location: str) -> float:
    market = get_market_price(crop=crop or "Onion", mandi_location=location or "Kalaburgi")
    price_text = str(market.get("current_price", "0"))
    number_match = re.search(r"([0-9]+(?:\.[0-9]+)?)", price_text.replace(",", ""))
    if not number_match:
        return 20.0

    value = float(number_match.group(1))
    lower_text = price_text.lower()
    if "quintal" in lower_text or "/qtl" in lower_text:
        return round(max(1.0, value / 100.0), 2)
    return round(max(1.0, value), 2)


def _build_twin_context(phone: str) -> str:
    try:
        twin = get_twin(phone)
    except Exception:
        return ""

    crops = ", ".join(twin.get("crops_grown", [])[:5]) or "unknown"
    preferred = ", ".join(twin.get("preferred_crops", [])[:3]) or "unknown"
    return (
        f"[Twin context: crops_grown={crops}; preferred_crops={preferred}; "
        f"soil_type={twin.get('soil_type', 'Unknown')}; avg_yield={twin.get('avg_yield', 0)}; "
        f"risk_score={twin.get('risk_score', 0)}]"
    )


def _build_direct_market_response(crop: str, location: str) -> str:
    crop_name = crop if crop and crop != "Unknown" else "Onion"
    location_name = location if location else "Kalaburgi"
    market = get_market_price(crop=crop_name, mandi_location=location_name)
    current_price = market.get("current_price", "not available")
    trend = market.get("market_trend", "")
    mandi = market.get("mandi", "Kalaburgi APMC")
    return (
        f"Current {crop_name} price at {mandi} is {current_price}. "
        f"{trend}"
    ).strip()



# ============================================
# PERSONALIZED GREETING CACHE
# ============================================
# Generating TTS inside the /twilio/voice webhook cost ~5s of dead air on the
# call. We cache per-name greetings and pre-warm them at dial time instead.
_GREETING_CACHE: dict = {}


def build_personalized_greeting(name: str) -> Optional[str]:
    """
    Return an audio URL for a personalized Kannada greeting.

    The MP3 is stored under a filename derived from a hash of the greeting text,
    so an already-generated greeting is reused instantly - including by a
    separate process (e.g. call.py pre-warming before it dials).
    """
    name = (name or "").strip()
    if not name:
        return None

    cached = _GREETING_CACHE.get(name.lower())
    if cached:
        return cached

    settings = get_settings()
    text = (
        f"ನಮಸ್ಕಾರ {name}, AI ಕೃಷಿ "
        f"ಸಹಾಲಕಕ್ಕೆ ಸ್ವಾಗತ. "
        f"ದಯವಿಟ್ಟು ನಿಮ್ಮ ಕೃಷಿ "
        f"ಸಮಸ್ಯೆಯನ್ನು ಹೇಳಿ."
    )

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
    stable_name = f"greet_{digest}.mp3"
    stable_path = os.path.join(settings.AUDIO_DIR, stable_name)

    if os.path.exists(stable_path) and os.path.getsize(stable_path) > 0:
        url = get_audio_url(stable_name)
        _GREETING_CACHE[name.lower()] = url
        logger.info(f"Reusing cached greeting for '{name}'")
        return url

    logger.info(f"Generating personalized greeting for: {name}")
    try:
        filename = generate_kannada_audio(text, audio_dir=settings.AUDIO_DIR)
    except Exception as e:
        logger.warning(f"Personalized greeting generation failed for '{name}': {e}")
        return None

    if not filename:
        return None

    try:
        os.replace(os.path.join(settings.AUDIO_DIR, filename), stable_path)
    except Exception as e:
        logger.warning(f"Could not stabilise greeting filename: {e}")
        stable_name = filename

    url = get_audio_url(stable_name)
    _GREETING_CACHE[name.lower()] = url
    return url


def _twiml_response(twiml: VoiceResponse) -> Response:
    """Return a TwiML XML response."""
    return Response(
        content=str(twiml),
        media_type="application/xml",
    )


def _build_gather_twiml(
    audio_url: Optional[str] = None,
    session: Optional[CallSession] = None,
) -> VoiceResponse:
    """
    Build TwiML that plays audio (if provided) and then gathers speech.
    Always uses <Play> (never <Say>) for Kannada.
    """
    response = VoiceResponse()
    settings = get_settings()

    # Play audio response if provided
    if audio_url:
        response.play(audio_url)

    # Gather speech input
    gather = Gather(
        input="speech",
        action=f"{settings.BASE_URL}/twilio/gather",
        method="POST",
        language="kn-IN",
        speech_timeout="auto",
        timeout=10,
    )

    response.append(gather)

    # If no speech detected, prompt again
    wait_audio = get_wait_audio()
    if wait_audio:
        response.play(get_audio_url(wait_audio))
    response.redirect(f"{settings.BASE_URL}/twilio/voice")

    return response


# ============================================
# INCOMING CALL WEBHOOK
# ============================================

@router.post(
    "/twilio/voice",
    dependencies=[Depends(verify_twilio_signature)],
)
async def handle_incoming_call(
    request: Request,
    CallSid: str = Form(default=""),
    From: str = Form(default=""),
    To: str = Form(default=""),
):
    """
    Handle incoming Twilio call.
    Plays personalized Kannada greeting and sets up first speech Gather.
    """
    logger.info(f"Incoming call: CallSid={CallSid}, From={From}")

    farmer_phone = _resolve_farmer_phone(From, To)

    # Create/get session for this call
    session = get_or_create_session(call_sid=CallSid, phone_number=farmer_phone)

    # Ensure farmer profile exists for wallet/dashboard usage.
    try:
        get_or_create_farmer(name=request.query_params.get("name", "Farmer"), phone=farmer_phone, city=session.location)
    except Exception:
        pass

    settings = get_settings()

    # Build TwiML with greeting
    name = request.query_params.get("name", "").strip()
    
    # Served from cache when /api/call pre-warmed it, so the caller hears the
    # greeting immediately instead of several seconds of TTS dead air.
    greeting_url = build_personalized_greeting(name)

    # Fallback to generic pre-generated greeting if no name or dynamic generation fails
    if not greeting_url:
        greeting_audio = get_greeting_audio()
        if greeting_audio:
            greeting_url = get_audio_url(greeting_audio)

    twiml = _build_gather_twiml(audio_url=greeting_url, session=session)

    return _twiml_response(twiml)


# ============================================
# SPEECH GATHER HANDLER (MAIN PROCESSING LOOP)
# ============================================

@router.post(
    "/twilio/gather",
    dependencies=[Depends(verify_twilio_signature)],
)
async def handle_gather(
    request: Request,
    background_tasks: BackgroundTasks,
    CallSid: str = Form(default=""),
    SpeechResult: str = Form(default=""),
    Confidence: str = Form(default="0"),
    RecordingUrl: str = Form(default=""),
    From: str = Form(default=""),
    To: str = Form(default=""),
):
    """
    Process gathered speech from farmer immediately returning a Hold Pause.
    Spawns a background task to: Translate → Gemini → TTS → Twilio Call Update.
    """
    start_time = time.time()
    settings = get_settings()

    logger.info(f"Gather received: CallSid={CallSid}, SpeechResult='{SpeechResult[:80] if SpeechResult else 'empty'}'")

    farmer_phone = _resolve_farmer_phone(From, To)
    session = get_or_create_session(call_sid=CallSid, phone_number=farmer_phone)

    try:
        speech_text = ""
        if SpeechResult:
            speech_text = SpeechResult.strip()
            logger.info(f"Using Twilio SpeechResult: '{speech_text[:80]}'")
        else:
            logger.warning("No speech detected by Twilio (or Whisper disabled)")

        if not speech_text:
            logger.warning("No speech detected in gather")
            wait_audio = get_wait_audio()
            url = get_audio_url(wait_audio) if wait_audio else None
            twiml = _build_gather_twiml(audio_url=url, session=session)
            return _twiml_response(twiml)

        # SPARK OFF BACKGROUND PROCESSING!
        background_tasks.add_task(
            process_speech_async,
            CallSid=CallSid,
            speech_text=speech_text,
            session=session,
            settings=settings
        )
        
        # RETURN IMMEDIATE RESPONSE TO SATISFY 15-SEC TIMEOUT!
        response = VoiceResponse()
        wait_audio = get_wait_audio()
        if wait_audio:
            response.play(get_audio_url(wait_audio))
        else:
            response.play(f"{settings.BASE_URL}/audio/wait.mp3")
        
        # Pause to keep the connection open while the LLM generates
        response.pause(length=40)
        
        elapsed = time.time() - start_time
        logger.info(f"Gather webhook returned immediately in {elapsed:.3f}s to hold line.")
        
        return _twiml_response(response)

    except Exception as e:
        logger.error(f"Error processing gather synchronously: {e}", exc_info=True)
        error_audio = get_error_audio()
        if error_audio:
            error_url = get_audio_url(error_audio)
            twiml = _build_gather_twiml(audio_url=error_url, session=session)
        else:
            twiml = VoiceResponse()
            twiml.say("Sorry, please try again.", language="en-US")
            twiml.redirect(f"{settings.BASE_URL}/twilio/voice")

        return _twiml_response(twiml)


async def process_speech_async(CallSid: str, speech_text: str, session: CallSession, settings) -> None:
    """The heavy lifter: Translates, hits Gemini, TTS, and updates the active Twilio Call."""
    try:
        start_time = time.time()
        
        # ---- STEP 2: Translate Kannada → English ----
        english_query = translate_to_english(speech_text)
        logger.info(f"[ASYNC] Translated to English: '{english_query[:80]}'")

        # Update crop/location context as early as possible for cache keys and direct handlers.
        _update_session_context(session, english_query)

        # ---- STEP 3: Get agricultural context locally ----
        soil_data = get_soil_info(session.location)
        soil_str = soil_data.get("soil_type", "Unknown")

        # ---- STEP 4A: Handle pending confirmation actions first ----
        if session.pending_action:
            if _is_affirmative(english_query):
                pending_type = session.pending_action.get("type", "")

                if pending_type == "sell":
                    result = sell_crop_autonomously(
                        crop=session.pending_action.get("crop", session.crop),
                        quantity=session.pending_action.get("quantity", "10 quintals"),
                        location=session.pending_action.get("location", session.location),
                        farmer_phone=session.phone_number,
                    )
                    buyer = result.get("assigned_buyer", {})
                    english_response = (
                        f"Great. I have initiated the sale. Buyer {buyer.get('name', 'assigned buyer')} "
                        f"will contact you on {buyer.get('contact', 'registered number')}. "
                        f"Sale lead ID is {result.get('lead_id', 'generated')}."
                    )
                    publish_activity_event(
                        event_type="sell_done",
                        title="Crop Sale Initiated",
                        details=(
                            f"{session.pending_action.get('crop', 'Crop')} | "
                            f"{session.pending_action.get('quantity', 'Qty')} | "
                            f"Buyer: {buyer.get('name', 'Assigned buyer')} | "
                            f"Lead: {result.get('lead_id', 'N/A')}"
                        ),
                        meta={
                            "call_sid": CallSid,
                            "buyer_id": buyer.get("buyer_id", ""),
                            "buyer_contact": buyer.get("contact", ""),
                            "farmer_phone": session.phone_number,
                        },
                    )
                elif pending_type == "order":
                    product = session.pending_action.get("product", "Urea")
                    quantity = session.pending_action.get("quantity", "1 bag")
                    estimate = estimate_order_amount(product, quantity)
                    wallet_result = debit_wallet(
                        phone=session.phone_number,
                        amount=estimate["amount"],
                        description=f"Order placed: {product} {quantity}",
                        metadata={
                            "call_sid": CallSid,
                            "dealer_id": session.pending_action.get("dealer_id", ""),
                            "dealer_name": session.pending_action.get("dealer_name", ""),
                        },
                    )

                    if not wallet_result.get("success"):
                        english_response = (
                            f"Order could not be placed due to insufficient wallet balance. "
                            f"Required INR {estimate['amount']:.0f}, available INR "
                            f"{wallet_result.get('wallet_balance', 0):.0f}."
                        )
                        publish_activity_event(
                            event_type="order_failed",
                            title="Order Failed - Low Balance",
                            details=(
                                f"{product} | Needed: INR {estimate['amount']:.0f} | "
                                f"Available: INR {wallet_result.get('wallet_balance', 0):.0f}"
                            ),
                            meta={"call_sid": CallSid, "farmer_phone": session.phone_number},
                        )
                    else:
                        order_result = place_order(
                            product=product,
                            quantity=quantity,
                            dealer_id=session.pending_action.get("dealer_id", "DLR-101"),
                        )
                        record_order(
                            phone=session.phone_number,
                            order_data={
                                "order_id": order_result.get("order_id", "N/A"),
                                "product": product,
                                "quantity": quantity,
                                "amount": estimate["amount"],
                                "dealer_name": session.pending_action.get("dealer_name", "Dealer"),
                                "status": "placed",
                                "timestamp": int(time.time()),
                            },
                        )
                        english_response = (
                            f"Done. Your order is placed with {session.pending_action.get('dealer_name', 'the dealer')}. "
                            f"Order ID is {order_result.get('order_id', 'generated')}. "
                            f"INR {estimate['amount']:.0f} has been deducted from your wallet. "
                            f"Current balance is INR {wallet_result.get('wallet_balance', 0):.0f}."
                        )
                        publish_activity_event(
                            event_type="order_done",
                            title="Input Order Placed",
                            details=(
                                f"{product} | {quantity} | Dealer: {session.pending_action.get('dealer_name', 'Dealer')} | "
                                f"Order: {order_result.get('order_id', 'N/A')} | Amount: INR {estimate['amount']:.0f}"
                            ),
                            meta={
                                "call_sid": CallSid,
                                "dealer_id": session.pending_action.get("dealer_id", ""),
                                "dealer_phone": session.pending_action.get("dealer_phone", ""),
                                "farmer_phone": session.phone_number,
                            },
                        )
                else:
                    english_response = "I did not find a pending action. Please tell me what you want to do next."

                session.pending_action = None
                set_cached_response(
                    query=english_query,
                    crop=session.crop,
                    location=session.location,
                    response=english_response,
                )

            elif _is_negative(english_query):
                pending_type = session.pending_action.get("type", "action")
                session.pending_action = None
                english_response = "Okay, I have canceled that action. Tell me what you want to do next."
                publish_activity_event(
                    event_type="action_cancelled",
                    title="Action Cancelled",
                    details=f"Farmer cancelled pending {pending_type} action.",
                    meta={"call_sid": CallSid, "farmer_phone": session.phone_number},
                )
            else:
                pending_type = session.pending_action.get("type", "action")
                english_response = (
                    f"Please say yes to confirm the {pending_type} action, or say no to cancel."
                )

        # ---- STEP 4B: Propose sell workflow and ask confirmation ----
        elif _is_sell_intent(english_query):
            crop_name = session.crop if session.crop != "Unknown" else "Onion"
            qty_text = _extract_quantity(english_query, default_value="50 kg")
            qty_kg = _quantity_to_kg(qty_text)
            base_price = _infer_base_price_per_kg(crop_name, session.location)

            listing = create_auction_listing(
                farmer_phone=session.phone_number,
                crop_name=crop_name,
                quantity=qty_kg,
                base_price_per_unit=base_price,
                expires_in_minutes=30,
            )
            highest = (listing.get("highest_bid") or {}).get("bid_price_per_unit", base_price)

            english_response = (
                f"Your auction listing is now live for {qty_kg:.0f} kg {crop_name}. "
                f"Current highest bid is INR {highest:.2f} per kg. "
                f"Listing id is {listing.get('id', 'generated')}."
            )

        # ---- STEP 4C: Bid on an open listing ----
        elif _is_bid_intent(english_query):
            listing = _pick_open_listing_for_bid(english_query, session.crop)
            bid_price = _extract_bid_price_per_kg(english_query)
            qty_text = _extract_quantity(english_query, default_value="50 kg")
            qty_kg = _quantity_to_kg(qty_text)

            if not listing:
                english_response = "There are no open listings right now. Please try again after a new listing is posted."
            elif bid_price is None:
                english_response = "Please tell me your bid price per kg, for example bid 32 rupees per kg."
            else:
                max_qty = float(listing.get("quantity") or 0)
                final_qty = min(max(qty_kg, 1.0), max_qty if max_qty > 0 else qty_kg)
                try:
                    bid = place_auction_bid(
                        listing_id=listing["id"],
                        buyer_id=session.phone_number,
                        bid_price_per_unit=float(bid_price),
                        quantity=float(final_qty),
                    )
                    updated_listing = get_auction_listing_by_id(listing["id"]) or listing
                    highest_bid = (updated_listing.get("highest_bid") or {}).get("bid_price_per_unit", bid_price)
                    english_response = (
                        f"Your bid is placed on listing {listing['id']} for {final_qty:.0f} kg at INR {bid_price:.2f} per kg. "
                        f"Current highest bid is INR {highest_bid:.2f} per kg."
                    )
                except ValueError as e:
                    english_response = f"Bid could not be placed: {str(e)}"

        # ---- STEP 4D: Propose buy/order workflow and ask confirmation ----
        elif _is_order_intent(english_query):
            product = _detect_product_from_query(english_query, session.crop)
            quantity = _extract_quantity(english_query, default_value="1 bag")
            dealers = find_dealers(product=product, location=session.location)
            dealer = dealers[0] if dealers else {"dealer_id": "DLR-101", "name": "Local Dealer"}
            estimate = estimate_order_amount(product, quantity)
            quote = _estimate_buy_price(product, quantity)

            farmer = get_farmer(session.phone_number)
            if not farmer and session.phone_number:
                try:
                    farmer = get_or_create_farmer(name="Farmer", phone=session.phone_number, city=session.location)
                except Exception:
                    farmer = None
            wallet_balance = float((farmer or {}).get("wallet_balance", 0.0))

            session.pending_action = {
                "type": "order",
                "product": product,
                "quantity": quantity,
                "dealer_id": dealer.get("dealer_id", "DLR-101"),
                "dealer_name": dealer.get("name", "Local Dealer"),
                "dealer_phone": dealer.get("phone", "NA"),
            }

            english_response = (
                f"Best dealer found is {dealer.get('name', 'Local Dealer')}. "
                f"Current price for {product} is INR {estimate['unit_price']:.0f} per {estimate['price_unit']}. "
                f"Estimated amount for {quantity} is INR {estimate['amount']:.0f}. "
                f"Your wallet balance is INR {wallet_balance:.0f}. "
                f"Do you want me to place this order now?"
            )
            publish_activity_event(
                event_type="order_proposed",
                title="Order Proposal Ready",
                details=(
                    f"{product} | {quantity} | Dealer: {dealer.get('name', 'Local Dealer')} | "
                    f"Price: INR {estimate['unit_price']:.0f}/{estimate['price_unit']} | "
                    f"Wallet: INR {wallet_balance:.0f}"
                ),
                meta={"call_sid": CallSid, "dealer_id": dealer.get("dealer_id", ""), "farmer_phone": session.phone_number},
            )

        # ---- STEP 4E: Serve from cache or existing local/Gemini paths ----
        else:
            cached_response = get_cached_response(
                query=english_query,
                crop=session.crop,
                location=session.location,
            )

            if cached_response:
                english_response = cached_response
                logger.info("[ASYNC] Served response from local cache (Gemini skipped)")
            elif _is_market_price_query(english_query):
                english_response = _build_direct_market_response(
                    crop=session.crop,
                    location=session.location,
                )
                set_cached_response(
                    query=english_query,
                    crop=session.crop,
                    location=session.location,
                    response=english_response,
                )
                logger.info("[ASYNC] Served direct market response without Gemini")
            else:
                twin_context = _build_twin_context(session.phone_number)
                enriched_query = f"{english_query} {twin_context}".strip()
                english_response = await get_farming_advice(
                    call_sid=CallSid,
                    farmer_query=enriched_query,
                    crop=session.crop,
                    soil=soil_str,
                    location=session.location,
                    weather="Sunny, 30°C", # Fast local fallback logic
                )
                set_cached_response(
                    query=english_query,
                    crop=session.crop,
                    location=session.location,
                    response=english_response,
                )

        # ---- STEP 5: Translate English → Kannada ----
        kannada_response = translate_to_kannada(english_response)
        logger.info(f"[ASYNC] Kannada response generated.")

        # ---- STEP 6: Generate Kannada audio ----
        audio_filename = generate_kannada_audio(
            text=kannada_response,
            audio_dir=settings.AUDIO_DIR,
        )

        if not audio_filename:
            raise RuntimeError("TTS audio generation failed")

        audio_url = get_audio_url(audio_filename)
        logger.info(f"[ASYNC] Audio URL finalized: {audio_url}")

        # ---- STEP 8: Store in session ----
        session.add_exchange(farmer_query=english_query, ai_response=english_response)

        # ---- STEP 10: Build TwiML and INJECT INTO ONGOING CALL ----
        elapsed = time.time() - start_time
        logger.info(f"[ASYNC] Total generative pipeline took {elapsed:.2f}s! Injecting TwiML back to active call...")
        
        twiml = _build_gather_twiml(audio_url=audio_url, session=session)

        if not _is_real_twilio_call_sid(CallSid):
            logger.info(f"[ASYNC] Skipping Twilio call update for non-live test CallSid: {CallSid}")
            return
        
        client = get_twilio_client()
        client.calls(CallSid).update(twiml=str(twiml))
        logger.info(f"[ASYNC] ✅ Successfully injected audio into Twilio Call {CallSid}")

    except Exception as e:
        logger.error(f"[ASYNC] Error processing heavy LLM sequence: {e}", exc_info=True)
        try:
            if not _is_real_twilio_call_sid(CallSid):
                logger.info(f"[ASYNC] Skipping Twilio error injection for non-live test CallSid: {CallSid}")
                return

            error_audio = get_error_audio()
            if error_audio:
                error_url = get_audio_url(error_audio)
                twiml = _build_gather_twiml(audio_url=error_url, session=session)
            else:
                twiml = VoiceResponse()
                twiml.say("System encountered an error.", language="en-US")
                twiml.redirect(f"{settings.BASE_URL}/twilio/voice")
                
            client = get_twilio_client()
            client.calls(CallSid).update(twiml=str(twiml))
        except Exception as e2:
            logger.error(f"[ASYNC] FATAL: Could not even inject error XML to Twilio: {e2}")


def _update_session_context(session: CallSession, english_query: str) -> None:
    """
    Try to extract crop names and location from the query
    to improve context in future turns.
    """
    query_lower = english_query.lower()

    # Common Karnataka crops
    crop_keywords = {
        "ragi": "Ragi", "rice": "Rice", "paddy": "Paddy",
        "jowar": "Jowar", "sorghum": "Jowar", "cotton": "Cotton",
        "sugarcane": "Sugarcane", "groundnut": "Groundnut",
        "coconut": "Coconut", "arecanut": "Arecanut",
        "maize": "Maize", "corn": "Maize", "wheat": "Wheat",
        "sunflower": "Sunflower", "chilli": "Chilli", "pepper": "Pepper",
        "coffee": "Coffee", "tomato": "Tomato", "potato": "Potato",
        "onion": "Onion", "banana": "Banana", "mango": "Mango",
        "turmeric": "Turmeric", "ginger": "Ginger",
    }

    for keyword, crop_name in crop_keywords.items():
        if keyword in query_lower:
            session.crop = crop_name
            logger.info(f"Detected crop in query: {crop_name}")
            break

    # Karnataka districts/cities
    location_keywords = [
        "bangalore", "mysore", "dharwad", "belgaum", "shimoga",
        "gulbarga", "mangalore", "raichur", "hassan", "tumkur",
        "mandya", "davangere", "hubli", "bellary", "bijapur",
        "karwar", "chikmagalur", "kodagu", "coorg", "udupi",
    ]

    for loc in location_keywords:
        if loc in query_lower:
            session.location = loc.capitalize()
            logger.info(f"Detected location in query: {session.location}")
            break


# ============================================
# CALL STATUS CALLBACK
# ============================================

@router.post(
    "/twilio/status",
    dependencies=[Depends(verify_twilio_signature)],
)
async def handle_call_status(
    request: Request,
    CallSid: str = Form(default=""),
    CallStatus: str = Form(default=""),
):
    """Handle Twilio call status updates."""
    logger.info(f"Call status: CallSid={CallSid}, Status={CallStatus}")

    if CallStatus in ("completed", "failed", "busy", "no-answer", "canceled"):
        remove_session(CallSid)

    return {"status": "ok"}


# ============================================
# OUTBOUND "CALL ME" ENDPOINT
# ============================================

@router.post(
    "/api/call",
    dependencies=[Depends(require_api_key), Depends(rate_limit_calls)],
)
async def initiate_call(request: Request):
    """
    Initiate an outbound call to a farmer.
    Request body: { "phone_number": "+91XXXXXXXXXX", "name": "Rajesh" }
    """
    settings = get_settings()

    try:
        body = await request.json()
        phone_number = body.get("phone_number", "")
        name = body.get("name", "").strip()

        if not phone_number:
            raise HTTPException(status_code=400, detail="phone_number is required")

        if not phone_number.startswith("+"):
            phone_number = f"+91{phone_number}"

        # Validate Twilio credentials
        if not twilio_configured():
            raise HTTPException(status_code=500, detail="Twilio credentials not configured")

        # Create Twilio client and make call
        client = get_twilio_client()

        voice_url = f"{settings.BASE_URL}/twilio/voice"
        if name:
            import urllib.parse
            voice_url += f"?name={urllib.parse.quote(name)}"
            # Pre-warm the TTS greeting now, while the phone is still ringing,
            # so the /twilio/voice webhook can answer instantly.
            build_personalized_greeting(name)

        call = client.calls.create(
            to=phone_number,
            from_=settings.TWILIO_PHONE_NUMBER,
            url=voice_url,
            status_callback=f"{settings.BASE_URL}/twilio/status",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
        )

        logger.info(f"Initiated call to {phone_number}: CallSid={call.sid}")

        return {
            "success": True,
            "call_sid": call.sid,
            "phone_number": phone_number,
            "status": call.status,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to initiate call: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to initiate call: {str(e)}")


# ============================================
# PROCESS SPEECH ENDPOINT (for n8n integration)
# ============================================

@router.post(
    "/api/process-speech",
    dependencies=[Depends(require_api_key), Depends(rate_limit_ai)],
)
async def process_speech_api(request: Request):
    """
    API endpoint for processing speech (used by n8n workflow).
    Accepts audio URL or text, processes through the full pipeline.

    Request body: {
        "text": "farmer's question in Kannada",
        "audio_url": "URL to audio file (optional)",
        "location": "Bangalore",
        "crop": "Ragi"
    }
    """
    settings = get_settings()

    try:
        body = await request.json()
        kannada_text = body.get("text", "")
        audio_url = body.get("audio_url", "")
        location = body.get("location", "Karnataka")
        crop = body.get("crop", "Unknown")
        phone = str(body.get("phone", "")).strip()

        # If audio URL provided, we cannot transcribe it without Whisper anymore.
        # So we just rely on kannada_text being passed.
        if audio_url and not kannada_text:
            logger.warning("Audio URL provided to process-speech but Whisper is disabled. Need text input.")

        if not kannada_text:
            raise HTTPException(status_code=400, detail="No text or audio provided")

        # Translate
        english_query = translate_to_english(kannada_text)

        # Get context
        weather = await get_weather(location)
        weather_str = format_weather_string(weather)
        soil = get_soil_info(location)

        cached_response = get_cached_response(query=english_query, crop=crop, location=location)
        if cached_response:
            english_response = cached_response
            logger.info("process-speech API served cached response")
        elif _is_sell_intent(english_query):
            crop_name = crop if crop != "Unknown" else "Onion"
            qty_text = _extract_quantity(english_query, default_value="50 kg")
            qty_kg = _quantity_to_kg(qty_text)
            base_price = _infer_base_price_per_kg(crop_name, location)

            if phone:
                listing = create_auction_listing(
                    farmer_phone=phone,
                    crop_name=crop_name,
                    quantity=qty_kg,
                    base_price_per_unit=base_price,
                    expires_in_minutes=30,
                )
                top_bid = (listing.get("highest_bid") or {}).get("bid_price_per_unit", base_price)
                english_response = (
                    f"Your auction listing is now live for {qty_kg:.0f} kg {crop_name}. "
                    f"Current highest bid is INR {top_bid:.2f} per kg."
                )
            else:
                english_response = (
                    f"I can create an auction listing for {qty_kg:.0f} kg {crop_name} at base INR {base_price:.2f} per kg. "
                    f"Please provide your phone in the API payload to execute listing creation."
                )
        elif _is_bid_intent(english_query):
            listing = _pick_open_listing_for_bid(english_query, crop)
            bid_price = _extract_bid_price_per_kg(english_query)
            qty_text = _extract_quantity(english_query, default_value="50 kg")
            qty_kg = _quantity_to_kg(qty_text)

            if not listing:
                english_response = "There are no open listings right now to place a bid."
            elif bid_price is None:
                english_response = "Please provide bid price per kg, for example bid 30 rupees per kg."
            elif not phone:
                english_response = "Please provide your phone in payload to place bid as buyer_id."
            else:
                max_qty = float(listing.get("quantity") or 0)
                final_qty = min(max(qty_kg, 1.0), max_qty if max_qty > 0 else qty_kg)
                try:
                    place_auction_bid(
                        listing_id=listing["id"],
                        buyer_id=phone,
                        bid_price_per_unit=float(bid_price),
                        quantity=float(final_qty),
                    )
                    updated_listing = get_auction_listing_by_id(listing["id"]) or listing
                    highest_bid = (updated_listing.get("highest_bid") or {}).get("bid_price_per_unit", bid_price)
                    english_response = (
                        f"Bid placed successfully on listing {listing['id']} at INR {bid_price:.2f} per kg for {final_qty:.0f} kg. "
                        f"Current highest bid is INR {highest_bid:.2f} per kg."
                    )
                except ValueError as e:
                    english_response = f"Bid could not be placed: {str(e)}"
        elif _is_market_price_query(english_query):
            english_response = _build_direct_market_response(crop=crop, location=location)
            set_cached_response(query=english_query, crop=crop, location=location, response=english_response)
        elif _is_order_intent(english_query):
            product = _detect_product_from_query(english_query, crop)
            quantity = _extract_quantity(english_query, default_value="1 bag")
            dealers = find_dealers(product=product, location=location)
            dealer = dealers[0] if dealers else {"name": "Local Dealer"}
            quote = _estimate_buy_price(product, quantity)
            english_response = (
                f"Best dealer found is {dealer.get('name', 'Local Dealer')}. "
                f"Estimated price for {product} is {quote}. "
                f"Please confirm by saying yes if I should place order now."
            )
        else:
            # Get advice from Gemini only when needed
            twin_context = _build_twin_context(phone) if phone else ""
            enriched_query = f"{english_query} {twin_context}".strip()
            english_response = await get_farming_advice(
                call_sid="n8n_webhook_session",
                farmer_query=enriched_query,
                crop=crop,
                soil=soil.get("soil_type", "Unknown"),
                location=location,
                weather=weather_str,
            )
            set_cached_response(query=english_query, crop=crop, location=location, response=english_response)

        # Translate back
        kannada_response = translate_to_kannada(english_response)

        # Generate audio
        audio_filename = generate_kannada_audio(kannada_response, audio_dir=settings.AUDIO_DIR)
        audio_url_out = get_audio_url(audio_filename) if audio_filename else None

        return {
            "success": True,
            "input_kannada": kannada_text,
            "input_english": english_query,
            "response_english": english_response,
            "response_kannada": kannada_response,
            "audio_url": audio_url_out,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Process speech API failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
