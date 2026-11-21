import { defineConfig } from 'vite';

// A relative base, so the built page works from any folder it is hosted in.
export default defineConfig({
  base: './',
  worker: { format: 'es' },
});
