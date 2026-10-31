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
