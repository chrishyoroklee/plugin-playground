import { describe, expect, it } from 'vitest';
import { PRESETS, INSTRUMENTS, normalize, type Settings } from '../src/plugin/model';
import {
  SYSTEMS, checkSystem, detectMachine, detectSystem, findInstaller, shape, type NavigatorLike,
} from '../src/plugin/installers';

const UA = {
  safari: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15',
  oldMac: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/13.1 Safari/605.1.15',
  windows: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
  windows7: 'Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36',
  linux: 'Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0',
  iphone: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1',
};
const chrome = (platform: string, platformVersion: string, architecture: string, userAgent: string): NavigatorLike => ({
  userAgent,
  userAgentData: { platform, mobile: false, getHighEntropyValues: async () => ({ platformVersion, architecture }) },
});

describe('which system the browser is on', () => {
  it('reads the system from the user agent', () => {
    expect(detectSystem({ userAgent: UA.safari })).toMatchObject({ system: 'macos', version: 'new' });
    expect(detectSystem({ userAgent: UA.oldMac })).toMatchObject({ system: 'macos', version: 'old' });
    expect(detectSystem({ userAgent: UA.windows }).system).toBe('windows');
    expect(detectSystem({ userAgent: UA.windows7 })).toMatchObject({ system: 'windows', version: 'old' });
    expect(detectSystem({ userAgent: UA.linux }).system).toBe('linux');
  });

  it('does not take a phone or an iPad for a computer', () => {
    expect(detectSystem({ userAgent: UA.iphone })).toEqual({ mobile: true });
    expect(detectSystem({ userAgent: UA.safari, maxTouchPoints: 5 })).toEqual({ mobile: true });
  });

  it('asks the browser for the real version where it will say', async () => {
    expect(await detectMachine(chrome('macOS', '14.5.0', 'arm', UA.safari)))
      .toMatchObject({ system: 'macos', version: 'new', versionName: 'macOS 14.5', arm: true });
    expect(await detectMachine(chrome('Windows', '15.0.0', 'x86', UA.windows)))
      .toMatchObject({ system: 'windows', version: '11', arm: false });
    expect(await detectMachine(chrome('Windows', '10.0.0', 'arm', UA.windows)))
      .toMatchObject({ system: 'windows', version: '10', arm: true });
  });
});

describe('checking a system before the download', () => {
  it('passes a machine that meets what the plugins need, naming what it found', async () => {
    const mac = await detectMachine(chrome('macOS', '14.5.0', 'arm', UA.safari));
    const verdict = checkSystem('macos', 'new', mac);
    expect(verdict.state).toBe('yes');
    expect(verdict.text).toContain('macOS 14.5');
  });

  it('holds the download back for a version too old to run them', () => {
    for (const s of SYSTEMS) {
      const old = s.versions.find((v) => !v.ok)!;
      expect(checkSystem(s.id, old.id, {}).state).toBe('no');
    }
  });

  it('warns, without refusing, when the installer is for another system or processor', async () => {
    expect(checkSystem('windows', '11', detectSystem({ userAgent: UA.safari })).state).toBe('warn');
    expect(checkSystem('macos', 'new', { mobile: true }).state).toBe('warn');
    const armPc = await detectMachine(chrome('Windows', '15.0.0', 'arm', UA.windows));
    expect(checkSystem('windows', '11', armPc).state).toBe('warn');
    // A Mac build is universal: Apple silicon is no warning.
    expect(checkSystem('macos', 'new', { system: 'macos', arm: true }).state).toBe('yes');
  });

  it('every system offers a version that works first', () => {
    for (const s of SYSTEMS) expect(s.versions[0].ok).toBe(true);
  });
});

describe('which installer goes with a device', () => {
  const builds = Object.fromEntries([...PRESETS, ...INSTRUMENTS].map((p) => [p.id, { macos: `downloads/${p.id}-macos.pkg` }]));
  const tube = PRESETS.find((p) => p.id === 'tube')!;

  it('an untouched preset gets its own', () => {
    const found = findInstaller(normalize(tube.settings), 'tube', PRESETS, builds)!;
    expect(found.preset.id).toBe('tube');
    expect(found.exact).toBe(true);
  });

  it('a preset with only its dials and name changed gets the same plugin, and is told so', () => {
    const mine: Settings = normalize(tube.settings);
    mine.name = 'My Tube';
    for (const entry of mine.chain) for (const id of Object.keys(entry.values)) entry.values[id] += 1;
    const found = findInstaller(mine, undefined, PRESETS, builds)!;
    expect(shape(found.preset.settings)).toBe(shape(mine));
    expect(found.exact).toBe(false);
  });

  it('a chain no preset has gets none', () => {
    const mine: Settings = normalize(tube.settings);
    mine.chain = [...mine.chain].reverse();
    for (const entry of mine.chain) entry.on = true;
    expect(findInstaller(mine, undefined, PRESETS, builds)).toBeUndefined();
  });

  it('an instrument is never matched to an effect', () => {
    const pad = INSTRUMENTS[0];
    const found = findInstaller(normalize(pad.settings), undefined, INSTRUMENTS, builds)!;
    expect(found.preset.settings.kind).toBe('instrument');
    expect(shape(pad.settings)).not.toBe(shape({ chain: pad.settings.chain }));
  });

  it('a module switched off does not change the shape; an option does', () => {
    const a = normalize(tube.settings);
    const b = normalize(tube.settings);
    const off = b.chain.find((e) => !e.on);
    if (off) off.values[Object.keys(off.values)[0]] += 1;
    expect(shape(b)).toBe(shape(a));
    const on = b.chain.find((e) => e.on && Object.keys(e.options).length)!;
    on.options[Object.keys(on.options)[0]] = 'something-else';
    expect(shape(b)).not.toBe(shape(a));
  });
});
