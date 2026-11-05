#!/usr/bin/env python3
"""Bake Earth maps for the web app from recorded public data (data-5).

Inputs, downloaded into the shared `.cache/earth-maps/` (not in git) and pinned by SHA-256 below:
- Natural Earth 1:10m ocean and lakes, public domain (`ne_10m_ocean.zip`, `ne_10m_lakes.zip`);
- NASA's Blue Marble (2002) cloud layer, `cloud_combined_2048.jpg`, under NASA's media guidelines;
- NOAA ETOPO 2022 60 arc-second surface elevation, CC0 (`ETOPO_2022_v1_60s_N90W180_surface.tif`);
- NASA's Blue Marble Next Generation base map for March 2004 (`world.200403.3x21600x10800.jpg`),
  which, unlike the topography versions, has no relief shading baked in.

Outputs in `apps/web/public/textures/`, each with its entry in that folder's `PROVENANCE.json`:
- `earth_water_natural_earth_4k.png`: 4096x2048 equirectangular water cover, 8-bit, 255 for water
  and 0 for land. A coast pixel holds the fraction of its area under water, measured over four
  sub-rows with exact spans across each; nothing is blurred or invented.
- `earth_clouds_bluemarble2002_2k.jpg`: NASA's file, byte for byte.
- `earth_height_etopo2022_4k.png`: 4096x2048 equirectangular heights, the mean over each pixel's
  ETOPO cells in metres above the EGM2008 geoid. Each pixel stores round(height + 11000) as
  R * 256 + G, with B = 0 and no alpha, so a browser decodes it losslessly as data.
- `earth_day_bluemarble_2004-03_base_8k.jpg`: the base map at 8192x4096 (Lanczos, JPEG quality 90),
  so relief is lit once, from the height map.

The shapefiles are read straight from the zip archives with the standard library and filled with
numpy, and Pillow reads the GeoTIFF, so the bake needs no GIS software. Usage, from the repository
root:
    uv run python scripts/bake_earth_maps.py
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import struct
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.contracts.provenance import AssetProvenanceRecord, dumps_record  # noqa: E402

DEFAULT_CACHE = ROOT_DIR / ".cache" / "earth-maps"
TEXTURES = ROOT_DIR / "apps" / "web" / "public" / "textures"
WATER_NAME = "earth_water_natural_earth_4k.png"
CLOUDS_NAME = "earth_clouds_bluemarble2002_2k.jpg"
HEIGHT_NAME = "earth_height_etopo2022_4k.png"
DAY_NAME = "earth_day_bluemarble_2004-03_base_8k.jpg"
DAY_SIZE = (8192, 4096)
WATER_SIZE = (4096, 2048)
HEIGHT_SIZE = (4096, 2048)
SUBROWS = 4
# Heights are stored as metres above EGM2008 plus this offset, rounded to the metre, in 16 bits
HEIGHT_OFFSET_M = 11_000
HEIGHT_RANGE_M = (-HEIGHT_OFFSET_M, 65_535 - HEIGHT_OFFSET_M)


@dataclass(frozen=True)
class Input:
    filename: str
    url: str
    sha256: str


INPUTS = {
    "ocean": Input(
        "ne_10m_ocean.zip",
        "https://naciscdn.org/naturalearth/10m/physical/ne_10m_ocean.zip",
        "db626fcd5d50b096b156c78a2cc95011b39f32a61b4e47d147e3f7a77b8b2719",
    ),
    "lakes": Input(
        "ne_10m_lakes.zip",
        "https://naciscdn.org/naturalearth/10m/physical/ne_10m_lakes.zip",
        "0803a06f9c3cb4671d89b68c48b142aad9366ba40f665245e12a913fbc61722a",
    ),
    "clouds": Input(
        "cloud_combined_2048.jpg",
        "https://assets.science.nasa.gov/content/dam/science/esd/eo/content-feature/bluemarble/images/cloud_combined_2048.jpg",
        "daddaad84d7a33bbbc86cdda3f591099f57cee8607b7bcf3b67eb7e4f7a1c793",
    ),
    "etopo": Input(
        "ETOPO_2022_v1_60s_N90W180_surface.tif",
        "https://www.ngdc.noaa.gov/mgg/global/relief/ETOPO2022/data/60s/60s_surface_elev_gtif/ETOPO_2022_v1_60s_N90W180_surface.tif",
        "9d27d4b8ea8e76977e2988bca667d7c8fa68b927355feffcddd6b4875a7fd08e",
    ),
    "day": Input(
        "world.200403.3x21600x10800.jpg",
        "https://assets.science.nasa.gov/content/dam/science/esd/eo/images/bmng/bmng-base/march/world.200403.3x21600x10800.jpg",
        "af37f61a9715c59dc86763df19f588a20c9d92b3d0a8034a0b00acd6b97ccb7d",
    ),
}


# =============================================================================================
# Shapefiles (ESRI Shapefile Technical Description, 1998): Polygon records only
# =============================================================================================


def read_shapefile_polygons(archive: Path, layer: str) -> list[list[np.ndarray]]:
    """Each Polygon record of `<layer>.shp` in a zip archive, as its rings of (lon, lat) points."""
    with zipfile.ZipFile(archive) as bundle:
        data = bundle.read(f"{layer}.shp")
    file_code, length_words = struct.unpack(">i", data[:4])[0], struct.unpack(">i", data[24:28])[0]
    if file_code != 9994 or length_words * 2 != len(data):
        raise ValueError(f"{layer}.shp: not a shapefile, or its length field is wrong")
    polygons: list[list[np.ndarray]] = []
    offset = 100
    while offset < len(data):
        _, content_words = struct.unpack(">2i", data[offset : offset + 8])
        content = data[offset + 8 : offset + 8 + content_words * 2]
        offset += 8 + content_words * 2
        (shape_type,) = struct.unpack("<i", content[:4])
        if shape_type == 0:  # null shape
            continue
        if shape_type != 5:
            raise ValueError(f"{layer}.shp: shape type {shape_type} is not handled (only 5, Polygon)")
        n_parts, n_points = struct.unpack("<2i", content[36:44])
        starts = list(struct.unpack(f"<{n_parts}i", content[44 : 44 + 4 * n_parts]))
        points = np.frombuffer(content, dtype="<f8", count=2 * n_points, offset=44 + 4 * n_parts).reshape(n_points, 2)
        polygons.append([points[a:b].copy() for a, b in zip(starts, [*starts[1:], n_points], strict=True)])
    return polygons


# =============================================================================================
# Filling polygons on an equirectangular grid
# =============================================================================================


def fill_polygons(polygons: list[list[np.ndarray]], width: int, height: int, *, subrows: int = SUBROWS) -> np.ndarray:
    """Fraction of each pixel inside the polygons, by the even-odd rule over all their rings.

    The grid spans longitude -180 to 180 and latitude 90 to -90. Each pixel row is sampled on
    `subrows` horizontal lines; along each line the inside spans are exact, so a pixel gets the
    covered share of every line that crosses it. Rings are closed if their last point is not
    their first.
    """
    x0s, y0s, x1s, y1s = [], [], [], []
    for rings in polygons:
        for ring in rings:
            if not np.array_equal(ring[0], ring[-1]):
                ring = np.vstack([ring, ring[:1]])
            x = (ring[:, 0] + 180.0) * (width / 360.0)
            y = (90.0 - ring[:, 1]) * (height / 180.0)
            x0s.append(x[:-1]), y0s.append(y[:-1]), x1s.append(x[1:]), y1s.append(y[1:])
    x0, y0, x1, y1 = (np.concatenate(part) for part in (x0s, y0s, x1s, y1s))
    sloped = y0 != y1
    x0, y0, x1, y1 = x0[sloped], y0[sloped], x1[sloped], y1[sloped]
    lines = height * subrows
    # An edge crosses line k, at y = (k + 0.5) / subrows, when min(y0, y1) <= y < max(y0, y1)
    first = np.clip(np.ceil(np.minimum(y0, y1) * subrows - 0.5), 0, lines).astype(np.int64)
    stop = np.clip(np.ceil(np.maximum(y0, y1) * subrows - 0.5), 0, lines).astype(np.int64)
    counts = stop - first
    edge = np.repeat(np.arange(counts.size), counts)
    line = np.repeat(first, counts) + (np.arange(edge.size) - np.repeat(np.cumsum(counts) - counts, counts))
    y = (line + 0.5) / subrows
    x = x0[edge] + (y - y0[edge]) * (x1[edge] - x0[edge]) / (y1[edge] - y0[edge])
    order = np.lexsort((x, line))
    line, x = line[order], np.clip(x[order], 0.0, float(width))
    if np.any(np.bincount(line, minlength=lines) % 2):
        raise ValueError("a line crosses the polygons an odd number of times; a ring is broken")
    row = line[0::2] // subrows
    a, b = x[0::2], x[1::2]
    weight = 1.0 / subrows
    cover = np.zeros((height, width + 2))
    ia, ib = np.floor(a).astype(np.int64), np.floor(b).astype(np.int64)
    same = ia == ib
    np.add.at(cover, (row[same], ia[same]), (b[same] - a[same]) * weight)
    split = ~same
    np.add.at(cover, (row[split], ia[split]), (ia[split] + 1 - a[split]) * weight)
    np.add.at(cover, (row[split], ib[split]), (b[split] - ib[split]) * weight)
    # Whole pixels strictly between the two ends, through a running sum of +weight and -weight
    steps = np.zeros((height, width + 2))
    np.add.at(steps, (row[split], ia[split] + 1), weight)
    np.add.at(steps, (row[split], ib[split]), -weight)
    cover += np.cumsum(steps, axis=1)
    return np.clip(cover[:, :width], 0.0, 1.0)


def water_cover(ocean_zip: Path, lakes_zip: Path, width: int, height: int) -> np.ndarray:
    """Water fraction per pixel: Natural Earth's ocean, and its lakes and reservoirs."""
    ocean = fill_polygons(read_shapefile_polygons(ocean_zip, "ne_10m_ocean"), width, height)
    lakes = fill_polygons(read_shapefile_polygons(lakes_zip, "ne_10m_lakes"), width, height)
    return np.maximum(ocean, lakes)


def coverage_to_mask(cover: np.ndarray) -> np.ndarray:
    return np.round(np.clip(cover, 0.0, 1.0) * 255.0).astype(np.uint8)


# =============================================================================================
# Relief: ETOPO 2022 heights
# =============================================================================================


def read_etopo(path: Path) -> Image.Image:
    """The ETOPO 2022 GeoTIFF as a Pillow float image, after checking its grid is the one assumed."""
    Image.MAX_IMAGE_PIXELS = None
    image = Image.open(path)
    tags = image.tag_v2
    if image.mode != "F" or image.size != (21600, 10800):
        raise ValueError(f"{path.name}: expected a 21600x10800 float grid, got {image.mode} {image.size}")
    if tuple(tags.get(33922, ()))[3:5] != (-180.0, 90.0) or tuple(tags.get(33550, ()))[:2] != (1 / 60, 1 / 60):
        raise ValueError(f"{path.name}: expected cells of 1/60 degree from longitude -180 and latitude 90")
    return image


def area_mean_heights(etopo: Image.Image, width: int, height: int) -> np.ndarray:
    """Mean height over each output pixel's cells (Pillow's box filter on the float grid)."""
    return np.asarray(etopo.resize((width, height), Image.Resampling.BOX), dtype=np.float64)


def encode_heights(heights: np.ndarray) -> np.ndarray:
    """Metres to an RGB8 image: R and G hold round(height + offset); B stays 0."""
    code = np.round(np.asarray(heights, dtype=np.float64) + HEIGHT_OFFSET_M)
    if not np.all(np.isfinite(code)) or code.min() < 0 or code.max() > 65_535:
        raise ValueError(f"heights must be finite and between {HEIGHT_RANGE_M[0]} and {HEIGHT_RANGE_M[1]} m")
    code = code.astype(np.uint16)
    rgb = np.zeros((*code.shape, 3), dtype=np.uint8)
    rgb[..., 0] = code >> 8
    rgb[..., 1] = code & 0xFF
    return rgb


def decode_heights(rgb: np.ndarray) -> np.ndarray:
    return rgb[..., 0].astype(np.int32) * 256 + rgb[..., 1].astype(np.int32) - HEIGHT_OFFSET_M


def resample_day_map(path: Path) -> bytes:
    """The base day map as an 8192x4096 JPEG: Lanczos, quality 90."""
    Image.MAX_IMAGE_PIXELS = None
    buffer = io.BytesIO()
    Image.open(path).convert("RGB").resize(DAY_SIZE, Image.Resampling.LANCZOS).save(buffer, format="JPEG", quality=90, optimize=True)
    return buffer.getvalue()


# =============================================================================================
# Provenance entries in textures/PROVENANCE.json
# =============================================================================================

NASA_GUIDELINES = {
    "spdx": "LicenseRef-NASA-Media-Guidelines",
    "name": "NASA Images and Media Usage Guidelines",
    "url": "https://www.nasa.gov/nasa-brand-center/images-and-media/",
    "conditions": (
        "NASA content is generally not subject to copyright in the United States. NASA should be "
        "acknowledged as the source, and no use may imply NASA's endorsement."
    ),
}
NATURAL_EARTH_TERMS = {
    "spdx": "LicenseRef-Natural-Earth-Public-Domain",
    "name": "Natural Earth terms of use: public domain",
    "url": "https://www.naturalearthdata.com/about/terms-of-use/",
}
JRC_CAVEAT = (
    "The terms of use say all Natural Earth raster and vector data are in the public domain, with "
    "credit optional ('Made with Natural Earth'). They add that some European river and lake data from "
    "the EC Joint Research Centre are licensed to Natural Earth for the sole purpose of creating a world "
    "base map; a planet texture is a world base map."
)


def layer_version(archive: Path, layer: str) -> str:
    with zipfile.ZipFile(archive) as bundle:
        return bundle.read(f"{layer}.VERSION.txt").decode("ascii").strip()


def record_sources(cache: Path) -> list[dict]:
    return [
        {
            "id": "natural-earth-10m-ocean",
            "title": "Natural Earth 1:10m physical vectors: ocean",
            "url": INPUTS["ocean"].url,
            "version": f"Version {layer_version(cache / INPUTS['ocean'].filename, 'ne_10m_ocean')}",
            "license": {**NATURAL_EARTH_TERMS, "conditions": JRC_CAVEAT},
            "credit": "Made with Natural Earth",
            "sha256": INPUTS["ocean"].sha256,
        },
        {
            "id": "natural-earth-10m-lakes",
            "title": "Natural Earth 1:10m physical vectors: lakes and reservoirs",
            "url": INPUTS["lakes"].url,
            "version": f"Version {layer_version(cache / INPUTS['lakes'].filename, 'ne_10m_lakes')}",
            "license": {**NATURAL_EARTH_TERMS, "conditions": JRC_CAVEAT},
            "credit": "Made with Natural Earth",
            "sha256": INPUTS["lakes"].sha256,
        },
        {
            "id": "nasa-blue-marble-2002-clouds",
            "title": "The Blue Marble (2002): cloud layer, 2048x1024",
            "url": "https://science.nasa.gov/earth/earth-observatory/the-blue-marble-true-color-global-imagery-at-1km-resolution/",
            "version": "cloud_combined_2048.jpg",
            "license": NASA_GUIDELINES,
            "credit": (
                "NASA Goddard Space Flight Center Image by Reto St\u00f6ckli (land surface, shallow water, "
                "clouds). Enhancements by Robert Simmon (ocean color, compositing, 3D globes, animation)."
            ),
            "sha256": INPUTS["clouds"].sha256,
            "note": (
                f"Download {INPUTS['clouds'].url}, linked from that page (checked 2026-11-04). The page says the "
                "cloud image is a composite of two days of visible-light imagery and a third day of thermal "
                "infrared over the poles; it does not date those days. The Blue Marble's other MODIS layers "
                "are from June to September 2001, so the clouds are most likely from 2001 too [inference]. "
                "Cloud cover of a few days, not today's weather."
            ),
        },
        {
            "id": "noaa-etopo-2022-60s",
            "title": "ETOPO 2022 Global Relief Model, 60 arc-second surface elevation",
            "url": INPUTS["etopo"].url,
            "version": "ETOPO 2022 v1, 60 arc-second surface grid",
            "license": {
                "spdx": "CC0-1.0",
                "name": "Creative Commons Zero v1.0 Universal",
                "url": "https://creativecommons.org/publicdomain/zero/1.0/",
                "conditions": "NOAA's metadata record gives this licence and adds: Not to be used for navigation.",
            },
            "credit": "NOAA National Centers for Environmental Information",
            "citation": (
                "NOAA National Centers for Environmental Information. 2022: ETOPO 2022 15 Arc-Second Global "
                "Relief Model. NOAA National Centers for Environmental Information. doi:10.25921/fd45-gt74"
            ),
            "doi": "10.25921/fd45-gt74",
            "sha256": INPUTS["etopo"].sha256,
            "note": (
                "Metres above the EGM2008 geoid (the GeoTIFF's vertical datum, 'WGS 84 + EGM2008 height'), on "
                "cells of 1/60 degree from longitude -180 and latitude 90, with no missing values. The surface "
                "grid gives the ice surface where there is ice and includes lake and sea floors: Lake Baikal's "
                "cells lie hundreds of metres below sea level."
            ),
        },
        {
            "id": "nasa-bmng-2004-03-base",
            "title": "Blue Marble: Next Generation, March 2004, base map",
            "url": "https://science.nasa.gov/earth/earth-observatory/blue-marble-next-generation/base-map/",
            "version": "March 2004, 21600x10800 JPEG",
            "license": NASA_GUIDELINES,
            "credit": "NASA Earth Observatory",
            "acknowledgement": (
                "Blue Marble: Next Generation was produced by Reto St\u00f6ckli, NASA Earth Observatory "
                "(NASA Goddard Space Flight Center)."
            ),
            "sha256": INPUTS["day"].sha256,
            "note": (
                f"Input file {INPUTS['day'].url}, linked from that page (checked 2026-11-05). The topography "
                "versions' pages say they include shading for topography (and bathymetry); the base map's "
                "page names no shading. NASA's Blue Marble: Next Generation page, "
                "https://science.nasa.gov/earth/earth-observatory/blue-marble-next-generation/, says: 'Anyone "
                "using or republishing Blue Marble: Next Generation please credit \u201cNASA Earth Observatory.\u201d' "
                "The same page says deep oceans are not in the source data and are given a uniform blue, and that "
                "snow and cloud are not completely told apart where snow lies for less than three or four months. "
                "Measured on this map at 1024x512 against earth_water_natural_earth_4k.png "
                "(tests/integration/test_earth_maps.py): north of latitude 60 degrees N it shows no sea ice, under "
                "0.1 per cent of the open sea there looking like ice, and Hudson Bay is dark open-water blue, not "
                "ice. Around Antarctica the water mask covers the ice shelves, the Ross and Ronne-Filchner among "
                "them, which the map draws white. A cloud-free surface composite for March 2004, not current "
                "conditions."
            ),
        },
    ]


def record_files() -> list[dict]:
    return [
        {
            "path": WATER_NAME,
            "sha256": hashlib.sha256((TEXTURES / WATER_NAME).read_bytes()).hexdigest(),
            "sources": ["natural-earth-10m-ocean", "natural-earth-10m-lakes"],
            "producedBy": {
                "method": "script",
                "script": "scripts/bake_earth_maps.py",
                "command": "uv run python scripts/bake_earth_maps.py",
            },
            "note": (
                "Water cover, 4096x2048, equirectangular from longitude -180 and latitude 90: 255 is water "
                "(ocean, lakes and reservoirs), 0 is land, and a coast pixel holds the fraction of its area "
                "under water, measured on four lines per pixel row. Land below sea level stays land."
            ),
        },
        {
            "path": CLOUDS_NAME,
            "sha256": hashlib.sha256((TEXTURES / CLOUDS_NAME).read_bytes()).hexdigest(),
            "sources": ["nasa-blue-marble-2002-clouds"],
            "producedBy": {"method": "downloaded-as-is"},
            "note": "NASA's cloud_combined_2048.jpg, byte for byte.",
        },
        {
            "path": HEIGHT_NAME,
            "sha256": hashlib.sha256((TEXTURES / HEIGHT_NAME).read_bytes()).hexdigest(),
            "sources": ["noaa-etopo-2022-60s"],
            "producedBy": {
                "method": "script",
                "script": "scripts/bake_earth_maps.py",
                "command": "uv run python scripts/bake_earth_maps.py",
            },
            "note": (
                "Heights, 4096x2048, equirectangular from longitude -180 and latitude 90: the mean over each "
                "pixel's ETOPO cells, in metres above EGM2008. Each pixel stores round(height + 11000) in R and G; "
                "decode as R * 256 + G - 11000. B is always 0 and there is no alpha, so load it as data, without "
                "colour conversion. Lake and sea floors are included: where earth_water_natural_earth_4k.png marks "
                "water, the height is the floor, not the water surface."
            ),
        },
        {
            "path": DAY_NAME,
            "sha256": hashlib.sha256((TEXTURES / DAY_NAME).read_bytes()).hexdigest(),
            "sources": ["nasa-bmng-2004-03-base"],
            "producedBy": {
                "method": "script",
                "script": "scripts/bake_earth_maps.py",
                "command": "uv run python scripts/bake_earth_maps.py",
            },
            "note": (
                "A Lanczos resample of the base map to 8192x4096, JPEG quality 90. It has no relief shading of its "
                "own: with earth_height_etopo2022_4k.png, relief is lit once, by the renderer."
            ),
        },
    ]


def replaced_in_place(entries: list[dict], replacements: list[dict], key: str) -> list[dict]:
    """Each entry whose key a replacement shares, replaced where it stands; replacements for new keys at the end."""
    new = {replacement[key]: replacement for replacement in replacements}
    return [new.pop(entry[key], entry) for entry in entries] + list(new.values())


def update_record(record_path: Path, sources: list[dict], files: list[dict]) -> None:
    """Replace this bake's sources and files in the folder's record where they stand, keeping every other entry
    where it is: scripts/bake_moon_maps.py writes the same record and its --verify compares the whole text."""
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["sources"] = replaced_in_place(record["sources"], sources, "id")
    record["files"] = replaced_in_place(record["files"], files, "path")
    record_path.write_text(dumps_record(AssetProvenanceRecord.model_validate(record)), encoding="utf-8")


# =============================================================================================
# Bake
# =============================================================================================


def check_inputs(cache: Path) -> None:
    for name, source in INPUTS.items():
        path = cache / source.filename
        if not path.is_file():
            raise SystemExit(f"[earth-maps] missing {path}; download {source.url} into {cache}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != source.sha256:
            raise SystemExit(f"[earth-maps] {name}: {path.name} has sha256 {digest}, expected {source.sha256}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args()
    check_inputs(args.cache)

    cover = water_cover(args.cache / INPUTS["ocean"].filename, args.cache / INPUTS["lakes"].filename, *WATER_SIZE)
    buffer = io.BytesIO()
    Image.fromarray(coverage_to_mask(cover)).save(buffer, format="PNG", optimize=True)
    (TEXTURES / WATER_NAME).write_bytes(buffer.getvalue())
    (TEXTURES / CLOUDS_NAME).write_bytes((args.cache / INPUTS["clouds"].filename).read_bytes())

    heights = area_mean_heights(read_etopo(args.cache / INPUTS["etopo"].filename), *HEIGHT_SIZE)
    buffer = io.BytesIO()
    Image.fromarray(encode_heights(heights)).save(buffer, format="PNG", optimize=True)
    (TEXTURES / HEIGHT_NAME).write_bytes(buffer.getvalue())
    (TEXTURES / DAY_NAME).write_bytes(resample_day_map(args.cache / INPUTS["day"].filename))

    update_record(TEXTURES / "PROVENANCE.json", record_sources(args.cache), record_files())
    for name in (WATER_NAME, CLOUDS_NAME, HEIGHT_NAME, DAY_NAME):
        print(f"[earth-maps] {name}: {(TEXTURES / name).stat().st_size / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
