# Design System — Sage Preview / Rosé Pine (dark)

A single source of truth for every visual surface the swarm produces. Agents generate a copy of this per project from the canonical theme files, snapshot it into the project, and reuse it instead of inventing colors, spacing, or type. Never hand-edit the canonical values; derive the project copy from `ui_themes/rose_pine/tokens.css`.

## Palette

| Token | Hex | Role |
|---|---|---|
| canvas | `#191724` | page background |
| surface | `#1f1d2e` | cards, panels |
| overlay | `#26233a` | borders, elevated surfaces |
| highlight-low | `#21202e` | subtle hover |
| highlight-med | `#403d52` | hover |
| highlight-high | `#524f67` | active |
| ink | `#e0def4` | primary text |
| ink-muted | `#908caa` | secondary text |
| ink-faint | `#6e6a86` | tertiary text, placeholders |
| iris | `#c4a7e7` | accent (primary actions) |
| foam | `#9ccfd8` | link, success |
| pine | `#31748f` | info |
| love | `#eb6f92` | danger, error |
| gold | `#f6c177` | warning |
| rose | `#ebbcba` | highlight |

Semantic aliases: accent=iris, accent-hover=`#cdb6f0`, accent-ink=`#191724`, link=foam, success=foam, warning=gold, danger=love, highlight=rose, border=overlay.

Chart series: `#9ccfd8`, `#c4a7e7`, `#ebbcba`, `#f6c177`, `#eb6f92`, `#31748f`, `#908caa`, `#6e6a86`.

Variants: Rosé Pine Moon (cooler) and Dawn (light) are one-file token swaps; Moon preferred when a cooler tint fits.

## Spacing (4px base grid)

4 · 8 · 12 · 16 · 24 · 32 · 48. Consistent gaps everywhere; prefer `gap` over margins.

## Typography

12 · 14 · 16 · 20 · 24 · 30 · 36. Body 16px at line-height 1.5; headings tighter at 1.2. Fonts: Geist Sans (UI text), Geist Mono (code, metrics, identifiers); fallbacks: JetBrains Mono + system stacks.

## Radii

6px cards/buttons · 8px cards/buttons · 12px panels · pill (999px) chips/badges.

## Shadows

- subtle: `0 1px 3px rgba(0,0,0,0.35), 0 1px 2px rgba(0,0,0,0.25)`
- elevated: `0 8px 24px rgba(0,0,0,0.4)`

Nothing heavier.

## Components

- Buttons: 10×16px padding, radius 8, hover (slight darken), active, 2px `:focus-visible` ring in accent. Primary=accent solid, secondary=neutral border, ghost=no border.
- Inputs/selects: label above, visible placeholder, 1px neutral border, focus ring matches buttons, disabled at 50% opacity.
- Cards: 1px neutral border + subtle shadow + 16–24px padding.
- Chips/badges: pill radius; semantic colors only (success/warning/danger) for meaning, never decoration.
- Status colors are semantic only: foam/success, gold/warning, love/error.

## Layout

- Page container: max-width 1100–1200px, centered, 24–32px horizontal padding (16px mobile).
- Flex/grid with `gap`; never absolute positioning or tables for layout.
- Responsive at 640 / 768 / 1024 / 1280; nothing overflows on mobile.
- Section whitespace 32–48px; group related content in cards.

## Page quality

- Realistic sample data — never lorem ipsum.
- Loading skeletons/spinners; empty states that explain what belongs there.
- Semantic HTML: `header`/`nav`/`main`/`section`, one `h1` per page, labels on inputs, alt text.
- WCAG AA contrast; minimum ~40px clickable target.
- Forbidden: rainbow gradients, emoji as icons, centered paragraphs, clipart, default browser chrome.

## Source of truth

This file is a snapshot. Canonical values live in `ui_themes/rose_pine/tokens.css` (CSS). When a project needs its own design system, copy this file and replace the header line with the project name — do not alter the tokens.
