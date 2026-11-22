// No window on this system yet: the host shows its own controls. See gui.h.

#include "gui.h"

namespace fxgui {

bool supported() { return false; }
Window* create(Model*) { return nullptr; }
void destroy(Window*) {}
void size(Window*, uint32_t* width, uint32_t* height) { *width = *height = 0; }
bool attach(Window*, void*) { return false; }
void show(Window*, bool) {}

}  // namespace fxgui
