import { z } from 'zod';
import { SourceDetectionSchema } from './astronomy.js';
import { SHA256_HEX_PATTERN } from './wire.js';

/**
 * What `GET /api/ingest/jobs/{job_id}/measured` serves (REQ-JOB-2.8, wait-detections-one): the detections a running
 * solve measured, published when its detect stage ended, each as an observed finding's `sourceDetection` holds it
 * (observed evidence of the photo's own pixels: no sky claim). The mirror of services/contracts/measured.py.
 */
export const MEASURED_DETECTIONS_MAX = 100_000;

export const MeasuredDetectionsSchema = z
  .object({
    sourceAssetSha256: z.string().regex(new RegExp(SHA256_HEX_PATTERN)),
    detections: z.array(SourceDetectionSchema).max(MEASURED_DETECTIONS_MAX),
  })
  .strict();
export type MeasuredDetections = z.infer<typeof MeasuredDetectionsSchema>;
