import { describe, expect, it } from 'vitest';
import { INSTRUMENTS, PRESETS, SCHEMA, normalize } from '../src/plugin/model';
import { answerSchema, brief, chatPrompt, fromSettings, parseAnswer, toSettings } from '../src/plugin/assist';

const base = normalize(PRESETS[0].settings);

describe('describe it: a device from an answer', () => {
  it('every preset survives being worded as an answer and read back', () => {
    for (const p of [...PRESETS, ...INSTRUMENTS]) {
      const s = normalize(p.settings);
      const kind = s.kind === 'instrument' ? 'instrument' : 'effect';
      const back = toSettings(fromSettings(s), kind, s);
      // modules that are off come back in their starting order, which is all that may differ
      const on = (x: typeof s) => x.chain.filter((e) => e.on);
      expect(on(back)).toEqual(on(s));
      expect(back.instrument).toEqual(s.instrument);
      expect(back.output).toEqual(s.output);
      expect(back.name).toBe(s.name);
      expect(back.motion?.routes).toEqual(s.motion?.routes);
      expect(back.motion?.knobs).toEqual(s.motion?.knobs);
    }
  });

  it('an answer becomes a device, with motion and a knob', () => {
    const made = toSettings({
      name: 'Tape Wobble', explanation: 'x',
      chain: [
        { id: 'delay', options: [{ id: 'style', choice: 'stereo' }], dials: [{ id: 'time', value: 340 }, { id: 'feedback', value: 45 }, { id: 'mix', value: 35 }] },
        { id: 'drive', options: [{ id: 'character', choice: 'tube' }], dials: [{ id: 'amount', value: 2 }, { id: 'tone', value: 4200 }] },
      ],
      synth: [], output_gain: -1,
      lfo1: { shape: 'sine', rate: 0.6 }, lfo2: { shape: 'triangle', rate: 5 }, follow_sens: 10,
      knobs: [{ name: 'Wow' }],
      motion: [{ source: 'lfo1', target: 'delay.time', amount: 4 }, { source: 'knob1', target: 'delay.time', amount: 20 },
               { source: 'follow', target: 'drive.amount', amount: 40 }],
    }, 'effect', base);
    expect(made.name).toBe('Tape Wobble');
    expect(made.accent).toBe(base.accent);
    expect(made.chain.filter((e) => e.on).map((e) => e.module)).toEqual(['delay', 'drive']);
    expect(made.chain[0].values.time).toBe(340);
    expect(made.motion!.knobs).toEqual([{ id: 'k1', name: 'Wow' }]);
    expect(made.motion!.routes).toEqual([
      { source: 'lfo1', target: 'delay.time', amount: 4 }, { source: 'k1', target: 'delay.time', amount: 20 },
      { source: 'follow', target: 'drive.amount', amount: 40 }]);
    expect(made.motion!.sources.lfo1.values.rate).toBe(0.6);
    expect(made.motion!.knobValues.k1).toBe(0);
  });

  it('nothing an answer says is trusted', () => {
    const made = toSettings({
      name: '<script>alert(1)</script> & "quotes"',
      chain: [
        { id: 'nonsense', options: [], dials: [] },
        { id: 'filter', options: [{ id: 'mode', choice: 'sideways' }], dials: [{ id: 'cutoff', value: 9e9 }, { id: 'res', value: 'loud' }] },
        { id: 'filter', options: [], dials: [] },
      ],
      output_gain: 500,
      lfo1: { shape: 'wibble', rate: -4 },
      knobs: [{ name: 'a' }, { name: 'b' }, { name: 'c' }, { name: 'd' }, { name: 'e' }, { name: 'f' }],
      motion: [{ source: 'knob9', target: 'filter.cutoff', amount: 50 }, { source: 'lfo1', target: 'reverb.nothing', amount: 50 },
               { source: 'lfo1', target: 'filter.cutoff', amount: 1e6 }],
    }, 'effect', base);
    expect(made.name).toMatch(/^[A-Za-z0-9 _-]{1,32}$/);
    const on = made.chain.filter((e) => e.on);
    expect(on.map((e) => e.module)).toEqual(['filter']);
    expect(on[0].options.mode).toBe('lowpass');
    const cutoff = SCHEMA.modules.find((m) => m.id === 'filter')!.params.find((p) => p.id === 'cutoff')!;
    expect(on[0].values.cutoff).toBe(cutoff.max);
    expect(on[0].values.res).toBe(SCHEMA.modules.find((m) => m.id === 'filter')!.params.find((p) => p.id === 'res')!.default);
    expect(made.output.gain).toBe(SCHEMA.output.params[0].max);
    expect(made.motion!.knobs.length).toBe(SCHEMA.motion.knobs.max);
    expect(made.motion!.routes).toEqual([{ source: 'lfo1', target: 'filter.cutoff', amount: 100 }]);
    expect(made.motion!.sources.lfo1.options.shape).toBe('sine');
    expect(toSettings('not an object', 'effect', base).name).toBe(base.name);
    expect(toSettings(null, 'instrument', normalize(INSTRUMENTS[0].settings)).kind).toBe('instrument');
  });

  it('reads the answer out of a chatbot\'s reply, whatever is written round it', () => {
    const answer = fromSettings(base);
    const reply = `Sure! Here is your device:\n\n\`\`\`json\n${JSON.stringify(answer, null, 2)}\n\`\`\`\n\nTry the Drive dial first.`;
    expect(parseAnswer(reply)).toEqual(answer);
    expect(() => parseAnswer('I cannot help with that.')).toThrow(/no answer/);
    expect(() => parseAnswer('{"name": "Cut off", "chain": [')).toThrow();
  });
});

describe('describe it: what the AI is told', () => {
  it('names every effect, option and dial with its range', () => {
    const text = brief('effect');
    for (const m of SCHEMA.modules) {
      expect(text).toContain(`- ${m.id} (${m.label})`);
      for (const p of m.params) expect(text).toContain(`dial ${p.id} (${p.label}): ${p.min} to ${p.max}`);
      for (const o of m.options) for (const c of o.choices) expect(text).toContain(c.id);
    }
    expect(text).toContain('follow');
    expect(brief('instrument')).toContain('- osc (');
    expect(brief('instrument')).not.toContain('follow_sens');
  });

  it('the prompt for a chatbot carries the request, the device and the shape to answer in', () => {
    const text = chatPrompt('  make it darker  ', base, 'effect');
    expect(text).toContain('The request: make it darker');
    expect(text).toContain(JSON.stringify(fromSettings(base)));
    expect(text).toContain('Reply with one JSON object and nothing else');
  });

  it('the schema closes every object, as a structured output has to', () => {
    const walk = (node: unknown): void => {
      if (!node || typeof node !== 'object') return;
      const n = node as { type?: string; properties?: Record<string, unknown>; required?: string[]; additionalProperties?: boolean; items?: unknown };
      if (n.type === 'object') {
        expect(n.additionalProperties).toBe(false);
        expect(n.required).toEqual(Object.keys(n.properties ?? {}));
        Object.values(n.properties ?? {}).forEach(walk);
      }
      walk(n.items);
    };
    walk(answerSchema('effect'));
    walk(answerSchema('instrument'));
    const effect = answerSchema('effect') as { properties: { motion: { items: { properties: { source: { enum: string[] } } } } } };
    expect(effect.properties.motion.items.properties.source.enum).toContain('follow');
    const inst = answerSchema('instrument') as typeof effect;
    expect(inst.properties.motion.items.properties.source.enum).not.toContain('follow');
  });
});
