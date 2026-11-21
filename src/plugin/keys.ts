// Playing an instrument on the page: a piano to click or drag across, the computer's
// keyboard (each letter is printed on the key it plays), a MIDI keyboard if one is
// plugged in, and a few phrases that loop so the hands are free to
// turn dials.
//
// Everything here ends in one call, `onNote(key, velocity)`, with a velocity of 0 for a
// key coming up: the same two numbers a notein object hands a patch. main.ts sends them
// to the notein of the running device.

/** MIDI note numbers for the piano drawn on the page: C2 up to C7. */
const LOW = 36;
const HIGH = 96;
/** The part of it a phone shows: C3 up to B5. */
const PHONE_LOW = 48;
const PHONE_HIGH = 83;
/** The octaves the computer keyboard can sit in, so that every letter stays on the piano. */
const LOWEST_OCTAVE = 2;
const HIGHEST_OCTAVE = 5;

const BLACK = new Set([1, 3, 6, 8, 10]); // semitones above C
const isBlack = (key: number): boolean => BLACK.has(key % 12);

/** The usual two rows: A S D F ... are the white keys from C, W E T Y U ... the black ones. */
const COMPUTER_KEYS: Record<string, number> = {
  KeyA: 0, KeyW: 1, KeyS: 2, KeyE: 3, KeyD: 4, KeyF: 5, KeyT: 6, KeyG: 7, KeyY: 8, KeyH: 9, KeyU: 10, KeyJ: 11,
  KeyK: 12, KeyO: 13, KeyL: 14, KeyP: 15, Semicolon: 16,
};

/** What is printed on a key for the letter that plays it. */
const letterOf = (code: string): string => (code === 'Semicolon' ? ';' : code.replace('Key', ''));

export class Keyboard {
  private readonly held = new Map<number, Set<string>>(); // key -> who is holding it
  private readonly keys = new Map<number, HTMLElement>();
  private octave = 4; // where the computer keyboard's A sits: C4, middle C
  private midi: MIDIAccess | undefined;
  /** Called when the computer keyboard moves to another octave, with the note its A now plays ("C4"). */
  onOctave: (name: string) => void = () => {};

  constructor(private readonly host: HTMLElement, private readonly onNote: (key: number, velocity: number) => void) {
    this.draw();
    this.label();
    this.listenToPointer();
  }

  /** Move the computer keyboard up or down by octaves. Whatever it was holding is let go. */
  shiftOctave(by: number): void {
    const next = Math.min(HIGHEST_OCTAVE, Math.max(LOWEST_OCTAVE, this.octave + by));
    if (next === this.octave) return;
    this.releaseAll('computer');
    this.octave = next;
    this.label();
    this.onOctave(`C${next}`);
  }

  /** The key the computer keyboard's A plays in the current octave. */
  private get base(): number {
    return 12 * (this.octave + 1);
  }

  /** Print each letter on the key it plays. */
  private label(): void {
    for (const el of this.keys.values()) delete el.dataset.letter;
    for (const [code, step] of Object.entries(COMPUTER_KEYS)) {
      const el = this.keys.get(this.base + step);
      if (el) el.dataset.letter = letterOf(code);
    }
  }

  /**
   * A key goes down. `who` names the source (the mouse, the phrase, a MIDI port), so a
   * key held by two of them only comes up when both have let go.
   */
  press(key: number, velocity: number, who: string): void {
    if (key < 0 || key > 127) return;
    const holders = this.held.get(key) ?? new Set<string>();
    const already = holders.size > 0;
    holders.add(who);
    this.held.set(key, holders);
    if (already) this.onNote(key, 0); // struck again: let go first, so the envelope restarts
    this.onNote(key, Math.max(1, Math.min(127, Math.round(velocity))));
    this.keys.get(key)?.classList.add('is-down');
  }

  release(key: number, who: string): void {
    const holders = this.held.get(key);
    if (!holders?.delete(who) || holders.size > 0) return;
    this.held.delete(key);
    this.onNote(key, 0);
    this.keys.get(key)?.classList.remove('is-down');
  }

  /** Let go of everything `who` holds; with no name, of everything. */
  releaseAll(who?: string): void {
    for (const [key, holders] of [...this.held]) {
      for (const holder of who ? [who] : [...holders]) this.release(key, holder);
    }
  }

  /**
   * The patch was rebuilt: its voices are new and silent, so nothing is sounding. Forget
   * what was held without sending note-offs to voices that never heard the note-ons.
   */
  forget(): void {
    this.held.clear();
    for (const el of this.keys.values()) el.classList.remove('is-down');
  }

  // ── the piano on the page ─────────────────────────────────────────────────────

  private draw(): void {
    this.host.replaceChildren();
    for (let key = LOW; key <= HIGH; key++) {
      const el = document.createElement('i');
      el.dataset.key = String(key);
      el.className = isBlack(key) ? 'key black' : 'key';
      // The outer octaves are left off a phone's screen, where the keys would be slivers.
      if (key < PHONE_LOW || key > PHONE_HIGH) el.classList.add('outer');
      if (key % 12 === 0) el.dataset.name = `C${key / 12 - 1}`;
      // A black key hangs between two white ones: it is drawn inside the white key to its left.
      if (isBlack(key)) this.keys.get(key - 1)?.append(el);
      else this.host.append(el);
      this.keys.set(key, el);
    }
  }

  private listenToPointer(): void {
    let down: number | undefined;
    const keyAt = (e: PointerEvent): number | undefined => {
      const el = document.elementFromPoint(e.clientX, e.clientY)?.closest<HTMLElement>('.key');
      return el && this.host.contains(el) ? Number(el.dataset.key) : undefined;
    };
    // Higher on a key is softer, lower is louder, as on most on-screen pianos.
    const velocityAt = (e: PointerEvent): number => {
      const box = this.host.getBoundingClientRect();
      return 50 + 77 * Math.min(1, Math.max(0, (e.clientY - box.top) / box.height));
    };
    const move = (e: PointerEvent): void => {
      const key = keyAt(e);
      if (key === down) return;
      if (down !== undefined) this.release(down, 'pointer');
      down = key;
      if (key !== undefined) this.press(key, velocityAt(e), 'pointer');
    };
    this.host.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      this.host.setPointerCapture(e.pointerId);
      move(e);
    });
    // Dragging across the keys plays each one it reaches.
    this.host.addEventListener('pointermove', (e) => {
      if (this.host.hasPointerCapture(e.pointerId)) move(e);
    });
    const up = (): void => {
      if (down !== undefined) this.release(down, 'pointer');
      down = undefined;
    };
    this.host.addEventListener('pointerup', up);
    this.host.addEventListener('pointercancel', up);
  }

  // ── the computer keyboard ─────────────────────────────────────────────────────

  /** A S D F ... play notes while `active()` says so. Z and X move down and up an octave. */
  listenToComputerKeys(active: () => boolean): void {
    const typing = (e: KeyboardEvent): boolean => {
      const t = e.target as HTMLElement;
      return t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable || e.metaKey || e.ctrlKey || e.altKey;
    };
    window.addEventListener('keydown', (e) => {
      if (!active() || typing(e) || e.repeat) return;
      if (e.code === 'KeyZ' || e.code === 'KeyX') {
        this.shiftOctave(e.code === 'KeyX' ? 1 : -1);
        return;
      }
      const step = COMPUTER_KEYS[e.code];
      if (step === undefined) return;
      e.preventDefault();
      this.press(this.base + step, 100, 'computer');
    });
    window.addEventListener('keyup', (e) => {
      const step = COMPUTER_KEYS[e.code];
      if (step !== undefined) this.release(this.base + step, 'computer');
    });
    window.addEventListener('blur', () => this.releaseAll('computer'));
  }

  // ── MIDI ──────────────────────────────────────────────────────────────────────

  /**
   * Listen to every MIDI input the browser will let us see. Resolves to their names; an
   * empty list means none is plugged in. Rejects if the browser has no Web MIDI or the
   * visitor said no.
   */
  async connectMidi(): Promise<string[]> {
    if (!navigator.requestMIDIAccess) throw new Error('This browser has no Web MIDI.');
    this.midi ??= await navigator.requestMIDIAccess();
    const listen = (): string[] => {
      const names: string[] = [];
      for (const input of this.midi!.inputs.values()) {
        names.push(input.name ?? 'MIDI input');
        input.onmidimessage = (e) => this.midiMessage(e.data, `midi:${input.id}`);
      }
      return names;
    };
    this.midi.onstatechange = () => listen(); // a keyboard plugged in later
    return listen();
  }

  private midiMessage(data: Uint8Array | null, who: string): void {
    if (!data || data.length < 3) return;
    const status = data[0] & 0xf0;
    if (status === 0x90 && data[2] > 0) this.press(data[1], data[2], who);
    else if (status === 0x80 || status === 0x90) this.release(data[1], who);
  }
}

// ── phrases ─────────────────────────────────────────────────────────────────────

/** A note of a phrase: when it starts and how long it lasts, in sixteenth notes. */
type Step = [at: number, key: number, length: number, velocity?: number];

export interface Phrase {
  id: string;
  label: string;
  /** Beats per minute. */
  tempo: number;
  /** Length of the loop, in sixteenth notes. */
  length: number;
  steps: Step[];
}

const chord = (at: number, keys: number[], length: number, velocity = 90): Step[] => keys.map((k) => [at, k, length, velocity]);

export const PHRASES: Phrase[] = [
  {
    id: 'chords', label: 'Chords', tempo: 96, length: 64,
    steps: [
      ...chord(0, [57, 60, 64, 67], 14), ...chord(16, [53, 57, 60, 64], 14),
      ...chord(32, [48, 55, 60, 64], 14), ...chord(48, [55, 59, 62, 66], 14),
    ],
  },
  {
    id: 'melody', label: 'Melody', tempo: 104, length: 32,
    steps: [
      [0, 69, 3], [4, 72, 2], [6, 76, 5], [12, 74, 2], [14, 72, 2],
      [16, 69, 3], [20, 67, 3], [24, 64, 6, 80], [30, 67, 2, 70],
    ],
  },
  {
    id: 'arpeggio', label: 'Arpeggio', tempo: 112, length: 32,
    steps: [57, 64, 69, 72, 76, 72, 69, 64, 53, 60, 65, 69, 72, 69, 65, 60].flatMap((key, i): Step[] => [
      [i * 2, key, 1, i % 4 === 0 ? 105 : 80],
    ]),
  },
  {
    id: 'bass', label: 'Bass line', tempo: 100, length: 32,
    steps: [33, 33, 45, 33, 36, 36, 43, 40, 29, 29, 41, 29, 31, 31, 43, 35].flatMap((key, i): Step[] => [
      [i * 2, key, 2, i % 2 === 0 ? 105 : 85],
    ]),
  },
];

/** Loops a phrase on a keyboard, so the piano shows what is being played. */
export class Sequencer {
  private timer = 0;
  private phrase: Phrase | undefined;
  private startedAt = 0;
  private position = 0; // sixteenth notes played since the start, not wrapped
  private readonly endings = new Map<number, number>(); // key -> the position it is let go at

  constructor(private readonly keyboard: Keyboard) {}

  get playing(): boolean {
    return this.phrase !== undefined;
  }

  start(phrase: Phrase): void {
    this.stop();
    this.phrase = phrase;
    this.startedAt = performance.now();
    this.position = 0;
    // A timer can run late, so each tick plays everything that has come due since the last.
    this.timer = window.setInterval(() => this.tick(), 20);
    this.tick();
  }

  stop(): void {
    clearInterval(this.timer);
    this.phrase = undefined;
    this.endings.clear();
    this.keyboard.releaseAll('phrase');
  }

  private tick(): void {
    const phrase = this.phrase;
    if (!phrase) return;
    const sixteenth = 60000 / phrase.tempo / 4;
    const now = Math.floor((performance.now() - this.startedAt) / sixteenth);
    for (; this.position <= now; this.position++) {
      for (const [key, end] of [...this.endings]) {
        if (end <= this.position) {
          this.keyboard.release(key, 'phrase');
          this.endings.delete(key);
        }
      }
      for (const [at, key, length, velocity = 95] of phrase.steps) {
        if (at !== this.position % phrase.length) continue;
        this.keyboard.press(key, velocity, 'phrase');
        this.endings.set(key, this.position + length);
      }
    }
  }
}
