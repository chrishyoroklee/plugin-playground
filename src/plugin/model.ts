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

export interface Schema {
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

export interface Settings {
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
  if (kind === 'effect') return { name, accent, chain, output };

  const instrument: Record<string, Section> = {};
  for (const mod of SCHEMA.instrument.modules) {
    const { options, values } = normalizeEntry(mod, s.instrument?.[mod.id] ?? {});
    instrument[mod.id] = { options, values };
  }
  return { kind, instrument, name, accent, chain, output };
}

/** Where a module's dial values live in the settings: the synth, the chain or the output. */
export function valuesOf(s: Settings, module: string): Record<string, number> | undefined {
  if (module === SCHEMA.output.id) return s.output;
  return s.instrument?.[module]?.values ?? s.chain.find((e) => e.module === module)?.values;
}

/**
 * The box `varname` fxgen gives each dial ("filter_cutoff"), mapped back to where its
 * value lives in the settings. The page listens to the running dials through this, so a
 * knob turned in the device view is the value that gets baked into the download.
 */
export function dialIndex(): Map<string, { module: string; param: string }> {
  const key = (modLabel: string, paramLabel: string): string => `${modLabel} ${paramLabel}`.toLowerCase().replace(/ /g, '_');
  const out = new Map<string, { module: string; param: string }>();
  for (const m of [...SCHEMA.modules, ...SCHEMA.instrument.modules]) {
    for (const p of m.params) out.set(key(m.label, p.label), { module: m.id, param: p.id });
  }
  for (const p of SCHEMA.output.params) out.set(key(SCHEMA.output.label, p.label), { module: SCHEMA.output.id, param: p.id });
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
