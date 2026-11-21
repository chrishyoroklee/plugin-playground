// Plugin Playground.app: the helper on macOS. The installer puts it in Applications and
// macOS then opens .ppdevice files with it (see the document type in its Info.plist,
// which scripts/plugin.py writes). Opening one makes the device's plugins from the
// runtime inside this app (Contents/Resources/Runtime) and says so. That is all it does;
// it quits afterwards.
//
// Opened on its own it says what it is for. Given `--install FILE` it does the same work
// with no window, which is how the tests run it.

#import <Cocoa/Cocoa.h>

#include "install.h"

namespace fs = std::filesystem;

static NSString* const kSite = @"https://chrishyoroklee.github.io/plugin-playground/";

static fs::path runtimeFolder() {
    return fs::path([[[NSBundle mainBundle] resourcePath] fileSystemRepresentation]) / "Runtime";
}

// PP_HELPER_LOG names a file to write what would have been said, with no window: a test
// can then open a device the way Finder does and read the outcome.
static bool say(NSString* title, NSString* detail, bool failed, bool offerSite) {
    const char* log = getenv("PP_HELPER_LOG");
    if (log && *log) {
        NSString* text = [NSString stringWithFormat:@"%@\n%@\n%@\n", failed ? @"failed" : @"ok", title, detail];
        [text writeToFile:@(log) atomically:YES encoding:NSUTF8StringEncoding error:nil];
        return false;
    }
    [NSApp activateIgnoringOtherApps:YES];
    NSAlert* alert = [[NSAlert alloc] init];
    alert.messageText = title;
    alert.informativeText = detail;
    alert.alertStyle = failed ? NSAlertStyleWarning : NSAlertStyleInformational;
    [alert addButtonWithTitle:@"OK"];
    if (offerSite) [alert addButtonWithTitle:@"Open Plugin Playground"];
    return [alert runModal] == NSAlertSecondButtonReturn;
}

static void openDevices(NSArray<NSURL*>* urls) {
    NSMutableArray<NSString*>* done = [NSMutableArray array];
    NSString* problem = nil;
    bool instrument = false;
    for (NSURL* url in urls) {
        const fs::path file([[url path] fileSystemRepresentation]);
        const ppinstall::Result result = ppinstall::install(file, runtimeFolder(), ppinstall::userFolders());
        if (result.ok) {
            [done addObject:@(result.name.c_str())];
            // the same file, read once more for its kind: the message names where Logic lists it
            mxvm::Device device;
            std::ifstream in(file, std::ios::binary);
            std::ostringstream text;
            text << in.rdbuf();
            if (device.parse(text.str(), nullptr)) instrument = device.instrument;
        } else {
            problem = [NSString stringWithFormat:@"%@: %@", [url lastPathComponent], @(result.message.c_str())];
        }
    }
    if (done.count > 0) {
        NSString* title = done.count == 1 ? [NSString stringWithFormat:@"“%@” is installed", done[0]]
                                          : [NSString stringWithFormat:@"%lu plugins are installed", (unsigned long)done.count];
        NSString* where = instrument ? @"Instrument ▸ AU Instruments ▸ Plugin Playground"
                                     : @"Audio FX ▸ Audio Units ▸ Plugin Playground";
        NSString* detail = [NSString stringWithFormat:@"It is an Audio Unit, a VST3 and a CLAP plugin. Restart your DAW to see it. "
                                                      @"In Logic Pro and GarageBand it is under %@.%@",
                                                      where, problem ? [@"\n\n" stringByAppendingString:problem] : @""];
        say(title, detail, false, false);
    } else {
        say(@"The plugin could not be installed", problem ?: @"That is not a Plugin Playground device.", true, false);
    }
}

@interface Helper : NSObject <NSApplicationDelegate>
@end

@implementation Helper {
    BOOL _opened;
}

- (void)application:(NSApplication*)application openURLs:(NSArray<NSURL*>*)urls {
    _opened = YES;
    openDevices(urls);
    [NSApp terminate:nil];
}

- (void)applicationDidFinishLaunching:(NSNotification*)notification {
    // A device being opened arrives a moment after launch. If none does, this was the
    // app opened on its own.
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.6 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        if (self->_opened) return;
        if (say(@"Plugin Playground is installed",
                @"Make an effect or an instrument on the Plugin Playground site and press Download. Open the file you "
                @"get, and it becomes a plugin in every DAW on this Mac.",
                false, true))
            [[NSWorkspace sharedWorkspace] openURL:[NSURL URLWithString:kSite]];
        [NSApp terminate:nil];
    });
}

@end

int main(int argc, const char** argv) {
    if (argc >= 3 && strcmp(argv[1], "--install") == 0) {
        @autoreleasepool {
            ppinstall::Folders folders = ppinstall::userFolders();
            if (argc >= 5 && strcmp(argv[3], "--to") == 0) folders = ppinstall::foldersUnder(argv[4]);
            const ppinstall::Result result = ppinstall::install(argv[2], runtimeFolder(), folders);
            if (!result.ok) {
                fprintf(stderr, "%s\n", result.message.c_str());
                return 1;
            }
            printf("Installed %s.\n", result.name.c_str());
            for (const fs::path& made : result.made) printf("  %s\n", made.c_str());
            return 0;
        }
    }
    @autoreleasepool {
        NSApplication* app = [NSApplication sharedApplication];
        Helper* helper = [[Helper alloc] init];
        app.delegate = helper;
        [app setActivationPolicy:NSApplicationActivationPolicyAccessory];
        [app run];
    }
    return 0;
}
