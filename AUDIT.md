# Technical Audit — AI Krishi Voice Assistant
### Benchmarked against SIH PS **26132** — *"Strengthening market linkages and price discovery for farmers"*

**Organisation:** Government of Maharashtra · Maharashtra State Innovation Society
**Audit date:** 2026-09-11 · **Codebase:** `D:\ai calling agent` · **~6,500 LOC Python**

> Every claim marked **VERIFIED** was executed against the running system during this audit.
> Items marked INFERRED were read from source but not executed.

---

## 1. Executive Summary

The project is a **working Kannada voice agent**, not yet a market-linkage platform.

The telephony and conversational-AI layer is genuinely strong and end-to-end functional: a real
phone call was placed and answered during this audit, and the full
`speech → translate → LLM (with tool calling) → translate → TTS → inject audio` loop completes in ~14.6s.

The **market linkage and price discovery layer — the entire subject of PS 26132 — is largely mock
data.** Price discovery reads a *single* mandi through a brittle HTML scraper that returns
**January 2024 prices labelled as "Live"**. Buyers, dealers and "Verified Partner 🟢" badges are
hardcoded Python dictionaries. There is **no price trend analysis, no forecasting, and no
sale-window recommendation logic anywhere in the codebase** — these are the PS's headline asks.

**Coverage against the PS's 17 named capabilities: 2 met, 3 partial, 12 missing.**

There are also **production-blocking defects**, four of which were fixed during this audit, and a
**critical unauthenticated endpoint that lets anyone place phone calls on your Twilio account**.

---

## 2. What Genuinely Works — VERIFIED

| Capability | Evidence |
|---|---|
| FastAPI app boots; ~60 routes registered | `from app.main import app` — clean import |
| **Outbound calling** | Calls `CA429e7360…` and `CA0bd355a8…`: `queued → ringing → in-progress → completed` to `+918618075133` |
| Webhook returns valid TwiML over public tunnel | `POST /twilio/voice` → `<Response><Play>…<Gather>` |
| Audio served publicly to Twilio | `GET /audio/greeting.mp3` → `200, audio/mpeg, 67,008 bytes` |
| **Gemini 2.5 Flash agent + function calling** | Live answer returned; `🔧 AGENT TOOL CALLED: get_market_price(...)` in logs |
| Kannada TTS | edge-tts `kn-IN-GaganNeural`, 22KB MP3 generated |
| KN→EN→KN translation | `"ನನ್ನ ರಾಗಿ ಬೆಳೆಗೆ ಯಾವ ಗೊಬ್ಬರ"` → `"What fertilizer for my millet crop"` |
| **Full voice pipeline** | Log: translate → Gemini → Kannada → TTS → audio URL, **14.58s total** |
| Weather (OpenWeatherMap) | `{'temperature': 30.37, 'humidity': 47, …}` — *only after the httpx fix in §3.1* |
| Auction / bidding engine | `create_listing`, `place_bid`, `accept_bid`, `close_listing`, `complete_order`, seller & buyer dashboards — `app/services/auction_service.py` (627 LOC) |
| Farmer identity + wallet | UGFID generation, wallet debit, orders, transactions — `app/farmer_store.py` |
| Digital twin | `farmer_twin` table with computed `risk_score` — `app/services/twin_service.py` |

**This is a real, working voice product.** The critique that follows is about the *market* layer.

---

## 3. Critical Defects

### 3.1 `httpx 0.13.3` is dead on Python 3.14 — ALL live data was silently failing — **FIXED**

The installed `httpx` (v0.13.3, released 2020) runs `import cgi` at module load.
**`cgi` was removed from the standard library in Python 3.13**; this project runs **Python 3.14.3**.

```
File "httpx/_models.py", line 1, in <module>
    import cgi
ModuleNotFoundError: No module named 'cgi'
```

Five modules import httpx: `agent_tools`, `agriculture`, `crop_predictor`, `gov_data`, `speech`.
Every one wraps its call in `try/except` and **falls back to hardcoded data on failure**.

> **The system appeared to work while serving fabricated data.** Weather, mandi prices and all
> data.gov.in calls were failing on every single request, silently.

**Fixed:** upgraded to `httpx 0.28.1`. Weather immediately began returning live values.

### 3.2 `allow_redirects` removed in httpx 0.28 — price scraper crashed — **FIXED**

Once httpx was upgraded, `app/agent_tools.py:83` broke:
`Client.get() got an unexpected keyword argument 'allow_redirects'`.
**Fixed:** renamed to `follow_redirects=True`. The scraper now returns data.

### 3.3 Market prices are from **January 2024**, presented to farmers as *"Live"* — **OPEN**

With the scraper repaired, this is what it returns **today**:

```
Onion  -> {'date': '03/01/2024', 'min_price': 500, 'max_price': 2000, 'modal_price': 1250}
Tomato -> {'date': '03/01/2024', 'min_price': 400, 'max_price': 1500, 'modal_price':  950}
```

The agent then tells the farmer:
`"market_trend": "Live Kalaburgi APMC update from 03/01/2024."`

**A price-discovery product is quoting 2.5-year-old prices as current.** For PS 26132 this is a
correctness failure at the core of the problem statement, and worse than showing nothing at all.

### 3.4 `/api/call` has **zero authentication** — toll fraud — **OPEN**

`app/twilio_handlers.py:832`. Anyone who learns the public URL can POST a phone number and your
Twilio account dials it. There is **no auth on any endpoint in the application** — no API key, no
session check, no `Depends(...)`. `debit_wallet()` is likewise reachable given only a phone number.

### 3.5 No Twilio webhook signature validation — **OPEN**

`twilio.request_validator.RequestValidator` is never used. Anyone can forge
`POST /twilio/gather`, drive the AI, burn Gemini quota, and write to your database.

### 3.6 Unsafe CORS — **OPEN**

`app/main.py:192` — `allow_origins=["*"]` **together with** `allow_credentials=True`. Browsers
reject this combination, and it signals that the auth model was never designed.

### 3.7 Five seconds of dead air at call start — **FIXED**

The personalised greeting was TTS-generated *inside* the `/twilio/voice` webhook.
Call `CA0bd355a8…`: answered `13:43:08`, audio ready `13:43:13`. The caller heard **5 seconds of
silence**, then hung up at 10s. Both test calls ended at ~9s — consistent with callers giving up.

**Fixed:** greetings are now content-addressed (`greet_<sha256[:24]>.mp3`) and cached on disk, so
they are reused across processes, and pre-warmed before dialling.
**Measured: webhook response 5s → 0.75s.**

### 3.8 `mandi_location` is silently ignored — **OPEN**

```python
get_market_price(crop='Onion', mandi_location='Bangalore')
# -> {'mandi': 'Kalaburgi APMC', ...}
```

The URL in `_fetch_kalaburgi_apmc_price` is hardcoded to `…/karnataka/gulbarga`.
**The system can only ever discover one mandi's price** — fatal for
"price discovery *across nearby markets*".

### 3.9 The `data.gov.in` integration is dead code — **OPEN**

`app/gov_data.py` (231 LOC) implements a *proper* multi-state, multi-district, multi-commodity
mandi API client — **and nothing calls it.** The voice agent uses the scraper instead.

Worse, it is unusable as configured:

- `DATA_GOV_API_KEY` is **not in `.env`**; it falls back to the hardcoded default at `config.py:36`,
  which is data.gov.in's **public sample key** (heavily throttled).
- Every request **times out**: `ReadTimeout` at both 25s and 60s.

This is the biggest missed opportunity in the codebase: the right architecture already exists,
merely disconnected.

### 3.10 `googletrans` pins `httpx==0.13.3` — **OPEN**

```
googletrans 4.0.0rc1 requires httpx==0.13.3, but you have httpx 0.28.1
```

Translation still worked under test, but the project is one `pip install` from breaking.
`googletrans 4.0.0rc1` is an **unmaintained release candidate** scraping an undocumented endpoint —
unacceptable on the critical path of a government deployment.

### 3.11 Buyers, dealers and schemes are hardcoded mocks — **OPEN**

`find_dealers`, `find_crop_buyers` and `find_government_schemes` return literal Python dicts.
`"status": "Verified Partner 🟢"` is **a hardcoded string**. There is no buyers table, no KYC, no
verification of any kind. The PS explicitly requires *"verified buyers"* and *"buyer credentials"*.

### 3.12 No price trend, forecast, or sale-window logic exists — **OPEN**

A codebase-wide search for `forecast|price_history|sale_window|moving_average|predict_price|trend_analysis`
returns **zero matches**. The `market_trend` field is a hand-written English sentence in a dict —
e.g. `"Volatile. Expected to drop next week."` — static text, identical regardless of actual market data.

**PS 26132 names "localised price trends and sale-window recommendations" as a primary deliverable.
This is entirely absent.**

### 3.13 "Predictions" are LLM free-text, not models — **OPEN**

`app/crop_predictor.py` prompts Gemini for JSON. Yield figures are **language-model guesses**
presented as predictions, with no agronomic model, training data, or confidence interval.

### 3.14 Secrets in plaintext; not under version control — **OPEN**

`.env` holds a live Twilio auth token, Gemini key and OpenWeather key. There is no `.gitignore`,
and the directory is not a git repository — no history, no rollback, no review.
**The Twilio auth token in `.env` should be rotated**, and `.env` must never be committed.

### 3.15 Stale Twilio configuration — **FIXED**

`.env` specified `+12764479081`, owned by **no** account. The number supplied mid-session
(`+17372508034`) was rejected by Twilio (`403 — 'from' number isn't assigned`); it had been
**released** — its resource `PNba4a5cfd…`, recovered from call history, now returns **404**.
The actually-working trial number is **`+17372212163`**, found by inspecting call history.
`.env` now points at it, using API-key auth via `config.get_twilio_client()`.

### 3.16 Fragile call-holding mechanism — **OPEN**

`/twilio/gather` returns a hardcoded `<Pause length="40"/>` while background work runs. The pipeline
measured **14.58s**; if Gemini is slow or rate-limited the caller hits silence and the call drops.
There is no streaming, no incremental audio, and no timeout safety net.

### 3.17 Zero tests — **OPEN**

No `pytest`, no test directory, no CI. ~6,500 LOC with no automated verification.
`accept_bid()` moves money and has no test at all.

### 3.18 SQLite under a global lock — **OPEN**

`DB_LOCK` serialises all writes. Acceptable for a demo; will not survive concurrent FPO usage.

---

## 4. Gap Analysis vs PS 26132

The problem statement names 17 required capabilities. Assessed individually:

| # | PS 26132 requirement | Status | Evidence / gap |
|---|---|---|---|
| 1 | Aggregate **mandi prices** | 🟡 Partial | One mandi only (Kalaburgi), scraped, **2024 data**; `mandi_location` ignored |
| 2 | Aggregate **buyer demand** | 🔴 Missing | `find_crop_buyers` returns 2 hardcoded dicts |
| 3 | **Quality specifications** | 🔴 Missing | No such field in any schema |
| 4 | **Arrival volumes** | 🔴 Missing | Not modelled. Present in the data.gov.in feed — unused |
| 5 | **Transport options** | 🔴 Missing | No logistics model |
| 6 | **Storage options** | 🔴 Missing | Only a hardcoded string `"cold storage"` inside a price trend |
| 7 | **Localised price trends** | 🔴 Missing | No time-series stored; `market_trend` is static text |
| 8 | **Sale-window recommendations** | 🔴 Missing | No forecasting code exists |
| 9 | Match farmers/FPOs with **verified buyers** | 🔴 Missing | "Verified Partner 🟢" is a literal string; no KYC |
| 10 | **Lot creation** | 🟢 Met | `auction_service.create_listing()` — solid |
| 11 | **Quality grading** | 🔴 Missing | No grade / variety / moisture fields |
| 12 | **Digital offers** | 🟢 Met | `place_bid` / `accept_bid` with highest-bid logic |
| 13 | **Logistics coordination** | 🔴 Missing | Not modelled |
| 14 | **Payment tracking** | 🟡 Partial | Wallet + `order_status` text; no escrow, settlement or reconciliation |
| 15 | **Dispute / grievance** | 🔴 Missing | Zero references in the codebase |
| 16 | **FPO aggregation** | 🔴 Missing | No FPO or group entity. The PS names FPOs three times |
| 17 | **Transparent transaction records** | 🟡 Partial | `transactions` table + activity feed exist; no immutable audit trail |

**Score: 2 met · 3 partial · 12 missing.**

### Against the PS's stated outcomes

| Outcome | Assessment |
|---|---|
| Improved farmer price realisation | ❌ Cannot be claimed — quoting 2024 prices from a single mandi |
| Reduced information asymmetry | ⚠️ Voice access in Kannada is a **genuine, strong contribution** |
| Lower transaction cost | ⚠️ The auction flow helps; unverified counterparties undermine it |
| Stronger FPO aggregation | ❌ FPOs are not modelled at all |
| Reduced post-harvest loss | ❌ Requires sale-window + storage logic; neither exists |
| Reliable buyer sourcing | ❌ Buyers are fictional |
| Transparent transaction records | ⚠️ Partial |

### The real strategic position

**The project's genuine differentiator is the Kannada voice channel.** A smallholder with a feature
phone and no literacy can call a number and talk to an agent. That directly attacks
*"limited visibility"* and *"information asymmetry"*, and most competing submissions will be
app-only. **That is a winning wedge.**

But the PS is scored on **market linkage and price discovery**, and that is precisely the layer
built on mock data. The build is currently *inverted* relative to what is being assessed: heavy
investment in the delivery channel, near-zero investment in the substance being delivered.

---

## 5. Improvement Plan

### Phase 0 — Stabilise (1–2 days) · do this first

| # | Action | Where |
|---|---|---|
| 0.1 | ✅ Upgrade `httpx` to ≥0.27 | `requirements.txt` |
| 0.2 | ✅ `allow_redirects` → `follow_redirects` | `agent_tools.py:83` |
| 0.3 | **Drop `googletrans`** → Google Cloud Translate or IndicTrans2 | `translation.py` |
| 0.4 | **Pin every dependency**; verify the set on Python 3.14 | `requirements.txt` |
| 0.5 | **Add API-key auth** to `/api/call`, `/api/process-speech`, all wallet routes | `main.py`, `twilio_handlers.py` |
| 0.6 | **Validate Twilio signatures** with `RequestValidator` | new `app/security.py` |
| 0.7 | Fix CORS — explicit origin allowlist | `main.py:192` |
| 0.8 | `git init`, `.gitignore` covering `.env`, **rotate the exposed Twilio token** | repo root |
| 0.9 | **Never label stale data "Live"** — surface the real date and its age | `agent_tools.py` |
| 0.10 | Replace silent `except → mock` with logged degradation plus a `data_quality` flag on every payload | all data modules |

> **0.10 is the most important line in this document.** The silent-fallback pattern is why a total
> data outage went unnoticed. Mock data must be *labelled* as mock, all the way through to the farmer.

### Phase 1 — Make price discovery real (1 week) · **highest scoring impact**

1. **Obtain a personal data.gov.in API key** (the sample key is throttled and times out) and put it in `.env`.
2. **Wire `gov_data.fetch_mandi_prices()` into the agent**, replacing the Kalaburgi scraper.
   The correct client already exists and is unused — this is mostly deletion.
3. **Honour `mandi_location`.** Resolve the farmer's district → query *all* mandis within ~100km →
   return a ranked table, not a single number.
4. **Persist a price time-series:**

```sql
CREATE TABLE mandi_price_history (
    id INTEGER PRIMARY KEY,
    state TEXT, district TEXT, market TEXT,
    commodity TEXT, variety TEXT, grade TEXT,
    min_price REAL, max_price REAL, modal_price REAL,
    arrival_qty REAL,                 -- PS requirement #4
    arrival_date TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,
    UNIQUE(market, commodity, variety, arrival_date)
);
CREATE INDEX idx_mph_lookup
    ON mandi_price_history(commodity, district, arrival_date DESC);
```

   Backfill with a daily scheduled job. **Without history there can be no trends and no sale windows.**

5. **Trend engine** — 7/30/90-day moving averages, percentage change, volatility, seasonal index.
6. **Sale-window recommendation** (PS #8) — the flagship feature. Combine seasonal index, current
   price vs five-year normal, arrival volumes and storability to produce output such as:

   > *"Hold for 12 days. Onion in Kalaburgi typically rises 18% after mid-October arrivals fall.
   > Confidence: medium. If you must sell now, Basavakalyan is ₹180/quintal above Kalaburgi today."*

   **Always show the reasoning and a confidence level — never a bare number.**
7. **Nearby-mandi comparison** with transport cost netted out, giving a *net realisable price*.

### Phase 2 — Verified buyers and FPO aggregation (1–2 weeks)

```sql
CREATE TABLE buyers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT,                            -- APMC_TRADER|PROCESSOR|EXPORTER|INSTITUTIONAL|FPO
    gstin TEXT, apmc_licence_no TEXT, pan TEXT,
    verification_status TEXT NOT NULL,    -- UNVERIFIED|PENDING|VERIFIED|SUSPENDED
    verified_at INTEGER, verified_by TEXT,
    rating REAL,
    total_txns INTEGER DEFAULT 0,
    payment_default_count INTEGER DEFAULT 0,
    avg_payment_days REAL,
    service_districts TEXT
);

CREATE TABLE buyer_demand (               -- PS requirement #2
    id TEXT PRIMARY KEY,
    buyer_id TEXT NOT NULL,
    commodity TEXT NOT NULL, variety TEXT,
    min_grade TEXT,
    quantity_needed REAL, unit TEXT,
    price_offered REAL,
    delivery_district TEXT,
    window_start INTEGER, window_end INTEGER,
    quality_spec_json TEXT,               -- PS requirement #3
    status TEXT,
    FOREIGN KEY(buyer_id) REFERENCES buyers(id)
);

CREATE TABLE fpos (                       -- PS requirement #16
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    registration_no TEXT, district TEXT,
    member_count INTEGER,
    verification_status TEXT
);

CREATE TABLE fpo_members (
    fpo_id TEXT, farmer_phone TEXT, joined_at INTEGER,
    PRIMARY KEY(fpo_id, farmer_phone)
);
```

- **Replace `"Verified Partner 🟢"` with a computed badge** derived from `verification_status`,
  `payment_default_count` and `avg_payment_days`. Never hardcode trust.
- **Aggregation:** pool member lots into a single FPO lot to reach buyer minimum volumes. This
  addresses the PS's *"buyers struggle to aggregate consistent volumes"* pain directly, and is the
  highest-value feature most competing teams will not build.

### Phase 3 — Lot quality, logistics, payments (2 weeks)

```sql
ALTER TABLE listings ADD COLUMN variety TEXT;
ALTER TABLE listings ADD COLUMN grade TEXT;             -- PS #11
ALTER TABLE listings ADD COLUMN moisture_pct REAL;
ALTER TABLE listings ADD COLUMN quality_notes TEXT;
ALTER TABLE listings ADD COLUMN photo_urls TEXT;
ALTER TABLE listings ADD COLUMN pickup_village TEXT;    -- PS #13
ALTER TABLE listings ADD COLUMN pickup_district TEXT;
ALTER TABLE listings ADD COLUMN harvest_date TEXT;
ALTER TABLE listings ADD COLUMN storage_type TEXT;      -- PS #6
ALTER TABLE listings ADD COLUMN fpo_id TEXT;
```

```sql
CREATE TABLE payments (                   -- PS #14
    id TEXT PRIMARY KEY,
    listing_id TEXT NOT NULL, bid_id TEXT,
    amount REAL NOT NULL,
    status TEXT NOT NULL,                 -- PENDING|ESCROW|RELEASED|FAILED|REFUNDED
    method TEXT, utr_ref TEXT,
    initiated_at INTEGER, settled_at INTEGER, due_date INTEGER
);

CREATE TABLE disputes (                   -- PS #15
    id TEXT PRIMARY KEY,
    listing_id TEXT NOT NULL,
    raised_by TEXT, raised_by_role TEXT,
    category TEXT,                        -- QUALITY|PAYMENT|QUANTITY|LOGISTICS
    description TEXT,
    status TEXT,                          -- OPEN|UNDER_REVIEW|RESOLVED|ESCALATED
    resolution TEXT,
    created_at INTEGER, resolved_at INTEGER
);

CREATE TABLE transport_providers (        -- PS #5
    id TEXT PRIMARY KEY,
    name TEXT, phone TEXT,
    vehicle_type TEXT, capacity_kg REAL,
    rate_per_km REAL, service_districts TEXT,
    verification_status TEXT
);
```

- **Quality grading through the voice call:** the farmer already has a phone — let them describe
  the lot and have Gemini map the description to a grade, flagged *self-declared* until verified.
- **Photo grading over WhatsApp** with Gemini vision is a natural, high-impact extension.

### Phase 4 — Make the voice agent trustworthy (ongoing)

- **Ground every market claim in a retrieved record**, speaking the mandi name and date aloud.
  Never let the LLM invent a price.
- **Fix the dead-air architecture:** stream partial audio, or play a "checking prices" filler driven
  by real tool-call progress, instead of a blind 40s `<Pause>`.
- **Log every tool call** with inputs, outputs and data source, for auditability. A government
  deployment will require this.
- **Add tests.** The price parser, grading logic and bid acceptance are pure functions and trivially
  testable.

---

## 6. Priority Order

| Priority | Item | Why |
|---|---|---|
| **P0** | Auth on `/api/call` + Twilio signature validation (§3.4, §3.5) | Live toll-fraud exposure, costs real money |
| **P0** | Rotate the exposed Twilio token; `.gitignore` the `.env` (§3.14) | Credentials sit in plaintext |
| **P0** | Stop labelling 2024 data "Live" (§3.3) | Actively misleads farmers about price |
| **P1** | Real multi-mandi prices via data.gov.in (§3.9, Phase 1.1–1.3) | The core of the PS; the client is already written |
| **P1** | Price history → trends → **sale windows** (Phase 1.4–1.6) | The PS's flagship ask; currently 100% absent |
| **P1** | Remove the silent mock-fallback pattern (§0.10) | Root cause of the undetected outage |
| **P2** | Verified buyer registry + demand board (Phase 2) | PS's "verified buyers" and "buyer demand" |
| **P2** | FPO aggregation (Phase 2) | Strongest differentiator; named three times in the PS |
| **P3** | Quality grading, logistics, payments, disputes (Phase 3) | Completes the transaction loop |
| **P3** | Replace `googletrans`; stop using the LLM as a predictor (§3.10, §3.13) | Production robustness |
| **P4** | Tests, CI, migration to Postgres | Engineering maturity |

---

## 7. Bottom Line

**Strength:** a working, genuinely accessible Kannada voice agent over real telephony, with LLM
tool-calling and a competent auction engine underneath. The hard, unglamorous telephony engineering
is done and it works — that is not easy, and it is a real asset.

**Weakness:** the layer PS 26132 actually judges — price discovery and market linkage — is mock data
behind a live-data façade. The system quotes one mandi's 2024 prices as current, and the PS's
headline deliverable, sale-window recommendations, does not exist in any form.

**The fastest path to a competitive submission is not more features.** It is **Phase 0 + Phase 1**:
make the price data genuinely live, multi-mandi and historical, then build trends and sale-window
recommendations on top. The data.gov.in client is already written and merely disconnected, so this
is far less work than it appears.

Then lead the demo with the thing nobody else will have: **a farmer on a feature phone calling a
number and being told, in Kannada, which nearby mandi pays most today — and whether to sell or wait.**
