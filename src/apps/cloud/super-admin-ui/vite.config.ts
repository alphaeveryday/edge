import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// super-admin-ui — 운영자 콘솔 (Node 워크스페이스 멤버). tenant-console-ui 와 동일 스택.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5175,
  },
});
