# AI Krishi Voice Assistant - Full Feature and Working Documentation

## 1. System Overview
This system is a Kannada-first AI voice assistant for farmers.
It answers farming queries over phone calls using Twilio webhooks, translation, AI reasoning, market data tools, and text-to-speech playback.

Core goals:
- Let farmers speak naturally in Kannada over a normal phone call.
- Provide agriculture guidance, mandi prices, dealer and buyer discovery.
- Keep latency low enough for real-time voice interactions.
- Continue operating even when external services fail or quota is exhausted.

## 2. Current Tech Stack
- Backend framework: FastAPI
- Telephony: Twilio Voice + TwiML
- AI engine: Gemini function-calling agent
- Translation: googletrans (free) with Google Cloud Translate fallback
- Speech synthesis: Google Cloud TTS with edge-tts fallback
- Market data source: CommodityOnline Kalaburgi APMC page (live scrape + parsing)
- Caching: TTL cache for response reuse and market prefetch cache
- Runtime: Python with background async tasks

## 3. High-Level Architecture
1. Farmer speaks on a live phone call.
2. Twilio posts speech transcript (SpeechResult) to backend.
3. Backend instantly returns hold audio + pause to avoid webhook timeout.
4. Background task processes the query:
   - Kannada to English translation
   - Cache/direct-local answer check
   - Gemini call only when needed
   - English to Kannada translation
   - Kannada audio generation
5. Backend injects new TwiML into active call using Twilio Calls update API.
6. Farmer hears generated Kannada response and can continue speaking.

## 4. Main Features

### 4.1 Twilio Voice Call Flow
- Incoming call endpoint: /twilio/voice
- Speech gather endpoint: /twilio/gather
- Call status endpoint: /twilio/status
- Outbound call trigger endpoint: /api/call

Behavior:
- Plays personalized greeting if caller name is supplied.
- Uses Gather with Kannada language setting (kn-IN).
- Uses Play for audio prompts and AI responses.
- Uses immediate hold response to keep call alive while AI runs asynchronously.

### 4.2 Asynchronous Processing Pipeline
The gather handler does not wait for full AI generation.
Instead, it starts process_speech_async as a background task, which reduces perceived blocking and prevents Twilio timeout failures.

Pipeline steps:
1. Translate farmer speech KN to EN.
2. Detect crop/location context from query.
3. Try response cache first.
4. If market-price intent, answer directly from market tool (no Gemini call).
5. Otherwise call Gemini.
6. Translate EN to KN.
7. Generate KN audio.
8. Update active Twilio call with new TwiML containing response audio.

### 4.3 Gemini Agent with Function Calling
Gemini is configured as an agriculture expert agent with tool access.
Available tools:
- get_market_price
- find_dealers
- find_crop_buyers
- contact_dealer
- place_order

Capabilities:
- Conversational farming advice
- Tool-assisted structured responses
- Multi-turn call context using per-call chat sessions

### 4.4 Gemini Quota Protection and Graceful Degradation
The system now includes safeguards for free-tier quota exhaustion:
- Detects 429/quota/resource exhausted errors.
- Parses retry delay from error text.
- Enters cooldown window to avoid repeated failing requests.
- Returns controlled fallback guidance during cooldown.

Result:
- Prevents hammering Gemini when limit is hit.
- Maintains call continuity instead of repeated hard failures.

### 4.5 Direct Local Handling for Market Queries
Common market-intent queries are handled locally without Gemini.
Intent keywords include words like price, rate, mandi, market, apmc, how much.

Local response path:
- Uses get_market_price tool directly.
- Builds spoken response in English and translates to Kannada.
- Reduces token usage and call cost.
- Improves latency.

### 4.6 Live Kalaburgi APMC Market Integration
Data.gov integration was replaced.
Current source is Kalaburgi/Gulbarga mandi data parsed from CommodityOnline market pages.

What get_market_price returns:
- mandi
- state
- crop
- min/max/modal price
- trend text
- source field
- farmer-safe advisory sentence

If live fetch fails:
- Returns static fallback crop price estimates.
- Marks source as local fallback.

### 4.7 Market Cache Warmup and Refresh
To avoid runtime scraping delays:
- Startup warmup preloads market prices for key crops.
- Background refresh updates market cache periodically.
- TTL cache is used per crop for fast reuse.

Impact:
- Faster first dashboard load.
- Faster price responses during calls.
- Lower repeated HTTP fetches.

### 4.8 Dashboard Live Market API
Endpoint: /api/market-prices

It returns a ready-to-render array for UI cards/tables with:
- crop name
- price string
- trend label/icon
- mandi label
- style hints (emoji/color tags)

Dashboard auto-fetch behavior:
- Fetches on page load.
- Polls periodically for updates.
- Displays fallback state if API fails.

### 4.9 Translation Layer with Fallbacks
Two-way translation:
- Kannada to English
- English to Kannada

Priority order:
1. googletrans
2. Google Cloud Translation
3. Original text fallback (if both fail)

This keeps pipeline functional even if one translation provider fails.

### 4.10 TTS Layer with Fallbacks
Speech generation path:
1. Google Cloud TTS (preferred)
2. edge-tts fallback

Startup audio pre-generation:
- greeting.mp3
- wait.mp3
- error.mp3

This reduces first-call latency and ensures basic prompts are always available.

### 4.11 Session Management
Per call_sid session tracks:
- phone number
- detected crop
- detected location
- soil info
- conversation history
- activity timestamps

Features:
- Auto creation on first webhook.
- Context reuse across turns in same call.
- Cleanup on call end or timeout.

### 4.12 Response Cache for Repeated Queries
A TTL cache stores generated responses by query+crop+location key.

Benefits:
- Repeated questions can skip Gemini.
- Faster responses for common asks.
- Lower API usage and lower cost.

### 4.13 System Reliability and Safety
- Global exception handler to avoid server crash from unhandled exceptions.
- Error audio fallback when processing fails.
- Twilio call update retry path with fallback TwiML.
- Multiple graceful fallback layers (AI, translation, TTS, market source).

## 5. HTTP Endpoints
- GET /health
  - Service health status.
- GET /
  - API metadata and endpoint list.
- GET /app
  - Serves frontend call UI.
- GET /dashboard
  - Serves operations dashboard.
- GET /api/market-prices
  - Dashboard market feed.
- POST /api/call
  - Outbound call initiation.
- POST /twilio/voice
  - Incoming call webhook.
- POST /twilio/gather
  - Speech gather webhook.
- POST /twilio/status
  - Call lifecycle status webhook.
- POST /api/process-speech
  - Non-call speech processing API (useful for n8n/integration).

## 6. Startup Sequence
At server startup:
1. Load settings and create audio directory.
2. Initialize Gemini model and tools.
3. Pre-generate system audio files.
4. Warm market cache for key crops.
5. Start background tasks:
   - audio cleanup loop
   - session cleanup loop
   - market cache refresh loop

## 7. End-to-End Call Lifecycle
1. Outbound call created by /api/call or inbound call arrives at /twilio/voice.
2. Greeting audio is played.
3. Gather listens for speech.
4. /twilio/gather returns immediate wait response.
5. Background processing runs full intelligence pipeline.
6. Generated response audio URL is injected into active call.
7. Gather resumes for next turn.
8. /twilio/status completed event removes call session.

## 8. Performance Optimizations Implemented
- Async background processing for heavy steps.
- Startup pre-generation of fixed audio assets.
- Market cache warmup and periodic refresh.
- Direct local answer path for market intents.
- Query response cache to avoid duplicate AI calls.
- Gemini quota cooldown guard to prevent waste.

## 9. Known Constraints
- Whisper local STT is currently disabled; system relies on Twilio SpeechResult.
- Market live source depends on external webpage structure and may need parser updates if markup changes.
- Free-tier Gemini limits can still block non-local complex advice unless billing or alternate model/provider is used.
- Python 3.14 compatibility for old httpx/googletrans stack needs cgi monkeypatch path via app startup context.

## 10. Operational Recommendations
- Keep this running with a stable BASE_URL (ngrok or domain).
- Monitor logs for:
  - Gemini 429 cooldown events
  - market source fallback frequency
  - TTS fallback frequency
- For production scale:
  - move from free-tier AI quota to paid tier
  - add metrics dashboard for token usage and success rates
  - harden market parser with backup providers

## 11. Summary
This system is a resilient, voice-first agricultural assistant optimized for real call conditions.
It combines telephony, translation, tool-driven AI, local rule shortcuts, and layered fallbacks to remain responsive even under API quota pressure or partial service outages.
