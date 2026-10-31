import {
  NavigationMachineState,
  VoiceMachineState,
} from '@albedian/contracts';
import { computeAudioBusGains } from '@albedian/design-system';

/**
 * ============================================================================
 * Albedian Web Audio Bus Controller
 * Implements Section D8 of the Spatial Experience Research:
 * - Authentic source ambient sound during `source` / `comparison` / `sky_registered`
 * - Automatic pause of source audio when entering `space` (never implies extraterrestrial sound)
 * - Gentle monitor ducking during active voice interaction
 * - Restrained dry selection click and harmonic calibration cue
 * ============================================================================
 */
export class AlbedianWebAudioBus {
  private ctx: AudioContext | null = null;
  private masterGain: GainNode | null = null;
  private sourceMonitorGain: GainNode | null = null;
  private uiCueGain: GainNode | null = null;
  private ambientOsc: OscillatorNode | null = null;
  private isAmbientRunning = false;

  private ensureContext(): AudioContext | null {
    if (typeof window === 'undefined') return null;
    const AudioCtx =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext?: typeof AudioContext })
        .webkitAudioContext;
    if (!AudioCtx) return null;

    if (!this.ctx) {
      this.ctx = new AudioCtx();
      this.masterGain = this.ctx.createGain();
      this.sourceMonitorGain = this.ctx.createGain();
      this.uiCueGain = this.ctx.createGain();

      this.sourceMonitorGain.connect(this.masterGain);
      this.uiCueGain.connect(this.masterGain);
      this.masterGain.connect(this.ctx.destination);
    }
    if (this.ctx.state === 'suspended') {
      void this.ctx.resume();
    }
    return this.ctx;
  }

  public syncBusState(params: {
    navigationState: NavigationMachineState;
    voiceState: VoiceMachineState;
    userSourceGain: number;
    isMuted: boolean;
    isPlaying: boolean;
  }): {
    sourceMonitorGain: number;
    sourcePlaybackAllowed: boolean;
  } {
    const policy = computeAudioBusGains({
      navigationState: params.navigationState,
      voiceState: params.voiceState,
      userSourceGain: params.userSourceGain,
      isMuted: params.isMuted,
    });

    if (this.ctx && this.sourceMonitorGain && this.uiCueGain) {
      const now = this.ctx.currentTime;
      const effectiveSourceGain =
        params.isPlaying && policy.sourcePlaybackAllowed
          ? policy.sourceMonitorGain
          : 0;
      this.sourceMonitorGain.gain.setTargetAtTime(effectiveSourceGain, now, 0.04);
      this.uiCueGain.gain.setTargetAtTime(policy.uiCueGain, now, 0.02);
    }

    return {
      sourceMonitorGain: policy.sourceMonitorGain,
      sourcePlaybackAllowed: policy.sourcePlaybackAllowed,
    };
  }

  /**
   * Plays a quiet, dry two-part observational instrument click on star/control selection.
   */
  public playSelectionClick(isMuted: boolean): void {
    if (isMuted) return;
    const ctx = this.ensureContext();
    if (!ctx || !this.uiCueGain) return;

    const now = ctx.currentTime;
    const osc = ctx.createOscillator();
    const env = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(640, now);
    osc.frequency.exponentialRampToValueAtTime(320, now + 0.028);

    env.gain.setValueAtTime(0.08, now);
    env.gain.exponentialRampToValueAtTime(0.0001, now + 0.032);

    osc.connect(env);
    env.connect(this.uiCueGain);
    osc.start(now);
    osc.stop(now + 0.035);
  }

  /**
   * Plays a brief harmonic arrival cue when sky calibration succeeds or returning to moment.
   */
  public playCalibrationHarmonic(isMuted: boolean): void {
    if (isMuted) return;
    const ctx = this.ensureContext();
    if (!ctx || !this.uiCueGain) return;

    const now = ctx.currentTime;
    for (const [idx, freq] of [440, 659.25].entries()) {
      const osc = ctx.createOscillator();
      const env = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, now + idx * 0.04);

      env.gain.setValueAtTime(0.0001, now + idx * 0.04);
      env.gain.linearRampToValueAtTime(0.06, now + idx * 0.04 + 0.02);
      env.gain.exponentialRampToValueAtTime(0.0001, now + idx * 0.04 + 0.24);

      osc.connect(env);
      env.connect(this.uiCueGain);
      osc.start(now + idx * 0.04);
      osc.stop(now + idx * 0.04 + 0.25);
    }
  }

  public dispose(): void {
    if (this.ambientOsc && this.isAmbientRunning) {
      this.ambientOsc.stop();
      this.isAmbientRunning = false;
    }
    if (this.ctx) {
      void this.ctx.close();
      this.ctx = null;
    }
  }
}
