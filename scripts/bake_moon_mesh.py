#!/usr/bin/env python3
"""Bake the Moon mesh, `apps/web/public/models/nasa_lro_moon_3d.glb`, from LOLA heights at true scale (data-9).

Input: `ldem_16_uint.tif` from NASA's CGI Moon Kit (SVS 4720), the file `scripts/bake_moon_maps.py` pins and reads:
5760 x 2880 LOLA heights, 16 samples per degree, unsigned half-metres above a 1727.4 km sphere, columns from
longitude -180 to +180 and rows from north to south, sample centres at half steps. SVS says the kit's maps are made
for appearance, not science; the record and doc repeat that.

Output: a latitude-longitude sphere on a 1 degree grid, 361 x 181 = 65,341 vertices and 128,880 triangles, with
POSITION, NORMAL and TEXCOORD_0 and nothing else (no material, no image):
- each vertex's radius is 1727.4 km plus 0.5 m times the mean height of the 16 x 16 LOLA samples in the 1 degree
  box centred on it; at each pole, the mean of the polar band within half a degree of it [measured];
- true scale, no exaggeration: 10 units = 1737.4 km, the scale `apps/web/src/spatial/webgl/cislunarRegime.ts` applies
  (it scales the mesh by moonRadius / 10);
- the conventions of the glb this replaces, measured on its vertices: +y is lunar north, longitude 0 lies toward +x
  and 90 E toward -z; u = (longitude + 180) / 360 and v = (90 - latitude) / 180, matching the app's equirectangular
  colour and bump maps; the seam column is doubled at +-180 so the UVs stay continuous, and each pole is a row of
  361 vertices at one point (the copy at +180 is used by no triangle);
- normals are the area-weighted mean of the faces around each point, with the seam's and the poles' copies sharing
  one normal.

Usage, from the repository root:
    uv run python scripts/bake_moon_mesh.py
    uv run python scripts/bake_moon_mesh.py --verify
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import struct
import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.contracts.provenance import AssetProvenanceRecord, dumps_record  # noqa: E402


def _load_moon_maps():
    spec = importlib.util.spec_from_file_location("bake_moon_maps", ROOT_DIR / "scripts" / "bake_moon_maps.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load scripts/bake_moon_maps.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


maps = _load_moon_maps()

DEFAULT_CACHE = ROOT_DIR / ".cache" / "moon-svs4720"
MODELS_DIR = ROOT_DIR / "apps" / "web" / "public" / "models"
TEXTURES_RECORD = ROOT_DIR / "apps" / "web" / "public" / "textures" / "PROVENANCE.json"
OUT_NAME = "nasa_lro_moon_3d.glb"
SOURCE_ID = "nasa-svs-4720-ldem-16"
SAMPLES_PER_DEGREE = 16
ROWS = 181  # latitude 90 to -90
COLUMNS = 361  # longitude -180 to +180, the seam doubled
REFERENCE_RADIUS_M = 1_737_400.0
UNITS_AT_REFERENCE = 10.0


def read_heights(path: Path) -> np.ndarray:
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(path) as image:
        heights = np.asarray(image, dtype=np.float64)
    if heights.shape != (180 * SAMPLES_PER_DEGREE, 360 * SAMPLES_PER_DEGREE):
        raise ValueError(f"expected 2880 x 5760 heights, got {heights.shape}")
    return heights


def box_mean_heights(heights: np.ndarray) -> np.ndarray:
    """Mean height (in the file's half-metre units) in the 1 degree box centred on each grid point, shape (181, 360);
    each pole row holds the mean of the half degree band around its pole."""
    s = SAMPLES_PER_DEGREE
    half = s // 2
    # Shift by half a degree so column block c spans longitude -180.5 + c to -179.5 + c, wrapping at the seam
    shifted = np.roll(heights, half, axis=1)
    interior = shifted[half : 180 * s - half].reshape(179, s, 360, s).mean(axis=(1, 3))
    north = np.full(360, heights[:half].mean())
    south = np.full(360, heights[180 * s - half :].mean())
    return np.vstack([north, interior, south])


def build_mesh(mean_heights: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Positions (float32), normals (float32), UVs (float32) and uint16 triangles of the 361 x 181 grid."""
    radius_m = maps.HEIGHT_BASE_M + maps.HEIGHT_STEP_M * mean_heights  # (181, 360)
    radius = np.concatenate([radius_m, radius_m[:, :1]], axis=1) / REFERENCE_RADIUS_M * UNITS_AT_REFERENCE  # seam
    lat = np.radians(90.0 - np.arange(ROWS, dtype=np.float64))[:, None]
    lon = np.radians(-180.0 + np.arange(COLUMNS, dtype=np.float64))[None, :]
    direction = np.stack(
        np.broadcast_arrays(np.cos(lat) * np.cos(lon), np.sin(lat), -np.cos(lat) * np.sin(lon)), axis=-1
    )
    points = (radius[..., None] * direction).reshape(-1, 3)

    def vid(r: int, c: int) -> int:
        return r * COLUMNS + c

    triangles = []
    for r in range(ROWS - 1):
        for c in range(COLUMNS - 1):
            a, b, d, e = vid(r, c), vid(r, c + 1), vid(r + 1, c), vid(r + 1, c + 1)
            if r == 0:
                triangles.append((a, d, e))  # the north pole row is one point
            elif r == ROWS - 2:
                triangles.append((a, d, b))  # the south pole row is one point
            else:
                triangles.extend([(a, d, e), (a, e, b)])
    tri = np.array(triangles, dtype=np.int64)

    # One normal per point: the seam column shares column 0's, each pole row one normal
    canonical = np.arange(ROWS * COLUMNS).reshape(ROWS, COLUMNS)
    canonical[:, COLUMNS - 1] = canonical[:, 0]
    canonical[0, :] = canonical[0, 0]
    canonical[ROWS - 1, :] = canonical[ROWS - 1, 0]
    canonical = canonical.reshape(-1)
    face = np.cross(points[tri[:, 1]] - points[tri[:, 0]], points[tri[:, 2]] - points[tri[:, 0]])  # area-weighted
    accum = np.zeros_like(points)
    for k in range(3):
        np.add.at(accum, canonical[tri[:, k]], face)
    normals = accum[canonical]
    normals /= np.linalg.norm(normals, axis=1, keepdims=True)
    if np.any(np.sum(normals * points, axis=1) <= 0):
        raise ValueError("a normal points inward")

    uv = np.stack(
        np.broadcast_arrays(np.arange(COLUMNS)[None, :] / 360.0, np.arange(ROWS)[:, None] / 180.0), axis=-1
    ).reshape(-1, 2)
    return points.astype(np.float32), normals.astype(np.float32), uv.astype(np.float32), tri.astype(np.uint16)


def dumps_glb(positions: np.ndarray, normals: np.ndarray, uvs: np.ndarray, triangles: np.ndarray) -> bytes:
    blobs = [np.ascontiguousarray(a, dtype=d).tobytes() for a, d in ((positions, "<f4"), (normals, "<f4"), (uvs, "<f4"), (triangles.ravel(), "<u2"))]
    offsets, offset = [], 0
    for blob in blobs:
        offsets.append(offset)
        offset += len(blob) + (-len(blob)) % 4
    binary = b"".join(blob + b"\x00" * ((-len(blob)) % 4) for blob in blobs)
    n = int(positions.shape[0])

    def bounds(a: np.ndarray) -> dict[str, list[float]]:
        return {"min": [float(x) for x in a.min(axis=0)], "max": [float(x) for x in a.max(axis=0)]}

    gltf: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "Albedian scripts/bake_moon_mesh.py", "copyright": "LOLA heights from NASA's Scientific Visualization Studio, CGI Moon Kit (SVS 4720)"},
        "scene": 0,
        "scenes": [{"name": "Moon", "nodes": [0]}],
        "nodes": [{"name": "Moon_LOLA_ldem16_1deg", "mesh": 0}],
        "meshes": [{"name": "Moon_LOLA_ldem16_1deg", "primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2}, "indices": 3, "mode": 4}]}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": n, "type": "VEC3", **bounds(positions)},
            {"bufferView": 1, "componentType": 5126, "count": n, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5126, "count": n, "type": "VEC2"},
            {"bufferView": 3, "componentType": 5123, "count": int(triangles.size), "type": "SCALAR", "min": [int(triangles.min())], "max": [int(triangles.max())]},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": offsets[i], "byteLength": len(blobs[i]), "target": 34963 if i == 3 else 34962}
            for i in range(4)
        ],
        "buffers": [{"byteLength": len(binary)}],
    }
    json_bytes = json.dumps(gltf, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    json_bytes += b" " * ((-len(json_bytes)) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(binary)
    return (
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
        + struct.pack("<II", len(binary), 0x004E4942) + binary
    )  # fmt: skip


def read_glb(glb: bytes) -> dict[str, np.ndarray]:
    """The mesh's attributes and indices, as this bake writes them (and as the glb it replaced held them)."""
    json_len, _ = struct.unpack_from("<I4s", glb, 12)
    gltf = json.loads(glb[20 : 20 + json_len])
    bin_len, _ = struct.unpack_from("<I4s", glb, 20 + json_len)
    binary = glb[28 + json_len : 28 + json_len + bin_len]
    primitive = gltf["meshes"][0]["primitives"][0]
    sizes = {"VEC3": 3, "VEC2": 2, "SCALAR": 1}
    dtypes = {5126: "<f4", 5123: "<u2", 5125: "<u4"}

    def accessor(index: int) -> np.ndarray:
        a = gltf["accessors"][index]
        view = gltf["bufferViews"][a["bufferView"]]
        k = sizes[a["type"]]
        arr = np.frombuffer(binary, dtype=dtypes[a["componentType"]], count=a["count"] * k, offset=view.get("byteOffset", 0) + a.get("byteOffset", 0))
        return arr.reshape(-1, k) if k > 1 else arr

    out = {name: accessor(i) for name, i in primitive["attributes"].items()}
    out["indices"] = accessor(primitive["indices"])
    return out


def ldem_source() -> dict[str, Any]:
    """The LOLA source as the textures folder's record has it, so both records name it alike."""
    record = json.loads(TEXTURES_RECORD.read_text(encoding="utf-8"))
    return next(s for s in record["sources"] if s["id"] == SOURCE_ID)


def file_note(glb_bytes: int) -> str:
    return (
        f"The Moon at true scale on a 1 degree latitude-longitude grid: 361 x 181 = 65,341 vertices, 128,880 "
        f"triangles, {glb_bytes:,} bytes. Measured: each vertex's radius is 1727.4 km plus the mean LOLA height in "
        "the 1 degree box centred on it (at each pole, the mean of the half degree band), with no exaggeration; 10 "
        "units = 1737.4 km. SVS made the CGI Moon Kit's maps for appearance, not science. Conventions kept from the "
        "mesh this replaced: +y north, longitude 0 toward +x, 90 E toward -z, u = (longitude + 180) / 360, "
        "v = (90 - latitude) / 180. POSITION, NORMAL and TEXCOORD_0 only, no material or image."
    )


def build_record(existing: AssetProvenanceRecord, glb: bytes) -> AssetProvenanceRecord:
    """The models folder record with the LOLA source (first, so the other bakes keep their order) and the mesh."""
    data = existing.model_dump(mode="json", by_alias=True, exclude_none=True)
    others = [s for s in data["sources"] if s["id"] != SOURCE_ID]
    data["sources"] = [ldem_source(), *others]
    entry = {
        "path": OUT_NAME,
        "sha256": hashlib.sha256(glb).hexdigest(),
        "sources": [SOURCE_ID],
        "producedBy": {"method": "script", "script": "scripts/bake_moon_mesh.py", "command": "uv run python scripts/bake_moon_mesh.py"},
        "note": file_note(len(glb)),
    }
    data["files"] = sorted([*(f for f in data["files"] if f["path"] != OUT_NAME), entry], key=lambda f: f["path"])
    return AssetProvenanceRecord.model_validate(data)


def bake(cache: Path) -> bytes:
    heights = read_heights(maps.pinned_input(cache, maps.HEIGHT_INPUT))
    return dumps_glb(*build_mesh(box_mean_heights(heights)))


def verify_outputs(cache: Path, models_dir: Path) -> list[str]:
    """Compare the committed mesh with a fresh bake within a stated tolerance (never byte for byte: the positions use
    trigonometry, whose last bit may differ by platform), then the record entry against the committed file."""
    errors = []
    target = models_dir / OUT_NAME
    committed = read_glb(target.read_bytes())
    fresh = read_glb(bake(cache))
    if not np.array_equal(committed["indices"], fresh["indices"]) or not np.array_equal(committed["TEXCOORD_0"], fresh["TEXCOORD_0"]):
        errors.append(f"{target}: triangles or UVs differ from a fresh bake")
    elif np.max(np.abs(committed["POSITION"] - fresh["POSITION"])) > 1e-5 or np.max(np.abs(committed["NORMAL"] - fresh["NORMAL"])) > 1e-5:
        errors.append(f"{target}: positions or normals differ from a fresh bake by more than 1e-5 units")
    record_path = models_dir / "PROVENANCE.json"
    existing = AssetProvenanceRecord.model_validate_json(record_path.read_text(encoding="utf-8"))
    if record_path.read_text(encoding="utf-8") != dumps_record(build_record(existing, target.read_bytes())):
        errors.append(f"{record_path} differs from the record this bake writes")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--models-dir", type=Path, default=MODELS_DIR)
    parser.add_argument("--verify", action="store_true", help="check the committed mesh and its record entry")
    args = parser.parse_args(argv)
    if args.verify:
        errors = verify_outputs(args.cache, args.models_dir)
        for err in errors:
            print(f"[moon-mesh] {err}", file=sys.stderr)
        if not errors:
            print("[moon-mesh] nasa_lro_moon_3d.glb and its record entry match a fresh bake")
        return 1 if errors else 0
    glb = bake(args.cache)
    (args.models_dir / OUT_NAME).write_bytes(glb)
    record_path = args.models_dir / "PROVENANCE.json"
    existing = AssetProvenanceRecord.model_validate_json(record_path.read_text(encoding="utf-8"))
    record_path.write_text(dumps_record(build_record(existing, glb)), encoding="utf-8")
    print(f"[moon-mesh] {OUT_NAME}: {len(glb):,} bytes, {ROWS * COLUMNS:,} vertices")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
