# NotebookLM Prompt for Hackathon PPT Generation

Use this compact, high-quality prompt directly in NotebookLM.

---

## Copy-Paste Prompt

You are a top-tier startup pitch deck creator, product storyteller, and visual presentation architect.

Create a premium hackathon deck for AI Krishi, a production-grade agri AI platform.

Goal:
- Win innovation and execution categories.
- Show emotional user pain, real product depth, technical credibility, and business value.

Audience:
- Hackathon judges
- Startup mentors
- Product and engineering reviewers

Style and quality bar:
- Modern, clean, high-trust, cinematic where needed
- No generic template output
- Visually rich, low text density, high clarity

## Output Scope

Create:
1. Main deck: 16 to 18 slides
2. Compressed deck: 5-minute version
3. Full deck: 10 to 12 minute version
4. Appendix: Q&A backup slides

For each main slide provide:
- Slide Number
- Slide Title
- Narrative Goal (1 line)
- Slide Content (max 5 bullets)
- Suggested Visuals
- Suggested Image Keywords (Unsplash/Pexels style)
- Optional AI Image Prompt
- Presenter Script (30 to 60 sec)

If output is cut off, continue from the last unfinished slide without repeating earlier sections.

## Visual Rules (Must Follow)

- At least 6 image-heavy slides
- Mix emotional farm imagery, product UI screenshots, and architecture visuals
- Use iconography for voice AI, auctions, wallet, digital twin, real-time stream, and analytics
- Include full-bleed visuals for opening, problem, impact, and closing
- Include subtle transition guidance per section (fade, push, zoom, reveal)

## Product Context (Include All)

Product: AI Krishi

Positioning:
A multilingual, voice-first agriculture operating system combining advisory, transactions, auction marketplace, and digital twin intelligence.

Implemented capabilities:
1. Platform
- FastAPI backend
- SQLite persistence (`data/krishi.sqlite3`)
- Modular models/routes/services
- Transaction-safe order and wallet flows

2. Voice AI assistant
- Twilio voice integration
- Kannada-first flow
- Gemini advisory
- Handles mandi queries
- Creates sell listings via call
- Places bids via call
- Handles buy/order with confirmation and wallet checks

3. Farmer system
- Farmer create/update profile
- Wallet and transactions
- Order history
- Dashboard with live context

4. Auction engine
- Reverse listings
- Bidding with base-price validation
- Highest bid tracking
- Accept bid to order conversion and buyer wallet debit
- Close listing and auto-expiry
- Complete order lifecycle support

5. Seller dashboard
- Listing management (active/expired/sold)
- Top bid and bid history
- Revenue KPIs
- Accept, close, complete actions
- Real-time updates

6. Buyer dashboard
- Open listing discovery
- Bid placement
- Personal bid history and KPIs

7. Digital Twin
- Profile fields: crops, soil, water, yield, preferences, risk score
- Auto-updated from order and sale behavior
- Recommendations endpoint
- Twin context used in AI advisory

8. Real-time intelligence
- Activity feed API
- SSE stream
- Events: listing_created, bid_placed, bid_accepted, twin_updated

9. Multi-role frontend
- Stitch pages: landing, sign-in, farmer, seller, buyer, admin
- Role-based sign-in routing

10. Decision intelligence
- Weather and soil enrichment
- Market price APIs
- Crop and yield prediction modules

## Story Flow (Enforce This Sequence)

Section A: Problem and urgency
- Fragmented workflows, poor price discovery, delayed decisions
- Emotional rural context

Section B: Vision
- AI Krishi as a farmer operating system, not only a chatbot

Section C: Solution walkthrough
- Voice assistant
- Farmer dashboard
- Auction system (seller and buyer)
- Digital twin recommendations

Section D: Technical depth
- Architecture diagram
- Data flow
- API and DB integrity
- Real-time eventing model

Section E: Demo journey
- Farmer calls AI
- Creates listing
- Buyers bid
- Seller accepts
- Wallet and order updates
- Twin refresh and personalized recommendation

Section F: Impact and business value (max 2 slides)
- Slide 1: KPI scorecard with baseline vs AI Krishi
- Slide 2: Before/after farmer journey and unit economics snapshot

Section G: Defensibility and scale
- Voice plus workflow plus marketplace plus twin data loop moat
- District to state rollout roadmap

Section H: Closing
- Why now
- Why this team
- Clear call to action

## Mandatory Slides

Include dedicated slides for:
- Problem statement
- Architecture
- Database and transaction integrity
- Reverse auction mechanics
- Digital twin loop
- SSE/live event flow
- Sample call utterances
- Pilot/traction plan
- Risk and mitigation
- Roadmap

Include one slide with exact sample voice commands:
- sell 50kg onion
- place bid 31 rupees per kg for 20 kg onion
- what is current mandi rate
- order urea 2 bags

Include one slide for role and portal map:
- farmer
- seller
- buyer
- admin
with route and core actions.

## Required Diagram Guidance

Provide:
- 3 architecture diagram options
- 2 process flow options
- 1 marketplace lifecycle timeline
- 1 digital twin intelligence cycle diagram

## Final Deliverables from You

After slides, also output:
1. 5-minute slide order
2. 10 to 12 minute slide order
3. Q&A backup list
4. Design system recommendation (fonts, colors, icon style)
5. Final pre-demo checklist

Add a final section: Replace with Real Metrics Here

Include placeholders for:
- Farmers onboarded
- Calls handled
- Listings created
- Bid conversion rate
- Order completion rate
- Response latency
- Model fallback success rate

Language quality rules:
- Crisp, persuasive, demo-ready
- No generic cliches
- Make product feel real and already working

---

## Optional Refinement Prompt

Refine the generated deck:
- Reduce text density by 25%
- Strengthen visual hierarchy
- Convert dense bullets into visual blocks
- Add micro-headlines
- Ensure one key takeaway per slide
- Tighten continuity from problem to demo to impact to close

---

## Suggested Assets to Upload to NotebookLM

- Farmer dashboard screenshots
- Seller dashboard screenshots
- Buyer dashboard screenshots
- API route summary
- Architecture sketch
- Feature list markdown
- One sample call transcript

Then ask:
Use uploaded screenshots directly in product proof slides with captions.
