"""
Build a native plugin from a preset or a settings file.

    npm run plugin -- --preset "Tape Lo-Fi"            # an effect preset, by name or id
    npm run plugin -- --preset "Soft Pad"              # ...or an instrument preset
    npm run plugin -- --settings my-effect.json        # the page's settings, saved to a file
    npm run plugin -- --preset tape --install          # ...and copy it where hosts look
    npm run plugin -- --list                           # the presets
    npm run plugin -- --all --package downloads        # every preset, as this system's installer

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
VERSION = "0.1.0"


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
    # The same device as a program for the runtime plugin: what the page's Download gives.
    device = out_dir / (patchc.slug(name) + ".ppdevice")
    device.write_text(patchc.compile_program(result["json"], name, result["settings"]), encoding="utf-8")
    return {"name": name, "slug": patchc.slug(name), "dir": out_dir, "effect": out_dir / "Effect.h",
            "device": device, "kind": result["settings"].get("kind", "effect")}


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


RUNTIME = NATIVE / "runtime"


def build_runtime_renderer(output):
    """
    The renderer built on the runtime plugin's Effect.h. It is no device in particular:
    hand render() or play() a .ppdevice as `device` and it is that one.
    """
    cxx = compiler()
    if cxx is None:
        raise BuildError("no C++ compiler found (tried $CXX, c++, g++, clang++)")
    cmd = [cxx, "-std=c++17", "-O2", "-I", str(NATIVE), "-I", str(RUNTIME),
           str(NATIVE / "render" / "render.cpp"), "-o", str(output)]
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        raise BuildError("the runtime did not compile:\n%s" % done.stderr)
    return Path(output)


def _renderer(renderer, device):
    return [str(renderer)] + (["--device", str(device)] if device else [])


def play(renderer, notes, seconds, wav_out, as_float=False, block=64, values=None, rate=44100, device=None):
    """Render an instrument. `notes` is "KEY@START+LENGTH:VELOCITY,...", times in seconds."""
    cmd = _renderer(renderer, device) + ["--notes", notes, "--seconds", str(seconds), str(wav_out),
                                         "--block", str(block), "--rate", str(rate)]
    if as_float:
        cmd.append("--float")
    for index, value in (values or {}).items():
        cmd += ["--set", "%d=%r" % (index, float(value))]
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        raise BuildError("render failed: %s" % done.stderr.strip())


def render(renderer, wav_in, wav_out, as_float=False, block=64, values=None, as_chrome=False, device=None):
    cmd = _renderer(renderer, device) + [str(wav_in), str(wav_out), "--block", str(block)]
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


def _run(cmd, what, **kwargs):
    done = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kwargs)
    if done.returncode != 0:
        raise BuildError("%s failed:\n%s\n%s" % (what, done.stdout[-4000:], done.stderr[-4000:]))
    return done


# ── the runtime plugin ──────────────────────────────────────────────────────────
#
# One plugin, compiled once, that becomes whichever device is in the file beside it, and
# the helper that makes a named plugin of it for a device. This is what lets the page
# hand out any device at all: it writes the device file, and nothing is compiled.

RUNTIME_NAME = "Plugin Playground Runtime"
APP_NAME = "Plugin Playground"
HELPER = "plugin-playground-install" + (".exe" if os.name == "nt" else "")


def _find(folder, name):
    hits = sorted((p for p in Path(folder).rglob(name) if "CMakeFiles" not in p.parts), key=lambda p: len(p.parts))
    if not hits:
        raise BuildError("the build finished but there is no %s under %s" % (name, folder))
    return hits[0]


def build_runtime(out, log=print):
    """
    Build the runtime plugin in every format, and the helper. Returns the folder holding
    them, laid out as the helper expects (native/helper/install.h):

        plugin-playground-install        the helper, for a terminal
        hostcheck                        for check_runtime()
        Runtime/Plugin Playground Runtime.clap, .vst3
        Runtime/effect/, Runtime/instrument/    macOS: the Audio Unit, which says in its
                                                code whether it takes notes
    """
    if shutil.which("cmake") is None:
        raise BuildError("cmake is not installed (https://cmake.org/download/)")
    out = Path(out)
    home = out / "_runtime"
    if home.exists():
        shutil.rmtree(home)
    (home / "Runtime").mkdir(parents=True)
    mac = platform.system() == "Darwin"
    for kind in (("effect", "instrument") if mac else ("effect",)):
        # The instrument build is only there for its Audio Unit.
        formats = "CLAP;VST3;AUV2" if (mac and kind == "effect") else "CLAP;AUV2" if mac else "CLAP;VST3"
        build = out / ("_build_runtime_" + kind)
        configure = [
            "cmake", "-S", str(NATIVE), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release", "-DFX_RUNTIME=ON",
            "-DFX_NAME=%s" % RUNTIME_NAME, "-DFX_SLUG=runtime", "-DFX_FORMATS=%s" % formats,
            "-DFX_KIND=%s" % kind, "-DFETCHCONTENT_BASE_DIR=%s" % (out / "_sdk"),
        ]
        if os.name != "nt" and shutil.which("ninja") and not (build / "CMakeCache.txt").exists():
            configure += ["-G", "Ninja"]
        log("  cmake: the runtime%s ..." % (" (Audio Unit for instruments)" if kind == "instrument" else ""))
        _run(configure, "cmake configure")
        _run(["cmake", "--build", str(build), "--target", "fx_all", "fx_helper", "--config", "Release", "--parallel"],
             "cmake build")
        products = build / "products"
        if kind == "effect":
            for ext in (".clap", ".vst3"):
                _copy(_find(products, RUNTIME_NAME + ext), home / "Runtime" / (RUNTIME_NAME + ext))
            shutil.copy2(_find(build, HELPER), home / HELPER)
            if os.name == "nt":
                shutil.copy2(_find(build, APP_NAME + ".exe"), home / (APP_NAME + ".exe"))
            check = "hostcheck" + (".exe" if os.name == "nt" else "")
            shutil.copy2(_find(build, check), home / check)
            if mac:
                shutil.copy2(_find(build, "guicheck"), home / "guicheck")
        if mac:
            _copy(_find(products, RUNTIME_NAME + ".component"), home / "Runtime" / kind / (RUNTIME_NAME + ".component"))
            if kind == "effect":
                shutil.copy2(_find(build, "plugin-playground-app"), home / "plugin-playground-app")
    if mac:
        _mac_app(home)
    return home


DEVICE_TYPE = "com.plugin-playground.device"

APP_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleDevelopmentRegion</key><string>en</string>
	<key>CFBundleExecutable</key><string>{name}</string>
	<key>CFBundleIdentifier</key><string>com.plugin-playground.helper</string>
	<key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
	<key>CFBundleName</key><string>{name}</string>
	<key>CFBundleDisplayName</key><string>{name}</string>
	<key>CFBundlePackageType</key><string>APPL</string>
	<key>CFBundleShortVersionString</key><string>{version}</string>
	<key>CFBundleVersion</key><string>{version}</string>
	<key>LSMinimumSystemVersion</key><string>10.15</string>
	<key>NSHighResolutionCapable</key><true/>
	<key>NSPrincipalClass</key><string>NSApplication</string>
	<key>CFBundleDocumentTypes</key>
	<array>
		<dict>
			<key>CFBundleTypeName</key><string>Plugin Playground Device</string>
			<key>CFBundleTypeRole</key><string>Viewer</string>
			<key>LSHandlerRank</key><string>Owner</string>
			<key>LSItemContentTypes</key><array><string>{type}</string></array>
		</dict>
	</array>
	<key>UTExportedTypeDeclarations</key>
	<array>
		<dict>
			<key>UTTypeIdentifier</key><string>{type}</string>
			<key>UTTypeDescription</key><string>Plugin Playground Device</string>
			<key>UTTypeConformsTo</key><array><string>public.data</string></array>
			<key>UTTypeTagSpecification</key>
			<dict>
				<key>public.filename-extension</key><array><string>ppdevice</string></array>
			</dict>
		</dict>
	</array>
</dict>
</plist>
"""


def _mac_app(home):
    """
    Plugin Playground.app: the helper as macOS wants it, with the runtime inside it, so
    one thing is installed and it works from wherever it is put. Its Info.plist is what
    tells macOS to open .ppdevice files with it.
    """
    app = home / (APP_NAME + ".app")
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    shutil.copy2(home / "plugin-playground-app", contents / "MacOS" / APP_NAME)
    (contents / "Info.plist").write_text(APP_PLIST.format(name=APP_NAME, version=VERSION, type=DEVICE_TYPE), encoding="utf-8")
    (contents / "PkgInfo").write_text("APPL????")
    shutil.copytree(home / "Runtime", contents / "Resources" / "Runtime", symlinks=True)
    return app


def install_device(home, device, to=None):
    """
    What opening a .ppdevice does: the helper makes its plugins from the runtime in `home`.
    Into `to` (under CLAP, VST3, Components) or, with none given, your own plugin folders.
    Returns the plugins' paths.
    """
    cmd = [home / HELPER, device, "--runtime", home / "Runtime"] + (["--to", to] if to else [])
    done = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if done.returncode != 0:
        raise BuildError("the helper refused %s: %s" % (Path(device).name, (done.stderr or done.stdout).strip()))
    return [Path(line.strip()) for line in done.stdout.splitlines()[1:] if line.strip()]


def check_runtime(home, made, work, log=print, window=True):
    """
    The plugin the helper makes for a device, loaded as a host would and held to the
    runtime's own Effect given the same device: hostcheck, as for a compiled plugin.
    """
    plugins = install_device(home, made["device"], work)
    clap = next(p for p in plugins if p.suffix == ".clap")
    binary = clap / "Contents" / "MacOS" / RUNTIME_NAME if clap.is_dir() else clap
    check = home / ("hostcheck" + (".exe" if os.name == "nt" else ""))
    done = subprocess.run([str(check), str(binary), str(made["device"])], capture_output=True, text=True)
    if done.returncode != 0:
        raise BuildError("the runtime plugin for %s failed its check:\n%s\n%s" % (made["name"], done.stdout[-3000:], done.stderr[-3000:]))
    log("  %s" % done.stdout.strip())
    if window and platform.system() == "Darwin":
        # The same plugin's window, opened as a host opens it: it has to draw the device,
        # follow the host, and tell the host when a dial is turned in it.
        done = subprocess.run([str(home / "guicheck"), str(binary), str(work / "windows" / made["slug"])],
                              capture_output=True, text=True)
        if done.returncode != 0:
            raise BuildError("the window of the runtime plugin for %s failed its check:\n%s\n%s"
                             % (made["name"], done.stdout[-3000:], done.stderr[-3000:]))
        log("  %s" % done.stdout.strip())
    return plugins


# ── packaging ───────────────────────────────────────────────────────────────────
#
# What a visitor downloads: one installer per system, the kind any plugin comes with.
#
#   macOS    <slug>-macos.pkg          opens in Installer; Audio Unit, VST3 and CLAP
#   Windows  <slug>-windows-setup.exe  a setup program (Inno Setup); VST3 and CLAP
#   Linux    <slug>-linux.tar.gz       the plugins and an install.sh; Linux has no one
#                                      installer format that every distribution opens
#
# Each puts the plugins in the folders every host scans, so nothing is copied by hand.

SYSTEM = {"Darwin": "macos", "Windows": "windows"}.get(platform.system(), "linux")
PACKAGE_NAMES = {"macos": "%s-macos.pkg", "windows": "%s-windows-setup.exe", "linux": "%s-linux.tar.gz"}
FORMAT_NAMES = {"clap": "CLAP", "vst3": "VST3", "au": "Audio Unit"}
# Where an installer puts each format: the folders shared by every user of the machine.
MAC_FOLDERS = {"au": "Library/Audio/Plug-Ins/Components", "vst3": "Library/Audio/Plug-Ins/VST3",
               "clap": "Library/Audio/Plug-Ins/CLAP"}
WINDOWS_FOLDERS = {"vst3": r"{commoncf64}\VST3", "clap": r"{commoncf64}\CLAP"}


def _what(made):
    return "an instrument, played with MIDI" if made["kind"] == "instrument" else "an audio effect"


def package(made, found, folder):
    """Make this system's installer for what was built. Returns the installer's path."""
    import tempfile

    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (PACKAGE_NAMES[SYSTEM] % made["slug"])
    if target.exists():
        target.unlink()
    with tempfile.TemporaryDirectory() as work:
        {"macos": package_macos, "windows": package_windows, "linux": package_linux}[SYSTEM](made, found, target, Path(work))
    return target


def package_macos(made, found, target, work):
    """
    A .pkg that Installer opens. It can install for every user (/Library, asks for an
    administrator's password) or for this user alone (~/Library).

    Two things here are what make the plugin load in a host afterwards. Each bundle is
    sealed with a signature: ad hoc unless FX_CODESIGN_IDENTITY names a Developer ID. And
    because Installer writes the files, they do not carry the quarantine flag that a
    plugin unzipped from a download does, which is what makes hosts refuse it.

    The package itself is signed only if FX_INSTALLER_IDENTITY names a Developer ID
    Installer certificate. Unsigned, macOS asks the visitor to allow it once.
    """
    root = work / "root"
    root.mkdir()
    identity = os.environ.get("FX_CODESIGN_IDENTITY") or "-"
    for fmt, path in found.items():
        staged = root / MAC_FOLDERS[fmt] / path.name
        staged.parent.mkdir(parents=True, exist_ok=True)
        _copy(path, staged)
        sign = ["codesign", "--force", "--sign", identity]
        if identity != "-":
            sign += ["--options", "runtime", "--timestamp"]
        _run(sign + [staged], "codesign")


    name = _xml(made["name"])
    where = ("Instrument > AU Instruments > Plugin Playground" if made["kind"] == "instrument"
             else "Audio FX > Audio Units > Plugin Playground")
    welcome = ("<p><b>%s</b> is %s, made with Plugin Playground.</p>"
               "<p>This installs it as %s, so every host on this Mac can use it.</p>"
               "<p>When it has finished, restart your host. In Logic Pro and GarageBand it is under "
               "<b>%s</b>.</p>"
               % (name, _what(made), _listed(FORMAT_NAMES[f] for f in ("au", "vst3", "clap") if f in found), where))
    # An Audio Unit copied in a moment ago is not always known to the system yet.
    _mac_pkg(work, root, "com.plugin-playground.%s" % made["slug"], made["name"], welcome, target,
             "killall -9 AudioComponentRegistrar >/dev/null 2>&1")


def _mac_pkg(work, root, identifier, title, welcome, target, postinstall):
    """
    A .pkg of the files under `root`, laid out as they should land on the disk. It can
    install for every user or for this one alone. Signed only if FX_INSTALLER_IDENTITY
    names a Developer ID Installer certificate; unsigned, macOS asks to allow it once.
    """
    import plistlib

    scripts, resources, parts = work / "scripts", work / "resources", work / "parts"
    for d in (scripts, resources, parts):
        d.mkdir(exist_ok=True)
    post = scripts / "postinstall"
    post.write_text("#!/bin/sh\n%s\nexit 0\n" % postinstall)
    post.chmod(0o755)

    # Without this, Installer "relocates" a bundle: finding another copy of it anywhere on
    # the disk (a build folder, say), it updates that copy and leaves the right place empty.
    components = work / "components.plist"
    _run(["pkgbuild", "--analyze", "--root", root, components], "pkgbuild --analyze")
    listed = plistlib.loads(components.read_bytes())
    for bundle in listed:
        bundle["BundleIsRelocatable"] = False
    components.write_bytes(plistlib.dumps(listed))

    _run(["pkgbuild", "--root", root, "--component-plist", components, "--scripts", scripts,
          "--identifier", identifier, "--version", VERSION, "--install-location", "/",
          parts / "plugins.pkg"], "pkgbuild")

    (resources / "welcome.html").write_text(
        "<html><body style=\"font: 13px -apple-system, sans-serif\">%s</body></html>" % welcome, encoding="utf-8")
    distribution = work / "distribution.xml"
    distribution.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<installer-gui-script minSpecVersion="2">\n'
        '  <title>%s</title>\n'
        '  <welcome file="welcome.html" mime-type="text/html"/>\n'
        '  <domains enable_localSystem="true" enable_currentUserHome="true"/>\n'
        '  <options customize="never" require-scripts="false" hostArchitectures="arm64,x86_64"/>\n'
        '  <volume-check><allowed-os-versions><os-version min="10.15"/></allowed-os-versions></volume-check>\n'
        '  <choices-outline><line choice="plugins"/></choices-outline>\n'
        '  <choice id="plugins" visible="false"><pkg-ref id="%s"/></choice>\n'
        '  <pkg-ref id="%s" version="%s">plugins.pkg</pkg-ref>\n'
        '</installer-gui-script>\n' % (_xml(title), identifier, identifier, VERSION), encoding="utf-8")
    build = ["productbuild", "--distribution", distribution, "--package-path", parts, "--resources", resources]
    if os.environ.get("FX_INSTALLER_IDENTITY"):
        build += ["--sign", os.environ["FX_INSTALLER_IDENTITY"], "--timestamp"]
    _run(build + [target], "productbuild")


def _xml(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _listed(words):
    words = ["an " + w if w[0] in "AEIOU" else "a " + w for w in words]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def inno_setup():
    """Inno Setup's compiler, which builds the Windows installer."""
    tool = shutil.which("iscc") or shutil.which("ISCC")
    if tool:
        return tool
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        guess = Path(base or "") / "Inno Setup 6" / "ISCC.exe"
        if base and guess.exists():
            return str(guess)
    raise BuildError("Inno Setup is not installed (https://jrsoftware.org/isinfo.php), which builds the Windows installer")


def package_windows(made, found, target, work):
    """
    A setup program made with Inno Setup: VST3 and CLAP into Common Files, where every
    host looks, with an entry under "Installed apps" to remove them again. Not signed, so
    SmartScreen asks the visitor to allow it once.
    """
    q = lambda text: str(text).replace('"', "")  # noqa: E731  (a name is never quoted in a .iss)
    files = []
    for fmt, path in found.items():
        folder = WINDOWS_FOLDERS[fmt]
        if path.is_dir():   # a VST3 can be a folder with the plugin inside it
            files.append('Source: "%s\\*"; DestDir: "%s\\%s"; Flags: ignoreversion recursesubdirs createallsubdirs'
                         % (q(path), folder, q(path.name)))
        else:
            files.append('Source: "%s"; DestDir: "%s"; Flags: ignoreversion' % (q(path), folder))
    script = work / "setup.iss"
    script.write_text("\n".join([
        "[Setup]",
        "AppId=PluginPlayground.%s" % made["slug"],
        "AppName=%s" % q(made["name"]),
        "AppVersion=%s" % VERSION,
        "AppVerName=%s (Plugin Playground)" % q(made["name"]),
        "AppPublisher=Plugin Playground",
        "CreateAppDir=no",
        "UninstallFilesDir={commonpf64}\\Plugin Playground\\%s" % made["slug"],
        "PrivilegesRequired=admin",
        "DisableProgramGroupPage=yes",
        "DisableWelcomePage=no",
        "Compression=lzma2",
        "SolidCompression=yes",
        "WizardStyle=modern",
        "OutputDir=%s" % q(target.parent),
        "OutputBaseFilename=%s" % target.name[:-len(".exe")],
        "",
        "[Messages]",
        "WelcomeLabel2=This installs %s, %s made with Plugin Playground, as %s.%%n%%n"
        "When it has finished, rescan plugins in your host." % (
            q(made["name"]), _what(made), _listed(FORMAT_NAMES[f] for f in ("vst3", "clap") if f in found)),
        "",
        "[Files]",
        *files,
        "",
    ]), encoding="utf-8-sig")
    _run([inno_setup(), "/Q", script], "Inno Setup")
    if not target.exists():
        raise BuildError("Inno Setup finished but did not write %s" % target)


INSTALL_SH = """#!/bin/sh
# Installs {name} for this user: VST3 into ~/.vst3 and CLAP into ~/.clap, the folders
# every Linux host scans. `sh install.sh --remove` takes it out again.
set -e
here=$(cd "$(dirname "$0")" && pwd)
{steps}
"""


def package_linux(made, found, target, work):
    """The plugins and an install.sh, as a .tar.gz so the script keeps its run bit."""
    import tarfile

    top = work / made["slug"]
    top.mkdir()
    steps = ['if [ "$1" = "--remove" ]; then']
    for fmt, path in found.items():
        steps.append('    rm -rf "$HOME/.%s/%s"' % (fmt, path.name))
    steps += ['    echo "Removed {name}."'.format(name=made["name"]), "    exit 0", "fi"]
    for fmt, path in found.items():
        _copy(path, top / path.name)
        steps += ['mkdir -p "$HOME/.%s"' % fmt, 'rm -rf "$HOME/.%s/%s"' % (fmt, path.name),
                  'cp -R "$here/%s" "$HOME/.%s/"' % (path.name, fmt)]
    steps.append('echo "Installed %s. Rescan plugins in your host."' % made["name"])
    script = top / "install.sh"
    script.write_text(INSTALL_SH.format(name=made["name"], steps="\n".join(steps)), encoding="utf-8")
    script.chmod(0o755)
    (top / "README.txt").write_text(
        "%s\n%s\n%s, made with Plugin Playground.\n\nTo install it, run this in a terminal, in this folder:\n\n"
        "    sh install.sh\n\nIt copies the VST3 into ~/.vst3 and the CLAP into ~/.clap. Then rescan plugins in\n"
        "your host. `sh install.sh --remove` takes it out again.\n"
        % (made["name"], "=" * len(made["name"]), _what(made).capitalize()), encoding="utf-8")
    with tarfile.open(target, "w:gz") as tar:
        tar.add(top, arcname=made["slug"])


# ── the one installer of the runtime ────────────────────────────────────────────
#
# What the site's Download menu offers first: Plugin Playground itself, once per machine.
# It holds the runtime plugin and the helper, and sets the helper to open .ppdevice files.
# Every device after that is a file the page writes.

RUNTIME_PACKAGES = {"macos": "plugin-playground-macos.pkg", "windows": "plugin-playground-windows-setup.exe",
                    "linux": "plugin-playground-linux.tar.gz"}

LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"


def package_runtime(home, folder):
    """Make this system's installer of the runtime built in `home`. Returns its path."""
    import tempfile

    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / RUNTIME_PACKAGES[SYSTEM]
    if target.exists():
        target.unlink()
    with tempfile.TemporaryDirectory() as work:
        {"macos": package_runtime_macos, "windows": package_runtime_windows,
         "linux": package_runtime_linux}[SYSTEM](Path(home), target, Path(work))
    return target


def package_runtime_macos(home, target, work):
    """
    A .pkg that puts Plugin Playground.app in Applications (the machine's, or this user's
    if "for me only" is chosen). The app is sealed with a signature on the way in: ad hoc
    unless FX_CODESIGN_IDENTITY names a Developer ID.
    """
    app = work / "root" / "Applications" / (APP_NAME + ".app")
    app.parent.mkdir(parents=True)
    _copy(home / (APP_NAME + ".app"), app)
    identity = os.environ.get("FX_CODESIGN_IDENTITY") or "-"
    sign = ["codesign", "--force", "--sign", identity] + (["--options", "runtime", "--timestamp"] if identity != "-" else [])
    # inside out: each plugin in the app, then the app round them
    for bundle in sorted(app.rglob("*"), key=lambda p: -len(p.parts)):
        if bundle.is_dir() and bundle.suffix in (".clap", ".vst3", ".component"):
            _run(sign + [bundle], "codesign")
    _run(sign + [app], "codesign")
    welcome = ("<p><b>Plugin Playground</b> turns the effects and instruments you make on the site into "
               "plugins for every DAW on this Mac: Audio Unit, VST3 and CLAP.</p>"
               "<p>You install this once. After that, each <b>Download</b> on the site gives you a small file. "
               "Open it and the plugin is installed under the name you gave it.</p>")
    # $3 is where it was installed: / or, for one user, their home folder. Telling Launch
    # Services about the app now is what makes a .ppdevice open with it straight away.
    _mac_pkg(work, work / "root", "com.plugin-playground.helper", APP_NAME, welcome, target,
             '"%s" -f "${3%%/}/Applications/%s.app" >/dev/null 2>&1' % (LSREGISTER, APP_NAME))


def package_runtime_windows(home, target, work):
    """
    A setup program made with Inno Setup. It puts the helper and the runtime in Program
    Files, sets Windows to open .ppdevice files with the helper, and makes the two folders
    the helper writes plugins into, under Common Files where every host scans, writable
    by every user: so opening a device asks for no administrator.
    """
    q = lambda text: str(text).replace('"', "")  # noqa: E731
    script = work / "setup.iss"
    script.write_text("\n".join([
        "[Setup]",
        "AppId=PluginPlayground.Runtime",
        "AppName=%s" % APP_NAME,
        "AppVersion=%s" % VERSION,
        "AppPublisher=Plugin Playground",
        "DefaultDirName={commonpf64}\\%s" % APP_NAME,
        "DisableDirPage=yes",
        "DisableProgramGroupPage=yes",
        "DisableWelcomePage=no",
        "PrivilegesRequired=admin",
        "ChangesAssociations=yes",
        "Compression=lzma2",
        "SolidCompression=yes",
        "WizardStyle=modern",
        "OutputDir=%s" % q(target.parent),
        "OutputBaseFilename=%s" % target.name[:-len(".exe")],
        "",
        "[Messages]",
        "WelcomeLabel2=Plugin Playground turns the effects and instruments you make on the site into VST3 and CLAP "
        "plugins for every DAW on this PC.%n%nYou install this once. After that, each Download on the site gives you a "
        "small file. Open it and the plugin is installed under the name you gave it.",
        "",
        "[Files]",
        'Source: "%s"; DestDir: "{app}"; Flags: ignoreversion' % q(home / (APP_NAME + ".exe")),
        'Source: "%s"; DestDir: "{app}"; Flags: ignoreversion' % q(home / HELPER),
        'Source: "%s\\*"; DestDir: "{app}\\Runtime"; Flags: ignoreversion recursesubdirs createallsubdirs' % q(home / "Runtime"),
        "",
        "[Dirs]",
        'Name: "{commoncf64}\\VST3\\%s"; Permissions: users-modify' % APP_NAME,
        'Name: "{commoncf64}\\CLAP\\%s"; Permissions: users-modify' % APP_NAME,
        "",
        "[Registry]",
        'Root: HKLM; Subkey: "Software\\Classes\\.ppdevice"; ValueType: string; ValueName: ""; '
        'ValueData: "PluginPlayground.Device"; Flags: uninsdeletevalue',
        'Root: HKLM; Subkey: "Software\\Classes\\PluginPlayground.Device"; ValueType: string; ValueName: ""; '
        'ValueData: "Plugin Playground Device"; Flags: uninsdeletekey',
        'Root: HKLM; Subkey: "Software\\Classes\\PluginPlayground.Device\\shell\\open\\command"; ValueType: string; '
        'ValueName: ""; ValueData: """{app}\\%s.exe"" ""%%1"""' % APP_NAME,
        "",
    ]), encoding="utf-8-sig")
    _run([inno_setup(), "/Q", script], "Inno Setup")
    if not target.exists():
        raise BuildError("Inno Setup finished but did not write %s" % target)


LINUX_INSTALL = """#!/bin/sh
# Installs Plugin Playground for this user. `sh install.sh --remove` takes it out again
# (the plugins you made stay in ~/.vst3 and ~/.clap).
set -e
here=$(cd "$(dirname "$0")" && pwd)
data="${XDG_DATA_HOME:-$HOME/.local/share}"
home="$data/plugin-playground"
bin="$HOME/.local/bin"
desktop="$data/applications/plugin-playground.desktop"
mime="$data/mime/packages/plugin-playground.xml"

if [ "$1" = "--remove" ]; then
    rm -rf "$home" "$bin/plugin-playground-install" "$desktop" "$mime"
    command -v update-mime-database >/dev/null 2>&1 && update-mime-database "$data/mime" >/dev/null 2>&1 || true
    echo "Removed Plugin Playground."
    exit 0
fi

rm -rf "$home"
mkdir -p "$home" "$bin" "$data/applications" "$data/mime/packages"
cp -R "$here/Runtime" "$here/plugin-playground-install" "$here/plugin-playground-open" "$home/"
chmod +x "$home/plugin-playground-install" "$home/plugin-playground-open"
ln -sf "$home/plugin-playground-install" "$bin/plugin-playground-install"

# So that opening a .ppdevice in a file manager installs it.
cat > "$mime" <<XML
<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-plugin-playground-device">
    <comment>Plugin Playground device</comment>
    <glob pattern="*.ppdevice"/>
  </mime-type>
</mime-info>
XML
cat > "$desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Plugin Playground
Comment=Install a Plugin Playground device as a plugin
Exec="$home/plugin-playground-open" %f
MimeType=application/x-plugin-playground-device;
NoDisplay=true
Terminal=false
DESKTOP
command -v update-mime-database >/dev/null 2>&1 && update-mime-database "$data/mime" >/dev/null 2>&1 || true
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$data/applications" >/dev/null 2>&1 || true
command -v xdg-mime >/dev/null 2>&1 && xdg-mime default plugin-playground.desktop application/x-plugin-playground-device >/dev/null 2>&1 || true

echo "Installed Plugin Playground."
echo "Open a .ppdevice from the site to make it a plugin, or run:"
echo "    plugin-playground-install \\"My Effect.ppdevice\\""
case ":$PATH:" in *":$bin:"*) ;; *) echo "($bin is not on your PATH; the full path is $home/plugin-playground-install)";; esac
"""

LINUX_OPEN = """#!/bin/sh
# What a file manager runs for a .ppdevice: install it, and say how it went where the
# desktop can show it.
here=$(cd "$(dirname "$0")" && pwd)
out=$("$here/plugin-playground-install" "$1" 2>&1)
status=$?
if command -v notify-send >/dev/null 2>&1; then notify-send "Plugin Playground" "$out"
elif command -v zenity >/dev/null 2>&1; then zenity --info --title="Plugin Playground" --text="$out"
else echo "$out"
fi
exit $status
"""


def package_runtime_linux(home, target, work):
    """The helper, the runtime and an install.sh, as a .tar.gz so the programs keep their run bit."""
    import tarfile

    top = work / "plugin-playground"
    top.mkdir()
    shutil.copytree(home / "Runtime", top / "Runtime", symlinks=True)
    shutil.copy2(home / HELPER, top / HELPER)
    for name, text in (("install.sh", LINUX_INSTALL), ("plugin-playground-open", LINUX_OPEN)):
        (top / name).write_text(text, encoding="utf-8")
        (top / name).chmod(0o755)
    (top / "README.txt").write_text(
        "Plugin Playground\n=================\nTurns the effects and instruments you make on the site into VST3 and CLAP\n"
        "plugins.\n\nTo install it, run this in a terminal, in this folder:\n\n    sh install.sh\n\n"
        "After that, open a .ppdevice you downloaded from the site, or run\n\n    plugin-playground-install \"My Effect.ppdevice\"\n\n"
        "and it is installed into ~/.vst3 and ~/.clap under its own name.\n", encoding="utf-8")
    with tarfile.open(target, "w:gz") as tar:
        tar.add(top, arcname="plugin-playground")


def check_runtime_installer(home, made, folder, log=print):
    """
    What a visitor does, with no window: run the installer of the runtime that
    package_runtime() wrote into `folder`, then open a device with what it installed, and
    require the plugins to be where hosts look. The CLAP is then loaded and held to the
    device (hostcheck), and on macOS the Audio Unit has to pass auval, which is the check
    Logic makes before it lists one. Installs for the whole machine: CI can.
    """
    import tempfile

    installer = Path(folder).resolve() / RUNTIME_PACKAGES[SYSTEM]
    if not installer.exists():
        raise BuildError("there is no %s: build it with --runtime --package first" % installer)
    device = made["device"]
    name = made["name"]
    if SYSTEM == "macos":
        _run(["sudo", "-n", "installer", "-pkg", installer, "-target", "/"], "installer")
        app = Path("/Applications") / (APP_NAME + ".app")
        _run([app / "Contents" / "MacOS" / APP_NAME, "--install", device], "the helper")
        base = Path.home() / "Library" / "Audio" / "Plug-Ins"
        where = {"clap": base / "CLAP" / (name + ".clap"), "vst3": base / "VST3" / (name + ".vst3"),
                 "au": base / "Components" / (name + ".component")}
        binary = where["clap"] / "Contents" / "MacOS" / RUNTIME_NAME
    elif SYSTEM == "windows":
        _run([installer, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], "the setup program")
        programs = Path(os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles") or r"C:\Program Files")
        common = Path(os.environ.get("CommonProgramW6432") or os.environ.get("CommonProgramFiles") or r"C:\Program Files\Common Files")
        _run([programs / APP_NAME / HELPER, device], "the helper")
        opens = _run(["reg", "query", r"HKLM\Software\Classes\PluginPlayground.Device\shell\open\command", "/ve"],
                     "the file association").stdout
        if (APP_NAME + ".exe") not in opens:
            raise BuildError("the installer did not set .ppdevice files to open with the helper:\n%s" % opens)
        log("  .ppdevice files open with the helper")
        where = {"vst3": common / "VST3" / APP_NAME / (name + ".vst3"), "clap": common / "CLAP" / APP_NAME / (name + ".clap")}
        binary = where["clap"]
    else:
        with tempfile.TemporaryDirectory() as work:
            with tarfile_open(installer) as tar:
                tar.extractall(work)
            _run(["sh", Path(work) / "plugin-playground" / "install.sh"], "install.sh")
        _run([Path.home() / ".local" / "bin" / HELPER, device], "the helper")
        where = {fmt: Path.home() / ("." + fmt) / (name + "." + fmt) for fmt in ("clap", "vst3")}
        binary = where["clap"]
    for fmt, path in where.items():
        if not path.exists():
            raise BuildError("the device was opened, but there is no %s" % path)
        log("  %s: %s" % (FORMAT_NAMES[fmt], path))
    done = _run([home / ("hostcheck" + (".exe" if os.name == "nt" else "")), binary, device], "hostcheck")
    log("  %s" % done.stdout.strip())
    if SYSTEM == "macos":
        subprocess.run(["killall", "-9", "AudioComponentRegistrar"], capture_output=True)
        done = _run(["auval", "-v", au_type(made), au_subtype(made["slug"]), "PlPg"], "auval")
        log("  auval: %s" % ([l for l in done.stdout.splitlines() if "VALIDATION" in l] or ["passed"])[-1].strip())


def tarfile_open(path):
    import tarfile
    return tarfile.open(path)


def check_installer(made, folder, log=print):
    """
    Run this system's installer for a preset, the one --package wrote into `folder`, with
    no window, and require every plugin to be where hosts look for it. On macOS the Audio
    Unit then has to pass Apple's auval, which is the check Logic makes before it lists one.
    Installs for the whole machine, so it needs administrator rights: CI has them.
    """
    import tempfile

    installer = Path(folder).resolve() / (PACKAGE_NAMES[SYSTEM] % made["slug"])
    if not installer.exists():
        raise BuildError("there is no %s: build it with --package first" % installer)
    names = {"clap": made["name"] + ".clap", "vst3": made["name"] + ".vst3", "au": made["name"] + ".component"}
    if SYSTEM == "macos":
        _run(["sudo", "-n", "installer", "-pkg", installer, "-target", "/"], "installer")
        where = {fmt: Path("/") / MAC_FOLDERS[fmt] / names[fmt] for fmt in ("clap", "vst3", "au")}
    elif SYSTEM == "windows":
        _run([installer, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], "the setup program")
        common = Path(os.environ.get("CommonProgramW6432") or os.environ.get("CommonProgramFiles") or r"C:\Program Files\Common Files")
        where = {"vst3": common / "VST3" / names["vst3"], "clap": common / "CLAP" / names["clap"]}
    else:
        import tarfile
        with tempfile.TemporaryDirectory() as work:
            with tarfile.open(installer) as tar:
                tar.extractall(work)
            _run(["sh", Path(work) / made["slug"] / "install.sh"], "install.sh")
        where = {fmt: Path.home() / ("." + fmt) / names[fmt] for fmt in ("clap", "vst3")}
    for fmt, path in where.items():
        if not path.exists():
            raise BuildError("the installer ran, but there is no %s" % path)
        log("  the installer put the %s at %s" % (FORMAT_NAMES[fmt], path))
    if SYSTEM == "macos":
        subprocess.run(["killall", "-9", "AudioComponentRegistrar"], capture_output=True)
        done = _run(["auval", "-v", au_type(made), au_subtype(made["slug"]), "PlPg"], "auval")
        log("  auval: %s" % ([l for l in done.stdout.splitlines() if "VALIDATION" in l] or ["passed"])[-1].strip())


def write_index(folder):
    """
    index.json for a folder of downloads: which system has an installer of the runtime, as
    the page will fetch it ({"runtime": {"macos": "downloads/plugin-playground-macos.pkg"}}).
    Returns how many systems it lists.
    """
    folder = Path(folder)
    found = {system: "%s/%s" % (folder.name, name) for system, name in RUNTIME_PACKAGES.items() if (folder / name).exists()}
    (folder / "index.json").write_text(json.dumps({"runtime": found}, indent=1), encoding="utf-8")
    return len(found)


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
                    help="write DIR/index.json, listing the runtime installers in DIR by system, and stop")
    ap.add_argument("--out", default=str(ROOT / "build" / "plugins"), help="where to build (default: build/plugins)")
    ap.add_argument("--formats", default=None, help="comma-separated: clap, vst3, au (default: all this system can build)")
    ap.add_argument("--install", action="store_true", help="copy the plugins into your user plugin folders")
    ap.add_argument("--validate", action="store_true",
                    help="run clap-validator, pluginval and (on macOS, with --install) auval, whichever are installed")
    ap.add_argument("--source-only", action="store_true", help="write Effect.h and stop")
    ap.add_argument("--play", nargs=3, metavar=("NOTES", "SECONDS", "OUT.wav"),
                    help='render an instrument playing notes, e.g. "60@0+1:100,67@0.5+1:80" (key@start+length:velocity)')
    ap.add_argument("--package", metavar="DIR",
                    help="also make this system's installer in DIR: a .pkg, a setup .exe, or a .tar.gz with install.sh")
    ap.add_argument("--runtime", action="store_true",
                    help="build the runtime plugin and the helper, not one device's plugin. With --preset or "
                         "--settings, also make that device from it, check it, and with --install put it in your plugin folders")
    ap.add_argument("--check-installer", metavar="DIR",
                    help="run the installer --package wrote into DIR, silently, and check what it installed (needs admin rights)")
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
        print("listed %d installers in %s" % (write_index(Path(args.index)), Path(args.index) / "index.json"))
        return 0
    if args.runtime:
        try:
            out = Path(args.out)
            print("Plugin Playground runtime")
            home = build_runtime(out)
            print("  built %s" % home)
            chosen = [find_preset(args.preset)["settings"]] if args.preset else \
                [read_settings(args.settings)] if args.settings else \
                [p["settings"] for p in PRESETS + INSTRUMENTS] if args.all else []
            if args.package:
                print("  packed %s" % package_runtime(home, Path(args.package)))
            for settings in chosen:
                made = generate(settings, out / patchc.slug(fxgen.normalize(settings)["name"]))
                print(made["name"])
                # A window takes five seconds to open and look at. Every preset's sound is
                # checked; with --all, the windows are left to the presets asked for by name.
                check_runtime(home, made, out / "_runtime_check", window=not args.all)
                if args.check_installer:
                    check_runtime_installer(home, made, Path(args.check_installer))
                if args.install or args.validate:
                    plugins = install_device(home, made["device"])
                    for path in plugins:
                        print("  installed %s" % path)
                    by_format = {".clap": "clap", ".vst3": "vst3", ".component": "au"}
                    found = {by_format[path.suffix]: path for path in plugins}
                    if args.validate and not validate(made, found, True):
                        return 1
            return 0
        except (BuildError, patchc.CompileError) as err:
            print("plugin: %s" % err, file=sys.stderr)
            return 1
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
        if args.check_installer:
            check_installer(made, Path(args.check_installer))
        if args.validate and not validate(made, found, args.install):
            return 1
        return 0
    except (BuildError, patchc.CompileError) as err:
        print("plugin: %s" % err, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
