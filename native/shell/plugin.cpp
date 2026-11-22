// The plugin shell: one Effect.h, wrapped as a CLAP plugin. That is either the Effect.h
// patchc generated for one device, or the runtime's (native/runtime), which becomes
// whichever device is in the file next to the plugin when the plugin is loaded. An effect gets a
// stereo input and output; an instrument (Effect::kIsInstrument) gets a note input and a
// stereo output, and is played by the host's notes or by raw MIDI.
//
// Hand-written and the same for every effect. The Effect.h it includes is whichever one
// patchc generated for this build; clap-wrapper turns the result into VST3 and Audio
// Unit as well. On macOS the plugin has a window of its own (gui.h), which draws the
// device as the page does; elsewhere the host shows its own controls for the parameters.
//
// Parameters. A linear dial is given to the host in its own units (0 to 100 %, -24 to
// +12 dB). A dial with a curve (parameter_exponent other than 1, as on every frequency
// and time) is given to the host as the knob's position, 0 to 1, so that the host's
// slider and its automation lanes have the feel of the dial in Live; the text the host
// shows is still the value in Hz or ms. Saved state holds values in the dial's units.
//
// Threads. The host may change parameters from the audio thread (events in process) or
// from the main thread (loading state, or flushing while the plugin is not running).
// `values` is the truth both can touch; the Effect itself is only touched from the audio
// thread, or from the main thread while the plugin is inactive. A dial dragged in the
// plugin's window is a third way in: it sets `values` like the others, and is queued so
// that the next process() or flush() can tell the host, which is how the move gets
// recorded as automation.

#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <memory>
#include <string>
#include <vector>

#include <clap/clap.h>

#include "Effect.h"
#include "entry.h"
#include "gui.h"
#ifdef FX_RUNTIME
#include "locate.h"
#endif

namespace {

// The number of dials. Written as a call because the runtime only knows once it has read
// its device; for a generated Effect.h it is a constant.
int N() { return Effect::kNumParams; }
constexpr char kStateMagic[4] = {'P', 'P', 'F', 'X'};
constexpr uint32_t kStateVersion = 1;

bool curved(int i) { return Effect::kParams[i].exponent != 1.0 && !Effect::kParams[i].integer; }
double toHost(int i, double value) { return curved(i) ? mxrt::valueToPosition(Effect::kParams[i], value) : value; }
double fromHost(int i, double v) {
    return curved(i) ? mxrt::positionToValue(Effect::kParams[i], v) : mxrt::clampValue(Effect::kParams[i], v);
}
int indexOf(clap_id id) {
    for (int i = 0; i < N(); i++)
        if (Effect::kParams[i].id == id) return i;
    return -1;
}

// Something done to a dial in the plugin's window, waiting to be told to the host.
struct UiMove {
    enum Kind { kGrab, kSet, kRelease } kind;
    int index;
    double value;
};

struct Plugin : fxgui::Model {
    clap_plugin_t plugin;
    const clap_host_t* host = nullptr;
    const clap_host_params_t* hostParams = nullptr;
    fxgui::Window* window = nullptr;
    // Written by the window on the main thread, read by whichever thread next talks to
    // the host. One writer, one reader, no lock: a slot is filled before `written` moves.
    static constexpr uint32_t kMoves = 512;
    UiMove moves[kMoves];
    std::atomic<uint32_t> written{0}, told{0};
    Effect effect;
    std::unique_ptr<std::atomic<double>[]> values;  // in the dial's units
    std::unique_ptr<std::atomic<bool>[]> dirty;     // changed off the audio thread; process() applies it
    std::vector<float> spare32;          // somewhere to render a channel the host did not give us
    std::vector<double> spare64;

    explicit Plugin(const clap_host_t* h)
        : host(h), values(new std::atomic<double>[N() > 0 ? N() : 1]), dirty(new std::atomic<bool>[N() > 0 ? N() : 1]) {
        for (int i = 0; i < N(); i++) {
            values[i].store(Effect::kParams[i].initial);
            dirty[i].store(false);
        }
    }

    // ── the window's side (fxgui::Model), on the main thread ──

    int count() override { return N(); }
    double value(int index) override { return index >= 0 && index < N() ? values[index].load() : 0.0; }
    const char* face() override { return Effect::kFace; }
    void grab(int index) override { queue({UiMove::kGrab, index, 0.0}); }
    void release(int index) override { queue({UiMove::kRelease, index, 0.0}); }
    void set(int index, double v) override {
        if (index < 0 || index >= N()) return;
        const double clamped = mxrt::clampValue(Effect::kParams[index], v);
        values[index].store(clamped);
        dirty[index].store(true);
        queue({UiMove::kSet, index, clamped});
    }

    void queue(const UiMove& move) {
        if (move.index < 0 || move.index >= N()) return;
        const uint32_t at = written.load(std::memory_order_relaxed);
        if (at - told.load(std::memory_order_acquire) >= kMoves) return;  // the host is not listening; the value is set all the same
        moves[at % kMoves] = move;
        written.store(at + 1, std::memory_order_release);
        // Ask to be called, so the move reaches the host even while no audio is running.
        if (hostParams && hostParams->request_flush) hostParams->request_flush(host);
    }

    // Tell the host what was done in the window since it was last told.
    void tell(const clap_output_events_t* out) {
        uint32_t at = told.load(std::memory_order_relaxed);
        const uint32_t end = written.load(std::memory_order_acquire);
        for (; at != end; at++) {
            if (!out) continue;
            const UiMove& move = moves[at % kMoves];
            const clap_id id = Effect::kParams[move.index].id;
            if (move.kind == UiMove::kSet) {
                clap_event_param_value_t ev = {};
                ev.header = {sizeof ev, 0, CLAP_CORE_EVENT_SPACE_ID, CLAP_EVENT_PARAM_VALUE, 0};
                ev.param_id = id;
                ev.note_id = -1;
                ev.port_index = -1;
                ev.channel = -1;
                ev.key = -1;
                ev.value = toHost(move.index, move.value);
                out->try_push(out, &ev.header);
            } else {
                clap_event_param_gesture_t ev = {};
                const uint16_t type = move.kind == UiMove::kGrab ? CLAP_EVENT_PARAM_GESTURE_BEGIN : CLAP_EVENT_PARAM_GESTURE_END;
                ev.header = {sizeof ev, 0, CLAP_CORE_EVENT_SPACE_ID, type, 0};
                ev.param_id = id;
                out->try_push(out, &ev.header);
            }
        }
        told.store(at, std::memory_order_release);
    }

    // A key going down or coming up. Only while audio is running: a note has no meaning
    // to an instrument that is not sounding.
    void note(const clap_event_header_t* header) {
        if (header->type == CLAP_EVENT_NOTE_ON || header->type == CLAP_EVENT_NOTE_OFF ||
            header->type == CLAP_EVENT_NOTE_CHOKE) {
            const auto* ev = reinterpret_cast<const clap_event_note_t*>(header);
            if (ev->key < 0 || ev->key > 127) return;
            if (header->type == CLAP_EVENT_NOTE_ON) effect.noteOn(ev->key, ev->velocity * 127.0);
            else effect.noteOff(ev->key);
        } else if (header->type == CLAP_EVENT_MIDI) {
            const auto* ev = reinterpret_cast<const clap_event_midi_t*>(header);
            const int status = ev->data[0] & 0xF0, key = ev->data[1] & 0x7F, velocity = ev->data[2] & 0x7F;
            if (status == 0x90 && velocity > 0) effect.noteOn(key, velocity);
            else if (status == 0x80 || status == 0x90) effect.noteOff(key);
        }
    }

    // An event, wherever it arrived. `live` means the audio thread owns the Effect now.
    void event(const clap_event_header_t* header, bool live) {
        if (header->space_id != CLAP_CORE_EVENT_SPACE_ID) return;
        if (header->type != CLAP_EVENT_PARAM_VALUE) {
            if (live && Effect::kIsInstrument) note(header);
            return;
        }
        const auto* ev = reinterpret_cast<const clap_event_param_value_t*>(header);
        const int i = indexOf(ev->param_id);
        if (i < 0) return;
        const double value = fromHost(i, ev->value);
        values[i].store(value);
        if (live) effect.setParam(i, value);
        else dirty[i].store(true);
    }

    void applyDirty() {
        for (int i = 0; i < N(); i++)
            if (dirty[i].exchange(false)) effect.setParam(i, values[i].load());
    }

    template <typename Sample>
    void render(Sample** in, uint32_t inChannels, Sample** out, uint32_t outChannels, std::vector<Sample>& spare,
                uint32_t from, uint32_t to) {
        const bool fed = Effect::kIsInstrument || (in && inChannels > 0);
        if (to <= from || !fed || outChannels == 0) return;
        const uint32_t frames = to - from;
        const bool room = spare.size() >= frames;
        Sample* outs[2] = {out[0] + from, outChannels > 1 ? out[1] + from : spare.data()};
        if (outChannels < 2 && !room) return;  // more frames than the host said it would send
        if (Effect::kIsInstrument) {
            effect.process(static_cast<const Sample* const*>(nullptr), outs, static_cast<int>(frames));
            return;
        }
        const Sample* ins[2] = {in[0] + from, (inChannels > 1 ? in[1] : in[0]) + from};
        effect.process(ins, outs, static_cast<int>(frames));
    }
};

Plugin* self(const clap_plugin_t* plugin) { return static_cast<Plugin*>(plugin->plugin_data); }

// ── the plugin ──────────────────────────────────────────────────────────────────

bool pluginInit(const clap_plugin_t* plugin) {
    Plugin* p = self(plugin);
    p->hostParams = static_cast<const clap_host_params_t*>(p->host->get_extension(p->host, CLAP_EXT_PARAMS));
    return true;
}

void pluginDestroy(const clap_plugin_t* plugin) {
    Plugin* p = self(plugin);
    fxgui::destroy(p->window);
    delete p;
}

bool pluginActivate(const clap_plugin_t* plugin, double sampleRate, uint32_t, uint32_t maxFrames) {
    Plugin* p = self(plugin);
    for (int i = 0; i < N(); i++) {
        p->dirty[i].store(false);
        p->effect.setParam(i, p->values[i].load());
    }
    p->effect.prepare(sampleRate);
    p->spare32.assign(maxFrames, 0.0f);
    p->spare64.assign(maxFrames, 0.0);
    return true;
}

void pluginDeactivate(const clap_plugin_t*) {}
bool pluginStartProcessing(const clap_plugin_t*) { return true; }
void pluginStopProcessing(const clap_plugin_t*) {}
void pluginReset(const clap_plugin_t* plugin) { self(plugin)->effect.reset(); }
void pluginOnMainThread(const clap_plugin_t*) {}

clap_process_status pluginProcess(const clap_plugin_t* plugin, const clap_process_t* process) {
    Plugin* p = self(plugin);
    mxrt::ScopedNoDenormals quiet;
    p->applyDirty();
    p->tell(process->out_events);

    const uint32_t frames = process->frames_count;
    const clap_input_events_t* events = process->in_events;
    const uint32_t count = events ? events->size(events) : 0;
    const clap_audio_buffer_t* in = process->audio_inputs_count > 0 ? &process->audio_inputs[0] : nullptr;
    clap_audio_buffer_t* out = process->audio_outputs_count > 0 ? &process->audio_outputs[0] : nullptr;
    const bool wired = out && (in || Effect::kIsInstrument);

    // Render up to each event, apply it, carry on: a parameter change lands on its sample.
    uint32_t next = 0, at = 0;
    for (;;) {
        uint32_t until = frames;
        while (next < count) {
            const clap_event_header_t* header = events->get(events, next);
            if (header->time > at) { until = header->time < frames ? header->time : frames; break; }
            p->event(header, true);
            next++;
        }
        if (at >= frames) break;
        if (wired) {
            // An instrument has no input buffer to say which width the host is using.
            const bool narrow = out->data32 && (!in || in->data32);
            const bool wide = out->data64 && (!in || in->data64);
            const uint32_t channels = in ? in->channel_count : 0;
            if (narrow)
                p->render(in ? in->data32 : nullptr, channels, out->data32, out->channel_count, p->spare32, at, until);
            else if (wide)
                p->render(in ? in->data64 : nullptr, channels, out->data64, out->channel_count, p->spare64, at, until);
        }
        at = until;
    }
    for (; next < count; next++) p->event(events->get(events, next), true);

    if (out) out->constant_mask = 0;
    return Effect::kHasTail ? CLAP_PROCESS_CONTINUE : CLAP_PROCESS_CONTINUE_IF_NOT_QUIET;
}

// ── audio ports: a stereo output, and for an effect a stereo input ──────────────

uint32_t portsCount(const clap_plugin_t*, bool isInput) { return isInput && Effect::kIsInstrument ? 0 : 1; }

bool portsGet(const clap_plugin_t* plugin, uint32_t index, bool isInput, clap_audio_port_info_t* info) {
    if (index >= portsCount(plugin, isInput)) return false;
    std::memset(info, 0, sizeof *info);
    info->id = 0;
    std::strncpy(info->name, isInput ? "Input" : "Output", sizeof info->name - 1);
    info->flags = CLAP_AUDIO_PORT_IS_MAIN | CLAP_AUDIO_PORT_SUPPORTS_64BITS;
    info->channel_count = 2;
    info->port_type = CLAP_PORT_STEREO;
    // An effect reads a frame before it writes it, so the host may use one buffer for both.
    info->in_place_pair = Effect::kIsInstrument ? CLAP_INVALID_ID : 0;
    return true;
}

const clap_plugin_audio_ports_t kAudioPorts = {portsCount, portsGet};

// ── note port: an instrument's way in ───────────────────────────────────────────

uint32_t notePortsCount(const clap_plugin_t*, bool isInput) { return isInput && Effect::kIsInstrument ? 1 : 0; }

bool notePortsGet(const clap_plugin_t* plugin, uint32_t index, bool isInput, clap_note_port_info_t* info) {
    if (index >= notePortsCount(plugin, isInput)) return false;
    std::memset(info, 0, sizeof *info);
    info->id = 0;
    info->supported_dialects = CLAP_NOTE_DIALECT_CLAP | CLAP_NOTE_DIALECT_MIDI;
    info->preferred_dialect = CLAP_NOTE_DIALECT_CLAP;
    std::strncpy(info->name, "Notes", sizeof info->name - 1);
    return true;
}

const clap_plugin_note_ports_t kNotePorts = {notePortsCount, notePortsGet};

// ── parameters ──────────────────────────────────────────────────────────────────

uint32_t paramsCount(const clap_plugin_t*) { return N(); }

bool paramsInfo(const clap_plugin_t*, uint32_t index, clap_param_info_t* info) {
    if (index >= static_cast<uint32_t>(N())) return false;
    const int i = static_cast<int>(index);
    const mxrt::ParamInfo& p = Effect::kParams[i];
    std::memset(info, 0, sizeof *info);
    info->id = p.id;
    info->flags = CLAP_PARAM_IS_AUTOMATABLE | (p.integer ? CLAP_PARAM_IS_STEPPED : 0);
    std::strncpy(info->name, p.name, sizeof info->name - 1);
    const double lo = toHost(i, p.min), hi = toHost(i, p.max);
    info->min_value = lo < hi ? lo : hi;
    info->max_value = lo < hi ? hi : lo;
    info->default_value = toHost(i, p.initial);
    return true;
}

bool paramsValue(const clap_plugin_t* plugin, clap_id id, double* value) {
    const int i = indexOf(id);
    if (i < 0) return false;
    *value = toHost(i, self(plugin)->values[i].load());
    return true;
}

bool paramsToText(const clap_plugin_t*, clap_id id, double value, char* out, uint32_t size) {
    const int i = indexOf(id);
    if (i < 0 || size == 0) return false;
    mxrt::formatValue(Effect::kParams[i], fromHost(i, value), out, size);
    return true;
}

bool paramsFromText(const clap_plugin_t*, clap_id id, const char* text, double* value) {
    const int i = indexOf(id);
    double parsed = 0;
    if (i < 0 || !mxrt::parseValue(Effect::kParams[i], text, &parsed)) return false;
    *value = toHost(i, parsed);
    return true;
}

// Called instead of process() when there is no audio running; the next process() or
// activate() hands the values to the Effect.
void paramsFlush(const clap_plugin_t* plugin, const clap_input_events_t* in, const clap_output_events_t* out) {
    Plugin* p = self(plugin);
    const uint32_t count = in ? in->size(in) : 0;
    for (uint32_t k = 0; k < count; k++) p->event(in->get(in, k), false);
    p->tell(out);
}

const clap_plugin_params_t kParams = {paramsCount, paramsInfo, paramsValue, paramsToText, paramsFromText, paramsFlush};

// ── state: "PPFX", a version, a count, then (parameter id, value) pairs ─────────

bool writeAll(const clap_ostream_t* stream, const void* data, size_t size) {
    const char* at = static_cast<const char*>(data);
    while (size > 0) {
        const int64_t n = stream->write(stream, at, size);
        if (n <= 0) return false;
        at += n;
        size -= static_cast<size_t>(n);
    }
    return true;
}

bool readAll(const clap_istream_t* stream, void* data, size_t size) {
    char* at = static_cast<char*>(data);
    while (size > 0) {
        const int64_t n = stream->read(stream, at, size);
        if (n <= 0) return false;
        at += n;
        size -= static_cast<size_t>(n);
    }
    return true;
}

bool stateSave(const clap_plugin_t* plugin, const clap_ostream_t* stream) {
    Plugin* p = self(plugin);
    const uint32_t count = N();
    if (!writeAll(stream, kStateMagic, 4) || !writeAll(stream, &kStateVersion, 4) || !writeAll(stream, &count, 4))
        return false;
    for (int i = 0; i < N(); i++) {
        const uint32_t id = Effect::kParams[i].id;
        const double value = p->values[i].load();
        if (!writeAll(stream, &id, 4) || !writeAll(stream, &value, 8)) return false;
    }
    return true;
}

// A parameter the state does not mention keeps its value; one this build does not have
// is skipped. So a state saved by an older or newer chain still loads what it can. The
// host is told to read the values again, since they changed without it asking.
bool stateLoad(const clap_plugin_t* plugin, const clap_istream_t* stream) {
    Plugin* p = self(plugin);
    char magic[4];
    uint32_t version = 0, count = 0;
    if (!readAll(stream, magic, 4) || std::memcmp(magic, kStateMagic, 4) != 0) return false;
    if (!readAll(stream, &version, 4) || version != kStateVersion) return false;
    if (!readAll(stream, &count, 4) || count > 4096) return false;
    bool changed = false;
    for (uint32_t k = 0; k < count; k++) {
        uint32_t id = 0;
        double value = 0;
        if (!readAll(stream, &id, 4) || !readAll(stream, &value, 8)) return false;
        const int i = indexOf(id);
        if (i < 0) continue;
        value = mxrt::clampValue(Effect::kParams[i], value);
        if (p->values[i].exchange(value) != value) changed = true;
        p->dirty[i].store(true);
    }
    if (changed && p->hostParams && p->hostParams->rescan) p->hostParams->rescan(p->host, CLAP_PARAM_RESCAN_VALUES);
    return true;
}

const clap_plugin_state_t kState = {stateSave, stateLoad};

// ── tail ────────────────────────────────────────────────────────────────────────

// A feedback delay or a reverb has no fixed length, so an effect with any memory in it
// asks the host to keep running it.
uint32_t tailGet(const clap_plugin_t*) { return Effect::kHasTail ? UINT32_MAX : 0; }

const clap_plugin_tail_t kTail = {tailGet};

// ── the window ──────────────────────────────────────────────────────────────────

// A window only where one is written (gui.h) and only for a device that says what it
// looks like. Otherwise the host shows its own controls.
bool hasWindow() { return fxgui::supported() && Effect::kFace && Effect::kFace[0] != 0; }

bool guiIsApiSupported(const clap_plugin_t*, const char* api, bool floating) {
    return hasWindow() && !floating && std::strcmp(api, CLAP_WINDOW_API_COCOA) == 0;
}

bool guiGetPreferredApi(const clap_plugin_t*, const char** api, bool* floating) {
    *api = CLAP_WINDOW_API_COCOA;
    *floating = false;
    return hasWindow();
}

bool guiCreate(const clap_plugin_t* plugin, const char* api, bool floating) {
    Plugin* p = self(plugin);
    if (!guiIsApiSupported(plugin, api, floating)) return false;
    if (!p->window) p->window = fxgui::create(p);
    return p->window != nullptr;
}

void guiDestroy(const clap_plugin_t* plugin) {
    Plugin* p = self(plugin);
    fxgui::destroy(p->window);
    p->window = nullptr;
}

bool guiSetScale(const clap_plugin_t*, double) { return false; }  // macOS scales windows itself

bool guiGetSize(const clap_plugin_t* plugin, uint32_t* width, uint32_t* height) {
    Plugin* p = self(plugin);
    if (!p->window) return false;
    fxgui::size(p->window, width, height);
    return true;
}

// One size: the device's, as its face lays it out.
bool guiCanResize(const clap_plugin_t*) { return false; }
bool guiGetResizeHints(const clap_plugin_t*, clap_gui_resize_hints_t*) { return false; }
bool guiAdjustSize(const clap_plugin_t* plugin, uint32_t* width, uint32_t* height) {
    return guiGetSize(plugin, width, height);
}
bool guiSetSize(const clap_plugin_t* plugin, uint32_t width, uint32_t height) {
    uint32_t w = 0, h = 0;
    return guiGetSize(plugin, &w, &h) && w == width && h == height;
}

bool guiSetParent(const clap_plugin_t* plugin, const clap_window_t* window) {
    return fxgui::attach(self(plugin)->window, window ? window->cocoa : nullptr);
}

bool guiSetTransient(const clap_plugin_t*, const clap_window_t*) { return false; }
void guiSuggestTitle(const clap_plugin_t*, const char*) {}

bool guiShow(const clap_plugin_t* plugin) {
    fxgui::show(self(plugin)->window, true);
    return self(plugin)->window != nullptr;
}

bool guiHide(const clap_plugin_t* plugin) {
    fxgui::show(self(plugin)->window, false);
    return self(plugin)->window != nullptr;
}

const clap_plugin_gui_t kGui = {guiIsApiSupported, guiGetPreferredApi, guiCreate, guiDestroy, guiSetScale,
                                guiGetSize, guiCanResize, guiGetResizeHints, guiAdjustSize, guiSetSize,
                                guiSetParent, guiSetTransient, guiSuggestTitle, guiShow, guiHide};

const void* pluginExtension(const clap_plugin_t*, const char* id) {
    if (std::strcmp(id, CLAP_EXT_GUI) == 0 && hasWindow()) return &kGui;
    if (std::strcmp(id, CLAP_EXT_AUDIO_PORTS) == 0) return &kAudioPorts;
    if (std::strcmp(id, CLAP_EXT_NOTE_PORTS) == 0 && Effect::kIsInstrument) return &kNotePorts;
    if (std::strcmp(id, CLAP_EXT_PARAMS) == 0) return &kParams;
    if (std::strcmp(id, CLAP_EXT_STATE) == 0) return &kState;
    if (std::strcmp(id, CLAP_EXT_TAIL) == 0) return &kTail;
    return nullptr;
}

// ── descriptor and factory ──────────────────────────────────────────────────────

const char* const kEffectFeatures[] = {CLAP_PLUGIN_FEATURE_AUDIO_EFFECT, CLAP_PLUGIN_FEATURE_STEREO, nullptr};
const char* const kInstrumentFeatures[] = {CLAP_PLUGIN_FEATURE_INSTRUMENT, CLAP_PLUGIN_FEATURE_SYNTHESIZER,
                                           CLAP_PLUGIN_FEATURE_STEREO, nullptr};

const clap_plugin_descriptor_t* descriptor() {
    static const std::string id = std::string("com.plugin-playground.") + Effect::kSlug;
    static const clap_plugin_descriptor_t desc = {
        CLAP_VERSION_INIT,
        id.c_str(),
        Effect::kName,
        "Plugin Playground",
        "https://github.com/chrishyoroklee/plugin-playground",
        "",
        "",
        FX_VERSION,
        Effect::kIsInstrument ? "An instrument made with Plugin Playground, from a Max patch."
                              : "An audio effect made with Plugin Playground, from a Max patch.",
        Effect::kIsInstrument ? kInstrumentFeatures : kEffectFeatures,
    };
    return &desc;
}

// A runtime plugin whose device could not be read is no plugin at all.
#ifdef FX_RUNTIME
bool ready() { return Effect::loaded(); }
#else
bool ready() { return true; }
#endif

uint32_t factoryCount(const clap_plugin_factory_t*) { return ready() ? 1 : 0; }

const clap_plugin_descriptor_t* factoryDescriptor(const clap_plugin_factory_t*, uint32_t index) {
    return index == 0 && ready() ? descriptor() : nullptr;
}

const clap_plugin_t* factoryCreate(const clap_plugin_factory_t*, const clap_host_t* host, const char* id) {
    if (!ready() || !host || !clap_version_is_compatible(host->clap_version) || std::strcmp(id, descriptor()->id) != 0)
        return nullptr;
    Plugin* p = new Plugin(host);
    p->plugin.desc = descriptor();
    p->plugin.plugin_data = p;
    p->plugin.init = pluginInit;
    p->plugin.destroy = pluginDestroy;
    p->plugin.activate = pluginActivate;
    p->plugin.deactivate = pluginDeactivate;
    p->plugin.start_processing = pluginStartProcessing;
    p->plugin.stop_processing = pluginStopProcessing;
    p->plugin.reset = pluginReset;
    p->plugin.process = pluginProcess;
    p->plugin.get_extension = pluginExtension;
    p->plugin.on_main_thread = pluginOnMainThread;
    return &p->plugin;
}

const clap_plugin_factory_t kFactory = {factoryCount, factoryDescriptor, factoryCreate};

}  // namespace

extern "C" {

#ifdef FX_RUNTIME
// The device is the file the helper put beside this plugin when it made it. The path a
// host passes is not used: through the VST3 and Audio Unit wrappers it is not always the
// plugin's own, and the plugin can find itself. A host may call this more than once.
bool fx_entry_init(const char*) {
    if (Effect::loaded()) return true;
    std::string why;
    const std::string file = fxlocate::deviceFile();
    if (file.empty() || !Effect::loadFile(file, &why)) {
        std::fprintf(stderr, "Plugin Playground: %s\n", file.empty() ? "this plugin has no device file" : why.c_str());
        return false;
    }
    return true;
}
#else
bool fx_entry_init(const char*) { return true; }
#endif

void fx_entry_deinit(void) {}

const void* fx_entry_get_factory(const char* factory_id) {
    return std::strcmp(factory_id, CLAP_PLUGIN_FACTORY_ID) == 0 ? &kFactory : nullptr;
}

}  // extern "C"
