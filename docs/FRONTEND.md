# Frontend design system: "Overprint"

LostLink's interface is Django templates, four stylesheets and two scripts.
There is no build step and no CSS or JavaScript framework. Every page works
without JavaScript; the scripts add live search, previews, in-place updates
and motion.

## Concept

The product's one job is to put a lost report next to a found report and say
how alike they are. The identity is built from that act:

- Every report is a disc of ink: **signal orange** for lost (something is
  missing) and **clear cyan** for found (it's safe).
- A match prints one disc over the other. Where they overlap, orange over cyan
  mixes into **deep green**, the colour of "this is it". The width of the
  overlap is the real similarity score: at 87 the lens is 87% of a disc's width.
- The discs use real multiply blending, in both the day and night schemes, so
  the green is the actual mix of the two inks.

The same rule is used everywhere, so a person learns it once:

| Where | What the overlap shows |
| --- | --- |
| Landing hero | The strongest real match on campus, large, with both photos |
| Home | The person's own strongest match, then every likely match as a row |
| Match card (item page, landing) | The pair with both reports either side |
| Why this score | Six small overlaps, one per signal, each overlapping by that signal's similarity, with its weight and points |
| Signals that couldn't be compared | Two dashed discs that don't touch |
| Empty states, errors | Two discs that haven't met |
| Brand, favicon, sign-in panel, closing banner | Two overlapping discs |

## Palette

| Name | Hex | Role |
| --- | --- | --- |
| Paper | `#FFFFFF` | Page |
| Mist | `#F2F5F3` | Quiet surfaces: panels, fields, match cards |
| Ink | `#10241B` | Text, primary actions |
| Lost orange | `#FF7A1A` | Lost reports, "I lost something" (ink text) |
| Found cyan | `#19B5D6` | Found reports, "I found something" (ink text) |
| Match green | `#195615` | Orange × cyan: matches, the main action in a view, focus rings |

Orange and cyan are too light for white text, so buttons in those inks use ink
text (above 6:1). Smaller coloured text uses darker variants (`--lost-text`
`#B54A00`, `--found-text` `#00728A`) to stay above 4.5:1. The score inside the
green lens is white (8.8:1). Report type is never shown by colour alone: a disc
is always paired with the word Lost or Found, tiers carry a shape (triangle,
diamond, ring) and statuses carry words.

The night scheme (deep green-black page, brighter inks, light green for actions)
applies only when chosen from the account menu. The inks still multiply there,
so the lens stays a readable green (`#3C6C37`, 6.2:1 with white).

## Type

- **Unbounded** for headings and numbers: a wide, round display face whose
  circular bowls echo the discs. Set tight, with negative tracking.
- **Hanken Grotesk** for everything people read: calm, open, 17px body text
  with 1.6 line height and lines under about 70 characters.

There are no all-caps labels, no eyebrows above headings and no single accented
words in headlines; size and the two faces carry the hierarchy.

## Layout

- A light, translucent masthead (blurred) with the current page marked by a
  pill and a green dot. Phones get a floating tab bar with the report action
  in the middle.
- Radius follows hierarchy: 28px for panels, 18px for cards and photos, 14px
  for controls, 8px for small labels, round for buttons, chips and discs.
- Content is grouped by tone, not borders: match cards, fact lists, filters
  and form sections sit on mist panels; lists are rows separated by hairlines.

## Motion

Motion explains something or answers a person:

- **Overlaps print into place.** When a match comes into view its discs slide
  from touching to their real overlap while the score counts up, so the number
  is seen being made of the overlap (`@property --k`, `app.js`).
- **Shared photo transition.** An item card's photo and the item page's photo
  share a `view-transition-name`, so opening a report morphs the photo into
  place (cross-document view transitions, current Chrome).
- **Search results** arrive with a short stagger after each search.
- **Responses:** buttons compress on press, menus, tabs and disclosures open
  with a short rise, toasts and the confirmation dialog arrive with a slight
  spring, the report-type choice cards grow their disc when chosen.
- With `prefers-reduced-motion` everything resolves instantly and overlaps
  are shown at their final position.

## Files

| File | Purpose |
| --- | --- |
| `static/css/tokens.css` | Inks, semantic colours (day default, opt-in night), type, spacing, geometry, motion. |
| `static/css/base.css` | Reset, type roles, layout primitives, utilities, keyframes, view transitions, reduced motion. |
| `static/css/components.css` | The overlap and signals, buttons, fields, choice cards, segmented controls, markers, badges, photos, item cards, match cards and rows, routes, rows, facts, notices, menus, dialog, toasts, empty states, pager, tables, chips, tabs, dropzone, Q&A, charts. |
| `static/css/layout.css` | Brand, masthead, tab bar, page heads, and each page's composition. |
| `templates/partials/overlap.html` | The overlap diagram: two items, a score, a size, optional photos and reveal. |
| `templates/partials/match_card.html`, `pair.html` | Full and compact matches built on the overlap. |
| `static/js/app.js` | Theme, menus, toasts, confirmation dialog, submit states, password tools, dropzone and live report preview, tabs, the overlap reveal and count-up, mark-read and unread polling. |
| `static/js/search.js` | Debounced AJAX search, skeletons, filter chips, URL state, staggered results. |

## Real data only

Every number and every overlap comes from the database or the matching engine.
The landing page shows the strongest current match between two public reports
with photos; without one it shows two discs that haven't met and says so.
