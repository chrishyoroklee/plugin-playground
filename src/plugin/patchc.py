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
  - An instrument has a notein instead of a plugin~. Its notes are followed through
    poly, pack, route and unpack to the voices, and what each voice does with a pitch
    and a velocity becomes the code behind noteOn() and noteOff().
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
        # For `+ 1.` and its kind: the C++ member holding the right operand, when a cord
        # into the right inlet can change it. Otherwise the argument is used as written.
        self.operand = None

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
        operand = box.operand or lit(arg)
        if isinstance(arg, int):
            return Num("mxrt::toInt(%s)" % op("mxrt::toInt(%s)" % x, operand))
        return Num(op(x, operand))
    return build


def _divide(box, value):
    if isinstance(box.num(0, 0), int):
        raise CompileError("%s divides in whole numbers; patchc models the float one (write `2.` for `2`)" % box)
    return Num("%s / %s" % (_one(box, value), box.operand or lit(box.num(0))))


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


# The control objects whose right inlet stores an operand for the next number on the left.
ARITHMETIC = {"+", "*", "!-", "/"}

CONTROL = {
    "+": _arith(lambda a, b: "%s + %s" % (a, b)),
    "*": _arith(lambda a, b: "%s * %s" % (a, b)),
    "!-": _arith(lambda a, b: "%s - %s" % (b, a)),
    "/": _divide,
    "mtof": _unary("mxrt::mtof(%s)"),
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


class Abs(Expr):
    prefix = "abs"

    def setup(self):
        self.expr = "std::fabs(%s)" % self.input(0)


class AtoDb(Expr):
    prefix = "atodb"

    def setup(self):
        self.expr = "mxrt::atodb(%s)" % self.input(0)


class DbToA(Expr):
    prefix = "dbtoa"

    def setup(self):
        self.expr = "mxrt::dbtoa(%s)" % self.input(0)


class MsToSamps(Expr):
    """mstosamps~: milliseconds as samples at the rate the plugin runs at."""
    prefix = "mstosamps"
    # Its second outlet gives a number for a number sent in. Only the signal one is modelled:
    # a cord from the other is refused (outlet out of range), not compiled wrong.

    def setup(self):
        self.expr = "%s * sr_ / 1000.0" % self.input(0)


class Slide(Expr):
    """slide~ up down: a smoother that rises and falls at different speeds (mxrt::Slide)."""
    prefix = "slide"
    stateful = True

    def signal_inlets(self):
        return {0, 1, 2}

    def setup(self):
        self.kernel("mxrt::Slide", "")
        up = self.operand(1, "up", self.box.num(0, 1))
        down = self.operand(2, "down", self.box.num(1, 1))
        self.expr = "%s.tick(%s, %s, %s)" % (self.member, self.input(0), up, down)


class Sig(Expr):
    """sig~: a number as a signal. A number in the left inlet replaces it."""
    prefix = "sig"

    def setup(self):
        self.expr = self.operand(0, "v", self.box.num(0, 0))


class Snapshot(Signal):
    """
    snapshot~ N: every N milliseconds, the signal's value as a number down a control cord.

    It is the one way from the signal side of a patch back to the control side, and what
    lets a signal move a dial's value. In the compiled effect it is a countdown in the
    sample loop: when it reaches zero, the statements a number leaving this box would run
    (`fired`, which the compiler fills in by following the cords) run with this sample.
    """
    prefix = "snapshot"
    outlets = 0   # nothing leaves it as a signal

    def __init__(self, box):
        super().__init__(box)
        self.fired = []
        self.sample = "s_%s" % box.ident
        self.count = self.member + "count"
        self.period = self.member + "period"

    def setup(self):
        ms = self.box.num(0, 0)
        if not ms > 0:
            raise CompileError("%s needs its interval as an argument: patchc models the one that reports by itself" % self.box)
        self.members.append("double %s = 1.0;  // %s" % (self.period, self.box.text))
        self.members.append("double %s = 0.0;  // %s" % (self.count, self.box.text))
        # the interval in whole samples, at least one
        self.prepare.append("%s = std::max(1.0, std::floor(%s * sr_ / 1000.0));" % (self.period, lit(ms)))
        self.prepare.append("%s = 0.0;" % self.count)
        self.reset.append("%s = 0.0;" % self.count)

    def inner(self):
        """What runs when the countdown reaches zero."""
        return ["const double %s = %s;" % (self.sample, self.input(0))] + self.fired

    def tick(self):
        return (["if (%s <= 0.0) {" % self.count, "    %s = %s;" % (self.count, self.period)]
                + ["    " + line for line in self.inner()]
                + ["}", "%s -= 1.0;" % self.count])

    def out(self, outlet):
        raise CompileError("%s: what leaves a snapshot~ is a number, not a signal" % self.box)


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


class Oscillator(Expr):
    """An oscillator: its frequency is a number in the left inlet, or a signal."""

    kernel_type = None

    def setup(self):
        if len(self.box.args) > 1:
            raise CompileError("%s: patchc models the frequency argument only" % self.box)
        self.kernel(self.kernel_type, "sr_, %s" % lit(self.box.num(0, 0)))
        if self.has_signal(0):
            self.expr = "%s.tick(%s)" % (self.member, self.input(0))
        else:
            self.call(0, "setFreq")
            self.expr = "%s.tick()" % self.member


class Cycle(Oscillator):
    prefix = "cycle"
    kernel_type = "mxrt::Cycle"


class Saw(Oscillator):
    prefix = "saw"
    kernel_type = "mxrt::Saw"


class Rect(Oscillator):
    prefix = "rect"
    kernel_type = "mxrt::Rect"


class Tri(Oscillator):
    prefix = "tri"
    kernel_type = "mxrt::Tri"


class Adsr(Expr):
    prefix = "adsr"
    stateful = True

    def signal_inlets(self):
        return set()

    def setup(self):
        self.box.need(4)
        self.kernel("mxrt::Adsr", "sr_, " + ", ".join(lit(self.box.num(i)) for i in range(4)))
        self.call(0, "gate")
        for inlet, name in enumerate(("attack", "decay", "sustain", "release"), start=1):
            self.field(inlet, name)
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
    "abs~": Abs,
    "atodb~": AtoDb,
    "dbtoa~": DbToA,
    "mstosamps~": MsToSamps,
    "slide~": Slide,
    "sig~": Sig,
    "snapshot~": Snapshot,
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
    "saw~": Saw,
    "rect~": Rect,
    "tri~": Tri,
    "adsr~": Adsr,
    "line~": Line,
    "tapin~": TapIn,
    "tapout~": TapOut,
}

# The boxes that carry a note from notein to a voice. They are read as one unit
# (Compiler.find_voices), not object by object.
NOTE_PATH = {"notein", "poly", "route", "unpack"}

SUPPORTED = sorted(set(SIGNAL) | set(CONTROL) | NOTE_PATH | UI_BOXES | {"live.dial"})


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


# ── pictures ────────────────────────────────────────────────────────────────────

def without_pictures(patcher):
    """
    The patch without its pictures: (boxes, cords, pictures).

    A picture (fxgen's graph(): an EQ's curve, a delay's echoes) is a v8ui that draws a
    module from its dials and sets them when it is dragged. It makes no sound. In the
    patch it is the v8ui, a `prepend` from each dial into it and a `route` from it back
    to the dials, and none of the three is compiled: the plugin's own window draws the
    same picture from the same dials (native/ui/face.html). What is kept is where each
    one goes, for that window: [{"kind", "rect"}].
    """
    boxes = [e["box"] for e in patcher.get("boxes", [])]
    lines = [e["patchline"] for e in patcher.get("lines", [])]
    drawn = {b["id"] for b in boxes if b.get("maxclass") == "v8ui"}
    gone = set(drawn)
    for line in lines:
        src, dst = line["source"][0], line["destination"][0]
        if dst in drawn:
            gone.add(src)
        if src in drawn:
            gone.add(dst)
    by_id = {b["id"]: b for b in boxes}
    pictures = []
    for b in boxes:
        if b["id"] not in drawn:
            continue
        # What the picture was set up with is the first line of its script: `var PP = {...};`
        setup = {}
        first = str((b.get("textfile") or {}).get("text", "")).split("\n", 1)[0].strip()
        if first.startswith("var PP = ") and first.endswith(";"):
            try:
                setup = json.loads(first[len("var PP = "):-1])
            except ValueError:
                setup = {}
        # Which dial is which: each dial reaches the picture through a `prepend <its id>`.
        dials = {}
        for line in lines:
            if line["destination"][0] != b["id"]:
                continue
            words = str(by_id.get(line["source"][0], {}).get("text", "")).split()
            if len(words) == 2 and words[0] == "prepend":
                feeder = next((l["source"][0] for l in lines if l["destination"][0] == line["source"][0]), None)
                if feeder:
                    dials[words[1]] = feeder
        pictures.append({"kind": str(setup.get("kind") or str(b.get("varname", "")).rsplit("_graph", 1)[0]),
                         "rect": [float(v) for v in (b.get("presentation_rect") or [0, 0, 0, 0])[:4]],
                         "options": setup.get("options") or {}, "colours": setup.get("colours") or {}, "dials": dials})
    return ([b for b in boxes if b["id"] not in gone],
            [l for l in lines if l["source"][0] not in gone and l["destination"][0] not in gone],
            pictures)


# ── the compiler ────────────────────────────────────────────────────────────────

class Compiler:
    def __init__(self, patch, name=None):
        patcher = patch.get("patcher") if isinstance(patch, dict) else None
        if not isinstance(patcher, dict):
            raise CompileError("not a Max patch: there is no \"patcher\" at the top")
        self.name = name
        self.device_width = patcher.get("devicewidth") or 0
        boxes, lines, self.pictures = without_pictures(patcher)
        self.boxes = {}
        for order, entry in enumerate(boxes):
            box = Box(entry, order)
            self.boxes[box.id] = box
        self.cords = []   # (source box, outlet, destination box, inlet), in patch order
        for line in lines:
            (src, outlet), (dst, inlet) = line["source"], line["destination"]
            if src not in self.boxes or dst not in self.boxes:
                raise CompileError("a cord runs between %s and %s, and one of them is not in the patch" % (src, dst))
            self.cords.append((self.boxes[src], int(outlet), self.boxes[dst], int(inlet)))

        self.params = []
        self.signals = {}    # box id -> Signal
        self.controls = {}   # box id -> Box
        self.operands = []   # C++ members for right operands a cord can change
        self.poly = None     # the poly box, in an instrument
        self.voices = []     # one unpack box per voice (None where a voice goes nowhere)
        self.note_path = self.find_voices()
        self.classify()
        self.connect_signals()
        self.find_operands()
        for node in self.signals.values():
            node.setup()
        self.order = self.sort()
        self.updates = [self.walk([(p.box, 0, Num("v")),
                                   (p.box, 1, Num("mxrt::valueToPosition(kParams[%d], v)" % p.index))])
                        for p in self.params]
        # what a number leaving each snapshot~ does, for the countdown in the sample loop to run
        for node in self.signals.values():
            if isinstance(node, Snapshot):
                node.fired = self.walk([(node.box, 0, Num(node.sample))])
        # unpack sends its right outlet first, so a voice hears the velocity, then the pitch
        self.voice_code = [self.walk([(box, 1, self.unpacked(box, 1, "velocity")),
                                      (box, 0, self.unpacked(box, 0, "pitch"))]) if box else []
                           for box in self.voices]

    @property
    def instrument(self):
        return self.poly is not None

    # 0. the way in for notes -----------------------------------------------------

    def cords_from(self, box, outlet):
        return [(dst, inlet) for src, o, dst, inlet in self.cords if src is box and o == outlet]

    def find_voices(self):
        """
        An instrument's note path, which has to be exactly this:

            notein -> poly N [steal] -> pack 0 0 0 -> route 1 2 .. N -> one unpack per voice

        Returns the ids of those boxes. They are not compiled one by one: poly becomes an
        mxrt::Poly, and the rest is the switch that hands its messages to the voices.
        """
        noteins = [b for b in self.boxes.values() if b.cls == "notein"]
        if not noteins:
            return set()
        if len(noteins) > 1:
            raise CompileError("an instrument has one notein; this patch has %d" % len(noteins))
        notein = noteins[0]
        shape = "patchc reads notes through notein -> poly -> pack 0 0 0 -> route 1 2 .. -> unpack"

        def only(box, outlet, cls, inlet):
            found = self.cords_from(box, outlet)
            if len(found) != 1 or found[0][0].cls != cls or found[0][1] != inlet:
                raise CompileError("outlet %d of %s should go to inlet %d of one %s: %s"
                                   % (outlet, box, inlet, cls, shape))
            return found[0][0]

        poly = only(notein, 0, "poly", 0)
        if only(notein, 1, "poly", 1) is not poly:
            raise CompileError("the pitch and velocity of %s should go to the same poly: %s" % (notein, shape))
        count = poly.num(0)
        if not isinstance(count, int) or not 1 <= count <= 64:
            raise CompileError("%s: the number of voices should be between 1 and 64" % poly)
        pack = only(poly, 0, "pack", 0)
        if only(poly, 1, "pack", 1) is not pack or only(poly, 2, "pack", 2) is not pack or len(pack.args) != 3:
            raise CompileError("the three outlets of %s should go to one `pack 0 0 0`: %s" % (poly, shape))
        route = only(pack, 0, "route", 0)
        if route.args != list(range(1, count + 1)):
            raise CompileError("%s should name every voice of %s, `route 1 .. %d`: %s" % (route, poly, count, shape))

        self.poly = poly
        path = {notein.id, poly.id, pack.id, route.id}
        for voice in range(count):
            found = self.cords_from(route, voice)
            if not found:
                self.voices.append(None)
                continue
            unpack = only(route, voice, "unpack", 0)
            if len(unpack.args) != 2:
                raise CompileError("%s should be `unpack 0 0`: a pitch and a velocity" % unpack)
            self.voices.append(unpack)
            path.add(unpack.id)
        return path

    @staticmethod
    def unpacked(box, outlet, name):
        """A pitch or velocity leaving an unpack: whole if that slot is an int, as it is in Max."""
        return Num("mxrt::toInt(%s)" % name if isinstance(box.args[outlet], int) else name)

    # 1. what each box is --------------------------------------------------------

    def classify(self):
        unknown = {}
        for box in self.boxes.values():
            if box.cls in UI_BOXES or box.id in self.note_path:
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

        count = lambda cls: len([n for n in self.signals.values() if n.box.cls == cls])
        if count("plugout~") != 1:
            raise CompileError("a device has one plugout~ (its output); this patch has %d" % count("plugout~"))
        if self.instrument and count("plugin~") != 0:
            raise CompileError("an instrument takes notes, not audio: it has a notein, so it cannot have a plugin~")
        if not self.instrument and count("plugin~") != 1:
            raise CompileError("an effect has one plugin~ (its input); this patch has %d" % count("plugin~"))

    # 2. signal cords ------------------------------------------------------------

    def connect_signals(self):
        self.edges = {id_: set() for id_ in self.signals}   # source -> destinations
        self.links = []                                     # (TapIn, TapOut)
        for src, outlet, dst, inlet in self.cords:
            node = self.signals.get(src.id)
            if node is None:
                continue   # a control cord; walk() follows those from the dials
            if isinstance(node, Snapshot):
                continue   # what leaves a snapshot~ is a number; walk() follows it from there
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

    # 4. what a dial or a note does ---------------------------------------------

    def find_operands(self):
        """Give a member to every `+ 1.` whose right inlet a cord can reach."""
        for src, outlet, dst, inlet in self.cords:
            if dst.id in self.controls and inlet == 1 and dst.cls in ARITHMETIC and dst.operand is None:
                dst.operand = "c_%s_operand_" % dst.ident
                self.operands.append((dst, dst.operand))

    def walk(self, sources):
        """
        The statements that run when a number leaves one of `sources`, a list of
        (box, outlet, value): everything downstream of it, in cord order.
        """
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
                    if inlet == 1 and dst.operand:
                        # a right inlet: remembered for the next number on the left
                        stored = _one(dst, value)
                        if isinstance(dst.num(0, 0), int):
                            stored = "mxrt::toInt(%s)" % stored
                        lines.append("%s = %s;  // %s, right inlet" % (dst.operand, stored, dst.text))
                        continue
                    if inlet != 0:
                        raise CompileError("a cord from %s goes into inlet %d of %s; patchc only models the left inlet "
                                           "of a control object, and the right inlet of + * / !-" % (src, inlet, dst))
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
                elif dst.id in self.note_path:
                    raise CompileError("a number from %s goes into %s; patchc only feeds the note path from notein"
                                       % (src, dst))
                # a cord into a comment or panel does nothing

        for box, outlet, value in sources:
            follow(box, outlet, value, frozenset())
        return lines

    # 5. what it looks like ------------------------------------------------------

    def face(self):
        """
        The device as the page draws it: every box the patch puts in presentation view,
        where the patch puts it, with each dial tied to its parameter. The plugin's window
        (native/ui/face.html) is drawn from this, so a device looks the same in a DAW as
        it does in the browser and in Live. The page's own drawing of the same boxes is
        renderDevice() in device-view.ts.
        """
        def colour(value, fallback):
            if not isinstance(value, list) or len(value) < 3:
                return fallback
            try:
                r, g, b = (int(round(min(1.0, max(0.0, float(c))) * 255)) for c in value[:3])
                a = float(value[3]) if len(value) > 3 else 1.0
            except (TypeError, ValueError):
                return fallback
            return "rgba(%d, %d, %d, %s)" % (r, g, b, ("%g" % a))

        by_box = {p.box.id: p for p in self.params}
        shown = [b for b in self.boxes.values()
                 if b.raw.get("presentation") == 1 and isinstance(b.raw.get("presentation_rect"), list)]
        # Max paints `background` boxes under everything else.
        shown.sort(key=lambda b: -int(b.raw.get("background") or 0))
        items = []
        try:
            width = float(self.device_width)   # the width fxgen set, which has the last card's margin in it
        except (TypeError, ValueError):
            width = 0.0
        for box in shown:
            rect = [float(v) for v in box.raw["presentation_rect"][:4]]
            width = max(width, rect[0] + rect[2])
            raw = box.raw
            if box.cls == "panel":
                items.append({"type": "panel", "rect": rect, "color": colour(raw.get("bgcolor"), "#fdf2eb"),
                              "radius": float(raw.get("rounded") or 0)})
            elif box.cls in ("live.comment", "comment"):
                items.append({"type": "label", "rect": rect, "text": str(raw.get("text") or ""),
                              "size": float(raw.get("fontsize") or 10), "color": colour(raw.get("textcolor"), "#2f1007"),
                              "center": int(raw.get("textjustification") or 0) == 1})
            elif box.id in by_box:
                p = by_box[box.id]
                items.append({"type": "dial", "rect": rect, "index": p.index, "accent": colour(raw.get("activedialcolor"), "#3f78bd"),
                              "ink": colour(raw.get("textcolor"), "#2f1007"), "track": colour(raw.get("dialcolor"), "#e6bdb3"),
                              "param": {"name": p.name, "short": p.short, "min": p.min, "max": p.max, "initial": p.initial,
                                        "exponent": p.exponent, "unit": p.unit, "integer": p.integer}})
        # The pictures, each with the parameters behind its dials, so the window can draw
        # them from the same values and turn the same dials (graphs.js, as on the page).
        for picture in self.pictures:
            tied = [{"id": name, "index": by_box[box_id].index} for name, box_id in picture["dials"].items() if box_id in by_box]
            if tied and picture["rect"][2] > 0:
                width = max(width, picture["rect"][0] + picture["rect"][2])
                items.append({"type": "graph", "rect": picture["rect"], "kind": picture["kind"], "options": picture["options"],
                              "colours": picture["colours"], "dials": tied})
        # The backdrop is the panel as wide as the device; the window round it takes its colour.
        backdrop = next((i["color"] for i in items if i["type"] == "panel" and i["rect"][2] >= 0.9 * width and width > 0), "#feddd4")
        return {"width": width, "height": 169.0, "background": backdrop, "items": items}

    def face_packed(self):
        """face(), as one word: JSON in base64. "" for a device with nothing to show."""
        import base64
        face = self.face()
        if not face["items"]:
            return ""
        return base64.b64encode(json.dumps(face, separators=(",", ":"), ensure_ascii=True).encode("ascii")).decode("ascii")

    # 6. the C++ -----------------------------------------------------------------

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
        w("    // An instrument is played with noteOn() and noteOff(); an effect is given audio.")
        w("    static constexpr bool kIsInstrument = %s;" % ("true" if self.instrument else "false"))
        # What the plugin's window draws (face()), in pieces short enough for any compiler.
        packed = self.face_packed()
        w("    // The device's face: where each card, label and dial goes, as JSON in base64.")
        w("    static constexpr const char* kFace =")
        for at in range(0, max(1, len(packed)), 1000):
            w("        \"%s\"%s" % (packed[at:at + 1000], ";" if at + 1000 >= len(packed) else ""))
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
        if self.instrument:
            w("        poly_.prepare(%d, %s);  // %s" % (self.poly.num(0), "true" if self.poly.num(1, 0) else "false",
                                                        self.poly.text))
        for box, member in self.operands:
            w("        %s = %s;" % (member, lit(box.num(0, 0))))
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
        if self.instrument:
            w("        poly_.reset();")
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
        if self.instrument:
            w("    // A key going down, with a velocity from 1 to 127, and the same key coming up.")
            w("    // Call them from the thread that calls process(), between calls.")
            w("    void noteOn(int key, double velocity) { note(key, velocity < 1.0 ? 1.0 : velocity); }")
            w("    void noteOff(int key) { note(key, 0.0); }")
            w("")
            w("    // Stereo out. `in` is not read: an instrument has no audio input.")
        else:
            w("    // Here so that code holding any Effect can be written once. An effect ignores notes.")
            w("    void noteOn(int, double) {}")
            w("    void noteOff(int) {}")
            w("")
            w("    // Stereo in, stereo out. `in` and `out` may be the same buffers.")
        w("    template <typename Sample>")
        w("    void process(const Sample* const* in, Sample* const* out, int frames) {")
        if self.instrument:
            w("        (void)in;")
        w("        for (int i = 0; i < frames; i++) {")
        if not self.instrument:
            w("            const double in_l = in[0][i], in_r = in[1][i];")
        for n in nodes:
            for line in n.tick():
                w("            %s%s" % (line, "" if isinstance(n, PlugOut) else "  // " + n.box.text))
        w("        }")
        w("    }")
        w("")
        w("private:")
        if self.instrument:
            w("    // notein -> poly -> pack -> route: the note goes to the voice poly picks for it.")
            w("    void note(int key, double velocity) {")
            w("        mxrt::Poly::Message sent[2];")
            w("        const int count = poly_.note(key, velocity, sent);")
            w("        for (int k = 0; k < count; k++) voice(sent[k].voice, sent[k].pitch, sent[k].velocity);")
            w("    }")
            w("")
            w("    // What each voice does with a note. A velocity of 0 is the note being let go.")
            w("    void voice(int number, double pitch, double velocity) {")
            w("        (void)pitch;")
            w("        (void)velocity;")
            w("        switch (number) {")
            for number, lines in enumerate(self.voice_code, start=1):
                w("            case %d: {" % number)
                for line in lines:
                    w("                " + line)
                w("                break;")
                w("            }")
            w("            default:")
            w("                break;")
            w("        }")
            w("    }")
            w("")
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
        if self.instrument:
            w("    mxrt::Poly poly_;  // %s" % self.poly.text)
        for box, member in self.operands:
            w("    double %s = %s;  // %s, right inlet" % (member, lit(box.num(0, 0)), box.text))
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


# ── the other backend: a program for the runtime plugin ─────────────────────────
#
# A plugin built from Effect.h has to be compiled, which a web page cannot do. The
# runtime plugin (native/runtime) is compiled once and runs a device it is handed as a
# program: the same statements emit() writes as C++, one instruction each, over the same
# kernels. So nothing about how a patch is read is decided twice. The statements are
# taken from the compiler as it wrote them and translated here, which is why this
# section parses a small part of C++: the part patchc itself writes.

PROGRAM_FORMAT = 1
# The newest runtime this file writes for. A device asks for the oldest runtime that can
# run it, which is 1 unless it uses something a later one added (NEEDS, below), so a
# device with none of that still loads in a runtime installed before it existed. When an
# instruction, a kernel or a function is added: raise this, and list what was added.
PROGRAM_RUNTIME = 3
# Runtime 2: `every` (snapshot~), and the functions abs, max and floor.
# Runtime 3: the function atodb and the kernel Slide (atodb~ and slide~, for the compressor).
NEEDS = {"every": 2, "f abs": 2, "f max": 2, "f floor": 2, "f atodb": 3}
KERNEL_NEEDS = {"Slide": 3}

# Registers every program has, by number.
FIXED_REGISTERS = ["sr_", "v", "pitch", "velocity", "in_l", "in_r", "out_l", "out_r"]

_TOKEN = re.compile(r"\s*(?:(\d+\.?\d*(?:[eE][-+]?\d+)?)|([A-Za-z_][A-Za-z_0-9]*(?:::[A-Za-z_][A-Za-z_0-9]*)*)|(.))")


def _tokens(text):
    out = []
    at = 0
    text = text.rstrip()
    while at < len(text):
        m = _TOKEN.match(text, at)
        if not m:
            raise CompileError("cannot read the statement %r" % text)
        at = m.end()
        if m.group(1) is not None:
            out.append(("num", m.group(1)))
        elif m.group(2) is not None:
            out.append(("name", m.group(2)))
        else:
            out.append(("op", m.group(3)))
    return out


def _quote(text):
    """A name as one word: anything but letters, digits, - and _ becomes %XX."""
    return "".join(c if (c.isascii() and c.isalnum()) or c in "-_" else "".join("%%%02X" % b for b in c.encode("utf-8"))
                   for c in str(text)) or "%00"


class Program:
    """Translates one Compiler's statements into the runtime's instructions."""

    FUNCTIONS = {"mxrt::clip": "clip", "mxrt::pong": "pong", "mxrt::overdrive": "overdrive", "mxrt::scale": "scale",
                 "mxrt::toInt": "toInt", "mxrt::mtof": "mtof", "mxrt::dbtoa": "dbtoa", "std::pow": "pow",
                 "std::tanh": "tanh", "std::sqrt": "sqrt", "std::sin": "sin", "std::cos": "cos",
                 "std::fabs": "abs", "std::max": "max", "std::floor": "floor", "mxrt::atodb": "atodb"}

    def __init__(self, compiler):
        self.c = compiler
        self.registers = {name: i for i, name in enumerate(FIXED_REGISTERS)}
        self.initial = {}     # register -> the number it starts at
        self.constants = {}   # literal -> register
        self.kernels = {}     # member name -> (number, type)
        self.code = None

    # registers --------------------------------------------------------------------

    def register(self, name):
        if name not in self.registers:
            self.registers[name] = len(self.registers)
        return self.registers[name]

    def temporary(self):
        return self.register("%%t%d" % len(self.registers))

    def constant(self, text):
        value = float(text)
        key = repr(value)
        if key not in self.constants:
            self.constants[key] = self.register("%%c%s" % key)
            self.initial[self.constants[key]] = key
        return self.constants[key]

    # statements -------------------------------------------------------------------

    def declare(self, line):
        """A member: `mxrt::OnePole onepole_3_;` or `double mul_3_b = 2.5;`."""
        words = line.split("  // ")[0].rstrip(";").split()
        if words[0] == "double":
            self.initial[self.register(words[1])] = repr(float(words[3]))
        elif words[0].startswith("mxrt::"):
            self.kernels[words[1]] = (len(self.kernels), words[0][len("mxrt::"):])
        else:
            raise CompileError("cannot read the member %r" % line)

    def statement(self, line):
        text = line.split("  // ")[0].strip()
        if not text.endswith(";"):
            raise CompileError("cannot read the statement %r" % line)
        text = text[:-1]
        if text.startswith("const double "):
            name, expr = text[len("const double "):].split(" = ", 1)
            self.expression(expr, self.register(name))
        elif text.startswith("out["):
            channel = int(text[4])
            expr = text.split(" = static_cast<Sample>(", 1)[1][:-1]
            self.expression(expr, self.register(("out_l", "out_r")[channel]))
        elif " = " in text and "(" not in text.split(" = ", 1)[0]:
            target, expr = text.split(" = ", 1)
            if "." in target:
                kernel, field = target.split(".")
                self.code.append("s %d %s %d" % (self.kernels[kernel][0], field, self.expression(expr)))
            else:
                self.expression(expr, self.register(target))
        else:
            self.tokens = _tokens(text)
            self.at = 0
            self.primary(void=True)
            if self.at != len(self.tokens):
                raise CompileError("cannot read the statement %r" % line)

    # expressions: + - * / over numbers, names, calls and fields --------------------

    # Where each instruction keeps the register it writes, by its first word.
    WRITES = {"+": 1, "-": 1, "*": 1, "/": 1, "neg": 1, "pos": 1, "g": 1, "f": 2, "c": 3}

    def expression(self, text, into=None):
        self.tokens = _tokens(text)
        self.at = 0
        fresh = len(self.registers)
        result = self.sum()
        if self.at != len(self.tokens):
            raise CompileError("cannot read the expression %r" % text)
        if into is None or result == into:
            return result
        # The last instruction wrote a temporary nothing else has seen: write `into` instead.
        words = self.code[-1].split() if self.code else [""]
        at = self.WRITES.get(words[0])
        if result >= fresh and at is not None and int(words[at]) == result:
            words[at] = str(into)
            self.code[-1] = " ".join(words)
        else:
            self.code.append("= %d %d" % (into, result))
        return into

    def peek(self):
        return self.tokens[self.at] if self.at < len(self.tokens) else (None, None)

    def take(self, value=None):
        token = self.peek()
        if token[0] is None or (value is not None and token[1] != value):
            raise CompileError("expected %r in a statement patchc wrote" % (value or "more"))
        self.at += 1
        return token

    def binary(self, operators, operand):
        left = operand()
        while self.peek() in [("op", o) for o in operators]:
            op = self.take()[1]
            right = operand()
            target = self.temporary()
            self.code.append("%s %d %d %d" % (op, target, left, right))
            left = target
        return left

    def sum(self):
        return self.binary("+-", lambda: self.binary("*/", self.unary))

    def unary(self):
        if self.peek() == ("op", "-"):
            self.take()
            if self.peek()[0] == "num":
                return self.constant("-" + self.take()[1])
            value = self.unary()
            target = self.temporary()
            self.code.append("neg %d %d" % (target, value))
            return target
        return self.primary()

    def arguments(self):
        self.take("(")
        args = []
        while self.peek() != ("op", ")"):
            args.append(self.sum())
            if self.peek() == ("op", ","):
                self.take()
        self.take(")")
        return args

    def primary(self, void=False):
        kind, value = self.take()
        if kind == "num":
            return self.constant(value)
        if (kind, value) == ("op", "("):
            inner = self.sum()
            self.take(")")
            return inner
        if kind != "name":
            raise CompileError("cannot read %r in a statement patchc wrote" % value)
        if value in ("true", "false"):
            return self.constant("1" if value == "true" else "0")
        if value == "mxrt::valueToPosition":
            # mxrt::valueToPosition(kParams[3], v): where the dial stands, 0 to 1
            self.take("(")
            self.take("kParams")
            self.take("[")
            index = int(self.take()[1])
            self.take("]")
            self.take(",")
            arg = self.sum()
            self.take(")")
            target = self.temporary()
            self.code.append("pos %d %d %d" % (target, index, arg))
            return target
        if value in self.FUNCTIONS:
            args = self.arguments()
            target = self.temporary()
            self.code.append("f %s %d %s" % (self.FUNCTIONS[value], target, " ".join(map(str, args))))
            return target
        if value in self.kernels:
            number, _ = self.kernels[value]
            self.take(".")
            member = self.take()[1]
            if self.peek() == ("op", "("):
                args = self.arguments()
                target = -1 if void else self.temporary()
                self.code.append(("c %d %s %d %s" % (number, member, target, " ".join(map(str, args)))).rstrip())
                return target
            target = self.temporary()
            self.code.append("g %d %d %s" % (target, number, member))
            return target
        if "::" in value or self.peek() == ("op", "("):
            raise CompileError("the runtime has no instruction for %r" % value)
        return self.register(value)

    # the whole device -------------------------------------------------------------

    def section(self, title, lines):
        self.code = []
        for line in lines:
            self.statement(line)
        return ["@%s" % title] + self.code

    def emit(self, settings=None):
        c = self.c
        name = c.name or "Effect"
        nodes = c.order
        for _, member in c.operands:
            self.initial[self.register(member)] = "0.0"
        for n in nodes:
            for line in n.members:
                self.declare(line)

        body = []
        body += self.section("prepare",
                             ["%s = %s;" % (member, lit(box.num(0, 0))) for box, member in c.operands]
                             + [line for n in nodes for line in n.prepare])
        # After the dials have been applied: a ramp starts at its dial's value, not on the way to it.
        body += self.section("settle", ["%s.reset();" % n.member for n in nodes if isinstance(n, Line)])
        body += self.section("reset", [line for n in nodes for line in n.reset])
        # The sample loop. A snapshot~ is one instruction there, `every`, which counts down
        # and runs a section of its own (what leaves the box) when it gets to zero.
        every = []
        self.code = []
        for n in nodes:
            if isinstance(n, Snapshot):
                self.code.append("every %d %d %d" % (len(every), self.register(n.count), self.register(n.period)))
                every.append(n)
            else:
                for line in n.tick():
                    self.statement(line)
        body += ["@process"] + self.code
        for index, n in enumerate(every):
            body += self.section("every %d" % index, n.inner())
        for p, lines in zip(c.params, c.updates):
            body += self.section("update %d" % p.index, lines)
        for number, lines in enumerate(c.voice_code, start=1):
            body += self.section("voice %d" % number, lines)

        needs = max([1] + [NEEDS.get(" ".join(line.split()[:2 if line.startswith("f ") else 1]), 1) for line in body]
                    + [KERNEL_NEEDS.get(ctype, 1) for _, ctype in self.kernels.values()])
        out = ["ppdevice %d" % PROGRAM_FORMAT, "runtime %d" % needs,
               "name %s" % _quote(name), "slug %s" % slug(name),
               "kind %s" % ("instrument" if c.instrument else "effect"),
               "tail %d" % (1 if any(n.stateful for n in nodes) else 0)]
        if c.instrument:
            out.append("poly %d %d" % (c.poly.num(0), 1 if c.poly.num(1, 0) else 0))
        for p in c.params:
            out.append("param %d %s %s %s %s %d %d %s %s" % (
                p.id, lit(p.min), lit(p.max), lit(p.initial), lit(p.exponent), p.unit, 1 if p.integer else 0,
                _quote(p.name), _quote(p.short)))
        out.append("registers %d" % len(self.registers))
        for register in sorted(self.initial):
            out.append("n %d %s" % (register, self.initial[register]))
        for member, (number, ctype) in sorted(self.kernels.items(), key=lambda item: item[1][0]):
            out.append("k %d %s" % (number, ctype))
        out += body
        out.append("@end")
        # What the plugin's window draws. After the program, where a runtime from before
        # there was a window stops reading.
        packed = c.face_packed()
        if packed:
            out.append("face %s" % packed)
        if settings is not None:
            # What the page needs to open this device again. The runtime does not read it.
            import base64
            packed = json.dumps(settings, separators=(",", ":"), ensure_ascii=True).encode("ascii")
            out.append("settings %s" % base64.b64encode(packed).decode("ascii"))
        return "\n".join(out) + "\n"


def compile_program(patch, name=None, settings=None):
    """Patch JSON (a dict) to a device file for the runtime plugin. Raises CompileError."""
    return Program(Compiler(patch, name)).emit(settings)



def device_for(settings_json):
    """
    For the page, which runs this under Pyodide: settings as JSON to the text of the
    device file its Download hands over. fxgen builds the patch, as it does for the
    sound in the browser and for the .amxd, so all three are the same device.
    """
    import fxgen
    result = fxgen.run(json.loads(settings_json), want_amxd=False)
    return compile_program(result["json"], result["settings"]["name"], result["settings"])


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
