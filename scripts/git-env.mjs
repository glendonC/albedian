// Git's environment for the Node scripts and their tests: this process's, without the variables that point git at
// another repository than the folder it is given. With GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE or GIT_COMMON_DIR set,
// `git -C <folder>` and `git init <folder>` act on the repository those name, so a test's scratch repository would
// be written into the shared one. Every git call in a tracked JavaScript or TypeScript file passes
// `env: gitEnvironment()`, checked by scripts/__tests__/git-env.test.mjs; the Python helper scripts/git_env.py is
// the same rule, checked by tests/integration/test_git_env.py.

export const GIT_REDIRECTS = Object.freeze(['GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR']);

/** This process's environment without GIT_REDIRECTS, for the env option of a git child process. */
export function gitEnvironment() {
  const env = { ...process.env };
  for (const name of GIT_REDIRECTS) delete env[name];
  return env;
}
