"""
Build a native plugin from a preset or a settings file.

    npm run plugin -- --preset "Tape Lo-Fi"            # an effect preset, by name or id
    npm run plugin -- --preset "Soft Pad"              # ...or an instrument preset
    npm run plugin -- --settings my-effect.json        # the page's settings, saved to a file
    npm run plugin -- --preset tape --install          # ...and copy it where hosts look
    npm run plugin -- --list                           # the presets
    npm run plugin -- --all --package downloads        # every preset, zipped per system

What it does, in order: fxgen.py builds the Max patch for the settings (the same patch
the page plays and the .amxd holds), patchc.py compiles that patch to C++ (Effect.h),
and CMake builds Effect.h into a .clap, a .vst3 and, on macOS, an Audio Unit.

Everything lands in build/plugins/<name>/: Effect.h and patch.json to read, the plugins
next to them. (build/plugins/_build and _sdk are CMake's, shared by every plugin built.) Needs Python with maxpylang (the wheel in public/py), CMake 3.21 or newer
and a C++17 compiler. The first build downloads the plugin SDKs (CLAP, clap-wrapper,
VST3), which takes a minute.

Other switches: --source-only stops after writing Effect.h; --render IN.wav OUT.wav plays
a file through the effect without building a plugin; --formats picks which to build;
--validate runs clap-validator, pluginval and auval on the result, if they are installed.

The functions here are also what tests/test_patchc.py and scripts/maxcheck/native.py use.
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "native"
sys.path.insert(0, str(ROOT / "src" / "plugin"))

import fxgen  # noqa: E402  (maxpylang itself comes from pip)
import patchc  # noqa: E402

PRESETS = json.loads((ROOT / "src" / "plugin" / "presets.json").read_text(encoding="utf-8"))
INSTRUMENTS = json.loads((ROOT / "src" / "plugin" / "instruments.json").read_text(encoding="utf-8"))
FORMATS = {"clap": "CLAP", "vst3": "VST3", "au": "AUV2"}


class BuildError(Exception):
    pass


# ── settings in ─────────────────────────────────────────────────────────────────

def find_preset(query):
    """A preset by id or name; failing that, the one preset whose name contains the words."""
    q = query.strip().lower()
    everything = PRESETS + INSTRUMENTS
    for p in everything:
        if q in (p["id"].lower(), p["name"].lower()):
            return p
    close = [p for p in everything if q in p["name"].lower() or q in p["id"].lower()]
    if len(close) == 1:
        return close[0]
    if not close:
        raise BuildError("no preset is called %r. `--list` shows them." % query)
    raise BuildError("%r could be any of: %s" % (query, ", ".join(p["name"] for p in close)))


def read_settings(path):
    """A settings file: the settings themselves, or a whole preset entry holding them."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("settings"), dict):
        data = data["settings"]
    if not isinstance(data, dict) or "chain" not in data:
        raise BuildError("%s does not look like settings: it has no \"chain\"" % path)
    return data


# ── settings -> patch -> C++ ────────────────────────────────────────────────────

def generate(settings, out_dir):
    """Write Effect.h and patch.json for these settings into out_dir. Returns what it made."""
    result = fxgen.run(settings, want_amxd=False)
    name = result["settings"]["name"]
    source = patchc.compile_patch(result["json"], name)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "Effect.h").write_text(source, encoding="utf-8")
    (out_dir / "patch.json").write_text(json.dumps(result["json"], indent=1), encoding="utf-8")
    return {"name": name, "slug": patchc.slug(name), "dir": out_dir, "effect": out_dir / "Effect.h",
            "kind": result["settings"].get("kind", "effect")}


# ── the offline renderer ────────────────────────────────────────────────────────

def compiler():
    for name in (os.environ.get("CXX"), "c++", "g++", "clang++"):
        if name and shutil.which(name):
            return name
    return None


def build_renderer(effect_dir, output=None):
    """Compile native/render/render.cpp against the Effect.h in effect_dir. One command, no CMake."""
    cxx = compiler()
    if cxx is None:
        raise BuildError("no C++ compiler found (tried $CXX, c++, g++, clang++)")
    output = Path(output or Path(effect_dir) / ("render.exe" if os.name == "nt" else "render"))
    cmd = [cxx, "-std=c++17", "-O2", "-I", str(NATIVE), "-I", str(effect_dir),
           str(NATIVE / "render" / "render.cpp"), "-o", str(output)]
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        raise BuildError("the generated C++ did not compile:\n%s" % done.stderr)
    return output


def play(renderer, notes, seconds, wav_out, as_float=False, block=64, values=None, rate=44100):
    """Render an instrument. `notes` is "KEY@START+LENGTH:VELOCITY,...", times in seconds."""
    cmd = [str(renderer), "--notes", notes, "--seconds", str(seconds), str(wav_out),
           "--block", str(block), "--rate", str(rate)]
    if as_float:
        cmd.append("--float")
    for index, value in (values or {}).items():
        cmd += ["--set", "%d=%r" % (index, float(value))]
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        raise BuildError("render failed: %s" % done.stderr.strip())


def render(renderer, wav_in, wav_out, as_float=False, block=64, values=None, as_chrome=False):
    cmd = [str(renderer), str(wav_in), str(wav_out), "--block", str(block)]
    if as_chrome:
        cmd.append("--as-chrome")
    if as_float:
        cmd.append("--float")
    for index, value in (values or {}).items():
        cmd += ["--set", "%d=%r" % (index, float(value))]
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        raise BuildError("render failed: %s" % done.stderr.strip())


# ── the plugin ──────────────────────────────────────────────────────────────────

def au_subtype(slug):
    """Four characters that tell one effect's Audio Unit from another's."""
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    h = patchc.fnv1a(slug)
    code = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[h % 26]
    h //= 26
    for _ in range(3):
        code += letters[h % len(letters)]
        h //= len(letters)
    return code


def au_type(made):
    """An Audio Unit says what it is: an effect (aufx) or a music device, played by notes (aumu)."""
    return "aumu" if made["kind"] == "instrument" else "aufx"


def default_formats():
    return ["clap", "vst3"] + (["au"] if platform.system() == "Darwin" else [])


def build_plugin(made, formats=None, log=print):
    """Configure and build with CMake. Returns the plugin files, by format."""
    if shutil.which("cmake") is None:
        raise BuildError("cmake is not installed (https://cmake.org/download/)")
    formats = formats or default_formats()
    if "au" in formats and platform.system() != "Darwin":
        raise BuildError("an Audio Unit can only be built on macOS")
    wanted = ["CLAP"] + [FORMATS[f] for f in formats if f != "clap"]
    # One build folder for every effect made here: the SDKs and the wrappers are compiled
    # once, and each further plugin only compiles its own Effect.h and links.
    build = made["dir"].parent / "_build"
    products = build / "products"
    configure = [
        "cmake", "-S", str(NATIVE), "-B", str(build),
        "-DCMAKE_BUILD_TYPE=Release",
        "-DFX_EFFECT_DIR=%s" % made["dir"],
        "-DFX_NAME=%s" % made["name"],
        "-DFX_SLUG=%s" % made["slug"],
        "-DFX_FORMATS=%s" % ";".join(wanted),
        "-DFX_AU_SUBTYPE=%s" % au_subtype(made["slug"]),
        "-DFX_KIND=%s" % made["kind"],
        "-DFETCHCONTENT_BASE_DIR=%s" % (made["dir"].parent / "_sdk"),
    ]
    # Ninja where it is the obvious choice; on Windows CMake's own pick (Visual Studio)
    # is the one that finds the right compiler.
    if os.name != "nt" and shutil.which("ninja") and not (build / "CMakeCache.txt").exists():
        configure += ["-G", "Ninja"]
    compile_ = ["cmake", "--build", str(build), "--target", "fx_all", "--config", "Release", "--parallel"]
    for step, cmd in (("configure", configure), ("build", compile_)):
        log("  cmake %s ..." % step)
        done = subprocess.run(cmd, capture_output=True, text=True)
        if done.returncode != 0:
            raise BuildError("cmake %s failed:\n%s\n%s" % (step, done.stdout[-4000:], done.stderr[-4000:]))

    # The plugin just built, loaded as a host would and compared with Effect.h.
    log("  checking the plugin against Effect.h ...")
    done = subprocess.run(["ctest", "--test-dir", str(build), "-C", "Release", "--output-on-failure"],
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise BuildError("the built plugin failed its check:\n%s\n%s" % (done.stdout[-4000:], done.stderr[-4000:]))

    found = {}
    for fmt, ext in (("clap", ".clap"), ("vst3", ".vst3"), ("au", ".component")):
        if fmt not in formats:
            continue
        hits = [p for p in products.rglob("*" + ext) if p.name == made["name"] + ext]
        if not hits:
            raise BuildError("the build finished but there is no %s%s under %s" % (made["name"], ext, products))
        target = made["dir"] / hits[0].name
        _copy(hits[0], target)
        found[fmt] = target
    return found


def _copy(src, dst):
    if dst.is_dir():
        shutil.rmtree(dst)
    elif dst.exists():
        dst.unlink()
    if src.is_dir():
        shutil.copytree(src, dst, symlinks=True)
    else:
        shutil.copy2(src, dst)


def install_dirs():
    system = platform.system()
    home = Path.home()
    if system == "Darwin":
        base = home / "Library" / "Audio" / "Plug-Ins"
        return {"clap": base / "CLAP", "vst3": base / "VST3", "au": base / "Components"}
    if system == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local")) / "Programs" / "Common"
        return {"clap": base / "CLAP", "vst3": base / "VST3"}
    return {"clap": home / ".clap", "vst3": home / ".vst3"}


def install(found, log=print):
    dirs = install_dirs()
    for fmt, path in found.items():
        dirs[fmt].mkdir(parents=True, exist_ok=True)
        _copy(path, dirs[fmt] / path.name)
        log("  installed %s" % (dirs[fmt] / path.name))


# ── packaging ───────────────────────────────────────────────────────────────────

SYSTEM = {"Darwin": "macos", "Windows": "windows"}.get(platform.system(), "linux")

README = """{name}
{rule}
Built by Plugin Playground from a Max patch. {what}

Copy the file for your host into the folder next to its name:

{folders}

Then rescan plugins in your host.

These builds are not signed. On macOS, a plugin downloaded from the web is quarantined
and the host will not load it until you clear that, in Terminal:

    xattr -dr com.apple.quarantine "<the plugin you copied>"
"""


def package(made, found, folder):
    """Zip what was built, with a note on where it goes. Returns the zip's path."""
    import zipfile

    folder.mkdir(parents=True, exist_ok=True)
    target = folder / ("%s-%s.zip" % (made["slug"], SYSTEM))
    names = {"clap": "CLAP", "vst3": "VST3", "au": "Audio Unit"}
    folders = "\n".join("    %-28s %s  (%s)" % (path.name, install_dirs()[fmt], names[fmt]) for fmt, path in found.items())
    what = "It is an instrument: play it with MIDI." if made["kind"] == "instrument" else "It is an audio effect."
    text = README.format(name=made["name"], rule="=" * len(made["name"]), what=what, folders=folders)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", text)
        for path in found.values():
            if path.is_dir():   # a macOS bundle: keep the folder and the run bit on what is inside
                for file in sorted(p for p in path.rglob("*") if p.is_file()):
                    z.write(file, str(Path(path.name) / file.relative_to(path)))
            else:
                z.write(path, path.name)
    return target


def write_index(folder):
    """
    index.json for a folder of packaged presets: preset id -> system -> the zip's path, as
    the page will fetch it ("downloads/soft-pad-macos.zip"). Returns how many presets it lists.
    """
    index = {}
    for p in PRESETS + INSTRUMENTS:
        for system in ("macos", "windows", "linux"):
            name = "%s-%s.zip" % (patchc.slug(p["settings"].get("name", p["name"])), system)
            if (folder / name).exists():
                index.setdefault(p["id"], {})[system] = "%s/%s" % (folder.name, name)
    (folder / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    return len(index)


# ── host compliance ─────────────────────────────────────────────────────────────

def validate(made, found, installed, log=print):
    """
    Run whichever plugin validators are on this machine against what was built:
    clap-validator on the .clap, pluginval on the .vst3 and the Audio Unit, and Apple's
    auval on the Audio Unit (which has to be installed first). Returns False if one failed.
    """
    runs = []
    if "au" in found and installed:
        # A component copied in a moment ago is not always known to the system yet.
        subprocess.run(["killall", "-9", "AudioComponentRegistrar"], capture_output=True)
    tool = shutil.which("clap-validator")
    if "clap" in found:
        runs.append(("clap-validator", tool and [tool, "validate", str(found["clap"])]))
    tool = shutil.which("pluginval")
    for fmt in ("vst3", "au"):
        if fmt in found and (fmt == "vst3" or installed):
            target = found[fmt] if fmt == "vst3" else install_dirs()["au"] / found[fmt].name
            runs.append(("pluginval (%s)" % fmt,
                         tool and [tool, "--strictness-level", "5", "--skip-gui-tests", "--validate", str(target)]))
    if "au" in found:
        if installed:
            runs.append(("auval", shutil.which("auval") and ["auval", "-v", au_type(made), au_subtype(made["slug"]), "PlPg"]))
        else:
            log("  auval: skipped, the Audio Unit has to be installed first (add --install)")

    ok = True
    for name, cmd in runs:
        if not cmd:
            log("  %s: not installed, skipped" % name)
            continue
        done = subprocess.run(cmd, capture_output=True, text=True)
        if done.returncode == 0:
            # the validator's own verdict: its last line that says anything
            last = [l for l in done.stdout.splitlines() if any(c.isalnum() for c in l)][-1:] or [""]
            log("  %s: passed  %s" % (name, last[0].strip()))
        else:
            ok = False
            log("  %s: FAILED\n%s\n%s" % (name, done.stdout[-6000:], done.stderr[-2000:]))
    return ok


# ── command line ────────────────────────────────────────────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(description="Build a native plugin (CLAP, VST3, Audio Unit) from a preset or settings.")
    ap.add_argument("--preset", help="a preset's name or id (part of the name is enough if only one matches)")
    ap.add_argument("--settings", help="a JSON file of settings, as the page keeps them")
    ap.add_argument("--list", action="store_true", help="list the presets and stop")
    ap.add_argument("--all", action="store_true", help="build every preset, effects and instruments, one after another")
    ap.add_argument("--index", metavar="DIR",
                    help="write DIR/index.json, listing the --package zips in DIR by preset and system, and stop")
    ap.add_argument("--out", default=str(ROOT / "build" / "plugins"), help="where to build (default: build/plugins)")
    ap.add_argument("--formats", default=None, help="comma-separated: clap, vst3, au (default: all this system can build)")
    ap.add_argument("--install", action="store_true", help="copy the plugins into your user plugin folders")
    ap.add_argument("--validate", action="store_true",
                    help="run clap-validator, pluginval and (on macOS, with --install) auval, whichever are installed")
    ap.add_argument("--source-only", action="store_true", help="write Effect.h and stop")
    ap.add_argument("--play", nargs=3, metavar=("NOTES", "SECONDS", "OUT.wav"),
                    help='render an instrument playing notes, e.g. "60@0+1:100,67@0.5+1:80" (key@start+length:velocity)')
    ap.add_argument("--package", metavar="DIR",
                    help="also zip the plugins into DIR, one file per system, ready to hand to someone")
    ap.add_argument("--render", nargs=2, metavar=("IN.wav", "OUT.wav"),
                    help="play a WAV through the effect instead of building a plugin")
    args = ap.parse_args(argv)

    if args.list:
        for title, presets in (("Effects", PRESETS), ("Instruments", INSTRUMENTS)):
            print(title)
            for p in presets:
                print("  %-18s %-20s %s" % (p["id"], p["name"], p["tag"]))
        return 0

    if args.index:
        print("listed %d presets in %s" % (write_index(Path(args.index)), Path(args.index) / "index.json"))
        return 0
    if args.all:
        # Each preset is its own run of this command; the first failure stops the rest.
        rest = [a for a in (sys.argv[1:] if argv is None else argv) if a != "--all"]
        for p in PRESETS + INSTRUMENTS:
            code = main(rest + ["--preset", p["id"]])
            if code != 0:
                return code
        return 0

    try:
        if bool(args.preset) == bool(args.settings):
            raise BuildError("give one of --preset or --settings (or --list)")
        settings = find_preset(args.preset)["settings"] if args.preset else read_settings(args.settings)
        formats = default_formats()
        if args.formats:
            formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
            bad = [f for f in formats if f not in FORMATS]
            if bad:
                raise BuildError("unknown format %s; the choices are clap, vst3, au" % ", ".join(bad))

        name = fxgen.normalize(settings)["name"]
        made = generate(settings, Path(args.out) / patchc.slug(name))
        print("%s" % made["name"])
        print("  wrote %s" % made["effect"])
        if args.render and made["kind"] == "instrument":
            raise BuildError("%s is an instrument: there is no audio to play through it. Use --play." % made["name"])
        if args.play:
            if made["kind"] != "instrument":
                raise BuildError("%s is an effect: play a file through it with --render." % made["name"])
            play(build_renderer(made["dir"]), args.play[0], float(args.play[1]), args.play[2])
            print("  rendered %s" % args.play[2])
            return 0
        if args.render:
            renderer = build_renderer(made["dir"])
            render(renderer, args.render[0], args.render[1])
            print("  rendered %s" % args.render[1])
            return 0
        if args.source_only:
            return 0
        found = build_plugin(made, formats)
        for path in found.values():
            print("  built %s" % path)
        if "au" in found:
            code = "%s %s PlPg" % (au_type(made), au_subtype(made["slug"]))
            print("  the Audio Unit is `%s` (auval -v %s, once installed)" % (code, code))
        if args.install:
            install(found)
        if args.package:
            print("  packed %s" % package(made, found, Path(args.package)))
        if args.validate and not validate(made, found, args.install):
            return 1
        return 0
    except (BuildError, patchc.CompileError) as err:
        print("plugin: %s" % err, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
