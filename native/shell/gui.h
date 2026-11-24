// The plugin's own window: the device as the page draws it, in place of the host's
// generic sliders. One page (native/ui/face.html) in a web view, drawn from the face the
// device carries.
//
// Written for macOS (gui_mac.mm, a WKWebView). On Windows and Linux there is none yet
// (gui_none.cpp), and the host shows its own controls, as before.
//
// Everything here is called on the host's main thread.

#pragma once

#include <cstdint>
#include <string>

namespace fxgui {

// What a window needs from the plugin it shows.
struct Model {
    virtual ~Model() = default;
    virtual int count() = 0;                        // how many dials
    virtual double value(int index) = 0;            // in the dial's own units
    virtual void grab(int index) = 0;               // the dial was taken hold of
    virtual void set(int index, double value) = 0;  // ...moved
    virtual void release(int index) = 0;            // ...and let go
    virtual const char* face(int program) = 0;      // patchc's face(): JSON in base64
    virtual std::string dialsOf(int program) = 0;   // JSON: the plugin's number for each dial of the face, or "null" for the same
    virtual int programs() = 0;                     // how many faces there are: one per program
    virtual int current() = 0;                      // the program playing, whose face is shown
    virtual int presets() = 0;                      // how many presets the menu has; 0 for no menu
    virtual int chosen() = 0;                       // the one last chosen, or -1
    virtual void choose(int index) = 0;             // a preset was picked in the menu
    virtual std::string presetList() = 0;           // [{name, values: [by dial]}], as JSON
    virtual bool resize(uint32_t width, uint32_t height) = 0;  // ask the host for a new size; false if it says no
};

struct Window;

// Whether this build has a window at all.
bool supported();
// A window for the model's face, or null if the face cannot be read.
Window* create(Model* model);
void destroy(Window* window);
void size(Window* window, uint32_t* width, uint32_t* height);
// Put the window inside the host's. `parent` is what the host calls its window: an NSView.
bool attach(Window* window, void* parent);
void show(Window* window, bool visible);

}  // namespace fxgui
