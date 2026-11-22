"""
Every preset, played and measured.

scripts/presetcheck.py says what is measured and why. Here it runs on what every machine
has: effects are fed the maxcheck signal, so the checks that pair a preset with the
sample the page plays it on (its level, its clipping) are left to `python
scripts/presetcheck.py` on a machine that can decode the samples. Everything else holds
for every preset: it builds, it is not silent, an effect changes the sound and dies away,
an instrument is in tune, quiet before its first note and at the common level, and a
preset with motion sounds different from the same preset without it.

Needs a C++ compiler and numpy; skipped without them.
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
needs_compiler = pytest.mark.skipif(plugin.compiler() is None, reason="no C++ compiler")
EVERYTHING = [("effect", p) for p in plugin.PRESETS] + [("instrument", p) for p in plugin.INSTRUMENTS]


@pytest.fixture(scope="session")
def checked(tmp_path_factory):
    pytest.importorskip("numpy")
    presetcheck = _load("presetcheck", ROOT / "scripts" / "presetcheck.py")
    results = presetcheck.check_all(real=False, work=tmp_path_factory.mktemp("presets"))
    return {(r.kind, r.id): r for r in results}


@needs_compiler
@pytest.mark.parametrize("kind, entry", EVERYTHING, ids=["%s-%s" % (k, p["id"]) for k, p in EVERYTHING])
def test_the_preset_is_sound(checked, kind, entry):
    result = checked[(kind, entry["id"])]
    assert not result.fails, "%s: %s" % (entry["name"], "; ".join(result.fails))


@needs_compiler
def test_no_two_presets_are_the_same_preset(checked):
    """Two that measure alike are listed for a person; two that measure the same are one preset twice."""
    import numpy as np
    for kind in ("effect", "instrument"):
        group = [r for r in checked.values() if r.kind == kind and r.print is not None]
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                assert float(np.sqrt(np.mean((a.print - b.print) ** 2))) > 0.01, "%s and %s" % (a.name, b.name)


def test_presets_have_their_own_names_and_a_line_each():
    for presets in (plugin.PRESETS, plugin.INSTRUMENTS):
        assert len({p["id"] for p in presets}) == len({p["name"] for p in presets}) == len(presets)
        for p in presets:
            assert p["settings"]["name"] == p["name"], p["id"]
            assert 10 <= len(p["tag"]) <= 90, p["name"]
