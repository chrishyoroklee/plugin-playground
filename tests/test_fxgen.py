"""
The plugin generator (src/plugin/fxgen.py), run natively.

The page runs this same file under Pyodide. These tests are what stand behind its claim
that the device you download is a patch maxpylang built and checked: every preset must
build, pass the type checks with nothing flagged and nothing unjudged, and survive the
round trip through the .amxd container.
"""

import importlib.util
import json
from pathlib import Path

import pytest

import maxpylang as mp

PLUGIN = Path(__file__).resolve().parents[1] / "src" / "plugin"

spec = importlib.util.spec_from_file_location("fxgen", PLUGIN / "fxgen.py")
fxgen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fxgen)

SCHEMA = json.loads((PLUGIN / "schema.json").read_text(encoding="utf-8"))
PRESETS = json.loads((PLUGIN / "presets.json").read_text(encoding="utf-8"))
INSTRUMENTS = json.loads((PLUGIN / "instruments.json").read_text(encoding="utf-8"))


def everything_on():
    settings = fxgen.default_settings()
    for entry in settings["chain"]:
        entry["on"] = True
    return settings


def boxes(result, maxclass=None):
    out = [b["box"] for b in result["json"]["patcher"]["boxes"]]
    return [b for b in out if b["maxclass"] == maxclass] if maxclass else out


def dial_names(result):
    return [b["saved_attribute_attributes"]["valueof"]["parameter_longname"] for b in boxes(result, "live.dial")]


def unchecked(cords):
    """
    The cords maxpylang could not check, other than the pictures'. A picture is a v8ui,
    which maxpylang does not know yet: fxgen places it as a jsui, and a jsui has no type
    data, so the one cord out of each picture goes unchecked. Nothing else may.
    """
    return [why for why in cords["not_checked_because"] if not why[0].startswith("jsui ")]


@pytest.fixture(scope="module")
def full():
    return fxgen.run(everything_on())


@pytest.mark.parametrize("preset", PRESETS, ids=[p["id"] for p in PRESETS])
def test_every_preset_builds_and_checks_clean(preset):
    result = fxgen.run(preset["settings"])
    cords = result["coverage"]["cords"]
    assert cords["flagged"] == 0
    assert unchecked(cords) == []
    assert cords["checked"] + cords["not_checked"] == cords["total"] > 0
    assert all(name.startswith("jsui ") for name in result["coverage"]["objects"]["not_seen_into"])
    assert result["warnings"] == []  # an unknown object would be an UnknownObjectWarning here
    # The script saves with verbose=False, so all maxpylang prints is its check report.
    report = [line for line in result["log"].splitlines() if line]
    # (and, under its last line, which objects it could not see into: the pictures)
    assert all(line.startswith("PatchCheck:") or line.strip().startswith("not seen into: jsui ") for line in report), result["log"]
    assert "no unknown objects" in result["log"]


@pytest.mark.parametrize("preset", PRESETS, ids=[p["id"] for p in PRESETS])
def test_every_preset_is_a_loadable_device(preset, tmp_path):
    import base64

    result = fxgen.run(preset["settings"])
    path = tmp_path / result["filename"]
    path.write_bytes(base64.b64decode(result["amxd"]))
    data = path.read_bytes()
    assert data[:4] == b"ampf" and data[8:12] == b"aaaa"  # an audio effect
    loaded = mp.load_amxd(str(path))
    assert loaded == result["json"]
    # and maxpylang can open what it wrote
    again = mp.MaxPatch(load_file=str(path), verbose=False)
    assert again.num_objs == len(boxes(result))


def test_presets_cover_every_module_and_are_distinct():
    used = {entry["module"] for p in PRESETS for entry in p["settings"]["chain"]}
    assert used == {m["id"] for m in SCHEMA["modules"]}
    assert len({p["id"] for p in PRESETS}) == len(PRESETS) >= 28
    assert len({p["name"] for p in PRESETS}) == len(PRESETS)


def test_presets_are_filed_under_the_effect_they_are():
    """The page lists presets by effect. Each section has a few, and each preset in a
    section named after a module actually uses that module."""
    sections = {}
    for p in PRESETS:
        sections.setdefault(p["group"], []).append(p)
    assert list(sections) == ["Distortion", "Lo-Fi", "Filter", "EQ", "Dynamics", "Chorus", "Tremolo", "Delay", "Reverb", "Motion", "Start"]
    by_label = {m["label"]: m["id"] for m in SCHEMA["modules"]}
    for name, presets in sections.items():
        if name == "Start":
            continue
        assert len(presets) >= 4, name
        if name in by_label and name != "EQ":   # EQ is a family: see below
            for p in presets:
                assert by_label[name] in [e["module"] for e in p["settings"]["chain"]], p["id"]
    # EQ is a family: the section holds every kind of it, and nothing that is not one.
    kinds = {"eq", "peq", "geq", "tilt"}
    used = [{e["module"] for e in p["settings"]["chain"]} for p in sections["EQ"]]
    assert all(u & kinds for u in used) and set().union(*used) >= kinds
    # and the reverbs are not all one space
    spaces = {e["options"].get("space", "hall") for p in sections["Reverb"] for e in p["settings"]["chain"] if e["module"] == "reverb"}
    assert spaces == {"room", "hall", "plate"}


def test_presets_stay_inside_the_dials():
    """A preset value outside its dial's range would be clamped without a word."""
    for p in PRESETS:
        normal = {e["module"]: e for e in fxgen.normalize(p["settings"])["chain"]}
        for entry in p["settings"]["chain"]:
            for key, value in entry["values"].items():
                assert normal[entry["module"]]["values"][key] == value, (p["id"], key)
            for key, value in entry["options"].items():
                assert normal[entry["module"]]["options"][key] == value, (p["id"], key)


def test_frequency_dials_stay_inside_what_max_accepts():
    """svf~ and onepole~ take cutoffs up to a quarter of the sample rate (their reference
    pages say so). Past that the dial would turn and nothing would change in Live."""
    for mod in SCHEMA["modules"]:
        for param in mod["params"]:
            if param["unit"] == "Hz" and (mod["id"], param["id"]) != ("chorus", "rate") and param["max"] > 100:
                assert param["max"] <= 44100 / 4, (mod["id"], param["id"])
    drive = next(m for m in SCHEMA["modules"] if m["id"] == "drive")
    assert next(p for p in drive["params"] if p["id"] == "amount")["max"] <= 10  # overdrive~'s range


def test_device_shape(full):
    patcher = full["json"]["patcher"]
    assert patcher["openinpresentation"] == 1
    texts = [b.get("text", "") for b in boxes(full, "newobj")]
    assert texts.count("plugin~") == 1 and texts.count("plugout~") == 1
    assert "ezdac~" not in texts and "dac~" not in texts
    # the project's rule: clip~ -1. 1. on each channel before plugout~
    assert sum(t.startswith("clip~ -1") for t in texts) >= 2
    shown = [b for b in boxes(full) if b.get("presentation") == 1]
    assert patcher["devicewidth"] >= max(b["presentation_rect"][0] + b["presentation_rect"][2] for b in shown)
    assert all(b["presentation_rect"][1] + b["presentation_rect"][3] <= 169 for b in shown)


def test_every_dial_is_a_live_parameter(full):
    dials = boxes(full, "live.dial")
    expected = sum(len(m["params"]) for m in SCHEMA["modules"]) + len(SCHEMA["output"]["params"])
    assert len(dials) == expected
    names = dial_names(full)
    assert len(set(names)) == len(names), "Live needs every parameter name to be unique"
    for d in dials:
        p = d["saved_attribute_attributes"]["valueof"]
        assert d["presentation"] == 1 and d["parameter_enable"] == 1
        assert p["parameter_mmin"] < p["parameter_mmax"]
        assert p["parameter_initial_enable"] == 1
        assert p["parameter_mmin"] <= p["parameter_initial"][0] <= p["parameter_mmax"]


def test_dials_do_not_overlap_in_the_device_view(full):
    rects = sorted(b["presentation_rect"] for b in boxes(full, "live.dial"))
    for a, b in zip(rects, rects[1:]):
        assert a[0] + a[2] <= b[0], (a, b)


def test_comments_say_what_they_were_given(full):
    words = [b["text"] for b in boxes(full) if b["maxclass"] in ("comment", "live.comment")]
    assert "=== FILTER ===" in words and "FILTER" in words
    assert not any(w.startswith(("comment", "live.comment")) for w in words)


def test_chain_order_is_signal_order():
    settings = fxgen.normalize({"chain": [{"module": "reverb"}, {"module": "filter"}]})
    src = fxgen.script(settings)
    assert src.index("# ── Reverb") < src.index("# ── Filter")
    assert "reverb -> filter -> output gain" in src
    # the filter reads the reverb's output, not the input
    assert "[reverb_outL.outs[0], filter_inL.ins[0]]" in src


def test_options_change_the_patch():
    def texts(chain):
        return [b.get("text", "") for b in boxes(fxgen.run({"chain": chain}), "newobj")]

    soft = texts([{"module": "drive", "options": {"character": "soft"}}])
    hard = texts([{"module": "drive", "options": {"character": "hard"}}])
    assert any(t.startswith("overdrive~") for t in soft) and not any(t.startswith("overdrive~") for t in hard)
    # each distortion type is built on its own Max object
    shaper = {"soft": "overdrive~", "tube": "tanh~", "hard": "clip~ -1", "fuzz": "clip~ -0.35", "fold": "pong~ 0"}
    for kind, obj in shaper.items():
        made = texts([{"module": "drive", "options": {"character": kind}}])
        assert any(t.startswith(obj) for t in made), kind
        assert any(t.startswith("pow ") for t in made), "the makeup gain follows the Drive dial"
    # a flanger is a swept comb~; a chorus is a tapin~ / tapout~ pair
    flanger = texts([{"module": "chorus", "options": {"voice": "flanger"}}])
    chorus = texts([{"module": "chorus", "options": {"voice": "chorus"}}])
    assert any(t.startswith("comb~") for t in flanger) and not any(t.startswith("tapin~") for t in flanger)
    assert any(t.startswith("tapin~") for t in chorus) and not any(t.startswith("comb~") for t in chorus)
    # the delay time reaches the taps as a gliding signal, and the loop saturates
    delay = texts([{"module": "delay"}])
    assert any(t.startswith("line~") for t in delay) and "tanh~" in delay
    lowpass = fxgen.script({"chain": [{"module": "filter", "options": {"mode": "lowpass"}}]})
    notch = fxgen.script({"chain": [{"module": "filter", "options": {"mode": "notch"}}]})
    assert "filter_L.outs[0]" in lowpass and "filter_L.outs[3]" in notch


def test_level_matching_constants():
    """The numbers the generator bakes in as starting values are the ones its control
    objects compute, so the device sounds the same before and after a dial is touched."""
    assert fxgen.drive_makeup("soft", 4) == pytest.approx(0.5)
    assert fxgen.drive_makeup("soft", 1) == 1.0
    assert fxgen.drive_makeup("fuzz", 10) < fxgen.drive_makeup("fuzz", 1)
    # a longer reverb is not a louder one: the comb sum is scaled down as feedback rises
    assert fxgen.reverb_level(fxgen.reverb_feedback(100)) < fxgen.reverb_level(fxgen.reverb_feedback(0))
    assert fxgen.reverb_feedback(100) < 1.0
    # the Reso dial covers the part of svf~'s range that is neither flat nor ringing forever
    assert 0.15 < fxgen.svf_resonance(0) < fxgen.svf_resonance(100) <= 0.8
    assert fxgen.filter_trim(100) == 0.5


def test_a_name_used_twice_in_the_script_is_an_error():
    """Two objects under one variable would silently rewire every cord of the first.
    (It happened: a control object took the Reso dial's name and the dial fed nothing.)"""
    import re

    names = re.findall(r"^(\w+) = (?:place|dial)\(", fxgen.script(everything_on()), re.M)
    assert len(names) == len(set(names)) > 80
    emitter = fxgen.Script()
    emitter.place("twice", "cycle~", 0, 0)
    with pytest.raises(ValueError):
        emitter.place("twice", "cycle~", 0, 0)


def test_the_script_is_the_program_that_ran(tmp_path, monkeypatch):
    """Exec the emitted source on its own: it must write the same device run() reports."""
    result = fxgen.run(PRESETS[0]["settings"], want_amxd=False)
    monkeypatch.chdir(tmp_path)
    # with the pictures' drawing code handed in, as run() hands it in
    exec(compile(result["script"], "script.py", "exec"), {"__name__": "__main__", "GRAPH_SOURCE": fxgen.graph_source()})
    assert mp.load_amxd(str(tmp_path / result["filename"])) == result["json"]


def test_settings_are_bounded_before_they_reach_the_script():
    hostile = {
        "name": 'x"); import os; os.system("echo pwned")\n#',
        "accent": "red; }",
        "chain": [
            {"module": "filter", "values": {"cutoff": 1e12, "res": "nan"}, "options": {"mode": "__import__"}},
            {"module": "filter"},
            {"module": "no_such_module"},
            "not a dict",
        ],
        "output": {"gain": -1e9},
    }
    st = fxgen.normalize(hostile)
    assert st["name"] == "x import os ossystemecho pwned"
    assert st["accent"] == SCHEMA["accents"][0]
    assert [e["module"] for e in st["chain"]].count("filter") == 1
    filt = next(e for e in st["chain"] if e["module"] == "filter")
    assert filt["values"]["cutoff"] == 10000 and 0 <= filt["values"]["res"] <= 100
    assert filt["options"]["mode"] == "lowpass"
    assert st["output"]["gain"] == -24
    src = fxgen.script(hostile)
    assert "os.system" not in src and "__import__" not in src
    compile(src, "hostile.py", "exec")
    assert fxgen.filename(hostile).endswith(".amxd") and "/" not in fxgen.filename(hostile)
    # garbage in is the default device out
    assert fxgen.normalize(None) == fxgen.normalize({})
    assert fxgen.normalize({"name": "   "})["name"] == "MaxPy FX"


def test_named_gaps_are_the_ones_the_page_explains():
    """Every workaround the script carries names a gap in maxpylang's gap log
    (docs/type-system/m4l-gaps.md in the MaxPyLang repo), and the page has a line for it."""
    import re

    page = (PLUGIN / "main.ts").read_text(encoding="utf-8")
    named = set(re.findall(r"gap (M4L-\d+)", fxgen.script(everything_on())))
    assert named == {"M4L-01", "M4L-02", "M4L-03", "M4L-04"}
    for gap_id in named:
        assert "'%s':" % gap_id in page


# ── instruments ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("preset", INSTRUMENTS, ids=[p["id"] for p in INSTRUMENTS])
def test_every_instrument_builds_checks_clean_and_loads(preset, tmp_path):
    import base64

    result = fxgen.run(preset["settings"])
    cords = result["coverage"]["cords"]
    assert cords["flagged"] == 0 and unchecked(cords) == []
    assert cords["checked"] + cords["not_checked"] == cords["total"] > 0
    assert result["warnings"] == []

    data = base64.b64decode(result["amxd"])
    assert data[:4] == b"ampf" and data[8:12] == b"iiii"  # an instrument, not an audio effect
    path = tmp_path / result["filename"]
    path.write_bytes(data)
    assert mp.load_amxd(str(path)) == result["json"]


def test_an_instrument_takes_notes_and_has_one_voice_per_setting():
    for voices in ("1", "4", "8"):
        settings = fxgen.default_settings("instrument")
        settings["instrument"]["osc"]["options"]["voices"] = voices
        result = fxgen.run(settings, want_amxd=False)
        texts = [b.get("text", "") for b in boxes(result)]
        assert "notein" in texts and "plugin~" not in texts and "plugout~" in texts
        assert "poly %s 1" % voices in texts
        assert len([t for t in texts if t.startswith("adsr~")]) == int(voices)


def test_instrument_dials_are_the_schemas():
    result = fxgen.run(fxgen.default_settings("instrument"), want_amxd=False)
    want = ["%s %s" % (m["label"], p["label"]) for m in SCHEMA["instrument"]["modules"] for p in m["params"]]
    assert dial_names(result) == want + ["Output Gain"]


def test_instrument_settings_are_bounded_like_any_other():
    st = fxgen.normalize({"kind": "instrument", "instrument": {"osc": {"options": {"wave": "nope", "voices": "99"},
                                                               "values": {"detune": 1e9}}}})
    assert st["kind"] == "instrument" and st["name"] == "MaxPy Synth"
    assert st["instrument"]["osc"]["options"] == {"wave": "saw", "second": "unison", "voices": "6"}
    assert st["instrument"]["osc"]["values"]["detune"] == 50
    assert "instrument" not in fxgen.normalize({}) and "kind" not in fxgen.normalize({})


# ── motion: what moves the dials ────────────────────────────────────────────────

MOVING = {
    "name": "Moving",
    "chain": [{"module": "filter", "on": True}, {"module": "drive", "on": True}],
    "motion": {
        "lfo1": {"options": {"shape": "triangle"}, "values": {"rate": 2}},
        "knobs": [{"id": "a", "name": "Wobble!"}, {"id": "b", "name": "wobble"}],
        "knobValues": {"a": 30},
        "routes": [
            {"source": "lfo1", "target": "filter.cutoff", "amount": 30},
            {"source": "a", "target": "filter.cutoff", "amount": 50},
            {"source": "follow", "target": "drive.amount", "amount": 60},
        ],
    },
}


def test_a_device_with_no_motion_has_the_settings_it_always_had():
    """Nothing about motion is written unless something is routed or a knob exists."""
    assert "motion" not in fxgen.normalize({"chain": [{"module": "filter", "on": True}]})
    assert "motion" not in fxgen.normalize({"motion": {"lfo1": {"values": {"rate": 9}}, "routes": []}})


def test_motion_is_bounded_like_everything_else():
    m = fxgen.normalize(dict(MOVING, motion=dict(MOVING["motion"], routes=MOVING["motion"]["routes"] + [
        {"source": "lfo9", "target": "filter.cutoff", "amount": 10},     # no such source
        {"source": "lfo1", "target": "filter.nothing", "amount": 10},    # no such dial
        {"source": "lfo1", "target": "crush.bits", "amount": 10},        # a whole-number dial is not moved
        {"source": "lfo1", "target": "filter.cutoff", "amount": 99},     # the same route twice
        {"source": "lfo2", "target": "filter.res", "amount": 0},         # moves nothing
        {"source": "lfo2", "target": "filter.res", "amount": 900},       # too far
    ])))["motion"]
    assert [(r["source"], r["target"], r["amount"]) for r in m["routes"]] == [
        ("lfo1", "filter.cutoff", 30.0), ("k1", "filter.cutoff", 50.0), ("follow", "drive.amount", 60.0),
        ("lfo2", "filter.res", 100.0)]
    # knobs are renumbered, their names made safe and different, their values carried over
    assert m["knobs"] == [{"id": "k1", "name": "Wobble"}, {"id": "k2", "name": "wobble 2"}]
    assert m["knobValues"] == {"k1": 30.0, "k2": 0.0}
    assert m["lfo1"]["options"]["shape"] == "triangle" and m["lfo2"]["values"]["rate"] == 6.0


def test_an_instrument_has_no_input_to_follow():
    st = fxgen.normalize({"kind": "instrument", "motion": {"routes": [
        {"source": "follow", "target": "tone.cutoff", "amount": 40}, {"source": "lfo1", "target": "tone.cutoff", "amount": 40}]}})
    assert [r["source"] for r in st["motion"]["routes"]] == ["lfo1"]


def test_a_moved_dial_is_worked_out_under_the_dial_and_checks_clean():
    result = fxgen.run(MOVING, want_amxd=False)
    texts = [str(b["box"].get("text", "")) for b in result["json"]["patcher"]["boxes"]]
    # one snapshot~ per moved dial; the LFO, the follower and the knob as signals
    assert sum(t.startswith("snapshot~") for t in texts) == 2
    assert any(t.startswith("tri~") for t in texts) and "abs~" in texts
    cords = result["coverage"]["cords"]
    assert cords["flagged"] == 0 and unchecked(cords) == [] and not result["warnings"]
    # the dials: the two modules', the output's, and one each for the LFO, the follower and the knob in use
    dials = [b["box"]["varname"] for b in result["json"]["patcher"]["boxes"] if b["box"].get("maxclass") == "live.dial"]
    assert {"sweep_speed", "loudness_sensitivity", "knobs_wobble"} <= set(dials) and "knobs_wobble_2" not in dials


def test_motion_aimed_at_a_module_that_is_off_builds_nothing():
    st = dict(MOVING, motion={"routes": [{"source": "lfo1", "target": "reverb.mix", "amount": 50}]})
    texts = [str(b["box"].get("text", "")) for b in fxgen.run(st, want_amxd=False)["json"]["patcher"]["boxes"]]
    assert not any(t.startswith(("snapshot~", "cycle~", "sig~")) for t in texts)
    assert fxgen.normalize(st)["motion"]["routes"], "the route is kept, for when the reverb is switched on"


# ── themes: the device's colours ────────────────────────────────────────────────

def test_the_first_theme_is_the_one_devices_always_had_and_is_not_written_down():
    assert "theme" not in fxgen.normalize({})
    assert "theme" not in fxgen.normalize({"theme": SCHEMA["themes"][0]["id"]})
    assert "theme" not in fxgen.normalize({"theme": "no such theme"})
    assert fxgen.normalize({"theme": "midnight"})["theme"] == "midnight"


@pytest.mark.parametrize("theme", [t["id"] for t in SCHEMA["themes"]])
def test_a_theme_colours_the_backdrop_the_cards_the_lettering_and_the_dials(theme):
    spec = next(t for t in SCHEMA["themes"] if t["id"] == theme)
    result = fxgen.run({"theme": theme, "chain": [{"module": "filter", "on": True}]}, want_amxd=False)
    boxes = [b["box"] for b in result["json"]["patcher"]["boxes"]]
    rgb = lambda colour: [round(c * 255) for c in colour[:3]]
    hexed = lambda text: [int(text[i:i + 2], 16) for i in (1, 3, 5)]
    width = result["json"]["patcher"]["devicewidth"]
    panels = [b for b in boxes if b.get("maxclass") == "panel"]
    # the backdrop is the first panel, so it is at the back, and it is as wide as the device
    assert panels[0]["presentation_rect"] == [0.0, 0.0, float(width), 169.0] and rgb(panels[0]["bgcolor"]) == hexed(spec["bg"])
    assert all(rgb(p["bgcolor"]) == hexed(spec["card"]) for p in panels[1:])
    for dial in (b for b in boxes if b.get("maxclass") == "live.dial"):
        assert rgb(dial["textcolor"]) == hexed(spec["ink"]) and rgb(dial["dialcolor"]) == hexed(spec["track"])
    assert result["coverage"]["cords"]["flagged"] == 0


def test_the_reverb_is_three_spaces_and_the_hall_is_the_one_it_always_was():
    """Room, hall and plate are the same layout with other comb and allpass times."""
    def combs(space):
        made = fxgen.run({"name": "Space", "chain": [{"module": "reverb", "on": True, "options": {"space": space}}]}, want_amxd=False)
        texts = [b["box"].get("text", "") for b in made["json"]["patcher"]["boxes"]]
        return sorted(float(t.split()[2]) for t in texts if t.startswith("comb~")), [t for t in texts if t.startswith("scale ")]
    hall, hall_scale = combs("hall")
    assert hall[:8:2] == pytest.approx(sorted(fxgen.COMBS)[:4]) and [float(x) for x in hall_scale[0].split()[1:]] == [0.0, 100.0, 0.7, 0.97]
    room, plate = combs("room")[0], combs("plate")[0]
    assert len(room) == len(hall) == len(plate) == 16
    assert max(room) < min(hall) and max(plate) < min(hall) and room != plate
    # an unknown space is the hall, as an unknown anything is its default
    assert fxgen.normalize({"chain": [{"module": "reverb", "options": {"space": "cave"}}]})["chain"][0]["options"]["space"] == "hall"
    # every space stops short of ringing for ever
    assert all(fxgen.reverb_feedback(100, s) < 1.0 for s in fxgen.SPACES)


def test_every_compressor_preset_is_in_dynamics():
    dynamics = [p for p in PRESETS if p["group"] == "Dynamics"]
    assert len(dynamics) >= 4 and all(any(e["module"] == "comp" for e in p["settings"]["chain"]) for p in dynamics)
