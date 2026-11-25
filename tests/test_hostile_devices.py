"""
A device file anyone could write: what it may not do to the plugin that runs it.

A device is text (patchc.py writes it; a shared link or a downloaded file carries it), so
the runtime cannot assume the page wrote it. A crafted one used to crash the host: a
delay of NaN milliseconds became an index of INT_MIN (a wild read on x86_64), and a
longest delay of 1e12 ms asked for a buffer the size of the address space, whose
exception went through the CLAP callback and ended the host. Now the delay kernels keep
what they are given finite and in range (mxrt.h, detail::within) and the parser caps how
much a file may ask for (mxvm.h).

The kernels are run here under the address and undefined-behaviour sanitizers where
the compiler has them: UBSan's float-cast-overflow catches a NaN turned into an int even
on Arm, where it happens not to crash.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "plugin"))
import patchc  # noqa: E402

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("plugin_cli", ROOT / "scripts" / "plugin.py")
plugin = sys.modules.get("plugin_cli") or importlib.util.module_from_spec(_spec)
if "plugin_cli" not in sys.modules:
    sys.modules["plugin_cli"] = plugin
    _spec.loader.exec_module(plugin)

needs_compiler = pytest.mark.skipif(plugin.compiler() is None, reason="no C++ compiler")

DRIVER = r"""
#include <cmath>
#include <cstdio>
#include <fstream>
#include <limits>
#include <sstream>
#include "mxrt.h"
#include "mxvm.h"

static int fails = 0;
static void expect(bool ok, const char* what) { if (!ok) { std::printf("FAILED: %s\n", what); fails++; } }

int main(int argc, char** argv) {
    if (argc == 3 && std::string(argv[1]) == "parse") {
        std::ifstream f(argv[2], std::ios::binary);
        std::ostringstream text;
        text << f.rdbuf();
        mxvm::Book book;
        std::string why;
        std::printf("%s\n", book.parse(text.str(), &why) ? "parsed" : why.c_str());
        return 0;
    }
    const double nan = std::numeric_limits<double>::quiet_NaN();
    const double inf = std::numeric_limits<double>::infinity();
    const double bad[] = {nan, inf, -inf, 1e300, -1e300, 1e12, -5.0, 0.0};
    const size_t most = static_cast<size_t>(20000.0 * 768000 / 1000) + 8;   // 20 s at the highest rate
    for (double v : bad) {
        mxrt::Comb comb;
        comb.prepare(v, v, v, 1, 1, 0.5);
        expect(comb.xb.size() >= 1 && comb.xb.size() <= most, "a comb's buffer stays within 20 s");
        for (int i = 0; i < 64; i++) { comb.tick(1.0); comb.tickSwept(1.0, v); }
        comb.prepare(44100, 60, 25, 1, 1, 0.5);
        for (int i = 0; i < 64; i++) comb.tickSwept(1.0, v);

        mxrt::Allpass ap;
        ap.prepare(v, v, v, 0.5);
        expect(ap.xb.size() >= 1 && ap.xb.size() <= most, "an allpass's buffer stays within 20 s");
        ap.setDelay(v);
        for (int i = 0; i < 64; i++) ap.tick(1.0);

        mxrt::TapLine tap;
        tap.prepare(v, v);
        expect(tap.buf.size() >= 1 && tap.buf.size() <= most, "a delay line stays within 20 s");
        for (int i = 0; i < 64; i++) { tap.readInLoop(v); tap.write(1.0); tap.read(v); }
    }
    std::printf("%d failed\n", fails);
    return fails ? 1 : 0;
}
"""


@pytest.fixture(scope="module")
def driver(tmp_path_factory):
    work = tmp_path_factory.mktemp("hostile")
    source = work / "driver.cpp"
    source.write_text(DRIVER)
    out = work / ("driver" + (".exe" if sys.platform == "win32" else ""))
    native = ROOT / "native"
    base = [plugin.compiler(), "-std=c++17", "-O1", "-g", "-I", native, "-I", native / "runtime", source, "-o", out]
    sanitized = base[:1] + ["-fsanitize=address,undefined", "-fno-sanitize-recover=all"] + base[1:]
    for cmd in ([sanitized] if sys.platform != "win32" else []) + [base]:
        if subprocess.run([str(c) for c in cmd], capture_output=True).returncode == 0:
            return out
    pytest.fail("the driver did not compile")


@needs_compiler
def test_the_delays_take_any_number_without_reading_outside_their_buffers(driver):
    done = subprocess.run([str(driver)], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.strip().endswith("0 failed")


def _parse(driver, tmp_path, text):
    path = tmp_path / "device.ppdevice"
    path.write_text(text)
    return subprocess.run([str(driver), "parse", str(path)], capture_output=True, text=True, check=True).stdout.strip()


def _device(module="drive"):
    return patchc.device_for(json.dumps({"name": "X", "chain": [{"module": module, "on": True}]}))


@needs_compiler
def test_a_file_may_not_hold_more_than_256_programs(driver, tmp_path):
    one = _device()
    assert _parse(driver, tmp_path, one) == "parsed"
    assert _parse(driver, tmp_path, one * 256) == "parsed"
    assert _parse(driver, tmp_path, one * 257) == "the device file holds too many programs"


@needs_compiler
def test_a_program_may_not_ask_for_more_than_4096_objects(driver, tmp_path):
    lines = _device("reverb").splitlines()
    last = max(i for i, l in enumerate(lines) if l.startswith("k "))
    count = sum(1 for l in lines if l.startswith("k "))
    extra = ["k %d Comb" % (count + i) for i in range(4097 - count)]
    assert _parse(driver, tmp_path, "\n".join(lines[:last + 1] + extra[:-1] + lines[last + 1:]) + "\n") == "parsed"
    assert _parse(driver, tmp_path, "\n".join(lines[:last + 1] + extra + lines[last + 1:]) + "\n") == \
        "the device file asks for too many objects"


@needs_compiler
@pytest.mark.parametrize("longest, delay", [("nan", "nan"), ("1e12", "25.31"), ("60.0", "-1e300"), ("inf", "inf")])
def test_a_reverb_with_impossible_delays_plays_to_the_end(tmp_path, longest, delay):
    """The real runtime, a real reverb, its comb's longest delay and delay made absurd."""
    pytest.importorskip("numpy")
    import numpy as np

    lines = _device("reverb").splitlines()
    # The first comb's prepare names its longest delay and its delay by register.
    prep = next(l.split() for l in lines if l.startswith("c 0 prepare"))
    edited = []
    for l in lines:
        w = l.split()
        if w[:1] == ["n"] and w[1] == prep[5]:
            l = "n %s %s" % (w[1], longest)
        elif w[:1] == ["n"] and w[1] == prep[6]:
            l = "n %s %s" % (w[1], delay)
        edited.append(l)
    device = tmp_path / "hostile.ppdevice"
    device.write_text("\n".join(edited) + "\n")
    signal = tmp_path / "signal.wav"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "maxcheck" / "make_signal.py"), str(signal)], check=True,
                   capture_output=True)
    renderer = plugin.build_runtime_renderer(tmp_path / ("render" + (".exe" if sys.platform == "win32" else "")))
    out = tmp_path / "out.wav"
    plugin.render(renderer, signal, out, as_float=True, device=device)
    # It ran to the end of the signal: what it sounds like does not matter, only that the
    # renderer (the plugin's own Effect) neither crashed nor stopped.
    assert len(np.frombuffer(out.read_bytes()[44:], dtype="<f4")) > 44100
