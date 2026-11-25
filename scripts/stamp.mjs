// stamp: put a device into Plugin Playground's installer, as the page's Download does.
//
//   node scripts/stamp.mjs SYSTEM INSTALLER DEVICE.ppdevice OUT [--name NAME] [--instrument]
//   node scripts/stamp.mjs --read SYSTEM INSTALLER        # print the device an installer carries
//
// SYSTEM is macos, windows or linux. The work is src/plugin/stamp.ts, the page's own code,
// run here so CI can install what a visitor would download (scripts/plugin.py,
// --check-stamped). Needs Node 22.18 or newer, which runs TypeScript as it is.

import { readFile, writeFile } from 'node:fs/promises';
import { deviceIn, stampInstaller } from '../src/plugin/stamp.ts';

const args = process.argv.slice(2);
const usage = () => {
  console.error('usage: node scripts/stamp.mjs SYSTEM INSTALLER DEVICE OUT [--name NAME] [--instrument]\n' +
    '       node scripts/stamp.mjs --read SYSTEM INSTALLER');
  process.exit(2);
};

try {
  if (args[0] === '--read') {
    if (args.length !== 3) usage();
    const found = await deviceIn(args[1], new Uint8Array(await readFile(args[2])));
    if (found === undefined) {
      console.error('this installer carries no device');
      process.exit(1);
    }
    process.stdout.write(found);
  } else {
    const [system, installer, device, out] = args;
    if (!out || !['macos', 'windows', 'linux'].includes(system)) usage();
    const text = await readFile(device, 'utf8');
    const named = args.includes('--name') ? args[args.indexOf('--name') + 1] : null;
    const fromFile = decodeURIComponent((text.match(/^name (\S+)$/m) ?? [, 'Device'])[1]);
    const stamped = await stampInstaller(system, new Uint8Array(await readFile(installer)), {
      text, name: named ?? fromFile, instrument: args.includes('--instrument'),
    });
    await writeFile(out, stamped);
    console.log(`stamped ${out} (${stamped.length} bytes)`);
  }
} catch (err) {
  console.error(`stamp: ${err.message ?? err}`);
  process.exit(1);
}
