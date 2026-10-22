/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Production: FastAPI serves `dist/` same-origin (FRONTEND_DIST_DIR, ADR-008).
// The proxy applies to the local `npm run dev` server only.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/search': 'http://127.0.0.1:8000',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    restoreMocks: true,
    unstubGlobals: true,
  },
});
