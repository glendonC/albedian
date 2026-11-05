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
