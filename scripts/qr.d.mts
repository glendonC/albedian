// The types of scripts/qr.mjs, so the web app's TypeScript reads the encoder the field start uses.

/** An error correction level, from the most data (L) to the most correction (H). */
export type QrLevel = 'L' | 'M' | 'Q' | 'H';

export interface QrOptions {
  /** L unless asked: the level the phone address is drawn at (UIUX-phone-code-READY.md Q1). */
  readonly level?: QrLevel;
  /** 1 to 40; the smallest that holds the text unless asked. */
  readonly version?: number;
  /** 0 to 7; the one the standard's penalty scores lowest unless asked. */
  readonly mask?: number;
}

export interface QrCode {
  readonly text: string;
  readonly version: number;
  readonly level: QrLevel;
  readonly mask: number;
  /** Modules a side, 17 + 4 x version, with no quiet zone: the drawing adds four light modules all round. */
  readonly size: number;
  /** modules[y][x], true for a dark module, row y from the top and column x from the left. */
  readonly modules: readonly (readonly boolean[])[];
}

export interface QrPenaltyScores {
  readonly n1: number;
  readonly n2: number;
  readonly n3: number;
  readonly n4: number;
}

export const QR_LEVELS: readonly QrLevel[];
/** The QR code of `text`, printable ASCII only; throws a RangeError for any other text or option. */
export function encodeQr(text: string, options?: QrOptions): QrCode;
export function qrSize(version: number): number;
export function byteCapacity(version: number, level: QrLevel): number;
export function dataCodewords(version: number, level: QrLevel): number;
export function totalCodewords(version: number): number;
export function alignmentCentres(version: number): number[];
export function formatBits(level: QrLevel, mask: number): number;
export function versionBits(version: number): number;
export function reedSolomon(data: readonly number[], degree: number): number[];
export function byteModeData(bytes: readonly number[], version: number, level: QrLevel): number[];
export function penaltyScores(dark: readonly (readonly boolean[])[]): QrPenaltyScores;
export function penalty(dark: readonly (readonly boolean[])[]): number;
