# Plugin Playground

Build a Max for Live audio effect in the browser: pick a preset or build an effect
chain, hear it on a sample, download the `.amxd`.

It was first built inside the [MaxPyLang](https://github.com/Barnard-PL-Labs/MaxPyLang)
repo (as `web/interactive-plugin.html`) and moved here to be its own project.

```sh
npm install
npm run dev        # http://localhost:5173
npm test           # the browser half (vitest)
npm run build      # static site in dist/, works from any folder

pip install maxpylang pytest
pytest tests       # the generator: every preset builds, checks clean, round-trips .amxd
```

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
- **The engine plays the device as a host would.** `src/runtime/host.ts` is a pair of
  buses standing in for the Live track: the page plays a sample into `plugin~` and
  listens to `plugout~`.
- **The sound is checked against Max.** `scripts/maxcheck/` renders a test signal through
  the same generated patches in Max and in this engine and compares the recordings. Its
  README has the method and the results.

## What is copied from MaxPyLang

Everything outside `src/plugin/` is a copy of MaxPyLang's web engine (`web/src/` there)
as of November 2026: only the files this page needs, unchanged. Engine fixes should go to
MaxPyLang first and be copied here.

`public/py/` holds the maxpylang wheel the page loads into Pyodide. To update it, run
`python -m pip wheel . --no-deps -w <this folder>/public/py` in a MaxPyLang checkout and
change `WHEEL_PATH` in `src/plugin/python.ts` if the version changed.

Where the generated script has to work around something maxpylang cannot say yet, it
says `gap M4L-NN`. Those entries are in MaxPyLang's
[gap log](https://github.com/Barnard-PL-Labs/MaxPyLang/blob/main/docs/type-system/m4l-gaps.md).

Samples are CC0 from Freesound (`public/samples/fx/CREDITS.md`). MIT licensed.
