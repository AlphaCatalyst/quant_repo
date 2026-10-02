#!/usr/bin/env python3
"""Read-only browser smoke check. Run with system Python against a local serve instance."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def check(page):
    return page.evaluate("""() => {
      const issues = [];
      const nav = document.querySelector('.nav');
      if (nav && nav.getBoundingClientRect().height > 52) issues.push('顶栏换行');
      if (document.documentElement.scrollWidth > innerWidth + 4) issues.push(`页面横向溢出 ${document.documentElement.scrollWidth - innerWidth}px`);
      for (const card of document.querySelectorAll('.card')) {
        const r = card.getBoundingClientRect();
        if (r.width < 100 || r.height < 150) continue;
        const nodes = [...card.querySelectorAll('h3,h4,p,li,pre,td,th,canvas,svg,details,a,.inbox-item,.stat,.progress,.empty,.bar-row')]
          .map(x => x.getBoundingClientRect()).filter(x => x.height > 0 && x.width > 0 && x.left < r.right && x.right > r.left);
        if (nodes.length) {
          const end = Math.max(...nodes.map(x => Math.min(x.bottom, r.bottom)));
          if (r.bottom - end > 120) issues.push(`卡片底部空白 ${Math.round(r.bottom-end)}px`);
        }
      }
      for (const table of document.querySelectorAll('table')) {
        const h = table.querySelector('thead th'), row = table.querySelector('tbody tr');
        if (!h || !row) continue;
        const a = h.getBoundingClientRect(), b = row.getBoundingClientRect();
        if (a.bottom > b.top + 2 && a.top < b.bottom - 2) issues.push('表头遮挡首行');
      }
      for (const wrap of document.querySelectorAll('.table-scroll')) {
        const table = wrap.querySelector('table');
        const overflow = !!table && table.scrollWidth > wrap.clientWidth + 1;
        if ((wrap.dataset.overflow === 'true') !== overflow) issues.push('表格横向滚动提示状态不符');
      }
      return issues;
    }""")


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--url', default='http://127.0.0.1:8732')
    p.add_argument('--shots', default='/tmp/as_shots/round4')
    p.add_argument('--browser-path', default=None, help='Optional local Chromium or Chrome executable')
    p.add_argument('--strategy', default=None, help='Optional non-holdout strategy trial for detail screenshot')
    args = p.parse_args()
    out = Path(args.shots)
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=args.browser_path)
        page = browser.new_page(viewport={'width': 1440, 'height': 900}, device_scale_factor=1)
        page.goto(args.url + '/#/mandates')
        page.wait_for_selector('.card', timeout=30000)
        page.wait_for_timeout(1200)
        strategy_links = page.locator('a[href^="#/strategy/S-"]:not([href$="-H"])')
        strategies = list(dict.fromkeys(strategy_links.evaluate_all(
            "els => els.map(el => el.getAttribute('href').split('/').pop())")))
        strategy = f'#/strategy/{args.strategy}' if args.strategy else strategy_links.first.get_attribute('href')
        if strategy and strategy.endswith('-H'):
            raise SystemExit('strategy screenshot must use a non-holdout trial')
        ids = [s for s in strategies if s and s.startswith('S-')]
        compare = f'#/compare?a={ids[0]}&b={ids[1]}' if len(ids) > 1 else None
        page.goto(args.url + '/#/factors')
        page.wait_for_selector('.card', timeout=30000)
        factor = page.locator('a[href^="#/factor/"]').first.get_attribute('href')
        page.goto(args.url + '/#/')
        page.wait_for_selector('.card', timeout=30000)
        campaign = page.locator('a[href^="#/campaign/"]').first.get_attribute('href')
        routes = {'overview': '#/', 'inbox': '#/inbox', 'mandates': '#/mandates',
                  'mandates-list': '#/mandates?view=list', 'mandates-holdout': '#/mandates?view=holdout',
                  'strategy': strategy,
                  'compare': compare,
                  'factors': '#/factors', 'factor': factor, 'campaign': campaign,
                  'ledger': '#/ledger', 'data': '#/data'}
        report = []
        for name, route in routes.items():
            if not route or route.endswith('-H'):
                report.append({'route': name, 'error': 'no eligible dev route'})
                continue
            for width in (1024, 1440):
                for theme in ('light', 'dark'):
                    context = browser.new_context(viewport={'width': width, 'height': 900}, device_scale_factor=1)
                    context.add_init_script(f"localStorage.setItem('alphasieve-theme','{theme}')")
                    context.add_init_script("localStorage.setItem('alphasieve-guide-seen','1')")
                    tab = context.new_page()
                    errors = []
                    tab.on('pageerror', lambda e: errors.append(str(e)))
                    tab.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
                    path = out / f'{name}-{width}-{theme}.png'
                    try:
                        tab.goto(args.url + '/' + route, wait_until='domcontentloaded')
                        tab.wait_for_selector('.card, .page-head', timeout=30000)
                        tab.wait_for_timeout(650)
                        issues = check(tab)
                        tab.screenshot(path=str(path), full_page=True, animations='disabled')
                        report.append({'route': name, 'width': width, 'theme': theme, 'shot': str(path),
                                       'issues': issues, 'errors': errors})
                    except Exception as exc:
                        report.append({'route': name, 'width': width, 'theme': theme, 'error': str(exc)[:300],
                                       'errors': errors})
                    context.close()
        guide_context = browser.new_context(viewport={'width': 1024, 'height': 900})
        guide = guide_context.new_page()
        guide.goto(args.url + '/#/', wait_until='domcontentloaded')
        guide.wait_for_selector('.intro-guide', timeout=30000)
        guide.wait_for_selector('.metric-strip', timeout=30000)
        guide.screenshot(path=str(out / 'first-visit-1024-light.png'), full_page=True, animations='disabled')
        guide_context.close()
        browser.close()
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    for row in report:
        print(f"{row['route']} {row.get('width','')} {row.get('theme','')}: {row.get('issues', [])} {row.get('errors', [])}")


if __name__ == '__main__':
    main()
