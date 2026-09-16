"""
Gemini API integration for agricultural advice.
Upgraded to Agentic Tool-Calling Architecture.
"""

import logging
from typing import Optional, Dict, Any
import asyncio
import re
import time

import google.generativeai as genai

from app.config import get_settings
from app.agent_tools import AGRICULTURE_TOOLS

logger = logging.getLogger(__name__)

# Global model instance
_gemini_model = None

# Global Chat Sessions store (CallSid -> ChatSession)
_chat_sessions: Dict[str, Any] = {}

# Timestamp until which Gemini should be considered rate-limited.
_gemini_rate_limited_until: float = 0.0

# ============================================
# SYSTEM PROMPT — AGENTIC VERSION
# ============================================
SYSTEM_PROMPT = """You are an AI agriculture agent acting as a warm, friendly Indian agricultural expert.
You help farmers with real-world farming problems in a conversational, human way.

CRITICAL RULES:
- You do NOT just answer. You must:
  * Understand farmer intent
  * Decide next action
  * Use tools when needed
  * Ask follow-up questions
  * Complete tasks step-by-step
- If a farmer asks what to grow based on their soil and weather → call predict_best_crops
- If a farmer asks how much yield they will get → call predict_expected_yield
- If a farmer asks about crop insurance, weather risks, or PMFBY → call check_crop_insurance
- If a farmer asks about government schemes, financial aid, or machinery subsidies → call find_government_schemes
- If a farmer asks about selling or current prices → call get_market_price
- If a farmer asks WHERE to sell or which mandi pays most → call compare_nearby_markets
- If a farmer asks whether prices are rising or falling → call get_price_trend
- If a farmer asks "should I sell now or wait?" → call get_sale_recommendation
- If a farmer specifically asks WHERE to sell their harvested crops or bulk produce → call find_crop_buyers
- If a farmer wants to buy seeds/fertilizers/equipment/pesticides → call find_dealers
- To purchase marketplace supplies directly on behalf of the farmer → call place_order
- If farmer intent is clear, execute actions autonomously using tools without unnecessary confirmation loops.
- For buying intent, you may directly contact dealer and place order with best-fit option.
- For selling intent, you may negotiate and trigger autonomous selling flow using available tools.
- Prefer action completion over generic advice. Take the next operational step whenever tool inputs are available.
- Talk like a caring elder or friend, not a textbook. Use phrases like "Don't worry, here's what we'll do..."
- Keep answers to 4-5 sentences maximum verbally.
- Never use bullet points, numbered lists, or formatting — speak naturally as if on a phone call.
- Respond ONLY in English. This is absolute and has no exceptions.
  Your English answer is machine-translated into Kannada afterwards, so replying
  in Hindi, Hinglish, Kannada or any transliteration corrupts that step and the
  farmer hears nonsense. Even when the farmer's question arrives in another
  language, or mentions an Indian place or crop name, your entire reply must
  still be plain English. Do not open with "Namaste" in Devanagari or Roman
  Hindi; write "Hello" or "Namaste" in plain English letters only.

MARKET DATA RULES - THESE OVERRIDE EVERYTHING ELSE:
- You must NEVER state a price, trend, average, forecast or market name that did
  not come from a tool result in this conversation. Not an estimate, not a
  "typical" figure, not a remembered one. If you did not call a tool, you do not
  know the price.
- Every market tool result contains "agent_instruction". Follow it exactly.
- If a result has "available": false, tell the farmer you could not get the data
  and suggest they check their local mandi. Never fill the gap with a guess.
- If "data_quality" is STALE or MOCK, say so in the same breath as the number.
  Never call such a figure "today's price".
- Report figures as the tool gives them. Do not recalculate, convert or round
  them differently, and do not add markets the tool did not return.
- Before calling get_sale_recommendation, ask the farmer how many days they can
  store the crop and whether they need money urgently. Both change the answer,
  and a farmer with no storage must never be told to wait.
- If a farmer asks "why?", answer from the "reasoning" and "signals" in the tool
  result. Never invent a justification.

"""

def initialize_gemini() -> None:
    """
    Initialize the Gemini model carefully with Agentic tools. Called at startup.
    """
    global _gemini_model
    settings = get_settings()

    if not settings.GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY is not set!")
        raise ValueError("GEMINI_API_KEY must be configured in .env")

    try:
        genai.configure(api_key=settings.GEMINI_API_KEY)
        # gemini-2.5-flash's free tier caps out at 20 requests/day, which a
        # live phone line burns through in minutes and then serves a "busy"
        # fallback for every farmer until the cooldown expires. The voice
        # pipeline already moved to flash-lite for its much higher free-tier
        # ceiling (see VOICE_NLU_MODEL / VOICE_REPLY_MODEL); match it here so
        # calls don't get cut short by quota either.
        _gemini_model = genai.GenerativeModel(
            model_name="gemini-3.5-flash-lite",
            system_instruction=SYSTEM_PROMPT,
            tools=AGRICULTURE_TOOLS,
        )
        logger.info("✅ Gemini Agent Initialized with Function Calling Tools!")
    except Exception as e:
        logger.error(f"Failed to initialize Gemini: {e}")
        raise

def get_agent_chat_session(call_sid: str) -> Any:
    """
    Returns a new or existing chat session for this specific phone call.
    """
    global _chat_sessions, _gemini_model
    if not _gemini_model:
        raise RuntimeError("Gemini model not initialized")
    
    if call_sid not in _chat_sessions:
        logger.info(f"Creating new Agent Chat Session for Call: {call_sid}")
        _chat_sessions[call_sid] = _gemini_model.start_chat(enable_automatic_function_calling=True)
        
    return _chat_sessions[call_sid]


def _extract_retry_seconds(error_text: str) -> int:
    """Best-effort parse for Gemini retry delays from error text."""
    match = re.search(r"Please retry in\s+([0-9]+(?:\.[0-9]+)?)s", error_text)
    if match:
        return max(1, int(float(match.group(1))))
    match = re.search(r"seconds:\s*([0-9]+)", error_text)
    if match:
        return max(1, int(match.group(1)))
    return 30  # Reduced from 60s


def _rate_limit_fallback_message(location: str = "Karnataka", soil: str = "Unknown") -> str:
    """Provide useful local crop data when Gemini is rate-limited."""
    try:
        from app.agriculture import get_crop_recommendations, get_soil_info
        soil_info = get_soil_info(location)
        soil_type = soil_info.get("soil_type", soil)
        crops = get_crop_recommendations(soil_type=soil_type)
        crop_names = [c.get("name", "") for c in crops[:5] if c.get("name")]
        if crop_names:
            return (
                f"Based on local data for {location} with {soil_type}: "
                f"Good crops to grow are {', '.join(crop_names)}. "
                f"For more detailed advice, please try again in a moment."
            )
    except Exception:
        pass
    return (
        "I am sorry, our expert AI service is currently busy due to usage limits. "
        "Please ask a crop price, mandi rate, or dealer question and I will answer from local data immediately."
    )

async def get_farming_advice(
    call_sid: str,
    farmer_query: str,
    crop: str = "Unknown",
    soil: str = "Unknown",
    location: str = "Karnataka",
    weather: str = "Unknown",
) -> str:
    """
    Stateful chat generation with tool calling.
    """
    global _gemini_rate_limited_until

    if _gemini_rate_limited_until > time.time():
        wait_seconds = int(_gemini_rate_limited_until - time.time())
        logger.warning(f"Gemini is in cooldown for another {wait_seconds}s; skipping API call")
        return _rate_limit_fallback_message(location=location, soil=soil)

    try:
        chat_session = get_agent_chat_session(call_sid)
        
        # We append context only if it's the very first message
        is_first_message = len(chat_session.history) == 0
        
        if is_first_message:
            context_prompt = (
                f"[SYSTEM CONTEXT - DO NOT READ ALOUD: Crop: {crop}, Soil: {soil}, Loc: {location}, Weather: {weather}]\n\n"
                f"FARMER SAYS: {farmer_query}"
            )
        else:
            context_prompt = farmer_query
            
        logger.info(f"Sending message to Gemini Agent (Call: {call_sid}): {farmer_query[:50]}...")
        
        loop = asyncio.get_event_loop()
        # send_message triggers the model. If it picks a tool, it auto-executes Python code and answers!
        response = await loop.run_in_executor(
            None, 
            lambda: chat_session.send_message(context_prompt)
        )
        
        return response.text.replace("*", "").replace("#", "").strip()

    except Exception as e:
        logger.error(f"Gemini Agent generation failed: {e}", exc_info=True)
        error_text = str(e)
        if "429" in error_text or "quota" in error_text.lower() or "resourceexhausted" in error_text.lower():
            retry_seconds = _extract_retry_seconds(error_text)
            _gemini_rate_limited_until = time.time() + retry_seconds
            logger.warning(f"Gemini quota exceeded. Cooldown set for {retry_seconds}s")
            return _rate_limit_fallback_message(location=location, soil=soil)

        return "I am sorry, my connection to the expert system is currently down. Please try again later."
