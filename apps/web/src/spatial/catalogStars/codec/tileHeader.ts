import {
  CATALOG_STAR_TILE_ENCODING_V1 as ENC,
  CATALOG_STAR_TILE_HEADER_VERSION,
} from '@albedian/contracts';
import type { CatalogStarTileHeader, ExpectedTileCoordinates } from './tileTypes.js';

const LITTLE_ENDIAN = new Uint8Array(new Uint16Array([1]).buffer)[0] === 1;

const HEADER_MAGIC_BYTES = 4;
const HEADER_VERSION_OFFSET = 4;
const HEADER_KIND_OFFSET = 6;
const HEADER_POSITION_BITS_OFFSET = 7;
const HEADER_LAYER_OFFSET = 8;
const HEADER_ORDER_OFFSET = 9;
const HEADER_PIX_OFFSET = 12;
const HEADER_COUNT_OFFSET = 16;

export function readTileHeader(buffer: ArrayBuffer): CatalogStarTileHeader {
  if (buffer.byteLength < ENC.headerBytes) {
    throw new Error('catalog star tile is shorter than its header');
  }
  const bytes = new Uint8Array(buffer, 0, HEADER_MAGIC_BYTES);
  const magic = String.fromCharCode(bytes[0] ?? 0, bytes[1] ?? 0, bytes[2] ?? 0, bytes[3] ?? 0);
  if (magic !== ENC.magic) {
    throw new Error('not an Albedian catalog star tile');
  }
  const view = new DataView(buffer);
  const version = view.getUint16(HEADER_VERSION_OFFSET, true);
  if (version !== CATALOG_STAR_TILE_HEADER_VERSION) {
    throw new Error(`unsupported catalog star tile header version ${version}`);
  }
  return {
    kind: view.getUint8(HEADER_KIND_OFFSET),
    positionBits: view.getUint8(HEADER_POSITION_BITS_OFFSET),
    layer: view.getUint8(HEADER_LAYER_OFFSET),
    order: view.getUint8(HEADER_ORDER_OFFSET),
    pix: view.getUint32(HEADER_PIX_OFFSET, true),
    count: view.getUint32(HEADER_COUNT_OFFSET, true),
  };
}

export function expectTile(
  header: CatalogStarTileHeader,
  kind: number,
  expected?: ExpectedTileCoordinates
): void {
  if (!LITTLE_ENDIAN) {
    throw new Error('catalog star tiles are little-endian; this platform is not');
  }
  if (header.kind !== kind) {
    throw new Error(`expected tile kind ${kind}, found ${header.kind}`);
  }
  if (
    expected &&
    (header.layer !== expected.layer || header.order !== expected.order || header.pix !== expected.pix)
  ) {
    throw new Error(
      `tile header L${header.layer}/o${header.order}/${header.pix} does not match request L${expected.layer}/o${expected.order}/${expected.pix}`
    );
  }
}

export function expectTileByteLength(
  actualByteLength: number,
  starCount: number,
  bytesPerStar: number,
  kindLabel: 'core' | 'detail' | 'astro'
): void {
  if (actualByteLength !== ENC.headerBytes + starCount * bytesPerStar) {
    throw new Error(`${kindLabel} tile length does not match its header`);
  }
}

type TypedColumnArray = Uint8Array | Uint16Array | Int16Array | Uint32Array;

interface TypedColumnConstructor<T extends TypedColumnArray> {
  readonly BYTES_PER_ELEMENT: number;
  new (buffer: ArrayBuffer, byteOffset: number, length: number): T;
}

/** Sequential structure-of-arrays TypedArray column cursor starting past the 32-byte tile header. */
export class TileColumnReader {
  private offset: number;
  private readonly buffer: ArrayBuffer;
  private readonly count: number;

  constructor(buffer: ArrayBuffer, count: number, initialOffset = ENC.headerBytes) {
    this.buffer = buffer;
    this.count = count;
    this.offset = initialOffset;
  }

  take<T extends TypedColumnArray>(View: TypedColumnConstructor<T>): T {
    const view = new View(this.buffer, this.offset, this.count);
    this.offset += View.BYTES_PER_ELEMENT * this.count;
    return view;
  }

  decodeFloat32Column<T extends Uint16Array | Int16Array>(
    View: TypedColumnConstructor<T>,
    decode: (code: number) => number
  ): Float32Array {
    const codes = this.take(View);
    const n = this.count;
    const out = new Float32Array(n);
    for (let i = 0; i < n; i++) out[i] = decode(codes[i] ?? 0);
    return out;
  }
}
