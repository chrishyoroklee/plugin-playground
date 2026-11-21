// The three functions a CLAP entry point is made of, kept in the static library so that
// clap-wrapper can link the same plugin into a .clap, a .vst3 and an Audio Unit.
#pragma once

extern "C" {
bool fx_entry_init(const char* path);
void fx_entry_deinit(void);
const void* fx_entry_get_factory(const char* factory_id);
}
