import { z } from 'zod';
import { EvidenceClassSchema } from './primitives.js';

/**
 * Render Layers: what each layer of the cosmos renderer draws and what kind of evidence its
 * pixels carry, so the UI can disclose it next to the image (AGENTS.md section 1).
 *
 * Mirrored in Python by `services/contracts/render_layer.py`. The shared samples in
 * `tests/fixtures/render_layer_contract.json` are written by the Python side and parsed here.
 */

export const RenderLayerKindSchema = z.enum([
  // Photographic all-sky panorama drawn at infinity
  'sky_background',
  // Catalog point sources drawn as point-spread-function sprites
  'catalog_stars',
  // Planet or moon surface from mapped textures
  'solar_system_body',
  // Scattering shell around a body
  'atmosphere',
  // Interstellar dust or nebula density, ray marched
  'dust_volume',
  // A place reconstructed from photos or video
  'capture_splats',
  // Analytic or ray-marched set piece with no measured counterpart
  'procedural',
  // Lens and sensor effects applied to the whole frame: bloom, aberration, streaks, vignette
  'camera_optics',
]);
export type RenderLayerKind = z.infer<typeof RenderLayerKindSchema>;

/** Kinds that never show measured data, so they can only be disclosed as simulated. */
export const ALWAYS_SIMULATED_RENDER_LAYER_KINDS: readonly RenderLayerKind[] = ['procedural', 'camera_optics'];

export const RENDER_LAYER_LIMITS = {
  titleMaxLength: 80,
  disclosureMaxLength: 240,
  sourceMaxLength: 200,
  maxSources: 12,
} as const;

export const RENDER_LAYER_ID_PATTERN = /^[a-z0-9]+(?:[._-][a-z0-9]+)*$/;

export const RenderLayerSchema = z
  .object({
    id: z.string().regex(RENDER_LAYER_ID_PATTERN),
    kind: RenderLayerKindSchema,
    title: z.string().trim().min(1).max(RENDER_LAYER_LIMITS.titleMaxLength),
    evidenceClass: EvidenceClassSchema,
    // One line the UI shows beside the image: what is measured and what is composed or modeled
    disclosure: z.string().trim().min(1).max(RENDER_LAYER_LIMITS.disclosureMaxLength),
    // Datasets and imagery the layer draws from, as short citations
    sources: z
      .array(z.string().trim().min(1).max(RENDER_LAYER_LIMITS.sourceMaxLength))
      .max(RENDER_LAYER_LIMITS.maxSources),
    // The user's recording, required when the layer shows its pixels
    sourceAssetId: z.string().min(1).optional(),
    // The observation solution whose independently verified WCS a catalog match relies on
    observationSolutionId: z.string().min(1).optional(),
  })
  .strict()
  .superRefine((layer, ctx) => {
    const fromSource = layer.evidenceClass === 'observed' || layer.evidenceClass === 'catalog_matched';
    if (fromSource && layer.sourceAssetId === undefined) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['sourceAssetId'],
        message: `A ${layer.evidenceClass} layer must trace to the SourceAsset whose pixels it shows.`,
      });
    }
    if (layer.evidenceClass === 'catalog_matched' && layer.observationSolutionId === undefined) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['observationSolutionId'],
        message: 'A catalog_matched layer must name the observation solution with the verified WCS.',
      });
    }
    if (ALWAYS_SIMULATED_RENDER_LAYER_KINDS.includes(layer.kind) && layer.evidenceClass !== 'simulated') {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['evidenceClass'],
        message: `A ${layer.kind} layer shows no measured data and must be disclosed as simulated.`,
      });
    }
  });
export type RenderLayer = z.infer<typeof RenderLayerSchema>;

/** The layers one scene draws, in draw order, each id once. */
export const RenderLayerSetSchema = z.array(RenderLayerSchema).superRefine((layers, ctx) => {
  const seen = new Set<string>();
  layers.forEach((layer, index) => {
    if (seen.has(layer.id)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: [index, 'id'],
        message: `Render layer id ${layer.id} appears more than once.`,
      });
    }
    seen.add(layer.id);
  });
});
export type RenderLayerSet = z.infer<typeof RenderLayerSetSchema>;
