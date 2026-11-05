#!/usr/bin/env node
/**
 * The public folder check, which the web build runs first (apps/web/package.json). Vite copies every file under
 * apps/web/public into apps/web/dist, dotfiles included, whether git tracks it or not, so a file git does not track
 * there, ignored or not, would ship with the build. The check lists every file under the folder and every file git
 * tracks there, and fails when any file is untracked.
 * - Vite also follows symbolic links when it copies the folder, so a link git tracks would ship whatever it points
 *   to, which git may not track. The check refuses every symbolic link under the folder, tracked or not, without
 *   following it, and counts links apart from untracked files.
 * - Its message gives only the counts and the commands that list the entries on this machine, never a path, so a
 *   build log never carries a file's or folder's name.
 * - Where git cannot list what it tracks (outside a git checkout, or without git), it fails: a build that cannot
 *   tell what it would ship should not ship.
 */
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const PUBLIC_DIR = 'apps/web/public';

/** Every entry under folder, as POSIX paths relative to root: { files, links }. Links are listed, never followed. */
function entriesUnder(root, folder) {
  const files = [];
  const links = [];
  const walk = (relative) => {
    for (const entry of fs.readdirSync(path.join(root, relative), { withFileTypes: true })) {
      const child = path.posix.join(relative, entry.name);
      if (entry.isSymbolicLink()) links.push(child);
      else if (entry.isDirectory()) walk(child);
      else files.push(child);
    }
  };
  if (fs.existsSync(path.join(root, folder))) walk(folder);
  return { files, links };
}

/**
 * Whether git tracks every file under publicDir in the checkout at root, and no symbolic link is there:
 * { ok, untracked, links, message }. untracked counts files git does not track; links counts every link.
 */
export function publicTrackedCheck(root, publicDir = PUBLIC_DIR) {
  let tracked;
  try {
    const listed = execFileSync('git', ['-C', root, 'ls-files', '-z', '--', publicDir], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    });
    tracked = new Set(listed.split('\0').filter(Boolean));
  } catch {
    return {
      ok: false,
      untracked: null,
      links: null,
      message: `[verify:public] FAIL: git could not list the files it tracks here, so the build cannot tell which files under ${publicDir} it would ship.`,
    };
  }
  const { files, links } = entriesUnder(root, publicDir);
  const untracked = files.filter((file) => !tracked.has(file)).length;
  if (untracked === 0 && links.length === 0) {
    return {
      ok: true,
      untracked: 0,
      links: 0,
      message: `[verify:public] OK: git tracks all ${files.length} files under ${publicDir}, and none is a symbolic link.`,
    };
  }
  const failures = [];
  if (untracked > 0) {
    const one = untracked === 1;
    failures.push(
      `${untracked} ${one ? 'file' : 'files'} under ${publicDir} ${one ? 'is' : 'are'} not tracked by git, ` +
        `and Vite would copy ${one ? 'it' : 'them'} into the build. List them on this machine with: git status --short --ignored ${publicDir}`
    );
  }
  if (links.length > 0) {
    const one = links.length === 1;
    failures.push(
      `${links.length} ${one ? 'entry' : 'entries'} under ${publicDir} ${one ? 'is a symbolic link' : 'are symbolic links'}, ` +
        `which Vite follows when it copies the folder into the build, so the check refuses ${one ? 'it' : 'them'} ` +
        `whether git tracks ${one ? 'it' : 'them'} or not. List them on this machine with: find ${publicDir} -type l`
    );
  }
  return {
    ok: false,
    untracked,
    links: links.length,
    message: failures.map((failure) => `[verify:public] FAIL: ${failure}`).join('\n'),
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  // The repository root is this script's parent folder, wherever the build runs it from
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  const { ok, message } = publicTrackedCheck(root);
  if (ok) console.log(message);
  else {
    console.error(message);
    process.exit(1);
  }
}
