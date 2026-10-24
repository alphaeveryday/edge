import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { execFileSync } from 'node:child_process';

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  define: { 'import.meta.env.VITE_BUILD_VERSION': JSON.stringify(execFileSync('git', ['rev-parse', '--short', 'HEAD']).toString().trim()) },
  base: '/portfolio/',
  build: { outDir: mode === 'admin' ? 'dist-admin' : 'dist', rollupOptions: { input: mode === 'admin' ? 'admin.html' : 'index.html' } },
  server: { proxy: { '/api/portfolio': 'http://127.0.0.1:8092' } },
}));
