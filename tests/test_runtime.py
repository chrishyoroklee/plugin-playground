"""
The runtime plugin against the compiled one.

A device reaches a host two ways. patchc writes it as C++ (Effect.h), which is compiled
into a plugin of its own; or patchc writes it as a program (a .ppdevice), which the
runtime plugin, compiled once, reads and runs. The page can only offer the second, so
the second has to be the first: these tests render every preset both ways and require
the same samples, not similar ones.

They need a C++ compiler and numpy, and are skipped without them.
"""

import importlib.util
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAXCHECK = ROOT / "scripts" / "maxcheck"


def _load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


plugin = _load("plugin_cli", ROOT / "scripts" / "plugin.py")
fxgen, patchc = plugin.fxgen, plugin.patchc
PRESETS, INSTRUMENTS = plugin.PRESETS, plugin.INSTRUMENTS
EVERYTHING = PRESETS + INSTRUMENTS

needs_compiler = pytest.mark.skipif(plugin.compiler() is None, reason="no C++ compiler")

# A chord, a note played over a held one, more keys than a small instrument has voices,
# and a key let go late: the note path, voice stealing and the release all get used.
NOTES = "48@0.1+0.9:100,60@0.1+0.9:90,64@0.3+0.5:127,67@0.3+0.5:60,72@0.5+0.2:100,76@0.6+0.2:100,79@0.7+0.2:100,84@0.8+1.0:1"


def _samples(path):
    import numpy as np
    data = Path(path).read_bytes()
    return np.frombuffer(data[44:], dtype="<f4")


@pytest.fixture(scope="session")
def both(tmp_path_factory):
    """Every preset, as its own compiled renderer and as a device file for the runtime's."""
    pytest.importorskip("numpy")
    work = tmp_path_factory.mktemp("runtime")
    signal = work / "signal.wav"
    subprocess.run([sys.executable, str(MAXCHECK / "make_signal.py"), str(signal)], check=True, capture_output=True)
    runtime = plugin.build_runtime_renderer(work / ("runtime-render" + (".exe" if sys.platform == "win32" else "")))
    made = {p["id"]: plugin.generate(p["settings"], work / p["id"]) for p in EVERYTHING}
    with ThreadPoolExecutor() as pool:
        compiled = dict(pool.map(lambda item: (item[0], plugin.build_renderer(item[1]["dir"])), made.items()))
    return {"work": work, "signal": signal, "runtime": runtime, "made": made, "compiled": compiled}


def _render_both(both, entry, tag, **how):
    """Render one preset through its compiled renderer and through the runtime: two arrays."""
    made = both["made"][entry["id"]]
    outs = [both["work"] / ("%s-%s-%s.wav" % (entry["id"], tag, side)) for side in ("compiled", "runtime")]
    for out, renderer, device in ((outs[0], both["compiled"][entry["id"]], None), (outs[1], both["runtime"], made["device"])):
        if made["kind"] == "instrument":
            plugin.play(renderer, NOTES, 2.5, out, as_float=True, device=device, **how)
        else:
            plugin.render(renderer, both["signal"], out, as_float=True, device=device, **how)
    return _samples(outs[0]), _samples(outs[1])


@needs_compiler
@pytest.mark.parametrize("entry", EVERYTHING, ids=[p["id"] for p in EVERYTHING])
def test_the_runtime_is_the_compiled_plugin_sample_for_sample(both, entry):
    import numpy as np
    compiled, runtime = _render_both(both, entry, "plain")
    assert len(compiled) == len(runtime) and len(compiled) > 0
    assert np.abs(compiled).max() > 0, "the compiled plugin made no sound, so the comparison says nothing"
    assert np.array_equal(compiled, runtime), "they differ by up to %g" % np.abs(compiled - runtime).max()


@needs_compiler
@pytest.mark.parametrize("entry", EVERYTHING, ids=[p["id"] for p in EVERYTHING])
def test_every_dial_does_the_same_thing_in_both(both, entry):
    """Each dial moved to a third of the way up, which no preset starts at."""
    import numpy as np
    program = both["made"][entry["id"]]["device"].read_text()
    values = {}
    for index, line in enumerate(l for l in program.splitlines() if l.startswith("param ")):
        lo, hi = float(line.split()[2]), float(line.split()[3])
        values[index] = lo + (hi - lo) / 3
    assert values, "a preset with no dials"
    compiled, runtime = _render_both(both, entry, "moved", values=values, block=37)
    assert np.array_equal(compiled, runtime), "they differ by up to %g" % np.abs(compiled - runtime).max()


# ── the device file ─────────────────────────────────────────────────────────────

def _program(preset_id="tube"):
    settings = next(p for p in EVERYTHING if p["id"] == preset_id)["settings"]
    result = fxgen.run(settings, want_amxd=False)
    return patchc.compile_program(result["json"], result["settings"]["name"], result["settings"])


def test_a_device_file_carries_its_name_its_dials_and_its_settings():
    import base64
    import json
    text = _program("tube")
    lines = text.splitlines()
    assert lines[0] == "ppdevice %d" % patchc.PROGRAM_FORMAT
    assert "name Warm%20Tube" in lines and "kind effect" in lines
    params = [l.split() for l in lines if l.startswith("param ")]
    assert [p[-1] for p in params] == ["Drive", "Tone", "Gain"]
    # the settings come back as the page saved them, so the device can be opened again
    packed = next(l for l in lines if l.startswith("settings ")).split()[1]
    assert json.loads(base64.b64decode(packed))["name"] == "Warm Tube"
    assert lines.index("@end") < len(lines) - 1


def test_an_instrument_says_how_many_voices_it_has():
    lines = _program(INSTRUMENTS[0]["id"]).splitlines()
    assert "kind instrument" in lines
    voices = int(next(l for l in lines if l.startswith("poly ")).split()[1])
    assert sum(1 for l in lines if l.startswith("@voice ")) == voices


def test_a_name_with_anything_in_it_is_still_one_word():
    result = fxgen.run(PRESETS[0]["settings"], want_amxd=False)
    text = patchc.compile_program(result["json"], "Caf\u00e9 / 100% \"wet\"")
    name = next(l for l in text.splitlines() if l.startswith("name "))
    assert len(name.split()) == 2 and "%C3%A9" in name and "%25" in name


@needs_compiler
@pytest.mark.parametrize("damage, says", [
    (lambda t: t.replace("ppdevice 1", "ppdevice 99"), "newer"),
    (lambda t: t.replace("runtime 1", "runtime 99"), "newer"),
    (lambda t: t.replace("f tanh", "f sorcery"), "newer"),
    (lambda t: t.replace("k 0 OnePole", "k 0 Wormhole"), "newer"),
    (lambda t: t[:t.index("@process")], "cut short"),
    (lambda t: "hello\n" + t, "not a Plugin Playground device"),
    (lambda t: t.replace("@process\n", "@process\n* 999999 4 8\n"), "cannot be read"),
])
def test_a_device_the_runtime_cannot_run_is_refused_with_a_reason(both, tmp_path, damage, says):
    """Never half a device: a file from a newer page, or a broken one, loads nothing and says why."""
    bad = tmp_path / "bad.ppdevice"
    bad.write_text(damage(_program("tube")))
    done = subprocess.run([str(both["runtime"]), "--device", str(bad), "--params"], capture_output=True, text=True)
    assert done.returncode != 0
    assert says in done.stderr, done.stderr
