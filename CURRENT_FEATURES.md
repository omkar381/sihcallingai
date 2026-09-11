# AI Krishi - Current Features (Live Build)

## 1. Voice AI Assistant
- Kannada-first agricultural voice assistant.
- Twilio inbound and outbound call support.
- Conversational speech gather loop for multi-turn calls.
- Personalized greeting with farmer name.
- Auto context tracking during calls (crop/location extraction).

## 2. AI + Response Engine
- Gemini-powered farming guidance with tool-calling.
- Local cache-first response reuse to reduce repeated LLM cost.
- Quota-aware fallback behavior for Gemini rate limits.
- Translation flow between Kannada and English.
- Hybrid logic: direct local handling for market queries without Gemini when possible.

## 3. Live Market Intelligence
- DataGov removed; current source is Kalaburgi/Gulbarga mandi path.
- Crop-wise market pricing (Onion, Tomato, Potato, Chilli, Maize).
- Startup market cache warmup.
- Background market cache refresh task.
- Dashboard and call flows consume same market source.

## 4. Agentic Buy/Sell Workflows
- Sell-intent detection and buyer recommendation.
- Order-intent detection and dealer recommendation.
- Confirmation-first execution model:
  - Proposal generated
  - Farmer says yes/no
  - Action executed only on explicit confirmation
- In-call response now includes:
  - Per-unit price (INR per unit)
  - Estimated total amount
  - Current wallet balance
  - Then asks order confirmation

## 5. Farmer Wallet + Orders (SQLite Persistent)
- SQLite-backed farmer storage (not in-memory only anymore).
- Persistent data file: `data/krishi.sqlite3`.
- Stored entities:
  - Farmers
  - Orders
  - Transactions (credit/debit ledger)
- Default wallet creation: INR 20,000.
- Wallet debit on confirmed order placement.
- Insufficient-balance guard blocks order.
- Order and debit data persist across server restart.

## 6. Farmer Profile Management
- Farmer create/session APIs.
- Profile fetch API with weather + soil context.
- Profile update API (name/city).
- Stitch farmer dashboard allows editing name/city and saving.
- Session values cached in browser for smoother re-entry.

## 7. Real-Time Activity + Streams
- Activity feed event model for system/order/sell actions.
- SSE endpoint for live updates.
- Farmer dashboard consumes live stream for order-related updates.
- Admin dashboard consumes live stream for ops visibility.

## 8. Dashboards and UX
- Stitch-based unified web experience served from FastAPI.
- Stitch pages are renamed and routed cleanly:
  - `/stitch/landing`
  - `/stitch/signin`
  - `/stitch/farmer`
  - `/stitch/admin`
  - `/stitch/404`
- Canonical farmer route now serves Stitch farmer dashboard.
- Sign-in supports role selection:
  - Farmer flow
  - Admin flow
- Mobile-responsive layouts for all key pages.

## 9. Admin Operations Dashboard
- Live activity feed rendering.
- Live mandi table rendering.
- KPI cards updated from activity stream/feed.
- Continuous refresh + SSE updates.

## 10. Backend APIs (Current)
- Farmer APIs:
  - `POST /api/farmer/session`
  - `POST /api/farmer/create`
  - `GET /api/farmer/{phone}`
  - `PATCH /api/farmer/{phone}`
  - `POST /api/farmer/{phone}/call`
- Ops and market APIs:
  - `GET /api/market-prices`
  - `GET /api/activity-feed`
  - `GET /api/activity-stream`

## 11. Operational Reliability
- Audio cleanup background task.
- Session cleanup background task.
- Market refresh background task.
- Global exception handling.
- Twilio SID guard for non-live local testing.

## 12. End-to-End Working Flow
- Sign in as farmer -> open dashboard -> call AI -> ask buy/sell -> confirm -> wallet/order updates -> admin sees events live.
- Data persists in SQLite and remains after restart.
