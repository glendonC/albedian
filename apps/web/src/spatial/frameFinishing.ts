import { DEFAULT_COSMOS_POST, type CosmosPostOptions } from './webgpu/postOptions.js';

/**
 * What the 3D view does to the whole finished frame, the photographs in it with everything else, worded from the
 * lists that make it: the WebGPU renderer's post options (webgpu/postOptions.ts), and the WebGL renderer's composer
 * (SparkPortalRenderer.ts) in the same terms. No three.js here: the Credits page reads it.
 */

/**
 * A switch of the post options: every option that is not a number. A number is a setting of a switch's step;
 * creditChanges.test.ts lists each with the switch it sets, and names any key that is neither.
 */
export type FrameSwitch = { [K in keyof CosmosPostOptions]: CosmosPostOptions[K] extends number ? never : K }[keyof CosmosPostOptions];
type Switches = Pick<CosmosPostOptions, FrameSwitch>;

/**
 * The WebGL renderer's finishing, as its composer has it: a render pass, UnrealBloomPass, the cinema optics pass and
 * OutputPass with ACES, with the canvas's own antialias off and no anti-aliasing pass. Nothing reads this to draw;
 * creditChanges.test.ts holds it to those lines.
 */
export const WEBGL_FRAME: Readonly<Switches> = { aa: 'none', bloom: true, cinemaOptics: true };

/** The words for each switch's step when it is on [proposed], null when it is off. */
export const FRAME_STEP_WORDS: { readonly [K in FrameSwitch]: (value: Switches[K]) => string | null } = {
  aa: (mode) => (mode === 'none' ? null : `smoothed along its high-contrast edges (${mode.toUpperCase()} anti-aliasing)`),
  bloom: (on) => (on ? 'bloomed' : null),
  cinemaOptics: (on) =>
    on ? 'given the camera optics (colour fringes toward the edges, a horizontal streak from bright light and a vignette)' : null,
};

/** The steps in the chains' order; tone mapping is no switch, as both renderers always tone-map. */
const ORDER: readonly (FrameSwitch | 'toneMap')[] = ['bloom', 'cinemaOptics', 'toneMap', 'aa'];

/**
 * Each step either renderer applies to the whole frame, in the chains' order, said once: plainly when both apply it,
 * with its renderer when one does.
 */
export function frameFinishingWords(
  webgpu: Readonly<Switches> = DEFAULT_COSMOS_POST,
  webgl: Readonly<Switches> = WEBGL_FRAME
): string[] {
  const words = <K extends FrameSwitch>(key: K): string | null => {
    const word = FRAME_STEP_WORDS[key] as (value: Switches[K]) => string | null;
    const gpu = word(webgpu[key]);
    const gl = word(webgl[key]);
    if (gpu && gl) return gpu === gl ? gpu : `${gl} in the WebGL renderer and ${gpu} in the WebGPU renderer`;
    if (gpu) return `${gpu} in the WebGPU renderer only`;
    return gl ? `${gl} in the WebGL renderer only` : null;
  };
  return ORDER.map((key) => (key === 'toneMap' ? 'tone-mapped' : words(key))).filter((w): w is string => w !== null);
}

/** A list said as English says it: commas, and "and" before the last. */
export function saidAsList(items: readonly string[]): string {
  return items.length <= 1 ? (items[0] ?? '') : `${items.slice(0, -1).join(', ')} and ${items.at(-1)}`;
}
