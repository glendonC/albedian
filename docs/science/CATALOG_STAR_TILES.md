# Catalog Star Tiles

The web sky draws real catalog stars: every Gaia DR3 source brighter than G 12, with
Bailer-Jones (2021) distance estimates, and Hipparcos 2007 entries where Gaia handles bright
stars poorly. They stream from static HEALPix tiles under `apps/web/public/catalog/stars/`,
layered by magnitude, so the Unveil gesture can move the limiting magnitude and load only
what the view needs. Beside every tile, an astro tile carries the Gaia DR3 temperature,
luminosity and extinction of the same stars, with their percentiles and the astrometric quality,
for the inspector and the audio engine.

These stars are catalog data (`evidenceKind: catalog-derived`). Positions, magnitudes,
distances, temperatures, luminosities and extinction come from the catalogs, not from the user's
recording, and must never be shown as observed. In the sky view they are catalog positions at the catalog epoch; placed in 3D by
distance they become `simulated` (Catalog Geometry, Distance Estimates).

## Sources

| Source | Table | Use | Citation |
|---|---|---|---|
| Gaia DR3 | `gaiadr3.gaia_source` | position (ICRS, J2016.0), G, BP-RP, proper motion, radial velocity; GSP-Phot effective temperature and extinction (A0 at 547.7 nm, AG) with their 16th/84th percentiles; RUWE; parallax standard error | Gaia Collaboration, Vallenari et al. 2023, A&A 674, A1 (mission: Prusti et al. 2016, A&A 595, A1); GSP-Phot: Andrae et al., doi:10.1051/0004-6361/202243462 |
| Gaia DR3 astrophysical parameters | `gaiadr3.astrophysical_parameters` | FLAME luminosity with its 16th/84th percentiles | Creevey et al., Apsis I, doi:10.1051/0004-6361/202243688; Fouesneau et al., Apsis II, doi:10.1051/0004-6361/202243919 |
| Bailer-Jones et al. 2021 | `external.gaiaedr3_distance` | distance median and 16th/84th percentile bounds; photogeometric when present, else geometric | AJ 161, 147 |
| Hipparcos 2007 | `public.hipparcos_newreduction` | bright stars Gaia handles poorly: position (propagated J1991.25 to J2016.0), Hp, B-V, parallax distance | van Leeuwen 2007, A&A 474, 653 |
| Gaia DR3 to Hipparcos-2 crossmatch | `gaiadr3.hipparcos2_best_neighbour` | pairs Hipparcos stars with Gaia sources | Marrese et al. 2019, A&A 621, A144 |
| IAU Catalog of Star Names | `IAU-CSN.txt` (WGSN, updated 2022-04-04) | proper names, attached by HIP number | IAU Working Group on Star Names |

All archive tables come from the ESA Gaia Archive TAP service. The manifest carries the full
citation, DOI and acknowledgement text for each source.

**Licence.** Data hosted in the ESA Space Science Archives, Gaia and Hipparcos included, are
distributed under CC BY-NC 3.0 IGO. Commercial use needs prior authorisation from ESA
(`data.licences@esa.int`, see the [ESA archive terms](https://www.cosmos.esa.int/web/esdc/terms-and-conditions)).
IAU products are released under Creative Commons Attribution.

## Selection

- Gaia DR3 sources with `phot_g_mean_mag < 12`, left-joined to Bailer-Jones distances. No
  other quality cuts.
- Bright end: a Hipparcos star replaces Gaia when Gaia has no usable counterpart (no best
  neighbour, or a best neighbour brighter than G 3, or one the crossmatch paired with a fainter
  companion). Gaia sources within 2 arcsec of a Hipparcos-supplied star, or within 10 arcsec
  and agreeing within 1.5 mag or saturated, are removed as duplicates; fainter companions
  (Sirius B, Rigel B, Almach B) stay. Every decision is logged in `bright-star-merge.json`.
- Result for `gaia-dr3`: 3,087,892 stars, 3,087,666 from Gaia DR3 and 226 from Hipparcos
  (155 Gaia sources removed as duplicates); 3,062,431 with a distance, 2,901,181 with a radial
  velocity; 410 IAU names attached (of 411 with HIP numbers).
- Astro values: the same Gaia sources left-joined to `gaiadr3.astrophysical_parameters`.
  1,812,688 stars carry a GSP-Phot temperature and the same 1,812,688 an
  extinction, 1,780,872 a FLAME luminosity, 3,062,208 a RUWE and
  3,062,208 a parallax error. The archive's own counts over the whole G < 12
  selection, before the bright-star merge removes duplicates, are 1,812,696, 1,780,875 and
  3,062,317 (verified by archive count queries). A value Gaia DR3 lacks stays missing and is never
  filled; the 226 Hipparcos entries carry none. GSP-Phot and FLAME values are model fits to
  BP/RP spectra, photometry and parallax; their papers above describe their reach and limits.
  61 FLAME luminosities lie below their own 16th percentile, as in the archive, where their
  `lum_flame_lower` is floored at 0.01 Lsun.

## Format

`<release>/manifest.json` describes the release (manifest format 2, validated by
`CatalogStarTileManifestSchema` in `@albedian/contracts`, which still reads format 1). Tiles live
at `L{layer}/{pix}.{core|detail|astro}.bin`, one file of each kind per non-empty HEALPix nested
pixel. Each layer covers a magnitude band at a fixed HEALPix order:

| Layer | Magnitude | Order | Position bits | Stars | Tiles | Core MB | Detail MB | Astro MB | Core tile median / max KB | Max position error (arcsec) |
|---|---|---|---|---|---|---|---|---|---|---|
| L0 | below 6.5 | 0 | 32 | 12,192 | 12 | 0.18 | 0.20 | 0.29 | 14.7 / 21.9 | 0.000 |
| L1 | 6.5 to 8.0 | 1 | 32 | 50,602 | 48 | 0.76 | 0.81 | 1.22 | 13.8 / 26.8 | 0.000 |
| L2 | 8.0 to 9.5 | 2 | 32 | 231,524 | 192 | 3.48 | 3.71 | 5.56 | 14.6 / 49.0 | 0.000 |
| L3 | 9.5 to 10.5 | 3 | 16 | 484,765 | 768 | 5.36 | 7.78 | 11.66 | 5.3 / 32.5 | 0.409 |
| L4 | 10.5 to 11.25 | 3 | 16 | 792,074 | 768 | 8.74 | 12.70 | 19.03 | 7.9 / 57.6 | 0.416 |
| L5 | 11.25 to 12.0 | 3 | 16 | 1,516,735 | 768 | 16.71 | 24.29 | 36.43 | 14.0 / 114.2 | 0.410 |

Totals: 35.2 MB of core tiles (streamed for rendering), 49.5 MB of detail tiles (fetched only
when a star is inspected), 74.2 MB of astro tiles (fetched on request), 160.0 MB on disk with
the manifest (30 KB), `names.json` (148 KB), `bright-star-merge.json` and `SHA256SUMS`.

Every tile starts with a 32-byte little-endian header (`ALBS`, header version 1 for every kind,
kind, position bits, layer, order, pixel, star count) followed by structure-of-arrays columns,
brightest star first. The astro kind joined in manifest format 2 without changing the header,
so core and detail tiles kept their bytes:

- **core** (11 bytes per star at 16-bit positions, 15 at 32-bit): sub-pixel position inside
  the tile's HEALPix pixel, magnitude (1 mmag), colour index (1 mmag), distance median
  (log-encoded, 0.023 % steps), flags (distance method, Gaia G or Hipparcos Hp, BP-RP or B-V,
  catalog, IAU-named).
- **detail** (16 bytes per star): Gaia `source_id` or HIP number (64-bit), proper motion and
  radial velocity (IEEE half floats, NaN when missing), distance bounds (log fractional offsets
  from the median).
- **astro** (24 bytes per star): GSP-Phot effective temperature with its 16th and 84th
  percentiles (whole kelvin), FLAME luminosity with its percentiles (log10 L/Lsun in 0.001 dex),
  GSP-Phot extinction A0 at 547.7 nm with its percentiles and AG (mmag), RUWE and the parallax
  standard error in mas (IEEE half floats). Reserved codes (0 K, -32768, 65535, NaN) mark a value
  Gaia DR3 does not give.

The encoding is defined once in `services/science/catalog_tiles.py` and mirrored by
`apps/web/src/spatial/catalogStars/tileCodec.ts`; `tests/fixtures/catalog_star_tiles/` holds a
core, detail and astro tile both decoders are tested against, with the archive's own values for
its two stars (one of which has no GSP-Phot or FLAME result in DR3). Quantization is far below
what the sky can show (positions within 0.42 arcsec, magnitudes within 0.5 mmag, temperatures
within 0.5 K, luminosities within 0.0005 dex, extinction within 0.5 mmag) but it is not the
archive value: use the archive for science.

## Client

`CatalogStarTileLoader` validates the manifest, then for each view asks for every layer whose
faint edge the limiting magnitude reaches, and inside it only the tiles that overlap the view
cone, brightest layer first and nearest the view center first. It keeps at most 1.5 million
stars resident and evicts the farthest tiles beyond that. `CatalogStarField` draws them in
Three.js at their catalog directions (ICRS north is scene +Y, RA 0h faces -Z, east to the
left), sizes and brightness following catalog flux, fading over the last 0.3 mag before the
limit. `SparkPortalRenderer.setLimitingMagnitude()` is the Unveil axis (default 6.5, catalog
limit 12). Names come from `names.json`; the renderer does not draw labels on its own, in line
with labels appearing on focus or a deliberate Show Labels.

Astrophysics load on request, never with the view. `loadAstro(layer, pix)` fetches a tile's
astro file once and keeps it; `getStarAstrophysics(layer, pix, index)` returns one star's
temperature, luminosity and A0 with their percentiles, AG, RUWE and parallax error, null
wherever Gaia DR3 gives no value, and the manifest source ids behind the values present.

### Measured load times

These figures were measured on cold loads (HTTP cache bypassed) from the Vite dev server on the
development machine, with 6 concurrent requests, for a 56 degree 16:9 view unless noted. No
automated script in the repository reproduces them, so read them as indicative of core tile
streaming (astro tiles load only on request):

| View | Limit | Tiles | Stars | Bytes | Wall time | Decode total |
|---|---|---|---|---|---|---|
| whole sky | 6.5 | 12 | 12,192 | 183 KB | 6 ms | 1 ms |
| Orion | 6.5 | 7 | 6,859 | 103 KB | 4 ms | under 1 ms |
| Orion | 9.5 | 83 | 98,875 | 1.49 MB | 39 ms | 3 ms |
| Orion | 11 | 447 | 412,517 | 4.95 MB | 203 ms | 13 ms |
| Orion | 12 | 629 | 770,123 | 8.89 MB | 374 ms | 24 ms |
| galactic center | 9.5 | 83 | 106,732 | 1.60 MB | 35 ms | 3 ms |
| galactic center | 12 | 638 | 1,058,025 | 12.09 MB | 492 ms | 35 ms |
| galactic center, 18 degree view | 12 | 176 | 401,619 | 4.59 MB | 82 ms | 12 ms |

Over a real network the request count dominates the deep layers. As an estimate, not a
measurement: at 50 Mbit/s with 40 ms round trips over HTTP/1.1 (6 connections), the naked-eye
view takes about 0.1 s, mag 9.5 about 0.8 s, and mag 12 over a wide view about 5 to 6 s; HTTP/2
hosting with a higher `maxConcurrent` cuts the latency share.

## Rebake and verify

```bash
uv run python scripts/bake_catalog_star_tiles.py               # fetch into .cache/catalog-star-bake and bake
uv run python scripts/bake_catalog_star_tiles.py --offline     # bake from the cache only
uv run python scripts/bake_catalog_star_tiles.py --verify      # rebake from the cache, compare SHA256SUMS
uv run python scripts/bake_catalog_star_tiles.py --fetch-only  # fill the cache, do not bake
```

A full bake makes about 210 archive queries for the base columns (each planned from an exact
count query, because the Gaia TAP service silently truncates synchronous results at 50,000 rows)
and 207 more for the astro columns: each astro query joins `gaiadr3.astrophysical_parameters` to
the same chunk of `gaiadr3.gaia_source` and must return exactly that chunk's rows. In initial
bakes the base fetch took 10.5 minutes and the astro fetch took about 100 minutes across
resumed runs (4 workers, then 2, as archive load varied); a rerun fetches only the chunks
still missing. The astro cache
keeps more DR3 columns than the tiles encode (logg, [M/H], GSP-Phot distance, E(BP-RP), FLAME
radius, mass and age, flags). An offline rebake takes about 70 seconds and
reproduces every file byte for byte.
The bake checks its HEALPix code against the index Gaia encodes in every `source_id`
(agreement 99.9993 % at order 3; the 23 exceptions are sources whose identifier was assigned
from an early position up to 40 arcsec away) and reads every tile back before writing it.

## Swapping releases (Gaia DR4, due 2 December 2026)

1. Add a `gaia-dr4` entry to `RELEASES` in `scripts/bake_catalog_star_tiles.py`: DR4 table
   names, the distance catalog that accompanies it, the astrophysical parameters table,
   crossmatch table, epoch, and citations.
2. Add the new catalog id to `CatalogStarCatalogIdSchema` in `@albedian/contracts` if tiles
   name it.
3. Bake into `apps/web/public/catalog/stars/gaia-dr4/` next to `gaia-dr3/` and review the
   manifest, merge log and names.
4. Point `PINNED_STAR_CATALOG_RELEASE` in `apps/web/src/spatial/catalogStars/CatalogStarField.ts`
   at `gaia-dr4`, then remove `gaia-dr3/` once nothing references it.

## Known limits

- Positions are at the catalog epoch J2016.0 and are not propagated to the viewing date (at
  most about 2 arcmin over a decade, for the fastest star; typically under 1 arcsec).
- Magnitudes are Gaia G except for the 226 Hipparcos entries, which carry Hp; colour tints for
  those use an approximate B-V to BP-RP slope and are a rendering aid only.
- The sky dome's Milky Way panorama is registered to these catalog stars in both renderers
  ([SKY_PANORAMA_REGISTRATION.md](SKY_PANORAMA_REGISTRATION.md)).
