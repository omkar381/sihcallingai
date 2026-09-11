# Google Stitch Frontend Prompt (AI Krishi SaaS)

Use this exact prompt in Google Stitch to generate a top-tier SaaS frontend for an AI agriculture voice assistant platform.

---

## MASTER PROMPT TO PASTE IN GOOGLE STITCH

Build a complete, premium, production-ready SaaS frontend called "Krishi AI" for Indian farmers and agri-operations teams.

Core product context:
- Krishi AI is a Kannada-first AI farming voice assistant.
- Farmers can receive advice via phone calls, place orders for inputs, and check wallet/orders.
- Admin team monitors live market prices, real-time events, and call/order activity.
- Backend APIs already exist; this task is frontend only.

Design objective:
- Create a visually exceptional SaaS interface at Dribbble/Stripe/Linear quality level.
- Look modern, premium, and trustworthy for agri-fintech + AI.
- Keep it highly usable for real-world users, including low-tech users on mobile.

Do not create generic template UI. Create a distinct visual identity.

### 1) Brand and Visual Direction

Brand personality:
- Intelligent
- Grounded in agriculture
- Trustworthy and operationally reliable
- Warm and human, not robotic

Color direction (avoid purple-heavy themes):
- Primary green: #006A39
- Accent green: #64DD91
- Earth tone accent: #8D6E63
- Base background: #FAF9F7
- Surface: #FFFFFF with soft glass overlays
- Text primary: #1A1C1B
- Text secondary: #3E4A3F

Typography:
- Headlines: Manrope ExtraBold
- Body/UI: Inter Regular/Medium/Semibold
- Numeric highlights: Manrope Bold

Visual style:
- Glassmorphism used selectively for hero cards and overlays.
- Soft gradients and atmospheric farm-inspired backgrounds.
- Rounded corners: 16px, 24px, 32px for hierarchy.
- Subtle depth shadows; avoid heavy dark shadows.

### 2) Required Pages and Routes

Create all these pages with consistent shared layout components:

1. Landing page (`/`)
2. Farmer dashboard (`/farmer`)
3. Admin operations dashboard (`/dashboard`)
4. Sign in page (`/signin`)
5. 404 page (`/404`)

### 3) Shared Layout Components

Generate reusable components and use them across pages:
- Top navigation bar (desktop + mobile variants)
- Sidebar (for dashboard pages)
- Reusable stat cards
- Reusable data table
- Reusable activity feed list
- Reusable CTA buttons (primary, secondary, ghost)
- Reusable status badges (success, warning, pending, error)
- Skeleton loaders
- Empty states
- Toast notifications
- Modal dialog for confirmations

### 4) Landing Page Details (`/`)

Sections in order:
- Sticky top nav with logo "Krishi AI", links, CTA "Get Started"
- Hero with bold headline: "Your Personal AI Farming Expert - Just One Call Away"
- Subheadline emphasizing local language and instant phone access
- Live KPI card cluster (temperature, mandi trend, active calls)
- Primary CTA card with phone input and "Call Me Now"
- "How it works" 3-step process
- Feature grid (voice AI, market intelligence, wallet, real-time ops)
- Testimonials section (3 cards)
- Pricing section (Starter, Growth, Enterprise)
- FAQ accordion
- Footer with product links and contact

Hero style:
- Full-width farm background image with gradient overlay for readability.
- Left side content, right side simulated phone/call UI mock card.

### 5) Farmer Dashboard Details (`/farmer`)

Primary goal: farmer can quickly view wallet, weather, soil, orders, and trigger call.

Sections:
- Welcome hero with farmer name and "AI Active" status chip
- Quick identity form (name, phone, city) and "Open Dashboard" button
- Four key cards: wallet, soil type, weather, profile
- Primary action panel: "Call AI Assistant" and "Refresh"
- Recent orders section with rich cards
- AI activity panel with live statuses
- Mini market panel (Onion, Tomato, Potato)

Interactions:
- Loading state while calling assistant (rotating messages)
- Success and failure toasts
- Real-time updates from event stream (visual pulse when new order arrives)
- Wallet value should animate on change

### 6) Admin Dashboard Details (`/dashboard`)

Primary goal: operations monitoring.

Layout:
- Left sidebar with sections: Overview, Calls, Orders, Market, Farmers, System
- Top bar with search, notifications, profile
- Overview row with KPI cards:
	- Active calls
	- Orders today
	- Sell actions today
	- Avg response time
- Live activity feed panel (auto-updating)
- Market table with crop, mandi, price, trend, source freshness
- Recent transactions panel
- System health panel (Twilio, AI, Market Scraper)

Include chart widgets:
- Orders per hour (line chart)
- Crop-wise order volume (bar chart)
- Mandi price trend preview (sparkline)

### 7) Sign In Page (`/signin`)

- Split layout: left branding panel, right form panel
- Fields: phone/email, password or OTP option
- Role switch (Farmer/Admin)
- "Continue" primary CTA
- Minimal but premium visual style

### 8) Mobile Responsiveness Rules

- Must be mobile-first and polished on 360px width.
- No horizontal scrolling.
- Collapse nav to drawer.
- Dashboard cards become 1-column on mobile.
- Keep call CTA always easy to reach.

### 9) Accessibility and UX Requirements

- WCAG AA color contrast.
- Keyboard focus states visible.
- Semantic landmarks (`header`, `main`, `nav`, `section`, `footer`).
- Buttons and inputs with clear labels.
- Motion should respect reduced-motion preference.

### 10) Animation and Motion System

Use purposeful, premium motion:
- Staggered reveal on page load (not excessive)
- Card hover lift with soft shadow shift
- Button press feedback
- Live status pulse for active systems
- Smooth transitions for numbers and list updates

Avoid gimmicky animations.

### 11) Image and Media Direction

Use high-quality, realistic agriculture images, not cartoon illustrations.

Image usage requirements:
- Hero background: Indian farmer in green field using smartphone.
- Farmer dashboard ambient background: subtle farm landscape texture.
- Testimonials avatars: authentic rural/field portraits.
- Keep image overlays subtle so text remains readable.

If image generation is needed, use prompts like:
- "Photorealistic Indian farmer in lush green field at golden hour, using smartphone, cinematic but natural lighting, high detail, 16:9"
- "Agricultural produce market in India, clean composition, natural colors, documentary style, high resolution"

### 12) Design Tokens (Create Variables)

Create tokenized design system variables:
- Colors (primary, secondary, surface, text, success, warning, error)
- Spacing scale (4, 8, 12, 16, 24, 32, 48, 64)
- Radius scale (12, 16, 24, 32)
- Shadow levels (sm, md, lg)
- Typography scale (xs through display)
- Motion durations and easing tokens

### 13) Frontend Tech Output Requirements

Generate complete frontend code files, organized and ready to run.

Output format must include:
- `index.html` (Landing)
- `farmer_dashboard.html`
- `dashboard.html`
- `signin.html`
- `404.html`
- `styles/theme.css`
- `styles/components.css`
- `scripts/main.js`
- `scripts/farmer.js`
- `scripts/dashboard.js`

Use:
- Tailwind-compatible structure or clean utility-first CSS style
- Plain HTML/CSS/JS output unless framework explicitly selected
- Reusable component patterns across files

### 14) Data Binding Placeholders (Frontend Only)

Prepare clean placeholders for these API integrations:
- `POST /api/farmer/session`
- `GET /api/farmer/{phone}`
- `POST /api/farmer/{phone}/call`
- `GET /api/market-prices`
- `GET /api/activity-feed`
- `GET /api/activity-stream`

Do not mock fake business logic deeply; keep hooks ready for real backend APIs.

### 15) Microcopy and Tone

Tone:
- Helpful
- Clear
- Respectful for farmers
- Confident and concise

Example copy style:
- "AI assistant is active and ready to call"
- "Wallet updated after confirmed order"
- "Live mandi prices synced"

### 16) Quality Bar (Non-Negotiable)

Final output must feel:
- Premium SaaS (not template)
- Consistent across all pages
- Real product-ready
- Visually rich but performant
- Strong on desktop and mobile

Return full code for all files with clear file-by-file output.

---

## Optional Add-On Prompt (Use if first result looks generic)

Refine the generated UI to be more bold and premium:
- Increase hierarchy contrast in typography.
- Improve whitespace rhythm and card composition.
- Add subtle atmospheric gradients and depth layers.
- Upgrade KPI cards with better iconography and data emphasis.
- Improve dashboard information density without clutter.
- Ensure the result looks custom-designed, not boilerplate.

