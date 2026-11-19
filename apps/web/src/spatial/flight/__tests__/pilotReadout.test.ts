import { describe, expect, it } from 'vitest';
import { CRAFTS, HANGAR_CRAFTS } from '../crafts.js';
import { CRAFT_CHOICES, pilotReadout, threeFigureYears, CRAFT_GROUPS, craftSpeedSource, craftGroups } from '../pilotReadout.js';

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
    const r = pilotReadout(CRAFTS['voyager-1']!, 'Acrux', 322 / LY_PER_PC, 1.5);
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
    const r = pilotReadout(CRAFTS['voyager-1']!, 'Acrux', 322 / LY_PER_PC, null);
    expect(r.lines).toEqual(['to Acrux at its real speed, 17.0 km/s', 'Speed: NASA, 17.0 km/s relative to the Sun (August 2024).']);
    expect(pilotReadout(CRAFTS['voyager-1']!, 'Acrux', 322 / LY_PER_PC, 0).lines).toHaveLength(2);
  });

  it('shows no number for a craft that never travels between stars, in the record\'s words', () => {
    const webb = pilotReadout(CRAFTS.jwst!, 'Acrux', 98.7, 1.5);
    expect(webb.name).toBe('James Webb Space Telescope');
    expect(webb.trip).toBeNull();
    expect(webb.lines).toEqual([]);
    expect(webb.none).toBe('No real travel time: Webb stays near Earth, at the Sun-Earth L2 point.');
    const suit = pilotReadout(CRAFTS['astronaut-emu']!, 'Acrux', 98.7, 1.5);
    expect(suit.name).toBe('Extravehicular Mobility Unit');
    expect(suit.none).toBe('No real travel time: a spacesuit is worn near a spacecraft, never flown between stars.');
    // D35 item 5: every suit takes the astronaut's sentence; near-Earth craft and the Lunar Module their own
    expect(pilotReadout(CRAFTS.z2!, 'Acrux', 98.7, 1.5).none).toBe('No real travel time: a spacesuit is worn near a spacecraft, never flown between stars.');
    expect(pilotReadout(CRAFTS.hubble!, 'Acrux', 98.7, 1.5).none).toBe('No real travel time: Hubble Space Telescope stays near Earth.');
    expect(pilotReadout(CRAFTS.iss!, 'Acrux', 98.7, 1.5).none).toBe('No real travel time: International Space Station stays near Earth.');
    expect(pilotReadout(CRAFTS['apollo-lm']!, 'Acrux', 98.7, 1.5).none).toBe('No real travel time: the Lunar Module flew between Earth and the Moon.');
  });

  it('keeps Parker Solar Probe, which orbits the Sun, from any trip, with D35\'s place and sentence (its hangar entry)', () => {
    const parker = CRAFTS['parker-solar-probe']!;
    expect(parker.speed).toBeNull();
    expect(pilotReadout(parker, 'Acrux', 98.7, 1.5).none).toBe('No real travel time: Parker Solar Probe orbits the Sun; 194 km/s is its speed at its closest pass.');
    expect(craftGroups([parker])[0]!.choices.map((c) => c.detail)).toEqual(['around the Sun']);
  });

  it('names the speed\'s source with its date, and with none where NASA\'s page gives none', () => {
    expect(craftSpeedSource(CRAFTS['voyager-2']!)).toBe('Speed: NASA, 15.4 km/s relative to the Sun (November 2017).');
    expect(craftSpeedSource(CRAFTS['new-horizons']!)).toBe('Speed: NASA, 15.3 km/s relative to the Sun.');
    expect(craftSpeedSource(CRAFTS.gemini!)).toBeNull();
  });
});

describe('CRAFT_CHOICES: the picker\'s rows (D26 item 3, D35 items 1 and 2)', () => {
  it('starts with your view, then the craft leaving the Solar System fastest first, the other craft by name, then the suits in the hangar\'s order', () => {
    // D35's rules over whatever the hangar holds (ASSETS adds entries): your view first; under Spacecraft the craft
    // that travel, fastest first, then the others by name; under Spacesuits the suits in the hangar's order
    const rows = CRAFT_CHOICES.map((c) => [c.id, c.name, c.detail] as const);
    expect(rows[0]).toEqual([null, 'Your view', 'no craft']);
    expect(rows.slice(1, 5)).toEqual([
      ['voyager-1', 'Voyager 1', '17.0 km/s'],
      ['voyager-2', 'Voyager 2', '15.4 km/s'],
      ['new-horizons', 'New Horizons', '15.3 km/s'],
      ['pioneer-10', 'Pioneer 10', '12.2 km/s'],
    ]);
    const [craft, suits] = CRAFT_GROUPS;
    const travelling = craft!.choices.filter((c) => CRAFTS[c.id!]!.speed);
    const others = craft!.choices.filter((c) => !CRAFTS[c.id!]!.speed);
    expect(craft!.choices).toEqual([...travelling, ...others]);
    expect(others.map((c) => c.name)).toEqual([...others.map((c) => c.name)].sort((x, y) => x.localeCompare(y, 'en')));
    expect(suits!.choices.map((c) => c.id)).toEqual(HANGAR_CRAFTS.filter((c) => c.kind === 'suit').map((c) => c.id));
    expect(suits!.choices.every((c) => c.detail === '')).toBe(true);
    expect(CRAFT_CHOICES).toHaveLength(1 + HANGAR_CRAFTS.length);
    // D35 item 2's places for the craft it names
    const detail = (id: string) => CRAFT_CHOICES.find((c) => c.id === id)!.detail;
    expect(['jwst', 'hubble', 'iss', 'shuttle', 'gemini'].map(detail)).toEqual(['near Earth', 'near Earth', 'near Earth', 'near Earth', 'near Earth']);
    expect([detail('apollo-lm'), detail('parker-solar-probe')]).toEqual(['to the Moon', 'around the Sun']);

  });
});
