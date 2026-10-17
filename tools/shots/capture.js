/**
 * Screenshots for the README, from the agent's real pages against tools/shots/demo-data.
 *
 *   python3 tools/shots/mock.py 8099 &
 *   node tools/shots/capture.js                # -> docs/img/*.png
 *
 * Needs playwright (npm i -D playwright) and a chromium build; set CHROME to use your own.
 */
const { chromium } = require('playwright');
const path = require('path');

const PORT = process.env.PORT || 8099;
const OUT = process.env.OUT || path.join(__dirname, '..', '..', 'docs', 'img');
const EXE = process.env.CHROME || undefined;

// [file, url, selector (null = viewport), { width, height, wait, before }]
const SHOTS = [
  ['dashboard',      '/',      '#playcard,#modecard', { width: 1100, height: 1400, group: ['#playcard', '#modecard'] }],
  ['sound-field',    '/',      '#roomcard',           { width: 1100, height: 1500, wait: 4000 }],
  ['led',            '/',      '#ledmodes',           { width: 1100, height: 1300, card: true }],
  ['speakers',       '/',      '#fleetcard',          { width: 1100, height: 1200 }],
  ['scenes',         '/',      '#scenecard',          { width: 1100, height: 1200 }],
  ['system',         '/',      '#syscard',            { width: 1100, height: 1400 }],
  ['setup-network',  '/setup', '#netcard',            { width: 1100, height: 1400, open: ['#netsettings'] }],
  ['setup-link',     '/setup', '#netcard',            { width: 1100, height: 1400, open: ['#linkbox'] }],
  ['setup-rooms',    '/setup', '#rooms',              { width: 1100, height: 1500 }],
  ['setup-layout',   '/setup', '#layoutcard',         { width: 1100, height: 1600, wait: 2500 }],
  ['setup-firmware', '/setup', '#fwcard',             { width: 1100, height: 1300 }],
  ['light-theme',    '/',      '#playcard',           { width: 1100, height: 1200, theme: 'light' }],
];

(async () => {
  const browser = await chromium.launch({ executablePath: EXE });
  for (const [name, url, sel, opt] of SHOTS) {
    const page = await browser.newPage({ viewport: { width: opt.width, height: opt.height }, deviceScaleFactor: 2 });
    const errs = [];
    page.on('pageerror', e => errs.push(String(e)));
    await page.goto(`http://127.0.0.1:${PORT}${url}`, { waitUntil: 'networkidle' }).catch(() => {});
    if (opt.theme) await page.evaluate(t => { document.documentElement.dataset.theme = t; try { localStorage.setItem('theme', t); } catch (e) {} }, opt.theme);
    await page.waitForTimeout(opt.wait || 1500);
    for (const s of opt.open || []) {
      await page.evaluate(sel => { const d = document.querySelector(sel)?.closest('details'); if (d) { d.open = true; d.dispatchEvent(new Event('toggle')); } }, s);
      await page.waitForTimeout(1200);
    }
    let target = null;
    if (opt.group) {
      // wrap several cards so they come out as one picture
      await page.evaluate(ids => {
        const box = document.createElement('div'); box.id = '__shot';
        box.style.cssText = 'display:flex;flex-direction:column;gap:14px;padding:14px;background:var(--bg)';
        const first = document.querySelector(ids[0]);
        first.parentNode.insertBefore(box, first);
        ids.forEach(i => box.appendChild(document.querySelector(i)));
      }, opt.group);
      await page.waitForTimeout(400);
      target = await page.$('#__shot');
    } else if (sel) {
      if (opt.card) {
        await page.evaluate(s => { const c = document.querySelector(s)?.closest('.card'); if (c) c.id = '__card'; }, sel);
        target = await page.$('#__card');
      } else target = await page.$(sel.split(',')[0]);
    }
    const file = path.join(OUT, name + '.png');
    if (target) await target.screenshot({ path: file });
    else await page.screenshot({ path: file });
    console.log('%-16s %s%s', name, file, errs.length ? '   page errors: ' + errs.slice(0, 2).join(' | ') : '');
    await page.close();
  }
  await browser.close();
})();
