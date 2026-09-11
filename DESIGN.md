# Design System: High-End Editorial for Modern Agriculture

## 1. Overview & Creative North Star: "The Digital Agronomist"
This design system moves away from the "industrial" feel of traditional ag-tech. Our Creative North Star is **The Digital Agronomist**—a synthesis of high-end editorial sophistication and organic warmth. 

The aesthetic rejects the rigid, boxy layouts of standard SaaS. Instead, we embrace **Soft Minimalism**: a high-contrast typography scale, intentional asymmetry, and "breathable" layouts. We treat the interface not as a dashboard, but as a premium digital journal. By layering translucent surfaces and utilizing expansive white space, we create a sense of "funded startup" prestige—where technology feels invisible, and the focus remains on the soil and the data.

---

## 2. Colors & Surface Philosophy
The palette is rooted in the earth but refined through a glass lens. We avoid harsh blacks and stark whites in favor of organic, muted tones that feel high-end.

### The Palette
*   **Primary (Deep Green - #006a39):** Used for authority and high-impact CTAs.
*   **Secondary (Sage - #3c6842):** Used for supportive UI elements and organic accents.
*   **Tertiary (Earth Brown - #72554b):** Our grounding element, used for highlights that require a human, tactile feel.
*   **Background (Soft Earth - #faf9f7):** A warm, off-white base that prevents eye strain and feels more premium than pure white.

### The "No-Line" Rule
**Explicit Instruction:** Designers are prohibited from using 1px solid borders for sectioning. Sectioning must be achieved through:
1.  **Tonal Shifts:** Placing a `surface-container-low` section against a `surface` background.
2.  **Negative Space:** Using the `12` (4rem) or `16` (5.5rem) spacing tokens to create mental boundaries.

### Glass & Gradient Soul
To ensure the UI doesn't feel "flat," use **Glassmorphism** for floating navigation and overlays. Apply `surface-container-lowest` at 70% opacity with a `24px` backdrop blur. Use subtle linear gradients (e.g., `primary` to `primary-container`) on hero buttons to give them a "jewel" quality that flat colors lack.

---

## 3. Typography: Editorial Authority
We use a dual-font approach to balance tech precision with editorial elegance.

*   **Display & Headlines (Manrope):** A modern geometric sans-serif with a high x-height. Use `display-lg` (3.5rem) with tight letter-spacing (-0.02em) for hero statements to create an "Apple-esque" authority.
*   **Body & Labels (Inter):** The industry standard for legibility. We use `body-lg` (1rem) as the default to maintain an airy, premium feel. 
*   **Hierarchy Note:** Always maintain a significant contrast between headlines and body text. If a headline is `headline-lg`, the sub-text should skip a level to `body-md` to emphasize the editorial scale.

---

## 4. Elevation & Depth: Tonal Layering
In this system, depth is biological, not mechanical. We do not "box" items; we "nest" them.

*   **The Layering Principle:** Stacking determines importance.
    *   *Level 0:* `surface` (The canvas)
    *   *Level 1:* `surface-container-low` (Content groupings)
    *   *Level 2:* `surface-container-lowest` (Interactive cards/modals)
*   **Ambient Shadows:** For floating elements, use a "Sunlight Shadow." Set the blur to `48px`, the spread to `0`, and the color to `on-surface` at 4% opacity. It should feel like a soft cloud casting a shadow on a field, not a digital drop shadow.
*   **The Ghost Border Fallback:** If a layout requires a boundary for accessibility (e.g., in high-glare outdoor use), use the `outline-variant` token at **15% opacity**. It must be a suggestion of a line, not a hard stop.

---

## 5. Components & Interface Elements

### Buttons: The Tactile Touch
*   **Primary:** High-pill shape (`full` rounding). Use `primary` background with a subtle inner-glow (top-down gradient). 
*   **Secondary:** Glass-style. Background: `surface-container-lowest` at 50% opacity. 
*   **Interaction:** On hover, buttons should scale to 102% with a `200ms` ease-out spring.

### Cards: The Floating Canvas
*   **Rule:** Forbid the use of divider lines within cards.
*   **Style:** Use `xl` (3rem) corner radius for large layout sections and `lg` (2rem) for standard cards. 
*   **Separation:** Use `spacing-6` (2rem) as the standard internal padding to ensure data "breathes."

### Agricultural Data Chips
*   **Visuals:** Use `secondary-container` backgrounds with `on-secondary-container` text. These should feel organic, like small pebbles. Rounding is always `full`.

### Input Fields: Minimalist Inputs
*   **Style:** No bottom lines or full boxes. Use a `surface-container-high` background with `none` border. Focus state triggers a subtle `outline-variant` "Ghost Border" at 20% opacity.

### Custom Component: The "Growth Index" Progress Bar
*   Instead of a flat bar, use a tapered gradient line from `tertiary-fixed` to `primary`. It visualizes the transition from earth/seed to lush growth.

---

## 6. Do’s and Don’ts

### Do:
*   **Do** use large-scale photography of Indian landscapes, but treat them with a subtle `surface-tint` overlay to integrate them into the UI.
*   **Do** use asymmetrical layouts (e.g., a large headline on the left, a floating glass card offset to the right).
*   **Do** utilize the `24` (8.5rem) spacing token for top-level section margins to create an "expensive" feel.

### Don’t:
*   **Don’t** use pure black (#000000) for text. Always use `on-surface` (#1a1c1b).
*   **Don’t** use standard 4px or 8px rounded corners. This system starts at `16px` (`DEFAULT`) and lives at `24px+` (`lg`).
*   **Don’t** use "Alert Red" for warnings unless absolutely critical. Try using `tertiary` (Earth Brown) for cautionary states to keep the palette harmonious.