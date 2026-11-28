// The QR encoder (scripts/qr.mjs) against ISO/IEC 18004's own worked examples and tables, the symbol's fixed parts,
// the mask penalty, and, on a Mac, its codes read back by the Mac's own two readers (scripts/qr-read-mac.swift).
//
// The read-back is registered only where Xcode's swiftc is found on macOS, which has Vision and Core Image: both CI
// guards fail a skipped test (scripts/vitest-no-unexpected-skips.mjs, tests/conftest.py), so on Linux it is absent,
// not skipped. The landing gate runs on a Mac, so every landing runs it.
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import zlib from 'node:zlib';
import fc from 'fast-check';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import {
  QR_LEVELS,
  alignmentCentres,
  byteCapacity,
  byteModeData,
  dataCodewords,
  encodeQr,
  formatBits,
  penalty,
  penaltyScores,
  qrSize,
  reedSolomon,
  totalCodewords,
  versionBits,
} from '../qr.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
// The longest address the field start can print for a phone: a private IPv4 address and a five-digit port
const LONGEST_ADDRESS = 'http://192.168.255.255:65535';
const VERSIONS = Array.from({ length: 40 }, (_, index) => index + 1);

// The standard's table of codewords a symbol holds in all, versions 1 to 40
const TOTAL_CODEWORDS = [
  26, 44, 70, 100, 134, 172, 196, 242, 292, 346, 404, 466, 532, 581, 655, 733, 815, 901, 991, 1085, 1156, 1258, 1364,
  1474, 1588, 1706, 1828, 1921, 2051, 2185, 2323, 2465, 2611, 2761, 2876, 3034, 3196, 3362, 3532, 3706,
];
// The standard's table of bytes a symbol holds in byte mode, versions 1 to 40, by level
const BYTE_CAPACITY = {
  L: [17, 32, 53, 78, 106, 134, 154, 192, 230, 271, 321, 367, 425, 458, 520, 586, 644, 718, 792, 858, 929, 1003, 1091,
    1171, 1273, 1367, 1465, 1528, 1628, 1732, 1840, 1952, 2068, 2188, 2303, 2431, 2563, 2699, 2809, 2953],
  M: [14, 26, 42, 62, 84, 106, 122, 152, 180, 213, 251, 287, 331, 362, 412, 450, 504, 560, 624, 666, 711, 779, 857, 911,
    997, 1059, 1125, 1190, 1264, 1370, 1452, 1538, 1628, 1722, 1809, 1911, 1989, 2099, 2213, 2331],
  Q: [11, 20, 32, 46, 60, 74, 86, 108, 130, 151, 177, 203, 241, 258, 292, 322, 364, 394, 442, 482, 509, 565, 611, 661,
    715, 751, 805, 868, 908, 982, 1030, 1112, 1168, 1228, 1283, 1351, 1423, 1499, 1579, 1663],
  H: [7, 14, 24, 34, 44, 58, 64, 84, 98, 119, 137, 155, 177, 194, 220, 250, 280, 310, 338, 382, 403, 439, 461, 511, 535,
    593, 625, 658, 698, 742, 790, 842, 898, 958, 983, 1051, 1093, 1139, 1219, 1273],
};

// The longest phone address's code under each mask, row by row from the top, each row's 25 modules as hex (dark 1).
// The Mac's two readers read each of these back whole (the read-back below); pinned so a platform without them,
// Linux CI, still holds the data's placement, interleaving and masks to codes a reader was seen to read
const LONGEST_ADDRESS_ROWS = {
  0: '1fc437f 1048f41 175205d 174835d 1749f5d 1047641 1fd557f 0013800 1df0cc4 0438c61 0ef4d77 0cac9f2 0154f6b 0dbcce9 11ec1b7 0d1feea 10fcff8 0010d1f 1fdc353 105ff1a 1750df2 174c794 1755f99 105999a 1fdcde3',
  1: '1fde97f 1052541 1748a5d 174295d 175355d 105dc41 1fd557f 0019200 1cda6f3 0e926cb 045e7dd 0606358 0bfe5c1 0716643 1b46b1d 07b5440 1a565f2 001a715 1fc6959 1055510 174a7f8 1746d3e 175f533 1053330 1fd6749',
  2: '1fc847f 105b741 174e75d 175bb5d 174585d 1054e41 1fd557f 0000000 1f7cbaa 1c9b482 09e8a6b 140f111 0648877 151f40a 16f06ab 15bc609 17e09f4 001351c 1fd055f 104c719 175cbfe 175ff77 1759885 105a179 1fd0aff',
  3: '1fd847f 1040141 1753c5d 175bb5d 175ee5d 1049541 1fd557f 000b600 1e5109d 1c9b482 1f73cb0 0f22a7c 0648877 03842d1 0ddddc6 15bc609 017bfff 001ef11 1fc055f 1047112 17411f3 175ff77 1752e5e 1057a14 1fd0aff',
  4: '1fd0a7f 1053941 175965d 175ca5d 174d65d 105c041 1fd557f 0007100 19c452f 12a3aba 186fbac 05880d6 087064f 1b27a32 077776c 043b7ce 19d87fc 001bb14 1fc7558 105b71e 17545f6 174714f 174e942 105d0be 1fd84c7',
  5: '1fce97f 1043541 174e75d 1757c5d 175585d 104cc41 1fd557f 0008200 18ecb18 1b8739e 09e8a6b 1607319 0bfe5c1 1717602 16f06ab 12a0115 17e09f4 001b714 1fd6959 1054511 174cbfe 174386b 1749885 1052371 1fd6749',
  6: '1fde97f 1043941 174ae5d 1747c5d 1747c5d 104ad41 1fd557f 0018e00 1b48241 1b8739e 0d7aef9 178129f 0bfe5c1 1b27a32 1fd4f8f 12a0115 1372df6 001d712 1fc6959 1044911 17583fa 175386b 174bc17 10542f7 1fd6749',
  7: '1fc437f 105c641 175045d 174835d 175d65d 1055241 1fd557f 0017100 1a62876 0438c61 07d0453 083ed60 0154f6b 04985cd 157e525 0d1feea 19d87fc 001291d 1fdc353 104b71e 17429f0 175c794 17416bd 105bd08 1fdcde3',
};

/** Printable ASCII of `length`, the same for the same seed: a payload a test can make again. */
function payload(length, seed) {
  let state = seed >>> 0;
  let text = '';
  for (let index = 0; index < length; index++) {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    text += String.fromCharCode(0x20 + (state >>> 8) % 95);
  }
  return text;
}

/** The 15 bits of each copy of the format information read from `modules`, least significant first. */
function formatCopies(modules) {
  const size = modules.length;
  const first = [];
  const second = [];
  for (let index = 0; index <= 5; index++) first.push(modules[index][8]);
  first.push(modules[7][8], modules[8][8], modules[8][7]);
  for (let index = 9; index < 15; index++) first.push(modules[8][14 - index]);
  for (let index = 0; index < 8; index++) second.push(modules[8][size - 1 - index]);
  for (let index = 8; index < 15; index++) second.push(modules[size - 15 + index][8]);
  const value = (bits) => bits.reduce((sum, bit, index) => sum | ((bit ? 1 : 0) << index), 0);
  return [value(first), value(second)];
}

/** True where a finder pattern with its separator stands with its upper left corner at (left, top). */
function finderAt(modules, left, top) {
  const size = modules.length;
  for (let dy = -1; dy <= 7; dy++) {
    for (let dx = -1; dx <= 7; dx++) {
      const x = left + dx;
      const y = top + dy;
      if (x < 0 || y < 0 || x >= size || y >= size) continue;
      const ring = Math.max(Math.abs(dx - 3), Math.abs(dy - 3));
      if (modules[y][x] !== (ring !== 2 && ring !== 4)) return false;
    }
  }
  return true;
}

describe('the QR encoder: the standard\'s own examples and tables', () => {
  it('makes the error correction codewords of the worked example, 01234567 as version 1 at M', () => {
    // The example's 16 data codewords: numeric mode, count 8, the digits in groups of three, the terminator, pads
    const data = [16, 32, 12, 86, 97, 128, 236, 17, 236, 17, 236, 17, 236, 17, 236, 17];
    expect(reedSolomon(data, 10)).toEqual([165, 36, 212, 193, 237, 54, 199, 135, 44, 85]);
  });

  it('puts byte mode data as the standard orders it: mode, count, bytes, terminator, then 11101100 and 00010001 by turns', () => {
    // "A" in version 1 at L: 0100, 00000001, 01000001, 0000, then pads to the 19 data codewords
    expect(byteModeData([0x41], 1, 'L')).toEqual([0x40, 0x14, 0x10, ...Array.from({ length: 16 }, (_, index) => (index % 2 ? 0x11 : 0xec))]);
    // Version 10 on, the count takes 16 bits: 0100, 0000000000000001, 01000001, 0000
    expect(byteModeData([0x41], 10, 'L').slice(0, 4)).toEqual([0x40, 0x00, 0x14, 0x10]);
    // A text that fills the capacity leaves no room for the terminator's bits past the last byte
    const full = byteModeData(Array.from({ length: 17 }, () => 0x7e), 1, 'L');
    expect(full).toHaveLength(19);
    expect(full.at(-1)).toBe(0xe0);
  });

  it('makes the format information of the example, level M with mask 101', () => {
    expect(formatBits('M', 0b101)).toBe(0b100000011001110);
  });

  it('makes the version information of the example, version 7', () => {
    expect(versionBits(7)).toBe(0b000111110010010100);
  });

  it('holds the codewords of the standard\'s table in each version', () => {
    expect(VERSIONS.map(totalCodewords)).toEqual(TOTAL_CODEWORDS);
  });

  it('holds the bytes of the standard\'s table in each version at each level', () => {
    for (const level of QR_LEVELS) expect(VERSIONS.map((version) => byteCapacity(version, level)), level).toEqual(BYTE_CAPACITY[level]);
  });

  it('holds the data codewords of the standard\'s table where it gives them', () => {
    expect(QR_LEVELS.map((level) => dataCodewords(1, level))).toEqual([19, 16, 13, 9]);
    expect(QR_LEVELS.map((level) => dataCodewords(40, level))).toEqual([2956, 2334, 1666, 1276]);
  });

  it('places the alignment patterns at the rows and columns of the standard\'s table', () => {
    expect(alignmentCentres(1)).toEqual([]);
    expect(alignmentCentres(2)).toEqual([6, 18]);
    expect(alignmentCentres(6)).toEqual([6, 34]);
    expect(alignmentCentres(7)).toEqual([6, 22, 38]);
    expect(alignmentCentres(14)).toEqual([6, 26, 46, 66]);
    expect(alignmentCentres(32)).toEqual([6, 34, 60, 86, 112, 138]);
    expect(alignmentCentres(36)).toEqual([6, 24, 50, 76, 102, 128, 154]);
    expect(alignmentCentres(40)).toEqual([6, 30, 58, 86, 114, 142, 170]);
    for (const version of VERSIONS.slice(1)) {
      const centres = alignmentCentres(version);
      expect(centres.length, `version ${version}`).toBe(Math.floor(version / 7) + 2);
      expect(centres.at(-1), `version ${version}`).toBe(qrSize(version) - 7);
    }
  });

  it('keeps every two format words apart by seven bits or more, as its BCH code does', () => {
    const words = QR_LEVELS.flatMap((level) => Array.from({ length: 8 }, (_, mask) => formatBits(level, mask)));
    expect(new Set(words).size).toBe(32);
    for (const [index, word] of words.entries()) {
      for (const other of words.slice(index + 1)) {
        let differ = word ^ other;
        let count = 0;
        for (; differ; differ &= differ - 1) count++;
        expect(count).toBeGreaterThanOrEqual(7);
      }
    }
  });
});

describe('the QR encoder: the symbol', () => {
  it('puts the longest phone address in version 2 at L, 25 modules a side', () => {
    const code = encodeQr(LONGEST_ADDRESS);
    expect([code.version, code.level, code.size]).toEqual([2, 'L', 25]);
    expect(code.modules).toHaveLength(25);
    expect(code.modules.every((row) => row.length === 25)).toBe(true);
    expect(encodeQr('http://172.20.10.3:5173').version).toBe(2);
  });

  it('draws the longest phone address as the codes the Mac readers read, under each mask', () => {
    for (let mask = 0; mask < 8; mask++) {
      const rows = encodeQr(LONGEST_ADDRESS, { mask }).modules.map((row) =>
        parseInt(row.map((dark) => (dark ? '1' : '0')).join(''), 2).toString(16).padStart(7, '0'),
      );
      expect(rows.join(' '), `mask ${mask}`).toBe(LONGEST_ADDRESS_ROWS[mask]);
    }
  });

  it('takes the smallest version that holds the text at each level', () => {
    for (const level of QR_LEVELS) {
      for (const version of [1, 2, 9, 10, 26, 40]) {
        const fits = encodeQr(payload(byteCapacity(version, level), version), { level });
        expect(fits.version, `${level} ${version}`).toBe(version);
        if (version < 40) expect(encodeQr(payload(byteCapacity(version, level) + 1, version), { level }).version).toBe(version + 1);
      }
    }
  });

  it('refuses a text it cannot encode and a version, level or mask outside the standard', () => {
    expect(() => encodeQr('café')).toThrow('only printable ASCII is encoded');
    expect(() => encodeQr('a\nb')).toThrow('only printable ASCII is encoded');
    expect(() => encodeQr('a\x7fb')).toThrow('only printable ASCII is encoded');
    expect(encodeQr(' ~').version).toBe(1);
    expect(() => encodeQr('x'.repeat(2954))).toThrow('longer than the 2953 bytes');
    expect(() => encodeQr('x'.repeat(18), { version: 1 })).toThrow('version 1 at L holds 17 bytes');
    expect(() => encodeQr('x', { version: 41 })).toThrow('a version is 1 to 40');
    expect(() => encodeQr('x', { level: 'X' })).toThrow('an error correction level');
    expect(() => encodeQr('x', { mask: 8 })).toThrow('a data mask is 0 to 7');
    expect(encodeQr('x'.repeat(2953)).version).toBe(40);
  });

  it('draws the fixed parts and both format copies for its level and mask, in every version', () => {
    for (const version of VERSIONS) {
      const level = QR_LEVELS[version % 4];
      const code = encodeQr(payload(10, version), { level, version });
      const { modules, size } = code;
      expect(size, `version ${version}`).toBe(qrSize(version));
      expect(finderAt(modules, 0, 0) && finderAt(modules, size - 7, 0) && finderAt(modules, 0, size - 7), `version ${version}`).toBe(true);
      for (let index = 8; index < size - 8; index++) {
        expect(modules[6][index], `version ${version} timing row at ${index}`).toBe(index % 2 === 0);
        expect(modules[index][6], `version ${version} timing column at ${index}`).toBe(index % 2 === 0);
      }
      expect(modules[size - 8][8], `version ${version} dark module`).toBe(true);
      expect(formatCopies(modules), `version ${version}`).toEqual([formatBits(level, code.mask), formatBits(level, code.mask)]);
    }
  });

  it('draws both copies of the version information from version 7 on', () => {
    for (const version of [7, 8, 21, 40]) {
      const { modules, size } = encodeQr('v', { version });
      const bits = versionBits(version);
      for (let index = 0; index < 18; index++) {
        const bit = ((bits >> index) & 1) === 1;
        expect(modules[Math.floor(index / 3)][size - 11 + (index % 3)], `version ${version} bit ${index}`).toBe(bit);
        expect(modules[size - 11 + (index % 3)][Math.floor(index / 3)], `version ${version} bit ${index}`).toBe(bit);
      }
    }
  });

  it('chooses the mask the penalty scores lowest, the first of equal ones', () => {
    for (const [text, level] of [[LONGEST_ADDRESS, 'L'], ['01234567', 'M'], [payload(120, 3), 'Q'], [payload(40, 9), 'H']]) {
      const chosen = encodeQr(text, { level });
      const scores = Array.from({ length: 8 }, (_, mask) => penalty(encodeQr(text, { level, mask }).modules));
      expect(chosen.mask, text).toBe(scores.indexOf(Math.min(...scores)));
      expect(encodeQr(text, { level, mask: chosen.mask }).modules).toEqual(chosen.modules);
    }
  });

  it('takes the first of two masks the penalty scores equal and lowest', () => {
    // Found by search (.lanetools/qr_find_tie.mjs): each address's two lowest masks score the same
    for (const [text, level, first, second] of [['http://192.168.21.0:5173', 'L', 5, 7], ['http://192.168.29.0:5173', 'H', 2, 7]]) {
      const scores = Array.from({ length: 8 }, (_, mask) => penalty(encodeQr(text, { level, mask }).modules));
      const lowest = Math.min(...scores);
      expect([scores[first], scores[second]], text).toEqual([lowest, lowest]);
      expect(encodeQr(text, { level }).mask, text).toBe(first);
    }
  });

  it('holds the same symbol for the same text, and the asked mask', () => {
    fc.assert(
      fc.property(
        fc.string({ unit: fc.integer({ min: 0x20, max: 0x7e }).map((code) => String.fromCharCode(code)), maxLength: 80 }),
        fc.constantFrom(...QR_LEVELS),
        fc.integer({ min: 0, max: 7 }),
        (text, level, mask) => {
          const code = encodeQr(text, { level, mask });
          expect(code.mask).toBe(mask);
          expect(byteCapacity(code.version, level)).toBeGreaterThanOrEqual(text.length);
          if (code.version > 1) expect(byteCapacity(code.version - 1, level)).toBeLessThan(text.length);
          expect(formatCopies(code.modules)).toEqual([formatBits(level, mask), formatBits(level, mask)]);
          expect(encodeQr(text, { level, mask }).modules).toEqual(code.modules);
        },
      ),
      { seed: SEED, numRuns: 60 },
    );
  });
});

/**
 * The modules a symbol of `version` leaves to data, in the standard's placement order, from the layout of its
 * function patterns written out again here: finders with separators, timing, alignment, format, the dark module and,
 * from version 7, the version information.
 */
function dataModulesInOrder(version) {
  const size = qrSize(version);
  const taken = Array.from({ length: size }, () => new Array(size).fill(false));
  const take = (x, y) => {
    if (x >= 0 && y >= 0 && x < size && y < size) taken[y][x] = true;
  };
  for (let index = 0; index < size; index++) take(index, 6), take(6, index);
  for (let a = 0; a < 8; a++) for (let b = 0; b < 8; b++) take(a, b), take(size - 1 - a, b), take(a, size - 1 - b);
  for (let index = 0; index < 9; index++) take(8, index), take(index, 8);
  for (let index = 0; index < 8; index++) take(size - 1 - index, 8), take(8, size - 1 - index);
  const centres = alignmentCentres(version);
  for (const cy of centres) for (const cx of centres) {
    if ((cx === 6 && cy === 6) || (cx === 6 && cy === size - 7) || (cx === size - 7 && cy === 6)) continue;
    for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) take(cx + dx, cy + dy);
  }
  if (version >= 7) for (let a = 0; a < 6; a++) for (let b = 0; b < 3; b++) take(a, size - 11 + b), take(size - 11 + b, a);
  const order = [];
  for (let pair = 0, right = size - 1; right > 0; pair++, right -= 2) {
    if (right === 6) right = 5;
    for (let step = 0; step < size; step++) {
      const y = pair % 2 === 0 ? size - 1 - step : step;
      for (const x of [right, right - 1]) if (!taken[y][x]) order.push([x, y]);
    }
  }
  return order;
}

describe('the QR encoder: the remainder bits', () => {
  it('leaves the modules past the last codeword light before the mask, as the standard\'s remainder bits are 0', () => {
    for (const [version, remainder] of [[2, 7], [7, 0], [14, 3], [21, 4], [35, 0]]) {
      const order = dataModulesInOrder(version);
      expect(order.length - 8 * totalCodewords(version), `version ${version}`).toBe(remainder);
      // Mask 1 turns the modules of even rows: under it a light remainder module is dark exactly on an even row
      const { modules } = encodeQr('r', { version, mask: 1 });
      for (const [x, y] of order.slice(order.length - remainder)) {
        expect(modules[y][x], `version ${version} at ${x}, ${y}`).toBe(y % 2 === 0);
      }
    }
  });
});

describe('the QR encoder: the mask penalty', () => {
  const light = (size) => Array.from({ length: size }, () => new Array(size).fill(false));
  const checker = (size) => Array.from({ length: size }, (_, y) => Array.from({ length: size }, (_, x) => (x + y) % 2 === 0));

  it('scores a checkerboard nothing but its share', () => {
    expect(penaltyScores(checker(11))).toEqual({ n1: 0, n2: 0, n3: 0, n4: 0 });
  });

  it('scores a run of five 3 and each module past five 1 more, in rows and in columns', () => {
    const five = checker(11);
    for (let x = 0; x < 5; x++) five[0][x] = true;
    // Row 0's run of five dark, and the columns are unchanged in their runs
    expect(penaltyScores(five).n1).toBe(3);
    const seven = checker(11);
    // Column 4 is dark on even rows: rows 1 to 7 light make a run of seven between the dark rows 0 and 8
    for (let y = 1; y < 8; y++) seven[y][4] = false;
    expect(penaltyScores(seven).n1).toBe(5);
  });

  it('scores each two by two block of one colour 3', () => {
    const block = checker(11);
    block[4][4] = block[4][5] = block[5][4] = block[5][5] = true;
    expect(penaltyScores(block).n2).toBe(3);
  });

  it('scores a finder-like pattern 40 with four light modules before or after it, outside the symbol light', () => {
    const symbol = light(15);
    // Row 7: four light, then 1 0 1 1 1 0 1, then four light
    for (const [x, dark] of [[4, 1], [5, 0], [6, 1], [7, 1], [8, 1], [9, 0], [10, 1]]) symbol[7][x] = dark === 1;
    expect(penaltyScores(symbol).n3).toBe(40);
    const atEdge = light(9);
    for (const [x, dark] of [[0, 1], [1, 0], [2, 1], [3, 1], [4, 1], [5, 0], [6, 1]]) atEdge[4][x] = dark === 1;
    // Light before it only outside the symbol; after it the row has two light modules, so the edge counts
    expect(penaltyScores(atEdge).n3).toBe(40);
    const boxed = light(13);
    for (const [x, dark] of [[0, 1], [1, 1], [2, 0], [3, 1], [4, 0], [5, 1], [6, 1], [7, 1], [8, 0], [9, 1], [10, 0], [11, 1], [12, 1]]) boxed[6][x] = dark === 1;
    // The pattern stands with one light module each side and dark beyond: no four light either side
    expect(penaltyScores(boxed).n3).toBe(0);
    const oneSide = light(15);
    for (const [x, dark] of [[4, 1], [5, 0], [6, 1], [7, 1], [8, 1], [9, 0], [10, 1], [11, 1]]) oneSide[7][x] = dark === 1;
    // Four light before it, dark right after it: the light before is enough
    expect(penaltyScores(oneSide).n3).toBe(40);
  });

  it('scores 10 for each whole 5 percent the dark share lies from half', () => {
    expect(penaltyScores(light(10)).n4).toBe(100);
    const share = (darkCount) => {
      const symbol = light(10);
      for (let index = 0; index < darkCount; index++) symbol[Math.floor(index / 10)][index % 10] = true;
      return penaltyScores(symbol).n4;
    };
    expect([share(50), share(54), share(55), share(45), share(44), share(60)]).toEqual([0, 0, 10, 10, 10, 20]);
  });
});

/** The path of swiftc on a Mac, or undefined where there is none (Linux, or a Mac without Xcode's tools). */
function macReader() {
  if (process.platform !== 'darwin') return undefined;
  const found = spawnSync('xcrun', ['--find', 'swiftc'], { encoding: 'utf8', timeout: 60_000 });
  return found.status === 0 && found.stdout.trim() ? found.stdout.trim() : undefined;
}

/** A grayscale PNG of `code` at `scale` pixels a module with the standard's four light modules all round. */
function png(code, scale) {
  const quiet = 4;
  const side = (code.size + 2 * quiet) * scale;
  const raw = Buffer.alloc(side * (side + 1), 255);
  for (let y = 0; y < side; y++) {
    raw[y * (side + 1)] = 0;
    const moduleY = Math.floor(y / scale) - quiet;
    for (let x = 0; x < side; x++) {
      const moduleX = Math.floor(x / scale) - quiet;
      const dark = moduleY >= 0 && moduleY < code.size && moduleX >= 0 && moduleX < code.size && code.modules[moduleY][moduleX];
      if (dark) raw[y * (side + 1) + 1 + x] = 0;
    }
  }
  const chunk = (type, data) => {
    const length = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(zlib.crc32(Buffer.concat([Buffer.from(type, 'ascii'), data])));
    return Buffer.concat([length, Buffer.from(type, 'ascii'), data, crc]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(side, 0);
  header.writeUInt32BE(side, 4);
  header.set([8, 0, 0, 0, 0], 8);
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', header),
    chunk('IDAT', zlib.deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

const swiftc = macReader();
if (swiftc !== undefined) {
  describe('the QR encoder: its codes read back by the Mac\'s own readers, Vision and Core Image', () => {
    let folder;
    let reader;

    beforeAll(() => {
      folder = fs.mkdtempSync(path.join(os.tmpdir(), 'albedian-qr-'));
      reader = path.join(folder, 'qr-read-mac');
      // Through xcrun, which gives the compiler the macOS SDK
      const built = spawnSync('xcrun', ['swiftc', '-O', '-o', reader, path.join(HERE, '..', 'qr-read-mac.swift')], {
        encoding: 'utf8',
        timeout: 300_000,
      });
      expect(built.error, 'swiftc ran').toBeUndefined();
      expect(built.status, built.stderr).toBe(0);
    }, 320_000);

    afterAll(() => {
      if (folder) fs.rmSync(folder, { recursive: true, force: true });
    });

    /** What both readers read from each code, by its file, in the order given. */
    function readBack(codes, scale) {
      const files = codes.map((code, index) => {
        const file = path.join(folder, `${index}.png`);
        fs.writeFileSync(file, png(code, scale));
        return file;
      });
      const read = spawnSync(reader, files, { encoding: 'utf8', timeout: 600_000, maxBuffer: 64 * 1024 * 1024 });
      expect(read.error, 'the reader ran to its end').toBeUndefined();
      expect(read.status, read.stderr).toBe(0);
      const lines = read.stdout.trim().split('\n').map((line) => JSON.parse(line));
      expect(lines.map((line) => line.file)).toEqual(files);
      return lines;
    }

    it('reads the longest phone address back whole, with each of the eight masks', () => {
      const codes = Array.from({ length: 8 }, (_, mask) => encodeQr(LONGEST_ADDRESS, { mask }));
      for (const [index, line] of readBack(codes, 8).entries()) {
        expect([line.vision, line.ciDetector], `mask ${index}`).toEqual([[LONGEST_ADDRESS], [LONGEST_ADDRESS]]);
      }
    }, 600_000);

    it('reads back every version at every level, each filled to its byte capacity', () => {
      const codes = QR_LEVELS.flatMap((level) =>
        VERSIONS.map((version) => encodeQr(payload(byteCapacity(version, level), version * 4 + QR_LEVELS.indexOf(level)), { level, version })),
      );
      const misread = [];
      for (const [index, line] of readBack(codes, 6).entries()) {
        const { text, version, level } = codes[index];
        if (line.vision?.[0] !== text) misread.push(`Vision ${version}-${level}`);
        if (line.ciDetector?.[0] !== text) misread.push(`Core Image ${version}-${level}`);
      }
      expect(misread).toEqual([]);
    }, 600_000);
  });
}
