```markdown
# Design System: The Digital Agronomist

## 1. Overview & Creative North Star
The Creative North Star for this design system is **"The Digital Agronomist."** This is not a generic storefront; it is a high-precision, editorial experience that mirrors the sophistication of modern agricultural science. We evoke the feeling of a high-end laboratory nestled within a dark, nocturnal forest.

To break the "template" look, we move away from rigid, centered grids. Instead, we utilize **intentional asymmetry**, overlapping translucent layers, and dramatic typographic scales. Content is treated like a curated exhibit—precise, authoritative, and premium. We favor breathing room (white space) and tonal depth over structural lines to define the user journey.

---

## 2. Colors & Surface Architecture
The palette is rooted in a deep, organic dark mode, punctuated by the vibrance of living chlorophyll and the prestige of harvest gold.

### Color Palette
*   **Deep Earth (Background):** `#09160e` (The foundation of the experience).
*   **Living Emerald (Primary):** `#4edea3` (Actionable energy) & `#10b981` (Container depth).
*   **Harvest Gold (Secondary):** `#e9c349` & `#D4AF37` (The mark of premium quality/accents).
*   **Neutral Tones:** `on-surface-variant` (`#bbcabf`) for secondary data to maintain a soft, low-fatigue reading environment.

### The "No-Line" Rule
**Explicit Instruction:** Prohibit 1px solid borders for sectioning. Boundaries must be defined solely through background color shifts. For example, a featured product section (`surface-container-low`) should sit directly on the `background` without a stroke. Use the `8.5rem` (24) or `7rem` (20) spacing tokens to create separation through "void" rather than lines.

### Surface Hierarchy & Nesting
Treat the UI as a series of physical layers—stacked sheets of frosted glass.
*   **Level 0 (Background):** `#09160e`.
*   **Level 1 (Sections):** `surface-container-low` (`#111e16`).
*   **Level 2 (Cards/Modules):** `surface-container` (`#15221a`).
*   **Level 3 (Interactive/Floating):** `surface-container-high` (`#202d24`) with glassmorphism.

### The "Glass & Gradient" Rule
To move beyond a "flat" digital look:
*   **Glassmorphism:** Use `surface-container-highest` at 60% opacity with a `20px` backdrop-blur for floating navigation or quick-view modals.
*   **Signature Gradients:** For Hero CTAs, use a linear gradient from `primary` (`#4edea3`) to `primary-container` (`#10b981`) at a 135-degree angle. This adds "soul" and mimics the shimmer of a healthy leaf.

---

## 3. Typography
We use a dual-typeface system to balance technical precision with editorial elegance.

*   **Headings (Manrope):** Chosen for its modern, geometric construction. Use `display-lg` (3.5rem) for hero statements with tight letter-spacing (-0.02em) to command authority.
*   **UI & Data (Inter):** Chosen for maximum legibility in technical specs. Use `label-md` for data points (e.g., pH levels, soil moisture) to ensure the "Digital Agronomist" aesthetic feels precise.
*   **Hierarchy Note:** Use **Harvest Gold** for `title-sm` or `label-md` sparingly to highlight "Premium" or "Certified" statuses, creating an immediate visual "seal of quality."

---

## 4. Elevation & Depth
In this system, depth is biological and atmospheric, not mechanical.

*   **The Layering Principle:** Stacking surface tiers creates a soft, natural lift. A `surface-container-lowest` card placed on a `surface-container-low` section creates a recessed, "etched" look into the earth.
*   **Ambient Shadows:** Use only when elements must float (e.g., a floating action button). Shadow color must be `#051109` (a tinted dark green) at 8% opacity with a `48px` blur. Avoid grey shadows at all costs.
*   **The "Ghost Border" Fallback:** If a container needs more definition against a complex background, use the `outline-variant` token at **15% opacity**. It should be felt, not seen.
*   **Edge Highlights:** Instead of a full border, use a 1px top-stroke on cards using `primary` at 10% opacity to simulate light hitting the edge of a glass pane.

---

## 5. Components

### Buttons
*   **Primary:** Gradient fill (`primary` to `primary-container`), `md` (0.375rem) corner radius. Typography: `title-sm` (Inter).
*   **Secondary:** Ghost style with a Harvest Gold (`secondary`) text and a 10% opacity Gold "Ghost Border."
*   **Tertiary:** Text only in `primary`, no background, with a subtle underline on hover.

### The "Agronomist" Card
*   **No Dividers:** Use `1.4rem` (4) of vertical padding to separate product titles from descriptions.
*   **Image Integration:** Product images should have no background (PNG) and appear to sit *on* the surface, with a very soft `primary` tinted glow behind them to suggest vitality.

### Selection Chips
*   **Filter Chips:** Use `surface-container-highest`. When active, transition to `primary` background with `on-primary` text. Use `full` (9999px) roundedness for an organic, seed-like shape.

### Input Fields
*   **Style:** Minimalist. Only a bottom-border (Ghost Border style). Focus state: The bottom border transitions to a `2px` `primary` solid line with a soft glow.

---

## 6. Do’s and Don’ts

### Do
*   **Do** use asymmetrical layouts (e.g., a large image on the left overlapping a text block that starts 1/3 into the screen).
*   **Do** use `display-lg` typography for single-word impact (e.g., "PRECISION").
*   **Do** favor the `2.75rem` (8) and `4rem` (12) spacing tokens to create a "gallery" feel.

### Don't
*   **Don't** use pure white (#FFFFFF). Use `on-surface` (#d7e7d9) to keep the dark-mode harmony.
*   **Don't** use standard "Add to Cart" icons. Use bespoke, thin-stroke icons that feel like architectural diagrams.
*   **Don't** use 1px solid borders to create grids. If you feel the need for a line, increase the spacing by two scale steps instead.
*   **Don't** use bright, generic "Sale" reds. Use Harvest Gold for all high-value callouts.

---

## 7. Spacing & Rhythm
Rhythm is achieved through a strict adherence to the spacing scale. For agricultural data tables, use `0.7rem` (2) for density. For editorial storytelling sections, jump to `5.5rem` (16) to allow the "Digital Agronomist" aesthetic to breathe and feel truly premium.```