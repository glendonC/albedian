# The predicted sky for an upload (layers 1 and 2)

Spec: `.kiro/specs/predicted-sky-for-every-upload/` (REQ-SKY-1, REQ-SKY-2, REQ-SKY-3). Code:
`services/science/predicted_sky.py`, `services/science/pose_estimate.py` and `services/science/camera_profiles.py`,
called by the solve job in `services/ingest/jobs.py`.

## What it is

When a photo or clip carries the time it was recorded and the place it was recorded, Albedian works out what was
above the horizon there at that moment: the bright stars of its pinned list, the Sun, the Moon, and the planets
Venus, Mars, Jupiter, Saturn and Uranus. It uses fixed astronomical formulas and catalogs, not the picture. Every
object in this layer is labelled `predicted`: computed for the time and place, never measured in the recording.
It works by day too; then it says the stars were up but outshone by daylight. [tested]

This is the first of three layers. The second lines the predicted sky up over the picture from the Moon or the Sun
when the camera's pointing can be worked out; the third is an exact match of the recorded stars (`catalog_matched`)
when enough stars are recorded and the held-out check passes. Layer 2 exists so far only for head-worn cameras
(below); without it no predicted object is placed on the picture, since nothing says where the camera pointed.
[tested]

## What a solve job does with it

After the plate solvers finish, or stop at their time limit, the job adds this layer before it writes the solution.
It adds no progress stage: the stages stay `store`, `decode`, `detect`, `solve` and `verify`. [tested]

| The solvers' result | What the person gets |
|---|---|
| `calibrated` (the stars matched and passed the held-out check) | The same solution, with the predicted objects that were not already matched to a recorded star added |
| `unresolved` or `ambiguous`, and a head-worn camera with the Moon found in one frame (layer 2, below) | `approximate`: the predicted objects lined up over that frame, with the uncertainty of the lining up |
| `unresolved` or `ambiguous` (too few stars, or no match that passed), with no pose | `predicted_only`: the predicted objects, the points the detector found kept as pixel positions (`observed`, with no sky position), the solvers' counts, warnings and name kept, and one sentence saying why the sky was not lined up |
| No recorded time, no recorded place, or a moment outside 1800 to 2050 | The solvers' result unchanged, with one sentence saying which is missing |

If computing this layer fails, the job keeps the solvers' result and adds the warning "The predicted sky could not be
computed." [tested]

The moment is the recorded start plus the frame's offset into the clip (`frameTimeSec`), written as the solution's
`observedAtUTC`. A layer 1 result uses the start of the clip, so the offset is 0; a layer 2 result uses the frame it
was lined up from. [tested]

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

## Layer 2 for head-worn cameras: lined up from the Moon, the camera's field of view and a level head

Spec REQ-SKY-3; code `services/science/pose_estimate.py` and `services/science/camera_profiles.py`. Camera glasses
often film the Moon with no horizon in view, so the horizon cannot fix the camera's tilt. Instead the pose rests on
the Moon's place in one frame and three assumptions: the camera's field of view, from its camera profile; that the
camera was not zoomed; and a level head. The result is `approximate`: the predicted sky drawn over that frame, with an honest bound on how far off it
may be, and never checked against stars. [tested]

### When it is tried

All of these must hold, else the result is layer 1's `predicted_only` with one more sentence saying which failed:
the solvers' result is not `calibrated`; the file has a recorded time and place in the ephemeris range; the file's
camera model matches a camera profile of kind `head_worn` exactly; the frame is not stored mirrored; the predicted
Moon is more than 5 degrees above the horizon (REQ-SKY-3.1); and one frame passes the Moon test and the fit. The
frames are read only once the earlier checks pass. [tested]

### The camera profile

One profile, keyed by the exact QuickTime model tag `Meta Ray-Ban Display Glasses`: kind `head_worn`, a diagonal
field of view of 80 to 110 degrees, labelled [open].

- Meta publishes a 100 degree field of view for the Ray-Ban Meta glasses' camera without naming its axis
  (https://www.meta.com/au/ai-glasses/compare/, "100° field of view, left-oriented" for Ray-Ban Meta (Gen 3)
  Wayfarer) [verified, read at source 2026-11-11]. A web search's excerpt of ray-ban.com's Ray-Ban Meta
  specification page gives the same figure, but that page answered HTTP 403 to a read that day, so it was not read
  at source. [open]
- No figure was found for the Meta Ray-Ban Display model. Its clip frames (1344 x 1792) are smaller than the
  1440 x 1920 video size retailers list, so a crop may narrow its field of view. [inference]
- The range of 80 to 110 degrees is the one the phone benchmark already uses for its modelled head-worn class
  (`research/benchmarks/phone_photos/cameras.py`); no product figure stands behind it. [open]
- The Display glasses have a zoom: a web search's excerpt of Meta's product page for them
  (https://www.meta.com/au/ai-glasses/meta-ray-ban-display-glasses-and-neural-band/) lists a "12MP camera with 3x
  zoom", but the page gave no readable text to a read on 2026-11-11, so it was not read at source. [open] The range
  is for no zoom, and the pose reads nothing in the file that would show a zoom; a zoomed clip sees a narrower field,
  which the sweep does not cover. [inference]

### Which frames are searched

A photo's own frame. For a clip, up to 16 frames spread over the whole clip by frame index: frame
floor((k + 0.5) N / 16) for k = 0 to 15, with N frames from the probed frame rate and duration, so each is the frame
holding the middle of one sixteenth of the clip (a clip of fewer than 16 frames gives each frame once). Each is
decoded alone by an accurate seek to half a frame before its time, at its stored size, in gray, with no scale filter,
and all of them within the decoder's 60 second bound; its time is its index over the frame rate. The first frame in
time order that passes the Moon test and the fit is used, and the solution's `frameTimeSec` is its time. The frames
are never stacked. [tested]

### The Moon test

Each frame is judged on its own. The Moon is found by the contour at half its peak height above the sky, not by
saturation [proposed: the lead's decision of 2026-11-11]. The Moon is a disk of a known angular size (its predicted
diameter from the ephemeris distance), and the contour at half its peak follows the disk's edge whatever the exposure
or tone curve; an unresolved light's region at half its peak is only its blur, a few pixels. A saturation rule cannot
see every Moon: the first real head-worn clip tried records its Moon as a peak, not a flat clipped core (frame maxima
241 to 244 of 255; within 3 codes of the top its spot is 1 to 4 px, but 8 codes down it is 146 px) [tested 2026-11-11,
counts only].

- The candidate is the frame's brightest point after a Gaussian of 2.5 px, the smoothing `horizon_solver.py` finds
  its Moon's peak with, so one hot pixel or a compression speck is not taken for it. Its peak and region are then
  measured on the frame's own pixels [proposed: smoothing would lower an unresolved light's peak and widen it toward
  the Moon's size]. [tested]
- The peak must be at least 200 of 255 [proposed: a night exposure records the Moon near its top code; a dimmer peak
  is a light or noise], and at least 10 times the noise above the sky: the sky is the frame's median, the noise 1.4826
  times its median absolute deviation (floored at 1e-4), as `horizon_solver.py` measures them, and 10 is the signal to
  noise its bright source test asks of its Moon [proposed]. [tested]
- The region is the pixels around the peak brighter than the sky plus half the peak's height over it; pixels
  touching at a side or a corner are one region. Its equivalent diameter, 2 sqrt(area / pi), must be at least 0.8
  times the Moon's predicted diameter at the profile's widest field of view (REQ-SKY-3.3: a distant point light is
  refused) and at most 8 times it at the narrowest [proposed: room for an overexposed Moon's bloom, but not a lit
  cloud or a band of twilight]. [tested]
- Every other region above that same level must be under half its area (REQ-SKY-3.3); two Moons of comparable size,
  a peak too dim or too faint, or no region of the right size are "not confirmed as the Moon". [tested]
- The Moon's predicted diameter is 2 asin(1737.4 km / its distance from the place), with 1737.4 km its mean radius
  (NASA, Moon Fact Sheet, https://nssdc.gsfc.nasa.gov/planetary/factsheet/moonfact.html) [verified, read at source
  2026-11-11].
- The spot's position is its sky-subtracted, brightness-weighted centre over the region grown by two pixels, so the
  disk's soft edge and any bloom count; its width comes from its second moments. Its reported signal to noise keeps
  the detector's rounding noise of the frame's code step as its floor, and it and the brightness are lower bounds
  where the brightest pixels are clipped. [tested]

### The fit and the sweep

- The Moon is placed where it is seen from the place: its ephemeris position lowered by its parallax shift (up to
  about a degree). Refraction is left out for the Moon and the stars alike, so it cancels to first order. [tested]
- The camera is a rectilinear (gnomonic) lens with no distortion, centred on the frame, its scale fixed by the
  diagonal field of view: 2 tan(field / 2) over the frame's diagonal in pixels. [proposed]
- For one field of view and one roll, the frame centre's direction and the frame's rotation are fitted so that the
  Moon lands on the spot's centre and the horizon has that roll in the frame as it is displayed (after its display
  orientation): two points at the frame centre's height, 10 degrees either side, sit at a row difference of
  tan(roll) times their column difference, a positive roll putting the horizon's right side lower. A fit that cannot
  reproduce these within half a pixel, or that shows the sky upside down, refuses the pose. [tested]
- The sweep: the profile's two limits and its middle (80, 95 and 110 degrees) by a roll of -10, 0 and +10 degrees
  [proposed: the lead's decision of a level head within 10 degrees], nine fits in all. The nominal one, the WCS the
  markers are drawn with, is the middle field of view at roll 0. The pose estimate keeps all nine, the largest angle between the nominal frame
  centre and a swept one (`maxCenterOffsetDeg`), and the Moon's predicted radius as the uncertainty of its centre
  (`beaconCentroidUncertaintyDeg`): an overexposed Moon's light sits somewhere on its disk, and a partly lit one's
  toward its lit edge. [tested]
- What the bound covers: for synthetic frames with a true field of view anywhere in 80 to 110 degrees, a true roll
  anywhere within 10 degrees, any display turn and the Moon anywhere in the frame, the Moon lands within half a pixel
  of its spot under every swept WCS, and every predicted star inside the frame lies within its ring (its largest
  distance to its nine swept positions plus the Moon's radius) [tested, P-SKY-5 under 20 seeds]. It covers nothing
  outside those ranges: a wider or narrower lens, a zoomed camera, a tilted head, lens distortion, or a spot that is
  not the Moon.
  [open]

### What the result holds

- Status `approximate`, the nominal WCS, the pose estimate, the frame's time and its moment (the recorded start plus
  the frame's time). Quality: the detector's count, one match (the Moon, to its own predicted position), no held-out
  pair and no held-out residuals; the held-out check is never passed. [tested]
- Findings: the detector's points as pixel positions (`observed`), less any in the same frame within the Moon spot's
  radius, so the Moon is listed once; points the detector found in other frames of a clip are kept as measured, and
  the Moon's finding says so, since the head moved between frames. Then the Moon itself, `observed` at its measured
  pixel with no sky position. Then the predicted sky for the frame's moment, each object's subtitle saying whether
  the nominal WCS puts it inside the frame; the ones inside also say they are placed only through the approximate
  pose. [tested]
- Warnings: the solvers' warnings; one sentence on how it was lined up (the Moon's pixel, the frame's time into the
  clip, the assumptions, and "within about N degrees near the Moon, more toward the edges; not checked against
  stars", N being the centre offset plus the Moon's radius rounded up); and the sentence for where the Sun was. None
  holds the capture time, the place or the file name. [tested]
- It never makes a `catalog_matched` finding, never gives an observed point a sky position, and never marks the
  held-out check as passed. [tested]

### What is open

- The field of view range, no zoom and the roll range are assumptions, not measurements of the owner's glasses.
  [open]
- The first real head-worn clip tried, tagged limited range, now ends `approximate`: 5 of its 16 spread frames pass
  the Moon test (peaks 234 to 236 of 255 over a sky at 1) and the first of them is used. There the Moon's region at
  half its peak is 588 px, 27.4 px across, about 3 times the 9.1 px predicted at the profile's middle field of view
  and inside the 5.5 to 94.5 px limits; the other 11 frames peak at 1 to 140 [tested 2026-11-11, counts only].
  What makes the real clip's Moon about 3 times its predicted size is unknown: bloom, motion blur, or zoom. The
  clip's metadata does not say whether it was zoomed (its comment tag names no zoom, lens or focal
  length, by the lead's check of 2026-11-11). If it was, its field of view is narrower than 80 degrees, outside the
  swept bound, and the rings do not cover the error. [open]
- The candidate is each frame's brightest point after smoothing, so a lamp, a lit cloud's edge or a glint of about
  the Moon's size, at 200 or more and brighter after smoothing than anything else in its frame, would be taken for
  the Moon; a brighter light of another size beside the Moon refuses that frame rather than guessing. [open]
- Lens distortion is not modelled, and head motion between frames is not tracked: the pose holds for its one frame.
  [open]
