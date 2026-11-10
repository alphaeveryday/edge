import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// b2b-console-ui — B2B 제안용 콘솔 데모. quarterback.etforca.com 으로 서빙한다.
// 통화 화면(#/call)의 /api/* 는 배포에선 CloudFront 가 통화 데모 함수로 보낸다. 로컬은 QB_API(기본: 배포된 사이트)로 넘긴다.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5177,
    proxy: { '/api': { target: process.env.QB_API ?? 'https://quarterback.etforca.com', changeOrigin: true } },
  },
});
