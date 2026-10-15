import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  base: '/portfolio/',
  build: { outDir: mode === 'admin' ? 'dist-admin' : 'dist', rollupOptions: { input: mode === 'admin' ? 'admin.html' : 'index.html' } },
  server: { proxy: { '/api/portfolio': 'http://127.0.0.1:8092' } },
}));
