// Effect.h for the runtime plugin: the same class a generated Effect.h declares, except
// that what it is comes from a device file read when the plugin loads, not from code
// compiled into it. The shell, the renderer and hostcheck are written against that class
// and are built unchanged against this one.
//
// A device file can hold a plugin's presets, and the programs they run (mxvm::Book). An
// Effect runs one program, the first unless it is made with another; the dials it takes
// are the plugin's, which are the same whichever program runs. The shell changes program
// by making an Effect for the new one.
//
// Effect::load() has to be called once, before anything else here is used. Until then
// (and if the file cannot be read) there is no device and nothing to make.

#pragma once

#include "mxvm.h"

#define FX_RUNTIME 1

class Effect {
public:
    // What a generated Effect.h has as constants. Here they follow the loaded device.
    static inline const char* kName = "Plugin Playground";
    static inline const char* kSlug = "plugin-playground";
    static inline int kNumParams = 0;
    static inline bool kHasTail = false;
    static inline bool kIsInstrument = false;
    static inline const mxrt::ParamInfo* kParams = nullptr;
    static inline const char* kFace = "";

    // Read a device file's text and become that device. False, with a reason, if it
    // cannot be run; the process then has no device.
    static bool load(const std::string& text, std::string* error = nullptr) {
        auto next = std::make_unique<mxvm::Book>();
        if (!next->parse(text, error)) return false;
        book() = std::move(next);
        const mxvm::Book& b = *book();
        const mxvm::Device& d = *b.programs[0];
        kName = d.name.c_str();
        kSlug = d.slug.c_str();
        kNumParams = static_cast<int>(b.params.size());
        kHasTail = b.tail;
        kIsInstrument = b.instrument;
        kParams = b.params.data();
        kFace = d.face.c_str();
        return true;
    }

    static bool loadFile(const std::string& path, std::string* error = nullptr) {
        std::ifstream file(path, std::ios::binary);
        if (!file) {
            if (error) *error = "there is no device file at " + path;
            return false;
        }
        std::ostringstream text;
        text << file.rdbuf();
        return load(text.str(), error);
    }

    static bool loaded() { return book() != nullptr; }

    // The plugin's programs and presets.
    static int programs() { return loaded() ? static_cast<int>(book()->programs.size()) : 0; }
    static const char* faceOf(int program) {
        return program >= 0 && program < programs() ? book()->programs[program]->face.c_str() : "";
    }
    static int presets() { return loaded() ? static_cast<int>(book()->presets.size()) : 0; }
    static const mxvm::Book::Preset& preset(int index) { return book()->presets[index]; }
    static int firstPreset() { return loaded() ? book()->current : -1; }
    // A program's own dial numbers (what its face says) as the plugin's.
    static const std::vector<int>& dialsOf(int program) { return book()->global[program]; }
    static int indexOf(uint32_t id) { return loaded() ? book()->indexOf(id) : -1; }

    explicit Effect(int program = 0) {
        if (!loaded() || program < 0 || program >= programs()) return;
        program_ = program;
        d_ = book()->programs[program].get();
        local_ = &book()->local[program];
        vm_.bind(d_);
        value_.resize(kNumParams);
        for (int i = 0; i < kNumParams; i++) value_[i] = kParams[i].initial;
        // The program's own starting values, where it has the dial.
        for (int i = 0; i < kNumParams; i++)
            if ((*local_)[i] >= 0) value_[i] = d_->params[(*local_)[i]].initial;
    }

    int program() const { return program_; }

    void prepare(double sampleRate) {
        if (!d_) return;
        vm_.reg(mxvm::kSampleRate) = sampleRate;
        if (d_->instrument) poly_.prepare(d_->voices, d_->steal);
        vm_.run(d_->prepare);
        for (int i = 0; i < kNumParams; i++) update(i);
        vm_.run(d_->settle);
    }

    void reset() {
        if (!d_) return;
        if (d_->instrument) poly_.reset();
        vm_.run(d_->reset);
    }

    void setParam(int index, double value) {
        if (!d_ || index < 0 || index >= kNumParams) return;
        value_[index] = mxrt::clampValue(kParams[index], value);
        update(index);
    }

    double getParam(int index) const { return d_ && index >= 0 && index < kNumParams ? value_[index] : 0.0; }

    void noteOn(int key, double velocity) { note(key, velocity < 1.0 ? 1.0 : velocity); }
    void noteOff(int key) { note(key, 0.0); }

    template <typename Sample>
    void process(const Sample* const* in, Sample* const* out, int frames) {
        if (!d_) {
            for (int i = 0; i < frames; i++) out[0][i] = out[1][i] = 0;
            return;
        }
        const bool fed = !d_->instrument && in;
        for (int i = 0; i < frames; i++) {
            if (fed) {
                vm_.reg(mxvm::kInL) = in[0][i];
                vm_.reg(mxvm::kInR) = in[1][i];
            }
            vm_.run(d_->process);
            out[0][i] = static_cast<Sample>(vm_.reg(mxvm::kOutL));
            out[1][i] = static_cast<Sample>(vm_.reg(mxvm::kOutR));
        }
    }

private:
    static std::unique_ptr<mxvm::Book>& book() {
        static std::unique_ptr<mxvm::Book> held;
        return held;
    }

    // A dial this program does not have does nothing.
    void update(int index) {
        const int own = (*local_)[index];
        if (own < 0) return;
        vm_.reg(mxvm::kValue) = value_[index];
        vm_.run(d_->update[own]);
    }

    void note(int key, double velocity) {
        if (!d_ || !d_->instrument) return;
        mxrt::Poly::Message sent[2];
        const int count = poly_.note(key, velocity, sent);
        for (int k = 0; k < count; k++) {
            const int voice = sent[k].voice;
            if (voice < 1 || voice > static_cast<int>(d_->voice.size())) continue;
            vm_.reg(mxvm::kPitch) = sent[k].pitch;
            vm_.reg(mxvm::kVelocity) = sent[k].velocity;
            vm_.run(d_->voice[voice - 1]);
        }
    }

    int program_ = 0;
    const mxvm::Device* d_ = nullptr;
    const std::vector<int>* local_ = nullptr;
    mxvm::Vm vm_;
    mxrt::Poly poly_;
    std::vector<double> value_;
};
