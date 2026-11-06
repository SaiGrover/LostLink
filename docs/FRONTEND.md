# Frontend design system

LostLink's interface is Django templates, four stylesheets and two scripts.
There is no build step and no CSS or JavaScript framework. Every page works
without JavaScript; the scripts add live search, previews, in-place updates
and motion.

## Colour: Black + Vermilion

Vermilion is the signature colour, set against warm off-white in light mode and
layered charcoal in dark mode. It is kept for what matters most: the primary
action in a view, lost reports, active navigation, focus and match scores.
Everything else sits on warm neutrals. All colours live in
`static/css/tokens.css` as semantic variables; components never use raw hex.

| Token | Light | Dark |
| --- | --- | --- |
| Page | `#F5F3F0` | `#101010` |
| Cards (`--surface`) | `#FFFFFF` | `#1B1918` |
| Secondary surface (`--mist`) | `#EEEAE5` | `#211F1E` |
| Tertiary surface (`--mist-2`) | `#E4DED8` | `#302D2B` |
| Dropdowns, modals (`--elevated`) | `#FFFEFC` | `#262321` |
| Navigation | `#FFFFFF` | `#151413` |
| Text / secondary / muted | `#211F1E` / `#625D59` / `#716A64` | `#FAF8F5` / `#C9C2BC` / `#A69E97` |
| Borders / dividers / strong | `#E4DED8` / `#EEEAE5` / `#C9C2BC` | `#302D2B` / `#302D2B` / `#625D59` |
| Brand accent (marks, dots, focus) | `#D6533B` | `#F0785E` |
| Accent text | `#B83E2B` | `#FFA18A` |
| Accent soft / border | `#FFF1ED` / `#FFC0AE` | `#49251F` / `#71372D` |
| Primary button / hover / pressed | `#C94B34` / `#B83E2B` / `#913021` | `#F0785E` / `#F78369` / `#E65E43` |
| Secondary button | `#EEEAE5` | `#302D2B` |

Statuses always pair a small dot with a word:

| Status | Light (text / background / dot) | Dark |
| --- | --- | --- |
| Lost | `#B83E2B` / `#FFF1ED` / `#D6533B` | `#FFA18A` / `#49251F` / `#F0785E` |
| Found | `#625D59` / `#EEEAE5` / `#827B75` | `#D5CEC7` / `#302D2B` / `#A69E97` |
| Matched, returned, high tier | `#8A542A` / `#F6E8D9` / `#B97845` | `#E8B98B` / `#403023` / `#D49A65` |
| Pending, medium tier | `#77623D` / `#F5EEDC` / `#B58A42` | `#E3C88E` / `#393020` / `#D8B56C` |
| Rejected, low tier | `#625D59` / `#EEEAE5` / `#827B75` | `#C9C2BC` / `#302D2B` / `#A69E97` |

**Contrast adjustments.** Three values in the brief fall below WCAG AA (4.5:1)
for normal-size text, so the nearest passing shade is used for text only:
primary buttons use `#C94B34` (white text 4.6:1; `#D6533B` gives 4.1:1 and stays
the brand colour for marks, dots, rings and focus), muted text uses `#716A64`
(4.8:1 on the page; `#827B75` is kept for placeholders), and accent-coloured
text uses `#B83E2B` (5.0:1).

**Match scores.** Each match is drawn as two discs, the lost report in
vermilion and the found report in warm slate, overlapping by the real score;
the overlap mixes to a deep vermilion-brown with the score in white.

## Theme switching

People choose **Light** (the default), **Dark** or **System** from the account
menu or the profile page; public pages have a toggle that cycles through the
three and announces the current choice. The choice is stored in
`localStorage` (`lostlink-theme`). An inline script in `base.html` resolves it
to `data-theme="light"` or `"dark"` before the first paint, so the wrong theme
never flashes; in System mode the theme follows the device and updates live
when it changes. Switching fades colours for 200ms. Other tabs follow along.

## Type

- **Unbounded** for headings and numbers: a wide, round display face whose
  circular bowls echo the discs. Set tight, with negative tracking.
- **Hanken Grotesk** for everything people read: calm, open, 17px body text
  with 1.6 line height and lines under about 70 characters.

There are no all-caps labels, no eyebrows above headings and no single accented
words in headlines; size and the two faces carry the hierarchy.

## Layout

- A solid masthead (white, or charcoal at night) with the current page marked
  by a soft vermilion pill and dot. Phones get a floating tab bar with the report action
  in the middle.
- Radius follows hierarchy: 28px for panels, 18px for cards and photos, 14px
  for controls, 8px for small labels, round for buttons, chips and discs.
- Match cards, fact lists, filters and form sections are white cards with a
  subtle border on the off-white page (charcoal layers at night); lists are rows
  separated by hairlines.

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
| `static/css/tokens.css` | Vermilion scale, semantic colours for light and dark, status colours, type, spacing, geometry, motion. |
| `static/css/base.css` | Reset, type roles, layout primitives, utilities, keyframes, view transitions, reduced motion. |
| `static/css/components.css` | The overlap and signals, buttons, fields, choice cards, segmented controls, markers, badges, photos, item cards, match cards and rows, routes, rows, facts, notices, menus, dialog, toasts, empty states, pager, tables, chips, tabs, dropzone, Q&A, charts. |
| `static/css/layout.css` | Brand, masthead, tab bar, page heads, and each page's composition. |
| `templates/partials/overlap.html` | The overlap diagram: two items, a score, a size, optional photos and reveal. |
| `templates/partials/match_card.html`, `pair.html` | Full and compact matches built on the overlap. |
| `static/js/app.js` | Theme (light, dark, system), menus, toasts, confirmation dialog, submit states, password tools, dropzone and live report preview, tabs, the overlap reveal and count-up, mark-read and unread polling. |
| `static/js/search.js` | Debounced AJAX search, skeletons, filter chips, URL state, staggered results. |

## Landing page

The landing page (`templates/items/home.html`, `static/css/landing.css`,
`static/js/landing.js`) uses its own type pairing, **Urbanist** for display and
**Inter** for text, loaded only on that page through the `fonts` block. Colours
come from the shared tokens, so both themes work there too.

- **Hero:** a typed headline (vanilla `TypewriterHeading`: 35ms per character
  after 400ms; characters hold their space from the start, so nothing shifts),
  a short description, *Report a lost item* and *Browse found items*, and a
  live line of real counts from the view.
- **Orbit visualization:** four rings (353, 501, 649 and 797px, alternating
  direction, 30 to 60s) carrying illustrated belongings that stay upright. When
  a real strongest match exists, its two photos ride the orbits and every seven
  seconds a connection arcs between them around the hub with the actual score.
  It is drawn at 920px (the true bounds of the outer ring and its objects) and
  scaled per breakpoint, with the wrapper taking the scaled size.
- **Header:** underline-on-hover links, a pill button with a rotating
  conic-gradient border (`@property --border-angle`, masked to a ring) and a
  sliding fill, and an accessible phone menu (Escape closes it, focus moves in).
  Signed-in visitors would see Dashboard and My reports, but the home view
  sends them to their home page first.
- **Ticker:** an infinite strip of words and item icons, 20s per loop, with
  faded edges; it pauses on hover.
- **Sequence:** header, hero copy, visualization, items (0.6 to 2.3s), ticker,
  typed heading, then the actions and the floating pointer. With reduced motion
  the page is shown at rest and the connection is drawn statically.

## Static file versions

`{% asset 'css/tokens.css' %}` (in `items/templatetags/ui.py`) appends the
file's modification time while `DEBUG` is on, so a browser never keeps a stale
stylesheet or script after a change. In production the manifest storage
already hashes file names and the URL is returned unchanged.

## Real data only

Every number and every overlap comes from the database or the matching engine.
The landing page shows the strongest current match between two public reports
with photos; without one it shows two discs that haven't met and says so.
