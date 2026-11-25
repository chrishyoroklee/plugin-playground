import { readFileSync } from 'node:fs';
import { defineConfig } from 'vite';

const { version } = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8')) as { version: string };

// A relative base, so the built page works from any folder it is hosted in. The version
// is package.json's, shown in the help menu.
export default defineConfig({
  base: './',
  worker: { format: 'es' },
  define: { __APP_VERSION__: JSON.stringify(version) },
});
