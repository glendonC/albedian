import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { verifyPublicTracked } from '../../scripts/verify-public-tracked.mjs';
import { refuseOnNetwork } from '../../scripts/field-proxy-guard.mjs';

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
        // Refuses media discover, which lists data/uploads, to any client that is not this machine (pnpm field --lan)
        bypass: refuseOnNetwork,
      },
      // The music samples, from apps/web/spike/music/serveSources.mjs on this machine (pnpm field starts it), so the
      // laptop and a phone on the network fetch them from the app's own origin
      '/music-sources/': {
        target: process.env.ALBEDIAN_MUSIC_TARGET?.trim() || 'http://127.0.0.1:5199',
        changeOrigin: true,
        rewrite: (url) => url.slice('/music-sources'.length),
      },
    },
  },
  test: {
    environment: 'jsdom',
  },
});
