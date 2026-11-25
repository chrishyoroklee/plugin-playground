# Changelog

## 0.1.0 beta 1

The first public beta. Everything below is new.

### Making a device

- **Effects**: twelve plugins in categories (filter; Three Band, Parametric, Graphic and
  Tilt EQ; compressor; distortion; bitcrusher; chorus and flanger; tremolo; delay; and a
  reverb that is a room, a hall or a plate), racks of several in a row, and 100 presets.
  Each plugin holds its presets; pick one from its list or the menu on its header.
- **Instrument**: a polyphonic synth with 41 presets, played from the on-screen keyboard,
  the computer keyboard, a MIDI keyboard or a looping phrase.
- **Edit**: add, remove and reorder effects, pick each one's type, add knobs of your own
  that turn several dials at once, and make any dial turn by itself (an LFO, or the
  input's level).
- **Describe it**: a device from a sentence, with your own Anthropic key, or free with
  any chatbot by copying a prompt and pasting the answer back.
- **Listen** on a library of sounds, including dry electric guitar for distortion and
  fuzz, or on your own audio file. Pictures above the dials show EQ curves, filters, the
  compressor and the synth's waves as they sound.
- A **link** opens exactly the device you had.
- A **quick tour** greets a first visit: four short cards, each with a small animation,
  that can be skipped. Help shows it again.

### Taking it away

- **Max for Live**: an `.amxd` for Ableton Live, with every dial a Live parameter.
- **Plugin**: one installer, named after your device, for macOS (`.pkg`: Audio Unit,
  VST3 and CLAP), Windows (setup `.exe`: VST3 and CLAP) or Linux (`.tar.gz`: VST3 and
  CLAP). It installs Plugin Playground and makes your device into a plugin with its
  presets. On macOS the plugin opens with the same face as the page, and a preset menu.
- **Build it yourself**: the settings as JSON, and the command that compiles them into a
  plugin of its own.

### Checked

- Every preset builds in Chromium, Firefox and WebKit, and its sound is measured.
- The effects match a Max 9 render to the last bit for the filter, the distortions, the
  delay, the flanger and the reverb.
- On Linux, macOS and Windows, CI makes every preset into a plugin, loads it, compares
  it with the device it came from, installs the page's installer with no window, and
  runs clap-validator, pluginval and (macOS) auval.
- A crafted device file or a name shared by another maker's plugin cannot crash a DAW or
  replace that plugin.

### Known limits

- The installers are not signed, so macOS asks you to choose Open Anyway and Windows to
  choose Run anyway, for each download.
- The plugin's own window and preset menu are on macOS only. On Windows and Linux the
  DAW shows its own sliders.
- Nobody has yet loaded the plugins in a DAW by hand, or clicked through the Windows and
  Linux installers. Please tell us how it goes.
