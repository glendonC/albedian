import { z } from 'zod';
import {
  CalibrationMachineStateSchema,
  MediaMachineStateSchema,
  NavigationMachineStateSchema,
  RestorationMachineStateSchema,
  VoiceMachineStateSchema,
} from './primitives.js';
import {
  ExportArtifactSchema,
  MediaVariantSchema,
  ProcessingAttemptSchema,
  ProcessingGraphSchema,
  SourceAssetSchema,
} from './media.js';
import { ObservationSolutionSchema } from './astronomy.js';

/**
 * Spatial Navigation, Reversible Checkpoints, Voice Direct-Manipulation Actions,
 * and the Root ObservationSession Aggregate.
 */

export const QuaternionSchema = z.tuple([z.number(), z.number(), z.number(), z.number()]);
export type QuaternionTuple = z.infer<typeof QuaternionSchema>;

export const CropTransformSchema = z.object({
  zoom: z.number().positive().max(20),
  panX: z.number().min(-1).max(1),
  panY: z.number().min(-1).max(1),
});
export type CropTransform = z.infer<typeof CropTransformSchema>;

export const SpatialViewStateSchema = z.object({
  mode: z.enum(['source_2d', 'registered_sky', 'catalog_3d', 'modeled_epoch']),
  transitionPhase: z.enum([
    'at_source',
    'phase_a_photo_to_sky',
    'at_registered_sky',
    'phase_b_sky_to_space',
    'at_space',
    'returning_to_source',
  ]),
  transitionProgress: z.number().min(0).max(1),
  activeTransitionId: z.string().optional(),
  observerOriginParsec: z.tuple([z.number(), z.number(), z.number()]),
  cameraQuaternion: QuaternionSchema,
  fovVerticalDeg: z.number().min(1).max(140),
  frustumShiftNdc: z.tuple([z.number(), z.number()]),
  lockedAnchorNdc: z.tuple([z.number(), z.number()]).optional(),
  simulationEpochISO: z.string().min(4),
  observationEpochISO: z.string().datetime(),
  sceneScalePolicy: z.enum(['physical_euclidean', 'nonlinear_compressed']),
  activeOverlays: z.object({
    observedMarkers: z.boolean(),
    catalogMatchedLabels: z.boolean(),
    predictedEphemerides: z.boolean(),
    constellationLines: z.boolean(),
    earthDirectionVector: z.boolean(),
    residualVectors: z.boolean(),
    sightlineCone: z.boolean(),
  }),
});
export type SpatialViewState = z.infer<typeof SpatialViewStateSchema>;

export const CameraCheckpointSchema = z.object({
  id: z.string().min(1),
  label: z.string().min(1),
  createdAtMs: z.number().nonnegative(),
  sourceAssetId: z.string().min(1),
  sourceTimeSec: z.number().nonnegative(),
  playbackWasPlaying: z.boolean(),
  audioGain: z.number().min(0).max(1),
  mediaVariantId: z.string().min(1),
  cropTransform: CropTransformSchema,
  selectedFindingId: z.string().nullable(),
  navigationState: NavigationMachineStateSchema,
  spatialView: SpatialViewStateSchema,
  viewRevision: z.number().int().nonnegative(),
});
export type CameraCheckpoint = z.infer<typeof CameraCheckpointSchema>;

export const NavigationHistorySchema = z.object({
  anchorMomentCheckpoint: CameraCheckpointSchema,
  checkpoints: z.array(CameraCheckpointSchema),
  cursorIndex: z.number().int().nonnegative(),
});
export type NavigationHistory = z.infer<typeof NavigationHistorySchema>;

export const ActionContextSchema = z.object({
  sessionId: z.string().min(1),
  sourceAssetId: z.string().min(1),
  sourceTimeSec: z.number().nonnegative(),
  navigationState: NavigationMachineStateSchema,
  calibrationState: CalibrationMachineStateSchema,
  selectedFindingId: z.string().nullable(),
  focusedFindingId: z.string().nullable(),
  recentPointer: z
    .object({
      xPx: z.number(),
      yPx: z.number(),
      atTimestampMs: z.number(),
      viewRevision: z.number().int().nonnegative(),
      nearestFindingId: z.string().optional(),
      overlappingCandidateIds: z.array(z.string()).optional(),
    })
    .optional(),
  simulationEpochISO: z.string(),
  viewRevision: z.number().int().nonnegative(),
  reducedMotion: z.boolean(),
});
export type ActionContext = z.infer<typeof ActionContextSchema>;

/** Scale regimes a voice action can fly to. planetary_surface stays out until it renders real data. */
export const ScaleRegimeSchema = z.enum(['earth_capture', 'cislunar', 'solar_neighborhood', 'deep_sky']);
export type ScaleRegime = z.infer<typeof ScaleRegimeSchema>;

/** The landing carousel's scenes by carousel key; each is a disclosed modelled composition. */
export const LandingSceneSchema = z.enum(['cislunar', 'black_hole', 'supergiant', 'saturn_horizon']);
export type LandingScene = z.infer<typeof LandingSceneSchema>;

export const AlbedianActionSchema = z.discriminatedUnion('type', [
  z.object({
    type: z.literal('media.restore'),
    requestId: z.string().min(1),
    expectedViewRevision: z.number().int().nonnegative(),
    sourceAssetId: z.string().min(1),
    profile: z.enum(['natural', 'temporal', 'star_protected']),
    constraints: z.object({
      preservePeople: z.boolean(),
      allowSyntheticSky: z.literal(false),
      protectStars: z.boolean(),
    }),
  }),
  z.object({
    type: z.literal('media.compare'),
    requestId: z.string().min(1),
    expectedViewRevision: z.number().int().nonnegative(),
    variantAId: z.string().min(1),
    variantBId: z.string().min(1),
    preserveTime: z.literal(true),
  }),
  z.object({
    type: z.literal('media.seek'),
    requestId: z.string().min(1),
    expectedViewRevision: z.number().int().nonnegative(),
    sourceTimeSec: z.number().nonnegative(),
    preserveAudioSync: z.literal(true),
  }),
  z.object({
    type: z.literal('sky.show'),
    requestId: z.string().min(1),
    expectedViewRevision: z.number().int().nonnegative(),
    mode: z.enum(['measured', 'predicted']),
    observationSolutionId: z.string().optional(),
  }),
  z.object({
    type: z.literal('finding.select'),
    requestId: z.string().min(1),
    expectedViewRevision: z.number().int().nonnegative(),
    findingId: z.string().min(1),
  }),
  z.object({
    type: z.literal('finding.describe'),
    requestId: z.string().min(1),
    expectedViewRevision: z.number().int().nonnegative(),
    findingId: z.string().min(1),
    fields: z.array(z.enum(['distance', 'type', 'motion', 'evidence', 'behind_sightline'])).min(1),
  }),
  z.object({
    type: z.literal('space.enter'),
    requestId: z.string().min(1),
    expectedViewRevision: z.number().int().nonnegative(),
    findingId: z.string().min(1),
    distancePolicy: z.literal('measured_or_estimated'),
  }),
  z.object({
    type: z.literal('space.viewpoint'),
    requestId: z.string().min(1),
    expectedViewRevision: z.number().int().nonnegative(),
    targetFindingId: z.string().min(1),
    units: z.literal('pc'),
    offsetParsec: z.tuple([z.number(), z.number(), z.number()]),
    trajectory: z.enum(['orbit', 'translate']),
  }),
  z.object({
    type: z.literal('space.epoch'),
    requestId: z.string().min(1),
    expectedViewRevision: z.number().int().nonnegative(),
    epochISO: z.string().min(4),
    yearCE: z.number().int().min(-10000).max(12000),
  }),
  z.object({
    type: z.literal('history.back'),
    requestId: z.string().min(1),
    expectedViewRevision: z.number().int().nonnegative(),
    mode: z.enum(['checkpoint', 'partial']),
    fraction: z.number().min(0.05).max(1).optional(),
  }),
  z.object({
    type: z.literal('navigation.cancel'),
    requestId: z.string().min(1),
    expectedViewRevision: z.number().int().nonnegative(),
    transitionId: z.string().optional(),
  }),
  z.object({
    type: z.literal('ui.overlay'),
    requestId: z.string().min(1),
    expectedViewRevision: z.number().int().nonnegative(),
    overlay: z.enum([
      'observedMarkers',
      'catalogMatchedLabels',
      'predictedEphemerides',
      'constellationLines',
      'earthDirectionVector',
      'residualVectors',
      'sightlineCone',
    ]),
    enabled: z.boolean(),
  }),
  z.object({
    type: z.literal('source.return'),
    requestId: z.string().min(1),
    expectedViewRevision: z.number().int().nonnegative(),
    checkpointId: z.string().optional(),
    restoreAudio: z.literal(true),
  }),
  z.object({
    type: z.literal('space.regime'),
    requestId: z.string().min(1),
    expectedViewRevision: z.number().int().nonnegative(),
    regime: ScaleRegimeSchema,
  }),
  z.object({
    type: z.literal('scene.show'),
    requestId: z.string().min(1),
    expectedViewRevision: z.number().int().nonnegative(),
    scene: LandingSceneSchema,
  }),
  z.object({
    type: z.literal('audio.set'),
    requestId: z.string().min(1),
    expectedViewRevision: z.number().int().nonnegative(),
    muted: z.boolean().optional(),
    gain: z.number().min(0).max(1).optional(),
  }),
  z.object({
    type: z.literal('question.ask'),
    requestId: z.string().min(1),
    expectedViewRevision: z.number().int().nonnegative(),
    question: z.string().min(1).max(500).regex(/\S/, 'A question needs at least one character that is not a space'),
    findingId: z.string().min(1).optional(),
  }),
]).superRefine((action, ctx) => {
  // A discriminated union member cannot carry its own refinement, so the audio rule lives here
  if (action.type === 'audio.set' && action.muted === undefined && action.gain === undefined) {
    ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['gain'], message: 'audio.set needs muted, gain or both' });
  }
});
export type AlbedianAction = z.infer<typeof AlbedianActionSchema>;

export const VoiceActionSchema = z.object({
  id: z.string().min(1),
  utterance: z.string().min(1),
  modality: z.enum(['voice', 'text', 'pointer']),
  context: ActionContextSchema,
  proposedAction: AlbedianActionSchema.optional(),
  createdAtUTC: z.string().datetime(),
});
export type VoiceAction = z.infer<typeof VoiceActionSchema>;

/** Who turned the utterance into the action: the deterministic rules, or a model and the model that answered. */
export const ActionInterpretationSchema = z.discriminatedUnion('parser', [
  z.object({ parser: z.literal('rules') }),
  z.object({
    parser: z.literal('model'),
    requestedModel: z.string().min(1),
    servedBy: z.string().min(1),
  }),
]);
export type ActionInterpretation = z.infer<typeof ActionInterpretationSchema>;

export const ActionOutcomeSchema = z.discriminatedUnion('status', [
  z.object({
    status: z.literal('completed'),
    actionId: z.string().min(1),
    interpretation: ActionInterpretationSchema.optional(),
    newStateVersion: z.number().int().nonnegative(),
    spokenFeedback: z.string().optional(),
    visualStatus: z.string().min(1),
  }),
  z.object({
    status: z.literal('needs_input'),
    actionId: z.string().min(1),
    interpretation: ActionInterpretationSchema.optional(),
    question: z.string().min(1),
    choices: z.array(
      z.object({
        id: z.string().min(1),
        label: z.string().min(1),
      })
    ),
  }),
  z.object({
    status: z.literal('rejected'),
    actionId: z.string().min(1),
    interpretation: ActionInterpretationSchema.optional(),
    reason: z.enum([
      'stale_revision',
      'unresolved_sky',
      'ambiguous_target',
      'no_distance_estimate',
      'invalid_epoch',
      'missing_source',
      'unsafe_synthetic_edit',
      'no_grounded_answer',
      'not_connected',
    ]),
    explanation: z.string().min(1),
    nextActions: z.array(z.string()),
  }),
  z.object({
    status: z.literal('canceled'),
    actionId: z.string().min(1),
    interpretation: ActionInterpretationSchema.optional(),
    settledStateVersion: z.number().int().nonnegative(),
    visualStatus: z.string().min(1),
  }),
]);
export type ActionOutcome = z.infer<typeof ActionOutcomeSchema>;

export const ObservationSessionSchema = z.object({
  id: z.string().min(1),
  title: z.string().min(1),
  viewRevision: z.number().int().nonnegative(),
  machines: z.object({
    media: MediaMachineStateSchema,
    restoration: RestorationMachineStateSchema,
    calibration: CalibrationMachineStateSchema,
    navigation: NavigationMachineStateSchema,
    voice: VoiceMachineStateSchema,
  }),
  sourceAsset: SourceAssetSchema,
  activeVariantId: z.string().min(1),
  compareVariantId: z.string().optional(),
  compareWipePosition: z.number().min(0).max(1),
  currentSourceTimeSec: z.number().nonnegative(),
  isPlaying: z.boolean(),
  audioGain: z.number().min(0).max(1),
  cropTransform: CropTransformSchema,
  processingGraphs: z.array(ProcessingGraphSchema),
  processingAttempts: z.array(ProcessingAttemptSchema),
  mediaVariants: z.array(MediaVariantSchema),
  observationSolution: ObservationSolutionSchema,
  selectedFindingId: z.string().nullable(),
  focusedFindingId: z.string().nullable(),
  spatialView: SpatialViewStateSchema,
  navigationHistory: NavigationHistorySchema,
  voiceActions: z.array(VoiceActionSchema),
  exportArtifacts: z.array(ExportArtifactSchema),
  reducedMotion: z.boolean(),
  lastStatusBanner: z.string().optional(),
});
export type ObservationSession = z.infer<typeof ObservationSessionSchema>;
