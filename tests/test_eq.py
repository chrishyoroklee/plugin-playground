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
        module = values.pop("module", "eq")
        settings = {"name": "EQ " + tag, "chain": [{"module": module, "on": True, "values": values}]}
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


# ── the other kinds: parametric, graphic, tilt ──────────────────────────────────
#
# Each is held to the same two promises as the three-band EQ (flat at 0 dB, and a dial's
# dB is that band's dB), and to its picture: graphs.js draws these from the filters as they
# run, so the model below is that drawing's arithmetic, and the render must agree with it.

SR = float(RATE)


def _zinv(f):
    return complex(math.cos(2 * math.pi * f / SR), -math.sin(2 * math.pi * f / SR))


def _pole(f, corner):
    k = 1 - math.exp(-2 * math.pi * min(SR / 4, corner) / SR)
    return k / (1 - (1 - k) * _zinv(f))


def _bell(f, centre, q):
    k, z = math.sin(2 * math.pi * min(SR / 4, centre) / SR), _zinv(f)
    return k * (1 - z) / ((1 - z) ** 2 + k * k * z + k / q * z * (1 - z)) * z / q


def _gain(db):
    return 10 ** (db / 20.0)


def _high(f, corner, g):
    return (g - 1) * fxgen.EQ_HIGH_FIT * g ** fxgen.EQ_HIGH_BEND * (1 - _pole(f, corner * math.sqrt(g)))


def _drawn(module, f, v):
    """What graphs.js draws for these dial values at f, in dB."""
    d = {p["id"]: v.get(p["id"], p["default"]) for p in fxgen._module(module)["params"]}
    if module == "peq":
        gl, gh = _gain(d["lg"]), _gain(d["hg"])
        h = 1 + (gl - 1) * _pole(f, d["lf"] / math.sqrt(gl)) + _high(f, d["hf"], gh)
        h += (_gain(d["g1"]) - 1) * _bell(f, d["f1"], d["q1"]) + (_gain(d["g2"]) - 1) * _bell(f, d["f2"], d["q2"])
    elif module == "geq":
        h = 1 + sum((_gain(d["b%d" % (i + 1)]) - 1) * _bell(f, hz, fxgen.GEQ_Q) for i, hz in enumerate(fxgen.GEQ_BANDS))
        h += _high(f, fxgen.GEQ_SHELF_HZ, _gain(d["b6"]))
    else:
        up, down = _gain(d["tilt"]), _gain(-d["tilt"])
        h = up + (down - up) * _pole(f, d["pivot"] * up)
    return 20 * math.log10(abs(h))


OTHER_KINDS = [
    ("peq", {}),
    ("peq", {"lg": 9}), ("peq", {"lg": -9}), ("peq", {"hg": 9}), ("peq", {"hg": -9, "hf": 5000}),
    ("peq", {"g1": 9}), ("peq", {"g1": -9, "q1": 6}), ("peq", {"g2": 9, "q2": 4, "f2": 5000}),
    ("peq", {"lf": 110, "lg": 3, "f1": 320, "g1": -4, "q1": 2, "f2": 3200, "g2": 3, "q2": 1.5, "hf": 4000, "hg": 4}),
    ("geq", {}),
    ("geq", {"b1": 9}), ("geq", {"b3": -9}), ("geq", {"b6": 9}), ("geq", {"b6": -9}),
    ("geq", {"b1": 6, "b2": 3, "b3": -2, "b4": -4, "b5": 2, "b6": 6}),
    ("tilt", {}),
    ("tilt", {"tilt": 6}), ("tilt", {"tilt": -4, "pivot": 2000}),
]


@needs_compiler
@pytest.mark.parametrize("case", range(len(OTHER_KINDS)))
def test_the_other_kinds_sound_as_they_are_drawn(bench, case):
    import numpy as np
    module, values = OTHER_KINDS[case]
    got, y, x = bench("kind%d" % case, module=module, **values)
    if not values:
        assert np.abs(y - x).max() < 1e-5   # every gain at 0 dB: untouched
    for f in TONES:
        assert got[f] == pytest.approx(_drawn(module, f, values), abs=0.15), (module, values, f)


@needs_compiler
@pytest.mark.parametrize("db", [9, -9])
def test_a_parametric_bell_is_its_gain_at_its_frequency_and_narrower_with_q(bench, db):
    wide, _, _ = bench("bellwide%d" % db, module="peq", g2=db, f2=1000, q2=1)
    thin, _, _ = bench("bellthin%d" % db, module="peq", g2=db, f2=1000, q2=8)
    assert wide[1000] == pytest.approx(db, abs=0.3) and thin[1000] == pytest.approx(db, abs=0.3)
    assert abs(thin[250]) < abs(wide[250]) and abs(thin[2500]) < abs(wide[2500])


@needs_compiler
def test_tilt_turns_the_lows_one_way_and_the_highs_the_other(bench):
    got, _, _ = bench("tiltup", module="tilt", tilt=6, pivot=1000)
    assert got[60] == pytest.approx(-6, abs=0.4) and got[10000] == pytest.approx(6, abs=1.3)
    assert abs(got[1000]) < 1.0   # the pivot is where next to nothing changes (0.7 dB at a full 6)
