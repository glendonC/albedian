# Sky dome registration

The landing scenes draw an all-sky dome behind the catalog stars from ESO's Milky Way panorama (`eso0932a`). Both renderers sample the dome by view direction through the same ICRS-to-scene orientation that positions the Gaia DR3 and Hipparcos catalog stars. On screen, the panorama's star images fall a median 0.22 to 0.23 texels from their catalog stars, at most 1.08 [measured, section 3].

The panorama is not in exact galactic coordinates. Its axes sit 3.78 degrees from galactic, and the stitching of its almost 300 fields leaves smooth offsets of up to 2.2 texels. `scripts/register_sky_panorama.py` fits both against catalog stars and writes the registration solution (`eso0932a_registration.json`) used by the renderers and the starless diffuse bake (`scripts/bake_starless_panorama.py`). Asset provenance is recorded in [`PROVENANCE.json`](../../apps/web/public/textures/PROVENANCE.json).

A texel here is one pixel of the 6000-pixel-wide panorama: 360/6000 degrees, 216 arcseconds.

## 1. What the dome draws

| File | What it is | Projection | Sampled through |
|---|---|---|---|
| `eso_milkyway_diffuse_6k.webp`, 6000x3000 | ESO/S. Brunier, *The Milky Way panorama* (eso0932a, 2009), CC BY 4.0, modified by `scripts/bake_starless_panorama.py`: resolved point stars removed using the registered catalog and local grain filter so the WebGPU landing draws point stars solely from the catalog tiles (to G 12) over the photograph's diffuse light; Venus and Jupiter preserved as photographed. [tested] | Same plate carree projection and dimensions as `eso_milkyway_panorama.jpg`. [verified] | `eso0932a_registration.json` (WebGPU cislunar host) |
| `eso_milkyway_panorama.jpg`, 6000x3000 | ESO/S. Brunier, *The Milky Way panorama* (eso0932a, 2009), CC BY 4.0. A photograph stitched from almost 300 fields. Identified from its embedded ESO metadata. [verified] | Plate carree, galactic plane horizontal, longitude increasing to the left. ESO names no projection; the frame was measured (section 2). [measured] | `eso0932a_registration.json` (WebGL renderer and registration reference) |

An earlier 1024x512 NASA SVS Deep Star Maps print (`nasa_starmap_4k.jpg`, a plotted catalog star map in right ascension and declination) is not loaded (finding 1).

## 2. Registering the panorama

### Model

From an ICRS unit vector `i` to a panorama pixel (x right, y down; pixel k spans [k, k + 1)):

1. `g = A_G i`: galactic coordinates. `A_G` is the ICRS-to-galactic matrix of the Hipparcos catalogue (ESA 1997, vol. 1, sect. 1.5.3), also given in the Gaia DR3 documentation (sect. 4.1.7). The tests check it against its defining pole and node.
2. `p = R g`: the panorama's own frame, `R` fitted.
3. `lon = atan2(p_y, p_x)`, `lat = asin(p_z)`; `x = W frac(0.5 - lon / 2 pi)`, `y = H (0.5 - lat / pi) + y0`, with `y0` fitted.
4. An offset table, looked up at the rotated position, moves the point by (east, north) texels: `x -= east / cos(lat)`, `y -= north`.

The renderers do the same from the scene direction, `p = R A_G S^T d`, where `S` is `ICRS_TO_SCENE_QUATERNION` in [`CatalogStarField.ts`](../../apps/web/src/spatial/catalogStars/CatalogStarField.ts).

### Fit

Code: pure functions in [`services/science/sky_registration.py`](../../services/science/sky_registration.py), run by [`scripts/register_sky_panorama.py`](../../scripts/register_sky_panorama.py). Inputs are repository files only: the panorama (pinned by SHA-256) and catalog tile layers 0 and 1 (to magnitude 8), pinned by a digest of their 60 core tiles, so new tile kinds elsewhere in the catalog leave the solution valid.

- **Stars:** the 5,112 catalog stars to magnitude 6.0 with no star within 0.3 degrees brighter than their own magnitude plus 2.
- **Measurement:** a difference-of-Gaussians image (sigma 1 and 8 pixels, wrapping in longitude). For each star, the brightest peak within 3 pixels of its modelled position, refined with a Gaussian-windowed centroid (sigma 1.5 pixels, re-centred until it settles). Unlike a fixed box, the window has no bias where a star falls on a pixel corner.
- **Rigid fit:** coarse to fine from galactic axes: search radii of 80, 40, 14 and 6 pixels on progressively fainter stars, then a robust least-squares fit (soft L1) of the rotation and `y0`. Stars more than 3.5 texels off are dropped, leaving 5,021 of the 5,078 matched.
- **Offset table:** the remaining residuals, as tangent vectors on the sphere, smoothed by Gaussian kernel regression with half a pseudo-star of zero offset everywhere, so the table relaxes to zero where stars are sparse. Evaluated on a 2 degree grid (180x90, within 0.1 texel of a 1 degree grid) and stored as bytes at 1/32 texel.
- **Held-out check:** two folds of alternate stars. The rigid model is fitted on one fold, the table built from that fold's residuals, and both are scored on the other fold. The kernel width is the one with the smallest held-out median.

### Results [measured]

Rigid solution: `R` is a 3.78 degree rotation. The image centre is at galactic (l 3.23, b -1.69) and its top edge at (l 330.83, b 88.00), with `y0` = -1.30 pixels. The offset table reaches 2.16 texels.

| Held-out residual (5,021 stars, 2 folds) | Median | 90th percentile | RMS |
|---|---|---|---|
| Rotation and offset only | 0.97 texel | 1.87 | 1.22 |
| With offset table, kernel 1.5 deg | 0.55 | 1.26 | 0.82 |
| With offset table, kernel 2 deg | 0.47 | 1.08 | 0.72 |
| **With offset table, kernel 3 deg (used)** | **0.44 (95 arcsec)** | **0.94** | 0.67 |
| With offset table, kernel 4 deg | 0.47 | 0.96 | 0.68 |
| With offset table, kernel 6 deg | 0.56 | 1.05 | 0.75 |

A table built from one half of the stars predicts the other half's residuals, so most of what the rotation leaves is a smooth distortion of the mosaic, not measurement noise. It comes in patches of about 10 degrees, the size of the mosaic's fields: [offset table](img/sky-registration/offset-table.png) (red east, green north, mid-grey zero, full scale 2 texels). The rotation alone leaves a median of about a texel and a 90th percentile of nearly two: 2 to 4 pixels at 4K, enough to set a photographed star beside its sprite.

## 3. In both renderers

- **WebGL**, `skyMat` in [`SparkPortalRenderer.ts`](../../apps/web/src/spatial/SparkPortalRenderer.ts): the vertex shader passes the direction from the camera, and the dome mesh has no rotation. The panorama texture is bound only once its solution has loaded.
- **WebGPU host**, [`skyBackground.ts`](../../apps/web/src/spatial/webgpu/cislunar/skyBackground.ts): the background node uses `positionWorldDirection` and is attached only once the solution has loaded.
- Both apply the rotation as one `mat3`, sample the offset table (RG8, bilinear, repeating in u), and sample the image with screen-space gradients wrapped at the longitude seam. [`skyRegistration.ts`](../../apps/web/src/spatial/sky/skyRegistration.ts) is the CPU version of the same math; its tests reproduce the Python model's position for all 81 bright named check stars to 0.01 pixels.
- The colour grade is unchanged, without the red boost (finding 3).

### Check on screen [measured]

`?scene=0&freeze=1&t=4` at 1920x1080 in headless Chrome 156 (WebGPU on the Apple M5 Max), captured by [`scripts/capture_landing.mjs`](../../scripts/capture_landing.mjs) with the catalog sprites drawn and again with them hidden, and measured by [`scripts/check_sky_dome_on_screen.py`](../../scripts/check_sky_dome_on_screen.py). Every IAU-named star brighter than magnitude 4.5 is projected through the captured camera at its catalog direction (fov 55.4 degrees, view centre RA 0h22m, Dec +1.0); stars behind the Moon, the Earth or its atmosphere shell are left out; and the dome's star image is centroided within 6 pixels (the brightest difference-of-Gaussians peak, refined by the Gaussian-windowed centroid of section 2). Screen scale at the centre is 1.08 pixels per texel.

| Renderer | Before (acecdc9): stars, median, max | After: stars, median, max |
|---|---|---|
| WebGL renderer | 19, 4.0, 6.9 texels | **20, 0.23, 1.08 texels** |
| WebGPU host | 19, 4.0, 6.7 texels | **20, 0.22, 1.08 texels** |

Counted are named stars in clear sky with a dome image at the search position; 7 sit behind the Moon or the Earth. Before the registration, the peak found was an unrelated star, so those figures only reflect the search window's size. The after frames show what ships: the panorama alone, without the NASA star map and the red boost (findings 1 and 3). The branch's first measurement, made with the star map drawn and a different centroid, gave medians of 0.27 to 0.28 texels and a maximum of 1.12. The before and after cameras are identical to the last bit, since the clock is held at t = 4.

Matched captures, canvas only: WebGL [before](img/sky-registration/cislunar-webgl-before.jpg) and [after](img/sky-registration/cislunar-webgl-after.jpg); WebGPU host [before](img/sky-registration/cislunar-webgpu-before.jpg) and [after](img/sky-registration/cislunar-webgpu-after.jpg). [Detail](img/sky-registration/named-stars-detail.jpg), WebGPU host, x5: Hamal, Enif, Algenib and Menkar, each marked at its catalog position. Columns: before with the dome only, after with the dome only, and after as shown, with the catalog sprite.

### Starless diffuse map (`eso_milkyway_diffuse_6k.webp`) [tested]

At the WebGPU landing's 26 degree vertical field of view, one panorama texel (216 arcsec) spans 2.49 screen pixels at 1080p and 4.99 screen pixels at 4K, turning resolved stars in `eso_milkyway_panorama.jpg` into magnified blocks underneath the sharp catalog sprites. [`scripts/bake_starless_panorama.py`](../../scripts/bake_starless_panorama.py) removes the photograph's point stars in linear light while preserving its diffuse galactic emission, dust lanes, nebulae, and the two photographed planets:

1. **Measured radial star profiles:** For isolated catalog stars (no neighbour within 0.3 degrees brighter than `mag + 2`) in each magnitude bin from `-1` to `6`, the script measures the mean radial luminance excess over the local annulus (`24..30 px`) and finds the radius where the excess above the outer crowding plateau (`12..24 px`) falls below 3 percent of peak (`7, 5, 4, 3, 3, 3, 2, 2 px` for bins `-1..6`).
2. **Star masking and grain filter:** All 21,417 catalog stars to `G <= 7.0` in core tile layers 0 and 1 are masked at their registered panorama pixel coordinates with their bin radius plus a 2 pixel margin (`7.36%` of image pixels). A longitudinal-wrapping `5x5` median filter and `1 px` Gaussian remove fainter unmasked point-star grain (`7 < G <= 8`), and multi-scale normalized Gaussian averaging (`sigma = 2..32 px`) followed by 200 Laplace iterations and a `1 px` Gaussian seam blend fills the masked disks from their smoothed surroundings.
3. **Regional diffuse light and planets:** Two passes of 1-degree (`16x16 px`) strided median luminance scaling match every 1-degree patch's median luminance to the original photograph (`2,088` patches checked between latitudes 70 S and 70 N in [`tests/integration/test_starless_panorama.py`](../../tests/integration/test_starless_panorama.py)). Venus and Jupiter are restored byte for byte within their saturated cores plus a 6 pixel margin.

## 4. Findings and decisions

1. **The star map duplicated the catalog stars. Removed.** It was a plot of Hipparcos-2, Tycho-2 and (2020 release) Gaia DR2 stars, so every star in it is a star the renderer already draws from Gaia DR3 and Hipparcos 2007, and its faint-star Milky Way duplicated ESO's photograph. Before the registration it was sampled with the panorama's coordinates, so its RA/Dec Milky Way arc crossed the panorama's band. Both renderers no longer load it, and the file and its sources entry are gone. [verified]
2. **The photograph contains two planets. Kept, disclosed.** Venus at RA 23h19m, Dec -3.9 and Jupiter at RA 19h30m, Dec -21.9 are saturated points in the image that no catalog star to magnitude 8 explains within 0.3 degrees. Through the registration, the repo's ephemeris (`services/science/ephemeris.py`) puts Venus within 0.06 degrees of the centroid of its saturated core at 02:00 UTC on 2009-01-22, and Jupiter within 0.04 degrees on 2008-06-09 [measured, `test_the_panorama_shows_venus_and_jupiter_where_the_ephemeris_puts_them`, which also checks that the ephemeris position a month earlier has no such point]. ESO says planets crossed the fields during the months of shooting. Registered, Venus sits beside the Moon in the landing frame as the brightest point in the sky, with the photograph's own diffraction spikes and the post chain's streak. Both planets stay in the photograph, unretouched, and the sky layer's disclosure names them as planets the 2008 to 2009 shoot photographed. [measured, inferred dates]
3. **`hAlphaKnots` was not H-alpha. Removed, with the band modes.** It boosted the panorama's red excess, `pow(max(0, r - 0.85 g), 1.6)`, with a fixed tint: a colour treatment of a broadband RGB photograph. The `halpha` and `jwst_ir` band modes of the WebGL dome, and the spectral monocle that showed them, mixed fixed weights of the same RGB into band-coloured images, so they showed no H-alpha or infrared data. All of them are gone from both renderers; the nebula plane's band remaps went with them. [verified]
4. **Landing view direction.** In the composed WebGL view looking toward RA 0h22m, Dec +1.0 (galactic latitude -61), the true sky has no Milky Way band. The WebGPU cislunar landing instead views the Earth in front of the galactic centre from beyond the lunar far side at 2025-03-07 11:00 UTC (see [`LANDING_MOMENT.md`](LANDING_MOMENT.md)). [measured, verified]
5. **Parallax between the dome and the catalog stars. Fixed.** The catalog stars were drawn on spheres of finite radius (85 units in the cislunar scene) centred on the regime anchor, while the dome is at infinity, so moving the camera off the anchor slid them against the dome by about the offset divided by the radius. Both renderers centre each star sphere on the camera every frame ([`skyMounts.ts`](../../apps/web/src/spatial/skyMounts.ts)), and the WebGPU sprites sit at the far plane, so any body in front covers them. With the camera nudged 1 unit sideways, the sprites slid a median 14.4 px (9.8 texels) off their catalog directions before the fix; after it they sit a median 0.8 px (WebGL) and 1.3 px (WebGPU) from them, as on the frame without the nudge, and the dome's stars do not move. The frame without a nudge is unchanged byte for byte. [measured]
6. **Point-star separation on the WebGPU landing.** In `eso_milkyway_panorama.jpg`, the photograph's star images sit under the catalog sprites and add soft 216-arcsecond texel blocks to their brightness. On the WebGPU landing, `eso_milkyway_diffuse_6k.webp` removes the photograph's resolved stars (`scripts/bake_starless_panorama.py`) and `CislunarWebGpuRenderer` draws catalog stars to `G <= 12`, so point stars come solely from the catalog and remain sharp at any zoom. [tested]

## 5. Reproducing

```sh
uv run python scripts/register_sky_panorama.py           # fit and write apps/web/public/textures/eso0932a_registration.json
uv run python scripts/register_sky_panorama.py --verify  # refit and compare byte for byte
uv run python scripts/bake_starless_panorama.py          # bake apps/web/public/textures/eso_milkyway_diffuse_6k.webp and update PROVENANCE.json
uv run python scripts/bake_starless_panorama.py --verify # rebake in memory and compare byte for byte
uv run pytest tests/integration/test_sky_registration.py tests/integration/test_starless_panorama.py
pnpm vitest run apps/web/src/spatial/sky

pnpm dev                                                  # the web app, on port 5173
node scripts/capture_landing.mjs --out /tmp/sky-capture   # both renderers; --url for another port
uv run python scripts/check_sky_dome_on_screen.py /tmp/sky-capture webgpu   # or webgl; --json, --detail
uv run pytest tests/integration/test_sky_dome_on_screen.py
```

## 6. Not verified

- Byte identity of the committed panorama with ESO's current download: it needs the file downloaded.
- The capture dates of the planets: the dates above are when the ephemeris agrees, not dates from ESO.
- Registration away from the landing view in the renderers. The fit covers the whole sky, but the screen check covered one frame.
