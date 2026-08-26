# Sage — Design Specification

> Complete design system for implementing the Sage web UI.
> Every value below is extracted from the approved mockups. Use these exact tokens — do not invent new colors, sizes, or patterns.

---

## 1. Design Philosophy

Sage is a **dark, warm-toned learning assistant**. The aesthetic is quiet and focused — a study environment, not a dashboard. The palette is Rosé Pine (dark), the typography pairs a serif display face with a clean sans body, and interactions are calm with purposeful motion.

**Core principles:**
- Dark canvas, warm neutrals — no cold grays, no pure black
- Serif for headings and display text, sans for body, mono for code and labels
- One accent color (iris/purple) — used sparingly for primary actions and focus states
- Semantic colors for status: teal = success, pink = error, gold = warning
- Generous whitespace, restrained decoration
- Every interactive element has visible focus, hover, and active states

---

## 2. Color Palette (Rosé Pine Dark)

Use these exact hex values. Map them to CSS custom properties.

| Token | Hex | Role |
|---|---|---|
| `--bg` | `#1a1a19` | Page background, main canvas |
| `--surface` | `#2f2f2c` | Card backgrounds, input fields |
| `--surface-raised` | `#3a3a37` | Hovered cards, elevated surfaces |
| `--surface-sunken` | `#141413` | Inset areas (diagram containers) |
| `--surface-warm` | `#404039` | Warm hover states, active items |
| `--overlay` | `#383835` | Subtle backgrounds (badges, toolbar buttons) |
| `--border` | `#444440` | Standard borders, dividers |
| `--border-soft` | `#3a3a37` | Subtle borders, section separators |
| `--fg` | `#f0efe8` | Primary text (warm off-white) |
| `--fg-2` | `#dbd9d0` | Secondary text |
| `--muted` | `#a8a69e` | Muted text, placeholders |
| `--meta` | `#7a7872` | Tertiary text, labels, metadata |
| `--accent` | `#c4a7e7` | Primary accent (iris/purple) — CTAs, focus rings, links |
| `--accent-dim` | `rgba(196,167,231,0.12)` | Accent background tint |
| `--accent-hover` | `#d0b8f0` | Accent hover state |
| `--foam` | `#9ccfd8` | Success, ready status, "done" indicators |
| `--love` | `#eb6f92` | Error, danger, delete actions |
| `--gold` | `#f6c177` | Warning, "plan" phase |
| `--pine` | `#31748f` | Info, secondary accent |
| `--rose` | `#ebbcba` | Highlight, "todo" artifact accent |

**Usage rules:**
- Background is always `--bg` — never pure `#000`
- Text is always `--fg` — never pure `#fff`
- Accent appears at most 2× per screen (primary CTA + one focus element)
- Semantic colors (foam/love/gold) only for status meaning, never decorative
- All borders use `--border` or `--border-soft` — no other border colors

---

## 3. Typography

### Font Stacks

| Role | Font | Fallback | Usage |
|---|---|---|---|
| Display | Newsreader | Georgia, serif | Headings, hero text, artifact titles, session titles |
| Body | Inter | system-ui, sans-serif | UI text, navigation, descriptions, labels |
| Mono | JetBrains Mono | monospace | Code, inline code, metadata, badges, API status text |

Load from Google Fonts:
```
Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;1,6..72,400
Inter:wght@400;500;600
JetBrains Mono:wght@400;500
```

### Type Scale

| Token | Size | Usage |
|---|---|---|
| Hero heading | 32px | Centered hero h1 |
| Section title | 24px | Library/Sessions page titles |
| Card title | 18px | Session card titles |
| Sub-heading | 16px | Artifact card titles, hero subtitle |
| Body | 15px | Base font size, message text |
| Body small | 14px | File table cells, descriptions, todo items |
| Label | 13px | Sidebar items, quiz options, composer toolbar buttons |
| Caption | 12px | Metadata, phase badges, session meta, delete buttons |
| Overline | 11px | Message role labels, artifact markers, status text |
| Micro | 10px | Artifact badges, API status text, sidebar badges |

### Weights

| Weight | Usage |
|---|---|
| 400 | Body text, descriptions |
| 500 | Headings (all serif), buttons, badges, labels, sidebar items |
| 600 | Bold emphasis in messages (`<strong>`), table headers |

### Line Heights

| Context | Line Height |
|---|---|
| Body text | 1.6 |
| Message bubbles | 1.7 |
| Headings | 1.2 |
| Labels/badges | 1.0 |

### Letter Spacing

| Context | Spacing |
|---|---|
| Hero heading | -0.02em |
| Logo | -0.02em |
| Uppercase labels | 0.04–0.06em |
| Mono metadata | 0.04em |
| Artifact markers | 0.1em |

---

## 4. Spacing System

Base unit: **4px**. All spacing is a multiple of 4.

| Token | Value | Usage |
|---|---|---|
| `--space-1` | 4px | Tight gaps (inline elements) |
| `--space-2` | 8px | Small gaps (list items, checkbox gaps) |
| `--space-3` | 12px | Medium gaps (card internal, sidebar padding) |
| `--space-4` | 16px | Standard padding (page gutters, card padding) |
| `--space-5` | 20px | Generous padding (session cards, message bubbles) |
| `--space-6` | 24px | Section padding, list gaps |
| `--space-8` | 32px | Large section spacing, library page padding |
| `--space-10` | 40px | Upload zone padding |
| `--space-12` | 48px | Extra large spacing |

**Rules:**
- Page horizontal padding: `--space-4` (16px) on mobile, `--space-8` (32px) on desktop
- Card internal padding: `--space-4` (16px) to `--space-5` (20px)
- Gap between cards in a list: `--space-3` (12px)
- Message spacing: `--space-5` (20px) vertical, `--space-4` (16px) horizontal

---

## 5. Border Radius

| Token | Value | Usage |
|---|---|---|
| `--r` | 8px | Universal radius — buttons, cards, inputs, sidebar items, badges, code blocks |

Everything uses the same 8px radius. No sharp corners, no pill shapes (except the artifact badge which uses `999px`).

---

## 6. Layout Structure

### App Shell

```
┌──────────────────────────────────────┐
│ Topbar (48px, no border)             │
├──────┬───────────────────┬───────────┤
│      │                   │           │
│ Side │    Main Content   │ Diagram   │
│ bar  │                   │  Panel    │
│260px │    (flex: 1)      │  480px    │
│      │                   │           │
└──────┴───────────────────┴───────────┘
```

### Key Dimensions

| Element | Size |
|---|---|
| Topbar height | 48px |
| Sidebar width | 260px |
| Diagram panel width | `min(480px, 42vw)`, min 320px |
| Chat max-width | 760px (centered) |
| Content max-width | 1000px (library, sessions) |

### Sidebar Behavior

- **Desktop (≥1024px):** Sidebar is in the flex layout. When collapsed, it uses `position: absolute; width: 0` so the main content fills full width.
- **Mobile (<1024px):** Sidebar overlays with `position: absolute` + backdrop. Collapsed state is identical.
- Transition: `transform 500ms ease, opacity 500ms ease`
- Collapsed: `transform: translateX(-100%); opacity: 0; visibility: hidden`

### Diagram Panel Behavior

- Sits to the right of the main content
- When closed: `transform: translateX(100%); pointer-events: none`
- Transition: `transform 500ms ease`
- On mobile: overlays full-width with max-width 480px

---

## 7. Component Specifications

### 7.1 Topbar

```
Height: 48px
Padding: 0 16px
Background: transparent
Border: none
Layout: flex, align-center, gap 12px
Z-index: 20
```

Contains:
- **Hamburger button** (34×34px icon button)
- **Logo** — "Sage" in Newsreader 22px weight 500, with the "a" in muted color

### 7.2 Sidebar

```
Width: 260px
Background: --bg
Border-right: 1px solid --border (when visible)
Overflow-y: auto
```

**Sidebar sections:**
- Border-bottom: 1px solid --border between sections
- Header: 12px uppercase, --meta color, letter-spacing 0.05em
- Header padding: 12px 16px
- Chevron: 14×14px, rotates 180deg when section is open

**Sidebar items:**
- Padding: 7px 12px
- Border-radius: 8px
- Font: 13px Inter
- Color: --muted
- Hover/active: background --overlay, color --fg
- Focus: 2px solid --accent, offset -2px

**Sidebar item badges:**
- 10px, weight 500, letter-spacing 0.03em
- Padding: 2px 7px, radius 8px
- Background: --overlay, color: --meta
- Phase colors: teach = --accent, plan = --gold, complete = --foam

**Status dots:**
- 6×6px circle
- Default: --meta
- Ready: --foam
- Error: --love

### 7.3 Chat Messages

**Message container:**
- Flex column, gap 8px
- Padding: 24px 16px
- Max-width: 760px, centered

**Message:**
- Padding: 20px 0
- Animation: cascade in from bottom (10px translateY, 700ms ease)
- Stagger: 250ms between messages

**Role label:**
- 11px, weight 500, uppercase, letter-spacing 0.04em
- Color: --meta
- Margin-bottom: 8px
- Padding: 0 16px

**Bubble:**
- Padding: 16px 20px
- Border-radius: 8px
- Line-height: 1.7

**User bubble:**
- Background: --surface-raised
- Color: --fg
- Aligned: flex-end (right-aligned)

**Assistant bubble:**
- Background: transparent
- Color: --fg
- Aligned: flex-start (left-aligned)

**Inline formatting:**
- `<strong>`: weight 600, color --fg
- `<em>`: color --muted, italic
- `<code>`: JetBrains Mono 13px, background --overlay, padding 2px 6px, radius 8px

### 7.4 Citations

```
Display: inline-flex, align-center, gap 3px
Font: 11px, weight 500
Padding: 2px 8px
Border-radius: 8px
Background: --overlay
Color: --accent
Cursor: pointer
Hover: background --surface-warm, color --accent-hover
Focus: 2px solid --accent, offset 1px
```

### 7.5 Input Bar

**Container:**
- Padding: 12px 16px 20px

**Composer toolbar (above input):**
- Flex, gap 4px, padding: 0 12px 8px
- Buttons: 11px JetBrains Mono, uppercase, weight 500, letter-spacing 0.04em
- Button padding: 6px 10px, radius 6px
- Button hover: color --fg, background --overlay
- Spacer pushes status text to the right
- Status text: 10px JetBrains Mono, --meta

**Input wrapper:**
- Background: --surface
- Border: 1px solid --border
- Border-radius: 8px
- Padding: 12px 16px
- Focus: border --accent, background --surface-raised, box-shadow 0 0 0 3px rgba(196,167,231,0.15)

**Textarea:**
- Font: 15px JetBrains Mono
- Line-height: 1.5
- Min-height: 22px, max-height: 120px
- No border, transparent background
- Placeholder color: --meta

**Send button:**
- 32×32px, radius 8px
- Background: --accent
- Color: --bg
- Hover: --accent-hover + box-shadow 0 0 0 3px rgba(196,167,231,0.2)
- Active: scale(0.9)
- Focus: 2px solid --accent, offset 2px

**Stop button:**
- Same size as send
- Background: --love
- Color: #fff
- Hidden by default, shown during streaming

### 7.6 Artifact Cards

**Card:**
- Background: --bg
- Border: 1px solid --border
- Border-left: 3px solid (color varies by type)
- Border-radius: 8px
- Padding: 16px
- Gap: 12px (flex column)

**Left border colors by type:**
| Kind | Color |
|---|---|
| Default | --accent |
| Mermaid/diagram | --foam |
| Quiz | --gold |
| Todo | --rose |

**Header:**
- Flex, space-between, gap 12px, wrap

**Marker:**
- 10px JetBrains Mono, weight 500, uppercase, letter-spacing 0.1em, --meta

**Title:**
- 16px Newsreader, italic, weight 500, --fg, line-height 1.3

**Badge:**
- 10px JetBrains Mono, weight 500, uppercase, letter-spacing 0.06em
- Padding: 2px 8px, radius 999px (pill)
- Background: --accent-dim, color: --accent
- Has a 5px foam dot before the text via `::before`

### 7.7 Todo Artifact

**Todo item:**
- Flex, align-start, gap 12px
- Padding: 8px 0
- Font: 14px, line-height 1.5
- Background: none, border: none
- Hover: color --fg
- Focus: 2px solid --accent, offset 1px

**Checkbox:**
- 18×18px, radius 4px
- Border: 1.5px solid --meta
- Checked: background --foam, border --foam
- Checkmark: 5×9px white border trick (CSS border rotate 45deg)

**Checked text:**
- Line-through, color --meta

### 7.8 Quiz Artifact

**Question:**
- Padding: 12px 0
- Border-top: 1px solid --border-soft (not on first)
- Text: 15px Newsreader, weight 500, --fg, line-height 1.4

**Options:**
- Flex column, gap 8px
- Each option: flex, align-center, gap 12px, padding 8px 12px, radius 8px
- Background: --overlay, border: 1px solid transparent
- Hover: background --surface-warm, color --fg
- Selected: border --accent, background --accent-dim
- Correct: background rgba(156,207,216,0.1), border --foam
- Incorrect: background rgba(235,111,146,0.1), border --love

**Radio indicator:**
- 14×14px circle
- Border: 1.5px solid --meta
- Selected: border --accent, background --accent
- Correct: border --foam, background --foam
- Incorrect: border --love, background --love

**Reveal button:**
- 11px JetBrains Mono, uppercase, weight 500
- Padding: 6px 12px, radius 5px
- Background: --overlay, color: --muted, border: 1px solid --border
- Hover: background --surface-warm, color --fg, border --border-soft
- Disabled after reveal

### 7.9 Buttons

**Primary button:**
- Padding: 8px 16px, radius 8px
- Font: 13px Inter, weight 500
- Background: --accent
- Color: --bg
- Hover: --accent-hover + box-shadow 0 0 0 3px rgba(196,167,231,0.2)
- Active: scale(0.96)
- Focus: 2px solid --accent, offset 2px

**Small variant (--sm):**
- Padding: 5px 12px, font: 12px

**Icon button:**
- 34×34px, radius 8px
- Color: --meta
- Hover: background --overlay, color --fg
- Focus: 2px solid --accent, offset 2px

**File action button:**
- 28×28px, radius 8px
- Color: --meta
- Hover: background --overlay, color --fg
- Danger variant hover: color --love

**Delete button (session cards):**
- 12px Inter, padding 4px 10px, radius 8px
- Background: --overlay, color: --meta
- Hover: color --love, background rgba(235,111,146,0.1)
- Focus: 2px solid --love, offset 1px

### 7.10 Session Cards

```
Background: --surface
Border: 1px solid --border
Border-radius: 8px
Padding: 20px
Margin-bottom: 12px
Cursor: pointer
Hover: border --border-soft, background --surface-raised, box-shadow 0 0 0 1px --border-soft
```

**Header:** flex, space-between, margin-bottom 8px
**Title:** 18px Newsreader, weight 500
**Phase badge:** 11px, uppercase, weight 500, letter-spacing 0.05em, padding 2px 8px, radius 8px
- Colors: teach = --accent, plan = --gold, complete = --foam
**Description:** 14px, --muted, line-height 1.6, margin-bottom 12px
**Meta:** flex, gap 16px, 12px, --meta

### 7.11 File Table

**Header:**
- 11px, weight 600, uppercase, letter-spacing 0.06em, --meta
- Border-bottom: 1px solid --border
- Padding: 12px 16px

**Cells:**
- 14px, padding 12px 16px
- Border-bottom: 1px solid --border
- Row hover: background --overlay

**Status badges:**
- Inline-flex, gap 5px, 12px, weight 500, padding 2px 8px, radius 8px
- Ready: background rgba(156,207,216,0.1), color --foam
- Error: background rgba(235,111,146,0.1), color --love
- Dot: 6×6px circle, same color as text

### 7.12 Upload Zone

```
Border: 2px dashed --border
Border-radius: 8px
Padding: 40px 16px
Text-align: center
Color: --meta
Cursor: pointer
Hover/dragover: border --accent, background --accent-dim
```

Text: 14px, hint: 12px --meta

### 7.13 Typing Indicator

```
80px wide, 4px tall bar
Radius: 2px
Background: linear-gradient(90deg, transparent, --accent, transparent)
Background-size: 200% 100%
Animation: gradient sweep 1.2s ease infinite
```

---

## 8. Animations

### Duration Tokens

| Token | Value | Usage |
|---|---|---|
| `--dur` | 180ms | Interactive transitions (hover, focus, color) |
| `--dur-slow` | 500ms | Layout transitions (sidebar slide, panel open) |

### Easing

```
--ease: cubic-bezier(0.33, 1, 0.68, 1)
```

### Message Cascade

```css
@keyframes msgCascade {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}
```
- Duration: 700ms
- Stagger: 250ms between messages (nth-child delays)

### Artifact Reveal

```css
@keyframes artifactReveal {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
```
- Duration: 500ms
- Stagger: 150ms between cards

### Mermaid Diagram Animation

1. Card slides in from right: `translateX(30px)` → `0`, 500ms
2. SVG lines draw in: stroke-dashoffset animates to 0, 600ms, staggered 150ms apart
3. Circles and text fade in: 400ms, staggered 150ms apart

### Quiz Typing Animation

1. Card reveals: translateY 8px → 0, 500ms
2. Question text types in: width 0 → 100%, 1s steps(30), with blinking cursor
3. Options cascade in: 300ms each, staggered 200ms apart
4. Correct answer glow: box-shadow 0 0 24px rgba(156,207,216,0.35) + color sweep

### Todo Flip Animation

1. Card reveals: 500ms
2. Each todo item flips in: `perspective(400px) rotateX(90deg)` → `0`, 500ms
3. Stagger: 120ms between items

### Reduced Motion

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
```

---

## 9. Focus & Accessibility

### Focus Ring (Global)

```css
:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  border-radius: var(--r);
}
:focus:not(:focus-visible) {
  outline: none;
}
```

### Per-Component Focus

| Component | Focus Style |
|---|---|
| Icon buttons | 2px solid --accent, offset 2px |
| Sidebar headers | 2px solid --accent, offset -2px |
| Sidebar items | 2px solid --accent, offset -2px |
| Composer toolbar buttons | 2px solid --accent, offset 1px |
| Primary buttons | 2px solid --accent, offset 2px |
| Send button | 2px solid --accent, offset 2px |
| File actions | 2px solid --accent, offset 1px |
| Quiz options | 2px solid --accent, offset 1px |
| Quiz reveal button | 2px solid --accent, offset 1px |
| Todo items | 2px solid --accent, offset 1px |
| Citations | 2px solid --accent, offset 1px |
| Delete buttons | 2px solid --love, offset 1px |

### ARIA Attributes

- Sidebar: `role="navigation"`, `aria-label="Sidebar"`
- Section toggles: `aria-expanded="true|false"`
- Chat messages: `role="log"`, `aria-live="polite"`
- Composer toolbar: `role="toolbar"`, `aria-label="Composer tools"`
- Textareas: `aria-label="Message input"`
- Diagram panel: `aria-label="Artifacts panel"`
- All buttons: `aria-label` when icon-only

### Keyboard Navigation

- Tab through all interactive elements
- Enter/Space activates buttons and sidebar items
- Escape closes sidebar (mobile)
- Textarea: Enter sends (without Shift), Shift+Enter for newline

---

## 10. Responsive Breakpoints

| Breakpoint | Width | Changes |
|---|---|---|
| Mobile | < 768px | Sidebar overlays, diagram panel overlays, library/sessions padding reduces to 16px |
| Desktop | ≥ 768px | Sidebar in layout, diagram panel in layout, full padding |

**Mobile sidebar:** `position: absolute; left: 0; top: 0; bottom: 0; z-index: 30`
**Mobile diagram:** `position: absolute; right: 0; top: 0; bottom: 0; z-index: 25; width: 100%; max-width: 480px`

---

## 11. Scrollbar

```css
::-webkit-scrollbar { width: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 8px; }
```

---

## 12. Page Structure Summary

| Page | Views | Key Components |
|---|---|---|
| Home | Hero + chat input | Centered hero with h1 + subtitle, single input bar |
| Workspace | Chat + diagram panel | Message list, citations, composer toolbar, artifact cards |
| Library | File management | Upload zone, file table with status badges, actions |
| Sessions | Session list | Session cards with phase badges, meta, delete |
| Settings | Configuration | (To be defined) |
| Health | System status | (To be defined) |

---

## 13. React Component Map

Map these mockup classes to React components:

```
<App>
  <Topbar>           — hamburger + logo
  <Layout>
    <Sidebar>        — navigation, sessions, plan, files sections
    <SidebarBackdrop> — mobile overlay
    <Main>
      <HomeView>     — hero + input
      <WorkspaceView> — chat + diagram panel
      <LibraryView>  — upload zone + file table
      <SessionsView> — session cards
    <DiagramPanel>   — artifact cards (mermaid, quiz, todo)
```

### Shared Components

```
<Button>            — variant: primary | icon | action | delete
<SidebarSection>    — collapsible section with header + list
<SidebarItem>       — nav item with optional badge/dot/meta
<StatusBadge>       — ready | error with dot indicator
<PhaseBadge>        — teach | plan | complete
<Citation>          — inline reference chip
<InputBar>          — textarea + send/stop + toolbar
<ArtifactCard>      — wrapper with kind-based left border
<TodoList>          — checklist with animated items
<QuizCard>          — question + options + reveal
<MermaidDiagram>    — rendered SVG with draw animation
<TypingIndicator>   — gradient sweep bar
<MessageBubble>     — user | assistant variant
<UploadZone>        — drag-and-drop area
<FileTable>         — sortable table with actions
<SessionCard>       — clickable card with meta
```

---

## 14. API Endpoints (for reference)

The FastAPI backend exposes these endpoints. The frontend should integrate with them:

```
GET  /api/files                          — list uploaded files
GET  /api/files/{id}/excerpts?chunk={id} — get chunk excerpts
POST /api/files/{id}/retry               — retry failed extraction
DELETE /api/files/{id}                   — delete file

POST /api/sessions                       — create session
GET  /api/sessions                       — list sessions
POST /api/sessions/{id}/turns            — send message (SSE stream)
POST /api/sessions/{id}/stop             — stop generation

GET  /api/sessions/{id}/outputs          — get artifacts
POST /api/sessions/{id}/outputs          — create artifact
```

Streaming uses Server-Sent Events (SSE). The frontend should consume the stream with `fetch` + `ReadableStream` or `EventSource`.

---

## 15. File Naming Convention

- Pages: `sage-{view}.html` (or React: `pages/{View}.tsx`)
- Components: `{ComponentName}.tsx`
- Styles: Tailwind utility classes (no separate CSS files needed)
- Design tokens: CSS custom properties in `:root` or Tailwind theme config

---

*This spec is the single source of truth for the Sage UI. When in doubt, refer to the mockup files in this directory for visual reference.*
