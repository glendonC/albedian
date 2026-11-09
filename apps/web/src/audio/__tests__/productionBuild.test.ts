import { fileURLToPath } from 'node:url';
import { build } from 'vite';
import { describe, expect, it } from 'vitest';

/**
 * The development-only parts of the music (the local sources server's address and the `?music=`
 * read) must not reach a production build. This builds the modules the app ships for the audio
 * with Vite, in production and, as a control that the check can see them, in development.
 */

const web = fileURLToPath(new URL('../../../', import.meta.url));

const ENTRIES = {
  engine: `${web}src/audio/ObservatoryAudioEngine.ts`,
  feed: `${web}src/audio/SkyAudioFeed.ts`,
  control: `${web}src/audio/MusicSettingControl.tsx`,
  credits: `${web}src/audio/useAudioCreditLines.ts`,
  moment: `${web}src/audio/music/soundtrackMoment.ts`,
};

const MUSIC_QUERY_READ = /\.get\(\s*["'`]music["'`]\s*\)/;

/** The bundled code of the audio modules. Vite decides development or production from NODE_ENV, as `pnpm build` leaves it. */
async function bundle(nodeEnv: 'production' | 'development'): Promise<string> {
  const before = process.env.NODE_ENV;
  process.env.NODE_ENV = nodeEnv;
  try {
    const result = await build({
      configFile: false,
      logLevel: 'silent',
      root: web,
      mode: nodeEnv,
      build: {
        write: false,
        minify: nodeEnv === 'production',
        lib: { entry: ENTRIES, formats: ['es'] },
        // Packages stay out: only the app's own code is checked
        rollupOptions: { external: (id) => !id.startsWith('.') && !id.startsWith('/') && !id.startsWith('\0') },
      },
    });
    const outputs = (Array.isArray(result) ? result : [result]) as { output: { type: string; code?: string }[] }[];
    return outputs.flatMap((o) => o.output).flatMap((chunk) => (chunk.type === 'chunk' && chunk.code ? [chunk.code] : [])).join('\n');
  } finally {
    if (before === undefined) delete process.env.NODE_ENV;
    else process.env.NODE_ENV = before;
  }
}

describe('a production build of the audio', () => {
  it('carries neither the development sources address nor a read of ?music=', { timeout: 60_000 }, async () => {
    const production = await bundle('production');
    expect(production.length).toBeGreaterThan(10_000);
    expect(production).not.toContain('5199');
    expect(production).not.toMatch(MUSIC_QUERY_READ);

    // The control: a development build of the same modules carries both
    const development = await bundle('development');
    expect(development).toContain('127.0.0.1:5199');
    expect(development).toMatch(MUSIC_QUERY_READ);
  });
});
