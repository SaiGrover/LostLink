"""Measure that page content clears the fixed mobile tab bar.

For each phone width, signs in, opens long pages, scrolls to the very bottom
and checks that the last interactive control inside <main> ends above the
tab bar and is the element actually hit by a tap at its centre. Also fails on
horizontal overflow. Read-only apart from signing in.

    python manage.py runserver 127.0.0.1:8010
    python scripts/mobile_nav_check.py --base http://127.0.0.1:8010

``--simulate-old-cascade`` re-adds the pre-fix rule order (a later
``.app main`` base rule) to show the regression the fix removes.
"""
import argparse
import json
import re
import sys

from playwright.sync_api import sync_playwright

VIEWPORTS = [(320, 640), (360, 740), (375, 812), (414, 896), (767, 1024)]
ACCOUNTS = [('saanvi', 'student123'), ('moderator', 'mod12345')]
OLD_RULE = '.app main { padding-top: var(--s8); padding-bottom: var(--s10); overflow-x: clip; }'

# Runs with the page scrolled to the very bottom.
#   contentClearance: tab bar top minus the bottom of all page content (main > .wrap)
#   lastControl*: the lowest visible interactive control in <main>; if it is not
#   on screen at the end of the page it is scrolled into view (block 'end',
#   which honours scroll-padding, i.e. where keyboard focus would put it).
MEASURE = """() => {
  const bar = document.querySelector('.tabbar');
  const barRect = bar && getComputedStyle(bar).display !== 'none' ? bar.getBoundingClientRect() : null;
  const limit = barRect ? barRect.top : innerHeight;
  const wrapBottom = document.querySelector('main > .wrap').getBoundingClientRect().bottom;
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [tabindex]:not([tabindex="-1"])';
  const shown = el => {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height || getComputedStyle(el).visibility === 'hidden') return false;
    if (el.closest('[hidden], dialog:not([open])')) return false;
    const closed = el.closest('details:not([open])');
    return !closed || (el.matches('summary') && el.parentElement === closed && !closed.parentElement.closest('details:not([open])'));
  };
  let last = null, lastBottom = -Infinity;
  for (const el of document.querySelector('main').querySelectorAll(sel)) {
    if (!shown(el)) continue;
    const b = el.getBoundingClientRect().bottom + scrollY;
    if (b > lastBottom) { lastBottom = b; last = el; }
  }
  const mast = document.querySelector('.mast');
  const top = mast ? mast.getBoundingClientRect().bottom : 0;
  let r = last.getBoundingClientRect();
  if (r.top < top || r.bottom > limit) { last.scrollIntoView({block: 'end'}); r = last.getBoundingClientRect(); }
  // tap the middle of the part that is visible between the masthead and the tab bar
  const y = (Math.max(r.top, top) + Math.min(r.bottom, limit)) / 2;
  const hit = document.elementFromPoint(r.left + Math.min(r.width / 2, 40), y);
  return {
    tabbarTop: barRect ? Math.round(barRect.top) : null,
    contentClearance: Math.round(limit - wrapBottom),
    lastControl: (last.tagName + ' ' + (last.innerText || last.value || last.name || '').trim()).slice(0, 40),
    lastBottom: Math.round(r.bottom),
    clearance: Math.round(limit - r.bottom),
    tappable: !!hit && (hit === last || last.contains(hit) || hit.contains(last)),
    overflowX: document.documentElement.scrollWidth > innerWidth,
  };
}"""


def discover(page, base):
    html = page.request.get(base + '/search/api/?type=found').json()['html']
    ids = re.findall(r'href="(/item/\d+/)"', html)
    return ids[0] if ids else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:8010')
    parser.add_argument('--simulate-old-cascade', action='store_true')
    args = parser.parse_args()
    failures, rows = 0, []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for username, password in ACCOUNTS:
            for width, height in VIEWPORTS:
                ctx = browser.new_context(viewport={'width': width, 'height': height}, reduced_motion='reduce',
                                          is_mobile=True, has_touch=True)
                page = ctx.new_page()
                page.goto(args.base + '/accounts/login/')
                page.fill('[name=username]', username)
                page.fill('[name=password]', password)
                page.click('main button[type=submit]')
                page.wait_for_url('**/dashboard/')
                detail = discover(page, args.base)
                paths = ['/report/', detail, '/dashboard/', '/claims/mine/', '/notifications/', '/search/']
                if username == 'moderator':
                    paths += ['/manage/moderation/']
                for path in filter(None, paths):
                    page.goto(args.base + path)
                    if args.simulate_old_cascade:
                        page.add_style_tag(content=OLD_RULE)
                    page.evaluate('window.scrollTo(0, document.documentElement.scrollHeight)')
                    page.wait_for_timeout(150)
                    m = page.evaluate(MEASURE)
                    ok = (m['contentClearance'] >= 0 and m['clearance'] >= 0 and m['tappable']
                          and not m['overflowX'])
                    failures += not ok
                    rows.append({'user': username, 'viewport': f'{width}x{height}', 'path': path, 'ok': ok, **m})
                    print(f"{'PASS' if ok else 'FAIL'} {username:<9} {width}x{height:<5} {path:<20} "
                          f"tabbar_top={m['tabbarTop']} content_clear={m['contentClearance']} "
                          f"last_bottom={m['lastBottom']} last_clear={m['clearance']} "
                          f"tappable={m['tappable']} overflow={m['overflowX']} [{m['lastControl']}]")
                ctx.close()
        browser.close()
    print(json.dumps({'checked': len(rows), 'failures': failures}))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
