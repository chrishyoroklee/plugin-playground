// render: play a WAV file through a compiled effect, as fast as the machine can go.
//
// This is how the C++ is checked. scripts/maxcheck/ renders one test signal through the
// same generated patches in Max and in the browser engine; this is the third renderer,
// and its output goes through the same compare.py.
//
//   render in.wav out.wav [--float] [--block N] [--as-chrome] [--set INDEX=VALUE ...]
//   render --params            the parameter table, as JSON
//
// The input is 16-bit PCM, mono or stereo. The output is 16-bit stereo, the format the
// Max side records, or 32-bit float with --float for sample-level comparison.
// A 16-bit sample is divided by 32768 on the way in. --as-chrome divides the positive
// ones by 32767 instead, which is what Chrome's decodeAudioData does, so that the
// browser engine and this can be given the very same numbers.
//
// Built once per effect: the Effect.h to wrap is found on the include path.

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "Effect.h"

namespace {

struct Wav {
    int sampleRate = 44100;
    std::vector<float> left, right;
};

uint32_t u32(const unsigned char* p) { return p[0] | (p[1] << 8) | (p[2] << 16) | (static_cast<uint32_t>(p[3]) << 24); }
uint16_t u16(const unsigned char* p) { return static_cast<uint16_t>(p[0] | (p[1] << 8)); }

bool readWav(const char* path, Wav& wav, bool asChrome, std::string& error) {
    FILE* f = std::fopen(path, "rb");
    if (!f) { error = std::string("cannot open ") + path; return false; }
    std::vector<unsigned char> data;
    unsigned char chunk[65536];
    size_t n;
    while ((n = std::fread(chunk, 1, sizeof chunk, f)) > 0) data.insert(data.end(), chunk, chunk + n);
    std::fclose(f);
    if (data.size() < 12 || std::memcmp(data.data(), "RIFF", 4) != 0 || std::memcmp(data.data() + 8, "WAVE", 4) != 0) {
        error = "not a WAV file";
        return false;
    }
    int channels = 0, bits = 0, format = 0;
    size_t at = 12;
    while (at + 8 <= data.size()) {
        const uint32_t size = u32(&data[at + 4]);
        const unsigned char* body = &data[at + 8];
        if (at + 8 + size > data.size()) break;
        if (std::memcmp(&data[at], "fmt ", 4) == 0 && size >= 16) {
            format = u16(body);
            channels = u16(body + 2);
            wav.sampleRate = static_cast<int>(u32(body + 4));
            bits = u16(body + 14);
        } else if (std::memcmp(&data[at], "data", 4) == 0) {
            if (format != 1 || bits != 16 || (channels != 1 && channels != 2)) {
                error = "the input should be 16-bit PCM, mono or stereo";
                return false;
            }
            const size_t frames = size / (2 * channels);
            wav.left.resize(frames);
            wav.right.resize(frames);
            for (size_t i = 0; i < frames; i++) {
                const unsigned char* p = body + i * 2 * channels;
                const auto sample = [asChrome](const unsigned char* at) {
                    const int16_t v = static_cast<int16_t>(u16(at));
                    return v / (asChrome && v > 0 ? 32767.0f : 32768.0f);
                };
                wav.left[i] = sample(p);
                wav.right[i] = channels == 2 ? sample(p + 2) : wav.left[i];
            }
            return true;
        }
        at += 8 + size + (size & 1);
    }
    error = "the WAV file has no audio in it";
    return false;
}

void put32(FILE* f, uint32_t v) { unsigned char b[4] = {static_cast<unsigned char>(v), static_cast<unsigned char>(v >> 8), static_cast<unsigned char>(v >> 16), static_cast<unsigned char>(v >> 24)}; std::fwrite(b, 1, 4, f); }
void put16(FILE* f, uint16_t v) { unsigned char b[2] = {static_cast<unsigned char>(v), static_cast<unsigned char>(v >> 8)}; std::fwrite(b, 1, 2, f); }

bool writeWav(const char* path, const Wav& wav, bool asFloat) {
    FILE* f = std::fopen(path, "wb");
    if (!f) return false;
    const uint32_t frames = static_cast<uint32_t>(wav.left.size());
    const uint16_t bytes = asFloat ? 4 : 2;
    std::fwrite("RIFF", 1, 4, f); put32(f, 36 + frames * 2 * bytes); std::fwrite("WAVE", 1, 4, f);
    std::fwrite("fmt ", 1, 4, f); put32(f, 16); put16(f, asFloat ? 3 : 1); put16(f, 2);
    put32(f, static_cast<uint32_t>(wav.sampleRate)); put32(f, static_cast<uint32_t>(wav.sampleRate) * 2 * bytes);
    put16(f, static_cast<uint16_t>(2 * bytes)); put16(f, static_cast<uint16_t>(8 * bytes));
    std::fwrite("data", 1, 4, f); put32(f, frames * 2 * bytes);
    for (uint32_t i = 0; i < frames; i++) {
        for (float v : {wav.left[i], wav.right[i]}) {
            if (asFloat) {
                uint32_t raw;
                std::memcpy(&raw, &v, 4);
                put32(f, raw);
            } else {
                // The same rounding as the browser's encodeWav (src/plugin/offline.ts).
                const float c = v < -1.0f ? -1.0f : v > 1.0f ? 1.0f : v;
                put16(f, static_cast<uint16_t>(static_cast<int16_t>(std::lround(c * 32767.0f))));
            }
        }
    }
    return std::fclose(f) == 0;
}

void printParams() {
    std::printf("{\"name\": \"%s\", \"hasTail\": %s, \"params\": [", Effect::kName, Effect::kHasTail ? "true" : "false");
    for (int i = 0; i < Effect::kNumParams; i++) {
        const mxrt::ParamInfo& p = Effect::kParams[i];
        char text[64];
        mxrt::formatValue(p, p.initial, text, sizeof text);
        std::printf("%s\n  {\"index\": %d, \"id\": %u, \"name\": \"%s\", \"short\": \"%s\", \"min\": %.17g, \"max\": %.17g, "
                    "\"initial\": %.17g, \"exponent\": %.17g, \"unit\": %d, \"integer\": %s, \"text\": \"%s\"}",
                    i ? "," : "", i, p.id, p.name, p.shortName, p.min, p.max, p.initial, p.exponent, p.unit,
                    p.integer ? "true" : "false", text);
    }
    std::printf("\n]}\n");
}

int usage() {
    std::fprintf(stderr, "usage: render in.wav out.wav [--float] [--block N] [--as-chrome] [--set INDEX=VALUE ...]\n"
                         "       render --params\n");
    return 2;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::strcmp(argv[1], "--params") == 0) { printParams(); return 0; }
    if (argc < 3) return usage();

    bool asFloat = false;
    int block = 64;
    bool asChrome = false;
    Effect effect;
    for (int i = 3; i < argc; i++) {
        if (std::strcmp(argv[i], "--float") == 0) asFloat = true;
        else if (std::strcmp(argv[i], "--block") == 0 && i + 1 < argc) block = std::atoi(argv[++i]);
        else if (std::strcmp(argv[i], "--as-chrome") == 0) asChrome = true;
        else if (std::strcmp(argv[i], "--set") == 0 && i + 1 < argc) {
            const char* eq = std::strchr(argv[++i], '=');
            const int index = std::atoi(argv[i]);
            if (!eq || index < 0 || index >= Effect::kNumParams) {
                std::fprintf(stderr, "render: --set takes INDEX=VALUE with INDEX below %d\n", Effect::kNumParams);
                return 2;
            }
            effect.setParam(index, std::atof(eq + 1));
        } else return usage();
    }
    if (block < 1) block = 1;

    Wav in;
    std::string error;
    if (!readWav(argv[1], in, asChrome, error)) { std::fprintf(stderr, "render: %s\n", error.c_str()); return 1; }

    Wav out;
    out.sampleRate = in.sampleRate;
    out.left.resize(in.left.size());
    out.right.resize(in.right.size());

    effect.prepare(in.sampleRate);
    mxrt::ScopedNoDenormals quiet;
    const int frames = static_cast<int>(in.left.size());
    for (int at = 0; at < frames; at += block) {
        const int n = frames - at < block ? frames - at : block;
        const float* ins[2] = {in.left.data() + at, in.right.data() + at};
        float* outs[2] = {out.left.data() + at, out.right.data() + at};
        effect.process(ins, outs, n);
    }

    if (!writeWav(argv[2], out, asFloat)) { std::fprintf(stderr, "render: cannot write %s\n", argv[2]); return 1; }
    return 0;
}
