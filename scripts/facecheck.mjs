// facecheck: is a device in the plugin's window the device on the page?
//
//   node scripts/facecheck.mjs --python path/to/python      # one with maxpylang in it
//
// The page draws a device from the patch's presentation view (device-view.ts). The
// plugin's window draws it from the face patchc.py writes into the .ppdevice
// (native/ui/face.html), which is a browser page too: WKWebView in the plugin, Chromium
// here. So both are opened side by side for a few presets and every card, label, dial
// and picture is measured: the same things, in the same places, and pictures that are
// the same drawing. A picture of each is saved in build/facecheck to look at.
//
// It does not open a DAW, and it says nothing about the Max for Live device, which Live
// draws with its own dials. It makes no sound (the same muting as sitecheck.mjs).

import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const flag = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback);
const PYTHON = flag('--python', 'python3');
const OUT = flag('--out', path.join(ROOT, 'build', 'facecheck'));
const PRESETS = flag('--presets', 'parametric,graphic,tiltbright,smilecurve,vocalplate,shoegaze,tape,wobble').split(',');
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.mp3': 'audio/mpeg',
  '.whl': 'application/octet-stream', '.png': 'image/png', '.svg': 'image/svg+xml', '.wasm': 'application/wasm' };

const MUTE = () => {
  const real = Object.getOwnPropertyDescriptor(BaseAudioContext.prototype, 'destination').get;
  const silent = new WeakMap();
  Object.defineProperty(BaseAudioContext.prototype, 'destination', {
    configurable: true,
    get() {
      const out = real.call(this);
      if (typeof OfflineAudioContext !== 'undefined' && this instanceof OfflineAudioContext) return out;
      if (!silent.has(this)) { const gate = this.createGain(); gate.gain.value = 0; gate.connect(out); silent.set(this, gate); }
      return silent.get(this);
    },
  });
};

/** The faces of these presets, as patchc writes them into a .ppdevice. */
function faces(ids) {
  const code = `
import json, sys
sys.path.insert(0, ${JSON.stringify(path.join(ROOT, 'scripts'))})
import plugin
presets = {p["id"]: p for p in json.load(open(${JSON.stringify(path.join(ROOT, 'src', 'plugin', 'presets.json'))}))}
out = {}
for i in ${JSON.stringify(ids)}:
    made = plugin.fxgen.run(presets[i]["settings"], want_amxd=False)
    out[i] = {"name": presets[i]["name"], "face": plugin.patchc.Compiler(made["json"], made["settings"]["name"]).face()}
print(json.dumps(out))`;
  return JSON.parse(execFileSync(PYTHON, ['-c', code], { encoding: 'utf8', maxBuffer: 1 << 26 }));
}

/** face.html as the plugin is built with it: the drawing code and the fonts pasted in. */
async function faceHtml() {
  let html = await readFile(path.join(ROOT, 'native', 'ui', 'face.html'), 'utf8');
  let fonts = '';
  for (const [weight, file] of [[400, 'DMMono-Regular.woff2'], [500, 'DMMono-Medium.woff2']]) {
    const data = (await readFile(path.join(ROOT, 'native', 'ui', 'fonts', file))).toString('base64');
    fonts += `@font-face { font-family: 'DM Mono'; font-weight: ${weight}; src: url(data:font/woff2;base64,${data}) format('woff2'); }\n`;
  }
  const graphs = await readFile(path.join(ROOT, 'src', 'plugin', 'graphs.js'), 'utf8');
  return html.replace('__FONTS__', () => fonts).replace('__GRAPHS__', () => graphs);
}

async function serve() {
  const dist = path.join(ROOT, 'dist');
  if (!existsSync(path.join(dist, 'index.html'))) throw new Error('there is no dist/: run `npm run build` first');
  const server = createServer(async (req, res) => {
    const url = decodeURIComponent(new URL(req.url, 'http://x').pathname).replace(/^\/plugin-playground/, '');
    try {
      const file = path.join(dist, url.endsWith('/') ? url + 'index.html' : url);
      const body = await readFile(file);
      res.writeHead(200, { 'content-type': TYPES[path.extname(file)] ?? 'application/octet-stream' });
      res.end(body);
    } catch { res.writeHead(404); res.end('not found'); }
  });
  await new Promise((done) => server.listen(0, '127.0.0.1', done));
  return { url: `http://127.0.0.1:${server.address().port}/plugin-playground/`, close: () => server.close() };
}

// Everything on a device, in the device's own units: [kind, x, y, w, h, text].
const MEASURE = (selectors) => {
  const device = document.querySelector('#device');
  const box = device.getBoundingClientRect();
  const k = box.width / device.offsetWidth;
  const out = [];
  for (const [kind, selector] of Object.entries(selectors)) {
    for (const el of device.querySelectorAll(selector)) {
      const r = el.getBoundingClientRect();
      out.push([kind, (r.left - box.left) / k, (r.top - box.top) / k, r.width / k, r.height / k, kind === 'label' ? el.textContent : '']);
    }
  }
  return { width: device.offsetWidth, height: device.offsetHeight, items: out.sort((a, b) => a[0].localeCompare(b[0]) || a[1] - b[1] || a[2] - b[2]) };
};
// A picture's ink: where it is drawn, on a coarse grid, so two sizes of canvas compare.
const INK = (selector) => [...document.querySelectorAll(selector)].map((c) => {
  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data, cols = 35, rows = 14, grid = new Array(cols * rows).fill(0);
  for (let y = 0; y < c.height; y++) for (let x = 0; x < c.width; x++) {
    if (d[(y * c.width + x) * 4 + 3] > 40) grid[Math.floor(y * rows / c.height) * cols + Math.floor(x * cols / c.width)]++;
  }
  const cell = (c.width / cols) * (c.height / rows);
  return { left: c.getBoundingClientRect().left, grid: grid.map((n) => n / cell) };
}).sort((a, b) => a.left - b.left).map((p) => p.grid);

const made = faces(PRESETS);
const html = await faceHtml();
await mkdir(OUT, { recursive: true });
const site = await serve();
const browser = await chromium.launch({ args: ['--mute-audio'] });
const context = await browser.newContext({ viewport: { width: 1500, height: 900 }, deviceScaleFactor: 2 });
await context.addInitScript(MUTE);
const page = await context.newPage();
await page.goto(site.url);
await page.waitForFunction(() => document.querySelectorAll('#presets .preset').length > 20 && !document.querySelector('#presets .preset[disabled]'), null, { timeout: 180000 });
const window_ = await context.newPage();

let wrong = 0;
for (const id of PRESETS) {
  const { name, face } = made[id];
  await page.locator('#presets .preset').filter({ has: page.locator('b', { hasText: new RegExp(`^${name}$`) }) }).first().click();
  await page.waitForFunction((n) => document.querySelectorAll('#device .dv-widget').length === n, face.items.filter((i) => i.type === 'dial').length, { timeout: 60000 });
  await page.waitForTimeout(600);
  const onPage = await page.evaluate(MEASURE, { panel: '.dv-panel', label: '.dv-label', dial: '.dv-widget', graph: '.dv-graph' });
  const pageInk = await page.evaluate(INK, '#device .dv-graph canvas');
  await page.locator('#device').screenshot({ path: path.join(OUT, `${id}-page.png`) });

  await window_.setViewportSize({ width: Math.round(face.width * 2), height: Math.round(face.height * 2) });
  await window_.setContent(html);
  await window_.evaluate((f) => window.ppFace(f), face);
  await window_.evaluate(() => document.fonts.ready);
  await window_.waitForTimeout(200);
  const inWindow = await window_.evaluate(MEASURE, { panel: '.panel', label: '.label', dial: '.dial', graph: '.graph' });
  const windowInk = await window_.evaluate(INK, '#device .graph canvas');
  await window_.screenshot({ path: path.join(OUT, `${id}-window.png`) });

  const problems = [];
  if (onPage.width !== inWindow.width || onPage.height !== inWindow.height) problems.push(`size ${onPage.width}x${onPage.height} on the page, ${inWindow.width}x${inWindow.height} in the window`);
  if (onPage.items.length !== inWindow.items.length) problems.push(`${onPage.items.length} things on the page, ${inWindow.items.length} in the window`);
  let far = 0;
  for (let i = 0; i < Math.min(onPage.items.length, inWindow.items.length); i++) {
    const a = onPage.items[i], b = inWindow.items[i];
    if (a[0] !== b[0] || a[5] !== b[5]) { problems.push(`${a[0]} "${a[5]}" on the page is ${b[0]} "${b[5]}" in the window`); continue; }
    far = Math.max(far, ...[1, 2, 3, 4].map((k) => Math.abs(a[k] - b[k])));
  }
  if (far > 0.75) problems.push(`something is ${far.toFixed(2)} px from where the page has it`);
  let inkOff = 0;
  if (pageInk.length !== windowInk.length) problems.push(`${pageInk.length} pictures on the page, ${windowInk.length} in the window`);
  else pageInk.forEach((grid, n) => { inkOff = Math.max(inkOff, grid.reduce((sum, v, i) => sum + Math.abs(v - windowInk[n][i]), 0) / grid.length); });
  if (inkOff > 0.03) problems.push(`a picture differs: ${(inkOff * 100).toFixed(1)}% of its ink`);
  const counts = ['panel', 'label', 'dial', 'graph'].map((k) => `${onPage.items.filter((i) => i[0] === k).length} ${k}s`).join(', ');
  console.log(`  ${problems.length ? 'FAIL' : 'ok  '}  ${name}: ${counts}; furthest ${far.toFixed(2)} px; pictures differ by ${(inkOff * 100).toFixed(2)}%`);
  for (const p of problems) console.log(`          ${p}`);
  wrong += problems.length;
}
await browser.close();
site.close();
console.log(wrong ? `\n${wrong} differences` : '\nthe window is the page');
process.exit(wrong ? 1 : 0);
