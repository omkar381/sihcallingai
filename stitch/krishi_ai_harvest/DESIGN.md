# Design System Specification: High-End Editorial Agri-Fintech

## 1. Overview & Creative North Star: "The Digital Agronomist"
This design system moves away from the rigid, sterile grids of traditional fintech to embrace an aesthetic we call **"The Digital Agronomist."** It is a philosophy that balances high-intelligence data with the organic, tactile warmth of the earth. 

The goal is to feel like a premium editorial magazine—spacious, authoritative, and human. We break the "SaaS template" look through **intentional asymmetry**, where large display typography sits offset from content blocks, and **overlapping glass layers** that mimic the depth of a physical landscape. We don't just present data; we curate an environment of trust and growth.

---

## 2. Colors & Surface Philosophy
The palette is rooted in deep forest greens and sun-bleached earth tones. We avoid pure blacks and harsh grays to maintain a "warm-blooded" digital experience.

### Color Tokens
*   **Primary (Growth):** `#004F29` (Deep, authoritative green)
*   **Secondary (Vitality):** `#64DD91` (Used for accents and success states)
*   **Tertiary (Earth):** `#583E35` (Grounded brown for stability)
*   **Background:** `#FAF9F7` (A warm, off-white "fine paper" base)

### The "No-Line" Rule
To achieve a premium feel, **1px solid borders are strictly prohibited for sectioning.** Boundaries must be defined through:
1.  **Background Color Shifts:** Use `surface-container-low` (`#F4F3F1`) sections sitting against the `background` (`#FAF9F7`).
2.  **Tonal Transitions:** Define areas using subtle 4px to 8px shifts in color rather than structural lines.

### Surface Hierarchy & Nesting
Treat the UI as a series of stacked sheets. 
*   **Lowest Level:** `surface-container-low` for large background regions.
*   **Mid Level:** `surface` (`#FAF9F7`) for standard content blocks.
*   **Highest Level:** `surface-container-lowest` (`#FFFFFF`) for interactive cards that need to "pop."

### The "Glass & Gradient" Rule
For Hero sections or high-impact data visualizations, use **Glassmorphism**. Apply a semi-transparent `surface` color with a `backdrop-filter: blur(20px)`. To provide "soul," apply a subtle linear gradient from `primary` (`#004F29`) to `primary-container` (`#006A39`) behind these glass elements to mimic the shifting light of a horizon.

---

## 3. Typography: Editorial Authority
We pair the geometric strength of **Manrope** with the high-utility legibility of **Inter**.

*   **Display & Headlines (Manrope):** Use **ExtraBold** for primary titles to command attention. The large x-height of Manrope provides an "intelligent" and modern feel. Use wide letter-spacing (-0.02em) for a tighter, premium look.
*   **Body & UI (Inter):** Reserved for data and long-form reading. Use **Medium** for labels to ensure they feel intentional, not secondary.
*   **The Scale:**
    *   `display-lg`: 3.5rem (Manrope ExtraBold) - For hero impact.
    *   `headline-md`: 1.75rem (Manrope Bold) - For section starts.
    *   `body-md`: 0.875rem (Inter Regular) - For standard readability.

---

## 4. Elevation & Depth: Tonal Layering
Traditional shadows are often too heavy. This system uses **Ambient Depth** to create a sophisticated, airy atmosphere.

*   **The Layering Principle:** Depth is achieved by stacking. A `surface-container-lowest` card placed on a `surface-container-low` background creates a natural lift without a single pixel of shadow.
*   **Ambient Shadows:** For floating elements (Modals/Active Cards), use a custom shadow: 
    *   `box-shadow: 0 12px 40px rgba(26, 28, 27, 0.04);`
    *   The shadow is tinted with the `on-surface` color (`#1A1C1B`) at 4% opacity to mimic natural light.
*   **The "Ghost Border" Fallback:** If a border is required for accessibility, use the `outline-variant` token at **15% opacity**. It should be felt, not seen.
*   **Roundedness Scale:**
    *   `xl` (24px - 32px): Use for main containers and hero cards.
    *   `lg` (16px): Use for standard interactive components (Buttons/Inputs).

---

## 5. Components & Primitive Styling

### Buttons: The Weighted Action
*   **Primary:** Solid `primary-container` (`#006A39`) with `on-primary` text. No border. 16px corner radius.
*   **Secondary:** `surface-container-highest` background. It feels like a subtle indentation in the page.
*   **Tertiary:** Text only in `primary`, using `title-sm` (Inter Semibold) for emphasis.

### Input Fields: The Quiet Collector
*   **Style:** Minimalist. No bottom line. Use `surface-container-highest` as a subtle block background.
*   **Focus State:** A 2px "Ghost Border" using `primary-fixed` at 50% opacity.
*   **Error State:** Use the `error` token (`#BA1A1A`) for text only; the background remains neutral to avoid visual "alarm."

### Cards & Lists: The Flow Principle
*   **Card Styling:** Forbid the use of divider lines. Separate content using `spacing-6` (1.5rem) or `spacing-8` (2rem) of vertical whitespace.
*   **Glass Hero Cards:** Use for AI-generated insights. Background: `rgba(255, 255, 255, 0.7)` with a 20px blur and a subtle `outline-variant` ghost border.

### New Component: The "Growth Metric" 
A specialized card for this system. A `display-sm` value sitting atop a `surface-variant` background, featuring a micro-sparkline in `secondary-fixed-dim` (`#64DD91`) that bleeds off the right edge, breaking the container's internal grid.

---

## 6. Do’s and Don’ts

### Do:
*   **Embrace Whitespace:** Use the `8px` grid religiously, but don't be afraid to leave large areas of `background` empty to let the typography breathe.
*   **Use Asymmetry:** Place high-level summaries on the left and detailed data visualizations on the right, slightly offset vertically.
*   **Tone-on-Tone:** Use `surface-container` shifts to group related items instead of boxes inside boxes.

### Don’t:
*   **Don't use 100% Black:** Never use `#000000`. Use `on-surface` (`#1A1C1B`) for text to keep the "organic" warmth.
*   **No Sharp Corners:** Avoid `0px` or `4px` radii. Everything in nature is rounded; our UI should be too.
*   **No Heavy Dividers:** If you feel the need to add a line, try adding `16px` of extra padding instead. Whitespace is the most premium divider.