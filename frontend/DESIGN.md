---
name: Organic Intelligence
colors:
  surface: '#131410'
  surface-dim: '#131410'
  surface-bright: '#3a3935'
  surface-container-lowest: '#0e0e0b'
  surface-container-low: '#1c1c18'
  surface-container: '#20201c'
  surface-container-high: '#2a2a26'
  surface-container-highest: '#353531'
  on-surface: '#e5e2dc'
  on-surface-variant: '#cdc6b7'
  inverse-surface: '#e5e2dc'
  inverse-on-surface: '#31312d'
  outline: '#969082'
  outline-variant: '#4b463b'
  surface-tint: '#d6c68f'
  primary: '#e5d49b'
  on-primary: '#393006'
  primary-container: '#c8b882'
  on-primary-container: '#54481d'
  inverse-primary: '#6a5e30'
  secondary: '#c2c9b9'
  on-secondary: '#2c3227'
  secondary-container: '#42493d'
  on-secondary-container: '#b1b7a8'
  tertiary: '#e6d1b8'
  on-tertiary: '#3b2e1d'
  tertiary-container: '#c9b69e'
  on-tertiary-container: '#554734'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#f3e2a9'
  primary-fixed-dim: '#d6c68f'
  on-primary-fixed: '#221b00'
  on-primary-fixed-variant: '#51461b'
  secondary-fixed: '#dee5d4'
  secondary-fixed-dim: '#c2c9b9'
  on-secondary-fixed: '#171d13'
  on-secondary-fixed-variant: '#42493d'
  tertiary-fixed: '#f5dfc6'
  tertiary-fixed-dim: '#d8c3ab'
  on-tertiary-fixed: '#241a0a'
  on-tertiary-fixed-variant: '#524532'
  background: '#131410'
  on-background: '#e5e2dc'
  surface-variant: '#353531'
typography:
  display-lg:
    fontFamily: Noto Serif
    fontSize: 48px
    fontWeight: '400'
    lineHeight: '1.1'
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Noto Serif
    fontSize: 32px
    fontWeight: '400'
    lineHeight: '1.2'
  headline-sm:
    fontFamily: Noto Serif
    fontSize: 24px
    fontWeight: '400'
    lineHeight: '1.3'
  body-lg:
    fontFamily: Manrope
    fontSize: 18px
    fontWeight: '400'
    lineHeight: '1.6'
  body-md:
    fontFamily: Manrope
    fontSize: 16px
    fontWeight: '400'
    lineHeight: '1.6'
  label-caps:
    fontFamily: Manrope
    fontSize: 12px
    fontWeight: '600'
    lineHeight: '1.0'
    letterSpacing: 0.1em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  unit: 4px
  gutter: 24px
  margin: 40px
  container-max: 1280px
---

## Brand & Style
The brand personality is intellectual, steady, and exclusive—resembling a private heritage library integrated with cutting-edge technology. It targets high-net-worth individuals and serious analysts who value calm, focused environments over traditional "loud" fintech dashboards.

The design style combines **Glassmorphism** with **Minimalism**, layered over a **Tactile** foundation. It utilizes depth through transparency and frosted textures, while maintaining a rigorous editorial structure. The experience should evoke the feeling of reading a premium physical journal under soft lighting.

Key stylistic imperatives:
- **Subtle Grain:** Apply a fine monochromatic noise overlay (2-3% opacity) across the entire background to eliminate digital flatness.
- **Staggered Motion:** UI elements must utilize a "fade-up-and-in" entrance animation with a 20ms stagger between adjacent items and a custom cubic-bezier (0.22, 1, 0.36, 1) timing function.
- **Atmospheric Depth:** The background is not a flat color but a slow-moving radial mesh of deep forest and olive tones.

## Colors
The palette is rooted in nature and prestige. 

- **Primary (Gold):** Used sparingly for interactive highlights, active states, and critical data points.
- **Background (Forest/Olive):** The core canvas. Always use a gradient mesh to create a sense of three-dimensional space.
- **Surface (Cream):** Reserved exclusively for high-readability content like news feeds and long-form reports. It should feel like high-quality vellum paper.
- **Functional Accents:** Use reduced-opacity versions of the Gold (#c8b882) for dividers (15-20% opacity) to maintain a soft, non-obstructive hierarchy.

## Typography
The typographic system relies on the tension between the classic authority of **Noto Serif** (substituting for Cormorant Garamond) and the clinical precision of **Manrope** (substituting for DM Sans).

- **Headlines:** Use Noto Serif for all editorial titles, section headers, and significant financial figures. This grounds the app in a "news and analysis" context.
- **Body & UI:** Manrope is used for all functional text, data grids, and button labels to ensure maximum legibility at small sizes.
- **Letter Spacing:** Increase tracking for uppercase labels to enhance the premium, airy feel of the design system.

## Layout & Spacing
This design system uses a **Fixed Grid** philosophy for desktop and a fluid, generous margin approach for mobile. 

The spacing rhythm is built on an 8px base unit, but emphasizes large "breathing rooms" (40px+) between major content blocks. 
- **Content Blocks:** Use a 12-column grid with 24px gutters.
- **News Surfaces:** Inside cream-colored surfaces, padding should be increased (32px+) to mimic the margins of a printed broadsheet.
- **Alignment:** Headlines should be center-aligned for high-level landing views but strictly left-aligned for data-heavy analysis pages.

## Elevation & Depth
Depth is achieved through material simulation rather than traditional shadows.

1.  **Backdrop Blurs:** High-level floating panels (modals, dropdowns) use a `20px` to `40px` backdrop-filter blur with a white or gold tint at 5% opacity.
2.  **Inner Glows:** Instead of drop shadows, use 1px inner borders (strokes) in a lighter shade of the background or 10% Gold to define edges.
3.  **The News Surface:** The cream news section should appear as a physical sheet laid on top of the dark forest background, utilizing a very soft, large-radius (60px) ambient shadow with low opacity (15%).

## Shapes
The shape language is "Soft" (Level 1), favoring architectural precision over "bubbly" consumer aesthetics. 

- **Cards & Panels:** Use 4px to 8px corner radii. This maintains a structured, professional appearance.
- **Interactive Elements:** Buttons and input fields should match this 4px radius. 
- **Charts:** Line graphs should use "natural" smoothing (catmull-rom) rather than sharp angles to align with the organic brand theme.

## Components
- **Buttons:** Primary buttons are Gold (#c8b882) with dark forest text. Secondary buttons use a frosted glass effect with a thin gold border. Avoid heavy gradients on buttons; keep them flat and matte.
- **News Cards:** Use the Cream (#f7f4ee) surface. Typography within these cards switches to dark forest green for text to maintain high contrast.
- **Dividers:** Horizontal rules should be 1px thick, using the Gold accent at 20% opacity. For news sections, use a traditional "hairline" black stroke at 10% opacity.
- **Inputs:** Minimalist bottom-border only or very light frosted containers. Focus states are indicated by a subtle glow of the Gold accent.
- **AI Analyst Insights:** These components should be wrapped in a specific frosted glass container with a subtle "shimmer" animation to denote active AI processing or "living" data.
- **Data Visuals:** Use a palette of desaturated greens, golds, and muted oranges. Avoid standard bright "red/green" for stock movements; use the Gold accent for positive and a muted terracotta for negative.