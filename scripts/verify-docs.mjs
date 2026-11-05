#!/usr/bin/env node
/**
 * The docs gate (`pnpm verify:docs`). It checks that:
 * - each of the 8 Kiro specs has requirements.md, design.md and tasks.md under .kiro/specs/<spec>/, none under 50 bytes;
 * - .kiro/steering/ holds at least 4 steering files, each starting with `inclusion:` frontmatter;
 * - the 4 preserved research reports exist in research/originals/;
 * - every relative Markdown link in README.md and in each .md file under docs/ names a file or folder that exists.
 * Links with a scheme (https:, mailto:) are not followed, and neither are fragments (#section) or reference-style
 * links.
 */
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const EXPECTED_SPECS = [
  'observation-session',
  'media-experiment-runtime',
  'observation-solution',
  'spatial-camera-navigation',
  'voice-native-actions',
  'design-system-accessibility',
  'export-and-provenance',
  'integration-and-testing',
];
const PRESERVED_REPORTS = [
  'Frontier_Media_Restoration_Research_2026-10-31.md',
  'Observable_Universe_Research_Extension_2026-10-31.md',
  'Albedian_Spatial_Experience_Motion_Voice_Research_2026-10-31.md',
  'Spatial_Astronomy_Evidence_Competitive_Intelligence_2026-11-02.md',
];
// [text](target) and ![alt](target), with an optional "title" after the target
const LINK = /\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g;
const SCHEME = /^[a-z][a-z0-9+.-]*:/i;

/** README.md and every Markdown file under docs/, as paths relative to root. */
export function markdownFiles(root) {
  const files = fs.existsSync(path.join(root, 'README.md')) ? ['README.md'] : [];
  const walk = (folder) => {
    for (const entry of fs.readdirSync(path.join(root, folder), { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const relative = path.posix.join(folder, entry.name);
      if (entry.isDirectory()) walk(relative);
      else if (entry.name.endsWith('.md')) files.push(relative);
    }
  };
  if (fs.existsSync(path.join(root, 'docs'))) walk('docs');
  return files;
}

/** The relative links in these files, each as { file, line, target }. */
export function relativeLinks(root, files = markdownFiles(root)) {
  const links = [];
  for (const file of files) {
    fs.readFileSync(path.join(root, file), 'utf8')
      .split('\n')
      .forEach((text, index) => {
        for (const match of text.matchAll(LINK)) {
          const target = match[1];
          if (!SCHEME.test(target) && !target.startsWith('#')) links.push({ file, line: index + 1, target });
        }
      });
  }
  return links;
}

/** `file:line: target` for each relative link whose target does not exist. */
export function deadLinks(root, links = relativeLinks(root)) {
  return links
    .filter(({ file, target }) => {
      let relative;
      try {
        relative = decodeURI(target.split('#')[0]);
      } catch {
        return true;
      }
      const resolved = relative.startsWith('/') ? path.join(root, relative) : path.join(root, path.dirname(file), relative);
      return !fs.existsSync(resolved);
    })
    .map(({ file, line, target }) => `${file}:${line}: ${target}`);
}

/** Every problem the docs gate finds under root, and the summary it prints when there is none. */
export function docsGate(root) {
  const errors = [];
  for (const spec of EXPECTED_SPECS) {
    for (const doc of ['requirements.md', 'design.md', 'tasks.md']) {
      const p = path.join(root, '.kiro/specs', spec, doc);
      if (!fs.existsSync(p) || fs.statSync(p).size < 50) errors.push(`Missing or empty Kiro spec document: .kiro/specs/${spec}/${doc}`);
    }
  }

  const steeringDir = path.join(root, '.kiro/steering');
  const steeringFiles = fs.existsSync(steeringDir) ? fs.readdirSync(steeringDir).filter((f) => f.endsWith('.md')) : [];
  if (steeringFiles.length < 4) errors.push('Expected at least 4 steering documents in .kiro/steering/');
  for (const sf of steeringFiles) {
    const content = fs.readFileSync(path.join(steeringDir, sf), 'utf8');
    if (!content.startsWith('---\ninclusion:')) errors.push(`Steering file ${sf} missing valid Kiro frontmatter (inclusion: ...).`);
  }

  for (const orig of PRESERVED_REPORTS) {
    if (!fs.existsSync(path.join(root, 'research/originals', orig))) {
      errors.push(`Missing preserved original research file: research/originals/${orig}`);
    }
  }

  const files = markdownFiles(root);
  const links = relativeLinks(root, files);
  for (const dead of deadLinks(root, links)) errors.push(`Dead relative link: ${dead}`);

  const summary =
    `[verify:docs] OK: verified ${EXPECTED_SPECS.length} Kiro specs (24 documents), ${steeringFiles.length} steering files, ` +
    `${PRESERVED_REPORTS.length} preserved research reports and ${links.length} relative links in ${files.length} Markdown files.`;
  return { errors, summary };
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const { errors, summary } = docsGate(process.cwd());
  for (const error of errors) console.error(error);
  if (errors.length > 0) process.exit(1);
  console.log(summary);
}
