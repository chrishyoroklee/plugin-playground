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
and each further plugin takes a few seconds.

## The runtime: any device, nothing compiled

Compiling is something a web page cannot do, so the site does not hand out compiled
devices. It hands out one plugin that runs any device, and a file per device:

```
settings ─▶ fxgen.py ─▶ patch JSON ─▶ patchc.py ─┬─▶ Effect.h ─▶ a compiler ─▶ one device's plugin
                                                 └─▶ a .ppdevice ─▶ the runtime plugin, as that device
```

- **The device file** (`.ppdevice`) is patchc's other output: the statements it would
  have written as C++, as instructions, with the device's name, its dials and the page's
  settings. It is text, about 5 KB. The page writes it in the browser, where patchc runs
  under Pyodide next to fxgen.
- **The runtime** (`native/runtime/`) is `mxvm.h`, which runs those instructions over the
  same kernels in `mxrt.h`, and an `Effect.h` with the same shape as a generated one. The
  shell, the renderer and hostcheck are built against it unchanged. A runtime plugin
  finds the device file beside it when it loads and is that device from then on.
- **The helper** (`native/helper/`) opens a device file: it copies the runtime under the
  device's name, puts the file inside, and on macOS writes the Info.plist an Audio Unit
  is listed by. On macOS it is `Plugin Playground.app` with the runtime inside it, on
  Windows `Plugin Playground.exe`, and everywhere `plugin-playground-install` for a
  terminal. The installer sets it to open `.ppdevice` files.
- **The download** is the installer with the device inside it. The page cannot compile,
  but it can put a device into an installer CI has built (`src/plugin/stamp.ts`): next to
  the install script in a `.pkg` (whose table of contents it writes again), after the end
  of the setup `.exe` with a footer giving its length, or as one more file in the
  `.tar.gz`. Each installer, finding a device on itself, has the helper make it into
  plugins once Plugin Playground is in. So a visitor downloads one file, and a device
  never meets an older runtime than the one it came with.

```sh
npm run plugin -- --runtime                                # build the runtime and the helper
npm run plugin -- --runtime --preset "Soft Pad" --install  # ...and make a device from them
npm run plugin -- --runtime --all --package downloads      # every preset checked; the installer
```

| System  | Installer                             | What it installs | Where a device's plugins go |
|---------|---------------------------------------|------------------|-----------------------------|
| macOS   | `plugin-playground-macos.pkg`         | `Plugin Playground.app` in Applications | `~/Library/Audio/Plug-Ins` (Audio Unit, VST3, CLAP); a device inside the installer goes beside the app, in `/Library` when installed for every user |
| Windows | `plugin-playground-windows-setup.exe` | the helper and runtime in Program Files | `Common Files\VST3\Plugin Playground` and the same under `CLAP`, which the installer makes writable by every user |
| Linux   | `plugin-playground-linux.tar.gz`      | `install.sh` copies it to `~/.local/share/plugin-playground` | `~/.vst3` and `~/.clap` |

Opening a device needs no administrator and no tools, and on macOS the plugins it makes
carry no quarantine flag, because a program on the machine wrote them: that flag is what
makes a host refuse a plugin unzipped from a download.

A device file names the oldest runtime that can run it (`PROGRAM_RUNTIME` in patchc.py,
`kRuntimeVersion` in mxvm.h). When an object is added that an installed runtime would
not know, raise both: the helper then tells someone with the old one to install the new
one, and does not make half a plugin.

**Not signed.** The installers are not signed with a paid developer certificate, so
each system asks the visitor to allow them: on macOS under System Settings, Privacy &
Security, Open Anyway; on Windows through SmartScreen's More info, Run anyway. An
installer with a device put in by the page could not keep a signature anyway (changing
it breaks one), so that is asked for every plugin downloaded. With an
Apple Developer ID, set `FX_CODESIGN_IDENTITY` ("Developer ID Application: ...") and
`FX_INSTALLER_IDENTITY` ("Developer ID Installer: ...") when packaging and both are used;
the package then still has to be notarized (`xcrun notarytool submit`, `xcrun stapler
staple`), which nothing here does yet. A plugin the helper makes is signed ad hoc on the
machine, which a Developer ID does not change.

`--check-stamped DIR` does what the page's Download does to DIR's installer
(`scripts/stamp.mjs` runs the page's code under Node 22.18 or newer), runs the result
with no window, and requires the plugin to be there and to pass hostcheck and auval. CI
runs it on all three systems, and on macOS for both ways Installer offers.

`--package DIR` without `--runtime` still wraps one compiled device in an installer of
its own, and `--check-installer DIR` runs it.

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
Unit from the same code. On macOS the plugin has a window of its own (below); on Windows
and Linux there is none yet, and the host shows its own controls.

A dial with a curve (every frequency and time) is given to the host as the knob's
position, 0 to 1, so the host's slider feels like the dial in Live. The text beside it
is still in Hz or ms, and saved state holds real values.

## The plugin's window

A device looks in a DAW as it does on the page and in Live. patchc writes down the
device's **face** next to its program: every box the patch puts in presentation view
(the cards, the labels, each dial with its range and unit), where the patch puts it. A
device file carries it on a `face` line and a generated Effect.h as `kFace`.

The face includes the device's colours: its theme's backdrop and cards, and each dial's
lettering and ring, which fxgen writes on the boxes. `ui/face.html` draws a face. It is one page with no dependencies, built into the plugin
as bytes with its two font files (DM Mono, OFL), and its dial is the page's own
(`src/objects/ui/live-dial.ts`): the same geometry, the same drag, wheel and
double-click. `shell/gui_mac.mm` shows it in a WKWebView and passes four kinds of message
between the page and the plugin. A dial turned in the window reaches the host as one
gesture (grabbed, moved, let go), which is what a host records as automation, and a dial
the host moves is redrawn in the window.

Every device's plugin is a copy of one binary, so nothing in the window code may be a
named Objective-C class: a host that loaded two devices would keep one copy of the class
for both. The one object it needs gets a class made at load time.

`test/guicheck.mm` opens the window as a host does, saves a picture of it, has the host
move a dial and requires the window to show it, then turns a dial in the window and
requires the host to be told. Every `npm run plugin -- --runtime` device goes through it
on macOS.

## Motion

A dial that something moves (an LFO, the follower, a user's knob) is worked out in the
patch, under the dial, by `Device.moved()` in fxgen.py: the dial's position as a signal,
plus each source times its amount, clipped to 0..1, read off every 5 ms by a
`snapshot~`, and put back through the dial's curve and range. So it is the same in Live,
in the browser and in a plugin, and nothing here knows the word "modulation".

`snapshot~` is the one object that goes from the signal side of a patch back to the
control side. patchc compiles it as a countdown in the sample loop that runs, when it
reaches zero, the statements a number leaving the box would run. In a device file that
is the instruction `every`, with a section of its own. It is what runtime 2 added; a
device that uses none of it still asks for runtime 1. Runtime 3 added the function
`atodb` and the kernel `Slide` (`atodb~` and `slide~`, for the compressor). Runtime 4
added presets (below).

## Presets

A plugin on the page (a Distortion, a Reverb, the Synth) has presets inside it, and its
download carries them: the page's Download writes the plugin's file with
`patchc.plugin_device()`, and the plugin's window has a menu of them along its top, with
arrows, that says "edited" once a dial has moved away from where the preset put it.

Most presets of a plugin differ only in where the dials stand. Some differ in a type
(Tube or Fuzz, Room or Hall), and a type is code, not a dial. So the file holds a
program for each set of types its presets use, one after the other, and after them a
line per preset naming its program and every dial's value (`mxvm::Book`). The plugin's
dials are every program's together, by id, so the host sees one list whichever runs; a
Synth preset with no delay leaves the delay's dials doing nothing.

Choosing a preset sets the dials and tells the host to read them again. If it needs
another program, the shell makes an Effect for it on the main thread, ready to play, and
the audio thread takes it at the top of its next block; nothing is made or freed on the
audio thread. The program and the preset are saved with the dials, so a session reopens
on the same one. A host can load one too, through CLAP's preset-load extension (the
preset's number as the key); hostcheck loads every preset that way and holds each one
that changes program to a fresh Effect of that program, sample for sample.

```sh
npm run plugin -- --runtime --plugins      # every plugin of the page, with its presets, made and checked
```

## Adding an object

One kernel in `mxrt.h` if the object has state, and one class in `patchc.py` (ten lines
or so, next to the others) saying what its inlets take and what C++ one sample of it is.
Then add it to a preset, or `tests/test_patchc.py` will say that patchc knows an object
no preset uses.

## How it is checked

- **The sound.** `scripts/maxcheck/native.py` renders the maxcheck signal through the
  compiled C++ for every test, next to the Max and browser renderings, and `compare.py`
  reads all three. Results are in [`scripts/maxcheck/README.md`](../scripts/maxcheck/README.md).
- **The runtime.** `pytest tests/test_runtime.py` renders every preset through its
  compiled C++ and through the runtime given their device file, once as they are and
  once with every dial moved, and requires the same samples to within rounding (1e-9;
  with clang they are identical, with gcc a reverb tail differed by 7e-15). A device
  file the runtime cannot run (a newer format, an unknown object, a cut-off file) has to
  be refused with the reason.
- **Every preset.** `pytest tests/test_patchc.py` compiles every effect preset and the 57
  module tests, renders each and holds its level to the browser engine's rendering of
  the same patch (`tests/reference/browser.json`), with no browser or Max needed.
- **Instruments.** The same file compiles every instrument preset and plays each one
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

Where that stands (November 2026): CI builds the runtime on Linux, macOS and Windows,
makes every preset from it with the helper (97 effects and 41 instruments) and each
passes hostcheck. It runs the installer, opens three devices with what was installed
(Ping-Pong, Tape Lo-Fi and the instrument Soft Pad) and puts them through
clap-validator, pluginval at strictness 5 (VST3 everywhere, the Audio Unit on macOS) and
auval. On a Mac, a chain no preset has, made on the page in a browser, downloaded and
opened in Finder's way, became a plugin that passes auval. Nobody has yet loaded one in
Logic or another host by hand, and the C++ has been compared with the browser engine,
not directly with Max recordings.

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
- A moved dial's value reaches its module every 5 ms, in steps. That is inaudible on a
  filter sweep or a level, but a fast LFO far up a delay time would be heard as steps.
- A dial moved by the host sets its numbers at once, as it does in Max: there is no
  smoothing beyond what the patch has (the delay time glides through its `line~`).
- The runtime costs more than compiled code: about 1 to 2 % of one core for the heaviest
  presets, against about 1 % compiled. Its sound is the same, to within rounding.
- The plugin's own window exists on macOS only. On Windows and Linux the host shows its
  generic controls, and so has no preset menu: the presets are there, but only a host
  that loads CLAP presets by their place in the plugin can reach them. They are not yet
  offered to hosts' own preset browsers (CLAP preset discovery, VST3 programs, Audio Unit
  factory presets). No RNBO export.
- The Synth's file holds a program for each of its 41 presets, because each uses its own
  waves and effects: about 700 KB, written in a few seconds on the page.

## Licences

CLAP, clap-wrapper and the VST3 SDK (3.8) are MIT licensed. Apple's AudioUnitSDK, which
clap-wrapper uses for the Audio Unit only, is Apache 2.0. They are downloaded at build
time and none of their code is in this repository.
