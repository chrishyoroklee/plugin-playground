"""
The helper that makes a device into plugins (native/helper/install.h), driven through its
terminal form (cli.cpp) against a stand-in runtime in a temporary folder.

What it must never do is replace a plugin it did not make. A device's name is the
visitor's (or a shared link's), and the installer runs the helper with every user's
plugin folders, as root on macOS: a device called "FabFilter Pro-Q 3" must not delete
FabFilter's plugin. Everything the helper makes carries its device (inside a bundle, or
beside a .clap that is one file), and that is how it knows its own.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "plugin"))
import patchc  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("plugin_cli", ROOT / "scripts" / "plugin.py")
plugin = sys.modules.get("plugin_cli") or importlib.util.module_from_spec(_spec)
if "plugin_cli" not in sys.modules:
    sys.modules["plugin_cli"] = plugin
    _spec.loader.exec_module(plugin)

needs_compiler = pytest.mark.skipif(plugin.compiler() is None, reason="no C++ compiler")
RUNTIME = "Plugin Playground Runtime"


@pytest.fixture(scope="module")
def helper(tmp_path_factory):
    out = tmp_path_factory.mktemp("helper") / ("install" + (".exe" if sys.platform == "win32" else ""))
    native = ROOT / "native"
    cmd = [plugin.compiler(), "-std=c++17", "-O1", "-I", native, "-I", native / "runtime", "-I", native / "helper",
           native / "helper" / "cli.cpp", "-o", out]
    subprocess.run([str(c) for c in cmd], check=True, capture_output=True)
    return out


@pytest.fixture
def runtime(tmp_path):
    """A stand-in for the runtime folder: a .clap that is one file and a .vst3 bundle."""
    folder = tmp_path / "Runtime"
    folder.mkdir()
    (folder / (RUNTIME + ".clap")).write_bytes(b"clap binary")
    vst3 = folder / (RUNTIME + ".vst3") / "Contents"
    (vst3 / "x86_64-linux").mkdir(parents=True)
    (vst3 / "x86_64-linux" / (RUNTIME + ".so")).write_bytes(b"vst3 binary")
    return folder


def _device(tmp_path, name):
    path = tmp_path / "device.ppdevice"
    path.write_text(patchc.device_for(json.dumps({"name": name, "chain": [{"module": "drive", "on": True}]})))
    return path


def _run(helper, device, runtime, to):
    # The helper writes UTF-8 (paths can hold any letter), whatever the console's code page.
    return subprocess.run([str(helper), str(device), "--runtime", str(runtime), "--to", str(to)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def _foreign(path, is_bundle):
    """Another maker's plugin with this name: something the helper did not make."""
    if is_bundle:
        (path / "Contents").mkdir(parents=True)
        (path / "Contents" / "Info.plist").write_text("<plist><string>com.fabfilter.ProQ3</string></plist>")
        (path / "Contents" / "theirs.bin").write_bytes(b"theirs")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"theirs")


@needs_compiler
def test_it_makes_the_plugins_and_replaces_its_own(helper, runtime, tmp_path):
    to = tmp_path / "plugins"
    device = _device(tmp_path, "Mine")
    for _ in range(2):   # a second install replaces the first: it is the helper's own
        done = _run(helper, device, runtime, to)
        assert done.returncode == 0, done.stderr
    assert (to / "CLAP" / "Mine.clap").read_bytes() == b"clap binary"
    assert (to / "CLAP" / "Mine.ppdevice").read_text() == device.read_text()
    assert (to / "VST3" / "Mine.vst3" / "Contents" / "Resources" / "device.ppdevice").read_text() == device.read_text()


@needs_compiler
@pytest.mark.parametrize("fmt", ["vst3", "clap"])
def test_it_never_replaces_a_plugin_it_did_not_make(helper, runtime, tmp_path, fmt):
    to = tmp_path / "plugins"
    theirs = to / fmt.upper() / ("FabFilter Pro-Q 3." + fmt)
    _foreign(theirs, fmt == "vst3")
    done = _run(helper, _device(tmp_path, "FabFilter Pro-Q 3"), runtime, to)
    assert done.returncode == 1
    assert "did not make" in done.stderr and "Rename your device" in done.stderr
    # theirs is as it was, and nothing of ours was written beside it
    if fmt == "vst3":
        assert (theirs / "Contents" / "theirs.bin").read_bytes() == b"theirs"
        assert not (theirs / "Contents" / "Resources").exists()
    else:
        assert theirs.read_bytes() == b"theirs"
        assert not theirs.with_suffix(".ppdevice").exists()
    other = "VST3" if fmt == "clap" else "CLAP"
    assert not any((to / other).glob("FabFilter*")) if (to / other).exists() else True


@needs_compiler
def test_a_link_in_the_way_is_not_followed(helper, runtime, tmp_path):
    to = tmp_path / "plugins"
    elsewhere = tmp_path / "elsewhere.vst3"
    (elsewhere / "Contents" / "Resources").mkdir(parents=True)
    (elsewhere / "Contents" / "Resources" / "device.ppdevice").write_text("looks like ours")
    (to / "VST3").mkdir(parents=True)
    try:
        (to / "VST3" / "Linked.vst3").symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("this system will not make a symbolic link")
    done = _run(helper, _device(tmp_path, "Linked"), runtime, to)
    assert done.returncode == 1 and "did not make" in done.stderr
    assert (elsewhere / "Contents" / "Resources" / "device.ppdevice").read_text() == "looks like ours"


@needs_compiler
def test_a_compiled_plugin_of_ours_can_be_replaced(helper, runtime, tmp_path):
    """One built by `npm run plugin` has no device inside, but carries our identifier."""
    to = tmp_path / "plugins"
    compiled = to / "VST3" / "Old.vst3" / "Contents"
    compiled.mkdir(parents=True)
    (compiled / "Info.plist").write_text("<plist><string>com.plugin-playground.old</string></plist>")
    done = _run(helper, _device(tmp_path, "Old"), runtime, to)
    assert done.returncode == 0, done.stderr
    assert (to / "VST3" / "Old.vst3" / "Contents" / "Resources" / "device.ppdevice").exists()


@needs_compiler
@pytest.mark.parametrize("name, file", [("CON", "CON-"), ("nul", "nul-"), ("Com1", "Com1-"), ("Console", "Console")])
def test_names_windows_keeps_for_itself_get_a_dash(helper, runtime, tmp_path, name, file):
    to = tmp_path / "plugins"
    done = _run(helper, _device(tmp_path, name), runtime, to)
    assert done.returncode == 0, done.stderr
    assert (to / "CLAP" / (file + ".clap")).exists()
