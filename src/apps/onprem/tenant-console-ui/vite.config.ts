import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// tenant-console-ui — 내부 관리자 콘솔 (Node 워크스페이스 멤버)
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
  },
});
