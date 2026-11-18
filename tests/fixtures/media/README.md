# Media fixtures

## sips-colour-*.heic (the colour HEIC tests of `tests/media/test_decode.py`)

Synthetic colour stills that the Mac's own `sips` encoded as HEIC, so the decoder meets the Y'CbCr layout Apple's
encoder writes (a tile grid of 512 x 512 HEVC tiles, its crop, and an `nclx` colour property) without the tests
needing `sips`. They hold no photograph, place, time or device of anyone: no EXIF, no XMP and no ICC profile.

How they were made, on macOS 26.7 with `sips-316`:

1. Pillow wrote an RGB PNG of six flat vertical bands, left to right (200, 30, 30),
   (30, 200, 30), (30, 30, 200), (220, 220, 40), (40, 220, 220) and (128, 128, 128), band `i` covering the
   columns from `round(i w / 6)` up to `round((i + 1) w / 6)`, with no profile and no metadata.
2. `sips -s format heic <frame>.png --out <frame>.heic`. For a PNG with no profile, sips writes an `nclx` `colr`
   property (primaries 2 and transfer 2, both unspecified; matrix 6, BT.601; full range) and no ICC profile, and
   one `irot` box with angle 0.

| File | Frame | Layout | Bytes | SHA-256 |
|---|---|---|---|---|
| `sips-colour-single-96x64.heic` | bands, 96 x 64 | one image (under one tile) | 529 | f28a1ad181afec52b2b1b41811cd7b18df42706774c87de984307b547470bbcd |
| `sips-colour-grid-1100x700.heic` | bands, 1100 x 700 | tile grid, 3 x 2 tiles (1536 x 1024), cropped | 2,665 | 2fb86ad38c8d44519cb1f9803737a2c4ba2b96857d594587220e7f0a49946883 |

ffprobe 8.1.1 reports each tile as `yuvj420p`, colour range `pc`, colour space `smpte170m`, chroma location
`left`, transfer and primaries unknown. Decoded by `services/media/decode.py`, the middle row of each band of the
single image comes back within 1 code of the band's colour [tested on this machine, 2026-11-12].
