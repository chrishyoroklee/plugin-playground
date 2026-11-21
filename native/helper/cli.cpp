// plugin-playground-install: make plugins from a device file, from a terminal.
//
//   plugin-playground-install DEVICE.ppdevice [--runtime DIR] [--to DIR]
//
// --runtime is the folder holding the runtime plugins; without it, the folder called
// Runtime next to this program. --to puts the plugins under DIR (in CLAP, VST3 and
// Components) and not in your own plugin folders, for trying it out.
//
// On Linux this is the helper: the installer registers it to open .ppdevice files. On
// macOS and Windows the helper is an app with a window (mac.mm, windows.cpp) and this is
// what the tests drive.

#include <cstdio>
#include <cstring>

#include "install.h"

#if defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#elif defined(__APPLE__)
#include <mach-o/dyld.h>
#else
#include <unistd.h>
#endif

namespace fs = std::filesystem;

static fs::path self(const char* argv0) {
#if defined(_WIN32)
    wchar_t wide[32768];
    const DWORD n = GetModuleFileNameW(nullptr, wide, 32768);
    return n ? fs::path(std::wstring(wide, n)) : fs::path(argv0);
#elif defined(__APPLE__)
    char path[4096];
    uint32_t size = sizeof path;
    return _NSGetExecutablePath(path, &size) == 0 ? fs::weakly_canonical(path) : fs::path(argv0);
#else
    std::error_code ec;
    const fs::path link = fs::read_symlink("/proc/self/exe", ec);
    return ec ? fs::path(argv0) : link;
#endif
}

int main(int argc, char** argv) {
    fs::path device, runtime = self(argv[0]).parent_path() / "Runtime";
    ppinstall::Folders folders = ppinstall::userFolders();
    for (int i = 1; i < argc; i++) {
        if (std::strcmp(argv[i], "--runtime") == 0 && i + 1 < argc) runtime = fs::u8path(argv[++i]);
        else if (std::strcmp(argv[i], "--to") == 0 && i + 1 < argc) folders = ppinstall::foldersUnder(fs::u8path(argv[++i]));
        else if (device.empty() && argv[i][0] != '-') device = fs::u8path(argv[i]);
        else {
            std::fprintf(stderr, "usage: plugin-playground-install DEVICE.ppdevice [--runtime DIR] [--to DIR]\n");
            return 2;
        }
    }
    if (device.empty()) {
        std::fprintf(stderr, "usage: plugin-playground-install DEVICE.ppdevice [--runtime DIR] [--to DIR]\n");
        return 2;
    }
    const ppinstall::Result result = ppinstall::install(device, runtime, folders);
    if (!result.ok) {
        std::fprintf(stderr, "%s\n", result.message.c_str());
        return 1;
    }
    std::printf("Installed %s. Restart your DAW, or rescan plugins in it.\n", result.name.c_str());
    for (const fs::path& made : result.made) std::printf("  %s\n", made.u8string().c_str());
    return 0;
}
