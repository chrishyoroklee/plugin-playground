// The page's plugins (src/plugin/plugins.ts): a category lists plugins, and a plugin holds
// presets that are settings of it, never a different device.

import { describe, expect, it } from 'vitest';
import { INSTRUMENTS, PRESETS, normalize } from '../src/plugin/model';
import { defaultsOf, pluginOfPreset, pluginOfSettings, pluginsOf, shapeOf } from '../src/plugin/plugins';

describe('plugins and their presets', () => {
  it('every preset is in exactly one plugin, and every plugin has one', () => {
    for (const [kind, presets] of [['effect', PRESETS], ['instrument', INSTRUMENTS]] as const) {
      const listed = pluginsOf(kind).flatMap((p) => p.presets.map((x) => x.id));
      expect(listed.sort()).toEqual(presets.map((p) => p.id).sort());
      expect(pluginsOf(kind).every((p) => p.presets.length > 0)).toBe(true);
    }
  });

  it("a plugin's presets are all the same device: same effects, same order, same things turning dials", () => {
    for (const plugin of pluginsOf('effect')) {
      for (const p of plugin.presets) expect([plugin.name, shapeOf(p.settings)]).toEqual([plugin.name, plugin.shape]);
    }
  });

  it('EQ is four plugins, each with its own presets, and a plugin is one effect', () => {
    const eq = pluginsOf('effect').filter((p) => p.category === 'EQ');
    expect(eq.map((p) => p.name)).toEqual(['Three Band EQ', 'Parametric EQ', 'Graphic EQ', 'Tilt EQ']);
    expect(eq[0].presets.map((p) => p.name)).toEqual(['Smile Curve', 'Mid Push', 'Warm Up', 'Air', 'Bass Boost']);
    expect(eq.every((p) => !p.rack)).toBe(true);
    // one plugin per effect, wherever it is listed
    const singles = pluginsOf('effect').filter((p) => !p.rack && !p.presets[0].settings.motion && p.shape);
    expect(new Set(singles.map((p) => p.shape)).size).toBe(singles.length);
    expect(singles.map((p) => p.name)).toEqual(expect.arrayContaining(['Distortion', 'Bitcrusher', 'Filter', 'Compressor', 'Delay', 'Reverb']));
    expect(pluginsOf('effect').find((p) => p.name === 'Bitcrusher')!.presets.length).toBeGreaterThanOrEqual(4);
  });

  it('a rack is several effects, apart from the plugins', () => {
    const shoegaze = pluginOfPreset('effect', 'shoegaze')!;
    expect(shoegaze.rack).toBe(true);
    expect(shoegaze.blurb).toContain('→');
  });

  it('settings with dials turned are still their plugin, and settings of another shape are not', () => {
    const plugin = pluginOfPreset('effect', 'smilecurve')!;
    const turned = normalize(plugin.presets[1].settings);
    turned.chain.find((e) => e.module === 'eq')!.values.low = 7;
    expect(pluginOfSettings('effect', turned)?.id).toBe(plugin.id);
    turned.chain.find((e) => e.module === 'reverb')!.on = true;
    expect(pluginOfSettings('effect', turned)).toBeUndefined();
  });

  it('a plugin that is one effect comes with every dial at its default; a rack has no default', () => {
    const eq = defaultsOf(pluginOfPreset('effect', 'smilecurve')!, 'effect')!;
    expect(eq.name).toBe('Three Band EQ');
    expect(eq.chain.filter((e) => e.on).map((e) => [e.module, e.values])).toEqual([['eq', { low: 0, mid: 0, freq: 1000, high: 0 }]]);
    expect(defaultsOf(pluginOfPreset('effect', 'shoegaze')!, 'effect')).toBeUndefined();
  });

  it('the instrument is one synth, its presets in their sections', () => {
    const synth = pluginsOf('instrument');
    expect(synth.map((p) => p.name)).toEqual(['Synth']);
    expect(synth[0].presets.length).toBe(INSTRUMENTS.length);
  });
});
