import { frameFinishingWords, saidAsList } from '../frameFinishing.js';

/**
 * How the 3D view grades ESO's Milky Way panorama (eso0932a), step by step, in one list: the WebGL sky dome
 * (webgl/skyDomeAndCinemaShader.ts) and the WebGPU sky (webgpu/cislunar/skyBackground.ts) are written from these
 * numbers, and the Credits page words every step of the list (panoramaGradeWords), so a step or a number changed here
 * changes the words. A step someone adds to either shader outside this list is not seen by the Credits test
 * (creditChanges.test.ts says so). No three.js here: the Credits page reads it.
 */
export interface PanoramaGrade {
  /** Taken from each channel first, then clamped at zero. */
  blackLevel: number;
  /** The power each channel is then raised to. */
  power: number;
  /** The warm tint each channel is then multiplied by, red, green and blue. */
  tint: readonly [number, number, number];
  /** The brightness then multiplied in. */
  gain: number;
  /** The deep blue base colour the graded panorama is then laid over (added). */
  base: readonly [number, number, number];
}

export const PANORAMA_GRADE: Readonly<PanoramaGrade> = {
  blackLevel: 0.042,
  power: 1.52,
  tint: [1.22, 1.04, 0.9],
  gain: 1.32,
  base: [0.002, 0.003, 0.008],
};

/**
 * Exposure of the WebGPU landing scene's sky: with the Moon at its true albedo the camera exposes for the sunlit bodies,
 * so the graded panorama is shown at this fraction there (webgpu/cislunar/cislunarSceneNodes.ts passes it).
 */
export const LANDING_SKY_GAIN = 0.3;

/** The words for each step of the grade [proposed], in the list's order. */
export function panoramaGradeWords(grade: Readonly<PanoramaGrade> = PANORAMA_GRADE): Record<keyof PanoramaGrade, string> {
  return {
    blackLevel: `darkened by a black level of ${grade.blackLevel}`,
    power: `raised to the power ${grade.power}`,
    tint: `tinted warm (${grade.tint.join(', ')} in red, green and blue)`,
    gain: `brightened ${grade.gain} times`,
    base: `laid over a deep blue base (${grade.base.join(', ')})`,
  };
}

/**
 * The Milky Way row's line for the 3D view [proposed]: the placement said once, every step of the grade, and then
 * every step either renderer applies to the whole finished frame (frameFinishingWords, from the post options and
 * the WebGL composer), said once to be the frame's and not this photograph's alone.
 */
export function milkyWay3dChanges(grade: Readonly<PanoramaGrade> = PANORAMA_GRADE, frame: readonly string[] = frameFinishingWords()): string {
  const w = panoramaGradeWords(grade);
  return (
    'In the 3D view it is placed on the sky through its registration, turned and moved a few pixels to meet the ' +
    `catalog stars, then ${w.blackLevel}, ${w.power}, ${w.tint}, ${w.gain} and ${w.base}; then the whole finished ` +
    `frame, not this photograph alone, is ${saidAsList(frame)}.`
  );
}

/**
 * The Milky Way row's line for what the WebGPU renderer does with the copy it draws in the photograph's place
 * [proposed]: the app's own steps only. How the copy differs from the photograph is its record's sentence, shown
 * whole beside this (creditRows.ts, recordChangeLines), with no words of the app's own.
 */
export function milkyWayCopyDrawn(gain = LANDING_SKY_GAIN): string {
  return `The 3D view's WebGPU renderer draws a copy made from it in its place, graded the same way and then scaled to ${gain}.`;
}
