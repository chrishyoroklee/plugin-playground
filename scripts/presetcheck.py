"""
presetcheck: play every preset and measure what comes out.

    python scripts/presetcheck.py                 # every preset, a table, and what is wrong
    python scripts/presetcheck.py --only Motion   # one section, or part of a name
    python scripts/presetcheck.py --level         # also say what output gain would level each one
    python scripts/presetcheck.py --json out.json # the numbers, for keeping

A preset is a promise: pick it and it sounds like its name, at about the level of the
one before, without clipping, humming or running away. Nobody can listen to a hundred of
them after every change, so this renders each one and holds it to what can be measured.
It cannot say whether a preset sounds good. It can say that it is not broken, not
silent, not a copy of its neighbour, and not twice as loud as the rest.

An effect is fed the sample the page plays it on (public/samples/fx, decoded with
afconvert or ffmpeg when one of them is here) or, failing that and in the tests, the
maxcheck signal. An instrument is played a phrase: a note, a chord, a run.

What is checked, for every preset (FAIL unless marked note):

  level      an effect: within LEVEL dB of what went in. An instrument: its loudest
             twentieth of a second between two set levels. So switching presets does
             not jump in volume.
  clipping   almost no samples at full scale. The output stage clips at 1.0, so a
             preset that leans on it is distorting where it does not mean to.
  DC         no constant offset.
  does it    an effect changes the sound (Clean Slate is the one that must not).
  ends       after the input stops, it dies away. It is quieter at the end than just
             after the input stopped, and no louder than it was while playing.
  in tune    an instrument playing A is at A, or an octave from it.
  silent     an instrument makes no sound before its first note.
  moves      a preset with motion sounds different from the same preset without it.
  its own    (note) no other preset measures the same. Every effect is also played the
             maxcheck signal, which pulls an effect apart (an impulse for its echoes and
             tail, sines for its distortion and tremble, noise for its tone), and two
             that come out alike are listed for a person to look at.

Renders go through the runtime (native/runtime), which tests/test_runtime.py holds to
the compiled plugin, so nothing is compiled per preset and all of them take a few seconds.
"""

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import plugin  # noqa: E402

fxgen, patchc = plugin.fxgen, plugin.patchc
SAMPLES = ROOT / "public" / "samples" / "fx"
RATE = 44100

# How far an effect's level may be from its input's, in dB, and where an instrument's
# loudest moment has to sit, in dB below full scale.
LEVEL = (-6.0, 3.5)
LOUDEST = (-24.0, -7.0)
CLIP = 0.005          # the share of samples allowed at full scale
TAIL = 4.0            # seconds of silence after the input, to hear what rings on
TWIN = 0.045          # two presets nearer than this, by signature() or fingerprint(), are listed as alike

# What each kind of instrument is played. Times in seconds: key@start+length:velocity.
PHRASE = "57@0.2+0.9:100,60@1.4+1.2:100,64@1.4+1.2:100,67@1.4+1.2:100,72@3.0+0.2:110,76@3.25+0.2:90,79@3.5+0.2:100,69@4.0+1.0:100"
BASS = "33@0.2+0.9:100,36@1.4+1.2:100,43@3.0+0.2:110,36@3.25+0.2:90,48@3.5+0.2:100,45@4.0+1.0:100"
PHRASE_END = 5.0
TUNE = "%d@0.1+1.6:100"    # one note by itself, for hearing what pitch it is


def db(x):
    return 20 * math.log10(max(float(x), 1e-9))


def rms(x):
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if len(x) else 0.0


# ── what goes in ────────────────────────────────────────────────────────────────

def _write(path, x):
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def _read_pcm(path):
    with wave.open(str(path), "rb") as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32767
        return data.reshape(-1, w.getnchannels())


def inputs(work, real=True):
    """The audio each effect is fed: {sample name: wav path}, each with TAIL seconds of silence after it."""
    out = {}
    signal = work / "signal.wav"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "maxcheck" / "make_signal.py"), str(signal)], check=True, capture_output=True)
    x = _read_pcm(signal)
    _write(work / "in-signal.wav", np.concatenate([x[: 8 * RATE], np.zeros((int(TAIL * RATE), 2))]))
    out[None] = work / "in-signal.wav"
    decoder = ["afconvert", "-f", "WAVE", "-d", "LEI16@44100", "-c", "2"] if shutil.which("afconvert") else \
        ["ffmpeg", "-y", "-loglevel", "error", "-i"] if shutil.which("ffmpeg") else None
    for mp3 in sorted(SAMPLES.glob("*.mp3")) if real and decoder else []:
        raw = work / (mp3.stem + "-raw.wav")
        cmd = decoder + [str(mp3), str(raw)] if decoder[0] == "afconvert" else decoder + [str(mp3), "-ar", "44100", "-ac", "2", str(raw)]
        if subprocess.run(cmd, capture_output=True).returncode != 0:
            continue
        x = _read_pcm(raw)[: 8 * RATE]
        if x.shape[1] == 1:
            x = np.repeat(x, 2, axis=1)
        _write(work / ("in-%s.wav" % mp3.stem), np.concatenate([x, np.zeros((int(TAIL * RATE), 2))]))
        out[mp3.stem] = work / ("in-%s.wav" % mp3.stem)
    return out


# ── what comes out ──────────────────────────────────────────────────────────────

def _floats(path):
    return np.frombuffer(Path(path).read_bytes()[44:], dtype="<f4").astype(np.float64).reshape(-1, 2)


BANDS = [60, 120, 250, 500, 1000, 2000, 4000, 8000, 16000]


def spectrum(x):
    """Level in nine octave bands, in dB, of the mono sum."""
    mono = x.mean(axis=1)
    if not len(mono):
        return np.zeros(len(BANDS))
    power = np.abs(np.fft.rfft(mono * np.hanning(len(mono)))) ** 2
    freq = np.fft.rfftfreq(len(mono), 1 / RATE)
    return np.array([10 * math.log10(power[(freq >= f / 1.41) & (freq < f * 1.41)].sum() + 1e-12) for f in BANDS])


def envelope(x, step=0.02):
    """The level of the mono sum every `step` seconds."""
    mono = x.mean(axis=1)
    n = int(step * RATE)
    return np.array([rms(mono[i:i + n]) for i in range(0, len(mono) - n, n)])


def fingerprint(x, reference=None):
    """
    A handful of numbers that two presets share only if they sound alike: the shape of
    the spectrum (against the input's, for an effect), how wide it is, how much the level
    moves, how long it takes to die away. Scaled so that 1.0 is "nothing in common".
    """
    spec = spectrum(x) - (spectrum(reference) if reference is not None else 0)
    spec = spec - spec.mean()
    env = envelope(x)
    loud = env[env > 0.1 * env.max()] if env.max() > 0 else env
    side, mid = rms(x[:, 0] - x[:, 1]), rms(x[:, 0] + x[:, 1])
    flutter = float(np.std(np.diff(np.log(loud + 1e-6)))) if len(loud) > 2 else 0.0
    tail = rms(x[-int(TAIL * RATE):]) / (rms(x[: -int(TAIL * RATE)]) + 1e-9)
    return np.concatenate([spec / 30, [side / (mid + 1e-9), 4 * flutter, min(1.0, 4 * tail)]])


def pitch(x):
    """The fundamental of a steady note, in Hz, from where its waveform first repeats best."""
    mono = x.mean(axis=1) - x.mean()
    if rms(mono) < 1e-5:
        return 0.0
    n = len(mono)
    spec = np.fft.rfft(mono * np.hanning(n), 4 * n)
    auto = np.fft.irfft(np.abs(spec) ** 2)[: n // 2]
    lo, hi = int(RATE / 1200), min(len(auto) - 1, int(RATE / 40))
    # A waveform always matches itself best at no lag at all. Walk down off that, then
    # take the highest point: the first lag at which it lines up with itself again.
    rising = np.nonzero(np.diff(auto[lo:hi]) > 0)[0]
    if not len(rising):
        return 0.0
    start = lo + int(rising[0])
    best = start + int(np.argmax(auto[start:hi]))
    # the same height a whole number of periods later is the same note: keep the shortest
    for k in (4, 3, 2):
        if best // k >= start and auto[best // k] > 0.9 * auto[best]:
            best //= k
            break
    # between samples: at 440 Hz one sample of lag is 17 cents
    if 0 < best < len(auto) - 1:
        a, b, c = auto[best - 1], auto[best], auto[best + 1]
        if a - 2 * b + c != 0:
            best = best + 0.5 * (a - c) / (a - 2 * b + c)
    return RATE / best


def loudest(x, window=0.05):
    """The level of the loudest `window` seconds, the same whether a sound is held or plucked."""
    env = envelope(x, window)
    return float(env.max()) if len(env) else 0.0


def signature(y, x):
    """
    What an effect does to the maxcheck signal, as numbers that two effects share only
    if they do the same thing: its tone (from the noise), its distortion and squeeze (from
    the two sines), its echoes and tail (from the impulse and the silence), its width,
    and how much it trembles in level and in pitch. Scaled so that 1.0 is far apart.
    """
    cut = lambda sig, a, b: sig[int(a * RATE):int(b * RATE)]
    level = lambda sig, a, b: db(rms(cut(sig, a, b)))
    tone = (spectrum(cut(y, 7, 8)) - spectrum(cut(x, 7, 8)))
    tone = (tone - tone.mean()) / 24
    # distortion: what is in the loud sine that is not at 220 Hz
    loud = cut(y, 3.1, 3.9).mean(axis=1)
    spec = np.abs(np.fft.rfft(loud * np.hanning(len(loud)))) ** 2
    freq = np.fft.rfftfreq(len(loud), 1 / RATE)
    home = spec[(freq > 200) & (freq < 242)].sum()
    dirt = max(-60.0, 10 * math.log10((spec.sum() - home) / (home + 1e-12) + 1e-9)) / 60 + 1
    # ...and which harmonics: a tube, a clipper and a folder each stack them differently
    def harmonics(a, b):
        tone_ = cut(y, a, b).mean(axis=1)
        power = np.abs(np.fft.rfft(tone_ * np.hanning(len(tone_)))) ** 2
        hz = np.fft.rfftfreq(len(tone_), 1 / RATE)
        at = lambda k: power[(hz > 220 * k - 15) & (hz < 220 * k + 15)].sum()
        return [max(-60.0, 10 * math.log10(at(k) / (at(1) + 1e-12) + 1e-9)) / 60 + 1 for k in (2, 3, 4, 5, 7)]
    shape = harmonics(2.1, 2.9) + harmonics(3.1, 3.9)
    squeeze = ((level(y, 3.1, 3.9) - level(y, 2.1, 2.9)) - (level(x, 3.1, 3.9) - level(x, 2.1, 2.9))) / 9
    # echoes and tail: where the impulse's energy is, a little later each time, then the silence
    ir = [max(-70.0, level(y, a, b) - level(x, 0.5, 0.501)) / 70 + 1 for a, b in ((0.52, 0.6), (0.6, 0.75), (0.75, 1.0), (1.0, 1.4), (1.4, 2.0))]
    tail = [max(-70.0, level(y, a, b) - level(y, 7, 8)) / 70 + 1 for a, b in ((8.05, 8.5), (8.5, 10.0), (10.0, 12.0))]
    side = lambda a, b: rms(cut(y, a, b)[:, 0] - cut(y, a, b)[:, 1]) / (rms(cut(y, a, b)[:, 0] + cut(y, a, b)[:, 1]) + 1e-9)
    env = envelope(cut(y, 2.1, 2.9), 0.01)
    tremble = min(1.0, 3 * float(np.std(np.log(env + 1e-6))))
    # pitch tremble: how unevenly the quiet sine crosses zero
    quiet = cut(y, 2.1, 2.9).mean(axis=1)
    cross = np.nonzero((quiet[:-1] < 0) & (quiet[1:] >= 0))[0]
    waver = min(1.0, 40 * float(np.std(np.diff(cross)) / (np.mean(np.diff(cross)) + 1e-9))) if len(cross) > 8 else 0.0
    # ...and how fast: a slow pulse and a helicopter tremble as deeply as each other
    def speed(series, per_second):
        if len(series) < 16 or np.std(series) < 1e-9:
            return 0.0
        spec = np.abs(np.fft.rfft((series - np.mean(series)) * np.hanning(len(series))))
        hz = np.fft.rfftfreq(len(series), 1 / per_second)[int(np.argmax(spec[1:])) + 1]
        return min(1.0, math.log2(1 + hz) / 5)
    tremble_speed = speed(np.log(env + 1e-6), 100) if tremble > 0.05 else 0.0
    waver_speed = speed(np.diff(cross).astype(float), 220) if waver > 0.05 else 0.0
    return np.concatenate([tone, [dirt, squeeze], shape, ir, tail,
                           [min(1.0, side(7, 8)), min(1.0, side(0.5, 2.0)), tremble, waver, tremble_speed, waver_speed]])


class Result:
    def __init__(self, entry, kind):
        self.id, self.name, self.group, self.kind = entry["id"], entry["name"], entry["group"], kind
        self.fails, self.notes, self.numbers = [], [], {}
        self.print = None    # fingerprint, for the "its own" check
        self.suggest = None  # the output gain that would level it

    def row(self):
        n = self.numbers
        return "%-11s %-20s %7s %6s %7s  %s" % (
            self.group, self.name, "%+.1f" % n["level"] if "level" in n else "", "%.1f%%" % (100 * n.get("clip", 0)),
            "%+.0f" % n["ends"] if "ends" in n else "", "; ".join(self.fails) or ("ok" + ("  (" + "; ".join(self.notes) + ")" if self.notes else "")))


def _settings_without_motion(settings):
    return {k: v for k, v in settings.items() if k != "motion"}


def check_effect(entry, wavs, renderer, work, made, still):
    r = Result(entry, "effect")
    settings = entry["settings"]
    # On its own sample, a preset's level and clipping are checked. With only the test
    # signal to hand (no decoder, or in the tests) they are not: a preset is levelled for
    # the sample the page plays it on, not for a sweep and a burst of noise.
    paired = entry.get("sample") in wavs or not entry.get("sample")
    source = wavs.get(entry.get("sample")) or wavs[None]
    dry = _read_pcm(source)
    out = work / (entry["id"] + ".wav")
    plugin.render(renderer, source, out, as_float=True, device=made["device"])
    y = _floats(out)
    playing = slice(0, len(dry) - int(TAIL * RATE))
    after = y[len(dry) - int(TAIL * RATE):]

    if not np.isfinite(y).all():
        r.fails.append("not a number comes out")
        return r
    r.numbers["level"] = db(rms(y[playing])) - db(rms(dry[playing]))
    r.numbers["clip"] = float((np.abs(y) >= 0.999).mean())
    # an offset the effect adds: what is there beyond the input's own
    r.numbers["dc"] = max(0.0, float(np.abs(y[playing].mean(axis=0)).max()) - float(np.abs(dry[playing].mean(axis=0)).max()))
    r.suggest = round(2 * (float(settings.get("output", {}).get("gain", 0)) - r.numbers["level"] - 1.0)) / 2

    if rms(y[playing]) < 1e-4:
        r.fails.append("silent")
        return r
    if paired and not LEVEL[0] <= r.numbers["level"] <= LEVEL[1]:
        r.fails.append("level %+.1f dB against its input" % r.numbers["level"])
    if paired and r.numbers["clip"] > CLIP:
        r.fails.append("%.1f%% of samples at full scale" % (100 * r.numbers["clip"]))
    if r.numbers["dc"] > 0.03:   # a clipper given a lopsided wave makes a little; this much is a fault
        r.fails.append("DC offset %.3f" % r.numbers["dc"])

    # Does it do anything: what is left when the input, at the best gain, is taken away.
    # (each side by itself: an auto-pan leaves the sum of the two alone)
    changed = []
    for ch in (0, 1):
        a, b = dry[playing][:, ch], y[playing][:, ch]
        gain = float(a @ b) / (float(a @ a) + 1e-12)
        changed.append(rms(b - gain * a) / (rms(b) + 1e-12))
    r.numbers["changed"] = max(changed)
    on = [e for e in settings.get("chain", []) if e.get("on", True)]
    if on and r.numbers["changed"] < 0.02:
        r.fails.append("comes out as it went in")
    if not on and not settings.get("motion") and r.numbers["changed"] > 0.001:
        r.fails.append("has nothing switched on, and still changes the sound")

    # Does it end: the last second against the first after the input stopped, and against itself playing.
    first, last = rms(after[:RATE]), rms(after[-RATE:])
    r.numbers["ends"] = db(last) - db(rms(y[playing]))
    if last > 1e-4 and (last > 0.7 * first or r.numbers["ends"] > -12):
        r.fails.append("does not die away (%+.0f dB after %g s)" % (r.numbers["ends"], TAIL))

    if still:
        out2 = work / (entry["id"] + "-still.wav")
        plugin.render(renderer, source, out2, as_float=True, device=still["device"])
        z = _floats(out2)
        r.numbers["moves"] = rms((y - z)[playing]) / (rms(y[playing]) + 1e-12)
        if r.numbers["moves"] < 0.05:
            r.fails.append("its motion cannot be heard (%.3f)" % r.numbers["moves"])
    # What it does to the test signal, for telling it from the others.
    probe = work / (entry["id"] + "-signal.wav")
    plugin.render(renderer, wavs[None], probe, as_float=True, device=made["device"])
    r.print = signature(_floats(probe), _read_pcm(wavs[None]))
    return r


def check_instrument(entry, renderer, work, made, still):
    r = Result(entry, "instrument")
    settings = entry["settings"]
    out = work / (entry["id"] + ".wav")
    release = float(settings["instrument"]["env"]["values"]["release"]) / 1000
    seconds = PHRASE_END + release + TAIL
    notes = BASS if entry["group"] == "Bass" else PHRASE
    plugin.play(renderer, notes, seconds, out, as_float=True, device=made["device"])
    y = _floats(out)
    cut = lambda span: y[int(span[0] * RATE):int(span[1] * RATE)]

    if not np.isfinite(y).all():
        r.fails.append("not a number comes out")
        return r
    if np.abs(y[: int(0.19 * RATE)]).max() > 0:
        r.fails.append("makes sound before it is played")
    chord = loudest(cut((0.2, PHRASE_END)))
    r.numbers["level"] = db(chord)
    r.numbers["clip"] = float((np.abs(y) >= 0.999).mean())
    r.numbers["dc"] = float(np.abs(cut((0.2, PHRASE_END)).mean(axis=0)).max())
    r.suggest = round(2 * (float(settings.get("output", {}).get("gain", 0)) + (-14.0 - r.numbers["level"]))) / 2
    if rms(cut((0.2, PHRASE_END))) < 1e-4:
        r.fails.append("silent")
        return r
    if not LOUDEST[0] <= r.numbers["level"] <= LOUDEST[1]:
        r.fails.append("its loudest moment is at %.1f dB" % r.numbers["level"])
    if r.numbers["clip"] > CLIP:
        r.fails.append("%.1f%% of samples at full scale" % (100 * r.numbers["clip"]))
    if r.numbers["dc"] > 0.03:   # a clipper given a lopsided wave makes a little; this much is a fault
        r.fails.append("DC offset %.3f" % r.numbers["dc"])

    # In tune: an A, played by itself so that no other note's tail is in it, and listened
    # to where it is loudest, which for a pluck is its start and for a pad is not. It may
    # sound an octave either side (a second oscillator an octave away).
    key = 45 if entry["group"] == "Bass" else 69
    alone = work / (entry["id"] + "-a.wav")
    plugin.play(renderer, TUNE % key, 1.7, alone, as_float=True, device=made["device"])
    a = _floats(alone)
    env = envelope(a, 0.05)
    at = int(np.argmax(env)) * int(0.05 * RATE) if len(env) else 0
    want = 440.0 * 2 ** ((key - 69) / 12)
    got = pitch(a[at:at + int(0.3 * RATE)])
    r.numbers["pitch"] = got
    if got <= 0 or min(abs(1200 * math.log2(got / (want * k))) for k in (0.25, 0.5, 1, 2)) > 35:
        r.fails.append("plays %.1f Hz for an A at %.1f" % (got, want))

    last = rms(y[-RATE // 2:])
    r.numbers["ends"] = db(last) - db(chord)
    if last > 1e-4 and r.numbers["ends"] > -20:
        r.fails.append("still sounding %g s after its release (%+.0f dB)" % (TAIL, r.numbers["ends"]))

    if still:
        out2 = work / (entry["id"] + "-still.wav")
        plugin.play(renderer, notes, seconds, out2, as_float=True, device=still["device"])
        z = _floats(out2)
        r.numbers["moves"] = rms(y - z) / (rms(y) + 1e-12)
        if r.numbers["moves"] < 0.05:
            r.fails.append("its motion cannot be heard (%.3f)" % r.numbers["moves"])
    # How it starts and fades, with its spectrum: a pad and a pluck of the same waves are not twins.
    held = envelope(cut((0.2, 1.1)), 0.02)
    shape = [float(np.argmax(held >= 0.5 * held.max())) / len(held), float(held[-1] / (held.max() + 1e-9))] if held.max() > 0 else [0.0, 0.0]
    r.print = np.concatenate([fingerprint(y), shape])
    return r


def check_all(only=None, real=True, work=None):
    """Check every preset (or those whose section or name contains `only`). Returns the Results."""
    own = None
    if work is None:
        own = tempfile.TemporaryDirectory()
        work = Path(own.name)
    try:
        renderer = plugin.build_runtime_renderer(work / ("render" + (".exe" if sys.platform == "win32" else "")))
        wavs = inputs(work, real)
        match = lambda p: not only or only.lower() in p["group"].lower() or only.lower() in p["name"].lower()
        jobs = [(p, "effect") for p in plugin.PRESETS if match(p)] + [(p, "instrument") for p in plugin.INSTRUMENTS if match(p)]

        # The devices are made one after another: fxgen changes the working directory
        # while it runs a script, which the whole process shares. Rendering them is what
        # takes the time, and that is done side by side.
        made, still, broken = {}, {}, {}
        for entry, kind in jobs:
            key = (kind, entry["id"])
            try:
                made[key] = plugin.generate(entry["settings"], work / kind / entry["id"])
                if entry["settings"].get("motion"):
                    still[key] = plugin.generate(_settings_without_motion(entry["settings"]), work / kind / (entry["id"] + "-still"))
            except (plugin.BuildError, patchc.CompileError, ValueError) as err:
                broken[key] = str(err).splitlines()[0]

        def one(job):
            entry, kind = job
            key = (kind, entry["id"])
            if key in broken:
                r = Result(entry, kind)
                r.fails.append("could not be built: %s" % broken[key])
                return r
            out = work / kind
            if kind == "effect":
                return check_effect(entry, wavs, renderer, out, made[key], still.get(key))
            return check_instrument(entry, renderer, out, made[key], still.get(key))

        with ThreadPoolExecutor() as pool:
            results = list(pool.map(one, jobs))
    finally:
        if own:
            own.cleanup()

    # Its own: within a kind, no two presets with the same fingerprint.
    for kind in ("effect", "instrument"):
        group = [r for r in results if r.kind == kind and r.print is not None]
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                near = float(np.sqrt(np.mean((a.print - b.print) ** 2)))
                if near < TWIN:
                    a.notes.append("measures like %s (%.3f)" % (b.name, near))
                    b.notes.append("measures like %s (%.3f)" % (a.name, near))
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description="Play every preset and measure what comes out.")
    ap.add_argument("--only", help="a section, or part of a preset's name")
    ap.add_argument("--signal", action="store_true", help="feed effects the test signal, not the samples the page plays them on")
    ap.add_argument("--level", action="store_true", help="say what output gain would bring each preset to the common level")
    ap.add_argument("--json", metavar="FILE", help="also write the numbers to FILE")
    args = ap.parse_args(argv)
    results = check_all(args.only, real=not args.signal)
    print("%-11s %-20s %7s %6s %7s  %s" % ("section", "preset", "level", "clip", "ends", "verdict"))
    for r in results:
        print(r.row())
        if args.level and r.suggest is not None and "level" in r.numbers:
            print("%32s output gain %+.1f dB would level it" % ("", max(-24.0, min(12.0, r.suggest))))
    bad = [r for r in results if r.fails]
    print("\n%d presets, %d with something wrong, %d to look at" % (len(results), len(bad), len([r for r in results if r.notes and not r.fails])))
    if args.json:
        Path(args.json).write_text(json.dumps([{"id": r.id, "name": r.name, "group": r.group, "kind": r.kind, "fails": r.fails,
                                                "notes": r.notes, "suggest": r.suggest, **{k: round(v, 4) for k, v in r.numbers.items()}}
                                               for r in results], indent=1), encoding="utf-8")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
