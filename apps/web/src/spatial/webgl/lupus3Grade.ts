/**
 * How the black-hole scene grades ESO's Lupus 3 photograph (eso1804a) where an escaping ray samples it, in one place:
 * deepSkyRegime.ts writes those lines of its shader from these numbers, and the Credits page words them
 * (lupus3Changes), so the words cannot outlive the treatment. No three.js here: the Credits page reads it.
 */
export interface Lupus3Grade {
  /** Taken from each channel first, so the photograph's dark sky goes to black. */
  blackLevel: number;
  /** The power each channel is then raised to. */
  power: number;
  /** The brightness kept after the power. */
  gain: number;
  /** The hand-set brightening near the photon sphere: up to 1 + ringBoost times, falling away from it. */
  ringBoost: number;
}

export const LUPUS3_GRADE: Readonly<Lupus3Grade> = {
  blackLevel: 0.08,
  power: 1.9,
  gain: 0.32,
  ringBoost: 1.85,
};

/**
 * Where the black-hole scene places the photograph: the view is rolled by rollRad and tilted by tiltRad (the modelled
 * disk's tilt) before the rays are marched, so the photograph they sample is turned with it. deepSkyRegime.ts writes
 * its shader from these numbers and the Credits page words them (lupus3SceneWords).
 */
export const LUPUS3_VIEW = { rollRad: 0.1, tiltRad: 0.165 } as const;

/** A number as a GLSL float literal: with a decimal point, or in exponent form where JavaScript writes one. */
export function glslFloat(value: number): string {
  if (!Number.isFinite(value)) throw new RangeError(`${value} is not a finite number, so it has no GLSL float`);
  const text = String(value);
  return /[.e]/.test(text) ? text : `${text}.0`;
}

/**
 * What the black-hole scene does to the photograph, as the Credits page says it [proposed]: worded from the shader in
 * deepSkyRegime.ts, its numbers read from the grade. creditChanges.test.ts ties each phrase to the shader's line.
 */
export function lupus3Changes(grade: Readonly<Lupus3Grade> = LUPUS3_GRADE): string {
  const most = Math.round((1 + grade.ringBoost) * 1000) / 1000;
  return (
    "In the 3D view's black-hole scene it is stretched around the whole sky, bent around the modelled black hole and " +
    `graded (black level ${grade.blackLevel}, power ${grade.power}, at ${grade.gain} of its brightness and up to ${most} ` +
    'times that near the photon sphere), then dimmed behind the modelled disk, tone-mapped, vignetted and blended over ' +
    'the sky.'
  );
}

/**
 * The light the black-hole scene adds over the photograph and where it places it, said once [proposed]: a glow around
 * the photon sphere and a horizontal flare, added before the tone map; the roll and the disk's tilt, in whole degrees.
 */
export function lupus3SceneWords(view: { rollRad: number; tiltRad: number } = LUPUS3_VIEW): string {
  const degrees = (rad: number) => Math.round((rad * 180) / Math.PI);
  return (
    'Over it the scene adds light of its own, a glow around the photon sphere and a horizontal flare, and it places ' +
    `the photograph turned by about ${degrees(view.rollRad)} degrees and tilted by about ${degrees(view.tiltRad)}.`
  );
}
