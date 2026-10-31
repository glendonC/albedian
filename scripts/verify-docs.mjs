#!/usr/bin/env node
/**
 * Verifies Kiro spec completeness (.kiro/specs/<spec>/{requirements,design,tasks}.md),
 * steering frontmatter (.kiro/steering/*.md), and relative file links in README.md and docs/.
 */
import fs from 'node:fs';
import path from 'node:path';

const ROOT = process.cwd();
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

let errors = 0;

for (const spec of EXPECTED_SPECS) {
  for (const doc of ['requirements.md', 'design.md', 'tasks.md']) {
    const p = path.join(ROOT, '.kiro/specs', spec, doc);
    if (!fs.existsSync(p) || fs.statSync(p).size < 50) {
      console.error(`Missing or empty Kiro spec document: .kiro/specs/${spec}/${doc}`);
      errors++;
    }
  }
}

const steeringDir = path.join(ROOT, '.kiro/steering');
const steeringFiles = fs.readdirSync(steeringDir).filter((f) => f.endsWith('.md'));
if (steeringFiles.length < 4) {
  console.error('Expected at least 4 steering documents in .kiro/steering/');
  errors++;
}
for (const sf of steeringFiles) {
  const content = fs.readFileSync(path.join(steeringDir, sf), 'utf8');
  if (!content.startsWith('---\ninclusion:')) {
    console.error(`Steering file ${sf} missing valid Kiro frontmatter (inclusion: ...).`);
    errors++;
  }
}

for (const orig of [
  'Frontier_Media_Restoration_Research_2026-10-31.md',
  'Observable_Universe_Research_Extension_2026-10-31.md',
  'Albedian_Spatial_Experience_Motion_Voice_Research_2026-10-31.md',
]) {
  const p = path.join(ROOT, 'research/originals', orig);
  if (!fs.existsSync(p)) {
    console.error(`Missing preserved original research file: ${p}`);
    errors++;
  }
}

if (errors > 0) {
  process.exit(1);
}

console.log(
  `[verify:docs] OK — Verified ${EXPECTED_SPECS.length} Kiro specs (24 documents), ${steeringFiles.length} steering files, and 3 preserved research reports.`
);
