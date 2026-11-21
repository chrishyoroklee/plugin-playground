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
import re
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
INSTRUMENTS = plugin.INSTRUMENTS
REFERENCE = json.loads((ROOT / "tests" / "reference" / "browser.json").read_text())
REF_TESTS = {t["name"]: t for t in REFERENCE["tests"]}


def preset(preset_id):
    return next(p for p in PRESETS + INSTRUMENTS if p["id"] == preset_id)["settings"]


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
    for entry in PRESETS + INSTRUMENTS:
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


# ── instruments ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("entry", INSTRUMENTS, ids=[p["id"] for p in INSTRUMENTS])
def test_every_instrument_compiles(entry):
    patch = patch_of(entry["settings"])
    source = patchc.compile_patch(patch, entry["name"])
    voices = int(entry["settings"]["instrument"]["osc"]["options"]["voices"])
    assert "static constexpr bool kIsInstrument = true;" in source
    assert "poly_.prepare(%d, true);" % voices in source
    # one case per voice, each setting two oscillators and opening an envelope
    assert source.count(".gate(") == voices and source.count("mxrt::mtof(") == 2 * voices
    assert "in_l" not in source   # an instrument reads no audio


def test_an_effect_is_not_an_instrument():
    source = patchc.compile_patch(patch_of(preset("tube")))
    assert "static constexpr bool kIsInstrument = false;" in source and "poly_" not in source


def test_the_note_path_has_to_be_the_one_patchc_reads():
    patch = patch_of(preset("plain-synth"))
    cut = json.loads(json.dumps(patch))
    poly, pack = _ids(cut, "poly")[0], _ids(cut, "pack 0 0 0")[0]
    cut["patcher"]["lines"] = [l for l in cut["patcher"]["lines"]
                               if not (l["patchline"]["source"] == [poly, 2] and l["patchline"]["destination"][0] == pack)]
    with pytest.raises(patchc.CompileError, match="notein -> poly -> pack 0 0 0 -> route"):
        patchc.compile_patch(cut)
    with pytest.raises(patchc.CompileError, match="cannot have a plugin~"):
        patchc.compile_patch(_with_box(patch, "plugin~", 2, 2))
    with pytest.raises(patchc.CompileError, match="one notein"):
        patchc.compile_patch(_with_box(patch, "notein", 1, 3))


def test_a_right_inlet_is_remembered_for_the_next_note():
    """The detune dial reaches each voice's `+` by its right inlet: stored, used by the next pitch."""
    source = patchc.compile_patch(patch_of(preset("plain-synth")))
    assert re.search(r"c_\d+_operand_ = c_\d+;  // \+ 0\.08, right inlet", source)
    assert re.search(r"mxrt::toInt\(pitch\) \+ c_\d+_operand_", source)


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


# ── the generated C++, played ───────────────────────────────────────────────────

def _float_wav(path):
    import numpy as np
    return np.frombuffer(path.read_bytes()[44:], dtype="<f4").reshape(-1, 2).astype(float)


def _rms(x, start, end, rate=44100):
    import numpy as np
    return float(np.sqrt((x[int(start * rate):int(end * rate)] ** 2).mean()))


@pytest.fixture(scope="session")
def instruments(tmp_path_factory):
    """Every instrument preset, compiled into its renderer: id -> the renderer's path."""
    pytest.importorskip("numpy")
    work = tmp_path_factory.mktemp("instruments")
    made = [(p["id"], plugin.generate(p["settings"], work / p["id"])) for p in INSTRUMENTS]
    with ThreadPoolExecutor() as pool:
        built = dict(pool.map(lambda item: (item[0], plugin.build_renderer(item[1]["dir"])), made))
    built["__work__"] = work
    return built


@needs_compiler
@pytest.mark.parametrize("entry", INSTRUMENTS, ids=[p["id"] for p in INSTRUMENTS])
def test_every_instrument_plays(instruments, entry):
    import numpy as np
    out = instruments["__work__"] / (entry["id"] + ".wav")
    # a chord half a second in, held for a second
    plugin.play(instruments[entry["id"]], "48@0.5+1:100,60@0.5+1:100,64@0.5+1:100,67@0.5+1:100", 4, out, as_float=True)
    y = _float_wav(out)
    assert np.isfinite(y).all() and np.abs(y).max() <= 1.0
    assert np.abs(y[:int(0.49 * 44100)]).max() == 0, "it makes sound before it is played"
    assert _rms(y, 0.6, 1.5) > 0.01, "the chord cannot be heard"


@needs_compiler
def test_a_note_is_in_tune(instruments, tmp_path):
    import numpy as np
    out = tmp_path / "a.wav"
    # Detune (0) and Blend (1) at zero leave one oscillator; Tone Env (4) at zero holds the filter still.
    plugin.play(instruments["plain-synth"], "69@0+2:100", 2, out, as_float=True, values={0: 0, 1: 0, 4: 0})
    y = _float_wav(out)[44100 // 2:, 0]
    spectrum = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    peak = np.fft.rfftfreq(len(y), 1 / 44100)[spectrum.argmax()]
    assert abs(peak - 440.0) < 1.0


@needs_compiler
def test_the_envelope_follows_its_dials_and_the_velocity(instruments, tmp_path):
    out = tmp_path / "env.wav"
    # Attack 200 ms (5), Decay 5 ms (6), Sustain 100 % (7), Release 100 ms (8); filter held still (4).
    shape = {4: 0, 5: 200, 6: 5, 7: 100, 8: 100}
    plugin.play(instruments["plain-synth"], "57@0.5+1:127", 2.5, out, as_float=True, values=shape)
    loud = _float_wav(out)[:, 0]
    assert _rms(loud, 0.5, 0.55) < 0.5 * _rms(loud, 0.8, 1.4), "the attack is not a slow rise"
    assert _rms(loud, 1.62, 2.4) < 1e-4 * _rms(loud, 0.8, 1.4), "the note rings past its release"
    plugin.play(instruments["plain-synth"], "57@0.5+1:64", 2.5, out, as_float=True, values=shape)
    soft = _float_wav(out)[:, 0]
    ratio = _rms(loud, 0.8, 1.4) / _rms(soft, 0.8, 1.4)
    assert abs(ratio - 127 / 64) < 0.02, "level does not follow velocity"


@needs_compiler
def test_a_mono_instrument_gives_its_voice_to_the_newest_note(instruments, tmp_path):
    import numpy as np
    out = tmp_path / "mono.wav"
    # Sub Bass has one voice. A second key pressed while the first is held takes it over.
    plugin.play(instruments["sub-bass"], "45@0+2:100,57@1+1:100", 2, out, as_float=True)
    y = _float_wav(out)[:, 0]

    def pitch(start, end):
        seg = y[int(start * 44100):int(end * 44100)]
        spectrum = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        return np.fft.rfftfreq(len(seg), 1 / 44100)[spectrum.argmax()]

    assert abs(pitch(0.3, 0.9) - 110) < 3 or abs(pitch(0.3, 0.9) - 55) < 3   # A2, or its octave-down partner
    assert abs(pitch(1.3, 1.9) - 220) < 3 or abs(pitch(1.3, 1.9) - 110) < 3   # A3 took the voice


@needs_compiler
def test_more_keys_than_voices_steals_the_oldest(instruments, tmp_path):
    out = tmp_path / "steal.wav"
    # Hollow Lead has four voices. Five keys: the first is let go to make room for the fifth,
    # so releasing the first key later changes nothing, and the result is the same as never
    # holding it past that point.
    held = "48@0+3:100,52@0.1+3:100,55@0.2+3:100,59@0.3+3:100,62@1+2:100"
    cut = "48@0+1:100,52@0.1+3:100,55@0.2+3:100,59@0.3+3:100,62@1+2:100"
    plugin.play(instruments["square-lead"], held, 2.5, out, as_float=True)
    a = out.read_bytes()
    plugin.play(instruments["square-lead"], cut, 2.5, out, as_float=True)
    assert out.read_bytes() == a
