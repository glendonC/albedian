import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { verifyPublicTracked } from '../../scripts/verify-public-tracked.mjs';

export default defineConfig({
  // verifyPublicTracked refuses a build while the public folder holds a file git does not track or a link
  plugins: [verifyPublicTracked(), react()],
  resolve: {
    dedupe: ['three'],
  },
  optimizeDeps: {
    include: ['three', 'three/webgpu', 'three/tsl', '@sparkjsdev/spark'],
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.ALBEDIAN_API_TARGET?.trim() || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
  },
});
