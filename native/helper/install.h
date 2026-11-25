// Turning a device file into plugins: what the helper does when a .ppdevice is opened.
//
// The runtime plugin is compiled once and is no device in particular. A device becomes a
// plugin of its own by copying the runtime under the device's name and putting the
// device file inside the copy, where the runtime looks for it (runtime/locate.h). On
// macOS the copy's Info.plist is written again, because that is where an Audio Unit's
// name and code live. Nothing is compiled, so this takes a moment and needs no tools.
//
// The plugins go where every host scans and where no administrator is needed:
//
//   macOS    ~/Library/Audio/Plug-Ins/CLAP, VST3 and Components
//   Windows  C:\Program Files\Common Files\CLAP\Plugin Playground, and the same under VST3
//   Linux    ~/.clap and ~/.vst3
//
// Windows has no per-user plugin folder that hosts agree on, so the installer makes those
// two "Plugin Playground" folders and lets every user write to them; hosts look inside
// folders they scan.
//
// The runtime folder, as the installer lays it out (scripts/plugin.py, build_runtime):
//
//   Plugin Playground Runtime.clap
//   Plugin Playground Runtime.vst3
//   effect/Plugin Playground Runtime.component        macOS: an Audio Unit says in its
//   instrument/Plugin Playground Runtime.component    code whether it takes notes
//
// Used by the helper on each system (mac.mm, windows.cpp, cli.cpp).

#pragma once

#include <cctype>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#if !defined(_WIN32)
#include <fcntl.h>
#include <spawn.h>
#include <sys/wait.h>
extern char** environ;
#endif

#include "mxvm.h"

namespace ppinstall {

namespace fs = std::filesystem;

constexpr const char* kRuntimeName = "Plugin Playground Runtime";
constexpr const char* kVersion = "0.1.0";

struct Folders {
    fs::path clap, vst3, au;  // au is only used on macOS
};

struct Result {
    bool ok = false;
    std::string name;                // the device's name, as its file says
    std::string message;             // why not, when !ok
    std::vector<fs::path> made;      // the plugins written
};

// This user's plugin folders.
inline Folders userFolders() {
    Folders f;
#if defined(_WIN32)
    // The 64-bit Common Files, whichever kind of program asks.
    const wchar_t* common = _wgetenv(L"CommonProgramW6432");
    if (!common || !*common) common = _wgetenv(L"CommonProgramFiles");
    const fs::path base = common && *common ? fs::path(common) : fs::path(L"C:\\Program Files\\Common Files");
    f.clap = base / "CLAP" / "Plugin Playground";
    f.vst3 = base / "VST3" / "Plugin Playground";
#elif defined(__APPLE__)
    const char* home = std::getenv("HOME");
    const fs::path base = fs::path(home ? home : ".") / "Library" / "Audio" / "Plug-Ins";
    f.clap = base / "CLAP";
    f.vst3 = base / "VST3";
    f.au = base / "Components";
#else
    const char* home = std::getenv("HOME");
    f.clap = fs::path(home ? home : ".") / ".clap";
    f.vst3 = fs::path(home ? home : ".") / ".vst3";
#endif
    return f;
}

// The same three folders under one of your choosing, for trying an install out.
inline Folders foldersUnder(const fs::path& root) { return {root / "CLAP", root / "VST3", root / "Components"}; }

namespace detail {

// A device's name as a file name: nothing a file system or a shell minds.
inline std::string fileName(const std::string& name) {
    std::string out;
    for (unsigned char c : name) {
        const bool bad = c < 32 || c == 127 || std::strchr("/\\:*?\"<>|", c) != nullptr;
        out.push_back(bad ? '-' : static_cast<char>(c));
    }
    while (!out.empty() && (out.back() == ' ' || out.back() == '.')) out.pop_back();
    while (!out.empty() && out.front() == ' ') out.erase(out.begin());
    if (out.empty()) return "Device";
    // Names Windows keeps for devices (CON, NUL, COM1...), whatever follows a dot: a file
    // called that cannot be made there. The same name is used on every system.
    std::string stem;
    for (char c : out.substr(0, out.find('.'))) stem.push_back(static_cast<char>(std::toupper(static_cast<unsigned char>(c))));
    while (!stem.empty() && stem.back() == ' ') stem.pop_back();
    static const char* reserved[] = {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6",
                                     "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6",
                                     "LPT7", "LPT8", "LPT9"};
    for (const char* r : reserved)
        if (stem == r) return out + "-";
    return out;
}

// Whether `to` is a plugin this helper made, which it may replace. Everything it makes
// carries its device: inside a bundle, or beside a .clap that is one file. Anything else
// with that name (another maker's plugin that happens to share it) is never touched.
inline bool ours(const fs::path& to) {
    std::error_code ec;
    const fs::file_status s = fs::symlink_status(to, ec);
    if (ec || !fs::exists(s)) return true;  // nothing there
    if (fs::is_symlink(s)) return false;
    if (fs::is_directory(s)) {
        if (fs::is_regular_file(to / "Contents" / "Resources" / "device.ppdevice", ec)) return true;
        // A plugin compiled from a device (scripts/plugin.py, native/CMakeLists.txt) has
        // no device in it, but is ours by its identifier.
        std::ifstream plist(to / "Contents" / "Info.plist", std::ios::binary);
        std::ostringstream text;
        text << plist.rdbuf();
        return text.str().find("<string>com.plugin-playground.") != std::string::npos;
    }
    return fs::is_regular_file(fs::path(to).replace_extension(".ppdevice"), ec);
}

inline std::string xml(const std::string& text) {
    std::string out;
    for (char c : text) {
        if (c == '&') out += "&amp;";
        else if (c == '<') out += "&lt;";
        else if (c == '>') out += "&gt;";
        else out.push_back(c);
    }
    return out;
}

inline uint32_t fnv1a(const std::string& text) {
    uint32_t h = 0x811C9DC5u;
    for (unsigned char c : text) h = (h ^ c) * 0x01000193u;
    return h;
}

// Four characters that tell one device's Audio Unit from another's. The same code
// scripts/plugin.py gives a compiled plugin of that name (au_subtype), so a device keeps
// its identity whichever way it was built.
inline std::string auSubtype(const std::string& slug) {
    static const char letters[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    uint32_t h = fnv1a(slug);
    std::string code(1, letters[h % 26]);
    h /= 26;
    for (int i = 0; i < 3; i++) {
        code.push_back(letters[h % 62]);
        h /= 62;
    }
    return code;
}

inline bool writeText(const fs::path& path, const std::string& text) {
    std::ofstream file(path, std::ios::binary | std::ios::trunc);
    file << text;
    return static_cast<bool>(file.flush());
}

// Run a tool that may not be there, and do not mind how it goes.
inline void tryRun(const std::vector<std::string>& words) {
#if !defined(_WIN32)
    std::vector<char*> argv;
    for (const std::string& w : words) argv.push_back(const_cast<char*>(w.c_str()));
    argv.push_back(nullptr);
    posix_spawn_file_actions_t quiet;
    posix_spawn_file_actions_init(&quiet);
    posix_spawn_file_actions_addopen(&quiet, 1, "/dev/null", O_WRONLY, 0);
    posix_spawn_file_actions_addopen(&quiet, 2, "/dev/null", O_WRONLY, 0);
    pid_t pid = 0;
    if (posix_spawn(&pid, argv[0], &quiet, nullptr, argv.data(), environ) == 0) {
        int status = 0;
        waitpid(pid, &status, 0);
    }
    posix_spawn_file_actions_destroy(&quiet);
#else
    (void)words;
#endif
}

// A macOS bundle's Info.plist. `format` is clap, vst3 or auv2; only the Audio Unit has
// the last part, which is what a host lists it by.
inline std::string infoPlist(const std::string& name, const std::string& slug, const std::string& format,
                             bool instrument) {
    std::ostringstream p;
    p << "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
         "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n"
         "<plist version=\"1.0\">\n<dict>\n"
         "\t<key>CFBundleDevelopmentRegion</key>\n\t<string>English</string>\n"
         "\t<key>CFBundleExecutable</key>\n\t<string>" << kRuntimeName << "</string>\n"
         "\t<key>CFBundleIdentifier</key>\n\t<string>com.plugin-playground." << slug << "." << format << "</string>\n"
         "\t<key>CFBundleInfoDictionaryVersion</key>\n\t<string>6.0</string>\n"
         "\t<key>CFBundleName</key>\n\t<string>" << xml(name) << "</string>\n"
         "\t<key>CFBundlePackageType</key>\n\t<string>BNDL</string>\n"
         "\t<key>CFBundleShortVersionString</key>\n\t<string>" << kVersion << "</string>\n"
         "\t<key>CFBundleSignature</key>\n\t<string>\?\?\?\?</string>\n"
         "\t<key>CFBundleVersion</key>\n\t<string>" << kVersion << "</string>\n"
         "\t<key>CSResourcesFileMapped</key>\n\t<true/>\n"
         "\t<key>NSHighResolutionCapable</key>\n\t<true/>\n";
    if (format == "auv2") {
        p << "\t<key>NSPrincipalClass</key>\n\t<string/>\n"
             "\t<key>AudioComponents</key>\n\t<array>\n\t\t<dict>\n"
             "\t\t\t<key>name</key>\n\t\t\t<string>Plugin Playground: " << xml(name) << "</string>\n"
             "\t\t\t<key>description</key>\n\t\t\t<string>" << xml(name) << ", made with Plugin Playground</string>\n"
             "\t\t\t<key>factoryFunction</key>\n\t\t\t<string>wrapAsAUV2_inst0Factory</string>\n"
             "\t\t\t<key>manufacturer</key>\n\t\t\t<string>PlPg</string>\n"
             "\t\t\t<key>subtype</key>\n\t\t\t<string>" << auSubtype(slug) << "</string>\n"
             "\t\t\t<key>type</key>\n\t\t\t<string>" << (instrument ? "aumu" : "aufx") << "</string>\n"
             "\t\t\t<key>version</key>\n\t\t\t<integer>256</integer>\n"
             "\t\t\t<key>sandboxSafe</key>\n\t\t\t<true/>\n"
             "\t\t\t<key>resourceUsage</key>\n\t\t\t<dict>\n"
             "\t\t\t\t<key>temporary-exception.files.all.read-write</key>\n\t\t\t\t<true/>\n"
             "\t\t\t</dict>\n\t\t</dict>\n\t</array>\n";
    }
    p << "</dict>\n</plist>\n";
    return p.str();
}

// Copy the runtime at `from` to `to` and give it the device. `format` is clap, vst3 or auv2.
inline void makePlugin(const fs::path& from, const fs::path& to, const std::string& device, const std::string& name,
                       const std::string& slug, const std::string& format, bool instrument) {
    fs::create_directories(to.parent_path());
    fs::remove_all(to);
    const fs::path sidecar = fs::path(to).replace_extension(".ppdevice");
    if (!fs::is_directory(from)) {
        // One file (a .clap on Windows and Linux): the device goes beside it.
        fs::copy_file(from, to);
        if (!writeText(sidecar, device)) throw std::runtime_error("could not write " + sidecar.u8string());
        return;
    }
    fs::remove(sidecar);
    fs::copy(from, to, fs::copy_options::recursive | fs::copy_options::copy_symlinks);
    const fs::path contents = to / "Contents";
#if defined(__APPLE__)
    // The seal, if the runtime had one, was over the old Info.plist.
    fs::remove_all(contents / "_CodeSignature");
    if (!writeText(contents / "Info.plist", infoPlist(name, slug, format, instrument)))
        throw std::runtime_error("could not write " + (contents / "Info.plist").u8string());
#else
    // A VST3 folder holds a binary that has to carry the folder's name.
    (void)slug; (void)format; (void)instrument;
    for (const auto& arch : fs::directory_iterator(contents)) {
        if (!arch.is_directory()) continue;
        std::vector<fs::path> binaries;
        for (const auto& file : fs::directory_iterator(arch.path()))
            if (file.path().stem().u8string() == kRuntimeName) binaries.push_back(file.path());
        for (const fs::path& binary : binaries)
            fs::rename(binary, binary.parent_path() / fs::u8path(name + binary.extension().u8string()));
    }
#endif
    fs::create_directories(contents / "Resources");
    // A VST3 can carry a list of what is in it, written when it was built. That list
    // would be the runtime's, not this device's; without it a host asks the plugin.
    fs::remove(contents / "Resources" / "moduleinfo.json");
    fs::remove(contents / "moduleinfo.json");
    if (!writeText(contents / "Resources" / "device.ppdevice", device))
        throw std::runtime_error("could not write the device into " + to.u8string());
#if defined(__APPLE__)
    // Seal the bundle again, ad hoc. A Mac always has codesign; if it somehow does not,
    // the binary still carries the signature it was built with, which is what loading needs.
    tryRun({"/usr/bin/codesign", "--force", "--sign", "-", to.string()});
#endif
}

}  // namespace detail

// Make the plugins for the device in `deviceFile`, from the runtime in `runtime`, in `folders`.
inline Result install(const fs::path& deviceFile, const fs::path& runtime, const Folders& folders) {
    Result result;
    std::ifstream file(deviceFile, std::ios::binary);
    if (!file) {
        result.message = "The file could not be opened.";
        return result;
    }
    std::ostringstream text;
    text << file.rdbuf();
    const std::string device = text.str();

    // Read all of it, as the plugin will: a device this runtime cannot run is refused
    // here, with the reason, and not later by a host that says nothing.
    mxvm::Book book;
    std::string why;
    if (!book.parse(device, &why)) {
        if (!why.empty()) why[0] = static_cast<char>(std::toupper(static_cast<unsigned char>(why[0])));
        result.message = why + ".";
        return result;
    }
    const mxvm::Device& parsed = *book.programs[0];
    result.name = parsed.name;
    const std::string name = detail::fileName(parsed.name);
    std::string slug;
    for (char c : parsed.slug)
        if ((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-') slug.push_back(c);
    if (slug.empty()) slug = "device";

    struct Format { const char* id; const char* extension; fs::path from; fs::path folder; };
    std::vector<Format> formats = {
        {"clap", ".clap", runtime / (std::string(kRuntimeName) + ".clap"), folders.clap},
        {"vst3", ".vst3", runtime / (std::string(kRuntimeName) + ".vst3"), folders.vst3},
    };
#if defined(__APPLE__)
    formats.push_back({"auv2", ".component",
                       runtime / (parsed.instrument ? "instrument" : "effect") / (std::string(kRuntimeName) + ".component"),
                       folders.au});
#endif
    // Nothing is written unless every plugin can be: a name another plugin already has
    // stops the install before it starts.
    for (const Format& f : formats) {
        const fs::path to = f.folder / fs::u8path(name + f.extension);
        if (fs::exists(f.from) && !detail::ours(to)) {
            result.message = "There is already a plugin called “" + name + "” that Plugin Playground did not make (" +
                             to.u8string() + "). It was left as it is. Rename your device on the site and download it again.";
            return result;
        }
    }
    try {
        for (const Format& f : formats) {
            if (!fs::exists(f.from)) continue;
            const fs::path to = f.folder / fs::u8path(name + f.extension);
            detail::makePlugin(f.from, to, device, parsed.name, slug, f.id, parsed.instrument);
            result.made.push_back(to);
        }
    } catch (const std::exception& e) {
        result.message = std::string("The plugin could not be written: ") + e.what();
        return result;
    }
    if (result.made.empty()) {
        result.message = "Plugin Playground is not fully installed: its runtime is missing from " + runtime.u8string() +
                         ". Install it again from the site.";
        return result;
    }
#if defined(__APPLE__)
    // An Audio Unit copied in a moment ago is not always known to the system yet.
    detail::tryRun({"/usr/bin/killall", "-9", "AudioComponentRegistrar"});
#endif
    result.ok = true;
    return result;
}

}  // namespace ppinstall
