# Design System Specification: The Living Observatory

## 1. Overview & Creative North Star
The Creative North Star for this design system is **"The Living Observatory."** 

We are moving away from the "utilitarian agricultural tool" and toward a "high-performance flight deck." This system treats agricultural data not as static rows and columns, but as a living, breathing ecosystem. By borrowing the precision of a Tesla dashboard and the tactile luxury of Apple’s glassmorphism, we create an authoritative environment for high-stakes decision-making. 

To break the "template" look, we embrace **Intentional Asymmetry**. Large-scale data visualizations should bleed off the edges of containers, and typography scales must be dramatic—pairing massive, thin display headers with hyper-legible, dense functional labels. We do not use grids to box things in; we use them to anchor elements in a vast, dark ethereal space.

---

## 2. Colors & Surface Philosophy

### The "No-Line" Rule
Standard 1px borders are strictly prohibited for sectioning. They create visual noise and "trap" the data. Instead, boundaries are defined by **Tonal Transitions**. A section is differentiated from the background by shifting from `surface` (#09160e) to `surface_container_low` (#111e16). If a separation is required, use a 24px to 40px vertical gap rather than a line.

### Surface Hierarchy & Nesting
The UI is a series of stacked, semi-transparent layers. 
- **Base Layer:** `surface` (#09160e) – The infinite field.
- **Section Layer:** `surface_container` (#15221a) – Large logic blocks.
- **Card Layer:** `surface_container_high` (#202d24) – Interactive elements.
- **Floating Layer:** `surface_container_highest` (#2a382f) – Modals and popovers.

### The "Glass & Gradient" Rule
To achieve the "Ultra-Premium" feel, use **Backdrop Blur (20px - 40px)** on all floating cards. 
- **Signature Texture:** Primary CTAs should not be flat. Apply a subtle linear gradient from `primary_container` (#1a6b3c) to `primary` (#89d89e) at a 135° angle. This adds a "lithium-ion" glow characteristic of high-end fintech dashboards.

---

## 3. Typography
We utilize a dual-font strategy to balance editorial authority with technical precision.

*   **Display & Headlines (Manrope):** Used for "The Observatory" feel. Wide apertures and geometric shapes feel modern and architectural. 
    *   *Scale:* Use `display-lg` (3.5rem) for hero metrics (e.g., Yield Forecast) to command attention.
*   **Functional & Body (Inter):** Used for high-density data and OS-level interactions. Inter’s tall x-height ensures readability against dark, blurred backgrounds.
    *   *Hierarchy:* `label-md` and `label-sm` are the workhorses of this system. In high-density dashboards, use `label-md` in All Caps with +5% letter spacing for a "technical instrument" aesthetic.

---

## 4. Elevation & Depth

### The Layering Principle
Depth is achieved through "Tonal Stacking." To elevate a component, do not simply add a shadow; move it one step up the surface-container scale. A `surface_container_lowest` card placed on a `surface_container_low` background creates a natural, recessed "well" effect, perfect for data input fields.

### Ambient Shadows
Shadows must be felt, not seen.
- **Value:** `0px 24px 48px rgba(0, 0, 0, 0.4)`. 
- **Tinting:** Never use pure black shadows on the emerald background. Mix 10% of the `primary_container` color into the shadow to simulate ambient light bouncing off a dark forest floor.

### The "Ghost Border" Fallback
If contrast is required for accessibility (e.g., input states), use a **Ghost Border**: `outline_variant` (#404940) at **15% opacity**. It should look like a faint reflection on the edge of a glass pane, not a drawn line.

---

## 5. Components

### Glassmorphism Cards
- **Backing:** `surface_container_low` at 70% opacity.
- **Effect:** `backdrop-filter: blur(24px)`.
- **Edge:** A top-down inner highlight (1px) using `outline_variant` at 20% to simulate light hitting the top edge of the glass.

### High-Density Data Viz
- **Sparklines:** Use `primary` (#89d89e) with a subtle glow (drop-shadow) of the same color.
- **Urgency Markers:** Use `secondary` (#e9c349) for alerts. This Gold color is reserved exclusively for high-priority status badges to ensure it never loses its "Urgency" meaning.

### Buttons & Inputs
- **Primary Button:** Gradient fill (`primary_container` to `primary`). Corner radius: `md` (0.375rem) for a precision-tool feel.
- **Input Fields:** `surface_container_lowest` background. No border. On focus, a subtle outer glow of `primary` at 10% opacity.
- **Chips:** Forbid the use of heavy backgrounds. Use a "Ghost Border" and `label-sm` text.

### The "Sleek Sidebar"
The sidebar should feel like a docked instrument panel. Use `surface_container_low` with a 1px "Ghost Border" only on the right edge. Active states should be indicated by a `primary` glow emanating from the left edge, rather than a solid background block.

---

## 6. Do’s and Don’ts

### Do:
- **Use Vertical Rhythm:** Use the `10` (2.5rem) and `12` (3rem) spacing tokens to let data "breathe." Premium design is defined by the space you *don't* use.
- **Embrace the Dark:** Ensure the `background` (#09160e) remains the dominant color. It represents the "soil" of the OS.
- **Layering:** Place charts inside `surface_container_high` cards to make them feel like floating glass slides over a satellite map.

### Don't:
- **Don't use Dividers:** Never use a horizontal rule `<hr>` to separate list items. Use a `1.5` (0.375rem) or `2` (0.5rem) spacing gap or a slight background shift.
- **Don't use Pure White for Body:** Use `on_surface_variant` (#bfc9be) for long-form text to prevent eye strain against the dark background. Reserve `on_surface` (#d7e7d9) for headings.
- **Don't over-round:** Avoid the `full` radius for buttons. Stick to `md` (0.375rem) to maintain an authoritative, professional "OS" character. Save `full` radius strictly for status tags and chips.