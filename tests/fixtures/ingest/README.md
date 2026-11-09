# Ingest fixtures

## synthetic-exif.heic

A synthetic still for the HEIC reader in `services/ingest/metadata.py`, so the tests meet the box
layout a real encoder writes. Every value in it is invented; it holds no picture, place, time or
device of anyone.

How it was made, on macOS:

1. Pillow 13 wrote a grey 640 x 480 JPEG (every pixel 40) with these EXIF tags:
   - IFD0: `Make` Synthetic, `Model` Synthetic Test Camera.
   - Exif IFD: `DateTimeOriginal` 2031:06:01 04:37:52, `SubSecTimeOriginal` 120,
     `OffsetTimeOriginal` +02:00, `LensModel` Synthetic 24 mm, `FocalLength` 42/10,
     `FocalLengthIn35mmFilm` 24, `ExposureTime` 10/1, `FNumber` 18/10, `ISOSpeedRatings` 1600.
   - GPS IFD: `GPSLatitudeRef` N, `GPSLatitude` 46 31 10.92, `GPSLongitudeRef` E,
     `GPSLongitude` 7 59 9.96, `GPSAltitudeRef` 0, `GPSAltitude` 3466, `GPSHPositioningError` 5.
2. `sips -s format heic --embedProfile sGrey-v4.icc synthetic-exif.jpg --out synthetic-exif.heic`
   converted it, embedding a public-domain grey profile in place of the one sips adds by default.

The profile: `profiles/sGrey-v4.icc` from https://github.com/saucecontrol/Compact-ICC-Profiles at
commit bdd84663061bc4ae95ca70decff54f581e27f702, released under CC0 1.0 Universal (the repository's
`license` file and readme). It is 360 bytes, SHA-256
00c0f94e09127520a17dc0e1d9264b5702081d96dbdb1549ee88e3631ce42a9d, and sits byte for byte in the
file's `colr` box. It was downloaded with the User-Agent `Albedian/0.1 (research prototype)`.

What the file holds, box by box (1,921 bytes; SHA-256
8678d2b91d59cbd4d2bf8a192b4c672d5bebfbde2e0384fb79438a55f130487f):

- `ftyp`: major brand `heic`; compatible `mif1`, `MiPr`, `miaf`, `MiHB`, `heic`.
- `meta`: `hdlr` (`pict`), `dinf` with `dref`, `pitm` (primary item 1), `iinf` with three `infe`
  (item 1 `hvc1`, item 2 `Exif`, item 3 `mime` of type `application/rdf+xml`), `iref`, `iprp`
  with `ipco` (`colr` holding the sGrey profile above, description "sGry", copyright "CC0"; `ispe`
  640 x 480; `irot` 0; `pixi`; `hvcC`) and `ipma`, and `iloc` for the three items.
- `mdat`: the HEVC image (item 1), the EXIF block (item 2) and an XMP packet (item 3) that sips added:
  `x:xmptk` "XMP Core 6.0.0" and `photoshop:DateCreated` 2031-06-01T04:37:52, a copy of the invented
  capture time without its zone.

The EXIF block (430 bytes) holds exactly the tags listed in step 1, plus IFD0's two pointers to the
Exif and GPS IFDs; it has no thumbnail (IFD1) or Interop IFD, and sips added no tag of its own.

## sips-*.heic (the HEIC tile grid and orientation tests)

Synthetic grey stills that the Mac's own `sips` encoded as HEIC, so the tests meet the layout Apple's encoder
writes (512 x 512 HEVC tiles in a tile grid, an `irot` box) without needing `sips` themselves. They hold no
photograph, place, time or device of anyone: no EXIF and no XMP.

How they were made, on macOS 26.7 with `sips-316`:

1. Pillow wrote a grey PNG of each frame. The "marked" frames are 10 everywhere, 230 in the top-left eighth of
   the width and quarter of the height, and 120 in three 5 x 5 dots at (w/2, h/2), (3w/4, h/3) and (w/3, 3h/4).
   The simulated frame is the phone-photo benchmark's `ci` suite frame 0 rendered at 1024 x 768 (its binning
   scaled to match) and turned to luma with the Rec. 709 weights: a simulated star field, not a photograph.
2. `sips -s format heic --embedProfile sGrey-v4.icc <frame>.png --out <frame>.heic`, with the same CC0 grey
   profile as above (copied byte for byte from `synthetic-exif.heic`), so no Apple profile is embedded.

| File | Frame | Layout | Bytes | SHA-256 |
|---|---|---|---|---|
| `sips-grid-4032x3024.heic` | marked, 4032 x 3024 | tile grid, 8 x 6 tiles | 12,436 | 46ff73198f349e58d4bf6dde9e67133454b5be09b92b53b23c8b075a68abc2bd |
| `sips-grid-3024x4032.heic` | marked, 3024 x 4032 | tile grid, 6 x 8 tiles | 12,396 | 3683615e97a7cb78c18ae9cc12887a1ad354ec18a883372ae1b1f08f169fe49a |
| `sips-grid-1024x768.heic` | marked, 1024 x 768 | tile grid, 2 x 2 tiles | 1,967 | 84a8c9235999ced3fc43c71d80a65283a6ddf6dfda486512b33a9efee5f05284 |
| `sips-grid-1100x700.heic` | marked, 1100 x 700 | tile grid, 3 x 2 tiles, cropped | 2,493 | 01fc7c42dab9b851b0b271fd9c6a4c848c4c050024860401b3f45888236ee8ca |
| `sips-single-480x360.heic` | marked, 480 x 360 | one image (under one tile) | 1,115 | 923a0350372af4cdee7a96689ba612f4c2e71175fbf215665fb0513e33a41d6d |
| `sips-simulated-ci00-1024x768.heic` | simulated star field, 1024 x 768 | tile grid, 2 x 2 tiles | 164,643 | fb2ce6fad03f352c23181fdbfa79d96efbf3b362ff69987f55b6cea18c820d93 |

Each has one `irot` box with angle 0; the tests rewrite its one-byte body for the other angles, since `sips -r`
rotates the pixels instead of writing `irot`.
