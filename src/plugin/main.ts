// The plugin generator page.
//
// One loop runs the whole thing:
//
//   settings ──▶ fxgen.py (maxpylang, in Pyodide) ──▶ patcher JSON ──┬─▶ engine (sound)
//       ▲                                                           ├─▶ device view
//       │                                                           └─▶ .amxd download
//       └──────────── a dial turned in the device view ◀────────────────┘
//
// The left panel edits STRUCTURE (which modules, in what order, their modes) and every
// such edit regenerates the patch. The dials in the device view edit VALUES; they act on
// the running patch directly, and are written back into the settings so the next build,
// the shared link and the downloaded file all carry them.

import '../objects'; // side effect: registers every object the engine can play
import './plugin.css';
import { Engine } from '../engine/engine';
import type { MaxNode } from '../engine/registry';
import { parseMaxPat } from '../parser/maxpat';
import { preloadWorklets } from '../runtime/worklet';
import { firstNum } from '../runtime/atoms';
import { Track } from './audio';
import { DEVICE_HEIGHT, highlightPython, renderDevice, renderPatch } from './device-view';
import { Keyboard, PHRASES, Sequencer } from './keys';
import {
  SCHEMA, decodeSettings, dialIndex, encodeSettings, kindOf, moduleSpec, normalize, presetsOf, valuesOf,
  type ChainEntry, type Kind, type ModuleSpec, type Preset, type Section, type Settings,
} from './model';
import { PyRuntime, base64ToBytes, type RunResult } from './python';
import { SOUNDS, findSound } from './sounds';
import { getTheme, setTheme } from './theme';

const $ = <T extends HTMLElement = HTMLElement>(id: string): T => document.getElementById(id) as T;

/** One line per workaround the generated script carries; the page links each to the log. */
const GAPS: Record<string, string> = {
  'M4L-01': 'A dial’s range, unit and start value cannot be set through edit()',
  'M4L-02': 'Presentation mode has no API, and edit() rejects it on some objects',
  'M4L-03': 'A comment’s text is saved with the object name in front of it',
  'M4L-04': 'Patcher settings (open in presentation, device width) have no API',
};
const GAP_LOG = 'https://github.com/Barnard-PL-Labs/MaxPyLang/blob/main/docs/type-system/m4l-gaps.md';

// ── state ───────────────────────────────────────────────────────────────────────

let activePreset: string | undefined;
let result: RunResult | undefined;
let buildSeq = 0;
let view: 'device' | 'patch' = 'device';
let sampleId = 'piano';
let customBuffer: AudioBuffer | undefined;
let dialOffs: (() => void)[] = [];
let deviceWidth = 0;
/** The running device's notein, when it is an instrument: where played notes are sent. */
let noteIn: MaxNode | undefined;
/** The device of the kind not on screen, kept for when its tab is chosen again. */
const parked: Partial<Record<Kind, { settings: Settings; preset: string | undefined }>> = {};
/** Ready-made plugin builds, by preset id and system, if this site was published with any. */
let builds: Record<string, Record<string, string>> = {};
// Last, because choosing the starting device also sets the preset and sample above.
let settings: Settings = initialSettings();

const kind = (): Kind => kindOf(settings);

const engine = new Engine();
const track = new Track(engine.ctx as AudioContext);
const worklets = preloadWorklets(engine.ctx);
const dials = dialIndex();
const python = new PyRuntime((message) => setStatus('loading', capitalize(message.replace(/…$/, ''))));

function initialSettings(): Settings {
  const fragment = location.hash.match(/^#s=(.+)$/)?.[1];
  const shared = fragment ? decodeSettings(fragment) : undefined;
  if (shared) return shared;
  return firstPreset('effect');
}

/** The device a tab opens with: its first preset. */
function firstPreset(which: Kind): Settings {
  const first = presetsOf(which)[0];
  activePreset = first.id;
  if (first.sample) sampleId = first.sample;
  return normalize(first.settings);
}

const capitalize = (s: string): string => (s ? s[0].toUpperCase() + s.slice(1) : s);

// ── status, toast ───────────────────────────────────────────────────────────────

function setStatus(state: 'loading' | 'building' | 'ready' | 'error', text: string): void {
  $('status').dataset.state = state;
  $('status-text').textContent = text;
  if (state === 'loading') $('boot-text').textContent = text;
}

let toastTimer = 0;
function toast(message: string): void {
  const el = $('toast');
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => el.classList.remove('show'), 2600);
}

// ── building ────────────────────────────────────────────────────────────────────

let buildTimer = 0;
/** Regenerate the device. `delay` coalesces bursts (typing a name, dragging a module). */
function scheduleBuild(delay = 0): void {
  clearTimeout(buildTimer);
  clearTimeout(scriptTimer);
  buildTimer = window.setTimeout(() => void build(), delay);
  syncHash();
}

async function build(): Promise<void> {
  const seq = ++buildSeq;
  try {
    await python.ready;
    if (seq !== buildSeq) return;
    setStatus('building', 'Building');
    const next = await python.run(settings, false);
    if (seq !== buildSeq) return; // a newer edit is already on its way
    await worklets;
    await apply(next);
    setStatus('ready', 'Python ready');
  } catch (err) {
    if (seq !== buildSeq) return;
    console.error(err);
    setStatus('error', 'Build failed');
    showCheckError(String((err as Error).message || err));
  }
}

async function apply(next: RunResult): Promise<void> {
  result = next;
  const patch = parseMaxPat(next.json);
  for (const off of dialOffs) off();
  dialOffs = [];

  const report = await track.duck(() => engine.build(patch));
  const noteInId = patch.nodes.find((n) => n.className === 'notein')?.id;
  noteIn = noteInId ? report.built.get(noteInId) : undefined;
  keyboard.forget(); // the voices are new: nothing is sounding

  // Listen to each dial, so a knob turned here becomes the value that gets saved.
  for (const node of patch.nodes) {
    if (node.maxclass !== 'live.dial') continue;
    const target = dials.get(String(node.raw?.varname ?? ''));
    const live = report.built.get(node.id);
    if (!target || !live?.onControlOut) continue;
    const off = live.onControlOut(0, (m) => {
      const v = firstNum(m);
      if (v !== undefined) dialMoved(target.module, target.param, v);
    });
    if (typeof off === 'function') dialOffs.push(off);
  }

  deviceWidth = renderDevice($('device'), next.json, report.built as ReadonlyMap<string, MaxNode>);
  const hidden = renderPatch($('patch-wrap'), patch);
  $('patch-wrap').dataset.hidden = String(hidden);
  fitDevice();

  $('boot').hidden = true;
  $<HTMLButtonElement>('btn-download').disabled = false;
  renderCheck(next);
  renderCode(next);
  renderHead();
}

let scriptTimer = 0;
function dialMoved(module: string, param: string, value: number): void {
  const store = valuesOf(settings, module);
  if (!store || Math.abs((store[param] ?? NaN) - value) < 1e-9) return;
  store[param] = value;
  markCustom();
  syncHash();
  // The sound already changed. Only the script and the check panel are stale, so refresh
  // those once the knob has come to rest, without touching the running patch.
  clearTimeout(scriptTimer);
  scriptTimer = window.setTimeout(() => void refreshScript(), 450);
}

async function refreshScript(): Promise<void> {
  const seq = buildSeq;
  try {
    const next = await python.run(settings, false);
    if (seq !== buildSeq) return;
    result = next;
    renderCode(next);
  } catch (err) {
    console.error(err);
  }
}

let hashTimer = 0;
function syncHash(): void {
  clearTimeout(hashTimer);
  hashTimer = window.setTimeout(() => {
    history.replaceState(null, '', `#s=${encodeSettings(settings)}`);
  }, 300);
}

// ── head, check panel, code ─────────────────────────────────────────────────────

function renderHead(): void {
  $('device-title').textContent = settings.name;
  const on = settings.chain.filter((e) => e.on).map((e) => moduleSpec(e.module)?.label ?? e.module);
  const voices = settings.instrument?.osc.options.voices;
  const source = voices ? ['notein', voices === '1' ? 'Synth, one voice' : `Synth, ${voices} voices`] : ['plugin~'];
  const flow = $('device-flow');
  flow.replaceChildren();
  for (const [i, part] of [...source, ...on, 'Output', 'plugout~'].entries()) {
    if (i) flow.append(Object.assign(document.createElement('i'), { textContent: '→' }));
    const io = part.endsWith('~') || part === 'notein';
    flow.append(Object.assign(document.createElement('span'), { textContent: part, className: io ? 'io' : '' }));
  }
}

function stat(value: string | number, label: string, tone = ''): HTMLElement {
  const el = document.createElement('div');
  el.className = `stat ${tone}`;
  el.append(
    Object.assign(document.createElement('b'), { textContent: String(value) }),
    Object.assign(document.createElement('span'), { textContent: label }),
  );
  return el;
}

function renderCheck(r: RunResult): void {
  const host = $('check');
  host.replaceChildren();
  const cords = r.coverage.cords;
  const objects = r.coverage.objects;
  const stats = document.createElement('div');
  stats.className = 'stats';
  if (cords && objects) {
    stats.append(
      stat(objects.total, 'objects'),
      stat(cords.total, 'cords'),
      stat(cords.checked, 'cords checked', cords.checked === cords.total ? 'good' : ''),
      stat(cords.flagged, 'flagged', cords.flagged ? 'bad' : 'good'),
      stat(cords.not_checked, 'not checked', cords.not_checked ? 'warn' : 'good'),
    );
  }
  host.append(stats);

  const lines: string[] = [];
  for (const w of r.warnings) lines.push(`${w.category}: ${w.message}`);
  // A reason arrives as [object, why, how many cords]. The same object class usually
  // appears once per channel, so the lines are merged by class.
  const unjudged = new Map<string, number>();
  for (const reason of cords?.not_checked_because ?? []) {
    const [what, why, count] = Array.isArray(reason) ? reason : [String(reason), '', 1];
    const key = `${String(what).replace(/ \(obj-\d+\)$/, '')}: ${String(why)}`;
    unjudged.set(key, (unjudged.get(key) ?? 0) + (Number(count) || 1));
  }
  for (const [key, count] of unjudged) lines.push(`not checked, ${count} cord${count === 1 ? '' : 's'} from ${key}`);
  for (const name of objects?.not_seen_into ?? []) lines.push(`cannot see into: ${name}`);
  if (lines.length) {
    const ul = document.createElement('ul');
    ul.className = 'check-lines';
    for (const line of lines) ul.append(Object.assign(document.createElement('li'), { textContent: line }));
    host.append(ul);
  }

  // What the checks did NOT judge because maxpylang cannot express it yet: every
  // workaround in the script names its gap, so the list is read off the script itself.
  const ids = [...new Set([...r.script.matchAll(/gap (M4L-\d+)/g)].map((m) => m[1]))].sort();
  if (ids.length) {
    const gaps = document.createElement('details');
    gaps.className = 'gaps';
    const head = document.createElement('summary');
    head.innerHTML = `<b>${ids.length} things this device needs that MaxPyLang cannot say yet.</b> The script works around each one, and each is in the <a href="${GAP_LOG}" target="_blank" rel="noopener">gap log</a>.`;
    const ul = document.createElement('ul');
    for (const id of ids) {
      const li = document.createElement('li');
      li.append(Object.assign(document.createElement('code'), { textContent: id }), ` ${GAPS[id] ?? ''}`);
      ul.append(li);
    }
    gaps.append(head, ul);
    host.append(gaps);
  }
}

function showCheckError(message: string): void {
  const host = $('check');
  host.replaceChildren();
  const pre = document.createElement('pre');
  pre.className = 'check-error';
  pre.textContent = message;
  host.append(Object.assign(document.createElement('p'), { textContent: 'Python raised an error while building this device:', className: 'bad' }), pre);
  // The check lives in the Python drawer, so an error opens it.
  setDrawer(true);
}

function renderCode(r: RunResult): void {
  $('code').innerHTML = highlightPython(r.script);
  const lines = r.script.split('\n').length;
  $('py-meta').textContent = `${lines} lines · writes ${r.filename}`;
}

// ── device stage ────────────────────────────────────────────────────────────────

const NARROW = window.matchMedia('(max-width: 980px)');

function fitDevice(): void {
  if (!deviceWidth) return;
  const wrap = $('device-wrap');
  const room = wrap.clientWidth - 48;
  // On a wide screen the stage has a fixed height (all the column has left), so the
  // device must fit it both ways. Stacked on a narrow one the stage grows with the
  // device, and only the width limits it. 128 is the wrapper's vertical padding.
  const tall = NARROW.matches ? Infinity : (wrap.clientHeight - 128) / DEVICE_HEIGHT;
  const zoom = Math.max(0.62, Math.min(2.4, room / deviceWidth, tall));
  const device = $('device');
  device.style.transform = `scale(${zoom})`;
  // A transform does not change layout size, so the wrapper is sized by hand.
  const frame = device.parentElement as HTMLElement;
  frame.style.setProperty('--dw', `${deviceWidth * zoom}px`);
  frame.style.setProperty('--dh', `${DEVICE_HEIGHT * zoom}px`);
}

function setView(next: 'device' | 'patch'): void {
  view = next;
  $('stage').dataset.view = next;
  $('view-device').setAttribute('aria-selected', String(next === 'device'));
  $('view-patch').setAttribute('aria-selected', String(next === 'patch'));
  $('device-wrap').hidden = next !== 'device';
  $('patch-wrap').hidden = next !== 'patch';
  if (next === 'device') fitDevice();
}

// ── left panel ──────────────────────────────────────────────────────────────────

function markCustom(): void {
  if (!activePreset) return;
  activePreset = undefined;
  for (const el of document.querySelectorAll('.preset.is-active')) el.classList.remove('is-active');
  renderDownloads();
}

/** The preset sections of the tab on screen, in file order, as the left panel lists them. */
const sections = (): string[] => [...new Set(presetsOf(kind()).map((p) => p.group))];
let section = 'All';

function renderSections(): void {
  const host = $('sections');
  host.replaceChildren();
  // 'Start' holds only the empty device; it is listed under All and needs no chip.
  for (const name of ['All', ...sections().filter((g) => g !== 'Start')]) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'section-chip';
    b.textContent = name;
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', String(name === section));
    b.addEventListener('click', () => {
      section = name;
      renderSections();
      renderPresets();
      $('presets').scrollIntoView({ block: 'nearest' });
    });
    host.append(b);
  }
}

function renderPresets(): void {
  const host = $('presets');
  host.replaceChildren();
  for (const group of sections()) {
    if (section !== 'All' && section !== group) continue;
    const list = presetsOf(kind()).filter((p) => p.group === group);
    const head = document.createElement('h3');
    head.append(group, Object.assign(document.createElement('small'), { textContent: String(list.length) }));
    host.append(head);
    const rows = document.createElement('div');
    rows.className = 'preset-list';
    for (const p of list) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = `preset${p.id === activePreset ? ' is-active' : ''}`;
      b.style.setProperty('--dot', p.settings.accent ?? SCHEMA.accents[0]);
      b.title = p.tag;
      b.append(
        Object.assign(document.createElement('b'), { textContent: p.name }),
        Object.assign(document.createElement('span'), { textContent: p.tag }),
      );
      b.addEventListener('click', () => loadPreset(p));
      rows.append(b);
    }
    host.append(rows);
  }
}

function loadPreset(p: Preset): void {
  settings = normalize(p.settings);
  activePreset = p.id;
  if (p.sample && !customBuffer) void chooseSample(p.sample);
  renderSide();
  renderDownloads();
  scheduleBuild();
}

function renderAccents(): void {
  const host = $('accents');
  host.replaceChildren();
  for (const colour of SCHEMA.accents) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'accent';
    b.style.background = colour;
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', String(colour === settings.accent));
    b.setAttribute('aria-label', colour);
    b.addEventListener('click', () => {
      settings.accent = colour;
      markCustom();
      applyAccent();
      renderAccents();
      scheduleBuild();
    });
    host.append(b);
  }
}

function applyAccent(): void {
  document.documentElement.style.setProperty('--accent', settings.accent);
}

let dragId: string | undefined;

function renderChain(): void {
  const host = $('chain');
  host.replaceChildren();
  settings.chain.forEach((entry, index) => host.append(chainItem(entry, index)));
}

/** One row of choices per option of a module ("Type: Chorus | Flanger"). */
function optionRows(mod: ModuleSpec, chosen: Record<string, string>, redraw: () => void): HTMLElement[] {
  return mod.options.map((opt) => {
    const seg = document.createElement('div');
    seg.className = 'seg';
    seg.setAttribute('role', 'radiogroup');
    seg.setAttribute('aria-label', `${mod.label} ${opt.label}`);
    for (const choice of opt.choices) {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = choice.label;
      b.setAttribute('role', 'radio');
      b.setAttribute('aria-checked', String(chosen[opt.id] === choice.id));
      b.addEventListener('click', () => {
        if (chosen[opt.id] === choice.id) return;
        chosen[opt.id] = choice.id;
        markCustom();
        redraw();
        renderHead();
        scheduleBuild();
      });
      seg.append(b);
    }
    return seg;
  });
}

/** The synth's sections. Unlike the chain they are always on and always in this order. */
function renderSynth(): void {
  const host = $('synth');
  host.replaceChildren();
  for (const mod of SCHEMA.instrument.modules) {
    const section: Section | undefined = settings.instrument?.[mod.id];
    if (!section) continue;
    const li = document.createElement('li');
    li.className = 'mod is-on is-fixed';
    const name = document.createElement('div');
    name.className = 'mod-name';
    name.append(
      Object.assign(document.createElement('b'), { textContent: mod.label }),
      Object.assign(document.createElement('span'), { textContent: mod.params.map((p) => p.label).join(' · ') }),
    );
    const row = document.createElement('div');
    row.className = 'mod-row';
    row.append(name);
    const body = document.createElement('div');
    body.className = 'mod-body';
    if (mod.blurb) body.append(Object.assign(document.createElement('p'), { textContent: mod.blurb }));
    body.append(...optionRows(mod, section.options, renderSynth));
    li.append(row, body);
    host.append(li);
  }
}

function chainItem(entry: ChainEntry, index: number): HTMLElement {
  const mod = moduleSpec(entry.module)!;
  const li = document.createElement('li');
  li.className = `mod${entry.on ? ' is-on' : ''}`;
  li.dataset.id = entry.module;
  li.draggable = true;

  const row = document.createElement('div');
  row.className = 'mod-row';

  const grip = document.createElement('span');
  grip.className = 'grip';
  grip.title = 'Drag to reorder';
  grip.innerHTML = '<svg viewBox="0 0 10 16" width="10" height="16" aria-hidden="true"><g fill="currentColor"><circle cx="3" cy="4" r="1.1"/><circle cx="7" cy="4" r="1.1"/><circle cx="3" cy="8" r="1.1"/><circle cx="7" cy="8" r="1.1"/><circle cx="3" cy="12" r="1.1"/><circle cx="7" cy="12" r="1.1"/></g></svg>';

  const name = document.createElement('div');
  name.className = 'mod-name';
  name.append(
    Object.assign(document.createElement('b'), { textContent: mod.label }),
    Object.assign(document.createElement('span'), { textContent: mod.params.map((p) => p.label).join(' · ') }),
  );

  const move = document.createElement('div');
  move.className = 'mod-move';
  for (const [dir, glyph, text] of [[-1, 'M3 9l4-4 4 4', 'Move up'], [1, 'M3 5l4 4 4-4', 'Move down']] as const) {
    const b = document.createElement('button');
    b.type = 'button';
    b.title = `${text}: ${mod.label}`;
    b.setAttribute('aria-label', `${text}: ${mod.label}`);
    b.disabled = index + dir < 0 || index + dir >= settings.chain.length;
    b.innerHTML = `<svg viewBox="0 0 14 14" width="12" height="12" aria-hidden="true"><path d="${glyph}" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    b.addEventListener('click', () => moveModule(entry.module, index + dir));
    move.append(b);
  }

  const sw = document.createElement('button');
  sw.type = 'button';
  sw.className = 'switch';
  sw.setAttribute('role', 'switch');
  sw.setAttribute('aria-checked', String(entry.on));
  sw.setAttribute('aria-label', `${mod.label} on`);
  sw.addEventListener('click', () => {
    entry.on = !entry.on;
    markCustom();
    renderChain();
    scheduleBuild();
  });

  row.append(grip, name, move, sw);
  li.append(row);

  if (entry.on) {
    const body = document.createElement('div');
    body.className = 'mod-body';
    if (mod.blurb) body.append(Object.assign(document.createElement('p'), { textContent: mod.blurb }));
    body.append(...optionRows(mod, entry.options, renderChain));
    li.append(body);
  }

  li.addEventListener('dragstart', (e) => {
    dragId = entry.module;
    li.classList.add('is-dragging');
    e.dataTransfer?.setData('text/plain', entry.module);
    if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
  });
  li.addEventListener('dragend', () => {
    dragId = undefined;
    for (const el of document.querySelectorAll('.mod')) el.classList.remove('is-dragging', 'drop-before', 'drop-after');
  });
  li.addEventListener('dragover', (e) => {
    if (!dragId || dragId === entry.module) return;
    e.preventDefault();
    const box = li.getBoundingClientRect();
    const after = e.clientY > box.top + box.height / 2;
    li.classList.toggle('drop-before', !after);
    li.classList.toggle('drop-after', after);
  });
  li.addEventListener('dragleave', () => li.classList.remove('drop-before', 'drop-after'));
  li.addEventListener('drop', (e) => {
    if (!dragId || dragId === entry.module) return;
    e.preventDefault();
    const after = li.classList.contains('drop-after');
    const from = settings.chain.findIndex((c) => c.module === dragId);
    let to = settings.chain.findIndex((c) => c.module === entry.module) + (after ? 1 : 0);
    if (from < to) to--;
    moveModule(dragId, to);
  });
  return li;
}

function moveModule(id: string, to: number): void {
  const from = settings.chain.findIndex((c) => c.module === id);
  if (from < 0 || to < 0 || to >= settings.chain.length || from === to) return;
  const [entry] = settings.chain.splice(from, 1);
  settings.chain.splice(to, 0, entry);
  markCustom();
  renderChain();
  // Order only matters to the sound between modules that are switched on, but the
  // script lists them in this order either way, so rebuild regardless.
  scheduleBuild();
}

function setTab(tab: 'presets' | 'build'): void {
  for (const id of ['presets', 'build'] as const) {
    $(`tab-${id}`).setAttribute('aria-selected', String(id === tab));
    $(`pane-${id}`).hidden = id !== tab;
  }
}

function renderSide(): void {
  renderSections();
  renderPresets();
  $<HTMLInputElement>('name').value = settings.name;
  applyAccent();
  renderAccents();
  renderSynth();
  renderChain();
  renderHead();
}

// ── effect or instrument ────────────────────────────────────────────────────────

/** Show the page for the kind of device in `settings`: its tab, its controls, its way of being played. */
function renderKind(): void {
  const instrument = kind() === 'instrument';
  $('mode-effect').setAttribute('aria-selected', String(!instrument));
  $('mode-instrument').setAttribute('aria-selected', String(instrument));
  document.body.dataset.kind = kind();
  $('synth-group').hidden = !instrument;
  $('play-keys').hidden = !instrument;
  $('play-sample').hidden = instrument;
  $('btn-bypass').hidden = instrument;
  $('btn-play').title = instrument ? 'Loop a phrase, so your hands are free for the dials' : '';
}

/** Switch tabs. Each kind keeps its own device, so coming back finds it as it was left. */
async function setKind(next: Kind): Promise<void> {
  if (next === kind()) return;
  await setPlaying(false);
  await quiet();
  parked[kind()] = { settings, preset: activePreset };
  const back = parked[next];
  if (back) {
    settings = back.settings;
    activePreset = back.preset;
  } else {
    settings = firstPreset(next);
  }
  section = 'All';
  if (next === 'instrument') track.clearBuffer(); // an instrument is played, not fed a sample
  else void chooseSample(sampleId);
  renderKind();
  renderSide();
  renderDownloads();
  scheduleBuild();
}

// ── playing an instrument ───────────────────────────────────────────────────────

/** A note for the running instrument. Velocity 0 lets the key go. Starts the audio if it is off. */
function sendNote(key: number, velocity: number): void {
  if (kind() !== 'instrument') return;
  const send = (): void => noteIn?.controlIns?.[0]?.([key, velocity, 1]);
  if (track.playing) send();
  else if (velocity > 0) void listen().then(send);
}

const keyboard = new Keyboard($('piano'), sendNote);
const sequencer = new Sequencer(keyboard);

/** Switch the audio on, without the play button: enough for a key to be heard. */
async function listen(): Promise<void> {
  await engine.start();
  track.play();
  startScope();
}

/** Let go of every key and switch the audio off. */
async function quiet(): Promise<void> {
  keyboard.releaseAll();
  track.stop();
  await engine.stop();
}

// ── transport ───────────────────────────────────────────────────────────────────

const PLAYING_ICON = '<svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true"><path d="M5 3.2v9.6L12.6 8z" fill="currentColor"/></svg>';

/** The sound library: every sound under its group, and the button that names the one playing. */
function renderLibrary(): void {
  const current = customBuffer ? undefined : findSound(sampleId);
  $('sound-name').textContent = current?.label ?? 'Library';
  $('btn-library').classList.toggle('is-active', !!current);
  document.querySelector('.chip.upload')?.classList.toggle('is-active', !!customBuffer);

  const host = $('sounds');
  host.replaceChildren();
  for (const group of new Set(SOUNDS.map((s) => s.group))) {
    const heading = document.createElement('h3');
    heading.textContent = group;
    const grid = document.createElement('div');
    grid.className = 'sound-grid';
    for (const sound of SOUNDS.filter((s) => s.group === group)) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'sound';
      b.dataset.sound = sound.id;
      b.setAttribute('role', 'radio');
      b.setAttribute('aria-checked', String(sound === current));
      b.innerHTML = `<i>${PLAYING_ICON}</i><b></b><span></span>`;
      b.querySelector('b')!.textContent = sound.label;
      b.querySelector('span')!.textContent = sound.blurb;
      // Trying a sound is one click: it is chosen and it plays. The library stays open
      // so the next one is one click too.
      b.addEventListener('click', () => {
        customBuffer = undefined;
        $('file-name').textContent = 'Your audio';
        void chooseSample(sound.id).then(() => { if (!track.playing) void setPlaying(true); });
      });
      grid.append(b);
    }
    host.append(heading, grid);
  }
}

async function chooseSample(id: string): Promise<void> {
  const sound = findSound(id);
  if (!sound) return;
  sampleId = id;
  renderLibrary();
  try {
    const buffer = await track.load(sound);
    if (sampleId === id && !customBuffer) track.setBuffer(buffer);
  } catch (err) {
    toast(String((err as Error).message || err));
  }
}

async function useFile(file: File): Promise<void> {
  try {
    const buffer = await track.decode(await file.arrayBuffer());
    customBuffer = buffer;
    $('file-name').textContent = file.name.length > 22 ? `${file.name.slice(0, 20)}…` : file.name;
    renderLibrary();
    track.setBuffer(buffer);
    if (!track.playing) await setPlaying(true);
  } catch {
    toast('That file could not be decoded as audio.');
  }
}

/**
 * The play button. For an effect it plays the chosen sound through the device. For an
 * instrument it loops the chosen phrase; stopping the loop leaves the audio on, so the
 * last notes ring out and the keys stay playable.
 */
async function setPlaying(on: boolean): Promise<void> {
  const btn = $('btn-play');
  if (kind() === 'instrument') {
    if (on) {
      await listen();
      sequencer.start(PHRASES.find((p) => p.id === $<HTMLSelectElement>('phrase').value) ?? PHRASES[0]);
    } else {
      sequencer.stop();
    }
  } else if (on) {
    await listen();
  } else {
    await quiet();
  }
  btn.setAttribute('aria-pressed', String(on));
  btn.setAttribute('aria-label', on ? 'Stop' : 'Play');
  document.body.classList.toggle('is-playing', on);
}

// ── scope + meter ───────────────────────────────────────────────────────────────

let scopeRunning = false;
function startScope(): void {
  if (scopeRunning) return;
  scopeRunning = true;
  const canvas = $<HTMLCanvasElement>('scope');
  const g = canvas.getContext('2d')!;
  const bins = new Uint8Array(new ArrayBuffer(track.analyser.frequencyBinCount));
  const meterL = $('meter-l');
  const meterR = $('meter-r');
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  let peaks: [number, number] = [0, 0];

  const frame = (): void => {
    if (!track.playing) {
      scopeRunning = false;
      g.clearRect(0, 0, canvas.width, canvas.height);
      meterL.style.setProperty('--level', '0');
      meterR.style.setProperty('--level', '0');
      return;
    }
    requestAnimationFrame(frame);

    const [l, r] = track.levels();
    peaks = [Math.max(l, peaks[0] * 0.9), Math.max(r, peaks[1] * 0.9)];
    const toBar = (p: number): string => String(Math.max(0, Math.min(1, 1 + (20 * Math.log10(p || 1e-6)) / 48)));
    meterL.style.setProperty('--level', toBar(peaks[0]));
    meterR.style.setProperty('--level', toBar(peaks[1]));
    meterL.classList.toggle('clip', l >= 0.999);
    meterR.classList.toggle('clip', r >= 0.999);

    if (reduce || view !== 'device') {
      g.clearRect(0, 0, canvas.width, canvas.height);
      return;
    }
    const dpr = Math.min(2, devicePixelRatio || 1);
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    track.analyser.getByteFrequencyData(bins);

    // A log-frequency spectrum from 40 Hz to 16 kHz, filled up from the stage floor.
    const nyquist = engine.ctx.sampleRate / 2;
    const lo = Math.log(40);
    const hi = Math.log(16000);
    const steps = 96;
    g.beginPath();
    g.moveTo(0, h);
    for (let i = 0; i <= steps; i++) {
      const f = Math.exp(lo + ((hi - lo) * i) / steps);
      const bin = Math.min(bins.length - 1, Math.round((f / nyquist) * bins.length));
      const y = h - (bins[bin] / 255) ** 1.5 * h * 0.92;
      g.lineTo((i / steps) * w, y);
    }
    g.lineTo(w, h);
    g.closePath();
    const fill = g.createLinearGradient(0, 0, 0, h);
    fill.addColorStop(0, `${settings.accent}88`);
    fill.addColorStop(1, `${settings.accent}00`);
    g.fillStyle = fill;
    g.fill();
  };
  requestAnimationFrame(frame);
}

// ── downloads, drawer, sharing ──────────────────────────────────────────────────

function save(bytes: BlobPart, filename: string, type: string): void {
  const url = URL.createObjectURL(new Blob([bytes], { type }));
  const a = Object.assign(document.createElement('a'), { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

async function download(): Promise<void> {
  const btn = $<HTMLButtonElement>('btn-download');
  btn.disabled = true;
  btn.classList.add('is-busy');
  try {
    // Built fresh, from the settings as they are this instant, so every dial is saved
    // where it currently stands.
    const out = await python.run(settings, true);
    if (!out.amxd) throw new Error('the generator returned no device');
    save(base64ToBytes(out.amxd), out.filename, 'application/octet-stream');
    result = out;
    renderCode(out);
    toast(`Saved ${out.filename}. Drag it onto ${kind() === 'instrument' ? 'a MIDI' : 'an audio'} track in Live.`);
  } catch (err) {
    console.error(err);
    toast('The download failed. The error is under View Python.');
    showCheckError(String((err as Error).message || err));
  } finally {
    btn.disabled = false;
    btn.classList.remove('is-busy');
  }
}

/** A system's name for a ready-made build, and the label its link gets. */
const SYSTEMS: [id: string, label: string][] = [['macos', 'macOS'], ['windows', 'Windows'], ['linux', 'Linux']];

/**
 * The plugin half of the download menu. A plugin is compiled code, so the page cannot
 * make one itself. An untouched preset may have builds published with the site; anything
 * else is built on the visitor's machine from the settings, with one command.
 */
function renderDownloads(): void {
  const ready = activePreset ? builds[activePreset] : undefined;
  const links = $('dl-builds');
  links.replaceChildren();
  links.hidden = !ready;
  for (const [id, label] of SYSTEMS) {
    if (!ready?.[id]) continue;
    const a = Object.assign(document.createElement('a'), { className: 'btn', textContent: label, href: ready[id] });
    a.setAttribute('download', '');
    links.append(a);
  }
  const file = `${settings.name.replace(/ /g, '_')}.json`;
  $('dl-how').textContent = ready
    ? 'Ready-made builds of this preset, unsigned. To build your own version, with your changes, save the settings and run this in a copy of the project:'
    : 'A plugin is compiled code, so it is built on your machine. Save the settings and run this in a copy of the project:';
  $('dl-command').textContent = `npm run plugin -- --settings ${file} --install`;
}

function saveSettings(): void {
  save(JSON.stringify(settings, null, 1), `${settings.name.replace(/ /g, '_')}.json`, 'application/json');
  toast('Saved the settings. Build the plugin with the command shown.');
}

function setDrawer(open: boolean): void {
  $('drawer').classList.toggle('open', open);
  $('drawer').setAttribute('aria-hidden', String(!open));
  $('btn-python').setAttribute('aria-expanded', String(open));
  $('scrim').hidden = !open;
  if (open) $('drawer-close').focus();
  else $('btn-python').focus();
}

async function copy(text: string, done: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    toast(done);
  } catch {
    toast('Copying was blocked by the browser.');
  }
}

// ── wiring ──────────────────────────────────────────────────────────────────────

/**
 * A button that opens the panel beside it. A click on the button toggles it; a click
 * anywhere outside closes it. Returns the function that closes it (for Escape).
 */
function popover(rootId: string, buttonId: string): () => void {
  const root = $(rootId);
  const button = $(buttonId);
  const set = (open: boolean): void => {
    root.classList.toggle('open', open);
    button.setAttribute('aria-expanded', String(open));
  };
  button.addEventListener('click', () => set(!root.classList.contains('open')));
  document.addEventListener('pointerdown', (e) => {
    if (!root.contains(e.target as Node)) set(false);
  });
  return () => set(false);
}

function init(): void {
  renderKind();
  renderSide();
  renderLibrary();
  renderDownloads();
  setView('device');

  $('mode-effect').addEventListener('click', () => void setKind('effect'));
  $('mode-instrument').addEventListener('click', () => void setKind('instrument'));

  // Playing an instrument: the phrase the play button loops, a MIDI keyboard, and A S D F.
  const phrase = $<HTMLSelectElement>('phrase');
  for (const p of PHRASES) phrase.append(Object.assign(document.createElement('option'), { value: p.id, textContent: p.label }));
  phrase.addEventListener('change', () => { if (sequencer.playing) void setPlaying(true); });
  $('btn-midi').addEventListener('click', () => {
    keyboard.connectMidi().then(
      (names) => {
        $('btn-midi').classList.toggle('is-active', names.length > 0);
        toast(names.length ? `Listening to ${names.join(', ')}.` : 'No MIDI keyboard found. Plug one in and press MIDI again.');
      },
      () => toast('This browser would not open MIDI. Chrome and Edge can, if you allow it.'),
    );
  });
  keyboard.listenToComputerKeys(() => kind() === 'instrument');

  // Ready-made plugin builds, if the site was published with them (.github/workflows/pages.yml).
  fetch(`${import.meta.env.BASE_URL}downloads/index.json`)
    .then((r) => (r.ok ? r.json() : {}))
    .then((found: typeof builds) => { builds = found; renderDownloads(); })
    .catch(() => { /* none published: the menu offers the command instead */ });

  $('name').addEventListener('input', () => {
    const typed = $<HTMLInputElement>('name').value;
    settings.name = normalize({ name: typed }).name;
    markCustom();
    renderHead();
    scheduleBuild(450);
  });
  $('name').addEventListener('blur', () => { $<HTMLInputElement>('name').value = settings.name; });

  $('tab-presets').addEventListener('click', () => setTab('presets'));
  $('tab-build').addEventListener('click', () => setTab('build'));
  $('view-device').addEventListener('click', () => setView('device'));
  $('view-patch').addEventListener('click', () => setView('patch'));

  const playing = (): boolean => (kind() === 'instrument' ? sequencer.playing : track.playing);
  $('btn-play').addEventListener('click', () => void setPlaying(!playing()));
  $('btn-bypass').addEventListener('click', () => {
    track.bypass = !track.bypass;
    $('btn-bypass').setAttribute('aria-pressed', String(track.bypass));
    $('stage').classList.toggle('is-bypassed', track.bypass);
  });
  $('file').addEventListener('change', () => {
    const file = $<HTMLInputElement>('file').files?.[0];
    if (file) void useFile(file);
    $<HTMLInputElement>('file').value = '';
  });

  // Drop an audio file anywhere on the page to play it through the device.
  window.addEventListener('dragover', (e) => {
    if (e.dataTransfer?.types.includes('Files')) e.preventDefault();
  });
  window.addEventListener('drop', (e) => {
    const file = e.dataTransfer?.files?.[0];
    if (!file) return;
    e.preventDefault();
    void useFile(file);
  });

  $('dl-amxd').addEventListener('click', () => void download());
  $('dl-settings').addEventListener('click', saveSettings);
  $('btn-python').addEventListener('click', () => setDrawer(!$('drawer').classList.contains('open')));
  $('drawer-close').addEventListener('click', () => setDrawer(false));
  $('scrim').addEventListener('click', () => setDrawer(false));
  $('btn-copy').addEventListener('click', () => { if (result) void copy(result.script, 'Script copied.'); });
  $('btn-py').addEventListener('click', () => {
    if (result) save(result.script, result.filename.replace(/\.amxd$/, '.py'), 'text/x-python');
  });
  $('btn-share').addEventListener('click', () => {
    history.replaceState(null, '', `#s=${encodeSettings(settings)}`);
    void copy(location.href, 'Link copied. It opens this exact device.');
  });

  // The three popovers. The help also peeks on hover or focus (CSS).
  const popovers = [
    popover('help', 'btn-help'), popover('settings', 'btn-settings'),
    popover('library', 'btn-library'), popover('downloads', 'btn-download'),
  ];

  const dark = $('dark-mode');
  dark.setAttribute('aria-checked', String(getTheme() === 'dark'));
  dark.addEventListener('click', () => {
    setTheme(getTheme() === 'dark' ? 'light' : 'dark');
    dark.setAttribute('aria-checked', String(getTheme() === 'dark'));
  });

  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      for (const close of popovers) close();
      if ($('help').contains(document.activeElement)) (document.activeElement as HTMLElement).blur();
    }
    if (e.key === 'Escape' && $('drawer').classList.contains('open')) setDrawer(false);
    const target = e.target as HTMLElement;
    const typing = target.tagName === 'INPUT' || target.tagName === 'TEXTAREA';
    const onControl = target.tagName === 'BUTTON' || target.getAttribute('role') === 'slider';
    if (e.code === 'Space' && !typing && !onControl) {
      e.preventDefault();
      void setPlaying(!playing());
    }
  });

  new ResizeObserver(() => fitDevice()).observe($('device-wrap'));

  python.ready.catch((err: Error) => {
    setStatus('error', 'Python failed to start');
    $('boot-text').textContent = 'Python could not start in this browser.';
    showCheckError(String(err.message || err));
  });

  if (kind() === 'instrument') track.clearBuffer();
  else void chooseSample(sampleId);
  scheduleBuild();
}

init();

if (import.meta.env.DEV) {
  const fx = { engine, track, get settings() { return settings; }, get result() { return result; } };
  Object.assign(window, { __fx: fx });
  // The offline renderer used to compare this engine with Max (see offline.ts).
  void import('./offline').then((offline) => Object.assign(fx, offline));
}
