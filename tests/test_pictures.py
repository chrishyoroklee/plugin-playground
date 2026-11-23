"""
The pictures above the dials: an EQ's curve, a delay's echoes, an envelope's shape.

fxgen puts one in each module's card as a v8ui that carries its own drawing code
(src/plugin/graphs.js, and graph-max.js for Max), joined to the module's dials both ways.
These tests hold the part Python is answerable for: that the picture is in the patch
where the card has room for it, that it knows its dials, that a device can do without
them, and that they change nothing about the sound or the plugin's parameters. What is
drawn, and what a drag does, is tested in test/graphs.test.ts.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "src" / "plugin"
sys.path.insert(0, str(PLUGIN))

import fxgen  # noqa: E402
import patchc  # noqa: E402

EFFECT = {"name": "Pictured", "chain": [
    {"module": "filter", "on": True, "options": {"mode": "highpass"}},
    {"module": "eq", "on": True, "values": {"mid": 6}},
    {"module": "delay", "on": True, "options": {"style": "pingpong"}},
]}
SYNTH = {"kind": "instrument", "name": "Pictured Synth"}


def boxes(result):
    return [b["box"] for b in result["json"]["patcher"]["boxes"]]


def lines(result):
    return [l["patchline"] for l in result["json"]["patcher"]["lines"]]


@pytest.fixture(scope="module")
def effect():
    return fxgen.run(EFFECT, want_amxd=False)


@pytest.fixture(scope="module")
def plain():
    return fxgen.run(dict(EFFECT, look="dials"), want_amxd=False)


def test_each_module_has_a_picture_in_its_card_above_its_dials(effect):
    pictures = {b["varname"]: b for b in boxes(effect) if b.get("maxclass") == "v8ui"}
    assert set(pictures) == {"filter_graph", "eq_graph", "delay_graph"}   # the output has none
    dials = [b for b in boxes(effect) if b.get("maxclass") == "live.dial"]
    for name, picture in pictures.items():
        module = name[:-len("_graph")]
        x, y, w, h = picture["presentation_rect"]
        assert picture["presentation"] == 1 and w >= fxgen.GRAPH_MIN_W and y + h <= fxgen.GRAPH_DIAL_Y
        mine = [d for d in dials if d["varname"].startswith(module + "_")]
        assert mine and all(d["presentation_rect"][1] == fxgen.GRAPH_DIAL_Y for d in mine)
        assert all(d["presentation_rect"][1] + d["presentation_rect"][3] <= fxgen.DEVICE_H - 6 for d in mine)
        # and it is inside its own card, which no other card overlaps
        assert all(x <= d["presentation_rect"][0] and d["presentation_rect"][0] + 44 <= x + w for d in mine)
    rects = sorted(b["presentation_rect"] for b in boxes(effect) if b.get("maxclass") == "panel" and b["presentation_rect"][3] == 157 and b["presentation_rect"][0] > 0)
    assert all(a[0] + a[2] <= b[0] for a, b in zip(rects, rects[1:]))


def test_a_picture_carries_its_drawing_code_and_what_it_is_a_picture_of(effect):
    picture = next(b for b in boxes(effect) if b.get("varname") == "filter_graph")
    text = picture["textfile"]["text"]
    assert picture["textfile"]["embed"] == 1 and picture["filename"] == "none" and "text" not in picture
    first, rest = text.split("\n", 1)
    setup = json.loads(first[len("var PP = "):-1])
    assert setup["kind"] == "filter" and setup["options"] == {"mode": "highpass"}
    assert [d["id"] for d in setup["dials"]] == ["cutoff", "res"]
    assert setup["dials"][0] == {"id": "cutoff", "min": 40.0, "max": 10000.0, "exponent": 3.0, "value": 1800.0}
    assert set(setup["colours"]) == {"ink", "track", "accent", "card", "well"}
    # the two files, whole and in order: what is drawn, then how Max draws it
    assert rest == fxgen.graph_source()
    assert rest.index("var PPGraphs") < rest.index("mgraphics.init()")
    assert (PLUGIN / "graphs.js").read_text(encoding="utf-8") in rest


def test_a_picture_and_its_dials_are_joined_both_ways(effect):
    by_id = {b["id"]: b for b in boxes(effect)}
    picture = next(b for b in boxes(effect) if b.get("varname") == "eq_graph")
    into = [by_id[l["source"][0]] for l in lines(effect) if l["destination"][0] == picture["id"]]
    assert sorted(b["text"] for b in into) == ["prepend freq", "prepend high", "prepend low", "prepend mid"]
    for tell in into:   # each is fed by the dial it names
        feeder = by_id[next(l["source"][0] for l in lines(effect) if l["destination"][0] == tell["id"])]
        assert feeder["maxclass"] == "live.dial" and feeder["varname"] in ("eq_" + tell["text"].split()[1], "eq_mid_freq")
    out = [by_id[l["destination"][0]] for l in lines(effect) if l["source"][0] == picture["id"]]
    assert [b["text"] for b in out] == ["route low mid freq high"]
    back = sorted((l["source"][1], by_id[l["destination"][0]]["varname"]) for l in lines(effect) if l["source"][0] == out[0]["id"])
    assert back == [(0, "eq_low"), (1, "eq_mid"), (2, "eq_mid_freq"), (3, "eq_high")]


def test_a_device_can_do_without_pictures(effect, plain):
    assert fxgen.normalize(dict(EFFECT, look="dials"))["look"] == "dials"
    assert "look" not in fxgen.normalize(EFFECT) and "look" not in fxgen.normalize(dict(EFFECT, look="anything else"))
    assert not [b for b in boxes(plain) if b.get("maxclass") == "v8ui"]
    assert not [b for b in boxes(plain) if str(b.get("text", "")).startswith("prepend ")]
    assert all(b["presentation_rect"][1] == fxgen.DIAL_Y for b in boxes(plain) if b.get("maxclass") == "live.dial")
    assert plain["coverage"]["cords"]["not_checked"] == 0
    assert plain["json"]["patcher"]["devicewidth"] < effect["json"]["patcher"]["devicewidth"]


def test_pictures_change_nothing_about_the_sound_or_the_parameters(effect, plain):
    with_pictures = patchc.Compiler(effect["json"], "Pictured")
    without = patchc.Compiler(plain["json"], "Pictured")
    assert [(p.key, p.min, p.max, p.initial, p.exponent) for p in with_pictures.params] == \
           [(p.key, p.min, p.max, p.initial, p.exponent) for p in without.params]
    assert sorted(b.cls for b in with_pictures.boxes.values() if b.cls not in ("panel", "live.comment")) == \
           sorted(b.cls for b in without.boxes.values() if b.cls not in ("panel", "live.comment"))
    # the device a plugin runs: the same instructions, line for line, up to the face
    program = lambda r: patchc.compile_program(r["json"], "Pictured", r["settings"]).split("\n@end")[0]
    assert program(effect) == program(plain)


def test_the_plugins_window_is_told_about_each_picture_and_its_dials(effect):
    compiler = patchc.Compiler(effect["json"], "Pictured")
    face = compiler.face()
    graphs = [i for i in face["items"] if i["type"] == "graph"]
    assert [g["kind"] for g in graphs] == ["filter", "eq", "delay"]
    assert graphs[0]["options"] == {"mode": "highpass"} and graphs[2]["options"] == {"style": "pingpong"}
    params = {p.index: p for p in compiler.params}
    for g in graphs:
        assert g["dials"] and all(params[d["index"]].key.startswith(g["kind"] + "_") for d in g["dials"])
        assert g["rect"][0] + g["rect"][2] <= face["width"]
    # every dial is listed before any picture, which the window relies on
    kinds = [i["type"] for i in face["items"]]
    assert max(i for i, k in enumerate(kinds) if k == "dial") < min(i for i, k in enumerate(kinds) if k == "graph")
    assert [i for i in patchc.Compiler(fxgen.run(dict(EFFECT, look="dials"), want_amxd=False)["json"], "P").face()["items"] if i["type"] == "graph"] == []


def test_an_instrument_has_its_three(tmp_path):
    result = fxgen.run(SYNTH, want_amxd=True)
    pictures = [b["varname"] for b in boxes(result) if b.get("maxclass") == "v8ui"]
    assert pictures == ["osc_graph", "tone_graph", "env_graph"]
    osc = next(b for b in boxes(result) if b.get("varname") == "osc_graph")
    setup = json.loads(osc["textfile"]["text"].split("\n", 1)[0][len("var PP = "):-1])
    assert set(setup["options"]) == {"wave", "second", "voices"} and [d["id"] for d in setup["dials"]] == ["detune", "blend"]
    assert result["amxd"]


def test_the_plugins_window_has_the_same_drawing_code_pasted_in():
    cmake = (ROOT / "native" / "CMakeLists.txt").read_text(encoding="utf-8")
    face = (ROOT / "native" / "ui" / "face.html").read_text(encoding="utf-8")
    assert face.count("__GRAPHS__") == 1 and 'string(REPLACE "__GRAPHS__"' in cmake and "src/plugin/graphs.js" in cmake
