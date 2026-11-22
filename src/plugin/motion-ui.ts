// Motion: the page's controls for making dials move by themselves.
//
// A device's motion is a list of routes, each one source moving one dial by some amount
// (model.ts, Motion; fxgen.py turns it into the patch). A source is an LFO, a follower of
// the incoming volume, or a knob the user added and named: a knob is how "one control
// for several dials" is made, by routing it to each of them.
//
// There are two ways in, and they edit the same list:
//
//   the Motion section of the Customize panel   every route, source and knob, as a list
//   the device itself, unlocked                 click a dial and say what should move it
//
// Everything here changes `settings` in place and then calls host.changed(), which
// rebuilds the device.

import {
  SCHEMA, emptyMotion, moduleSpec, motionSources, motionTargets,
  type Kind, type Motion, type Route, type Settings,
} from './model';

export interface MotionHost {
  settings(): Settings;
  kind(): Kind;
  /** Something about the motion changed: rebuild the device and redraw. */
  changed(): void;
}

const el = <K extends keyof HTMLElementTagNameMap>(tag: K, className = '', text = ''): HTMLElementTagNameMap[K] => {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text) e.textContent = text;
  return e;
};

// ── the list being edited ───────────────────────────────────────────────────────

const motionOf = (s: Settings): Motion => (s.motion ??= emptyMotion());

/** A device with no route and no knob has no motion at all, as before there was any. */
function tidy(s: Settings): void {
  if (s.motion && !s.motion.routes.length && !s.motion.knobs.length) delete s.motion;
}

/** The dials that can be moved right now: those of the modules that are switched on. */
export function liveTargets(s: Settings, kind: Kind): { key: string; label: string; short: string }[] {
  const on = new Set<string>([SCHEMA.output.id, ...s.chain.filter((e) => e.on).map((e) => e.module), ...Object.keys(s.instrument ?? {})]);
  const out: { key: string; label: string; short: string }[] = [];
  for (const [key, { module, param }] of motionTargets(kind)) {
    if (on.has(module.id)) out.push({ key, label: `${module.label} ${param.label}`, short: param.short ?? param.label });
  }
  return out;
}

const sourceLabel = (s: Settings, kind: Kind, id: string): string => motionSources(kind, s.motion).find((x) => x.id === id)?.label ?? id;

function addRoute(s: Settings, source: string, target: string, amount = 30): void {
  const m = motionOf(s);
  if (m.routes.length >= SCHEMA.motion.maxRoutes || m.routes.some((r) => r.source === source && r.target === target)) return;
  m.routes.push({ source, target, amount });
}

function removeRoute(s: Settings, route: Route): void {
  if (!s.motion) return;
  s.motion.routes = s.motion.routes.filter((r) => r !== route);
  tidy(s);
}

/** A new knob, named `name` or after its number. Returns its id, or undefined when there is no room. */
function addKnob(s: Settings, name = ''): string | undefined {
  const m = motionOf(s);
  if (m.knobs.length >= SCHEMA.motion.knobs.max) { tidy(s); return undefined; }
  const id = `k${m.knobs.length + 1}`;
  const taken = new Set(m.knobs.map((k) => k.name.toLowerCase()));
  let label = name.replace(/[^A-Za-z0-9 ]/g, '').trim().slice(0, 10) || `Knob ${m.knobs.length + 1}`;
  while (taken.has(label.toLowerCase())) label = `${label.slice(0, 8)} ${m.knobs.length + 1}`.trim();
  m.knobs.push({ id, name: label });
  m.knobValues[id] = 0;   // at rest: adding a knob changes nothing until it is turned
  return id;
}

/** Take a knob away, with its routes. The knobs after it move up a number, and their routes follow. */
function removeKnob(s: Settings, id: string): void {
  const m = s.motion;
  if (!m) return;
  const rename = new Map<string, string>();
  const kept = m.knobs.filter((k) => k.id !== id);
  const values: Record<string, number> = {};
  kept.forEach((k, i) => {
    rename.set(k.id, `k${i + 1}`);
    values[`k${i + 1}`] = m.knobValues[k.id] ?? 0;
  });
  m.routes = m.routes.filter((r) => r.source !== id).map((r) => ({ ...r, source: rename.get(r.source) ?? r.source }));
  m.knobs = kept.map((k, i) => ({ id: `k${i + 1}`, name: k.name }));
  m.knobValues = values;
  tidy(s);
}

const signed = (amount: number): string => `${amount > 0 ? '+' : ''}${Math.round(amount)} %`;

/** A slider for how far a route moves its dial, either way. It rebuilds when let go, not while dragged. */
function amountSlider(route: Route, host: MotionHost): HTMLElement {
  const wrap = el('span', 'motion-amount');
  const range = el('input');
  range.type = 'range';
  range.min = '-100';
  range.max = '100';
  range.step = '1';
  range.value = String(route.amount);
  range.setAttribute('aria-label', 'How far');
  const read = el('span', 'motion-read', signed(route.amount));
  range.addEventListener('input', () => { read.textContent = signed(Number(range.value)); });
  range.addEventListener('change', () => {
    // An amount of nothing moves nothing, and fxgen would drop the route: keep it just off zero.
    route.amount = Number(range.value) === 0 ? 1 : Number(range.value);
    host.changed();
  });
  wrap.append(range, read);
  return wrap;
}

function removeButton(label: string, act: () => void): HTMLButtonElement {
  const b = el('button', 'motion-x');
  b.type = 'button';
  b.title = label;
  b.setAttribute('aria-label', label);
  b.innerHTML = '<svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true"><path d="M2.5 2.5l7 7M9.5 2.5l-7 7" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
  b.addEventListener('click', act);
  return b;
}

// ── the Motion section of the Customize panel ───────────────────────────────────

/** Where a quick start aims: the filter's cutoff if there is one, otherwise the first dial there is. */
function firstTarget(s: Settings, kind: Kind): string | undefined {
  const targets = liveTargets(s, kind);
  return (targets.find((t) => t.key === 'filter.cutoff') ?? targets.find((t) => t.key === 'tone.cutoff') ?? targets[0])?.key;
}

export function renderMotion(root: HTMLElement, host: MotionHost): void {
  const s = host.settings();
  const kind = host.kind();
  const targets = liveTargets(s, kind);
  const sources = motionSources(kind, s.motion);
  root.replaceChildren();

  const routes = s.motion?.routes ?? [];
  if (!routes.length) {
    root.append(el('p', 'motion-hint', 'Nothing is moving yet. Start with one of these, or press Edit above the device and click a dial.'));
  }

  // Each route: which dial, moved by what, how far.
  const list = el('div', 'motion-list');
  for (const route of routes) {
    const row = el('div', 'motion-row');
    const dial = el('select');
    dial.setAttribute('aria-label', 'The dial that moves');
    // A route to a dial of a module that is switched off stays, greyed, for when it is switched on again.
    const known = targets.some((t) => t.key === route.target);
    if (!known) dial.append(new Option(`${route.target.replace('.', ' ')} (off)`, route.target));
    for (const t of targets) dial.append(new Option(t.label, t.key));
    dial.value = route.target;
    dial.addEventListener('change', () => {
      if (routes.some((r) => r !== route && r.source === route.source && r.target === dial.value)) { dial.value = route.target; return; }
      route.target = dial.value;
      host.changed();
    });
    const by = el('select');
    by.setAttribute('aria-label', 'What moves it');
    for (const src of sources) by.append(new Option(src.label, src.id));
    by.value = route.source;
    by.addEventListener('change', () => {
      if (routes.some((r) => r !== route && r.target === route.target && r.source === by.value)) { by.value = route.source; return; }
      route.source = by.value;
      host.changed();
    });
    const top = el('div', 'motion-pair');
    top.append(dial, el('span', 'motion-by', 'by'), by, removeButton('Stop moving this dial', () => { removeRoute(s, route); host.changed(); }));
    row.append(top, amountSlider(route, host));
    if (!known) row.classList.add('is-off');
    list.append(row);
  }
  root.append(list);

  // Ways to start, and to add.
  const starts = el('div', 'motion-starts');
  const start = (label: string, title: string, act: () => void): void => {
    const b = el('button', 'chip', label);
    b.type = 'button';
    b.title = title;
    b.addEventListener('click', () => { act(); host.changed(); });
    starts.append(b);
  };
  const aim = firstTarget(s, kind);
  const full = routes.length >= SCHEMA.motion.maxRoutes;
  if (aim && !full) {
    start('Slow sweep', 'LFO 1, slowly, on the first dial worth sweeping', () => {
      motionOf(s).sources.lfo1.values.rate = 0.4;
      addRoute(s, 'lfo1', aim, 30);
    });
    start('Wobble', 'LFO 2, a few times a second', () => {
      motionOf(s).sources.lfo2.values.rate = 5;
      addRoute(s, 'lfo2', aim, 20);
    });
    if (kind === 'effect') start('Follow the volume', 'Opens up the louder the sound coming in', () => addRoute(s, 'follow', aim, 45));
  }
  if (!full && (s.motion?.knobs.length ?? 0) < SCHEMA.motion.knobs.max) {
    start('Add a knob', 'A knob of your own, to route to any dials you like', () => {
      const id = addKnob(s);
      if (id && aim) addRoute(s, id, aim, 50);
    });
  }
  if (starts.childElementCount) root.append(starts);

  // The LFOs in use: their shape. Their speed is a dial on the device, like every other dial.
  for (const spec of SCHEMA.motion.sources) {
    if (!routes.some((r) => r.source === spec.id) || !spec.options.length || !s.motion) continue;
    const entry = s.motion.sources[spec.id];
    for (const opt of spec.options) {
      const line = el('div', 'motion-source');
      line.append(el('span', '', `${spec.label} ${opt.label.toLowerCase()}`));
      const seg = el('div', 'seg');
      seg.setAttribute('role', 'radiogroup');
      seg.setAttribute('aria-label', `${spec.label} ${opt.label}`);
      for (const choice of opt.choices) {
        const b = el('button', '', choice.label);
        b.type = 'button';
        b.setAttribute('role', 'radio');
        b.setAttribute('aria-checked', String(entry.options[opt.id] === choice.id));
        b.addEventListener('click', () => {
          if (entry.options[opt.id] === choice.id) return;
          entry.options[opt.id] = choice.id;
          host.changed();
        });
        seg.append(b);
      }
      line.append(seg);
      root.append(line);
    }
  }

  // The user's knobs: a name each. A knob with no route is kept but is not on the device.
  for (const knob of s.motion?.knobs ?? []) {
    const line = el('div', 'motion-source');
    const routed = routes.filter((r) => r.source === knob.id).length;
    const name = el('input');
    name.value = knob.name;
    name.maxLength = 10;
    name.spellcheck = false;
    name.setAttribute('aria-label', 'The knob\'s name');
    name.addEventListener('change', () => {
      const typed = name.value.replace(/[^A-Za-z0-9 ]/g, '').trim().slice(0, 10);
      const clash = s.motion!.knobs.some((k) => k !== knob && k.name.toLowerCase() === typed.toLowerCase());
      if (!typed || clash) { name.value = knob.name; return; }
      knob.name = typed;
      host.changed();
    });
    line.append(el('span', '', 'Knob'), name, el('small', '', routed ? `moves ${routed} dial${routed === 1 ? '' : 's'}` : 'not routed yet'),
      removeButton('Remove this knob', () => { removeKnob(s, knob.id); host.changed(); }));
    root.append(line);
  }
}

// ── the device, unlocked: click a dial ──────────────────────────────────────────

/**
 * Mark the dials of a freshly drawn device: which can be moved (data-target, "module.dial")
 * and which already are (is-moved). `index` is dialIndex() for these settings.
 */
export function markDevice(device: HTMLElement, s: Settings, kind: Kind, index: Map<string, { module: string; param: string }>): void {
  const targets = motionTargets(kind);
  const moved = new Set((s.motion?.routes ?? []).map((r) => r.target));
  for (const dial of device.querySelectorAll<HTMLElement>('.live-dial[data-param]')) {
    const at = index.get((dial.dataset.param ?? '').toLowerCase().replace(/ /g, '_'));
    const key = at ? `${at.module}.${at.param}` : '';
    const widget = dial.closest<HTMLElement>('.dv-widget');
    if (!widget || !targets.has(key)) continue;
    widget.dataset.target = key;
    widget.classList.toggle('is-moved', moved.has(key));
  }
}

/** Draw the menu for one dial into `root`: what moves it now, and what could. */
export function renderDialMenu(root: HTMLElement, target: string, host: MotionHost): void {
  const s = host.settings();
  const kind = host.kind();
  const spec = motionTargets(kind).get(target);
  root.replaceChildren();
  if (!spec) return;
  const mod = moduleSpec(spec.module.id) ?? spec.module;
  root.append(el('h2', '', `${mod.label} ${spec.param.label}`));

  const routes = (s.motion?.routes ?? []).filter((r) => r.target === target);
  for (const route of routes) {
    const row = el('div', 'motion-row');
    const top = el('div', 'motion-pair');
    top.append(el('b', '', sourceLabel(s, kind, route.source)), removeButton('Stop', () => { removeRoute(s, route); host.changed(); }));
    row.append(top, amountSlider(route, host));
    root.append(row);
  }

  const free = motionSources(kind, s.motion).filter((src) => !routes.some((r) => r.source === src.id));
  const room = (s.motion?.routes.length ?? 0) < SCHEMA.motion.maxRoutes;
  if (!room) {
    root.append(el('p', 'muted small', 'This device has all the motion it can hold.'));
    return;
  }
  root.append(el('p', 'motion-ask', routes.length ? 'Also move it with' : 'Move it with'));
  const picks = el('div', 'motion-starts');
  const pick = (label: string, title: string, act: () => void): void => {
    const b = el('button', 'chip', label);
    b.type = 'button';
    b.title = title;
    b.addEventListener('click', () => { act(); host.changed(); });
    picks.append(b);
  };
  const blurb = new Map(SCHEMA.motion.sources.map((src) => [src.id, src.blurb ?? '']));
  for (const src of free) pick(src.label, blurb.get(src.id) ?? 'Your knob', () => addRoute(s, src.id, target, src.id.startsWith('k') ? 50 : 30));
  if ((s.motion?.knobs.length ?? 0) < SCHEMA.motion.knobs.max) {
    pick('A new knob', 'A knob of your own on the device, which you can route to other dials too', () => {
      const id = addKnob(s, spec.param.short ?? spec.param.label);
      if (id) addRoute(s, id, target, 50);
    });
  }
  root.append(picks);
}
