# Plugin GUI technology for a very small team (2025 to 2026), with emphasis on embedded web views

Research date: 2026-11-21. Versions observed on that date (GitHub API): CLAP 1.2.10 (tag), clap-wrapper v0.18.0 (2026-11-19), JUCE 9.0.6 (2026-11-19), CHOC (no tags, main pushed 2026-10-23), iPlug2 v1.0.0-beta tag (master pushed 2026-11-21), DPF (no tags, pushed 2026-11-12), webview/webview 0.12.0, wry 0.57.3, visage v1.0.0, VSTGUI 4.15.1, Dear ImGui 1.93.0, RmlUi 6.3, Slint 1.19.0, Cmajor 1.0.3209, WebView2 SDK reference 1.0.4314.35.

Labels used below: [DOC] official documentation or specification, [SRC] read directly from source code or a repository, [ISSUE] an issue tracker report, [FORUM] a forum anecdote, [SNIPPET] seen only in a search result summary and not opened in full (lower confidence).

## 1. Web views in plugins on Windows (WebView2)

### Takeaway
WebView2 is workable inside a CLAP `win32` child window and is what every framework uses on Windows, but it needs three things done deliberately: a loader strategy (ship `WebView2Loader.dll`, link the static loader, or embed the DLL as CHOC does), an explicit writable user data folder (the default is next to the host's `.exe` and usually fails), and fully asynchronous creation on the host's UI thread. The Evergreen runtime is part of Windows 11 and present on "the vast majority" of Windows 10 machines, but Microsoft still tells you to check for it. The recurring field problems are keyboard handling, multi-instance creation races, and re-entrancy during creation; DPI is controllable through documented API.

### Cited Findings

Runtime and distribution
- [DOC] "The Evergreen WebView2 Runtime will be included as part of the Windows 11 operating system." Devices before Windows 11 usually have it because other apps installed it, "however, some devices might not have the Runtime pre-installed, so it's a good practice to check." [Microsoft: Distribute your app and the WebView2 Runtime](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)
- [DOC] "The vast majority of Windows 10 devices have the WebView2 Runtime installed already... A small number of Windows 10 devices don't." Microsoft recommends handling that case by deploying the runtime with your installer or sending users to the download page. [Microsoft: distribution](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)
- [DOC] Detection: read `pv` (REG_SZ) under `HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}` and the same key under `HKCU\Software\...`; missing, empty, or `0.0.0.0` means not installed. Alternative: `GetAvailableCoreWebView2BrowserVersionString` returning `nullptr`. [Microsoft: distribution](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)
- [DOC] Installers: the Evergreen Bootstrapper is about 2 MB and downloads the runtime (`MicrosoftEdgeWebview2Setup.exe /silent /install`); the Evergreen Standalone Installer works offline. Elevated install is per-machine, otherwise per-user. [Microsoft: distribution](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)
- [DOC] Fixed Version: you bundle a specific runtime and pass its folder as `browserExecutableFolder`. "The Fixed Version binaries are over 250 MB." Since v120 on Windows 10, unpackaged Win32 apps must run two `icacls` grants on the runtime folder. It cannot run from a network or UNC path, and you must update it yourself. [Microsoft: distribution](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)
- [DOC] A production WebView2 app cannot use the installed Edge Stable browser as its backing platform; it must use the WebView2 Runtime. [Microsoft: distribution](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)
- [DOC] Evergreen updates automatically and a running process keeps the old version until all environment references are released or the app restarts. New APIs must be feature-detected (`QueryInterface`) because a machine may have an older runtime. [Microsoft: distribution](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)

What must be redistributed
- [DOC] "The `WebView2Loader` code needs to be shipped with the app. This can be done by statically linking `WebView2Loader.lib` into the app binaries, or by including the `WebView2Loader.dll` that matches the app's architecture." The DLL is architecture specific (x86, x64, arm64). [Microsoft: distribution, "Files to ship with the app"](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)
- [DOC] Static linking is selected in MSBuild with `<WebView2LoaderPreference>Static</WebView2LoaderPreference>`. The loader is "a small component that helps apps locate the WebView2 Runtime"; the runtime itself is still required. [Microsoft: Distribute a WebView2 app as a single executable file](https://learn.microsoft.com/en-us/microsoft-edge/webview2/how-to/static)
- [SRC] CHOC avoids both: the loader DLL is embedded as bytes in `choc_WebView.h` (the header is about 1.04 MB) and loaded from memory through `choc_MemoryDLL.h`; define `CHOC_FIND_WEBVIEW2LOADER_DLL` to supply your own. The header also carries Microsoft's redistribution notice for the loader. [Tracktion/choc choc_WebView.h](https://github.com/Tracktion/choc/blob/main/choc/gui/choc_WebView.h); override bug fixed in [choc issue 68](https://github.com/Tracktion/choc/issues/68)
- [FORUM] JUCE forum, 2021 to 2022, Windows: the same VST3 showed Edge (WebView2) in Ableton Live 10 and FL Studio 20 but fell back to Internet Explorer 11 in Studio One 5 and Cubase 11. Reported fix: put `WebView2Loader.dll` next to the plugin binary and set a user data folder. Root cause was never stated. [JUCE forum 46215](https://forum.juce.com/t/webview2-is-not-working-at-some-daws/46215)

User data folder (UDF)
- [DOC] Win32 default: the folder of the running `.exe` plus `.WebView2` (example `D:\WebView2App\WebView2.exe.WebView2\`). In a plugin the running `.exe` is the DAW. [Microsoft: Manage user data folders](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/user-data-folder)
- [DOC] "In most cases, you should specify a custom UDF location." Installed apps live in protected directories and creation fails with "Unable to create user data folder, Access Denied." Pass `userDataFolder` to `CreateCoreWebView2EnvironmentWithOptions`. Do not use a network drive. [Microsoft: user data folders](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/user-data-folder)
- [DOC] The UDF is not removed automatically for Win32 hosts; an uninstaller must clean it. It cannot be deleted while a session is active. [Microsoft: user data folders](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/user-data-folder)
- [DOC] One UDF maps to one browser process group (one browser process, one or more renderers, GPU and utility helpers). WebViews sharing a UDF share that group, "whether the WebView2 controls are in the same host app or different host apps." [Microsoft: user data folders](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/user-data-folder); [Microsoft: Process model](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/process-model)
- [DOC] If a second environment uses a UDF already in use with different options (example given: a different `Language`), "the second `CoreWebView2Environment` will fail to create `WebView2` objects. This is true for `CoreWebView2Environment` objects that are in the same process or in different processes." [Microsoft: Process model](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/process-model)
- [DOC] "Each WebView2 browser process consumes additional memory and disk space. Therefore, avoid running a WebView2 control with too many different UDFs at the same time." [Microsoft: user data folders](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/user-data-folder)
- [SRC] CHOC's choice (main, 2026-11-21): `getUserDataFolder()` returns `%APPDATA%` (CSIDL_APPDATA, roaming) plus the running executable's file name, so the folder is named after the host `.exe`, and the environment is created with null options. [choc_WebView.h](https://github.com/Tracktion/choc/blob/main/choc/gui/choc_WebView.h)

Threading and creation
- [DOC] "The WebView2 must be created on a UI thread that uses a message pump. All callbacks occur on that thread." It requires an STA thread. Blocking the pump (`WaitForSingleObject` and similar) means completion handlers never run. Nested message loops inside WebView2 event handlers are unsupported re-entrancy. [Microsoft: Threading model](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/threading-model)
- [ISSUE] CHOC, Windows, Ableton Live and REAPER (Feb 2025): inserting a plugin with the editor open and deleting it immediately crashed the DAW, because CHOC pumped `DispatchMessage` while waiting for WebView2 and the host destroyed the plugin inside that pump. Reproduced in Lunacy Audio BEAM. Julian Storer's fix: "The only safe way to do this involved adding an asynchronous callback when the webview is ready" (`Options::webviewIsReady`, `isReady()`). [choc issue 71](https://github.com/Tracktion/choc/issues/71)
- [ISSUE] CHOC, Windows (Sep 2025, open): after a later CHOC change the web view "non deterministically fails to load"; maintainer links it to "spinning the message loop while the webview starts up, which is certainly something that could cause nondeterministic problems when more than one of these is being created." [choc issue 89](https://github.com/Tracktion/choc/issues/89)

Embedding in a parent HWND
- [DOC] CLAP `win32` API: "uses physical size", "embed using SetParent". [CLAP gui.h](https://github.com/free-audio/clap/blob/main/include/clap/ext/gui.h)
- [SRC] CHOC creates its own `WS_POPUP` window (class `CHOCWebView`) and exposes it through `getViewHandle()`; the embedder re-parents it. DPF's CHOC-based Windows path does exactly `flags = (flags & ~WS_POPUP) | WS_CHILD` before parenting. [choc_WebView.h](https://github.com/Tracktion/choc/blob/main/choc/gui/choc_WebView.h); [DPF WebViewImpl.cpp](https://github.com/DISTRHO/DPF/blob/main/distrho/extra/WebViewImpl.cpp)
- [DOC] `NotifyParentWindowPositionChanged` must be called when the parent or any ancestor HWND moves; "needed for accessibility and certain dialogs in WebView to work correctly." [ICoreWebView2Controller](https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2controller)
- [DOC] `put_IsVisible(FALSE)` when hidden or minimized gives "CPU and memory benefits": Chromium throttles animations and "WebView2 will purge some caches." A child WebView2 does not get messages when the top window is minimized, so the host code must toggle it. [ICoreWebView2Controller](https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2controller)
- [DOC] `Close()` should be called explicitly to shut down and break reference cycles; the browser process exits when no other WebView uses it. [ICoreWebView2Controller](https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2controller)

Keyboard focus
- [DOC] `AcceleratorKeyPressed` fires for keys pressed while the WebView is focused when Ctrl or Alt is held or the key does not map to a character; Escape is always an accelerator. In windowed mode the handler runs synchronously and the browser process is blocked until it returns or `Handled()` is set. `MoveFocus` and `MoveFocusRequested` move focus in and out. [ICoreWebView2Controller](https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2controller)
- [FORUM] JUCE forum thread opened Oct 2024 and still active Sep 2026: with a `WebBrowserComponent` focused, the spacebar and computer-keyboard MIDI do not reach the host. JUCE staff (attila, Oct 2024) said a generic fix needs "a more nuanced approach" and that JUCE planned to make shortcuts easier; no fix commit is referenced in the thread. Posters in Feb and Sep 2026 report it on Windows. [JUCE forum 63987](https://forum.juce.com/t/fixing-webview-keyboard-focus-issue/63987)
- [FORUM] FL Studio 2021 (Windows, any VST3 that takes focus, not web view specific): spacebar not passed to the DAW; the user-side fix was FL's per-plugin keyboard option. [JUCE forum 55214](https://forum.juce.com/t/vst3-plugin-in-fl-studio-2021-spacebar-not-being-passed-onto-daw/55214) [SNIPPET]
- [SRC] Signalsmith's `webview-gui` README lists as open TODO: "Key event handling needs work", with separate problems on Mac and Windows. [geraintluff/webview-gui](https://github.com/geraintluff/webview-gui)
- [ISSUE] CHOC issue requesting `MoveFocus()` exposure has been open since May 2023. [choc issue 12](https://github.com/Tracktion/choc/issues/12)

DPI scaling
- [DOC] `ICoreWebView2Controller3` (SDK 1.0.774.44 and later): `RasterizationScale`, `ShouldDetectMonitorScaleChanges`, and `BoundsMode`. In `USE_RAW_PIXELS` mode, `Bounds` are raw screen pixels; in `USE_RASTERIZATION_SCALE` mode, logical size times scale equals raw pixels. Apps that set their own scale should set `ShouldDetectMonitorScaleChanges` to false. [ICoreWebView2Controller3](https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2controller3)
- [FORUM] General VST3 on Windows, not web view specific: Cubase and Studio One users report plugin windows opening at the wrong size at 125% to 225% scaling; Steinberg documents a Cubase "HiDPI" preference and a workaround for DPI-unaware plugins. [Steinberg help: DPI-unaware plug-ins](https://helpcenter.steinberg.de/hc/en-us/articles/360017509919-Cubase-11-Using-DPI-unaware-plug-ins-on-Windows); [Steinberg forum 764809](https://forums.steinberg.net/t/cubase-issues-with-hidpi-and-vst3-plugins-of-some-developers/764809) [SNIPPET]

Multiple instances, startup, memory
- [ISSUE] CHOC, Windows (Mar 2024, fixed): two different CHOC-based plugins in one host, the second showed a blank view because both registered the same Win32 window class name; fixed upstream, both plugins had to update CHOC. [choc issue 52](https://github.com/Tracktion/choc/issues/52)
- [FORUM] JUCE 8, Windows, FL Studio and Cubase (Jan 2025): with several instances, every UI after the first froze and froze the DAW when `evaluateJavascript` or `emitEventIfBrowserIsVisible` ran before first page load. Root cause was a faulty serial-creation mechanism; fixed in JUCE commit `90e72dbd` ("Windows: Fix faulty mechanism for creating multiple WebView2 instances serially", 17 Jan 2025). Not reproducible on macOS. [JUCE forum 64917](https://forum.juce.com/t/br-webview-freezes-daw/64917)
- [FORUM] JUCE 8.0.6, Windows (Jan 2025, unresolved): a WebView2 plugin open in one DAW made the UI in a second DAW process hang; the controller-created callback returned a NULL controller. JUCE staff could not reproduce. DAW names not given. [JUCE forum 64853](https://forum.juce.com/t/crash-when-using-webview2-plugins-in-multiple-daws-in-parallel-on-windows/64853)
- [FORUM] Memory, one developer's measurement (May 2024, JUCE WebView2, UI with about 33 MB of images): about 100 MB base for the shared "WebView2 Manager" process, then up to about 80 MB while an instance loads, settling near 35 MB per instance. [JUCE forum 61156](https://forum.juce.com/t/webview-vs-juce-graphics/61156)
- [ISSUE] A Windows 11 install where the WebView2 runtime failed to load was reported by a CHOC user (Jan 2025), cause not given. [choc issue 68](https://github.com/Tracktion/choc/issues/68)
- [ISSUE] CHOC's hand-written COM declarations use `AddWebResourceRequestedFilter`, which now logs a deprecation warning; maintainer has not updated ("I'd have to manually add 22 COM class declarations"). [choc issue 70](https://github.com/Tracktion/choc/issues/70)

Wine
- [SNIPPET] A community FL Studio on Linux (Wine) setup project reports WebView2 runtimes below version 139 "consistently gave blank windows". [Torbuntu/fl-studio-linux-setup issue 3](https://github.com/Torbuntu/fl-studio-linux-setup/issues/3)

### Inferences
- A plugin should never rely on the default UDF: in a DAW it resolves to something like `C:\Program Files\<DAW>\<daw>.exe.WebView2`, which is not writable. A per-vendor folder under `%LOCALAPPDATA%` is the safe choice.
- The UDF choice determines sharing. CHOC's `%APPDATA%\<host exe name>` with null options means every CHOC-based plugin from any vendor loaded in the same DAW shares one browser process group, which saves memory but couples them: per the process-model rule, any plugin that opened the same folder with different environment options would make the other fail. A vendor-specific UDF with fixed options avoids that at the cost of one more browser process group (roughly the 100 MB base figure from the forum measurement).
- The unresolved "second DAW hangs with NULL controller" report is consistent with the documented UDF rule (two processes, same UDF, mismatched options or an elevated/non-elevated mix), but nobody confirmed it in the thread.
- For a hand-written CLAP plugin, creation must be asynchronous end to end: return from `gui->create()` / `set_parent()` immediately, finish in the completion handlers, and buffer outgoing messages until the page is ready. Pumping messages to make creation look synchronous is what produced the CHOC crashes.
- Because CLAP `win32` sizes are physical pixels, the simplest mapping is `BoundsMode = USE_RAW_PIXELS`, set `RasterizationScale` from the host's `set_scale` value, and turn off `ShouldDetectMonitorScaleChanges` when the host supplies a scale.
- For Windows 10 customers the installer should run the 2 MB bootstrapper when the registry check fails; Fixed Version (250 MB plus ACL steps) is hard to justify for a plugin.

### Gaps
- No source found documenting WebView2 behaviour inside sandboxed or bridged plugin hosts (Bitwig's plugin sandbox, FL Studio bridged mode, jBridge). Not confirmed either way.
- No measured startup time for WebView2 in a plugin was found. No Ableton Live specific WebView2 defect was found beyond the generic crash-on-quick-delete report.
- Whether the JUCE keyboard-focus issue is fixed in JUCE 9 was not checked.
- Reaper specific WebView2 problems: only the CHOC quick-delete crash. Cubase: only the 2021 loader fallback and the 2025 multi-instance freeze (fixed).

## 2. Web views in plugins on Linux (WebKitGTK)

### Takeaway
Linux is the weak platform for web view plugins. The only embeddable system web view is WebKitGTK, it only embeds through GtkPlug/XEmbed (X11 or XWayland), and loading GTK into a host process is a known hazard. Frameworks split into two camps: run WebKitGTK in a separate child process (JUCE, DPF) or load GTK in-process and accept the risk (CHOC, and iPlug2 as of three days before this research). Several Rust and CLAP helper projects simply do not support Linux.

### Cited Findings
- [DOC] CLAP: X11 "embed using XEmbed"; Wayland "embed is currently not supported, use floating windows." [CLAP gui.h](https://github.com/free-audio/clap/blob/main/include/clap/ext/gui.h)
- [ISSUE] CLAP "GUI on Wayland" (opened Oct 2025, open). baconpaul: "I don't think any plugin has done this and I'm not sure any daw has supported the APIs implicit in clap gui." abique: Wayland "didn't have the equivalent of xembed" and passing `wl_display*` is rejected because it "inherits all the wayland ABI issues." [free-audio/clap issue 474](https://github.com/free-audio/clap/issues/474)
- [SRC] JUCE (current master): the Linux `WebBrowserComponent` runs WebKit in a separate `GtkChildProcess`, talks to it over pipes, embeds via `gtk_plug_new` and XEmbed, `dlopen`s `libwebkit2gtk-4.1.so.0` then falls back to `libwebkit2gtk-4.0.so.37`, and forces the X11 GDK backend with the comment "webkit2gtk crashes when using the wayland backend embedded into an x11 window." [juce_WebBrowserComponent_linux.cpp](https://github.com/juce-framework/JUCE/blob/master/modules/juce_gui_extra/native/juce_WebBrowserComponent_linux.cpp)
- [FORUM] JUCE 7.0.2, Ubuntu 22.04 (2022): `WebBrowserComponent` died with "GtkPlug only works under X11" on a Wayland session; JUCE (reuk) posted a patch forcing webkit2gtk to X11. [JUCE forum 53142](https://forum.juce.com/t/gtkplug-only-works-under-x11-juce-7-0-2-ubuntu-22-04-lts/53142)
- [FORUM] JUCE forum (May 2025): when the plugin crashes, the Linux web view helper process spins at 100% CPU; one-line fix proposed by a user, no staff reply on the page. [JUCE forum 66123](https://forum.juce.com/t/linux-webview-helper-spins-cpu-when-plugin-crashes/66123)
- [FORUM] JUCE 8 Linux build issues: CMake linked plugins against libwebkit2gtk, causing a crash via libfontconfig in one report; a host blacklisted a plugin over an undefined webkit2gtk-4.0 symbol; 4.0 versus 4.1 mismatches on Arch. [JUCE forum 63870](https://forum.juce.com/t/bug-cmake-linux-tooling-should-not-link-plugins-against-libwebkit2gtk/63870); [JUCE forum 56487](https://forum.juce.com/t/juce-and-arch-linux-webkit2gtk-4-0-to-4-1-issues/56487) [SNIPPET]
- [SRC] CHOC: Linux uses GTK 3 plus webkit2gtk-4.1 in-process (`gtk_init_check`), requires `libgtk-3-dev` and `libwebkit2gtk-4.1-dev`, and `getViewHandle()` returns a `GtkWidget*`. Its optional JUCE helper wraps the widget in `gtk_plug_new(0)` and hands the plug id to `juce::XEmbedComponent`. [choc_WebView.h](https://github.com/Tracktion/choc/blob/main/choc/gui/choc_WebView.h)
- [FORUM] CHOC WebView in a JUCE plugin on Ubuntu 22.04 (Feb to Apr 2024): black window in Bitwig 5.1.6; in Reaper 7.14 the page appeared but the window was corrupted and "Reaper locked up completely." No solution in the thread; the original poster paused the work to wait for JUCE 8. [JUCE forum 60100](https://forum.juce.com/t/xembedcomponent-and-choc-webview/60100)
- [FORUM] A developer notes that an in-process GTK web view needs the GTK main loop running, which clashes with JUCE's loop, and that plugins expecting a GTK loop "won't work in JUCE hosts currently." [JUCE forum 66326](https://forum.juce.com/t/gtk-vs-juce-event-loops/66326) [SNIPPET]
- [ISSUE] CHOC web view renders garbage on Raspberry Pi 5 / Pi OS (Oct 2025, open) while JUCE's browser works on the same machine. [choc issue 91](https://github.com/Tracktion/choc/issues/91)
- [SRC] DPF: three backends in `WebViewImpl.cpp`: CHOC on Windows, direct WKWebView on macOS, and on Linux with X11 an "X11 IPC" mode that uses `ChildProcess.hpp`, a POSIX shared-memory ring buffer (`/dpf-webview-N`) and semaphores to talk to a separate web view process. The file references both GTK and Qt headers (commented out). [DPF WebViewImpl.cpp](https://github.com/DISTRHO/DPF/blob/main/distrho/extra/WebViewImpl.cpp)
- [SRC] iPlug2: `IPlugWebView_linux.cpp` was added on 2026-11-18 ("WebView: add a WebKitGTK editor for CLAP and VST3 on Linux"). It is in-process GTK 3 with `GtkPlug`, calls `gdk_set_allowed_backends("x11")` ("GtkPlug is X11-only"), calls `gtk_disable_setlocale()` because `gtk_init_check()` "would otherwise call setlocale(LC_ALL, ""), changing e.g. the decimal separator for the host and every other plugin", and pumps the GLib main context manually ("GTK is process-global"). [iPlug2 IPlugWebView_linux.cpp](https://github.com/iPlug2/iPlug2/blob/master/IPlug/Extras/WebView/IPlugWebView_linux.cpp)
- [SRC] wry 0.57 (Tauri): WebKitGTK on Linux; child web views are "supported on macOS, Windows and Linux (X11 Only)". [wry on docs.rs](https://docs.rs/crate/wry/0.57.0) [SNIPPET]
- [SRC] nih-plug-webview README: "work in progress, not production-ready yet", tested on macOS only, Windows "needs to be verified", Linux not mentioned; last push 2025-01-06. [httnn/nih-plug-webview](https://github.com/httnn/nih-plug-webview)
- [SRC] webview/webview 0.12.0 supports three Linux stacks: WebKitGTK 6.0 with GTK 4, WebKitGTK 4.1 with GTK 3 and libsoup 3, WebKitGTK 4.0 with GTK 3 and libsoup 2. [webview/webview README](https://github.com/webview/webview)
- [SRC] Signalsmith `webview-gui`: macOS native, otherwise CHOC if available, otherwise a placeholder. Linux support is not stated. [geraintluff/webview-gui](https://github.com/geraintluff/webview-gui)

### Inferences
- The GTK conflict risk is structural: a process can hold only one GTK major version, GTK is process-global (locale, main loop, display backend), and WebKitGTK exists in three ABI families (4.0, 4.1, 6.0). A plugin that loads GTK 3 in-process can break, or be broken by, a GTK 2 or GTK 4 host or another plugin. This is why JUCE and DPF pay for a helper process, and why iPlug2's brand new in-process code carries defensive comments.
- For a hand-written CLAP plugin, the lowest-risk Linux design is a small helper executable that owns GTK and WebKitGTK, embeds a `GtkPlug` into the host's X11 window by XID, and exchanges messages over a pipe or shared memory. That is extra code CHOC does not provide.
- A pragmatic alternative is to ship Linux without the web UI (generic host UI) or with a native non-web UI, since frameworks marketed as web view capable (nih-plug-webview, webview-gui) leave Linux out.
- Wayland-only sessions work only through XWayland; there is no path to a native Wayland embedded plugin web view today.

### Gaps
- Could not confirm exactly what DPF's Linux child process loads (GTK 3 WebKitGTK versus Qt WebEngine) or how mature it is; only the IPC structure was read. DPF's FEATURES.md does not mention web views.
- No shipping commercial Linux plugin with a web view UI was identified.
- iPlug2's Linux web view is three days old at the time of writing; no field reports exist.
- Cmajor's Linux plugin web view behaviour was not verified (it uses CHOC, so the in-process caveats presumably apply).

## 3. macOS WKWebView in plugins

### Takeaway
WKWebView is the least troublesome of the three platforms, which matches the project's own experience. Rendering runs out of process, so its memory does not show inside the DAW process. Known problems are keyboard routing, occasional WebKit process-lifecycle crashes, host-specific hide/show behaviour (FL Studio), and an AUv2 wrapper bug in some hosts that destroys the GUI on sample-rate change. No evidence was found that Logic's AUHostingService breaks WKWebView as such.

### Cited Findings
- [FORUM] Apple developer forum answer: WKWebView "performs all of its work out of process and its memory usage is accounted for separately from that of your app." [Apple forums 21956](https://developer.apple.com/forums/thread/21956) [SNIPPET]
- [FORUM] JUCE 6.1.2 (2021): opening a plugin with a `WebBrowserComponent` created several helper processes that stayed alive after the plugin was removed. [JUCE forum 48367](https://forum.juce.com/t/bug-macos-webbrowsercomponent-orphaned-leaking-processes/48367) [SNIPPET]
- [FORUM] JUCE 8.0.12, macOS 26.2, Apple Silicon, Ableton Live 12.3.2 (Jan 2026): intermittent `EXC_BREAKPOINT` on the main thread inside WebKit `ProcessThrottler::sendPrepareToSuspendIPC`. No workaround, no replies. The poster attributes it to WebKit process suspend/resume in a plugin host. [JUCE forum 68055](https://forum.juce.com/t/webkit-processthrottler-crash-in-juce-8-webview-plugin-macos/68055)
- [ISSUE] Logic Pro (AU, out of process): repeated right-click in a `juce::WebBrowserComponent` crashed the Audio Unit ("An Audio Unit plug-in reported a problem"); not reproducible in Reaper; first seen with a CHOC WebView template; fixed by a JUCE commit (Apr 2024). The search summary attributes it to JUCE's accessibility integration. [JUCE issue 1376](https://github.com/juce-framework/JUCE/issues/1376)
- [ISSUE] FL Studio 2025, macOS Sequoia 15 (Sep 2025, open): the web view intermittently becomes `about:blank` after closing and reopening the plugin, and `pointerout` has dead spots. A user reports FL needs JUCE's `.withKeepPageLoadedWhenBrowserIsHidden()` because the default replaces the page with a blank one when hidden. [JUCE issue 1577](https://github.com/juce-framework/JUCE/issues/1577)
- [FORUM] Keyboard: macOS Logic, spacebar and computer-keyboard note-offs do not reach the host while the web view has focus; macOS Ableton, notes register late. Workaround: add Objective-C `keyDown:`/`keyUp:` overrides that forward to the next responder (the author calls it partial). [JUCE forum 63987](https://forum.juce.com/t/fixing-webview-keyboard-focus-issue/63987)
- [ISSUE] CHOC, macOS (2024, fixed): with `performKeyEquivalent` registered, arrow keys never reached the page; fix was to call the superclass. CHOC exposes `enableDefaultClipboardKeyShortcutsInSafari` (default on) to intercept copy, cut, paste, undo, redo and select-all, and `acceptsFirstMouseClick`. [choc issue 56](https://github.com/Tracktion/choc/issues/56); [choc_WebView.h](https://github.com/Tracktion/choc/blob/main/choc/gui/choc_WebView.h)
- [SRC] nih-plug-webview: pressing Escape in Ableton Live on macOS crashed the host; reported to Ableton; worked around by consuming the key. Signalsmith's webview-gui lists a possible "Mac crash on pressing Esc" as a TODO. [httnn/nih-plug-webview](https://github.com/httnn/nih-plug-webview); [geraintluff/webview-gui](https://github.com/geraintluff/webview-gui)
- [ISSUE] clap-wrapper AUv2 (Oct 2026, open): in Ableton Live and REAPER, changing the sample rate calls `WrapAsAUV2::Cleanup()`, which calls the plugin's `gui->destroy()`: "At worst, this causes a crash. At best... the window [turns] black and the user must close and reopen it." Logic 12.2 behaves normally because it closes and reopens the GUI. [clap-wrapper issue 595](https://github.com/free-audio/clap-wrapper/issues/595)
- [FORUM] Apple forum: Logic Pro 12 crashes if an Audio Unit takes more than about 20 to 30 seconds to initialize; the AU itself runs in `AUHostingServiceXPC`. Not web view specific. [Apple forums 828298](https://developer.apple.com/forums/thread/828298) [SNIPPET]
- [FORUM] On Apple Silicon, Logic hosts native AUs in AUHostingService; macOS killing idle AUHostingService processes has taken JUCE plugins down (Logic 10.8.1 era). Not web view specific. [JUCE forum 59020](https://forum.juce.com/t/juce-plug-ins-crashing-under-logic-pro-10-8-1/59020) [SNIPPET]
- [SRC] Offline use: CHOC serves the page from C++ through `Options::fetchResource` on a custom scheme (default `choc://choc.choc/`), and the CLAP draft web view extension has `get_resource()` for the same purpose, so no network or local HTTP server is needed. [choc_WebView.h](https://github.com/Tracktion/choc/blob/main/choc/gui/choc_WebView.h); [CLAP webview.h](https://github.com/free-audio/clap/blob/main/include/clap/ext/draft/webview.h)

### Inferences
- A single self-contained HTML page (the project's current approach) sidesteps the resource-serving and sandbox file-access questions entirely; it should keep working out of process because WKWebView already does its work in separate WebContent processes regardless of where the AU lives.
- The clap-wrapper AUv2 sample-rate issue matters more for a web view UI than a drawn UI, because recreating a web view is slow and loses page state; the page must be able to rebuild its state from the plugin on every load.
- Keyboard forwarding needs an explicit policy per key (page consumes text input, everything else forwarded to the host) on both macOS and Windows; no framework has solved it generically.

### Gaps
- No measured per-instance memory or process count for WKWebView in a plugin was found. WKWebView normally uses a WebContent process plus shared Networking and GPU processes, but how many are created per plugin instance, and whether `WKProcessPool` sharing still reduces it on current macOS, was not confirmed.
- No measured startup latency.
- No source confirms or rules out sandbox-related WKWebView failures in Logic's AUHostingService, GarageBand, or AUv3 app extensions. The project's own working AU is the best evidence available.
- The ProcessThrottler crash has a single reporter and no confirmation.

## 4. Frameworks and libraries that provide plugin web views

### Takeaway
CHOC's `choc::ui::WebView` is the only mature piece that can be dropped into a hand-written CLAP plugin without adopting a framework: one ISC-licensed header covering WKWebView, WebView2 (loader embedded) and WebKitGTK. Signalsmith's `webview-gui` is a thin CLAP-specific layer on top of it that mirrors the new CLAP draft web view extension. Everything else either requires adopting its framework (JUCE, iPlug2, DPF, Cmajor) or is Rust.

### Cited Findings

CHOC (Tracktion, Julian Storer)
- [SRC] ISC licence, header-only, no tagged releases (pin a commit), 865 stars, last push 2026-10-23. Backends: WKWebView (link WebKit.framework), WebView2 ("no extra build steps needed"), WebKitGTK 4.1 with GTK 3. [Tracktion/choc](https://github.com/Tracktion/choc); [choc_WebView.h](https://github.com/Tracktion/choc/blob/main/choc/gui/choc_WebView.h)
- [SRC] API: `Options` (`enableDebugMode`, `enableDebugInspector`, `acceptsFirstMouseClick`, `customUserAgent`, `webviewIsReady`, `fetchResource`, `customSchemeURI`, `transparentBackground`, `enableDefaultClipboardKeyShortcutsInSafari`, `shouldAllowRequest`); `setHTML`, `navigate`, `evaluateJavascript` (async, optional completion), `bind`/`unbind` (expose a C++ function to JS as a named function returning a promise), `addInitScript`, `getViewHandle` (NSView*, HWND, or GtkWidget*), `loadedOK`, `isReady`. Constructor and destructor must run on the message thread. [choc_WebView.h](https://github.com/Tracktion/choc/blob/main/choc/gui/choc_WebView.h)
- [SRC] "Creates an embedded browser which can be placed inside some kind of parent window... its getViewHandle() returns a platform-specific handle that can be added to whatever kind of window is appropriate." It needs a running message loop but does not require CHOC's own. [choc_WebView.h](https://github.com/Tracktion/choc/blob/main/choc/gui/choc_WebView.h)
- [ISSUE] Open issues relevant to plugins: drag and drop (64), passing ArrayBuffer to C++ (54), open links in system browser (83), navigation allow/deny on WebView2 (111), Windows creation race (89), Pi 5 rendering (91), WebView2 deprecation (70), `MoveFocus` (12). [choc issues](https://github.com/Tracktion/choc/issues?q=webview)
- [SRC] Used by others without the rest of the stack: DPF uses CHOC for its Windows web view; The Audio Programmer publishes a JUCE plugin template using the CHOC web view; Splice maintains a fork (`splice/choc`, referenced in issue 56); Lunacy Audio BEAM is named as a CHOC web view plugin in issue 71. [DPF WebViewImpl.cpp](https://github.com/DISTRHO/DPF/blob/main/distrho/extra/WebViewImpl.cpp); [choc issue 71](https://github.com/Tracktion/choc/issues/71); [choc issue 56](https://github.com/Tracktion/choc/issues/56)

CLAP draft web view extension and Signalsmith webview-gui
- [SRC] `CLAP_EXT_WEBVIEW = "clap.webview/3"` in `ext/draft/webview.h`, introduced in CLAP 1.2.7. Plugin side: `get_uri()`, `get_resource(path, mime, stream)`, `receive(buffer, size)`; host side: `send(buffer, size)`; all `[main-thread]`. Messages are binary: an `ArrayBuffer` in a `MessageEvent` on the page, and `window.parent.postMessage()` back. A new window API `CLAP_WINDOW_API_WEBVIEW = "webview"` uses logical sizes and a NULL window. [CLAP webview.h](https://github.com/free-audio/clap/blob/main/include/clap/ext/draft/webview.h); origin in [clap issue 416](https://github.com/free-audio/clap/issues/416); version per [signalsmith-clap-cpp](https://github.com/geraintluff/signalsmith-clap-cpp) [SNIPPET]
- [SRC] `webview-gui` README: the draft extension "is primarily for WCLAPs (CLAPs compiled to WebAssembly). Native hosts don't support it and are unlikely to." Its `clap-webview-gui.h` therefore implements `clap.gui` natively and forwards to the plugin's web view extension, using Cocoa directly on macOS and CHOC elsewhere. 6 stars, no licence file detected by GitHub, pushed 2026-10-07. [geraintluff/webview-gui](https://github.com/geraintluff/webview-gui)

JUCE
- [SRC] JUCE 8 (2024) added the WebView plugin UI feature set (`WebBrowserComponent` with native functions, events, resource provider, `WebSliderRelay` style parameter relays, `WebViewPluginDemo`); current release is JUCE 9.0.6 (2026-11-19). Backends: WKWebView, WebView2, WebKitGTK in a child process. [JUCE repository](https://github.com/juce-framework/JUCE); [WebViewPluginDemo.h in JUCE 8.0.6](https://sources.debian.org/src/juce/8.0.6%2Bds-2/examples/Plugins/WebViewPluginDemo.h); [juce_WebBrowserComponent_linux.cpp](https://github.com/juce-framework/JUCE/blob/master/modules/juce_gui_extra/native/juce_WebBrowserComponent_linux.cpp)
- [FORUM] Field reports from JUCE 8 are listed in sections 1 to 3: keyboard focus (open two years), multi-instance freeze (fixed Jan 2025), FL Studio blank page, 30 Hz event emission crash on repeated open/close (unconfirmed cause), Linux helper CPU spin. Community advice: "commit to either 100% webview or 100% JUCE UI." [JUCE forum 66868](https://forum.juce.com/t/i-want-advice-on-combining-juce-with-web-technologies/66868) [SNIPPET]; [JUCE forum 62131](https://forum.juce.com/t/juce-8-webview-bug-webviewplugindemo-crashes-when-repeatedly-launched/62131) [SNIPPET]

iPlug2
- [SRC] zlib-like licence, 2,452 stars, active (pushed 2026-11-21), tag v1.0.0-beta. `IPlug/Extras/WebView` has WKWebView (macOS, iOS), WebView2 (Windows), a web/WASM path, and since 2026-11-18 a WebKitGTK Linux file. Official minimum targets include "Linux (X11)". The web view is tied to iPlug2's `WebViewEditorDelegate`. [iPlug2](https://github.com/iPlug2/iPlug2); [IPlug/Extras/WebView](https://github.com/iPlug2/iPlug2/tree/master/IPlug/Extras/WebView)

DPF and dpfwebui
- [SRC] DPF: ISC, LADSPA/DSSI/LV2/VST2/VST3/CLAP. Has `distrho/extra/WebView.hpp` and `dgl/WebView.hpp` (copyright 2012 to 2024), backends as in section 2. Not documented in the README or FEATURES.md. [DISTRHO/DPF](https://github.com/DISTRHO/DPF)
- [SRC] `lucianoiam/dpfwebui` (third party, ISC, 52 stars) last pushed 2024-05-20. [dpfwebui](https://github.com/lucianoiam/dpfwebui)

Cmajor
- [SRC] Cmajor 1.0.3209 (2026-09-24), built on CHOC; patches carry JavaScript/HTML GUIs shown in a CHOC web view. Julian Storer gave the ADC 2024 talk 'Javascript, WebViews and C++: "If You Can't Beat Them, Join Them"' (12 Nov 2024): "more and more developers have been drawn to web-technology for their GUIs" but "gaffer-taping a WebView onto some back-end code written in C++ can be complicated." [cmajor-lang/cmajor](https://github.com/cmajor-lang/cmajor); [ADC speaker page](https://conference.audio.dev/speakers/julian-storer/)

webview/webview (C/C++)
- [SRC] MIT, 14.3k stars, 0.12.0. WebKit on macOS, WebView2 on Windows, WebKitGTK (three GTK/WebKitGTK combinations) on Linux. Designed around owning an application window and run loop. [webview/webview](https://github.com/webview/webview)

Rust: nih-plug, nih-plug-webview, wry, NovoNotes wxp
- [SRC] nih-plug: ISC, 3,040 stars, last push 2026-09-03. nih-plug-webview: experimental, macOS tested only, built on wry and baseview, stale since Jan 2025. [nih-plug](https://github.com/robbert-vdh/nih-plug); [nih-plug-webview](https://github.com/httnn/nih-plug-webview)
- [SRC] NovoNotes "WRAC" stack (talk dated June 2026): WebView GUI, Rust DSP, CLAP via clap-sys, converted to VST3/AU with clap-wrapper. Web view is wry wrapped by their MIT `wxp` crate (Tauri-inspired API, "adaptive IPC mode that switches methods based on payload size") plus a `run_loop` crate for non-blocking main-thread work. They say using wry as-is for plugins is cumbersome. "Already used by over 15,000 NovoNotes users." [NovoNotes slides](https://speakerdeck.com/novonotes/write-0-lines-of-c-plus-plus-building-commercial-audio-plugins-with-rust-and-web-gui); [novonotes/wxp](https://github.com/novonotes/wxp)

Others
- [SRC] React-JUCE / Blueprint: MIT, last push 2024-12-14 (it rendered React to JUCE components, not a web view). Elementary Audio: MIT, last push 2026-01-19 (a JS DSP runtime; its plugin examples pair it with a web view UI). [react-juce](https://github.com/JoshMarler/react-juce); [elementary](https://github.com/elemaudio/elementary)
- [SRC] Atsushi Eno, COSCUP 2024 (2 Aug 2024): names DPF-webui, Cmajor and JUCE 8 as the web view GUI efforts; challenges listed: performant data flow between audio thread and web view (especially audio buffers), bundling WebView2 DLLs on Windows in Evergreen mode, integrating JS builds into CMake, and controversial community reaction. [Slides](https://speakerdeck.com/atsushieno/coscup2024-catching-up-trends-in-audio-app-development)

### Inferences
- For this project (hand-written C++ CLAP, existing WKWebView path), CHOC's header for Windows only is the smallest step: keep the existing macOS code, add `choc::ui::WebView` on Windows, re-parent its HWND under the CLAP parent (as DPF does), and handle readiness asynchronously. That reuses the embedded loader and avoids writing COM boilerplate by hand.
- CHOC's risks for a commercial plugin are its Windows UDF policy (shared, host-named), its unreleased-main development model, stale COM declarations, and an open creation race. All are manageable by pinning a commit and, if needed, patching `getUserDataFolder()`.
- Shaping the page-to-plugin protocol like the CLAP draft (`get_resource`, binary `send`/`receive`) costs little and keeps the door open to WebCLAP/WCLAP hosts and to Signalsmith's helper, even though native hosts will not implement the extension.
- Tauri itself is not usable for plugins (it owns the process and event loop); wry is the reusable part, and only from Rust.

### Gaps
- JUCE 9's web view changes were not reviewed; all JUCE findings are from JUCE 8-era sources plus current master source for Linux.
- The licence of `geraintluff/webview-gui` is not stated in what was read.
- Cmajor's licence terms were not confirmed from the README lines read (GitHub reports no SPDX match).
- iPlug2's web view cannot obviously be used without its editor delegate; not verified by reading the code in full.

## 5. Shipping products with web view interfaces and what developers report

### Takeaway
Web view plugin UIs are shipping commercially, but public, quantified developer reports are scarce. The firmest data points are NovoNotes (15,000+ users on a WebView + Rust + CLAP + clap-wrapper stack, which is close to this project's architecture), Lunacy Audio BEAM (CHOC), and one forum memory measurement.

### Cited Findings
- [SRC] NovoNotes (spatial audio, 8 products, about 30K users per their slide): WRAC-stack plugins used by over 15,000 users, shipping CLAP, VST3, AU and standalone with a web GUI; benefits cited are CSS, hot reload, DevTools; requirement cited is 30 Hz IPC for meters and automation without blocking the host main thread. [NovoNotes slides, June 2026](https://speakerdeck.com/novonotes/write-0-lines-of-c-plus-plus-building-commercial-audio-plugins-with-rust-and-web-gui)
- [ISSUE] Lunacy Audio BEAM is identified by a developer (ncblair) as a CHOC web view based plugin that reproduced the Windows quick-delete crash in Ableton Live and REAPER. [choc issue 71](https://github.com/Tracktion/choc/issues/71)
- [ISSUE] Splice maintains a fork of CHOC and its engineers filed web view key-handling fixes (2024). Which Splice product uses it is not stated. [choc issue 56](https://github.com/Tracktion/choc/issues/56)
- [SNIPPET] A JUCE Marketplace listing ("Sound Field") describes a commercial VST3/AU plugin whose UI is "a React + Three.js app running inside a native VST3/AU plugin" via JUCE 8's WebView. [JUCE Marketplace](https://marketplace.juce.com/products/sound-field)
- [SRC] Signalsmith Audio's CLAP examples and WebCLAP work use web view UIs through the draft extension. [signalsmith-clap-cpp](https://github.com/geraintluff/signalsmith-clap-cpp); [WebCLAP organisation](https://github.com/WebCLAP)
- [FORUM] Memory measurement (normalised, May 2024, WebView2): about 100 MB shared base, about 35 MB per instance steady state, up to 80 MB transient. [JUCE forum 61156](https://forum.juce.com/t/webview-vs-juce-graphics/61156)
- [FORUM] Limitations reported by developers in the same thread: pointer capture for knobs "works in major browsers but not in the JUCE webview" (TobbenTM); WebKit versus Chromium differences and platform web view updates breaking plugins (ruurdadema, leehu); recommendation to use drawn graphics for analysers and fine knobs and web views for menu-heavy UIs (adamwilson). [JUCE forum 61156](https://forum.juce.com/t/webview-vs-juce-graphics/61156)
- [SNIPPET] A KVR user complains that a web view based plugin "prevents [them] from working if your internet is slow or out", which is a design failure of that product (remote content), not of web views. [KVR thread](https://www.kvraudio.com/forum/viewtopic.php?p=9026626)
- [SNIPPET] A forum poster guesses that the VSTi "TEKNO" uses a web view; explicitly a hunch. [JUCE forum 67863](https://forum.juce.com/t/significant-gui-sluggishness-daw-freezes-on-windows-nvidia-suspected-juce-8-opengl-d2d-conflict/67863)
- [SNIPPET] An ADC 2023 talk on building a high-performance audio app with a web GUI and C++ engine is referenced in Atsushi Eno's slides. [COSCUP 2024 slides](https://speakerdeck.com/atsushieno/coscup2024-catching-up-trends-in-audio-app-development)

### Inferences
- The products that ship successfully bundle all UI assets and serve them from the binary; anything that needs the network at load time generates user complaints.
- The memory cost is real but modest relative to sample libraries: on Windows roughly 100 MB once plus tens of MB per open editor (one anecdote), mostly outside the DAW process.
- NovoNotes is the closest public precedent to this project (CLAP first, clap-wrapper for VST3 and AU, web UI), and its existence at 15,000+ users is evidence the architecture is viable on macOS and Windows.

### Gaps
- No vendor write-up with startup-time, crash-rate, or support-ticket numbers for a web view plugin was found.
- Could not confirm from primary sources which widely known commercial plugins (for example from Output, Arturia, Native Instruments, or others often rumoured to use web tech) use web views; nothing is asserted here.
- The ADC 2023 and ADC 2024 talk contents were not read (only abstracts or references); the ADC 2025 programme was not searched.

## 6. Communication between page and plugin

### Takeaway
Every stack uses the same shape: asynchronous message passing on the host's main (UI) thread, JSON or binary payloads, with the audio thread only writing to lock-free state that a main-thread timer forwards at roughly 30 Hz. Calling into the web view before the page is ready, or from the wrong thread, is the most common source of freezes.

### Cited Findings
- [DOC] WebView2: all calls and callbacks on the creating UI thread; "It isn't safe to use the WebView2 from another thread"; never block the pump waiting for a result. [Microsoft: Threading model](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/threading-model)
- [DOC] CLAP draft web view extension: `send` and `receive` are `[main-thread]`, one message per call, binary buffers; `get_uri` must be called before any messages; `send` "must return false if the webview isn't open." [CLAP webview.h](https://github.com/free-audio/clap/blob/main/include/clap/ext/draft/webview.h)
- [SRC] CHOC: `bind(name, fn)` exposes a C++ callback as a JS function (arguments and result as `choc::value`, JSON under the hood, returned to JS as a promise); `evaluateJavascript(script, completion)` is asynchronous; `addInitScript` injects a bootstrap before page scripts. [choc_WebView.h](https://github.com/Tracktion/choc/blob/main/choc/gui/choc_WebView.h)
- [SRC] iPlug2 Linux/mac pattern: page calls `window.IPlugSendMsg(msg)` which posts `JSON.stringify(msg)` to a script message handler (`window.webkit.messageHandlers.iplug.postMessage`). [IPlugWebView_linux.cpp](https://github.com/iPlug2/iPlug2/blob/master/IPlug/Extras/WebView/IPlugWebView_linux.cpp)
- [SRC] Signalsmith webview-gui: C++ sends and receives `const unsigned char*` buffers; the page uses `ArrayBuffer` with `parent.postMessage()` and `addEventListener('message', ...)`. [geraintluff/webview-gui](https://github.com/geraintluff/webview-gui)
- [SRC] NovoNotes: meters and knob automation need "WebView IPC at 30Hz without blocking the host's main thread"; their wxp crate switches IPC method by payload size. [NovoNotes slides](https://speakerdeck.com/novonotes/write-0-lines-of-c-plus-plus-building-commercial-audio-plugins-with-rust-and-web-gui)
- [FORUM] JUCE 8: emitting events or evaluating JS before the first page load froze the UI with multiple instances (Windows, FL Studio and Cubase); workaround was to wait for `pageFinishedLoading`. Another thread: events emitted inside `pageFinishedLoading` did not arrive unless deferred slightly; another: emitting before `window.__JUCE__.backend` exists asserts. [JUCE forum 64917](https://forum.juce.com/t/br-webview-freezes-daw/64917); [JUCE forum 62723](https://forum.juce.com/t/webbrowsercomponent-pagefinishedloading-bug-or-misunderstanding/62723) [SNIPPET]; [JUCE forum 68857](https://forum.juce.com/t/webbrowsercomponent-allows-events-to-be-emitted-before-backend-is-initialised/68857) [SNIPPET]
- [FORUM] JUCE 8 `WebViewPluginDemo` with a 30 Hz timer emitting spectrum data crashed when the editor was opened and closed repeatedly; commenting out the emit stopped it. Root cause not confirmed. [JUCE forum 62131](https://forum.juce.com/t/juce-8-webview-bug-webviewplugindemo-crashes-when-repeatedly-launched/62131) [SNIPPET]
- [FORUM] `emitEventIfBrowserIsVisible` is implemented on top of `evaluateJavascript`. [JUCE forum 61061](https://forum.juce.com/t/the-new-webbrowsercomponent-and-valuetrees/61061) [SNIPPET]
- [FORUM] One developer streams rendered pixels over a WebSocket into a WebGL2 canvas and finds it "a bit janky". [JUCE forum 61156](https://forum.juce.com/t/webview-vs-juce-graphics/61156)
- [ISSUE] clap-wrapper shows the cost of getting thread affinity wrong: `request_resize` from a non-main thread failed in Reaper on Windows through the VST3 wrapper until the wrapper deferred to its idle callback. [clap-wrapper issue 404](https://github.com/free-audio/clap-wrapper/issues/404)

### Inferences
- Recommended pattern for this project: (1) audio thread writes meter peaks and parameter values into atomics or a single-producer ring buffer, never calls the web view; (2) a main-thread timer (CLAP `timer-support` where the host offers it, otherwise an OS timer on the GUI thread) drains it at 30 Hz, coalesces to the latest value per parameter, and sends one batched message per tick; (3) the page sends parameter gestures (begin, value, end) as messages that the main thread turns into CLAP parameter events queued to the audio thread; (4) nothing is sent until the page posts a "ready" message, and on ready the plugin sends a full state snapshot (this also covers hosts that destroy and recreate the GUI).
- Message hop latency is one main-thread tick plus cross-process IPC, so meters at 30 Hz lag by roughly one to two frames; this is acceptable for meters and automation display but not for sample-accurate visuals. Batch and use binary payloads for spectra or waveforms.
- Stop the timer when the view is hidden (WebView2 `put_IsVisible(FALSE)`, CLAP `hide()`), since hosts keep hidden editors alive.

### Gaps
- No published latency measurements for page-to-plugin messaging in a plugin context were found.
- No authoritative JUCE tutorial text on `WebSliderRelay` or meter data was retrieved in this pass.
- Throughput limits of `evaluateJavascript`/`postMessage` for large payloads on each platform were not found.

## 7. Non-web alternatives for a single cross-platform implementation

### Takeaway
If Linux must have the same UI as macOS and Windows from one codebase, a GPU-drawn toolkit embedded in the host window is the dependable route. Visage (MIT, v1.0.0, by the author of Vital) is the closest match to a CSS-styled look and already runs in CLAP plugins on all three systems. RmlUi is the only option that consumes HTML/CSS-like source, but it is not a browser and does not run JavaScript. All of them mean rewriting the existing TypeScript/HTML/CSS front end.

### Cited Findings
- Visage: [SRC] MIT. v1.0.0 tagged after Aug 2026 ("Just pushed a 1.0 release", mtytel). Direct3D 11 on Windows, Metal on macOS, Vulkan on Linux (Wayland or X11), WebGL via Emscripten. "Attach the UI to a window owned by a host, like an audio plugin." Flexbox layout, widgets, SVG, gradients, blend modes, blur, bloom, backdrop effects, custom shaders, emoji, Unicode text editing, partial rendering. [VitalAudio/visage](https://github.com/VitalAudio/visage). [ISSUE] An evaluator reports "16 embedded instances in one process at 60 fps, CLAP instances rendering inside REAPER's FX windows"; the maintainer was on leave during 2026 and a dozen PRs were open before 1.0. [visage issue 103](https://github.com/VitalAudio/visage/issues/103). [ISSUE] Linux: CLAP plugin crash on resize in Reaper and Bitwig (Oct 2025, fixed by a graphics dependency update), with residual black flashes or missing repaint when resizing. [visage issue 57](https://github.com/VitalAudio/visage/issues/57). Windows 11 resize crash (2025, closed). [visage issue 44](https://github.com/VitalAudio/visage/issues/44). bgfx initialised once and never shut down (2026, closed). [visage issue 99](https://github.com/VitalAudio/visage/issues/99). clap-wrapper maintainers have "run vstgui juce and visage plugs in the au host without problem." [clap-wrapper issue 362](https://github.com/free-audio/clap-wrapper/issues/362)
- VSTGUI: [SRC] Steinberg, "A user interface toolkit mainly for audio plug-ins", 4.15.1 (2026-07-15), actively maintained, 1,113 stars. [steinbergmedia/vstgui](https://github.com/steinbergmedia/vstgui)
- Dear ImGui: [SRC] MIT, 1.93.0 (2026-10-14). A CLAP support library exists: `free-audio/clap-imgui-support` (MIT, "Support library for using ImGui in your CLAP plugin", pushed 2026-08-18). [ocornut/imgui](https://github.com/ocornut/imgui); [clap-imgui-support](https://github.com/free-audio/clap-imgui-support)
- NanoVG with pugl (the DPF approach): [SRC] NanoVG is zlib licensed, OpenGL based, last pushed 2026-02-19 (low activity). pugl is ISC, "A minimal portable API for embeddable GUIs", actively maintained. DPF (ISC) builds its DGL on pugl. [nanovg](https://github.com/memononen/nanovg); [pugl](https://github.com/lv2/pugl); [DPF](https://github.com/DISTRHO/DPF)
- Elements (cycfi): [SRC] MIT, v1.0.2, C++20, declarative. "Elements is embeddable... you can utilize it for the creation of plugins." [cycfi/elements](https://github.com/cycfi/elements)
- egui and iced (Rust): [SRC] egui Apache-2.0 (GitHub SPDX), iced MIT; plugin embedding goes through `baseview` (Apache-2.0, pushed 2026-11-05) with adapters such as `egui-baseview` (MIT, "A baseview backend and NIH-plug adapter"). [egui](https://github.com/emilk/egui); [iced](https://github.com/iced-rs/iced); [baseview](https://github.com/RustAudio/baseview); [egui-baseview](https://github.com/BillyDM/egui-baseview). [ISSUE] A nih-plug CLAP wrapped to AU with clap-wrapper had a UI that was unresponsive in Logic Pro unless transport was running (2025, open). [clap-wrapper issue 362](https://github.com/free-audio/clap-wrapper/issues/362)
- Slint: [SRC] 1.19.0. Three licences: royalty-free licence for proprietary desktop, mobile or web apps, GPLv3, or commercial (needed for proprietary embedded). C++, Rust, JS, Python APIs with its own `.slint` markup. [slint-ui/slint](https://github.com/slint-ui/slint)
- RmlUi: [SRC] MIT, 6.3 (2026-08-22). "Based on the HTML and CSS standards"; "takes your HTML/CSS-like source files and turns them into vertices, indices and draw commands, and then you bring your own renderer." Supports "most of CSS2 with some CSS3 features" including flexbox, transforms, transitions, animations, filters and backdrop filters, plus data binding. "We do not aim to be fully compliant with CSS or HTML." Scripting is C++ (Lua plugin available), not JavaScript. [mikke89/RmlUi](https://github.com/mikke89/RmlUi)

Difficulty of reproducing a CSS-styled design (assessment, see Inferences): Visage medium; RmlUi medium for styling but full rewrite of logic; VSTGUI and Elements medium to high; NanoVG/pugl high (everything hand drawn); Dear ImGui high (immediate mode, default look is tool-like); egui high for bespoke styling, iced medium to high; Slint medium.

### Inferences
- None of these reuses the existing TypeScript UI. The honest comparison is "web view on macOS and Windows plus a reduced or absent Linux UI" versus "rewrite the UI once in a native toolkit for all three."
- Visage offers the nearest equivalents of CSS visual vocabulary (flex layout, gradients, shadows via blur, backdrop blur, rounded shapes) and has a WebGL/Emscripten target, so one Visage UI could in principle also run in the browser front end; that is a possible unification route in the opposite direction, at the cost of abandoning HTML/CSS.
- RmlUi is attractive only if the team values keeping an HTML/CSS authoring model; the application must supply windowing, a renderer backend and all embedding code, and existing TypeScript logic would be ported to C++.
- Rust toolkits are a poor fit for a hand-written C++ CLAP codebase.
- Slint's royalty-free licence appears to cover desktop plugins, but that should be read carefully before relying on it.

### Gaps
- VSTGUI's licence is not reported as an SPDX id by GitHub and was not opened in this pass (it is commonly described as BSD-style; unverified here).
- Embedding specifics (how each toolkit attaches to an HWND, NSView and X11 window) were confirmed from sources only for Visage (host-owned window) and pugl (embeddable by design). For Dear ImGui, Elements, Slint and RmlUi the embedding work was not verified; ImGui and RmlUi need a platform and renderer layer supplied by the plugin (for example pugl or clap-imgui-support).
- No evidence was found of Slint or RmlUi used in a shipping audio plugin.
- iced's and egui's Wayland or XEmbed status inside hosts was not checked.

## 8. Resizable and high-DPI windows through CLAP gui and clap-wrapper

### Takeaway
The CLAP gui contract is clear and small: physical pixels plus `set_scale` on Windows and X11, logical points and no `set_scale` on macOS, with host-driven resize through `can_resize`, `adjust_size`, `set_size` and plugin-driven resize through `request_resize`. In clap-wrapper the VST3 path has had its resize bugs fixed in 2025 and 2026, while the AUv2 path still has open defects (resize during `create()`, GUI destruction on sample-rate change in Live and REAPER) as of v0.18.0.

### Cited Findings

CLAP gui extension (CLAP 1.2.x)
- [DOC] Per API: `win32` "uses physical size", embed with `SetParent`; `cocoa` "uses logical size, don't call clap_plugin_gui->set_scale()"; `x11` "uses physical size", embed with XEmbed; `wayland` physical size, "embed is currently not supported, use floating windows." [CLAP gui.h](https://github.com/free-audio/clap/blob/main/include/clap/ext/gui.h)
- [DOC] `set_scale`: "Set the absolute GUI scaling factor, and override any OS info. Should not be used if the windowing api relies upon logical pixels." A plugin that prefers to query the OS itself can ignore the call and return false. [CLAP gui.h](https://github.com/free-audio/clap/blob/main/include/clap/ext/gui.h)
- [DOC] Embedded show sequence: `create()`, then `set_scale()`, then `can_resize()`, then if resizable with a remembered size `set_size()` else `get_size()`, then `set_parent()`, then `show()`. [CLAP gui.h](https://github.com/free-audio/clap/blob/main/include/clap/ext/gui.h)
- [DOC] Host drag resize: only if `can_resize()` is true; host calls `adjust_size(new_size)` to get a working size, then `set_size(working_size)`. `adjust_size` "does not change the size." `get_resize_hints` can declare per-axis resizability and aspect-ratio preservation. [CLAP gui.h](https://github.com/free-audio/clap/blob/main/include/clap/ext/gui.h)
- [DOC] Plugin-initiated resize: `clap_host_gui->request_resize(w, h)`, marked `[thread-safe & !floating]`; "if not called from the main thread, then a return value simply means that the host acknowledged the request and will process it asynchronously." All plugin-side gui calls are `[main-thread]`. [CLAP gui.h](https://github.com/free-audio/clap/blob/main/include/clap/ext/gui.h)
- [DOC] "The Embedding protocol is by far the most common, supported by all hosts to date, and a plugin author should support at least that case." [CLAP gui.h](https://github.com/free-audio/clap/blob/main/include/clap/ext/gui.h)

clap-wrapper (v0.18.0 on 2026-11-19; issue reports cite v0.14.0 and v0.16.0)
- [ISSUE] VST3, Reaper on Windows (Jul 2025, fixed in `next` via PR 411): `request_resize()` from a non-main thread failed because VST3 `IPlugFrame::resizeView` must be called on the main thread; the wrapper now defers to its idle callback. [clap-wrapper issue 404](https://github.com/free-audio/clap-wrapper/issues/404)
- [ISSUE] VST3 (2026): a prior bug (referenced as 572) involved "an inverted `tresult` and a cached rect handed to the host while a reentrant `getSize` was writing to it"; described as fixed. [clap-wrapper issue 573](https://github.com/free-audio/clap-wrapper/issues/573)
- [ISSUE] AUv2 (Sep 2026, closed after a second attempt): `request_resize` synchronously re-entered the plugin through `-setFrame:` into `set_size()`, ignored the calling thread, and always reported success. First fix (PR 574) was reverted because "it broke the editor in Logic"; second attempt (PR 576/577) landed in `next`. [clap-wrapper issue 573](https://github.com/free-audio/clap-wrapper/issues/573)
- [ISSUE] AUv2 (Oct 2026, open): if a plugin calls `request_resize()` inside `gui->create()`, the wrapper calls `setFrame:` on an NSView whose `initWithFrame:` has not run yet, and AppKit throws `NSInternalInconsistencyException: '_trackingAreaHelper not allocated yet!'`. [clap-wrapper issue 582](https://github.com/free-audio/clap-wrapper/issues/582)
- [ISSUE] AUv2 (Oct 2026, open): Ableton Live and REAPER call `Cleanup()` on sample-rate change, which destroys the plugin GUI (black window or crash). Logic 12.2 unaffected. Other AUv2 plugins (Serum, JUCE, Soundtoys) keep their GUI in the same situation. [clap-wrapper issue 595](https://github.com/free-audio/clap-wrapper/issues/595)
- [ISSUE] AUv3 (2026): window opened at a 400x500 placeholder instead of the declared size (closed); iOS subview ignores `adjust_size` (open). [clap-wrapper issue 519](https://github.com/free-audio/clap-wrapper/issues/519); [clap-wrapper issue 553](https://github.com/free-audio/clap-wrapper/issues/553)
- [ISSUE] Unrelated to GUI but relevant to the same wrapper in Ableton Live: VST3 state restore (`setComponentState`) was missing until 2026 (closed), and a VST3 parameter loading issue from 2024 is still open. [clap-wrapper issue 475](https://github.com/free-audio/clap-wrapper/issues/475); [clap-wrapper issue 303](https://github.com/free-audio/clap-wrapper/issues/303)

Host behaviour around VST3 scaling on Windows (general, not wrapper specific)
- [FORUM] JUCE forum: moving a VST3 between monitors with different DPI gave the wrong window size because the host never called `setContentScaleFactor`; Ableton Live calls `checkSizeConstraint` continuously during a drag. [JUCE forum 50712](https://forum.juce.com/t/wrong-window-size-when-moving-vst3-between-displays/50712) [SNIPPET]
- [FORUM] Steinberg forum: a VST3 that resizes by drag correctly "does not respond to drag resizing" once wrapped to AU with Steinberg's own auwrapper. [Steinberg forum 755381](https://forums.steinberg.net/t/auwrapper-and-gui-resizing/755381) [SNIPPET]

Web view specifics
- [ISSUE] CHOC, macOS (Dec 2024, fixed): "Resizing window results in content moved away on Apple WebKit." [choc issue 67](https://github.com/Tracktion/choc/issues/67)
- [FORUM] JUCE forum thread title "WebView Resize Jitter Bug" exists (not opened). [JUCE forum 68515](https://forum.juce.com/t/webview-resize-jitter-bug/68515) [SNIPPET]
- [DOC] WebView2 bounds and rasterization scale controls as described in section 1. [ICoreWebView2Controller3](https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2controller3)

### Inferences
- With a web view the plugin does not need to compute layout natively; it only needs to resize the child view in `set_size` and let CSS reflow. That makes `can_resize = true` cheap, provided `adjust_size` enforces a minimum and (optionally) an aspect ratio.
- To stay clear of the open AUv2 wrapper bugs: never call `request_resize` from inside `create()`; call it only from the main thread after `show()`; treat `destroy()` followed by a fresh `create()` as normal and rebuild page state from the plugin; and test sample-rate changes in Live and REAPER with the AU build.
- On Windows the plugin should accept `set_scale`, store it, and apply it as the WebView2 rasterization scale while sizing the HWND in physical pixels. If the host never calls `set_scale` (or the wrapper does not forward one), fall back to the monitor DPI of the parent HWND.
- On macOS the existing WKWebView path needs no scale handling because CLAP `cocoa` sizes are logical points and WebKit handles the backing scale.
- Pin clap-wrapper to a release at or after the `next` fixes for issues 404 and 573, and re-test Logic whenever it is bumped, since one resize fix already had to be reverted for breaking Logic.

### Gaps
- Could not confirm from source how clap-wrapper's VST3 wrapper maps `IPlugViewContentScaleSupport::setContentScaleFactor` to `clap_plugin_gui->set_scale`, or what scale an AUv2/VST3-wrapped plugin receives on Windows per host. A GitHub search for "HiDPI" in clap-wrapper issues returned nothing.
- No reports specific to web view plugins resized through clap-wrapper were found.
- Per-host Windows behaviour for CLAP `set_scale` (Bitwig, Reaper, FL Studio, Studio One) was not found documented.
- Whether issue 573's fix is included in v0.18.0 or only in `next` was not verified.
