// The exported symbol each plugin format looks for. Compiled once per format by
// clap-wrapper's make_clapfirst_plugins; everything else is in plugin.cpp.
#include <clap/clap.h>

#include "entry.h"

extern "C" {
#ifdef __GNUC__
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wattributes"
#endif

const CLAP_EXPORT struct clap_plugin_entry clap_entry = {CLAP_VERSION, fx_entry_init, fx_entry_deinit,
                                                         fx_entry_get_factory};

#ifdef __GNUC__
#pragma GCC diagnostic pop
#endif
}
