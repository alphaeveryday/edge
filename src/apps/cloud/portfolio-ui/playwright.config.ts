import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  use: { baseURL: 'http://localhost:5180', reducedMotion: 'reduce' },
  projects: [{ name: 'mobile', use: { viewport: { width: 390, height: 844 } } }, { name: 'desktop', use: { viewport: { width: 1280, height: 900 } } }],
  webServer: { command: 'pnpm dev', url: 'http://localhost:5180/portfolio/', reuseExistingServer: true },
});
