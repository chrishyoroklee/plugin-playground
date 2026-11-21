// Plugin Playground.exe: the helper on Windows. The installer puts it in Program Files
// and sets Windows to open .ppdevice files with it. Opening one makes the device's
// plugins from the runtime in the folder next to this program (Runtime) and says so in a
// message box. Opened on its own it says what it is for.
//
// plugin-playground-install.exe (cli.cpp) does the same work in a terminal.

#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef UNICODE
#define UNICODE
#endif
#include <windows.h>
#include <shellapi.h>

#include "install.h"

namespace fs = std::filesystem;

static std::wstring wide(const std::string& utf8) {
    if (utf8.empty()) return L"";
    const int size = MultiByteToWideChar(CP_UTF8, 0, utf8.c_str(), static_cast<int>(utf8.size()), nullptr, 0);
    std::wstring out(static_cast<size_t>(size), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, utf8.c_str(), static_cast<int>(utf8.size()), &out[0], size);
    return out;
}

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    wchar_t self[32768];
    const DWORD n = GetModuleFileNameW(nullptr, self, 32768);
    const fs::path runtime = fs::path(std::wstring(self, n)).parent_path() / "Runtime";

    if (!argv || argc < 2) {
        MessageBoxW(nullptr,
                    L"Make an effect or an instrument on the Plugin Playground site and press Download. "
                    L"Open the file you get, and it becomes a plugin in every DAW on this PC.",
                    L"Plugin Playground is installed", MB_OK | MB_ICONINFORMATION);
        return 0;
    }

    std::wstring done, problem;
    int count = 0;
    for (int i = 1; i < argc; i++) {
        const fs::path file(argv[i]);
        const ppinstall::Result result = ppinstall::install(file, runtime, ppinstall::userFolders());
        if (result.ok) {
            count++;
            done = wide(result.name);
        } else {
            problem = file.filename().wstring() + L": " + wide(result.message);
        }
    }
    if (count > 0) {
        const std::wstring title = count == 1 ? L"\u201C" + done + L"\u201D is installed"
                                              : std::to_wstring(count) + L" plugins are installed";
        std::wstring text = L"It is a VST3 and a CLAP plugin. Rescan plugins in your DAW, or restart it, to see it.";
        if (!problem.empty()) text += L"\n\n" + problem;
        MessageBoxW(nullptr, text.c_str(), title.c_str(), MB_OK | MB_ICONINFORMATION);
        return 0;
    }
    MessageBoxW(nullptr, problem.empty() ? L"That is not a Plugin Playground device." : problem.c_str(),
                L"The plugin could not be installed", MB_OK | MB_ICONWARNING);
    return 1;
}
