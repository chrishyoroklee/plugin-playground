// The sound library: what the page can play through a device.
//
// Two kinds of sound. The recordings are the CC0 clips in public/samples/fx: three of
// them are an electric guitar recorded clean and direct (DI), with no amp, which is what
// a distortion or a fuzz is meant to be fed. The
// rest are computed here, in the browser, the first time each is chosen: a few synth
// loops, a drum machine, and the test signals an effect is usually judged with. Computing
// them means the library costs nothing to download and has no licences to track.
//
// Every computed sound is a loop: it is written so that its end runs into its start
// (notes that ring past the last bar are folded back into the first), is the same every
// time (the noise is seeded), and peaks at half scale like the recordings.
//
// Adding a recording: put the file in public/samples/fx, credit it in CREDITS.md, and
// add a line to RECORDINGS. Adding a computed sound: a function that returns its
// channels, and a line in SOUNDS.

export interface Sound {
  id: string;
  label: string;
  /** One line under the name: what it is, or what it is good for hearing. */
  blurb: string;
  group: string;
  /** A file to fetch and decode... */
  url?: string;
  /** ...or a function that computes the channels (one for mono, two for stereo). */
  render?: () => Float32Array[];
}

/** The rate the computed sounds are written at. The browser resamples if it has to. */
export const SOUND_RATE = 44100;

const SR = SOUND_RATE;
const TAU = 2 * Math.PI;
const PEAK = 0.5;

// ── building blocks ─────────────────────────────────────────────────────────────

const seconds = (s: number): number => Math.round(s * SR);
const hz = (midiNote: number): number => 440 * 2 ** ((midiNote - 69) / 12);

/** Repeatable noise, -1..1 (mulberry32), so a sound is identical on every visit. */
function noise(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return (((t ^ (t >>> 14)) >>> 0) / 2147483648) - 1;
  };
}

/** A sawtooth without the harsh aliasing of a plain ramp (PolyBLEP). `phase` is 0..1. */
function saw(phase: number, step: number): number {
  let y = 2 * phase - 1;
  if (phase < step) {
    const t = phase / step;
    y -= t + t - t * t - 1;
  } else if (phase > 1 - step) {
    const t = (phase - 1) / step;
    y -= t * t + t + t + 1;
  }
  return y;
}

/** One-pole low-pass, in place. */
function lowpass(x: Float32Array, cutoff: number): void {
  const g = 1 - Math.exp((-TAU * cutoff) / SR);
  let y = 0;
  for (let i = 0; i < x.length; i++) {
    y += g * (x[i] - y);
    x[i] = y;
  }
}

/** One-pole high-pass, in place. */
function highpass(x: Float32Array, cutoff: number): void {
  const g = 1 - Math.exp((-TAU * cutoff) / SR);
  let low = 0;
  for (let i = 0; i < x.length; i++) {
    low += g * (x[i] - low);
    x[i] -= low;
  }
}

/** Add a note into a loop at `start`. What runs past the end comes back in at the top. */
function mix(loop: Float32Array, start: number, note: Float32Array, gain = 1): void {
  for (let i = 0; i < note.length; i++) loop[(start + i) % loop.length] += note[i] * gain;
}

/** Scale every channel by the same amount so the loudest sample is at half scale. */
function level(channels: Float32Array[]): Float32Array[] {
  let peak = 0;
  for (const ch of channels) for (const v of ch) peak = Math.max(peak, Math.abs(v));
  if (peak > 0) for (const ch of channels) for (let i = 0; i < ch.length; i++) ch[i] *= PEAK / peak;
  return channels;
}

/** A few milliseconds of fade at both ends, for a signal that does not loop by itself. */
function fadeEnds(x: Float32Array, ms = 8): Float32Array {
  const n = seconds(ms / 1000);
  for (let i = 0; i < n; i++) {
    x[i] *= i / n;
    x[x.length - 1 - i] *= i / n;
  }
  return x;
}

// ── synths ──────────────────────────────────────────────────────────────────────

/** Slow chords from detuned saws, a little different in each ear. */
function pad(): Float32Array[] {
  const chords = [[57, 60, 64, 67], [53, 57, 60, 64], [48, 55, 60, 64], [55, 59, 62, 66]]; // Am7 Fmaj7 C Gmaj7
  const chordTime = 2;
  const attack = 0.35;
  const release = 0.6;
  const length = seconds(chords.length * chordTime);
  return level([-1, 1].map((side) => {
    const loop = new Float32Array(length);
    chords.forEach((chord, c) => {
      const note = new Float32Array(seconds(chordTime + release));
      for (const midiNote of chord) {
        for (const cents of [-7, 6 + 2 * side]) {
          const step = (hz(midiNote) * 2 ** (cents / 1200)) / SR;
          let phase = (midiNote * 0.37 + cents * 0.11 + 1) % 1;
          for (let i = 0; i < note.length; i++) {
            const t = i / SR;
            const env = Math.min(1, t / attack) * Math.min(1, (note.length / SR - t) / release);
            note[i] += saw(phase, step) * env;
            phase = (phase + step) % 1;
          }
        }
      }
      mix(loop, seconds(c * chordTime), note);
    });
    lowpass(loop, 1500);
    lowpass(loop, 2600);
    return loop;
  }));
}

/** A plucked string (Karplus-Strong): a burst of noise going round a short delay. */
function pluckNote(freq: number, time: number, seed: number): Float32Array {
  const out = new Float32Array(seconds(time));
  const period = Math.max(2, Math.round(SR / freq));
  const line = new Float32Array(period);
  const rand = noise(seed);
  for (let i = 0; i < period; i++) line[i] = rand();
  lowpass(line, 5000);
  let at = 0;
  for (let i = 0; i < out.length; i++) {
    const next = (at + 1) % period;
    out[i] = line[at];
    line[at] = 0.996 * 0.5 * (line[at] + line[next]);
    at = next;
  }
  return out;
}

/** Sixteenth-note arpeggio on plucked strings, two bars. */
function arpeggio(): Float32Array[] {
  const stepTime = 60 / 108 / 4;
  const bars = [[57, 64, 69, 72, 76, 72, 69, 64], [53, 60, 65, 69, 72, 69, 65, 60]]; // Am, F
  const loop = new Float32Array(seconds(stepTime * 32));
  bars.forEach((notes, bar) => {
    for (let s = 0; s < 16; s++) {
      const midiNote = notes[s % notes.length];
      const note = pluckNote(hz(midiNote), 1.2, 7 + bar * 16 + s);
      mix(loop, seconds((bar * 16 + s) * stepTime), note, s % 4 === 0 ? 1 : 0.7); // the beat is accented
    }
  });
  return level([loop]);
}

/** A bass line: one saw through a filter that closes over each note. */
function bass(): Float32Array[] {
  const stepTime = 60 / 100 / 2;
  const line = [33, 33, 45, 33, 36, 36, 43, 40, 29, 29, 41, 29, 31, 31, 43, 35];
  const loop = new Float32Array(seconds(stepTime * line.length));
  line.forEach((midiNote, s) => {
    const note = new Float32Array(seconds(stepTime * 0.92));
    const step = hz(midiNote) / SR;
    let phase = 0;
    let y = 0;
    for (let i = 0; i < note.length; i++) {
      const t = i / SR;
      // the filter opens with the note and closes behind it
      const g = 1 - Math.exp((-TAU * (180 + 2400 * Math.exp(-t * 14))) / SR);
      y += g * (saw(phase, step) - y);
      const env = Math.min(1, t / 0.004) * Math.min(1, (note.length / SR - t) / 0.03);
      note[i] = y * env;
      phase = (phase + step) % 1;
    }
    mix(loop, seconds(s * stepTime), note);
  });
  return level([loop]);
}

/** Electric-piano chords: each note is one sine bending another (FM), fading as it rings. */
function keys(): Float32Array[] {
  const beat = 60 / 92;
  const hits: [number, number[]][] = [
    [0, [52, 59, 62, 67]], [1.5, [52, 59, 62, 67]], [2.5, [50, 57, 60, 66]],
    [4, [48, 55, 59, 64]], [5.5, [48, 55, 59, 64]], [6.5, [47, 54, 57, 62]],
  ];
  const loop = new Float32Array(seconds(beat * 8));
  for (const [at, chord] of hits) {
    const note = new Float32Array(seconds(2.4));
    for (const midiNote of chord) {
      const w = (TAU * hz(midiNote)) / SR;
      for (let i = 0; i < note.length; i++) {
        const t = i / SR;
        const bend = 1.6 * Math.exp(-t * 6) + 0.25;
        note[i] += Math.sin(w * i + bend * Math.sin(w * i)) * Math.exp(-t * 2.2) * Math.min(1, t / 0.003);
      }
    }
    mix(loop, seconds(at * beat), note);
  }
  return level([loop]);
}

/** A lead line: two saws an octave apart, with vibrato once a note has settled. */
function lead(): Float32Array[] {
  const beat = 60 / 96;
  const tune: [number, number][] = [
    [69, 1], [72, 0.5], [76, 1.5], [74, 0.5], [72, 0.5], [69, 1], [67, 1], [64, 2],
  ]; // note, beats
  const total = tune.reduce((sum, [, beats]) => sum + beats, 0);
  const loop = new Float32Array(seconds(total * beat));
  let at = 0;
  for (const [midiNote, beats] of tune) {
    const note = new Float32Array(seconds(beats * beat * 0.95));
    let phase = 0;
    let below = 0; // the same note an octave down
    for (let i = 0; i < note.length; i++) {
      const t = i / SR;
      const vibrato = 1 + 0.006 * Math.sin(TAU * 5.5 * t) * Math.min(1, Math.max(0, (t - 0.18) / 0.2));
      const step = (hz(midiNote) * vibrato) / SR;
      const env = Math.min(1, t / 0.012) * Math.min(1, (note.length / SR - t) / 0.06);
      note[i] = (saw(phase, step) + 0.5 * saw(below, step / 2)) * env;
      phase = (phase + step) % 1;
      below = (below + step / 2) % 1;
    }
    mix(loop, seconds(at * beat), note);
    at += beats;
  }
  lowpass(loop, 3200);
  return level([loop]);
}

// ── rhythm ──────────────────────────────────────────────────────────────────────

function kick(): Float32Array {
  const out = new Float32Array(seconds(0.4));
  let phase = 0;
  for (let i = 0; i < out.length; i++) {
    const t = i / SR;
    phase += (45 + 130 * Math.exp(-t * 28)) / SR; // the pitch drops as the drum is hit
    out[i] = Math.sin(TAU * phase) * Math.exp(-t * 9);
  }
  return out;
}

function snare(seed: number): Float32Array {
  const out = new Float32Array(seconds(0.25));
  const rand = noise(seed);
  for (let i = 0; i < out.length; i++) out[i] = rand() * Math.exp((-i / SR) * 22);
  highpass(out, 900);
  for (let i = 0; i < out.length; i++) out[i] += 0.5 * Math.sin((TAU * 190 * i) / SR) * Math.exp((-i / SR) * 30);
  return out;
}

function hat(seed: number, open: boolean): Float32Array {
  const out = new Float32Array(seconds(open ? 0.3 : 0.06));
  const rand = noise(seed);
  for (let i = 0; i < out.length; i++) out[i] = rand() * Math.exp((-i / SR) * (open ? 14 : 90));
  highpass(out, 7000);
  highpass(out, 7000);
  return out;
}

/** Two bars of kick, snare and hats on a sixteenth-note grid. */
function drumMachine(): Float32Array[] {
  const stepTime = 60 / 104 / 4;
  //             1 . . . 2 . . . 3 . . . 4 . . .
  const kicks = 'x.....x...x.....x..x..x...x.....';
  const snares = '....x.......x.......x.......x..x';
  const hats = 'x.x.x.x.x.x.x.o.x.x.x.x.x.x.x.o.';
  const loop = new Float32Array(seconds(stepTime * 32));
  for (let s = 0; s < 32; s++) {
    const at = seconds(s * stepTime);
    if (kicks[s] === 'x') mix(loop, at, kick(), 1);
    if (snares[s] === 'x') mix(loop, at, snare(100 + s), 0.6);
    if (hats[s] !== '.') mix(loop, at, hat(200 + s, hats[s] === 'o'), s % 4 === 0 ? 0.3 : 0.2);
  }
  return level([loop]);
}

// ── test signals ────────────────────────────────────────────────────────────────

/** One click every half second: an echo's timing and a reverb's tail, laid bare. */
function clicks(): Float32Array[] {
  const loop = new Float32Array(seconds(2));
  for (let k = 0; k < 4; k++) {
    const at = seconds(k * 0.5);
    const gain = k === 0 ? 1 : 0.6; // the first of each bar is louder
    for (let i = 0; i < 40; i++) loop[at + i] = gain * Math.sin((Math.PI * i) / 40) ** 2 * (i % 2 ? -1 : 1);
  }
  return level([loop]);
}

/** A steady 220 Hz sine, a whole number of cycles long: what distortion adds is all you hear. */
function sine(): Float32Array[] {
  const loop = new Float32Array(seconds(2));
  for (let i = 0; i < loop.length; i++) loop[i] = Math.sin((TAU * 220 * i) / SR);
  return level([loop]);
}

/** 40 Hz to 16 kHz over four seconds, each octave taking the same time. */
function sweep(): Float32Array[] {
  const time = 4;
  const loop = new Float32Array(seconds(time));
  const k = Math.log(16000 / 40) / time;
  for (let i = 0; i < loop.length; i++) loop[i] = Math.sin((TAU * 40 * (Math.exp((k * i) / SR) - 1)) / k);
  return level([fadeEnds(loop)]);
}

function whiteNoise(): Float32Array[] {
  const loop = new Float32Array(seconds(4));
  const rand = noise(1);
  for (let i = 0; i < loop.length; i++) loop[i] = rand();
  return level([loop]);
}

/** Noise with equal energy in every octave (Paul Kellet's filter), closer to how music is spread. */
function pinkNoise(): Float32Array[] {
  const loop = new Float32Array(seconds(4));
  const rand = noise(2);
  let b0 = 0, b1 = 0, b2 = 0;
  for (let i = 0; i < loop.length; i++) {
    const white = rand();
    b0 = 0.99765 * b0 + white * 0.099046;
    b1 = 0.963 * b1 + white * 0.2965164;
    b2 = 0.57 * b2 + white * 1.0526913;
    loop[i] = b0 + b1 + b2 + white * 0.1848;
  }
  return level([fadeEnds(loop)]);
}

// ── the library ─────────────────────────────────────────────────────────────────

const recording = (id: string, label: string, blurb: string): Sound => ({
  id, label, blurb, group: 'Recordings', url: `${import.meta.env.BASE_URL}samples/fx/${id}.mp3`,
});

const RECORDINGS: Sound[] = [
  recording('piano', 'Piano', 'A chord melody, with room to ring'),
  recording('electric', 'Electric guitar', 'Clean DI: strums, then picking'),
  recording('riff', 'Guitar riff', 'Clean DI single notes: fuzz it'),
  recording('strat', 'Clean Strat', 'DI picked loop, 71 bpm'),
  recording('guitar', 'Acoustic guitar', 'An acoustic loop, strummed'),
  recording('vocals', 'Vocals', 'A sung melody, on its own'),
  recording('drums', 'Drums', 'A live kit, 100 bpm'),
];

/** Every sound the page offers, in the order the library lists them. */
export const SOUNDS: Sound[] = [
  ...RECORDINGS,
  { id: 'pad', label: 'Pad', blurb: 'Slow, wide chords. Try chorus or reverb', group: 'Synths', render: pad },
  { id: 'keys', label: 'Electric piano', blurb: 'Soft chords that bark when driven', group: 'Synths', render: keys },
  { id: 'arpeggio', label: 'Plucked arpeggio', blurb: 'Fast single notes. Made for delay', group: 'Synths', render: arpeggio },
  { id: 'lead', label: 'Lead', blurb: 'A bright melody with vibrato', group: 'Synths', render: lead },
  { id: 'bass', label: 'Bass', blurb: 'A low line, to hear what a filter keeps', group: 'Synths', render: bass },
  { id: 'drum-machine', label: 'Drum machine', blurb: 'Kick, snare and hats, tight and dry', group: 'Rhythm', render: drumMachine },
  { id: 'clicks', label: 'Clicks', blurb: 'One every half second: count the echoes', group: 'Rhythm', render: clicks },
  { id: 'sine', label: 'Sine, 220 Hz', blurb: 'A pure tone. Anything else is the effect', group: 'Test signals', render: sine },
  { id: 'sweep', label: 'Sweep', blurb: '40 Hz to 16 kHz: hear a filter’s shape', group: 'Test signals', render: sweep },
  { id: 'pink', label: 'Pink noise', blurb: 'Every octave at once, evenly', group: 'Test signals', render: pinkNoise },
  { id: 'white', label: 'White noise', blurb: 'Every frequency at once, bright', group: 'Test signals', render: whiteNoise },
];

export const findSound = (id: string): Sound | undefined => SOUNDS.find((s) => s.id === id);
