/**
 * `pnpm field --check`: what a field run needs on this laptop and what is missing, without starting anything.
 *
 * Each check reads the machine through `probes`, so the tests run with fakes. The report holds counts and versions
 * only: never a file name, never a path under data/, never a key's value. A required item that is missing makes the
 * exit code 1; advice (HEIC support, the music, the disk, a busy port, another pnpm) leaves it 0.
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';

export const DISK_ADVICE_GB = 10;
export const HEIC_ADVICE =
  "iPhone HEIC photos will be refused: install ffmpeg 7.1 or later, or set the iPhone's Camera Formats to Most Compatible (JPEG)";
const MUSIC_MANIFEST = 'apps/web/spike/music/sources.json';
const MUSIC_SOURCES = '.cache/music/sources';
const CATALOG_DIR = 'apps/web/public/catalog';
const VOICE_OFFLINE = "typed commands work offline; spoken input depends on the browser's own speech recognition";

const LABELS = {
  node: 'Node',
  pnpm: 'pnpm',
  uv: 'uv',
  dependencies: 'Dependencies',
  ffmpeg: 'ffmpeg',
  ffprobe: 'ffprobe',
  heic: 'HEIC photos',
  music: 'Music',
  catalogs: 'Star tiles and catalogs',
  voice: 'Voice',
  disk: 'Disk',
  ports: 'Ports',
};

const firstLine = (text) => String(text ?? '').split('\n')[0].trim();

/** The real machine, read only: commands run with a timeout, files are stat'ed, nothing is written or started. */
export function realProbes(repoRoot) {
  const abs = (rel) => path.join(repoRoot, rel);
  const lstat = (rel) => {
    try {
      return fs.lstatSync(abs(rel));
    } catch {
      return null;
    }
  };
  return {
    nodeVersion: process.version,
    env: process.env,
    run(command, args) {
      const result = spawnSync(command, args, { cwd: repoRoot, encoding: 'utf8', timeout: 15_000, maxBuffer: 64 * 1024 * 1024 });
      return { ok: !result.error && result.status === 0, stdout: result.stdout ?? '' };
    },
    readJson(rel) {
      try {
        return JSON.parse(fs.readFileSync(abs(rel), 'utf8'));
      } catch {
        return null;
      }
    },
    fileSize(rel) {
      const stat = lstat(rel);
      return stat?.isFile() ? stat.size : null;
    },
    // As apps/web/spike/music/serveSources.mjs serves it: a regular file inside the folder, with no link on the way
    servedSize(folderRel, rel) {
      try {
        const root = abs(folderRel);
        const file = path.resolve(root, rel);
        if (!file.startsWith(root + path.sep)) return null;
        if (path.relative(fs.realpathSync(root), fs.realpathSync(file)) !== path.relative(root, file)) return null;
        const stat = fs.statSync(file);
        return stat.isFile() ? stat.size : null;
      } catch {
        return null;
      }
    },
    isLink: (rel) => Boolean(lstat(rel)?.isSymbolicLink()),
    isDir: (rel) => Boolean(lstat(rel)?.isDirectory()),
    workspaceNodeModules() {
      const folders = ['node_modules'];
      for (const group of ['apps', 'packages']) {
        let names = [];
        try {
          names = fs.readdirSync(abs(group)).sort();
        } catch {
          names = [];
        }
        for (const name of names) if (fs.existsSync(abs(`${group}/${name}/package.json`))) folders.push(`${group}/${name}/node_modules`);
      }
      return folders;
    },
    freeBytes() {
      const stats = fs.statfsSync(repoRoot);
      return stats.bavail * stats.bsize;
    },
    portFree(port, host) {
      return new Promise((resolve) => {
        const server = net.createServer();
        server.once('error', () => resolve(false));
        server.listen({ port, host, exclusive: true }, () => server.close(() => resolve(true)));
      });
    },
  };
}

function nodeLine(probes) {
  const wanted = probes.readJson('package.json')?.engines?.node ?? '>=22.0.0';
  const min = Number(/(\d+)/.exec(wanted)?.[1] ?? 22);
  const major = Number(/^v?(\d+)/.exec(probes.nodeVersion)?.[1] ?? 0);
  const detail = `Node ${probes.nodeVersion} (package.json asks for ${wanted})`;
  return { name: 'node', status: major >= min ? 'ok' : 'missing', detail };
}

function pnpmLine(probes) {
  const pinned = String(probes.readJson('package.json')?.packageManager ?? '').split('@')[1] ?? '';
  const result = probes.run('pnpm', ['--version']);
  if (!result.ok) return { name: 'pnpm', status: 'missing', detail: `pnpm not found; the repository pins ${pinned}` };
  const version = firstLine(result.stdout);
  if (pinned && version !== pinned) {
    return { name: 'pnpm', status: 'advice', detail: `pnpm ${version}; the repository pins ${pinned}` };
  }
  return { name: 'pnpm', status: 'ok', detail: `pnpm ${version}` };
}

function uvLine(probes) {
  const result = probes.run('uv', ['--version']);
  return result.ok
    ? { name: 'uv', status: 'ok', detail: firstLine(result.stdout) }
    : { name: 'uv', status: 'missing', detail: 'uv not found; the API runs through it' };
}

function dependenciesLine(probes) {
  const folders = probes.workspaceNodeModules();
  const linked = folders.filter((rel) => probes.isLink(rel)).length;
  const absent = folders.filter((rel) => !probes.isLink(rel) && !probes.isDir(rel)).length;
  const venv = probes.isDir('.venv') || probes.isLink('.venv');
  const problems = [];
  if (linked) problems.push(`${linked} node_modules folders are links into another checkout`);
  if (absent) problems.push(`${absent} node_modules folders are missing`);
  if (linked || absent) problems.push('run pnpm install --frozen-lockfile --offline');
  if (!venv) problems.push('.venv is missing; run uv sync');
  if (problems.length) return { name: 'dependencies', status: 'missing', detail: problems.join('; ') };
  return { name: 'dependencies', status: 'ok', detail: `node_modules installed in ${folders.length} folders; .venv present` };
}

function toolLine(probes, tool) {
  const result = probes.run(tool, ['-hide_banner', '-version']);
  return result.ok
    ? { name: tool, status: 'ok', detail: firstLine(result.stdout) }
    : { name: tool, status: 'missing', detail: `${tool} not found; uploads are decoded and probed with it` };
}

function heicLine(probes) {
  const result = probes.run('ffprobe', ['-hide_banner', '-h']);
  if (result.ok && result.stdout.includes('show_stream_groups')) {
    return { name: 'heic', status: 'ok', detail: 'ffprobe reads HEIC tile grids (-show_stream_groups)' };
  }
  return { name: 'heic', status: 'advice', detail: HEIC_ADVICE };
}

function musicLine(probes) {
  const files = probes.readJson(MUSIC_MANIFEST)?.files;
  if (!Array.isArray(files)) return { name: 'music', status: 'advice', detail: 'the music manifest could not be read' };
  const present = files.filter((file) => probes.servedSize(MUSIC_SOURCES, file.path) === file.bytes).length;
  const counted = `${present} of ${files.length} sample files present at their listed size`;
  if (present === files.length) return { name: 'music', status: 'ok', detail: counted };
  return {
    name: 'music',
    status: 'advice',
    detail: `${counted}; without them the music stays silent (docs/FIELD_USE.md says how to fetch them)`,
  };
}

function catalogsLine(probes) {
  const listed = probes.run('git', ['ls-files', '-z', CATALOG_DIR]);
  if (!listed.ok) return { name: 'catalogs', status: 'advice', detail: 'git could not list the catalog files, so they were not counted' };
  const files = listed.stdout.split('\0').filter(Boolean);
  const present = files.filter((rel) => probes.fileSize(rel) !== null).length;
  const counted = `${present} of ${files.length} tracked catalog files present (star tiles; constellation, deep-sky, eclipse and exoplanet catalogs)`;
  if (files.length > 0 && present === files.length) return { name: 'catalogs', status: 'ok', detail: counted };
  return { name: 'catalogs', status: 'missing', detail: `${counted}; restore them with git checkout -- ${CATALOG_DIR}` };
}

function voiceLine(probes) {
  const enabled = probes.env.ALBEDIAN_ENABLE_LIVE_CLAUDE === '1';
  const keySet = String(probes.env.ANTHROPIC_API_KEY ?? '').trim() !== '';
  const key = keySet ? 'key set' : 'key not set';
  if (enabled && keySet) {
    return { name: 'voice', status: 'ok', detail: `live Claude on (paid calls), ${key}; with no internet the built-in rules answer; ${VOICE_OFFLINE}` };
  }
  if (enabled) {
    return { name: 'voice', status: 'advice', detail: `live Claude on but ${key}, so the built-in rules answer; ${VOICE_OFFLINE}` };
  }
  return { name: 'voice', status: 'ok', detail: `live Claude off, ${key}; the built-in rules answer; ${VOICE_OFFLINE}` };
}

function diskLine(probes) {
  const gb = probes.freeBytes() / 1e9;
  const free = `${Math.floor(gb)} GB free on this disk`;
  if (gb >= DISK_ADVICE_GB) return { name: 'disk', status: 'ok', detail: free };
  return { name: 'disk', status: 'advice', detail: `${free}; under ${DISK_ADVICE_GB} GB, and one clip upload can be up to 2 GiB` };
}

async function portsLine(probes, ports, lan) {
  const wanted = [
    [ports.api, '127.0.0.1', 'the API', '--api-port'],
    [ports.web, lan ? '0.0.0.0' : 'localhost', 'the web app', '--web-port'],
    [ports.music, '127.0.0.1', 'the music sources server', '--music-port'],
  ];
  const busy = [];
  for (const [port, host, what, flag] of wanted) {
    if (!(await probes.portFree(port, host))) busy.push(`port ${port} is in use, so ${what} could not start (free it or pass ${flag})`);
  }
  if (busy.length) return { name: 'ports', status: 'advice', detail: busy.join('; ') };
  return { name: 'ports', status: 'ok', detail: `ports ${ports.api}, ${ports.web} and ${ports.music} free` };
}

/** Every check in order, and the exit code: 1 when a required item is missing, else 0. */
export async function fieldCheck({ probes, ports, lan = false }) {
  const lines = [
    nodeLine(probes),
    pnpmLine(probes),
    uvLine(probes),
    dependenciesLine(probes),
    toolLine(probes, 'ffmpeg'),
    toolLine(probes, 'ffprobe'),
    heicLine(probes),
    musicLine(probes),
    catalogsLine(probes),
    voiceLine(probes),
    diskLine(probes),
    await portsLine(probes, ports, lan),
  ];
  return { lines, exitCode: lines.some((line) => line.status === 'missing') ? 1 : 0 };
}

/** One header line, then one line per check, its status first. */
export function formatReport({ lines, exitCode }) {
  const missing = lines.filter((line) => line.status === 'missing').length;
  const header = exitCode
    ? `pnpm field --check: ${missing} required item${missing === 1 ? ' is' : 's are'} missing`
    : 'pnpm field --check: everything a field run requires is here';
  const status = { ok: 'ok', missing: 'MISSING', advice: 'advice' };
  return [header, ...lines.map((line) => `${status[line.status].padEnd(8)}${LABELS[line.name]}: ${line.detail}`)].join('\n');
}
