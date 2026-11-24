"""
fxgen: turn a settings dict into a Max for Live device, using maxpylang.

Two kinds of device come out of it. An effect ("kind": "effect", the default) takes the
track's audio from plugin~. An instrument ("kind": "instrument") takes notes from notein
and makes its own sound: a polyphonic synth whose voices are laid out one beside the
other, followed by the same effect chain. Both end in an output gain and plugout~.

The interactive plugin page (web/interactive-plugin.html) runs this file in the browser
under Pyodide. It works in two steps, and the split is the point:

    script(settings)  ->  the source of a plain maxpylang program
    run(settings)     ->  executes that program and returns the patch it built

So the Python the page shows is not a description of what happened, it is what ran. Copy
it into a file, `pip install maxpylang`, and it writes the same .amxd.

The module list, every dial's range and the presets' vocabulary live in schema.json next
to this file. The page reads the same file to draw its controls, so the two cannot drift.

Where maxpylang has no public way to say something a device needs, the emitted script
reaches into the box dict and says so in a comment that names the entry in
docs/type-system/m4l-gaps.md. Those workarounds are the gap log's evidence.
"""

import base64
import contextlib
import io
import json
import math
import os
import re
import tempfile
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))

SCHEMA = None


def load_schema(source=None):
    """Load schema.json. `source` is its text; without it the sibling file is read."""
    global SCHEMA
    if source is None:
        with open(os.path.join(HERE, "schema.json"), encoding="utf-8") as fh:
            source = fh.read()
    SCHEMA = json.loads(source)
    return SCHEMA


def schema():
    return SCHEMA if SCHEMA is not None else load_schema()


GRAPH_SOURCE = None


def load_graphs(source=None):
    """
    The pictures' drawing code, as the text that goes inside each v8ui: graphs.js (what
    is drawn, shared with the page and the plugin's window) and then graph-max.js (how
    it is drawn in Max). `source` is that text; without it the sibling files are read.
    """
    global GRAPH_SOURCE
    if source is None:
        parts = []
        for name in ("graphs.js", "graph-max.js"):
            with open(os.path.join(HERE, name), encoding="utf-8") as fh:
                parts.append(fh.read())
        source = "\n".join(parts)
    GRAPH_SOURCE = source
    return GRAPH_SOURCE


def graph_source():
    return GRAPH_SOURCE if GRAPH_SOURCE is not None else load_graphs()


# ── settings ────────────────────────────────────────────────────────────────────

DEFAULT_NAME = "MaxPy FX"
DEFAULT_INSTRUMENT_NAME = "MaxPy Synth"
NAME_OK = re.compile(r"[^A-Za-z0-9 _\-]")
HEX_OK = re.compile(r"^#[0-9a-fA-F]{6}$")


def _module(module_id):
    for m in schema()["modules"]:
        if m["id"] == module_id:
            return m
    return None


def _clamp_param(param, value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = float(param["default"])
    if v != v:  # NaN
        v = float(param["default"])
    v = min(float(param["max"]), max(float(param["min"]), v))
    return float(round(v)) if param.get("integer") else v


def default_settings(kind="effect"):
    return normalize({"kind": kind})


def _instrument_module(module_id):
    for m in schema()["instrument"]["modules"]:
        if m["id"] == module_id:
            return m
    return None


def normalize(settings):
    """
    Fill in and bound a settings dict, so everything after this can trust it.

    The page sends whatever is in its state, a shared link can carry anything, and the
    result is spliced into Python source, so nothing is passed through unchecked: unknown
    modules are dropped, a module listed twice is kept once, every value is clamped to its
    dial's range, and the name is reduced to characters that are safe in a file name.
    """
    sch = schema()
    settings = settings if isinstance(settings, dict) else {}

    kind = "instrument" if settings.get("kind") == "instrument" else "effect"
    fallback = DEFAULT_INSTRUMENT_NAME if kind == "instrument" else DEFAULT_NAME
    name = NAME_OK.sub("", str(settings.get("name") or fallback)).strip()[:32] or fallback
    accent = settings.get("accent")
    if not (isinstance(accent, str) and HEX_OK.match(accent)):
        accent = sch["accents"][0]

    chain, seen = [], set()
    given = settings.get("chain")
    for entry in given if isinstance(given, list) else []:
        if not isinstance(entry, dict):
            continue
        mod = _module(entry.get("module"))
        if mod is None or mod["id"] in seen:
            continue
        seen.add(mod["id"])
        chain.append(_normalize_entry(mod, entry))
    for mod in sch["modules"]:
        if mod["id"] not in seen:
            chain.append(_normalize_entry(mod, {"on": False}))

    out_values = (settings.get("output") or {}) if isinstance(settings.get("output"), dict) else {}
    output = {p["id"]: _clamp_param(p, out_values.get(p["id"], p["default"])) for p in sch["output"]["params"]}

    out = {"name": name, "accent": accent.lower(), "chain": chain, "output": output}
    # The colours of the device. The first theme is the one every device had before there
    # were themes, and is not written down, so those devices' settings are unchanged.
    themes = [t["id"] for t in sch.get("themes", [])]
    if settings.get("theme") in themes[1:]:
        out["theme"] = settings["theme"]
    # Pictures above the dials (an EQ's curve, a delay's echoes) are how a device looks
    # unless it asks for dials alone. Like the first theme, the usual look is not written.
    if settings.get("look") == "dials":
        out["look"] = "dials"
    motion = _normalize_motion(settings.get("motion"), kind)
    if motion:
        out["motion"] = motion
    if kind == "instrument":
        # The synth's three sections are always there, in a fixed order.
        given = settings.get("instrument") if isinstance(settings.get("instrument"), dict) else {}
        out["kind"] = kind
        out["instrument"] = {}
        for mod in sch["instrument"]["modules"]:
            entry = given.get(mod["id"]) if isinstance(given.get(mod["id"]), dict) else {}
            entry = _normalize_entry(mod, entry)
            out["instrument"][mod["id"]] = {"options": entry["options"], "values": entry["values"]}
    return out


def _normalize_entry(mod, entry):
    options = entry.get("options") if isinstance(entry.get("options"), dict) else {}
    values = entry.get("values") if isinstance(entry.get("values"), dict) else {}
    opts = {}
    for opt in mod["options"]:
        ids = [c["id"] for c in opt["choices"]]
        opts[opt["id"]] = options.get(opt["id"]) if options.get(opt["id"]) in ids else opt["default"]
    vals = {p["id"]: _clamp_param(p, values.get(p["id"], p["default"])) for p in mod["params"]}
    return {"module": mod["id"], "on": bool(entry.get("on", True)), "options": opts, "values": vals}


KNOB_NAME_OK = re.compile(r"[^A-Za-z0-9 ]")


def motion_targets(kind="effect"):
    """Every dial something can move, as "module.dial" -> (module, dial). Whole-number dials are left out."""
    sch = schema()
    mods = list(sch["modules"]) + [sch["output"]] + (sch["instrument"]["modules"] if kind == "instrument" else [])
    return {"%s.%s" % (m["id"], p["id"]): (m, p) for m in mods for p in m["params"] if not p.get("integer")}


def _normalize_motion(given, kind):
    """
    The part of the settings that makes dials move by themselves: the sources (two LFOs,
    a follower of the incoming volume, and knobs the user adds and names), and the routes
    from a source to a dial. Returns None when nothing is routed and no knob exists, so a
    device without any of this has exactly the settings it had before there was such a thing.
    """
    spec = schema().get("motion")
    if not spec or not isinstance(given, dict):
        return None
    out = {}
    for source in spec["sources"]:
        entry = given.get(source["id"]) if isinstance(given.get(source["id"]), dict) else {}
        entry = _normalize_entry(source, entry)
        out[source["id"]] = {"options": entry["options"], "values": entry["values"]}

    knobs, values, taken = [], {}, set()
    given_values = given.get("knobValues") if isinstance(given.get("knobValues"), dict) else {}
    for index, knob in enumerate(given.get("knobs") if isinstance(given.get("knobs"), list) else []):
        if len(knobs) >= spec["knobs"]["max"] or not isinstance(knob, dict):
            continue
        knob_id = "k%d" % (len(knobs) + 1)
        # A knob's name becomes a dial's name and a variable in the script, so it is kept to
        # letters, digits and spaces, and no two knobs may share one.
        name = KNOB_NAME_OK.sub("", str(knob.get("name") or "")).strip()[:10] or "Knob %d" % (len(knobs) + 1)
        while name.lower() in taken:
            name = (name[:8] + " %d" % (len(knobs) + 1)).strip()
        taken.add(name.lower())
        knobs.append({"id": knob_id, "name": name, "was": str(knob.get("id") or knob_id)})
        values[knob_id] = _clamp_param(dict(spec["knobs"]["param"]), given_values.get(str(knob.get("id") or knob_id), 0))
    renamed = {k.pop("was"): k["id"] for k in knobs}

    targets = motion_targets(kind)
    sources = {src["id"] for src in spec["sources"] if not (src.get("effectOnly") and kind == "instrument")}
    sources |= {k["id"] for k in knobs}
    routes, seen = [], set()
    for route in given.get("routes") if isinstance(given.get("routes"), list) else []:
        if not isinstance(route, dict) or len(routes) >= spec["maxRoutes"]:
            continue
        source = renamed.get(str(route.get("source")), str(route.get("source")))
        target = str(route.get("target"))
        try:
            amount = float(route.get("amount", 0))
        except (TypeError, ValueError):
            continue
        amount = max(-100.0, min(100.0, amount))
        if source not in sources or target not in targets or (source, target) in seen or amount != amount or amount == 0:
            continue
        seen.add((source, target))
        routes.append({"source": source, "target": target, "amount": amount})

    if not routes and not knobs:
        return None
    out["knobs"] = knobs
    out["knobValues"] = values
    out["routes"] = routes
    return out


def filename(settings):
    return normalize(settings)["name"].replace(" ", "_") + ".amxd"


# ── script emission ─────────────────────────────────────────────────────────────

# Patching-view geometry. Left and right channels run down two columns; each module's
# dials and control math sit in a third; presentation-only boxes are parked in a fourth.
X = {"L": 40, "R": 250}
SUB = 95          # a second object beside the first, inside one channel column
XC = 480          # control column
DX = 135          # between dials
XP = 860          # parked presentation boxes
ROW = 40
GAP = 50
DIAL_ROW = 62     # a live.dial is taller than an object box

# Presentation geometry. A Live device is 169 px tall; the width is ours to set.
DEVICE_H = 169
BRAND_W = 128
CARD_GAP = 6
CARD_PAD = 8
DIAL_W, DIAL_H = 44, 48
DIAL_STEP = 50
CARD_Y, CARD_H = 6, 157
TITLE_Y = 14
NAME_Y = 75       # the device's name, alone at the left, level with the middle of the cards
DIAL_Y = 72
# A card with a picture: the picture under the title, the dials along the bottom. Only
# where plugins of that kind usually have one, because the picture tells you something
# the knobs cannot: an EQ's or a filter's curve, a compressor's transfer curve, and a
# synth's wave, filter and envelope. Distortion, crush, chorus, tremolo, delay and reverb
# are knobs, as they are on most plugins of their kind, with their type named on the card.
GRAPHS = ("filter", "eq", "peq", "geq", "tilt", "comp", "osc", "tone", "env")
GRAPH_Y, GRAPH_H = 34, 70
GRAPH_MIN_W = 136
GRAPH_DIAL_Y = 109
# A card with a type and no picture: the type's name under the title, where the page puts
# its buttons (one TYPE_W wide for each choice).
TYPES_Y = 38
TYPE_W = 34

UNITSTYLE = {"": "FLOAT", "x": "FLOAT", "Hz": "HERTZ", "ms": "MS", "%": "PERCENT", "dB": "DB", "bit": "INT"}

PREAMBLE = '''import json

import maxpylang as mp

patch = mp.MaxPatch(verbose=False)

# Live's parameter_unitstyle values: how Ableton prints a dial's number.
INT, FLOAT, MS, HERTZ, DB, PERCENT = 0, 1, 2, 3, 4, 5


def place(text, x, y, fit=True):
    obj = patch.place(text, verbose=False)[0]
    if fit:
        # An object box as wide as its text, so nothing wraps when Max opens the patch.
        obj.edit(patching_rect=[float(x), float(y), max(40.0, 6.6 * len(text) + 12.0), 22.0])
    else:
        obj.move(float(x), float(y))
    return obj


def box(obj):
    # maxpylang has no public way to set the box keys used below, so this reaches into
    # the box dict. Each use names its entry in docs/type-system/m4l-gaps.md.
    return obj._dict["box"]


def show(obj, x, y, w, h):
    """Put a box in presentation mode, the view Ableton shows. (gap M4L-02)"""
    box(obj)["presentation"] = 1
    box(obj)["presentation_rect"] = [float(x), float(y), float(w), float(h)]


def dial(longname, shortname, at, slot, lo, hi, init, unit, exponent=1.0):
    """A live.dial: a knob Ableton can automate, map and save with the set."""
    d = place("live.dial", *at, fit=False)
    show(d, slot[0], slot[1], 44, 48)
    box(d)["varname"] = longname.lower().replace(" ", "_")
    box(d)["activedialcolor"] = ACCENT
    # The rest of the dial in the device's colours: its ring, its pointer, its lettering.
    box(d)["dialcolor"] = TRACK
    box(d)["needlecolor"] = INK
    box(d)["activeneedlecolor"] = INK
    box(d)["textcolor"] = INK
    # The dial's range, unit and starting value. (gap M4L-01)
    box(d)["saved_attribute_attributes"] = {"valueof": {
        "parameter_longname": longname,
        "parameter_shortname": shortname,
        "parameter_type": 1 if unit == INT else 0,
        "parameter_mmin": float(lo),
        "parameter_mmax": float(hi),
        "parameter_initial_enable": 1,
        "parameter_initial": [float(init)],
        "parameter_unitstyle": unit,
        "parameter_exponent": float(exponent),
    }}
    return d


def label(text, at, slot, size=10.0, bold=False, color=None):
    """Text in the device view."""
    c = place("live.comment", *at, fit=False)
    box(c)["text"] = text  # (gap M4L-03)
    box(c)["fontsize"] = float(size)
    box(c)["fontface"] = 1 if bold else 0
    box(c)["textjustification"] = 1 if len(slot) == 4 else 0
    if color:
        box(c)["textcolor"] = color
    show(c, slot[0], slot[1], slot[2] if len(slot) > 2 else 90, 18)
    return c


def types(module, text, at, slot):
    """The type a module is (Hall, Tube), under its title on a card with no picture."""
    c = label(text, at, slot, size=9.0, color=ACCENT)
    box(c)["varname"] = "%s_types" % module
    return c


def card(at, x, w, color):
    """A rounded panel behind one module's dials."""
    p = place("panel", *at, fit=False)
    p.edit(patching_rect=[float(at[0]), float(at[1]), 40.0, 20.0])
    box(p)["bgcolor"] = color
    box(p)["rounded"] = 8
    box(p)["background"] = 1  # (gap M4L-02)
    show(p, x, 6, w, 157)
    return p


# The pictures' drawing code (graphs.js and graph-max.js, next to fxgen.py). fxgen hands
# it in when it runs this script; run by hand without it, the pictures are empty boxes
# and the dials work the same.
GRAPH_SOURCE = globals().get("GRAPH_SOURCE", "")


def graph(kind, at, slot, options, dials):
    """
    A picture of a module above its dials, which can be dragged: a v8ui (Max 9, so Live
    12.2 or newer) carrying its own drawing code. `dials` is [(id, the live.dial), ...].
    Each dial tells the picture its value, and a drag on the picture sets the dials, so
    the dials stay the parameters Ableton automates and saves.
    """
    g = place("jsui", *at, fit=False)
    b = box(g)
    setup = {
        "kind": kind,
        "options": options,
        "dials": [{"id": name, "min": v["parameter_mmin"], "max": v["parameter_mmax"],
                   "exponent": v["parameter_exponent"], "value": v["parameter_initial"][0]}
                  for name, v in ((name, box(d)["saved_attribute_attributes"]["valueof"]) for name, d in dials)],
        "colours": {"ink": INK[:3], "track": TRACK[:3], "accent": ACCENT[:3], "card": CARD[:3], "well": BG},
    }
    # maxpylang knows jsui and not yet v8ui, so it is placed as one and renamed. Its
    # cord out is the one cord in the device that maxpylang's checks cannot see into.
    b["maxclass"] = "v8ui"
    b.pop("text", None)
    b["filename"] = "none"
    b["border"] = 0
    b["varname"] = "%s_graph" % kind
    b["textfile"] = {"text": "var PP = %s;\\n%s" % (json.dumps(setup), GRAPH_SOURCE),
                     "filename": "none", "flags": 0, "embed": 1, "autowatch": 1}
    show(g, *slot)
    back = place("route " + " ".join(name for name, _ in dials), at[0], at[1] + 30)
    cords = [[g.outs[0], back.ins[0]]]
    for i, (name, d) in enumerate(dials):
        tell = place("prepend " + name, at[0] + 170 + i * 110, at[1])
        cords += [[d.outs[0], tell.ins[0]], [tell.outs[0], g.ins[0]], [back.outs[i], d.ins[0]]]
    patch.connect(*cords, verbose=False)
    return g


def note(text, x, y):
    """A comment in the patching view."""
    c = place("comment", x, y, fit=False)
    c.edit(patching_rect=[float(x), float(y), 260.0, 20.0])
    box(c)["text"] = text  # after edit(), which rewrites the text key (gap M4L-03)
    return c
'''


def fnum(v):
    """A number as Max box text: floats always carry a decimal point (`0.5`, `1800.`)."""
    s = "%g" % float(v)   # six significant digits
    if "e" in s:
        s = ("%.8f" % float(v)).rstrip("0")
    return s if "." in s else s + "."


def pynum(v):
    return repr(round(float(v), 4))


def theme_of(settings):
    """The device's colours: its theme's, or the first theme's."""
    themes = schema().get("themes") or [{"id": "blush", "bg": "#feddd4", "card": "#fdf2eb", "ink": "#2f1007", "track": "#e6bdb3"}]
    return next((t for t in themes if t["id"] == settings.get("theme")), themes[0])


def _mix(a, b, t):
    """Two hex colours, t of the way from a to b."""
    ca, cb = (int(a.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)), (int(b.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    return "#" + "".join("%02x" % round(x + (y - x) * t) for x, y in zip(ca, cb))


def _rgba(hex_color, alpha=1.0):
    h = hex_color.lstrip("#")
    return [round(int(h[i:i + 2], 16) / 255.0, 3) for i in (0, 2, 4)] + [alpha]


class Script:
    """Collects the lines of the emitted program, one module section at a time."""

    def __init__(self):
        self.lines = []
        self.wires = []
        self.names = set()

    def emit(self, line=""):
        self.lines.append(line)

    def name(self, var):
        """
        Claim a variable name in the emitted program. A name used twice would quietly
        rebind it, and every cord written against the first object would land on the
        second, so it is an error here rather than a wrong patch later.
        """
        if var in self.names:
            raise ValueError("fxgen: the name %r is already taken in the generated script" % var)
        self.names.add(var)
        return var

    def place(self, var, text, x, y):
        self.emit('%s = place("%s", %d, %d)' % (self.name(var), text, x, y))
        return var

    def wire(self, src, dst):
        self.wires.append((src, dst))

    def flush(self):
        """Write the section's cords as one connect() call."""
        if not self.wires:
            return
        self.emit("patch.connect(")
        for src, dst in self.wires:
            self.emit("    [%s, %s]," % (src, dst))
        self.emit("    verbose=False,")
        self.emit(")")
        self.wires = []

    def text(self):
        return "\n".join(self.lines) + "\n"


class Device:
    """The emitter's state while it walks the chain: where things go, in both views."""

    def __init__(self, settings):
        self.s = Script()
        self.settings = settings
        self.y = 30
        self.math_rows = 0
        self.card_x = BRAND_W + CARD_GAP
        self.park_y = 30
        self.mod_rows = 0
        self.dial_y = DIAL_Y
        self.pictured = settings.get("look") != "dials"
        self.sources = {}   # motion source id -> the outlet carrying its signal
        self.routes = {}    # "module.dial" -> [(source id, amount), ...]

    # presentation -----------------------------------------------------------------

    def open_card(self, mod):
        """Reserve the next card in the device view; returns the x of its first dial."""
        n = len(mod["params"])
        w = CARD_PAD * 2 + n * DIAL_STEP - (DIAL_STEP - DIAL_W)
        w = max(w, 2 * CARD_PAD + 60)
        self.graph = None
        self.dial_y = DIAL_Y
        kinds = mod["options"][0] if mod.get("options") else None
        if self.pictured and mod["id"] in GRAPHS:
            w = max(w, 2 * CARD_PAD + GRAPH_MIN_W)
            self.dial_y = GRAPH_DIAL_Y
            self.graph = (self.card_x + CARD_PAD, GRAPH_Y, w - 2 * CARD_PAD, GRAPH_H)
        elif kinds:
            w = max(w, 2 * CARD_PAD + TYPE_W * len(kinds["choices"]))
        x = self.card_x
        self.s.emit("card((%d, %d), x=%d, w=%d, color=CARD)" % (XP, self.park_y, x, w))
        self.s.emit('label("%s", (%d, %d), (%d, %d, %d, 18), size=10.0, bold=True, color=INK)'
                    % (mod["label"].upper(), XP + 60, self.park_y, x, TITLE_Y, w))
        self.park_y += 30
        if kinds and not self.graph:
            chosen = self.options_of(mod["id"]).get(kinds["id"], kinds["default"])
            text = next((c["label"] for c in kinds["choices"] if c["id"] == chosen), chosen)
            self.s.emit('types("%s", "%s", (%d, %d), (%d, %d, %d, 18))'
                        % (mod["id"], text.upper(), XP + 60, self.park_y, x + CARD_PAD, TYPES_Y, w - 2 * CARD_PAD))
            self.park_y += 30
        self.card_x = x + w + CARD_GAP
        inner = n * DIAL_STEP - (DIAL_STEP - DIAL_W)
        return x + (w - inner) // 2

    def dial(self, mod, param, index, first_x, value):
        var = self.s.name("%s_%s" % (mod["id"], param["id"]))
        self.s.emit(
            '%s = dial("%s %s", "%s", at=(%d, %d), slot=(%d, %d), lo=%s, hi=%s, init=%s, unit=%s, exponent=%s)'
            % (var, mod["label"], param["label"], param.get("short", param["label"]),
               XC + index * DX, self.y + ROW, first_x + index * DIAL_STEP, self.dial_y,
               pynum(param["min"]), pynum(param["max"]), pynum(value),
               UNITSTYLE[param["unit"]], pynum(param.get("exponent", 1.0))))
        return var

    def dials(self, mod, values, math_rows=0):
        """
        Open a module's section: its dials on top, `math_rows` rows of control math under
        them, and the signal objects under those, so every cord in the section runs
        downward. ctl(k) and sig(k) give the y of the k-th row of each band.
        """
        self.math_rows = math_rows
        first_x = self.open_card(mod)
        moved = [p for p in mod["params"] if self.routes.get("%s.%s" % (mod["id"], p["id"]))]
        self.mod_rows = MOTION_ROWS if moved else 0
        out = {}
        drawn = []
        for i, p in enumerate(mod["params"]):
            out[p["id"]] = self.dial(mod, p, i, first_x, values[p["id"]])
            drawn.append((p["id"], out[p["id"]]))
            if i == len(mod["params"]) - 1 and self.graph:
                # after the last dial, which it reads, and before anything that moves one
                self.s.emit("%s = graph(%s, (%d, %d), %r, %s, [%s])" % (
                    self.s.name(mod["id"] + "_graph"), json.dumps(mod["id"]), XP + 200, self.park_y, self.graph,
                    repr(self.options_of(mod["id"])), ", ".join('("%s", %s)' % pair for pair in drawn)))
                self.park_y += 70
        for i, p in enumerate(mod["params"]):
            if p in moved:
                out[p["id"]] = self.moved(mod, p, i, out[p["id"]], values[p["id"]])
        return out

    def options_of(self, module):
        """A module's choices (a filter's mode, an oscillator's wave), which its picture draws by."""
        st = self.settings
        if module in (st.get("instrument") or {}):
            return dict(st["instrument"][module].get("options") or {})
        return dict(next((e.get("options") or {} for e in st.get("chain", []) if e["module"] == module), {}))

    def moved(self, mod, param, index, dial, value):
        """
        A dial that something moves. What reaches the module is no longer the dial's own
        number but this, worked out under the dial:

            where the dial stands (0 to 1) + each source * its amount, kept between 0 and 1,
            read off every few milliseconds, and put back through the dial's curve and range

        It is added up as signals, so an LFO, the follower and a knob can all push the same
        dial, and it is added to the dial's position, not its value, so a sweep of a
        frequency sounds even the way turning the knob does. Returns the name of the last
        object, which stands in for the dial: its left outlet carries the moved value.
        """
        s = self.s
        key = "%s_%s" % (mod["id"], param["id"])
        x = XC + index * DX
        row = lambda k: self.y + ROW + DIAL_ROW + k * ROW
        lo, hi, exponent = float(param["min"]), float(param["max"]), float(param.get("exponent", 1.0))
        position = (max(0.0, min(1.0, (value - lo) / (hi - lo))) if hi != lo else 0.0) ** (1.0 / exponent)
        s.place(key + "_pos", "sig~ %s" % fnum(position), x, row(0))
        s.place(key + "_sum", "clip~ 0. 1.", x, row(2))
        s.place(key + "_tick", "snapshot~ %d" % MOTION_MS, x, row(3))
        s.wire(dial + ".outs[1]", key + "_pos.ins[0]")   # a live.dial's right outlet: its position, 0 to 1
        s.wire(key + "_pos.outs[0]", key + "_sum.ins[0]")
        for j, (source, amount) in enumerate(self.routes["%s.%s" % (mod["id"], param["id"])]):
            by = s.place("%s_by%d" % (key, j + 1), "*~ %s" % fnum(amount / 100.0), x + 48 + j * 62, row(1))
            s.wire(self.sources[source], by + ".ins[0]")
            s.wire(by + ".outs[0]", key + "_sum.ins[0]")
        s.wire(key + "_sum.outs[0]", key + "_tick.ins[0]")
        last = key + "_tick"
        if exponent != 1.0:
            s.place(key + "_curve", "pow %s" % fnum(exponent), x, row(4))
            s.wire(last + ".outs[0]", key + "_curve.ins[0]")
            last = key + "_curve"
        s.place(key + "_mod", "scale 0. 1. %s %s" % (fnum(lo), fnum(hi)), x, row(5))
        s.wire(last + ".outs[0]", key + "_mod.ins[0]")
        return key + "_mod"

    def ctl(self, k):
        return self.y + ROW + DIAL_ROW + self.mod_rows * ROW + k * ROW

    def sig(self, k):
        return self.ctl(self.math_rows) + k * ROW

    def end(self, sig_rows):
        """Height of the section just emitted, given how many signal rows it used."""
        return self.sig(sig_rows) - self.y + GAP

    def header(self, title):
        self.s.emit()
        self.s.emit("# ── %s %s" % (title, "─" * max(4, 72 - len(title))))
        self.s.emit('note("=== %s ===", %d, %d)' % (title.upper(), X["L"], self.y))

    # shared sub-circuits ------------------------------------------------------------

    def mix(self, prefix, mix_dial, mix_value, dry, wet, row, ctl_row=0, equal_power=False):
        """
        Dry/wet crossfade for both channels. `dry` and `wet` map a channel to the outlet
        expression carrying it. `row` is the signal row the crossfade starts on; `ctl_row`
        the control row its math starts on. Returns the outgoing signal and the number of
        signal rows used so far.

        Linear (dry * (1 - mix) + wet * mix) is right when the wet signal is a copy of the
        dry one, as in a chorus: the two add up in step. Echoes and reverb are not in step
        with the dry signal, and a linear fade would dip in level around the middle, so
        those use an equal-power fade: dry * cos(mix * pi/2) + wet * sin(mix * pi/2).
        """
        s = self.s
        m = mix_value / 100.0
        y = self.sig(row)
        x = XC + 2 * DX
        if equal_power:
            turn = math.pi / 2
            dry_gain, wet_gain = math.cos(m * turn), math.sin(m * turn)
            s.place(prefix + "_turn", "* %s" % fnum(turn / 100.0), x, self.ctl(ctl_row))
            s.place(prefix + "_wet", "sin", x, self.ctl(ctl_row + 1))
            s.place(prefix + "_dry", "cos", x + 60, self.ctl(ctl_row + 1))
            s.wire("%s.outs[0]" % mix_dial, "%s_turn.ins[0]" % prefix)
            s.wire("%s_turn.outs[0]" % prefix, "%s_wet.ins[0]" % prefix)
            s.wire("%s_turn.outs[0]" % prefix, "%s_dry.ins[0]" % prefix)
        else:
            dry_gain, wet_gain = 1 - m, m
            s.place(prefix + "_wet", "* 0.01", x, self.ctl(ctl_row))
            s.place(prefix + "_dry", "!- 1.", x, self.ctl(ctl_row + 1))
            s.wire("%s.outs[0]" % mix_dial, "%s_wet.ins[0]" % prefix)
            s.wire("%s_wet.outs[0]" % prefix, "%s_dry.ins[0]" % prefix)
        out = {}
        for ch in ("L", "R"):
            d, w, a = "%s_dry%s" % (prefix, ch), "%s_wet%s" % (prefix, ch), "%s_out%s" % (prefix, ch)
            s.place(d, "*~ %s" % fnum(dry_gain), X[ch], y)
            s.place(w, "*~ %s" % fnum(wet_gain), X[ch] + SUB, y)
            s.place(a, "+~", X[ch], y + ROW)
            s.wire(dry[ch], "%s.ins[0]" % d)
            s.wire(wet[ch], "%s.ins[0]" % w)
            s.wire("%s_dry.outs[0]" % prefix, "%s.ins[1]" % d)
            s.wire("%s_wet.outs[0]" % prefix, "%s.ins[1]" % w)
            s.wire("%s.outs[0]" % d, "%s.ins[0]" % a)
            s.wire("%s.outs[0]" % w, "%s.ins[1]" % a)
            out[ch] = "%s.outs[0]" % a
        return out, row + 2


# ── motion: what moves the dials ────────────────────────────────────────────────

MOTION_ROWS = 6   # the rows Device.moved() takes under a dial
MOTION_MS = 5     # how often a moved dial's value is read off and sent on
LFO_SHAPES = {"sine": "cycle~", "triangle": "tri~", "square": "rect~", "saw": "saw~"}
FOLLOW_SMOOTH = 12.0   # Hz: how quickly the follower lets go of a peak


def motion_plan(st):
    """
    What of the settings' motion is in use: (routes by target, ids of the sources they
    need). A route to a dial of a module that is switched off moves nothing and is left out.
    """
    motion = st.get("motion") or {}
    instrument = st.get("kind") == "instrument"
    live = {e["module"] for e in st["chain"] if e["on"]} | {schema()["output"]["id"]}
    if instrument:
        live |= set(st["instrument"])
    routes, used = {}, []
    for route in motion.get("routes", []):
        if route["target"].split(".")[0] not in live:
            continue
        routes.setdefault(route["target"], []).append((route["source"], route["amount"]))
        if route["source"] not in used:
            used.append(route["source"])
    return routes, used


def m_motion(dev, st, used, incoming):
    """
    The sources in use, each ending in a signal that Device.moved() adds to a dial's
    position. An LFO swings between -1 and 1. The follower and a knob run from 0 to 1.
    Sets dev.sources. `incoming` is the device's input, for the follower.
    """
    s = dev.s
    motion = st["motion"]
    spec = schema()["motion"]
    for source in spec["sources"]:
        sid = source["id"]
        if sid not in used:
            continue
        dev.header(source["label"])
        d = dev.dials(source, motion[sid]["values"], math_rows=0)
        if source["kind"] == "lfo":
            osc = LFO_SHAPES[motion[sid]["options"]["shape"]]
            s.place(sid + "_osc", "%s %s" % (osc, fnum(motion[sid]["values"]["rate"])), XC, dev.sig(0))
            s.wire(d["rate"] + ".outs[0]", sid + "_osc.ins[0]")
            dev.sources[sid] = sid + "_osc.outs[0]"
            rows = 1
        else:
            # How loud the input is: both channels, the sign taken off, smoothed, scaled up
            # by the Sens dial and stopped at 1.
            s.place(sid + "_in", "*~ 0.5", X["L"], dev.sig(0))
            s.place(sid + "_abs", "abs~", X["L"], dev.sig(1))
            s.place(sid + "_smooth", "onepole~ %s" % fnum(FOLLOW_SMOOTH), X["L"], dev.sig(2))
            s.place(sid + "_gain", "*~ %s" % fnum(motion[sid]["values"]["sens"]), X["L"], dev.sig(3))
            s.place(sid + "_top", "clip~ 0. 1.", X["L"], dev.sig(4))
            for ch in ("L", "R"):
                s.wire(incoming[ch], sid + "_in.ins[0]")
            s.wire(sid + "_in.outs[0]", sid + "_abs.ins[0]")
            s.wire(sid + "_abs.outs[0]", sid + "_smooth.ins[0]")
            s.wire(sid + "_smooth.outs[0]", sid + "_gain.ins[0]")
            s.wire(d["sens"] + ".outs[0]", sid + "_gain.ins[1]")
            s.wire(sid + "_gain.outs[0]", sid + "_top.ins[0]")
            dev.sources[sid] = sid + "_top.outs[0]"
            rows = 5
        s.flush()
        dev.y += dev.end(rows)

    knobs = [k for k in motion["knobs"] if k["id"] in used]
    if knobs:
        # The user's own knobs, on one card: each is a dial from 0 to 100 %, as a signal from 0 to 1.
        spec_knob = spec["knobs"]
        mod = {"id": spec_knob["id"], "label": spec_knob["label"] + "s",
               "params": [dict(spec_knob["param"], id=k["id"], label=k["name"]) for k in knobs]}
        dev.header("knobs")
        d = dev.dials(mod, motion["knobValues"], math_rows=1)
        for i, k in enumerate(knobs):
            s.place(k["id"] + "_unit", "* 0.01", XC + i * DX, dev.ctl(0))
            s.place(k["id"] + "_sig", "sig~ %s" % fnum(motion["knobValues"][k["id"]] / 100.0), XC + i * DX, dev.sig(0))
            s.wire(d[k["id"]] + ".outs[0]", k["id"] + "_unit.ins[0]")
            s.wire(k["id"] + "_unit.outs[0]", k["id"] + "_sig.ins[0]")
            dev.sources[k["id"]] = k["id"] + "_sig.outs[0]"
        s.flush()
        dev.y += dev.end(1)


# Each module takes the device, the incoming signal per channel, its options and values,
# and returns the outgoing signal per channel plus how many rows it used.

# The Reso dial runs 0 to 100 percent and is mapped onto the useful part of svf~'s 0 to 1
# resonance. Measured in Max, svf~ is flat until about 0.17, and by 0.9 its peak is 36 dB
# high and rings for seconds, so the dial stops at 0.75 (a peak of about 21 dB).
SVF_RES = (0.17, 0.75)


def svf_resonance(percent):
    lo, hi = SVF_RES
    return lo + (hi - lo) * float(percent) / 100.0


def filter_trim(percent):
    """Input gain that falls as the resonance rises, so the peak has headroom to ring in."""
    return 1.0 - 0.005 * float(percent)


def m_filter(dev, src, opt, val):
    s = dev.s
    d = dev.dials(_module("filter"), val, math_rows=2)
    outlet = {"lowpass": 0, "highpass": 1, "bandpass": 2, "notch": 3}[opt["mode"]]
    s.place("filter_reso", "scale 0. 100. %s %s" % (fnum(SVF_RES[0]), fnum(SVF_RES[1])), XC + DX, dev.ctl(0))
    # trim = 1 - reso / 200: half the level at full resonance
    s.place("filter_half", "* -0.005", XC + 2 * DX + 40, dev.ctl(0))
    s.place("filter_trim", "+ 1.", XC + 2 * DX + 40, dev.ctl(1))
    s.wire(d["res"] + ".outs[0]", "filter_reso.ins[0]")
    s.wire(d["res"] + ".outs[0]", "filter_half.ins[0]")
    s.wire("filter_half.outs[0]", "filter_trim.ins[0]")
    out = {}
    for ch in ("L", "R"):
        g = s.place("filter_in" + ch, "*~ %s" % fnum(filter_trim(val["res"])), X[ch], dev.sig(0))
        v = s.place("filter_" + ch, "svf~ %s %s" % (fnum(val["cutoff"]), fnum(svf_resonance(val["res"]))), X[ch], dev.sig(1))
        s.wire(src[ch], g + ".ins[0]")
        s.wire("filter_trim.outs[0]", g + ".ins[1]")
        s.wire(g + ".outs[0]", v + ".ins[0]")
        s.wire(d["cutoff"] + ".outs[0]", v + ".ins[1]")
        s.wire("filter_reso.outs[0]", v + ".ins[2]")
        out[ch] = "%s.outs[%d]" % (v, outlet)  # svf~ outlets: lowpass, highpass, bandpass, notch
    return out, 2


# The EQ is three bands added to the sound itself, each by how far its gain is from 1:
#
#   out = in + (low - 1) * lows + (mid - 1) * middle + (high - 1) * highs
#
# The lows are a onepole~ low-pass and the highs are what a second onepole~ leaves behind
# (in minus its low-pass), which makes two gentle shelves. Left at that, a cut would not
# be the mirror of a boost: -12 dB would take far less away than +12 dB adds. It is the
# mirror if the corner moves with the gain, down for the low shelf and up for the high
# one as the gain rises (corner = centre / sqrt(gain), and centre * sqrt(gain)), which
# also puts exactly half the gain, in dB, at the centre frequency whatever the gain is.
#
# The middle is svf~'s band-pass at the resonance where its peak is exactly as loud as
# what went in, so a Mid of +6 dB is +6 dB at the Freq dial, tapering off about an octave
# either side. svf~'s band-pass comes out a sample ahead of the sound it was made from.
# That does not matter to a boost, but a cut works by cancelling, and at 2.5 kHz a sample
# is 20 degrees: -12 dB came out as -8.6. So the band is held back one sample (a biquad~
# that only delays) before it is added, and a cut is as deep as its dial says.
#
# onepole~ does not quite reach silence at the top of the range, so the high shelf by
# itself stops about a fifth short of its dial. Its band is scaled up to make up for it
# (EQ_HIGH_FIT * gain ** EQ_HIGH_BEND, fitted so that 10 kHz reads the dial to within 0.7 dB).
#
# With every gain at 0 dB the three terms are zero and the sound passes untouched.
EQ_LOW_HZ = 250.0
EQ_HIGH_HZ = 2500.0
EQ_HIGH_FIT, EQ_HIGH_BEND = 1.37, 0.13   # the pair with the least error at 10 kHz over -15 to +15 dB: 0.63 dB
EQ_ALIGN = "biquad~ 0. 1. 0. 0. 0."   # y[n] = x[n-1]: one sample later, nothing else


def eq_amount(db):
    """How much of a band is added for a gain in dB: its gain, less the 1 already there."""
    return 10 ** (float(db) / 20.0) - 1.0


def eq_corner(centre, db, power):
    """A shelf's corner for a gain in dB: centre * gain ** power (-0.5 for the low shelf, 0.5 for the high)."""
    return centre * (10 ** (float(db) / 20.0)) ** power


def eq_high_fit(db):
    """What the high band is scaled by, to reach its dial at the top of the range."""
    return EQ_HIGH_FIT * (10 ** (float(db) / 20.0)) ** EQ_HIGH_BEND


def m_eq(dev, src, opt, val):
    s = dev.s
    d = dev.dials(_module("eq"), val, math_rows=6)
    for i, band in enumerate(("low", "mid", None, "high")):
        if band is None:
            continue   # the third dial is the middle's frequency, which goes straight to svf~
        x = XC + i * DX
        s.place("eq_%s_amp" % band, "dbtoa", x, dev.ctl(0))
        s.place("eq_%s_add" % band, "+ -1.", x, dev.ctl(1))
        s.wire(d[band] + ".outs[0]", "eq_%s_amp.ins[0]" % band)
        s.wire("eq_%s_amp.outs[0]" % band, "eq_%s_add.ins[0]" % band)
    for band, centre, power in (("low", EQ_LOW_HZ, -0.5), ("high", EQ_HIGH_HZ, 0.5)):
        x = XC + (0 if band == "low" else 3) * DX
        s.place("eq_%s_root" % band, "pow %s" % fnum(power), x, dev.ctl(2))
        s.place("eq_%s_hz" % band, "* %s" % fnum(centre), x, dev.ctl(3))
        s.wire("eq_%s_amp.outs[0]" % band, "eq_%s_root.ins[0]" % band)
        s.wire("eq_%s_root.outs[0]" % band, "eq_%s_hz.ins[0]" % band)
    s.place("eq_high_bend", "pow %s" % fnum(EQ_HIGH_BEND), XC + 3 * DX, dev.ctl(4))
    s.place("eq_high_fit", "* %s" % fnum(EQ_HIGH_FIT), XC + 3 * DX, dev.ctl(5))
    s.wire("eq_high_amp.outs[0]", "eq_high_bend.ins[0]")
    s.wire("eq_high_bend.outs[0]", "eq_high_fit.ins[0]")
    out = {}
    for ch in ("L", "R"):
        lo = s.place("eq_lows" + ch, "onepole~ %s" % fnum(eq_corner(EQ_LOW_HZ, val["low"], -0.5)), X[ch], dev.sig(0))
        lo_g = s.place("eq_low" + ch, "*~ %s" % fnum(eq_amount(val["low"])), X[ch] + SUB, dev.sig(0))
        mid = s.place("eq_middle" + ch, "svf~ %s %s" % (fnum(val["freq"]), fnum(SVF_RES[0])), X[ch], dev.sig(1))
        late = s.place("eq_align" + ch, EQ_ALIGN, X[ch] + SUB, dev.sig(1))
        mid_g = s.place("eq_mid" + ch, "*~ %s" % fnum(eq_amount(val["mid"])), X[ch] + SUB, dev.sig(2))
        under = s.place("eq_under" + ch, "onepole~ %s" % fnum(eq_corner(EQ_HIGH_HZ, val["high"], 0.5)), X[ch], dev.sig(3))
        flip = s.place("eq_flip" + ch, "*~ -1.", X[ch] + SUB, dev.sig(3))
        highs = s.place("eq_highs" + ch, "+~", X[ch], dev.sig(4))
        hi_g = s.place("eq_high" + ch, "*~ %s" % fnum(eq_amount(val["high"])), X[ch] + SUB, dev.sig(4))
        fit = s.place("eq_fit" + ch, "*~ %s" % fnum(eq_high_fit(val["high"])), X[ch] + SUB, dev.sig(5))
        total = s.place("eq_" + ch, "+~", X[ch], dev.sig(6))
        s.wire(src[ch], lo + ".ins[0]")
        s.wire("eq_low_hz.outs[0]", lo + ".ins[1]")
        s.wire(lo + ".outs[0]", lo_g + ".ins[0]")
        s.wire("eq_low_add.outs[0]", lo_g + ".ins[1]")
        s.wire(src[ch], mid + ".ins[0]")
        s.wire(d["freq"] + ".outs[0]", mid + ".ins[1]")
        s.wire(mid + ".outs[2]", late + ".ins[0]")   # svf~ outlets: lowpass, highpass, bandpass, notch
        s.wire(late + ".outs[0]", mid_g + ".ins[0]")
        s.wire("eq_mid_add.outs[0]", mid_g + ".ins[1]")
        s.wire(src[ch], under + ".ins[0]")
        s.wire("eq_high_hz.outs[0]", under + ".ins[1]")
        s.wire(under + ".outs[0]", flip + ".ins[0]")
        s.wire(src[ch], highs + ".ins[0]")
        s.wire(flip + ".outs[0]", highs + ".ins[0]")
        s.wire(highs + ".outs[0]", hi_g + ".ins[0]")
        s.wire("eq_high_add.outs[0]", hi_g + ".ins[1]")
        s.wire(hi_g + ".outs[0]", fit + ".ins[0]")
        s.wire("eq_high_fit.outs[0]", fit + ".ins[1]")
        for part in (src[ch], lo_g + ".outs[0]", mid_g + ".outs[0]", fit + ".outs[0]"):
            s.wire(part, total + ".ins[0]")
        out[ch] = total + ".outs[0]"
    return out, 7


# ── more kinds of EQ ────────────────────────────────────────────────────────────
#
# The same idea as the three-band EQ, with other shapes: every band is the sound through
# a filter, added back by how far its gain is from 1, so 0 dB everywhere is the sound
# untouched.
#
# A bell is svf~'s band-pass. Its width is svf~'s damping, which is 1 / Q, so the Q dial
# is turned into svf~'s resonance (1 - Q ** -0.5 / 1.2, the inverse of what svf~ does with
# it), and the band is scaled by 1 / Q because a narrower svf~ band-pass is also a louder
# one. Q runs from 1, the widest svf~ goes, to 8. A cut made this way is narrower than the
# same boost, as it is on a good many hardware EQs.
#
# A shelf is onepole~ for the lows, and the sound less onepole~ for the highs, with the
# corner moved by the gain as the three-band EQ's are (corner = freq * gain ** -+0.5), so
# a cut mirrors a boost. The corner is a dial times a number worked out from another dial,
# and either can change. Max's `*` only answers its left inlet, so there are two of them,
# each with one of the pair on the left: whichever dial moves, the one it is on the left
# of sends the product.

GEQ_BANDS = (100.0, 230.0, 520.0, 1200.0, 2700.0)   # the bells; the sixth band is a shelf
GEQ_SHELF_HZ = 3500.0   # where the top band is half way up: it is level from about 6 kHz
GEQ_Q = 1.6   # bands 1.2 octaves apart: wide enough to meet, narrow enough to be five
PEQ_ROWS = 11


def bell_resonance(q):
    """svf~'s resonance for a band-pass of this Q."""
    return 1.0 - float(q) ** -0.5 / 1.2


def bell(dev, key, src, freq, res, amount, row, col=0):
    """One bell on both channels: returns what to add to the sound, per channel."""
    s = dev.s
    out = {}
    for ch in ("L", "R"):
        x = X[ch] + col
        band = s.place("%s_band%s" % (key, ch), "svf~ %s %s" % (fnum(freq[1]), fnum(res[1])), x, dev.sig(row))
        late = s.place("%s_align%s" % (key, ch), EQ_ALIGN, x, dev.sig(row + 1))
        level = s.place("%s_level%s" % (key, ch), "*~ %s" % fnum(amount[1]), x, dev.sig(row + 2))
        s.wire(src[ch], band + ".ins[0]")
        if freq[0]:
            s.wire(freq[0], band + ".ins[1]")
        if res[0]:
            s.wire(res[0], band + ".ins[2]")
        s.wire(band + ".outs[2]", late + ".ins[0]")   # svf~ outlets: lowpass, highpass, bandpass, notch
        s.wire(late + ".outs[0]", level + ".ins[0]")
        s.wire(amount[0], level + ".ins[1]")
        out[ch] = level + ".outs[0]"
    return out


def m_peq(dev, src, opt, val):
    s = dev.s
    d = dev.dials(_module("peq"), val, math_rows=5)
    col = {"lf": 0, "lg": 1, "f1": 2, "g1": 3, "q1": 4, "f2": 5, "g2": 6, "q2": 7, "hf": 8, "hg": 9}
    at = lambda name: XC + col[name] * DX
    parts = {"L": [src["L"]], "R": [src["R"]]}

    # the two shelves
    for band, freq, gain_id, power in (("low", "lf", "lg", -0.5), ("high", "hf", "hg", 0.5)):
        key = "peq_" + band
        g = 10 ** (val[gain_id] / 20.0)
        s.place(key + "_amp", "dbtoa", at(gain_id), dev.ctl(0))
        s.place(key + "_add", "+ -1.", at(gain_id), dev.ctl(1))
        s.place(key + "_root", "pow %s" % fnum(power), at(gain_id) + 60, dev.ctl(1))
        s.place(key + "_hz", "* %s" % fnum(g ** power), at(freq), dev.ctl(2))        # the freq dial moved
        s.place(key + "_hz2", "* %s" % fnum(val[freq]), at(gain_id) + 60, dev.ctl(2))   # the gain dial moved
        s.wire(d[gain_id] + ".outs[0]", key + "_amp.ins[0]")
        s.wire(key + "_amp.outs[0]", key + "_add.ins[0]")
        s.wire(key + "_amp.outs[0]", key + "_root.ins[0]")
        s.wire(d[freq] + ".outs[0]", key + "_hz.ins[0]")
        s.wire(key + "_root.outs[0]", key + "_hz.ins[1]")
        s.wire(key + "_root.outs[0]", key + "_hz2.ins[0]")
        s.wire(d[freq] + ".outs[0]", key + "_hz2.ins[1]")
        for ch in ("L", "R"):
            x = X[ch] + (0 if band == "low" else SUB)
            lows = s.place("%s_pole%s" % (key, ch), "onepole~ %s" % fnum(val[freq] * g ** power), x, dev.sig(0))
            s.wire(src[ch], lows + ".ins[0]")
            s.wire(key + "_hz.outs[0]", lows + ".ins[1]")
            s.wire(key + "_hz2.outs[0]", lows + ".ins[1]")
            part = lows
            if band == "high":
                flip = s.place("%s_flip%s" % (key, ch), "*~ -1.", x, dev.sig(1))
                rest = s.place("%s_rest%s" % (key, ch), "+~", x, dev.sig(2))
                s.wire(lows + ".outs[0]", flip + ".ins[0]")
                s.wire(src[ch], rest + ".ins[0]")
                s.wire(flip + ".outs[0]", rest + ".ins[0]")
                part = rest
            level = s.place("%s_level%s" % (key, ch), "*~ %s" % fnum(g - 1.0), x, dev.sig(3))
            s.wire(part + ".outs[0]", level + ".ins[0]")
            s.wire(key + "_add.outs[0]", level + ".ins[1]")
            if band == "high":
                # made up for what onepole~ leaves at the top, as the three-band EQ's is
                fit = s.place("%s_fit%s" % (key, ch), "*~ %s" % fnum(eq_high_fit(val[gain_id])), x, dev.sig(4))
                s.wire(level + ".outs[0]", fit + ".ins[0]")
                s.wire(key + "_fit.outs[0]", fit + ".ins[1]")
                level = fit
            parts[ch].append(level + ".outs[0]")
        if band == "high":
            s.place(key + "_bend", "pow %s" % fnum(EQ_HIGH_BEND), at(gain_id), dev.ctl(3))
            s.place(key + "_fit", "* %s" % fnum(EQ_HIGH_FIT), at(gain_id), dev.ctl(4))
            s.wire(key + "_amp.outs[0]", key + "_bend.ins[0]")
            s.wire(key + "_bend.outs[0]", key + "_fit.ins[0]")

    # the two bells
    for n in ("1", "2"):
        key = "peq_bell" + n
        f, g, q = val["f" + n], 10 ** (val["g" + n] / 20.0), val["q" + n]
        s.place(key + "_amp", "dbtoa", at("g" + n), dev.ctl(0))
        s.place(key + "_add", "+ -1.", at("g" + n), dev.ctl(1))
        s.place(key + "_thin", "pow -1.", at("q" + n), dev.ctl(0))                    # 1 / Q
        s.place(key + "_amt", "* %s" % fnum(1.0 / q), at("g" + n), dev.ctl(2))        # the gain dial moved
        s.place(key + "_amt2", "* %s" % fnum(g - 1.0), at("q" + n), dev.ctl(2))       # the Q dial moved
        s.place(key + "_damp", "pow -0.5", at("q" + n) + 60, dev.ctl(0))
        s.place(key + "_span", "* %s" % fnum(-1.0 / 1.2), at("q" + n) + 60, dev.ctl(1))
        s.place(key + "_res", "+ 1.", at("q" + n) + 60, dev.ctl(3))
        s.wire(d["g" + n] + ".outs[0]", key + "_amp.ins[0]")
        s.wire(key + "_amp.outs[0]", key + "_add.ins[0]")
        s.wire(d["q" + n] + ".outs[0]", key + "_thin.ins[0]")
        s.wire(key + "_add.outs[0]", key + "_amt.ins[0]")
        s.wire(key + "_thin.outs[0]", key + "_amt.ins[1]")
        s.wire(key + "_thin.outs[0]", key + "_amt2.ins[0]")
        s.wire(key + "_add.outs[0]", key + "_amt2.ins[1]")
        s.wire(d["q" + n] + ".outs[0]", key + "_damp.ins[0]")
        s.wire(key + "_damp.outs[0]", key + "_span.ins[0]")
        s.wire(key + "_span.outs[0]", key + "_res.ins[0]")
        out = bell(dev, key, src, (d["f" + n] + ".outs[0]", f), (key + "_res.outs[0]", bell_resonance(q)),
                   (key + "_amt.outs[0]", (g - 1.0) / q), 5 if n == "1" else 8)
        for ch in ("L", "R"):
            s.wire(key + "_amt2.outs[0]", "%s_level%s.ins[1]" % (key, ch))
            parts[ch].append(out[ch])

    result = {}
    for ch in ("L", "R"):
        total = s.place("peq_" + ch, "+~", X[ch], dev.sig(PEQ_ROWS))
        for part in parts[ch]:
            s.wire(part, total + ".ins[0]")
        result[ch] = total + ".outs[0]"
    return result, PEQ_ROWS + 1


def m_geq(dev, src, opt, val):
    s = dev.s
    mod = _module("geq")
    d = dev.dials(mod, val, math_rows=3)
    parts = {"L": [src["L"]], "R": [src["R"]]}
    top = mod["params"][-1]["id"]
    g = 10 ** (val[top] / 20.0)
    s.place("geq_top_amp", "dbtoa", XC + 5 * DX, dev.ctl(0))
    s.place("geq_top_add", "+ -1.", XC + 5 * DX, dev.ctl(1))
    s.place("geq_top_root", "pow 0.5", XC + 5 * DX + 60, dev.ctl(1))
    s.place("geq_top_hz", "* %s" % fnum(GEQ_SHELF_HZ), XC + 5 * DX + 60, dev.ctl(2))
    s.place("geq_top_bend", "pow %s" % fnum(EQ_HIGH_BEND), XC + 6 * DX, dev.ctl(1))
    s.place("geq_top_fit", "* %s" % fnum(EQ_HIGH_FIT), XC + 6 * DX, dev.ctl(2))
    s.wire(d[top] + ".outs[0]", "geq_top_amp.ins[0]")
    for a, b in (("amp", "add"), ("amp", "root"), ("root", "hz"), ("amp", "bend"), ("bend", "fit")):
        s.wire("geq_top_%s.outs[0]" % a, "geq_top_%s.ins[0]" % b)
    for ch in ("L", "R"):
        x = X[ch] + SUB
        lows = s.place("geq_top_pole" + ch, "onepole~ %s" % fnum(eq_corner(GEQ_SHELF_HZ, val[top], 0.5)), x, dev.sig(6))
        flip = s.place("geq_top_flip" + ch, "*~ -1.", x, dev.sig(7))
        rest = s.place("geq_top_rest" + ch, "+~", X[ch], dev.sig(7))
        level = s.place("geq_top_level" + ch, "*~ %s" % fnum(g - 1.0), X[ch], dev.sig(8))
        fit = s.place("geq_top_fit" + ch, "*~ %s" % fnum(eq_high_fit(val[top])), x, dev.sig(8))
        s.wire(src[ch], lows + ".ins[0]")
        s.wire("geq_top_hz.outs[0]", lows + ".ins[1]")
        s.wire(lows + ".outs[0]", flip + ".ins[0]")
        s.wire(src[ch], rest + ".ins[0]")
        s.wire(flip + ".outs[0]", rest + ".ins[0]")
        s.wire(rest + ".outs[0]", level + ".ins[0]")
        s.wire("geq_top_add.outs[0]", level + ".ins[1]")
        s.wire(level + ".outs[0]", fit + ".ins[0]")
        s.wire("geq_top_fit.outs[0]", fit + ".ins[1]")
        parts[ch].append(fit + ".outs[0]")
    for i, (p, hz) in enumerate(zip(mod["params"], GEQ_BANDS)):
        key = "geq_" + p["id"]
        x = XC + i * DX
        s.place(key + "_amp", "dbtoa", x, dev.ctl(0))
        s.place(key + "_add", "+ -1.", x, dev.ctl(1))
        s.place(key + "_amt", "* %s" % fnum(1.0 / GEQ_Q), x, dev.ctl(2))
        s.wire(d[p["id"]] + ".outs[0]", key + "_amp.ins[0]")
        s.wire(key + "_amp.outs[0]", key + "_add.ins[0]")
        s.wire(key + "_add.outs[0]", key + "_amt.ins[0]")
        amount = (10 ** (val[p["id"]] / 20.0) - 1.0) / GEQ_Q
        out = bell(dev, key, src, (None, hz), (None, bell_resonance(GEQ_Q)), (key + "_amt.outs[0]", amount),
                   3 * (i // 2), SUB * (i % 2))
        for ch in ("L", "R"):
            parts[ch].append(out[ch])
    result = {}
    for ch in ("L", "R"):
        total = s.place("geq_" + ch, "+~", X[ch], dev.sig(9))
        for part in parts[ch]:
            s.wire(part, total + ".ins[0]")
        result[ch] = total + ".outs[0]"
    return result, 10


def m_tilt(dev, src, opt, val):
    """
    Tilt: the lows by one gain and the highs by its opposite, split by one onepole~.
    out = highs * in + (lows - highs) * onepole~(in), written as three multiplies so each
    gain comes straight from the dial. A split at the Pivot dial itself would leave the
    pivot louder or quieter than it went in, because the two halves meet a quarter turn
    apart; with the corner at pivot * highs, the pivot is where the level is unchanged.
    (That product is the two `*` again: see the parametric EQ's shelves.)
    """
    s = dev.s
    d = dev.dials(_module("tilt"), val, math_rows=2)
    up, down = 10 ** (val["tilt"] / 20.0), 10 ** (-val["tilt"] / 20.0)
    s.place("tilt_highs", "dbtoa", XC, dev.ctl(0))
    s.place("tilt_flip", "* -1.", XC + DX, dev.ctl(0))
    s.place("tilt_lows", "dbtoa", XC + DX, dev.ctl(1))
    s.place("tilt_less", "* -1.", XC, dev.ctl(1))
    s.wire(d["tilt"] + ".outs[0]", "tilt_highs.ins[0]")
    s.wire(d["tilt"] + ".outs[0]", "tilt_flip.ins[0]")
    s.wire("tilt_flip.outs[0]", "tilt_lows.ins[0]")
    s.wire("tilt_highs.outs[0]", "tilt_less.ins[0]")
    s.place("tilt_hz", "* %s" % fnum(up), XC + 2 * DX, dev.ctl(0))              # the pivot dial moved
    s.place("tilt_hz2", "* %s" % fnum(val["pivot"]), XC + 2 * DX, dev.ctl(1))   # the tilt dial moved
    s.wire(d["pivot"] + ".outs[0]", "tilt_hz.ins[0]")
    s.wire("tilt_highs.outs[0]", "tilt_hz.ins[1]")
    s.wire("tilt_highs.outs[0]", "tilt_hz2.ins[0]")
    s.wire(d["pivot"] + ".outs[0]", "tilt_hz2.ins[1]")
    out = {}
    for ch in ("L", "R"):
        pole = s.place("tilt_pole" + ch, "onepole~ %s" % fnum(val["pivot"] * up), X[ch], dev.sig(0))
        all_ = s.place("tilt_all" + ch, "*~ %s" % fnum(up), X[ch] + SUB, dev.sig(0))
        lows = s.place("tilt_low" + ch, "*~ %s" % fnum(down), X[ch], dev.sig(1))
        less = s.place("tilt_less" + ch, "*~ %s" % fnum(-up), X[ch] + SUB, dev.sig(1))
        total = s.place("tilt_" + ch, "+~", X[ch], dev.sig(2))
        s.wire(src[ch], pole + ".ins[0]")
        s.wire("tilt_hz.outs[0]", pole + ".ins[1]")
        s.wire("tilt_hz2.outs[0]", pole + ".ins[1]")
        s.wire(src[ch], all_ + ".ins[0]")
        s.wire("tilt_highs.outs[0]", all_ + ".ins[1]")
        s.wire(pole + ".outs[0]", lows + ".ins[0]")
        s.wire("tilt_lows.outs[0]", lows + ".ins[1]")
        s.wire(pole + ".outs[0]", less + ".ins[0]")
        s.wire("tilt_less.outs[0]", less + ".ins[1]")
        for part in (all_, lows, less):
            s.wire(part + ".outs[0]", total + ".ins[0]")
        out[ch] = total + ".outs[0]"
    return out, 3


# ── the compressor ──────────────────────────────────────────────────────────────
#
# Feed-forward, with the two channels linked so the stereo image holds still:
#
#     max(|L|, |R|) ──▶ slide~ (attack up, release down) ──▶ atodb~ ──▶ less the threshold
#        ──▶ nothing below zero ──▶ times (1 / ratio - 1) ──▶ plus makeup ──▶ dbtoa~ ──▶ gain
#
# and that gain times each channel. The louder of the two channels is
# (|L| + |R| + ||L| - |R||) / 2, which needs nothing but abs~, *~ and +~.
#
# slide~ counts in samples, and a plugin may run at 44.1 kHz or 48 or 96. So the attack
# and release dials go through sig~ into mstosamps~, which knows the rate it runs at, and
# reach slide~ as signals. Its level is floored at -100 dB (clip~) before atodb~, so
# silence has a level to take the threshold from. Mix is the parallel blend: the sound as
# it came in, under the compressed one with its makeup.

COMP_FLOOR = 0.00001   # -100 dB


def comp_slope(ratio):
    """dB taken off for each dB over the threshold."""
    return 1.0 / float(ratio) - 1.0


def m_comp(dev, src, opt, val):
    s = dev.s
    d = dev.dials(_module("comp"), val, math_rows=3)
    x = lambda i: XC + i * DX
    s.place("comp_less", "* -1.", x(0), dev.ctl(0))
    s.place("comp_inverse", "pow -1.", x(1), dev.ctl(0))
    s.place("comp_slope", "+ -1.", x(1), dev.ctl(1))
    s.wire(d["thresh"] + ".outs[0]", "comp_less.ins[0]")
    s.wire(d["ratio"] + ".outs[0]", "comp_inverse.ins[0]")
    s.wire("comp_inverse.outs[0]", "comp_slope.ins[0]")
    for k, (name, i) in enumerate((("attack", 2), ("release", 3))):
        s.place("comp_%s_sig" % name, "sig~ %s" % fnum(val[name]), x(i), dev.ctl(0))
        s.place("comp_%s_n" % name, "mstosamps~", x(i), dev.ctl(1))
        s.wire(d[name] + ".outs[0]", "comp_%s_sig.ins[0]" % name)
        s.wire("comp_%s_sig.outs[0]" % name, "comp_%s_n.ins[0]" % name)

    # the level of the louder channel, in one column
    col, sub = X["L"], X["R"]
    absl = s.place("comp_absL", "abs~", col, dev.sig(0))
    absr = s.place("comp_absR", "abs~", sub, dev.sig(0))
    flip = s.place("comp_flip", "*~ -1.", sub, dev.sig(1))
    gap = s.place("comp_gap", "+~", col, dev.sig(1))
    apart = s.place("comp_apart", "abs~", col, dev.sig(2))
    both = s.place("comp_both", "+~", col, dev.sig(3))
    peak = s.place("comp_peak", "*~ 0.5", col, dev.sig(4))
    s.wire(src["L"], absl + ".ins[0]")
    s.wire(src["R"], absr + ".ins[0]")
    s.wire(absr + ".outs[0]", flip + ".ins[0]")
    s.wire(absl + ".outs[0]", gap + ".ins[0]")
    s.wire(flip + ".outs[0]", gap + ".ins[0]")
    s.wire(gap + ".outs[0]", apart + ".ins[0]")
    for part in (absl, absr, apart):
        s.wire(part + ".outs[0]", both + ".ins[0]")
    s.wire(both + ".outs[0]", peak + ".ins[0]")

    # the gain computer
    rate = 44.1   # what the slides start at, until mstosamps~ says (it does on the first sample)
    follow = s.place("comp_follow", "slide~ %s %s" % (fnum(val["attack"] * rate), fnum(val["release"] * rate)), col, dev.sig(5))
    floor = s.place("comp_floor", "clip~ %s 1000." % fnum(COMP_FLOOR), col, dev.sig(6))
    level = s.place("comp_level", "atodb~", col, dev.sig(7))
    over = s.place("comp_over", "+~ %s" % fnum(-val["thresh"]), col, dev.sig(8))
    above = s.place("comp_above", "clip~ 0. 1000.", col, dev.sig(9))
    cut = s.place("comp_cut", "*~ %s" % fnum(comp_slope(val["ratio"])), col, dev.sig(10))
    lift = s.place("comp_lift", "+~ %s" % fnum(val["makeup"]), col, dev.sig(11))
    gain = s.place("comp_gain", "dbtoa~", col, dev.sig(12))
    s.wire(peak + ".outs[0]", follow + ".ins[0]")
    s.wire("comp_attack_n.outs[0]", follow + ".ins[1]")
    s.wire("comp_release_n.outs[0]", follow + ".ins[2]")
    s.wire(follow + ".outs[0]", floor + ".ins[0]")
    s.wire(floor + ".outs[0]", level + ".ins[0]")
    s.wire(level + ".outs[0]", over + ".ins[0]")
    s.wire("comp_less.outs[0]", over + ".ins[1]")
    s.wire(over + ".outs[0]", above + ".ins[0]")
    s.wire(above + ".outs[0]", cut + ".ins[0]")
    s.wire("comp_slope.outs[0]", cut + ".ins[1]")
    s.wire(cut + ".outs[0]", lift + ".ins[0]")
    s.wire(d["makeup"] + ".outs[0]", lift + ".ins[1]")
    s.wire(lift + ".outs[0]", gain + ".ins[0]")
    wet = {}
    for ch in ("L", "R"):
        # `*~ 0.`, not `*~`: with a signal in its right inlet the browser engine adds the
        # argument to it (Web Audio sums into a gain), so the argument has to be zero.
        g = s.place("comp_" + ch, "*~ 0.", X[ch] + (SUB if ch == "L" else 0) + 10, dev.sig(13))
        s.wire(src[ch], g + ".ins[0]")
        s.wire(gain + ".outs[0]", g + ".ins[1]")
        wet[ch] = g + ".outs[0]"
    return dev.mix("comp", d["mix"], val["mix"], src, wet, 14, ctl_row=2)


# Makeup gain per distortion type: scale * drive ** exponent. A clipper flattens the peaks
# as the drive rises, so without this the Drive dial would mostly be a volume dial. The
# numbers were set by rendering each type in Max at drive 1, 3 and 10 and matching the
# output level to the input's.
DRIVE_MAKEUP = {"soft": (1.0, -0.5), "tube": (1.0, -0.5), "hard": (0.85, -0.5),
                "fuzz": (0.56, -0.25), "fold": (1.0, -0.4)}
FUZZ_PUSH = 4.0   # fuzz hits its clipper this much harder than the dial reads
DC_BLOCK = "biquad~ 1. -1. 0. -0.998 0."   # y = x - x[n-1] + 0.998 * y[n-1], a 14 Hz high-pass


def drive_makeup(character, amount):
    scale, exponent = DRIVE_MAKEUP[character]
    return scale * float(amount) ** exponent


def m_drive(dev, src, opt, val):
    s = dev.s
    kind = opt["character"]
    d = dev.dials(_module("drive"), val, math_rows=2)
    # makeup = scale * drive ** exponent
    s.place("drive_root", "pow %s" % fnum(DRIVE_MAKEUP[kind][1]), XC + 2 * DX, dev.ctl(0))
    s.place("drive_makeup", "* %s" % fnum(DRIVE_MAKEUP[kind][0]), XC + 2 * DX, dev.ctl(1))
    s.wire(d["amount"] + ".outs[0]", "drive_root.ins[0]")
    s.wire("drive_root.outs[0]", "drive_makeup.ins[0]")
    push = d["amount"] + ".outs[0]"
    if kind == "fuzz":
        s.place("drive_push", "* %s" % fnum(FUZZ_PUSH), XC, dev.ctl(0))
        s.wire(d["amount"] + ".outs[0]", "drive_push.ins[0]")
        push = "drive_push.outs[0]"
    out = {}
    rows = 0
    for ch in ("L", "R"):
        row = 0
        if kind == "soft":
            # overdrive~ has its own gain: 1 is clean, 10 is as far as it goes.
            g = s.place("drive_" + ch, "overdrive~ %s" % fnum(val["amount"]), X[ch], dev.sig(row))
            s.wire(src[ch], g + ".ins[0]")
            s.wire(push, g + ".ins[1]")
            shaped = g + ".outs[0]"
            row += 1
        else:
            pre = val["amount"] * (FUZZ_PUSH if kind == "fuzz" else 1.0)
            g = s.place("drive_" + ch, "*~ %s" % fnum(pre), X[ch], dev.sig(row))
            s.wire(src[ch], g + ".ins[0]")
            s.wire(push, g + ".ins[1]")
            row += 1
            if kind == "tube":
                c = s.place("drive_shape" + ch, "tanh~", X[ch], dev.sig(row))
                s.wire(g + ".outs[0]", c + ".ins[0]")
            elif kind == "hard":
                c = s.place("drive_shape" + ch, "clip~ -1. 1.", X[ch], dev.sig(row))
                s.wire(g + ".outs[0]", c + ".ins[0]")
            elif kind == "fold":
                # pong~ mode 0 folds the wave back at the limits instead of flattening it.
                c = s.place("drive_shape" + ch, "pong~ 0 -1. 1.", X[ch], dev.sig(row))
                s.wire(g + ".outs[0]", c + ".ins[0]")
            else:  # fuzz
                # Lopsided clipping: an offset pushes one half of the wave into the rail
                # first. The offset would leave DC, so a one-zero, one-pole blocker follows.
                o = s.place("drive_bias" + ch, "+~ 0.2", X[ch], dev.sig(row))
                k = s.place("drive_clip" + ch, "clip~ -0.35 1.", X[ch], dev.sig(row + 1))
                c = s.place("drive_shape" + ch, DC_BLOCK, X[ch], dev.sig(row + 2))
                s.wire(g + ".outs[0]", o + ".ins[0]")
                s.wire(o + ".outs[0]", k + ".ins[0]")
                s.wire(k + ".outs[0]", c + ".ins[0]")
                row += 2
            shaped = c + ".outs[0]"
            row += 1
        t = s.place("drive_tone" + ch, "onepole~ %s" % fnum(val["tone"]), X[ch], dev.sig(row))
        m = s.place("drive_level" + ch, "*~ %s" % fnum(drive_makeup(kind, val["amount"])), X[ch], dev.sig(row + 1))
        s.wire(shaped, t + ".ins[0]")
        s.wire(d["tone"] + ".outs[0]", t + ".ins[1]")
        s.wire(t + ".outs[0]", m + ".ins[0]")
        s.wire("drive_makeup.outs[0]", m + ".ins[1]")
        out[ch] = m + ".outs[0]"
        rows = row + 2
    return out, rows


def m_crush(dev, src, opt, val):
    s = dev.s
    d = dev.dials(_module("crush"), val, math_rows=1)
    s.place("crush_ratio", "* 0.01", XC, dev.ctl(0))
    s.wire(d["rate"] + ".outs[0]", "crush_ratio.ins[0]")
    out = {}
    for ch in ("L", "R"):
        v = s.place("crush_" + ch, "degrade~ %s %d" % (fnum(val["rate"] / 100.0), int(val["bits"])), X[ch], dev.sig(0))
        # degrade~ rounds every sample the same way, which shifts the whole wave off
        # centre at low bit depths. The blocker takes that offset back out.
        b = s.place("crush_dc" + ch, DC_BLOCK, X[ch], dev.sig(1))
        s.wire(src[ch], v + ".ins[0]")
        s.wire("crush_ratio.outs[0]", v + ".ins[1]")
        s.wire(d["bits"] + ".outs[0]", v + ".ins[2]")
        s.wire(v + ".outs[0]", b + ".ins[0]")
        out[ch] = b + ".outs[0]"
    return out, 2


# Chorus sweeps a 12 ms tapin~ / tapout~ line by the full depth. A flanger is a much
# shorter line with a narrower sweep and some of its output fed back in, which is what
# makes the comb peaks ring. That is exactly a comb~ with its delay time swept, so the
# flanger is one comb~ per channel.
CHORUS = {"chorus": {"base": 12.0, "sweep": 1.0}, "flanger": {"base": 3.0, "sweep": 0.3}}
FLANGER_FEEDBACK = 0.5
FLANGER_TRIM = 0.7  # feedback rings up to twice the level at its peaks; this keeps the mix under full scale


def m_chorus(dev, src, opt, val):
    s = dev.s
    flanger = opt["voice"] == "flanger"
    v = CHORUS[opt["voice"]]
    d = dev.dials(_module("chorus"), val, math_rows=3)
    s.place("chorus_lfo", "cycle~ %s" % fnum(val["rate"]), XC, dev.ctl(0))
    s.place("chorus_depth_sig", "*~ %s" % fnum(val["depth"] * v["sweep"]), XC, dev.ctl(1))
    s.place("chorus_invert", "*~ -1.", XC, dev.ctl(2))
    s.wire(d["rate"] + ".outs[0]", "chorus_lfo.ins[0]")
    s.wire("chorus_lfo.outs[0]", "chorus_depth_sig.ins[0]")
    if v["sweep"] == 1.0:
        s.wire(d["depth"] + ".outs[0]", "chorus_depth_sig.ins[1]")
    else:
        s.place("chorus_narrow", "* %s" % fnum(v["sweep"]), XC + DX, dev.ctl(0))
        s.wire(d["depth"] + ".outs[0]", "chorus_narrow.ins[0]")
        s.wire("chorus_narrow.outs[0]", "chorus_depth_sig.ins[1]")
    s.wire("chorus_depth_sig.outs[0]", "chorus_invert.ins[0]")
    sweep = {"L": "chorus_depth_sig.outs[0]", "R": "chorus_invert.outs[0]"}
    wet = {}
    for ch in ("L", "R"):
        # The delay time, in ms, as a signal: the base time plus or minus the sweep.
        t = s.place("chorus_time" + ch, "+~ %s" % fnum(v["base"]), X[ch] + SUB, dev.sig(0))
        s.wire(sweep[ch], t + ".ins[0]")
        if flanger:
            # comb~ arguments: longest delay, delay, direct, delayed, feedback. The delay
            # is left at 0 because the signal in the second inlet sets it.
            c = s.place("chorus_comb" + ch, "comb~ 20 0. 0. 1. %s" % fnum(FLANGER_FEEDBACK), X[ch], dev.sig(1))
            g = s.place("chorus_trim" + ch, "*~ %s" % fnum(FLANGER_TRIM), X[ch], dev.sig(2))
            s.wire(src[ch], c + ".ins[0]")
            s.wire(t + ".outs[0]", c + ".ins[1]")
            s.wire(c + ".outs[0]", g + ".ins[0]")
            wet[ch] = g + ".outs[0]"
        else:
            i = s.place("chorus_in" + ch, "tapin~ 100", X[ch], dev.sig(0))
            o = s.place("chorus_tap" + ch, "tapout~", X[ch], dev.sig(1))
            s.wire(src[ch], i + ".ins[0]")
            s.wire(i + ".outs[0]", o + ".ins[0]")
            s.wire(t + ".outs[0]", o + ".ins[0]")
            wet[ch] = o + ".outs[0]"
    return dev.mix("chorus", d["mix"], val["mix"], src, wet, 3 if flanger else 2)


def m_tremolo(dev, src, opt, val):
    s = dev.s
    d = dev.dials(_module("tremolo"), val, math_rows=2)
    half = val["depth"] / 200.0
    # gain = (1 - depth/2) + (depth/2) * sin, so full depth swings between 0 and 1.
    s.place("tremolo_lfo", "cycle~ %s" % fnum(val["rate"]), XC, dev.ctl(0))
    s.place("tremolo_half", "* 0.005", XC + DX, dev.ctl(0))
    s.place("tremolo_base", "!- 1.", XC + DX, dev.ctl(1))
    s.wire(d["rate"] + ".outs[0]", "tremolo_lfo.ins[0]")
    s.wire(d["depth"] + ".outs[0]", "tremolo_half.ins[0]")
    s.wire("tremolo_half.outs[0]", "tremolo_base.ins[0]")
    lfo = {"L": "tremolo_lfo.outs[0]", "R": "tremolo_lfo.outs[0]"}
    if opt["mode"] == "autopan":
        s.place("tremolo_invert", "*~ -1.", XC, dev.ctl(1))
        s.wire("tremolo_lfo.outs[0]", "tremolo_invert.ins[0]")
        lfo["R"] = "tremolo_invert.outs[0]"
    out = {}
    for ch in ("L", "R"):
        a = s.place("tremolo_swing" + ch, "*~ %s" % fnum(half), X[ch] + SUB, dev.sig(0))
        m = s.place("tremolo_gain" + ch, "+~ %s" % fnum(1 - half), X[ch] + SUB, dev.sig(1))
        v = s.place("tremolo_" + ch, "*~ 0.", X[ch], dev.sig(2))
        s.wire(lfo[ch], a + ".ins[0]")
        s.wire("tremolo_half.outs[0]", a + ".ins[1]")
        s.wire(a + ".outs[0]", m + ".ins[0]")
        s.wire("tremolo_base.outs[0]", m + ".ins[1]")
        s.wire(src[ch], v + ".ins[0]")
        s.wire(m + ".outs[0]", v + ".ins[1]")
        out[ch] = v + ".outs[0]"
    return out, 3


DELAY_GLIDE = 80  # ms the delay time takes to reach a new dial position
# The feedback path runs through 0.5 * tanh(2 * x): unity gain for quiet echoes, and a
# ceiling at half scale for loud ones. Repeats that pile up saturate, the way tape does,
# instead of growing until they hit the output clip.
LOOP_CEILING = 0.5


def m_delay(dev, src, opt, val):
    s = dev.s
    d = dev.dials(_module("delay"), val, math_rows=2)
    # The time reaches the taps as a signal that glides to each new value. Sent as a plain
    # number it would jump, and a jump in a delay line is a click.
    s.place("delay_ramp", "pack 0. %d" % DELAY_GLIDE, XC, dev.ctl(0))
    s.place("delay_time_sig", "line~ %s" % fnum(val["time"]), XC, dev.ctl(1))
    s.place("delay_fb", "* %s" % fnum(0.01 / LOOP_CEILING), XC + DX, dev.ctl(0))
    s.wire(d["time"] + ".outs[0]", "delay_ramp.ins[0]")
    s.wire("delay_ramp.outs[0]", "delay_time_sig.ins[0]")
    s.wire(d["feedback"] + ".outs[0]", "delay_fb.ins[0]")
    wet = {}
    for ch in ("L", "R"):
        i = s.place("delay_in" + ch, "tapin~ 2000", X[ch], dev.sig(0))
        o = s.place("delay_tap" + ch, "tapout~", X[ch], dev.sig(1))
        f = s.place("delay_fb" + ch, "*~ %s" % fnum(val["feedback"] / 100.0 / LOOP_CEILING), X[ch] + SUB, dev.sig(2))
        k = s.place("delay_tanh" + ch, "tanh~", X[ch] + SUB, dev.sig(3))
        t = s.place("delay_sat" + ch, "*~ %s" % fnum(LOOP_CEILING), X[ch] + SUB, dev.sig(4))
        s.wire(i + ".outs[0]", o + ".ins[0]")
        s.wire("delay_time_sig.outs[0]", o + ".ins[0]")
        s.wire(o + ".outs[0]", f + ".ins[0]")
        s.wire("delay_fb.outs[0]", f + ".ins[1]")
        s.wire(f + ".outs[0]", k + ".ins[0]")
        s.wire(k + ".outs[0]", t + ".ins[0]")
        wet[ch] = o + ".outs[0]"
    if opt["style"] == "pingpong":
        # Left and right are averaged into the left line; each side's echo then feeds the
        # other side's line, so the repeats alternate.
        s.place("delay_mono", "*~ 0.5", X["L"], dev.sig(5))
        s.wire(src["L"], "delay_mono.ins[0]")
        s.wire(src["R"], "delay_mono.ins[0]")
        s.wire("delay_mono.outs[0]", "delay_inL.ins[0]")
        s.wire("delay_satL.outs[0]", "delay_inR.ins[0]")
        s.wire("delay_satR.outs[0]", "delay_inL.ins[0]")
        rows = 6
    else:
        for ch in ("L", "R"):
            s.wire(src[ch], "delay_in%s.ins[0]" % ch)
            s.wire("delay_sat%s.outs[0]" % ch, "delay_in%s.ins[0]" % ch)
        rows = 5
    return dev.mix("delay", d["mix"], val["mix"], src, wet, rows, equal_power=True)


# The reverb is Freeverb's layout: eight parallel combs into four allpasses in series,
# with its delay lengths (given there in samples at 44.1 kHz, here in ms). The right
# channel's lines are 23 samples longer, which is what makes the tail wide instead of the
# same reverb in both speakers.
COMBS = (25.31, 26.94, 28.96, 30.75, 32.24, 33.81, 35.31, 36.67)
ALLPASSES = (12.61, 10.0, 7.73, 5.1)
SPREAD = {"L": 0.0, "R": 0.52}
REVERB_FB = (0.7, 0.97)

# Three spaces from the one layout, by how long the combs are and how hard they feed back.
# A comb of d ms with feedback g rings for about 3 * d / -log10(g) ms, so:
#   room   combs of 12 to 20 ms, feedback 0.6 to 0.9: a quarter of a second to a second,
#          and close echoes, which is what makes a space sound small;
#   hall   the one every device had before there was a choice: 0.6 to 7 seconds;
#   plate  short combs that are close together and fed back hard, with shorter allpasses:
#          dense from the first instant and smooth, with no echoes to hear, 0.5 to 4.5 s.
SPACES = {
    "room": {"combs": (12.07, 13.33, 14.59, 15.73, 16.87, 17.93, 19.01, 20.11),
             "allpasses": (8.31, 6.13, 4.37, 2.89), "feedback": (0.6, 0.9)},
    "hall": {"combs": COMBS, "allpasses": ALLPASSES, "feedback": REVERB_FB},
    "plate": {"combs": (11.29, 12.71, 13.99, 15.13, 16.31, 17.57, 18.89, 20.23),
              "allpasses": (6.07, 4.21, 2.93, 1.87), "feedback": (0.8, 0.975)},
}


def reverb_feedback(size, space="hall"):
    lo, hi = SPACES[space]["feedback"]
    return lo + (hi - lo) * float(size) / 100.0


def reverb_level(feedback):
    """
    What the summed combs are scaled by. A comb with feedback g raises the level of what
    passes through it by 1 / sqrt(1 - g * g), so a long tail would also be a loud one.
    Dividing by that undoes it, and dividing by sqrt(8) averages the eight combs.
    """
    return (1.0 - feedback * feedback) ** 0.5 / len(COMBS) ** 0.5


def m_reverb(dev, src, opt, val):
    s = dev.s
    d = dev.dials(_module("reverb"), val, math_rows=3)
    space = SPACES[opt["space"]]
    fb = reverb_feedback(val["size"], opt["space"])
    s.place("reverb_fb", "scale 0. 100. %s %s" % (fnum(space["feedback"][0]), fnum(space["feedback"][1])), XC, dev.ctl(0))
    # level = sqrt(1 - fb * fb) / sqrt(8), one object per step
    s.place("reverb_square", "pow 2.", XC, dev.ctl(1))
    s.place("reverb_rest", "!- 1.", XC, dev.ctl(2))
    s.place("reverb_root", "sqrt", XC + DX, dev.ctl(1))
    s.place("reverb_level", "* %s" % fnum(1.0 / len(COMBS) ** 0.5), XC + DX, dev.ctl(2))
    s.wire(d["size"] + ".outs[0]", "reverb_fb.ins[0]")
    s.wire("reverb_fb.outs[0]", "reverb_square.ins[0]")
    s.wire("reverb_square.outs[0]", "reverb_rest.ins[0]")
    s.wire("reverb_rest.outs[0]", "reverb_root.ins[0]")
    s.wire("reverb_root.outs[0]", "reverb_level.ins[0]")
    wet = {}
    half = len(COMBS) // 2
    for ch in ("L", "R"):
        total = s.place("reverb_sum" + ch, "*~ %s" % fnum(reverb_level(fb)), X[ch], dev.sig(half))
        s.wire("reverb_level.outs[0]", total + ".ins[1]")
        for n, ms in enumerate(space["combs"]):
            # two columns of four, so the section stays a readable height
            x, row = X[ch] + (SUB + 10 if n >= half else 0), n % half
            c = s.place("reverb_comb%s%d" % (ch, n + 1),
                        "comb~ 60 %s 0. 1. %s" % (fnum(ms + SPREAD[ch]), fnum(fb)), x, dev.sig(row))
            s.wire(src[ch], c + ".ins[0]")
            s.wire("reverb_fb.outs[0]", c + ".ins[4]")
            s.wire(c + ".outs[0]", total + ".ins[0]")
        last = total + ".outs[0]"
        for n, ms in enumerate(space["allpasses"]):
            ap = s.place("reverb_ap%s%d" % (ch, n + 1), "allpass~ 20 %s 0.5" % fnum(ms + SPREAD[ch]), X[ch], dev.sig(half + 1 + n))
            s.wire(last, ap + ".ins[0]")
            last = ap + ".outs[0]"
        dm = s.place("reverb_damp" + ch, "onepole~ %s" % fnum(val["damp"]), X[ch], dev.sig(half + 1 + len(ALLPASSES)))
        s.wire(last, dm + ".ins[0]")
        s.wire(d["damp"] + ".outs[0]", dm + ".ins[1]")
        wet[ch] = dm + ".outs[0]"
    return dev.mix("reverb", d["mix"], val["mix"], src, wet, half + 2 + len(ALLPASSES), equal_power=True)


# ── the instrument ──────────────────────────────────────────────────────────────
#
# A polyphonic synth as one flat patch. notein feeds poly, which gives each note a voice
# number; pack and route send the note to that voice. A voice is two oscillators, a
# low-pass filter that its envelope opens, and the envelope on its level:
#
#     pitch ──▶ mtof ──▶ osc 1 ─┐
#     pitch ──▶ + offset ▶ mtof ▶ osc 2 ─┴▶ svf~ ──▶ *~ ──▶ (all voices summed)
#     velocity ──▶ / 127. ──▶ adsr~ ──────────┴──────────┘
#
# Max would usually wrap the voice in poly~, which needs a second patch file. Laying the
# voices out side by side keeps the device one file, and one flat graph that the page can
# play and patchc can compile.

WAVES = {"saw": "saw~", "square": "rect~", "triangle": "tri~", "sine": "cycle~"}
SECOND_OSC = {"unison": 0.0, "up": 12.0, "down": -12.0}   # semitones from the first
ENV_REACH = 80.0     # Hz the filter opens per percent of Env, at the top of a full-velocity note
VOICE_DX = 230       # between voices in the patching view


def voice_gain(voices):
    """What the summed voices are scaled by, so a full chord stays under full scale."""
    return 0.7 / voices ** 0.5


def osc_offset(second, detune):
    """Where the second oscillator sits, in semitones: its octave, plus the detune in cents."""
    return SECOND_OSC[second] + float(detune) / 100.0


def m_synth(dev, inst):
    """The note path and the voices. Returns the summed signal, as both channels."""
    s = dev.s
    osc, tone, env = inst["osc"], inst["tone"], inst["env"]
    voices = int(osc["options"]["voices"])
    wave = WAVES[osc["options"]["wave"]]
    blend = osc["values"]["blend"] / 100.0
    offset = osc_offset(osc["options"]["second"], osc["values"]["detune"])
    res = svf_resonance(tone["values"]["res"])
    sustain = env["values"]["sustain"] / 100.0

    # Each section's dials, with the control maths that follows them.
    dev.header("oscillators")
    d_osc = dev.dials(_instrument_module("osc"), osc["values"], math_rows=2)
    s.place("osc_semitones", "* 0.01", XC, dev.ctl(0))
    s.place("osc_offset", "+ %s" % fnum(SECOND_OSC[osc["options"]["second"]]), XC, dev.ctl(1))
    s.place("osc_second", "* 0.01", XC + DX, dev.ctl(0))
    s.place("osc_first", "!- 1.", XC + DX, dev.ctl(1))
    s.wire(d_osc["detune"] + ".outs[0]", "osc_semitones.ins[0]")
    s.wire("osc_semitones.outs[0]", "osc_offset.ins[0]")
    s.wire(d_osc["blend"] + ".outs[0]", "osc_second.ins[0]")
    s.wire("osc_second.outs[0]", "osc_first.ins[0]")
    dev.y += dev.end(0)

    dev.header("tone")
    d_tone = dev.dials(_instrument_module("tone"), tone["values"], math_rows=1)
    s.place("tone_reso", "scale 0. 100. %s %s" % (fnum(SVF_RES[0]), fnum(SVF_RES[1])), XC + DX, dev.ctl(0))
    s.place("tone_reach", "* %s" % fnum(ENV_REACH), XC + 2 * DX + 40, dev.ctl(0))
    s.wire(d_tone["res"] + ".outs[0]", "tone_reso.ins[0]")
    s.wire(d_tone["env"] + ".outs[0]", "tone_reach.ins[0]")
    dev.y += dev.end(0)

    dev.header("envelope")
    d_env = dev.dials(_instrument_module("env"), env["values"], math_rows=1)
    s.place("env_level", "* 0.01", XC + 2 * DX, dev.ctl(0))
    s.wire(d_env["sustain"] + ".outs[0]", "env_level.ins[0]")
    dev.y += dev.end(0)

    # Notes in, one voice each.
    dev.header("voices")
    dev.math_rows = 0
    dev.mod_rows = 0
    s.place("note_in", "notein", X["L"], dev.sig(0))
    s.place("note_poly", "poly %d 1" % voices, X["L"], dev.sig(1))
    s.place("note_pack", "pack 0 0 0", X["L"], dev.sig(2))
    s.place("note_route", "route %s" % " ".join(str(v + 1) for v in range(voices)), X["L"], dev.sig(3))
    s.wire("note_in.outs[0]", "note_poly.ins[0]")
    s.wire("note_in.outs[1]", "note_poly.ins[1]")
    for i in range(3):
        s.wire("note_poly.outs[%d]" % i, "note_pack.ins[%d]" % i)
    s.wire("note_pack.outs[0]", "note_route.ins[0]")

    s.place("synth_bus", "*~ %s" % fnum(voice_gain(voices)), X["L"], dev.sig(14))
    for v in range(voices):
        n = "v%d_" % (v + 1)
        x, x2 = X["L"] + v * VOICE_DX, X["L"] + v * VOICE_DX + SUB + 10
        s.place(n + "note", "unpack 0 0", x, dev.sig(4))
        s.place(n + "pitch", "mtof", x, dev.sig(5))
        s.place(n + "shift", "+ %s" % fnum(offset), x2, dev.sig(5))
        s.place(n + "pitch2", "mtof", x2, dev.sig(6))
        s.place(n + "osc", "%s 220." % wave, x, dev.sig(7))
        s.place(n + "osc2", "%s 220." % wave, x2, dev.sig(7))
        s.place(n + "first", "*~ %s" % fnum(1 - blend), x, dev.sig(8))
        s.place(n + "second", "*~ %s" % fnum(blend), x2, dev.sig(8))
        s.place(n + "mix", "+~", x, dev.sig(9))
        s.place(n + "vel", "/ 127.", x2, dev.sig(9))
        s.place(n + "env", "adsr~ %s %s %s %s" % (fnum(env["values"]["attack"]), fnum(env["values"]["decay"]),
                                                  fnum(sustain), fnum(env["values"]["release"])), x2, dev.sig(10))
        s.place(n + "reach", "*~ %s" % fnum(tone["values"]["env"] * ENV_REACH), x2, dev.sig(11))
        s.place(n + "cutoff", "+~ %s" % fnum(tone["values"]["cutoff"]), x2, dev.sig(12))
        # The cutoff arrives as a signal (the dial plus the envelope), so the argument is 0.
        s.place(n + "filter", "svf~ 0. %s" % fnum(res), x, dev.sig(12))
        s.place(n + "amp", "*~ 0.", x, dev.sig(13))

        s.wire("note_route.outs[%d]" % v, n + "note.ins[0]")
        s.wire(n + "note.outs[0]", n + "pitch.ins[0]")
        s.wire(n + "note.outs[0]", n + "shift.ins[0]")
        s.wire("osc_offset.outs[0]", n + "shift.ins[1]")
        s.wire(n + "shift.outs[0]", n + "pitch2.ins[0]")
        s.wire(n + "pitch.outs[0]", n + "osc.ins[0]")
        s.wire(n + "pitch2.outs[0]", n + "osc2.ins[0]")
        s.wire(n + "osc.outs[0]", n + "first.ins[0]")
        s.wire("osc_first.outs[0]", n + "first.ins[1]")
        s.wire(n + "osc2.outs[0]", n + "second.ins[0]")
        s.wire("osc_second.outs[0]", n + "second.ins[1]")
        s.wire(n + "first.outs[0]", n + "mix.ins[0]")
        s.wire(n + "second.outs[0]", n + "mix.ins[1]")
        s.wire(n + "note.outs[1]", n + "vel.ins[0]")
        s.wire(n + "vel.outs[0]", n + "env.ins[0]")
        s.wire(d_env["attack"] + ".outs[0]", n + "env.ins[1]")
        s.wire(d_env["decay"] + ".outs[0]", n + "env.ins[2]")
        s.wire("env_level.outs[0]", n + "env.ins[3]")
        s.wire(d_env["release"] + ".outs[0]", n + "env.ins[4]")
        s.wire(n + "env.outs[0]", n + "reach.ins[0]")
        s.wire("tone_reach.outs[0]", n + "reach.ins[1]")
        s.wire(n + "reach.outs[0]", n + "cutoff.ins[0]")
        s.wire(d_tone["cutoff"] + ".outs[0]", n + "cutoff.ins[1]")
        s.wire(n + "mix.outs[0]", n + "filter.ins[0]")
        s.wire(n + "cutoff.outs[0]", n + "filter.ins[1]")
        s.wire("tone_reso.outs[0]", n + "filter.ins[2]")
        s.wire(n + "filter.outs[0]", n + "amp.ins[0]")
        s.wire(n + "env.outs[0]", n + "amp.ins[1]")
        s.wire(n + "amp.outs[0]", "synth_bus.ins[0]")
    s.flush()
    dev.y += dev.end(15)
    return {"L": "synth_bus.outs[0]", "R": "synth_bus.outs[0]"}


MODULES = {
    "filter": m_filter,
    "eq": m_eq,
    "peq": m_peq,
    "geq": m_geq,
    "tilt": m_tilt,
    "comp": m_comp,
    "drive": m_drive,
    "crush": m_crush,
    "chorus": m_chorus,
    "tremolo": m_tremolo,
    "delay": m_delay,
    "reverb": m_reverb,
}


def script(settings):
    """The maxpylang program that builds the device these settings describe."""
    st = normalize(settings)
    sch = schema()
    dev = Device(st)
    s = dev.s
    active = [e for e in st["chain"] if e["on"]]
    instrument = st.get("kind") == "instrument"
    stages = [_module(e["module"])["label"].lower() for e in active] + ["output gain"]

    s.emit('"""')
    if instrument:
        s.emit("%s: a Max for Live instrument, generated with MaxPyLang." % st["name"])
        s.emit("")
        s.emit("Signal flow: notein -> poly -> %s voices -> %s -> clip~ -> plugout~"
               % (st["instrument"]["osc"]["options"]["voices"], " -> ".join(stages)))
    else:
        s.emit("%s: a Max for Live audio effect, generated with MaxPyLang." % st["name"])
        s.emit("")
        s.emit("Signal flow: plugin~ -> %s -> clip~ -> plugout~" % " -> ".join(stages))
    s.emit("Run it with `pip install maxpylang`; it writes %s next to this file." % filename(st))
    s.emit('"""')
    s.emit(PREAMBLE)
    s.emit()
    s.emit("ACCENT = %r" % _rgba(st["accent"]))
    # The device's colours, from its theme: the backdrop, the cards on it, the lettering
    # and the dials' rings. The accent is the part of a ring that is filled.
    theme = theme_of(st)
    s.emit("BG = %r" % _rgba(theme["bg"]))
    s.emit("CARD = %r" % _rgba(theme["card"]))
    s.emit("INK = %r" % _rgba(theme["ink"]))
    s.emit("DIM = %r" % _rgba(_mix(theme["ink"], theme["track"], 0.5)))
    s.emit("TRACK = %r" % _rgba(theme["track"]))
    # The backdrop: one panel behind everything, made first so that it is at the back,
    # and stretched to the device's width once that is known.
    s.emit("backdrop = card((%d, %d), x=0, w=10, color=BG)" % (XP, dev.park_y))
    s.emit('box(backdrop)["rounded"] = 0')
    dev.park_y += 30

    # the name, in the device view
    brand = ['label(%s, (%d, %d), (8, %d, %d), size=12.0, bold=True, color=INK)'
             % (json.dumps(st["name"]), XP, dev.park_y, NAME_Y, BRAND_W - 8)]
    dev.park_y += 30

    # where the sound comes from: the track (an effect) or the synth's voices (an instrument)
    routes, used = motion_plan(st)
    if instrument:
        for line in brand:
            s.emit(line)
        if used:
            m_motion(dev, st, used, None)
            dev.routes = routes
        sig = m_synth(dev, st["instrument"])
    else:
        dev.header("input")
        s.place("plug_in", "plugin~", X["L"], dev.y + ROW)
        for line in brand:
            s.emit(line)
        sig = {"L": "plug_in.outs[0]", "R": "plug_in.outs[1]"}
        s.flush()
        dev.y += 2 * ROW + GAP
        if used:
            m_motion(dev, st, used, sig)
            dev.routes = routes

    for entry in active:
        mod = _module(entry["module"])
        dev.header(mod["label"])
        sig, rows = MODULES[mod["id"]](dev, sig, entry["options"], entry["values"])
        s.flush()
        dev.y += dev.end(rows)

    # output: gain in dB, a safety clip, and out to Live's mixer
    out = sch["output"]
    dev.header("output")
    d = dev.dials(out, st["output"], math_rows=1)
    s.place("output_amp", "dbtoa", XC, dev.ctl(0))
    s.wire(d["gain"] + ".outs[0]", "output_amp.ins[0]")
    amp = 10 ** (st["output"]["gain"] / 20.0)
    for ch in ("L", "R"):
        s.place("output_" + ch, "*~ %s" % fnum(amp), X[ch], dev.sig(0))
        s.place("output_clip" + ch, "clip~ -1. 1.", X[ch], dev.sig(1))
    s.place("plug_out", "plugout~", X["L"], dev.sig(2))
    for i, ch in enumerate(("L", "R")):
        g, c = "output_" + ch, "output_clip" + ch
        s.wire(sig[ch], g + ".ins[0]")
        s.wire("output_amp.outs[0]", g + ".ins[1]")
        s.wire(g + ".outs[0]", c + ".ins[0]")
        s.wire(c + ".outs[0]", "plug_out.ins[%d]" % i)
    s.flush()

    width = dev.card_x
    s.emit()
    s.emit("# ── device " + "─" * 66)
    s.emit("# Open in presentation mode, at the width the cards add up to. (gap M4L-04)")
    s.emit('patcher = patch._patcher_dict["patcher"]')
    s.emit('patcher["openinpresentation"] = 1')
    s.emit('patcher["devicewidth"] = %s' % pynum(width))
    s.emit('box(backdrop)["presentation_rect"] = [0.0, 0.0, %s, %s]' % (pynum(width), pynum(DEVICE_H)))
    s.emit()
    s.emit('patch.save(%s, device_type="%s", verbose=False)'
           % (json.dumps(filename(st)), "instrument" if instrument else "audio_effect"))
    return s.text()


# ── running it ──────────────────────────────────────────────────────────────────

def run(settings, want_amxd=True):
    """
    Execute script(settings) and report what it produced.

    Returns a dict: the script, the patcher JSON, the .amxd bytes (base64) when asked for,
    everything maxpylang printed or warned while building and checking the patch, and the
    type checks' coverage numbers. The page shows the last two as its "type check" panel,
    which is where a gap in what maxpylang can judge first becomes visible.
    """
    import maxpylang as mp

    st = normalize(settings)
    src = script(st)
    out = io.StringIO()
    ns = {"__name__": "__fxgen__", "GRAPH_SOURCE": graph_source()}
    cwd = os.getcwd()
    result = {"settings": st, "script": src, "filename": filename(st)}
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            with warnings.catch_warnings(record=True) as caught, contextlib.redirect_stdout(out):
                warnings.simplefilter("always")
                exec(compile(src, "<%s>" % filename(st), "exec"), ns)
            path = os.path.join(tmp, filename(st))
            if want_amxd:
                with open(path, "rb") as fh:
                    result["amxd"] = base64.b64encode(fh.read()).decode("ascii")
        finally:
            os.chdir(cwd)

    patch = ns["patch"]
    assert isinstance(patch, mp.MaxPatch)
    result["json"] = patch.get_json()
    result["log"] = out.getvalue()
    result["warnings"] = [{"category": w.category.__name__, "message": str(w.message)} for w in caught]
    try:
        result["coverage"] = json.loads(json.dumps(patch.get_coverage(), default=str))
    except Exception as err:  # the page still works without the numbers
        result["coverage"] = {"error": str(err)}
    return result


def run_json(settings_json, want_amxd=True):
    """run(), with JSON text in and out. The entry point the Pyodide worker calls."""
    return json.dumps(run(json.loads(settings_json), want_amxd))
