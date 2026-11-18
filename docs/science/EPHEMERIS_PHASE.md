# The Moon's phase, the planets' magnitudes, rise and set

Code: `services/science/ephemeris.py` (`moon_phase`, `apparent_magnitude`, `rise_transit_set`) and
`services/science/radiometry.py` (its phase now comes from `moon_phase`). Tests:
`tests/integration/test_ephemeris_phase.py`, `test_ephemeris_magnitudes.py` and `test_ephemeris_rise_set.py`.
Everything here is `predicted`: computed from a time and a place, never measured in a picture. Each value says
which frame it is seen from and what it leaves out.

## The Moon's phase

The phase angle is the angle at the Moon between the Sun and the observer, from the same ERFA vectors as the
Moon's and the Sun's positions ([SKY_TRANSFORMS.md](SKY_TRANSFORMS.md)). The illuminated fraction is that of a
sphere, (1 + cos i) / 2. With a place it is seen from the place (topocentric), without one from the Earth's centre
(geocentric), and the record says which. The two differ by up to the angle the place's offset from the Earth's
centre subtends at the Moon, about the Moon's parallax; at the tests' synthetic moment and place, by 0.604 degree.
[tested]

Its bound comes from the Moon's and the Sun's documented position errors. Against JPL Horizons' stored
rows (four open-ocean places and the Earth's centre, synthetic instants) the phase angle stays within that bound plus
the down-leg aberration Horizons says its angle carries. [tested] The name follows 45-degree sectors of the
synodic angle, the convention `radiometry.py` always used; within the synodic angle's bound of an edge no name is
given.

`radiometry.py` used a six-term series and was off Horizons' phase angle by up to 2.17 degrees over the 32 stored
geocentric rows; on `moon_phase` it is within 0.016 degree (its output is rounded to 0.01). [tested]

## The planets' magnitudes

Mallama and Hilton (2018), "Computing apparent planetary magnitudes for The Astronomical Almanac" (Astronomy and
Computing 25, 10-24), read at the source: the formulas it gives for an observer on the Earth, each only inside the
range it states, and refused outside rather than extrapolated:

| Planet | Formula | Range |
|---|---|---|
| Mercury | Eq. 2 | 2 < phase angle < 170 degrees (the observed span the paper names) |
| Venus | Eq. 3, Eq. 4 | 0 < phase angle <= 163.7, and 163.7 < phase angle < 179 |
| Mars | Eq. 6 | phase angle <= 50 |
| Jupiter | Eq. 8 | phase angle <= 12 |
| Saturn, with its rings | Eq. 10 | phase angle < 6.5 and ring tilt < 27 |
| Uranus | Eq. 14 | the angles seen from the Earth |
| Neptune | Eq. 16 | before 1980.0, 1980.0 to 2000.0, after 2000.0 |

- No uncertainty is stated: the paper gives no residual of its formulas against the observations. [verified: read]
- Mars leaves out two corrections (the longitude of the sub-Earth meridian, up to 0.060 mag by the paper, and the
  season), because their tables are not printed in the paper. Together they can change Mars by about 0.21 mag at most
  [inference: the paper's Table 3 gives the season term a range of 0.15].
- Uranus uses the flattening from the IAU report's radii, 0.022927. The paper prints 0.0022927, the same digits with
  one zero too many [inference: a misprint].
- The paper doubts Neptune's long-term predictive accuracy: it brightened from about 1980 to 2000 for a reason not
  known.

JPL Horizons' manual says its planetary magnitudes "are based on" the same paper, so the stored Horizons rows check
this code, not the model. Every stored planet row agrees within an allowance computed for that row: Horizons'
printing, the formula's slope times the phase angles' difference, and the position bounds. Mars also gets the 0.21
mag above. For Neptune after 2000.0 Horizons' rows follow the paper's Eq. 17, whose phase term Eq. 16 leaves out
(up to about 0.016 mag here). [tested] Two Mercury rows lie past the observed span: Horizons gives a number there,
this code refuses. [tested]

## Rise, transit and set

USNO's definitions (aa.usno.navy.mil/faq/RST_defs, read at the source):

- Sunrise and sunset: the Sun's upper edge on the horizon, its centre 50 arcmin below (16 arcmin radius, 34 arcmin
  refraction).
- Moonrise and moonset: the Moon's upper edge on the horizon, with the Moon's own radius from the place.
- A planet is taken as a point.
- Transit: the centre crosses the meridian.

Each is computed on the topocentric, airless altitude at sea level. The convention assumes standard refraction and a
flat, level horizon at sea level. Hills, buildings and the real air of the night are not known.

Each time carries a spread: the rms deviation of 144 measured refractions at the horizon, 0.16 degree (Schaefer and
Liller 1990, PASP 102, 796, read at the source), over the body's own rate of altitude change at that event. It is
one standard deviation of a measured spread, not a bound. Where the body's highest or lowest altitude next to a rise
or set lies within 0.32 degree of the threshold, or its altitude changes so slowly (as near a pole) that the 0.64
degree 95 percent range of refraction spans a whole day or more, no time is stated. That 0.32 degree is half the
paper's 95 percent range of refraction [proposed]: in real air the body may not rise or set there at all. A near-miss
with no crossing is listed as a graze, with the time of its turning point (refraction does not move that time).

All 419 rises, transits and sets in the stored Horizons tables agree within their allowance. Horizons prints the
minute after each event. Beyond that minute, the allowance is the gap between its refraction model and USNO's 34
arcmin, and the position bound, both over the event's rate. [tested]

## What the stored Horizons answers are

`scripts/fetch_horizons_ephemeris_checks.py --set phase` asked JPL Horizons once, one request at a time, at synthetic
instants and three synthetic three-day spans, from four open-ocean places and the Earth's centre. The answers are
kept as received, with their requests and SHA-256, in `tests/fixtures/horizons_phase_rts.json.gz`. The Horizons
pages state no data licence: they link Caltech/JPL's notice, which says documents on its server "may be protected"
by copyright and that "Permission to reproduce may be required".
