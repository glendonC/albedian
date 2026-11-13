// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';

// The app starts where the runtime has no cluster splitter (Intl.Segmenter: Firefox before 125, for one, which the
// build's default target takes in): nothing is built when a module loads, and a cut then falls between code points,
// never inside a surrogate pair (arrive-home's fourth version).

const intl = Intl as unknown as Record<string, unknown>;
const saved = intl.Segmenter;
afterEach(() => {
  intl.Segmenter = saved;
  vi.resetModules();
});

describe('without Intl.Segmenter', () => {
  it('imports the splitter, the refusal, the API client and the app, and none of them throws', async () => {
    delete intl.Segmenter;
    vi.resetModules();
    expect('Segmenter' in Intl).toBe(false);
    for (const module of ['../graphemes.js', '../../components/homeRefusal.js', '../../services/apiClient.js', '../../App.js']) {
      await expect(import(/* @vite-ignore */ module), module).resolves.toBeDefined();
    }
  });

  it('cuts a name and a reason between code points, never inside a surrogate pair', async () => {
    delete intl.Segmenter;
    vi.resetModules();
    const { graphemes } = await import('../graphemes.js');
    const { shortFileName } = await import('../../components/homeRefusal.js');
    const { boundedDetail, UPLOAD_DETAIL_MAX_CHARS } = await import('../../services/apiClient.js');
    expect(() => graphemes('ab'), 'a cut with no splitter').not.toThrow();
    // Code points: an accent apart from its letter, a skin tone apart from its emoji; an astral character whole
    expect(graphemes('éx')).toEqual(['e', '́', 'x']);
    expect(graphemes('\u{1F44D}\u{1F3FD}')).toEqual(['\u{1F44D}', '\u{1F3FD}']);
    expect(graphemes('\u{12000}\u{12001}')).toEqual(['\u{12000}', '\u{12001}']);
    expect(shortFileName('\u{12000}'.repeat(40))).toBe(`${'\u{12000}'.repeat(19)}…${'\u{12000}'.repeat(12)}`);
    expect(boundedDetail('\u{12000}'.repeat(UPLOAD_DETAIL_MAX_CHARS + 1))).toBe(`${'\u{12000}'.repeat(UPLOAD_DETAIL_MAX_CHARS)}…`);
    // No lone surrogate anywhere in a cut
    const cut = shortFileName('\u{1F44D}\u{1F3FD}'.repeat(40));
    expect(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(cut)).toBe(false);
  });
});

describe('with Intl.Segmenter', () => {
  it('builds the splitter when a cut is first needed, not when the module loads, and only once', async () => {
    let built = 0;
    const Real = saved as typeof Intl.Segmenter;
    intl.Segmenter = class extends Real {
      constructor(...args: ConstructorParameters<typeof Intl.Segmenter>) {
        super(...args);
        built += 1;
      }
    };
    vi.resetModules();
    const { graphemes } = await import('../graphemes.js');
    expect(built).toBe(0);
    expect(graphemes('éx')).toEqual(['é', 'x']);
    expect(built).toBe(1);
    graphemes('ab');
    expect(built).toBe(1);
  });
});
