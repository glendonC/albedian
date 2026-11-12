# The sky transforms: the apparent place and the rigid frame rotation

Code: `services/science/catalog.py`. Tests: `tests/integration/test_sky_transforms.py` and
`tests/integration/test_moon_pose_paths.py`. Every step is one of the IAU's ERFA routines (pyerfa,
[THIRD_PARTY.md](../architecture/THIRD_PARTY.md)); the time scales come from `time_scales` (UTC with ERFA's leap
seconds from 1960, UT1 taken as UTC within 0.9 s; before 1960 the moment is taken as UT1).

There are two ways to turn an ICRS direction to the horizon, and they are named apart so that no caller takes the
wrong one by a default.

## The apparent place: where something is seen

`icrs_to_altaz` and its inverse `altaz_to_icrs`, ERFA's eraAtciq and eraAtioq: light deflection by the Sun, annual
and diurnal aberration, IAU 2006/2000A precession and nutation, and the Earth's rotation, for a place at sea level,
airless unless refraction is asked for (Saemundsson, 1010 hPa and 10 degrees C). Everything that states where a body or
a star is seen uses it: the ephemeris records' altitude and azimuth, every finding's altitude and azimuth, the
predicted sky, and whether the Moon is up. It agrees with the solver lane's independent benchmark chain within that
chain's stated omissions (0.84 arcsec) plus the deflection ERFA applies. [tested] It leaves out polar motion (under
1 arcsec) and UT1 - UTC (up to 13.5 arcsec of rotation). [inference, from the IERS bounds]

It is not a rotation: aberration and deflection move directions by different amounts across the sky. Two directions
55 degrees apart, one toward the Earth's motion, change their angle by more than 5 arcsec. [tested]

## The rigid frame rotation: what a screen draws with

`rigid_horizon_rotation` (a 3 x 3 matrix from ICRS to east, north and up), `icrs_to_rigid_altaz` and
`rigid_altaz_to_icrs`: ERFA's bias-precession-nutation matrix, the Earth rotation angle and the place's horizon
(eraAtioq), without deflection or aberration. It is a rotation (orthonormal, determinant one), keeps every angle,
and its round trip is exact within 1e-9 arcsec. [tested, a property under 20 seeds] It never states where anything is
seen.

`anchored_horizon_rotation` turns it by the least rotation that puts one beacon exactly where it is seen. A screen that
draws catalog positions through one TAN WCS draws them through one such rotation, so the two poses
(`services/science/pose_estimate.py`: the head-worn pose and, since sky-4, the Moon plus horizon pose) use it for that
alone: the WCS is the camera seen through the rotation anchored on the Moon. Everything else about a pose is on the
apparent sky: the camera is level in the true horizon, the closed form (the head-worn pose) or the fit's start (the
Moon plus horizon pose) takes the Moon's apparent altitude and azimuth, and the centre a pose states (`centerAltDeg`,
`centerAzDeg`) is its pointing in the apparent horizon, where the camera's centre is seen.
[tested: the pose's truth is the apparent sky]

## How far apart they are

What the rigid frame rotation leaves out of the apparent place, at most, for moments from 1800 to 2050:

| Part | At most | From |
|---|---|---|
| Aberration | 21.178 arcsec | The observer's barycentric speed over ERFA's speed of light: the Earth's greatest speed from eraEpv00 over the span, 30.3158 km/s, plus the rotation at the equator, 0.4651 km/s (eraApco carries the rotation in its velocity and sets its separate diurnal term to 0) |
| Light deflection | 5.857 arcsec | ERFA's restrained eraLdsun at its peak, about 5 arcmin from the Sun's centre, at the Earth's least distance from the Sun (0.983166 au); 1.75 at the limb, under 0.01 a degree away |
| Together | 27.035, held as 27.1 arcsec | `RIGID_FRAME_OMISSION_ARCSEC` |

Measured on 2026-11-11 over 6,000 random moments, places and directions, half of them within 20 arcmin of the Sun: at
most 26.62 arcsec. [tested: every direction the property draws lies within the parts and the total]

## What a pose's positions leave out: the frame term

Anchored on a beacon m, the rotation draws any other direction n off its apparent place by the aberration and
deflection that differ from the beacon's. First-order aberration moves a direction p by k (u - (u . p) p), k the
observer's speed over the speed of light and u its direction; after the least rotation that makes m exact, what is
left at n is k [u (1 - m . n) + (u . n) (m - n)], at most k |m - n|, the chord 2 sin(theta / 2) of their angle
theta [inference: that map's largest eigenvalue at 12 angles to 5 digits]. So `anchored_rotation_omission_arcsec`
states, for a field of frames (each its centre and half its diagonal field of view):

    k 2 sin(theta / 2) + 2 D + 0.01 arcsec

with theta the farthest a frame point lies from the beacon (the beacon's angle from a frame's centre plus half its
diagonal, by the triangle inequality: the field of view and where the beacon sits), k the moment's own (at most
21.178 arcsec), D the larger of the deflection ERFA applies at the nearest a frame comes to the Sun and the one at
the beacon itself, once for the direction and once for the beacon, and 0.01 for aberration's second order (about
0.002). A beacon inside a frame is never nearer the Sun than that frame, so for a pose D is the frame's; a beacon
outside every frame and nearer the Sun brings its own larger deflection to the whole field (a random seed found that
case on 2026-11-12, and it is now a test). [tested] For a Moon 15 degrees from the centre of a
110 degree frame it is about 21 2 sin(35 deg) = 24 arcsec at night. Over 3,000 random ERFA cases, near the Sun
included, the miss came to at most 0.9997 of k 2 sin(theta / 2) plus the two directions' own deflections, and never
over it (measured by the ephemeris lane on 2026-11-11). [tested, a property under 20 seeds, at a field's edge]

The pose adds this frame term to its beacon term, next to the Moon's predicted radius and the Moon record's own stated
uncertainty, so every ring a pose draws holds it; it is tested at every corner of every swept frame. [tested] Against
the rest of a pose's bound (degrees, from the swept field of view and roll) it is small, and it is stated anyway.

## The Moon a pose lines up

The ephemeris record (`services/science/ephemeris.py`) is topocentric whenever a place is given: its parallax is
applied once, at the source, and no pose shifts it again. The Moon each pose path uses (the head-worn pose's and the
horizon pose's) lies within the record's stated uncertainty of ERFA's own topocentric chain (eraAtco13, given the
Moon with its parallax), the Moon near the horizon included, and either pose's frame rotation takes it to where
ERFA sees it. [tested, a property under 20 seeds]

## A place

Every function here that takes a place refuses a latitude outside -90 to 90 degrees or a longitude outside -180 to
180, or either one not finite, with `PlaceOutOfRange` (catalog.py `require_place`), as the API's requests do. A
latitude past a pole names no place: the transforms would answer for the point on the far meridian, its azimuth half
a turn off. [tested: tests/integration/test_place_doors.py, at each end of both ranges for every such function]

## The Moon's phase, the planets' magnitudes, rise and set

The same vectors give the Moon's phase, the planets' magnitudes (Mallama and Hilton 2018) and rise, transit and set
by USNO's convention, each with its method's stated limits: [EPHEMERIS_PHASE.md](EPHEMERIS_PHASE.md).
