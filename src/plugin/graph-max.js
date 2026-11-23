// A picture inside a Max for Live device: graphs.js drawn with mgraphics in a v8ui.
//
// fxgen.py puts three things into one v8ui object, in this order: a line that sets PP
// (which picture, the module's options, its dials and the device's colours), graphs.js,
// and this file. v8ui is Max 9, so the picture needs Live 12.2 or newer; in an older
// Live the box is empty and the dials under it work as they always did.
//
// It is a view of the dials and nothing more. Each dial sends its value in as
// "<dial> <value>", which only redraws. Dragging sends "<dial> <value>" out, which a
// route under this object hands to that dial. The dial then says its new value back,
// and that, again, only redraws: so nothing goes round in a circle.

mgraphics.init();
mgraphics.relative_coords = 0;
mgraphics.autofill = 0;
inlets = 1;
outlets = 1;

var V = {};   // the dials' values and the module's options
var M = {};   // each dial's range
var hot = null;

(function () {
  var key;
  for (key in PP.options) V[key] = PP.options[key];
  for (var i = 0; i < PP.dials.length; i++) {
    var d = PP.dials[i];
    V[d.id] = d.value;
    M[d.id] = { min: d.min, max: d.max, exponent: d.exponent };
  }
})();

function source(c) { mgraphics.set_source_rgba(c[0], c[1], c[2], c.length > 3 ? c[3] : 1); }

var G = {
  path: function (pts, stroke, width, fill) {
    if (pts.length < 4) return;
    mgraphics.move_to(pts[0], pts[1]);
    for (var i = 2; i < pts.length; i += 2) mgraphics.line_to(pts[i], pts[i + 1]);
    if (fill) {
      mgraphics.close_path();
      source(fill);
      mgraphics.fill();
    } else if (stroke) {
      source(stroke);
      mgraphics.set_line_width(width || 1);
      mgraphics.stroke();
    }
  },
  dot: function (x, y, r, fill, stroke) {
    mgraphics.ellipse(x - r, y - r, 2 * r, 2 * r);
    source(fill);
    mgraphics.fill();
    mgraphics.ellipse(x - r, y - r, 2 * r, 2 * r);
    source(stroke);
    mgraphics.set_line_width(1.6);
    mgraphics.stroke();
  },
  line: function (x0, y0, x1, y1, colour, width) {
    mgraphics.move_to(x0, y0);
    mgraphics.line_to(x1, y1);
    source(colour);
    mgraphics.set_line_width(width || 1);
    mgraphics.stroke();
  },
};

function size() { return [mgraphics.size[0], mgraphics.size[1]]; }

function paint() {
  var s = size();
  mgraphics.rectangle_rounded(0, 0, s[0], s[1], 8, 8);
  source(PP.colours.well);
  mgraphics.fill();
  PPGraphs.render(PP.kind, G, V, s[0], s[1], PP.colours, M, hot);
}

// "<dial> <value>" from a dial: remember it and redraw. Nothing is sent on.
function anything() {
  var args = arrayfromargs(arguments);
  if (M[messagename] && typeof args[0] === 'number') {
    V[messagename] = args[0];
    mgraphics.redraw();
  }
}

function move(x, y) {
  var s = size();
  var next = PPGraphs.drag(PP.kind, hot, x, y, V, s[0], s[1], M);
  for (var key in next) {
    V[key] = next[key];
    outlet(0, key, next[key]);
  }
  mgraphics.redraw();
}

function onclick(x, y) {
  var s = size();
  hot = PPGraphs.pick(PP.kind, x, y, V, s[0], s[1], M);
  if (hot) move(x, y);
}

function ondrag(x, y, button) {
  if (!hot) return;
  if (!button) { hot = null; mgraphics.redraw(); return; }
  move(x, y);
}

function onresize() { mgraphics.redraw(); }
