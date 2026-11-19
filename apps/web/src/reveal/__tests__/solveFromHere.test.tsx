// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PersonAlignmentSchema, type IngestReceipt } from '@albedian/contracts';
import { personAlignmentOf, type AlignPose } from '../alignPose.js';
import { solveStoredUpload } from '../../services/apiClient.js';
import { WorkingScreen } from '../WorkingScreen.js';
import type { Arrival } from '../arrival.js';

// D24's Solve from here (UIUX-align-READY.md steps 2 and 3, decided by the lead 07:40), against SOLVER's contract
// (packages/contracts/src/alignment.ts) and route (POST /api/ingest/{sha}/solve with the alignment as its body): what
// the screen sends is the person's pose, never a measurement, and while it solves the rings stay at that pose with
// "Matching near your alignment.", their source line (the predicted disclosure every early mark carries, from the
// first frame: the lead's 17:38 rule), the solve's rows and Cancel.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('personAlignmentOf', () => {
  it("sends the pose's centre, turn and diagonal field, the person's, as the contract takes it", () => {
    const pose: AlignPose = { azDeg: 360 - 1e-12, altDeg: 34.2, rollDeg: -12.5, fovXDeg: 53, aspect: 4 / 3 };
    const sent = personAlignmentOf(pose);
    expect(PersonAlignmentSchema.safeParse(sent).success).toBe(true);
    expect(sent).toMatchObject({ format: 'albedian.person-alignment', formatVersion: 1, source: 'person', centerAltDeg: 34.2, rollDeg: -12.5 });
    // The azimuth wraps into [0, 360)
    expect(sent.centerAzDeg).toBeGreaterThanOrEqual(0);
    expect(sent.centerAzDeg).toBeLessThan(360);
    // The diagonal of a 53 degree field across a 4:3 frame (height over width): tan(d/2) = tan(26.5) * hypot(1, 4/3)
    expect(sent.fovDiagonalDeg).toBeCloseTo((2 * Math.atan(Math.tan((26.5 * Math.PI) / 180) * Math.hypot(1, 4 / 3)) * 180) / Math.PI, 9);
  });

  it("keeps the turn's sign: a positive turn puts the horizon's right side lower in both the drawing and the solver", () => {
    expect(personAlignmentOf({ azDeg: 10, altDeg: 20, rollDeg: 7, fovXDeg: 40, aspect: 0.75 }).rollDeg).toBe(7);
  });
});

describe('solveStoredUpload with an alignment', () => {
  afterEach(() => vi.restoreAllMocks());
  it('starts the solve with the alignment as its JSON body', async () => {
    const sha = 'c'.repeat(64);
    const receipt = { sourceAsset: { id: `src_${sha.slice(0, 16)}`, title: 'night.png', mediaKind: 'image', sha256: sha, uri: `/api/ingest/${sha}/original`, byteSize: 12, mimeType: 'image/png', width: 960, height: 540, durationMs: 0, videoCodec: 'png', audioStreams: [], privacyClass: 'private', immutable: true }, hints: {}, alreadyStored: true } as unknown as IngestReceipt;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      calls.push({ url: String(input), init });
      return new Response(JSON.stringify({ detail: 'stop here' }), { status: 500 });
    });
    const alignment = personAlignmentOf({ azDeg: 200, altDeg: 30, rollDeg: 0, fovXDeg: 50, aspect: 0.75 });
    await solveStoredUpload(receipt, { alignment, pollMs: 0 });
    expect(calls[0]!.url).toBe(`/api/ingest/${sha}/solve?wait=false`);
    expect(calls[0]!.init?.method).toBe('POST');
    expect((calls[0]!.init?.headers as Record<string, string>)['content-type']).toBe('application/json');
    expect(JSON.parse(String(calls[0]!.init?.body))).toEqual(alignment);
  });
});

describe('the sent state (step 3)', () => {
  let container: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });
  it('keeps the rings at the pose with "Matching near your alignment.", their source line, Cancel and the rows', () => {
    const arrival = { fileName: 'night.jpg', byteSize: 10, stored: true, widthPx: 3024, heightPx: 4032, job: { id: 'job_0123456789abcdef', state: 'running', stages: [] }, failure: null } as Arrival;
    act(() =>
      root.render(
        createElement(WorkingScreen, {
          arrival,
          photoRect: { leftPx: 382.5, topPx: 0, widthPx: 675, heightPx: 900 },
          onTryAgain: () => {},
          onUploadAnother: () => {},
          onCancel: () => {},
          aligned: { marks: [{ id: 'finding-predicted-star-vega', name: 'Vega', u: 0.5, v: 0.5 }] },
        })
      )
    );
    const block = container.querySelector('[data-testid="work-early"]')!;
    expect([...block.children].map((el) => el.className)).toEqual(['work-early__lead', 'work-early__from', 'work-acts', 'work-steps']);
    expect(block.querySelector('.work-early__lead')!.textContent).toBe('Matching near your alignment.');
    // Drawn as predicted with the rings, in D25's decided words for an alignment
    expect(block.querySelector('.work-early__from')!.textContent).toBe('Placed from your alignment: predicted until matched.');
    expect(container.querySelectorAll('[data-testid="early-marks"] circle')).toHaveLength(1);
  });
});

describe('placedAtPose', () => {
  it('places each target inside the frame through the pose, as shares of the shown photo, and none outside', async () => {
    const { placedAtPose, projectAltAz } = await import('../alignPose.js');
    const pose: AlignPose = { azDeg: 180, altDeg: 30, rollDeg: 0, fovXDeg: 50, aspect: 0.75 };
    const finding = { id: 'finding-predicted-star-a' } as never;
    const placed = placedAtPose(
      [
        { finding, name: 'A', altDeg: 30, azDeg: 180, magnitude: 1 },
        { finding: { id: 'finding-predicted-star-b' } as never, name: 'B', altDeg: 30, azDeg: 225, magnitude: 1 },
      ],
      pose
    );
    const at = projectAltAz(30, 180, pose)!;
    expect(placed).toEqual([{ id: 'finding-predicted-star-a', name: 'A', u: at.u, v: at.v }]);
  });
});
