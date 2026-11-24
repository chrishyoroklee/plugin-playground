// sitecheck: use the site the way a visitor does, in a real browser, and say what broke.
//
//   node scripts/sitecheck.mjs                         # the built site in dist/, served here, in Chromium
//   node scripts/sitecheck.mjs --browser webkit        # ...or firefox, or all
//   node scripts/sitecheck.mjs --url https://chrishyoroklee.github.io/plugin-playground/
//   node scripts/sitecheck.mjs --quick                 # a handful of presets, not every one
//
// The unit tests hold the pieces. This holds the page: that it loads and starts Python,
// that every preset builds in the browser's engine, that it makes sound, that each
// download is the file it claims to be, that Edit does what its buttons say, that a
// shared link reopens the same device, and that nothing logs an error along the way.
// It needs the network (Pyodide comes from a CDN) and `npx playwright install`. It makes
// no sound: see MUTE.

import { chromium, firefox, webkit } from 'playwright';
import { createServer } from 'node:http';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const flag = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback);
const QUICK = args.includes('--quick');
const ENGINES = { chromium, firefox, webkit };
const wanted = flag('--browser', 'chromium');
const browsers = wanted === 'all' ? Object.keys(ENGINES) : [wanted];
const OUT = flag('--out', path.join(ROOT, 'build', 'sitecheck'));

// ── the built site, served from a folder, as GitHub Pages serves it ─────────────

const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.mp3': 'audio/mpeg',
  '.whl': 'application/octet-stream', '.md': 'text/markdown', '.png': 'image/png', '.svg': 'image/svg+xml', '.wasm': 'application/wasm' };

async function serve() {
  const dist = path.join(ROOT, 'dist');
  if (!existsSync(path.join(dist, 'index.html'))) throw new Error('there is no dist/: run `npm run build` first');
  const server = createServer(async (req, res) => {
    const url = decodeURIComponent(new URL(req.url, 'http://x').pathname).replace(/^\/plugin-playground/, '');
    // With no installers built here, say that one exists, so the Download menu shows what a visitor sees.
    if (url === '/downloads/index.json' && !existsSync(path.join(dist, 'downloads', 'index.json'))) {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ runtime: { macos: 'downloads/plugin-playground-macos.pkg', windows: 'downloads/plugin-playground-windows-setup.exe', linux: 'downloads/plugin-playground-linux.tar.gz' } }));
      return;
    }
    const file = path.join(dist, url.endsWith('/') ? url + 'index.html' : url);
    try {
      const body = await readFile(file);
      res.writeHead(200, { 'content-type': TYPES[path.extname(file)] ?? 'application/octet-stream' });
      res.end(body);
    } catch {
      res.writeHead(404);
      res.end('not found');
    }
  });
  await new Promise((done) => server.listen(0, '127.0.0.1', done));
  return { url: `http://127.0.0.1:${server.address().port}/plugin-playground/`, close: () => server.close() };
}

// ── silence ─────────────────────────────────────────────────────────────────────

// Runs in the page before anything of its own. Whatever the page connects to "the
// speakers" is connected to a gain of zero in front of them.
const MUTE = () => {
  const real = Object.getOwnPropertyDescriptor(BaseAudioContext.prototype, 'destination').get;
  const silent = new WeakMap();
  Object.defineProperty(BaseAudioContext.prototype, 'destination', {
    configurable: true,
    get() {
      const out = real.call(this);
      if (typeof OfflineAudioContext !== 'undefined' && this instanceof OfflineAudioContext) return out;
      if (!silent.has(this)) {
        const gate = this.createGain();
        gate.gain.value = 0;
        gate.connect(out);
        silent.set(this, gate);
      }
      return silent.get(this);
    },
  });
  for (const tag of [HTMLAudioElement, HTMLVideoElement]) {
    const play = tag.prototype.play;
    tag.prototype.play = function () { this.muted = true; this.volume = 0; return play.call(this); };
  }
  window.__muted = true;
};

// ── one browser ─────────────────────────────────────────────────────────────────

async function check(name, url) {
  const problems = [];
  const note = (ok, what, detail = '') => {
    console.log(`  ${ok ? 'ok  ' : 'FAIL'}  ${what}${detail ? `  ${detail}` : ''}`);
    if (!ok) problems.push(`${what}${detail ? `: ${detail}` : ''}`);
  };
  // Silent, always. The page is asked to play, and a headless browser still has the
  // machine's speakers: so the browser is muted where it has a switch for that, and in
  // every browser the page's way out to the speakers is swapped for one turned to zero.
  // The checks read the page's own level meter, which sits before the output.
  const launch = name === 'chromium' ? { args: ['--mute-audio', '--autoplay-policy=no-user-gesture-required'] }
    : name === 'firefox' ? { firefoxUserPrefs: { 'media.volume_scale': '0.0', 'media.autoplay.default': 0, 'media.autoplay.blocking_policy': 0 } } : {};
  const browser = await ENGINES[name].launch(launch);
  const context = await browser.newContext({ acceptDownloads: true, viewport: { width: 1400, height: 950 } });
  await context.addInitScript(MUTE);
  if (name === 'chromium') await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const page = await context.newPage();
  const errors = [];
  const failed = [];
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('response', (r) => { if (r.status() >= 400) failed.push(`${r.status()} ${r.url()}`); });
  const shots = path.join(OUT, name);
  await mkdir(shots, { recursive: true });

  const state = () => page.evaluate(() => document.body.dataset.state);
  const settle = async (timeout = 90000) => {
    await page.waitForTimeout(350);
    await page.waitForFunction(() => ['ready', 'error'].includes(document.body.dataset.state), null, { timeout });
    return state();
  };
  const level = () => page.evaluate(() => Number(getComputedStyle(document.getElementById('meter-l')).getPropertyValue('--level')));
  const loudest = async (ms = 1500) => {
    let top = 0;
    for (let waited = 0; waited < ms; waited += 100) { await page.waitForTimeout(100); top = Math.max(top, await level()); }
    return top;
  };
  const stopEditing = async () => {
    await page.keyboard.press('Escape');
    if (await page.evaluate(() => document.body.classList.contains('is-editing'))) await page.locator('#btn-edit').click();
  };

  // A preset, the way a visitor reaches it: open its plugin in the left panel, then click it.
  const presetRow = (name) => page.locator('#presets .preset').filter({ has: page.locator('b', { hasText: new RegExp(`^${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`) }) }).first();
  const reveal = async (row) => {
    if (await row.isVisible()) return;
    await row.locator('xpath=ancestor::div[contains(@class, "plugin")][1]').locator('.plugin-head').click();
    await settle();
    if (!(await row.isVisible())) await row.locator('xpath=ancestor::div[contains(@class, "plugin")][1]').locator('.plugin-head').click();
  };
  const choose = async (name) => {
    const row = presetRow(name);
    await reveal(row);
    await row.click();
  };
  const presetShown = () => page.evaluate(() => { const s = document.getElementById('preset-pick'); return s.options[s.selectedIndex]?.textContent ?? ''; });

  try {
    // 1. it loads, and Python starts
    const began = Date.now();
    await page.goto(url);
    const first = await settle(240000).catch(() => 'timeout');
    note(first === 'ready', 'the page loads and builds its first device', `${((Date.now() - began) / 1000).toFixed(1)} s`);
    if (first !== 'ready') throw new Error('the page did not start');
    // Not a check of the site: a check that this run cannot be heard, before it presses Play.
    const gain = await page.evaluate(() => {
      if (!window.__muted) return 'the mute did not run';
      const probe = new AudioContext();
      const value = probe.destination.gain ? probe.destination.gain.value : 'not a gain';
      void probe.close();
      return value;
    });
    if (gain !== 0) throw new Error(`this run would be audible (${gain}); stopping before it plays anything`);

    // 2. every preset builds, on both tabs. Nothing has been played yet, which is how a
    // visitor first meets the tabs (and where Safari once never switched).
    const lists = {};
    for (const tab of ['effect', 'instrument']) {
      await page.locator(`#mode-${tab}`).click();
      const switched = await page.waitForFunction((id) => document.getElementById(id).getAttribute('aria-selected') === 'true', `mode-${tab}`, { timeout: 15000 })
        .then(() => true, () => false);
      note(switched, `the ${tab === 'effect' ? 'Effect' : 'Instrument'} tab opens before anything has been played`);
      if (!switched) continue;
      await settle();
      const count = await page.locator('#presets .preset').count();
      lists[tab] = await page.locator('#presets .preset b').first().textContent();
      const which = QUICK ? [0, 1, Math.floor(count / 2), count - 2, count - 1] : [...Array(count).keys()];
      const bad = [];
      for (const i of which) {
        const preset = page.locator('#presets .preset').nth(i);
        const title = await preset.locator('b').textContent();
        await reveal(preset);
        await preset.click();
        const s = await settle().catch(() => 'timeout');
        const dials = await page.locator('#device .live-dial').count();
        const shown = await presetShown();
        if (s !== 'ready' || dials < 1 || shown !== title) bad.push(`${title} (${s}, ${dials} dials, menu says "${shown}")`);
        if (await page.locator('#drawer.open').count()) await page.locator('#drawer-close').click();
      }
      note(bad.length === 0, `${which.length} of ${count} ${tab} presets build`, bad.slice(0, 5).join('; '));
    }
    note(lists.effect !== lists.instrument, 'the two tabs list different presets', `${lists.effect} / ${lists.instrument}`);

    // 2b. a category lists plugins, and a plugin's presets are inside it
    await page.locator('#mode-effect').click();
    await settle();
    await page.locator('#sections .section-chip', { hasText: /^EQ$/ }).click();
    const eqPlugins = await page.locator('#presets .plugin:not(.is-rack) .plugin-head b').allTextContents();
    note(eqPlugins.join(',') === 'Three Band EQ,Parametric EQ,Graphic EQ,Tilt EQ', 'EQ lists four plugins, not a dozen presets', eqPlugins.join(', '));
    await page.locator('#presets .plugin[data-plugin="eq"] .plugin-head').click();
    await settle();
    const inside = await page.locator('#presets .plugin[data-plugin="eq"] .preset b').allTextContents();
    const title = await page.locator('#device-title').textContent();
    note(title === 'Three Band EQ' && inside.includes('Smile Curve') && inside.includes('Mid Push'), 'opening a plugin shows its presets inside it', `${title}: ${inside.join(', ')}`);
    const menu = await page.locator('#preset-pick option').allTextContents();
    await page.locator('#preset-pick').selectOption({ label: 'Mid Push' });
    await settle();
    const afterPick = await presetShown();
    await page.locator('#preset-next').click();
    await settle();
    const afterNext = await presetShown();
    await page.locator('#preset-pick').selectOption({ label: 'Default' });
    await settle();
    const flat = await page.evaluate(() => JSON.parse(decodeURIComponent(escape(atob(location.hash.slice(3).replace(/-/g, '+').replace(/_/g, '/'))))).chain.find((e) => e.module === 'eq').values);
    note(menu[0] === 'Default' && afterPick === 'Mid Push' && afterNext !== 'Mid Push' && flat.low === 0 && flat.mid === 0 && flat.high === 0
      && await page.locator('#device-title').textContent() === 'Three Band EQ',
      'the plugin\'s preset menu and arrows change its settings, and Default is flat', `${menu.join(', ')}; then ${afterPick}, ${afterNext}`);
    await page.locator('#sections .section-chip', { hasText: /^All$/ }).click();

    // 3. it makes sound: an effect on its sample, bypassed, and an instrument on a key
    await page.locator('#mode-effect').click();
    await settle();
    await choose('Shoegaze Wall');
    await settle();
    await page.locator('#btn-play').click();
    const wet = await loudest(2500);
    note(wet > 0.05, 'an effect plays its sample', `meter ${wet.toFixed(2)}`);
    await page.locator('#btn-bypass').click();
    const dry = await loudest(1200);
    note(dry > 0.05, 'bypass plays the sample dry', `meter ${dry.toFixed(2)}`);
    await page.locator('#btn-bypass').click();
    await page.locator('#btn-play').click();
    await page.waitForTimeout(300);

    await page.locator('#mode-instrument').click();
    await settle();
    await choose('Saw Lead');
    await settle();
    const key = page.locator('#piano .key').nth(12);
    const box = await key.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height * 0.7);
    await page.mouse.down();
    const played = await loudest(1200);
    await page.mouse.up();
    note(played > 0.05, 'an instrument sounds when a key is pressed', `meter ${played.toFixed(2)}`);
    await page.waitForTimeout(600);

    // 4. the downloads are the files they say they are
    for (const tab of ['effect', 'instrument']) {
      await page.locator(`#mode-${tab}`).click();
      await settle();
      await page.locator('#btn-download').click();
      const [amxd] = await Promise.all([page.waitForEvent('download', { timeout: 60000 }), page.locator('#dl-amxd').click()]);
      const amxdBytes = await readFile(await amxd.path());
      note(amxdBytes.subarray(0, 4).toString('latin1') === 'ampf' && amxdBytes.length > 4000, `${tab}: the Max for Live device downloads`,
        `${amxd.suggestedFilename()}, ${amxdBytes.length} bytes`);
      if (!(await page.locator('#downloads.open').count())) await page.locator('#btn-download').click();
      const [dev] = await Promise.all([page.waitForEvent('download', { timeout: 60000 }), page.locator('#dl-device').click()]);
      const text = (await readFile(await dev.path())).toString('utf8');
      const ok = text.startsWith('ppdevice 1\n') && text.includes(`\nkind ${tab}\n`) && text.includes('\n@end\n') && /\nface \S+/.test(text) && /\nsettings \S+/.test(text);
      note(ok && dev.suggestedFilename().endsWith('.ppdevice'), `${tab}: the plugin's device file downloads`, `${dev.suggestedFilename()}, ${text.length} bytes`);
      // ...with the plugin's presets in it, for the menu in its window, and the programs they need
      const presets = (text.match(/\npreset \d+ /g) ?? []).length;
      const programs = (text.match(/(^|\n)ppdevice 1\n/g) ?? []).length;
      note(presets >= 1 && /\ncurrent -?\d+\n$/.test(text) && /^ppdevice 1\nruntime 4\n/.test(text), `${tab}: the file holds the plugin's presets`,
        `${presets} presets, ${programs} programs`);
      const href = await page.locator('#dl-installer').getAttribute('href');
      note(!!href && /plugin-playground-(macos|windows|linux)/.test(href), `${tab}: the installer is offered`, href ?? 'no link');
      await page.keyboard.press('Escape');
      await page.mouse.click(700, 500);
    }

    // 5. Edit: effects, the EQ, a knob, a dial that turns by itself, colours
    await page.locator('#mode-effect').click();
    await settle();
    await choose('Quarter Echo');
    await settle();
    await page.locator('#btn-edit').click();
    await page.waitForTimeout(200);
    const onDevice = () => page.locator('#device .edit-card.is-effect').count();
    const cardsBefore = await onDevice();
    await page.locator('.edit-add', { hasText: 'Effect' }).click();
    const offered = await page.locator('#dial-pop .edit-option b').allTextContents();
    note(offered.includes('EQ') && offered.includes('Parametric EQ') && offered.includes('Reverb'), 'Edit: + Effect offers what is not on the device', offered.join(', '));
    // a click anywhere else closes the menu, whichever one it is
    await page.mouse.click(8, 8);
    const shutEffect = await page.locator('#dial-pop').isHidden();
    await page.locator('.edit-add', { hasText: 'Look' }).click();
    const lookOpen = await page.locator('#dial-pop').isVisible();
    await page.mouse.click(8, 8);
    note(shutEffect && lookOpen && await page.locator('#dial-pop').isHidden(), 'Edit: a click away closes the + Effect and Look menus');
    await page.locator('.edit-add', { hasText: 'Effect' }).click();
    await page.locator('#dial-pop .edit-option').filter({ has: page.locator('b', { hasText: /^EQ$/ }) }).click();
    await settle();
    const eqDials = await page.locator('#device .dv-widget[data-module="eq"]').count();
    note((await onDevice()) === cardsBefore + 1 && eqDials === 4, 'Edit: the EQ goes on with its four dials', `${eqDials} dials`);

    const cards = page.locator('#device .edit-card.is-effect');
    const grip = await cards.last().locator('.edit-grip').boundingBox();
    const target = await cards.first().boundingBox();
    const orderOf = () => page.locator('#chain .mod.is-on').evaluateAll((els) => els.map((e) => e.dataset.id));
    const before = await orderOf();
    await page.mouse.move(grip.x + grip.width / 2, grip.y + 10);
    await page.mouse.down();
    await page.mouse.move(grip.x - 30, grip.y + 10, { steps: 4 });
    await page.mouse.move(target.x + 4, target.y + 12, { steps: 8 });
    await page.mouse.up();
    await settle();
    const after = await orderOf();
    note(after[0] === before[before.length - 1], 'Edit: dragging an effect moves it', `${before.join(' > ')}  to  ${after.join(' > ')}`);

    await page.locator('#device .dv-widget[data-target="delay.time"]').click();
    await page.locator('#dial-pop .edit-option', { hasText: 'Sweep' }).click();
    await settle();
    note((await page.locator('#device .dv-widget.is-moved').count()) === 1 && (await page.locator('#device .moved-ring').count()) === 1,
      'Edit: a dial set to sweep is marked and shows how far');
    await page.keyboard.press('Escape');
    // the end of that arc is a handle: taken round to the dial's far end, it sweeps further
    const saved = () => page.evaluate(() => JSON.parse(decodeURIComponent(escape(atob(location.hash.slice(3).replace(/-/g, '+').replace(/_/g, '/'))))));
    await page.waitForTimeout(500);
    const farBefore = (await saved()).motion.routes[0].amount;
    const ring = await page.locator('#device .dv-widget[data-target="delay.time"] .moved-ring').boundingBox();
    const gripAt = await page.locator('#device .dv-widget[data-target="delay.time"] .moved-grip').boundingBox();
    await page.mouse.move(gripAt.x + gripAt.width / 2, gripAt.y + gripAt.height / 2);
    await page.mouse.down();
    // the far end of a dial's travel is at half past four: 45 degrees below the horizontal, on the right
    const unit = ring.width / 44;
    await page.mouse.move(ring.x + (22 + 14.4 * Math.SQRT1_2) * unit, ring.y + (25 + 14.4 * Math.SQRT1_2) * unit, { steps: 8 });
    await page.mouse.up();
    await settle();
    await page.waitForTimeout(500);
    const farAfter = (await saved()).motion.routes[0].amount;
    note(farAfter !== farBefore && farAfter > 30 && farAfter < 60 && (await page.locator('#dial-pop:not([hidden]) h2').count()) === 0,
      'Edit: dragging the end of the arc sets how far the dial sweeps', `${farBefore} to ${farAfter}`);

    await page.locator('.edit-add', { hasText: 'Knob' }).click();
    await settle();
    await page.locator('#device .dv-widget[data-target="delay.mix"]').click();
    await settle();
    await page.locator('#device .dv-widget[data-target="eq.high"]').click();
    await settle();
    const linked = await page.locator('#device .dv-widget.is-linked').count();
    await page.locator('.edit-done').click();
    await settle();
    note(linked === 2 && (await page.locator('#device .dv-widget[data-knob]').count()) === 1, 'Edit: a knob of one\'s own links to the dials clicked', `${linked} linked`);

    await page.locator('.edit-add', { hasText: 'Look' }).click();
    await page.locator('.edit-theme', { hasText: 'Midnight' }).click();
    await settle();
    const backdrop = await page.locator('#device .dv-panel').first().evaluate((e) => getComputedStyle(e).backgroundColor);
    note(backdrop === 'rgb(23, 24, 28)', 'Edit: a theme recolours the device', backdrop);
    await page.locator('#device .edit-card.is-effect').first().locator('.edit-x').click();
    await settle();
    note((await onDevice()) === cardsBefore, 'Edit: an effect\'s x takes it off');
    await stopEditing();
    await page.screenshot({ path: path.join(shots, 'edited.png') });

    // 5a. the other kinds of EQ and of reverb: each is its own device with its own picture
    for (const [preset, kind, dials] of [['Parametric Sculpt', 'peq', 10], ['Graphic Curve', 'geq', 6], ['Tilt Bright', 'tilt', 2], ['Bus Glue', 'comp', 6]]) {
      await choose(preset);
      await settle();
      const canvas = page.locator(`#device .dv-graph[data-kind="${kind}"] canvas`);
      const drawn = (await canvas.count()) === 1 && await canvas.evaluate((c) => {
        const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
        let n = 0;
        for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
        return n;
      });
      const count = await page.locator(`#device .dv-widget[data-module="${kind}"]`).count();
      note(drawn > 200 && count === dials, `${preset}: its own picture and ${dials} dials`, `${drawn} pixels, ${count} dials`);
    }
    await choose('Vocal Plate');
    await settle();
    const spaces = page.locator('#device .dv-typeslot[data-kind="reverb"] .dv-types button');
    const reverbPicture = await page.locator('#device .dv-graph[data-kind="reverb"]').count();
    const spaceNames = await spaces.allTextContents();
    await spaces.filter({ hasText: 'Room' }).click();
    await settle();
    const nowRoom = await page.evaluate(() => JSON.parse(decodeURIComponent(escape(atob(location.hash.slice(3).replace(/-/g, '+').replace(/_/g, '/'))))).chain.find((e) => e.module === 'reverb').options.space);
    note(spaceNames.join(',') === 'Room,Hall,Plate' && nowRoom === 'room' && reverbPicture === 0,
      'the reverb is knobs, with its three spaces picked on its card', `${spaceNames.join(', ')}; now ${nowRoom}; ${reverbPicture} pictures`);

    // 5b. pictures: an EQ's curve is there, dragging it turns the dial, and Look can take them away
    await choose('Mid Push');
    await settle();
    const picture = page.locator('#device .dv-graph[data-kind="eq"] canvas');
    const midBefore = await page.locator('#device .live-dial[data-param="EQ Mid"]').getAttribute('aria-valuenow');
    const pb = await picture.boundingBox();
    const inked = pb && await picture.evaluate((c) => {
      const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
      let n = 0;
      for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
      return n;
    });
    note(!!pb && inked > 200, 'a picture is drawn above the EQ\'s dials', `${inked} pixels`);
    if (pb) {
      // the middle dot sits at the Freq dial's frequency and the Mid dial's gain: take it down and to the left
      const handle = await page.evaluate(() => {
        const s = JSON.parse(decodeURIComponent(escape(atob(location.hash.slice(3).replace(/-/g, '+').replace(/_/g, '/')))));
        return s.chain.find((e) => e.module === 'eq').values;
      });
      const hx = pb.x + pb.width * Math.log(handle.freq / 20) / Math.log(1000);
      const hy = pb.y + pb.height / 2 - (handle.mid / 18) * (pb.height / 2 - 3 * pb.height / 70);
      await page.mouse.move(hx, hy);
      await page.mouse.down();
      await page.mouse.move(hx - pb.width * 0.15, pb.y + pb.height * 0.8, { steps: 6 });
      await page.mouse.up();
      await page.waitForTimeout(700);
      const midAfter = Number(await page.locator('#device .live-dial[data-param="EQ Mid"]').getAttribute('aria-valuenow'));
      const saved = await page.evaluate(() => JSON.parse(decodeURIComponent(escape(atob(location.hash.slice(3).replace(/-/g, '+').replace(/_/g, '/'))))).chain.find((e) => e.module === 'eq').values);
      note(midAfter < 0 && Math.abs(saved.mid - midAfter) < 0.01 && saved.freq < handle.freq, 'dragging the picture turns the dials, and is saved',
        `Mid ${midBefore} to ${midAfter.toFixed(1)} dB, Freq ${Math.round(handle.freq)} to ${Math.round(saved.freq)} Hz`);
    }
    // while it plays, what is sounding is drawn behind the curve
    if (pb) {
      const filled = () => picture.evaluate((c) => {
        const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
        let n = 0;
        for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
        return n;
      });
      const silent = await filled();
      await page.locator('#btn-play').click();
      let most = 0;
      for (let i = 0; i < 12; i++) { await page.waitForTimeout(150); most = Math.max(most, await filled()); }
      await page.locator('#btn-play').click();
      await page.waitForTimeout(400);
      note(most > silent * 1.15 && Math.abs((await filled()) - silent) < silent * 0.05, 'while it plays, the spectrum is drawn behind the EQ\'s curve, and goes when it stops',
        `${silent} pixels silent, ${most} playing`);
    }
    // a module's kinds are along the top of its picture
    await choose('Telephone');
    await settle();
    const types = page.locator('#device .dv-graph[data-kind="filter"] .dv-types button');
    const was = await page.locator('#device .dv-graph[data-kind="filter"] .dv-types button[aria-checked="true"]').getAttribute('data-choice');
    const other = was === 'highpass' ? 'lowpass' : 'highpass';
    await page.locator(`#device .dv-graph[data-kind="filter"] .dv-types button[data-choice="${other}"]`).click();
    await settle();
    await page.waitForTimeout(500);
    const mode = (await saved()).chain.find((e) => e.module === 'filter').options.mode;
    note((await types.count()) === 4 && mode === other
      && (await page.locator('#device .dv-graph[data-kind="filter"] .dv-types button[aria-checked="true"]').getAttribute('data-choice')) === other,
      'a picture\'s type buttons change the module\'s kind', `${was} to ${mode}`);

    await page.locator('#btn-edit').click();
    await page.locator('.edit-add', { hasText: 'Look' }).click();
    await page.locator('.edit-look', { hasText: 'Dials only' }).click();
    await settle();
    note((await page.locator('#device .dv-graph').count()) === 0, 'Look: Dials only takes the pictures away');
    await page.locator('.edit-look', { hasText: 'A picture and dials' }).click();
    await settle();
    note((await page.locator('#device .dv-graph').count()) > 0, 'Look: and puts them back');
    await stopEditing();

    // 6. a link reopens exactly this device
    await page.waitForTimeout(600);
    const link = await page.evaluate(() => location.href);
    const mine = await page.evaluate(() => ({ code: document.getElementById('code').textContent, title: document.getElementById('device-title').textContent }));
    const second = await context.newPage();
    await second.goto(link);
    await second.waitForFunction(() => ['ready', 'error'].includes(document.body.dataset.state), null, { timeout: 240000 });
    const theirs = await second.evaluate(() => ({ code: document.getElementById('code').textContent, title: document.getElementById('device-title').textContent }));
    note(link.includes('#s=') && theirs.code === mine.code && theirs.title === mine.title, 'a link reopens the same device, script for script');
    await second.close();

    // 7. Describe it, the free way: a pasted answer becomes a device
    await page.locator('#tab-build').click();
    await page.locator('#ai-request').fill('a bright slap echo');
    await page.evaluate(() => { document.getElementById('ai-paste-box').open = true; });
    const answer = { name: 'Slap Check', explanation: 'A short bright echo.', synth: [], output_gain: 0, lfo1: { shape: 'sine', rate: 1 }, lfo2: { shape: 'sine', rate: 6 },
      follow_sens: 6, knobs: [], motion: [],
      chain: [{ id: 'delay', options: [{ id: 'style', choice: 'stereo' }], dials: [{ id: 'time', value: 110 }, { id: 'feedback', value: 10 }, { id: 'mix', value: 30 }] },
        { id: 'eq', options: [], dials: [{ id: 'low', value: -2 }, { id: 'mid', value: 0 }, { id: 'freq', value: 1000 }, { id: 'high', value: 5 }] }] };
    await page.locator('#ai-answer').fill(`Here it is:\n\`\`\`json\n${JSON.stringify(answer, null, 1)}\n\`\`\``);
    await page.locator('#ai-apply').click();
    await settle();
    note((await page.locator('#device-title').textContent()) === 'Slap Check' && (await page.locator('#device .live-dial').count()) === 8,
      'Describe it: a pasted answer builds the device', await page.locator('#ai-note').textContent());

    // 8. dark mode, and a phone-sized window
    await page.locator('#btn-settings').click();
    await page.locator('#dark-mode').click();
    await page.waitForTimeout(200);
    note((await page.evaluate(() => document.documentElement.dataset.theme)) === 'dark', 'dark mode switches on');
    await page.screenshot({ path: path.join(shots, 'dark.png') });
    await page.locator('#dark-mode').click();
    await page.keyboard.press('Escape');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(400);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    note(overflow <= 1, 'at phone width nothing runs off the side', `${overflow} px`);
    await page.screenshot({ path: path.join(shots, 'phone.png') });
    await page.setViewportSize({ width: 1400, height: 950 });

    // 9. nothing went wrong quietly
    const quiet = [...new Set(failed)].filter((f) => !/favicon/.test(f));
    note(quiet.length === 0, 'no request failed', quiet.slice(0, 4).join('; '));
    const loud = [...new Set(errors)].filter((e) => !/Failed to load resource/.test(e));
    note(loud.length === 0, 'no error was logged', loud.slice(0, 4).join(' | '));
  } catch (err) {
    note(false, 'the check itself stopped', String(err.message ?? err).split('\n')[0]);
    await page.screenshot({ path: path.join(shots, 'stopped.png') }).catch(() => {});
    if (errors.length) console.log(`        logged: ${[...new Set(errors)].slice(0, 3).join(' | ')}`);
  }
  await browser.close();
  return problems;
}

const given = flag('--url', '');
const local = given ? null : await serve();
const url = given || local.url;
await mkdir(OUT, { recursive: true });
let total = 0;
const report = {};
for (const name of browsers) {
  console.log(`\n${name}  ${url}`);
  report[name] = await check(name, url);
  total += report[name].length;
}
local?.close();
await writeFile(path.join(OUT, 'report.json'), JSON.stringify(report, null, 1));
console.log(total ? `\n${total} problem${total === 1 ? '' : 's'}` : '\nall of it works');
process.exit(total ? 1 : 0);
