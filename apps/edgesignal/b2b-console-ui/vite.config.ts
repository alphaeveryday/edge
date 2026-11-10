import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// b2b-console-ui — B2B 제안용 콘솔 데모(정적, 백엔드 없음). quarterback.etforca.com 으로 서빙한다.
export default defineConfig({
  plugins: [react()],
  server: { port: 5177 },
});
