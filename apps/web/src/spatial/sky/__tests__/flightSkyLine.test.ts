import { describe, expect, it } from 'vitest';
import { flightSkyLineOf } from '../flightMilkyWay.js';

// flight-sky-line-state-one (DECIDED-BY-LEAD 21:56): a layer that is drawn has its line, in every state of the flight.

const at = (over: Partial<Parameters<typeof flightSkyLineOf>[0]>) => flightSkyLineOf({ steering: false, glowDrawn: false, domeDrawn: false, photographDrawn: false, glowFailed: false, ...over });

describe('flightSkyLineOf', () => {
  it('names the glow once it has drawn, even while the photograph still lies under it', () => {
    expect(at({ glowDrawn: true })).toBe('glow');
    expect(at({ glowDrawn: true, photographDrawn: true })).toBe('glow');
  });
  it('names the photograph while it shows and the glow has not come', () => {
    expect(at({ photographDrawn: true })).toBe('photograph');
  });
  it('names nothing when the glow failed, the photograph having left, nor while nothing has drawn', () => {
    expect(at({ photographDrawn: true, glowFailed: true })).toBeNull();
    expect(at({})).toBeNull();
  });
  it('while steering names the glow once the dome has drawn, and nothing before, whatever lies under', () => {
    expect(at({ steering: true, domeDrawn: true })).toBe('glow');
    expect(at({ steering: true, photographDrawn: true, glowDrawn: true })).toBeNull();
  });
});
