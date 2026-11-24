// guicheck: open a plugin's window the way a host does, and look at it. macOS only.
//
//   guicheck <the plugin's binary> <a folder for pictures>
//
// It loads the .clap, asks it for a window, puts that in a window of its own off the
// edge of the screen, and then checks the three things a window is for:
//
//   it draws           a picture of it is saved (window.png), with every dial on it
//   it follows         the host moves a dial; the window shows the new value (moved.png)
//   it leads           a dial is turned in the window; the host is told, as one gesture
//
// hostcheck covers everything about the plugin that has no window in it.

#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>

#include <dlfcn.h>
#include <string>
#include <vector>

#include <clap/clap.h>

static int failures = 0, checks = 0;
static void check(bool ok, const char* what) {
    checks++;
    if (!ok) {
        failures++;
        fprintf(stderr, "guicheck: FAILED: %s\n", what);
    }
}

static int flushesAsked = 0, rescans = 0;
static void hostRescan(const clap_host_t*, clap_param_rescan_flags) { rescans++; }
static void hostClear(const clap_host_t*, clap_id, clap_param_clear_flags) {}
static void hostFlush(const clap_host_t*) { flushesAsked++; }
static const clap_host_params_t kHostParams = {hostRescan, hostClear, hostFlush};
static const void* hostExtension(const clap_host_t*, const char* id) {
    return strcmp(id, CLAP_EXT_PARAMS) == 0 ? &kHostParams : nullptr;
}
static void hostNothing(const clap_host_t*) {}
static const clap_host_t kHost = {CLAP_VERSION_INIT, nullptr, "guicheck", "Plugin Playground", "", "1", hostExtension,
                                  hostNothing, hostNothing, hostNothing};

// What the plugin tells the host, and one thing the host tells the plugin.
struct Told { uint16_t type; clap_id id; double value; };
static std::vector<Told> told;
static bool pushOut(const clap_output_events_t*, const clap_event_header_t* h) {
    Told t = {h->type, 0, 0};
    if (h->type == CLAP_EVENT_PARAM_VALUE) {
        t.id = reinterpret_cast<const clap_event_param_value_t*>(h)->param_id;
        t.value = reinterpret_cast<const clap_event_param_value_t*>(h)->value;
    } else if (h->type == CLAP_EVENT_PARAM_GESTURE_BEGIN || h->type == CLAP_EVENT_PARAM_GESTURE_END) {
        t.id = reinterpret_cast<const clap_event_param_gesture_t*>(h)->param_id;
    }
    told.push_back(t);
    return true;
}
static clap_event_param_value_t sending;
static bool haveSending = false;
static uint32_t inSize(const clap_input_events_t*) { return haveSending ? 1 : 0; }
static const clap_event_header_t* inGet(const clap_input_events_t*, uint32_t) { return &sending.header; }

static void spin(double seconds) {
    [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:seconds]];
}

static WKWebView* webIn(NSView* view) {
    if ([view isKindOfClass:[WKWebView class]]) return (WKWebView*)view;
    for (NSView* sub in view.subviews)
        if (WKWebView* found = webIn(sub)) return found;
    return nil;
}

static NSString* ask(WKWebView* web, NSString* script) {
    __block NSString* answer = nil;
    __block bool done = false;
    [web evaluateJavaScript:script completionHandler:^(id result, NSError* error) {
        answer = error ? [NSString stringWithFormat:@"error: %@", error.localizedDescription] : [NSString stringWithFormat:@"%@", result];
        done = true;
    }];
    for (int i = 0; i < 200 && !done; i++) spin(0.02);
    return answer ?: @"(no answer)";
}

static bool picture(WKWebView* web, NSString* path) {
    __block bool done = false, ok = false;
    [web takeSnapshotWithConfiguration:nil completionHandler:^(NSImage* image, NSError*) {
        if (image) {
            NSBitmapImageRep* rep = [[NSBitmapImageRep alloc] initWithData:[image TIFFRepresentation]];
            ok = [[rep representationUsingType:NSBitmapImageFileTypePNG properties:@{}] writeToFile:path atomically:YES];
        }
        done = true;
    }];
    for (int i = 0; i < 300 && !done; i++) spin(0.02);
    return ok;
}

int main(int argc, char** argv) {
    @autoreleasepool {
        if (argc != 3) {
            fprintf(stderr, "usage: guicheck <plugin binary> <folder for pictures>\n");
            return 2;
        }
        NSString* out = @(argv[2]);
        [[NSFileManager defaultManager] createDirectoryAtPath:out withIntermediateDirectories:YES attributes:nil error:nil];
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];

        void* library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
        const auto* entry = library ? static_cast<const clap_plugin_entry_t*>(dlsym(library, "clap_entry")) : nullptr;
        if (!entry || !entry->init(argv[1])) {
            fprintf(stderr, "guicheck: %s is not a loadable CLAP plugin\n", argv[1]);
            return 1;
        }
        const auto* factory = static_cast<const clap_plugin_factory_t*>(entry->get_factory(CLAP_PLUGIN_FACTORY_ID));
        const clap_plugin_descriptor_t* desc = factory->get_plugin_descriptor(factory, 0);
        const clap_plugin_t* plugin = factory->create_plugin(factory, &kHost, desc->id);
        check(plugin && plugin->init(plugin), "the plugin can be created");
        if (failures) return 1;
        const auto* gui = static_cast<const clap_plugin_gui_t*>(plugin->get_extension(plugin, CLAP_EXT_GUI));
        const auto* params = static_cast<const clap_plugin_params_t*>(plugin->get_extension(plugin, CLAP_EXT_PARAMS));
        check(gui != nullptr, "the plugin has a window");
        if (failures) return 1;
        check(gui->is_api_supported(plugin, CLAP_WINDOW_API_COCOA, false), "the window can sit inside a host's");
        check(gui->create(plugin, CLAP_WINDOW_API_COCOA, false), "the window can be made");
        uint32_t width = 0, height = 0;
        check(gui->get_size(plugin, &width, &height) && width > 100 && height > 100, "the window says how big it is");

        // A window off the edge of the screen: it has to be a real one for the page to draw.
        NSWindow* window = [[NSWindow alloc] initWithContentRect:NSMakeRect(-10000, -10000, width, height)
                                                       styleMask:NSWindowStyleMaskBorderless
                                                         backing:NSBackingStoreBuffered
                                                           defer:NO];
        clap_window_t parent = {CLAP_WINDOW_API_COCOA, {(__bridge void*)window.contentView}};
        check(gui->set_parent(plugin, &parent), "the window goes into the host's");
        check(gui->show(plugin), "the window can be shown");
        [window orderBack:nil];

        WKWebView* web = webIn(window.contentView);
        check(web != nil, "there is a page in the window");
        if (failures) return 1;
        NSString* dials = @"0";
        for (int i = 0; i < 100 && [dials intValue] == 0; i++) {
            spin(0.1);
            dials = ask(web, @"document.querySelectorAll('.dial').length");
        }
        const uint32_t count = params->count(plugin);
        // A plugin with presets has a menu, and its dials are every program's: the window
        // shows the ones the program playing has.
        const bool menu = [ask(web, @"document.getElementById('bar').hidden ? 0 : 1") intValue] == 1;
        const uint32_t shown = static_cast<uint32_t>([dials intValue]);
        check(count > 0 && (menu ? shown > 0 && shown <= count : shown == count), "the window shows a dial for each parameter it has");
        check(picture(web, [out stringByAppendingPathComponent:@"window.png"]), "a picture of the window can be taken");

        // 1. It follows: the host sets the first parameter to the top of its range.
        clap_param_info_t info;
        params->get_info(plugin, 0, &info);
        NSString* before = ask(web, @"document.querySelector('.dial .readout').textContent");
        sending = {};
        sending.header = {sizeof sending, 0, CLAP_CORE_EVENT_SPACE_ID, CLAP_EVENT_PARAM_VALUE, 0};
        sending.param_id = info.id;
        sending.note_id = sending.port_index = sending.channel = sending.key = -1;
        sending.value = info.max_value;
        haveSending = true;
        clap_input_events_t in = {nullptr, inSize, inGet};
        clap_output_events_t outEvents = {nullptr, pushOut};
        params->flush(plugin, &in, &outEvents);
        haveSending = false;
        spin(0.4);
        NSString* after = ask(web, @"document.querySelector('.dial .readout').textContent");
        char text[64] = "";
        params->value_to_text(plugin, info.id, info.max_value, text, sizeof text);
        check(![before isEqualToString:after], "a dial moves when the host moves its parameter");
        check([after isEqualToString:@(text)], "the dial reads what the host reads");
        picture(web, [out stringByAppendingPathComponent:@"moved.png"]);

        // 2. It leads: the second dial (the first, if there is only one) is turned in the window.
        const uint32_t which = count > 1 ? 1 : 0;
        params->get_info(plugin, which, &info);
        double start = 0;
        params->get_value(plugin, info.id, &start);
        told.clear();
        flushesAsked = 0;
        ask(web, [NSString stringWithFormat:@"document.querySelectorAll('.dial')[%u].dispatchEvent(new WheelEvent('wheel', "
                                            @"{deltaY: %d, bubbles: true, cancelable: true})); 1", which,
                                            start > (info.min_value + info.max_value) / 2 ? 300 : -300]);
        spin(0.6);  // the page ends a wheel gesture a moment after the wheel stops
        check(flushesAsked > 0, "turning a dial asks the host to listen");
        in = {nullptr, inSize, inGet};
        params->flush(plugin, &in, &outEvents);
        double now = start;
        params->get_value(plugin, info.id, &now);
        check(now != start, "turning a dial in the window changes the parameter");
        bool began = false, ended = false, valued = false, ordered = true;
        for (const Told& t : told) {
            if (t.id != info.id) continue;
            if (t.type == CLAP_EVENT_PARAM_GESTURE_BEGIN) began = true;
            if (t.type == CLAP_EVENT_PARAM_VALUE) { valued = true; ordered = ordered && began && !ended && t.value == now; }
            if (t.type == CLAP_EVENT_PARAM_GESTURE_END) ended = true;
        }
        check(began && valued && ended && ordered, "the host is told: grabbed, the new value, let go");

        // 3. Presets: the arrow picks the next one, the host is told the values changed, and
        // the menu says which it is on.
        if (menu) {
            NSString* was = ask(web, @"document.getElementById('pick').value");
            rescans = 0;
            ask(web, @"document.getElementById('next').click(); 1");
            spin(0.5);
            NSString* now = ask(web, @"document.getElementById('pick').value");
            check(rescans > 0, "choosing a preset tells the host the values changed");
            check(![now isEqualToString:was] && ![now isEqualToString:@"none"], "the menu shows the preset chosen");
            const int options = [ask(web, @"document.getElementById('pick').options.length") intValue];
            check(options > 2, "the menu lists the presets");
            // Every dial in the window reads what the host reads for its parameter, the
            // preset's program having its own dials, numbered its own way.
            NSString* shownText = ask(web, @"Array.from(document.querySelectorAll('.dial')).map(function (d) { "
                                           @"return d.dataset.index + '=' + d.querySelector('.readout').textContent; }).join('|')");
            bool same = shownText.length > 0;
            for (NSString* pair in [shownText componentsSeparatedByString:@"|"]) {
                NSArray* parts = [pair componentsSeparatedByString:@"="];
                if (parts.count != 2) { same = false; break; }
                clap_param_info_t dialInfo;
                double v = 0;
                char hostText[64] = "";
                const uint32_t at = static_cast<uint32_t>([parts[0] intValue]);
                same = same && params->get_info(plugin, at, &dialInfo) && params->get_value(plugin, dialInfo.id, &v) &&
                       params->value_to_text(plugin, dialInfo.id, v, hostText, sizeof hostText) && [parts[1] isEqualToString:@(hostText)];
                if (!same) { fprintf(stderr, "guicheck: dial %u shows %s, the host %s\n", at, [parts[1] UTF8String], hostText); break; }
            }
            check(same, "after a preset, each dial shows its own parameter's value");
            picture(web, [out stringByAppendingPathComponent:@"preset.png"]);
        }

        gui->hide(plugin);
        gui->destroy(plugin);
        plugin->destroy(plugin);
        entry->deinit();
        if (failures) {
            fprintf(stderr, "guicheck: %d of %d checks failed for %s\n", failures, checks, desc->name);
            return 1;
        }
        printf("guicheck: ok, %d checks, a %u by %u window (%s)\n", checks, width, height, desc->name);
        return 0;
    }
}
