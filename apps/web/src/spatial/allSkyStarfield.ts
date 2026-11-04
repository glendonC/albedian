import { raDecToUnitVector } from '@albedian/camera-rig';

export interface BackgroundStar3D {
  id: string;
  name?: string;
  constellation?: string;
  raDeg: number;
  decDeg: number;
  mag: number;
  bpRp: number;
  distancePc: number;
  cartesianParsec: [number, number, number];
  pmRaMasYr: number;
  pmDecMasYr: number;
  colorHex: string;
}

export interface ConstellationEdgeDef {
  constellation: string;
  fromId: string;
  toId: string;
}

/**
 * Convert Gaia BP-RP color index to authentic photometric blackbody hex color.
 */
export function bpRpToStarColorHex(bpRp: number): string {
  if (bpRp < -0.15) return '#9BB8FF'; // Hot O/B blue-white (Rigel, Spica, Bellatrix)
  if (bpRp < 0.15) return '#CAD8FF'; // B/A white-blue (Sirius, Vega, Fomalhaut, Deneb)
  if (bpRp < 0.45) return '#F4F6FF'; // A/F white (Altair, Procyon, Deneb Algedi)
  if (bpRp < 0.85) return '#FFF4EA'; // F/G yellow-white (Capella, Polaris, Sadalsuud)
  if (bpRp < 1.35) return '#FFD6A5'; // K orange giant (Aldebaran, Arcturus, Pollux, Dubhe)
  return '#FF9E6D'; // M red supergiant (Betelgeuse, Antares)
}

function makeNamedStar(
  id: string,
  name: string,
  constellation: string,
  raDeg: number,
  decDeg: number,
  mag: number,
  bpRp: number,
  distancePc: number,
  pmRaMasYr = 0,
  pmDecMasYr = 0
): BackgroundStar3D {
  const u = raDecToUnitVector(raDeg, decDeg);
  return {
    id,
    name,
    constellation,
    raDeg,
    decDeg,
    mag,
    bpRp,
    distancePc,
    cartesianParsec: [u[0] * distancePc, u[1] * distancePc, u[2] * distancePc],
    pmRaMasYr,
    pmDecMasYr,
    colorHex: bpRpToStarColorHex(bpRp),
  };
}

/**
 * Curated all-sky navigational & constellation stars (65 real Hipparcos / Gaia DR3 stars)
 * spanning Orion, Taurus, Auriga, Gemini, Canis Major, Capricornus, Aquarius,
 * Piscis Austrinus, Pegasus, Andromeda, Cassiopeia, Cygnus, Lyra, Aquila,
 * Ursa Major, Ursa Minor, Cetus, Aries, Scorpius, Leo, Boötes, and Virgo.
 */
export const NAMED_ALL_SKY_STARS_3D: BackgroundStar3D[] = [
  // Orion
  makeNamedStar('bg_betelgeuse', 'Betelgeuse', 'Orion', 88.7929, 7.4071, 0.42, 1.85, 168.0, 27.54, 11.3),
  makeNamedStar('bg_rigel', 'Rigel', 'Orion', 78.6345, -8.2016, 0.13, -0.03, 264.0, 1.31, 0.5),
  makeNamedStar('bg_bellatrix', 'Bellatrix', 'Orion', 81.2828, 6.3497, 1.64, -0.22, 77.4, -8.11, -12.88),
  makeNamedStar('bg_alnilam', 'Alnilam', 'Orion', 84.0534, -1.2019, 1.69, -0.18, 412.0, 1.44, -0.78),
  makeNamedStar('bg_alnitak', 'Alnitak', 'Orion', 85.1897, -1.9426, 1.77, -0.20, 226.0, 3.19, 2.03),
  makeNamedStar('bg_mintaka', 'Mintaka', 'Orion', 83.0017, -0.2991, 2.23, -0.21, 212.0, 0.64, -0.69),
  makeNamedStar('bg_saiph', 'Saiph', 'Orion', 86.9391, -9.6696, 2.06, -0.17, 198.0, 1.46, -1.28),
  makeNamedStar('bg_meissa', 'Meissa', 'Orion', 83.7845, 9.9342, 3.39, -0.24, 337.0, -0.2, -2.1),
  // Taurus & Auriga & Gemini & Canis Major/Minor
  makeNamedStar('bg_aldebaran', 'Aldebaran', 'Taurus', 68.9802, 16.5093, 0.86, 1.54, 20.4, 63.45, -188.94),
  makeNamedStar('bg_elnath', 'Elnath', 'Taurus', 81.573, 28.6075, 1.65, -0.13, 41.0, 22.76, -173.58),
  makeNamedStar('bg_tianguan', 'Tianguan', 'Taurus', 84.4112, 21.1425, 3.01, -0.16, 136.0, 1.22, -23.51),
  makeNamedStar('bg_capella', 'Capella', 'Auriga', 79.1723, 45.998, 0.08, 0.80, 13.1, 75.25, -426.89),
  makeNamedStar('bg_menkalinan', 'Menkalinan', 'Auriga', 89.8822, 44.9474, 1.90, 0.08, 24.9, -56.4, -0.9),
  makeNamedStar('bg_pollux', 'Pollux', 'Gemini', 116.329, 28.0262, 1.14, 1.00, 10.4, -626.55, -45.8),
  makeNamedStar('bg_castor', 'Castor', 'Gemini', 113.6494, 31.8883, 1.58, 0.03, 15.6, -191.45, -145.19),
  makeNamedStar('bg_alhena', 'Alhena', 'Gemini', 99.428, 16.3993, 1.93, 0.0, 33.5, -2.0, -66.9),
  makeNamedStar('bg_sirius', 'Sirius', 'Canis Major', 101.2872, -16.7161, -1.46, 0.0, 2.64, -546.01, -1223.07),
  makeNamedStar('bg_procyon', 'Procyon', 'Canis Minor', 114.8255, 5.225, 0.34, 0.42, 3.51, -714.59, -1036.8),
  // Southwest Morocco Sky: Capricornus, Aquarius, Piscis Austrinus, Cetus
  makeNamedStar('bg_deneb_algedi', 'Deneb Algedi', 'Capricornus', 326.7602, -16.1273, 2.85, 0.38, 11.9, 261.71, -296.68),
  makeNamedStar('bg_dabih', 'Dabih', 'Capricornus', 305.2528, -14.7815, 3.05, 0.92, 101.2, 48.36, 14.02),
  makeNamedStar('bg_algedi', 'Algedi', 'Capricornus', 304.5136, -12.5085, 3.58, 0.94, 33.4, 62.6, 2.4),
  makeNamedStar('bg_nashira', 'Nashira', 'Capricornus', 325.0227, -16.6624, 3.69, 0.32, 43.1, 187.6, -22.3),
  makeNamedStar('bg_sadalsuud', 'Sadalsuud', 'Aquarius', 322.8897, -5.5712, 2.87, 0.98, 164.7, 22.79, -6.21),
  makeNamedStar('bg_sadalmelik', 'Sadalmelik', 'Aquarius', 331.446, -0.3199, 2.94, 1.02, 167.8, 18.27, -9.39),
  makeNamedStar('bg_skat', 'Skat', 'Aquarius', 343.6632, -15.8208, 3.27, 0.08, 49.0, -43.2, -30.2),
  makeNamedStar('bg_fomalhaut', 'Fomalhaut', 'Piscis Austrinus', 344.4127, -29.6222, 1.16, 0.09, 7.7, 328.95, -164.67),
  makeNamedStar('bg_diphda', 'Diphda', 'Cetus', 10.8974, -17.9866, 2.02, 1.02, 29.5, 232.55, 31.99),
  makeNamedStar('bg_menkar', 'Menkar', 'Cetus', 45.5699, 4.0897, 2.54, 1.63, 76.4, -10.4, -76.8),
  // Pegasus, Andromeda, Perseus, Aries
  makeNamedStar('bg_enif', 'Enif', 'Pegasus', 326.0465, 9.875, 2.38, 1.52, 211.4, 26.92, 0.44),
  makeNamedStar('bg_markab', 'Markab', 'Pegasus', 346.1902, 15.2053, 2.48, -0.04, 40.9, 60.4, -41.3),
  makeNamedStar('bg_scheat', 'Scheat', 'Pegasus', 345.9436, 28.0828, 2.42, 1.67, 60.0, 187.6, 136.9),
  makeNamedStar('bg_algenib', 'Algenib', 'Pegasus', 3.309, 15.1836, 2.84, -0.19, 120.0, 4.7, -9.3),
  makeNamedStar('bg_alpheratz', 'Alpheratz', 'Andromeda', 2.0969, 29.0904, 2.06, -0.07, 29.7, 137.46, -163.44),
  makeNamedStar('bg_mirach', 'Mirach', 'Andromeda', 17.433, 35.6206, 2.07, 1.58, 60.5, 175.9, -112.2),
  makeNamedStar('bg_almach', 'Almach', 'Andromeda', 30.9748, 42.3297, 2.1, 1.2, 109.0, 43.1, -50.9),
  makeNamedStar('bg_mirfak', 'Mirfak', 'Perseus', 51.0807, 49.8612, 1.79, 0.48, 155.0, 23.75, -26.23),
  makeNamedStar('bg_algol', 'Algol', 'Perseus', 47.0422, 40.9556, 2.12, -0.05, 27.6, 2.99, -1.66),
  makeNamedStar('bg_hamal', 'Hamal', 'Aries', 31.7934, 23.4624, 2.0, 1.15, 20.2, 190.7, -148.1),
  makeNamedStar('bg_sheratan', 'Sheratan', 'Aries', 28.66, 20.808, 2.64, 0.13, 18.0, 99.4, -110.3),
  // Summer Triangle: Aquila, Lyra, Cygnus
  makeNamedStar('bg_altair', 'Altair', 'Aquila', 297.6958, 8.8683, 0.76, 0.22, 5.13, 536.23, 385.29),
  makeNamedStar('bg_tarazed', 'Tarazed', 'Aquila', 296.5649, 10.6133, 2.72, 1.52, 121.0, 16.0, -2.0),
  makeNamedStar('bg_alshain', 'Alshain', 'Aquila', 298.8283, 6.4068, 3.71, 0.86, 13.7, 45.4, -481.9),
  makeNamedStar('bg_vega', 'Vega', 'Lyra', 279.2347, 38.7837, 0.03, 0.0, 7.68, 200.94, 286.23),
  makeNamedStar('bg_deneb', 'Deneb', 'Cygnus', 310.358, 45.2803, 1.25, 0.09, 802.0, 2.01, 1.85),
  makeNamedStar('bg_sadr', 'Sadr', 'Cygnus', 305.5571, 40.2567, 2.23, 0.67, 560.0, 2.4, -0.9),
  makeNamedStar('bg_albireo', 'Albireo', 'Cygnus', 292.6803, 27.9597, 3.05, 1.13, 133.0, -7.2, -6.2),
  // Cassiopeia & Ursa Minor & Ursa Major
  makeNamedStar('bg_schedar', 'Schedar', 'Cassiopeia', 10.1268, 56.5373, 2.24, 1.17, 70.0, 50.4, -32.1),
  makeNamedStar('bg_caph', 'Caph', 'Cassiopeia', 2.2945, 59.1498, 2.28, 0.34, 16.8, 523.5, -179.8),
  makeNamedStar('bg_gamma_cas', 'Navi', 'Cassiopeia', 14.1772, 60.7167, 2.47, -0.15, 168.0, 25.2, -3.8),
  makeNamedStar('bg_ruchbah', 'Ruchbah', 'Cassiopeia', 21.454, 60.2353, 2.68, 0.16, 30.5, 296.5, -49.6),
  makeNamedStar('bg_segin', 'Segin', 'Cassiopeia', 28.5988, 63.6701, 3.35, -0.15, 135.0, 31.9, -18.7),
  makeNamedStar('bg_polaris', 'Polaris', 'Ursa Minor', 37.9546, 89.2641, 1.98, 0.6, 133.0, 44.48, -11.85),
  makeNamedStar('bg_kochab', 'Kochab', 'Ursa Minor', 222.6764, 74.1555, 2.07, 1.47, 40.1, -32.6, 11.4),
  makeNamedStar('bg_dubhe', 'Dubhe', 'Ursa Major', 165.932, 61.751, 1.79, 1.07, 37.7, -134.1, -34.7),
  makeNamedStar('bg_merak', 'Merak', 'Ursa Major', 165.4603, 56.3824, 2.37, 0.03, 24.4, 81.4, 33.5),
  makeNamedStar('bg_alioth', 'Alioth', 'Ursa Major', 193.5073, 55.9598, 1.76, 0.02, 25.3, 111.7, -9.0),
  makeNamedStar('bg_mizar', 'Mizar', 'Ursa Major', 200.9814, 54.9254, 2.23, 0.06, 26.3, 119.0, -26.0),
  makeNamedStar('bg_alkaid', 'Alkaid', 'Ursa Major', 206.8852, 49.3133, 1.85, -0.18, 31.9, -121.2, -14.9),
  // Bright Seasonal Anchors
  makeNamedStar('bg_antares', 'Antares', 'Scorpius', 247.3519, -26.432, 0.96, 1.83, 170.0, -12.11, -23.3),
  makeNamedStar('bg_arcturus', 'Arcturus', 'Bootes', 213.9153, 19.1824, -0.05, 1.23, 11.26, -1093.45, -1999.4),
  makeNamedStar('bg_spica', 'Spica', 'Virgo', 201.2982, -11.1613, 0.97, -0.23, 77.0, -42.35, -30.67),
  makeNamedStar('bg_regulus', 'Regulus', 'Leo', 152.093, 11.9672, 1.36, -0.11, 24.3, -248.73, 5.59),
];

export const ALL_SKY_CONSTELLATION_EDGES: ConstellationEdgeDef[] = [
  // Orion
  { constellation: 'Orion', fromId: 'bg_betelgeuse', toId: 'bg_bellatrix' },
  { constellation: 'Orion', fromId: 'bg_betelgeuse', toId: 'bg_alnitak' },
  { constellation: 'Orion', fromId: 'bg_bellatrix', toId: 'bg_mintaka' },
  { constellation: 'Orion', fromId: 'bg_alnitak', toId: 'bg_alnilam' },
  { constellation: 'Orion', fromId: 'bg_alnilam', toId: 'bg_mintaka' },
  { constellation: 'Orion', fromId: 'bg_alnitak', toId: 'bg_saiph' },
  { constellation: 'Orion', fromId: 'bg_mintaka', toId: 'bg_rigel' },
  { constellation: 'Orion', fromId: 'bg_saiph', toId: 'bg_rigel' },
  { constellation: 'Orion', fromId: 'bg_meissa', toId: 'bg_betelgeuse' },
  { constellation: 'Orion', fromId: 'bg_meissa', toId: 'bg_bellatrix' },
  // Taurus
  { constellation: 'Taurus', fromId: 'bg_aldebaran', toId: 'bg_elnath' },
  { constellation: 'Taurus', fromId: 'bg_aldebaran', toId: 'bg_tianguan' },
  // Capricornus (Southwest Morocco sky right beside the Moon!)
  { constellation: 'Capricornus', fromId: 'bg_algedi', toId: 'bg_dabih' },
  { constellation: 'Capricornus', fromId: 'bg_dabih', toId: 'bg_nashira' },
  { constellation: 'Capricornus', fromId: 'bg_nashira', toId: 'bg_deneb_algedi' },
  { constellation: 'Capricornus', fromId: 'bg_algedi', toId: 'bg_deneb_algedi' },
  // Aquarius
  { constellation: 'Aquarius', fromId: 'bg_sadalsuud', toId: 'bg_sadalmelik' },
  { constellation: 'Aquarius', fromId: 'bg_sadalsuud', toId: 'bg_skat' },
  // Pegasus Great Square & Andromeda
  { constellation: 'Pegasus', fromId: 'bg_markab', toId: 'bg_scheat' },
  { constellation: 'Pegasus', fromId: 'bg_scheat', toId: 'bg_alpheratz' },
  { constellation: 'Pegasus', fromId: 'bg_alpheratz', toId: 'bg_algenib' },
  { constellation: 'Pegasus', fromId: 'bg_algenib', toId: 'bg_markab' },
  { constellation: 'Pegasus', fromId: 'bg_markab', toId: 'bg_enif' },
  { constellation: 'Andromeda', fromId: 'bg_alpheratz', toId: 'bg_mirach' },
  { constellation: 'Andromeda', fromId: 'bg_mirach', toId: 'bg_almach' },
  // Cassiopeia W
  { constellation: 'Cassiopeia', fromId: 'bg_caph', toId: 'bg_schedar' },
  { constellation: 'Cassiopeia', fromId: 'bg_schedar', toId: 'bg_gamma_cas' },
  { constellation: 'Cassiopeia', fromId: 'bg_gamma_cas', toId: 'bg_ruchbah' },
  { constellation: 'Cassiopeia', fromId: 'bg_ruchbah', toId: 'bg_segin' },
  // Cygnus Northern Cross & Summer Triangle
  { constellation: 'Cygnus', fromId: 'bg_deneb', toId: 'bg_sadr' },
  { constellation: 'Cygnus', fromId: 'bg_sadr', toId: 'bg_albireo' },
  { constellation: 'Aquila', fromId: 'bg_tarazed', toId: 'bg_altair' },
  { constellation: 'Aquila', fromId: 'bg_altair', toId: 'bg_alshain' },
  // Gemini
  { constellation: 'Gemini', fromId: 'bg_castor', toId: 'bg_pollux' },
  { constellation: 'Gemini', fromId: 'bg_pollux', toId: 'bg_alhena' },
];
