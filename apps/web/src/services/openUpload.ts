import { IngestReceiptSchema, ObservationSolutionSchema, type ObservationSession } from '@albedian/contracts';
import { buildSessionFromIngestReceipt } from './apiClient.js';

/**
 * The address that opens one stored upload: `?upload=<sha256>`. The iPhone's Open leads here after the laptop has
 * stored and solved the photo it sent, so this reads the stored receipt and the stored solution and nothing else: no
 * upload and no new solve.
 *
 * The value comes from the address and goes into a request path, so it is checked first: exactly 64 characters of
 * 0 to 9 and a to f, the form the server's own routes take. Anything else is ignored and no request is made.
 */

/** A sha256 as the server's routes write it: 64 lowercase hex characters, and nothing around them. */
const SHA256 = /^[0-9a-f]{64}$/;

/** The upload an address names: the one `upload` value when it is a sha256, else none (a key given twice names none). */
export function uploadAddressed(search: string): string | null {
  const values = new URLSearchParams(search).getAll('upload');
  if (values.length !== 1) return null;
  const value = values[0]!;
  return SHA256.test(value) ? value : null;
}

/** `fetch` for a JSON answer, or undefined for no answer, an error status or a body that is not JSON. */
async function readJson(url: string): Promise<unknown> {
  try {
    const res = await fetch(url);
    if (!res.ok) return undefined;
    return await res.json();
  } catch {
    return undefined;
  }
}

/**
 * The stored upload as a session ready to show, or null when it cannot be opened: the value is no sha256, no file
 * with that hash is stored, it has no stored solution, or an answer does not fit its contract or names another file.
 * The photo shown is the stored original, from the server's own route for that hash. It never throws.
 */
export async function openStoredUpload(sha256: string): Promise<ObservationSession | null> {
  if (!SHA256.test(sha256)) return null;
  const receipt = IngestReceiptSchema.safeParse(await readJson(`/api/ingest/${sha256}`));
  if (!receipt.success || receipt.data.sourceAsset.sha256 !== sha256) return null;
  const solution = ObservationSolutionSchema.safeParse(await readJson(`/api/ingest/${sha256}/solution`));
  if (!solution.success || solution.data.sourceSha256 !== sha256) return null;
  try {
    return buildSessionFromIngestReceipt(receipt.data, {
      localObjectUrl: `/api/ingest/${sha256}/original`,
      solution: solution.data,
    });
  } catch {
    return null;
  }
}
