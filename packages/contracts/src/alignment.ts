import { z } from 'zod';
import { SKY_CONTEXT_CONVENTION } from './sky-context.js';
import { wireNumber } from './wire.js';

/**
 * A person's alignment of the predicted sky over their photo: what the screen sends with "Solve from here" (D24, line
 * it up yourself). Mirrored by services/contracts/alignment.py; both are judged by
 * tests/fixtures/person_alignment_contract.json, which tests/integration/test_person_alignment_contract.py writes.
 *
 * The pose the person left the drawn sky at: the frame centre's altitude and azimuth, its roll and its diagonal field of
 * view, with source "person". It is the person's, never a measurement: the solver takes it only as a search hint,
 * searching within the contract's stated uncertainty of the centre (`PERSON_ALIGNMENT_UNCERTAINTY_DEG`, [tested] by
 * align-2 on public photos), and the unchanged held-out gate alone decides whether the photo becomes
 * calibrated. A refused gate keeps the alignment as the person's, predicted, never observed or catalog matched.
 *
 * The pose is in the convention of the predicted sky the person aligned (sky-context's): topocentric at sea level,
 * azimuth from true north through east, altitude apparent and airless; the roll is the turn of the upright frame (the
 * frame as displayed), positive turning the picture clockwise, as a pose estimate's roll is.
 *
 * Strict on both sides by the wire rules: unknown keys and Python field names are refused.
 */
export const PERSON_ALIGNMENT_FORMAT = 'albedian.person-alignment';
export const PERSON_ALIGNMENT_FORMAT_VERSION = 1;
export const PERSON_ALIGNMENT_SOURCE = 'person';
/** The predicted sky's own convention, and the roll's. */
export const PERSON_ALIGNMENT_CONVENTION = { ...SKY_CONTEXT_CONVENTION, roll: 'upright_frame_positive_clockwise' } as const;
/**
 * How far from the person's centre the solver searches, in degrees [tested, align-2 on 19 November: the 13 public
 * photos with a place that no star search matched, the person simulated from astrometry.net's solution; on the four
 * a search at the right field of view matches, an alignment 0 to 10 degrees off gave a correct solve in 24 of 26
 * tries, 15 and 20 degrees off none;
 * no wrong solve among 80 calibrated results, and none of 76 wrong alignments or random skies calibrated;
 * .lead/kiro/reports/SOLVER/align-2-measure.md].
 */
export const PERSON_ALIGNMENT_UNCERTAINTY_DEG = 10;
/** The widest diagonal field of view taken, as a pose estimate's. */
export const PERSON_ALIGNMENT_FOV_MAX_DEG = 175;

export const PersonAlignmentSchema = z
  .object({
    format: z.literal(PERSON_ALIGNMENT_FORMAT),
    formatVersion: z.literal(PERSON_ALIGNMENT_FORMAT_VERSION),
    source: z.literal(PERSON_ALIGNMENT_SOURCE),
    centerAltDeg: wireNumber().min(-90).max(90),
    centerAzDeg: wireNumber().min(0).lt(360),
    rollDeg: wireNumber().min(-180).max(180),
    fovDiagonalDeg: wireNumber().positive().max(PERSON_ALIGNMENT_FOV_MAX_DEG),
  })
  .strict();
export type PersonAlignment = z.infer<typeof PersonAlignmentSchema>;
