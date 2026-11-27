// hostcheck: load a built .clap the way a host does, and hold it to the Effect.h it was
// built from.
//
//   hostcheck <the plugin's binary>     (on macOS, the file inside Contents/MacOS)
//
// It plays noise through the plugin in blocks (or, for an instrument, plays it notes),
// moving every parameter part-way through a block, and does the same to a bare Effect at
// the same samples; the two must agree exactly. Then it checks what a host relies on: the parameter list and its
// text, and that saved state restores every value in a second instance.
//
// clap-validator covers the CLAP rules far more widely. This covers the one thing it
// cannot know: that the plugin is this effect.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#if defined(_WIN32)
#define NOMINMAX
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
#include <dlfcn.h>
#endif

#include <clap/clap.h>

#include "Effect.h"

namespace {

int failures = 0, checks = 0;

void check(bool ok, const char* what) {
    checks++;
    if (!ok) {
        failures++;
        std::fprintf(stderr, "hostcheck: FAILED: %s\n", what);
    }
}

// ── the least a host can be ─────────────────────────────────────────────────────

int rescans = 0;
void hostRescan(const clap_host_t*, clap_param_rescan_flags) { rescans++; }
void hostClear(const clap_host_t*, clap_id, clap_param_clear_flags) {}
void hostFlush(const clap_host_t*) {}
const clap_host_params_t kHostParams = {hostRescan, hostClear, hostFlush};

const void* hostExtension(const clap_host_t*, const char* id) {
    return std::strcmp(id, CLAP_EXT_PARAMS) == 0 ? &kHostParams : nullptr;
}
void hostNothing(const clap_host_t*) {}
const clap_host_t kHost = {CLAP_VERSION_INIT, nullptr, "hostcheck", "Plugin Playground", "", "1", hostExtension,
                           hostNothing, hostNothing, hostNothing};

// ── event lists and streams ─────────────────────────────────────────────────────

// One block's events, in time order: parameter moves and notes.
struct Events {
    union Any {
        clap_event_header_t header;
        clap_event_param_value_t param;
        clap_event_note_t note;
    };
    std::vector<Any> list;
    clap_input_events_t in;
    Events() {
        in.ctx = this;
        in.size = [](const clap_input_events_t* e) {
            return static_cast<uint32_t>(static_cast<Events*>(e->ctx)->list.size());
        };
        in.get = [](const clap_input_events_t* e, uint32_t i) -> const clap_event_header_t* {
            return &static_cast<Events*>(e->ctx)->list[i].header;
        };
    }
    void note(uint32_t time, bool on, int key, double velocity) {
        Any any;
        std::memset(&any, 0, sizeof any);
        clap_event_note_t& ev = any.note;
        ev.header.size = sizeof ev;
        ev.header.time = time;
        ev.header.space_id = CLAP_CORE_EVENT_SPACE_ID;
        ev.header.type = on ? CLAP_EVENT_NOTE_ON : CLAP_EVENT_NOTE_OFF;
        ev.note_id = -1;
        ev.port_index = 0;
        ev.channel = 0;
        ev.key = static_cast<int16_t>(key);
        ev.velocity = velocity;
        list.push_back(any);
    }
    void add(uint32_t time, clap_id id, double value) {
        Any any;
        std::memset(&any, 0, sizeof any);
        clap_event_param_value_t& ev = any.param;
        ev.header.size = sizeof ev;
        ev.header.time = time;
        ev.header.space_id = CLAP_CORE_EVENT_SPACE_ID;
        ev.header.type = CLAP_EVENT_PARAM_VALUE;
        ev.param_id = id;
        ev.note_id = -1; ev.port_index = -1; ev.channel = -1; ev.key = -1;
        ev.value = value;
        list.push_back(any);
    }
};

bool pushNothing(const clap_output_events_t*, const clap_event_header_t*) { return true; }
const clap_output_events_t kOutEvents = {nullptr, pushNothing};

struct Bytes {
    std::vector<char> data;
    size_t at = 0;
};
int64_t streamWrite(const clap_ostream_t* s, const void* buffer, uint64_t size) {
    Bytes* b = static_cast<Bytes*>(s->ctx);
    const uint64_t n = size < 5 ? size : 5;  // a few bytes at a time, as a host may
    b->data.insert(b->data.end(), static_cast<const char*>(buffer), static_cast<const char*>(buffer) + n);
    return static_cast<int64_t>(n);
}
int64_t streamRead(const clap_istream_t* s, void* buffer, uint64_t size) {
    Bytes* b = static_cast<Bytes*>(s->ctx);
    uint64_t n = b->data.size() - b->at;
    if (n > size) n = size;
    if (n > 7) n = 7;
    std::memcpy(buffer, b->data.data() + b->at, n);
    b->at += n;
    return static_cast<int64_t>(n);
}

// A repeatable noise source.
struct Noise {
    uint32_t state = 12345;
    float next() {
        state = state * 1664525u + 1013904223u;
        return (static_cast<int32_t>(state) / 2147483648.0f) * 0.5f;
    }
};

const clap_plugin_entry_t* load(const char* path) {
#if defined(_WIN32)
    HMODULE lib = LoadLibraryA(path);
    return lib ? reinterpret_cast<const clap_plugin_entry_t*>(GetProcAddress(lib, "clap_entry")) : nullptr;
#else
    void* lib = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!lib) std::fprintf(stderr, "hostcheck: %s\n", dlerror());
    return lib ? static_cast<const clap_plugin_entry_t*>(dlsym(lib, "clap_entry")) : nullptr;
#endif
}

}  // namespace

int main(int argc, char** argv) {
#ifdef FX_RUNTIME
    // Built on the runtime, the Effect here is no device until it is told which: the same
    // file the helper put inside the plugin being checked.
    std::string why;
    if (argc != 3 || !Effect::loadFile(argv[2], &why)) {
        std::fprintf(stderr, "hostcheck: %s\n", why.empty() ? "usage: hostcheck <plugin binary> <device file>" : why.c_str());
        return 2;
    }
#else
    if (argc != 2) {
        std::fprintf(stderr, "usage: hostcheck <plugin binary>\n");
        return 2;
    }
#endif
    const clap_plugin_entry_t* entry = load(argv[1]);
    if (!entry || !entry->init(argv[1])) {
        std::fprintf(stderr, "hostcheck: %s is not a loadable CLAP plugin\n", argv[1]);
        return 1;
    }
    const auto* factory = static_cast<const clap_plugin_factory_t*>(entry->get_factory(CLAP_PLUGIN_FACTORY_ID));
    check(factory && factory->get_plugin_count(factory) == 1, "the file holds one plugin");
    if (failures) return 1;
    const clap_plugin_descriptor_t* desc = factory->get_plugin_descriptor(factory, 0);
    check(std::strcmp(desc->name, Effect::kName) == 0, "the plugin has the effect's name");
    check(std::string(desc->id) == std::string("com.plugin-playground.") + Effect::kSlug,
          "the plugin id is built from the name");

    const clap_plugin_t* plugin = factory->create_plugin(factory, &kHost, desc->id);
    check(plugin && plugin->init(plugin), "the plugin can be created");
    if (failures) return 1;

    const auto* ports = static_cast<const clap_plugin_audio_ports_t*>(plugin->get_extension(plugin, CLAP_EXT_AUDIO_PORTS));
    const auto* params = static_cast<const clap_plugin_params_t*>(plugin->get_extension(plugin, CLAP_EXT_PARAMS));
    const auto* state = static_cast<const clap_plugin_state_t*>(plugin->get_extension(plugin, CLAP_EXT_STATE));
    check(ports && params && state, "audio ports, parameters and state are all offered");
    if (failures) return 1;

    for (bool input : {true, false}) {
        clap_audio_port_info_t info;
        if (input && Effect::kIsInstrument) {
            check(ports->count(plugin, true) == 0, "an instrument has no audio input");
            continue;
        }
        check(ports->count(plugin, input) == 1 && ports->get(plugin, 0, input, &info) && info.channel_count == 2,
              "one stereo port each way");
    }
    const auto* notes = static_cast<const clap_plugin_note_ports_t*>(plugin->get_extension(plugin, CLAP_EXT_NOTE_PORTS));
    if (Effect::kIsInstrument) {
        clap_note_port_info_t info;
        check(notes && notes->count(plugin, true) == 1 && notes->get(plugin, 0, true, &info) &&
                  (info.supported_dialects & CLAP_NOTE_DIALECT_CLAP),
              "an instrument has one note input");
    } else {
        check(!notes, "an effect takes no notes");
    }

    // The parameter list is the dials, in order, and text survives a round trip.
    const int n = Effect::kNumParams;
    check(params->count(plugin) == static_cast<uint32_t>(n), "one parameter per dial");
    std::vector<clap_param_info_t> infos(n);
    for (int i = 0; i < n; i++) {
        clap_param_info_t& info = infos[i];
        check(params->get_info(plugin, i, &info), "a parameter describes itself");
        check(info.id == Effect::kParams[i].id && std::strcmp(info.name, Effect::kParams[i].name) == 0,
              "a parameter has its dial's id and name");
        const bool inside = info.default_value >= info.min_value && info.default_value <= info.max_value;
        check(info.min_value < info.max_value && inside, "a parameter's default is inside its range");
        double value = 0;
        check(params->get_value(plugin, info.id, &value) && value == info.default_value,
              "a parameter starts at its default");
        char text[64] = "";
        double back = 0;
        check(params->value_to_text(plugin, info.id, info.default_value, text, sizeof text) && text[0] != 0,
              "a value prints as text");
        char expect[64];
        mxrt::formatValue(Effect::kParams[i], Effect::kParams[i].initial, expect, sizeof expect);
        check(std::strcmp(text, expect) == 0, "the default prints the way the dial shows it");
        check(params->text_to_value(plugin, info.id, text, &back) &&
                  std::fabs(back - info.default_value) <= 0.02 * (info.max_value - info.min_value),
              "the text reads back as about the same value");
    }

    // Audio: the plugin against a bare Effect, with every parameter moved mid-block.
    const double sampleRate = 48000;
    const uint32_t block = 256, blocks = 400, moveBlock = 20, moveAt = 100;
    check(plugin->activate(plugin, sampleRate, 1, block) && plugin->start_processing(plugin), "the plugin starts");
    Effect direct;
    direct.prepare(sampleRate);
    // The plugin switches denormals off while it runs; the comparison has to as well, or a
    // tail that has decayed to 1e-38 is that in one and zero in the other.
    mxrt::ScopedNoDenormals quiet;

    std::vector<float> inL(block), inR(block), outL(block), outR(block), wantL(block), wantR(block);
    std::vector<double> in64L(block), in64R(block), out64L(block), out64R(block);
    Noise noise;
    double worst = 0;
    bool finite = true, heard = false;
    for (uint32_t b = 0; b < blocks; b++) {
        for (uint32_t i = 0; i < block; i++) {
            // Noise, then silence, so tails are compared too.
            const bool on = b < blocks / 2;
            inL[i] = on ? noise.next() : 0.0f;
            inR[i] = on ? noise.next() : 0.0f;
            in64L[i] = inL[i];
            in64R[i] = inR[i];
        }
        Events events;
        const float* directIn[2] = {inL.data(), inR.data()};
        float* directOut[2] = {wantL.data(), wantR.data()};
        // An instrument is played a rising chord, more notes than it has voices so that
        // some are stolen, each landing mid-block; all are let go halfway through.
        const bool chord = Effect::kIsInstrument && b % 8 == 2 && b < 100 && b != moveBlock;
        const bool letGo = Effect::kIsInstrument && b == blocks / 2;
        if (chord || letGo) {
            const uint32_t at = 37;
            direct.process(directIn, directOut, static_cast<int>(at));
            if (chord) {
                const int key = 48 + static_cast<int>(b / 8) * 3 % 36;
                const double velocity = 0.4 + 0.05 * (b / 8 % 10);
                events.note(at, true, key, velocity);
                direct.noteOn(key, velocity * 127.0);
            } else {
                for (int key = 0; key < 128; key++) {
                    events.note(at, false, key, 0.0);
                    direct.noteOff(key);
                }
            }
            const float* restIn[2] = {inL.data() + at, inR.data() + at};
            float* restOut[2] = {wantL.data() + at, wantR.data() + at};
            direct.process(restIn, restOut, static_cast<int>(block - at));
        } else if (b == moveBlock) {
            for (int i = 0; i < n; i++) {
                // Somewhere between 30 % and 70 % of the way up, different for each parameter.
                const double way = 0.3 + 0.4 * i / (n > 1 ? n - 1 : 1);
                events.add(moveAt, infos[i].id, infos[i].min_value + (infos[i].max_value - infos[i].min_value) * way);
            }
            direct.process(directIn, directOut, static_cast<int>(moveAt));
            for (int i = 0; i < n; i++) {
                const mxrt::ParamInfo& p = Effect::kParams[i];
                const bool curved = p.exponent != 1.0 && !p.integer;
                const double v = events.list[i].param.value;
                direct.setParam(i, curved ? mxrt::positionToValue(p, v) : v);
            }
            const float* restIn[2] = {inL.data() + moveAt, inR.data() + moveAt};
            float* restOut[2] = {wantL.data() + moveAt, wantR.data() + moveAt};
            direct.process(restIn, restOut, static_cast<int>(block - moveAt));
        } else {
            direct.process(directIn, directOut, static_cast<int>(block));
        }

        // Odd blocks are sent as doubles and in place, the other ways a host may call.
        const bool wide = b % 2 == 1;
        float* in32[2] = {inL.data(), inR.data()};
        float* out32[2] = {outL.data(), outR.data()};
        double* inout64[2] = {in64L.data(), in64R.data()};
        clap_audio_buffer_t in, out;
        std::memset(&in, 0, sizeof in);
        std::memset(&out, 0, sizeof out);
        in.channel_count = out.channel_count = 2;
        if (wide) { in.data64 = inout64; out.data64 = inout64; }
        else { in.data32 = in32; out.data32 = out32; }
        const bool hasInput = !Effect::kIsInstrument;
        clap_process_t process;
        std::memset(&process, 0, sizeof process);
        process.steady_time = -1;
        process.frames_count = block;
        process.audio_inputs = hasInput ? &in : nullptr;
        process.audio_outputs = &out;
        process.audio_inputs_count = hasInput ? 1 : 0;
        process.audio_outputs_count = 1;
        process.in_events = &events.in;
        process.out_events = &kOutEvents;
        check(plugin->process(plugin, &process) != CLAP_PROCESS_ERROR, "a block is processed");

        for (uint32_t i = 0; i < block; i++) {
            const double l = wide ? in64L[i] : outL[i], r = wide ? in64R[i] : outR[i];
            finite = finite && std::isfinite(l) && std::isfinite(r);
            heard = heard || l != 0 || r != 0;
            // A block sent as doubles is not rounded to float on the way out; allow for that.
            const double slack = wide ? 1e-6 : 0.0;
            const double dl = std::fabs(l - wantL[i]), dr = std::fabs(r - wantR[i]);
            if (dl > slack && dl > worst) worst = dl;
            if (dr > slack && dr > worst) worst = dr;
        }
    }
    check(finite, "the output is finite");
    check(heard, "the output is not silent");
    if (worst != 0) std::fprintf(stderr, "hostcheck: the plugin and the Effect differ by up to %g\n", worst);
    check(worst == 0, "the plugin's output is the Effect's output, sample for sample");

    // State: what this instance saves, a second instance loads.
    Bytes saved;
    clap_ostream_t ostream = {&saved, streamWrite};
    check(state->save(plugin, &ostream) && !saved.data.empty(), "state saves");
    const clap_plugin_t* second = factory->create_plugin(factory, &kHost, desc->id);
    check(second && second->init(second), "a second instance can be created");
    if (second) {
        const auto* params2 = static_cast<const clap_plugin_params_t*>(second->get_extension(second, CLAP_EXT_PARAMS));
        const auto* state2 = static_cast<const clap_plugin_state_t*>(second->get_extension(second, CLAP_EXT_STATE));
        clap_istream_t istream = {&saved, streamRead};
        rescans = 0;
        check(state2->load(second, &istream), "state loads");
        check(n == 0 || rescans > 0, "loading state tells the host the values changed");
        int moved = 0;
        for (int i = 0; i < n; i++) {
            double a = 0, b = 1;
            params->get_value(plugin, infos[i].id, &a);
            params2->get_value(second, infos[i].id, &b);
            check(a == b, "a loaded parameter has the saved value");
            if (a != infos[i].default_value) moved++;
        }
        // A stepped parameter can be moved onto its own default; most cannot.
        check(n == 0 || moved > 0, "the saved values were not just the defaults");
        Bytes junk;
        junk.data.assign({'n', 'o', 'p', 'e'});
        clap_istream_t bad = {&junk, streamRead};
        check(!state2->load(second, &bad), "state that is not ours is refused");
        second->destroy(second);
    }

    // Mono: a host may make an effect's ports mono, as Logic does on a mono audio track,
    // which is offered only effects that take mono. Mono in feeds both sides of the Effect;
    // mono out is the mean of its two sides. An instrument stays stereo.
    const clap_plugin_t* mono = factory->create_plugin(factory, &kHost, desc->id);
    check(mono && mono->init(mono), "an instance for a mono track can be created");
    if (mono) {
        using Request = clap_audio_port_configuration_request_t;
        const auto* layouts = static_cast<const clap_plugin_configurable_audio_ports_t*>(
            mono->get_extension(mono, CLAP_EXT_CONFIGURABLE_AUDIO_PORTS));
        const auto* monoPorts = static_cast<const clap_plugin_audio_ports_t*>(mono->get_extension(mono, CLAP_EXT_AUDIO_PORTS));
        if (Effect::kIsInstrument) {
            check(!layouts, "an instrument's ports are not made mono");
        } else {
            check(layouts != nullptr, "an effect's ports can be made mono");
        }
        if (layouts && !Effect::kIsInstrument) {
            const Request both[2] = {{true, 0, 1, CLAP_PORT_MONO, nullptr}, {false, 0, 1, CLAP_PORT_MONO, nullptr}};
            const Request monoIn[1] = {{true, 0, 1, CLAP_PORT_MONO, nullptr}};
            const Request monoOut[1] = {{false, 0, 1, CLAP_PORT_MONO, nullptr}};
            const Request four[1] = {{true, 0, 4, nullptr, nullptr}};
            const Request misnamed[1] = {{true, 0, 1, CLAP_PORT_STEREO, nullptr}};
            const Request elsewhere[1] = {{true, 1, 1, CLAP_PORT_MONO, nullptr}};
            check(layouts->can_apply_configuration(mono, monoIn, 1), "mono in, stereo out is offered");
            check(!layouts->can_apply_configuration(mono, monoOut, 1), "stereo in, mono out is not");
            check(!layouts->can_apply_configuration(mono, four, 1) && !layouts->can_apply_configuration(mono, misnamed, 1) &&
                      !layouts->can_apply_configuration(mono, elsewhere, 1),
                  "four channels, a mislabelled port and a port that is not there are refused");
            // Mono in, stereo out: two widths, so the ports are no longer one buffer in place.
            check(layouts->apply_configuration(mono, monoIn, 1), "mono in, stereo out is applied");
            clap_audio_port_info_t wideIn, wideOut;
            check(monoPorts->get(mono, 0, true, &wideIn) && monoPorts->get(mono, 0, false, &wideOut) &&
                      wideIn.channel_count == 1 && wideOut.channel_count == 2 &&
                      wideIn.in_place_pair == CLAP_INVALID_ID && wideOut.in_place_pair == CLAP_INVALID_ID,
                  "mono in, stereo out are not offered as one buffer in place");
            check(layouts->apply_configuration(mono, both, 2), "mono in and out is applied");
            clap_audio_port_info_t a, b;
            check(monoPorts->get(mono, 0, true, &a) && monoPorts->get(mono, 0, false, &b) && a.channel_count == 1 &&
                      b.channel_count == 1 && std::strcmp(a.port_type, CLAP_PORT_MONO) == 0 &&
                      a.in_place_pair == b.id && b.in_place_pair == a.id,
                  "the ports then say mono, and may share a buffer again");
            check(mono->activate(mono, sampleRate, 1, block) && mono->start_processing(mono), "the mono plugin starts");
            check(!layouts->can_apply_configuration(mono, monoIn, 1), "the ports are not changed while it runs");
            Effect ref;
            ref.prepare(sampleRate);
            std::vector<float> m(block), mo(block);
            double off = 0;
            bool monoHeard = false;
            for (uint32_t blk = 0; blk < 100; blk++) {
                for (uint32_t i = 0; i < block; i++) m[i] = blk < 50 ? noise.next() : 0.0f;
                const float* directIn[2] = {m.data(), m.data()};
                float* directOut[2] = {wantL.data(), wantR.data()};
                ref.process(directIn, directOut, static_cast<int>(block));
                float* in32[1] = {m.data()};
                float* out32[1] = {mo.data()};
                clap_audio_buffer_t in, out;
                std::memset(&in, 0, sizeof in);
                std::memset(&out, 0, sizeof out);
                in.channel_count = out.channel_count = 1;
                in.data32 = in32;
                out.data32 = out32;
                Events events;
                clap_process_t process;
                std::memset(&process, 0, sizeof process);
                process.steady_time = -1;
                process.frames_count = block;
                process.audio_inputs = &in;
                process.audio_outputs = &out;
                process.audio_inputs_count = 1;
                process.audio_outputs_count = 1;
                process.in_events = &events.in;
                process.out_events = &kOutEvents;
                check(mono->process(mono, &process) != CLAP_PROCESS_ERROR, "a mono block is processed");
                for (uint32_t i = 0; i < block; i++) {
                    const float want = (wantL[i] + wantR[i]) * 0.5f;
                    off = std::max(off, std::fabs(double(mo[i]) - want));
                    monoHeard = monoHeard || mo[i] != 0;
                }
            }
            if (off != 0) std::fprintf(stderr, "hostcheck: mono differs from the Effect's mean by up to %g\n", off);
            check(monoHeard && off == 0, "mono out is the mean of the Effect's two sides, fed the mono input");
            mono->stop_processing(mono);
            mono->deactivate(mono);
        }
        mono->destroy(mono);
    }

#ifdef FX_RUNTIME
    // Presets: each one sets the dials it names (and the rest to their defaults) and tells
    // the host. One that runs another program swaps it in, playing, on the next block;
    // the plugin is then a fresh Effect of that program, so it sounds like one.
    const auto* presets = static_cast<const clap_plugin_preset_load_t*>(plugin->get_extension(plugin, CLAP_EXT_PRESET_LOAD));
    check((presets != nullptr) == (Effect::presets() > 0), "presets can be loaded exactly when the plugin has them");
    check(!presets || !presets->from_location(plugin, CLAP_PRESET_DISCOVERY_LOCATION_PLUGIN, nullptr, "999999"),
          "a preset that is not there is refused");
    int program = 0;
    for (int k = 0; presets && k < Effect::presets(); k++) {
        const mxvm::Book::Preset& preset = Effect::preset(k);
        rescans = 0;
        const std::string key = std::to_string(k);
        check(presets->from_location(plugin, CLAP_PRESET_DISCOVERY_LOCATION_PLUGIN, nullptr, key.c_str()), "a preset loads");
        check(n == 0 || rescans > 0, "loading a preset tells the host the values changed");
        Effect fresh(preset.program);
        for (int i = 0; i < n; i++) fresh.setParam(i, Effect::kParams[i].initial);
        for (const auto& [id, value] : preset.values) fresh.setParam(Effect::indexOf(id), value);
        bool where = true;
        for (int i = 0; i < n; i++) {
            double v = 0;
            params->get_value(plugin, infos[i].id, &v);
            const mxrt::ParamInfo& p = Effect::kParams[i];
            const double want = p.exponent != 1.0 && !p.integer ? mxrt::valueToPosition(p, fresh.getParam(i)) : fresh.getParam(i);
            where = where && std::fabs(v - want) <= 1e-9 * (1 + std::fabs(want));
        }
        check(where, "a preset puts every dial where it says");
        const bool swapped = preset.program != program;
        program = preset.program;
        if (!swapped) continue;
        fresh.prepare(sampleRate);
        double off = 0;
        bool sounds = true;
        for (uint32_t b = 0; b < 40; b++) {
            for (uint32_t i = 0; i < block; i++) { inL[i] = noise.next(); inR[i] = noise.next(); }
            Events events;
            if (Effect::kIsInstrument && b == 0) {
                events.note(0, true, 60, 0.8);
                fresh.noteOn(60, 0.8 * 127.0);
            }
            const float* directIn[2] = {inL.data(), inR.data()};
            float* directOut[2] = {wantL.data(), wantR.data()};
            fresh.process(directIn, directOut, static_cast<int>(block));
            float* in32[2] = {inL.data(), inR.data()};
            float* out32[2] = {outL.data(), outR.data()};
            clap_audio_buffer_t in, out;
            std::memset(&in, 0, sizeof in);
            std::memset(&out, 0, sizeof out);
            in.channel_count = out.channel_count = 2;
            in.data32 = in32;
            out.data32 = out32;
            clap_process_t process;
            std::memset(&process, 0, sizeof process);
            process.steady_time = -1;
            process.frames_count = block;
            process.audio_inputs = Effect::kIsInstrument ? nullptr : &in;
            process.audio_outputs = &out;
            process.audio_inputs_count = Effect::kIsInstrument ? 0 : 1;
            process.audio_outputs_count = 1;
            process.in_events = &events.in;
            process.out_events = &kOutEvents;
            sounds = sounds && plugin->process(plugin, &process) != CLAP_PROCESS_ERROR;
            for (uint32_t i = 0; i < block; i++)
                off = std::max(off, std::max(std::fabs(double(outL[i]) - wantL[i]), std::fabs(double(outR[i]) - wantR[i])));
        }
        if (off != 0) std::fprintf(stderr, "hostcheck: preset %s differs from its program by up to %g\n", preset.name.c_str(), off);
        check(sounds && off == 0, "a preset with another program plays as that program, from the next block");
    }
    if (presets) {
        // The program and the preset are saved with the dials, and come back.
        Bytes again;
        clap_ostream_t ostream2 = {&again, streamWrite};
        check(state->save(plugin, &ostream2), "state with a preset saves");
        const clap_plugin_t* third = factory->create_plugin(factory, &kHost, desc->id);
        if (third && third->init(third)) {
            const auto* state3 = static_cast<const clap_plugin_state_t*>(third->get_extension(third, CLAP_EXT_STATE));
            clap_istream_t istream = {&again, streamRead};
            check(state3->load(third, &istream), "state with a preset loads");
            Bytes back;
            clap_ostream_t ostream3 = {&back, streamWrite};
            check(state3->save(third, &ostream3) && back.data == again.data, "...as the same program, preset and dials");
            third->destroy(third);
        }
    }
#endif

    plugin->stop_processing(plugin);
    plugin->deactivate(plugin);
    plugin->destroy(plugin);
    entry->deinit();

    if (failures) {
        std::fprintf(stderr, "hostcheck: %d of %d checks failed for %s\n", failures, checks, Effect::kName);
        return 1;
    }
    std::printf("hostcheck: ok, %d checks (%s)\n", checks, Effect::kName);
    return 0;
}
