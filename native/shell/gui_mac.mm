// The plugin's window on macOS: face.html in a WKWebView. See gui.h.
//
// The page and the plugin talk in the few messages listed at the top of face.html. The
// page asks for the face when it has loaded; after that a timer tells it where the dials
// are whenever the host has moved one, and it tells the plugin when a dial is dragged.
// A plugin with presets has a menu of them along the top; choosing one can change the
// program, and then the page is given that program's face.
//
// One Objective-C object is needed, to receive the page's messages and the timer. Its
// class is made when the plugin is loaded and named after where this binary sits in
// memory, not written out as a class. That is deliberate. Every device's plugin is a
// copy of the same binary, and a host that loads two of them would find the same class
// in both: the Objective-C runtime keeps one, warns of "mysterious crashes", and the
// other plugin's objects would run the first one's code.

#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>
#import <objc/runtime.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <string>
#include <vector>

#include "face_html.h"  // kFaceHtml: face.html with its fonts, made when the plugin is built
#include "gui.h"

namespace fxgui {

// The window is drawn larger than the patch lays it out: a Live device is 169 px tall,
// which is small for a window of its own. A device with many dials is wide, though, and
// is drawn less large so that it still fits across a laptop's screen.
static const double kZoom = 1.5, kWidest = 1240;
// The preset menu's strip above the device, in the face's units (face.html, BAR).
static const double kBar = 26;

struct Window {
    Model* model = nullptr;
    WKWebView* web = nil;
    id handler = nil;
    NSTimer* timer = nil;
    std::vector<double> shown;  // the values the page was last told
    int shownProgram = -1, shownChosen = -2;
    bool ready = false;
    double width = 0, height = 0;

    void message(NSDictionary* body);
    void tick(bool all);
};

}  // namespace fxgui

namespace fxgui {

// The window an object of the link class passes things on to: kept on the object itself.
static char kWindowKey;
static Window* windowOf(id link) {
    NSValue* held = objc_getAssociatedObject(link, &kWindowKey);
    return held ? static_cast<Window*>(held.pointerValue) : nullptr;
}
static void setWindow(id link, Window* window) {
    objc_setAssociatedObject(link, &kWindowKey, window ? [NSValue valueWithPointer:window] : nil, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
}

static void linkMessage(id self, SEL, WKUserContentController*, WKScriptMessage* message) {
    Window* window = windowOf(self);
    if (window && [message.body isKindOfClass:[NSDictionary class]]) window->message(message.body);
}
static void linkTick(id self, SEL, NSTimer*) {
    if (Window* window = windowOf(self)) window->tick(false);
}

// This binary's own class for that object, made once.
static Class linkClass() {
    static Class made = nil;
    if (made) return made;
    char name[64];
    snprintf(name, sizeof name, "PPFaceLink_%p", static_cast<void*>(&kWindowKey));
    Class cls = objc_allocateClassPair([NSObject class], name, 0);
    if (!cls) return made = objc_getClass(name);
    class_addMethod(cls, @selector(userContentController:didReceiveScriptMessage:), (IMP)linkMessage, "v@:@@");
    class_addMethod(cls, @selector(tick:), (IMP)linkTick, "v@:@");
    class_addProtocol(cls, @protocol(WKScriptMessageHandler));
    objc_registerClassPair(cls);
    return made = cls;
}

}  // namespace fxgui

namespace fxgui {

// The size a program's face is drawn at, with the preset strip if there is one. False if
// the face cannot be read.
static bool sizeOf(Model* model, int program, double* width, double* height) {
    NSData* packed = [[NSData alloc] initWithBase64EncodedString:@(model->face(program)) options:0];
    NSDictionary* face = packed ? [NSJSONSerialization JSONObjectWithData:packed options:0 error:nil] : nil;
    if (![face isKindOfClass:[NSDictionary class]]) return false;
    const double w = [face[@"width"] doubleValue], h = [face[@"height"] doubleValue] + (model->presets() > 0 ? kBar : 0);
    if (!(w > 0) || !(h > 0)) return false;
    const double zoom = std::max(1.0, std::min(kZoom, kWidest / w));
    *width = std::ceil(w * zoom);
    *height = std::ceil(h * zoom);
    return true;
}

void Window::message(NSDictionary* body) {
    NSString* type = body[@"type"];
    if (![type isKindOfClass:[NSString class]] || !model) return;
    if ([type isEqualToString:@"ready"]) {
        if (model->presets() > 0) {
            NSString* list = [NSString stringWithUTF8String:model->presetList().c_str()];
            [web evaluateJavaScript:[NSString stringWithFormat:@"ppPresets(%@)", list] completionHandler:nil];
        }
        ready = true;
        tick(true);
        return;
    }
    NSNumber* index = body[@"index"];
    if (![index isKindOfClass:[NSNumber class]]) return;
    const int i = index.intValue;
    if ([type isEqualToString:@"preset"]) {
        model->choose(i);
        tick(false);
        return;
    }
    if (i < 0 || i >= model->count()) return;
    if ([type isEqualToString:@"begin"]) model->grab(i);
    else if ([type isEqualToString:@"end"]) model->release(i);
    else if ([type isEqualToString:@"set"] && [body[@"value"] isKindOfClass:[NSNumber class]]) {
        const double value = [body[@"value"] doubleValue];
        model->set(i, value);
        // What the plugin made of it is what the page already shows, unless it was clamped.
        if (i < static_cast<int>(shown.size())) shown[i] = value;
    }
}

// Tell the page where the dials are, if any has moved since it was last told.
void Window::tick(bool all) {
    if (!ready || !model) return;
    if (model->current() != shownProgram) {
        // Another program: its face, and its size if the host lets the window change it
        // (a synth preset with more effects after it is wider). If it does not, the page
        // fits the face into the window as it is. The face went into the file as base64
        // and the page takes it apart again.
        const bool first = shownProgram < 0;
        shownProgram = model->current();
        double w = 0, h = 0;
        if (!first && sizeOf(model, shownProgram, &w, &h) && (w != width || h != height) &&
            model->resize(static_cast<uint32_t>(w), static_cast<uint32_t>(h))) {
            width = w;
            height = h;
            NSRect frame = web.frame;
            frame.size = NSMakeSize(w, h);
            web.frame = frame;
        }
        NSString* script = [NSString stringWithFormat:@"ppFace(JSON.parse(atob('%s')), %s)", model->face(shownProgram),
                                                     model->dialsOf(shownProgram).c_str()];
        [web evaluateJavaScript:script completionHandler:nil];
        all = true;
    }
    if (model->chosen() != shownChosen) {
        shownChosen = model->chosen();
        [web evaluateJavaScript:[NSString stringWithFormat:@"ppChosen(%d)", shownChosen] completionHandler:nil];
    }
    const int n = model->count();
    bool changed = all || static_cast<int>(shown.size()) != n;
    shown.resize(n, 0.0);
    NSMutableString* list = [NSMutableString stringWithString:@"ppValues(["];
    for (int i = 0; i < n; i++) {
        const double v = model->value(i);
        if (v != shown[i]) changed = true;
        shown[i] = v;
        [list appendFormat:@"%s%.17g", i ? "," : "", v];
    }
    if (!changed) return;
    [list appendString:@"])"];
    [web evaluateJavaScript:list completionHandler:nil];
}

bool supported() { return true; }

Window* create(Model* model) {
    @autoreleasepool {
        // The window's size is the face's, which only the face knows: the face of the
        // program playing. Every program's has to be readable.
        double width = 0, height = 0;
        for (int k = 0; k < model->programs(); k++)
            if (!sizeOf(model, k, &width, &height)) return nullptr;
        sizeOf(model, model->current(), &width, &height);

        Window* window = new Window();
        window->model = model;
        window->width = width;
        window->height = height;

        id link = [[linkClass() alloc] init];
        setWindow(link, window);
        window->handler = link;
        WKWebViewConfiguration* config = [[WKWebViewConfiguration alloc] init];
        [config.userContentController addScriptMessageHandler:link name:@"pp"];
        window->web = [[WKWebView alloc] initWithFrame:NSMakeRect(0, 0, window->width, window->height) configuration:config];
        window->web.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
        NSString* html = [[NSString alloc] initWithBytes:kFaceHtml length:sizeof(kFaceHtml) - 1 encoding:NSUTF8StringEncoding];
        [window->web loadHTMLString:html baseURL:nil];

        // In the common modes, so the dials keep following the host while a menu is open.
        window->timer = [NSTimer timerWithTimeInterval:1.0 / 30 target:link selector:@selector(tick:) userInfo:nil repeats:YES];
        [[NSRunLoop mainRunLoop] addTimer:window->timer forMode:NSRunLoopCommonModes];
        return window;
    }
}

void destroy(Window* window) {
    if (!window) return;
    @autoreleasepool {
        [window->timer invalidate];
        setWindow(window->handler, nullptr);
        [window->web.configuration.userContentController removeScriptMessageHandlerForName:@"pp"];
        [window->web removeFromSuperview];
        window->web = nil;
        window->handler = nil;
        window->timer = nil;
    }
    delete window;
}

void size(Window* window, uint32_t* width, uint32_t* height) {
    *width = window ? static_cast<uint32_t>(window->width) : 0;
    *height = window ? static_cast<uint32_t>(window->height) : 0;
}

bool attach(Window* window, void* parent) {
    if (!window || !parent) return false;
    NSView* host = (__bridge NSView*)parent;
    window->web.frame = NSMakeRect(0, 0, window->width, window->height);
    [host addSubview:window->web];
    return true;
}

void show(Window* window, bool visible) {
    if (window) window->web.hidden = !visible;
}

}  // namespace fxgui
