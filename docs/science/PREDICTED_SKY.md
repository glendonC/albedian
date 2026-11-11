# The predicted sky for an upload (layer 1)

Spec: `.kiro/specs/predicted-sky-for-every-upload/` (REQ-SKY-1, REQ-SKY-2). Code: `services/science/predicted_sky.py`,
called by the solve job in `services/ingest/jobs.py`.

## What it is

When a photo or clip carries the time it was recorded and the place it was recorded, Albedian works out what was
above the horizon there at that moment: the bright stars of its pinned list, the Sun, the Moon, and the planets
Venus, Mars, Jupiter, Saturn and Uranus. It uses fixed astronomical formulas and catalogs, not the picture. Every
object in this layer is labelled `predicted`: computed for the time and place, never measured in the recording.
It works by day too; then it says the stars were up but outshone by daylight. [tested]

This is the first of three layers. The second lines the predicted sky up over the picture from the Moon or the Sun
when the camera's pointing can be worked out; the third is an exact match of the recorded stars (`catalog_matched`)
when enough stars are recorded and the held-out check passes. Uploads do not reach layer 2 yet, so here no predicted
object is placed on the picture: nothing says where the camera pointed. [tested]

## What a solve job does with it

After the plate solvers finish, or stop at their time limit, the job adds this layer before it writes the solution.
It adds no progress stage: the stages stay `store`, `decode`, `detect`, `solve` and `verify`. [tested]

| The solvers' result | What the person gets |
|---|---|
| `calibrated` (the stars matched and passed the held-out check) | The same solution, with the predicted objects that were not already matched to a recorded star added |
| `unresolved` or `ambiguous` (too few stars, or no match that passed) | `predicted_only`: the predicted objects, the points the detector found kept as pixel positions (`observed`, with no sky position), and the solvers' counts, warnings and name kept |
| No recorded time, no recorded place, or a moment outside 1800 to 2050 | The solvers' result unchanged, with one sentence saying which is missing |

If computing this layer fails, the job keeps the solvers' result and adds the warning "The predicted sky could not be
computed." [tested]

The moment is the recorded start plus the frame's offset into the clip (`frameTimeSec`), written as the solution's
`observedAtUTC`. The solve job uses the start of the clip, so the offset is 0. [tested]

### Which times and places count

- A recorded capture time: EXIF `DateTimeOriginal` with its UTC offset, or QuickTime
  `com.apple.quicktime.creationdate`. A file's own written time (QuickTime `creation_time`) and an EXIF time without
  its offset are not capture times, and no other time (the upload time, the computer's clock) stands in. [tested]
- A recorded place: EXIF GPS, or a QuickTime ISO 6709 location. [tested]
- No warning, job record or error holds the capture time, the place or the file name: they say "the recorded
  capture time" and "the recorded place". [tested]

## Where the Sun was

The last warning is one sentence that names the Sun's height above or below the horizon, rounded to a whole degree,
and its band. The bands are USNO's, from the height of the Sun's centre, without refraction (USNO, "Rise, Set, and
Twilight Definitions", https://aa.usno.navy.mil/faq/RST_defs) [verified, read at source]:

| Band | Height of the Sun's centre | What USNO says of it |
|---|---|---|
| Day | above 50 arcminutes below the horizon (-0.833 degrees: 16 for the Sun's radius, 34 for refraction) | The Sun's upper edge is on or above the horizon |
| Civil twilight | -0.833 to -6 degrees | Sunlight scattered in the upper atmosphere still lights the sky |
| Nautical twilight | -6 to -12 degrees | The horizon can still be seen |
| Astronomical twilight | -12 to -18 degrees | Scattered sunlight is not yet fainter than starlight |
| Night | below -18 degrees | Scattered sunlight is fainter than starlight |

USNO sets the edges but not which side an exact edge value falls on. Here day is strictly above -0.833 degrees, and
each twilight band holds its darker edge (exactly -6 is civil twilight, exactly -18 astronomical). [proposed]

## The star list and its limits

- The 71 pinned bright stars (`services/science/catalog.py` `get_all_sky_catalog_stars`): 59 Hipparcos 2007 rows
  and 12 Gaia DR3 rows, magnitudes -1.46 to 3.77. Each is carried from its catalog epoch to the date by its proper
  motion, and its limitation names its catalog release. [tested]
- Each star's position is given within 55 arcseconds: the horizon transform leaves out nutation and annual
  aberration (at most 37 arcseconds, `tests/integration/test_sky_transforms.py`) and UT1 - UTC (at most 14), and the
  pinned positions sit within 3 arcseconds of their catalog rows. [tested]
- Only these 71 stars: the fainter stars of the G 12 tiles are not in this layer. [tested]
- Above the horizon means a height above 0 degrees without refraction. Refraction lifts what is near the horizon by
  about 34 arcminutes (USNO, above), so an object listed just above the horizon may be hidden and one just below may
  show. Hills, buildings, clouds, haze, light pollution and the brightness of the sky are not taken into account:
  the list says what was above the horizon, not what could be seen. [open]

## The ephemeris and its limits

`services/science/ephemeris.py`, valid only from 1800-01-01 to 2050-12-31 UTC; outside it the layer is left out
with a sentence saying so. [tested]

- The Sun and the planets come from the approximate Keplerian elements of Standish and Williams (1992), as JPL's
  Solar System Dynamics group publishes them for 1800 to 2050, seen from the Earth-Moon barycentre. The Sun is the
  barycentre's own position reversed, so it adds no model. Its stated uncertainty, about 45 arcseconds, holds the
  barycentre's published error, the barycentre's offset from the Earth's centre (up to 5,000 km) and the observer's
  offset from it (6,378 km). At the moments of USNO's "Earth's Seasons" table for 2030 and 1850
  (https://aa.usno.navy.mil/data/Earth_Seasons), the Sun's declination of date is 0 at the equinoxes and the
  obliquity at the solstices within that uncertainty; the largest difference over the seven moments was
  9 arcseconds. [tested]
- Each planet's uncertainty is computed for the moment from the published errors. At three moments (1850, 2030 and
  2050) it ran from 70 arcseconds (Venus) to 901 arcseconds, a quarter of a degree (Saturn). [tested]
- The Moon comes from Meeus (1998) chapter 47, cut to its largest terms, within 1,300 arcseconds. It is geocentric:
  from a place on the Earth it appears up to about a degree lower, its horizontal parallax of 54 to 61 arcminutes
  (USNO, above), and its limitation states the shift for the moment. [tested]
- No brightness is computed for any body. [tested]

## What this layer never does

It never changes an observed point's pixels, never gives one a sky position, never makes a `catalog_matched`
finding, and never marks the held-out check as passed. A calibrated result keeps its status, WCS, quality and every
finding as the solver wrote them. [tested]
