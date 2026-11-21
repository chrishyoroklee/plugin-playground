"""Render every test through the compiled C++ (patchc + native/mxrt.h), the third
renderer next to Max and the browser. Each test's settings are built into a patch,
compiled to Effect.h, built into native/render and run on the signal.
Usage: native.py tests.json signal.wav <dir for the WAVs> [--float] [--as-chrome]
--float writes 32-bit float WAVs; --as-chrome reads the signal the way Chrome decodes a
16-bit file (positive samples over 32767), for a sample-level comparison with the browser."""
import json, pathlib, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import plugin

args = [a for a in sys.argv[1:] if not a.startswith("--")]
tests, signal, out = json.load(open(args[0])), args[1], pathlib.Path(args[2])
as_float = "--float" in sys.argv
as_chrome = "--as-chrome" in sys.argv
out.mkdir(parents=True, exist_ok=True)

with tempfile.TemporaryDirectory() as tmp:
    # fxgen runs one at a time (it changes directory while it works); the compiles do not.
    made = [(t["name"], plugin.generate(t["settings"], pathlib.Path(tmp) / t["name"])) for t in tests]
    def one(item):
        name, m = item
        plugin.render(plugin.build_renderer(m["dir"]), signal, out / (name + ".wav"), as_float=as_float, as_chrome=as_chrome)
        return name
    with ThreadPoolExecutor() as pool:
        done = list(pool.map(one, made))
print(len(done))
