// The pictures in the device view: an EQ's curve, a delay's echoes, an envelope's shape.
//
// What is drawn, and what a drag means, is graphs.js: one file that the page, the
// plugin's window and the Max for Live device all use as it stands. This is the page's
// way of drawing it, on a canvas, and of joining it to the running dials. A picture is a
// view of its module's dials: dragging it turns them through the same call a knob drag
// makes, and a dial turned by hand redraws it.

import source from './graphs.js?raw';

type Colour = number[];
type Values = Record<string, number | string>;
type Ranges = Record<string, { min: number; max: number; exponent: number }>;

interface Drawing {
  path(points: number[], stroke: Colour | null, width: number, fill: Colour | null): void;
  dot(x: number, y: number, radius: number, fill: Colour, stroke: Colour): void;
  line(x0: number, y0: number, x1: number, y1: number, colour: Colour, width: number): void;
}

interface Core {
  has(kind: string): boolean;
  render(kind: string, g: Drawing, v: Values, w: number, h: number, c: Record<string, Colour>, m: Ranges, hot: string | null): void;
  pick(kind: string, x: number, y: number, v: Values, w: number, h: number, m: Ranges): string | null;
  drag(kind: string, id: string, x: number, y: number, v: Values, w: number, h: number, m: Ranges): Record<string, number>;
}

// graphs.js is plain script (Max's v8ui has no modules), so it is run, not imported.
export const graphs = new Function(`${source}\nreturn PPGraphs;`)() as Core;

/** One dial a picture reads and turns. */
export interface GraphDial {
  id: string;
  min: number;
  max: number;
  exponent: number;
  value: number;
  /** Turn the dial, as a drag on it would. */
  set(value: number): void;
  /** Hear of every change to it, whoever made it. Returns how to stop. */
  watch(heard: (value: number) => void): (() => void) | void;
}

export interface GraphColours { ink: string; track: string; accent: string; card: string; well: string }

function rgb(hex: string): Colour {
  const h = hex.replace('#', '');
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
}
const css = (c: Colour): string => `rgba(${Math.round(c[0] * 255)}, ${Math.round(c[1] * 255)}, ${Math.round(c[2] * 255)}, ${c.length > 3 ? c[3] : 1})`;

/**
 * Draw `kind` into `host` and keep it in step with its dials. Returns how to take it down.
 * `locked` says when a drag should do nothing (while the device is being edited).
 */
export function mountGraph(
  host: HTMLElement, kind: string, options: Record<string, string>, dials: GraphDial[], colours: GraphColours, locked: () => boolean,
): () => void {
  if (!graphs.has(kind)) return () => {};
  const w = host.clientWidth || parseFloat(host.style.width) || 140;
  const h = host.clientHeight || parseFloat(host.style.height) || 70;
  const ratio = Math.min(3, window.devicePixelRatio || 1) * 2;   // the device view is often zoomed in
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(w * ratio);
  canvas.height = Math.round(h * ratio);
  canvas.style.cssText = 'display:block;width:100%;height:100%;touch-action:none;';
  host.replaceChildren(canvas);
  host.style.background = colours.well;
  const ctx = canvas.getContext('2d');
  if (!ctx) return () => {};

  const values: Values = { ...options };
  const ranges: Ranges = {};
  const byId = new Map<string, GraphDial>();
  for (const d of dials) {
    values[d.id] = d.value;
    ranges[d.id] = { min: d.min, max: d.max, exponent: d.exponent };
    byId.set(d.id, d);
  }
  const palette = { ink: rgb(colours.ink), track: rgb(colours.track), accent: rgb(colours.accent), card: rgb(colours.card) };
  let hot: string | null = null;

  const g: Drawing = {
    path(points, stroke, width, fill) {
      if (points.length < 4) return;
      ctx.beginPath();
      ctx.moveTo(points[0], points[1]);
      for (let i = 2; i < points.length; i += 2) ctx.lineTo(points[i], points[i + 1]);
      if (fill) { ctx.closePath(); ctx.fillStyle = css(fill); ctx.fill(); }
      else if (stroke) { ctx.strokeStyle = css(stroke); ctx.lineWidth = width || 1; ctx.lineJoin = 'round'; ctx.stroke(); }
    },
    dot(x, y, radius, fill, stroke) {
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.fillStyle = css(fill);
      ctx.fill();
      ctx.strokeStyle = css(stroke);
      ctx.lineWidth = 1.6;
      ctx.stroke();
    },
    line(x0, y0, x1, y1, colour, width) {
      ctx.beginPath();
      ctx.moveTo(x0, y0);
      ctx.lineTo(x1, y1);
      ctx.strokeStyle = css(colour);
      ctx.lineWidth = width || 1;
      ctx.lineCap = 'round';
      ctx.stroke();
    },
  };

  let queued = 0;
  const draw = (): void => {
    queued = 0;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, w, h);
    graphs.render(kind, g, values, w, h, palette, ranges, hot);
  };
  const redraw = (): void => { if (!queued) queued = requestAnimationFrame(draw); };
  draw();

  const offs: (() => void)[] = [];
  for (const d of dials) {
    const off = d.watch((value) => { values[d.id] = value; redraw(); });
    if (typeof off === 'function') offs.push(off);
  }

  // Where the pointer is, in the picture's own units, whatever the zoom.
  const at = (e: PointerEvent): [number, number] => {
    const box = canvas.getBoundingClientRect();
    return [(e.clientX - box.left) * (w / box.width), (e.clientY - box.top) * (h / box.height)];
  };
  const move = (e: PointerEvent): void => {
    if (!hot) return;
    const [x, y] = at(e);
    const next = graphs.drag(kind, hot, x, y, values, w, h, ranges);
    for (const [id, value] of Object.entries(next)) {
      values[id] = value;
      byId.get(id)?.set(value);
    }
    redraw();
  };
  canvas.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || locked()) return;
    const [x, y] = at(e);
    hot = graphs.pick(kind, x, y, values, w, h, ranges);
    if (!hot) return;
    canvas.setPointerCapture(e.pointerId);
    e.preventDefault();
    move(e);
  });
  canvas.addEventListener('pointermove', move);
  const release = (e: PointerEvent): void => {
    hot = null;
    if (canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
    redraw();
  };
  canvas.addEventListener('pointerup', release);
  canvas.addEventListener('pointercancel', release);

  return () => {
    for (const off of offs) off();
    if (queued) cancelAnimationFrame(queued);
  };
}
