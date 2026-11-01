import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      // Local dev convenience: proxies to the Modal/uvicorn backend so CORS
      // never bites while iterating. In production the client hits
      // VITE_BACKEND_URL directly (with the Modal-side CORS allowlist).
      '/api': {
        target: process.env.VITE_DEV_BACKEND || 'http://127.0.0.1:8080',
        changeOrigin: true,
        // Reasoning models need a long window; Vite's proxy otherwise aborts
        // the upstream connection at the Node default.
        timeout: 300000,
        proxyTimeout: 300000,
      },
    },
  },
  preview: {
    host: '127.0.0.1',
    port: 4173,
    strictPort: true,
  },
  build: {
    target: 'es2022',
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
});
