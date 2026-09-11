# AI Krishi - All Present Features

## Voice Assistant
- Kannada-first AI farming voice assistant.
- Twilio inbound and outbound call handling.
- Multi-turn conversational call flow.
- Personalized greeting with farmer name.
- Speech gather and async response injection into active calls.

## AI and Language Stack
- Gemini-powered farming assistant with tool-calling.
- Cache-first response path for repeated queries.
- Quota/cooldown handling for Gemini rate limits.
- Kannada-English two-way translation pipeline.
- Direct local handling for market-price intents to reduce LLM usage.

## Market Data and Intelligence
- Live mandi source integrated for Kalaburgi/Gulbarga path.
- Crop price support for Onion, Tomato, Potato, Chilli, Maize.
- Market cache warmup at startup.
- Periodic market cache refresh in background.
- Market API for dashboard-ready data cards.

## Agentic Buy/Sell Workflows
- Sell intent detection and buyer recommendation.
- Order intent detection and dealer recommendation.
- Confirmation-first execution (yes/no before final action).
- In-call order proposal now speaks:
- Per-unit price.
- Estimated total amount.
- Current wallet balance.
- Then asks confirmation.

## Farmer Wallet, Orders, and Persistence
- SQLite-backed persistent storage.
- Database file: data/krishi.sqlite3.
- Persistent entities:
- Farmers.
- Orders.
- Transactions ledger.
- Default wallet initialization: INR 20,000.
- Wallet debit on confirmed order.
- Insufficient-balance guard blocks order execution.
- Orders, debits, and balances survive restart.

## Farmer Profile Management
- Farmer session/create APIs.
- Farmer profile fetch API.
- Farmer profile update API (name and city).
- Stitch farmer dashboard supports profile edit and save.
- Browser session storage for smoother revisit.

## Real-Time Events
- Activity event feed for order/sell/system actions.
- Server-Sent Events stream endpoint.
- Farmer dashboard live update from stream.
- Admin dashboard live update from stream.

## Stitch Web App
- Stitch pages integrated and served from FastAPI.
- Routes available:
- /stitch/landing
- /stitch/signin
- /stitch/farmer
- /stitch/admin
- /stitch/404
- Canonical /farmer route serves Stitch farmer dashboard.
- Sign-in supports Farmer and Admin role flows.

## Farmer Dashboard (Stitch)
- Live wallet card with API data.
- Soil and weather context from backend.
- Live market cards from market API.
- Recent orders list from farmer profile API.
- Live Orders Dashboard section with KPIs.
- Real-time order event panel via activity stream.
- Call AI Assistant action integrated with backend call endpoint.

## Admin Dashboard (Stitch)
- Dynamic KPI cards.
- Live market table from backend API.
- Live activity feed from feed and SSE stream.
- Periodic refresh plus stream updates.

## API Endpoints Present
- POST /api/farmer/session
- POST /api/farmer/create
- GET /api/farmer/{phone}
- PATCH /api/farmer/{phone}
- POST /api/farmer/{phone}/call
- GET /api/market-prices
- GET /api/activity-feed
- GET /api/activity-stream
- Twilio call webhooks for voice, gather, and status.

## Reliability and Operations
- Global exception handling.
- Background tasks for session cleanup, market refresh, and audio cleanup.
- Twilio SID guard for non-live local test calls.
- Startup initialization for Gemini and system audio generation.
