// Types for scripts/verify-public-tracked.mjs, so that a type-checked Vite config (apps/webgpu-spike's) can import
// its plugin. The plugin is typed by its shape, so this file needs no Vite types.
export declare const PUBLIC_DIR: string;
export declare const PLUGIN_NAME: string;

export interface PublicTrackedResult {
  ok: boolean;
  untracked: number | null;
  changed: number | null;
  links: number | null;
  message: string;
}

export declare function publicTrackedCheck(root: string, publicDir?: string): PublicTrackedResult;

export interface VerifyPublicTrackedPlugin {
  name: string;
  apply: 'build';
  configResolved(config: {
    publicDir: string;
    build: { watch: unknown; copyPublicDir: boolean };
    logger: { info(message: string): void };
  }): void;
  buildStart(): void;
}

export declare function verifyPublicTracked(): VerifyPublicTrackedPlugin;
