// Edit: the device, unlocked.
//
// Press Edit and the device on screen becomes the thing you change, not a picture of it:
//
//   an effect's card   drag it to move it, press its x to take it out, pick its type
//   + Effect           add one of the effects that is not on the device yet
//   + Knob             a knob of your own; then click the dials it should turn
//   a dial             choose what turns it by itself (a slow sweep, a wobble, how loud
//                      the sound coming in is), or put it on one of your knobs
//   a knob of yours    rename it, see what it turns, link more dials, delete it
//
// Nothing here draws the device. Python does, from the settings (fxgen.py), and the page
// shows what it built. So every action changes `settings` and calls host.changed(), which
// rebuilds; decorate() then lays the controls above over the new device. That is also why
// there is a moment of "Building" after each change.
//
// "What turns a dial" is a list of routes in the settings (model.ts, Motion). A sweep, a
// wobble and the loudness are fixed sources; a knob is one the user made. The Customize
// panel shows the same list (renderMotion), for people who would sooner read it.

import type { MaxNode } from '../engine/registry';
import type { IRPatch } from '../ir/types';
import { liveParam, paramPosition } from '../objects/ui/live-param';
import { firstNum } from '../runtime/atoms';
import {
  SCHEMA, emptyMotion, moduleSpec, motionSources, motionTargets, themeOf,
  type Kind, type Motion, type Route, type Settings,
} from './model';

export interface EditHost {
  settings(): Settings;
  kind(): Kind;
  editing(): boolean;
  /** Something changed: rebuild the device and redraw. */
  changed(): void;
  /** The floating menu, and the line of help above the device. */
  menu: HTMLElement;
  hint: HTMLElement;
}

type DialIndex = Map<string, { module: string; param: string }>;

const el = <K extends keyof HTMLElementTagNameMap>(tag: K, className = '', text = ''): HTMLElementTagNameMap[K] => {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text) e.textContent = text;
  return e;
};

const button = (className: string, text: string, title: string, act: (e: MouseEvent) => void): HTMLButtonElement => {
  const b = el('button', className, text);
  b.type = 'button';
  if (title) { b.title = title; b.setAttribute('aria-label', title); }
  b.addEventListener('click', act);
  return b;
};

const CROSS = '<svg viewBox="0 0 12 12" width="9" height="9" aria-hidden="true"><path d="M2.5 2.5l7 7M9.5 2.5l-7 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>';

// ── the settings being edited ───────────────────────────────────────────────────

const motionOf = (s: Settings): Motion => (s.motion ??= emptyMotion());

/** A device with no route and no knob has no motion at all, as before there was any. */
function tidy(s: Settings): void {
  if (s.motion && !s.motion.routes.length && !s.motion.knobs.length) delete s.motion;
}

const routesTo = (s: Settings, target: string): Route[] => (s.motion?.routes ?? []).filter((r) => r.target === target);
const routesFrom = (s: Settings, source: string): Route[] => (s.motion?.routes ?? []).filter((r) => r.source === source);
const isKnob = (id: string): boolean => /^k\d+$/.test(id);

/** The dials that can be turned right now: those of the modules that are on the device. */
export function liveTargets(s: Settings, kind: Kind): { key: string; label: string }[] {
  const on = new Set<string>([SCHEMA.output.id, ...s.chain.filter((e) => e.on).map((e) => e.module), ...Object.keys(s.instrument ?? {})]);
  const out: { key: string; label: string }[] = [];
  for (const [key, { module, param }] of motionTargets(kind)) if (on.has(module.id)) out.push({ key, label: `${module.label} ${param.label}` });
  return out;
}

const targetLabel = (kind: Kind, key: string): string => {
  const t = motionTargets(kind).get(key);
  return t ? `${t.module.label} ${t.param.label}` : key.replace('.', ' ');
};

function addRoute(s: Settings, source: string, target: string): void {
  const m = motionOf(s);
  if (m.routes.length >= SCHEMA.motion.maxRoutes || m.routes.some((r) => r.source === source && r.target === target)) { tidy(s); return; }
  // A knob pushes one way from where the dial stands; a sweep swings both ways round it.
  m.routes.push({ source, target, amount: isKnob(source) ? 50 : source === 'follow' ? 45 : 25 });
}

function removeRoute(s: Settings, route: Route): void {
  if (!s.motion) return;
  s.motion.routes = s.motion.routes.filter((r) => r !== route);
  tidy(s);
}

function toggleRoute(s: Settings, source: string, target: string): void {
  const had = (s.motion?.routes ?? []).find((r) => r.source === source && r.target === target);
  if (had) removeRoute(s, had);
  else addRoute(s, source, target);
}

/** A new knob. Returns its id, or undefined when the device has all it can hold. */
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

/** Take a knob away, with what it turned. The knobs after it move up a number, and their routes follow. */
function removeKnob(s: Settings, id: string): void {
  const m = s.motion;
  if (!m) return;
  const kept = m.knobs.filter((k) => k.id !== id);
  const rename = new Map(kept.map((k, i) => [k.id, `k${i + 1}`]));
  const values: Record<string, number> = {};
  for (const k of kept) values[rename.get(k.id)!] = m.knobValues[k.id] ?? 0;
  m.routes = m.routes.filter((r) => r.source !== id).map((r) => ({ ...r, source: rename.get(r.source) ?? r.source }));
  m.knobs = kept.map((k, i) => ({ id: `k${i + 1}`, name: k.name }));
  m.knobValues = values;
  tidy(s);
}

/** Put an effect on the device, after the ones already there. */
function addEffect(s: Settings, module: string): void {
  const entry = s.chain.find((e) => e.module === module);
  if (!entry) return;
  entry.on = true;
  s.chain = [...s.chain.filter((e) => e.on && e !== entry), entry, ...s.chain.filter((e) => !e.on)];
}

/** Move an effect so that it is the `to`-th of the effects that are on. */
function moveEffect(s: Settings, module: string, to: number): boolean {
  const on = s.chain.filter((e) => e.on);
  const from = on.findIndex((e) => e.module === module);
  if (from < 0) return false;
  const [entry] = on.splice(from, 1);
  on.splice(Math.max(0, Math.min(on.length, to)), 0, entry);
  const before = s.chain.filter((e) => e.on).map((e) => e.module).join();
  s.chain = [...on, ...s.chain.filter((e) => !e.on)];
  return before !== on.map((e) => e.module).join();
}

// ── small pieces shared by the menus and the panel ──────────────────────────────

const signed = (amount: number): string => `${amount > 0 ? '+' : ''}${Math.round(amount)} %`;

/** How far a route turns its dial, either way. It rebuilds when let go, not while dragged. */
function amountSlider(route: Route, host: EditHost): HTMLElement {
  const wrap = el('label', 'motion-amount');
  wrap.append(el('span', 'motion-how', 'How far'));
  const range = el('input');
  range.type = 'range';
  range.min = '-100';
  range.max = '100';
  range.step = '1';
  range.value = String(route.amount);
  const read = el('span', 'motion-read', signed(route.amount));
  range.addEventListener('input', () => { read.textContent = signed(Number(range.value)); });
  range.addEventListener('change', () => {
    // An amount of nothing turns nothing, and fxgen would drop the route: keep it just off zero.
    route.amount = Number(range.value) === 0 ? 1 : Number(range.value);
    host.changed();
  });
  wrap.append(range, read);
  return wrap;
}

const sourceName = (s: Settings, kind: Kind, id: string): string => motionSources(kind, s.motion).find((x) => x.id === id)?.label ?? id;

// ── the panel: the same list, written out ───────────────────────────────────────

export function renderMotion(root: HTMLElement, host: EditHost): void {
  const s = host.settings();
  const kind = host.kind();
  root.replaceChildren();
  const routes = s.motion?.routes ?? [];
  if (!routes.length && !(s.motion?.knobs.length)) {
    root.append(el('p', 'motion-hint', 'Nothing turns by itself yet. Press Edit above the device, then click a dial to make it sweep, wobble or follow how loud you play, or add a knob of your own.'));
    return;
  }
  const targets = liveTargets(s, kind);
  for (const route of routes) {
    const row = el('div', 'motion-row');
    const off = !targets.some((t) => t.key === route.target);
    const top = el('div', 'motion-pair');
    const what = el('span', 'motion-what');
    what.append(el('b', '', targetLabel(kind, route.target)), ` turned by ${isKnob(route.source) ? 'your knob ' : ''}`, el('b', '', sourceName(s, kind, route.source)));
    if (off) what.append(' (that effect is off)');
    const x = button('motion-x', '', 'Stop turning this dial', () => { removeRoute(s, route); host.changed(); });
    x.innerHTML = CROSS;
    top.append(what, x);
    row.append(top, amountSlider(route, host));
    if (off) row.classList.add('is-off');
    root.append(row);
  }
  for (const knob of s.motion?.knobs ?? []) {
    if (routesFrom(s, knob.id).length) continue;
    root.append(el('p', 'motion-hint', `Your knob “${knob.name}” turns nothing yet. Press Edit, click it, and link it to some dials.`));
  }
}

// ── the device ──────────────────────────────────────────────────────────────────

/** The knob whose dials are being picked, when the device is in that mode. */
let linking: string | undefined;

export function stopLinking(): void {
  linking = undefined;
}

interface Card { id: string; kind: 'effect' | 'synth' | 'source' | 'knobs' | 'output'; left: number; top: number; width: number; height: number }

const px = (v: string): number => parseFloat(v) || 0;

/** The cards of the device as drawn, each with the module it belongs to, from the dials on it. */
function cardsOf(device: HTMLElement, s: Settings): Card[] {
  const chain = new Set(s.chain.map((e) => e.module));
  const out: Card[] = [];
  for (const panel of device.querySelectorAll<HTMLElement>('.dv-panel')) {
    const left = px(panel.style.left);
    const width = px(panel.style.width);
    if (px(panel.style.height) < 100) continue;   // not a card (the theme's backdrop is taller than every card and is skipped below)
    const inside = [...device.querySelectorAll<HTMLElement>('.dv-widget')].find((w) => px(w.style.left) >= left && px(w.style.left) < left + width);
    const id = inside?.dataset.module;
    if (!id || width > 0.9 * device.offsetWidth) continue;
    const kind = chain.has(id) ? 'effect' : id === SCHEMA.output.id ? 'output' : id === SCHEMA.motion.knobs.id ? 'knobs'
      : SCHEMA.motion.sources.some((x) => x.id === id) ? 'source' : 'synth';
    out.push({ id, kind, left, top: px(panel.style.top), width, height: px(panel.style.height) });
  }
  return out.sort((a, b) => a.left - b.left);
}

function place(node: HTMLElement, left: number, top: number, width?: number, height?: number): void {
  node.style.left = `${left}px`;
  node.style.top = `${top}px`;
  if (width !== undefined) node.style.width = `${width}px`;
  if (height !== undefined) node.style.height = `${height}px`;
}

/** A small menu under an option's name on a card: its choices. */
function optionPicker(label: string, choices: { id: string; label: string }[], current: string, pick: (id: string) => void): HTMLSelectElement {
  const select = el('select', 'edit-pick');
  select.title = label;
  select.setAttribute('aria-label', label);
  for (const c of choices) select.append(new Option(c.label, c.id));
  select.value = current;
  select.addEventListener('change', () => pick(select.value));
  return select;
}

/**
 * Mark the dials of a freshly drawn device, and, in Edit, lay the controls over it.
 * `index` is dialIndex() for these settings.
 */
export function decorate(device: HTMLElement, host: EditHost, index: DialIndex): void {
  const s = host.settings();
  const kind = host.kind();
  const targets = motionTargets(kind);

  for (const dial of device.querySelectorAll<HTMLElement>('.live-dial[data-param]')) {
    const at = index.get((dial.dataset.param ?? '').toLowerCase().replace(/ /g, '_'));
    const widget = dial.closest<HTMLElement>('.dv-widget');
    if (!at || !widget) continue;
    widget.dataset.module = at.module;
    const key = `${at.module}.${at.param}`;
    if (at.module === SCHEMA.motion.knobs.id) widget.dataset.knob = at.param;
    else if (targets.has(key)) {
      widget.dataset.target = key;
      widget.classList.toggle('is-moved', routesTo(s, key).length > 0);
      widget.classList.toggle('is-linked', !!linking && routesTo(s, key).some((r) => r.source === linking));
    }
  }

  device.querySelector('.edit-layer')?.remove();
  device.classList.toggle('is-linking', !!linking && host.editing());
  if (!host.editing()) { linking = undefined; return; }
  if (linking && !s.motion?.knobs.some((k) => k.id === linking)) linking = undefined;

  const layer = el('div', 'edit-layer');
  const cards = cardsOf(device, s);
  const effects = cards.filter((c) => c.kind === 'effect');

  for (const card of cards) {
    const box = el('div', `edit-card is-${card.kind}`);
    place(box, card.left, card.top, card.width, card.height);
    const spec = moduleSpec(card.id) ?? SCHEMA.instrument.modules.find((m) => m.id === card.id) ?? SCHEMA.motion.sources.find((m) => m.id === card.id);

    if (card.kind === 'effect') {
      const grip = el('div', 'edit-grip');
      grip.title = 'Drag to move this effect';
      grip.addEventListener('pointerdown', (e) => startDrag(e, card, box, effects, device, layer, host));
      box.append(grip);
    }
    if (card.kind === 'effect' || card.kind === 'source') {
      const x = button('edit-x', '', card.kind === 'effect' ? `Take ${spec?.label ?? card.id} off the device` : `Stop ${spec?.label ?? card.id} turning anything`, () => {
        if (card.kind === 'effect') s.chain.find((e) => e.module === card.id)!.on = false;
        else if (s.motion) { s.motion.routes = s.motion.routes.filter((r) => r.source !== card.id); tidy(s); }
        closeMenu(host);
        host.changed();
      });
      x.innerHTML = CROSS;
      box.append(x);
    }
    // The module's options (a filter's type, an LFO's shape), under its name.
    const store = card.kind === 'effect' ? s.chain.find((e) => e.module === card.id)?.options
      : card.kind === 'synth' ? s.instrument?.[card.id]?.options
        : card.kind === 'source' ? s.motion?.sources[card.id]?.options : undefined;
    if (spec && store && spec.options.length) {
      const picks = el('div', 'edit-picks');
      for (const opt of spec.options) {
        picks.append(optionPicker(`${spec.label} ${opt.label}`, opt.choices, store[opt.id], (id) => { store[opt.id] = id; host.changed(); }));
      }
      box.append(picks);
    }
    layer.append(box);
  }

  // + Effect and + Knob, under the device's name.
  const adds = el('div', 'edit-adds');
  place(adds, 8, 96, 112);
  const offEffects = s.chain.filter((e) => !e.on);
  if (offEffects.length) {
    adds.append(button('edit-add', '+ Effect', 'Add an effect to the device', (e) => {
      const menu = host.menu;
      menu.replaceChildren(el('h2', '', 'Add an effect'));
      for (const entry of offEffects) {
        const mod = moduleSpec(entry.module)!;
        const row = button('edit-option', '', '', () => { addEffect(s, entry.module); closeMenu(host); host.changed(); });
        row.append(el('b', '', mod.label), el('span', '', mod.blurb ?? mod.params.map((p) => p.label).join(', ')));
        menu.append(row);
      }
      openMenu(host, (e.currentTarget as HTMLElement).getBoundingClientRect());
    }));
  }
  if ((s.motion?.knobs.length ?? 0) < SCHEMA.motion.knobs.max && (s.motion?.routes.length ?? 0) < SCHEMA.motion.maxRoutes) {
    adds.append(button('edit-add', '+ Knob', 'Add a knob of your own, and choose the dials it turns', () => {
      const id = addKnob(s);
      if (!id) return;
      linking = id;
      closeMenu(host);
      host.changed();
    }));
  }
  adds.append(button('edit-add', 'Look', 'Pictures or dials alone, and the device\'s colours', (e) => {
    drawLookMenu(host);
    openMenu(host, (e.currentTarget as HTMLElement).getBoundingClientRect());
  }));
  layer.append(adds);
  device.append(layer);
  renderHint(host);
}

/** The line above the device: what a click does right now. */
function renderHint(host: EditHost): void {
  const s = host.settings();
  const hint = host.hint;
  hint.replaceChildren();
  hint.classList.toggle('is-linking', !!linking);
  const knob = s.motion?.knobs.find((k) => k.id === linking);
  if (!knob) {
    hint.textContent = 'Drag an effect to move it. Click a dial to make it turn by itself.';
    return;
  }
  const count = routesFrom(s, knob.id).length;
  hint.append(`Click the dials “${knob.name}” should turn`, el('small', '', count ? ` (${count} so far)` : ''),
    button('edit-done', 'Done', '', () => { linking = undefined; host.changed(); }));
}

// ── how it looks ────────────────────────────────────────────────────────────────

function drawLookMenu(host: EditHost): void {
  const s = host.settings();
  const menu = host.menu;
  menu.replaceChildren(el('h2', '', 'Look'));
  // Pictures above the dials (an EQ's curve, a delay's echoes), or the dials alone.
  menu.append(el('p', 'edit-ask', 'Each effect shows'));
  const looks = el('div', 'edit-looks');
  for (const [id, label] of [['', 'A picture and dials'], ['dials', 'Dials only']] as const) {
    const b = button('edit-look', label, '', () => {
      if (id) s.look = id; else delete s.look;
      host.changed();
      drawLookMenu(host);
    });
    b.setAttribute('aria-pressed', String((s.look ?? '') === id));
    looks.append(b);
  }
  menu.append(looks, el('p', 'edit-ask', 'The device'));
  const themes = el('div', 'edit-themes');
  for (const theme of SCHEMA.themes) {
    const b = button('edit-theme', '', theme.label, () => {
      if (theme.id === SCHEMA.themes[0].id) delete s.theme; else s.theme = theme.id;
      host.changed();
      drawLookMenu(host);
    });
    b.setAttribute('aria-pressed', String(themeOf(s).id === theme.id));
    b.style.background = theme.bg;
    const card = el('i');
    card.style.background = theme.card;
    card.style.color = theme.ink;
    card.textContent = 'Aa';
    b.append(card, el('span', '', theme.label));
    (b.lastChild as HTMLElement).style.color = theme.ink;
    themes.append(b);
  }
  menu.append(themes, el('p', 'edit-ask', 'The dials'));
  const accents = el('div', 'edit-accents');
  for (const colour of SCHEMA.accents) {
    const b = button('edit-accent', '', colour, () => { s.accent = colour; host.changed(); drawLookMenu(host); });
    b.setAttribute('aria-pressed', String(s.accent === colour));
    b.style.background = colour;
    accents.append(b);
  }
  menu.append(accents);
}

// ── dragging an effect ──────────────────────────────────────────────────────────

function startDrag(e: PointerEvent, card: Card, ghost: HTMLElement, effects: Card[], device: HTMLElement, layer: HTMLElement, host: EditHost): void {
  if (e.button !== 0) return;
  e.preventDefault();
  const grip = e.currentTarget as HTMLElement;
  grip.setPointerCapture(e.pointerId);
  const scale = device.getBoundingClientRect().width / device.offsetWidth || 1;
  const toDevice = (clientX: number): number => (clientX - device.getBoundingClientRect().left) / scale;
  const others = effects.filter((c) => c.id !== card.id);
  const mark = el('div', 'edit-insert');
  let to = effects.findIndex((c) => c.id === card.id);
  let moved = false;

  const where = (x: number): number => others.filter((c) => x > c.left + c.width / 2).length;
  const onMove = (ev: PointerEvent): void => {
    const x = toDevice(ev.clientX);
    if (!moved && Math.abs(ev.clientX - e.clientX) < 4) return;
    if (!moved) { moved = true; layer.append(mark); ghost.classList.add('is-dragging'); }
    to = where(x);
    // the gap the effect would land in: before the card at `to`, or after the last one
    const at = to < others.length ? others[to].left - 3 : (others.length ? others[others.length - 1].left + others[others.length - 1].width + 3 : card.left);
    place(mark, at - 1, card.top, 2, card.height);
    ghost.style.transform = `translateX(${x - (card.left + card.width / 2)}px)`;
  };
  const onUp = (): void => {
    grip.removeEventListener('pointermove', onMove);
    grip.removeEventListener('pointerup', onUp);
    grip.removeEventListener('pointercancel', onUp);
    mark.remove();
    ghost.classList.remove('is-dragging');
    ghost.style.transform = '';
    if (moved && moveEffect(host.settings(), card.id, to)) host.changed();
  };
  grip.addEventListener('pointermove', onMove);
  grip.addEventListener('pointerup', onUp);
  grip.addEventListener('pointercancel', onUp);
}

// ── the menus ───────────────────────────────────────────────────────────────────

/** What the open menu is about, so it can be drawn again after a rebuild. */
let menuFor: { kind: 'dial'; target: string } | { kind: 'knob'; id: string } | undefined;

function openMenu(host: EditHost, at: DOMRect): void {
  const pop = host.menu;
  pop.hidden = false;
  const width = pop.offsetWidth;
  const left = Math.min(window.innerWidth - width - 12, Math.max(12, at.left + at.width / 2 - width / 2));
  const below = at.bottom + 10;
  const top = below + pop.offsetHeight > window.innerHeight - 12 ? Math.max(12, at.top - pop.offsetHeight - 10) : below;
  pop.style.left = `${left}px`;
  pop.style.top = `${top}px`;
}

export function closeMenu(host: EditHost): void {
  menuFor = undefined;
  host.menu.hidden = true;
}

/** Draw the open menu again, from the settings as they are now. */
export function refreshMenu(host: EditHost): void {
  if (!menuFor || host.menu.hidden) return;
  if (menuFor.kind === 'dial') drawDialMenu(host, menuFor.target);
  else if (host.settings().motion?.knobs.some((k) => k.id === (menuFor as { id: string }).id)) drawKnobMenu(host, menuFor.id);
  else closeMenu(host);
}

/** A click on the device, in Edit. Returns true if it was dealt with. */
export function deviceClick(target: HTMLElement, host: EditHost): boolean {
  if (!host.editing()) return false;
  const widget = target.closest<HTMLElement>('.dv-widget');
  if (!widget || target.closest('.edit-layer')) return false;
  const s = host.settings();
  if (linking) {
    if (!widget.dataset.target) return true;
    toggleRoute(s, linking, widget.dataset.target);
    if (!s.motion?.knobs.some((k) => k.id === linking)) linking = undefined;
    host.changed();
    return true;
  }
  if (widget.dataset.knob) {
    menuFor = { kind: 'knob', id: widget.dataset.knob };
    drawKnobMenu(host, widget.dataset.knob);
  } else if (widget.dataset.target) {
    menuFor = { kind: 'dial', target: widget.dataset.target };
    drawDialMenu(host, widget.dataset.target);
  } else return false;
  openMenu(host, widget.getBoundingClientRect());
  return true;
}

/** One way to turn a dial, as a row that is on or off, with how far under it when on. */
function choice(title: string, about: string, route: Route | undefined, toggle: () => void, host: EditHost): HTMLElement {
  const wrap = el('div', `edit-choice${route ? ' is-on' : ''}`);
  const row = button('edit-option', '', '', toggle);
  row.setAttribute('aria-pressed', String(!!route));
  row.append(el('i', 'edit-tick'), el('b', '', title), el('span', '', about));
  wrap.append(row);
  if (route) wrap.append(amountSlider(route, host));
  return wrap;
}

function drawDialMenu(host: EditHost, target: string): void {
  const s = host.settings();
  const kind = host.kind();
  const menu = host.menu;
  menu.replaceChildren(el('h2', '', targetLabel(kind, target)));
  const full = (s.motion?.routes.length ?? 0) >= SCHEMA.motion.maxRoutes;
  const act = (source: string) => (): void => { toggleRoute(s, source, target); host.changed(); };
  const routeOf = (source: string): Route | undefined => routesTo(s, target).find((r) => r.source === source);

  menu.append(el('p', 'edit-ask', 'Make it turn by itself'));
  for (const src of SCHEMA.motion.sources) {
    if (src.effectOnly && kind === 'instrument') continue;
    if (full && !routeOf(src.id)) continue;
    menu.append(choice(src.label, src.blurb ?? '', routeOf(src.id), act(src.id), host));
  }
  menu.append(el('p', 'edit-ask', 'Or put it on a knob of your own'));
  for (const knob of s.motion?.knobs ?? []) {
    if (full && !routeOf(knob.id)) continue;
    menu.append(choice(knob.name, 'Turning that knob turns this dial too.', routeOf(knob.id), act(knob.id), host));
  }
  if (!full && (s.motion?.knobs.length ?? 0) < SCHEMA.motion.knobs.max) {
    const short = motionTargets(kind).get(target)?.param.short ?? motionTargets(kind).get(target)?.param.label ?? '';
    const add = button('edit-option', '', '', () => {
      const id = addKnob(s, short);
      if (id) addRoute(s, id, target);
      host.changed();
    });
    add.append(el('i', 'edit-plus', '+'), el('b', '', 'A new knob'), el('span', '', 'One knob on the device that can turn this and other dials.'));
    menu.append(add);
  }
  if (full) menu.append(el('p', 'muted small', 'This device has all the turning it can hold.'));
}

function drawKnobMenu(host: EditHost, id: string): void {
  const s = host.settings();
  const kind = host.kind();
  const knob = s.motion?.knobs.find((k) => k.id === id);
  const menu = host.menu;
  menu.replaceChildren();
  if (!knob) return;
  menu.append(el('h2', '', 'Your knob'));
  const name = el('input', 'edit-name');
  name.value = knob.name;
  name.maxLength = 10;
  name.spellcheck = false;
  name.setAttribute('aria-label', 'The knob\'s name');
  name.addEventListener('change', () => {
    const typed = name.value.replace(/[^A-Za-z0-9 ]/g, '').trim().slice(0, 10);
    if (!typed || s.motion!.knobs.some((k) => k !== knob && k.name.toLowerCase() === typed.toLowerCase())) { name.value = knob.name; return; }
    knob.name = typed;
    host.changed();
  });
  menu.append(name);

  const routes = routesFrom(s, id);
  menu.append(el('p', 'edit-ask', routes.length ? 'It turns' : 'It turns nothing yet'));
  for (const route of routes) {
    const row = el('div', 'motion-row');
    const top = el('div', 'motion-pair');
    const x = button('motion-x', '', 'Unlink this dial', () => { removeRoute(s, route); host.changed(); });
    x.innerHTML = CROSS;
    top.append(el('b', '', targetLabel(kind, route.target)), x);
    row.append(top, amountSlider(route, host));
    menu.append(row);
  }
  const actions = el('div', 'edit-actions');
  actions.append(
    button('btn', 'Link dials', 'Click the dials this knob should turn', () => { linking = id; closeMenu(host); host.changed(); }),
    button('btn edit-delete', 'Delete knob', '', () => { removeKnob(s, id); closeMenu(host); host.changed(); }),
  );
  menu.append(actions);
}

// ── seeing it: a dial that is being turned shows where it is turned to ──────────

const A0 = 135;
const SWEEP = 270;
const CX = 22;
const CY = 25;
const RING = 14.4;   // just outside the dial's own ring
const SVG = 'http://www.w3.org/2000/svg';

const polar = (position: number): [number, number] => {
  const a = ((A0 + SWEEP * Math.min(1, Math.max(0, position))) * Math.PI) / 180;
  return [CX + RING * Math.cos(a), CY + RING * Math.sin(a)];
};

function arc(from: number, to: number): string {
  const [x0, y0] = polar(from);
  const [x1, y1] = polar(to);
  return `M ${x0} ${y0} A ${RING} ${RING} 0 ${(to - from) * SWEEP > 180 ? 1 : 0} 1 ${x1} ${y1}`;
}

/**
 * Show each moved dial being moved. The dial's own pointer stays where the person left
 * it; an arc outside its ring shows how far it is being turned either way, and, while
 * sound is playing, a dot runs along the arc at the value the effect is being given right
 * now. That value is read from the patch itself: the number leaving the last object under
 * the dial (fxgen.py, Device.moved), found by following the cords from the dial.
 * Returns what to call before the next device is drawn.
 */
export function watchMoved(
  patch: IRPatch, built: ReadonlyMap<string, MaxNode>, device: HTMLElement, s: Settings, index: DialIndex, changed: () => void = () => {},
): () => void {
  const offs: (() => void)[] = [];
  const next = new Map<string, { to: string; outlet: number }[]>();
  for (const e of patch.edges) {
    if (!next.has(e.from.id)) next.set(e.from.id, []);
    next.get(e.from.id)!.push({ to: e.to.id, outlet: e.from.outlet });
  }
  for (const node of patch.nodes) {
    if (node.maxclass !== 'live.dial') continue;
    const at = index.get(String(node.raw?.varname ?? ''));
    const key = at ? `${at.module}.${at.param}` : '';
    const routes = routesTo(s, key);
    if (!routes.length) continue;
    const param = liveParam(node.raw, 'live.dial');
    const widget = [...device.querySelectorAll<HTMLElement>('.dv-widget')].find((w) => w.dataset.target === key);
    if (!widget) continue;

    // dial (right outlet) -> sig~ -> clip~ -> snapshot~ -> [pow] -> scale: the scale is what the effect hears
    let id: string | undefined = next.get(node.id)?.find((x) => x.outlet === 1)?.to;
    let last: string | undefined;
    for (let hops = 0; id && hops < 6; hops++) {
      if (patch.byId.get(id)?.className === 'scale') { last = id; break; }
      id = next.get(id)?.[0]?.to;
    }

    const svg = document.createElementNS(SVG, 'svg');
    svg.setAttribute('viewBox', '0 0 44 48');
    svg.setAttribute('class', 'moved-ring');
    const range = document.createElementNS(SVG, 'path');
    range.setAttribute('class', 'moved-range');
    const dot = document.createElementNS(SVG, 'circle');
    dot.setAttribute('class', 'moved-dot');
    dot.setAttribute('r', '1.9');
    dot.setAttribute('opacity', '0');
    const grip = document.createElementNS(SVG, 'circle');
    grip.setAttribute('class', 'moved-grip');
    grip.setAttribute('r', '2.6');
    const tip = document.createElementNS(SVG, 'title');
    tip.textContent = 'Drag round the dial: how far it is turned';
    grip.append(tip);
    svg.append(range, dot, grip);
    widget.append(svg);

    // How far the dial can be turned from where it stands: a sweep swings both ways, a
    // knob or the loudness pushes one way.
    // The first thing turning it is the one the arc's handle sets. One that pushes one way
    // (a knob, the loudness) has its handle at that end; a sweep's is at the far end.
    const first = routes[0];
    const oneWay = isKnob(first.source) || first.source === 'follow';
    let standing = param.initial;
    const drawRange = (value: number): void => {
      standing = value;
      const here = paramPosition(param, value);
      let lo = 0;
      let hi = 0;
      for (const r of routes) {
        const a = r.amount / 100;
        if (isKnob(r.source) || r.source === 'follow') { if (a < 0) lo += a; else hi += a; } else { lo -= Math.abs(a); hi += Math.abs(a); }
      }
      range.setAttribute('d', arc(Math.max(0, here + lo), Math.min(1, here + hi)));
      const end = oneWay && first.amount < 0 ? Math.max(0, here + lo) : Math.min(1, here + hi);
      const [gx, gy] = polar(end);
      grip.setAttribute('cx', String(gx));
      grip.setAttribute('cy', String(gy));
    };
    drawRange(param.initial);

    // Dragging the handle round the dial: where it is let go, less where the dial
    // stands, is how far the dial is turned. It is drawn as it goes and built once.
    const toPosition = (e: PointerEvent): number => {
      const box = svg.getBoundingClientRect();
      const x = ((e.clientX - box.left) / box.width) * 44 - CX;
      const y = ((e.clientY - box.top) / box.height) * 48 - CY;
      const round = ((Math.atan2(y, x) * 180) / Math.PI - A0 + 720) % 360;
      // the gap at the bottom of the dial belongs to whichever end is nearer
      return round <= SWEEP ? round / SWEEP : round - SWEEP < (360 - SWEEP) / 2 ? 1 : 0;
    };
    let gripping = false;
    grip.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      gripping = true;
      grip.setPointerCapture(e.pointerId);
      e.preventDefault();
      e.stopPropagation();
    });
    grip.addEventListener('pointermove', (e) => {
      if (!gripping) return;
      const far = Math.round((toPosition(e) - paramPosition(param, standing)) * 100);
      const amount = oneWay ? far : Math.sign(first.amount || 1) * Math.abs(far);
      first.amount = Math.max(-100, Math.min(100, amount)) || (first.amount < 0 ? -1 : 1);   // never nothing: that would drop it
      drawRange(standing);
    });
    const letGo = (e: PointerEvent): void => {
      if (!gripping) return;
      gripping = false;
      if (grip.hasPointerCapture(e.pointerId)) grip.releasePointerCapture(e.pointerId);
      e.stopPropagation();
      changed();
    };
    grip.addEventListener('pointerup', letGo);
    grip.addEventListener('pointercancel', letGo);
    grip.addEventListener('click', (e) => e.stopPropagation());   // not a click on the dial, which opens its menu
    const dial = built.get(node.id);
    const offDial = dial?.onControlOut?.(0, (m) => { const v = firstNum(m); if (v !== undefined) drawRange(v); });
    if (typeof offDial === 'function') offs.push(offDial);

    let latest: number | undefined;
    let queued = false;
    let idle = 0;
    const off = last ? built.get(last)?.onControlOut?.(0, (m) => {
      latest = firstNum(m);
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => {
        queued = false;
        if (latest === undefined) return;
        const [x, y] = polar(paramPosition(param, latest));
        dot.setAttribute('cx', String(x));
        dot.setAttribute('cy', String(y));
        dot.setAttribute('opacity', '1');
        // no reading for a moment means the sound has stopped: put the dot away
        clearTimeout(idle);
        idle = window.setTimeout(() => dot.setAttribute('opacity', '0'), 400);
      });
    }) : undefined;
    if (typeof off === 'function') offs.push(off);
    offs.push(() => clearTimeout(idle));
  }
  return () => { for (const off of offs) off(); };
}
