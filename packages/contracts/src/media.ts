import { z } from 'zod';
import { deterministicHexDigest } from './primitives.js';

/**
 * Media Domain Entities: SourceAsset, ProcessingGraph, ProcessingAttempt,
 * MediaVariant, ExportArtifact, and Deterministic DAG Validation.
 */

export const AudioStreamDescriptorSchema = z.object({
  index: z.number().int().nonnegative(),
  codec: z.string().min(1),
  sampleRateHz: z.number().int().positive(),
  channels: z.number().int().positive(),
  startPtsMs: z.number(),
  durationMs: z.number().nonnegative(),
  sha256: z.string().min(8),
});
export type AudioStreamDescriptor = z.infer<typeof AudioStreamDescriptorSchema>;

export const SourceAssetSchema = z.object({
  id: z.string().min(1),
  title: z.string().min(1),
  mediaKind: z.enum(['image', 'video']),
  sha256: z.string().min(16),
  uri: z.string().min(1),
  width: z.number().int().positive(),
  height: z.number().int().positive(),
  durationMs: z.number().nonnegative(),
  fpsNumerator: z.number().int().positive(),
  fpsDenominator: z.number().int().positive(),
  videoCodec: z.string().min(1),
  audioStreams: z.array(AudioStreamDescriptorSchema),
  capturedAtUTC: z.string().datetime().optional(),
  observerLocation: z
    .object({
      latDeg: z.number().min(-90).max(90),
      lonDeg: z.number().min(-180).max(180),
      elevationM: z.number().optional(),
      placeName: z.string().optional(),
      uncertaintyMeters: z.number().nonnegative().optional(),
    })
    .optional(),
  cameraMetadata: z
    .object({
      make: z.string().optional(),
      model: z.string().optional(),
      focalLengthMm: z.number().positive().optional(),
      estimatedFovDeg: z.number().positive().max(175).optional(),
      exposureTimeSec: z.number().positive().optional(),
      iso: z.number().positive().optional(),
    })
    .optional(),
  privacyClass: z.enum(['private', 'consented_demo', 'public_sample']),
  immutable: z.literal(true),
});
export type SourceAsset = z.infer<typeof SourceAssetSchema>;

export const ProcessingOperationKindSchema = z.enum([
  'source_decode',
  'tone_transfer',
  'spatial_denoise',
  'temporal_stack',
  'region_mask',
  'star_protect_merge',
  'deblock',
  'encode_mux',
]);
export type ProcessingOperationKind = z.infer<typeof ProcessingOperationKindSchema>;

export const ProcessingNodeSchema = z.object({
  nodeId: z.string().min(1),
  kind: ProcessingOperationKindSchema,
  operationVersion: z.string().min(1),
  inputNodeIds: z.array(z.string()),
  parameters: z.record(z.string(), z.union([z.string(), z.number(), z.boolean()])),
  maskId: z.string().optional(),
  modelSha256: z.string().optional(),
  preservePointSources: z.boolean().default(true),
});
export type ProcessingNode = z.infer<typeof ProcessingNodeSchema>;

export const FidelityPolicySchema = z.object({
  noSyntheticSceneContent: z.boolean(),
  allowSyntheticSky: z.literal(false),
  preserveOriginalAudio: z.boolean(),
  preserveFacesAndPeople: z.boolean(),
  protectObservedStarPoints: z.boolean(),
});
export type FidelityPolicy = z.infer<typeof FidelityPolicySchema>;

export const ProcessingGraphSchema = z.object({
  id: z.string().min(1),
  sourceAssetId: z.string().min(1),
  sourceSha256: z.string().min(16),
  parentGraphId: z.string().optional(),
  revisionSha256: z.string().min(8),
  profileName: z.enum(['original', 'natural', 'temporal', 'star_protected', 'custom']),
  fidelityPolicy: FidelityPolicySchema,
  nodes: z.array(ProcessingNodeSchema).min(1),
  createdAtUTC: z.string().datetime(),
});
export type ProcessingGraph = z.infer<typeof ProcessingGraphSchema>;

export const AttemptEvaluationMetricsSchema = z.object({
  evaluationMethod: z.string().min(1),
  hasPairedReference: z.boolean(),
  shadowSnrGainDb: z.number(),
  estimatedNoiseReductionPct: z.number().min(0).max(100),
  pointSourceRetentionRatio: z.number().min(0).max(1.5),
  falsePointArtifactCount: z.number().int().nonnegative(),
  faceRegionStabilityScore: z.number().min(0).max(1).optional(),
  temporalFlickerIndex: z.number().nonnegative(),
  audioSyncOffsetMs: z.number().nonnegative(),
  validityNotes: z.array(z.string()),
});
export type AttemptEvaluationMetrics = z.infer<typeof AttemptEvaluationMetricsSchema>;

export const ProcessingAttemptSchema = z.object({
  id: z.string().min(1),
  sourceAssetId: z.string().min(1),
  graphId: z.string().min(1),
  parentAttemptId: z.string().optional(),
  label: z.string().min(1),
  status: z.enum(['queued', 'running', 'evaluating', 'ready', 'failed', 'canceled', 'superseded']),
  cacheKey: z.string().min(16),
  startedAtUTC: z.string().datetime(),
  completedAtUTC: z.string().datetime().optional(),
  elapsedMs: z.number().nonnegative(),
  cpuMs: z.number().nonnegative(),
  gpuMs: z.number().nonnegative(),
  peakMemoryBytes: z.number().int().nonnegative(),
  outputVariantId: z.string().optional(),
  metrics: AttemptEvaluationMetricsSchema.optional(),
  failureReason: z.string().optional(),
});
export type ProcessingAttempt = z.infer<typeof ProcessingAttemptSchema>;

export const MediaVariantSchema = z.object({
  id: z.string().min(1),
  sourceAssetId: z.string().min(1),
  attemptId: z.string().min(1),
  graphId: z.string().min(1),
  label: z.string().min(1),
  uri: z.string().min(1),
  sha256: z.string().min(16),
  width: z.number().int().positive(),
  height: z.number().int().positive(),
  durationMs: z.number().nonnegative(),
  frameTimestampsMs: z.array(z.number().nonnegative()),
  audioStreamCopiedFromSource: z.boolean(),
  audioSyncOffsetMs: z.number().nonnegative(),
  summaryNote: z.string().min(1),
});
export type MediaVariant = z.infer<typeof MediaVariantSchema>;

export const ExportArtifactSchema = z.object({
  id: z.string().min(1),
  sessionId: z.string().min(1),
  sourceAssetId: z.string().min(1),
  sourceSha256: z.string().min(16),
  exportedVariantId: z.string().min(1),
  outputUri: z.string().min(1),
  outputSha256: z.string().min(16),
  containerFormat: z.enum(['mp4', 'png', 'json_manifest']),
  videoCodec: z.string().min(1),
  audioPreserved: z.boolean(),
  audioCodec: z.string().optional(),
  audioSyncOffsetMs: z.number().nonnegative(),
  processingGraphId: z.string().min(1),
  observationSolutionId: z.string().optional(),
  catalogCitations: z.array(z.string()),
  epistemicSummary: z.object({
    observedCount: z.number().int().nonnegative(),
    catalogMatchedCount: z.number().int().nonnegative(),
    predictedCount: z.number().int().nonnegative(),
    simulatedViewsIncluded: z.boolean(),
    strictFidelityEnforced: z.boolean(),
  }),
  exportedAtUTC: z.string().datetime(),
});
export type ExportArtifact = z.infer<typeof ExportArtifactSchema>;

export interface DAGValidationResult {
  valid: boolean;
  topologicalOrder: string[];
  errors: string[];
}

export function validateProcessingGraphDAG(graph: ProcessingGraph): DAGValidationResult {
  const errors: string[] = [];
  const nodeMap = new Map<string, ProcessingNode>();

  for (const node of graph.nodes) {
    if (nodeMap.has(node.nodeId)) {
      errors.push(`Duplicate nodeId: ${node.nodeId}`);
    }
    nodeMap.set(node.nodeId, node);
  }

  for (const node of graph.nodes) {
    if (graph.fidelityPolicy.protectObservedStarPoints && !node.preservePointSources) {
      errors.push(
        `Node ${node.nodeId} disables preservePointSources while protectObservedStarPoints is true.`
      );
    }
    for (const inputId of node.inputNodeIds) {
      if (!nodeMap.has(inputId)) {
        errors.push(`Node ${node.nodeId} references missing inputNodeId: ${inputId}`);
      }
    }
  }

  const rootDecodeNodes = graph.nodes.filter(
    (n) => n.kind === 'source_decode' && n.inputNodeIds.length === 0
  );
  if (rootDecodeNodes.length === 0) {
    errors.push('ProcessingGraph must contain at least one root source_decode node.');
  }

  if (errors.length > 0) {
    return { valid: false, topologicalOrder: [], errors };
  }

  const inDegree = new Map<string, number>();
  const adjacency = new Map<string, string[]>();
  for (const node of graph.nodes) {
    inDegree.set(node.nodeId, node.inputNodeIds.length);
    adjacency.set(node.nodeId, []);
  }
  for (const node of graph.nodes) {
    for (const parentId of node.inputNodeIds) {
      adjacency.get(parentId)?.push(node.nodeId);
    }
  }

  const queue: string[] = [];
  for (const [nodeId, deg] of inDegree.entries()) {
    if (deg === 0) {
      queue.push(nodeId);
    }
  }
  queue.sort();

  const topologicalOrder: string[] = [];
  while (queue.length > 0) {
    const current = queue.shift()!;
    topologicalOrder.push(current);
    const children = adjacency.get(current) ?? [];
    for (const child of children) {
      const nextDeg = (inDegree.get(child) ?? 1) - 1;
      inDegree.set(child, nextDeg);
      if (nextDeg === 0) {
        queue.push(child);
        queue.sort();
      }
    }
  }

  if (topologicalOrder.length !== graph.nodes.length) {
    errors.push('Cycle detected in ProcessingGraph DAG.');
    return { valid: false, topologicalOrder: [], errors };
  }

  return { valid: true, topologicalOrder, errors: [] };
}

export function computeNodeCacheKeys(graph: ProcessingGraph): Record<string, string> {
  const validation = validateProcessingGraphDAG(graph);
  if (!validation.valid) {
    throw new Error(`Cannot compute cache keys for invalid DAG: ${validation.errors.join('; ')}`);
  }

  const nodeMap = new Map(graph.nodes.map((n) => [n.nodeId, n]));
  const cacheKeys: Record<string, string> = {};

  for (const nodeId of validation.topologicalOrder) {
    const node = nodeMap.get(nodeId)!;
    const sortedParams = Object.keys(node.parameters)
      .sort()
      .map((k) => `${k}:${JSON.stringify(node.parameters[k])}`)
      .join('|');
    const upstreamHashes = node.inputNodeIds
      .slice()
      .sort()
      .map((id) => cacheKeys[id])
      .join(',');
    const canonicalPayload = [
      `src=${graph.sourceSha256}`,
      `kind=${node.kind}`,
      `ver=${node.operationVersion}`,
      `params=${sortedParams}`,
      `mask=${node.maskId ?? 'none'}`,
      `model=${node.modelSha256 ?? 'none'}`,
      `starProtect=${node.preservePointSources}`,
      `up=${upstreamHashes}`,
    ].join('::');

    cacheKeys[nodeId] = deterministicHexDigest(canonicalPayload);
  }

  return cacheKeys;
}
