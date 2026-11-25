// One download that is the plugin: Plugin Playground's installer with a device inside.
//
// CI builds Plugin Playground's installer for each system (scripts/plugin.py, --runtime
// --package). Each one installs the runtime and, if it carries a device, makes that
// device into plugins straight away. The page cannot compile, but it can put a device
// into an installer that is already built, which is what this file does:
//
//   macOS    the .pkg is a xar archive. The device joins the install script in its Scripts
//            archive (a gzipped cpio), where the script looks for it; the archive's table
//            of contents is written again with the new offsets and checksums. Its title and
//            welcome page are the device's.
//   Windows  the setup program is left as it is and the device is added after its end,
//            with a footer saying how long it is. The setup reads its own file for it.
//   Linux    the .tar.gz gets one more file, device.ppdevice, next to install.sh.
//
// Nothing here needs the DOM: the tests and scripts/stamp.mjs run it under Node. A stamped
// installer is never signed (changing it would break a signature), which is why the page
// says how to get past the warning that comes with that.

export type StampSystem = 'macos' | 'windows' | 'linux';

export interface StampedDevice {
  /** The device file, as patchc writes it. */
  text: string;
  /** The device's name, as the page shows it. */
  name: string;
  instrument: boolean;
}

/** The file name a stamped installer downloads as. */
export function installerFileName(system: StampSystem, name: string): string {
  const safe = name.replace(/[\\/:*?"<>|\u0000-\u001f]/g, '-').replace(/^[ .]+|[ .]+$/g, '') || 'Device';
  if (system === 'macos') return `${safe}.pkg`;
  if (system === 'windows') return `${safe} Setup.exe`;
  const slug = safe.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'device';
  return `${slug}-linux.tar.gz`;
}

/** `template` (this system's installer of Plugin Playground) with `device` inside it. */
export async function stampInstaller(system: StampSystem, template: Uint8Array, device: StampedDevice): Promise<Uint8Array> {
  if (system === 'macos') return stampPkg(template, device);
  if (system === 'windows') return stampExe(template, device);
  return stampTar(template, device);
}

/** The device an installer carries, or undefined for none. Throws if the installer is damaged. */
export async function deviceIn(system: StampSystem, installer: Uint8Array): Promise<string | undefined> {
  if (system === 'macos') {
    const xar = await readXar(installer);
    const scripts = await xar.read('Scripts');
    const entry = cpioEntries(await gunzip(scripts)).find((e) => e.name === DEVICE_IN_SCRIPTS);
    return entry ? text(entry.data) : undefined;
  }
  if (system === 'windows') {
    const found = exeFooter(installer);
    return found ? text(installer.subarray(found.start, found.end)) : undefined;
  }
  const tar = await gunzip(installer);
  let found: string | undefined;
  for (const e of tarEntries(tar)) if (e.name.endsWith(`/${DEVICE_FILE}`)) found = text(e.data);
  return found;
}

const DEVICE_FILE = 'device.ppdevice';
const DEVICE_IN_SCRIPTS = `./${DEVICE_FILE}`;
const utf8 = new TextEncoder();
const text = (b: Uint8Array): string => new TextDecoder().decode(b);

// ── compression and checksums, as browsers and Node both have them ──────────────

async function through(data: Uint8Array, stream: CompressionStream | DecompressionStream): Promise<Uint8Array> {
  const out = new Blob([data as BlobPart]).stream().pipeThrough(stream);
  return new Uint8Array(await new Response(out).arrayBuffer());
}
/** zlib, as xar keeps its table of contents and its compressed files. */
const deflate = (d: Uint8Array): Promise<Uint8Array> => through(d, new CompressionStream('deflate'));
const inflate = (d: Uint8Array): Promise<Uint8Array> => through(d, new DecompressionStream('deflate'));
const gzip = (d: Uint8Array): Promise<Uint8Array> => through(d, new CompressionStream('gzip'));
const gunzip = (d: Uint8Array): Promise<Uint8Array> => through(d, new DecompressionStream('gzip'));

async function sha1(d: Uint8Array): Promise<Uint8Array> {
  return new Uint8Array(await crypto.subtle.digest('SHA-1', d as BufferSource));
}
const hex = (b: Uint8Array): string => Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');

function concat(parts: Uint8Array[]): Uint8Array {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) { out.set(p, at); at += p.length; }
  return out;
}

// ── macOS: the .pkg (a xar archive) ─────────────────────────────────────────────

const xmlEscape = (s: string): string =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

async function stampPkg(template: Uint8Array, device: StampedDevice): Promise<Uint8Array> {
  const xar = await readXar(template);
  const entries = cpioEntries(await gunzip(await xar.read('Scripts'))).filter((e) => e.name !== DEVICE_IN_SCRIPTS);
  const trailer = entries.findIndex((e) => e.name === 'TRAILER!!!');
  if (trailer < 0) throw new Error('the installer has no install script to put the device beside');
  const ino = Math.max(0, ...entries.map((e) => e.ino)) + 1;
  entries.splice(trailer, 0, { name: DEVICE_IN_SCRIPTS, ino, mode: 0o100644, data: utf8.encode(device.text), raw: undefined });
  await xar.write('Scripts', await gzip(cpioBytes(entries)), false);

  const name = xmlEscape(device.name);
  const distribution = text(await xar.read('Distribution')).replace(/<title>[^<]*<\/title>/, () => `<title>${name}</title>`);
  await xar.write('Distribution', utf8.encode(distribution), true);
  const where = device.instrument ? 'Instrument, AU Instruments, Plugin Playground' : 'Audio FX, Audio Units, Plugin Playground';
  const welcome =
    `<p><b>${name}</b> is ${device.instrument ? 'an instrument' : 'an effect'} made with Plugin Playground.</p>` +
    '<p>This installs it as an Audio Unit, a VST3 and a CLAP plugin, so every DAW on this Mac can use it, ' +
    'together with Plugin Playground, which runs it.</p>' +
    `<p>When it has finished, restart your DAW. In Logic Pro and GarageBand it is under <b>${where}</b>.</p>`;
  await xar.write('welcome.html', utf8.encode(
    `<html><body style="font: 13px -apple-system, sans-serif">${welcome}</body></html>`), true);
  return xar.bytes();
}

interface Xar {
  read(name: string): Promise<Uint8Array>;
  /** Replace a file's contents. The new bytes go at the end of the heap; the old ones stay, unlisted. */
  write(name: string, data: Uint8Array, compress: boolean): Promise<void>;
  bytes(): Promise<Uint8Array>;
}

/**
 * A xar archive: a 28-byte header, the table of contents (XML, zlib), then the heap. The
 * table lists each file's offset into the heap, its length there, its size unpacked and
 * a SHA-1 of each; the first bytes of the heap are the SHA-1 of the compressed table.
 * Reading checks every checksum, so a damaged download is caught here and not by Installer.
 */
async function readXar(bytes: Uint8Array): Promise<Xar> {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (bytes.length < 28 || text(bytes.subarray(0, 4)) !== 'xar!') throw new Error('this is not a .pkg');
  const headerSize = view.getUint16(4);
  const packed = Number(view.getBigUint64(8));
  if (view.getUint32(24) !== 1) throw new Error('the .pkg uses a checksum other than SHA-1');
  const tocPacked = bytes.subarray(headerSize, headerSize + packed);
  let toc = text(await inflate(tocPacked));
  const heap = bytes.subarray(headerSize + packed);
  const sum = toc.match(/<checksum style="sha1">\s*<offset>(\d+)<\/offset>\s*<size>(\d+)<\/size>/);
  if (!sum) throw new Error('the .pkg has no checksum of its contents');
  const sumAt = Number(sum[1]);
  if (hex(heap.subarray(sumAt, sumAt + 20)) !== hex(await sha1(tocPacked))) throw new Error('the .pkg is damaged: its contents do not match their checksum');

  const added: Uint8Array[] = [];
  let heapLength = heap.length;
  // A file's <data> block: the one followed, before any other <file>, by its <name>.
  const block = (name: string): RegExp => new RegExp(
    `(<file id="\\d+">\\s*<data>)((?:(?!<file)[\\s\\S])*?)(</data>)(?=(?:(?!<file)[\\s\\S])*?<name>${name.replace(/\./g, '\\.')}</name>)`);
  const field = (body: string, tag: string): string => {
    const m = body.match(new RegExp(`<${tag}(?: [^>]*)?>([^<]*)</${tag}>`));
    if (!m) throw new Error(`the .pkg lists a file without its ${tag}`);
    return m[1];
  };
  const find = (name: string): string => {
    const m = toc.match(block(name));
    if (!m) throw new Error(`the .pkg has no ${name}`);
    return m[2];
  };

  return {
    async read(name) {
      const body = find(name);
      const at = Number(field(body, 'offset'));
      const stored = heap.subarray(at, at + Number(field(body, 'length')));
      if (hex(await sha1(stored)) !== field(body, 'archived-checksum')) throw new Error(`the .pkg is damaged: ${name} does not match its checksum`);
      return /application\/x-gzip/.test(body) ? inflate(stored) : stored;
    },
    async write(name, data, compress) {
      const stored = compress ? await deflate(data) : data;
      const offset = heapLength;
      added.push(stored);
      heapLength += stored.length;
      const [archived, extracted] = [hex(await sha1(stored)), hex(await sha1(data))];
      toc = toc.replace(block(name), (_all, open: string, body: string, close: string) => {
        const set = (b: string, tag: string, value: string | number): string =>
          b.replace(new RegExp(`(<${tag}(?: [^>]*)?>)[^<]*(</${tag}>)`), `$1${value}$2`);
        let b = set(set(set(body, 'length', stored.length), 'offset', offset), 'size', data.length);
        b = set(set(b, 'archived-checksum', archived), 'extracted-checksum', extracted);
        b = b.replace(/<encoding style="[^"]*"\/>/, `<encoding style="application/${compress ? 'x-gzip' : 'octet-stream'}"/>`);
        return open + b + close;
      });
    },
    async bytes() {
      const tocBytes = utf8.encode(toc);
      const newPacked = await deflate(tocBytes);
      const newHeap = concat([heap, ...added]);
      newHeap.set(await sha1(newPacked), sumAt);
      const header = new Uint8Array(28);
      const h = new DataView(header.buffer);
      header.set(utf8.encode('xar!'), 0);
      h.setUint16(4, 28);
      h.setUint16(6, 1);
      h.setBigUint64(8, BigInt(newPacked.length));
      h.setBigUint64(16, BigInt(tocBytes.length));
      h.setUint32(24, 1);
      return concat([header, newPacked, newHeap]);
    },
  };
}

// The Scripts archive: cpio in the "odc" form, each entry a 76-byte header in octal
// digits, the name and its NUL, then the data.
interface CpioEntry {
  name: string;
  ino: number;
  mode: number;
  data: Uint8Array;
  /** The entry exactly as read, written back as it was. */
  raw: Uint8Array | undefined;
}

function cpioEntries(bytes: Uint8Array): CpioEntry[] {
  const out: CpioEntry[] = [];
  let at = 0;
  const oct = (from: number, len: number): number => parseInt(text(bytes.subarray(at + from, at + from + len)), 8);
  while (at + 76 <= bytes.length) {
    if (text(bytes.subarray(at, at + 6)) !== '070707') throw new Error('the install scripts are not a cpio archive');
    const nameSize = oct(59, 6);
    const size = oct(65, 11);
    const name = text(bytes.subarray(at + 76, at + 76 + nameSize - 1));
    const end = at + 76 + nameSize + size;
    out.push({ name, ino: oct(12, 6), mode: oct(18, 6), data: bytes.subarray(at + 76 + nameSize, end), raw: bytes.subarray(at, end) });
    at = end;
    if (name === 'TRAILER!!!') break;
  }
  return out;
}

function cpioBytes(entries: CpioEntry[]): Uint8Array {
  return concat(entries.map((e) => {
    if (e.raw) return e.raw;
    const o = (n: number, w: number): string => n.toString(8).padStart(w, '0');
    const name = utf8.encode(e.name);
    const header = '070707' + o(0, 6) + o(e.ino, 6) + o(e.mode, 6) + o(0, 6) + o(0, 6) + o(1, 6) + o(0, 6) +
      o(0, 11) + o(name.length + 1, 6) + o(e.data.length, 11);
    return concat([utf8.encode(header), name, new Uint8Array(1), e.data]);
  }));
}

// ── Windows: the device after the end of the setup program ──────────────────────
//
// Then a footer of 20 bytes: PPDEVICE and the device's length in 12 decimal digits. The
// setup (its [Code], scripts/plugin.py) looks at the last 20 bytes of its own file.

const FOOTER = 'PPDEVICE';

function exeFooter(exe: Uint8Array): { start: number; end: number } | undefined {
  if (exe.length < 20) return undefined;
  const tail = text(exe.subarray(exe.length - 20));
  if (!tail.startsWith(FOOTER) || !/^\d{12}$/.test(tail.slice(8))) return undefined;
  const end = exe.length - 20;
  return { start: end - Number(tail.slice(8)), end };
}

function stampExe(template: Uint8Array, device: StampedDevice): Uint8Array {
  if (text(template.subarray(0, 2)) !== 'MZ') throw new Error('this is not a Windows program');
  const before = exeFooter(template);
  const exe = before ? template.subarray(0, before.start) : template;
  const data = utf8.encode(device.text);
  return concat([exe, data, utf8.encode(FOOTER + String(data.length).padStart(12, '0'))]);
}

// ── Linux: one more file in the .tar.gz ─────────────────────────────────────────

interface TarEntry {
  name: string;
  data: Uint8Array;
}

function tarEntries(tar: Uint8Array): TarEntry[] & { end: number } {
  const out = [] as unknown as TarEntry[] & { end: number };
  let at = 0;
  while (at + 512 <= tar.length) {
    const header = tar.subarray(at, at + 512);
    if (header.every((b) => b === 0)) break;
    const field = (from: number, len: number): string => text(header.subarray(from, from + len)).replace(/\0.*$/s, '');
    const size = parseInt(field(124, 12).trim() || '0', 8);
    const prefix = field(345, 155);
    const name = (prefix ? `${prefix}/` : '') + field(0, 100);
    const type = field(156, 1);
    if (type === '0' || type === '') out.push({ name, data: tar.subarray(at + 512, at + 512 + size) });
    else if (!'5xgLK12'.includes(type)) out.push({ name, data: new Uint8Array() });
    at += 512 + Math.ceil(size / 512) * 512;
  }
  out.end = at;
  return out;
}

function tarHeader(name: string, size: number, mode: number): Uint8Array {
  const h = new Uint8Array(512);
  const put = (from: number, value: string): void => h.set(utf8.encode(value), from);
  put(0, name);
  put(100, mode.toString(8).padStart(7, '0') + '\0');
  put(108, '0000000\0');
  put(116, '0000000\0');
  put(124, size.toString(8).padStart(11, '0') + '\0');
  put(136, Math.floor(Date.now() / 1000).toString(8).padStart(11, '0') + '\0');
  put(148, '        ');
  put(156, '0');
  put(257, 'ustar\0');
  put(263, '00');
  const sum = h.reduce((n, b) => n + b, 0);
  put(148, sum.toString(8).padStart(6, '0') + '\0 ');
  return h;
}

async function stampTar(template: Uint8Array, device: StampedDevice): Promise<Uint8Array> {
  const tar = await gunzip(template);
  const entries = tarEntries(tar);
  const top = entries.map((e) => e.name.replace(/^\.\//, '').split('/')[0]).find((n) => n) ?? 'plugin-playground';
  const name = `${top}/${DEVICE_FILE}`;
  if (utf8.encode(name).length > 99) throw new Error('the archive\'s folder name is too long');
  const data = utf8.encode(device.text);
  const padded = new Uint8Array(Math.ceil(data.length / 512) * 512);
  padded.set(data);
  // The new file where the archive ended, then the two empty blocks that end it again.
  return gzip(concat([tar.subarray(0, entries.end), tarHeader(name, data.length, 0o644), padded, new Uint8Array(1024)]));
}
