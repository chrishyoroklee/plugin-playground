// The settings model: what the left panel edits and what fxgen.py turns into a device.
//
// schema.json is the single description of the modules, shared with the Python
// generator, so a module added there appears in both places. normalize() here mirrors
// fxgen.normalize() closely enough for the UI to have complete settings before Python
// has loaded; Python normalizes again, and its word is final for what gets built.

import schemaJson from './schema.json';
import presetsJson from './presets.json';
import instrumentsJson from './instruments.json';

export interface ParamSpec {
  id: string;
  label: string;
  short?: string;
  min: number;
  max: number;
  default: number;
  unit: string;
  exponent?: number;
  integer?: boolean;
}

export interface OptionSpec {
  id: string;
  label: string;
  default: string;
  choices: { id: string; label: string }[];
}

export interface ModuleSpec {
  id: string;
  label: string;
  blurb?: string;
  options: OptionSpec[];
  params: ParamSpec[];
}

/** Something that can move a dial: an LFO, or a follower of the incoming volume. */
export interface SourceSpec extends ModuleSpec {
  kind: 'lfo' | 'follower';
  /** True for a source that needs audio coming in, which an instrument has none of. */
  effectOnly?: boolean;
}

export interface MotionSpec {
  sources: SourceSpec[];
  /** The knobs a user adds: how many, and the dial each one is. */
  knobs: { id: string; label: string; max: number; param: Omit<ParamSpec, 'id' | 'label'> };
  maxRoutes: number;
}

export interface Schema {
  motion: MotionSpec;
  modules: ModuleSpec[];
  /** The sections of an instrument's synth: always there, in this order. */
  instrument: { modules: ModuleSpec[] };
  output: { id: string; label: string; params: ParamSpec[] };
  accents: string[];
}

/** An effect takes the track's audio. An instrument takes notes and makes its own. */
export type Kind = 'effect' | 'instrument';

/** One section of the synth: what is chosen and where its dials stand. */
export interface Section {
  options: Record<string, string>;
  values: Record<string, number>;
}

export interface ChainEntry {
  module: string;
  on: boolean;
  options: Record<string, string>;
  values: Record<string, number>;
}

/** One source moving one dial. `target` is "module.dial"; `amount` is percent of the dial's travel, either way. */
export interface Route {
  source: string;
  target: string;
  amount: number;
}

/** A knob the user added. Its id is k1, k2 ...; its name is theirs. */
export interface Knob {
  id: string;
  name: string;
}

/** What moves the dials. Only written on a device that has a route or a knob, as fxgen.py does. */
export interface Motion {
  /** The fixed sources' settings, by source id (lfo1, lfo2, follow). */
  sources: Record<string, Section>;
  knobs: Knob[];
  knobValues: Record<string, number>;
  routes: Route[];
}

export interface Settings {
  motion?: Motion;
  /** Only written on an instrument, as fxgen.py does; an effect has neither field. */
  kind?: 'instrument';
  instrument?: Record<string, Section>;
  name: string;
  accent: string;
  chain: ChainEntry[];
  output: Record<string, number>;
}

export interface Preset {
  id: string;
  name: string;
  tag: string;
  group: string;
  sample: string | null;
  settings: Partial<Settings>;
}

export const SCHEMA = schemaJson as unknown as Schema;
export const PRESETS = presetsJson as unknown as Preset[];
export const INSTRUMENTS = instrumentsJson as unknown as Preset[];

export const presetsOf = (kind: Kind): Preset[] => (kind === 'instrument' ? INSTRUMENTS : PRESETS);
export const kindOf = (s: Pick<Settings, 'kind'>): Kind => (s.kind === 'instrument' ? 'instrument' : 'effect');

export const moduleSpec = (id: string): ModuleSpec | undefined => SCHEMA.modules.find((m) => m.id === id);

const DEFAULT_NAME: Record<Kind, string> = { effect: 'MaxPy FX', instrument: 'MaxPy Synth' };

function clampParam(p: ParamSpec, value: unknown): number {
  let v = Number(value);
  if (!Number.isFinite(v)) v = p.default;
  v = Math.min(p.max, Math.max(p.min, v));
  return p.integer ? Math.round(v) : v;
}

function normalizeEntry(mod: ModuleSpec, entry: Partial<ChainEntry>): ChainEntry {
  const options: Record<string, string> = {};
  for (const opt of mod.options) {
    const given = entry.options?.[opt.id];
    options[opt.id] = opt.choices.some((c) => c.id === given) ? (given as string) : opt.default;
  }
  const values: Record<string, number> = {};
  for (const p of mod.params) values[p.id] = clampParam(p, entry.values?.[p.id] ?? p.default);
  return { module: mod.id, on: entry.on ?? true, options, values };
}

/** Every dial something can move, by "module.dial". Whole-number dials are left out, as in fxgen.py. */
export function motionTargets(kind: Kind): Map<string, { module: ModuleSpec | Schema['output']; param: ParamSpec }> {
  const mods = [...SCHEMA.modules, SCHEMA.output, ...(kind === 'instrument' ? SCHEMA.instrument.modules : [])];
  const out = new Map<string, { module: ModuleSpec | Schema['output']; param: ParamSpec }>();
  for (const m of mods) for (const p of m.params) if (!p.integer) out.set(`${m.id}.${p.id}`, { module: m, param: p });
  return out;
}

/** The sources a device of this kind can use: the fixed ones, then its knobs. */
export function motionSources(kind: Kind, motion: Motion | undefined): { id: string; label: string }[] {
  const fixed = SCHEMA.motion.sources.filter((s) => !(s.effectOnly && kind === 'instrument')).map((s) => ({ id: s.id, label: s.label }));
  return [...fixed, ...(motion?.knobs ?? []).map((k) => ({ id: k.id, label: k.name }))];
}

/** A device's motion with nothing in it yet, ready to be given a route or a knob. */
export function emptyMotion(): Motion {
  const sources: Record<string, Section> = {};
  for (const spec of SCHEMA.motion.sources) {
    const { options, values } = normalizeEntry(spec, {});
    sources[spec.id] = { options, values };
  }
  return { sources, knobs: [], knobValues: {}, routes: [] };
}

// fxgen.py keeps each source's settings beside the knobs and routes ({lfo1: .., routes: ..});
// here they sit under `sources`. These two turn one into the other.
type WireMotion = Record<string, unknown> & { knobs?: unknown; knobValues?: unknown; routes?: unknown };

function normalizeMotion(given: unknown, kind: Kind): Motion | undefined {
  if (!given || typeof given !== 'object') return undefined;
  const g = given as WireMotion & { sources?: Record<string, Partial<Section>> };
  const spec = SCHEMA.motion;
  const out = emptyMotion();
  for (const source of spec.sources) {
    const entry = (g.sources?.[source.id] ?? g[source.id]) as Partial<ChainEntry> | undefined;
    const { options, values } = normalizeEntry(source, entry && typeof entry === 'object' ? entry : {});
    out.sources[source.id] = { options, values };
  }

  const givenValues = (g.knobValues && typeof g.knobValues === 'object' ? g.knobValues : {}) as Record<string, unknown>;
  const renamed = new Map<string, string>();
  const taken = new Set<string>();
  for (const knob of Array.isArray(g.knobs) ? (g.knobs as Partial<Knob>[]) : []) {
    if (out.knobs.length >= spec.knobs.max || !knob || typeof knob !== 'object') continue;
    const id = `k${out.knobs.length + 1}`;
    let name = String(knob.name ?? '').replace(/[^A-Za-z0-9 ]/g, '').trim().slice(0, 10) || `Knob ${out.knobs.length + 1}`;
    while (taken.has(name.toLowerCase())) name = `${name.slice(0, 8)} ${out.knobs.length + 1}`.trim();
    taken.add(name.toLowerCase());
    const was = String(knob.id ?? id);
    renamed.set(was, id);
    out.knobs.push({ id, name });
    out.knobValues[id] = clampParam({ ...spec.knobs.param, id, label: name }, givenValues[was] ?? 0);
  }

  const targets = motionTargets(kind);
  const sources = new Set(motionSources(kind, out).map((s) => s.id));
  const seen = new Set<string>();
  for (const route of Array.isArray(g.routes) ? (g.routes as Partial<Route>[]) : []) {
    if (!route || typeof route !== 'object' || out.routes.length >= spec.maxRoutes) continue;
    const source = renamed.get(String(route.source)) ?? String(route.source);
    const target = String(route.target);
    const amount = Math.max(-100, Math.min(100, Number(route.amount ?? 0)));
    const key = `${source}>${target}`;
    if (!sources.has(source) || !targets.has(target) || seen.has(key) || !Number.isFinite(amount) || amount === 0) continue;
    seen.add(key);
    out.routes.push({ source, target, amount });
  }
  return out.routes.length || out.knobs.length ? out : undefined;
}

/** A device's settings as fxgen.py reads them: the motion's sources beside its knobs and routes. */
export function toWire(s: Settings): Record<string, unknown> {
  if (!s.motion) return s as unknown as Record<string, unknown>;
  const { sources, ...rest } = s.motion;
  return { ...s, motion: { ...sources, ...rest } };
}

export function normalize(input: unknown): Settings {
  const s = (input && typeof input === 'object' ? input : {}) as Partial<Settings>;
  const kind = kindOf(s);
  const name = String(s.name ?? DEFAULT_NAME[kind]).replace(/[^A-Za-z0-9 _-]/g, '').trim().slice(0, 32) || DEFAULT_NAME[kind];
  const accent = typeof s.accent === 'string' && /^#[0-9a-fA-F]{6}$/.test(s.accent) ? s.accent.toLowerCase() : SCHEMA.accents[0];

  const chain: ChainEntry[] = [];
  const seen = new Set<string>();
  for (const entry of Array.isArray(s.chain) ? s.chain : []) {
    const mod = entry && typeof entry === 'object' ? moduleSpec(entry.module) : undefined;
    if (!mod || seen.has(mod.id)) continue;
    seen.add(mod.id);
    chain.push(normalizeEntry(mod, entry));
  }
  for (const mod of SCHEMA.modules) if (!seen.has(mod.id)) chain.push(normalizeEntry(mod, { on: false }));

  const output: Record<string, number> = {};
  for (const p of SCHEMA.output.params) output[p.id] = clampParam(p, s.output?.[p.id] ?? p.default);
  const motion = normalizeMotion(s.motion, kind);
  if (kind === 'effect') return { name, accent, chain, output, ...(motion ? { motion } : {}) };

  const instrument: Record<string, Section> = {};
  for (const mod of SCHEMA.instrument.modules) {
    const { options, values } = normalizeEntry(mod, s.instrument?.[mod.id] ?? {});
    instrument[mod.id] = { options, values };
  }
  return { kind, instrument, name, accent, chain, output, ...(motion ? { motion } : {}) };
}

/** Where a module's dial values live in the settings: the synth, the chain or the output. */
export function valuesOf(s: Settings, module: string): Record<string, number> | undefined {
  if (module === SCHEMA.output.id) return s.output;
  if (module === SCHEMA.motion.knobs.id) return s.motion?.knobValues;
  if (s.motion?.sources[module]) return s.motion.sources[module].values;
  return s.instrument?.[module]?.values ?? s.chain.find((e) => e.module === module)?.values;
}

/**
 * The box `varname` fxgen gives each dial ("filter_cutoff"), mapped back to where its
 * value lives in the settings. The page listens to the running dials through this, so a
 * knob turned in the device view is the value that gets baked into the download.
 */
export function dialIndex(motion?: Motion): Map<string, { module: string; param: string }> {
  const key = (modLabel: string, paramLabel: string): string => `${modLabel} ${paramLabel}`.toLowerCase().replace(/ /g, '_');
  const out = new Map<string, { module: string; param: string }>();
  for (const m of [...SCHEMA.modules, ...SCHEMA.instrument.modules]) {
    for (const p of m.params) out.set(key(m.label, p.label), { module: m.id, param: p.id });
  }
  for (const p of SCHEMA.output.params) out.set(key(SCHEMA.output.label, p.label), { module: SCHEMA.output.id, param: p.id });
  // The dials motion adds: each source's, and one per knob, named by the user.
  for (const m of SCHEMA.motion.sources) for (const p of m.params) out.set(key(m.label, p.label), { module: m.id, param: p.id });
  for (const k of motion?.knobs ?? []) out.set(key(`${SCHEMA.motion.knobs.label}s`, k.name), { module: SCHEMA.motion.knobs.id, param: k.id });
  return out;
}

// ── sharing: settings in the URL fragment ───────────────────────────────────────

export function encodeSettings(s: Settings): string {
  // Only what is switched on, in order. normalize() restores the rest.
  const slim = {
    kind: s.kind,
    instrument: s.instrument,
    name: s.name,
    accent: s.accent,
    chain: s.chain.filter((e) => e.on).map((e) => ({ module: e.module, on: true, options: e.options, values: e.values })),
    output: s.output,
    motion: s.motion,
  };
  const bytes = new TextEncoder().encode(JSON.stringify(slim));
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function decodeSettings(fragment: string): Settings | undefined {
  try {
    const b64 = fragment.replace(/-/g, '+').replace(/_/g, '/');
    const bin = atob(b64);
    const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
    return normalize(JSON.parse(new TextDecoder().decode(bytes)));
  } catch {
    return undefined;
  }
}
