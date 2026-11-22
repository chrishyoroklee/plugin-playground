// mxvm: runs a device program, the other thing patchc writes besides C++.
//
// A plugin built from a generated Effect.h has to be compiled for each device. This is
// what lets one plugin, compiled once, be any device: patchc translates the statements
// it would have written as C++ into instructions (src/plugin/patchc.py, Program), and
// this runs them over the same kernels in mxrt.h. One instruction is one of those
// statements, so a device sounds the same either way, to within rounding; tests/test_runtime.py holds the
// two to each other for every preset.
//
// A device file is text, one item per line:
//
//   ppdevice 1                        the format
//   runtime 1                         the oldest runtime that can run it
//   name Warm%20Tube                  (anything but letters, digits, - and _ is %XX)
//   slug warm-tube
//   kind effect | instrument
//   tail 0 | 1
//   poly 8 1                          an instrument: voices, and whether they are stolen
//   param ID MIN MAX INITIAL EXPONENT UNIT INTEGER NAME SHORTNAME
//   registers N                       how many numbers the program keeps
//   n REGISTER VALUE                  a register that does not start at zero
//   k NUMBER TYPE                     a kernel: OnePole, Svf, Comb ...
//   @prepare, @settle, @reset, @process, @update I, @voice N, @every I    then that section's code
//   @end
//   face ...                          what the plugin's window draws: JSON in base64
//   settings ...                      for the page; not read here
//
// Instructions, with registers as numbers:
//
//   = D A                             D = A
//   + D A B   - D A B   * D A B   / D A B   neg D A
//   f NAME D A...                     D = a function of mxrt.h or <cmath>
//   pos D I A                         D = where a value A puts dial I, 0 to 1
//   every I C P                       count down in C; at zero refill it from P and run @every I
//   c K METHOD D A...                 call a kernel; D is -1 if nothing comes back
//   g D K FIELD                       read a kernel's outlet
//   s K FIELD A                       set a kernel's field
//
// The first eight registers are fixed: the sample rate, a dial's value, a note's pitch
// and velocity, the two inputs, the two outputs.

#pragma once

#include <cstring>
#include <fstream>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#include "mxrt.h"

namespace mxvm {

// The newest device a build of this file understands. patchc's PROGRAM_RUNTIME is what
// a device asks for.
constexpr int kRuntimeVersion = 2;  // 2: `every`, and the functions abs, max and floor
constexpr int kFormat = 1;

enum Register { kSampleRate, kValue, kPitch, kVelocity, kInL, kInR, kOutL, kOutR, kFixedRegisters };

enum Op : int {
    kMov, kAdd, kSub, kMul, kDiv, kNeg, kPos, kEvery,
    // functions
    kClip, kPong, kOverdrive, kScale, kToInt, kMtof, kDbtoa, kPow, kTanh, kSqrt, kSin, kCos, kAbs, kMax, kFloor,
    // kernels
    kOnePolePrepare, kOnePoleReset, kOnePoleSetFreq, kOnePoleTick,
    kSvfPrepare, kSvfReset, kSvfSetFreq, kSvfSetRes, kSvfTick, kSvfLp, kSvfHp, kSvfBp, kSvfNotch,
    kBiquadPrepare, kBiquadReset, kBiquadSet, kBiquadTick,
    kDegradePrepare, kDegradeReset, kDegradeSetRatio, kDegradeSetBits, kDegradeTick,
    kCombPrepare, kCombReset, kCombTick, kCombTickSwept, kCombDelayMs, kCombA, kCombB, kCombC,
    kAllpassPrepare, kAllpassReset, kAllpassSetDelay, kAllpassTick, kAllpassG,
    kCyclePrepare, kCycleReset, kCycleSetFreq, kCycleTick, kCycleTickAt,
    kSawPrepare, kSawReset, kSawSetFreq, kSawTick, kSawTickAt,
    kRectPrepare, kRectReset, kRectSetFreq, kRectTick, kRectTickAt,
    kTriPrepare, kTriReset, kTriSetFreq, kTriTick, kTriTickAt,
    kAdsrPrepare, kAdsrReset, kAdsrGate, kAdsrTick, kAdsrAttack, kAdsrDecay, kAdsrSustain, kAdsrRelease,
    kLinePrepare, kLineReset, kLineJump, kLineRamp, kLineTick,
    kTapPrepare, kTapReset, kTapWrite, kTapRead, kTapReadInLoop,
};

struct Ins {
    int op = kMov;
    int d = 0;      // the register written, or -1
    int k = 0;      // which kernel of its type, or a dial's number for kPos
    int a[6] = {};  // the registers read
};

using Code = std::vector<Ins>;

enum KernelType { kOnePole, kSvf, kBiquad, kDegrade, kComb, kAllpass, kCycle, kSaw, kRect, kTri, kAdsr, kLine, kTapLine, kKernelTypes };

// A device: everything a file says, checked, and ready to be run by any number of Vm.
struct Device {
    std::string name = "Plugin Playground", slug = "plugin-playground";
    bool instrument = false, tail = false;
    int voices = 0;
    bool steal = false;
    std::vector<std::string> paramNames, paramShortNames;  // what the ParamInfo point at
    std::vector<mxrt::ParamInfo> params;
    std::vector<double> registers;       // as they start
    int kernels[kKernelTypes] = {};      // how many of each type
    Code prepare, settle, reset, process;
    std::vector<Code> update, voice;
    std::vector<Code> every;             // what each snapshot~ runs when its countdown ends
    std::string face;                    // what the window draws: JSON in base64, or empty

    // Read a device file's text. On failure `error` says why, in words for a person.
    bool parse(const std::string& text, std::string* error);
};

namespace detail {

inline std::string unquote(const std::string& word) {
    std::string out;
    for (size_t i = 0; i < word.size(); i++) {
        if (word[i] == '%' && i + 2 < word.size()) {
            const int byte = static_cast<int>(std::strtol(word.substr(i + 1, 2).c_str(), nullptr, 16));
            if (byte != 0) out.push_back(static_cast<char>(byte));
            i += 2;
        } else {
            out.push_back(word[i]);
        }
    }
    return out;
}

struct Named { const char* name; int value; int reads; };

// f NAME: the function, and how many numbers it takes.
inline const Named* function(const std::string& name) {
    static const Named table[] = {
        {"clip", kClip, 3}, {"pong", kPong, 4}, {"overdrive", kOverdrive, 2}, {"scale", kScale, 5},
        {"toInt", kToInt, 1}, {"mtof", kMtof, 1}, {"dbtoa", kDbtoa, 1}, {"pow", kPow, 2},
        {"tanh", kTanh, 1}, {"sqrt", kSqrt, 1}, {"sin", kSin, 1}, {"cos", kCos, 1},
        {"abs", kAbs, 1}, {"max", kMax, 2}, {"floor", kFloor, 1},
    };
    for (const Named& f : table)
        if (name == f.name) return &f;
    return nullptr;
}

inline int kernelType(const std::string& name) {
    static const char* const names[kKernelTypes] = {"OnePole", "Svf", "Biquad", "Degrade", "Comb", "Allpass", "Cycle",
                                                    "Saw", "Rect", "Tri", "Adsr", "Line", "TapLine"};
    for (int t = 0; t < kKernelTypes; t++)
        if (name == names[t]) return t;
    return -1;
}

struct Method { int type; const char* name; int reads; int op; };

// c K METHOD, g D K FIELD and s K FIELD A: by kernel type, name and how many numbers go in.
// A field read takes none and a field write takes one, so they sit in the same table.
inline const Method* method(int type, const std::string& name, int reads, char kind) {
    static const Method calls[] = {
        {kOnePole, "prepare", 2, kOnePolePrepare}, {kOnePole, "reset", 0, kOnePoleReset},
        {kOnePole, "setFreq", 1, kOnePoleSetFreq}, {kOnePole, "tick", 1, kOnePoleTick},
        {kSvf, "prepare", 3, kSvfPrepare}, {kSvf, "reset", 0, kSvfReset}, {kSvf, "setFreq", 1, kSvfSetFreq},
        {kSvf, "setRes", 1, kSvfSetRes}, {kSvf, "tick", 1, kSvfTick},
        {kBiquad, "prepare", 5, kBiquadPrepare}, {kBiquad, "reset", 0, kBiquadReset}, {kBiquad, "set", 2, kBiquadSet},
        {kBiquad, "tick", 1, kBiquadTick},
        {kDegrade, "prepare", 2, kDegradePrepare}, {kDegrade, "reset", 0, kDegradeReset},
        {kDegrade, "setRatio", 1, kDegradeSetRatio}, {kDegrade, "setBits", 1, kDegradeSetBits},
        {kDegrade, "tick", 1, kDegradeTick},
        {kComb, "prepare", 6, kCombPrepare}, {kComb, "reset", 0, kCombReset}, {kComb, "tick", 1, kCombTick},
        {kComb, "tickSwept", 2, kCombTickSwept},
        {kAllpass, "prepare", 4, kAllpassPrepare}, {kAllpass, "reset", 0, kAllpassReset},
        {kAllpass, "setDelay", 1, kAllpassSetDelay}, {kAllpass, "tick", 1, kAllpassTick},
        {kCycle, "prepare", 2, kCyclePrepare}, {kCycle, "reset", 0, kCycleReset}, {kCycle, "setFreq", 1, kCycleSetFreq},
        {kCycle, "tick", 0, kCycleTick}, {kCycle, "tick", 1, kCycleTickAt},
        {kSaw, "prepare", 2, kSawPrepare}, {kSaw, "reset", 0, kSawReset}, {kSaw, "setFreq", 1, kSawSetFreq},
        {kSaw, "tick", 0, kSawTick}, {kSaw, "tick", 1, kSawTickAt},
        {kRect, "prepare", 2, kRectPrepare}, {kRect, "reset", 0, kRectReset}, {kRect, "setFreq", 1, kRectSetFreq},
        {kRect, "tick", 0, kRectTick}, {kRect, "tick", 1, kRectTickAt},
        {kTri, "prepare", 2, kTriPrepare}, {kTri, "reset", 0, kTriReset}, {kTri, "setFreq", 1, kTriSetFreq},
        {kTri, "tick", 0, kTriTick}, {kTri, "tick", 1, kTriTickAt},
        {kAdsr, "prepare", 5, kAdsrPrepare}, {kAdsr, "reset", 0, kAdsrReset}, {kAdsr, "gate", 1, kAdsrGate},
        {kAdsr, "tick", 0, kAdsrTick},
        {kLine, "prepare", 2, kLinePrepare}, {kLine, "reset", 0, kLineReset}, {kLine, "jump", 1, kLineJump},
        {kLine, "ramp", 2, kLineRamp}, {kLine, "tick", 0, kLineTick},
        {kTapLine, "prepare", 2, kTapPrepare}, {kTapLine, "reset", 0, kTapReset}, {kTapLine, "write", 1, kTapWrite},
        {kTapLine, "read", 1, kTapRead}, {kTapLine, "readInLoop", 1, kTapReadInLoop},
    };
    static const Method reads_[] = {
        {kSvf, "lp", 0, kSvfLp}, {kSvf, "hp", 0, kSvfHp}, {kSvf, "bp", 0, kSvfBp}, {kSvf, "notch", 0, kSvfNotch},
    };
    static const Method writes[] = {
        {kComb, "delayMs", 1, kCombDelayMs}, {kComb, "a", 1, kCombA}, {kComb, "b", 1, kCombB}, {kComb, "c", 1, kCombC},
        {kAllpass, "g", 1, kAllpassG},
        {kAdsr, "attack", 1, kAdsrAttack}, {kAdsr, "decay", 1, kAdsrDecay}, {kAdsr, "sustain", 1, kAdsrSustain},
        {kAdsr, "release", 1, kAdsrRelease},
    };
    const Method* at = kind == 'c' ? calls : kind == 'g' ? reads_ : writes;
    const size_t count = kind == 'c' ? sizeof calls / sizeof *calls
                       : kind == 'g' ? sizeof reads_ / sizeof *reads_ : sizeof writes / sizeof *writes;
    for (size_t i = 0; i < count; i++)
        if (at[i].type == type && at[i].reads == reads && name == at[i].name) return &at[i];
    return nullptr;
}

}  // namespace detail

inline bool Device::parse(const std::string& text, std::string* error) {
    *this = Device();
    const auto fail = [&](const std::string& why) {
        if (error) *error = why;
        return false;
    };
    const std::string newer = "this device needs a newer Plugin Playground: install the current one from the site";

    std::vector<int> kernelType, kernelIndex;  // by a kernel's number in the file
    Code* code = nullptr;
    int count = -1;
    bool headed = false, ended = false;
    std::istringstream lines(text);
    std::string line;
    int number = 0;
    while (std::getline(lines, line)) {
        number++;
        if (!line.empty() && line.back() == '\r') line.pop_back();
        std::istringstream in(line);
        std::vector<std::string> w;
        for (std::string word; in >> word;) w.push_back(word);
        if (w.empty()) continue;
        if (ended) {
            if (w[0] == "face" && w.size() == 2) face = w[1];
            continue;
        }
        const auto bad = [&]() { return fail("line " + std::to_string(number) + " of the device cannot be read: " + line); };
        const auto num = [&](size_t i) { return i < w.size() ? std::strtod(w[i].c_str(), nullptr) : 0.0; };
        const auto whole = [&](size_t i) { return i < w.size() ? static_cast<int>(std::strtol(w[i].c_str(), nullptr, 10)) : 0; };
        // A register named by an instruction has to exist.
        const auto reg = [&](size_t i, int* out, bool optional = false) {
            if (i >= w.size()) return false;
            const int r = whole(i);
            if (r == -1 && optional) { *out = -1; return true; }
            if (r < 0 || r >= count) return false;
            *out = r;
            return true;
        };
        const auto kernel = [&](size_t i, int* type, int* index) {
            const int k = whole(i);
            if (i >= w.size() || k < 0 || k >= static_cast<int>(kernelType.size()) || kernelType[k] < 0) return false;
            *type = kernelType[k];
            *index = kernelIndex[k];
            return true;
        };

        if (!headed) {
            if (w[0] != "ppdevice") return fail("this is not a Plugin Playground device file");
            if (whole(1) > kFormat) return fail(newer);
            headed = true;
            continue;
        }
        if (w[0][0] == '@') {
            if (count < 0) return bad();
            if (w[0] == "@end") { ended = true; continue; }
            if (w[0] == "@prepare") code = &prepare;
            else if (w[0] == "@settle") code = &settle;
            else if (w[0] == "@reset") code = &reset;
            else if (w[0] == "@process") code = &process;
            else if (w[0] == "@update" && whole(1) >= 0 && whole(1) < static_cast<int>(params.size())) {
                update.resize(params.size());
                code = &update[whole(1)];
            } else if (w[0] == "@voice" && whole(1) >= 1 && whole(1) <= mxrt::Poly::kMaxVoices) {
                if (static_cast<int>(voice.size()) < whole(1)) voice.resize(whole(1));
                code = &voice[whole(1) - 1];
            } else if (w[0] == "@every" && whole(1) >= 0 && whole(1) < 4096) {
                if (static_cast<int>(every.size()) <= whole(1)) every.resize(whole(1) + 1);
                code = &every[whole(1)];
            } else return bad();
            continue;
        }
        if (!code) {
            if (w[0] == "runtime") { if (whole(1) > kRuntimeVersion) return fail(newer); }
            else if (w[0] == "name" && w.size() > 1) name = detail::unquote(w[1]);
            else if (w[0] == "slug" && w.size() > 1) slug = w[1];
            else if (w[0] == "kind" && w.size() > 1) instrument = w[1] == "instrument";
            else if (w[0] == "tail") tail = whole(1) != 0;
            else if (w[0] == "poly") { voices = whole(1); steal = whole(2) != 0; }
            else if (w[0] == "param" && w.size() == 10) {
                mxrt::ParamInfo p{};
                p.id = static_cast<uint32_t>(std::strtoul(w[1].c_str(), nullptr, 10));
                p.min = num(2); p.max = num(3); p.initial = num(4); p.exponent = num(5);
                p.unit = whole(6); p.integer = whole(7) != 0;
                params.push_back(p);
                paramNames.push_back(detail::unquote(w[8]));
                paramShortNames.push_back(detail::unquote(w[9]));
            } else if (w[0] == "registers") {
                count = whole(1);
                if (count < kFixedRegisters || count > (1 << 20)) return bad();
                registers.assign(count, 0.0);
            } else if (w[0] == "n" && w.size() == 3) {
                int r;
                if (!reg(1, &r)) return bad();
                registers[r] = num(2);
            } else if (w[0] == "k" && w.size() == 3) {
                const int type = detail::kernelType(w[2]);
                if (type < 0) return fail(newer);
                if (whole(1) != static_cast<int>(kernelType.size())) return bad();
                kernelType.push_back(type);
                kernelIndex.push_back(kernels[type]++);
            }
            // Anything else in the heading is something a newer page added that this
            // runtime can do without; "runtime" is how a device says it cannot.
            continue;
        }

        Ins ins;
        const std::string& op = w[0];
        if (op == "=" || op == "neg") {
            ins.op = op == "=" ? kMov : kNeg;
            if (w.size() != 3 || !reg(1, &ins.d) || !reg(2, &ins.a[0])) return bad();
        } else if (op == "+" || op == "-" || op == "*" || op == "/") {
            ins.op = op == "+" ? kAdd : op == "-" ? kSub : op == "*" ? kMul : kDiv;
            if (w.size() != 4 || !reg(1, &ins.d) || !reg(2, &ins.a[0]) || !reg(3, &ins.a[1])) return bad();
        } else if (op == "every") {
            // every I COUNT PERIOD: count down in COUNT; at zero, refill it from PERIOD and run @every I
            ins.op = kEvery;
            ins.d = -1;
            ins.k = whole(1);
            if (w.size() != 4 || ins.k < 0 || ins.k >= 4096 || !reg(2, &ins.a[0]) || !reg(3, &ins.a[1])) return bad();
            // A section may not run another: that is all the nesting the countdown needs.
            if (code != &process) return bad();
        } else if (op == "pos") {
            ins.op = kPos;
            ins.k = whole(2);
            if (w.size() != 4 || !reg(1, &ins.d) || !reg(3, &ins.a[0]) || ins.k < 0 ||
                ins.k >= static_cast<int>(params.size())) return bad();
        } else if (op == "f") {
            const detail::Named* f = w.size() > 2 ? detail::function(w[1]) : nullptr;
            if (!f) return fail(newer);
            ins.op = f->value;
            if (static_cast<int>(w.size()) != 3 + f->reads || !reg(2, &ins.d)) return bad();
            for (int i = 0; i < f->reads; i++)
                if (!reg(3 + i, &ins.a[i])) return bad();
        } else if (op == "c" || op == "g" || op == "s") {
            // c K METHOD D A...    g D K FIELD    s K FIELD A
            const size_t kAt = op == "g" ? 2 : 1, nameAt = op == "g" ? 3 : 2, firstArg = op == "c" ? 4 : 3;
            int type = 0;
            if (w.size() <= nameAt || !kernel(kAt, &type, &ins.k)) return bad();
            const int reads = op == "g" ? 0 : static_cast<int>(w.size() - firstArg);
            if (reads < 0 || reads > 6) return bad();
            const detail::Method* m = detail::method(type, w[nameAt], reads, op[0]);
            if (!m) return fail(newer);
            ins.op = m->op;
            ins.d = -1;
            if (op == "g" && !reg(1, &ins.d)) return bad();
            if (op == "c" && !reg(3, &ins.d, true)) return bad();
            for (int i = 0; i < reads; i++)
                if (!reg(firstArg + i, &ins.a[i])) return bad();
        } else {
            return fail(newer);
        }
        code->push_back(ins);
    }
    if (!headed) return fail("this is not a Plugin Playground device file");
    if (!ended) return fail("the device file is cut short");
    for (const Ins& ins : process)
        if (ins.op == kEvery && ins.k >= static_cast<int>(every.size())) return fail("the device file is cut short");
    update.resize(params.size());
    if (instrument && (voices < 1 || voices > mxrt::Poly::kMaxVoices)) return fail("the device file names no voices");
    for (size_t i = 0; i < params.size(); i++) {
        params[i].name = paramNames[i].c_str();
        params[i].shortName = paramShortNames[i].c_str();
    }
    return true;
}

// One running copy of a device: its registers and kernels. Several can share a Device.
class Vm {
public:
    void bind(const Device* device) {
        d_ = device;
        r_ = device->registers;
        onePole_.assign(device->kernels[kOnePole], {});
        svf_.assign(device->kernels[kSvf], {});
        biquad_.assign(device->kernels[kBiquad], {});
        degrade_.assign(device->kernels[kDegrade], {});
        comb_.assign(device->kernels[kComb], {});
        allpass_.assign(device->kernels[kAllpass], {});
        cycle_.assign(device->kernels[kCycle], {});
        saw_.assign(device->kernels[kSaw], {});
        rect_.assign(device->kernels[kRect], {});
        tri_.assign(device->kernels[kTri], {});
        adsr_.assign(device->kernels[kAdsr], {});
        line_.assign(device->kernels[kLine], {});
        tap_.assign(device->kernels[kTapLine], {});
    }

    double& reg(int i) { return r_[i]; }

    void run(const Code& code) {
        double* const r = r_.data();
        for (const Ins& i : code) {
            const int* a = i.a;
            double out = 0;
            switch (i.op) {
                case kMov: out = r[a[0]]; break;
                case kAdd: out = r[a[0]] + r[a[1]]; break;
                case kSub: out = r[a[0]] - r[a[1]]; break;
                case kMul: out = r[a[0]] * r[a[1]]; break;
                case kDiv: out = r[a[0]] / r[a[1]]; break;
                case kNeg: out = -r[a[0]]; break;
                case kPos: out = mxrt::valueToPosition(d_->params[i.k], r[a[0]]); break;
                case kEvery:
                    if (r[a[0]] <= 0.0) {
                        r[a[0]] = r[a[1]];
                        run(d_->every[i.k]);
                    }
                    r[a[0]] -= 1.0;
                    break;

                case kClip: out = mxrt::clip(r[a[0]], r[a[1]], r[a[2]]); break;
                case kPong: out = mxrt::pong(r[a[0]], static_cast<int>(r[a[1]]), r[a[2]], r[a[3]]); break;
                case kOverdrive: out = mxrt::overdrive(r[a[0]], r[a[1]]); break;
                case kScale: out = mxrt::scale(r[a[0]], r[a[1]], r[a[2]], r[a[3]], r[a[4]]); break;
                case kToInt: out = mxrt::toInt(r[a[0]]); break;
                case kMtof: out = mxrt::mtof(r[a[0]]); break;
                case kDbtoa: out = mxrt::dbtoa(r[a[0]]); break;
                case kPow: out = std::pow(r[a[0]], r[a[1]]); break;
                case kTanh: out = std::tanh(r[a[0]]); break;
                case kSqrt: out = std::sqrt(r[a[0]]); break;
                case kSin: out = std::sin(r[a[0]]); break;
                case kCos: out = std::cos(r[a[0]]); break;
                case kAbs: out = std::fabs(r[a[0]]); break;
                case kMax: out = std::max(r[a[0]], r[a[1]]); break;
                case kFloor: out = std::floor(r[a[0]]); break;

                case kOnePolePrepare: onePole_[i.k].prepare(r[a[0]], r[a[1]]); break;
                case kOnePoleReset: onePole_[i.k].reset(); break;
                case kOnePoleSetFreq: onePole_[i.k].setFreq(r[a[0]]); break;
                case kOnePoleTick: out = onePole_[i.k].tick(r[a[0]]); break;

                case kSvfPrepare: svf_[i.k].prepare(r[a[0]], r[a[1]], r[a[2]]); break;
                case kSvfReset: svf_[i.k].reset(); break;
                case kSvfSetFreq: svf_[i.k].setFreq(r[a[0]]); break;
                case kSvfSetRes: svf_[i.k].setRes(r[a[0]]); break;
                case kSvfTick: svf_[i.k].tick(r[a[0]]); break;
                case kSvfLp: out = svf_[i.k].lp; break;
                case kSvfHp: out = svf_[i.k].hp; break;
                case kSvfBp: out = svf_[i.k].bp; break;
                case kSvfNotch: out = svf_[i.k].notch; break;

                case kBiquadPrepare: biquad_[i.k].prepare(r[a[0]], r[a[1]], r[a[2]], r[a[3]], r[a[4]]); break;
                case kBiquadReset: biquad_[i.k].reset(); break;
                case kBiquadSet: {
                    const int which = static_cast<int>(r[a[0]]);
                    if (which >= 0 && which < 5) biquad_[i.k].set(which, r[a[1]]);
                    break;
                }
                case kBiquadTick: out = biquad_[i.k].tick(r[a[0]]); break;

                case kDegradePrepare: degrade_[i.k].prepare(r[a[0]], r[a[1]]); break;
                case kDegradeReset: degrade_[i.k].reset(); break;
                case kDegradeSetRatio: degrade_[i.k].setRatio(r[a[0]]); break;
                case kDegradeSetBits: degrade_[i.k].setBits(r[a[0]]); break;
                case kDegradeTick: out = degrade_[i.k].tick(r[a[0]]); break;

                case kCombPrepare: comb_[i.k].prepare(r[a[0]], r[a[1]], r[a[2]], r[a[3]], r[a[4]], r[a[5]]); break;
                case kCombReset: comb_[i.k].reset(); break;
                case kCombTick: out = comb_[i.k].tick(r[a[0]]); break;
                case kCombTickSwept: out = comb_[i.k].tickSwept(r[a[0]], r[a[1]]); break;
                case kCombDelayMs: comb_[i.k].delayMs = r[a[0]]; break;
                case kCombA: comb_[i.k].a = r[a[0]]; break;
                case kCombB: comb_[i.k].b = r[a[0]]; break;
                case kCombC: comb_[i.k].c = r[a[0]]; break;

                case kAllpassPrepare: allpass_[i.k].prepare(r[a[0]], r[a[1]], r[a[2]], r[a[3]]); break;
                case kAllpassReset: allpass_[i.k].reset(); break;
                case kAllpassSetDelay: allpass_[i.k].setDelay(r[a[0]]); break;
                case kAllpassTick: out = allpass_[i.k].tick(r[a[0]]); break;
                case kAllpassG: allpass_[i.k].g = r[a[0]]; break;

                case kCyclePrepare: cycle_[i.k].prepare(r[a[0]], r[a[1]]); break;
                case kCycleReset: cycle_[i.k].reset(); break;
                case kCycleSetFreq: cycle_[i.k].setFreq(r[a[0]]); break;
                case kCycleTick: out = cycle_[i.k].tick(); break;
                case kCycleTickAt: out = cycle_[i.k].tick(r[a[0]]); break;

                case kSawPrepare: saw_[i.k].prepare(r[a[0]], r[a[1]]); break;
                case kSawReset: saw_[i.k].reset(); break;
                case kSawSetFreq: saw_[i.k].setFreq(r[a[0]]); break;
                case kSawTick: out = saw_[i.k].tick(); break;
                case kSawTickAt: out = saw_[i.k].tick(r[a[0]]); break;

                case kRectPrepare: rect_[i.k].prepare(r[a[0]], r[a[1]]); break;
                case kRectReset: rect_[i.k].reset(); break;
                case kRectSetFreq: rect_[i.k].setFreq(r[a[0]]); break;
                case kRectTick: out = rect_[i.k].tick(); break;
                case kRectTickAt: out = rect_[i.k].tick(r[a[0]]); break;

                case kTriPrepare: tri_[i.k].prepare(r[a[0]], r[a[1]]); break;
                case kTriReset: tri_[i.k].reset(); break;
                case kTriSetFreq: tri_[i.k].setFreq(r[a[0]]); break;
                case kTriTick: out = tri_[i.k].tick(); break;
                case kTriTickAt: out = tri_[i.k].tick(r[a[0]]); break;

                case kAdsrPrepare: adsr_[i.k].prepare(r[a[0]], r[a[1]], r[a[2]], r[a[3]], r[a[4]]); break;
                case kAdsrReset: adsr_[i.k].reset(); break;
                case kAdsrGate: adsr_[i.k].gate(r[a[0]]); break;
                case kAdsrTick: out = adsr_[i.k].tick(); break;
                case kAdsrAttack: adsr_[i.k].attack = r[a[0]]; break;
                case kAdsrDecay: adsr_[i.k].decay = r[a[0]]; break;
                case kAdsrSustain: adsr_[i.k].sustain = r[a[0]]; break;
                case kAdsrRelease: adsr_[i.k].release = r[a[0]]; break;

                case kLinePrepare: line_[i.k].prepare(r[a[0]], r[a[1]]); break;
                case kLineReset: line_[i.k].reset(); break;
                case kLineJump: line_[i.k].jump(r[a[0]]); break;
                case kLineRamp: line_[i.k].ramp(r[a[0]], r[a[1]]); break;
                case kLineTick: out = line_[i.k].tick(); break;

                case kTapPrepare: tap_[i.k].prepare(r[a[0]], r[a[1]]); break;
                case kTapReset: tap_[i.k].reset(); break;
                case kTapWrite: tap_[i.k].write(r[a[0]]); break;
                case kTapRead: out = tap_[i.k].read(r[a[0]]); break;
                case kTapReadInLoop: out = tap_[i.k].readInLoop(r[a[0]]); break;
                default: break;
            }
            if (i.d >= 0) r[i.d] = out;
        }
    }

private:
    const Device* d_ = nullptr;
    std::vector<double> r_;
    std::vector<mxrt::OnePole> onePole_;
    std::vector<mxrt::Svf> svf_;
    std::vector<mxrt::Biquad> biquad_;
    std::vector<mxrt::Degrade> degrade_;
    std::vector<mxrt::Comb> comb_;
    std::vector<mxrt::Allpass> allpass_;
    std::vector<mxrt::Cycle> cycle_;
    std::vector<mxrt::Saw> saw_;
    std::vector<mxrt::Rect> rect_;
    std::vector<mxrt::Tri> tri_;
    std::vector<mxrt::Adsr> adsr_;
    std::vector<mxrt::Line> line_;
    std::vector<mxrt::TapLine> tap_;
};

}  // namespace mxvm
