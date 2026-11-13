"""Git's environment for the scripts and the tests that run git: this process's, without the variables that point
git at another repository than the folder it is given.

With GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE or GIT_COMMON_DIR set, `git -C <folder>` and `git init <folder>` act on
the repository those name instead, so a test that builds a throwaway repository would write into the shared one.
Every git call in a tracked Python file passes `env=git_environment()`; tests/integration/test_git_env.py checks
it. The JavaScript and TypeScript files use scripts/git-env.mjs, the same rule, checked by
scripts/__tests__/git-env.test.mjs.
"""

from __future__ import annotations

import os

GIT_REDIRECTS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")


def git_environment() -> dict[str, str]:
    """This process's environment without GIT_REDIRECTS, for subprocess's env= on a git call."""
    return {key: value for key, value in os.environ.items() if key not in GIT_REDIRECTS}
