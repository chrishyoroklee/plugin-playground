"""
patchc: compile a generated Max patch into C++.

    patch JSON (what fxgen.run() returns under "json")  ->  the source of an Effect.h

The Effect.h holds one plain class with prepare(), setParam() and process(), built on
the kernels in native/mxrt.h. It knows nothing about plugin formats; native/shell wraps
it as a CLAP plugin and native/render plays a WAV file through it.

How a patch is read:

  - Every live.dial becomes a parameter, with the name, range, unit and curve saved on
    the box.
  - The control objects between a dial and a signal inlet (scale, pow, pack ...) become
    straight-line code that runs when that parameter changes. In these patches every
    control cord goes into a left inlet, so each object is a function of the one number
    arriving, and the chain behind a dial is a function of that dial.
  - The signal objects are sorted so each runs after what feeds it, and written out as
    one loop over the samples. A loop in the patch is legal only where Max allows it:
    through a tapin~ / tapout~ pair, whose delay is what makes the loop computable.
  - Panels and comments are dropped.

It covers the objects fxgen.py emits and nothing else. An object, an inlet or a kind of
cord it does not model is a CompileError that names the box, never a silent skip: a
plugin that quietly leaves something out would still load and still make sound.

Adding an object: one kernel in mxrt.h if it has state, and one class below.

Command line:  python patchc.py patch.json [-o Effect.h] [--name "My Effect"]
"""

import argparse
import json
import math
import os
import re
import sys


class CompileError(Exception):
    """The patch uses something patchc does not model. The message names the box."""


# ── reading the patch ───────────────────────────────────────────────────────────

UI_BOXES = {"comment", "live.comment", "panel"}  # drawn, never heard


def _atom(token):
    """A word of box text as Max reads it: an int, a float, or a symbol."""
    try:
        return int(token)
    except ValueError:
        pass
    try:
        value = float(token)
    except ValueError:
        return token
    if not math.isfinite(value):
        return token
    return value


def lit(value):
    """A number as a C++ double literal that reads back to the same double."""
    value = float(value)
    if not math.isfinite(value):
        raise CompileError("a number in the patch is not finite: %r" % value)
    return repr(value)


def cstr(text):
    return json.dumps(str(text), ensure_ascii=True)


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "effect"


def fnv1a(text):
    h = 0x811C9DC5
    for byte in text.encode("utf-8"):
        h = ((h ^ byte) * 0x01000193) & 0xFFFFFFFF
    return h


class Box:
    def __init__(self, raw, order):
        self.raw = raw
        self.id = raw["id"]
        self.order = order
        maxclass = raw.get("maxclass", "")
        if maxclass == "newobj":
            words = str(raw.get("text", "")).split()
            if not words:
                raise CompileError("%s is an empty object box" % self.id)
            self.cls = words[0]
            self.args = [_atom(w) for w in words[1:]]
            self.text = " ".join(words)
        else:
            self.cls = maxclass
            self.args = []
            self.text = maxclass
        self.ident = re.sub(r"[^0-9A-Za-z]+", "_", self.id.replace("obj-", "")).strip("_")

    def __str__(self):
        return "[%s] (%s)" % (self.text, self.id)

    # arguments ------------------------------------------------------------------

    def need(self, count):
        if len(self.args) < count:
            raise CompileError("%s needs %d argument%s; patchc does not guess Max's defaults for it"
                               % (self, count, "" if count == 1 else "s"))

    def num(self, index, default=None):
        if index >= len(self.args):
            if default is None:
                raise CompileError("%s is missing argument %d" % (self, index + 1))
            return default
        value = self.args[index]
        if isinstance(value, str):
            raise CompileError("%s: argument %d should be a number, not %r" % (self, index + 1, value))
        return value


# ── values in the control domain ────────────────────────────────────────────────

class Num:
    """A number travelling down a control cord: a C++ expression of type double."""

    def __init__(self, expr):
        self.expr = expr


class List:
    """A list (what pack sends): a tuple of C++ double expressions."""

    def __init__(self, items):
        self.items = items


def _one(box, value):
    if not isinstance(value, Num):
        raise CompileError("%s is sent a list; patchc only sends it single numbers" % box)
    return value.expr


# ── control objects ─────────────────────────────────────────────────────────────
#
# Each takes the box and the value arriving at its left inlet, and returns what leaves
# its outlet. Max's int and float boxes differ (`* 2` drops the fraction of what it is
# sent, `* 2.` does not), and the argument's spelling decides which, as it does in Max.

def _arith(op):
    def build(box, value):
        x = _one(box, value)
        arg = box.num(0, 0)
        if isinstance(arg, int):
            return Num("mxrt::toInt(%s)" % op("mxrt::toInt(%s)" % x, lit(arg)))
        return Num(op(x, lit(arg)))
    return build


def _unary(template):
    return lambda box, value: Num(template % _one(box, value))


def _pow(box, value):
    return Num("std::pow(%s, %s)" % (_one(box, value), lit(box.num(0, 0))))


def _scale(box, value):
    box.need(4)
    args = [box.num(i) for i in range(4)]
    if all(isinstance(a, int) for a in args):
        raise CompileError("%s has only whole-number arguments, which makes Max's scale work in "
                           "ints; patchc models the float one (write `0.` for `0`)" % box)
    return Num("mxrt::scale(%s, %s)" % (_one(box, value), ", ".join(lit(a) for a in args)))


def _pack(box, value):
    box.need(2)
    slots = [box.num(i) for i in range(len(box.args))]
    first = _one(box, value)
    if isinstance(slots[0], int):
        first = "mxrt::toInt(%s)" % first
    return List([first] + [lit(s) for s in slots[1:]])


CONTROL = {
    "+": _arith(lambda a, b: "%s + %s" % (a, b)),
    "*": _arith(lambda a, b: "%s * %s" % (a, b)),
    "!-": _arith(lambda a, b: "%s - %s" % (b, a)),
    "pow": _pow,
    "scale": _scale,
    "sqrt": _unary("std::sqrt(%s)"),
    "sin": _unary("std::sin(%s)"),
    "cos": _unary("std::cos(%s)"),
    "dbtoa": _unary("mxrt::dbtoa(%s)"),
    "pack": _pack,
}


# ── signal objects ──────────────────────────────────────────────────────────────

class Signal:
    """
    One signal object in the compiled effect.

    setup() runs first and declares what the object needs. A subclass then answers:
    tick(), the statements that compute this sample, and out(k), the expression holding
    outlet k afterwards. Numbers arriving on control cords are handled by the setters
    registered in setup(): inlet -> a function from a value to a C++ statement.
    """

    outlets = 1
    prefix = "obj"
    stateful = False   # keeps sounding after its input stops

    def __init__(self, box):
        self.box = box
        self.sig = {}        # inlet -> [expression, ...], the signal cords arriving
        self.setters = {}    # inlet -> fn(value) -> statement
        self.members = []    # C++ member declarations
        self.prepare = []    # statements for Effect::prepare
        self.reset = []      # statements for Effect::reset
        self.local = "%s_%s" % (self.prefix, box.ident)
        self.member = self.local + "_"

    # helpers for subclasses -------------------------------------------------------

    def input(self, inlet=0):
        """The sum of the signal cords into an inlet, as Max sums them."""
        cords = self.sig.get(inlet, [])
        if not cords:
            return "0.0"
        return cords[0] if len(cords) == 1 else "(%s)" % " + ".join(cords)

    def has_signal(self, inlet):
        return bool(self.sig.get(inlet))

    def kernel(self, ctype, prepare_args, reset=True):
        self.members.append("%s %s;  // %s" % (ctype, self.member, self.box.text))
        self.prepare.append("%s.prepare(%s);" % (self.member, prepare_args))
        if reset:
            self.reset.append("%s.reset();" % self.member)

    def operand(self, inlet, name, initial):
        """
        A number an inlet can change: a signal if one is connected (it replaces the
        argument, as in Max), otherwise a member that starts at the box's argument and
        follows any control cord.
        """
        if self.has_signal(inlet):
            return self.input(inlet)
        var = "%s%s" % (self.member, name)
        self.members.append("double %s = %s;  // %s" % (var, lit(initial), self.box.text))
        self.prepare.append("%s = %s;" % (var, lit(initial)))
        self.setters[inlet] = lambda value, var=var: "%s = %s;" % (var, _one(self.box, value))
        return var

    def call(self, inlet, method):
        """A control cord into `inlet` calls a kernel setter."""
        self.setters[inlet] = lambda value: "%s.%s(%s);" % (self.member, method, _one(self.box, value))

    def field(self, inlet, name):
        self.setters[inlet] = lambda value: "%s.%s = %s;" % (self.member, name, _one(self.box, value))

    def signal_inlets(self):
        """Inlets that take a signal; anything else arriving as one is an error."""
        return {0}

    def setup(self):
        pass

    def tick(self):
        return []

    def out(self, outlet):
        return self.local


class Expr(Signal):
    """A memoryless object: one expression per sample."""

    def tick(self):
        return ["const double %s = %s;" % (self.local, self.expr)]


class Times(Expr):
    prefix = "mul"

    def signal_inlets(self):
        return {0, 1}

    def setup(self):
        # With no argument and nothing in the right inlet, Max multiplies by zero.
        self.expr = "%s * %s" % (self.input(0), self.operand(1, "b", self.box.num(0, 0)))


class Plus(Expr):
    prefix = "add"

    def signal_inlets(self):
        return {0, 1}

    def setup(self):
        self.expr = "%s + %s" % (self.input(0), self.operand(1, "b", self.box.num(0, 0)))


class Clip(Expr):
    prefix = "clip"

    def signal_inlets(self):
        return {0, 1, 2}

    def setup(self):
        self.box.need(2)
        lo, hi = self.operand(1, "lo", self.box.num(0)), self.operand(2, "hi", self.box.num(1))
        self.expr = "mxrt::clip(%s, %s, %s)" % (self.input(0), lo, hi)


class Tanh(Expr):
    prefix = "tanh"

    def setup(self):
        self.expr = "std::tanh(%s)" % self.input(0)


class Pong(Expr):
    prefix = "pong"

    def signal_inlets(self):
        return {0, 1, 2}

    def setup(self):
        self.box.need(3)
        mode = self.box.num(0)
        if mode not in (0, 1, 2):
            raise CompileError("%s: the mode should be 0 (fold), 1 (wrap) or 2 (clip)" % self.box)
        lo, hi = self.operand(1, "lo", self.box.num(1)), self.operand(2, "hi", self.box.num(2))
        self.expr = "mxrt::pong(%s, %d, %s, %s)" % (self.input(0), mode, lo, hi)


class Overdrive(Expr):
    prefix = "overdrive"

    def signal_inlets(self):
        return {0, 1}

    def setup(self):
        self.box.need(1)
        self.expr = "mxrt::overdrive(%s, %s)" % (self.input(0), self.operand(1, "drive", self.box.num(0)))


class Degrade(Expr):
    prefix = "degrade"

    def setup(self):
        self.box.need(2)
        self.kernel("mxrt::Degrade", "%s, %s" % (lit(self.box.num(0)), lit(self.box.num(1))))
        self.call(1, "setRatio")
        self.call(2, "setBits")
        self.expr = "%s.tick(%s)" % (self.member, self.input(0))


class OnePole(Expr):
    prefix = "onepole"
    stateful = True

    def setup(self):
        self.box.need(1)
        self.kernel("mxrt::OnePole", "sr_, %s" % lit(self.box.num(0)))
        self.call(1, "setFreq")
        self.expr = "%s.tick(%s)" % (self.member, self.input(0))


class Biquad(Expr):
    prefix = "biquad"
    stateful = True

    def setup(self):
        self.box.need(5)
        self.kernel("mxrt::Biquad", ", ".join(lit(self.box.num(i)) for i in range(5)))
        for i in range(5):
            self.setters[i + 1] = lambda value, i=i: "%s.set(%d, %s);" % (self.member, i, _one(self.box, value))
        self.expr = "%s.tick(%s)" % (self.member, self.input(0))


class Svf(Signal):
    prefix = "svf"
    outlets = 4
    stateful = True
    NAMES = ("lp", "hp", "bp", "notch")

    def signal_inlets(self):
        return {0, 1, 2}

    def setup(self):
        self.box.need(2)
        self.kernel("mxrt::Svf", "sr_, %s, %s" % (lit(self.box.num(0)), lit(self.box.num(1))))
        self.call(1, "setFreq")
        self.call(2, "setRes")

    def tick(self):
        lines = []
        if self.has_signal(1):
            lines.append("%s.setFreq(%s);" % (self.member, self.input(1)))
        if self.has_signal(2):
            lines.append("%s.setRes(%s);" % (self.member, self.input(2)))
        lines.append("%s.tick(%s);" % (self.member, self.input(0)))
        return lines

    def out(self, outlet):
        return "%s.%s" % (self.member, self.NAMES[outlet])


class Comb(Expr):
    prefix = "comb"
    stateful = True

    def signal_inlets(self):
        return {0, 1}

    def setup(self):
        self.box.need(5)
        self.kernel("mxrt::Comb", "sr_, " + ", ".join(lit(self.box.num(i)) for i in range(5)))
        self.field(1, "delayMs")
        self.field(2, "a")
        self.field(3, "b")
        self.field(4, "c")
        if self.has_signal(1):
            self.expr = "%s.tickSwept(%s, %s)" % (self.member, self.input(0), self.input(1))
        else:
            self.expr = "%s.tick(%s)" % (self.member, self.input(0))


class Allpass(Expr):
    prefix = "allpass"
    stateful = True

    def setup(self):
        self.box.need(3)
        self.kernel("mxrt::Allpass", "sr_, " + ", ".join(lit(self.box.num(i)) for i in range(3)))
        self.call(1, "setDelay")
        self.field(2, "g")
        self.expr = "%s.tick(%s)" % (self.member, self.input(0))


class Cycle(Expr):
    prefix = "cycle"

    def setup(self):
        self.kernel("mxrt::Cycle", "sr_, %s" % lit(self.box.num(0, 0)))
        if self.has_signal(0):
            self.expr = "%s.tick(%s)" % (self.member, self.input(0))
        else:
            self.call(0, "setFreq")
            self.expr = "%s.tick()" % self.member


class Line(Expr):
    prefix = "line"

    def signal_inlets(self):
        return set()

    def setup(self):
        self.kernel("mxrt::Line", "sr_, %s" % lit(self.box.num(0, 0)))
        self.setters[0] = self.message
        self.expr = "%s.tick()" % self.member

    def message(self, value):
        if isinstance(value, Num):
            return "%s.jump(%s);" % (self.member, value.expr)
        if len(value.items) != 2:
            raise CompileError("%s is sent a list of %d; patchc models `target` and `target time`"
                               % (self.box, len(value.items)))
        return "%s.ramp(%s, %s);" % (self.member, value.items[0], value.items[1])


class TapIn(Signal):
    prefix = "tapin"
    stateful = True

    def setup(self):
        self.box.need(1)
        self.kernel("mxrt::TapLine", "sr_, %s" % lit(self.box.num(0)))

    def tick(self):
        return ["%s.write(%s);" % (self.member, self.input(0))]

    def out(self, outlet):
        raise CompileError("%s: the outlet of tapin~ only goes to a tapout~" % self.box)


class TapOut(Signal):
    prefix = "tapout"

    def __init__(self, box):
        super().__init__(box)
        self.outlets = max(1, len(box.args))
        self.line = None       # the TapIn this reads
        self.in_loop = False   # read before the line is written this sample

    def signal_inlets(self):
        return set(range(self.outlets))

    def tick(self):
        if self.line is None:
            raise CompileError("%s is not connected to a tapin~" % self.box)
        read = "readInLoop" if self.in_loop else "read"
        lines = []
        for k in range(self.outlets):
            time = self.input(k) if self.has_signal(k) else lit(self.box.num(k, 0))
            lines.append("const double %s = %s.%s(%s);" % (self.out(k), self.line.member, read, time))
        return lines

    def out(self, outlet):
        return self.local if self.outlets == 1 else "%s_%d" % (self.local, outlet)


class PlugIn(Signal):
    prefix = "in"
    outlets = 2

    def signal_inlets(self):
        return set()

    def out(self, outlet):
        return ("in_l", "in_r")[outlet]


class PlugOut(Signal):
    prefix = "out"
    outlets = 0

    def signal_inlets(self):
        return {0, 1}

    def tick(self):
        return ["out[0][i] = static_cast<Sample>(%s);" % self.input(0),
                "out[1][i] = static_cast<Sample>(%s);" % self.input(1)]


SIGNAL = {
    "plugin~": PlugIn,
    "plugout~": PlugOut,
    "*~": Times,
    "+~": Plus,
    "clip~": Clip,
    "tanh~": Tanh,
    "pong~": Pong,
    "overdrive~": Overdrive,
    "degrade~": Degrade,
    "onepole~": OnePole,
    "biquad~": Biquad,
    "svf~": Svf,
    "comb~": Comb,
    "allpass~": Allpass,
    "cycle~": Cycle,
    "line~": Line,
    "tapin~": TapIn,
    "tapout~": TapOut,
}

SUPPORTED = sorted(set(SIGNAL) | set(CONTROL) | UI_BOXES | {"live.dial"})


# ── parameters ──────────────────────────────────────────────────────────────────

class Param:
    def __init__(self, box, index):
        self.box = box
        self.index = index
        saved = (box.raw.get("saved_attribute_attributes") or {}).get("valueof") or {}

        def first(key, default):
            v = saved.get(key, default)
            v = v[0] if isinstance(v, list) and v else v
            try:
                v = float(v)
            except (TypeError, ValueError):
                return float(default)
            return v if math.isfinite(v) else float(default)

        self.name = str(saved.get("parameter_longname") or box.raw.get("varname") or box.id)
        self.short = str(saved.get("parameter_shortname") or self.name)
        self.key = str(box.raw.get("varname") or self.name)
        self.min = first("parameter_mmin", 0)
        self.max = first("parameter_mmax", 127)   # Max's defaults for a float parameter
        self.integer = int(first("parameter_type", 0)) == 1
        lo, hi = min(self.min, self.max), max(self.min, self.max)
        initial = first("parameter_initial", self.min) if int(first("parameter_initial_enable", 0)) == 1 else self.min
        self.initial = min(hi, max(lo, initial))
        self.exponent = max(0.01, first("parameter_exponent", 1))
        self.unit = int(first("parameter_unitstyle", 0 if self.integer else 1))
        self.id = fnv1a(self.key)
        self.value = "value_[%d]" % index


# ── the compiler ────────────────────────────────────────────────────────────────

class Compiler:
    def __init__(self, patch, name=None):
        patcher = patch.get("patcher") if isinstance(patch, dict) else None
        if not isinstance(patcher, dict):
            raise CompileError("not a Max patch: there is no \"patcher\" at the top")
        self.name = name
        self.boxes = {}
        for order, entry in enumerate(patcher.get("boxes", [])):
            box = Box(entry["box"], order)
            self.boxes[box.id] = box
        self.cords = []   # (source box, outlet, destination box, inlet), in patch order
        for entry in patcher.get("lines", []):
            line = entry["patchline"]
            (src, outlet), (dst, inlet) = line["source"], line["destination"]
            if src not in self.boxes or dst not in self.boxes:
                raise CompileError("a cord runs between %s and %s, and one of them is not in the patch" % (src, dst))
            self.cords.append((self.boxes[src], int(outlet), self.boxes[dst], int(inlet)))

        self.params = []
        self.signals = {}    # box id -> Signal
        self.controls = {}   # box id -> Box
        self.classify()
        self.connect_signals()
        for node in self.signals.values():
            node.setup()
        self.order = self.sort()
        self.updates = [self.walk(p) for p in self.params]

    # 1. what each box is --------------------------------------------------------

    def classify(self):
        unknown = {}
        for box in self.boxes.values():
            if box.cls in UI_BOXES:
                continue
            if box.cls == "live.dial":
                self.params.append(Param(box, len(self.params)))
            elif box.cls in SIGNAL:
                self.signals[box.id] = SIGNAL[box.cls](box)
            elif box.cls in CONTROL:
                self.controls[box.id] = box
            else:
                unknown.setdefault(box.cls, []).append(box.id)
        if unknown:
            raise CompileError(
                "patchc has no C++ for %s. It covers: %s" % (
                    "; ".join("%s (%s)" % (cls, ", ".join(ids)) for cls, ids in sorted(unknown.items())),
                    " ".join(SUPPORTED)))

        ids = {}
        for p in self.params:
            if p.id in ids or p.id == 0xFFFFFFFF:
                raise CompileError("the dials %r and %r need different varnames" % (ids.get(p.id), p.key))
            ids[p.id] = p.key

        for cls, what in (("plugin~", "input"), ("plugout~", "output")):
            found = [n for n in self.signals.values() if n.box.cls == cls]
            if len(found) != 1:
                raise CompileError("an effect has one %s (its %s); this patch has %d" % (cls, what, len(found)))

    # 2. signal cords ------------------------------------------------------------

    def connect_signals(self):
        self.edges = {id_: set() for id_ in self.signals}   # source -> destinations
        self.links = []                                     # (TapIn, TapOut)
        for src, outlet, dst, inlet in self.cords:
            node = self.signals.get(src.id)
            if node is None:
                continue   # a control cord; walk() follows those from the dials
            to = self.signals.get(dst.id)
            if to is None:
                raise CompileError("a signal from %s goes into %s, which is not a signal object" % (src, dst))
            if isinstance(node, TapIn):
                if not isinstance(to, TapOut):
                    raise CompileError("%s is connected to %s; a tapin~ only feeds tapout~" % (src, dst))
                if to.line not in (None, node):
                    raise CompileError("%s is connected to two tapin~ objects" % dst)
                if to.line is None:
                    to.line = node
                    self.links.append((node, to))
                continue
            if outlet >= node.outlets:
                raise CompileError("%s: patchc has no signal for outlet %d" % (src, outlet))
            if inlet not in to.signal_inlets():
                raise CompileError("a signal from %s goes into inlet %d of %s, which patchc only sets by number"
                                   % (src, inlet, dst))
            to.sig.setdefault(inlet, []).append(node.out(outlet))
            self.edges[src.id].add(dst.id)

    # 3. the order the objects run in --------------------------------------------

    def sort(self):
        """
        Order the signal objects so each runs after everything feeding it.

        A tapin~ normally runs before the tapout~ objects reading it. Where that would
        be circular (the tap's output finds its way back into the line) the tap is read
        first instead, which is what a feedback delay means. Any loop left after
        that has no delay in it and cannot be computed a sample at a time; Max refuses
        to run such a patch too.
        """
        graph = {id_: set(dsts) for id_, dsts in self.edges.items()}
        for line, tap in self.links:
            graph[line.box.id].add(tap.box.id)
        # One tap at a time, because turning one round can open the whole loop: a
        # ping-pong delay is a single loop through two lines, and only one of its taps
        # has to be read early for both hops to come out at their full length.
        while True:
            group = _components(graph)
            inside = [(line, tap) for line, tap in self.links
                      if not tap.in_loop and group[line.box.id] == group[tap.box.id]]
            if not inside:
                break
            line, tap = inside[0]
            tap.in_loop = True
            graph[line.box.id].discard(tap.box.id)

        waiting = {id_: 0 for id_ in graph}
        for dsts in graph.values():
            for d in dsts:
                waiting[d] += 1
        by_order = lambda id_: self.boxes[id_].order
        ready = sorted((id_ for id_, n in waiting.items() if n == 0), key=by_order)
        order = []
        while ready:
            id_ = ready.pop(0)
            order.append(self.signals[id_])
            for d in graph[id_]:
                waiting[d] -= 1
                if waiting[d] == 0:
                    ready.append(d)
            ready.sort(key=by_order)
        if len(order) != len(graph):
            stuck = sorted((id_ for id_, n in waiting.items() if n > 0), key=by_order)
            raise CompileError("these objects feed each other with no tapin~ / tapout~ delay in the loop: %s"
                               % ", ".join(str(self.boxes[id_]) for id_ in stuck))
        return order

    # 4. what a dial does --------------------------------------------------------

    def walk(self, param):
        """The statements that run when `param` changes: its control chain, in cord order."""
        lines = []
        names = {}

        def follow(src, outlet, value, path):
            for s, o, dst, inlet in self.cords:
                if s is not src or o != outlet:
                    continue
                if dst.id in self.signals:
                    setter = self.signals[dst.id].setters.get(inlet)
                    if setter is None:
                        raise CompileError("a number from %s goes into inlet %d of %s, which patchc does not model"
                                           % (src, inlet, dst))
                    if self.signals[dst.id].has_signal(inlet):
                        raise CompileError("inlet %d of %s has both a signal and a number arriving" % (inlet, dst))
                    lines.append("%s  // %s" % (setter(value), dst.text))
                elif dst.id in self.controls:
                    if inlet != 0:
                        raise CompileError("a cord from %s goes into inlet %d of %s; patchc only models the left inlet "
                                           "of a control object" % (src, inlet, dst))
                    if dst.id in path:
                        raise CompileError("the control objects around %s feed each other in a loop" % dst)
                    result = CONTROL[dst.cls](dst, value)
                    if isinstance(result, Num):
                        count = names[dst.id] = names.get(dst.id, 0) + 1
                        var = "c_%s%s" % (dst.ident, "" if count == 1 else "_%d" % count)
                        lines.append("const double %s = %s;  // %s" % (var, result.expr, dst.text))
                        result = Num(var)
                    follow(dst, 0, result, path | {dst.id})
                elif dst.cls == "live.dial":
                    raise CompileError("%s is driven by %s; patchc models dials the host sets, not each other"
                                       % (dst, src))
                # a cord into a comment or panel does nothing

        follow(param.box, 0, Num("v"), frozenset())
        follow(param.box, 1, Num("mxrt::valueToPosition(kParams[%d], v)" % param.index), frozenset())
        return lines

    # 5. the C++ -----------------------------------------------------------------

    def emit(self):
        name = self.name or "Effect"
        params = self.params
        nodes = self.order
        tail = any(n.stateful for n in nodes)
        out = []
        w = out.append

        w("// Effect.h: %s, compiled from its Max patch by patchc." % cstr(name))
        w("// Generated code. Change the patch (src/plugin/fxgen.py) and compile it again.")
        w("//")
        w("// Each line of process() is one object of the patch, named after its box, in an order")
        w("// where every object runs after what feeds it. update() is the control maths between")
        w("// each dial and the signal objects it sets.")
        w("")
        w("#pragma once")
        w("")
        w("#include \"mxrt.h\"")
        w("")
        w("class Effect {")
        w("public:")
        w("    static constexpr const char* kName = %s;" % cstr(name))
        w("    static constexpr const char* kSlug = %s;" % cstr(slug(name)))
        w("    static constexpr int kNumParams = %d;" % len(params))
        w("    // Whether sound can keep coming out after the input stops (filters, delays, reverb).")
        w("    static constexpr bool kHasTail = %s;" % ("true" if tail else "false"))
        w("")
        w("    // id, name, short name, min, max, initial, exponent, unit, integer")
        w("    static constexpr mxrt::ParamInfo kParams[%d] = {" % max(1, len(params)))
        for p in params:
            w("        {0x%08Xu, %s, %s, %s, %s, %s, %s, %d, %s},"
              % (p.id, cstr(p.name), cstr(p.short), lit(p.min), lit(p.max), lit(p.initial), lit(p.exponent),
                 p.unit, "true" if p.integer else "false"))
        if not params:
            w("        {0u, \"\", \"\", 0.0, 0.0, 0.0, 1.0, 1, false},  // the patch has no dials")
        w("    };")
        w("")
        w("    Effect() {")
        w("        for (int i = 0; i < kNumParams; i++) value_[i] = kParams[i].initial;")
        w("    }")
        w("")
        w("    // Call before process(), and again whenever the sample rate changes. The only")
        w("    // function here that allocates. Parameter values are kept.")
        w("    void prepare(double sampleRate) {")
        w("        sr_ = sampleRate;")
        for n in nodes:
            for line in n.prepare:
                w("        " + line)
        w("        for (int i = 0; i < kNumParams; i++) update(i);")
        for n in nodes:
            if isinstance(n, Line):
                w("        %s.reset();  // start at the dial's value, not on the way to it" % n.member)
        w("    }")
        w("")
        w("    // Silence everything still ringing. Parameter values are kept.")
        w("    void reset() {")
        for n in nodes:
            for line in n.reset:
                w("        " + line)
        w("    }")
        w("")
        w("    // A value in the dial's own units, clamped to its range. Call it from the thread")
        w("    // that calls process(), between calls.")
        w("    void setParam(int index, double value) {")
        w("        if (index < 0 || index >= kNumParams) return;")
        w("        value_[index] = mxrt::clampValue(kParams[index], value);")
        w("        update(index);")
        w("    }")
        w("")
        w("    double getParam(int index) const {")
        w("        return index >= 0 && index < kNumParams ? value_[index] : 0.0;")
        w("    }")
        w("")
        w("    // Stereo in, stereo out. `in` and `out` may be the same buffers.")
        w("    template <typename Sample>")
        w("    void process(const Sample* const* in, Sample* const* out, int frames) {")
        w("        for (int i = 0; i < frames; i++) {")
        w("            const double in_l = in[0][i], in_r = in[1][i];")
        for n in nodes:
            for line in n.tick():
                w("            %s%s" % (line, "" if isinstance(n, PlugOut) else "  // " + n.box.text))
        w("        }")
        w("    }")
        w("")
        w("private:")
        w("    void update(int index) {")
        w("        const double v = value_[index];")
        w("        (void)v;")
        w("        switch (index) {")
        for p, lines in zip(params, self.updates):
            w("            case %d: {  // %s" % (p.index, p.name))
            for line in lines:
                w("                " + line)
            w("                break;")
            w("            }")
        w("            default:")
        w("                break;")
        w("        }")
        w("    }")
        w("")
        w("    double sr_ = 44100.0;")
        w("    double value_[%d] = {};" % max(1, len(params)))
        for n in nodes:
            for line in n.members:
                w("    " + line)
        w("};")
        return "\n".join(out) + "\n"


def _components(graph):
    """Strongly connected components (Tarjan, iterative): node -> component number."""
    index, low, group = {}, {}, {}
    stack, on_stack = [], set()
    counter = [0, 0]
    for root in graph:
        if root in index:
            continue
        work = [(root, iter(sorted(graph[root])))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]
                    counter[0] += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(sorted(graph[nxt]))))
                    advanced = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    group[member] = counter[1]
                    if member == node:
                        break
                counter[1] += 1
    return group


def compile_patch(patch, name=None):
    """Patch JSON (a dict) to the source of its Effect.h. Raises CompileError."""
    return Compiler(patch, name).emit()


def objects(patch):
    """The object classes a patch uses, for reporting."""
    return sorted({Box(e["box"], 0).cls for e in patch["patcher"].get("boxes", [])})


def main(argv=None):
    ap = argparse.ArgumentParser(description="Compile a Max patch (the JSON fxgen produces) into Effect.h.")
    ap.add_argument("patch", help="a .json or .maxpat file")
    ap.add_argument("-o", "--output", help="where to write Effect.h (default: standard output)")
    ap.add_argument("--name", help="the effect's name (default: the file's name)")
    args = ap.parse_args(argv)
    with open(args.patch, encoding="utf-8") as fh:
        patch = json.load(fh)
    name = args.name or os.path.splitext(os.path.basename(args.patch))[0].replace("_", " ")
    try:
        source = compile_patch(patch, name)
    except CompileError as err:
        print("patchc: %s" % err, file=sys.stderr)
        return 1
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(source)
    else:
        sys.stdout.write(source)
    return 0


if __name__ == "__main__":
    sys.exit(main())
