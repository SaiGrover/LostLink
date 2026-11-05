"""Browser sweep against a running, seeded local LostLink server.

Signs in as each demo role, opens every main route at phone, tablet and
desktop widths in light and dark themes, and fails on HTTP errors, horizontal
overflow, uncaught JavaScript errors, or a page without exactly one <h1>.
Screenshots of key pages are written to ``tmp/browser-check/``.

    python manage.py seed_demo --fresh && python manage.py rebuild_matches
    python manage.py runserver 127.0.0.1:8010
    python scripts/browser_check.py --base http://127.0.0.1:8010

Apart from signing in, it only reads pages (GET); it never submits other forms.
"""
import argparse
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path('tmp/browser-check')
WIDTHS = (390, 768, 1440)
SHOTS = {'/dashboard/', '/search/', '/report/', '/notifications/', '/manage/moderation/', '/manage/analytics/'}
ROLES = [('student', 'saanvi', 'student123'), ('moderator', 'moderator', 'mod12345'), ('admin', 'admin', 'admin123')]


def discover(page, base):
    """Find a real item, claim and found item to visit."""
    html = page.request.get(base + '/search/api/?type=found').json()['html']
    found = re.findall(r'href="(/item/\d+/)"', html)
    claims = re.findall(r'href="(/claims/\d+/)"', page.request.get(base + '/claims/mine/').text())
    mine = re.findall(r'href="(/item/\d+/)"', page.request.get(base + '/mine/').text())
    routes = list(dict.fromkeys(found[:1] + mine[:1] + claims[:1]))
    if found:
        routes.append('/claims/start/' + found[0].split('/')[2] + '/')
    return routes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:8000')
    base = parser.parse_args().base.rstrip('/')
    ROOT.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        # Public pages
        for scheme in ('light', 'dark'):
            for width in WIDTHS:
                ctx = browser.new_context(viewport={'width': width, 'height': 900}, color_scheme=scheme, reduced_motion='reduce')
                page = ctx.new_page()
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                for route in ('/', '/accounts/login/', '/accounts/register/', '/search/', '/no-such-page/'):
                    response = page.goto(base + route)
                    results.append(check(page, 'public', scheme, width, route, response, errors))
                    if route == '/' and width != 768:
                        page.screenshot(path=str(ROOT / f'public-{scheme}-{width}-home.png'), full_page=True)
                ctx.close()
        for role, user, password in ROLES:
            for scheme in ('light', 'dark'):
                ctx = browser.new_context(viewport={'width': 1440, 'height': 900}, color_scheme=scheme, reduced_motion='reduce')
                page = ctx.new_page()
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(base + '/accounts/login/')
                page.fill('[name=username]', user)
                page.fill('[name=password]', password)
                page.click('button[type=submit]')
                page.wait_for_url('**/dashboard/')
                routes = ['/dashboard/', '/search/', '/search/?q=wallet', '/search/?study=manual', '/lost/', '/found/',
                          '/report/', '/mine/', '/claims/mine/', '/notifications/', '/accounts/profile/']
                routes += discover(page, base)
                if role in ('moderator', 'admin'):
                    routes += ['/manage/moderation/', '/manage/moderation/history/']
                if role == 'admin':
                    routes += ['/manage/analytics/', '/manage/users/', '/manage/taxonomy/']
                for width in WIDTHS:
                    page.set_viewport_size({'width': width, 'height': 900})
                    for route in routes:
                        response = page.goto(base + route)
                        results.append(check(page, role, scheme, width, route, response, errors))
                        if route in SHOTS and scheme == 'light' or (route in SHOTS and width == 1440):
                            name = f'{role}-{scheme}-{width}-{route.strip("/").replace("/", "-") or "home"}.png'
                            page.screenshot(path=str(ROOT / name), full_page=True)
                ctx.close()
        browser.close()
    (ROOT / 'results.json').write_text(json.dumps(results, indent=2))
    failed = [r for r in results if r['problems']]
    print(json.dumps({'checked': len(results), 'failures': failed[:40], 'failure_count': len(failed)}, indent=2))
    if failed:
        raise SystemExit(1)


def check(page, role, scheme, width, route, response, errors):
    problems = []
    expected = 404 if route == '/no-such-page/' else 200
    if response.status != expected:
        problems.append(f'status {response.status}')
    if page.evaluate('document.documentElement.scrollWidth > innerWidth + 1'):
        culprits = page.evaluate('''() => [...document.querySelectorAll('body *')]
            .filter(e => e.getBoundingClientRect().right > innerWidth + 1)
            .slice(0, 5).map(e => e.tagName + '.' + String(e.className).slice(0, 40))''')
        problems.append(f'horizontal overflow: {culprits}')
    h1 = page.locator('h1').count()
    if h1 != 1:
        problems.append(f'{h1} h1 elements')
    if errors:
        problems.append(f'js errors: {errors[:3]}')
        errors.clear()
    return {'role': role, 'scheme': scheme, 'width': width, 'route': route, 'status': response.status, 'problems': problems}


if __name__ == '__main__':
    main()
