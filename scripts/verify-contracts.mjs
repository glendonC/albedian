#!/usr/bin/env node
/**
 * Verifies that the core Albedian domain schemas are exported in @albedian/contracts
 * and validates all Kiro 1.4.62 workspace hooks (.kiro/hooks/*.kiro.hook).
 */
import fs from 'node:fs';
import path from 'node:path';

const ROOT = process.cwd();
const REQUIRED_ENTITY_SCHEMAS = [
  'ObservationSessionSchema',
  'SourceAssetSchema',
  'MediaVariantSchema',
  'ProcessingGraphSchema',
  'ProcessingAttemptSchema',
  'ObservationSolutionSchema',
  'CelestialFindingSchema',
  'CatalogReferenceSchema',
  'ScientificEvidenceSchema',
  'SpatialViewStateSchema',
  'CameraCheckpointSchema',
  'VoiceActionSchema',
  'NavigationHistorySchema',
  'ExportArtifactSchema',
  'RenderLayerSchema',
  'AssetProvenanceRecordSchema',
  'MetadataSourceSchema',
  'DisplayOrientationSchema',
  'CaptureTimeHintSchema',
  'OrientationHintSchema',
  'MetadataHintsSchema',
  'IngestReceiptSchema',
  'JobStageProgressSchema',
  'SolveJobSchema',
  'VoiceCommandRequestSchema',
  'VoiceCommandResponseSchema',
  'SolarEclipseCatalogSchema',
  'LunarEclipseCatalogSchema',
  'ExoplanetTableSchema',
  'DeepSkyCatalogSchema',
  'ConstellationLinesSchema',
  'ConstellationBoundariesSchema',
  'BrightStarSupplementSchema',
];

const VALID_WHEN_TYPES = new Set([
  'fileEdited',
  'fileCreated',
  'fileDeleted',
  'userTriggered',
  'promptSubmit',
  'agentStop',
  'preToolUse',
  'postToolUse',
  'preTaskExecution',
  'postTaskExecution',
  'sessionStart',
]);

const VALID_THEN_TYPES = new Set(['askAgent', 'runCommand']);

const contractsSrcDir = path.join(ROOT, 'packages/contracts/src');
const contractsSource = fs
  .readdirSync(contractsSrcDir)
  .filter((f) => f.endsWith('.ts'))
  .map((f) => fs.readFileSync(path.join(contractsSrcDir, f), 'utf8'))
  .join('\n');

const missingSchemas = REQUIRED_ENTITY_SCHEMAS.filter(
  (schemaName) => !contractsSource.includes(`export const ${schemaName}`)
);

if (missingSchemas.length > 0) {
  console.error('Missing required domain entity schemas:', missingSchemas);
  process.exit(1);
}

const hooksDir = path.join(ROOT, '.kiro/hooks');
const hookFiles = fs
  .readdirSync(hooksDir)
  .filter((f) => f.endsWith('.kiro.hook'));

if (hookFiles.length === 0) {
  console.error('No .kiro.hook files found in .kiro/hooks/');
  process.exit(1);
}

for (const file of hookFiles) {
  const fullPath = path.join(hooksDir, file);
  const parsed = JSON.parse(fs.readFileSync(fullPath, 'utf8'));
  if (!parsed.name || typeof parsed.name !== 'string') {
    console.error(`Invalid hook name in ${file}`);
    process.exit(1);
  }
  if (!parsed.version || typeof parsed.version !== 'string') {
    console.error(`Invalid hook version in ${file}`);
    process.exit(1);
  }
  if (!parsed.when || !VALID_WHEN_TYPES.has(parsed.when.type)) {
    console.error(`Invalid hook when.type in ${file}: ${parsed.when?.type}`);
    process.exit(1);
  }
  if (!parsed.then || !VALID_THEN_TYPES.has(parsed.then.type)) {
    console.error(`Invalid hook then.type in ${file}: ${parsed.then?.type}`);
    process.exit(1);
  }
}

console.log(
  `[verify:contracts] OK: verified ${REQUIRED_ENTITY_SCHEMAS.length} domain entity schemas and ${hookFiles.length} Kiro 1.4.62 hooks.`
);
