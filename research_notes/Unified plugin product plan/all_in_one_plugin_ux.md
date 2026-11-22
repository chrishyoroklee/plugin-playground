# All-in-one multi-effect and instrument plugin UX (layout, browsing, macros, lock modes), 2025-2026

Reading notes for the report writer:

- "Fetched" means the page itself was read. "Search snippet" means the detail came only from a search result summary of that URL and was not read in full, so treat it as lower confidence.
- Anything older than 2022 is marked "(older)".
- No hands-on testing was done. Everything here is from manuals, vendor announcements, reviews and forum threads.

## 1. Neural DSP Archetype plugins (Cory Wong X and the "X" redesigns)

### Takeaway
Archetype X plugins present a fixed left-to-right signal chain as a short row of section icons at the top (Pre FX, Amp, Cab, EQ, Post FX), with a persistent global strip for presets, input/output, gate, transpose, doubler and tuner. The X generation added drag-to-resize, a left-panel Preset Explorer with tags and favorites, undo/redo, tooltips and a "Global Lock" that protects global settings across preset changes. Reviewers praise the signposting and consistency, and the only recurring criticism is "where do I start" when many effects are on offer.

### Cited Findings

Cory Wong X specifically (fetched, vendor announcement dated 1-2 October 2024):
- Sections, in the order the announcement lists them: Wah-Wah (with auto-wah option and MIDI expression pedal learn); Pre-Effects with four pedals (The Postal Service envelope filter, 4th Position Compressor, The Tuber booster, Big Rig Overdrive); three Amps (D.I. Funk Console, The Clean Machine, The Amp Snob); Cabsim (six virtual mics, 108 IRs per cab, 324 total, third-party IR loading, mono or stereo); Post Effects with three units (The 80s chorus, Delay-y-y BBD delay, The Wash reverb with Shimmer switch); Room Reverb driven by a Room Send in the cab module. [Neural DSP](https://neuraldsp.com/news/introducing-archetype-cory-wong-x)
- A 9-band graphic EQ with high-pass and low-pass filters is included, but the announcement does not state its position in the chain. [Neural DSP](https://neuraldsp.com/news/introducing-archetype-cory-wong-x)
- Global features: Transpose, Doubler, Live Tuner, Metronome (standalone only). Link/Unlink pairs each amp head with its matched cab or lets the user mix and match. [Neural DSP](https://neuraldsp.com/news/introducing-archetype-cory-wong-x)
- The window is resizable by "Click and Drag". The UI is described by the vendor as "completely redesigned" and more intuitive. 133 artist presets were added. Free update for owners of the original. [Neural DSP](https://neuraldsp.com/news/introducing-archetype-cory-wong-x)
- Guitar World's 2025 roundup summarizes Cory Wong X as 3 amps, 3 cabs and 9 effects (search snippet). [Guitar World](https://guitarworld.com/gear/plugins-apps/best-neural-dsp-plugins)

Section order and navigation in the X family (fetched reviews):
- Sound On Sound's John Mayer X review (April 2026) describes five signal-flow sections, Pre FX, Amp, Cab, EQ & Comp, Post FX, reached through icons along the top of the display. The review mentions six icons, so one extra tab exists (the review ties a "Gravity Tank" vibrato and spring reverb tab to this row). A persistent set of global controls gives access to presets, Doubler, Tuner and MIDI configuration. [Sound On Sound](https://www.soundonsound.com/reviews/neural-dsp-archetype-john-mayer-x)
- The same review describes a "Blended" amp mode that acts like a macro layer: input gain, a blend between the three amps, output level and room ambience, with the full front panels of each amp still available individually. [Sound On Sound](https://www.soundonsound.com/reviews/neural-dsp-archetype-john-mayer-x)
- Praise in that review: stylish GUI, workflow "not reinvented" from earlier Archetypes (consistency across the product line), and a "very intuitive" graphic cab/mic interface. No UI criticisms listed. [Sound On Sound](https://www.soundonsound.com/reviews/neural-dsp-archetype-john-mayer-x)
- Guitar World's Misha Mansoor X review (9 October 2025) gives the chain as Pre-FX pedalboard, three Amps (Clean, Rhythm, Lead), Post-FX (Modulator, Stereo Delay, Reverb with Shimmer), plus "universal" input and output dials, Noise Gate, Transpose and Doubler. [Guitar World](https://www.guitarworld.com/gear/plugins-apps/neural-dsp-archetype-misha-mansoor-x)
- Praise: layout is "well signposted and easy to find", spacious, pastel color scheme welcomed. Criticism: the range of effects can be "a little overwhelming" when deciding where to start; choosing what to do is harder than navigating. [Guitar World](https://www.guitarworld.com/gear/plugins-apps/neural-dsp-archetype-misha-mansoor-x)

Presets, lock and other X-generation features:
- Morgan Amps Suite X (fetched, announcement dated 28 October 2026) lists the current X feature set: Global Lock ("keeps the input, gate, transpose, doubler and output settings in place when switching between presets"), Preset Explorer as a dedicated left panel for navigating preset folders with search, customizable tags, favorites and recently used, Tooltips shown in a bottom utility bar on hover, Undo/Redo with edit history, and alternative amp skins. [Neural DSP](https://neuraldsp.com/news/introducing-morgan-amps-suite-x)
- Misha Mansoor X shipped with a Preset Explorer and 34 presets by the artist plus 338 others; a 1.1.0 update added Preset Explorer and undo/redo to Tim Henson X and Rabea X (search snippet). [Neural DSP](https://neuraldsp.com/news/archetype-misha-mansoor-x-tim-henson-x-and-rabea-x-1-1-0-are-now-available)
- Users on Neural DSP's own forum had been requesting parameter locking across preset changes, for example keeping transpose fixed for a gig (search snippets, thread dates not confirmed). [Neural DSP forum](https://unity.neuraldsp.com/t/request-add-parameter-locking/8949), [Neural DSP forum](https://unity.neuraldsp.com/t/locking-parameters-when-switching-presets/22523)

### Inferences
- The Archetype pattern is "one screen per stage, stages shown as a top icon row in signal order, globals always visible". It is not a reorderable chain: section order is fixed and only on/off and choice within a section vary. This is the closest commercial analog to "pick a category, then a type, then tweak".
- Global Lock is evidence that the first lock users want is not "freeze everything" but "do not let a preset change my level, tuning and gate".
- Tooltips in a fixed bottom bar and undo/redo were added late, as retrofits. Both are cheap confidence features for beginners.

### Gaps
- Whether Cory Wong X itself has received Preset Explorer, Global Lock and undo/redo was not confirmed. The 2024 announcement does not mention them.
- No full hands-on review of Cory Wong X was found, only launch coverage and roundups. Layout praise and criticism above comes from sibling X plugins.
- Exact position of the EQ tab in Cory Wong X and how presets are categorized there (by artist, by tone type) could not be confirmed.
- No Reddit or Gearspace threads on Archetype layout were retrieved.

## 2. Kilohearts Snap Heap, Multipass and Phase Plant

### Takeaway
Kilohearts uses one consistent grammar across all three hosts: vertical lanes, click the empty space at the bottom of a lane to add a Snapin, eight macro knobs under the patch name, and a modulation strip along the bottom. Reviewers consistently call it approachable for a modular system, but note that beginners can still be intimidated by signal-path concepts.

### Cited Findings
- Snap Heap has seven lanes. Each lane has a header (Enable, Mute, Solo), a main area holding Snapins, and a footer (Gain, Pan, Mix). Lanes process left to right in series by default; a parallel routing button between adjacent lanes links them in parallel. (fetched) [Kilohearts docs](https://kilohearts.com/docs/snap_heap)
- Adding: "Adding new Snapin is as easy as clicking the empty space at the bottom of a lane." (fetched) [Kilohearts docs](https://kilohearts.com/docs/snap_heap)
- Macros: eight macro knobs sit below the patch name; a macro can drive several parameters. Top bar has a Browse button for the preset browser plus Undo and Redo. (fetched) [Kilohearts docs](https://kilohearts.com/docs/snap_heap)
- When a host is itself loaded as a Snapin inside another host, it collapses to a compact view: up/down arrows to scan presets, and Multipass "shows only the named macros that have modulation bindings". A pen icon opens the full interface in a separate window. (fetched for Multipass; pen icon from search snippet) [Kilohearts docs](https://kilohearts.com/docs/multipass), [Kilohearts docs](https://kilohearts.com/docs/snap_heap)
- Multipass: up to five band lanes fed by a band splitter (drag split bars on the spectrum), plus a pre-FX lane and a post-FX lane. Enabling or disabling a lane adds or removes a split automatically. Lane footer adds a "post" control to gain, pan and mix. (fetched) [Kilohearts docs](https://kilohearts.com/docs/multipass)
- Modulation assignment: drag the plus symbol from a modulator to a destination and set amount; double-click to remove. Each Snapin has a down-arrow menu that includes Randomise (search snippet of a 2018 Computer Music walkthrough, older, and it describes an earlier four-lane build). [Computer Music via PressReader](https://www.pressreader.com/australia/computer-music/20180905/282553019106543)
- Phase Plant reception: MusicTech calls it detailed yet simple, and says a blank patch feels less intimidating than on most synths because you drag in modules and hear results. Producer Hive scores ease of use 8.5/10 and calls modulation "simple and intuitive" but lists "intimidating for beginners" as a con. Admiral Bumblebee (2019, older) says the interface makes it clear what a patch is doing. (search snippets) [MusicTech](https://www.musictech.com/reviews/software-instruments/kilohearts-phase-plant/), [Producer Hive](https://producerhive.com/?p=19518), [Admiral Bumblebee](https://www.admiralbumblebee.com/music/2019/07/13/kilohearts-phase-plant-review)

### Inferences
- "Empty slot at the end of the chain is the add button" is the lowest-friction module picker seen in this research. It needs no separate browser panel.
- Showing only macros that are named and bound is a useful rule for a locked or play view: empty macros are hidden instead of shown as dead knobs. Compare the Analog Lab V complaint about "two blank, non-working controls" in section 4.
- Every Snapin has only a handful of controls and the same header affordances, which is why a stack of them stays readable.

### Gaps
- The docs pages fetched do not describe how a Snapin is reordered, removed or bypassed, nor whether macros can be renamed. General knowledge suggests drag by header and a per-Snapin power/close control, but this was not confirmed from a source.
- Lane counts differ between sources (seven in current docs, four in the 2018 article), which reflects version changes.
- No KVR or Reddit threads specifically about Snap Heap usability were retrieved.

## 3. Other multi-effects: module pickers, browsers, macros, simple versus advanced views

### Takeaway
Three layouts recur. (a) Slot grid with one macro (Baby Audio Transit): few slots, each effect usable once, four or fewer controls per module. (b) Rack plus browser plus overview sidebar (Guitar Rig 7, Soundtoys Effect Rack, MGuitarArchitect): powerful but gets "busy", and needs an overview strip to stay navigable. (c) Two-tier view (Minimal Audio Play versus full views, Arturia "Advanced" button, Melda Easy versus Edit screen): a small front panel with one to four macros, and a button that reveals depth.

### Cited Findings

Baby Audio Transit (fetched, Sound On Sound, January 2024):
- Seven effect slots, 18 effect types, any type in any slot in any order, and "only one instance of each effect can be used per preset". An eighth position holds the macro. [Sound On Sound](https://www.soundonsound.com/reviews/baby-audio-transit)
- Each slot's top strip has: bypass, drag-and-drop handle for reordering, per-module preset load/save, per-module randomise (dice). [Sound On Sound](https://www.soundonsound.com/reviews/baby-audio-transit)
- One macro ("T") knob. A small dot in the centre of any control links it to the macro (turns yellow when linked); the user sets min and max per linked parameter. An Infinity button keeps the chain active at macro zero so the plugin doubles as a static multi-effect. [Sound On Sound](https://www.soundonsound.com/reviews/baby-audio-transit)
- Global randomise in the top strip, with a lock/unlock system deciding which parameters, and which effect choices, get randomised. Mix and Output sliders along the bottom. [Sound On Sound](https://www.soundonsound.com/reviews/baby-audio-transit)
- Praise: GUI is "something of a triumph", "beautifully designed for ease of use", each module has four or fewer controls, less intimidating than Sugar Bytes Turnado. Mild criticism: randomise/lock interactions take time to learn; reverb and delay are shallow. [Sound On Sound](https://www.soundonsound.com/reviews/baby-audio-transit)
- Other reviewers flag missing output metering and no volume compensation across a sweep (search snippets). [Record Production](https://www.recordproduction.com/reviews/baby-audio-transit), [MusicTech](https://musictech.com/reviews/plug-ins/baby-audio-transit-review/)
- Transit 2 raised the module count to 28 and added more motion modes (search snippet). [Rekkerd](https://rekkerd.org/review-transit-2-motion-effects-playground-by-baby-audio/)

Native Instruments Guitar Rig 7:
- Interface areas: Header (presets, preferences, I/O, engine), Browser (presets and Components with Favorites, Filters, Search), Rack (with a Toolbar giving access to Rack Tools, Macros among them), Sidebar (Signal Flow plus an Info pane that describes interface elements). (fetched) [NI manual](https://docs.native-instruments.com/online-guides/guitar-rig-manual/en/overview-of-guitar-rig)
- Scaling: "View size" magnification in 9 steps from 75% to 200%, savable as default; Rack height adjustable by dragging. (fetched) [NI manual](https://docs.native-instruments.com/online-guides/guitar-rig-manual/en/overview-of-guitar-rig)
- Sound On Sound (March 2024, fetched): the new Sidebar "provides a useful visual overview of your signal chain"; clicking a component there selects it in the Rack, and components can be dragged to reorder, bypassed or deleted from the Sidebar. It is "a very welcome addition". The review also says "there are so many features on offer that things can get pretty busy in both the Browser and Rack displays." [Sound On Sound](https://www.soundonsound.com/reviews/native-instruments-guitar-rig-pro-7)
- Preset search covers titles and metadata including Filter tags and author; users can edit tags on User presets and add their own User Filter tags; all factory rack presets come with pre-mapped macros (search snippets of NI manual pages). [NI manual](https://native-instruments.com/ni-tech-manuals/guitar-rig-manual/en/using-the-browser), [NI manual](https://native-instruments.com/ni-tech-manuals/guitar-rig-manual/en/using-presets)
- The Guitar Rig 6 manual (2020, older) describes a Macro Layout selector switching between 8 and 16 macros (search snippet; not confirmed for version 7). [NI GR6 manual PDF](https://www.native-instruments.com/fileadmin/ni_media/downloads/manuals/gr6/GUITAR_RIG_6_Manual_English_01_10_2020.pdf)

Minimal Audio Rift and Current:
- Rift's Play View gives "immediate control over presets with two macro knobs and other key parameters", with a central scope visualizing the result. (fetched) [Minimal Audio manual](https://manual.minimal.audio/rift-manual/play-view)
- Current has four primary views, Play, Engine, Effects and Stream, switched from a left navigation bar (search snippet). [Minimal Audio manual](https://manual.minimal.audio/current-manual/overview-and-navigation)
- Parameter locking across the Minimal Audio range: right-click any knob, slider, button or dropdown and choose Lock; locked controls are shown in brackets and keep their value when presets load. Setup-type settings (MPE mode, Global Tune) are locked by default. (fetched) [Minimal Audio manual](https://manual.minimal.audio/current-manual/additional-settings/parameter-locking)

Output Portal (fetched, vendor support page):
- Opens by default to a Main Page containing: an XY control whose handle moves two macros at once, a preset name that toggles the browser, next/previous arrows, a Reverse toggle, Dry/Wet, a large value readout panel that shows the number for whatever is being adjusted, input/output gain and a VU meter. [Output support](https://support.output.com/en/articles/10297849-main-page)
- Macro assignment: drag a "+" indicator onto almost any control, then drag up or down to set range. [Output support](https://support.output.com/en/articles/10297849-main-page)
- Dry/Wet lock: right-click the Dry/Wet slider to lock it "so it stays put when you cycle through presets". [Output support](https://support.output.com/en/articles/10297849-main-page)

Arturia Efx series (search snippets):
- A simple main panel plus an "Advanced" button that reveals a lower panel. For Efx Fragments, a reviewer notes the Advanced button reveals the granular controls not visible on the simple main panel; for Efx Motions, CDM says you may leave the Advanced panel alone for a while because the main view is already deep. [Gearnews](https://www.gearnews.com/first-look-review-arturia-efx-fragments-frees-granular-effects-from-obscurity), [CDM](https://cdm.link/arturia-efx-motions-review/), [Sound On Sound](https://www.soundonsound.com/reviews/arturia-efx-motions)

MeldaProduction MXXX and MGuitarArchitect (search snippets, mostly KVR forum threads of uncertain date, likely pre-2022, treat as older):
- Melda plugins have an Easy screen driven by "Active Presets" and a full Edit screen. Staff state the Easy-page active presets are fixed and cannot be changed by users; to modify one you save it as an ordinary preset. A preset saved while the Easy screen is showing reopens on the Easy screen. [KVR forum](https://www.kvraudio.com/forum/viewtopic.php?p=6715738), [KVR forum](https://www.kvraudio.com/forum/viewtopic.php?p=6180971)
- Users note that adding or removing modules forces rework of a parent easy screen, so custom easy screens are hard to maintain. [KVR forum](https://www.kvraudio.com/forum/viewtopic.php?p=8113134)
- MGuitarArchitect offers three customizable chains of 16 slots each plus a sidechain chain (Sonic State, 2023); Sound On Sound describes up to four parallel chains. [Sonic State](https://sonicstate.com/amped/2023/10/05/meldaproduction-creates-their-ultimate-guitar-solution/), [Sound On Sound](https://www.soundonsound.com/reviews/melda-mguitararchitect?amp)

Soundtoys Effect Rack (search snippet of the vendor manual, version 5):
- A Control Panel at the top holds global controls for everything in the rack below (tempo, global Mix where noon is 50/50, and a global Recycle feedback control). New units are inserted above, below or in place of existing slots. The manual excerpt mentions up to six plug-ins but the sentence was truncated. Presets exist at both levels: whole racks, and each individual unit's own presets. [Soundtoys manual PDF](https://www.soundtoys.com/wp-content/uploads/Soundtoys-Effect-Rack-Manual.pdf)

### Inferences
- Plugin Playground's existing rule (seven modules, each usable once, reorderable serial chain) matches Transit's constraint almost exactly, and Transit is the best-reviewed layout for ease of use in this set. The "one instance each" limit was not criticized by the reviewer.
- The control-count ceiling that reviewers reward is about four controls per module.
- Racks need an overview. Guitar Rig added a signal-flow Sidebar in version 7 precisely because the rack got busy. With seven modules a single row of tiles can serve as both overview and editor, so a separate sidebar is unnecessary.
- A large value readout (Portal) and a hover-description bar (Guitar Rig Info pane, Neural tooltips) are recurring beginner aids.

### Gaps
- Not researched or not confirmed for lack of sources retrieved: Unfiltered Audio (BYOME/TRIAD), AudioThing Space Strip, "Collage", Output Thermal and Movement, Baby Audio Crystalline and Humanoid, Arturia Pigments Play view. No claims are made about them here.
- Minimal Audio Rift's Advanced view contents and the switch control were not on the page fetched.
- Portal's macro count beyond the two on the XY pad, and how its advanced page is reached, were not stated on the page fetched.
- Guitar Rig 7 macro count, and exactly how a component is added to the rack (drag versus double-click), were not confirmed.
- Soundtoys slot limit and reorder gesture need checking against the full PDF.
- The Melda Easy/Edit detail comes from forum snippets, not the official manual.

## 4. Instruments: "pick a category, pick a sound, tweak a few macros"

### Takeaway
Preset-driven instruments converge on a browser filtered by type/style tags, a small fixed set of macros with consistent names across every preset (four in Analog Lab, Arcade and Current; six in NI Play Series; eight in Kilohearts and Serum 2), and a button that opens the full editor. The strongest caution comes from Analog Lab V: users rebelled when generic macros replaced preset-specific controls and restricted ranges.

### Cited Findings

Arturia Analog Lab:
- Play view macros are labelled Brightness, Timbre, Time, Movement, followed by effect controls such as Distortion Mix, Phaser Mix, Delay Volume, Reverb Volume. Labels and functions can be changed in all but the free Play edition (search snippet, Gearspace review). [Gearspace](https://gearspace.com/gear/arturia-analog-lab.3807/)
- Browser sorts presets by Types, Instruments, Styles, Banks, Designer and user-made, with favourites (search snippet). [Gearspace](https://gearspace.com/gear/arturia-analog-lab.3807/)
- Bonedo's Analog Lab Pro review (July 2025, search snippet): browser filters by type, style and genre; editing is limited to the macros; a Studio view handles mixing, effects, chain order and two-part split/layer; a Stage view plays through prepared playlists. Its main criticism is that navigation to Studio and Stage views could be clearer. The snippet reports eight macro knobs on the main view, which conflicts with the "four macros plus four effect controls" description above; the two are probably the same eight controls counted differently. [Bonedo](https://www.bonedo.de/artikel/arturia-analog-lab-pro-test/)
- KVR thread "Analog Lab V vs 4 - Why have they destroyed the controls?" (fetched; opened February 2021, posts through December 2021, older): complaints that Brightness and Timbre are generic and Time is unclear; preset-specific controls such as ADSR were lost; some presets showed "two blank, non-working controls"; master bass/mid/treble appear on every preset; macro ranges are restricted (cutoff stopping near 70%), which Arturia support said was intentional; one knob moving several parameters felt unintuitive to some. One user called version 5 "just awful to use" and went back to 4; another said the macro change did not bother them. A workaround was found: a setting changes the bottom controls to "Factory", and a macro editor lets users reset Brightness and Timbre ranges. [KVR forum](https://www.kvraudio.com/forum/viewtopic.php?p=8296634)

Minimal Audio Current (fetched):
- Play View has four areas: a Macro XY pad showing the preset's pack art with two draggable handles, each handle tied to two macros (four macros total, with an icon showing whether horizontal or vertical movement drives each); preset info with favorite, previous, next and random; an engine overview listing each sound module with bypass and solo, where clicking a module name jumps to it in the Engine View; and a keyboard with scale and root note, with a scale lock. [Minimal Audio manual](https://manual.minimal.audio/current-manual/overview-and-navigation/play-view)
- Macros are renamed by clicking the label. [Minimal Audio manual](https://manual.minimal.audio/current-manual/overview-and-navigation/play-view)

Native Instruments Kontakt Play Series (fetched):
- Six macro knobs on a bottom panel that is always accessible; each Snapshot (preset) has its own assignments and the knob labels update to match. Two sound layers A and B with a Balance control. Buttons SOUND, FX, SEQ, MACROS and a settings cog each open and close an editor panel. A Randomize button changes sources, balance and macros, with modifier-click for partial randomization. [NI manual](https://docs.native-instruments.com/online-guides/play-series-manual/en/main-overview)
- The Macro Editor lets the user assign parameters, set min and max, shape the response curve and reorder the macro controls. [NI manual](https://docs.native-instruments.com/online-guides/play-series-manual/en/main-overview)

Output Arcade (search snippets; several sources are from 2019-2021, older):
- Browser hierarchy is Lines (libraries), Kits (presets) and Loops; version 2.0 added chromatic Instruments reachable from an Instruments tab. Each instrument has four Macro sliders; a "Tweak" button opens the Macro Overview; any parameter can be assigned to a slider by right-clicking it; a parameter cannot be assigned to two macros. [Sound On Sound](https://www.soundonsound.com/reviews/output-arcade), [Engadget](https://www.engadget.com/arcade-20-note-kits-instruments-sampler-hands-on-161548233.html), [Output](https://shop.output.com/?p=1626743)

Serum 2 (search snippets, 2025):
- Eight macros (up from four in Serum 1), assignable by drag-and-drop or the mod matrix. The preset browser adds audio preview play buttons, plus search and filtering by category, tag or name with ratings. Reviews call it intuitive but note a steep learning curve for beginners. [Xfer "What's New" PDF](https://static.xferrecords.com/Serum%202%20What's%20New.pdf), [Xfer support](https://support.xferrecords.com/article/52-serum-2-preset-previews), [MusicRadar](https://www.musicradar.com/music-tech/plugins/a-copy-of-serum-might-be-the-smartest-investment-any-budding-producer-makes-xfer-serum-2-review)

Spitfire LABS (search snippets; reviews circa 2020, older):
- Three main controls: sliders for expression and dynamics and one knob whose target the user can choose; effects reached via the central button. Praised as "wonderfully simple"; one reviewer rated the UI 7/10, clean but with a lot of empty space. KVR and Audiotool state that standalone LABS has been replaced by Splice Instrument, so the current interface may differ. [Producer Hive](https://producerhive.com/?p=24351), [KVR](https://www.kvraudio.com/product/labs-by-spitfire-audio), [Audiotool manual](https://www.audiotool.com/help/manuals/plugins/synthesizer/labs.html)

Baby Audio BA-1 (search snippets, 2023):
- Reviewed as simple and inviting to tweak, with a "Re-Gen" button that generates a new musically tuned patch per click for when "the simple interface is not fast enough", and 500+ presets. Limited by design: single LFO and envelope, no routing flexibility. [MusicRadar](https://musicradar.com/reviews/baby-audio-ba-1-review), [Sound On Sound](https://www.soundonsound.com/reviews/baby-audio-ba-1), [App Store listing](https://apps.apple.com/us/app/id6449602868)

### Inferences
- Macro counts cluster at four for beginner-facing front panels (Analog Lab, Arcade, Current). Six to eight appears in products aimed at sound designers.
- Two macro philosophies exist. Fixed semantic names on every preset (Analog Lab: Brightness, Timbre, Time, Movement) make every preset feel the same to tweak. Per-preset labels (NI Play Series, Kilohearts) are more precise but must be authored per preset. The Analog Lab V backlash suggests fixed names work only if the full range stays reachable and real controls are one click away.
- For a simple synth with oscillators, filter and envelope, the real controls are few enough that showing them directly may beat abstract macros. The KVR complaint was specifically about losing ADSR.
- "Next, previous, random, favorite" beside the preset name (Current) covers most beginner browsing without opening a browser at all.

### Gaps
- The official Analog Lab 5.11 manual (April 2025) was not read; macro and browser details are from a review snippet and a 2021 forum thread.
- Vital: no usable source was retrieved on its macro count, tabs or browser. Nothing is claimed.
- Arcade and LABS details are from older sources and both products have changed (Arcade 2.x, LABS moving to Splice Instrument).
- No source confirmed whether BA-1 is strictly a single-page UI.

## 5. Recurring usability lessons from reviews and forums

### Takeaway
What reads as "easy": a visible signal order, few controls per module, one to four macros, a default landing page built for playing, and protection of level and mix when changing presets. What overwhelms: too many choices with no starting point, busy racks without an overview, and abstraction that hides or restricts the real controls.

### Cited Findings
- Few controls per module is explicitly credited for ease of use: Transit modules have four or fewer controls and the GUI is called "a triumph". [Sound On Sound](https://www.soundonsound.com/reviews/baby-audio-transit)
- Choice overload, not navigation, is the complaint in a well-signposted plugin: Misha Mansoor X's range of effects is "a little overwhelming" when deciding where to start. [Guitar World](https://www.guitarworld.com/gear/plugins-apps/neural-dsp-archetype-misha-mansoor-x)
- Feature density makes racks busy; an overview strip that mirrors the chain and supports reorder, bypass and delete was the fix in Guitar Rig 7. [Sound On Sound](https://www.soundonsound.com/reviews/native-instruments-guitar-rig-pro-7)
- Consistency across a product family is praised: John Mayer X's workflow is "not reinvented" from earlier Archetypes. [Sound On Sound](https://www.soundonsound.com/reviews/neural-dsp-archetype-john-mayer-x)
- Macros that are vague, restricted in range, or left blank cause backlash (Analog Lab V thread, 2021, older). [KVR forum](https://www.kvraudio.com/forum/viewtopic.php?p=8296634)
- Typical macro counts: 1 (Transit), 2 (Rift Play View, Portal XY), 4 (Analog Lab, Arcade, Current), 6 (NI Play Series), 8 (Snap Heap, Multipass, Serum 2), 8 or 16 (Guitar Rig 6, older). Sources as cited in sections 2 to 4.
- Preset tagging and search: Neural's Preset Explorer uses folders, search, customizable tags, favorites and recents. Guitar Rig searches titles, Filter tags and author and allows user tags. Analog Lab filters by Types, Instruments, Styles, Banks and Designer. Serum 2 adds audio previews. [Neural DSP](https://neuraldsp.com/news/introducing-morgan-amps-suite-x), [NI manual](https://native-instruments.com/ni-tech-manuals/guitar-rig-manual/en/using-the-browser), [Gearspace](https://gearspace.com/gear/arturia-analog-lab.3807/), [Xfer support](https://support.xferrecords.com/article/52-serum-2-preset-previews)
- Lock and safe modes found, all about surviving preset changes or randomisation, not about freezing the whole panel:
  - Neural DSP Global Lock: input, gate, transpose, doubler, output held across presets. [Neural DSP](https://neuraldsp.com/news/introducing-morgan-amps-suite-x)
  - Minimal Audio: per-control Lock via right-click, shown in brackets, with setup settings locked by default. [Minimal Audio manual](https://manual.minimal.audio/current-manual/additional-settings/parameter-locking)
  - Output Portal: right-click lock on Dry/Wet. [Output support](https://support.output.com/en/articles/10297849-main-page)
  - Softube: right-click any parameter to lock its value across preset changes (search snippet). [Softube manual](https://softube.com/user-manuals/parameter-value-lock)
  - Waves: "Lock Input" and "Lock Mix on Preset Load" menu options (search snippet). [Waves support](https://www.waves.com/support/how-to-lock-input-and-mix-values-on-preset-load)
  - Transit: lock/unlock decides what randomise may touch, including effect choice. [Sound On Sound](https://www.soundonsound.com/reviews/baby-audio-transit)
  - Current: scale lock on the Play View. [Minimal Audio manual](https://manual.minimal.audio/current-manual/overview-and-navigation/play-view)
- Design debate in forums: whether lock state should be saved in presets (posters argue it should not), and a developer view that always-visible padlock icons clutter the interface (search snippets, KVR, dates not confirmed). [KVR forum](https://www.kvraudio.com/forum/viewtopic.php?p=6668784), [KVR forum](https://www.kvraudio.com/forum/viewtopic.php?p=6229074)
- Undo/redo and hover help are treated as headline usability features in recent releases (Neural X updates, Kilohearts top bar, Guitar Rig Info pane). [Neural DSP](https://neuraldsp.com/news/introducing-morgan-amps-suite-x), [Kilohearts docs](https://kilohearts.com/docs/snap_heap), [NI manual](https://docs.native-instruments.com/online-guides/guitar-rig-manual/en/overview-of-guitar-rig)
- Resizing is now expected: Neural X drag-to-resize; Guitar Rig 75% to 200% in 9 steps. [Neural DSP](https://neuraldsp.com/news/introducing-archetype-cory-wong-x), [NI manual](https://docs.native-instruments.com/online-guides/guitar-rig-manual/en/overview-of-guitar-rig)
- Randomise as a beginner entry point: BA-1 Re-Gen, Transit dice (global and per module), NI Play Series Randomize, Current random preset. Sources as cited above.

### Inferences
- No product found has a literal "play mode that disables all editing". The market's "lock" means "keep these values when the preset changes" and "play view" means "a smaller panel", not a frozen one. A Plugin Playground lock/unlock toggle would therefore be novel; the nearest precedents are Play versus Engine views (Minimal Audio), Easy versus Edit screens (Melda) and the Kilohearts compact mode with a pen icon to open the editor.
- If a lock mode is built, the evidence suggests two separate ideas should not be merged: (1) layout lock, meaning the chain and module choices cannot be changed while knobs still work, and (2) value hold, meaning output level and mix survive preset changes.
- A "where do I start" answer (good default preset, a random button, a short curated preset list by category) addresses the main complaint found against otherwise well laid out plugins.

### Gaps
- No Reddit or Gearspace threads were retrieved, so user sentiment here is from KVR, vendor forums and professional reviews only.
- No quantitative usability data (studies, surveys) was found; all lessons are qualitative.
- No source directly compares macro counts for beginner comprehension.

## 6. Products that let the end user add, remove or relabel front-panel knobs

### Takeaway
User-editable front panels exist but are almost always limited to reassigning and renaming a fixed number of macro slots. Fully user-built panels (Melda) exist and draw maintenance complaints. Hiding unused macros is the common way to make a fixed slot count feel customized.

### Cited Findings
- Minimal Audio Current: click a macro's label on the Play View to rename it; four fixed macro slots on two XY handles. (fetched) [Minimal Audio manual](https://manual.minimal.audio/current-manual/overview-and-navigation/play-view)
- NI Kontakt Play Series: a dedicated MACROS editor to assign parameters, set min/max, shape the curve and reorder the six macro controls; labels follow the Snapshot. Renaming is not described on the page read. (fetched) [NI manual](https://docs.native-instruments.com/online-guides/play-series-manual/en/main-overview)
- Kilohearts: eight macros per patch; in compact Snapin mode only named macros with bindings are displayed. (fetched) [Kilohearts docs](https://kilohearts.com/docs/multipass)
- Output Portal and Arcade: assign by dragging a "+" onto a control (Portal) or right-clicking a parameter and choosing a slider (Arcade); Arcade allows unlimited parameters per macro but one macro per parameter. (Portal fetched; Arcade search snippet) [Output support](https://support.output.com/en/articles/10297849-main-page), [Output](https://shop.output.com/?p=1626743)
- Baby Audio Transit: link dot in the centre of each control, with per-parameter min and max. (fetched) [Sound On Sound](https://www.soundonsound.com/reviews/baby-audio-transit)
- Arturia Analog Lab: macro labels and functions can be changed except in the free Play edition, and a macro editor sets ranges (search snippet plus 2021 forum thread, older). [Gearspace](https://gearspace.com/gear/arturia-analog-lab.3807/), [KVR forum](https://www.kvraudio.com/forum/viewtopic.php?p=8296634)
- Spitfire LABS: one knob whose target the user picks (search snippet, older). [KVR](https://www.kvraudio.com/product/labs-by-spitfire-audio)
- MeldaProduction: users can build custom "active presets" and easy-screen GUIs from multiparameters, covered in vendor tutorials; forum users report parent easy screens need rework whenever modules are added or removed (search snippets; forum posts likely older). [Melda tutorial](https://www.meldaproduction.com/tutorials/multiparameters-introduction), [KVR forum](https://www.kvraudio.com/forum/viewtopic.php?p=8113134)
- Guitar Rig: macros are global controls assignable to any Rack parameter, with layouts of 8 or 16 in version 6 (search snippet, 2020 manual, older). [NI GR6 manual PDF](https://www.native-instruments.com/fileadmin/ni_media/downloads/manuals/gr6/GUITAR_RIG_6_Manual_English_01_10_2020.pdf)

### Inferences
- The three assignment gestures in use are: drag a "+" from the macro to a control (Portal, Kilohearts), a link dot on every control (Transit), and right-click "assign to" (Arcade). The link dot is the most discoverable because it is visible on the target itself, and it suits a single-macro or few-macro design.
- A realistic scope for a beginner product is "fixed number of front knobs, each can be pointed at a parameter and renamed, unassigned ones are hidden". Free-form panel building (Melda) is the outlier and the one with maintenance complaints.
- Because Plugin Playground's chain is reorderable and modules can be removed, front-panel assignments need to survive or cleanly disappear when a module is removed. This is exactly the failure Melda users describe.

### Gaps
- Not confirmed: macro renaming in Kilohearts hosts, Serum 2, Guitar Rig 7 and Portal.
- No product was found that lets an end user add an arbitrary number of knobs to a front panel in a beginner-oriented plugin. This may exist (for example DAW-level racks such as Ableton's macro racks) but was out of the plugin scope and not researched.
- The Melda custom easy screen workflow (how knobs and buttons are added and named) was not read from the official tutorial; only its existence is confirmed.
