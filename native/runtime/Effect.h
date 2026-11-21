// Effect.h for the runtime plugin: the same class a generated Effect.h declares, except
// that what it is comes from a device file read when the plugin loads, not from code
// compiled into it. The shell, the renderer and hostcheck are written against that class
// and are built unchanged against this one.
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

    // Read a device file's text and become that device. False, with a reason, if it
    // cannot be run; the process then has no device.
    static bool load(const std::string& text, std::string* error = nullptr) {
        auto next = std::make_unique<mxvm::Device>();
        if (!next->parse(text, error)) return false;
        device() = std::move(next);
        const mxvm::Device& d = *device();
        kName = d.name.c_str();
        kSlug = d.slug.c_str();
        kNumParams = static_cast<int>(d.params.size());
        kHasTail = d.tail;
        kIsInstrument = d.instrument;
        kParams = d.params.data();
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

    static bool loaded() { return device() != nullptr; }

    Effect() {
        if (!loaded()) return;
        d_ = device().get();
        vm_.bind(d_);
        value_.resize(kNumParams);
        for (int i = 0; i < kNumParams; i++) value_[i] = d_->params[i].initial;
    }

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
        value_[index] = mxrt::clampValue(d_->params[index], value);
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
    static std::unique_ptr<mxvm::Device>& device() {
        static std::unique_ptr<mxvm::Device> held;
        return held;
    }

    void update(int index) {
        vm_.reg(mxvm::kValue) = value_[index];
        vm_.run(d_->update[index]);
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

    const mxvm::Device* d_ = nullptr;
    mxvm::Vm vm_;
    mxrt::Poly poly_;
    std::vector<double> value_;
};
