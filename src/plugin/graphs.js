// The pictures on a device: an EQ's curve, a filter's slope, a delay's echoes, an
// envelope's shape. Each one is drawn from the dials under it and can be dragged, which
// turns those dials. The dials stay: they are the parameters, and the exact numbers.
//
// This one file is used in three places, as it stands:
//   the page            (src/plugin/graph-view.ts draws it on a canvas)
//   the plugin's window (native/ui/face.html, which has it pasted in when it is built)
//   the Max for Live device (fxgen.py puts it inside a v8ui object, with mgraphics)
// So it is plain script with no imports, it does not touch the DOM, and it draws through
// three calls its host provides:
//   g.path(points, stroke, width, fill)   points is [x0, y0, x1, y1, ...]; stroke and fill
//                                         are [r, g, b, a] from 0 to 1, or null
//   g.dot(x, y, radius, fill, stroke)
//   g.line(x0, y0, x1, y1, colour, width)
//
// A kind of picture is three functions:
//   draw(g, v, w, h, c, m, live)
//                              v: the dials' values and the module's options, by id
//                              c: the device's colours {ink, track, accent, card}
//                              m: each dial's {min, max, exponent}
//                              live: what is sounding now, where the host can tell, or
//                              nothing. All of it is optional:
//                                spectrum  levels 0 to 1, spread evenly across the
//                                          picture's frequency axis (20 Hz to 20 kHz)
//                                time      seconds the sound has been running
//                                note      {on: seconds since the last note began,
//                                           off: seconds since it was let go, or null}
//   handles(v, w, h, m)        the dots that can be dragged: [{id, x, y}]
//   drag(id, x, y, v, w, h, m) the new values for a dot dragged to x, y: {dial: value}

var PPGraphs = (function () {
  var PI = Math.PI;
  function clamp(x, lo, hi) { return x < lo ? lo : x > hi ? hi : x; }
  function a(colour, alpha) { return [colour[0], colour[1], colour[2], alpha]; }

  // Where a dial stands, 0 to 1, the way a live.dial counts it, and back.
  function pos(p, value) {
    if (!p || p.max === p.min) return 0;
    return Math.pow(clamp((value - p.min) / (p.max - p.min), 0, 1), 1 / (p.exponent || 1));
  }
  function val(p, position) {
    return p.min + (p.max - p.min) * Math.pow(clamp(position, 0, 1), p.exponent || 1);
  }

  // Frequency across the picture: 20 Hz at the left edge, 20 kHz at the right, in octaves.
  var F_LO = 20, F_HI = 20000;
  function fx(freq, w) { return w * Math.log(freq / F_LO) / Math.log(F_HI / F_LO); }
  function xf(x, w) { return F_LO * Math.pow(F_HI / F_LO, clamp(x / w, 0, 1)); }
  // Level up the picture, in dB, `span` above and below the middle.
  function dy(db, h, span) { return h / 2 - (db / span) * (h / 2 - 3); }
  function yd(y, h, span) { return (h / 2 - y) / (h / 2 - 3) * span; }

  // Complex numbers as [re, im], for working out what a filter does to each frequency.
  function cdiv(n, d) { var k = d[0] * d[0] + d[1] * d[1]; return [(n[0] * d[0] + n[1] * d[1]) / k, (n[1] * d[0] - n[0] * d[1]) / k]; }
  function mag(z) { return Math.sqrt(z[0] * z[0] + z[1] * z[1]); }
  function db(x) { return 20 * Math.log(Math.max(x, 1e-6)) / Math.LN10; }
  function gain(dB) { return Math.pow(10, dB / 20); }

  /** Points of a curve y(x) sampled across the width. */
  function curve(w, fn) {
    var pts = [], n = Math.max(24, Math.round(w / 2));
    for (var i = 0; i <= n; i++) { var x = w * i / n; pts.push(x, fn(x)); }
    return pts;
  }
  /** The same points closed down to a level line, to fill under a curve. */
  function under(pts, w, y) { return pts.concat([w, y, 0, y]); }

  function grid(g, w, h, c, lines) {
    for (var i = 0; i < lines.length; i++) g.line(lines[i], 0, lines[i], h, a(c.ink, 0.07), 1);
    g.line(0, h / 2, w, h / 2, a(c.ink, 0.12), 1);
  }
  function freqGrid(g, w, h, c) { grid(g, w, h, c, [fx(100, w), fx(1000, w), fx(10000, w)]); }

  /** What is sounding, behind a frequency picture: the spectrum, filled up from the floor. */
  function spectrum(g, w, h, c, live) {
    var s = live && live.spectrum;
    if (!s || s.length < 2) return;
    var pts = [];
    for (var i = 0; i < s.length; i++) pts.push(w * i / (s.length - 1), h - clamp(s[i], 0, 1) * (h - 4));
    g.path(under(pts, w, h), null, 0, a(c.ink, 0.13));
  }
  /** A small solid dot: where the sound is on a picture, as against a ringed dot, which can be dragged. */
  function now(g, x, y, c) { g.dot(x, y, 2.2, c.accent, c.accent); }

  // ── EQ: one curve, three dots ───────────────────────────────────────────────────
  // What fxgen.py's m_eq builds, as the level it gives each frequency: two shelves whose
  // corners move with their gain, and a band-pass around the Freq dial.
  var EQ_SPAN = 18, EQ_Q = 0.992, EQ_LOW = 250, EQ_HIGH = 2500, EQ_FIT = 1.37, EQ_BEND = 0.13;
  var EQ_LOW_X = 60, EQ_HIGH_X = 9000;   // where the two shelf dots sit
  function eqLevel(f, v) {
    var gl = gain(v.low), gm = gain(v.mid), gh = gain(v.high);
    var rl = f / (EQ_LOW / Math.sqrt(gl)), rh = f / (EQ_HIGH * Math.sqrt(gh)), rm = f / v.freq;
    var lp = cdiv([1, 0], [1, rl]);
    var hp = cdiv([0, rh], [1, rh]);
    var bp = cdiv([0, EQ_Q * rm], [1 - rm * rm, EQ_Q * rm]);
    var kh = EQ_FIT * Math.pow(gh, EQ_BEND) * (gh - 1);
    return db(mag([1 + (gl - 1) * lp[0] + (gm - 1) * bp[0] + kh * hp[0], (gl - 1) * lp[1] + (gm - 1) * bp[1] + kh * hp[1]]));
  }
  var eq = {
    draw: function (g, v, w, h, c, m, live) {
      spectrum(g, w, h, c, live);
      freqGrid(g, w, h, c);
      var pts = curve(w, function (x) { return clamp(dy(eqLevel(xf(x, w), v), h, EQ_SPAN), 1, h - 1); });
      g.path(under(pts, w, h / 2), null, 0, a(c.accent, 0.2));
      g.path(pts, c.accent, 1.6, null);
    },
    handles: function (v, w, h) {
      return [
        { id: 'low', x: fx(EQ_LOW_X, w), y: dy(v.low, h, EQ_SPAN) },
        { id: 'mid', x: fx(v.freq, w), y: dy(v.mid, h, EQ_SPAN) },
        { id: 'high', x: fx(EQ_HIGH_X, w), y: dy(v.high, h, EQ_SPAN) },
      ];
    },
    drag: function (id, x, y, v, w, h, m) {
      var out = {};
      out[id] = clamp(yd(y, h, EQ_SPAN), m[id].min, m[id].max);
      if (id === 'mid') out.freq = clamp(xf(x, w), m.freq.min, m.freq.max);
      return out;
    },
  };

  // ── the other EQs: parametric, graphic, tilt ────────────────────────────────────
  // These are drawn from the filters as they run, sample by sample, and not from the
  // textbook curves, because near the top of the range the two part company: a onepole~
  // shelf stops short and an svf~ bell sits a little under its dial. So the picture is
  // what is heard. It is worked out for 44.1 kHz; at 48 the top octave differs by a hair.
  var SR = 44100, QUARTER = SR / 4;
  function cmul(p, q) { return [p[0] * q[0] - p[1] * q[1], p[0] * q[1] + p[1] * q[0]]; }
  function zinv(f) { var t = 2 * PI * f / SR; return [Math.cos(t), -Math.sin(t)]; }
  /** onepole~ with its corner at c, as it answers frequency f. */
  function pole(f, c) {
    var k = 1 - Math.exp(-2 * PI * clamp(c, 0, QUARTER) / SR), z = zinv(f);
    return cdiv([k, 0], [1 - (1 - k) * z[0], -(1 - k) * z[1]]);
  }
  /** svf~'s band-pass at c with this Q, a sample late (fxgen's bell), scaled to peak at 1. */
  function bellAt(f, c, q) {
    var k = Math.sin(2 * PI * clamp(c, 0, QUARTER) / SR), d = Math.min(1, 1 / q), z = zinv(f);
    var one = [1 - z[0], -z[1]];
    var den = cmul(one, one), kd = cmul(z, one);
    den = [den[0] + k * k * z[0] + k * d * kd[0], den[1] + k * k * z[1] + k * d * kd[1]];
    var b = cmul(cdiv([k * one[0], k * one[1]], den), z);
    return [b[0] / q, b[1] / q];
  }
  function add(sum, part, amount) { sum[0] += part[0] * amount; sum[1] += part[1] * amount; }
  function highShelf(sum, f, c, g) {
    var p = pole(f, c * Math.sqrt(g));
    add(sum, [1 - p[0], -p[1]], EQ_FIT * Math.pow(g, EQ_BEND) * (g - 1));
  }
  function eqKind(level, span, dots, dragged) {
    return {
      draw: function (g, v, w, h, c, m, live) {
        spectrum(g, w, h, c, live);
        freqGrid(g, w, h, c);
        var pts = curve(w, function (x) { return clamp(dy(level(xf(x, w), v), h, span), 1, h - 1); });
        g.path(under(pts, w, h / 2), null, 0, a(c.accent, 0.2));
        g.path(pts, c.accent, 1.6, null);
      },
      handles: function (v, w, h) {
        var out = dots(v);
        for (var i = 0; i < out.length; i++) out[i] = { id: out[i][0], x: fx(out[i][1], w), y: dy(out[i][2], h, span) };
        return out;
      },
      drag: function (id, x, y, v, w, h) { return dragged(id, xf(x, w), yd(y, h, span)); },
    };
  }

  function peqLevel(f, v) {
    var sum = [1, 0], gl = gain(v.lg), gh = gain(v.hg);
    add(sum, pole(f, v.lf / Math.sqrt(gl)), gl - 1);
    highShelf(sum, f, v.hf, gh);
    add(sum, bellAt(f, v.f1, v.q1), gain(v.g1) - 1);
    add(sum, bellAt(f, v.f2, v.q2), gain(v.g2) - 1);
    return db(mag(sum));
  }
  var PEQ_FREQ = { lg: 'lf', g1: 'f1', g2: 'f2', hg: 'hf' };
  var peq = eqKind(peqLevel, EQ_SPAN,
    function (v) { return [['lg', v.lf, v.lg], ['g1', v.f1, v.g1], ['g2', v.f2, v.g2], ['hg', v.hf, v.hg]]; },
    function (id, freq, level) { var out = {}; out[id] = level; out[PEQ_FREQ[id]] = freq; return out; });

  var GEQ_HZ = [100, 230, 520, 1200, 2700], GEQ_Q = 1.6, GEQ_SHELF = 3500, GEQ_TOP_X = 6000, GEQ_SPAN = 15;
  function geqLevel(f, v) {
    var sum = [1, 0];
    for (var i = 0; i < GEQ_HZ.length; i++) add(sum, bellAt(f, GEQ_HZ[i], GEQ_Q), gain(v['b' + (i + 1)]) - 1);
    highShelf(sum, f, GEQ_SHELF, gain(v.b6));
    return db(mag(sum));
  }
  var geq = eqKind(geqLevel, GEQ_SPAN,
    function (v) {
      var out = [];
      for (var i = 0; i < 6; i++) out.push(['b' + (i + 1), i < 5 ? GEQ_HZ[i] : GEQ_TOP_X, v['b' + (i + 1)]]);
      return out;
    },
    function (id, freq, level) { var out = {}; out[id] = level; return out; });

  var TILT_SPAN = 9;
  function tiltLevel(f, v) {
    var up = gain(v.tilt), down = gain(-v.tilt), p = pole(f, v.pivot * up);
    return db(mag([up + (down - up) * p[0], (down - up) * p[1]]));
  }
  // The dot is on the pivot (where the level stays as it was), as high as the highs are raised.
  var tilt = eqKind(tiltLevel, TILT_SPAN,
    function (v) { return [['tilt', v.pivot, v.tilt]]; },
    function (id, freq, level) { return { tilt: level, pivot: freq }; });

  // ── compressor: level in against level out ──────────────────────────────────────
  // The straight line is the sound left alone; past the threshold it bends down to one dB
  // out for every Ratio dB in, and Makeup lifts all of it. Two dots: the bend, which slides
  // along the line to set the threshold, and the end at full level, which sets the ratio.
  // With Mix under 100 the blend with the sound as it was is drawn as well, thinner.
  var COMP_LO = -60, COMP_HI = 12;
  function cx(db, w) { return 4 + (w - 8) * (db - COMP_LO) / -COMP_LO; }
  function xc(x, w) { return COMP_LO - COMP_LO * (x - 4) / (w - 8); }
  function cy(db, h) { return h - 4 - (h - 8) * clamp((db - COMP_LO) / (COMP_HI - COMP_LO), 0, 1); }
  function yc(y, h) { return COMP_LO + (COMP_HI - COMP_LO) * (h - 4 - y) / (h - 8); }
  function compOut(level, v) { return (level > v.thresh ? v.thresh + (level - v.thresh) / v.ratio : level) + v.makeup; }
  var comp = {
    draw: function (g, v, w, h, c) {
      g.line(cx(COMP_LO, w), cy(COMP_LO, h), cx(0, w), cy(0, h), a(c.ink, 0.15), 1);
      g.line(cx(v.thresh, w), 0, cx(v.thresh, w), h, a(c.ink, 0.1), 1);
      var mix = (v.mix === undefined ? 100 : v.mix) / 100;
      if (mix < 0.999) {
        g.path(curve(w, function (x) {
          var level = xc(x, w), wet = gain(compOut(level, v)), dry = gain(level);
          return cy(db((1 - mix) * dry + mix * wet), h);
        }), a(c.accent, 0.5), 1, null);
      }
      var pts = curve(w, function (x) { return cy(compOut(xc(x, w), v), h); });
      g.path(under(pts, w, h), null, 0, a(c.accent, 0.18));
      g.path(pts, c.accent, 1.6, null);
    },
    handles: function (v, w, h) {
      return [
        { id: 'thresh', x: cx(v.thresh, w), y: cy(v.thresh + v.makeup, h) },
        { id: 'ratio', x: cx(0, w), y: cy(compOut(0, v), h) },
      ];
    },
    drag: function (id, x, y, v, w, h) {
      if (id === 'thresh') return { thresh: xc(x, w) };
      // where the end is, back to the ratio that puts it there: thresh + -thresh / ratio
      var rise = yc(y, h) - v.makeup - v.thresh;
      return { ratio: rise <= 0.05 ? 100 : -v.thresh / rise };
    },
  };

  // ── filter: the slope, and one dot for cutoff and resonance ─────────────────────
  // svf~ as fxgen drives it: resonance 0.17 to 0.75, and the trim that keeps a loud
  // resonance from clipping.
  var FILTER_SPAN = 24;
  function svfLevel(f, cutoff, res, mode) {
    var r = f / cutoff, rr = 0.17 + 0.58 * res / 100, d = 1.2 * (1 - rr), q = d * d;
    var den = [1 - r * r, q * r];
    var num = mode === 'highpass' ? [-r * r, 0] : mode === 'bandpass' ? [0, r] : mode === 'notch' ? [1 - r * r, 0] : [1, 0];
    return db(mag(cdiv(num, den)) * (1 - res / 200));
  }
  function filterKind(extra) {
    return {
      draw: function (g, v, w, h, c, m, live) {
        spectrum(g, w, h, c, live);
        freqGrid(g, w, h, c);
        var mode = v.mode || 'lowpass';
        if (extra && v.env > 0) {
          // How far a note's envelope opens it (fxgen: 80 Hz for each percent of Env).
          var top = Math.min(F_HI, v.cutoff + v.env * 80);
          g.path(curve(w, function (x) { return clamp(dy(svfLevel(xf(x, w), top, v.res, mode), h, FILTER_SPAN), 1, h + 4); }), a(c.accent, 0.4), 1, null);
        }
        var pts = curve(w, function (x) { return clamp(dy(svfLevel(xf(x, w), v.cutoff, v.res, mode), h, FILTER_SPAN), 1, h + 4); });
        g.path(under(pts, w, h + 4), null, 0, a(c.accent, 0.2));
        g.path(pts, c.accent, 1.6, null);
      },
      handles: function (v, w, h, m) {
        return [{ id: 'cutoff', x: fx(v.cutoff, w), y: 6 + (1 - pos(m.res, v.res)) * (h - 12) }];
      },
      drag: function (id, x, y, v, w, h, m) {
        return { cutoff: clamp(xf(x, w), m.cutoff.min, m.cutoff.max), res: val(m.res, 1 - (y - 6) / (h - 12)) };
      },
    };
  }

  // ── distortion: what goes in against what comes out ─────────────────────────────
  function shape(kind, x, amount) {
    var y = x * amount;
    if (kind === 'hard') return clamp(y, -1, 1);
    if (kind === 'fold') { y = ((y + 1) % 4 + 4) % 4; return y < 2 ? y - 1 : 3 - y; }
    if (kind === 'fuzz') return clamp(y * 4 + 0.2, -0.35, 1);
    if (kind === 'soft') return y / (1 + Math.abs(y)) * 1.5 > 1 ? 1 : Math.max(-1, y / (1 + Math.abs(y)) * 1.5);
    var e = Math.exp(2 * clamp(y, -20, 20)); return (e - 1) / (e + 1);   // tube: tanh
  }
  var drive = {
    draw: function (g, v, w, h, c) {
      grid(g, w, h, c, [w / 2]);
      g.line(0, h - 2, w, 2, a(c.ink, 0.18), 1);   // what comes out with no distortion at all
      var kind = v.character || 'soft';
      g.path(curve(w, function (x) { return h / 2 - shape(kind, x / w * 2 - 1, v.amount) * (h / 2 - 3); }), c.accent, 1.6, null);
    },
    handles: function (v, w, h, m) {
      return [{ id: 'amount', x: w * 0.5 + pos(m.amount, v.amount) * w * 0.42, y: 6 + (1 - pos(m.amount, v.amount)) * (h - 12) }];
    },
    drag: function (id, x, y, v, w, h, m) { return { amount: val(m.amount, 1 - (y - 6) / (h - 12)) }; },
  };

  // ── crush: a wave cut into steps ────────────────────────────────────────────────
  var crush = {
    draw: function (g, v, w, h, c) {
      grid(g, w, h, c, []);
      var across = 96, hold = Math.max(1, Math.round(100 / v.rate)), levels = Math.pow(2, Math.min(v.bits, 12)) / 2;
      var pts = [], held = 0;
      for (var i = 0; i <= across; i++) {
        if (i % hold === 0) held = Math.floor(Math.sin(i / across * 4 * PI) * levels) / levels;
        var y = h / 2 - clamp(held, -1, 1) * (h / 2 - 4);
        pts.push(w * i / across, y, w * Math.min(across, i + 1) / across, y);
      }
      g.path(curve(w, function (x) { return h / 2 - Math.sin(x / w * 4 * PI) * (h / 2 - 4); }), a(c.ink, 0.2), 1, null);
      g.path(pts, c.accent, 1.6, null);
    },
    handles: function (v, w, h, m) {
      return [{ id: 'rate', x: 6 + pos(m.rate, v.rate) * (w - 12), y: 6 + (1 - pos(m.bits, v.bits)) * (h - 12) }];
    },
    drag: function (id, x, y, v, w, h, m) {
      return { rate: val(m.rate, (x - 6) / (w - 12)), bits: Math.round(val(m.bits, 1 - (y - 6) / (h - 12))) };
    },
  };

  // ── chorus and tremolo: the wave that does the moving ───────────────────────────
  // Across is how fast, up is how far.
  function cycles(m, v) { return 1 + pos(m.rate, v.rate) * 7; }
  function waveKind(isTremolo) {
    return {
      draw: function (g, v, w, h, c, m, live) {
        grid(g, w, h, c, []);
        var n = cycles(m, v), depth = pos(m.depth, v.depth), mid = h / 2, reach = h / 2 - 5;
        // Where the sweep is: a dot going along the wave at the wave's real speed.
        var at = live && typeof live.time === 'number' ? ((live.time * v.rate) % n) / n * w : null;
        if (isTremolo) {
          // The loudness itself: full at the top, dipping by the depth. Auto-pan is the
          // same dip on each side in turn.
          var level = function (phase) { return function (x) { return 4 + (h - 8) * depth * (0.5 - 0.5 * Math.cos(x / w * n * 2 * PI + phase)); }; };
          var left = curve(w, level(0));
          g.path(under(left, w, h), null, 0, a(c.accent, 0.2));
          g.path(left, c.accent, 1.6, null);
          if (v.mode === 'autopan') g.path(curve(w, level(PI)), a(c.ink, 0.55), 1.2, null);
          if (at !== null) now(g, at, level(0)(at), c);
        } else {
          // Two voices a quarter turn apart, as the chorus's two sides are.
          g.path(curve(w, function (x) { return mid - Math.cos(x / w * n * 2 * PI) * reach * depth; }), a(c.ink, 0.35), 1.2, null);
          g.path(curve(w, function (x) { return mid - Math.sin(x / w * n * 2 * PI) * reach * depth; }), c.accent, 1.6, null);
          if (at !== null) now(g, at, mid - Math.sin(at / w * n * 2 * PI) * reach * depth, c);
        }
      },
      handles: function (v, w, h, m) {
        return [{ id: 'rate', x: 6 + pos(m.rate, v.rate) * (w - 12), y: 6 + (1 - pos(m.depth, v.depth)) * (h - 12) }];
      },
      drag: function (id, x, y, v, w, h, m) {
        return { rate: val(m.rate, (x - 6) / (w - 12)), depth: val(m.depth, 1 - (y - 6) / (h - 12)) };
      },
    };
  }

  // ── delay: the sound, then each echo ────────────────────────────────────────────
  // Time runs to three seconds across the picture, stretched at the short end. The dot
  // is the top of the second echo: how far along is the time, how tall is the feedback.
  var DELAY_MS = 3000;
  function tx(ms, w) { return 5 + (w - 10) * Math.sqrt(clamp(ms / DELAY_MS, 0, 1)); }
  function xt(x, w) { var k = clamp((x - 5) / (w - 10), 0, 1); return k * k * DELAY_MS; }
  var delay = {
    draw: function (g, v, w, h, c) {
      var ping = v.style === 'pingpong', base = ping ? h / 2 : h - 3, reach = ping ? h / 2 - 4 : h - 8;
      g.line(0, base, w, base, a(c.ink, 0.12), 1);
      var bar = function (ms, level, up, colour) {
        var x = tx(ms, w), y = up ? base - level * reach : base + level * reach;
        g.line(x, base, x, y, colour, 3);
      };
      bar(0, 1, true, a(c.ink, 0.55));
      if (ping) bar(0, 1, false, a(c.ink, 0.55));
      var wet = 0.35 + 0.65 * (v.mix / 100), fb = v.feedback / 100;
      for (var n = 1; n <= 24; n++) {
        var ms = v.time * n, level = Math.pow(fb, n - 1);
        if (ms > DELAY_MS || level < 0.02) break;
        // A ping-pong echo lands on one side, then the other; a plain one on both at once.
        bar(ms, level, !ping || n % 2 === 1, a(c.accent, wet));
      }
    },
    handles: function (v, w, h) {
      var ping = v.style === 'pingpong', base = ping ? h / 2 : h - 3, reach = ping ? h / 2 - 4 : h - 8;
      var level = v.feedback / 100;
      return [{ id: 'time', x: tx(v.time * 2, w), y: ping ? base + level * reach : base - level * reach }];
    },
    drag: function (id, x, y, v, w, h, m) {
      var ping = v.style === 'pingpong', base = ping ? h / 2 : h - 3, reach = ping ? h / 2 - 4 : h - 8;
      var level = ping ? (y - base) / reach : (base - y) / reach;
      return { time: clamp(xt(x, w) / 2, m.time.min, m.time.max), feedback: clamp(level * 100, m.feedback.min, m.feedback.max) };
    },
  };

  // ── reverb: the tail ────────────────────────────────────────────────────────────
  // How long it rings (Size) is how far the tail reaches; Damp is how soon its top end
  // goes, drawn as the brighter part inside it.
  var reverb = {
    draw: function (g, v, w, h, c, m) {
      var size = pos(m.size, v.size), bright = pos(m.damp, v.damp), tall = (h - 8) * (0.45 + 0.55 * v.mix / 100);
      var reach = v.space === 'room' ? 0.4 : v.space === 'plate' ? 0.75 : 1;   // how long each space can ring
      var tau = w * (0.05 + 0.45 * size * size) * reach;
      var tail = function (t) { return function (x) { return h - 3 - tall * Math.exp(-x / t); }; };
      g.line(0, h - 3, w, h - 3, a(c.ink, 0.12), 1);
      var whole = curve(w, tail(tau));
      g.path(under(whole, w, h - 3), null, 0, a(c.accent, 0.18));
      g.path(under(curve(w, tail(tau * (0.2 + 0.8 * bright))), w, h - 3), null, 0, a(c.accent, 0.3));
      g.path(whole, c.accent, 1.6, null);
    },
    handles: function (v, w, h, m) {
      return [{ id: 'size', x: 6 + pos(m.size, v.size) * (w - 12), y: 6 + (1 - pos(m.damp, v.damp)) * (h - 12) }];
    },
    drag: function (id, x, y, v, w, h, m) {
      return { size: val(m.size, (x - 6) / (w - 12)), damp: val(m.damp, 1 - (y - 6) / (h - 12)) };
    },
  };

  // ── oscillators: the wave the two of them make together ─────────────────────────
  function wave(kind, phase) {
    var p = phase - Math.floor(phase);
    if (kind === 'saw') return 1 - 2 * p;
    if (kind === 'square') return p < 0.5 ? 1 : -1;
    if (kind === 'triangle') return p < 0.5 ? 4 * p - 1 : 3 - 4 * p;
    return Math.sin(p * 2 * PI);
  }
  var osc = {
    draw: function (g, v, w, h, c) {
      grid(g, w, h, c, [w / 2]);
      var kind = v.wave || 'saw', blend = v.blend / 100;
      var ratio = v.second === 'up' ? 2 : v.second === 'down' ? 0.5 : 1;
      ratio *= 1 + v.detune / 50 * 0.06;   // enough of the detune to see the two drift apart
      var n = Math.max(48, Math.round(w)), pts = [];
      for (var i = 0; i <= n; i++) {
        var t = i / n * 2;
        var y = (1 - blend / 2) * wave(kind, t) + (blend / 2) * wave(kind, t * ratio);
        pts.push(w * i / n, h / 2 - y * (h / 2 - 5));
      }
      g.path(pts, c.accent, 1.6, null);
    },
    handles: function (v, w, h, m) {
      return [{ id: 'blend', x: 6 + pos(m.blend, v.blend) * (w - 12), y: 6 + (1 - pos(m.detune, v.detune)) * (h - 12) }];
    },
    drag: function (id, x, y, v, w, h, m) {
      return { blend: val(m.blend, (x - 6) / (w - 12)), detune: val(m.detune, 1 - (y - 6) / (h - 12)) };
    },
  };

  // ── envelope: attack, decay, sustain, release ───────────────────────────────────
  // Each stage is as wide as its dial is far round, so a short attack is still a slope
  // you can get hold of. The level it holds at is drawn as a flat stretch of fixed width.
  function envPoints(v, w, h, m) {
    var stage = (w - 12) * 0.27, hold = (w - 12) * 0.19, top = 5, floor = h - 4;
    var xa = 6 + pos(m.attack, v.attack) * stage;
    var xd = xa + pos(m.decay, v.decay) * stage;
    var ys = floor - (floor - top) * v.sustain / 100;
    var xs = xd + hold;
    var xr = xs + pos(m.release, v.release) * stage;
    return { x0: 6, xa: xa, xd: xd, xs: xs, xr: xr, top: top, floor: floor, ys: ys, stage: stage };
  }
  /**
   * Where the latest note is on the shape, in real time: up the attack, down the decay,
   * waiting on the level it holds at, then down the release from wherever it was let go.
   * Nothing once the release is over.
   */
  function envNow(v, e, note) {
    var ms = note.on * 1000, held = note.off === null || note.off === undefined ? ms : ms - note.off * 1000;
    var at = function (t) {   // the shape at t ms into a held note: [x, y]
      if (t < v.attack) { var k = t / v.attack; return [e.x0 + (e.xa - e.x0) * k, e.floor + (e.top - e.floor) * k]; }
      if (t < v.attack + v.decay) { var d = (t - v.attack) / v.decay; return [e.xa + (e.xd - e.xa) * d, e.top + (e.ys - e.top) * d]; }
      return [(e.xd + e.xs) / 2, e.ys];
    };
    if (held >= ms) return at(ms);
    var from = at(Math.max(0, held)), gone = (ms - held) / v.release;
    if (gone >= 1) return null;
    return [e.xs + (e.xr - e.xs) * gone, from[1] + (e.floor - from[1]) * gone];
  }
  var env = {
    draw: function (g, v, w, h, c, m, live) {
      var e = envPoints(v, w, h, m);
      g.line(0, e.floor, w, e.floor, a(c.ink, 0.12), 1);
      var pts = [e.x0, e.floor, e.xa, e.top, e.xd, e.ys, e.xs, e.ys, e.xr, e.floor];
      g.path(pts, null, 0, a(c.accent, 0.2));
      g.path(pts, c.accent, 1.6, null);
      var p = live && live.note ? envNow(v, e, live.note) : null;
      if (p) now(g, p[0], p[1], c);
    },
    handles: function (v, w, h, m) {
      var e = envPoints(v, w, h, m);
      return [{ id: 'attack', x: e.xa, y: e.top }, { id: 'decay', x: e.xd, y: e.ys }, { id: 'release', x: e.xr, y: e.floor }];
    },
    drag: function (id, x, y, v, w, h, m) {
      var e = envPoints(v, w, h, m);
      if (id === 'attack') return { attack: val(m.attack, (x - e.x0) / e.stage) };
      if (id === 'release') return { release: val(m.release, (x - e.xs) / e.stage) };
      return { decay: val(m.decay, (x - e.xa) / e.stage), sustain: clamp((e.floor - y) / (e.floor - e.top) * 100, 0, 100) };
    },
  };

  var kinds = {
    eq: eq, peq: peq, geq: geq, tilt: tilt, comp: comp, filter: filterKind(false), tone: filterKind(true), drive: drive, crush: crush,
    chorus: waveKind(false), tremolo: waveKind(true), delay: delay, reverb: reverb, osc: osc, env: env,
  };

  /** Everything a host draws for one picture: the kind's own drawing, then its dots. */
  function render(kind, g, v, w, h, c, m, hot, live) {
    var k = kinds[kind];
    if (!k) return;
    k.draw(g, v, w, h, c, m, live || null);
    var dots = k.handles(v, w, h, m);
    for (var i = 0; i < dots.length; i++) {
      var d = dots[i], on = hot === d.id;
      g.dot(clamp(d.x, 4, w - 4), clamp(d.y, 4, h - 4), on ? 5 : 4, c.card, c.accent);
    }
  }

  /** The dot a press at x, y takes hold of: the nearest one. A picture with one dot is all handle. */
  function pick(kind, x, y, v, w, h, m) {
    var dots = kinds[kind].handles(v, w, h, m), best = null, far = Infinity;
    for (var i = 0; i < dots.length; i++) {
      var d = (dots[i].x - x) * (dots[i].x - x) + (dots[i].y - y) * (dots[i].y - y);
      if (d < far) { far = d; best = dots[i].id; }
    }
    return best;
  }

  function drag(kind, id, x, y, v, w, h, m) {
    var next = kinds[kind].drag(id, x, y, v, w, h, m), out = {};
    for (var key in next) if (m[key]) out[key] = clamp(next[key], m[key].min, m[key].max);
    return out;
  }

  // Which kinds have something to show while sound is running, and what they need for it.
  var moving = { eq: 'spectrum', peq: 'spectrum', geq: 'spectrum', tilt: 'spectrum', filter: 'spectrum', tone: 'spectrum', chorus: 'time', tremolo: 'time', env: 'note' };

  return { kinds: kinds, render: render, pick: pick, drag: drag, has: function (kind) { return !!kinds[kind]; },
           moves: function (kind) { return moving[kind] || null; } };
})();
