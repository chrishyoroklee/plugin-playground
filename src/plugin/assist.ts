// Describe it: turning a sentence into a device.
//
// A device is a small settings object, and everything else is worked out from it. So an
// AI does not have to write a patch or any code here: it has to fill in an answer in a
// fixed shape (Answer, below), which is then bounded by normalize() like any other
// settings. Whatever comes back, the worst it can be is a dull device.
//
// The site has no server and pays for nothing, so there are two ways to get an answer:
//
//   copy the prompt   Paste it into any chatbot, and paste the reply back. Free, no key.
//   your own key      An Anthropic API key, kept in this browser, sent only to Anthropic.
//                     The request goes straight from the page (ask(), below).
//
// Both use the same prompt and read the same answer.

import {
  SCHEMA, motionTargets, normalize,
  type Kind, type ModuleSpec, type Settings,
} from './model';

/** What an AI is asked to fill in. The same shape describes the device it starts from. */
export interface Answer {
  name: string;
  /** One sentence for the person, on what was made. */
  explanation: string;
  /** The effects that are on, in the order the sound passes through them. */
  chain: Part[];
  /** An instrument's synth sections. Left empty for an effect. */
  synth: Part[];
  output_gain: number;
  lfo1: { shape: string; rate: number };
  lfo2: { shape: string; rate: number };
  follow_sens: number;
  /** Knobs of the device's own, by name. They are knob1, knob2 ... in `motion`, in this order. */
  knobs: { name: string }[];
  /** What moves which dial, and how far (percent of the dial's travel, either way). */
  motion: { source: string; target: string; amount: number }[];
}

interface Part {
  id: string;
  options: { id: string; choice: string }[];
  dials: { id: string; value: number }[];
}

export const MODEL = 'claude-haiku-5-5';
const KEY = 'pp-anthropic-key';

// ── the key, kept in this browser ───────────────────────────────────────────────

export function savedKey(): string {
  try { return localStorage.getItem(KEY) ?? ''; } catch { return ''; }
}

export function saveKey(key: string): void {
  try {
    if (key.trim()) localStorage.setItem(KEY, key.trim());
    else localStorage.removeItem(KEY);
  } catch { /* private browsing: the key lasts as long as the page */ }
}

// ── settings <-> answer ─────────────────────────────────────────────────────────

const part = (id: string, options: Record<string, string>, values: Record<string, number>): Part => ({
  id,
  options: Object.entries(options).map(([oid, choice]) => ({ id: oid, choice })),
  dials: Object.entries(values).map(([did, value]) => ({ id: did, value })),
});

/** A device, worded as an answer: what the AI is shown as the place it starts from. */
export function fromSettings(s: Settings): Answer {
  const m = s.motion;
  const knobName = new Map((m?.knobs ?? []).map((k, i) => [k.id, `knob${i + 1}`]));
  return {
    name: s.name,
    explanation: '',
    chain: s.chain.filter((e) => e.on).map((e) => part(e.module, e.options, e.values)),
    synth: Object.entries(s.instrument ?? {}).map(([id, sec]) => part(id, sec.options, sec.values)),
    output_gain: s.output.gain ?? 0,
    lfo1: { shape: m?.sources.lfo1.options.shape ?? 'sine', rate: m?.sources.lfo1.values.rate ?? 1 },
    lfo2: { shape: m?.sources.lfo2.options.shape ?? 'sine', rate: m?.sources.lfo2.values.rate ?? 6 },
    follow_sens: m?.sources.follow.values.sens ?? 6,
    knobs: (m?.knobs ?? []).map((k) => ({ name: k.name })),
    motion: (m?.routes ?? []).map((r) => ({ source: knobName.get(r.source) ?? r.source, target: r.target, amount: r.amount })),
  };
}

const list = <T>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);
const record = <T>(items: { id?: unknown }[], pick: (item: never) => T): Record<string, T> => {
  const out: Record<string, T> = {};
  for (const item of items) if (item && typeof item === 'object' && typeof item.id === 'string') out[item.id] = pick(item as never);
  return out;
};

/**
 * An answer, as a device of `kind`. Everything in it is bounded by normalize(), which
 * drops what it does not know and clamps every number, so nothing here is trusted. What
 * the answer does not say (the accent colour) is kept from `current`.
 */
export function toSettings(answer: unknown, kind: Kind, current: Settings): Settings {
  const a = (answer && typeof answer === 'object' ? answer : {}) as Partial<Answer>;
  const entry = (p: Part) => ({
    options: record(list<{ id?: unknown; choice?: unknown }>(p.options), (o: { choice: unknown }) => String(o.choice)),
    values: record(list<{ id?: unknown; value?: unknown }>(p.dials), (d: { value: unknown }) => Number(d.value)),
  });
  const chain = list<Part>(a.chain).filter((p) => p && typeof p.id === 'string').map((p) => ({ module: p.id, on: true, ...entry(p) }));
  const instrument: Record<string, unknown> = {};
  for (const p of list<Part>(a.synth)) if (p && typeof p.id === 'string') instrument[p.id] = entry(p);
  const knobs = list<{ name?: unknown }>(a.knobs).map((k, i) => ({ id: `knob${i + 1}`, name: String(k?.name ?? '') }));
  return normalize({
    ...(kind === 'instrument' ? { kind, instrument } : {}),
    name: typeof a.name === 'string' && a.name.trim() ? a.name : current.name,
    accent: current.accent,
    theme: current.theme,
    look: current.look,
    chain,
    output: { gain: Number(a.output_gain ?? 0) },
    motion: {
      lfo1: { options: { shape: a.lfo1?.shape }, values: { rate: a.lfo1?.rate } },
      lfo2: { options: { shape: a.lfo2?.shape }, values: { rate: a.lfo2?.rate } },
      follow: { values: { sens: a.follow_sens } },
      knobs,
      // a knob is at rest until it is turned, so adding one changes nothing by itself
      knobValues: Object.fromEntries(knobs.map((k) => [k.id, 0])),
      routes: list<{ source?: unknown; target?: unknown; amount?: unknown }>(a.motion)
        .map((r) => ({ source: String(r?.source), target: String(r?.target), amount: Number(r?.amount) })),
    },
  });
}

/** The answer in a chatbot's reply: the JSON object in it, whatever was written round it. */
export function parseAnswer(text: string): unknown {
  const from = text.indexOf('{');
  const to = text.lastIndexOf('}');
  if (from < 0 || to <= from) throw new Error('There is no answer in that: it should have a block that starts with { and ends with }.');
  try {
    return JSON.parse(text.slice(from, to + 1));
  } catch {
    throw new Error('The answer was cut off or changed on the way: copy the whole reply and paste it again.');
  }
}

// ── the prompt ──────────────────────────────────────────────────────────────────

function describe(mod: ModuleSpec): string {
  const lines = [`- ${mod.id} (${mod.label})${mod.blurb ? `: ${mod.blurb}` : ''}`];
  for (const o of mod.options) lines.push(`    option ${o.id}: one of ${o.choices.map((c) => c.id).join(', ')} (starts at ${o.default})`);
  for (const p of mod.params) {
    lines.push(`    dial ${p.id} (${p.label}): ${p.min} to ${p.max}${p.unit ? ` ${p.unit}` : ''}${p.integer ? ', whole numbers' : ''}, starts at ${p.default}`);
  }
  return lines.join('\n');
}

/** What there is to work with, and the rules: the same text for a chatbot and for the API. */
export function brief(kind: Kind): string {
  const targets = [...motionTargets(kind).keys()].join(', ');
  const m = SCHEMA.motion;
  const synth = kind === 'instrument'
    ? `\nIt is an instrument: a polyphonic synth, played with notes, followed by the effects. Its synth sections are always there, under "synth":\n${SCHEMA.instrument.modules.map(describe).join('\n')}\n`
    : '\nIt is an audio effect: sound comes in, goes through the effects in order, and comes out. Leave "synth" as an empty list.\n';
  return `You design devices for Plugin Playground, a tool that turns a small description into an audio ${kind === 'instrument' ? 'instrument' : 'effect'} plugin.
${synth}
The effects, under "chain". List only the ones that should be on, in the order the sound goes through them, each at most once:
${SCHEMA.modules.map(describe).join('\n')}

"output_gain" is the level at the end, ${SCHEMA.output.params[0].min} to ${SCHEMA.output.params[0].max} dB. Leave it near 0 unless an effect makes things much louder or quieter.

Motion makes dials move by themselves. Under "motion", each entry is one source moving one dial:
- source: lfo1 or lfo2 (each sweeps up and down; set its "shape" to one of ${m.sources[0].options[0].choices.map((c) => c.id).join(', ')} and its "rate" from ${m.sources[0].params[0].min} to ${m.sources[0].params[0].max} Hz)${kind === 'effect' ? `, follow (rises with the volume of the sound coming in; "follow_sens" from ${m.sources[2].params[0].min} to ${m.sources[2].params[0].max} is how easily)` : ''}, or knob1 to knob${m.knobs.max} (knobs of the device's own, named under "knobs" in that order; a knob is how the person playing gets one control that moves several dials)
- target: one of ${targets}
- amount: -100 to 100, the share of the dial's whole travel the source moves it, added to where the dial stands. An LFO swings both ways, so 15 to 35 is a clear sweep and more than 50 is extreme. A knob or follow pushes one way, so 30 to 70 is usual.
Use motion when the request asks for movement (wobble, sweep, pulse, wah, breathing, "follows my playing") or for a control of its own. Leave "motion" and "knobs" empty otherwise.

Rules:
- Make what was asked for, with as few effects as it takes. Three is a lot.
- Give every dial of every effect you use a value inside its range, chosen for the sound, not left at where it starts.
- "name" is two or three words for the device: letters, digits and spaces only, 32 characters at most.
- "explanation" is one plain sentence telling the person what you made and which dial to try first.`;
}

/** The shape of an answer, for an API that can hold a model to one. */
export function answerSchema(kind: Kind): Record<string, unknown> {
  const str = { type: 'string' };
  const num = { type: 'number' };
  const obj = (properties: Record<string, unknown>) => ({ type: 'object', properties, required: Object.keys(properties), additionalProperties: false });
  const partOf = (ids: string[]) => obj({
    id: { type: 'string', enum: ids },
    options: { type: 'array', items: obj({ id: str, choice: str }) },
    dials: { type: 'array', items: obj({ id: str, value: num }) },
  });
  const lfo = obj({ shape: { type: 'string', enum: SCHEMA.motion.sources[0].options[0].choices.map((c) => c.id) }, rate: num });
  const sources = ['lfo1', 'lfo2', ...(kind === 'effect' ? ['follow'] : []), ...Array.from({ length: SCHEMA.motion.knobs.max }, (_, i) => `knob${i + 1}`)];
  return obj({
    name: str,
    explanation: str,
    chain: { type: 'array', items: partOf(SCHEMA.modules.map((mod) => mod.id)) },
    synth: { type: 'array', items: partOf(SCHEMA.instrument.modules.map((mod) => mod.id)) },
    output_gain: num,
    lfo1: lfo,
    lfo2: lfo,
    follow_sens: num,
    knobs: { type: 'array', items: obj({ name: str }) },
    motion: { type: 'array', items: obj({ source: { type: 'string', enum: sources }, target: { type: 'string', enum: [...motionTargets(kind).keys()] }, amount: num }) },
  });
}

const starting = (current: Settings): string => `The device as it is now:\n${JSON.stringify(fromSettings(current))}`;

/** The whole prompt as one text, for pasting into any chatbot. */
export function chatPrompt(request: string, current: Settings, kind: Kind): string {
  const blank = JSON.stringify({ ...fromSettings(current), explanation: '...' }, null, 1);
  return `${brief(kind)}

${starting(current)}

The request: ${request.trim()}

Reply with one JSON object and nothing else, in exactly this shape (this one is the device as it is now):
${blank}`;
}

// ── asking, with the person's own key ───────────────────────────────────────────

/** A failure worded for the person who typed the request. */
export class AssistError extends Error {}

/**
 * Ask Claude for a device, straight from this page with the person's own key. Returns the
 * answer as it came; toSettings() makes a device of it.
 */
export async function ask(request: string, current: Settings, kind: Kind, key: string): Promise<Answer> {
  // The SDK is only fetched by someone who uses this.
  const { default: Anthropic } = await import('@anthropic-ai/sdk');
  // The key is the visitor's own, typed into their own browser, and this page has no
  // server it could be kept on instead: that is the case this switch is for.
  const client = new Anthropic({ apiKey: key, dangerouslyAllowBrowser: true, maxRetries: 1 });
  try {
    const response = await client.messages.create({
      model: MODEL,
      max_tokens: 8000,
      system: brief(kind),
      messages: [{ role: 'user', content: `${starting(current)}\n\nThe request: ${request.trim()}` }],
      output_config: { effort: 'low', format: { type: 'json_schema', schema: answerSchema(kind) } },
    });
    if (response.stop_reason === 'refusal') throw new AssistError('Claude declined that request. Try wording it as a sound.');
    if (response.stop_reason === 'max_tokens') throw new AssistError('The answer was cut short. Try a simpler request.');
    const text = response.content.find((block) => block.type === 'text');
    if (!text || text.type !== 'text') throw new AssistError('Claude sent back no answer. Try again.');
    return JSON.parse(text.text) as Answer;
  } catch (err) {
    if (err instanceof AssistError) throw err;
    if (err instanceof Anthropic.AuthenticationError) throw new AssistError('That key was not accepted. Check it was copied whole, and that it has not been deleted.');
    if (err instanceof Anthropic.PermissionDeniedError) throw new AssistError(`That key is not allowed to use ${MODEL}.`);
    if (err instanceof Anthropic.RateLimitError) throw new AssistError('Your key has reached its limit for now. Wait a minute, or check its spending cap.');
    if (err instanceof Anthropic.APIConnectionError) throw new AssistError('The request did not get through. Check the connection, and that nothing is blocking api.anthropic.com.');
    if (err instanceof Anthropic.APIError) {
      if (err.status === 529 || (err.status ?? 0) >= 500) throw new AssistError('Anthropic is busy at the moment. Try again shortly.');
      if (err.status === 402) throw new AssistError('The account behind that key has a billing problem.');
      throw new AssistError(`Anthropic turned the request down: ${err.message}`);
    }
    throw new AssistError('The answer could not be read. Try again.');
  }
}
