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
VERSION = "0.1.0"
FORMAT_NAMES = {"clap": "CLAP", "vst3": "VST3", "au": "Audio Unit"}
# Where an installer puts each format: the folders shared by every user of the machine.
MAC_FOLDERS = {"au": "Library/Audio/Plug-Ins/Components", "vst3": "Library/Audio/Plug-Ins/VST3",
               "clap": "Library/Audio/Plug-Ins/CLAP"}
WINDOWS_FOLDERS = {"vst3": r"{commoncf64}\VST3", "clap": r"{commoncf64}\CLAP"}


def _run(cmd, what, **kwargs):
    done = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kwargs)
    if done.returncode != 0:
        raise BuildError("%s failed:\n%s\n%s" % (what, done.stdout[-4000:], done.stderr[-4000:]))
    return done


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
    import plistlib

    root, scripts, resources, parts = work / "root", work / "scripts", work / "resources", work / "parts"
    for d in (root, scripts, resources, parts):
        d.mkdir()
    identity = os.environ.get("FX_CODESIGN_IDENTITY") or "-"
    for fmt, path in found.items():
        staged = root / MAC_FOLDERS[fmt] / path.name
        staged.parent.mkdir(parents=True, exist_ok=True)
        _copy(path, staged)
        sign = ["codesign", "--force", "--sign", identity]
        if identity != "-":
            sign += ["--options", "runtime", "--timestamp"]
        _run(sign + [staged], "codesign")


    # An Audio Unit copied in a moment ago is not always known to the system yet.
    post = scripts / "postinstall"
    post.write_text("#!/bin/sh\nkillall -9 AudioComponentRegistrar >/dev/null 2>&1\nexit 0\n")
    post.chmod(0o755)

    # Without this, Installer "relocates" a bundle: finding another copy of it anywhere on
    # the disk (a build folder, say), it updates that copy and leaves the plugin folder empty.
    components = work / "components.plist"
    _run(["pkgbuild", "--analyze", "--root", root, components], "pkgbuild --analyze")
    listed = plistlib.loads(components.read_bytes())
    for bundle in listed:
        bundle["BundleIsRelocatable"] = False
    components.write_bytes(plistlib.dumps(listed))

    identifier = "com.plugin-playground.%s" % made["slug"]
    _run(["pkgbuild", "--root", root, "--component-plist", components, "--scripts", scripts,
          "--identifier", identifier, "--version", VERSION, "--install-location", "/",
          parts / "plugins.pkg"], "pkgbuild")

    name = _xml(made["name"])
    where = ("Instrument > AU Instruments > Plugin Playground" if made["kind"] == "instrument"
             else "Audio FX > Audio Units > Plugin Playground")
    (resources / "welcome.html").write_text(
        "<html><body style=\"font: 13px -apple-system, sans-serif\">"
        "<p><b>%s</b> is %s, made with Plugin Playground.</p>"
        "<p>This installs it as %s, so every host on this Mac can use it.</p>"
        "<p>When it has finished, restart your host. In Logic Pro and GarageBand it is under "
        "<b>%s</b>.</p></body></html>"
        % (name, _what(made), _listed(FORMAT_NAMES[f] for f in ("au", "vst3", "clap") if f in found), where),
        encoding="utf-8")
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
        '</installer-gui-script>\n' % (name, identifier, identifier, VERSION), encoding="utf-8")
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
    index.json for a folder of packaged presets: preset id -> system -> the installer's
    path, as the page will fetch it ("downloads/soft-pad-macos.pkg"). Returns how many
    presets it lists.
    """
    index = {}
    for p in PRESETS + INSTRUMENTS:
        for system, pattern in PACKAGE_NAMES.items():
            name = pattern % patchc.slug(p["settings"].get("name", p["name"]))
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
                    help="write DIR/index.json, listing the --package installers in DIR by preset and system, and stop")
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
