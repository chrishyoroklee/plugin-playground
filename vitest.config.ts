import { defineConfig } from 'vitest/config';

// Runs in Node. The Web Audio mock lets signal objects be created without a browser.
export default defineConfig({
  test: {
    environment: 'node',
    setupFiles: ['./test/setup/webaudio-mock.ts'],
    include: ['test/**/*.test.ts'],
  },
});
