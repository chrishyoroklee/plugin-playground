// The plugin generator's browser half: the Max for Live objects the engine gained for it
// (live.dial, plugin~, plugout~, overdrive~), and the settings model the page edits.
// The Python half, fxgen.py, is tested natively in tests/test_fxgen.py.

import { existsSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import '../src/objects/ui/live-dial';
import '../src/objects/audio/plug';
import '../src/objects/audio/dynamics';
import { getFactory, isSupported } from '../src/engine/registry';
import { formatParam, liveParam, paramPosition, paramValue } from '../src/objects/ui/live-param';
import { hostBus } from '../src/runtime/host';
import type { IRNode } from '../src/ir/types';
import type { Msg } from '../src/runtime/atoms';
import { highlightPython } from '../src/plugin/device-view';
import {
  INSTRUMENTS, PRESETS, SCHEMA, decodeSettings, dialIndex, encodeSettings, kindOf, normalize, toWire, valuesOf,
} from '../src/plugin/model';
import '../src/objects/control/poly';
import { SOUNDS, SOUND_RATE, findSound } from '../src/plugin/sounds';

const ctx = new (globalThis as any).OfflineAudioContext(2, 128, 44100) as BaseAudioContext;

function dialNode(valueof: Record<string, unknown>): IRNode {
  return {
    id: 'obj-1', className: 'live.dial', args: [], maxclass: 'live.dial', numInlets: 1, numOutlets: 2,
    outletDomains: ['control', 'control'], rect: [0, 0, 44, 48], text: 'live.dial',
    raw: { saved_attribute_attributes: { valueof } },
  };
}

function dial(valueof: Record<string, unknown>) {
  const node = getFactory('live.dial')!([], { ctx, node: dialNode(valueof) });
  const out: Msg[] = [];
  const raw: Msg[] = [];
  node.onControlOut!(0, (m) => out.push(m));
  node.onControlOut!(1, (m) => raw.push(m));
  return { node, out, raw, send: (m: Msg) => node.controlIns![0]!(m) };
}

const CUTOFF = {
  parameter_longname: 'Filter Cutoff', parameter_shortname: 'Cutoff', parameter_mmin: 40, parameter_mmax: 18000,
  parameter_initial_enable: 1, parameter_initial: [1800], parameter_unitstyle: 3, parameter_exponent: 3,
};

describe('live parameter block', () => {
  it('reads range, unit, curve and start value off the box', () => {
    const p = liveParam({ saved_attribute_attributes: { valueof: CUTOFF } });
    expect(p).toMatchObject({ longname: 'Filter Cutoff', shortname: 'Cutoff', min: 40, max: 18000, initial: 1800, exponent: 3, unitstyle: 3 });
  });

  it('falls back to Max defaults when the box has no block', () => {
    expect(liveParam(undefined, 'live.dial')).toMatchObject({ longname: 'live.dial', min: 0, max: 127, initial: 0, exponent: 1 });
  });

  it('maps position to value along the exponent, and back', () => {
    const p = liveParam({ saved_attribute_attributes: { valueof: CUTOFF } });
    expect(paramValue(p, 0)).toBe(40);
    expect(paramValue(p, 1)).toBe(18000);
    expect(paramValue(p, 0.5)).toBeCloseTo(40 + 17960 * 0.125, 6);
    for (const v of [40, 250, 1800, 18000]) expect(paramValue(p, paramPosition(p, v))).toBeCloseTo(v, 6);
  });

  it('prints values the way Live does', () => {
    const unit = (unitstyle: number) => liveParam({ saved_attribute_attributes: { valueof: { parameter_unitstyle: unitstyle } } });
    expect(formatParam(unit(3), 3400)).toBe('3.4 kHz');
    expect(formatParam(unit(3), 440)).toBe('440 Hz');
    expect(formatParam(unit(2), 320)).toBe('320 ms');
    expect(formatParam(unit(2), 1500)).toBe('1.5 s');
    expect(formatParam(unit(4), 0)).toBe('0 dB');
    expect(formatParam(unit(4), 3)).toBe('+3 dB');
    expect(formatParam(unit(5), 38)).toBe('38 %');
    expect(formatParam(unit(0), 11.2)).toBe('11');
  });
});

describe('live.dial', () => {
  it('is a real object, not a stub', () => {
    expect(isSupported('live.dial')).toBe(true);
  });

  it('outputs its start value when the transport starts, value left and position right', () => {
    const d = dial(CUTOFF);
    expect(d.out).toEqual([]); // nothing from the constructor: the cords do not exist yet
    d.node.start!();
    expect(d.out).toEqual([[1800]]);
    expect(d.raw[0][0]).toBeCloseTo(((1800 - 40) / 17960) ** (1 / 3), 6);
  });

  it('a number sets and outputs, clamped to the range', () => {
    const d = dial(CUTOFF);
    d.send([500]);
    d.send([99999]);
    d.send([-5]);
    expect(d.out).toEqual([[500], [18000], [40]]);
  });

  it('set stores silently, bang re-outputs, init returns to the start value', () => {
    const d = dial(CUTOFF);
    d.send(['set', 900]);
    expect(d.out).toEqual([]);
    d.send(['bang']);
    expect(d.out).toEqual([[900]]);
    d.send(['init']);
    expect(d.out[1]).toEqual([1800]);
  });

  it('rawfloat sets from a 0..1 position', () => {
    const d = dial(CUTOFF);
    d.send(['rawfloat', 1]);
    d.send(['rawfloat', 0]);
    expect(d.out).toEqual([[18000], [40]]);
  });

  it('an int parameter only ever holds whole numbers', () => {
    const d = dial({ parameter_type: 1, parameter_mmin: 2, parameter_mmax: 16, parameter_initial_enable: 1, parameter_initial: [8] });
    d.send([10.6]);
    expect(d.out).toEqual([[11]]);
  });
});

describe('plugin~ / plugout~', () => {
  it('one pair of host buses per context', () => {
    expect(hostBus(ctx)).toBe(hostBus(ctx));
    const other = new (globalThis as any).OfflineAudioContext(2, 128, 44100) as BaseAudioContext;
    expect(hostBus(other)).not.toBe(hostBus(ctx));
  });

  it('plugin~ has two signal outlets, plugout~ two signal inlets that pass through', () => {
    const plugin = getFactory('plugin~')!([], { ctx });
    const plugout = getFactory('plugout~')!([], { ctx });
    expect(plugin.signalOuts.filter(Boolean).length).toBe(2);
    expect(plugout.signalIns.filter(Boolean).length).toBe(2);
    expect(plugout.signalOuts.filter(Boolean).length).toBe(2);
    expect(isSupported('plugin~') && isSupported('plugout~')).toBe(true);
  });

  it('both can be torn down, twice, without throwing', () => {
    for (const name of ['plugin~', 'plugout~']) {
      const node = getFactory(name)!([], { ctx });
      expect(() => { node.dispose!(); node.dispose!(); }).not.toThrow();
    }
  });
});

describe('overdrive~', () => {
  it('has a signal inlet and outlet, and takes its drive on inlet 1', () => {
    const node = getFactory('overdrive~')!([3], { ctx });
    expect(node.signalIns[0]).toBeDefined();
    expect(node.signalOuts[0]).toBeDefined();
    expect(() => node.controlIns![1]!([8])).not.toThrow();
  });

  it('is clean at drive 1 and keeps full scale at full scale at any drive', () => {
    for (const drive of [1, 4, 12]) {
      const node = getFactory('overdrive~')!([drive], { ctx });
      const curve = (node.signalIns[0] as WaveShaperNode).curve!;
      expect(curve[0]).toBeCloseTo(-1, 5);
      expect(curve[curve.length - 1]).toBeCloseTo(1, 5);
      const quarter = curve[Math.round((curve.length - 1) * 0.625)]; // input 0.25
      if (drive === 1) expect(quarter).toBeCloseTo(0.25, 2);
      else expect(quarter).toBeGreaterThan(0.3); // quiet input is pushed up
    }
  });
});

describe('settings model', () => {
  it('normalize fills in every module, off, after the ones that were given', () => {
    const s = normalize({ chain: [{ module: 'reverb' }, { module: 'filter' }] });
    expect(s.chain.map((e) => e.module).slice(0, 2)).toEqual(['reverb', 'filter']);
    expect(s.chain.length).toBe(SCHEMA.modules.length);
    expect(s.chain.filter((e) => e.on).map((e) => e.module)).toEqual(['reverb', 'filter']);
  });

  it('normalize clamps values, rejects unknown options and cleans the name', () => {
    const s = normalize({
      name: '<script>alert(1)</script>',
      accent: 'javascript:1',
      chain: [{ module: 'filter', values: { cutoff: 1e9, res: 'x' }, options: { mode: 'nope' } }, { module: 'filter' }, { module: 'ghost' }],
      output: { gain: 500 },
    });
    expect(s.name).toBe('scriptalert1script');
    expect(s.accent).toBe(SCHEMA.accents[0]);
    const filter = s.chain[0];
    expect(filter.values.cutoff).toBe(10000); // a quarter of the sample rate is as far as svf~ goes
    expect(filter.values.res).toBe(30);
    expect(filter.options.mode).toBe('lowpass');
    expect(s.chain.filter((e) => e.module === 'filter').length).toBe(1);
    expect(s.output.gain).toBe(12);
    expect(normalize(null)).toEqual(normalize({}));
  });

  it('a shared link round-trips every preset', () => {
    for (const preset of PRESETS) {
      const s = normalize(preset.settings);
      const back = decodeSettings(encodeSettings(s))!;
      expect(back.name).toBe(s.name);
      expect(back.chain.filter((e) => e.on)).toEqual(s.chain.filter((e) => e.on));
      expect(back.output).toEqual(s.output);
    }
    expect(decodeSettings('not base64 json')).toBeUndefined();
  });

  it('every dial fxgen names maps back to a setting', () => {
    const index = dialIndex();
    // the modules' dials, the output's, and the ones motion's sources add (LFO 1 Rate ...)
    const count = [...SCHEMA.modules, ...SCHEMA.instrument.modules, ...SCHEMA.motion.sources].reduce((n, m) => n + m.params.length, SCHEMA.output.params.length);
    expect(index.size).toBe(count);
    expect(index.get('sweep_speed')).toEqual({ module: 'lfo1', param: 'rate' });
    expect(index.get('filter_cutoff')).toEqual({ module: 'filter', param: 'cutoff' });
    expect(index.get('delay_feedback')).toEqual({ module: 'delay', param: 'feedback' });
    expect(index.get('output_gain')).toEqual({ module: 'output', param: 'gain' });
  });

  it('every preset names real modules, and its sample is one the page ships', () => {
    const modules = new Set(SCHEMA.modules.map((m) => m.id));
    for (const p of PRESETS) {
      for (const e of p.settings.chain ?? []) expect(modules.has(e.module)).toBe(true);
      if (p.sample) expect(SOUNDS.filter((s) => s.url).map((s) => s.id)).toContain(p.sample);
    }
  });
});

describe('script view', () => {
  it('escapes the source and marks comments, strings and gap references', () => {
    const html = highlightPython('x = place("a<b>", 1, 2.5)  # why (gap M4L-01)\nreturn x');
    expect(html).not.toContain('<b>');
    expect(html).toContain('<span class="c-str">"a&lt;b&gt;"</span>');
    expect(html).toContain('<span class="c-gap">gap M4L-01</span>');
    expect(html).toContain('<span class="c-kw">return</span>');
    expect(html).toContain('<span class="c-num">2.5</span>');
  });

  it('does not treat a # inside a string as a comment', () => {
    expect(highlightPython('c = "#ff7a45"')).toBe('c = <span class="c-str">"#ff7a45"</span>');
  });
});

describe('sound library', () => {
  const computed = SOUNDS.filter((s) => s.render);

  it('lists the recordings and the computed sounds, each once, each one or the other', () => {
    expect(new Set(SOUNDS.map((s) => s.id)).size).toBe(SOUNDS.length);
    const recordings = SOUNDS.filter((s) => s.url).map((s) => s.id);
    expect(recordings).toEqual(['piano', 'electric', 'riff', 'strat', 'guitar', 'vocals', 'drums']);
    // each is a file the page ships, credited
    const credits = readFileSync(new URL('../public/samples/fx/CREDITS.md', import.meta.url), 'utf8');
    for (const id of recordings) {
      expect(existsSync(new URL(`../public/samples/fx/${id}.mp3`, import.meta.url)), id).toBe(true);
      expect(credits, id).toContain(`\`${id}.mp3\``);
    }
    expect(computed.length).toBeGreaterThanOrEqual(10);
    for (const s of SOUNDS) expect(Boolean(s.url) !== Boolean(s.render), s.id).toBe(true);
  });

  it('has the sound every preset asks for', () => {
    for (const p of PRESETS) if (p.sample) expect(findSound(p.sample), p.id).toBeDefined();
  });

  it.each(computed.map((s) => [s.id, s] as const))('%s is a loop of sensible length, at half scale', (_id, sound) => {
    const channels = sound.render!();
    expect([1, 2]).toContain(channels.length);
    const seconds = channels[0].length / SOUND_RATE;
    expect(seconds).toBeGreaterThanOrEqual(1);
    expect(seconds).toBeLessThanOrEqual(10);
    let peak = 0;
    for (const ch of channels) {
      expect(ch.length).toBe(channels[0].length);
      for (const v of ch) {
        if (!Number.isFinite(v)) throw new Error(`${sound.id} has a sample that is not a number`);
        peak = Math.max(peak, Math.abs(v));
      }
    }
    expect(peak).toBeCloseTo(0.5, 5);
  });

  it('computes the same sound every time', () => {
    for (const id of ['arpeggio', 'drum-machine', 'pink']) {
      const sound = findSound(id)!;
      expect(sound.render!()[0]).toEqual(sound.render!()[0]);
    }
  });
});

describe('instruments', () => {
  it('an instrument has the synth sections, and an effect does not', () => {
    const synth = normalize({ kind: 'instrument' });
    expect(kindOf(synth)).toBe('instrument');
    expect(synth.name).toBe('My Synth');
    expect(Object.keys(synth.instrument!)).toEqual(SCHEMA.instrument.modules.map((m) => m.id));
    const effect = normalize({});
    expect(kindOf(effect)).toBe('effect');
    expect(effect.kind).toBeUndefined();
    expect(effect.instrument).toBeUndefined();
  });

  it('bounds what it is given, like any other setting', () => {
    const s = normalize({ kind: 'instrument', instrument: { osc: { options: { wave: 'nope', voices: '99' }, values: { detune: 1e9 } } } });
    expect(s.instrument!.osc.options).toEqual({ wave: 'saw', second: 'unison', voices: '6' });
    expect(s.instrument!.osc.values.detune).toBe(50);
  });

  it('every instrument preset is complete, and survives a shared link', () => {
    expect(new Set(INSTRUMENTS.map((p) => p.id)).size).toBe(INSTRUMENTS.length);
    for (const p of INSTRUMENTS) {
      const s = normalize(p.settings);
      expect(kindOf(s), p.id).toBe('instrument');
      expect(decodeSettings(encodeSettings(s)), p.id).toEqual(s);
    }
  });

  it('finds a dial\'s value in the synth, the chain or the output', () => {
    const s = normalize(INSTRUMENTS[0].settings);
    expect(dialIndex().get('tone_cutoff')).toEqual({ module: 'tone', param: 'cutoff' });
    expect(valuesOf(s, 'tone')).toBe(s.instrument!.tone.values);
    expect(valuesOf(s, 'delay')).toBe(s.chain.find((e) => e.module === 'delay')!.values);
    expect(valuesOf(s, 'output')).toBe(s.output);
  });
});

describe('poly', () => {
  // What leaves the object for each note: [voice, pitch, velocity], as the pack below it sees them.
  function poly(...args: number[]) {
    const node = getFactory('poly')!(args, { ctx });
    const sent: number[][] = [];
    const latest = [0, 0, 0];
    for (const outlet of [2, 1]) node.onControlOut!(outlet, (m) => { latest[outlet] = m[0] as number; });
    node.onControlOut!(0, (m) => sent.push([m[0] as number, latest[1], latest[2]]));
    const note = (pitch: number, velocity: number): void => {
      node.controlIns![1]!([velocity]);
      node.controlIns![0]!([pitch]);
    };
    return { sent, note };
  }

  it('gives each note the lowest free voice, and its note-off to the same one', () => {
    const { sent, note } = poly(3, 1);
    note(60, 100); note(64, 90); note(60, 0); note(67, 80);
    expect(sent).toEqual([[1, 60, 100], [2, 64, 90], [1, 60, 0], [1, 67, 80]]);
  });

  it('steals the voice held longest, letting its note go first', () => {
    const { sent, note } = poly(2, 1);
    note(60, 100); note(64, 100); note(67, 100);
    expect(sent.slice(2)).toEqual([[1, 60, 0], [1, 67, 100]]);
    note(60, 0); // already stolen: nothing to let go
    expect(sent).toHaveLength(4);
  });

  it('drops the extra note when stealing is off', () => {
    const { sent, note } = poly(1, 0);
    note(60, 100); note(64, 100);
    expect(sent).toEqual([[1, 60, 100]]);
  });
});

describe('motion: what moves the dials', () => {
  const moving = {
    chain: [{ module: 'filter', on: true }, { module: 'drive', on: true }],
    motion: {
      lfo1: { options: { shape: 'triangle' }, values: { rate: 2 } },
      knobs: [{ id: 'a', name: 'Wobble!' }, { id: 'b', name: 'wobble' }],
      knobValues: { a: 30 },
      routes: [
        { source: 'lfo1', target: 'filter.cutoff', amount: 30 },
        { source: 'a', target: 'filter.cutoff', amount: 50 },
        { source: 'follow', target: 'drive.amount', amount: 60 },
        { source: 'lfo9', target: 'filter.cutoff', amount: 10 },
        { source: 'lfo1', target: 'crush.bits', amount: 10 },
        { source: 'lfo1', target: 'filter.cutoff', amount: 99 },
        { source: 'lfo2', target: 'filter.res', amount: 900 },
      ],
    },
  };

  it('a device with none has the settings it always had', () => {
    expect(normalize({ chain: [{ module: 'filter', on: true }] }).motion).toBeUndefined();
    expect(normalize({ motion: { routes: [] } }).motion).toBeUndefined();
  });

  it('is bounded the way fxgen.py bounds it', () => {
    // the same input and the same answers as test_motion_is_bounded_like_everything_else in tests/test_fxgen.py
    const m = normalize(moving).motion!;
    expect(m.routes).toEqual([
      { source: 'lfo1', target: 'filter.cutoff', amount: 30 },
      { source: 'k1', target: 'filter.cutoff', amount: 50 },
      { source: 'follow', target: 'drive.amount', amount: 60 },
      { source: 'lfo2', target: 'filter.res', amount: 100 },
    ]);
    expect(m.knobs).toEqual([{ id: 'k1', name: 'Wobble' }, { id: 'k2', name: 'wobble 2' }]);
    expect(m.knobValues).toEqual({ k1: 30, k2: 0 });
    expect(m.sources.lfo1.options.shape).toBe('triangle');
    expect(m.sources.lfo2.values.rate).toBe(6);
  });

  it('an instrument has no input to follow', () => {
    const m = normalize({ kind: 'instrument', motion: { routes: [
      { source: 'follow', target: 'tone.cutoff', amount: 40 }, { source: 'lfo1', target: 'tone.cutoff', amount: 40 }] } }).motion!;
    expect(m.routes.map((r) => r.source)).toEqual(['lfo1']);
  });

  it('goes to Python worded its way, and comes back the same', () => {
    const s = normalize(moving);
    const wire = toWire(s) as { motion: Record<string, unknown> };
    expect(wire.motion.lfo1).toEqual(s.motion!.sources.lfo1);
    expect(wire.motion.sources).toBeUndefined();
    expect(normalize(wire)).toEqual(s);
    expect(toWire(normalize({}))).toEqual(normalize({}));
  });

  it('survives a shared link, and its dials map back to its settings', () => {
    const s = normalize(moving);
    expect(decodeSettings(encodeSettings(s))).toEqual(s);
    const index = dialIndex(s.motion);
    expect(index.get('knobs_wobble')).toEqual({ module: 'knobs', param: 'k1' });
    expect(index.get('loudness_sensitivity')).toEqual({ module: 'follow', param: 'sens' });
    expect(valuesOf(s, 'knobs')).toBe(s.motion!.knobValues);
    expect(valuesOf(s, 'lfo1')).toBe(s.motion!.sources.lfo1.values);
  });

  it('every preset under Motion moves something', () => {
    const presets = PRESETS.filter((p) => p.group === 'Motion');
    expect(presets.length).toBeGreaterThanOrEqual(4);
    for (const p of presets) expect(normalize(p.settings).motion!.routes.length).toBeGreaterThan(0);
  });
});

describe('themes: the device\'s colours', () => {
  it('the first theme is not written down, and an unknown one is dropped', () => {
    expect(normalize({}).theme).toBeUndefined();
    expect(normalize({ theme: SCHEMA.themes[0].id }).theme).toBeUndefined();
    expect(normalize({ theme: 'no such theme' }).theme).toBeUndefined();
    expect(normalize({ theme: 'midnight' }).theme).toBe('midnight');
    expect(normalize({ kind: 'instrument', theme: 'plum' }).theme).toBe('plum');
  });

  it('a theme survives a shared link', () => {
    const s = normalize({ theme: 'ocean', chain: [{ module: 'delay', on: true }] });
    expect(decodeSettings(encodeSettings(s))).toEqual(s);
  });
});
