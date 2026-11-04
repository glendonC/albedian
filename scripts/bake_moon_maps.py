#!/usr/bin/env python3
"""Bake the Moon's colour and normal maps for the WebGPU landing from NASA's CGI Moon Kit (SVS 4720).

Inputs, downloaded once into .cache/moon-svs4720/ (--fetch) and pinned by SHA-256 below:
- lroc_color_16bit_srgb_8k.tif: 8192x4096, 16-bit sRGB colour from the LROC WAC mosaic. NASA's
  Scientific Visualization Studio made it for appearance, not science.
- ldem_16_uint.tif: 5760x2880 (16 samples per degree) LOLA heights, unsigned half-metres above a
  1727.4 km sphere.
Both run from longitude -180 at the left edge to +180 at the right, north at the top. Checked on
the data: the highest point falls at 158.7 W 5.4 N and the lowest at 172.5 W 70.3 S, where LOLA
puts them, and the maria are dark on the near side at the centre.

Outputs, in apps/web/public/textures/, each with its entry in that folder's PROVENANCE.json (the
bake rewrites its two sources and two files there and leaves every other entry):
- moon_svs4720_color_8k.jpg: the colour map at 8 bits (Pillow's TIFF reader keeps the high byte of
  each 16-bit sample), JPEG quality 90, 4:2:0, no metadata.
- moon_svs4720_lola_normal_16ppd.webp: tangent-space normals at the LOLA grid, RGB = (east, north,
  up) * 127.5 + 127.5, lossless WebP. True slopes, no exaggeration. Each normal is the cross
  product of two chords between surface points (radius = 1727.4 km + height): east-west over k
  samples each side, k = round(1 / cos(latitude)) up to a quarter turn, so the baseline stays near
  the 3.8 km north-south one at every latitude; north-south over the rows above and below, across
  the pole for the first and last rows. Near the poles the longitude samples crowd together
  (0.6 m apart in the first row), so a one-sample stencil there would turn height noise into
  slopes near 90 degrees.

Usage:
  uv run python scripts/bake_moon_maps.py --fetch [--cache DIR]    download the two inputs (264 MB)
  uv run python scripts/bake_moon_maps.py [--cache DIR]
  uv run python scripts/bake_moon_maps.py --verify [--cache DIR]   rebake in memory, compare bytes
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.contracts.provenance import AssetProvenanceRecord, dumps_record  # noqa: E402
DEFAULT_CACHE = REPO_ROOT / ".cache" / "moon-svs4720"
TEXTURES = REPO_ROOT / "apps" / "web" / "public" / "textures"

SVS_4720 = "https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720"
USER_AGENT = "Albedian/0.1 (research prototype)"
COLOUR_INPUT = ("lroc_color_16bit_srgb_8k.tif", "db7808e878b6a55eb409bb231eab8deb477f84b5c9d7396d76ff73e5d54992d9")
HEIGHT_INPUT = ("ldem_16_uint.tif", "45a2b32d56e81ed30db07fead8abc842b249b6511219d9ca2c53f81bc2dc5d62")
COLOUR_OUTPUT = "moon_svs4720_color_8k.jpg"
NORMAL_OUTPUT = "moon_svs4720_lola_normal_16ppd.webp"

RECORD = TEXTURES / "PROVENANCE.json"
# The day the pinned inputs were downloaded into the cache
INPUTS_RETRIEVED = "2026-11-04"
NASA_GUIDELINES = {
    "spdx": "LicenseRef-NASA-Media-Guidelines",
    "name": "NASA Images and Media Usage Guidelines",
    "url": "https://www.nasa.gov/nasa-brand-center/images-and-media/",
    "conditions": (
        "NASA content is generally not subject to copyright in the United States. NASA should be "
        "acknowledged as the source, and no use may imply NASA's endorsement."
    ),
}
SVS_CAVEAT = "SVS says the CGI Moon Kit maps are optimized for aesthetics, not science."

# SVS 4720: heights are unsigned half-metres above this sphere
HEIGHT_BASE_M = 1_727_400.0
HEIGHT_STEP_M = 0.5
JPEG_QUALITY = 90


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(cache: Path, name_and_sha: tuple[str, str]) -> None:
    """Downloads one SVS 4720 file into the cache, unless a copy with the pinned SHA-256 is there."""
    name, expected = name_and_sha
    target = cache / name
    if target.exists() and sha256_file(target) == expected:
        print(f"{target}: already cached")
        return
    cache.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(f"{SVS_4720}/{name}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as out:
        for block in iter(lambda: response.read(1 << 20), b""):
            out.write(block)
    actual = sha256_file(partial)
    if actual != expected:
        partial.unlink()
        raise SystemExit(f"{name} downloaded with SHA-256 {actual}, not the pinned {expected}")
    partial.replace(target)
    print(f"{target}: downloaded, SHA-256 matches")


def pinned_input(cache: Path, name_and_sha: tuple[str, str]) -> Path:
    name, expected = name_and_sha
    path = cache / name
    if not path.exists():
        raise SystemExit(f"missing input {path}; download SVS 4720's {name} there first")
    actual = sha256_file(path)
    if actual != expected:
        raise SystemExit(f"{path} has SHA-256 {actual}, not the pinned {expected}")
    return path


def surface_normals(radius_m: np.ndarray) -> np.ndarray:
    """Unit normals (east, north, up) of the surface r(lat, lon) on an equirectangular grid whose
    rows run from north to south and columns from longitude -180 to +180, sample centres at half
    steps. Returns an array of shape (rows, columns, 3)."""
    rows, columns = radius_m.shape
    lat = np.radians(90.0 - (np.arange(rows) + 0.5) * 180.0 / rows)
    lon = np.radians(-180.0 + (np.arange(columns) + 0.5) * 360.0 / columns)
    cos_lat, sin_lat = np.cos(lat)[:, None], np.sin(lat)[:, None]
    cos_lon, sin_lon = np.cos(lon)[None, :], np.sin(lon)[None, :]
    up = np.stack(
        np.broadcast_arrays(cos_lat * cos_lon, cos_lat * sin_lon, sin_lat), axis=-1
    )
    points = radius_m[..., None] * up

    # East-west chords, k samples each side; symmetric about the sample, so along its east
    k = np.clip(np.rint(1.0 / np.cos(lat)), 1, columns // 4).astype(int)
    east_chord = np.empty_like(points)
    for row in range(rows):
        east_chord[row] = np.roll(points[row], -k[row], axis=0) - np.roll(points[row], k[row], axis=0)

    # North-south chords; the first and last rows reach across the pole to the opposite meridian
    north_point = np.empty_like(points)
    south_point = np.empty_like(points)
    north_point[1:] = points[:-1]
    south_point[:-1] = points[1:]
    north_point[0] = np.roll(points[0], columns // 2, axis=0)
    south_point[-1] = np.roll(points[-1], columns // 2, axis=0)
    north_chord = north_point - south_point

    normal = np.cross(east_chord, north_chord)
    normal /= np.linalg.norm(normal, axis=-1, keepdims=True)
    east = np.stack(np.broadcast_arrays(-sin_lon, cos_lon, np.zeros_like(cos_lat)), axis=-1)
    north = np.stack(np.broadcast_arrays(-sin_lat * cos_lon, -sin_lat * sin_lon, cos_lat), axis=-1)
    return np.stack([(normal * east).sum(-1), (normal * north).sum(-1), (normal * up).sum(-1)], axis=-1)


def encode_normals(normals: np.ndarray) -> np.ndarray:
    return np.clip(np.rint((normals + 1.0) * 127.5), 0, 255).astype(np.uint8)


def decode_normals(rgb: np.ndarray) -> np.ndarray:
    return rgb.astype(np.float64) / 127.5 - 1.0


def bake_colour(source: Path) -> bytes:
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(source) as image:
        rgb = image.convert("RGB")
    buffer = io.BytesIO()
    rgb.save(buffer, "JPEG", quality=JPEG_QUALITY, subsampling=2, optimize=True)
    return buffer.getvalue()


def normal_pixels(source: Path) -> np.ndarray:
    """The encoded normal map, before compression."""
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(source) as image:
        heights = np.asarray(image, dtype=np.float64)
    return encode_normals(surface_normals(HEIGHT_BASE_M + HEIGHT_STEP_M * heights))


def bake_normals(source: Path) -> bytes:
    rgb = normal_pixels(source)
    buffer = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(buffer, "WEBP", lossless=True, quality=100, method=6)
    return buffer.getvalue()


def record_sources() -> list[dict]:
    return [
        {
            "id": "nasa-svs-4720-lroc-color-8k",
            "title": "CGI Moon Kit: LROC WAC colour map, 8192x4096, 16-bit sRGB",
            "url": f"{SVS_4720}/{COLOUR_INPUT[0]}",
            "retrieved": INPUTS_RETRIEVED,
            "license": NASA_GUIDELINES,
            "credit": "NASA's Scientific Visualization Studio",
            "sha256": COLOUR_INPUT[1],
            "note": f"From the Lunar Reconnaissance Orbiter Camera's WAC mosaic. {SVS_CAVEAT}",
        },
        {
            "id": "nasa-svs-4720-ldem-16",
            "title": "CGI Moon Kit: LOLA elevation, 16 samples per degree, unsigned half-metres above 1727.4 km",
            "url": f"{SVS_4720}/{HEIGHT_INPUT[0]}",
            "retrieved": INPUTS_RETRIEVED,
            "license": NASA_GUIDELINES,
            "credit": "NASA's Scientific Visualization Studio",
            "sha256": HEIGHT_INPUT[1],
            "note": f"From the Lunar Orbiter Laser Altimeter, LOLA. {SVS_CAVEAT}",
        },
    ]


def record_files(outputs: dict[str, bytes]) -> list[dict]:
    produced_by = {"method": "script", "script": "scripts/bake_moon_maps.py", "command": "uv run python scripts/bake_moon_maps.py"}
    return [
        {"path": COLOUR_OUTPUT, "sha256": hashlib.sha256(outputs[COLOUR_OUTPUT]).hexdigest(),
         "sources": ["nasa-svs-4720-lroc-color-8k"], "producedBy": produced_by},
        {"path": NORMAL_OUTPUT, "sha256": hashlib.sha256(outputs[NORMAL_OUTPUT]).hexdigest(),
         "sources": ["nasa-svs-4720-ldem-16"], "producedBy": produced_by},
    ]


def updated_record(record_text: str, sources: list[dict], files: list[dict]) -> str:
    """The folder's record with this bake's sources and files replaced and every other entry kept."""
    record = json.loads(record_text)
    ids = {source["id"] for source in sources}
    paths = {entry["path"] for entry in files}
    record["sources"] = [s for s in record["sources"] if s["id"] not in ids] + sources
    record["files"] = [f for f in record["files"] if f["path"] not in paths] + files
    return dumps_record(AssetProvenanceRecord.model_validate(record))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--fetch", action="store_true", help="download the inputs from SVS 4720, one at a time")
    parser.add_argument("--verify", action="store_true", help="rebake in memory and compare with the committed files")
    args = parser.parse_args()

    if args.fetch:
        for name_and_sha in (COLOUR_INPUT, HEIGHT_INPUT):
            fetch(args.cache, name_and_sha)
        return

    outputs = {
        COLOUR_OUTPUT: bake_colour(pinned_input(args.cache, COLOUR_INPUT)),
        NORMAL_OUTPUT: bake_normals(pinned_input(args.cache, HEIGHT_INPUT)),
    }
    failed = False
    for name, data in outputs.items():
        target = TEXTURES / name
        digest = hashlib.sha256(data).hexdigest()
        if args.verify:
            same = target.exists() and target.read_bytes() == data
            failed |= not same
            print(f"{name}: {'matches' if same else 'DIFFERS from'} the rebake ({len(data):,} bytes, sha256 {digest})")
        else:
            target.write_bytes(data)
            print(f"wrote {target.relative_to(REPO_ROOT)}: {len(data):,} bytes, sha256 {digest}")
    record_text = RECORD.read_text(encoding="utf-8")
    new_record = updated_record(record_text, record_sources(), record_files(outputs))
    if args.verify:
        same = new_record == record_text
        failed |= not same
        print(f"{RECORD.name}: {'matches' if same else 'DIFFERS from'} the entries this bake writes")
    else:
        RECORD.write_text(new_record, encoding="utf-8")
        print(f"updated {RECORD.relative_to(REPO_ROOT)}: 2 sources, 2 files")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
