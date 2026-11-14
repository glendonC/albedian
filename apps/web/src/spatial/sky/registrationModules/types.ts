export const SKY_PANORAMA_REGISTRATION_URL = '/textures/eso0932a_registration.json';
export const SKY_PANORAMA_REGISTRATION_FORMAT = 'albedian.sky-panorama-registration';

/** Row-major 3x3 matrix. */
export type Mat3Rows = readonly [number, number, number, number, number, number, number, number, number];

/**
 * ICRS to galactic: A_G' of the Hipparcos catalogue (ESA 1997, vol. 1, sect. 1.5.3), as given in
 * the Gaia DR3 documentation (sect. 4.1.7). Rows are the galactic centre, l = 90 deg and north
 * galactic pole directions in ICRS. `services/science/sky_registration.py` holds the same values.
 */
export const ICRS_TO_GALACTIC: Mat3Rows = [
  -0.0548755604162154, -0.873437090234885, -0.4838350155487132, 0.4941094278755837, -0.4448296299600112,
  0.7469822444972189, -0.8676661490190047, -0.1980763734312015, 0.4559837761750669,
];

export interface PanoramaOffsetTable {
  width: number;
  height: number;
  /** Codes per texel of offset; value = (code - bias) / scale. */
  scale: number;
  bias: number;
  /** Interleaved (east, north) codes per texel, row 0 at v = 0 (latitude -90). */
  codes: Uint8Array;
}

export interface PanoramaRegistration {
  imageWidth: number;
  imageHeight: number;
  galacticToPanorama: Mat3Rows;
  /** Vertical offset of the image in pixels (y down), after the rotation. */
  yOffsetPx: number;
  offsetTable: PanoramaOffsetTable;
}
