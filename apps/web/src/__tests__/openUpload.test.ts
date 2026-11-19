import fs from 'node:fs';
import path from 'node:path';
import fc from 'fast-check';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ObservationSessionSchema, type IngestReceipt, type ObservationSolution } from '@albedian/contracts';
import { openStoredUpload, uploadAddressed } from '../services/openUpload';

// The address that opens one stored upload: ?upload=<sha256> (64 lowercase hex; anything else is ignored). It opens
// that upload's stored reveal through GET /api/ingest/{sha256} and its solution route: no upload, no new solve. A
// value from the address reaches a request path, so it is checked before any request is made.

const SEED = process.env.ALBEDIAN_FC_SEED ? { seed: Number(process.env.ALBEDIAN_FC_SEED) } : {};
const sha256 = '4f055db773c14698'.padEnd(64, 'a');
const assetId = `src_${sha256.slice(0, 16)}`;
const solutionId = `sol_${sha256.slice(0, 16)}`;
const receipt: IngestReceipt = {
  sourceAsset: {
    id: assetId,
    title: 'night.jpg',
    mediaKind: 'image',
    sha256,
    uri: `/api/ingest/${sha256}/original`,
    byteSize: 2_382_523,
    mimeType: 'image/jpeg',
    width: 3024,
    height: 4032,
    durationMs: 0,
    videoCodec: 'mjpeg',
    audioStreams: [],
    privacyClass: 'private',
    immutable: true,
  },
  hints: {},
  alreadyStored: true,
  solveJobId: `job_${sha256.slice(0, 16)}`,
};
const samples = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, '../../../../tests/fixtures/python_contract_solutions.json'), 'utf8')
) as Record<string, ObservationSolution>;
const base = samples['plate_calibrated']!;
const solution: ObservationSolution = {
  ...base,
  id: solutionId,
  sourceAssetId: assetId,
  sourceSha256: sha256,
  findings: base.findings.map((f) => ({
    ...f,
    observationSolutionId: solutionId,
    sourceDetection: f.sourceDetection ? { ...f.sourceDetection, assetId } : undefined,
    evidence: { ...f.evidence, sourceAssetSha256: sha256, wcsSolutionId: solutionId },
  })),
};

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
function server(routes: { receipt?: () => Response | Promise<Response>; solution?: () => Response | Promise<Response> } = {}) {
  const calls: string[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = typeof input === 'string' ? input : input.toString();
    calls.push(`${init?.method ?? 'GET'} ${url}`);
    if (url === `/api/ingest/${sha256}`) return (routes.receipt ?? (() => json(receipt)))();
    if (url === `/api/ingest/${sha256}/solution`) return (routes.solution ?? (() => json(solution)))();
    return new Response('Not found', { status: 404 });
  });
  return calls;
}
afterEach(() => vi.restoreAllMocks());

describe('uploadAddressed: the upload an address names, or none', () => {
  it('reads one sha256 of 64 lowercase hex characters', () => {
    expect(uploadAddressed(`?upload=${sha256}`)).toBe(sha256);
    expect(uploadAddressed(`?view=observatory&upload=${sha256}&scene=1`)).toBe(sha256);
    expect(uploadAddressed(`upload=${sha256}`)).toBe(sha256);
  });

  it.each([
    ['no address', ''],
    ['no such key', '?view=observatory'],
    ['an empty value', '?upload='],
    ['63 characters', `?upload=${sha256.slice(1)}`],
    ['65 characters', `?upload=${sha256}a`],
    ['capital letters', `?upload=${sha256.toUpperCase()}`],
    ['a letter past f', `?upload=${'g'.repeat(64)}`],
    ['a path', `?upload=..%2F..%2F${'a'.repeat(58)}`],
    ['a slash inside', `?upload=${sha256.slice(0, 32)}/${sha256.slice(33)}`],
    ['a space before it', `?upload=%20${sha256}`],
    ['a line break after it', `?upload=${sha256}%0A`],
    ['a query of its own', `?upload=${sha256}%3Fx%3D1`],
    ['the key twice, both good', `?upload=${sha256}&upload=${sha256}`],
    ['the key twice, one good', `?upload=nonsense&upload=${sha256}`],
    ['another key holding a hash', `?uploads=${sha256}`],
    ['a key in capitals', `?UPLOAD=${sha256}`],
  ])('ignores %s', (_label, search) => {
    expect(uploadAddressed(search)).toBeNull();
  });

  it('PBT: answers a value only when it is exactly 64 characters of 0 to 9 and a to f, and then that value', () => {
    fc.assert(
      fc.property(fc.string({ maxLength: 80 }), (value) => {
        const read = uploadAddressed(`?upload=${encodeURIComponent(value)}`);
        if (/^[0-9a-f]{64}$/.test(value)) expect(read).toBe(value);
        else expect(read).toBeNull();
      }),
      { numRuns: 500, ...SEED }
    );
    fc.assert(
      fc.property(fc.hexaString({ minLength: 64, maxLength: 64 }), (value) => {
        const lower = value.toLowerCase();
        expect(uploadAddressed(`?upload=${lower}`)).toBe(lower);
      }),
      { numRuns: 200, ...SEED }
    );
  });
});

describe('openStoredUpload: the stored reveal, with no upload and no new solve', () => {
  it('reads the receipt and the solution, and nothing else: no POST is made', async () => {
    const calls = server();
    const session = await openStoredUpload(sha256);
    expect(calls).toEqual([`GET /api/ingest/${sha256}`, `GET /api/ingest/${sha256}/solution`]);
    ObservationSessionSchema.parse(session);
    expect(session!.sourceAsset!.sha256).toBe(sha256);
    expect(session!.observationSolution!.id).toBe(solutionId);
    expect(session!.observationSolution!.status).toBe('calibrated');
    expect(session!.machines.calibration).toBe('calibrated');
  });

  it("shows the stored original from the server's own route for that hash, whatever address the receipt carries", async () => {
    const elsewhere: IngestReceipt = { ...receipt, sourceAsset: { ...receipt.sourceAsset, uri: 'https://example.invalid/other.jpg' } };
    server({ receipt: () => json(elsewhere) });
    const session = await openStoredUpload(sha256);
    expect(session!.sourceAsset!.uri).toBe(`/api/ingest/${sha256}/original`);
  });

  it.each([
    ['a path', `../../${'a'.repeat(58)}`],
    ['capital letters', sha256.toUpperCase()],
    ['too short', sha256.slice(1)],
    ['empty', ''],
  ])('makes no request for a value that is not a sha256 (%s)', async (_label, value) => {
    const calls = server();
    expect(await openStoredUpload(value)).toBeNull();
    expect(calls).toEqual([]);
  });

  it('opens nothing when no file with that hash is stored, and does not ask for a solution', async () => {
    const calls = server({ receipt: () => json({ detail: 'no file with that hash is stored' }, 404) });
    expect(await openStoredUpload(sha256)).toBeNull();
    expect(calls).toEqual([`GET /api/ingest/${sha256}`]);
  });

  it('opens nothing when the server answers a receipt for another file', async () => {
    const other = 'b'.repeat(64);
    const wrong: IngestReceipt = { ...receipt, sourceAsset: { ...receipt.sourceAsset, sha256: other, id: `src_${other.slice(0, 16)}`, uri: `/api/ingest/${other}/original` } };
    const calls = server({ receipt: () => json(wrong) });
    expect(await openStoredUpload(sha256)).toBeNull();
    expect(calls).toEqual([`GET /api/ingest/${sha256}`]);
  });

  it('opens nothing when the upload has no stored solution: it never starts a solve', async () => {
    const calls = server({ solution: () => json({ detail: 'no solution is stored for that file' }, 404) });
    expect(await openStoredUpload(sha256)).toBeNull();
    expect(calls).toEqual([`GET /api/ingest/${sha256}`, `GET /api/ingest/${sha256}/solution`]);
    expect(calls.some((call) => call.startsWith('POST'))).toBe(false);
  });

  it('opens nothing from an error answer, even one whose body would pass as a receipt or a solution', async () => {
    const calls = server({ receipt: () => json(receipt, 500) });
    expect(await openStoredUpload(sha256)).toBeNull();
    expect(calls).toEqual([`GET /api/ingest/${sha256}`]);
    vi.restoreAllMocks();
    server({ solution: () => json(solution, 409) });
    expect(await openStoredUpload(sha256)).toBeNull();
  });

  it.each([
    ['a receipt the contract refuses', { receipt: () => json({ sourceAsset: {} }) }],
    ['a solution the contract refuses', { solution: () => json({ id: 'x' }) }],
    ['a receipt that is not JSON', { receipt: () => new Response('<html>', { status: 200 }) }],
    ['no answer at all', { receipt: () => Promise.reject(new TypeError('Failed to fetch')) }],
    ['a solution for another file', { solution: () => json({ ...solution, sourceSha256: 'b'.repeat(64) }) }],
  ])('opens nothing on %s, and never throws', async (_label, routes) => {
    server(routes);
    await expect(openStoredUpload(sha256)).resolves.toBeNull();
  });
});
