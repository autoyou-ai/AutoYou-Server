import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react(), { name: 'portable-html-lines', transformIndexHtml(html) { return html.replace(/\r\n?/g, '\n'); } }],
  base: './',
  // The native runtime packager deliberately excludes folders named dist.
  build: { outDir: 'web', emptyOutDir: true, target: 'es2022' },
  server: { proxy: { '/api': 'http://127.0.0.1:8112', '/health': 'http://127.0.0.1:8112' } },
});
