import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// tenant-console-ui — 내부 관리자 콘솔 (Node 워크스페이스 멤버)
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '');
  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5174,
      // tenant-console-api 프록시 — same-origin 이 되어 세션 쿠키(SameSite=Strict)가
      // 실린다. 기본 18081 = compose 의 tenant-console-api. bootRun 직접 기동이면
      // VITE_API_PROXY_TARGET=http://localhost:8080 으로 덮는다.
      proxy: {
        '/api': {
          target: env.VITE_API_PROXY_TARGET || 'http://localhost:18081',
          changeOrigin: true,
        },
      },
    },
  };
});
