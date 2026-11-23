# Checking the browser engine and the C++ against Max

The plugin generator plays a device in the browser, downloads it for Live, and compiles
it to a native plugin. These scripts check that the three sound the same, by rendering
one test signal through the same generated patches in Max, in the browser engine and in
the compiled C++, and comparing the recordings.

The Max side needs Max installed (macOS paths below); the other two do not. Nothing is
played through the speakers.

```sh
cd scripts/maxcheck && mkdir -p out && cd out
python ../make_signal.py signal.wav
python ../tests.py                                  # writes tests.json

# Max: one patch, every test, about half a minute. Max opens, records and quits.
python ../max_batch.py tests.json "$PWD/signal.wav" 10.3 "$PWD/max"
open -W -a Max max/batch.maxpat

# Browser: headless Chromium against the dev server.
python ../web_patches.py tests.json web_patches.json
python ../server.py . &                             # port 8766
(cd ../../.. && npm run dev) &                      # port 5173
node ../headless.mjs web_patches.json signal.wav web

# C++: each patch compiled by patchc and rendered by native/render. Needs a C++ compiler.
python ../native.py tests.json signal.wav native

python ../analyze.py max                            # what each effect does, in Max
python ../compare.py max web                        # browser minus Max
python ../compare.py max native                     # C++ minus Max
python ../compare.py web native                     # C++ minus browser
```

## What it found

Measured in Max 9.1.5 at 44.1 kHz, and now built into the engine and the generator:

| Object | What Max does |
|---|---|
| `overdrive~` | `y = sign(x) * (1 - (1 - abs(x)) ^ drive)`, flat past full scale |
| `svf~` | frequency coefficient `sin(2*pi*fc/sr)`; damping `(1.2 * (1 - res))^2`, capped at 1; cutoff up to sr/4 |
| `onepole~` | `y += g * (x - y)`, `g = 1 - exp(-2*pi*fc/sr)`; cutoff up to sr/4 |
| `degrade~` | rounds down, not to nearest; takes a sample, then advances its phase, in single precision |
| `comb~`, `allpass~` | a delay given as a number is cut down to whole samples |
| `comb~` | a delay given as a signal is read between samples (linear) |
| `tanh~`, `clip~`, `pong~ 0` | the plain math: tanh, clip, fold |

With those in place the browser matches Max to the last bit of a 16-bit recording for
the filter, every distortion type, the delay, the flanger and the reverb.

What still differs, and why it is left alone:

- **Chorus, flanger and tremolo sweeps** start wherever their LFO happens to be. In Max
  the `cycle~` has been running since audio was switched on.
- **`degrade~` at low rates** holds different samples for the same reason: its phase
  free-runs.
- **A `tapout~` with a signal time and no feedback** reads 64 samples (one signal vector)
  early in Max, so the chorus sits 1.5 ms closer than its dial says. The browser uses
  the time as written.
- **A ping-pong delay** has the same 64 samples in one of its two hops in Max.

One thing was the browser's, not Max's: Chrome runs every feedback cycle one render
quantum (128 samples) late. `tapout~` now offers an early read for cords that close a
loop (`loopOuts` in `engine/registry.ts`), and `comb~` is a worklet with its loop inside.

## The C++ against the browser engine

`native.py` renders the same tests through the C++ that `patchc.py` compiles
(`native/README.md`). Run in November 2026 on all 57 tests above plus the 31 presets,
against the browser engine in headless Chromium, comparing 32-bit float output sample by
sample (`native.py --float --as-chrome`, which also reads the signal the way Chrome
decodes it: positive samples divided by 32767, negative by 32768):

- 67 of the 88 agree to within 0.000002 of full scale, which is the browser's
  single-precision signal path against doubles. That covers the filter, four of the five
  distortions, the crusher, the tremolo, both delays and the reverb.
- Level in tenth-of-a-second windows agrees within 0.02 dB wherever either rendering is
  above -80 dB, on 86 of the 88. The other two are the whole chain (0.3 dB in one window
  above -60 dB, 0.5 dB in a quieter one) and Blown Speaker's click, below.

The EQ came later and was added the same way: 9 tests and its 5 presets, rendered in
headless Chromium and in the C++. The browser's files were 16-bit that time, and the two
agree to within half a step of one (0.000016 of full scale) on all 14. The EQ has not
been rendered in Max.

Where the samples differ by more, and why:

| Tests | Largest difference | Cause |
|---|---|---|
| Fuzz | 0.0002 | The browser's `clip~` is a 1024-point table, which rounds the corner at -0.35. The C++ clips exactly. |
| Chorus and flanger | 0.0003 at the default depth, 0.0026 with rate and depth at full | Chrome's `DelayNode` keeps its read position in single precision: a constant 12 ms tap is read up to 0.0002 samples off (measured). The C++ uses doubles. |
| Anything with the crusher after one of those | 0.004 | `degrade~` rounds down, so a difference of any size in what it is fed moves a sample a whole step now and then. |

**Blown Speaker clicks once in the silence after a sound**, at -62 dB, and not at the
same moment in each renderer. Its fuzz leaves a tail that creeps up to zero from below,
and `degrade~` rounds down, so the crusher sits one step below zero until the tail is
exactly zero; the step back up is the click. In the browser that is 0.9 s after the
impulse, where single precision runs out. The C++ computes in doubles, as Max does, and
its tail is still running when the ten-second test ends.

One thing found on the way: a ping-pong delay is one loop through two delay lines. Only
one of its taps has to be read before its line is written; the C++ reads the other after,
so both hops are exactly the dial's time, as they are in the browser.

There are no Max recordings in this repository, so `compare.py max native` has not been
run here: what the C++ has been held to directly is the browser engine. Its two known
differences from Max carry over unchanged (a `tapout~` with a signal time is 64 samples
later than Max's, and LFOs start at zero phase).

`tests/reference/browser.json` is the browser's rendering boiled down to levels, which is
what `pytest tests/test_patchc.py` holds the C++ to. To make it again after changing the
engine or the generator, render `web/` as above (with the presets added to `tests.json`
as `preset_<id>` if you want them covered) and run:

```sh
python ../reference.py tests.json signal.wav web ../../../tests/reference/browser.json
```
