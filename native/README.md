# Native plugins

The same patch the page plays and the `.amxd` holds, compiled to C++ and built as a CLAP,
VST3 and Audio Unit plugin: an audio effect, or an instrument played with MIDI. No Max,
no RNBO and no JUCE are involved in the build.

```sh
npm run plugin -- --list                       # the presets, effects and instruments
npm run plugin -- --preset "Tape Lo-Fi"        # by name or id; part of a name is enough
npm run plugin -- --preset "Soft Pad"          # an instrument
npm run plugin -- --settings my-effect.json    # the page's settings, saved as a file
npm run plugin -- --preset pingpong --install  # ...and copy the plugins where hosts look
```

It needs Python with `maxpylang` (`pip install public/py/maxpylang-*.whl`, the build the
page itself loads), CMake 3.21 or newer and a C++17 compiler. The first
build downloads the plugin SDKs. Everything lands in `build/plugins/<name>/`:

```
Effect.h         the effect or instrument, as C++ you can read
patch.json       the Max patch it was compiled from
<Name>.clap  <Name>.vst3  <Name>.component (macOS)
```

`build/plugins/_build` and `_sdk` are CMake's. They are shared, so the SDKs compile once
and each further plugin takes a few seconds. `--all --package DIR` builds every preset
and zips each one for the system it was built on, which is how the site's ready-made
downloads are made (`.github/workflows/`). Those builds are not signed: on macOS a
downloaded plugin has to have its quarantine flag cleared before a host will load it,
and the README inside each zip says how.

## How it works

```
settings ─▶ fxgen.py ─▶ patch JSON ─▶ patchc.py ─▶ Effect.h ─┬─▶ shell/plugin.cpp ─▶ .clap ─▶ .vst3, .component
                                                   mxrt.h ───┘└─▶ render/render.cpp ─▶ a WAV, for checking
```

**`src/plugin/patchc.py`, the compiler.** It reads the patch JSON and writes one C++
class:

- Every `live.dial` becomes a parameter, with the name, range, unit and curve saved on
  the box.
- The control objects between a dial and a signal inlet (`scale`, `pow`, `sin`, `cos`,
  `sqrt`, `dbtoa`, `*`, `+`, `!-`, `pack`) become a few lines that run when that
  parameter changes.
- The signal objects are sorted so each runs after what feeds it and written as one
  loop over the samples, one line per object, named after its box.
- A feedback loop is allowed where Max allows it, through `tapin~` / `tapout~`. The
  tap that closes a loop is read before its line is written; every other tap after.
- An instrument has a `notein` instead of a `plugin~`. Its note path has to be the one
  the generator writes, `notein → poly → pack 0 0 0 → route 1 2 .. → unpack` per voice:
  `poly` becomes a voice allocator, and what each voice does with its pitch and velocity
  becomes the code behind `noteOn()` and `noteOff()`.
- Panels and comments are dropped.
- Anything it does not model is an error naming the box: an unknown object, a cord into
  an inlet it has no code for, a loop with no delay in it. It never skips something and
  carries on.

**`mxrt.h`, the kernels.** One small struct or function per Max object, header only,
standard library only. `prepare()` is the only place that allocates. The maths is the
browser engine's, which `scripts/maxcheck/` checked against Max 9: `svf~`'s coefficient
and damping, `onepole~`, `overdrive~`'s curve, `degrade~` rounding down in single
precision, `comb~` and `allpass~` cutting a delay to whole samples.

**`Effect.h`, what patchc writes.** A plain class with no plugin format in it:

```cpp
Effect fx;
fx.prepare(sampleRate);          // allocates; call again if the rate changes
fx.setParam(index, value);       // in the dial's own units; Effect::kParams describes them
fx.process(in, out, frames);     // stereo, float or double, in place if you like
fx.reset();                      // silence what is still ringing
fx.noteOn(key, velocity);        // an instrument (Effect::kIsInstrument): velocity 1 to 127
fx.noteOff(key);
```

**`shell/plugin.cpp`, the plugin.** One hand-written CLAP wrapper around `Effect.h`:
stereo in and out, parameters with their units as text, sample-accurate parameter
changes, saved state, and a tail so hosts keep running delays and reverbs. An instrument
gets a note input in place of the audio input, takes the host's notes or raw MIDI on the
sample they arrive, and is declared a music device to Audio Unit hosts.
[clap-wrapper](https://github.com/free-audio/clap-wrapper) builds the VST3 and the Audio
Unit from the same code. There is no custom window; the host shows its own controls.

A dial with a curve (every frequency and time) is given to the host as the knob's
position, 0 to 1, so the host's slider feels like the dial in Live. The text beside it
is still in Hz or ms, and saved state holds real values.

## Adding an object

One kernel in `mxrt.h` if the object has state, and one class in `patchc.py` (ten lines
or so, next to the others) saying what its inlets take and what C++ one sample of it is.
Then add it to a preset, or `tests/test_patchc.py` will say that patchc knows an object
no preset uses.

## How it is checked

- **The sound.** `scripts/maxcheck/native.py` renders the maxcheck signal through the
  compiled C++ for every test, next to the Max and browser renderings, and `compare.py`
  reads all three. Results are in [`scripts/maxcheck/README.md`](../scripts/maxcheck/README.md).
- **Every preset.** `pytest tests/test_patchc.py` compiles all 31 presets and the 57
  module tests, renders each and holds its level to the browser engine's rendering of
  the same patch (`tests/reference/browser.json`), with no browser or Max needed.
- **Instruments.** The same file compiles all 12 instrument presets and plays each one
  (`render --notes`): a note has to be in tune, the envelope has to follow its dials and
  the velocity, a mono instrument has to hand its voice to the newest key, and a key
  past the last free voice has to steal the oldest.
- **The plugin.** Every `npm run plugin` build ends by running `test/hostcheck.cpp`: a
  small host that loads the `.clap`, moves every parameter in the middle of a block and
  requires the output to equal a bare `Effect` given the same moves, sample for sample;
  then checks the parameter list, its text, and that saved state comes back.
- **Host rules.** `npm run plugin -- --preset X --install --validate` runs
  `clap-validator`, `pluginval` and, on macOS, `auval`, whichever are installed.
  `.github/workflows/native.yml` does this on Linux, macOS and Windows.

Where that stands (November 2026): CI builds every preset, 31 effects and 12 instruments,
on Linux, macOS and Windows, and each passes hostcheck. Three of them (Ping-Pong, Tape
Lo-Fi and the instrument Soft Pad) are also put through clap-validator, pluginval at
strictness 5 (VST3 everywhere, the Audio Unit on macOS) and auval. Nobody has yet loaded
one in Logic or another host by hand, and the C++ has been compared with the browser
engine, not directly with Max recordings.

## Limits

- Only the objects `fxgen.py` uses are covered: `patchc.SUPPORTED` lists them. An
  arbitrary Max patch will stop with an error at its first unknown object.
- A `tapout~` with a signal for its time is read 64 samples early in Max. The C++ uses
  the time as written, as the browser engine does, so a chorus sits 1.5 ms further out
  than in Live, and one hop of a ping-pong differs by the same amount.
- The instrument objects have not been measured against Max the way the effect objects
  were. `adsr~` is straight lines scaled by the note's velocity, `saw~`, `rect~` and
  `tri~` are band-limited by a method of our own, and `poly` gives a note the lowest
  free voice and steals the oldest: right in kind, not checked sample for sample, and
  Max's `poly` may hand out free voices in another order.
- The page plays an instrument with the browser's own oscillators, so it and the plugin
  agree in pitch, level and envelope, not waveform for waveform.
- LFOs start at zero phase. In Max a `cycle~` has been running since audio was switched
  on, so where a sweep starts there is arbitrary.
- A dial moved by the host sets its numbers at once, as it does in Max: there is no
  smoothing beyond what the patch has (the delay time glides through its `line~`).
- No custom window, and no RNBO export.

## Licences

CLAP, clap-wrapper and the VST3 SDK (3.8) are MIT licensed. Apple's AudioUnitSDK, which
clap-wrapper uses for the Audio Unit only, is Apache 2.0. They are downloaded at build
time and none of their code is in this repository.
