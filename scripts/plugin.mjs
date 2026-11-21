// `npm run plugin` on any system: Python is `python3` on macOS and Linux and usually
// `python` on Windows. Everything else is in plugin.py, next to this file.
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const script = fileURLToPath(new URL('./plugin.py', import.meta.url));
for (const python of process.platform === 'win32' ? ['python', 'py', 'python3'] : ['python3', 'python']) {
  const run = spawnSync(python, [script, ...process.argv.slice(2)], { stdio: 'inherit' });
  if (run.error?.code === 'ENOENT') continue;
  process.exit(run.status ?? 1);
}
console.error('plugin: Python 3 is not installed (https://www.python.org/downloads/)');
process.exit(1);
