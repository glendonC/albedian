import { graphemes } from '../text/graphemes.js';

/**
 * A refused upload, as the home shows it beside Upload (approved 2026-11-12): the file's own name, and the server's
 * own reason when it gave one. Nothing here makes up a reason: no reason from the server means line 1 alone.
 */
export interface HomeRefusal {
  /** The chosen file's own name, as the browser gives it. */
  fileName: string;
  /** The server's own words for the refusal (its `detail`), or null when its answer held none. */
  detail: string | null;
}

/**
 * Line 2: the server's reason with its first letter capital and a full stop added, or null when there is none. A reason
 * the client cut (apiClient boundedDetail) keeps its ellipsis instead of a full stop.
 */
export function refusalSentence(detail: string | null): string | null {
  const words = detail?.trim() ?? '';
  if (words === '') return null;
  const capital = words.charAt(0).toUpperCase() + words.slice(1);
  return /[.!?\u2026]$/.test(capital) ? capital : `${capital}.`;
}

/**
 * The most characters of a file's name line 1 shows, and how many of its last characters a cut keeps. Where the
 * platform has a cluster splitter they are characters as a person sees them (graphemes), so a cut never splits an
 * emoji from its skin tone or a letter from its accent; where it has none, the cut falls between code points
 * (text/graphemes.ts says what that can split).
 */
export const FILE_NAME_MAX_CHARS = 32;
export const FILE_NAME_TAIL_CHARS = 12;

/** A shown name's head and tail: the tail is its last characters, which the home keeps on screen at any width. */
export function nameParts(shown: string): { head: string; tail: string } {
  const chars = graphemes(shown);
  const cut = Math.max(0, chars.length - FILE_NAME_TAIL_CHARS);
  return { head: chars.slice(0, cut).join(''), tail: chars.slice(cut).join('') };
}

/** A file's name within the bound: a longer one is cut in the middle, so its ending (and its type) stays readable. */
export function shortFileName(name: string): string {
  const chars = graphemes(name);
  if (chars.length <= FILE_NAME_MAX_CHARS) return name;
  const head = chars.slice(0, FILE_NAME_MAX_CHARS - FILE_NAME_TAIL_CHARS - 1).join('');
  return `${head}\u2026${chars.slice(-FILE_NAME_TAIL_CHARS).join('')}`;
}
