// The page's Download: Plugin Playground's installer with a device put inside it
// (src/plugin/stamp.ts). The installers here are small stand-ins laid out as the real
// ones are (test/fixtures/make.sh); the .pkg is Apple's own pkgbuild and productbuild.
// Each stamped file is read back here by other means than stamp.ts's: Node's zlib and
// SHA-1 for the .pkg (and Apple's pkgutil, on a Mac), the system's tar for the .tar.gz.
// CI then installs real stamped installers on each system (plugin.py --check-stamped).

import { describe, expect, it } from 'vitest';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { gunzipSync, inflateSync } from 'node:zlib';
import { deviceIn, installerFileName, stampInstaller, type StampSystem } from '../src/plugin/stamp';

const fixture = (name: string): Uint8Array => new Uint8Array(readFileSync(path.join(__dirname, 'fixtures', name)));
const TEMPLATES: Record<StampSystem, string> = { macos: 'runtime-macos.pkg', windows: 'runtime-windows.exe', linux: 'runtime-linux.tar.gz' };
const DEVICE = 'ppdevice 1\nruntime 4\nname Fuzz%20%26%20Co\nslug fuzz-co\nkind effect\n@end\ncurrent 0\n';
const device = { text: DEVICE, name: 'Fuzz & <Co>', instrument: false };
const sha1 = (b: Uint8Array): string => createHash('sha1').update(b).digest('hex');

/** A xar archive, read and every checksum checked, with Node's own zlib and SHA-1. */
function openXar(bytes: Uint8Array): Map<string, Buffer> {
  const buf = Buffer.from(bytes);
  expect(buf.subarray(0, 4).toString('latin1')).toBe('xar!');
  const headerSize = buf.readUInt16BE(4);
  const packed = Number(buf.readBigUInt64BE(8));
  expect(Number(buf.readBigUInt64BE(16))).toBe(inflateSync(buf.subarray(headerSize, headerSize + packed)).length);
  const tocPacked = buf.subarray(headerSize, headerSize + packed);
  // A file's extended attributes (<ea>) have names of their own: not the file's.
  const toc = inflateSync(tocPacked).toString('utf8').replace(/<ea id="\d+">[\s\S]*?<\/ea>/g, '');
  const heap = buf.subarray(headerSize + packed);
  expect(heap.subarray(0, 20).toString('hex')).toBe(sha1(tocPacked));
  const files = new Map<string, Buffer>();
  // Each <file> whose first child is <data>: its name is the <name> after it.
  for (const m of toc.matchAll(/<file id="\d+">\s*<data>([\s\S]*?)<\/data>(?:(?!<file)[\s\S])*?<name>([^<]+)<\/name>/g)) {
    const [, data, name] = m;
    const get = (tag: string): string => data.match(new RegExp(`<${tag}(?: [^>]*)?>([^<]*)</${tag}>`))![1];
    const stored = heap.subarray(Number(get('offset')), Number(get('offset')) + Number(get('length')));
    expect(sha1(stored), `${name}: archived checksum`).toBe(get('archived-checksum'));
    const out = /x-gzip/.test(data) ? inflateSync(stored) : stored;
    expect(out.length, `${name}: size`).toBe(Number(get('size')));
    expect(sha1(out), `${name}: extracted checksum`).toBe(get('extracted-checksum'));
    files.set(name, out);
  }
  return files;
}

/** The entries of an odc cpio archive, by name. */
function cpio(bytes: Buffer): Map<string, { mode: number; data: Buffer }> {
  const out = new Map<string, { mode: number; data: Buffer }>();
  for (let at = 0; at < bytes.length;) {
    expect(bytes.subarray(at, at + 6).toString('latin1')).toBe('070707');
    const oct = (from: number, len: number): number => parseInt(bytes.subarray(at + from, at + from + len).toString('latin1'), 8);
    const nameSize = oct(59, 6);
    const size = oct(65, 11);
    const name = bytes.subarray(at + 76, at + 76 + nameSize - 1).toString('utf8');
    out.set(name, { mode: oct(18, 6), data: bytes.subarray(at + 76 + nameSize, at + 76 + nameSize + size) });
    at += 76 + nameSize + size;
    if (name === 'TRAILER!!!') break;
  }
  return out;
}

describe('the macOS installer', () => {
  it('carries the device beside its install script, with every checksum right', async () => {
    const before = openXar(fixture(TEMPLATES.macos));
    const stamped = await stampInstaller('macos', fixture(TEMPLATES.macos), device);
    const files = openXar(stamped);
    // Nothing else changed: the app it installs and its receipt are the same bytes.
    for (const name of ['Payload', 'Bom', 'PackageInfo']) expect(files.get(name)).toEqual(before.get(name));
    const scripts = cpio(gunzipSync(files.get('Scripts')!));
    expect(scripts.get('./device.ppdevice')!.data.toString('utf8')).toBe(DEVICE);
    expect(scripts.get('./device.ppdevice')!.mode).toBe(0o100644);
    expect(scripts.get('./postinstall')!.data).toEqual(cpio(gunzipSync(before.get('Scripts')!)).get('./postinstall')!.data);
    expect(scripts.get('./postinstall')!.mode & 0o777).toBe(0o755);
    expect(await deviceIn('macos', stamped)).toBe(DEVICE);
  });

  it('is titled for the device, with its name escaped', async () => {
    const files = openXar(await stampInstaller('macos', fixture(TEMPLATES.macos), device));
    expect(files.get('Distribution')!.toString('utf8')).toContain('<title>Fuzz &amp; &lt;Co&gt;</title>');
    const welcome = files.get('welcome.html')!.toString('utf8');
    expect(welcome).toContain('<b>Fuzz &amp; &lt;Co&gt;</b> is an effect');
    expect(welcome).toContain('Audio FX, Audio Units');
    const synth = openXar(await stampInstaller('macos', fixture(TEMPLATES.macos), { ...device, instrument: true }));
    expect(synth.get('welcome.html')!.toString('utf8')).toContain('AU Instruments');
  });

  it('stamped again, carries only the new device', async () => {
    const once = await stampInstaller('macos', fixture(TEMPLATES.macos), device);
    const twice = await stampInstaller('macos', once, { ...device, text: 'ppdevice 1\nsecond\n', name: 'Second' });
    const scripts = cpio(gunzipSync(openXar(twice).get('Scripts')!));
    expect([...scripts.keys()].filter((n) => n.endsWith('device.ppdevice'))).toHaveLength(1);
    expect(await deviceIn('macos', twice)).toBe('ppdevice 1\nsecond\n');
  });

  it('has no device before it is stamped, and a damaged one is refused', async () => {
    expect(await deviceIn('macos', fixture(TEMPLATES.macos))).toBeUndefined();
    const broken = fixture(TEMPLATES.macos).slice();
    broken[40] ^= 0xff;   // inside the table of contents
    await expect(stampInstaller('macos', broken, device)).rejects.toThrow();
    await expect(stampInstaller('macos', fixture(TEMPLATES.windows), device)).rejects.toThrow(/not a \.pkg/);
  });

  it.runIf(process.platform === 'darwin')('opens with Apple\'s pkgutil', async () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'stamp-'));
    const file = path.join(dir, 'Fuzz.pkg');
    writeFileSync(file, await stampInstaller('macos', fixture(TEMPLATES.macos), device));
    execFileSync('pkgutil', ['--expand-full', file, path.join(dir, 'x')]);
    const scripts = path.join(dir, 'x', 'plugins.pkg', 'Scripts');
    expect(readFileSync(path.join(scripts, 'device.ppdevice'), 'utf8')).toBe(DEVICE);
    expect(existsSync(path.join(scripts, 'postinstall'))).toBe(true);
    expect(readFileSync(path.join(dir, 'x', 'Distribution'), 'utf8')).toContain('<title>Fuzz &amp; &lt;Co&gt;</title>');
  });
});

describe('the Windows setup program', () => {
  it('ends with the device and a footer the setup reads', async () => {
    const template = fixture(TEMPLATES.windows);
    const stamped = await stampInstaller('windows', template, device);
    const bytes = Buffer.from(stamped);
    expect(bytes.subarray(0, template.length)).toEqual(Buffer.from(template));
    // The setup's [Code] (scripts/plugin.py, WINDOWS_CODE): the last 20 bytes.
    const footer = bytes.subarray(bytes.length - 20).toString('latin1');
    expect(footer.slice(0, 8)).toBe('PPDEVICE');
    const size = Number(footer.slice(8));
    expect(bytes.subarray(bytes.length - 20 - size, bytes.length - 20).toString('utf8')).toBe(DEVICE);
    expect(await deviceIn('windows', stamped)).toBe(DEVICE);
  });

  it('stamped again, carries only the new device; anything else is refused', async () => {
    const once = await stampInstaller('windows', fixture(TEMPLATES.windows), device);
    const twice = await stampInstaller('windows', once, { ...device, text: 'second' });
    expect(twice.length).toBe(fixture(TEMPLATES.windows).length + 'second'.length + 20);
    expect(await deviceIn('windows', twice)).toBe('second');
    expect(await deviceIn('windows', fixture(TEMPLATES.windows))).toBeUndefined();
    await expect(stampInstaller('windows', fixture(TEMPLATES.linux), device)).rejects.toThrow(/not a Windows program/);
  });
});

describe('the Linux archive', () => {
  it('unpacks with tar to install.sh and the device beside it', async () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'stamp-'));
    const file = path.join(dir, 'fuzz.tar.gz');
    writeFileSync(file, await stampInstaller('linux', fixture(TEMPLATES.linux), device));
    execFileSync('tar', ['-xzf', file, '-C', dir]);
    expect(readFileSync(path.join(dir, 'plugin-playground', 'device.ppdevice'), 'utf8')).toBe(DEVICE);
    expect(readFileSync(path.join(dir, 'plugin-playground', 'install.sh'), 'utf8')).toContain('install.sh');
    const listed = execFileSync('tar', ['-tzf', file], { encoding: 'utf8' }).trim().split('\n');
    expect(listed.filter((n) => n.endsWith('device.ppdevice'))).toEqual(['plugin-playground/device.ppdevice']);
  });

  it('has no device before it is stamped', async () => {
    expect(await deviceIn('linux', fixture(TEMPLATES.linux))).toBeUndefined();
    expect(await deviceIn('linux', await stampInstaller('linux', fixture(TEMPLATES.linux), device))).toBe(DEVICE);
  });
});

describe('the file names', () => {
  it('are the device\'s, with nothing a file system minds', () => {
    expect(installerFileName('macos', 'Shoegaze Wall')).toBe('Shoegaze Wall.pkg');
    expect(installerFileName('windows', 'Shoegaze Wall')).toBe('Shoegaze Wall Setup.exe');
    expect(installerFileName('linux', 'Shoegaze Wall')).toBe('shoegaze-wall-linux.tar.gz');
    expect(installerFileName('macos', 'A/B: "C"')).toBe('A-B- -C-.pkg');
    expect(installerFileName('linux', '...')).toBe('device-linux.tar.gz');
  });
});
