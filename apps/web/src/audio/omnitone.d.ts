// Minimal types for omnitone@2 (Apache-2.0), which ships JavaScript only. Covers the
// first-order renderer surface the skyglow bed uses; see node_modules/omnitone/build.
declare module 'omnitone' {
  export type OmnitoneRenderingMode = 'ambisonic' | 'bypass' | 'off';

  export interface OmnitoneFOARenderer {
    /** Four-channel ACN/SN3D input (W, Y, Z, X). */
    readonly input: GainNode;
    /** Binaural stereo output. */
    readonly output: GainNode;
    initialize(): Promise<void>;
    setRotationMatrix3(rotationMatrix3: ArrayLike<number>): void;
    setRotationMatrix4(rotationMatrix4: ArrayLike<number>): void;
    setRenderingMode(mode: OmnitoneRenderingMode): void;
  }

  export interface OmnitoneStatic {
    readonly browserInfo: { readonly name: string; readonly version: string; readonly platform: string };
    createFOARenderer(
      context: BaseAudioContext,
      config?: {
        channelMap?: number[];
        hrirPathList?: string[];
        renderingMode?: OmnitoneRenderingMode;
      }
    ): OmnitoneFOARenderer;
  }

  const Omnitone: OmnitoneStatic;
  export default Omnitone;
}
