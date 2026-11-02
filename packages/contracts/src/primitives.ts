import { z } from 'zod';

/**
 * Epistemic Evidence Classes, Solution Statuses, 4+1 Orthogonal State Machines,
 * and Deterministic Hashing Primitives.
 */

export const EvidenceClassSchema = z.enum([
  'observed',
  'catalog_matched',
  'predicted',
  'simulated',
]);
export type EvidenceClass = z.infer<typeof EvidenceClassSchema>;

/**
 * Granular 8-tier EvidenceKind taxonomy from the Albedian Master Research & Design Brief
 * (Track 7 & Track 14). Maps cleanly to the 4 top-level EvidenceClass categories while
 * preserving provenance across captured frames, alternate surveys, reconstructions, and illustrations.
 */
export const EvidenceKindSchema = z.enum([
  'captured',
  'registered',
  'catalog-derived',
  'observed-elsewhere',
  'propagated',
  'reconstructed',
  'simulated',
  'illustrative',
]);
export type EvidenceKind = z.infer<typeof EvidenceKindSchema>;

export const RepresentationModeSchema = z.enum([
  'capture',
  'sky',
  'specimen',
  'physical-model',
  'similarity-atlas',
]);
export type RepresentationMode = z.infer<typeof RepresentationModeSchema>;

export const CoordinateSpaceKindSchema = z.enum([
  'astronomical',
  'similarity',
  'explanatory',
]);
export type CoordinateSpaceKind = z.infer<typeof CoordinateSpaceKindSchema>;

export const OperationalInstrumentStatusSchema = z.enum([
  'REGISTERING',
  'MATCHED',
  'PROPAGATED',
  'RECONSTRUCTED',
  'NO COVERAGE',
  'SOURCE UNAVAILABLE',
]);
export type OperationalInstrumentStatus = z.infer<typeof OperationalInstrumentStatusSchema>;

export const EvidenceRecordSchema = z.object({
  id: z.string(),
  kind: EvidenceKindSchema,
  title: z.string(),
  sourceUrl: z.string().optional(),
  sourceIdentifier: z.string().optional(),
  license: z.string().optional(),
  observationTimeUtc: z.string().optional(),
  modelEpochUtc: z.string().optional(),
  coordinateFrame: z.string().optional(),
  coordinateSpace: CoordinateSpaceKindSchema.optional(),
  units: z.record(z.string(), z.string()).optional(),
  uncertaintyDescription: z.string().optional(),
  processingDescription: z.string().optional(),
  coverageDescription: z.string().optional(),
});
export type EvidenceRecord = z.infer<typeof EvidenceRecordSchema>;

export const MasterObservationStateSchema = z.object({
  captureId: z.string(),
  selectedEvidenceId: z.string().optional(),
  selectedTargetId: z.string().optional(),
  representation: RepresentationModeSchema,
  activeBand: z.string().optional(),
  activeReferenceFrame: z.string().optional(),
  specimenTiltDeg: z.number().min(0).max(90).optional(),
  immersive: z.boolean(),
});
export type MasterObservationState = z.infer<typeof MasterObservationStateSchema>;

export const SolutionStatusSchema = z.enum([
  'pending',
  'calibrated',
  'approximate',
  'predicted_only',
  'unresolved',
  'ambiguous',
]);
export type SolutionStatus = z.infer<typeof SolutionStatusSchema>;

export const MediaMachineStateSchema = z.enum([
  'empty',
  'importing',
  'ready',
  'decode_error',
]);
export type MediaMachineState = z.infer<typeof MediaMachineStateSchema>;

export const RestorationMachineStateSchema = z.enum([
  'idle',
  'analyzing',
  'planning',
  'queued',
  'processing',
  'evaluating',
  'ready',
  'failed',
  'canceled',
]);
export type RestorationMachineState = z.infer<typeof RestorationMachineStateSchema>;

export const CalibrationMachineStateSchema = z.enum([
  'not_requested',
  'detecting',
  'solving',
  'verifying',
  'calibrated',
  'predicted_only',
  'ambiguous',
  'unresolved',
]);
export type CalibrationMachineState = z.infer<typeof CalibrationMachineStateSchema>;

export const NavigationMachineStateSchema = z.enum([
  'source',
  'comparison',
  'sky_registered',
  'transitioning',
  'space',
  'inspecting',
  'returning',
]);
export type NavigationMachineState = z.infer<typeof NavigationMachineStateSchema>;

export const VoiceMachineStateSchema = z.enum([
  'off',
  'permission',
  'listening',
  'hearing',
  'interpreting',
  'awaiting_tool',
  'speaking',
  'interrupted',
  'unavailable',
]);
export type VoiceMachineState = z.infer<typeof VoiceMachineStateSchema>;

export function deterministicHexDigest(input: string): string {
  let h1 = 0xdeadbeef ^ input.length;
  let h2 = 0x41c6ce57 ^ input.length;
  let h3 = 0x9e3779b9 ^ input.length;
  let h4 = 0x85ebca6b ^ input.length;

  for (let i = 0; i < input.length; i++) {
    const ch = input.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
    h3 = Math.imul(h3 ^ ch, 2246822507);
    h4 = Math.imul(h4 ^ ch, 3266489909);
  }

  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  h3 = Math.imul(h3 ^ (h3 >>> 16), 2246822507) ^ Math.imul(h4 ^ (h4 >>> 13), 3266489909);
  h4 = Math.imul(h4 ^ (h4 >>> 16), 2246822507) ^ Math.imul(h3 ^ (h3 >>> 13), 3266489909);

  const toHex = (n: number) => (n >>> 0).toString(16).padStart(8, '0');
  return `${toHex(h1)}${toHex(h2)}${toHex(h3)}${toHex(h4)}`;
}
