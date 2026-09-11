# Build Status — Market Intelligence & Transaction Engine

**Against:** SIH PS 26132 · *Strengthening market linkages and price discovery for farmers*
**Last updated:** 2026-09-11

**This build so far:** 4,868 lines of new production code · 211 tests, all passing.

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

## Not yet built

Scoped but not started. Roughly in dependency order:

| Section | Component |
|---|---|
| §16–19 | Buyer registry, verification state machine, structured demand, farmer↔buyer match scoring |
| §20–21 | FPO entities, membership, lot aggregation into buyer-ready volumes |
| §22–23 | Full lot lifecycle (DRAFT→COMPLETED), structured quality grading |
| §24–25 | Transport provider registry, storage provider registry |
| §26–27 | Payment lifecycle with escrow states; dispute/grievance engine |
| §28 | Immutable audit event trail |
| §39–40 | Admin control centre and analytics dashboard |

The FPO aggregation work (§20–21) has a ready-made economic justification from
the engine already built: freight per quintal falls sharply with load size, so
pooling smallholder lots measurably raises every member's net realisation. That
is tested today in `test_logistics.py::test_freight_per_quintal_falls_as_load_grows`.

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
