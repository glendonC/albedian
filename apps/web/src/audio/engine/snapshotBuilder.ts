import type { ScaleAudioRegime } from '../mappings.js';
import type { ReverbStatus } from './ConvolutionReverb.js';
import type { BedRenderer } from './SkyglowBed.js';
import type { SlotState } from './VoicePool.js';
import type { EngineSnapshot, SilentOutcome, VoiceSnapshot } from './engineTypes.js';
import type { BedLevelEvaluation, PlannedAudioFrame } from './framePlanner.js';

export interface SnapshotInputs {
  readonly contextState: AudioContextState | 'none';
  readonly enabled: boolean;
  readonly regime: ScaleAudioRegime;
  readonly reverbStatus: ReverbStatus;
  readonly reverbError: string | null;
  readonly bedRenderer: BedRenderer | 'loading' | 'none';
  readonly bedLevel: BedLevelEvaluation;
  readonly bedFallbackReason: string | null;
  readonly frame: PlannedAudioFrame;
  readonly poolStates: readonly SlotState[];
}

/** Projects the planned frame, live voice slots, reverb state, and skyglow bed status into an EngineSnapshot. */
export function buildEngineSnapshot(inputs: SnapshotInputs): EngineSnapshot {
  const statesById = new Map(inputs.poolStates.map((state) => [state.id, state]));
  const voices: VoiceSnapshot[] = inputs.frame.selected.map((plan) => {
    const state = statesById.get(plan.id);
    return {
      ...plan,
      slot: state?.slot ?? null,
      pitchHz: state?.pitchHz ?? inputs.frame.pitches.get(plan.id) ?? null,
      centroidHz: state?.centroidHz ?? null,
    };
  });
  const selectedIds = new Set(inputs.frame.selected.map((plan) => plan.id));
  const notSelected = inputs.frame.outcomes.flatMap((outcome) =>
    outcome.status === 'audible' && !selectedIds.has(outcome.plan.id) ? [outcome.plan.id] : []
  );
  const silent = inputs.frame.outcomes.filter(
    (outcome): outcome is SilentOutcome => outcome.status === 'silent'
  );
  return {
    contextState: inputs.contextState,
    enabled: inputs.enabled,
    regime: inputs.regime,
    reverb: { status: inputs.reverbStatus, error: inputs.reverbError },
    bed: {
      renderer: inputs.bedRenderer,
      levelDb: inputs.bedLevel.levelDb,
      silentBecause: inputs.bedLevel.silentBecause,
      fallbackReason: inputs.bedFallbackReason,
    },
    voices,
    notSelected,
    silent,
  };
}
