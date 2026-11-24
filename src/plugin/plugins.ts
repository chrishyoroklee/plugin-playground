// What the page offers: categories of plugins, and presets inside each plugin.
//
// A plugin is one kind of device: a Parametric EQ, a Compressor. Its presets are settings
// of that same device (Smile Curve and Mid Push are both the Three Band EQ), so a preset
// can change any dial or type on it, and never what it is made of. A rack is a device made
// of several effects in a row (Shoegaze Wall: distortion, chorus, delay, reverb); it is
// listed apart from the plugins, and its presets share that same row.
//
// So what a device is made of decides where a preset goes: two presets are presets of one
// plugin exactly when they are in the same category and use the same effects in the same
// order, with the same things turning their dials. That is worked out here from
// presets.json and instruments.json, and only the names are written down.

import { INSTRUMENTS, PRESETS, SCHEMA, normalize, type Kind, type Preset, type Settings } from './model';

export interface Plugin {
  id: string;
  name: string;
  /** The section of the left panel it is listed in: Distortion, EQ, ... */
  category: string;
  /** A plugin is one effect (or the synth); a rack is several in a row. */
  rack: boolean;
  /** One line under its name. */
  blurb: string;
  /** What it is made of; a device with the same shape is this plugin. */
  shape: string;
  presets: Preset[];
}

// The name of the plugin each effect is, on its own.
const NAMES: Record<string, string> = {
  filter: 'Filter', eq: 'Three Band EQ', peq: 'Parametric EQ', geq: 'Graphic EQ', tilt: 'Tilt EQ',
  comp: 'Compressor', drive: 'Distortion', crush: 'Bitcrusher', chorus: 'Chorus & Flanger',
  tremolo: 'Tremolo & Pan', delay: 'Delay', reverb: 'Reverb',
};

// Racks that hold more than one preset, by category and shape. A rack with one preset is
// called what that preset is called.
const RACKS: Record<string, string> = {
  'Distortion|drive>reverb': 'Drive Room',
  'Chorus|chorus>reverb': 'Chorus Room',
  'Tremolo|tremolo>delay': 'Trem Echo',
  'Delay|delay>reverb': 'Echo Space',
  'Reverb|filter>reverb': 'Filtered Reverb',
};

const ARROW = ' → ';

/**
 * What a device is made of: the effects that are on, in order, and what turns which dial.
 * The values of the dials, the types (a filter's mode) and the colours are not part of it:
 * those are what presets change.
 */
export function shapeOf(s: Partial<Settings>): string {
  if (s.kind === 'instrument') return 'synth';
  const chain = (s.chain ?? []).filter((e) => e.on !== false).map((e) => e.module).join('>');
  const m = s.motion;
  if (!m) return chain;
  const routes = (m.routes ?? []).map((r) => `${r.source}:${r.target}`).sort().join(',');
  const knobs = (m.knobs ?? []).map((k) => k.name).join(',');
  return `${chain}|${routes}|${knobs}`;
}

const label = (module: string): string => SCHEMA.modules.find((m) => m.id === module)?.label ?? module;

function build(presets: Preset[], kind: Kind): Plugin[] {
  if (kind === 'instrument') {
    return [{
      id: 'synth', name: 'Synth', category: 'Synth', rack: false, shape: 'synth', presets,
      blurb: 'Two oscillators, a filter and an envelope on every voice',
    }];
  }
  const out: Plugin[] = [];
  const byKey = new Map<string, Plugin>();
  for (const p of presets) {
    const shape = shapeOf(p.settings);
    const key = `${p.group}|${shape}`;
    let plugin = byKey.get(key);
    if (!plugin) {
      const modules = (p.settings.chain ?? []).filter((e) => e.on !== false).map((e) => e.module);
      const moving = !!p.settings.motion;
      const one = modules.length === 1 && !moving;
      const name = one ? NAMES[modules[0]] ?? label(modules[0])
        : RACKS[`${p.group}|${modules.join('>')}`] ?? p.name;
      const spec = one ? SCHEMA.modules.find((m) => m.id === modules[0]) : undefined;
      plugin = {
        id: one ? modules[0] : p.id,
        name,
        category: p.group,
        rack: modules.length > 1,
        shape,
        presets: [],
        blurb: spec?.blurb ?? (modules.length ? modules.map(label).join(ARROW) : 'Nothing on it yet: add effects in Edit'),
      };
      byKey.set(key, plugin);
      out.push(plugin);
    }
    plugin.presets.push(p);
  }
  return out;
}

const EFFECT_PLUGINS = build(PRESETS, 'effect');
const INSTRUMENT_PLUGINS = build(INSTRUMENTS, 'instrument');

export const pluginsOf = (kind: Kind): Plugin[] => (kind === 'instrument' ? INSTRUMENT_PLUGINS : EFFECT_PLUGINS);

/** The plugin a preset is a preset of. */
export function pluginOfPreset(kind: Kind, presetId: string): Plugin | undefined {
  return pluginsOf(kind).find((p) => p.presets.some((x) => x.id === presetId));
}

/** The plugin these settings are, if they are one: the same shape as it, whatever the dials say. */
export function pluginOfSettings(kind: Kind, s: Settings, hint?: string): Plugin | undefined {
  const shape = shapeOf(s);
  const all = pluginsOf(kind);
  return all.find((p) => p.id === hint && p.shape === shape) ?? all.find((p) => p.shape === shape);
}

/**
 * A plugin as it comes, before any preset: every dial at its default. Only a plugin that is
 * one effect has one; a rack is its presets.
 */
export function defaultsOf(plugin: Plugin, kind: Kind): Settings | undefined {
  if (plugin.rack || plugin.presets[0]?.settings.motion) return undefined;
  const first = plugin.presets[0].settings;
  const base = kind === 'instrument'
    ? { kind: 'instrument' as const, name: plugin.name, accent: first.accent, output: first.output }
    : { name: plugin.name, accent: first.accent, chain: (first.chain ?? []).map((e) => ({ module: e.module, on: e.on })) };
  return normalize(base);
}
