import { mkdirSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig, type Plugin } from 'vite';
import { verifyPublicTracked } from '../../scripts/verify-public-tracked.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));

// Benchmark runs POST their JSON here so results from any browser (built-in pane,
// Chrome, Safari, headless) land on disk without a browser automation protocol.
// SPIKE_BENCH_OUT overrides the directory; the default stays outside the repo.
function benchResultSink(): Plugin {
  const outDir = process.env.SPIKE_BENCH_OUT ?? path.join(os.tmpdir(), 'albedian-webgpu-spike');
  return {
    name: 'albedian-bench-result-sink',
    configureServer(server) {
      server.middlewares.use('/__bench', (req, res) => {
        if (req.method !== 'POST') {
          res.statusCode = 405;
          res.end();
          return;
        }
        const chunks: Buffer[] = [];
        req.on('data', (chunk: Buffer) => chunks.push(chunk));
        req.on('end', () => {
          try {
            const body = Buffer.concat(chunks).toString('utf8');
            const parsed = JSON.parse(body) as { runId?: string };
            const runId = (parsed.runId ?? `run-${Date.now()}`).replace(/[^a-zA-Z0-9_.-]/g, '_');
            mkdirSync(outDir, { recursive: true });
            const file = path.join(outDir, `${runId}.json`);
            writeFileSync(file, JSON.stringify(parsed, null, 2));
            res.setHeader('content-type', 'application/json');
            res.end(JSON.stringify({ ok: true, file }));
          } catch (error) {
            res.statusCode = 400;
            res.end(JSON.stringify({ ok: false, error: String(error) }));
          }
        });
      });
    },
  };
}

export default defineConfig({
  root: here,
  // Reuse the app's public assets so /models/lb_splats.json is the exact file the app loads.
  publicDir: path.resolve(here, '../web/public'),
  // verifyPublicTracked refuses a build while that folder holds a file git does not track or a link
  plugins: [verifyPublicTracked(), benchResultSink()],
  resolve: {
    dedupe: ['three'],
  },
  optimizeDeps: {
    include: ['three', 'three/webgpu', 'three/tsl', '@sparkjsdev/radix'],
  },
  server: {
    port: 5191,
    strictPort: true,
    host: '127.0.0.1',
  },
  build: {
    target: 'es2023',
  },
});
