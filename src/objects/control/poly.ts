// poly : give each incoming note a voice.
//
// The object a polyphonic patch is built around: pitch in the left inlet, velocity in
// the right, and out come the voice number (counting from 1), the pitch and the
// velocity. A note-off (velocity 0) goes to the voice that is holding that pitch.
//
// Arguments: how many voices, and whether to steal. With stealing on, a note arriving
// when every voice is busy takes the voice that has been held longest; that voice is
// sent a note-off first. With it off, the extra note is dropped.
//
// A free voice is the lowest-numbered one. This allocation is also what the compiled
// plugins do (mxrt::Poly in native/mxrt.h); the two are kept the same on purpose. It has
// not been compared with Max's own poly, which may hand out free voices in another order.

import { num, register, type MaxNode } from '../../engine/registry';
import { makeOutlets } from '../../runtime/outlets';
import { firstNum, type Msg } from '../../runtime/atoms';

register('poly', (args) => {
  const voices = Math.max(1, Math.floor(num(args[0], 1)));
  const steal = num(args[1], 0) !== 0;
  const pitchOf: (number | undefined)[] = new Array(voices).fill(undefined);
  const startedAt: number[] = new Array(voices).fill(0);
  let clock = 0;
  let velocity = 0;
  const o = makeOutlets();

  // Outlets fire right to left, as in Max, so a pack on the far side has the velocity
  // and pitch before the voice number arrives and sets it off.
  const send = (voice: number, pitch: number, vel: number): void => {
    o.emit(2, [vel]);
    o.emit(1, [pitch]);
    o.emit(0, [voice + 1]);
  };

  const noteOff = (pitch: number): void => {
    let found = -1;
    for (let v = 0; v < voices; v++) {
      if (pitchOf[v] === pitch && (found < 0 || startedAt[v] < startedAt[found])) found = v;
    }
    if (found < 0) return;
    pitchOf[found] = undefined;
    send(found, pitch, 0);
  };

  const noteOn = (pitch: number, vel: number): void => {
    let voice = pitchOf.indexOf(undefined);
    if (voice < 0) {
      if (!steal) return;
      voice = 0;
      for (let v = 1; v < voices; v++) if (startedAt[v] < startedAt[voice]) voice = v;
      send(voice, pitchOf[voice] as number, 0);
    }
    pitchOf[voice] = pitch;
    startedAt[voice] = ++clock;
    send(voice, pitch, vel);
  };

  return {
    signalIns: [],
    signalOuts: [],
    controlIns: [
      (m: Msg) => {
        const pitch = firstNum(m);
        if (pitch === undefined) return;
        if (velocity > 0) noteOn(pitch, velocity);
        else noteOff(pitch);
      },
      (m: Msg) => { const v = firstNum(m); if (v !== undefined) velocity = v; },
    ],
    onControlOut: o.onControlOut,
  } satisfies MaxNode;
});
