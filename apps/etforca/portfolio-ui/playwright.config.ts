import { defineConfig } from '@playwright/test';

const port = process.env.PLAYWRIGHT_PORT || '5180';
export default defineConfig({
  testDir: './tests',
  use: { baseURL: `http://localhost:${port}`, reducedMotion: 'reduce' },
  projects: [{ name: 'mobile', use: { viewport: { width: 390, height: 844 } } }, { name: 'desktop', use: { viewport: { width: 1280, height: 900 } } }],
  webServer: { command: `pnpm dev --port ${port} --strictPort`, url: `http://localhost:${port}/portfolio/`, reuseExistingServer: false },
});
