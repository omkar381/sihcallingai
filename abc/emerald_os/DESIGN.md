# Design System Strategy: The Agrarian Intelligence OS

## 1. Overview & Creative North Star: "The Digital Soil"
This design system moves away from "app-like" interfaces toward a high-end, automotive-grade OS experience. Our Creative North Star is **"The Digital Soil"**—a concept where data isn't just displayed; it is cultivated. 

By merging the minimalist restraint of Apple with the high-performance telemetry of Tesla, we create a signature visual identity. We break the "template" look through **Atmospheric Depth**: using starfield grid dots and emerald glows to create a sense of infinite space, while high-contrast Manrope display type provides an editorial, authoritative voice. The interface should feel like a premium command center for a modern estate.

---

## 2. Colors & Atmospheric Tones
The palette is rooted in a "Deep Earth" philosophy. We use dark, desaturated greens to create a canvas where information can breathe.

### The "No-Line" Rule
**Explicit Instruction:** Do not use 1px solid borders to define sections. All separation must be achieved through background shifts (e.g., a `surface-container-low` card resting on a `surface` background) or subtle tonal transitions. Lines are for data visualization, not layout containment.

### Surface Hierarchy & Nesting
Treat the UI as a series of physical layers. 
- **Base Layer:** `surface` (#09160e) for the global background.
- **Secondary Layer:** `surface-container-low` (#111e16) for main sidebars.
- **Action Layer:** `surface-container-high` (#202d24) for active chat bubbles or focused cards.
- **Glass Layer:** Use `surface-variant` with a `backdrop-filter: blur(20px)` at 60% opacity for floating navigation or top bars.

### The "Emerald Glow" (Signature Texture)
Main CTAs and critical data points should use a gradient from `primary` (#4edea3) to `primary-container` (#10b981). To add "soul," apply a low-opacity radial gradient of `primary` behind key AI responses to mimic a bioluminescent glow.

---

## 3. Typography: Editorial Authority
The interplay between Manrope and Inter creates a balance between human warmth and machine precision.

*   **Display & Headlines (Manrope):** Use `display-lg` and `headline-md` for high-impact agricultural data (e.g., "Yield Forecast"). Manrope’s geometric nature feels engineered and premium.
*   **UI & Body (Inter):** Use `body-md` for chat messages and `label-sm` for technical metadata. Inter’s legibility ensures that complex farming data is digestible at a glance.
*   **Hierarchy Tip:** Never center-align body text. Use intentional asymmetry—keep text left-aligned to the "starfield grid" to maintain a technical, "OS" aesthetic.

---

## 4. Elevation & Depth: Tonal Layering
Traditional drop shadows are forbidden. We use **Ambient Occlusion** to define space.

*   **The Layering Principle:** Depth is achieved by "stacking." A `surface-container-highest` card placed on a `surface-dim` background creates a natural lift.
*   **Ambient Shadows:** If a card must "float," use a shadow with a 40px blur, 0% spread, and an opacity of 6% using the `on-surface` color. This mimics natural light rather than a digital effect.
*   **The Ghost Border Fallback:** If a boundary is strictly required for accessibility, use the `outline-variant` token (#3c4a42) at **15% opacity**. This creates a "suggestion" of an edge.
*   **Glassmorphism:** All modal overlays must use `surface-container-lowest` with a 70% opacity and a `24px` backdrop blur. This allows the "starfield grid" of the background to bleed through, maintaining a sense of place.

---

## 5. Components: The OS Primitives

### Buttons
*   **Primary:** `primary` background, `on-primary` text. No border. Corner radius: `md` (1.5rem).
*   **Secondary (Glass):** `surface-variant` at 40% opacity with blur. Text: `primary`.
*   **State:** On hover, increase the "Emerald Glow" via a `box-shadow: 0 0 20px #10b98133`.

### Input Fields (The Chat Bar)
*   **Style:** A wide, floating glass pill. 
*   **Background:** `surface-container-highest` at 80% opacity. 
*   **Typography:** `body-lg`. 
*   **Visual:** Forbid the standard "box" look. The input should span the width of the container with `xl` (3rem) rounded corners.

### Sleek Data Cards
*   **Rule:** No dividers. Separate header, body, and footer using the Spacing Scale (e.g., `8` (2.75rem) between sections).
*   **Background:** `surface-container-low`.
*   **Detail:** Add a `px` (1px) "top-light" highlight using `outline-variant` at 20% opacity on the top edge only to simulate a chamfered glass edge.

### AI "Saarthi" Chips
*   **Style:** Selection chips using `tertiary-container`. 
*   **Interaction:** On selection, transition to `primary` with a subtle `primary-fixed` glow.

---

## 6. Do’s and Don’ts

### Do:
*   **Use Asymmetry:** Place a high-density data card next to a large, breathing display headline.
*   **Embrace the Dark:** Keep the majority of the screen in the `background` (#09160e) to make the Emerald accents feel "ultra-premium."
*   **Use the Grid:** Align all elements to the "starfield dot grid" (spaced at `spacing-4` intervals).

### Don't:
*   **Don't use pure white:** All "white" text must be `on-surface` (#d7e7d9). Pure white (#FFFFFF) is too harsh for this high-end OS style.
*   **Don't use 100% opaque borders:** They break the "Glass & Soil" immersion.
*   **Don't crowd the interface:** If a screen feels "busy," increase the vertical spacing by two steps on the scale (e.g., move from `8` to `12`).