import { configDefaults, defineConfig } from 'vitest/config';

// Session worktrees under .claude/ are full copies of the repo; their tests belong to those sessions
export default defineConfig({
  test: {
    exclude: [...configDefaults.exclude, '.claude/**'],
  },
});
