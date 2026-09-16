# Build Status — Market Intelligence & Transaction Engine

**Against:** SIH PS 26132 · *Strengthening market linkages and price discovery for farmers*
**Last updated:** 2026-09-11

**This build so far:** ~11,800 lines of new production code · 285 tests, all passing.
Market intelligence, the marketplace (buyers, FPO, lots, offers, payments, disputes,
audit) and the operator console are all in place.

---

## The rule everything is built on

> No fake data disguised as real data.

It is enforced structurally, not by convention. `DataQuality` is attached at
ingestion and travels with every value through storage, analytics, the API and
the spoken Kannada answer. There is no code path where a missing source becomes
a plausible-looking number.

Proof, from a live agent call during this build:

> *"...the demonstration data shows that Solapur, though 136 kilometers away, might give you about 353 rupees more per quintal after all transport costs... **Remember, this is based on demonstration data and not live market prices**, so please use this for guidance only."*

The model was never told to say that in this turn. The tool payload carried
`data_quality: MOCK`, and the label survived all the way into speech.

---

## Built and verified

### Foundation

| Module | Lines | What it does |
|---|--:|---|
| `app/market/provenance.py` | 265 | `DataQuality` (LIVE/FRESH/STALE/DEGRADED/UNAVAILABLE/MOCK), freshness classification, `Provenance` records |
| `app/market/normalization.py` | 425 | Canonical commodities with Kannada/Marathi/Hindi aliases, market renames, units, spoken quantities |
| `app/market/schema.py` | 213 | Price history, market registry, ingestion audit. **57 real markets seeded** with coordinates |
| `app/market/repository.py` | 644 | All SQL. Idempotent upserts, price series, geo search, ingestion audit |
| `app/market/validation.py` | 260 | Rejects unit errors, future dates, impossible prices; repairs transposed min/max |
| `app/market/ingestion.py` | 285 | data.gov.in adapter with retries. **Fails honestly** — never substitutes fixtures |

### Intelligence

| Module | Lines | What it does |
|---|--:|---|
| `app/market/trends.py` | 332 | 7/30/90-day averages, median, change %, volatility (CV), arrival-pressure analytics |
| `app/market/logistics.py` | 275 | Vehicle selection, freight, handling, APMC fees, commission, rejection loss → **net realisable price** |
| `app/market/comparison.py` | ~250 | Ranks nearby markets by what the farmer *keeps*, not the headline price |
| `app/market/sale_window.py` | 445 | **SELL_NOW / WAIT / SELL_PARTIAL / COMPARE_MARKETS / INSUFFICIENT_DATA**, with weighted signals and reasoning |
| `app/market/service.py` | 462 | Single facade for voice, API and dashboard. Generates natural spoken output |

### Delivery

| Module | Lines | What it does |
|---|--:|---|
| `app/market/voice_tools.py` | 198 | 5 Gemini tools, each carrying an `agent_instruction` that blocks improvisation |
| `app/routes/market_routes.py` | 240 | 13 REST endpoints |
| `app/security.py` | 305 | API-key auth, Twilio signature validation, rate limiting, CORS allowlist |
| `app/market/demo_data.py` | 214 | Isolated fixture generator. Every row tagged `MOCK` |

### Evidence it works

```
Onion @ Kalaburagi
  7d   n=7   avg=2524  change=+15.97%  volatility=LOW     RISING
  30d  n=30  avg=2591  change= +4.15%  volatility=LOW     RISING
  90d  n=90  avg=2344  change=+72.50%  volatility=MEDIUM  RISING
  arrivals 90d: -15.7%  FALLING        <- genuine supply-tightening signal
```

Net realisation for 30 quintal of onion from Kalaburagi:

```
market          dist    gross     net
Solapur       136.5km    3280    2884
Humnabad       75.6km    3150    2854
Kalaburagi      0.0km    2760    2532   <- farmer's own market (always shown)

best: Solapur   +353/quintal   =  +10,588 for the lot
```

And the same lot at 300 kg instead ranks **Humnabad first**, because freight on a
small load outweighs Solapur's higher price. The engine is doing real economics,
not sorting by price.

---

## Defects found and fixed during the build

| # | Defect | Impact |
|---|---|---|
| 1 | `httpx 0.13.3` imports `cgi`, removed in Python 3.13 | **Every live data call silently failed**; all responses came from hardcoded fallbacks |
| 2 | `allow_redirects` removed in httpx 0.28 | Price scraper crashed once httpx was upgraded |
| 3 | `0.0 or float("inf")` falsy trap | Farmer's own market sorted **last** in every nearby-market search |
| 4 | Market suffix stripping ran once | "Lasalgaon Market Yard" → "Lasalgaon Market" |
| 5 | `_slug` stripped decimal points | **"2.5 quintal" parsed as 200 kg** — a 20% error on lot size |
| 6 | Coverage recomputed freshness instead of reading stored quality | MOCK rows reported as **LIVE** |
| 7 | Comparison truncated the origin market out of results | "Solapur is better" shown with no local baseline |
| 8 | 5 s of TTS dead air inside the voice webhook | Both test calls died at ~9 s. Fixed: webhook **5 s → 0.75 s** |
| 9 | `/api/call` had no authentication | Toll fraud — anyone with the URL could dial on the account |
| 10 | Twilio webhooks unverified | Forged requests could drive the AI and write to the database |
| 11 | `allow_origins=["*"]` with `allow_credentials=True` | Invalid combination; browsers reject it |

Items 3, 5, 6 and 7 were found **by the tests**, not by inspection.

---

## Honest state of the data

`GET /api/market/coverage` reports:

```json
{ "total_records": 3872, "mock_records": 3872, "real_records": 0, "is_demo_data": true }
```

**All stored price data is currently demo fixture data.** The live source is
unreachable:

```
data.gov.in unreachable after 3 attempts: ReadTimeout
(the configured key is data.gov.in's shared public sample key,
 which is heavily rate limited - register for a personal key)
```

The ingestion pipeline, validation rules and storage schema are identical for
real data. **Registering a personal data.gov.in key and setting
`DATA_GOV_API_KEY` is the only step between this and live mandi prices** — the
adapter, retries, validation and audit trail are already written and tested.

The recommendation engine **refuses** to advise on MOCK data unless
`MARKET_DEMO_MODE=true` is explicitly set, and stamps the output when it does.

---

## Marketplace scope — status after the second pass

| Section | Component | Status |
|---|---|---|
| §16–19 | Buyer registry, verification, structured demand, match scoring | Built |
| §20–21 | FPO entities, membership, lot aggregation | Built |
| §22–23 | Lot lifecycle, structured quality grading | Built |
| §26–27 | Payment escrow lifecycle, dispute engine | Built |
| §28 | Append-only audit trail with integrity checking | Built |
| §24–25 | Transport and storage **provider registries** | Tables exist; booking flow not wired |
| §39–40 | Admin analytics charts | Console tables only |

Details of the second pass are at the end of this document.

---

## Running it

```bash
python -m pytest tests/ -q
```

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001
```

Key endpoints:

```
GET  /api/market/price?commodity=onion&location=Kalaburagi
GET  /api/market/compare?commodity=onion&location=Kalaburagi&quantity=30%20quintal
GET  /api/market/trend?commodity=onion&location=Kalaburagi
GET  /api/market/sale-window?commodity=onion&storage_days=20
GET  /api/market/coverage
GET  /api/market/ingestion/runs
```

Write endpoints require `X-API-Key` (set in `.env`).

**Two things still need you:** register a personal data.gov.in key, and rotate
the Twilio auth token that is sitting in plaintext in `.env`.

---

## Transaction infrastructure (second build pass)

| Module | Lines | What it does |
|---|--:|---|
| `app/trade/schema.py` | 388 | 14 tables: buyers, demand, FPOs, lots, offers, logistics, payments, disputes, audit |
| `app/trade/audit.py` | 258 | Append-only event trail with monotonic sequence and tamper detection |
| `app/trade/buyers.py` | 864 | Verification state machine, document gating, behaviour-based trust scoring |
| `app/trade/fpo.py` | 633 | FPO registry, membership, compatible-lot pooling, weight-proportional settlement |
| `app/trade/lots.py` | 521 | 14-state lot lifecycle, layered quality grading (claim vs evidence) |
| `app/trade/matching.py` | 385 | 7-factor weighted farmer↔buyer matching with per-factor explanation |
| `app/trade/offers.py` | 389 | Offer lifecycle; acceptance is one transaction with a race guard |
| `app/trade/payments.py` | 440 | Escrow state machine in integer paise; settlement reconciliation |
| `app/trade/disputes.py` | 428 | Grievance workflow that freezes funds while open |
| `app/routes/trade_routes.py` | 483 | 37 REST endpoints |
| `static/console.*` | 1,490 | Operator console |

### What it proved

**FPO aggregation, five smallholders pooling 4,200 kg of Grade A onion:**

```
Sold separately   ₹1,14,836
Pooled            ₹1,23,938      one 7-ton truck instead of five small loads
Gain              ₹9,102         ₹1,820 per farmer · ₹217 per quintal
```

Not an assumption — computed with the same net-realisation model used for
market comparison, and covered by
`test_pooling_beats_shipping_separately`.

**Buyer trust is computed, never hardcoded.** The same buyer's farmer-facing
badge moved from *"Verified buyer, no trading history yet"* (60) to
*"Verified buyer, strong payment record"* (95) purely from settlement
outcomes. A second buyer with one default sits at 55 with a
`1 default(s)` flag.

**Settlement reconciles to the paisa.** A ₹1,11,520 aggregate sale splits
across four farmers by weight with the rounding remainder assigned to the
largest contributor: `11152000 == 11152000 paise`.

**Full lifecycle runs end to end:**
`lot → offer → accept → escrow → delivery → release → settlement split`,
with the lot and payment state machines kept in step and every step audited.

### Defects found and fixed in this pass

| # | Defect | Impact |
|---|---|---|
| 12 | Below-minimum grade scored 0.2, not 0.0 | A buyer requiring Grade A was shown Grade C produce — the lot would be rejected on arrival |
| 13 | `release()` attempted `DELIVERED → PAID` directly | Illegal transition failed silently; lot stayed `DELIVERED` while its payment read `RELEASED` |
| 14 | Offer expiry used `<` not `<=` | An offer expiring exactly now was still acceptable |
| 15 | Re-aggregation reported a status error, not the real cause | Misleading message for an already-pooled lot |
| 16 | Console showed "Pooling earns ₹0 more" for single-lot groups | Meaningless — one lot cannot be pooled |
| 17 | Speech pipeline ran on the webhook's own event loop (first via `BackgroundTasks`, then via `create_task`) | The gather webhook logged `returned in 0.065s` while Twilio actually waited 8–14s for the same response. The caller heard **silence instead of the hold clip**, and any turn crossing Twilio's 15s webhook deadline ended in **"an application error has occurred."** Fixed by running the pipeline in its own thread — webhook now answers in <0.1s and the redirect poll works as designed |

Defects 12–14 were found **by the tests**, not by inspection.

---

## Operator console

`GET /console` — nine views over the same APIs the voice agent calls.

Design constraint: **no number renders without its data quality.** The console
states at the top of every screen that all stored price data is demonstration
data, and every price row carries a `Demo data` tag. An evaluator cannot
mistake fixture data for live mandi prices, because the UI is built so that
mistake is impossible to make.

Restrained on purpose — one accent colour, borders rather than shadows,
tabular figures, no gradients. It reads as an operations console, not a
landing page. Light and dark both supported.

Views: Overview · Price discovery · Sale window · Lots · Buyers ·
FPO aggregation · Payments · Disputes · Audit trail.

---

## Still not built

| Section | Component |
|---|---|
| §24–25 | Transport and storage **provider registries** (tables exist; the cost model and booking flow do not yet read from them) |
| §39–40 | Admin analytics charts beyond the console's current tables |
| §26 | Binding payment RELEASE to a real payment provider webhook |

Everything else from the original scope is implemented and tested.


---

## Third pass: voice agent realigned to PS 26132, new frontend (2026-09-14)

### Why

The phone path still carried the pre-PS product. Substring routing sent
"should I **sell** now or wait?" to an auction listing with an inferred price,
and "who will **buy** my onion?" to a fertiliser order that debited a wallet.
Replies were written in English and machine-translated, so figures were read
aloud as symbols ("2480-3040 INR/ಕ್ವಿಂಟಲ್ (ಸರಾಸರಿ 2760)"). And the gather webhook
held its TwiML until the whole pipeline finished, which is what produced
"an application error has occurred" on calls.

### What the caller can do now

| PS 26132 need | Say (Kannada) | The agent |
|---|---|---|
| Price discovery | ಕಲಬುರಗಿಯಲ್ಲಿ ಈರುಳ್ಳಿ ಬೆಲೆ ಎಷ್ಟು? | Modal, range, per-kg; offers a market comparison |
| Localised trends, arrivals | ಬೆಲೆ ಏರುತ್ತಿದೆಯಾ? | Week change, month average, arrival change |
| Best market after transport | ಯಾವ ಮಾರುಕಟ್ಟೆ ಉತ್ತಮ? | Asks quantity, ranks by net realisation |
| Sale-window recommendation | ಈಗ ಮಾರಬೇಕಾ ಕಾಯಬೇಕಾ? | Asks storage days and cash need, then decides with reasons |
| Verified buyer demand | ಯಾರು ಖರೀದಿ ಮಾಡ್ತಾರೆ? | Verified buyers only, with trust record |
| Lot creation, quality | ಇಪ್ಪತ್ತು ಕ್ವಿಂಟಲ್ ಈರುಳ್ಳಿ ಮಾರಬೇಕು | Asks grade, reads back, lists on yes |
| Digital offers | ಆಫರ್ ಬಂದಿದೆಯಾ? | Best offer vs market price; accepts on yes |
| Payment tracking | ಹಣ ಬಂತಾ? | Escrow state in plain words, overdue warning |
| Grievance | ತೂಕದಲ್ಲಿ ಮೋಸ, ದೂರು ಕೊಡಬೇಕು | Picks the deal, confirms, files, freezes held money |
| FPO aggregation | ಎಫ್‌ಪಿಒ ಜೊತೆ ಒಟ್ಟಾಗಿ ಮಾರಿದರೆ? | The farmer's own pool and the rupee gain |

### Architecture

```
speech -> Gemini 3.5 Flash-Lite (intent + slots + English gloss, ~1.8s)
       -> dialogue manager (engines only, no invented figures)
       -> Kannada composed directly from the numbers
       -> edge-tts in-process, content-addressed cache
       -> call transcript (voice_calls / voice_turns) + live stream
```

| Module | What it does |
|---|---|
| `app/voice/nlu.py` | Structured intent/slot parsing from Kannada; keyword fallback when Gemini is unavailable |
| `app/voice/dialogue.py` | PS-scoped conversation: slot filling, suggestions, yes/no confirmation for anything that commits the farmer |
| `app/voice/kannada.py` | Kannada names for all 24 crops and 57 markets; TTS-safe numbers |
| `app/voice/speech.py` | In-process synthesis, cached by hash of the text |
| `app/voice/pipeline.py` | One turn end to end, recorded |
| `app/voice/call_log.py` | Persistent transcripts and a sequenced live feed |
| `app/twilio_handlers.py` | Rewritten: hold prompt, poll loop, interruptible replies, silence handling, hangup |
| `app/routes/voice_routes.py` | Overview, farmer workspace, calls + SSE stream, browser simulator, demo seeding |
| `app/trade/demo_seed.py` | Demo marketplace around one phone number, every entity registered as a fixture |

### Defects found and fixed in this pass

| # | Defect | Impact |
|---|---|---|
| 17 | Speech pipeline ran inside the webhook's response cycle | Caller heard silence; slow turns ended in "application error" |
| 18 | Substring intent routing | Sell-or-wait became an auction listing; "who will buy" became a fertiliser order |
| 19 | English replies machine-translated to Kannada | Symbols and ranges read aloud; stiff phrasing |
| 20 | Unsupported crop reused the previous crop | "Dragon fruit price" answered with the tur price |
| 21 | Payment status ignored the crop named | "My tur money" reported an onion payment |
| 22 | Complaint reply always claimed money was in escrow | False reassurance on an unpaid deal |
| 23 | Demo caveat appended after the closing question | Turn ended on a statement; farmer did not know to answer |
| 24 | `/api/farmer/{phone}/call` unauthenticated | Anyone could dial any number on the account |
| 25 | Provenance tests read the real clock | Passed on the day written, failed three days later |

Defects 20 to 23 were found by running a full scripted Kannada conversation,
not by inspection.

### Measured

- Intent parsing: 13 of 13 Kannada test phrases correct, 1.7 to 2.4 s.
- Webhook response: under 0.1 s (it was 8 to 14 s).
- Full turn: 4 to 7 s. Speech synthesis is most of it (about 1 s per 110
  characters). Splitting sentences and synthesising them in parallel was
  measured slower (5.9 s against 3.9 s) and was not kept. Cached sentences
  are instant.

### Frontend

`/` now serves one product for the current feature set; the old Stitch pages
(wallet, input orders, auctions, crop prediction) redirect to it.

| Page | For | Shows |
|---|---|---|
| `/` | Everyone | What it does mapped to PS 26132, live platform numbers, net-of-transport comparison |
| `/prices` | Farmers, extension staff | Price, trend with arrivals, sell-or-wait with reasons and risks, market ranking with costs |
| `/farmer` | Farmer | Offers to decide, produce, payment progress, complaints, FPO pool, call history |
| `/buyer` | Trader, processor | Verification and documents, demand, produce open for offers, payments to make |
| `/fpo` | FPO manager | Pools with separate-vs-pooled rupee benefit, one-click pooling, settlement split |
| `/calls` | Evaluators, operators | Live transcripts (Kannada + English, intent, data quality, timings), and talk to the agent in the browser |

Tests: 314 passing (29 new for the voice agent).
