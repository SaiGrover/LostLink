"""Read-only browser smoke checks against seeded local demo users."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

root=Path('tmp/browser-check')
root.mkdir(parents=True,exist_ok=True)
results=[]
with sync_playwright() as p:
    browser=p.chromium.launch()
    for role,user,password in [('student','saanvi','student123'),('moderator','moderator','mod12345'),('admin','admin','admin123')]:
        context=browser.new_context(viewport={'width':1440,'height':1000})
        page=context.new_page()
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:8000/accounts/login/')
        page.locator('[name=username]').fill(user)
        page.locator('[name=password]').fill(password)
        page.locator('button[type=submit]').click()
        page.wait_for_url('**/dashboard/')
        routes=['/dashboard/','/search/','/search/?study=manual','/report/','/mine/','/claims/mine/','/notifications/','/accounts/profile/']
        if role in ('moderator','admin'): routes+=['/manage/moderation/']
        if role=='admin': routes+=['/manage/analytics/','/manage/users/','/manage/taxonomy/']
        for width in (1440,768,390):
            page.set_viewport_size({'width':width,'height':1000})
            for route in routes:
                response=page.goto('http://127.0.0.1:8000'+route)
                overflow=page.evaluate('document.documentElement.scrollWidth > innerWidth')
                culprits=page.evaluate('''() => [...document.querySelectorAll('*')].filter(e => e.getBoundingClientRect().right > innerWidth + 1).slice(-10).map(e => [e.tagName, String(e.className), Math.round(e.getBoundingClientRect().right)])''') if overflow else []
                results.append({'role':role,'width':width,'route':route,'status':response.status,'overflow':overflow,'culprits':culprits})
                if overflow or route in ('/dashboard/','/manage/analytics/','/report/','/notifications/'):
                    page.screenshot(path=str(root/f'{role}-{width}-{route.strip("/").replace("/","-")}.png'),full_page=True)
        if errors: raise RuntimeError(errors)
        context.close()
    browser.close()
(root/'results.json').write_text(json.dumps(results,indent=2))
failed=[r for r in results if r['status']!=200 or r['overflow']]
print(json.dumps({'checked':len(results),'failures':failed},indent=2))
if failed: raise SystemExit(1)
