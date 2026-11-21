// mxrt: the Max objects the plugin generator uses, as plain C++.
//
// One small struct or function per object. patchc (src/plugin/patchc.py) compiles a
// generated patch into an Effect.h that calls these; nothing here knows about a plugin
// format. The maths is the browser engine's (src/runtime/worklet.ts and
// src/objects/audio/), which scripts/maxcheck/ checked against Max 9: where Max does
// something a textbook version would not, the comment on the kernel says what.
//
// Rules the kernels keep:
//   - prepare() is the only place that allocates. tick() and the setters never do, so
//     both are safe on the audio thread.
//   - Signals are double, as they are in Max. degrade~'s phase is single precision
//     because Max's is, and its hold lengths depend on it.
//   - Expressions that turn milliseconds into samples are written in the same order as
//     the engine's, so both round a time such as 10 ms to the same whole sample.
//
// No dependencies beyond the standard library. MIT licensed, like the rest of the repo.

#pragma once

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <vector>

#if defined(__SSE2__) || defined(_M_X64) || (defined(_M_IX86_FP) && _M_IX86_FP >= 2)
#include <immintrin.h>
#define MXRT_SSE 1
#endif

namespace mxrt {

constexpr double kPi = 3.14159265358979323846;

// ── parameters ──────────────────────────────────────────────────────────────────

// Live's parameter_unitstyle: how a dial's number is printed.
enum Unit { kInt = 0, kFloat = 1, kMs = 2, kHertz = 3, kDb = 4, kPercent = 5 };

// One live.dial, as the patch saved it.
struct ParamInfo {
    uint32_t id;            // stable across builds: a hash of the dial's varname
    const char* name;       // "Filter Cutoff"
    const char* shortName;  // "Cutoff"
    double min, max, initial;
    double exponent;        // value = min + (max - min) * position ^ exponent
    int unit;               // a Unit
    bool integer;
};

inline double clampValue(const ParamInfo& p, double v) {
    const double lo = std::min(p.min, p.max), hi = std::max(p.min, p.max);
    if (!(v == v)) v = p.initial;  // NaN
    v = std::min(hi, std::max(lo, v));
    return p.integer ? std::round(v) : v;
}

// Knob position (0..1) to value, and back. Live's own curve.
inline double positionToValue(const ParamInfo& p, double position) {
    const double t = std::min(1.0, std::max(0.0, position));
    const double v = p.min + (p.max - p.min) * std::pow(t, p.exponent);
    return p.integer ? std::round(v) : v;
}

inline double valueToPosition(const ParamInfo& p, double value) {
    if (p.max == p.min) return 0.0;
    const double t = std::min(1.0, std::max(0.0, (value - p.min) / (p.max - p.min)));
    return std::pow(t, 1.0 / p.exponent);
}

namespace detail {
// "%.*f" with trailing zeros removed: 1.50 -> 1.5, 2.00 -> 2.
inline int trimmed(char* out, size_t size, double v, int digits) {
    int n = std::snprintf(out, size, "%.*f", digits, v);
    if (n < 0 || static_cast<size_t>(n) >= size) return n;
    bool dot = false;
    for (int i = 0; i < n; i++) dot = dot || out[i] == '.';
    if (dot) {
        while (n > 0 && out[n - 1] == '0') out[--n] = 0;
        if (n > 0 && out[n - 1] == '.') out[--n] = 0;
    }
    return n;
}
}  // namespace detail

// A value as Ableton prints it under the dial ("1.8 kHz", "-3 dB", "35 %").
inline void formatValue(const ParamInfo& p, double value, char* out, size_t size) {
    if (size == 0) return;
    char num[48];
    // The number with at most `digits` decimals, then the unit.
    const auto print = [&](double v, int digits, const char* unit) {
        detail::trimmed(num, sizeof num, v, digits);
        std::snprintf(out, size, "%s%s", num, unit);
    };
    switch (p.unit) {
        case kInt:
            std::snprintf(out, size, "%ld", std::lround(value));
            return;
        case kMs:
            if (value >= 1000) print(value / 1000, 2, " s");
            else print(value, value < 10 ? 1 : 0, " ms");
            return;
        case kHertz:
            if (value >= 1000) print(value / 1000, 2, " kHz");
            else print(value, value < 10 ? 2 : 0, " Hz");
            return;
        case kDb:
            detail::trimmed(num, sizeof num, value, 1);
            std::snprintf(out, size, "%s%s dB", value > 0 ? "+" : "", num);
            return;
        case kPercent:
            print(value, 0, " %");
            return;
        default:
            print(value, std::fabs(value) >= 100 ? 0 : 2, "");
    }
}

// The reverse: "1.8 kHz", "1800", "250ms" and "1.5 s" all read. False if there is no number.
inline bool parseValue(const ParamInfo& p, const char* text, double* value) {
    char* end = nullptr;
    double v = std::strtod(text, &end);
    if (end == text) return false;
    while (*end == ' ') end++;
    if ((p.unit == kHertz && (*end == 'k' || *end == 'K')) || (p.unit == kMs && (*end == 's' || *end == 'S'))) v *= 1000;
    *value = clampValue(p, v);
    return true;
}

// ── control maths ───────────────────────────────────────────────────────────────

// What Max does to a float arriving at an int inlet (`* 2`, `pack 0 0`): drop the fraction.
inline double toInt(double v) { return std::trunc(v); }

inline double dbtoa(double db) { return std::pow(10.0, db / 20.0); }

inline double scale(double x, double inLo, double inHi, double outLo, double outHi) {
    const double span = inHi - inLo != 0 ? inHi - inLo : 1.0;
    return outLo + ((x - inLo) / span) * (outHi - outLo);
}

// ── memoryless shapers ──────────────────────────────────────────────────────────

// State that has decayed to nothing becomes exactly zero just before it would turn into a
// denormal, which the CPU handles a hundred times slower. The threshold is as low as that
// allows on purpose. degrade~ rounds down, so a tail that is still a hair below zero
// reads as one step below zero, and zeroing the tail ends that step with a small click:
// the later the better, and Max, computing in doubles, lets a tail run about this far.
inline double flush(double v) { return std::fabs(v) < 1e-300 ? 0.0 : v; }

// clip~
inline double clip(double x, double lo, double hi) { return std::max(lo, std::min(hi, x)); }

// overdrive~, read off a ramp rendered in Max 9: y = sign(x) * (1 - (1 - |x|) ^ drive),
// and flat past full scale. Not oversampled, because Max's is not.
inline double overdrive(double x, double drive) {
    const double a = std::min(1.0, std::fabs(x));
    const double s = 1.0 - std::pow(1.0 - a, std::max(0.0, drive));
    return x < 0 ? -s : s;
}

// pong~: mode 0 folds the wave back at the limits, 1 wraps it round, 2 clips.
inline double pong(double x, int mode, double lo, double hi) {
    const double range = hi - lo;
    if (!(range > 0)) return lo;
    if (mode == 0) {
        double t = std::fmod(x - lo, 2 * range);
        if (t < 0) t += 2 * range;
        return lo + (t <= range ? t : 2 * range - t);
    }
    if (mode == 1) {
        double t = std::fmod(x - lo, range);
        if (t < 0) t += range;
        return lo + t;
    }
    return clip(x, lo, hi);
}

// ── filters ─────────────────────────────────────────────────────────────────────

// onepole~: y += g * (x - y), g = 1 - exp(-2*pi*fc/sr). Max takes cutoffs up to sr/4.
struct OnePole {
    double sr = 44100, g = 0, y = 0;

    void prepare(double sampleRate, double hz) { sr = sampleRate; setFreq(hz); reset(); }
    void reset() { y = 0; }
    void setFreq(double hz) {
        const double f = std::max(0.0, std::min(sr / 4, hz));
        g = 1.0 - std::exp(-2 * kPi * f / sr);
    }
    double tick(double x) {
        y = flush(y + g * (x - y));
        return y;
    }
};

// svf~: Chamberlin state-variable filter with four outputs at once. Matched to Max 9
// sample for sample, which fixed three things a textbook version gets wrong:
//   the frequency coefficient is sin(2*pi*fc/sr), not 2*sin(pi*fc/sr);
//   the damping is (1.2 * (1 - res))^2, capped at 1;
//   the cutoff stops at a quarter of the sample rate.
struct Svf {
    double sr = 44100, f = 0, q = 1;
    double low = 0, band = 0;
    double lp = 0, hp = 0, bp = 0, notch = 0;  // the four outlets, after tick()

    void prepare(double sampleRate, double hz, double res) { sr = sampleRate; setFreq(hz); setRes(res); reset(); }
    void reset() { low = band = lp = hp = bp = notch = 0; }
    void setFreq(double hz) {
        const double fc = std::max(0.0, std::min(sr / 4, hz));
        f = std::sin(2 * kPi * fc / sr);
    }
    void setRes(double res) {
        const double d = 1.2 * (1.0 - std::min(1.0, std::max(0.0, res)));
        q = std::max(0.0001, std::min(1.0, d * d));
    }
    void tick(double x) {
        low += f * band;
        const double high = x - low - q * band;
        band += f * high;
        lp = low; hp = high; bp = band; notch = high + low;
        low = flush(low);
        band = flush(band);
    }
};

// biquad~: direct form I with Max's signs, y = a0*x + a1*x1 + a2*x2 - b1*y1 - b2*y2.
struct Biquad {
    double c[5] = {1, 0, 0, 0, 0};  // a0 a1 a2 b1 b2
    double x1 = 0, x2 = 0, y1 = 0, y2 = 0;

    void prepare(double a0, double a1, double a2, double b1, double b2) {
        c[0] = a0; c[1] = a1; c[2] = a2; c[3] = b1; c[4] = b2;
        reset();
    }
    void reset() { x1 = x2 = y1 = y2 = 0; }
    void set(int i, double v) { c[i] = v; }
    double tick(double x) {
        const double y = flush(c[0] * x + c[1] * x1 + c[2] * x2 - c[3] * y1 - c[4] * y2);
        x2 = x1; x1 = x; y2 = y1; y1 = y;
        return y;
    }
};

// ── degrade~ ────────────────────────────────────────────────────────────────────

// Sample-and-hold at a fraction of the sample rate, then quantise. As measured in Max:
// it rounds down, not to nearest; it takes a sample and then advances its phase; and the
// phase is single precision, so a ratio of 0.1 holds for exactly ten samples.
struct Degrade {
    float ratio = 1, phase = 1;
    double half = 0, held = 0;

    void prepare(double r, double bits) { setRatio(r); setBits(bits); reset(); }
    void reset() { phase = 1; held = 0; }
    void setRatio(double r) { ratio = static_cast<float>(std::max(0.0001, std::min(1.0, r))); }
    void setBits(double bits) {
        const double b = std::min(32.0, std::max(1.0, bits));
        half = b >= 24 ? 0.0 : std::pow(2.0, b) / 2;  // 24 bits and up is left alone
    }
    double tick(double x) {
        if (phase >= 1) {
            phase = phase - 1.0f;
            held = half != 0 ? std::floor(x * half) / half : x;
        }
        phase = phase + ratio;
        return held;
    }
};

// ── delays ──────────────────────────────────────────────────────────────────────

// comb~: y[n] = a*x[n] + b*x[n-D] + c*y[n-D], D in milliseconds. As in Max, a delay set
// by a number is cut down to whole samples, and a delay driven by a signal is read
// between samples (linear), which is what lets a flanger sweep it without zipper noise.
struct Comb {
    double sr = 44100, delayMs = 0, a = 0, b = 0, c = 0;
    int limit = 1, size = 5, w = 0;
    std::vector<double> xb, yb;

    void prepare(double sampleRate, double maxMs, double ms, double a_, double b_, double c_) {
        sr = sampleRate; delayMs = ms; a = a_; b = b_; c = c_;
        limit = std::max(1, static_cast<int>(std::floor(maxMs * sr / 1000)));
        size = limit + 4;
        xb.assign(size, 0.0);
        yb.assign(size, 0.0);
        w = 0;
    }
    void reset() {
        std::fill(xb.begin(), xb.end(), 0.0);
        std::fill(yb.begin(), yb.end(), 0.0);
        w = 0;
    }
    // The delay as a number: whole samples.
    double tick(double x) { return step(x, delayMs, false); }
    // The delay as a signal: read between samples.
    double tickSwept(double x, double ms) { return step(x, ms, true); }

private:
    double step(double x, double ms, bool swept) {
        double D = ms * sr / 1000;
        D = D < 1 ? 1 : D > limit ? limit : D;
        const int whole = static_cast<int>(std::floor(D));
        const int r0 = (w - whole + size) % size;
        double xd = xb[r0], yd = yb[r0];
        if (swept) {
            const double frac = D - whole;
            const int r1 = (r0 - 1 + size) % size;
            xd += frac * (xb[r1] - xd);
            yd += frac * (yb[r1] - yd);
        }
        const double y = a * x + b * xd + c * yd;
        xb[w] = x;
        yb[w] = flush(y);
        w = (w + 1) % size;
        return y;
    }
};

// allpass~: y = -g*x + x[n-D] + g*y[n-D]. The delay is whole samples, rounded down.
struct Allpass {
    double sr = 44100, g = 0;
    int maxDelay = 1, D = 1, w = 0;
    std::vector<double> xb, yb;

    void prepare(double sampleRate, double maxMs, double ms, double gain) {
        sr = sampleRate; g = gain;
        maxDelay = toSamples(maxMs);
        xb.assign(maxDelay, 0.0);
        yb.assign(maxDelay, 0.0);
        w = 0;
        setDelay(ms);
    }
    void reset() {
        std::fill(xb.begin(), xb.end(), 0.0);
        std::fill(yb.begin(), yb.end(), 0.0);
        w = 0;
    }
    void setDelay(double ms) { D = std::min(maxDelay, toSamples(ms)); }
    double tick(double x) {
        const int r = (w - D + maxDelay) % maxDelay;
        const double y = flush(-g * x + xb[r] + g * yb[r]);
        xb[w] = x;
        yb[w] = y;
        w = (w + 1) % maxDelay;
        return y;
    }

private:
    int toSamples(double ms) const { return std::max(1, static_cast<int>(std::floor((ms / 1000) * sr))); }
};

// tapin~ and the tapout~ objects reading it: one delay line, any number of taps, each
// read between samples (linear).
//
// A tap outside a feedback loop is read after the line has taken this sample, so its
// time can go down to zero. A tap inside a loop (tapout~ -> ... -> the same tapin~) has
// to be read before the write, because what is written depends on it; that tap's time
// cannot go below one sample. patchc works out which is which.
//
// Max reads a tap that has a signal for its time 64 samples (one signal vector) early.
// This does not: the time is used as written, as the browser engine does.
struct TapLine {
    double sr = 44100, limit = 1;
    int size = 4, w = 0;
    std::vector<double> buf;

    void prepare(double sampleRate, double maxMs) {
        sr = sampleRate;
        limit = std::max(1.0, std::floor(maxMs * sr / 1000));
        size = static_cast<int>(limit) + 4;
        buf.assign(size, 0.0);
        w = 0;
    }
    void reset() { std::fill(buf.begin(), buf.end(), 0.0); w = 0; }
    void write(double x) {
        buf[w] = x;
        w = (w + 1) % size;
    }
    // After write(): a time of 0 is the sample just written.
    double read(double ms) const { return at(ms, 0.0, 1); }
    // Before write(): the newest sample in the line is the previous one.
    double readInLoop(double ms) const { return at(ms, 1.0, 0); }

private:
    double at(double ms, double least, int newest) const {
        double D = ms * sr / 1000;
        D = D < least ? least : D > limit ? limit : D;
        const int whole = static_cast<int>(std::floor(D));
        const double frac = D - whole;
        const int r0 = ((w - newest - whole) % size + size) % size;
        const int r1 = (r0 - 1 + size) % size;
        return buf[r0] + frac * (buf[r1] - buf[r0]);
    }
};

// ── generators ──────────────────────────────────────────────────────────────────

// cycle~: a sine at a frequency in Hz. It starts at zero phase, where the browser
// engine's starts. In Max the oscillator has been running since audio was switched on,
// so where a sweep begins is arbitrary there.
struct Cycle {
    double sr = 44100, freq = 0, phase = 0;

    void prepare(double sampleRate, double hz) { sr = sampleRate; freq = hz; reset(); }
    void reset() { phase = 0; }
    void setFreq(double hz) { freq = hz; }
    double tick() { return tick(freq); }
    double tick(double hz) {
        const double y = std::sin(2 * kPi * phase);
        phase += hz / sr;
        phase -= std::floor(phase);
        return y;
    }
};

// line~: holds a value, or ramps to a new one in a straight line over a time in ms.
struct Line {
    double sr = 44100, value = 0, from = 0, target = 0;
    long total = 0, done = 0;

    void prepare(double sampleRate, double initial) { sr = sampleRate; jump(initial); }
    // What reset means for a ramp: be where it was going.
    void reset() { jump(target); }
    void jump(double v) { value = from = target = v; total = done = 0; }
    void ramp(double v, double ms) {
        const long n = std::lround(ms * sr / 1000);
        if (n <= 0) { jump(v); return; }
        from = value; target = v; total = n; done = 0;
    }
    double tick() {
        const double y = value;
        if (done < total) {
            done++;
            const double progress = static_cast<double>(done) / static_cast<double>(total);
            value = done == total ? target : from + (target - from) * progress;
        }
        return y;
    }
};

// ── denormals ───────────────────────────────────────────────────────────────────

// Reverb tails decay into numbers so small the CPU handles them a hundred times slower.
// The kernels flush their own state; this covers everything else for one process call.
struct ScopedNoDenormals {
#ifdef MXRT_SSE
    unsigned int saved;
    ScopedNoDenormals() : saved(_mm_getcsr()) { _mm_setcsr(saved | 0x8040); }  // FTZ and DAZ
    ~ScopedNoDenormals() { _mm_setcsr(saved); }
#elif defined(__aarch64__)
    uint64_t saved;
    ScopedNoDenormals() {
        __asm__ __volatile__("mrs %0, fpcr" : "=r"(saved));
        const uint64_t on = saved | (1ull << 24);  // FZ
        __asm__ __volatile__("msr fpcr, %0" : : "r"(on));
    }
    ~ScopedNoDenormals() { __asm__ __volatile__("msr fpcr, %0" : : "r"(saved)); }
#else
    ScopedNoDenormals() {}
#endif
};

}  // namespace mxrt
