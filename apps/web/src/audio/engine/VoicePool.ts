import { IDENTITY_PITCHES_HZ, type VoicePlan } from '../voicePlanning.js';
import { createSharedVoiceResources } from './voices/harmonicWaves.js';
import { VoiceSlot } from './voices/VoiceSlot.js';
import { DEFAULT_HARMONIC_CEILING_HZ, FALLBACK_PITCH_HZ, type SlotState } from './voices/voiceTypes.js';

export { DEFAULT_HARMONIC_CEILING_HZ, type SlotState };

/**
 * A fixed number of HRTF voices. `apply` takes plans that `selectVoices` already chose
 * (at most `capacity`) and realizes them: matching ids keep their slot, departed ids fade
 * out, new ids take a free slot.
 */
export class VoicePool {
  private readonly slots: VoiceSlot[];

  constructor(
    private readonly ctx: BaseAudioContext,
    readonly capacity: number,
    output: AudioNode,
    reverbInput: AudioNode,
    harmonicCeilingHz: number = DEFAULT_HARMONIC_CEILING_HZ
  ) {
    const shared = createSharedVoiceResources(ctx, harmonicCeilingHz);
    this.slots = Array.from(
      { length: Math.max(0, capacity) },
      (_, index) => new VoiceSlot(ctx, index, output, reverbInput, shared)
    );
  }

  apply(selected: readonly VoicePlan[], pitches: ReadonlyMap<string, number>): void {
    const now = this.ctx.currentTime;
    const wanted = new Set(selected.map((plan) => plan.id));
    for (const slot of this.slots) {
      if (slot.id !== null && !wanted.has(slot.id)) slot.release(now);
    }
    for (const plan of selected.slice(0, this.slots.length)) {
      const pitch = pitches.get(plan.id) ?? IDENTITY_PITCHES_HZ[0] ?? FALLBACK_PITCH_HZ;
      const current = this.slots.find((slot) => slot.id === plan.id);
      if (current) {
        current.update(plan, pitch, now);
        continue;
      }
      const free =
        this.slots.find((slot) => slot.id === null && slot.tailCount(now) === 0) ??
        this.slots.find((slot) => slot.id === null);
      free?.assign(plan, pitch, now);
    }
  }

  states(): SlotState[] {
    const states: SlotState[] = [];
    for (const slot of this.slots) {
      if (slot.id !== null) {
        states.push({
          slot: slot.index,
          id: slot.id,
          pitchHz: slot.pitchHz,
          centroidHz: slot.centroidHz,
        });
      }
    }
    return states;
  }

  dispose(): void {
    const now = this.ctx.currentTime;
    for (const slot of this.slots) slot.dispose(now);
  }
}
