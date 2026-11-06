"""Drive the complete recovery journey through the real UI.

Student A reports a found item, student B reports the matching lost item, a
moderator approves both, B sees the match and claims the item, A asks a
private question, B answers, the moderator approves and confirms the
handover. Every step goes through forms and confirmation dialogs in a real
browser. It WRITES to the database, so run it only against a local demo
database seeded with ``seed_demo``.

    python scripts/e2e_workflow.py --base http://127.0.0.1:8000
"""
import argparse
import io
import re
import time

from PIL import Image, ImageDraw
from playwright.sync_api import expect, sync_playwright

TAG = time.strftime('%H%M%S')


def photo():
    img = Image.new('RGB', (320, 240), (30, 60, 140))
    d = ImageDraw.Draw(img)
    d.rectangle([90, 60, 230, 180], fill=(230, 200, 40))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return {'name': 'umbrella.png', 'mimeType': 'image/png', 'buffer': buf.getvalue()}


def login(browser, base, user, password):
    page = browser.new_context(viewport={'width': 1280, 'height': 900}, reduced_motion='reduce').new_page()
    page.goto(base + '/accounts/login/')
    page.fill('[name=username]', user)
    page.fill('[name=password]', password)
    page.click('button[type=submit]')
    page.wait_for_url('**/dashboard/')
    return page


def report(page, base, rtype, title, description):
    page.goto(base + '/report/')
    page.locator('.choice label', has=page.locator(f'input[value={rtype}]')).click()
    expect(page.locator(f'input[name=report_type][value={rtype}]')).to_be_checked()
    page.fill('input[name=title]', title)
    page.select_option('[name=category]', label='Others')
    page.select_option('[name=location]', label='Cafeteria')
    page.fill('textarea[name=description]', description)
    page.fill('[name=color]', 'blue')
    page.set_input_files('input[type=file]', photo())
    expect(page.locator('[data-preview] [data-p=title]')).to_have_text(title)
    page.click('button[type=submit]')
    page.wait_for_url(re.compile(r'/item/\d+/$'))
    expect(page.locator('.notice--warn')).to_contain_text('Waiting for a moderator')
    return int(re.search(r'/item/(\d+)/', page.url).group(1))


def confirm(page, note=None):
    dialog = page.locator('#confirm-dialog')
    expect(dialog).to_be_visible()
    if note is not None:
        page.fill('#confirm-note', note)
    page.click('[data-confirm-ok]')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:8000')
    base = parser.parse_args().base.rstrip('/')
    steps = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        finder = login(browser, base, 'prankit', 'student123')
        owner = login(browser, base, 'aditya', 'student123')
        mod = login(browser, base, 'moderator', 'mod12345')

        found_id = report(finder, base, 'found', f'Blue umbrella {TAG}',
                          'Navy blue folding umbrella with a yellow square patch and a wooden handle')
        lost_id = report(owner, base, 'lost', f'Lost blue umbrella {TAG}',
                         'Blue folding umbrella, yellow square patch on the canopy, wooden handle')
        steps.append(f'reports created: found LL-{found_id:04d}, lost LL-{lost_id:04d} (pending)')

        for item_id in (found_id, lost_id):
            mod.goto(f'{base}/item/{item_id}/')
            mod.click('form[action*="/approve/"] button')
            confirm(mod, note='Clear report')
            mod.wait_for_url(f'**/item/{item_id}/')
            expect(mod.locator('.badge--info').first).to_contain_text('Approved')
        steps.append('moderator approved both reports through the confirmation dialog')

        owner.goto(f'{base}/item/{lost_id}/')
        match = owner.locator('.match').first
        expect(match).to_contain_text(f'Blue umbrella {TAG}')
        score = match.locator('.overlap__score').first.inner_text().replace('\n', '')
        steps.append(f'owner sees the match on their lost report: {score} with full breakdown')
        owner.goto(base + '/notifications/')
        expect(owner.locator('[data-notification]').first).to_contain_text('umbrella')
        steps.append('owner received an in-app match notification')

        owner.goto(f'{base}/item/{lost_id}/')
        owner.click('.match__foot a.btn')
        owner.fill('[name=statement]', 'It has a yellow square patch sewn on by me and a chipped wooden handle.')
        owner.click('button[type=submit]')
        owner.wait_for_url(re.compile(r'/claims/\d+/$'))
        claim_url = owner.url
        steps.append(f'owner started a claim linked to the match: {claim_url}')

        finder.goto(claim_url)
        expect(finder.locator('.contact').first).to_contain_text('You')
        finder.fill('[name=text]', 'What is written inside the handle strap?')
        finder.click('form[action*="/question/"] button')
        steps.append('finder asked a private verification question')

        owner.goto(claim_url)
        owner.fill('textarea[name=answer]', 'The initials A.S. in black marker')
        owner.click('form[action*="/answer/"] button')
        expect(owner.locator('.qa__a')).to_contain_text('A.S.')
        expect(owner.locator('.contact').first).to_contain_text('Hidden until approval')
        steps.append('owner answered; finder contact still hidden before approval')

        mod.goto(claim_url)
        mod.click('form[action*="/decide/"][action$="/approve/"] button')
        confirm(mod, note='Initials match the finder\'s check')
        mod.wait_for_url(claim_url)
        expect(mod.locator('.notice--ok')).to_contain_text('Approved, not yet returned')
        steps.append('moderator approved the claim; item not yet marked returned')

        owner.goto(claim_url)
        expect(owner.locator('.contact a').first).to_be_visible()
        steps.append('contact details now visible to the owner')

        mod.goto(claim_url)
        mod.click('form[action*="/return/"] button')
        confirm(mod, note='Collected at the library desk')
        mod.wait_for_url(claim_url)
        expect(mod.locator('.route > li.is-done')).to_have_count(5)
        mod.goto(f'{base}/item/{found_id}/')
        expect(mod.locator('.notice--ok')).to_contain_text('Returned')
        steps.append('handover confirmed: claim returned, report closed, timeline complete')

        mod.goto(base + '/manage/moderation/history/')
        expect(mod.locator('tbody')).to_contain_text('Handover confirmed')
        steps.append('decision log records approvals, claim decision and handover')
        browser.close()
    for s in steps:
        print('✓', s)


if __name__ == '__main__':
    main()
