/**
 * Text split into characters as a person sees them: Unicode's extended grapheme clusters, by the platform's own
 * Intl.Segmenter. An emoji with its skin tone or its zero width joiners, a letter with its combining marks, a flag's two
 * regional indicators and CR LF are each one. Where the platform has the splitter, a cut made between these never
 * splits a character; where it has none, see below.
 *
 * The splitter is built when a cut is first needed, never when the module loads, so the app starts where the runtime
 * has none (Intl.Segmenter came to Firefox in version 125; the build's default target takes older browsers in). There
 * the text is split between code points instead, as before: a cut never falls inside a surrogate pair, so an astral
 * character stays whole, but it can split a cluster of several code points (an emoji from its skin tone, joined emoji
 * from each other, a letter from its combining accent, a flag's two letters, CR from LF).
 */
let segmenter: Intl.Segmenter | null | undefined;

function splitter(): Intl.Segmenter | null {
  if (segmenter === undefined) {
    segmenter = typeof Intl === 'object' && typeof Intl.Segmenter === 'function' ? new Intl.Segmenter(undefined, { granularity: 'grapheme' }) : null;
  }
  return segmenter;
}

export function graphemes(text: string): string[] {
  const cluster = splitter();
  return cluster ? Array.from(cluster.segment(text), (part) => part.segment) : [...text];
}
