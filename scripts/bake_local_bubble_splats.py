#!/usr/bin/env python3
"""Bake the Local Bubble splats, `apps/web/public/models/lb_splats.json`, from the O'Neill et al. (2024) shell table (data-8).

Input: the table `scripts/bake_local_bubble_mesh.py` reads, `ONeill2024_LocalBubble_ShellProperties_A0.5.fits`
(Harvard Dataverse doi:10.7910/DVN/INB1RB, CC0-1.0, registered in `data/downloads.json`), and the mesh that script
wrote from it, `apps/web/public/models/local_bubble_oneill2024.glb` (27,648 vertices, one per HEALPix nside 48 cell,
heliocentric Galactic Cartesian, 1 unit = 100 pc).

One splat per mesh vertex, as a 14-number row [x, y, z, r, g, b, sx, sy, sz, qx, qy, qz, qw, opacity], the layout
`apps/web/src/spatial/splatRows.ts` reads. Within each nside 48 cell the table rows whose nside 256 directions fall
in it (19 to 36 finite rows, median 30) are averaged, as the mesh averages the shell distance:

- centre: the mesh vertex itself, at the cell's mean peak distance d, written as the shortest decimal that reads back
  as the same float32 [measured]; the slab is centred there, although d_inner and d_outer need not be symmetric
  about d;
- orientation: the third splat axis along the cell's mean shell normal (the table's nx, ny, nz, each first turned to
  point away from the Sun, since the table's sign is arbitrary: 48% of its normals point inward) [measured];
- thickness along the normal: the table's thickness, thick = d_outer - d_inner, is measured along the line of sight;
  each row's thick times |n . r| (n its normal, r its direction; |n . r| = cos of the paper's inclination gamma)
  projects it onto the normal, and the cell's mean of that is drawn as a uniform slab's sigma, width / sqrt(12)
  [measured, projected];
- footprint across the normal: the cell's side, sqrt(4 pi / 27,648) times the vertex distance, as a slab sigma: grid
  geometry, a presentation value, not a measurement;
- opacity: 1 - 10^(-0.4 A_G), the fraction of G-band light the shell absorbs along the line of sight from the Sun,
  with A_G the cell's mean integrated extinction through the shell (O'Neill et al. 2024, section II.3.1 and Table 1).
  This is a presentation mapping of a measured quantity: a splat seen from elsewhere does not absorb that fraction;
- colour: one neutral grey for every splat, simulated. Dust extinction is not emission, so no colour stands for an
  emission line.

Usage, from the repository root:
    uv run python scripts/bake_local_bubble_splats.py
    uv run python scripts/bake_local_bubble_splats.py --verify
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import struct
import sys
from pathlib import Path

import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.contracts.provenance import (  # noqa: E402
    ASSET_PROVENANCE_FORMAT,
    ASSET_PROVENANCE_FORMAT_VERSION,
    AssetProvenanceRecord,
    dumps_record,
)


def _load_mesh_bake():
    spec = importlib.util.spec_from_file_location("bake_local_bubble_mesh", ROOT_DIR / "scripts" / "bake_local_bubble_mesh.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load scripts/bake_local_bubble_mesh.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


mesh = _load_mesh_bake()

DEFAULT_IN = ROOT_DIR / ".cache" / "dust" / "oneill2024"
MODELS_DIR = ROOT_DIR / "apps" / "web" / "public" / "models"
OUT_NAME = "lb_splats.json"
NSIDE_OUT = mesh.DEFAULT_NSIDE_OUT
NSIDE_IN = mesh.DEFAULT_NSIDE_IN
PC_PER_UNIT = mesh.PC_PER_UNIT
# One neutral grey, a presentation value (equal red, green and blue)
NEUTRAL_GREY = 0.8
SLAB_SIGMA = 1.0 / math.sqrt(12.0)
ROUND = 4  # decimals for footprint, quaternion and opacity (0.01 pc on a footprint)


def read_glb_positions(glb: bytes) -> np.ndarray:
    """The POSITION accessor of the mesh's single primitive, float32 (n, 3)."""
    magic, _version, _length = struct.unpack_from("<4sII", glb, 0)
    if magic != b"glTF":
        raise ValueError("not a glb")
    json_len, json_type = struct.unpack_from("<I4s", glb, 12)
    if json_type != b"JSON":
        raise ValueError("first glb chunk is not JSON")
    gltf = json.loads(glb[20 : 20 + json_len])
    bin_offset = 20 + json_len
    bin_len, bin_type = struct.unpack_from("<I4s", glb, bin_offset)
    if bin_type != b"BIN\x00":
        raise ValueError("second glb chunk is not BIN")
    binary = glb[bin_offset + 8 : bin_offset + 8 + bin_len]
    accessor = gltf["accessors"][gltf["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
    view = gltf["bufferViews"][accessor["bufferView"]]
    if accessor["componentType"] != 5126 or accessor["type"] != "VEC3":
        raise ValueError("POSITION is not float32 VEC3")
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    return np.frombuffer(binary, dtype="<f4", count=accessor["count"] * 3, offset=start).reshape(-1, 3)


def outward(normal: np.ndarray, radial: np.ndarray) -> np.ndarray:
    """Each normal, negated where it points toward the Sun (the table's sign is arbitrary)."""
    return np.where(np.sum(normal * radial, axis=1, keepdims=True) < 0.0, -normal, normal)


def cell_means(cell: np.ndarray, radial: np.ndarray, normal: np.ndarray, thick_pc: np.ndarray, ag_mag: np.ndarray, n_cells: int) -> dict[str, np.ndarray]:
    """Per cell, over its rows: the mean outward unit normal, the mean thickness along the normal (each row's
    line-of-sight thickness times |n . r|), the mean A_G, and the row count. Every row given must be finite."""
    counts = np.bincount(cell, minlength=n_cells)
    if np.any(counts == 0):
        raise ValueError(f"{int(np.sum(counts == 0))} cell(s) have no finite table row")

    def mean(values: np.ndarray) -> np.ndarray:
        return np.bincount(cell, weights=values, minlength=n_cells) / counts

    turned = outward(normal, radial)
    n_mean = np.column_stack([mean(turned[:, k]) for k in range(3)])
    n_len = np.linalg.norm(n_mean, axis=1)
    if np.any(n_len < 1e-6):
        raise ValueError("a cell's outward normals cancel")
    along_normal = thick_pc * np.abs(np.sum(normal * radial, axis=1))
    return {"thick_normal_pc": mean(along_normal), "ag_mag": mean(ag_mag), "normal": n_mean / n_len[:, None], "rows": counts}


def coarse_cell_means(cols: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """`cell_means` over the table's finite rows, per nside 48 cell, indexed as `aggregate_shell_distances` indexes the
    mesh vertices. A row counts only if every value used is finite (the 40 blank rows are blank in all of them)."""
    face, fx, fy = mesh.radec_to_face_xy(cols["l"], cols["b"])
    ix = np.clip((fx * NSIDE_OUT).astype(np.int64), 0, NSIDE_OUT - 1)
    iy = np.clip((fy * NSIDE_OUT).astype(np.int64), 0, NSIDE_OUT - 1)
    cell = face * NSIDE_OUT * NSIDE_OUT + ix * NSIDE_OUT + iy
    lr, br = np.radians(cols["l"]), np.radians(cols["b"])
    radial = np.column_stack([np.cos(br) * np.cos(lr), np.cos(br) * np.sin(lr), np.sin(br)])
    normal = np.column_stack([cols["nx"], cols["ny"], cols["nz"]])
    ok = np.all(np.isfinite(np.column_stack([cols["d"], cols["thick"], cols["Ag"], normal])), axis=1)
    return cell_means(cell[ok], radial[ok], normal[ok], cols["thick"][ok], cols["Ag"][ok], 12 * NSIDE_OUT * NSIDE_OUT)


def frame_quaternions(normal: np.ndarray) -> np.ndarray:
    """Unit quaternions (x, y, z, w) of rotations whose third axis is `normal`; the first axis lies in the Galactic plane."""
    z_gal = np.array([0.0, 0.0, 1.0])
    u_x = np.cross(z_gal, normal)
    small = np.linalg.norm(u_x, axis=1) < 1e-6
    u_x[small] = np.array([1.0, 0.0, 0.0])
    u_x /= np.linalg.norm(u_x, axis=1, keepdims=True)
    u_y = np.cross(normal, u_x)
    m = np.stack([u_x, u_y, normal], axis=2)  # columns are the splat axes
    w = np.sqrt(np.maximum(0.0, 1.0 + m[:, 0, 0] + m[:, 1, 1] + m[:, 2, 2])) / 2.0
    x = np.sqrt(np.maximum(0.0, 1.0 + m[:, 0, 0] - m[:, 1, 1] - m[:, 2, 2])) / 2.0
    y = np.sqrt(np.maximum(0.0, 1.0 - m[:, 0, 0] + m[:, 1, 1] - m[:, 2, 2])) / 2.0
    z = np.sqrt(np.maximum(0.0, 1.0 - m[:, 0, 0] - m[:, 1, 1] + m[:, 2, 2])) / 2.0
    x = np.copysign(x, m[:, 2, 1] - m[:, 1, 2])
    y = np.copysign(y, m[:, 0, 2] - m[:, 2, 0])
    z = np.copysign(z, m[:, 1, 0] - m[:, 0, 1])
    return np.column_stack([x, y, z, w])


def f32_text(value: np.float32) -> float:
    """The shortest decimal that reads back as this float32."""
    return float(np.format_float_positional(np.float32(value), unique=True, trim="-"))


def splat_rows(positions: np.ndarray, cells: dict[str, np.ndarray], n_grid_cells: int) -> list[list[float]]:
    """One row per position from its cell's means; the cross-normal footprint is the side of one of n_grid_cells
    equal-area cells at the position's distance."""
    distance_units = np.linalg.norm(positions.astype(np.float64), axis=1)
    across = math.sqrt(4.0 * math.pi / n_grid_cells) * distance_units * SLAB_SIGMA
    along = cells["thick_normal_pc"] / PC_PER_UNIT * SLAB_SIGMA
    quats = frame_quaternions(cells["normal"])
    opacity = 1.0 - np.power(10.0, -0.4 * cells["ag_mag"])
    rows = []
    for i in range(positions.shape[0]):
        rows.append(
            [f32_text(positions[i, 0]), f32_text(positions[i, 1]), f32_text(positions[i, 2]),
             NEUTRAL_GREY, NEUTRAL_GREY, NEUTRAL_GREY,
             round(float(across[i]), ROUND), round(float(across[i]), ROUND), round(float(along[i]), ROUND),
             *(round(float(q), ROUND) for q in quats[i]),
             round(float(opacity[i]), ROUND)]
        )  # fmt: skip
    return rows


def build_rows(cols: dict[str, np.ndarray], positions: np.ndarray) -> list[list[float]]:
    n_cells = 12 * NSIDE_OUT * NSIDE_OUT
    if positions.shape != (n_cells, 3):
        raise ValueError(f"the mesh has {positions.shape[0]} vertices, expected {n_cells}")
    return splat_rows(positions, coarse_cell_means(cols), n_cells)


def dumps_rows(rows: list[list[float]]) -> str:
    """Single-line compact JSON with a final newline (one line, so the trip guard never pairs values across rows)."""
    return json.dumps(rows, allow_nan=False, separators=(",", ":")) + "\n"


SPLATS_NOTE = (
    "One Gaussian splat per vertex of local_bubble_oneill2024.glb (27,648; rows [x, y, z, r, g, b, sx, sy, sz, qx, "
    "qy, qz, qw, opacity], 1 unit = 100 pc), from the same O'Neill et al. (2024) shell table, averaged per HEALPix "
    "nside 48 cell over 19 to 36 rows. Measured: the centre (the mesh vertex, at the peak distance d, where the slab "
    "is centred), the orientation (the table's shell normal, turned outward), the thickness along the normal (the "
    "table's line-of-sight thickness times |n . r|, drawn as a uniform slab's sigma, width / sqrt(12)). Grid "
    "geometry, a presentation value: the footprint across the normal (the cell's side as a slab sigma). Presentation "
    "mapping of a measured quantity: opacity = 1 - 10^(-0.4 A_G), the fraction of G-band light the shell absorbs "
    "along the line of sight from the Sun. Simulated: the colour, one neutral grey for every splat; dust extinction "
    "is not emission, so no colour stands for an emission line."
)


def build_record(existing: AssetProvenanceRecord, splats_sha256: str) -> AssetProvenanceRecord:
    """The models folder record with the splat entry added or replaced; every other entry kept as it is."""
    if mesh.SOURCE_ID not in {s.id for s in existing.sources}:
        raise ValueError(f"the models record lacks the source {mesh.SOURCE_ID}; bake the mesh first")
    others = [f.model_dump(mode="json", by_alias=True, exclude_none=True) for f in existing.files if f.path != OUT_NAME]
    entry = {
        "path": OUT_NAME,
        "sha256": splats_sha256,
        "sources": [mesh.SOURCE_ID],
        "producedBy": {
            "method": "script",
            "script": "scripts/bake_local_bubble_splats.py",
            "command": "uv run python scripts/bake_local_bubble_splats.py",
        },
        "note": SPLATS_NOTE,
    }
    data = existing.model_dump(mode="json", by_alias=True, exclude_none=True)
    data["files"] = sorted([*others, entry], key=lambda f: f["path"])
    return AssetProvenanceRecord.model_validate(data)


def built_text(in_dir: Path, models_dir: Path) -> str:
    fits_path = mesh.verify_input(in_dir)
    cols = mesh.read_shell_fits_table(fits_path)
    positions = read_glb_positions((models_dir / mesh.GLB_FILENAME).read_bytes())
    return dumps_rows(build_rows(cols, positions))


def verify_outputs(in_dir: Path, models_dir: Path) -> list[str]:
    """Compare the committed splats with a fresh bake: centres exactly as float32, every other value within half a
    rounding step (never byte for byte: the bake uses trigonometry and powers, whose last bit may differ by platform);
    then check the record entry against the committed file's own checksum."""
    errors = []
    target = models_dir / OUT_NAME
    if not target.is_file():
        return [f"{target} is missing"]
    committed = np.array(json.loads(target.read_text(encoding="utf-8")), dtype=np.float64)
    fresh = np.array(json.loads(built_text(in_dir, models_dir)), dtype=np.float64)
    if committed.shape != fresh.shape:
        errors.append(f"{target} has shape {committed.shape}, a fresh bake {fresh.shape}")
    elif not np.array_equal(committed[:, :3].astype(np.float32), fresh[:, :3].astype(np.float32)):
        errors.append(f"{target}: a centre differs from its mesh vertex")
    elif np.max(np.abs(committed[:, 3:] - fresh[:, 3:])) > 0.5 * 10.0**-ROUND + 1e-9:
        errors.append(f"{target}: a value differs from a fresh bake by more than half a rounding step")
    record_path = models_dir / "PROVENANCE.json"
    existing = AssetProvenanceRecord.model_validate_json(record_path.read_text(encoding="utf-8"))
    expected = dumps_record(build_record(existing, hashlib.sha256(target.read_bytes()).hexdigest()))
    if record_path.read_text(encoding="utf-8") != expected:
        errors.append(f"{record_path} differs from the record this bake writes")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--in-dir", type=Path, default=DEFAULT_IN)
    parser.add_argument("--models-dir", type=Path, default=MODELS_DIR)
    parser.add_argument("--verify", action="store_true", help="check the committed splats and record against a fresh bake")
    args = parser.parse_args(argv)
    if args.verify:
        errors = verify_outputs(args.in_dir, args.models_dir)
        for err in errors:
            print(f"[lb-splats] {err}", file=sys.stderr)
        if not errors:
            print("[lb-splats] lb_splats.json and its record entry match a fresh bake")
        return 1 if errors else 0
    text = built_text(args.in_dir, args.models_dir)
    (args.models_dir / OUT_NAME).write_text(text, encoding="utf-8")
    record_path = args.models_dir / "PROVENANCE.json"
    existing = AssetProvenanceRecord.model_validate_json(record_path.read_text(encoding="utf-8"))
    record_path.write_text(dumps_record(build_record(existing, hashlib.sha256(text.encode("utf-8")).hexdigest())), encoding="utf-8")
    print(f"[lb-splats] {OUT_NAME}: {(args.models_dir / OUT_NAME).stat().st_size:,} bytes, {12 * NSIDE_OUT * NSIDE_OUT:,} splats")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
