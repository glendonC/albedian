import { describe, expect, it } from 'vitest';
import { CRAFTS } from '../crafts.js';
import { CRAFT_CHOICES, pilotReadout, threeFigureYears } from '../pilotReadout.js';

const LY_PER_PC = 3.261563777;

describe('threeFigureYears: years to three figures, million and billion in words (D26 item 4)', () => {
  it('writes the record\'s example and its neighbours', () => {
    expect(threeFigureYears(5_679_000)).toEqual({ value: '5.68 million', unit: 'years' });
    expect(threeFigureYears(3_786_000)).toEqual({ value: '3.79 million', unit: 'years' });
    expect(threeFigureYears(1_234_000_000)).toEqual({ value: '1.23 billion', unit: 'years' });
    expect(threeFigureYears(1e9)).toEqual({ value: '1.00 billion', unit: 'years' });
    expect(threeFigureYears(1e6)).toEqual({ value: '1.00 million', unit: 'years' });
    expect(threeFigureYears(456_789)).toEqual({ value: '457,000', unit: 'years' });
    expect(threeFigureYears(25.21)).toEqual({ value: '25.2', unit: 'years' });
    expect(threeFigureYears(1)).toEqual({ value: '1.00', unit: 'year' });
    // Rounding up into the next word
    expect(threeFigureYears(999_600)).toEqual({ value: '1.00 million', unit: 'years' });
  });
});

describe('pilotReadout: the craft\'s real trip under the two distances (D26 items 4 and 5)', () => {
  it('gives Voyager 1\'s real trip to the star, its speed and source, and the flight\'s own pace', () => {
    const r = pilotReadout(CRAFTS['voyager-1'], 'Acrux', 322 / LY_PER_PC, 1.5);
    expect(r.name).toBe('Voyager 1');
    expect(r.trip).toEqual({ value: '5.68 million', unit: 'years' });
    expect(r.lines).toEqual([
      'to Acrux at its real speed, 17.0 km/s',
      'Shown here in 1.5 s: about 3.79 million years each second.',
      'Speed: NASA, 17.0 km/s relative to the Sun (August 2024).',
    ]);
    expect(r.none).toBeNull();
  });

  it('leaves out the pace when the flight did not travel (reduced motion), never dividing by nothing', () => {
    const r = pilotReadout(CRAFTS['voyager-1'], 'Acrux', 322 / LY_PER_PC, null);
    expect(r.lines).toEqual(['to Acrux at its real speed, 17.0 km/s', 'Speed: NASA, 17.0 km/s relative to the Sun (August 2024).']);
    expect(pilotReadout(CRAFTS['voyager-1'], 'Acrux', 322 / LY_PER_PC, 0).lines).toHaveLength(2);
  });

  it('shows no number for a craft that never travels between stars, in the record\'s words', () => {
    const webb = pilotReadout(CRAFTS.jwst, 'Acrux', 98.7, 1.5);
    expect(webb.name).toBe('James Webb Space Telescope');
    expect(webb.trip).toBeNull();
    expect(webb.lines).toEqual([]);
    expect(webb.none).toBe('No real travel time: Webb stays near Earth, at the Sun-Earth L2 point.');
    const suit = pilotReadout(CRAFTS.astronaut, 'Acrux', 98.7, 1.5);
    expect(suit.name).toBe('Astronaut');
    expect(suit.none).toBe('No real travel time: a spacesuit is worn near a spacecraft, never flown between stars.');
  });
});

describe('CRAFT_CHOICES: the picker\'s rows (D26 item 3)', () => {
  it('starts with your view, then each NASA craft with its speed or near Earth', () => {
    expect(CRAFT_CHOICES.map((c) => [c.id, c.name, c.detail])).toEqual([
      [null, 'Your view', 'no craft'],
      ['voyager-1', 'Voyager 1', '17.0 km/s'],
      ['jwst', 'James Webb Space Telescope', 'near Earth'],
      ['astronaut', 'Astronaut', 'near Earth'],
    ]);
  });
});
