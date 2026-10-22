import path from 'node:path';
import { defineConfig, devices } from '@playwright/test';

// Both projects drive the built page as FastAPI serves it (same origin, FRONTEND_DIST_DIR), so
// the static-serving integration and its Content-Security-Policy are exercised too.
//   fixture: every /search/** and /assistant/** request is answered by the browser from
//            synthetic fixtures; no request reaches the API, the database or a model.
//   live:    no mocks; needs the local stack (database, seeded catalog, embeddings, local model
//            snapshots) configured for the API by the repository's usual settings.
// Run `npm run build` first. Reports from the two projects are separate by design.
// E2E_BASE_URL (M26A): drive an already running server instead, such as the local container
// image; no server is started and the local build is not used.
const repoRoot = path.resolve(import.meta.dirname, '..');
const port = Number(process.env.E2E_PORT ?? 8765);
const externalBaseURL = process.env.E2E_BASE_URL;
const baseURL = externalBaseURL ?? `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  outputDir: './test-results',
  use: {
    baseURL,
    trace: 'off',
    screenshot: 'off',
  },
  projects: [
    {
      name: 'fixture',
      testMatch: ['fixture.spec.ts', 'assistant-fixture.spec.ts'],
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'live',
      testMatch: 'live.spec.ts',
      timeout: 120_000,
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: externalBaseURL
    ? undefined
    : {
        command: `uv run uvicorn ecommerce_search.api.app:create_app --factory --host 127.0.0.1 --port ${port}`,
        cwd: repoRoot,
        env: { FRONTEND_DIST_DIR: path.join(repoRoot, 'frontend', 'dist') },
        url: `${baseURL}/`,
        reuseExistingServer: false,
        timeout: 120_000,
        stdout: 'ignore',
        stderr: 'pipe',
      },
});
