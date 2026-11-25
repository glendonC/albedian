/** A number as a GLSL float literal: with a decimal point, or in exponent form where JavaScript writes one. */
export function glslFloat(value: number): string {
  if (!Number.isFinite(value)) throw new RangeError(`${value} is not a finite number, so it has no GLSL float`);
  const text = String(value);
  return /[.e]/.test(text) ? text : `${text}.0`;
}
