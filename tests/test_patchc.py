"""
The patch compiler (src/plugin/patchc.py) and the C++ it writes (native/).

Three layers, each needing more than the last:

  - patchc itself, in plain Python: every preset compiles, and a patch it cannot model
    is an error that says which box, not a plugin with something missing.
  - the generated C++, which needs a C++17 compiler (skipped without one): every test in
    tests/reference/browser.json is built and rendered, and its level has to follow the
    browser engine's rendering of the same patch. That file is the browser's output on
    scripts/maxcheck's signal, boiled down to levels per tenth of a second; the browser
    engine is in turn what scripts/maxcheck checks against Max.
  - the plugin around it is checked where it is built: `npm run plugin` runs
    native/test/hostcheck.cpp against the .clap it has just made.
"""

import importlib.util
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAXCHECK = ROOT / "scripts" / "maxcheck"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


plugin = _load("plugin_cli", ROOT / "scripts" / "plugin.py")
fxgen, patchc = plugin.fxgen, plugin.patchc

PRESETS = plugin.PRESETS
REFERENCE = json.loads((ROOT / "tests" / "reference" / "browser.json").read_text())
REF_TESTS = {t["name"]: t for t in REFERENCE["tests"]}


def preset(preset_id):
    return next(p for p in PRESETS if p["id"] == preset_id)["settings"]


def patch_of(settings):
    return fxgen.run(settings, want_amxd=False)["json"]


# ── the compiler ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("entry", PRESETS, ids=[p["id"] for p in PRESETS])
def test_every_preset_compiles(entry):
    patch = patch_of(entry["settings"])
    source = patchc.compile_patch(patch, entry["name"])
    dials = [b["box"] for b in patch["patcher"]["boxes"] if b["box"]["maxclass"] == "live.dial"]
    assert "static constexpr int kNumParams = %d;" % len(dials) in source
    for dial in dials:
        saved = dial["saved_attribute_attributes"]["valueof"]
        assert json.dumps(saved["parameter_longname"]) in source
    # every signal object of the patch is a line of process(), named after its box
    for box in (b["box"] for b in patch["patcher"]["boxes"]):
        if box["maxclass"] == "newobj" and box["text"].split()[0] in patchc.SIGNAL:
            if box["text"].split()[0] not in ("plugin~", "plugout~"):
                assert "_%s" % box["id"].replace("obj-", "") in source, box["text"]


def test_the_presets_use_every_object_patchc_knows():
    used = set()
    for entry in PRESETS:
        used |= set(patchc.objects(patch_of(entry["settings"])))
    assert used == set(patchc.SUPPORTED)


def _with_box(patch, text, numinlets=1, numoutlets=1):
    patch = json.loads(json.dumps(patch))
    patch["patcher"]["boxes"].append({"box": {"id": "obj-9001", "maxclass": "newobj", "text": text,
                                              "numinlets": numinlets, "numoutlets": numoutlets}})
    return patch


def _ids(patch, text_starts):
    return [b["box"]["id"] for b in patch["patcher"]["boxes"] if b["box"].get("text", "").startswith(text_starts)]


def _cord(patch, src, outlet, dst, inlet):
    patch["patcher"]["lines"].append({"patchline": {"source": [src, outlet], "destination": [dst, inlet]}})


def test_an_unknown_object_is_an_error_naming_it():
    patch = _with_box(patch_of(preset("tube")), "lores~ 800. 0.5")
    with pytest.raises(patchc.CompileError) as err:
        patchc.compile_patch(patch)
    assert "lores~" in str(err.value) and "obj-9001" in str(err.value)


def test_a_loop_with_no_delay_in_it_is_an_error():
    patch = patch_of(preset("tube"))
    tanh = _ids(patch, "tanh~")[0]
    gain = _ids(patch, "*~")[0]            # the drive gain, which feeds tanh~
    _cord(patch, tanh, 0, gain, 0)
    with pytest.raises(patchc.CompileError, match="no tapin~ / tapout~ delay"):
        patchc.compile_patch(patch)


def test_a_cord_patchc_does_not_model_is_an_error():
    patch = patch_of(preset("tube"))
    dial = next(b["box"]["id"] for b in patch["patcher"]["boxes"] if b["box"]["maxclass"] == "live.dial")
    # a number into the right inlet of a control object
    with pytest.raises(patchc.CompileError, match="left inlet"):
        cold = json.loads(json.dumps(patch))
        _cord(cold, dial, 0, _ids(cold, "pow")[0], 1)
        patchc.compile_patch(cold)
    # a signal into an inlet that is only ever set by number
    with pytest.raises(patchc.CompileError, match="only sets by number"):
        sig = json.loads(json.dumps(patch))
        _cord(sig, _ids(sig, "tanh~")[0], 0, _ids(sig, "onepole~")[0], 1)
        patchc.compile_patch(sig)
    # a number into a signal-only inlet
    with pytest.raises(patchc.CompileError, match="does not model"):
        num = json.loads(json.dumps(patch))
        _cord(num, dial, 0, _ids(num, "tanh~")[0], 0)
        patchc.compile_patch(num)


def test_an_effect_needs_one_input_and_one_output():
    patch = _with_box(patch_of(preset("tube")), "plugin~", 2, 2)
    with pytest.raises(patchc.CompileError, match="one plugin~"):
        patchc.compile_patch(patch)


def test_int_boxes_drop_the_fraction_as_max_does():
    class FakeBox(patchc.Box):
        def __init__(self, text):
            super().__init__({"id": "obj-1", "maxclass": "newobj", "text": text}, 0)

    x = patchc.Num("x")
    assert patchc.CONTROL["*"](FakeBox("* 0.5"), x).expr == "x * 0.5"
    assert patchc.CONTROL["*"](FakeBox("* 2"), x).expr == "mxrt::toInt(mxrt::toInt(x) * 2.0)"
    assert patchc.CONTROL["!-"](FakeBox("!- 1."), x).expr == "1.0 - x"
    assert patchc.CONTROL["pack"](FakeBox("pack 0. 80"), x).items == ["x", "80.0"]
    with pytest.raises(patchc.CompileError, match="whole-number"):
        patchc.CONTROL["scale"](FakeBox("scale 0 100 0 1"), x)


def test_only_the_tap_that_closes_a_loop_is_read_early():
    # Two lines, each feeding itself: both taps are read before their line is written.
    stereo = patchc.compile_patch(patch_of(preset("slapback")))
    assert stereo.count(".readInLoop(") == 2 and ".read(" not in stereo
    # Ping-pong is one loop through both lines. Reading one tap early opens it, so the
    # other is read after its line is written and both hops are the full delay time.
    pingpong = patchc.compile_patch(patch_of(preset("pingpong")))
    assert pingpong.count(".readInLoop(") == 1 and pingpong.count(".read(") == 1
    # A chorus has no loop: its taps are plain reads.
    chorus = patchc.compile_patch(patch_of(preset("wide")))
    assert ".readInLoop(" not in chorus and chorus.count(".read(") == 2


def test_parameters_keep_their_range_unit_and_curve():
    source = patchc.compile_patch(patch_of(preset("sweep")), "Sweep")
    # id, name, short name, min, max, initial, exponent, unit (3 = Hz), integer
    assert '"Filter Cutoff", "Cutoff", 40.0, 10000.0,' in source
    line = next(l for l in source.splitlines() if '"Filter Cutoff"' in l)
    assert line.rstrip().endswith("3.0, 3, false},")
    crush = patchc.compile_patch(patch_of(preset("arcade")), "Arcade")
    assert next(l for l in crush.splitlines() if '"Crush Bits"' in l).rstrip().endswith("1.0, 0, true},")
    # ids come from the dial's varname, so they survive the chain being reordered
    assert "0x%08Xu" % patchc.fnv1a("filter_cutoff") in source


# ── the generated C++ ───────────────────────────────────────────────────────────

needs_compiler = pytest.mark.skipif(plugin.compiler() is None, reason="no C++ compiler on this machine")

TOLERANCE_DB = 0.5   # measured: 0.3 dB at worst, on the chain with every module on
# Windows where both renderings are quieter than this are not compared. It sits above the
# one thing that legitimately differs: Blown Speaker's crusher clicks once in the silence
# after a sound, at -62 dB, at a moment that depends on the last bits of the arithmetic
# (scripts/maxcheck/README.md).
AUDIBLE_DB = -55.0


@pytest.fixture(scope="session")
def rendered(tmp_path_factory):
    """Every reference test, compiled and rendered: name -> path of its 16-bit WAV."""
    pytest.importorskip("numpy")
    work = tmp_path_factory.mktemp("native")
    signal = work / "signal.wav"
    subprocess.run([sys.executable, str(MAXCHECK / "make_signal.py"), str(signal)], check=True, capture_output=True)
    # numpy's sine and noise can differ in the last place from one machine to the next, so
    # the signal is held to the one the reference was rendered from by level, not by bytes.
    import numpy as np
    sys.path.insert(0, str(MAXCHECK))
    import reference
    if np.abs(np.array(reference.profile(str(signal))) - np.array(REFERENCE["signal"])).max() > 2:
        pytest.skip("make_signal.py no longer writes the signal the reference was rendered from; "
                    "render the reference again, see scripts/maxcheck/README.md")
    made = [(t["name"], plugin.generate(t["settings"], work / t["name"])) for t in REFERENCE["tests"]]

    def one(item):
        name, m = item
        out = work / (name + ".wav")
        plugin.render(plugin.build_renderer(m["dir"]), signal, out)
        return name, out

    with ThreadPoolExecutor() as pool:
        paths = dict(pool.map(one, made))
    paths["__signal__"] = signal
    paths["__work__"] = work
    return paths


@needs_compiler
@pytest.mark.parametrize("name", list(REF_TESTS))
def test_the_cpp_follows_the_browser_engine(rendered, name):
    import numpy as np
    sys.path.insert(0, str(MAXCHECK))
    import reference

    want = np.array(REF_TESTS[name]["levels"]) / 100.0
    got = np.array(reference.profile(str(rendered[name]))) / 100.0
    assert got.shape == want.shape
    audible = np.maximum(want, got) > AUDIBLE_DB
    assert audible.any(), "the reference has no signal in it"
    worst = float(np.abs(want - got)[audible].max())
    assert worst <= TOLERANCE_DB, "level differs from the browser engine's by %.2f dB" % worst


@needs_compiler
def test_with_nothing_switched_on_the_signal_passes_untouched(rendered):
    import numpy as np

    def pcm(path):
        return np.frombuffer(path.read_bytes()[44:], dtype="<i2").astype(int)

    # Read as n / 32768 and written as n * 32767, as the browser does, so a loud sample
    # can come back one step smaller. Nothing else may change.
    assert np.abs(pcm(rendered["init"]) - pcm(rendered["__signal__"])).max() <= 1


@needs_compiler
@pytest.mark.parametrize("name", ["chain_all", "delay_ping", "preset_tape"])
def test_the_block_size_does_not_change_the_sound(rendered, name):
    renderer = rendered["__work__"] / name / ("render.exe" if sys.platform == "win32" else "render")
    out = rendered["__work__"] / (name + "_blocks.wav")
    for block in (1, 4096):
        plugin.render(renderer, rendered["__signal__"], out, block=block)
        assert out.read_bytes() == rendered[name].read_bytes()


@needs_compiler
def test_the_compiled_effect_reports_its_dials(rendered):
    renderer = rendered["__work__"] / "preset_sweep" / ("render.exe" if sys.platform == "win32" else "render")
    table = json.loads(subprocess.run([str(renderer), "--params"], check=True, capture_output=True, text=True).stdout)
    assert table["name"] == "Resonant Low-Pass" and table["hasTail"] is True
    cutoff = table["params"][0]
    want = preset("sweep")["chain"][0]["values"]["cutoff"]
    assert (cutoff["name"], cutoff["min"], cutoff["max"], cutoff["exponent"]) == ("Filter Cutoff", 40, 10000, 3)
    assert cutoff["initial"] == want and cutoff["text"].endswith("Hz")


@needs_compiler
def test_setting_a_parameter_is_the_same_as_building_it_in(rendered, tmp_path):
    """Output gain at -24 dB set through setParam() equals the patch generated at -24 dB."""
    renderer = rendered["__work__"] / "init" / ("render.exe" if sys.platform == "win32" else "render")
    out = tmp_path / "quiet.wav"
    plugin.render(renderer, rendered["__signal__"], out, values={0: -24})
    assert out.read_bytes() == rendered["out_m24"].read_bytes()
