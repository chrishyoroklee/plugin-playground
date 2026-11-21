// Where a runtime plugin finds its device: the file the helper put with it.
//
//   in a bundle     <bundle>/Contents/Resources/device.ppdevice
//                   (every format on macOS; a VST3 on Windows and Linux)
//   a single file   the plugin's own path with .ppdevice for its extension
//                   (a .clap on Windows and Linux: "Dusty Keys.clap", "Dusty Keys.ppdevice")
//
// The plugin finds itself by asking the system which file this code was loaded from.

#pragma once

#include <fstream>
#include <string>

#if defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#else
#include <dlfcn.h>
#endif

namespace fxlocate {

// The file this code is in: the binary inside the bundle, not the bundle. UTF-8.
inline std::string modulePath() {
#if defined(_WIN32)
    HMODULE module = nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            reinterpret_cast<LPCWSTR>(&modulePath), &module))
        return "";
    wchar_t wide[32768];
    const DWORD n = GetModuleFileNameW(module, wide, 32768);
    if (n == 0 || n >= 32768) return "";
    const int size = WideCharToMultiByte(CP_UTF8, 0, wide, static_cast<int>(n), nullptr, 0, nullptr, nullptr);
    std::string out(static_cast<size_t>(size), '\0');
    WideCharToMultiByte(CP_UTF8, 0, wide, static_cast<int>(n), &out[0], size, nullptr, nullptr);
    return out;
#else
    Dl_info info;
    // The address of a function in this file, which the plugin's binary holds.
    if (!dladdr(reinterpret_cast<const void*>(&modulePath), &info) || !info.dli_fname) return "";
    return info.dli_fname;
#endif
}

inline bool exists(const std::string& path) {
#if defined(_WIN32)
    const int size = MultiByteToWideChar(CP_UTF8, 0, path.c_str(), -1, nullptr, 0);
    std::wstring wide(static_cast<size_t>(size), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, path.c_str(), -1, &wide[0], size);
    const DWORD attributes = GetFileAttributesW(wide.c_str());
    return attributes != INVALID_FILE_ATTRIBUTES && !(attributes & FILE_ATTRIBUTE_DIRECTORY);
#else
    return std::ifstream(path).good();
#endif
}

// The device file for the plugin this code is in, or "" if there is none.
inline std::string deviceFile() {
    const std::string module = modulePath();
    const size_t slash = module.find_last_of("/\\");
    if (module.empty() || slash == std::string::npos) return "";
    const std::string folder = module.substr(0, slash);             // .../Contents/MacOS
    const size_t up = folder.find_last_of("/\\");
    if (up != std::string::npos) {
        const std::string inBundle = folder.substr(0, up) + "/Resources/device.ppdevice";
        if (exists(inBundle)) return inBundle;
    }
    const size_t dot = module.find_last_of('.');
    const std::string beside = (dot != std::string::npos && dot > slash ? module.substr(0, dot) : module) + ".ppdevice";
    return exists(beside) ? beside : "";
}

}  // namespace fxlocate
