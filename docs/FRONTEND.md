# Frontend design system

LostLink's interface is plain Django templates, four stylesheets and two
scripts. There is no build step, CSS framework or JavaScript framework. Every
page works without JavaScript; scripts add live search, feedback and motion.

## Files

| File | Purpose |
| --- | --- |
| `static/css/tokens.css` | Palette slots, semantic colour tokens (light and dark), type scale, spacing, radii, motion and elevation. |
| `static/css/base.css` | Reset, typography, layout primitives (`stack`, `cluster`, `split`), motion keyframes, utilities. |
| `static/css/components.css` | Buttons, fields, segmented controls, choice tiles, dropzone, badges, chips, panels, stats, tables, lists, item cards, meters, score dials, contribution bars, tabs, menus, dialog, toasts, tooltip, callouts, empty and skeleton states, pagination, timeline, Q&A thread. |
| `static/css/layout.css` | App shell, page compositions (dashboard, match explainer, search, report form, item detail, claims, notifications, moderation, analytics, landing, auth, error pages). |
| `static/js/app.js` | Theme toggle, mobile drawer, menus, toasts, confirmation dialog, submit loading states, password reveal and caps-lock hint, live password guidance, inline validation, character counters, photo dropzone and preview, live report preview, tabs, scroll reveal, AJAX mark-read and unread badge polling. |
| `static/js/search.js` | Debounced AJAX search with skeleton loading, active-filter chips, category chips, URL state and history, retry on error. |
| `templates/partials/` | Icon sprite, brand mark, badges (type, status, tier), thumbnails, empty state, pagination and the match explainer. |
| `items/templatetags/ui.py` | `{% icon %}`, `report_id`, `claim_id`, `percent_of`, `times100`, `{% query_with %}`, `{% qs_without %}`. |

## Applying the final palette

No palette has been chosen. Every component reads **semantic** tokens
(`--surface`, `--text-secondary`, `--accent`, `--tier-high`, …), and every
semantic token is derived in OKLCH from a short list of **palette slots** at
the top of `tokens.css`:

```css
--neutral-h / --neutral-c            tint of greys, surfaces and text
--accent-h / --accent-c / --accent-l primary action colour
--success-h --warning-h --danger-h --info-h   functional status hues
--lost-h --found-h                   report-type hues
--status-c                           saturation of functional colours
```

The defaults are deliberately near-monochrome: ink-on-paper with muted status
colours. To apply a brand palette, change those numbers only. Dark mode
re-derives the same semantics from the same slots, both for an explicit
choice (`data-theme="dark"`) and for the operating-system preference. The two
dark blocks in `tokens.css` must stay identical.

Status is never colour-only: report type has an icon and label, confidence
tiers have a distinct glyph (triangle, diamond, ring) plus a label, and
claim statuses carry text.

## Typography

- Display: Bricolage Grotesque (optical sizes 12–96).
- Text: Geist.
- Data and references: Geist Mono (report IDs such as `LL-0042`, eyebrows).

The scale is fluid (`--text-xs` … `--text-4xl`). Numbers use tabular figures.

## Signature patterns

- **Match explainer** (`partials/match_card.html`): both reports, a score
  dial, a contribution bar where each segment is that signal's real share of
  the total, and a breakdown table (similarity, effective weight, points).
  Components with no evidence are shown hatched as “Not compared” with the
  reason. Every value comes from the stored `Match` row; nothing is
  hard-coded. Copy states that similarity is not proof of ownership.
- **Recovery timeline** (`Claim.timeline()`): submitted → questions →
  moderator review → approved and handover → returned, with rejected and
  disputed branches. Approval is never shown as “returned”.
- **Confirmation dialog**: any form with `data-confirm` opens a native
  `<dialog>` explaining the consequence; `data-confirm-note` adds a note field
  that is submitted with the form (used for moderator reasons).

## Motion

Short, purposeful transitions built on `--dur-*` and `--ease-*` tokens: a
staggered page entry, card lift on hover, dial and bar fills, menu and dialog
pop, toast spring, skeleton shimmer and scroll reveals on the landing page.
`prefers-reduced-motion` reduces all animation and transition durations to
effectively zero, and reveal content is shown immediately.

## Accessibility notes

- Skip link, landmark regions, one `h1` per page, labelled controls.
- Visible `:focus-visible` ring from `--border-focus`.
- Tabs use `role="tablist"` with arrow-key navigation; without JavaScript all
  panels are shown.
- Live search results announce counts through an `aria-live` region and set
  `aria-busy` while loading.
- Charts are CSS-drawn with an `aria-label` summary; the weekly trend has a
  “View as table” alternative.
- Tables collapse into labelled rows on narrow screens (`.table--stack`).
