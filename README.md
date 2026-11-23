# Plugin Playground

Make an audio plugin in the browser, hear it, and download it.

- An **effect**: pick one of 91 presets or build your own chain from eleven modules
  (filter, four kinds of EQ: three-band, parametric, graphic and tilt, distortion, crush,
  chorus, tremolo, delay, and a reverb that is a room, a hall or a plate), and hear it on a sound
  from the library or on your own audio.
- An **instrument**: a polyphonic synth with 41 presets, played from an on-screen
  keyboard, your computer keyboard, a MIDI keyboard or a looping phrase.

Either one downloads as a **Max for Live device** (`.amxd`) for Ableton Live, or as a
**VST3, CLAP or Audio Unit plugin** for any other host.

Try it at <https://chrishyoroklee.github.io/plugin-playground/>.

## Using the page

- **Effect / Instrument** (top left) switches what you are making. Each keeps its own
  device, so switching back finds it as you left it.
- **Presets / Customize** (left panel) picks a starting point, then changes it: the
  name, the accent colour, the synth's sections, and which effects are on and in what
  order.
- **The device** in the middle is what you get. Each effect has a picture of what it
  does above its dials: an EQ's curve, a filter's slope, a distortion's bend, a delay's
  echoes, a reverb's tail, an envelope's shape. Drag the dot on a picture and its dials
  turn; turn a dial and the picture follows. The dials are the plugin's parameters, and
  the exact numbers. **Edit**, **Look** has "Dials only" for a device without pictures.
  A module that comes in kinds (a filter's four, a distortion's five) has them as buttons
  along the top of its picture. While sound is running, the page draws it on the
  pictures: the device's output spectrum behind an EQ's or a filter's curve, a dot going
  along a tremolo's or a chorus's wave at its real speed, and a dot going round the
  envelope with the latest note. Double-click its name to rename it. The corner of the stage has a zoom
  control (pinch or Ctrl-scroll works too), and **Patch** shows the Max patch behind it.
- **Play.** For an effect, the play button runs a sound through the device: choose one
  from the library (recordings, synth loops, a drum machine, test signals) or use your
  own file. For an instrument, click or drag the keyboard, play the letters printed on
  its keys, press **MIDI** to use a MIDI keyboard, or press play to loop a phrase.
- **Download** offers the Max for Live device and the plugin. The plugin takes two steps.
  Once, you install Plugin Playground for your system (macOS, Windows or Linux: you pick
  it and its version, and the page checks it). After that every device, a preset or a
  chain of your own, is one small file: open it and it is a plugin in every DAW on the
  machine, under the name you gave it, with every dial where it stood. On macOS the plugin
  opens with the same face it has on the page; on Windows and Linux the DAW shows its
  own sliders for now.
- **Edit**, above the device, unlocks it. Drag an effect's card to move it, press its x to
  take it out, pick its type on the card, and add another with **+ Effect**. **+ Knob**
  adds a knob of your own: click the dials it should turn, and one knob moves them all.
  Click any dial to make it turn by itself: a slow sweep, a quick wobble, or further the
  louder you play. A dial that is being turned shows an arc for how far, and a dot that
  runs along it while sound plays; in Edit the end of the arc is a handle, and dragging
  it round the dial sets how far. **Look** chooses pictures or dials alone, and the
  device's theme and its dials' colour.
- **Describe it** (under Customize) makes a device from a sentence. The site has no
  server and pays for nothing, so there are two ways: **Copy prompt** gives you a prompt
  for any chatbot, and you paste its reply back; or you put in your own Anthropic key,
  which stays in your browser, and the page asks Claude Haiku directly. Either way the
  AI only fills in settings, which are bounded like any others.
- **Settings** (the gear) has dark mode, a link that reopens this exact device, and the
  Python that built it.

## Running it

```sh
npm install
npm run dev        # the page, at http://localhost:5173
npm test           # the browser half (vitest)
npm run build      # a static site in dist/, which works from any folder

pip install public/py/maxpylang-*.whl pytest numpy
pytest tests       # the generator, and the C++ compiled from its patches
```

Install maxpylang from the wheel in `public/py`, as above. It is the build the page
itself loads; the 0.1.1 on PyPI is older and cannot build these devices.

## Building a plugin

```sh
npm run plugin -- --list                           # every preset, effects and instruments
npm run plugin -- --preset "Tape Lo-Fi"            # an effect, by name or id
npm run plugin -- --preset "Soft Pad" --install    # an instrument, copied where hosts look
npm run plugin -- --settings My_Synth.json         # settings saved from the page's Download menu
```

This needs Python with maxpylang, CMake 3.21 or newer and a C++17 compiler; no Max, RNBO
or JUCE. The plugin lands in `build/plugins/<name>/` as `.clap`, `.vst3` and, on macOS,
`.component`, next to the C++ it was built from. [`native/README.md`](native/README.md)
has the details, the design and its limits.

That command compiles one device into a plugin of its own. The site does not need it:
what it installs is one plugin, compiled once, that runs any device from a file
(`native/runtime/`), and the page writes that file itself. `npm run plugin -- --runtime
--package DIR` builds that plugin, its helper and their installer (a `.pkg` for macOS, a
setup `.exe` for Windows, a `.tar.gz` with an install script for Linux). The installers
are not signed with a paid developer certificate yet, so macOS and Windows each ask to
allow them once; the Download menu says how.

## The presets

Every preset is played and measured (`python scripts/presetcheck.py`, and
`tests/test_presets.py` for what every machine can run): an effect on the sample the page
plays it on, an instrument on a note, a chord and a run. Each has to sit at the common
level, not clip, die away when the sound stops, do something, be in tune, and, if it has
dials that turn by themselves, sound different from the same preset without them. Two
that measure alike are listed for a person to listen to. It cannot say that a preset
sounds good, only that it is not broken, silent, a copy, or twice as loud as the rest.

## How it works

```
settings ─▶ fxgen.py ─▶ Max patch (JSON) ─┬─▶ the page's engine: what you hear and see
   ▲                                      ├─▶ .amxd: the Max for Live device
   └── a dial turned on the page ◀────────┴─▶ patchc.py ─▶ C++ ─▶ VST3, CLAP, Audio Unit
```

One Max patch is the source of everything. The page plays it, the `.amxd` is that patch
saved for Live, and the plugin is that patch compiled.

- **The generator is Python, and runs in the browser.** `src/plugin/fxgen.py` writes a
  plain [MaxPyLang](https://github.com/Barnard-PL-Labs/MaxPyLang) script for the current
  settings and runs it under Pyodide. Settings > "View the Python that built it" shows
  the script that ran, with MaxPyLang's checks of the patch above it.
- **An effect** takes the track's audio from `plugin~`, runs it through the modules that
  are switched on, and ends in an output gain and `plugout~`.
- **An instrument** has a synth where `plugin~` was: `notein`, a `poly` that gives each
  note a voice, and one to eight voices side by side (two oscillators, a low-pass filter
  its envelope opens, an `adsr~`), followed by the same effect chain.
- **The page's engine plays the patch as a host would.** It builds the patch out of Web
  Audio nodes (`src/engine`, `src/objects`) and stands in for the Live track
  (`src/runtime/host.ts`): a sound or your notes go in, and you hear what comes out.
- **The plugin is the same patch as C++.** `src/plugin/patchc.py` compiles the patch into
  one readable class, on small kernels in `native/mxrt.h`, and one hand-written CLAP
  wrapper turns that into every plugin format.
- **The sound is checked.** `scripts/maxcheck/` renders one test signal through the same
  patches in Max, in the page's engine and in the C++, and compares them. Its
  [README](scripts/maxcheck/README.md) has the method, the results and what still differs.

## Where things are

| Path | What it is |
|---|---|
| `index.html`, `src/plugin/main.ts`, `plugin.css` | The page |
| `src/plugin/schema.json` | The modules and every dial's range, read by the generator and the page |
| `src/plugin/presets.json`, `instruments.json` | The preset libraries |
| `src/plugin/fxgen.py` | The generator: settings to a Max patch |
| `src/plugin/patchc.py` | The compiler: a Max patch to C++ |
| `src/plugin/graphs.js`, `graph-view.ts`, `graph-max.js` | The pictures above the dials: what is drawn and what a drag does (one file, used by the page, the plugin's window and the Live device), and how the page and Max each draw it |
| `src/plugin/keys.ts` | Playing an instrument: the keyboard, MIDI, phrases |
| `src/plugin/sounds.ts` | The sound library; most of it is computed in the browser |
| `src/plugin/theme.ts` | Light and dark; the colours are variables at the top of `plugin.css` |
| `src/engine`, `src/objects`, `src/runtime` | The Web Audio engine that plays a patch |
| `native/` | The C++ kernels, the plugin wrapper, the offline renderer, the CMake build |
| `scripts/plugin.py` | `npm run plugin` |
| `scripts/maxcheck/` | Comparing Max, the page and the C++ |
| `tests/`, `test/` | Python tests (pytest) and browser tests (vitest) |

## Tests and deploys

- `npm test` covers the page's model, the engine objects made for it, and the sound
  library. `pytest tests` covers the generator (every preset builds, passes MaxPyLang's
  checks and reloads as a device) and the compiled C++ (every preset is rendered and
  held to the page's engine; instruments are played and checked for pitch, envelope and
  voice handling).
- `node scripts/sitecheck.mjs --browser all` uses the built site the way a visitor does,
  in Chromium, Firefox and WebKit: every preset builds, it plays, each download is the
  file it claims to be, Edit does what its buttons say, a shared link reopens the same
  device, and nothing logs an error. It makes no sound: the browsers are muted and the
  page's output is turned to zero. It needs `npm run build` and `npx playwright install`,
  and is run by hand before a release, not by the workflows.
- `node scripts/facecheck.mjs --python <one with maxpylang>` opens a few presets on the
  page and in the plugin window's own page (`native/ui/face.html`) side by side and
  measures them: the same cards, labels, dials and pictures in the same places, and
  pictures that are the same drawing. The one thing the page has that the window does
  not is the row of type buttons on a picture (a filter's four, a reverb's three): a
  type is part of the device, chosen on the page before it is downloaded. It says
  nothing about the Max for Live device, which Live draws with its own dials.
- `.github/workflows/native.yml` runs the Python tests and builds every preset as a
  plugin on Linux, macOS and Windows. Each build loads its own plugin and compares it
  with the C++ it came from; three presets also go through clap-validator, pluginval and,
  on macOS, auval.
- `.github/workflows/pages.yml` runs on every push to `main`: it builds the site, calls
  the workflow above, and publishes the site with the three installers as its downloads.
  That takes about 18 minutes, most of it the macOS builds. GitHub Pages has to use
  "GitHub Actions" as its source, because the page is TypeScript and cannot be served
  from the repository as it is.

## Not yet checked

- Nothing has been loaded in Ableton Live or another host by hand. The `.amxd` files
  pass MaxPyLang's checks and the plugins pass the validators above.
- The effects were measured against Max and match it closely
  ([details](scripts/maxcheck/README.md)). The instrument's objects (`adsr~`, `saw~`,
  `rect~`, `tri~`, `poly`) were not: they are right in kind, not confirmed sample for
  sample.
- What is sounding is drawn on the pictures on the page only. The plugin's window and
  the Max for Live device show the pictures and follow the dials, without the spectrum
  or the moving dots, and without the type buttons (changing a module's kind builds a
  different device, which only the page can do). The dot on a tremolo's wave moves at
  the wave's speed but is not locked to where the real sweep is in its cycle.
- The pictures in a Max for Live device have not been seen in Live. There they are a
  `v8ui` object carrying its own script, which is the documented way to do it and needs
  Max 9 (Live 12.2 or newer). The script is the one the page and the plugin's window
  run, and it is tested against a stand-in for Max's drawing calls
  (`test/graphs.test.ts`), which is not the same as Max drawing it. What an older Live
  makes of the object is not known either: "Dials only" leaves it out.
- The parametric, graphic and tilt EQs are drawn from their filters as they run at
  44.1 kHz, and the render agrees with the drawing to 0.15 dB (`tests/test_eq.py`). What
  they are not: a bell's cut is narrower than the same boost, a bell near 5 kHz sits a
  little under its dial (svf~'s tuning), and a high shelf stops about a dB short at the
  very top. All of that is in the picture. None of the EQs was measured against Max.
- The EQ was not measured against Max. Its dials are held to what they say
  (`tests/test_eq.py`: +12 dB on a band is +12 dB there, a cut mirrors a boost, 0 dB
  everywhere passes the sound untouched), and the browser and the C++ agree on it to
  the last bit of a 16-bit file.
- Describe it has not made a real request to Anthropic here, for want of a key. The
  request is built the way the SDK documents, and the answer's path (the same one a
  pasted answer takes) is tested.
- The plugin's own window exists on macOS only. On Windows and Linux the host shows its
  own sliders.
- The page plays an instrument with the browser's own oscillators, so the page and the
  plugin agree in pitch, level and envelope, not waveform for waveform.

## Built on MaxPyLang

The project began inside the [MaxPyLang](https://github.com/Barnard-PL-Labs/MaxPyLang)
repository and moved here to be its own. Two parts still come from there:

- **The engine.** `src/` outside `src/plugin/` is a copy of MaxPyLang's web engine as of
  November 2026, with only the files this page needs. Three things have since changed
  here and should go back upstream: a `poly` object was added, `notein` sends its outlets
  right to left, and `adsr~` scales with the value that triggers it, all as in Max. The
  `live.dial` widget was also redrawn.
- **The wheel.** `public/py/` holds the maxpylang build the page loads into Pyodide. To
  update it, run `python -m pip wheel . --no-deps -w <this folder>/public/py` in a
  MaxPyLang checkout, and change `WHEEL_PATH` in `src/plugin/python.ts` if the version
  changed.

Where the generated script works around something MaxPyLang cannot say yet, it says
`gap M4L-NN`; those entries are in MaxPyLang's
[gap log](https://github.com/Barnard-PL-Labs/MaxPyLang/blob/main/docs/type-system/m4l-gaps.md).

## Credits and licence

The four recordings in the sound library are CC0, from Freesound
([credits](public/samples/fx/CREDITS.md)); the other sounds are computed. The page sets
its text in DM Sans and DM Mono, loaded from Google Fonts. The plugin builds use CLAP,
clap-wrapper and the VST3 SDK (MIT) and, for the Audio Unit, Apple's AudioUnitSDK
(Apache 2.0); none of their code is in this repository.

MIT licensed.
