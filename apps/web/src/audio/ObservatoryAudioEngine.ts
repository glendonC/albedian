/**
 * Albedian Observational & Spatial Sound Engine (Web Audio API)
 *
 * Implements three layers of acoustic grounding specified in the Master Research Brief:
 * 1. Continuous Atmospheric Pullback (Contact-style low-pass filter sweep from Sahara night wind
 *    into deep-space sub-bass cosmic resonance across Cislunar, Local Bubble, and Deep Sky scales).
 * 2. Spectral Monocle & Target Lock Sonification (Planckian spectral-index harmonic intervals
 *    spatialized via StereoPannerNode).
 * 3. Tactile Hardware Detents (Leica/optical aperture micro-clicks when tilting the 01-07 Siterra
 *    specimen plates or engaging Genie 4 navigation controls).
 */

export type ScaleAudioRegime = "earth_capture" | "cislunar" | "solar_neighborhood" | "deep_sky";

export class ObservatoryAudioEngine {
  private ctx: AudioContext | null = null;
  private isEnabled = false;
  private masterGain: GainNode | null = null;

  // Ambient desert wind nodes
  private desertGain: GainNode | null = null;
  private desertFilter: BiquadFilterNode | null = null;

  // Deep-space harmonic drone nodes
  private spaceGain: GainNode | null = null;
  private spaceFilter: BiquadFilterNode | null = null;
  private droneOsc1: OscillatorNode | null = null;
  private droneOsc2: OscillatorNode | null = null;

  private currentRegime: ScaleAudioRegime = "earth_capture";

  public toggle(): boolean {
    if (!this.isEnabled) {
      this.enable();
    } else {
      this.disable();
    }
    return this.isEnabled;
  }

  public getEnabled(): boolean {
    return this.isEnabled;
  }

  public enable(): void {
    if (typeof window === "undefined") return;
    if (!this.ctx) {
      const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      this.ctx = new AudioCtx();
      this.setupAmbientGraph();
    }
    if (this.ctx.state === "suspended") {
      void this.ctx.resume();
    }
    this.isEnabled = true;
    if (this.masterGain && this.ctx) {
      this.masterGain.gain.setTargetAtTime(0.85, this.ctx.currentTime, 0.25);
    }
    this.setScaleRegime(this.currentRegime);
    this.playDetentClick(1100);
  }

  public disable(): void {
    this.isEnabled = false;
    if (this.masterGain && this.ctx) {
      this.masterGain.gain.setTargetAtTime(0.0001, this.ctx.currentTime, 0.18);
    }
  }

  private setupAmbientGraph(): void {
    if (!this.ctx) return;
    const now = this.ctx.currentTime;

    this.masterGain = this.ctx.createGain();
    this.masterGain.gain.setValueAtTime(0.001, now);
    this.masterGain.connect(this.ctx.destination);

    // 1. Sahara Night Wind Generator (Pink-ish filtered noise buffer)
    const bufferSize = this.ctx.sampleRate * 4;
    const noiseBuffer = this.ctx.createBuffer(1, bufferSize, this.ctx.sampleRate);
    const output = noiseBuffer.getChannelData(0);
    let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0;
    for (let i = 0; i < bufferSize; i++) {
      const white = Math.random() * 2 - 1;
      b0 = 0.99886 * b0 + white * 0.0555179;
      b1 = 0.99332 * b1 + white * 0.0750759;
      b2 = 0.96900 * b2 + white * 0.1538520;
      b3 = 0.86650 * b3 + white * 0.3104856;
      b4 = 0.55000 * b4 + white * 0.5329522;
      b5 = -0.7616 * b5 - white * 0.0168980;
      output[i] = (b0 + b1 + b2 + b3 + b4 + b5 + b6 + white * 0.5362) * 0.018;
      b6 = white * 0.115926;
    }

    const noiseSource = this.ctx.createBufferSource();
    noiseSource.buffer = noiseBuffer;
    noiseSource.loop = true;

    this.desertFilter = this.ctx.createBiquadFilter();
    this.desertFilter.type = "bandpass";
    this.desertFilter.frequency.setValueAtTime(420, now);
    this.desertFilter.Q.setValueAtTime(1.4, now);

    this.desertGain = this.ctx.createGain();
    this.desertGain.gain.setValueAtTime(0.32, now);

    noiseSource.connect(this.desertFilter);
    this.desertFilter.connect(this.desertGain);
    this.desertGain.connect(this.masterGain);
    noiseSource.start(now);

    // 2. Deep-Space Sub-Bass & Harmonic Cavity Drone
    this.spaceFilter = this.ctx.createBiquadFilter();
    this.spaceFilter.type = "lowpass";
    this.spaceFilter.frequency.setValueAtTime(160, now);
    this.spaceFilter.Q.setValueAtTime(2.0, now);

    this.spaceGain = this.ctx.createGain();
    this.spaceGain.gain.setValueAtTime(0.04, now);

    this.droneOsc1 = this.ctx.createOscillator();
    this.droneOsc1.type = "sine";
    this.droneOsc1.frequency.setValueAtTime(55.0, now); // A1 sub-fundamental

    this.droneOsc2 = this.ctx.createOscillator();
    this.droneOsc2.type = "triangle";
    this.droneOsc2.frequency.setValueAtTime(82.41, now); // E2 fifth

    this.droneOsc1.connect(this.spaceFilter);
    this.droneOsc2.connect(this.spaceFilter);
    this.spaceFilter.connect(this.spaceGain);
    this.spaceGain.connect(this.masterGain);

    this.droneOsc1.start(now);
    this.droneOsc2.start(now);
  }

  /**
   * Smoothly sweeps the acoustic environment between Earth's atmospheric capture
   * and deep-space vacuum / cavity scales.
   */
  public setScaleRegime(regime: ScaleAudioRegime): void {
    this.currentRegime = regime;
    if (!this.ctx || !this.isEnabled || !this.desertGain || !this.desertFilter || !this.spaceGain || !this.spaceFilter || !this.droneOsc1 || !this.droneOsc2) {
      return;
    }
    const now = this.ctx.currentTime;

    if (regime === "earth_capture") {
      // Sahara Desert night air audible, cosmic drone subtle
      this.desertFilter.frequency.setTargetAtTime(460, now, 0.6);
      this.desertGain.gain.setTargetAtTime(0.34, now, 0.6);
      this.spaceGain.gain.setTargetAtTime(0.04, now, 0.6);
      this.spaceFilter.frequency.setTargetAtTime(120, now, 0.6);
      this.droneOsc1.frequency.setTargetAtTime(55.0, now, 0.8);
      this.droneOsc2.frequency.setTargetAtTime(82.41, now, 0.8);
    } else if (regime === "cislunar") {
      // Vacuum pullback: desert air muffled via low-pass, lunar orbital sub-harmonic emerges
      this.desertFilter.frequency.setTargetAtTime(110, now, 0.7);
      this.desertGain.gain.setTargetAtTime(0.05, now, 0.7);
      this.spaceGain.gain.setTargetAtTime(0.16, now, 0.7);
      this.spaceFilter.frequency.setTargetAtTime(195, now, 0.7);
      this.droneOsc1.frequency.setTargetAtTime(65.41, now, 0.8); // C2
      this.droneOsc2.frequency.setTargetAtTime(98.0, now, 0.8);  // G2
    } else if (regime === "solar_neighborhood") {
      // Local Bubble Chimney cavity resonance (O'Neill et al. 165 pc cavity)
      this.desertGain.gain.setTargetAtTime(0.005, now, 0.7);
      this.spaceGain.gain.setTargetAtTime(0.22, now, 0.7);
      this.spaceFilter.frequency.setTargetAtTime(260, now, 0.7);
      this.droneOsc1.frequency.setTargetAtTime(55.0, now, 0.8);  // A1
      this.droneOsc2.frequency.setTargetAtTime(110.0, now, 0.8); // A2 octave
    } else {
      // Deep Sky 3D Nebula (NASA Pillars / M42 ionized H-II region)
      this.desertGain.gain.setTargetAtTime(0.002, now, 0.7);
      this.spaceGain.gain.setTargetAtTime(0.26, now, 0.7);
      this.spaceFilter.frequency.setTargetAtTime(340, now, 0.7);
      this.droneOsc1.frequency.setTargetAtTime(49.0, now, 0.9);  // G1 deep gravitas
      this.droneOsc2.frequency.setTargetAtTime(73.42, now, 0.9); // D2
    }
  }

  /**
   * Crisp optical-mechanical detent click (used for 01-07 Siterra layer hover,
   * Spectral Monocle band switching, and Genie 4 keycap presses).
   */
  public playDetentClick(pitchHz = 920): void {
    if (!this.ctx || !this.isEnabled || !this.masterGain) return;
    const now = this.ctx.currentTime;

    const osc = this.ctx.createOscillator();
    const gain = this.ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(pitchHz, now);
    osc.frequency.exponentialRampToValueAtTime(pitchHz * 0.35, now + 0.024);

    gain.gain.setValueAtTime(0.11, now);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.026);

    osc.connect(gain);
    gain.connect(this.masterGain);
    osc.start(now);
    osc.stop(now + 0.028);
  }

  /**
   * Plays a spatialized Planckian harmonic lock tone when selecting a star or specimen target.
   * @param bvIndex Color index (-0.3 blue hot -> +1.85 red supergiant)
   * @param panX Stereo pan (-1 left to +1 right)
   */
  public playTargetLockTone(bvIndex = 0.2, panX = 0.0): void {
    if (!this.ctx || !this.isEnabled || !this.masterGain) return;
    const now = this.ctx.currentTime;

    // Hot blue stars have higher harmonic fundamental; cool red supergiants (Betelgeuse) have warm low fundamental
    const normalizedTemp = 1.0 - Math.min(1.0, Math.max(0.0, (bvIndex + 0.3) / 2.2));
    const baseFreq = 220 + normalizedTemp * 310;

    const panner = this.ctx.createStereoPanner();
    panner.pan.setValueAtTime(Math.max(-0.9, Math.min(0.9, panX)), now);

    const oscFundamental = this.ctx.createOscillator();
    const oscFifth = this.ctx.createOscillator();
    const env = this.ctx.createGain();

    oscFundamental.type = "sine";
    oscFundamental.frequency.setValueAtTime(baseFreq, now);

    oscFifth.type = "sine";
    oscFifth.frequency.setValueAtTime(baseFreq * 1.498, now);

    env.gain.setValueAtTime(0.001, now);
    env.gain.linearRampToValueAtTime(0.09, now + 0.03);
    env.gain.exponentialRampToValueAtTime(0.0001, now + 0.65);

    oscFundamental.connect(env);
    oscFifth.connect(env);
    env.connect(panner);
    panner.connect(this.masterGain);

    oscFundamental.start(now);
    oscFifth.start(now);
    oscFundamental.stop(now + 0.68);
    oscFifth.stop(now + 0.68);
  }
}

export const observatoryAudio = new ObservatoryAudioEngine();
