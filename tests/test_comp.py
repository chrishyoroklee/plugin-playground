"""
The compressor, held to what its dials say.

fxgen.py's m_comp: the louder channel's level, followed by slide~ (attack up, release
down), in dB with atodb~, less the threshold, times (1 / ratio - 1), plus makeup, back
with dbtoa~, on both channels. These tests play it steady tones and level steps through
the runtime plugin and measure.

A sine's level is read by its peak. The follower rides a little under a sine's peak
between cycles (it is falling at the release speed while the wave goes through zero), so
over the threshold a sine is turned down by a little less than the curve says: under a
dB at the settings here. That is what a peak compressor does, and the tolerances say so.

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

LEVELS = list(range(-40, 1, 4))   # dB, a 1 kHz sine at each for half a second


@pytest.fixture(scope="session")
def bench(tmp_path_factory):
    np = pytest.importorskip("numpy")
    work = tmp_path_factory.mktemp("comp")
    renderer = plugin.build_runtime_renderer(work / ("render" + (".exe" if sys.platform == "win32" else "")))
    count = [0]

    def write(name, left, right=None, rate=44100):
        right = left if right is None else right
        pcm = (np.stack([left, right], 1) * 32767).clip(-32767, 32767).astype("<i2")
        with wave.open(str(work / name), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm.tobytes())
        return pcm / 32767.0

    def run(values, source):
        """Both channels of `source` through a compressor with these dials."""
        count[0] += 1
        settings = {"name": "Comp %d" % count[0], "chain": [{"module": "comp", "on": True, "values": values}]}
        made = plugin.generate(settings, work / ("c%d" % count[0]))
        out = work / ("c%d.wav" % count[0])
        plugin.render(renderer, work / source, out, as_float=True, device=made["device"])
        return np.frombuffer(out.read_bytes()[44:], dtype="<f4").astype("f8").reshape(-1, 2)

    rate = 44100
    half = rate // 2
    t = np.arange(half) / rate
    steps = write("steps.wav", np.concatenate([10 ** (db / 20) * np.sin(2 * np.pi * 1000 * t) for db in LEVELS]))

    def curve(values):
        """Output peak, in dB, for each of LEVELS (after the follower has settled)."""
        y = run(dict({"attack": 5, "release": 50}, **values), "steps.wav")
        peak = lambda s, k: 20 * math.log10(np.abs(s[k * half + half // 2:(k + 1) * half, 0]).max())
        return [peak(y, k) for k in range(len(LEVELS))], y, steps

    return {"np": np, "run": run, "write": write, "curve": curve}


@needs_compiler
def test_under_the_threshold_nothing_changes(bench):
    out, _, _ = bench["curve"]({"thresh": -20, "ratio": 4})
    for level, got in zip(LEVELS, out):
        if level <= -24:
            assert got == pytest.approx(level, abs=0.05), level


@needs_compiler
@pytest.mark.parametrize("thresh, ratio", [(-20, 4), (-10, 2), (-30, 20)])
def test_over_the_threshold_each_ratio_db_in_is_one_out(bench, thresh, ratio):
    out, _, _ = bench["curve"]({"thresh": thresh, "ratio": ratio})
    over = [(level, got) for level, got in zip(LEVELS, out) if level >= thresh + 4]
    # the slope: 1 / ratio, to a twentieth of a dB per dB
    for (a, ya), (b, yb) in zip(over, over[1:]):
        assert (yb - ya) / (b - a) == pytest.approx(1.0 / ratio, abs=0.05), (a, b)
    # and where the line is: on the curve, give or take the follower riding under a peak
    for level, got in over:
        want = thresh + (level - thresh) / ratio
        assert want - 0.05 <= got <= want + 1.2, (level, got, want)


@needs_compiler
def test_makeup_is_its_dial_in_db_and_mix_zero_is_the_sound_untouched(bench):
    np = bench["np"]
    plain, _, _ = bench["curve"]({"thresh": -20, "ratio": 4, "makeup": 0})
    lifted, _, _ = bench["curve"]({"thresh": -20, "ratio": 4, "makeup": 9})
    for a, b in zip(plain, lifted):
        assert b - a == pytest.approx(9, abs=0.01)
    _, y, x = bench["curve"]({"thresh": -40, "ratio": 20, "makeup": 12, "mix": 0})
    assert np.abs(y - x).max() < 1e-4


@needs_compiler
def test_the_channels_are_turned_down_together(bench):
    """Linked: a loud left turns a quiet right down by as much, so the image holds still."""
    np = bench["np"]
    t = np.arange(44100) / 44100
    x = bench["write"]("panned.wav", 0.9 * np.sin(2 * np.pi * 500 * t), 0.05 * np.sin(2 * np.pi * 700 * t))
    y = bench["run"]({"thresh": -20, "ratio": 10, "attack": 1, "release": 100}, "panned.wav")
    late = slice(22050, 44100)
    rms = lambda s: float(np.sqrt(np.mean(s ** 2)))
    left = 20 * math.log10(rms(y[late, 0]) / rms(x[late, 0]))
    right = 20 * math.log10(rms(y[late, 1]) / rms(x[late, 1]))
    assert left < -10 and right == pytest.approx(left, abs=0.3)


def _times(bench, rate, attack, release):
    """ms until 63% of the reduction is reached after a jump up, and until it is down to 37% after a jump back."""
    np = bench["np"]
    seg = [(-30, rate // 2), (-6, rate // 2), (-30, rate)]
    sig = np.concatenate([10 ** (db / 20) * np.sin(2 * np.pi * 1000 * np.arange(n) / rate) for db, n in seg])
    bench["write"]("step%d.wav" % rate, sig, rate=rate)
    y = bench["run"]({"thresh": -30, "ratio": 20, "attack": attack, "release": release}, "step%d.wav" % rate)[:, 0]
    ms = rate // 1000
    env = lambda s: np.array([np.abs(s[i:i + ms]).max() for i in range(0, len(s) - ms, ms)])
    g = 20 * np.log10(env(y) / env(sig[:len(y)]))
    up, down = g[500:1000], g[1000:2000]
    return int(np.argmax(up < up[-1] * 0.632)), int(np.argmax(down > down[0] * 0.368)), float(up[-1])


@needs_compiler
def test_attack_and_release_are_milliseconds_at_any_sample_rate(bench):
    slow = _times(bench, 44100, 10, 200)
    fast = _times(bench, 44100, 1, 50)
    assert slow[2] < -15 and fast[2] < -15             # it does turn the loud part down
    assert fast[0] < slow[0] and fast[1] < slow[1]     # a shorter time is quicker
    assert 4 <= slow[0] <= 16 and 250 <= slow[1] <= 550
    # slide~ counts samples; mstosamps~ is what makes the dials milliseconds wherever it runs
    high = _times(bench, 96000, 10, 200)
    assert abs(high[0] - slow[0]) <= 1 and abs(high[1] - slow[1]) <= max(3, slow[1] // 50)


def test_a_device_with_a_compressor_asks_for_the_runtime_that_has_one():
    def program(settings):
        result = fxgen.run(settings, want_amxd=False)
        return plugin.patchc.compile_program(result["json"], result["settings"]["name"], result["settings"]).splitlines()
    comp = program({"name": "C", "chain": [{"module": "comp", "on": True}]})
    other = program({"name": "D", "chain": [{"module": "eq", "on": True}]})
    assert "runtime 3" in comp and "runtime 1" in other
    assert any(line.endswith(" Slide") for line in comp) and any(line.startswith("f atodb ") for line in comp)


def test_the_compressor_builds_from_objects_every_side_knows():
    result = fxgen.run({"name": "C", "chain": [{"module": "comp", "on": True}]}, want_amxd=True)
    cords = result["coverage"]["cords"]
    assert cords["flagged"] == 0 and not result["warnings"] and result["amxd"]
    texts = [b["box"].get("text", "") for b in result["json"]["patcher"]["boxes"]]
    for name in ("slide~", "atodb~", "dbtoa~", "mstosamps~"):
        assert any(t.split()[:1] == [name] for t in texts), name
    # the gain is multiplied in by `*~ 0.`: the browser adds an argument to a signal in that inlet
    assert sum(t.split()[0:1] == ["*~"] and len(t.split()) == 2 and float(t.split()[1]) == 0 for t in texts) >= 2
