# Spect8 dashboard UI design specification

## Authority and capture

- Visual authority:
  `https://ig-hedge-fund-dashboard.alonghuman.chatgpt.site`
- Inspected: 2026-07-30
- Desktop viewport: 1440 × 900
- Tablet viewport: 768 × 1024
- Mobile viewport: 390 × 844
- Typeface observed: Geist assets with an Inter/system fallback; tabular figures
  are enabled.

The live page was visually inspected at all three widths. The Phase 1
implementation must reproduce its shell, hierarchy, density, status language,
opportunity table, and event tape while replacing all mock content with the two
selected synthetic golden cases. No strategy values may be calculated in the
frontend.

## Captures

- `dashboard-full.png` — complete desktop dashboard.
- `opportunity-table.png` — desktop opportunity matrix.
- `filtered-signal-pipeline.png` — desktop Filter and Signal projection.
- `event-tape.png` — desktop recent-activity tape.
- `dashboard-tablet.png` — complete 768 px layout.
- `dashboard-mobile.png` — complete 390 px layout.

## Page hierarchy

```text
Application shell
├── Sidebar (desktop)
│   ├── Strategy Intelligence brand
│   ├── Primary navigation
│   └── Connection state
└── Workspace
    ├── Sticky top bar
    │   ├── Workspace label and Market Scanner title
    │   └── Connection state and Run Scan action
    └── Content
        ├── Four summary cards
        └── Dashboard grid
            ├── Signal Pipeline
            ├── Opportunity Matrix
            └── Right rail
                ├── Connection / system health
                └── Recent Activity event tape
```

Phase 1 retains this hierarchy but uses walking-skeleton metrics: monitored
strategy instances, filtered instances, confirmed signals, and feed health.

## Colour tokens

| Token | Value | Use |
|---|---:|---|
| Background | `#06101d` | Workspace background |
| Deep background | `#040b14` | App shell and deepest surfaces |
| Panel | `#0b1828` | Cards and panels |
| Elevated panel | `#0e1d2e` | Secondary panel surface |
| Hover panel | `#11243a` | Interactive hover |
| Border | `#1d3046` | Panel and control borders |
| Soft border | `#15273a` | Internal separators |
| Primary text | `#e8f0f8` | Headings and key figures |
| Muted text | `#8798ac` | Labels and secondary values |
| Dim text | `#5f7289` | Eyebrows, timestamps and tertiary copy |
| Cyan | `#19bfe5` | Active navigation, connection and WATCHING |
| Blue | `#4187ff` | Secondary metrics and emphasis |
| Green | `#27d292` | BUY, confirmed, healthy |
| Amber | `#efb533` | Filtered, warning, stale |
| Red | `#ff5c6c` | SELL and error/unavailable |

The background uses a subtle cyan radial glow over the deep background. Panels
use low-contrast navy gradients and restrained inner shadows.

## Typography

- UI family: Geist/Inter/system sans-serif.
- Numeric family: tabular sans figures; identifiers may use Geist Mono.
- Page title: 20 px desktop, 17 px at tablet/mobile, regular weight.
- Panel title: 14 px.
- KPI number: 29 px, bold, tight negative tracking.
- Table instrument: 12 px, bold.
- Table body: 9–10 px.
- Eyebrow/section kicker: 9 px, uppercase, 0.12 em tracking, bold.
- Auxiliary text and event metadata: 8–9 px.

## Spacing and dimensions

- Desktop sidebar: 174 px wide and sticky at full viewport height.
- Desktop top bar: 76 px high; 24–26 px horizontal padding.
- Main content: 20 px top, 22 px horizontal, 28 px bottom.
- Standard panel radius: 8 px.
- Control radius: 5–7 px.
- Panel gap: 12–13 px.
- Summary grid: four equal columns; cards are at least 104 px high.
- Dashboard grid: 204 px pipeline, flexible matrix (minimum 570 px), 250 px
  right rail.
- Panel heading: at least 62 px high with 15 px padding.
- Opportunity rows: 73 px desktop.
- Opportunity table minimum width: 620 px; horizontal overflow remains
  available on narrow screens.
- Status pill: at least 57 × 23 px.
- Direction icon: 19 × 19 px circle.

## Opportunity table

The approved mock-up columns are:

1. Instrument
2. TF
3. Filter state
4. Signal
5. Entry
6. Stop
7. Target
8. Score

For the frozen Phase 1 contract, the final column becomes `Risk / Contract` so
the UI can show `$100` and the provider contract status without inventing a
strategy score. Instrument rows also expose last update below their primary
identifier on compact layouts.

## Component language

### Header and navigation

- Cyan active nav accent with a 2 px inset left line.
- Read-only workspace eyebrow above the page title.
- Connection indicator is a 7 px green dot with a soft halo.
- Primary action is a 124 × 40 px cyan gradient button.

### Summary cards

- Dark navy gradient, 1 px border, 8 px radius.
- A 2 px semantic colour rule sits at the left edge.
- Circular 42 px icon, large metric, short label, and dim annotation.

### Signal Pipeline

- Three semantic stages: MARKET, FILTER, SIGNAL.
- Desktop uses descending trapezoids and vertical connectors.
- Tablet/mobile removes clipping and arranges equal rectangular stages in one
  row.

### Filter and signal styles

- `WATCHING`: cyan text and cyan-bordered navy pill.
- Filter matched: amber text and amber-bordered translucent pill.
- Qualified/confirmed Filter: green text and green-bordered translucent pill.
- BUY: green text with an upward circular icon.
- SELL: red text with a downward circular icon.
- No signal: muted text with a neutral circular dash.

### Event tape

- Panel title uses `LATEST CHANGES` eyebrow and `Recent Activity`.
- Rows contain semantic direction/event icon, instrument, event label, and UTC
  timestamp.
- Event history remains chronological; newest entries appear first in the
  compact tape.

## Runtime states

The live mock-up shows healthy, watching, filtered, BUY, and SELL states. The
following required states were not displayed by the mock-up and therefore use
the existing token system without altering the page hierarchy:

- Loading: preserve panel dimensions and show muted skeleton rows; do not show
  calculated placeholder values.
- Stale data: amber connection dot and `SYNTHETIC DATA STALE`; retain the last
  successful update visibly.
- Data unavailable/error: red connection dot, red bordered alert inside the
  affected panel, and no candidate levels.
- Authentication error: red inline message within the login panel; never reveal
  whether a password hash or deployment configuration exists.

## Responsive behaviour

- Above 1180 px: full 174 px sidebar and three-column dashboard grid.
- 821–1180 px: sidebar contracts to 72 px icons; dashboard uses pipeline plus
  matrix, with right-rail panels spanning a second row.
- 541–820 px: sidebar is hidden; content padding becomes 14 px; KPI cards use two
  columns; dashboard panels stack; pipeline stages become horizontal.
- 540 px and below: KPI cards use one column; header status detail and eyebrow
  are hidden; matrix controls stack; the table remains horizontally scrollable.

No mobile-only calculations or alternate data model is permitted. Responsive
views render the same API-provided status and event records.
