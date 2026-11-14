import {
  SKY_PANORAMA_REGISTRATION_FORMAT,
  SKY_PANORAMA_REGISTRATION_URL,
  type Mat3Rows,
  type PanoramaRegistration,
} from './types.js';

const EXPECTED_FORMAT_VERSION = 1;
const MAT3_ENTRY_COUNT = 9;
const CHANNELS_PER_OFFSET_TEXEL = 2;

function base64ToBytes(data: string): Uint8Array {
  const binary = atob(data);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function isMat3Rows(values: readonly unknown[]): values is Mat3Rows {
  return values.length === MAT3_ENTRY_COUNT && values.every(isFiniteNumber);
}

/** Reads `eso0932a_registration.json`; throws on anything malformed rather than drawing the photograph unregistered. */
export function parsePanoramaRegistration(json: unknown): PanoramaRegistration {
  const doc = json as {
    format?: unknown;
    formatVersion?: unknown;
    image?: { width?: unknown; height?: unknown };
    galacticToPanorama?: unknown;
    yOffsetPx?: unknown;
    offsetTable?: { width?: unknown; height?: unknown; scale?: unknown; bias?: unknown; data?: unknown };
  };
  if (doc?.format !== SKY_PANORAMA_REGISTRATION_FORMAT || doc.formatVersion !== EXPECTED_FORMAT_VERSION) {
    throw new Error('not a version 1 sky panorama registration');
  }
  const rows: readonly unknown[] = Array.isArray(doc.galacticToPanorama) ? doc.galacticToPanorama.flat() : [];
  const { image, offsetTable: table } = doc;
  if (
    !isMat3Rows(rows) ||
    !isFiniteNumber(image?.width) ||
    !isFiniteNumber(image?.height) ||
    !isFiniteNumber(doc.yOffsetPx) ||
    !isFiniteNumber(table?.width) ||
    !isFiniteNumber(table?.height) ||
    !isFiniteNumber(table?.scale) ||
    !isFiniteNumber(table?.bias) ||
    typeof table?.data !== 'string'
  ) {
    throw new Error('sky panorama registration is missing fields');
  }
  const codes = base64ToBytes(table.data);
  if (codes.length !== table.width * table.height * CHANNELS_PER_OFFSET_TEXEL) {
    throw new Error('sky panorama offset table has the wrong size');
  }
  return {
    imageWidth: image.width,
    imageHeight: image.height,
    galacticToPanorama: rows,
    yOffsetPx: doc.yOffsetPx,
    offsetTable: { width: table.width, height: table.height, scale: table.scale, bias: table.bias, codes },
  };
}

export async function loadPanoramaRegistration(
  url: string = SKY_PANORAMA_REGISTRATION_URL,
  fetchImpl: typeof fetch = fetch
): Promise<PanoramaRegistration> {
  const response = await fetchImpl(url);
  if (!response.ok) throw new Error(`sky panorama registration: HTTP ${response.status}`);
  return parsePanoramaRegistration(await response.json());
}
