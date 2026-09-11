// vitest's defineConfig, not vite's — it is the one that knows about `test`.
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    // `npm run dev` serves the UI; the Python dashboard serves the API.
    proxy: { '/api': 'http://127.0.0.1:8500' },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // Stable filenames, not content hashes. The build is committed into the
    // Python package, and a hashed name would change on every rebuild — a
    // two-file diff for a one-line CSS tweak. Nothing serves these from a CDN.
    rollupOptions: {
      output: {
        entryFileNames: 'assets/[name].js',
        chunkFileNames: 'assets/[name].js',
        assetFileNames: 'assets/[name][extname]',
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
  },
});
