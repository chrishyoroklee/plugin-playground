"""
The EQ, held to what its dials say.

It is built from objects that were already there (two onepole~ and an svf~ per channel,
fxgen.py, m_eq), so nothing new is compiled. What is new is a promise: +6 dB on a dial is
+6 dB in that band and not somewhere else, and with every gain at 0 dB the sound comes out
exactly as it went in. These tests play it sine tones and measure.

Needs a C++ compiler and numpy; skipped without them.
"""

import importlib.util
import math
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


plugin = _load("plugin_cli", ROOT / "scripts" / "plugin.py")
fxgen = plugin.fxgen
needs_compiler = pytest.mark.skipif(plugin.compiler() is None, reason="no C++ compiler")

RATE = 44100
TONES = (60, 250, 1000, 2500, 5000, 10000)   # one per second, in that order


@pytest.fixture(scope="session")
def bench(tmp_path_factory):
    np = pytest.importorskip("numpy")
    work = tmp_path_factory.mktemp("eq")
    t = np.arange(RATE) / RATE
    x = np.concatenate([0.1 * np.sin(2 * np.pi * f * t) for f in TONES])
    pcm = (np.stack([x, x], 1) * 32767).astype("<i2")
    x = pcm[:, 0] / 32767.0   # what is in the file: the tones as 16 bits hold them
    with wave.open(str(work / "tones.wav"), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    renderer = plugin.build_runtime_renderer(work / ("render" + (".exe" if sys.platform == "win32" else "")))

    def gains(tag, **values):
        """What the EQ does to each tone, in dB, with these dial values."""
        settings = {"name": "EQ " + tag, "chain": [{"module": "eq", "on": True, "values": values}]}
        made = plugin.generate(settings, work / tag)
        out = work / (tag + ".wav")
        plugin.render(renderer, work / "tones.wav", out, as_float=True, device=made["device"])
        y = np.frombuffer(out.read_bytes()[44:], dtype="<f4").astype("f8").reshape(-1, 2)[:, 0]
        level = lambda sig, k: float(np.sqrt(np.mean(sig[k * RATE + RATE // 4:(k + 1) * RATE] ** 2)))
        return {f: 20 * math.log10(level(y, k) / level(x, k)) for k, f in enumerate(TONES)}, y, x

    return gains


@needs_compiler
def test_with_every_gain_at_zero_the_sound_is_untouched(bench):
    import numpy as np
    _, y, x = bench("flat", low=0, mid=0, freq=1000, high=0)
    # to the last bit of the 32-bit float it is written as: three terms that are each times zero
    # (to within how the file's 16 bits are read back)
    assert np.abs(y - x).max() < 1e-5


@needs_compiler
@pytest.mark.parametrize("db", [12, 6, -6, -12])
def test_low_is_a_shelf_at_the_bottom_and_a_cut_mirrors_a_boost(bench, db):
    g, _, _ = bench("low%d" % db, low=db, mid=0, freq=1000, high=0)
    assert abs(g[60] - db) < 1.2, g           # nearly all of it, well below the centre
    assert abs(g[250] - db / 2) < 0.6, g      # exactly half of it, in dB, at the centre: for a cut as for a boost
    assert abs(g[10000]) < 0.6, g             # and the top left alone


@needs_compiler
@pytest.mark.parametrize("db", [12, 6, -6, -12])
def test_high_is_a_shelf_at_the_top_that_reaches_its_dial(bench, db):
    g, _, _ = bench("high%d" % db, low=0, mid=0, freq=1000, high=db)
    assert abs(g[10000] - db) < 1.0, g                 # what the dial says, at the top
    assert 0.45 < g[2500] / db < 0.65, g               # about half of it at the centre, for a cut as for a boost
    assert abs(g[60]) < 0.3 and abs(g[250]) < 0.6, g   # and the bottom left alone


@needs_compiler
@pytest.mark.parametrize("db", [12, 6, -6, -12])
@pytest.mark.parametrize("centre", [250, 1000, 2500, 5000])
def test_mid_is_a_peak_where_its_frequency_dial_points(bench, db, centre):
    """As deep a cut as a boost is high, all the way up the Freq dial."""
    g, _, _ = bench("mid%d-%d" % (db, centre), low=0, mid=db, freq=centre, high=0)
    assert abs(g[centre] - db) < 0.6, g
    far = 60 if centre >= 1000 else 10000
    assert abs(g[far]) < 1.2, g   # an octave or so wide, not the whole range


@needs_compiler
def test_the_bands_add_up(bench):
    """All three at once: each band still where its dial says, give or take what its neighbours add."""
    g, _, _ = bench("all", low=6, mid=-6, freq=1000, high=6)
    assert 4.5 < g[60] < 7.5 and -7.5 < g[1000] < -3.0 and 4.5 < g[10000] < 7.5, g


def test_the_eq_is_a_module_like_the_others():
    st = fxgen.normalize({"chain": [{"module": "eq", "on": True, "values": {"low": 99, "freq": 1}}]})
    entry = st["chain"][0]
    assert entry["module"] == "eq" and entry["values"] == {"low": 15.0, "mid": 0.0, "freq": 200.0, "high": 0.0}
    result = fxgen.run({"chain": [{"module": "eq", "on": True, "values": {"low": 6, "mid": -3, "high": 4}}]}, want_amxd=True)
    cords = result["coverage"]["cords"]
    # the one cord nothing checks is the picture's (a v8ui, which maxpylang does not know yet)
    assert cords["flagged"] == 0 and not result["warnings"]
    assert all(why[0].startswith("jsui ") for why in cords["not_checked_because"])
    dials = [b["box"]["varname"] for b in result["json"]["patcher"]["boxes"] if b["box"].get("maxclass") == "live.dial"]
    assert dials == ["eq_low", "eq_mid", "eq_mid_freq", "eq_high", "output_gain"]
    assert result["amxd"]
