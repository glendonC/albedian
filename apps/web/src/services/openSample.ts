import {
  ObservationSolutionSchema,
  SAMPLE_ID_PATTERN,
  SampleAssetSchema,
  SampleRecordSchema,
  sampleAssetMatches,
  type ObservationSession,
  type SampleNight,
} from '@albedian/contracts';
import { buildSessionFromSampleAsset } from './apiClient.js';

/**
 * The address that opens a sample night's reveal: `?sample=<id>` (UIUX-sample-reveal-READY.md item 4, RULED 03:48 on 27
 * Nov), as `?upload=<sha256>` opens a stored upload's; a sample never takes `?upload=`. It reads the sample's record, its
 * photo as the app reads it and the app's own kept solve (PLATFORM's routes, `services/samples/router.py`) and nothing
 * else. If the asset or the solution does not answer, the page lands on the home.
 *
 * The value comes from the address and goes into a request path, so it is checked first against the record's own id
 * pattern (`night-` and up to 40 of a to z, 0 to 9 and hyphens); anything else is ignored and no request is made.
 */

/** The sample an address names: the one `sample` value when it is a sample id, else none (a key given twice names none). */
export function sampleAddressed(search: string): string | null {
  const values = new URLSearchParams(search).getAll('sample');
  if (values.length !== 1) return null;
  const value = values[0]!;
  return SAMPLE_ID_PATTERN.test(value) ? value : null;
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

/** A sample night opened: its record (title and credit) and its reveal's session. */
export interface OpenedSample {
  night: SampleNight;
  session: ObservationSession;
}

/**
 * The sample night as a session ready to show, with its record, or null when it cannot be opened: the id is not one,
 * the record does not list it, the asset is not this night's photo (`sampleAssetMatches`), or the kept solve does not
 * answer or names another photo (`sourceSha256` and `sourceAssetId`, as PLATFORM's note asks). It never throws.
 */
export async function openSample(id: string): Promise<OpenedSample | null> {
  if (!SAMPLE_ID_PATTERN.test(id)) return null;
  const record = SampleRecordSchema.safeParse(await readJson('/api/samples'));
  const night = record.success ? record.data.samples.find((s) => s.id === id) : undefined;
  if (!night) return null;
  const asset = SampleAssetSchema.safeParse(await readJson(`/api/samples/${id}/asset`));
  if (!asset.success || !sampleAssetMatches(night, asset.data)) return null;
  const solution = ObservationSolutionSchema.safeParse(await readJson(`/api/samples/${id}/solution`));
  if (!solution.success || solution.data.sourceSha256 !== asset.data.sha256 || solution.data.sourceAssetId !== asset.data.id) return null;
  try {
    return { night, session: buildSessionFromSampleAsset(asset.data, solution.data) };
  } catch {
    return null;
  }
}

/**
 * The sample the home offers (sample-pill-one): the record's first night, opened as its address opens it (its asset and
 * its kept solve both answering, each checked), or null when any of them does not answer. It never throws.
 */
export async function readySample(): Promise<OpenedSample | null> {
  const record = SampleRecordSchema.safeParse(await readJson('/api/samples'));
  const first = record.success ? record.data.samples[0] : undefined;
  return first ? openSample(first.id) : null;
}

/** Whether the session shows this sample night's photo: a public sample whose bytes the night's record names. */
export function isSampleShown(session: ObservationSession, night: SampleNight | null | undefined): night is SampleNight {
  const asset = session.sourceAsset;
  return !!night && !!asset && asset.privacyClass === 'public_sample' && asset.sha256 === night.photo.sha256;
}
