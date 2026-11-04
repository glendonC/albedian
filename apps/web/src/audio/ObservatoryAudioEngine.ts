/**
 * Albedian Observational & Spatial Sound Engine (Web Audio API)
 *
 * Implements three layers of acoustic grounding:
 * 1. Continuous 5-Regime Environmental Soundscape (`01 Sky` -> `02 Orbit` -> `03 Stars` ->
 *    `04 Deep Space` -> `05 Surface`), a procedural ambient bed per spatial regime.
 * 2. Spectral Monocle & Target Lock Sonification (Planckian spectral-index harmonic intervals
 *    spatialized via StereoPannerNode).
 * 3. Tactile Hardware Detents (optical aperture micro-clicks on navigation controls).
 */

export type ScaleAudioRegime =
  | "earth_capture"
  | "cislunar"
  | "solar_neighborhood"
  | "deep_sky"
  | "planetary_surface";

export class ObservatoryAudioEngine {
  private ctx: AudioContext | null = null;
  private isEnabled = false;
  private masterGain: GainNode | null = null;

  // Ambient night air / planetary canyon wind nodes
  private desertGain: GainNode | null = null;
  private desertFilter: BiquadFilterNode | null = null;
  private surfaceWindLfo: OscillatorNode | null = null;
  private surfaceWindLfoGain: GainNode | null = null;

  // Deep-space & stellar MHD harmonic drone nodes
  private spaceGain: GainNode | null = null;
  private spaceFilter: BiquadFilterNode | null = null;
  private droneOsc1: OscillatorNode | null = null;
  private droneOsc2: OscillatorNode | null = null;
  private mhdLfo: OscillatorNode | null = null;
  private mhdLfoGain: GainNode | null = null;

  // Cislunar reaction-wheel & orbital telemetry carrier harmonic nodes
  private orbitalCarrierOsc: OscillatorNode | null = null;
  private orbitalCarrierFilter: BiquadFilterNode | null = null;
  private orbitalCarrierGain: GainNode | null = null;

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
      const AudioCtx =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
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

    // 1. Desert Night Breeze & Planetary Canyon / Hydrocarbon Wave Generator (Pink-ish filtered noise)
    const bufferSize = this.ctx.sampleRate * 4;
    const noiseBuffer = this.ctx.createBuffer(1, bufferSize, this.ctx.sampleRate);
    const output = noiseBuffer.getChannelData(0);
    let b0 = 0,
      b1 = 0,
      b2 = 0,
      b3 = 0,
      b4 = 0,
      b5 = 0,
      b6 = 0;
    for (let i = 0; i < bufferSize; i++) {
      const white = Math.random() * 2 - 1;
      b0 = 0.99886 * b0 + white * 0.0555179;
      b1 = 0.99332 * b1 + white * 0.0750759;
      b2 = 0.969 * b2 + white * 0.153852;
      b3 = 0.8665 * b3 + white * 0.3104856;
      b4 = 0.55 * b4 + white * 0.5329522;
      b5 = -0.7616 * b5 - white * 0.016898;
      output[i] = (b0 + b1 + b2 + b3 + b4 + b5 + b6 + white * 0.5362) * 0.018;
      b6 = white * 0.115926;
    }

    const noiseSource = this.ctx.createBufferSource();
    noiseSource.buffer = noiseBuffer;
    noiseSource.loop = true;

    this.desertFilter = this.ctx.createBiquadFilter();
    this.desertFilter.type = "bandpass";
    this.desertFilter.frequency.setValueAtTime(460, now);
    this.desertFilter.Q.setValueAtTime(1.4, now);

    // Slow LFO for Planetary Surface canyon wind / Titan hydrocarbon wave surf (240–580 Hz sweep)
    this.surfaceWindLfo = this.ctx.createOscillator();
    this.surfaceWindLfo.type = "sine";
    this.surfaceWindLfo.frequency.setValueAtTime(0.14, now);

    this.surfaceWindLfoGain = this.ctx.createGain();
    this.surfaceWindLfoGain.gain.setValueAtTime(18.0, now);

    this.surfaceWindLfo.connect(this.surfaceWindLfoGain);
    this.surfaceWindLfoGain.connect(this.desertFilter.frequency);
    this.surfaceWindLfo.start(now);

    this.desertGain = this.ctx.createGain();
    this.desertGain.gain.setValueAtTime(0.32, now);

    noiseSource.connect(this.desertFilter);
    this.desertFilter.connect(this.desertGain);
    this.desertGain.connect(this.masterGain);
    noiseSource.start(now);

    // 2. Deep-Space Sub-Bass, Stellar MHD Convection & Planetary Resonance Drone
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

    // Slow magnetic MHD modulation LFO for Stellar Neighborhood & Deep Space
    this.mhdLfo = this.ctx.createOscillator();
    this.mhdLfo.type = "sine";
    this.mhdLfo.frequency.setValueAtTime(0.22, now);

    this.mhdLfoGain = this.ctx.createGain();
    this.mhdLfoGain.gain.setValueAtTime(0.0, now);

    this.mhdLfo.connect(this.mhdLfoGain);
    this.mhdLfoGain.connect(this.spaceFilter.frequency);
    this.mhdLfo.start(now);

    this.droneOsc1.connect(this.spaceFilter);
    this.droneOsc2.connect(this.spaceFilter);
    this.spaceFilter.connect(this.spaceGain);
    this.spaceGain.connect(this.masterGain);

    this.droneOsc1.start(now);
    this.droneOsc2.start(now);

    // 3. Cislunar Spacecraft Reaction-Wheel & High-Q Telemetry Carrier Harmonic
    this.orbitalCarrierOsc = this.ctx.createOscillator();
    this.orbitalCarrierOsc.type = "sine";
    this.orbitalCarrierOsc.frequency.setValueAtTime(392.0, now); // G4 harmonic

    this.orbitalCarrierFilter = this.ctx.createBiquadFilter();
    this.orbitalCarrierFilter.type = "bandpass";
    this.orbitalCarrierFilter.frequency.setValueAtTime(392.0, now);
    this.orbitalCarrierFilter.Q.setValueAtTime(14.0, now);

    this.orbitalCarrierGain = this.ctx.createGain();
    this.orbitalCarrierGain.gain.setValueAtTime(0.0001, now);

    this.orbitalCarrierOsc.connect(this.orbitalCarrierFilter);
    this.orbitalCarrierFilter.connect(this.orbitalCarrierGain);
    this.orbitalCarrierGain.connect(this.masterGain);
    this.orbitalCarrierOsc.start(now);

  }

  /**
   * Smoothly sweeps the procedural Web Audio environment across all 5 scale regimes:
   * - `01 Sky` (`earth_capture`)
   * - `02 Orbit` (`cislunar`)
   * - `03 Stars` (`solar_neighborhood`)
   * - `04 Deep Space` (`deep_sky`)
   * - `05 Surface` (`planetary_surface`)
   */
  public setScaleRegime(regime: ScaleAudioRegime): void {
    this.currentRegime = regime;
    if (
      !this.ctx ||
      !this.isEnabled ||
      !this.desertGain ||
      !this.desertFilter ||
      !this.spaceGain ||
      !this.spaceFilter ||
      !this.droneOsc1 ||
      !this.droneOsc2
    ) {
      return;
    }
    const now = this.ctx.currentTime;

    if (regime === "earth_capture") {
      // 01 Sky: Desert/alpine night breeze (~460 Hz bandpass pink noise + subtle cosmic stillness)
      this.desertFilter.frequency.setTargetAtTime(460, now, 0.6);
      this.desertFilter.Q.setTargetAtTime(1.4, now, 0.6);
      this.desertGain.gain.setTargetAtTime(0.34, now, 0.6);
      this.surfaceWindLfoGain?.gain.setTargetAtTime(18.0, now, 0.6);

      this.spaceGain.gain.setTargetAtTime(0.04, now, 0.6);
      this.spaceFilter.frequency.setTargetAtTime(120, now, 0.6);
      this.droneOsc1.frequency.setTargetAtTime(55.0, now, 0.8);
      this.droneOsc2.frequency.setTargetAtTime(82.41, now, 0.8);
      this.mhdLfoGain?.gain.setTargetAtTime(0.0, now, 0.6);
      this.orbitalCarrierGain?.gain.setTargetAtTime(0.0001, now, 0.5);
    } else if (regime === "cislunar") {
      // 02 Orbit: Muffled vacuum pullback + 65.41 Hz C2 / 98.0 Hz G2 + high-Q reaction-wheel telemetry harmonic
      this.desertFilter.frequency.setTargetAtTime(110, now, 0.7);
      this.desertFilter.Q.setTargetAtTime(1.2, now, 0.7);
      this.desertGain.gain.setTargetAtTime(0.045, now, 0.7);
      this.surfaceWindLfoGain?.gain.setTargetAtTime(0.0, now, 0.6);

      this.spaceGain.gain.setTargetAtTime(0.16, now, 0.7);
      this.spaceFilter.frequency.setTargetAtTime(195, now, 0.7);
      this.droneOsc1.frequency.setTargetAtTime(65.41, now, 0.8); // C2
      this.droneOsc2.frequency.setTargetAtTime(98.0, now, 0.8); // G2
      this.mhdLfoGain?.gain.setTargetAtTime(6.0, now, 0.7);
      this.orbitalCarrierGain?.gain.setTargetAtTime(0.016, now, 0.7);
    } else if (regime === "solar_neighborhood") {
      // 03 Stars: Stellar plasma resonance & MHD convection hum (55.0 Hz A1 / 110.0 Hz A2 + slow magnetic modulation)
      this.desertFilter.frequency.setTargetAtTime(140, now, 0.7);
      this.desertGain.gain.setTargetAtTime(0.005, now, 0.7);
      this.surfaceWindLfoGain?.gain.setTargetAtTime(0.0, now, 0.6);

      this.spaceGain.gain.setTargetAtTime(0.22, now, 0.7);
      this.spaceFilter.frequency.setTargetAtTime(260, now, 0.7);
      this.droneOsc1.frequency.setTargetAtTime(55.0, now, 0.8); // A1
      this.droneOsc2.frequency.setTargetAtTime(110.0, now, 0.8); // A2 octave
      this.mhdLfoGain?.gain.setTargetAtTime(48.0, now, 0.7);
      this.orbitalCarrierGain?.gain.setTargetAtTime(0.0001, now, 0.5);
    } else if (regime === "deep_sky") {
      // 04 Deep Space: Relativistic sub-bass & event-horizon gravitas (36.71 Hz D1 / 55.0 Hz A1 deep sub-bass)
      this.desertFilter.frequency.setTargetAtTime(90, now, 0.7);
      this.desertGain.gain.setTargetAtTime(0.002, now, 0.7);
      this.surfaceWindLfoGain?.gain.setTargetAtTime(0.0, now, 0.6);

      this.spaceGain.gain.setTargetAtTime(0.28, now, 0.7);
      this.spaceFilter.frequency.setTargetAtTime(175, now, 0.7);
      this.droneOsc1.frequency.setTargetAtTime(36.71, now, 0.9); // D1 relativistic sub-bass
      this.droneOsc2.frequency.setTargetAtTime(55.0, now, 0.9); // A1 fifth gravitas
      this.mhdLfoGain?.gain.setTargetAtTime(14.0, now, 0.8);
      this.orbitalCarrierGain?.gain.setTargetAtTime(0.0001, now, 0.5);
    } else if (regime === "planetary_surface") {
      // 05 Surface: Canyon wind / cryogenic hydrocarbon wave surf (slow LFO-swept bandpass noise at 240–580 Hz + planetary resonance)
      this.desertFilter.frequency.setTargetAtTime(410, now, 0.7);
      this.desertFilter.Q.setTargetAtTime(2.2, now, 0.7);
      this.desertGain.gain.setTargetAtTime(0.38, now, 0.7);
      this.surfaceWindLfoGain?.gain.setTargetAtTime(170.0, now, 0.7); // 410 ± 170 Hz -> 240–580 Hz sweep

      this.spaceGain.gain.setTargetAtTime(0.15, now, 0.7);
      this.spaceFilter.frequency.setTargetAtTime(210, now, 0.7);
      this.droneOsc1.frequency.setTargetAtTime(49.0, now, 0.8); // G1 planetary resonance
      this.droneOsc2.frequency.setTargetAtTime(73.42, now, 0.8); // D2
      this.mhdLfoGain?.gain.setTargetAtTime(22.0, now, 0.7);
      this.orbitalCarrierGain?.gain.setTargetAtTime(0.0001, now, 0.5);
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
