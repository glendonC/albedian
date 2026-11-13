// postcss ships its tokenizer (postcss/lib/tokenize, in its package exports) without types: the guard reads its tokens
declare module 'postcss/lib/tokenize' {
  import type { Input } from 'postcss';
  /** A token: its kind, its text, and where it starts and ends. */
  export type Token = [kind: string, text: string, start?: number, end?: number];
  export default function tokenizer(input: Input, options?: { ignoreErrors?: boolean }): {
    nextToken(): Token | undefined;
    endOfFile(): boolean;
  };
}
