# AI Krishi - Features List

## Core Voice Assistant
- Kannada-first AI voice assistant for farmers.
- Twilio voice call handling for inbound and outbound calls.
- Speech capture with conversational call flow.
- Personalized greeting support using farmer name.

## AI Intelligence
- Gemini-powered farming advice with tool-calling support.
- Local cache-first responses for repeated queries.
- Graceful fallback behavior when Gemini quota is hit.
- Translation pipeline between Kannada and English.

## Market Intelligence
- Live Kalaburgi/Gulbarga mandi pricing integration.
- Crop-wise market price lookup (Onion, Tomato, Potato, Chilli, Maize).
- Cached market data with startup warmup.
- Periodic background market refresh for faster responses.

## Agentic Actions
- Autonomous dealer discovery for farm inputs.
- Crop sale proposal and buyer recommendation.
- Autonomous order placement workflow.
- Confirmation-first safety flow (ask farmer before executing sell/order).

## Wallet and Farmer Profile
- Farmer profile creation by name and phone.
- Default wallet initialization (INR 20,000).
- Wallet deduction on confirmed orders.
- Order history tracking per farmer.
- Farmer context enrichment (city, weather, soil type).

## Dashboards
- Public landing page with call CTA.
- Admin operations dashboard with market table and activity view.
- Farmer premium dashboard with wallet, weather, soil, orders, and call controls.
- Mobile-responsive UI for farmer and admin pages.

## Real-Time Activity
- In-memory activity event feed for system actions.
- Server-Sent Events (SSE) stream endpoint for live updates.
- Real-time farmer dashboard refresh on relevant events.
- Real-time admin dashboard activity rendering.

## Backend and API
- FastAPI service with structured API endpoints.
- Farmer APIs:
  - POST /api/farmer/session
  - GET /api/farmer/{phone}
  - POST /api/farmer/{phone}/call
- Dashboard and data APIs:
  - GET /api/market-prices
  - GET /api/activity-feed
  - GET /api/activity-stream

## Reliability and Operations
- Session lifecycle management and cleanup tasks.
- Background audio cleanup task.
- Global error handling for safer runtime behavior.
- Twilio test SID guard to avoid invalid call updates during local tests.
