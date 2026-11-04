import { defineConfig } from '@playwright/test';

const port = process.env.PLAYWRIGHT_PORT || '5180';
// 시험 브라우저는 실제 PostHog 에 닿지 못한다. 가로채기(route)가 끝난 뒤 페이지를 닫으며 밀린 이벤트를 보내던 것이
// 운영 분석에 시험 방문자로 쌓였다(2026-10-21~22, 413명). 이름 풀이를 막아도 route 가로채기는 그대로 동작한다.
export const blockAnalyticsHost = '--host-resolver-rules=MAP *.posthog.com ~NOTFOUND';
export default defineConfig({
  testDir: './tests',
  use: { baseURL: `http://localhost:${port}`, reducedMotion: 'reduce', launchOptions: { args: [blockAnalyticsHost] } },
  projects: [{ name: 'mobile', use: { viewport: { width: 390, height: 844 } } }, { name: 'desktop', use: { viewport: { width: 1280, height: 900 } } }],
  webServer: { command: `pnpm dev --port ${port} --strictPort`, url: `http://localhost:${port}/portfolio/`, reuseExistingServer: false },
});
