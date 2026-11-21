# Plugin Playground

Build a Max for Live device in the browser and download it: an **audio effect** (pick a
preset or build an effect chain, hear it on a sample) or a **MIDI instrument** (a
polyphonic synth you play on the page). Either one downloads as an `.amxd` for Ableton
Live, and compiles to a VST3, CLAP or Audio Unit plugin for any other host.

It was first built inside the [MaxPyLang](https://github.com/Barnard-PL-Labs/MaxPyLang)
repo (as `web/interactive-plugin.html`) and moved here to be its own project.

```sh
npm install
npm run dev        # http://localhost:5173
npm test           # the browser half (vitest)
npm run build      # static site in dist/, works from any folder

pip install public/py/maxpylang-*.whl pytest numpy   # the wheel the page loads
pytest tests       # the generator, and the C++ compiled from its patches

npm run plugin -- --preset "Tape Lo-Fi"   # a native CLAP / VST3 / Audio Unit plugin
npm run plugin -- --preset "Soft Pad"     # ...an instrument works the same way
```

The site is at <https://chrishyoroklee.github.io/plugin-playground/>. Every push to `main`
runs the tests, builds `dist/` and publishes it (`.github/workflows/pages.yml`). GitHub
Pages has to use "GitHub Actions" as its source: the page is TypeScript, so the repository
root cannot be served as it is.

## How it works

```
settings ──▶ src/plugin/fxgen.py (maxpylang, in Pyodide) ──▶ patcher JSON ──┬─▶ engine (sound)
    ▲                                                                      ├─▶ device view
    └────────────── a dial turned in the device view ◀─────────────────────┴─▶ .amxd
```

- **Python is the source of truth.** `fxgen.py` writes a plain maxpylang script for the
  current settings and runs it. The page plays the resulting JSON, and the download is
  the same patch through `save(..., device_type="audio_effect")`. "View Python" shows
  the script that ran, with maxpylang's type check results above it.
- **`src/plugin/schema.json`** describes the modules and every dial's range, and is read
  by both the generator and the page. **`presets.json`** is the preset library.
- **The sound library is mostly computed.** `src/plugin/sounds.ts` lists what the page can
  play through a device: the four recordings in `public/samples/fx`, and synth loops, a
  drum machine and test signals that are computed in the browser when first chosen. You
  can also drop in your own audio file.
- **Light by default.** Dark mode is a switch under the gear (`src/plugin/theme.ts`); every
  colour that differs is a variable at the top of `plugin.css`.
- **The engine plays the device as a host would.** `src/runtime/host.ts` is a pair of
  buses standing in for the Live track: the page plays a sample into `plugin~` and
  listens to `plugout~`.
- **An instrument is the same thing with notes in front.** Under the Instrument tab the
  generator puts a synth where `plugin~` was: `notein`, a `poly` that hands each note a
  voice, and the voices laid out side by side (two oscillators, a filter, an envelope),
  followed by the same effect chain. The page plays it from an on-screen piano, the
  computer keyboard, a MIDI keyboard or a looping phrase (`src/plugin/keys.ts`).
- **The same patch compiles to a native plugin.** `src/plugin/patchc.py` turns the
  patcher JSON into C++ (one readable `Effect.h` per effect, on the kernels in
  `native/mxrt.h`), and `npm run plugin` builds that as CLAP, VST3 and Audio Unit, so the
  effect runs outside Ableton Live. No Max or RNBO is needed to build.
  The site also carries ready-made builds of every preset, made by CI on each deploy.
  [`native/README.md`](native/README.md) has the design and its limits.
- **The sound is checked against Max.** `scripts/maxcheck/` renders a test signal through
  the same generated patches in Max and in this engine and compares the recordings. Its
  README has the method and the results.

## What is copied from MaxPyLang

Everything under `src/` outside `src/plugin/` is a copy of MaxPyLang's web engine
(`web/src/` there) as of November 2026: only the files this page needs, unchanged. Engine
fixes should go to MaxPyLang first and be copied here.

`public/py/` holds the maxpylang wheel the page loads into Pyodide. To update it, run
`python -m pip wheel . --no-deps -w <this folder>/public/py` in a MaxPyLang checkout and
change `WHEEL_PATH` in `src/plugin/python.ts` if the version changed.

Where the generated script has to work around something maxpylang cannot say yet, it
says `gap M4L-NN`. Those entries are in MaxPyLang's
[gap log](https://github.com/Barnard-PL-Labs/MaxPyLang/blob/main/docs/type-system/m4l-gaps.md).

Samples are CC0 from Freesound (`public/samples/fx/CREDITS.md`). MIT licensed.
