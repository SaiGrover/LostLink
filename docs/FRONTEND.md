# Frontend design system: "Tag & Thread"

LostLink's interface is Django templates, four stylesheets and two scripts.
There is no build step and no CSS or JavaScript framework. Every page works
without JavaScript; scripts add live search, motion and feedback.

## Creative concept

The product reconnects people with belongings, so the interface is built from
the physical objects of a lost-property office:

| Idea | Component | Where |
| --- | --- | --- |
| A report is a **luggage tag** | `.tag`: die-cut corners, a punched eyelet (a real CSS mask hole), a perforated tear line in the report-type colour, a stamped serial and a rubber-stamp LOST/FOUND | the wall, desk, My tags, landing |
| A match is a **thread** | `.board` evidence board: six threads fan out from one tag, pass through their signal label and rejoin the other tag. Thickness = that signal's real points | item page, desk, landing |
| A decision is a **stamp** | `.stamp` (and `--xl`), rotated, animated in with `stamp` | statuses, tiers, claim headers, confirm dialog |
| A case is a **route** | `.route`: stops on a thread, with done / current / blocked states | claim page, cases list, desk |
| Facts are a **ledger** | `.ledger`: mono keys, dotted leaders, values | item page, profile, almanac |
| A photo is a **specimen** | `.specimen`: a mounted print with tape and a stamped type | item page |

Typography carries the voice: **Instrument Serif** for headlines and quotes
(with italic emphasis), **Bricolage Grotesque** for the interface, and its
condensed width axis (`'wdth' 75`) for numbers and stamps. **Geist Mono** is
used for serials, marginalia and machine facts.

Navigation is a slim top masthead on desktop (Desk · The wall · My tags ·
Cases · Staff menu) and a bottom tab bar with a raised "new tag" button on
phones. Each role's main screen has its own composition rather than a shared
dashboard template:

- **Student desk:** a greeting, a one-sentence brief with linked numbers, a tally, a rail of match pairs, one full evidence board, cases with route bars, an editorial ledger of activity, and a rail of your tags.
- **Review desk:** a vertical queue switcher and inspection cards with approve or reject stamps.
- **Almanac:** a data-journalism layout with large condensed figures and annotated charts.

## Files

| File | Purpose |
| --- | --- |
| `static/css/tokens.css` | Palette slots, semantic colours (paper default, opt-in dark), type, spacing, geometry, motion, textures (grain, dots, hatch). |
| `static/css/base.css` | Reset, typography roles, layout primitives, motion keyframes, view transitions, reduced-motion rules. |
| `static/css/components.css` | Buttons, fields, stamps, tags, sheets, ledger, route, evidence board, pairs, notes, chips, segmented controls, stamp choice, dropzone, tables, lists, figures and charts, menus, dialog, toasts, empty states, skeletons, pager, transcript, dispatches. |
| `static/css/layout.css` | Masthead, tab bar, page heads, and each page's composition (desk, wall, case file, report form, claim, review desk, almanac, landing story, auth, errors). |
| `static/js/app.js` | Theme, menus, toasts, stamped confirmation dialog, submit states, password tools, dropzone and live tag preview, tabs, in-view choreography (reveals, thread drawing, count-ups), the landing scroll story, hero parallax, AJAX mark-read and unread polling. |
| `static/js/search.js` | Debounced AJAX search, skeleton tags, filter chips, category chips, URL state, staggered wall entry. |

## Applying the final palette

No palette has been chosen. Components only read semantic tokens. Every
semantic token is derived in OKLCH from these slots at the top of
`tokens.css`:

```css
--paper-h --paper-c                  surfaces (paper, card, kraft)
--ink-h --ink-c                      text and rules
--thread-h --thread-c --thread-l     the connecting accent
--lost-h --found-h                   report-type hues
--ok-h --warn-h --bad-h --info-h     status hues
--status-c                           saturation of type and status colours
```

Paper is the default theme. Dark mode exists only when a person chooses it;
the operating-system preference is deliberately not followed. Status is never
shown by colour alone: stamps carry words, tiers carry a glyph (triangle,
diamond, ring), and report types carry an icon.

## Motion

- **Entrance:** headline lines wipe up from a mask; content rises in a stagger.
- **Tags:** tags swing around their eyelet on hover; hero and preview tags sway on their strings; wall results drop in after each search.
- **Threads:** threads draw when a board scrolls into view; hovering a signal thickens its thread and dims the others.
- **Stamps:** decision stamps land with a short overshoot.
- **Landing:** the story chapters drive a sticky scene (lost, found, tied, questioned, returned).
- **Pages:** cross-document view transitions fade between pages where the browser supports them.
- **Reduced motion:** with `prefers-reduced-motion`, all of this resolves instantly and content is shown at rest.

## Real data only

Every number on screen comes from the database or the matching engine. The
landing page shows the strongest current match between two public reports
with photos, and the ticker shows live counts. Without such a match, the
landing page says so instead of inventing one.
