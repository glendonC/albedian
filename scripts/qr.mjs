// A QR code encoder for a short text such as the laptop's phone address, written in this repository to ISO/IEC 18004
// (QR Code bar code symbology specification) so nothing is downloaded: byte mode, every version (1 to 40) and every
// error correction level, the smallest version that holds the text unless one is asked for, and the data mask the
// standard's penalty scores lowest unless one is asked for. It has no imports, so the field start (scripts/field.mjs,
// plain Node) and the web app (through Vite) draw the same code from the same text.
//
//   encodeQr('http://172.20.10.3:5173') -> { version: 2, level: 'L', mask, size: 25, modules }
//
// `modules[y][x]` is true for a dark module, row y from the top and column x from the left, with no quiet zone: the
// standard asks for four light modules all round, which the drawing adds (UIUX-phone-code-READY.md Q2 and Q3).
//
// Only printable ASCII is taken (0x20 to 0x7E), one byte a character: an address is ASCII, and every reader takes
// those bytes as themselves, where other bytes would need an ECI the readers do not all agree on.

/** The four error correction levels, from the most data to the most correction. */
export const QR_LEVELS = Object.freeze(['L', 'M', 'Q', 'H']);

// The level's two bits in the format information (L 01, M 00, Q 11, H 10)
const LEVEL_BITS = { L: 0b01, M: 0b00, Q: 0b11, H: 0b10 };

// The standard's error correction table, version by version from 1 to 40 (index 0 unused): the error correction
// codewords in each block, and the number of blocks the codewords are cut into
const EC_CODEWORDS_PER_BLOCK = {
  L: [0, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
  M: [0, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28],
  Q: [0, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
  H: [0, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
};
const EC_BLOCKS = {
  L: [0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25],
  M: [0, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49],
  Q: [0, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68],
  H: [0, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81],
};

// The penalty weights of the mask evaluation: N1 a run of five or more, N2 a two by two block, N3 a finder-like
// pattern, N4 the share of dark modules away from half
const N1 = 3;
const N2 = 3;
const N3 = 40;
const N4 = 10;
// The byte mode indicator, and the pad codewords that fill the data capacity after the terminator
const BYTE_MODE = 0b0100;
const PAD_CODEWORDS = [0b11101100, 0b00010001];
// The generator polynomials of the format information's BCH (15, 5) code and the version information's BCH (18, 6)
// code, and the pattern the format information is XORed with so it is never all light
const FORMAT_GENERATOR = 0b10100110111;
const FORMAT_MASK = 0b101010000010010;
const VERSION_GENERATOR = 0b1111100100101;

// GF(256) by the primitive polynomial x^8 + x^4 + x^3 + x^2 + 1, which the standard's Reed-Solomon code uses
const EXP = new Uint8Array(512);
const LOG = new Uint8Array(256);
for (let power = 0, value = 1; power < 255; power++) {
  EXP[power] = value;
  LOG[value] = power;
  value <<= 1;
  if (value & 0x100) value ^= 0x11d;
}
for (let power = 255; power < 512; power++) EXP[power] = EXP[power - 255];

function gfMultiply(a, b) {
  return a === 0 || b === 0 ? 0 : EXP[LOG[a] + LOG[b]];
}

/** The modules a side of a symbol of `version`. */
export function qrSize(version) {
  return 17 + 4 * version;
}

/** The row and column centres of the alignment patterns of `version`, from the top or the left. */
export function alignmentCentres(version) {
  if (version === 1) return [];
  const count = Math.floor(version / 7) + 2;
  const last = qrSize(version) - 7;
  // Evenly spaced back from the last by an even step, the first gap taking what is left; version 32 is the one
  // version whose table row has a step under the rule's
  const step = version === 32 ? 26 : Math.ceil((last - 6) / (count - 1) / 2) * 2;
  const centres = [6];
  for (let index = count - 2; index >= 0; index--) centres.push(last - index * step);
  return centres;
}

/** The remainder of `value` (of `valueBits` bits) times x^degree, divided by `generator` (of that degree), over GF(2). */
function bchRemainder(value, valueBits, generator, degree) {
  let remainder = value << degree;
  for (let bit = valueBits + degree - 1; bit >= degree; bit--) {
    if ((remainder >> bit) & 1) remainder ^= generator << (bit - degree);
  }
  return remainder;
}

/** The 15 format information bits for `level` and `mask`, most significant first, as drawn. */
export function formatBits(level, mask) {
  const data = (LEVEL_BITS[level] << 3) | mask;
  return ((data << 10) | bchRemainder(data, 5, FORMAT_GENERATOR, 10)) ^ FORMAT_MASK;
}

/** The 18 version information bits of `version` (7 to 40), most significant first. */
export function versionBits(version) {
  return (version << 12) | bchRemainder(version, 6, VERSION_GENERATOR, 12);
}

/** The `degree` Reed-Solomon error correction codewords of `data`, the remainder by the standard's generator polynomial. */
export function reedSolomon(data, degree) {
  // The generator, the product of (x - a^i) for i from 0 to degree - 1, its coefficients from the highest power down,
  // the leading 1 left out
  let generator = [1];
  for (let root = 0; root < degree; root++) {
    const next = new Array(generator.length + 1).fill(0);
    for (let index = 0; index < generator.length; index++) {
      next[index] ^= generator[index];
      next[index + 1] ^= gfMultiply(generator[index], EXP[root]);
    }
    generator = next;
  }
  const remainder = new Array(degree).fill(0);
  for (const codeword of data) {
    const factor = codeword ^ remainder.shift();
    remainder.push(0);
    for (let index = 0; index < degree; index++) remainder[index] ^= gfMultiply(generator[index + 1], factor);
  }
  return remainder;
}

/** A symbol of `version` with its function patterns drawn: `dark` and `reserved`, each by row then column. */
function functionPatterns(version) {
  const size = qrSize(version);
  const dark = Array.from({ length: size }, () => new Array(size).fill(false));
  const reserved = Array.from({ length: size }, () => new Array(size).fill(false));
  const set = (x, y, isDark) => {
    dark[y][x] = isDark;
    reserved[y][x] = true;
  };
  // The timing patterns, row 6 and column 6, dark on even modules; the finders overwrite their ends
  for (let index = 0; index < size; index++) {
    set(index, 6, index % 2 === 0);
    set(6, index, index % 2 === 0);
  }
  // The three finder patterns, each with its light separator, a square of 8 at three corners
  for (const [left, top] of [[0, 0], [size - 7, 0], [0, size - 7]]) {
    for (let dy = -1; dy <= 7; dy++) {
      for (let dx = -1; dx <= 7; dx++) {
        const x = left + dx;
        const y = top + dy;
        if (x < 0 || y < 0 || x >= size || y >= size) continue;
        const ring = Math.max(Math.abs(dx - 3), Math.abs(dy - 3));
        set(x, y, ring !== 2 && ring !== 4);
      }
    }
  }
  // The alignment patterns, except the three that would lie on a finder
  const centres = alignmentCentres(version);
  for (const cy of centres) {
    for (const cx of centres) {
      const onFinder = (cx === 6 && cy === 6) || (cx === 6 && cy === size - 7) || (cx === size - 7 && cy === 6);
      if (onFinder) continue;
      for (let dy = -2; dy <= 2; dy++) {
        for (let dx = -2; dx <= 2; dx++) set(cx + dx, cy + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
      }
    }
  }
  // The format information's two places, drawn per mask, around the timing patterns' modules at (8, 6) and (6, 8);
  // the dark module beside the lower left finder
  for (let index = 0; index <= 8; index++) {
    if (index === 6) continue;
    set(8, index, false);
    set(index, 8, false);
  }
  for (let index = 0; index < 8; index++) {
    set(size - 1 - index, 8, false);
    set(8, size - 1 - index, false);
  }
  set(8, size - 8, true);
  // The version information's two blocks of six by three, version 7 on
  if (version >= 7) {
    const bits = versionBits(version);
    for (let index = 0; index < 18; index++) {
      const bit = ((bits >> index) & 1) === 1;
      const near = size - 11 + (index % 3);
      const far = Math.floor(index / 3);
      set(near, far, bit);
      set(far, near, bit);
    }
  }
  return { size, dark, reserved };
}

/** The format information drawn into `dark` for `level` and `mask`: bit 0 is the least significant. */
function drawFormat(dark, level, mask) {
  const size = dark.length;
  const bits = formatBits(level, mask);
  const bit = (index) => ((bits >> index) & 1) === 1;
  // Around the upper left finder
  for (let index = 0; index <= 5; index++) dark[index][8] = bit(index);
  dark[7][8] = bit(6);
  dark[8][8] = bit(7);
  dark[8][7] = bit(8);
  for (let index = 9; index < 15; index++) dark[8][14 - index] = bit(index);
  // Beside the upper right finder, then under the lower left one
  for (let index = 0; index < 8; index++) dark[8][size - 1 - index] = bit(index);
  for (let index = 8; index < 15; index++) dark[size - 15 + index][8] = bit(index);
}

/** The codewords a symbol of `version` holds in all, data and error correction. */
export function totalCodewords(version) {
  const { reserved } = functionPatterns(version);
  let free = 0;
  for (const row of reserved) for (const taken of row) if (!taken) free++;
  return Math.floor(free / 8);
}

/** The data codewords a symbol of `version` at `level` holds. */
export function dataCodewords(version, level) {
  return totalCodewords(version) - EC_CODEWORDS_PER_BLOCK[level][version] * EC_BLOCKS[level][version];
}

/** The bits of the byte mode's character count of `version`. */
function countBits(version) {
  return version <= 9 ? 8 : 16;
}

/** The bytes a symbol of `version` at `level` holds in byte mode. */
export function byteCapacity(version, level) {
  return Math.floor((dataCodewords(version, level) * 8 - 4 - countBits(version)) / 8);
}

/** The data codewords of `bytes` for `version` at `level`: mode, count, data, terminator, then the pad codewords. */
export function byteModeData(bytes, version, level) {
  const capacityBits = dataCodewords(version, level) * 8;
  const bits = [];
  const put = (value, length) => {
    for (let bit = length - 1; bit >= 0; bit--) bits.push((value >> bit) & 1);
  };
  put(BYTE_MODE, 4);
  put(bytes.length, countBits(version));
  for (const byte of bytes) put(byte, 8);
  put(0, Math.min(4, capacityBits - bits.length));
  put(0, (8 - (bits.length % 8)) % 8);
  const codewords = [];
  for (let index = 0; index < bits.length; index += 8) {
    codewords.push(bits.slice(index, index + 8).reduce((value, bit) => (value << 1) | bit, 0));
  }
  for (let pad = 0; codewords.length < capacityBits / 8; pad++) codewords.push(PAD_CODEWORDS[pad % 2]);
  return codewords;
}

/** The final codeword sequence: the data cut into the level's blocks, each with its own error correction, interleaved. */
function finalSequence(data, version, level) {
  const blockCount = EC_BLOCKS[level][version];
  const degree = EC_CODEWORDS_PER_BLOCK[level][version];
  const shortLength = Math.floor(data.length / blockCount);
  const shortCount = blockCount - (data.length % blockCount);
  const blocks = [];
  for (let block = 0, start = 0; block < blockCount; block++) {
    const length = shortLength + (block < shortCount ? 0 : 1);
    const part = data.slice(start, start + length);
    blocks.push({ data: part, ec: reedSolomon(part, degree) });
    start += length;
  }
  const sequence = [];
  for (let index = 0; index <= shortLength; index++) {
    for (const block of blocks) if (index < block.data.length) sequence.push(block.data[index]);
  }
  for (let index = 0; index < degree; index++) for (const block of blocks) sequence.push(block.ec[index]);
  return sequence;
}

/** True where data mask `mask` turns the module at row `y`, column `x`. */
function masks(mask, y, x) {
  switch (mask) {
    case 0: return (y + x) % 2 === 0;
    case 1: return y % 2 === 0;
    case 2: return x % 3 === 0;
    case 3: return (y + x) % 3 === 0;
    case 4: return (Math.floor(y / 2) + Math.floor(x / 3)) % 2 === 0;
    case 5: return ((y * x) % 2) + ((y * x) % 3) === 0;
    case 6: return (((y * x) % 2) + ((y * x) % 3)) % 2 === 0;
    case 7: return (((y + x) % 2) + ((y * x) % 3)) % 2 === 0;
    default: throw new RangeError('a data mask is 0 to 7');
  }
}

/**
 * The four penalty scores of a symbol, `dark` by row then column: N1 for each run of five or more modules of one
 * colour in a row or column (3, and 1 more for each module past five), N2 for each two by two block of one colour
 * (3), N3 for each dark, light, dark dark dark, light, dark pattern in a row or column with four light modules
 * before it or after it (40; outside the symbol is light, as its quiet zone is), and N4 for the share of dark modules
 * (10 for each whole 5 percent it lies from half).
 */
export function penaltyScores(dark) {
  const size = dark.length;
  const lines = [];
  for (let y = 0; y < size; y++) lines.push(dark[y]);
  for (let x = 0; x < size; x++) lines.push(dark.map((row) => row[x]));
  let runs = 0;
  let finders = 0;
  for (const line of lines) {
    for (let start = 0; start < size;) {
      let end = start;
      while (end < size && line[end] === line[start]) end++;
      if (end - start >= 5) runs += N1 + (end - start - 5);
      start = end;
    }
    const lightAt = (index) => index < 0 || index >= size || !line[index];
    for (let start = 0; start + 7 <= size; start++) {
      const finderLike = line[start] && !line[start + 1] && line[start + 2] && line[start + 3] && line[start + 4] &&
        !line[start + 5] && line[start + 6];
      if (!finderLike) continue;
      const lightBefore = [1, 2, 3, 4].every((back) => lightAt(start - back));
      const lightAfter = [7, 8, 9, 10].every((ahead) => lightAt(start + ahead));
      if (lightBefore || lightAfter) finders += N3;
    }
  }
  let blocks = 0;
  for (let y = 0; y + 1 < size; y++) {
    for (let x = 0; x + 1 < size; x++) {
      const colour = dark[y][x];
      if (dark[y][x + 1] === colour && dark[y + 1][x] === colour && dark[y + 1][x + 1] === colour) blocks += N2;
    }
  }
  let darkCount = 0;
  for (const row of dark) for (const module of row) if (module) darkCount++;
  const total = size * size;
  const share = N4 * Math.floor(Math.abs(20 * darkCount - 10 * total) / total);
  return { n1: runs, n2: blocks, n3: finders, n4: share };
}

/** The sum of a symbol's four penalty scores. */
export function penalty(dark) {
  const { n1, n2, n3, n4 } = penaltyScores(dark);
  return n1 + n2 + n3 + n4;
}

/** The symbol of `sequence` in `version` at `level` with data mask `mask`. */
function symbol(sequence, version, level, mask, patterns) {
  const { size, reserved } = patterns;
  const dark = patterns.dark.map((row) => row.slice());
  // Two columns at a time from the right edge, up then down by turns, past the vertical timing pattern; a module
  // after the last codeword's bits is a remainder bit, light
  let bit = 0;
  let upward = true;
  for (let right = size - 1; right > 0; right -= 2) {
    if (right === 6) right = 5;
    for (let step = 0; step < size; step++) {
      const y = upward ? size - 1 - step : step;
      for (const x of [right, right - 1]) {
        if (reserved[y][x]) continue;
        const codeword = sequence[bit >> 3];
        const value = codeword !== undefined && ((codeword >> (7 - (bit & 7))) & 1) === 1;
        dark[y][x] = value !== masks(mask, y, x);
        bit++;
      }
    }
    upward = !upward;
  }
  drawFormat(dark, level, mask);
  return dark;
}

/**
 * The QR code of `text` (printable ASCII), in byte mode at error correction `level` (L unless asked), in the
 * smallest version that holds it unless `version` is asked, with the data mask of the lowest penalty (the first of
 * equal ones) unless `mask` is asked.
 */
export function encodeQr(text, options = {}) {
  const { level = 'L', version: askedVersion, mask: askedMask } = options;
  if (typeof text !== 'string') throw new TypeError('the text is a string');
  if (!QR_LEVELS.includes(level)) throw new RangeError('an error correction level is L, M, Q or H');
  const bytes = [];
  for (let index = 0; index < text.length; index++) {
    const code = text.charCodeAt(index);
    if (code < 0x20 || code > 0x7e) throw new RangeError('only printable ASCII is encoded');
    bytes.push(code);
  }
  let version = askedVersion;
  if (version === undefined) {
    version = 1;
    while (version <= 40 && byteCapacity(version, level) < bytes.length) version++;
    if (version > 40) throw new RangeError(`the text is longer than the ${byteCapacity(40, level)} bytes a code holds at ${level}`);
  } else if (!Number.isInteger(version) || version < 1 || version > 40) {
    throw new RangeError('a version is 1 to 40');
  } else if (byteCapacity(version, level) < bytes.length) {
    throw new RangeError(`version ${version} at ${level} holds ${byteCapacity(version, level)} bytes`);
  }
  if (askedMask !== undefined && !(Number.isInteger(askedMask) && askedMask >= 0 && askedMask <= 7)) {
    throw new RangeError('a data mask is 0 to 7');
  }
  const sequence = finalSequence(byteModeData(bytes, version, level), version, level);
  const patterns = functionPatterns(version);
  let chosen;
  for (let mask = 0; mask < 8; mask++) {
    if (askedMask !== undefined && mask !== askedMask) continue;
    const dark = symbol(sequence, version, level, mask, patterns);
    const score = penalty(dark);
    if (!chosen || score < chosen.score) chosen = { mask, dark, score };
  }
  return { text, version, level, mask: chosen.mask, size: patterns.size, modules: chosen.dark };
}
