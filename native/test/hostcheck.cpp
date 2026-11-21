// hostcheck: load a built .clap the way a host does, and hold it to the Effect.h it was
// built from.
//
//   hostcheck <the plugin's binary>     (on macOS, the file inside Contents/MacOS)
//
// It plays noise through the plugin in blocks, moving every parameter part-way through a
// block, and through a bare Effect given the same moves at the same samples; the two
// must agree exactly. Then it checks what a host relies on: the parameter list and its
// text, and that saved state restores every value in a second instance.
//
// clap-validator covers the CLAP rules far more widely. This covers the one thing it
// cannot know: that the plugin is this effect.

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

struct Events {
    std::vector<clap_event_param_value_t> list;
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
    void add(uint32_t time, clap_id id, double value) {
        clap_event_param_value_t ev;
        std::memset(&ev, 0, sizeof ev);
        ev.header.size = sizeof ev;
        ev.header.time = time;
        ev.header.space_id = CLAP_CORE_EVENT_SPACE_ID;
        ev.header.type = CLAP_EVENT_PARAM_VALUE;
        ev.param_id = id;
        ev.note_id = -1; ev.port_index = -1; ev.channel = -1; ev.key = -1;
        ev.value = value;
        list.push_back(ev);
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
    if (argc != 2) {
        std::fprintf(stderr, "usage: hostcheck <plugin binary>\n");
        return 2;
    }
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
        check(ports->count(plugin, input) == 1 && ports->get(plugin, 0, input, &info) && info.channel_count == 2,
              "one stereo port each way");
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
        if (b == moveBlock) {
            for (int i = 0; i < n; i++) {
                // Somewhere between 30 % and 70 % of the way up, different for each parameter.
                const double way = 0.3 + 0.4 * i / (n > 1 ? n - 1 : 1);
                events.add(moveAt, infos[i].id, infos[i].min_value + (infos[i].max_value - infos[i].min_value) * way);
            }
            direct.process(directIn, directOut, static_cast<int>(moveAt));
            for (int i = 0; i < n; i++) {
                const mxrt::ParamInfo& p = Effect::kParams[i];
                const bool curved = p.exponent != 1.0 && !p.integer;
                direct.setParam(i, curved ? mxrt::positionToValue(p, events.list[i].value) : events.list[i].value);
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
        clap_process_t process;
        std::memset(&process, 0, sizeof process);
        process.steady_time = -1;
        process.frames_count = block;
        process.audio_inputs = &in;
        process.audio_outputs = &out;
        process.audio_inputs_count = process.audio_outputs_count = 1;
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
