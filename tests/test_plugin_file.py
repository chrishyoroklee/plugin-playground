"""
A plugin's file: its presets, and the programs they run (patchc.plugin_device).

A plugin on the page is one kind of device with presets inside it (src/plugin/plugins.ts).
Downloaded, it is one file that holds them, so its window can offer them in a menu: one
program for each set of types its presets use (a Tube preset and a Fuzz preset are other
code), and for each preset, the program and every dial's value. The runtime plugin reads
it as an mxvm::Book. These tests hold the file to the presets; hostcheck (in
`plugin.py --runtime --plugins`) loads each preset in a real plugin and listens to it.
"""

import importlib.util
import sys
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
fxgen, patchc = plugin.fxgen, plugin.patchc
PLUGINS = plugin.page_plugins()
needs_compiler = pytest.mark.skipif(plugin.compiler() is None, reason="no C++ compiler")


def _plugin(name):
    return next(p for p in PLUGINS if p["name"] == name)


def _programs(text):
    """The file cut into its programs (each to its @end), and every line outside them."""
    programs, rest, inside = [], [], False
    for line in text.splitlines():
        if line.startswith("ppdevice "):
            programs.append([])
            inside = True
        if inside:
            programs[-1].append(line)
            inside = line != "@end"
        else:
            rest.append(line)
    return programs, rest


def _params(program):
    return {int(l.split()[1]): l.split() for l in program if l.startswith("param ")}


def test_the_plugins_are_the_pages():
    names = [p["name"] for p in PLUGINS]
    assert len(names) == len(set(names))
    eq = [p for p in PLUGINS if p["name"].startswith("EQ ")]
    assert sorted(p["name"] for p in eq) == ["EQ eq", "EQ geq", "EQ peq", "EQ tilt"]
    synth = _plugin("Synth")
    assert len(synth["presets"]) == 1 + len(plugin.INSTRUMENTS) and synth["presets"][0]["name"] == "Default"
    # every preset is in exactly one plugin
    assert sum(len(p["presets"]) - (p["presets"][0]["name"] == "Default") for p in PLUGINS) == \
           len(plugin.PRESETS) + len(plugin.INSTRUMENTS)


def test_every_presets_dials_are_where_the_page_puts_them():
    """dial_values names each dial as fxgen does: the same ids and values as the compiled device."""
    for preset in plugin.PRESETS + plugin.INSTRUMENTS:
        settings = fxgen.normalize(preset["settings"])
        result = fxgen.run(settings, want_amxd=False)
        compiled = patchc.Compiler(result["json"], "X")
        want = {p.id: p.initial for p in compiled.params}
        got = patchc.dial_values(settings)
        assert set(want) <= set(got), preset["id"]
        for i, value in want.items():
            assert got[i] == pytest.approx(value), (preset["id"], i)


def test_a_distortion_holds_a_program_per_type_and_every_preset():
    entry = _plugin("Distortion drive")
    text = patchc.plugin_device(dict(entry["presets"][0]["settings"], name="Distortion"), entry["presets"])
    programs, rest = _programs(text)
    types = {fxgen.normalize(p["settings"])["chain"][0]["options"]["character"]
             for p in entry["presets"] if p["settings"].get("chain")} | {"soft"}
    assert len(programs) == len(types)
    # only the first asks for the runtime that knows presets; each is named after the plugin
    assert programs[0][1] == "runtime %d" % patchc.PRESETS_RUNTIME
    assert all(p[2] == "name Distortion" for p in programs)
    # the same dials in every program
    assert all(set(_params(p)) == set(_params(programs[0])) for p in programs)
    presets = [l.split() for l in rest if l.startswith("preset ")]
    assert [patchc._quote(p["name"]) for p in entry["presets"]] == [p[2] for p in presets]
    assert rest[-1] == "current 0"   # it starts on Default, which is what it is
    # only the first program carries the page's settings
    assert sum(1 for l in text.splitlines() if l.startswith("settings ")) == 1


def test_a_preset_names_its_program_and_every_dial():
    entry = _plugin("Distortion drive")
    text = patchc.plugin_device(dict(entry["presets"][2]["settings"], name="Distortion"), entry["presets"])
    programs, rest = _programs(text)
    presets = [l.split() for l in rest if l.startswith("preset ")]
    by_type = {}
    for preset, line in zip(entry["presets"], presets):
        program = programs[int(line[1])]
        settings = fxgen.normalize(preset["settings"])
        # one program per type, and the same program for the same type
        kind = settings["chain"][0]["options"]["character"]
        assert by_type.setdefault(kind, line[1]) == line[1]
        values = dict(zip(map(int, line[4::2]), map(float, line[5::2])))
        assert int(line[3]) == len(values) == len(_params(program))
        assert values == pytest.approx({i: v for i, v in patchc.dial_values(settings).items() if i in _params(program)})
    assert len(set(by_type.values())) == len(by_type)
    assert rest[-1] == "current 2"


def test_the_synths_presets_share_one_list_of_dials():
    """Synth presets carry different effects after the synth; the plugin's dials are all of them."""
    entry = _plugin("Synth")
    text = patchc.plugin_device(dict(entry["presets"][1]["settings"], name="Synth"), entry["presets"])
    programs, rest = _programs(text)
    every = {}
    for program in programs:
        for i, words in _params(program).items():
            shape = words[2:4] + words[5:8]
            assert every.setdefault(i, shape) == shape
    assert len(every) > len(_params(programs[0]))
    assert len([l for l in rest if l.startswith("preset ")]) == len(entry["presets"])


def test_a_device_on_its_own_is_unchanged():
    """A chain built in Edit, with no plugin, still downloads as one program and no presets."""
    settings = {"name": "Mine", "chain": [{"module": "drive", "on": True}, {"module": "comp", "on": True}]}
    text = patchc.device_for(__import__("json").dumps(settings))
    assert text.count("ppdevice ") == 1 and "preset " not in text and "\ncurrent " not in text


@needs_compiler
def test_the_runtime_plays_a_plugin_file_as_its_first_program(tmp_path):
    """Opened, the plugin is the device downloaded: the same samples as that device alone."""
    pytest.importorskip("numpy")
    import subprocess

    import numpy as np
    signal = tmp_path / "signal.wav"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "maxcheck" / "make_signal.py"), str(signal)], check=True,
                   capture_output=True)
    renderer = plugin.build_runtime_renderer(tmp_path / ("render" + (".exe" if sys.platform == "win32" else "")))
    entry = _plugin("Reverb reverb")
    start = dict(entry["presets"][3]["settings"], name="Reverb")
    whole = tmp_path / "plugin.ppdevice"
    whole.write_text(patchc.plugin_device(start, entry["presets"]))
    alone = tmp_path / "alone.ppdevice"
    alone.write_text(patchc.device_for(__import__("json").dumps(start)))
    outs = []
    for device in (whole, alone):
        out = tmp_path / (device.stem + ".wav")
        plugin.render(renderer, signal, out, as_float=True, device=device)
        outs.append(np.frombuffer(out.read_bytes()[44:], dtype="<f4"))
    assert np.abs(outs[1]).max() > 0 and np.array_equal(outs[0], outs[1])
