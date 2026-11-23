// The pictures on a device (src/plugin/graphs.js), without a browser or Max: that every
// kind draws finite shapes, that a drag gives values its dials accept, that the EQ's
// curve says what the EQ does, and that the Max for Live wrapper (graph-max.js) runs
// against a stand-in for mgraphics.

import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import schema from '../src/plugin/schema.json';

const source = readFileSync(new URL('../src/plugin/graphs.js', import.meta.url), 'utf8');
const maxSource = readFileSync(new URL('../src/plugin/graph-max.js', import.meta.url), 'utf8');
const graphs = new Function(`${source}\nreturn PPGraphs;`)();

type Param = { id: string; min: number; max: number; default: number; exponent?: number };
type Module = { id: string; params: Param[]; options?: { id: string; choices: { id: string }[] }[] };
const modules = [...(schema.modules as Module[]), ...(schema.instrument.modules as Module[])];
const W = 140, H = 70;
const colours = { ink: [0.2, 0.1, 0.1], track: [0.9, 0.7, 0.7], accent: [0.2, 0.5, 0.7], card: [1, 1, 1] };

function setup(mod: Module, pick: (p: Param) => number, choice = 0) {
  const v: Record<string, number | string> = {};
  const m: Record<string, { min: number; max: number; exponent: number }> = {};
  for (const o of mod.options ?? []) v[o.id] = o.choices[Math.min(choice, o.choices.length - 1)].id;
  for (const p of mod.params) { v[p.id] = pick(p); m[p.id] = { min: p.min, max: p.max, exponent: p.exponent ?? 1 }; }
  return { v, m };
}

function recorder() {
  const numbers: number[] = [];
  let calls = 0;
  return {
    numbers,
    get calls() { return calls; },
    g: {
      path(points: number[]) { calls++; numbers.push(...points); },
      dot(x: number, y: number, r: number) { calls++; numbers.push(x, y, r); },
      line(x0: number, y0: number, x1: number, y1: number) { calls++; numbers.push(x0, y0, x1, y1); },
    },
  };
}

describe('the pictures', () => {
  it('there is one for every module, effect and instrument', () => {
    for (const mod of modules) expect(graphs.has(mod.id), mod.id).toBe(true);
    expect(graphs.has('output')).toBe(false);
  });

  it('each draws something finite at every end of its dials, for every choice', () => {
    for (const mod of modules) {
      const choices = Math.max(1, ...(mod.options ?? []).map((o) => o.choices.length));
      for (let choice = 0; choice < choices; choice++) {
        for (const pick of [(p: Param) => p.min, (p: Param) => p.max, (p: Param) => p.default]) {
          const { v, m } = setup(mod, pick, choice);
          const r = recorder();
          graphs.render(mod.id, r.g, v, W, H, colours, m, null);
          expect(r.calls, mod.id).toBeGreaterThan(1);
          expect(r.numbers.every(Number.isFinite), `${mod.id} choice ${choice}`).toBe(true);
        }
      }
    }
  });

  it('a drag anywhere, even off the picture, gives values the dials accept', () => {
    for (const mod of modules) {
      const { v, m } = setup(mod, (p) => p.default);
      for (const [x, y] of [[-50, -50], [0, 0], [W / 2, H / 2], [W, H], [W + 80, H + 80]]) {
        const id = graphs.pick(mod.id, x, y, v, W, H, m);
        expect(id, mod.id).toBeTruthy();
        const next = graphs.drag(mod.id, id, x, y, v, W, H, m);
        expect(Object.keys(next).length, mod.id).toBeGreaterThan(0);
        for (const [key, value] of Object.entries(next) as [string, number][]) {
          expect(m[key], `${mod.id}.${key} is a dial`).toBeDefined();
          expect(Number.isFinite(value) && value >= m[key].min && value <= m[key].max, `${mod.id}.${key} = ${value}`).toBe(true);
        }
      }
    }
  });

  it('a dot dragged to where it already is leaves its dials where they are', () => {
    for (const mod of modules) {
      const { v, m } = setup(mod, (p) => p.default);
      for (const dot of graphs.kinds[mod.id].handles(v, W, H, m) as { id: string; x: number; y: number }[]) {
        const next = graphs.drag(mod.id, dot.id, dot.x, dot.y, v, W, H, m) as Record<string, number>;
        for (const [key, value] of Object.entries(next)) {
          const span = m[key].max - m[key].min;
          expect(Math.abs(value - (v[key] as number)) / span, `${mod.id}.${key}: ${v[key]} became ${value}`).toBeLessThan(0.02);
        }
      }
    }
  });

  it('the EQ curve is flat at 0 dB and reads its dials where they point', () => {
    const eq = modules.find((x) => x.id === 'eq')!;
    const yAt = (v: Record<string, number | string>, m: unknown, freq: number): number => {
      let points: number[] = [];
      graphs.kinds.eq.draw({ path(p: number[], stroke: unknown) { if (stroke) points = p; }, dot() {}, line() {} }, v, 400, 200, colours, m);
      const x = 400 * Math.log(freq / 20) / Math.log(1000);
      let best = 0;
      for (let i = 0; i < points.length; i += 2) if (Math.abs(points[i] - x) < Math.abs(points[best] - x)) best = i;
      return (100 - points[best + 1]) / 97 * 18;   // back from pixels to dB
    };
    const flat = setup(eq, () => 0);
    flat.v.freq = 1000;
    for (const f of [40, 250, 1000, 4000, 15000]) expect(Math.abs(yAt(flat.v, flat.m, f))).toBeLessThan(0.05);
    const mid = setup(eq, () => 0);
    Object.assign(mid.v, { mid: 12, freq: 1000 });
    expect(yAt(mid.v, mid.m, 1000)).toBeGreaterThan(11);
    expect(Math.abs(yAt(mid.v, mid.m, 40))).toBeLessThan(1);
    const low = setup(eq, () => 0);
    Object.assign(low.v, { low: -12, freq: 1000 });
    expect(yAt(low.v, low.m, 30)).toBeLessThan(-10.5);
    expect(Math.abs(yAt(low.v, low.m, 15000))).toBeLessThan(0.6);
  });
});

describe('the Max for Live wrapper', () => {
  /** graph-max.js run against a stand-in for v8ui's globals. Returns what it sent out and whether it drew. */
  function inMax(mod: Module) {
    const { v } = setup(mod, (p) => p.default);
    const PP = {
      kind: mod.id,
      options: Object.fromEntries(Object.entries(v).filter(([, x]) => typeof x === 'string')),
      dials: mod.params.map((p) => ({ id: p.id, min: p.min, max: p.max, exponent: p.exponent ?? 1, value: p.default })),
      colours: { ...colours, well: [1, 0.9, 0.9, 1] },
    };
    const sent: [string, number][] = [];
    let drawn = 0, redraws = 0;
    const count = (): void => { drawn++; };
    const mgraphics = {
      size: [W, H], relative_coords: 1, autofill: 1, init() {}, redraw() { redraws++; },
      move_to: count, line_to: count, close_path: count, fill: count, stroke: count, ellipse: count, rectangle_rounded: count,
      set_source_rgba(...rgba: number[]) { if (rgba.length !== 4 || !rgba.every(Number.isFinite)) throw new Error(`a colour of ${rgba}`); },
      set_line_width(w: number) { if (!(w > 0)) throw new Error(`a line ${w} wide`); },
    };
    const api = new Function('mgraphics', 'outlet', 'arrayfromargs', 'setName',
      `var inlets, outlets, messagename; var PP = ${JSON.stringify(PP)};\n${source}\n${maxSource}\n` +
      'return { paint: paint, onclick: onclick, ondrag: ondrag, anything: function (name, value) { messagename = name; anything(value); }, V: V };',
    )(mgraphics, (_outlet: number, name: string, value: number) => sent.push([name, value]), (a: IArguments) => Array.from(a));
    return { api, sent, drawn: () => drawn, redraws: () => redraws };
  }

  it('paints every picture, takes a dial\'s value without sending it back, and sends a drag to the dials', () => {
    for (const mod of modules) {
      const max = inMax(mod);
      max.api.paint();
      expect(max.drawn(), mod.id).toBeGreaterThan(5);

      const first = mod.params[0];
      max.api.anything(first.id, first.max);
      expect(max.api.V[first.id]).toBe(first.max);
      expect(max.sent, `${mod.id}: a value coming in went straight back out`).toEqual([]);
      expect(max.redraws()).toBe(1);

      max.api.onclick(W * 0.3, H * 0.3);
      max.api.ondrag(W * 0.6, H * 0.7, 1);
      max.api.ondrag(W * 0.6, H * 0.7, 0);
      expect(max.sent.length, mod.id).toBeGreaterThan(1);
      for (const [name, value] of max.sent) {
        const p = mod.params.find((x) => x.id === name);
        expect(p, `${mod.id} sent ${name}`).toBeDefined();
        expect(value >= p!.min && value <= p!.max).toBe(true);
      }
      const before = max.sent.length;
      max.api.ondrag(W * 0.2, H * 0.2, 1);   // after letting go: nothing is held, nothing moves
      expect(max.sent.length).toBe(before);
    }
  });
});
