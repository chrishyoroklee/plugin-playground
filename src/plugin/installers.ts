// The plugin installers the site carries, and which one a visitor should be given.
//
// CI builds every preset as a plugin on each system and wraps it in that system's own
// kind of installer (scripts/plugin.py, --package). This file is what the page knows
// about them: what each system needs, how to tell which system the browser is on, and
// which installer goes with the device on screen.

import { normalize, type Preset, type Settings } from './model';

export type SystemId = 'macos' | 'windows' | 'linux';

/** A version of a system the visitor can say they have, and whether the plugins run on it. */
export interface VersionChoice {
  id: string;
  label: string;
  ok: boolean;
}

export interface SystemInfo {
  id: SystemId;
  label: string;
  /** What the downloaded file is, for the button. */
  file: string;
  formats: string;
  needs: string;
  versions: VersionChoice[];
  /** What to do with the download. `{where}` is where Logic lists it. */
  steps: string;
  /** The builds are not signed with a paid developer certificate: what the system says, and the way past it. */
  unsigned: string;
}

export const SYSTEMS: SystemInfo[] = [
  {
    id: 'macos',
    label: 'macOS',
    file: 'installer (.pkg)',
    formats: 'Audio Unit, VST3 and CLAP',
    needs: 'macOS 10.15 Catalina or newer, on Apple silicon or Intel',
    versions: [
      { id: 'new', label: 'macOS 10.15 Catalina or newer', ok: true },
      { id: 'old', label: 'macOS 10.14 Mojave or older', ok: false },
    ],
    steps: 'Open the .pkg and follow the installer, then restart your DAW. In Logic Pro and GarageBand it is under {where}.',
    unsigned: 'The installer is not signed by Apple yet, so macOS blocks it the first time: open System Settings, Privacy & Security, scroll down and press Open Anyway.',
  },
  {
    id: 'windows',
    label: 'Windows',
    file: 'installer (.exe)',
    formats: 'VST3 and CLAP',
    needs: 'Windows 10 or 11, 64-bit',
    versions: [
      { id: '11', label: 'Windows 11', ok: true },
      { id: '10', label: 'Windows 10 (64-bit)', ok: true },
      { id: 'old', label: 'Windows 8.1 or older', ok: false },
    ],
    steps: 'Run the setup program, then rescan plugins in your DAW.',
    unsigned: 'The installer is not signed yet, so Windows may say it protected your PC: press More info, then Run anyway.',
  },
  {
    id: 'linux',
    label: 'Linux',
    file: 'archive (.tar.gz)',
    formats: 'VST3 and CLAP',
    needs: 'a 64-bit Intel or AMD system as new as Ubuntu 24.04',
    versions: [
      { id: 'new', label: 'As new as Ubuntu 24.04 (glibc 2.39)', ok: true },
      { id: 'old', label: 'Older than that', ok: false },
    ],
    steps: 'Unpack it and run sh install.sh in the folder, then rescan plugins in your DAW. Linux has no single installer format, so this is a script.',
    unsigned: '',
  },
];

export const systemInfo = (id: SystemId): SystemInfo => SYSTEMS.find((s) => s.id === id)!;

/** What the browser can tell us about the machine it is on. Any part may be unknown. */
export interface Machine {
  system?: SystemId;
  /** A phone or tablet: no plugin host runs there. */
  mobile?: boolean;
  /** The id of the VersionChoice this machine is, where the browser says. */
  version?: string;
  /** The version as a person would say it ("macOS 14.5"), where the browser says. */
  versionName?: string;
  /** True on an Arm processor, where the browser says. */
  arm?: boolean;
}

/** The parts of `navigator` this reads, so a test can hand in its own. */
export interface NavigatorLike {
  userAgent: string;
  maxTouchPoints?: number;
  userAgentData?: {
    platform?: string;
    mobile?: boolean;
    getHighEntropyValues?: (hints: string[]) => Promise<{ platformVersion?: string; architecture?: string }>;
  };
}

/** The system, from what every browser says without being asked. */
export function detectSystem(nav: NavigatorLike): Machine {
  const ua = nav.userAgent;
  const said = nav.userAgentData?.platform ?? '';
  // An iPad asks for desktop sites by calling itself a Mac; a Mac has no touch screen.
  const ipad = /Macintosh/.test(ua) && (nav.maxTouchPoints ?? 0) > 1;
  if (nav.userAgentData?.mobile || ipad || /Android|iPhone|iPad|iPod/.test(ua)) return { mobile: true };
  if (said === 'macOS' || /Macintosh|Mac OS X/.test(ua)) {
    // Browsers stopped counting at 10.15, so that number means "10.15 or newer".
    const m = ua.match(/Mac OS X (\d+)[_.](\d+)/);
    const old = !!m && Number(m[1]) === 10 && Number(m[2]) < 15;
    return { system: 'macos', version: old ? 'old' : 'new', versionName: old ? `macOS ${m![1]}.${m![2]}` : undefined };
  }
  if (said === 'Windows' || /Windows/.test(ua)) {
    const nt = ua.match(/Windows NT (\d+)\.(\d+)/);
    if (nt && Number(nt[1]) < 10) return { system: 'windows', version: 'old', versionName: 'an older Windows' };
    return { system: 'windows', arm: /\bARM64\b/i.test(ua) || undefined };
  }
  if (said === 'Linux' || said === 'Chrome OS' || /Linux|CrOS|X11/.test(ua)) {
    return { system: 'linux', arm: /aarch64|arm(v\d+)?l?\b/i.test(ua) || undefined };
  }
  return {};
}

/** The same, then the real version and processor where the browser will say (Chrome, Edge). */
export async function detectMachine(nav: NavigatorLike): Promise<Machine> {
  const machine = detectSystem(nav);
  const ask = nav.userAgentData?.getHighEntropyValues;
  if (!machine.system || !ask) return machine;
  try {
    const more = await ask.call(nav.userAgentData, ['platformVersion', 'architecture']);
    if (more.architecture) machine.arm = more.architecture === 'arm';
    const parts = (more.platformVersion ?? '').split('.').map(Number);
    if (!more.platformVersion || Number.isNaN(parts[0])) return machine;
    if (machine.system === 'macos') {
      const old = parts[0] < 10 || (parts[0] === 10 && (parts[1] ?? 0) < 15);
      machine.version = old ? 'old' : 'new';
      machine.versionName = `macOS ${parts[0]}.${parts[1] ?? 0}`;
    } else if (machine.system === 'windows') {
      // Windows reports its build family: 13 and up is Windows 11, 1 to 10 is Windows 10.
      machine.version = parts[0] >= 13 ? '11' : parts[0] >= 1 ? '10' : 'old';
      machine.versionName = parts[0] >= 13 ? 'Windows 11' : parts[0] >= 1 ? 'Windows 10' : 'an older Windows';
    }
  } catch { /* the browser declined; what we have stands */ }
  return machine;
}

export interface Verdict {
  /** 'yes': it will run. 'no': it will not, and the download is held back. 'warn': it is the visitor's call. */
  state: 'yes' | 'no' | 'warn';
  text: string;
}

/** Whether the installer for `system`, version `version`, suits the machine the browser is on. */
export function checkSystem(system: SystemId, version: string, machine: Machine): Verdict {
  const info = systemInfo(system);
  const choice = info.versions.find((v) => v.id === version) ?? info.versions[0];
  if (!choice.ok) return { state: 'no', text: `These plugins need ${info.needs}.` };
  if (machine.mobile) {
    return { state: 'warn', text: `Plugins install on a computer. This is the ${info.label} installer, to open there.` };
  }
  if (machine.system && machine.system !== system) {
    return { state: 'warn', text: `This browser is on ${systemInfo(machine.system).label}. The ${info.label} installer only opens on ${info.label}.` };
  }
  if (machine.system === system && machine.arm && system !== 'macos') {
    return {
      state: 'warn',
      text: system === 'windows'
        ? 'This PC has an Arm processor. The plugins are built for x64, so they load in x64 hosts only.'
        : 'This machine has an Arm processor, and the Linux plugins are built for Intel and AMD only.',
    };
  }
  if (machine.system === system && machine.version === choice.id && machine.versionName) {
    return { state: 'yes', text: `Checked: this computer runs ${machine.versionName}, and the plugins need ${info.needs}.` };
  }
  return { state: 'yes', text: `Works on ${info.needs}.` };
}

// ── which installer ─────────────────────────────────────────────────────────────

/**
 * What a device compiles to, leaving out everything that is only a number: its kind, the
 * modules switched on, in order, and each one's options. Two devices with the same shape
 * are the same plugin with its dials in different places.
 */
export function shape(input: Partial<Settings>): string {
  const s = normalize(input);
  const options = (o: Record<string, string>): string => Object.keys(o).sort().map((k) => `${k}=${o[k]}`).join(',');
  const parts: string[] = [s.kind ?? 'effect'];
  for (const [id, part] of Object.entries(s.instrument ?? {}).sort(([a], [b]) => a.localeCompare(b))) {
    parts.push(`${id}(${options(part.options)})`);
  }
  for (const entry of s.chain) if (entry.on) parts.push(`${entry.module}(${options(entry.options)})`);
  return parts.join('>');
}

export interface Installer {
  preset: Preset;
  /** The installer for each system that has one. */
  files: Partial<Record<SystemId, string>>;
  /** False when the device has been changed from the preset, but only in its dials or name. */
  exact: boolean;
}

/**
 * The ready-made installer for the device on screen: its preset's if it is an untouched
 * preset, otherwise that of a preset with the same shape, if there is one.
 */
export function findInstaller(
  settings: Settings,
  activePreset: string | undefined,
  presets: Preset[],
  builds: Record<string, Partial<Record<SystemId, string>>>,
): Installer | undefined {
  const exact = presets.find((p) => p.id === activePreset && builds[p.id]);
  if (exact) return { preset: exact, files: builds[exact.id], exact: true };
  const mine = shape(settings);
  const kind = settings.kind ?? 'effect';
  const same = presets.filter((p) => (p.settings.kind ?? 'effect') === kind && builds[p.id] && shape(p.settings) === mine);
  // Several presets can share a shape; the one this device is named after is the one it came from.
  const from = same.find((p) => p.name === settings.name) ?? same[0];
  return from ? { preset: from, files: builds[from.id], exact: false } : undefined;
}
