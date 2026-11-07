#!/usr/bin/env python3
"""Bake the O'Neill et al. (2024) Local Bubble 3D shell mesh into a glTF 2.0 binary (`.glb`).

Reads the Harvard Dataverse FITS binary table
`ONeill2024_LocalBubble_ShellProperties_A0.5.fits` (`doi:10.7910/DVN/INB1RB`,
companion data to O'Neill et al. 2024, ApJ 973, 136, `doi:10.3847/1538-4357/ad47b5`),
which tabulates the Local Bubble shell distance `d` (in parsecs) and heliocentric
Galactic Cartesian coordinates `(x, y, z)` across all 786,432 directions of an
`nside = 256` HEALPix sky grid derived from the Edenhofer et al. (2024) 3D dust map
at extinction threshold `A_G = 0.5 mag/kpc`.

Pipeline:
1. Verify the input FITS file's byte count and SHA-256 against the pinned
   Harvard Dataverse release (`CC0-1.0`).
2. Parse the FITS `BINTABLE` extension (`NAXIS2 = 786,432` rows of big-endian
   IEEE-754 float64 columns) and verify 1-to-1 coverage of the `nside = 256`
   HEALPix grid.
3. Aggregate valid (`isfinite(d)`) fine pixels into each cell of the coarse
   `nside = 48` HEALPix grid (`12 * 48^2 = 27,648` vertices). The 40 `NaN`
   directions in the `nside = 256` table (`0.005%` of the sky, near the Northern
   Galactic Chimney) fall inside coarse `48x48` cells that each retain at least
   19 valid samples, yielding a complete, finite shell distance on every coarse
   cell without artificial extrapolation.
4. Build the closed, orientable 2-manifold triangulation of the 12-face HEALPix
   sphere (`24 * nside^2 - 4 = 55,292` outward-facing triangles, Euler
   characteristic `V - E + F = 27,648 - 82,938 + 55,292 = 2`), convert
   heliocentric Galactic Cartesian coordinates to scene units (`1 unit = 100 pc`),
   compute area-weighted outward unit vertex normals, and write
   `apps/web/public/models/local_bubble_oneill2024.glb` and
   `apps/web/public/models/PROVENANCE.json`.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.contracts.provenance import (  # noqa: E402
    ASSET_PROVENANCE_FORMAT,
    ASSET_PROVENANCE_FORMAT_VERSION,
    AssetProvenanceRecord,
    MadeByScript,
    ProvenanceFile,
    ProvenanceLicense,
    ProvenanceSource,
    dumps_record,
    sha256_file,
)

DEFAULT_IN_DIR = ROOT / ".cache" / "dust" / "oneill2024"
DEFAULT_OUT_DIR = ROOT / "apps" / "web" / "public" / "models"
GLB_FILENAME = "local_bubble_oneill2024.glb"

DEFAULT_NSIDE_IN = 256
DEFAULT_NSIDE_OUT = 48
PC_PER_UNIT = 100.0
PRODUCING_SCRIPT = "scripts/bake_local_bubble_mesh.py"
PRODUCING_COMMAND = "uv run python scripts/bake_local_bubble_mesh.py"
SOURCE_ID = "oneill-2024-local-bubble"


@dataclass(frozen=True)
class InputSpec:
    filename: str
    url: str
    size: int
    sha256: str


INPUT = InputSpec(
    filename="ONeill2024_LocalBubble_ShellProperties_A0.5.fits",
    url="https://dataverse.harvard.edu/api/access/datafile/8943783",
    size=138421440,
    sha256="3d8f214426f0c4b4f6eeb69030610f66c0166b87fbb3764bdab1b3f9751ca9ff",
)


# ---------------------------------------------------------------------------
# HEALPix base-face geometry (12 base faces, Górski et al. 2005)
# ---------------------------------------------------------------------------

_FJR = np.array([2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4], dtype=np.float64)
_FJC = np.array([1, 3, 5, 7, 0, 2, 4, 6, 1, 3, 5, 7], dtype=np.float64)


def radec_to_face_xy(lon_deg: np.ndarray, lat_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Project spherical `(lon_deg, lat_deg)` onto HEALPix `(face, fx, fy)` with `fx, fy in [0, 1)`."""
    phi = np.deg2rad(np.mod(lon_deg, 360.0))
    theta = np.deg2rad(90.0 - np.clip(lat_deg, -90.0, 90.0))
    z = np.cos(theta)
    za = np.abs(z)
    tt = np.mod(phi / (0.5 * math.pi), 4.0)

    face = np.zeros_like(z, dtype=np.int64)
    fx = np.zeros_like(z, dtype=np.float64)
    fy = np.zeros_like(z, dtype=np.float64)

    eq = za <= (2.0 / 3.0)
    if np.any(eq):
        temp1 = 0.5 + tt[eq]
        temp2 = z[eq] * 0.75
        jp = temp1 - temp2
        jm = temp1 + temp2
        ifp = np.floor(jp).astype(np.int64)
        ifm = np.floor(jm).astype(np.int64)
        f_eq = np.where(ifp == ifm, (ifp % 4) + 4, np.where(ifp < ifm, ifp % 4, (ifm % 4) + 8))
        face[eq] = f_eq
        fx[eq] = np.mod(jm, 1.0)
        fy[eq] = 1.0 - np.mod(jp, 1.0)

    pol = ~eq
    if np.any(pol):
        ntt = np.minimum(np.floor(tt[pol]).astype(np.int64), 3)
        tp = tt[pol] - ntt
        tmp = np.sqrt(3.0 * (1.0 - za[pol]))
        jp = tp * tmp
        jm = (1.0 - tp) * tmp
        jp = np.minimum(jp, 1.0 - 1e-12)
        jm = np.minimum(jm, 1.0 - 1e-12)
        north = z[pol] >= 0.0
        face[pol] = np.where(north, ntt, ntt + 8)
        fx[pol] = np.where(north, 1.0 - jm, jp)
        fy[pol] = np.where(north, 1.0 - jp, jm)

    return face, np.clip(fx, 0.0, 1.0 - 1e-12), np.clip(fy, 0.0, 1.0 - 1e-12)


def face_xy_to_vec(
    face: np.ndarray, fx: np.ndarray, fy: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Inverse HEALPix mapping: `(face, fx, fy)` to unit Cartesian vector `(ux, uy, uz)`."""
    jr = _FJR[face] - fx - fy
    nr = np.ones_like(jr)
    z = np.zeros_like(jr)
    kshift = np.zeros_like(jr)

    north = jr < 1.0
    south = jr > 3.0
    mid = ~(north | south)

    if np.any(north):
        nr[north] = jr[north]
        z[north] = 1.0 - nr[north] * nr[north] / 3.0
    if np.any(south):
        nr[south] = 4.0 - jr[south]
        z[south] = -1.0 + nr[south] * nr[south] / 3.0
    if np.any(mid):
        nr[mid] = 1.0
        z[mid] = (2.0 - jr[mid]) * 2.0 / 3.0
        kshift[mid] = np.mod(jr[mid] - 1.0, 2.0)

    jp = (_FJC[face] * nr + fx - fy + 1.0 + kshift) * 0.5
    phi = (jp - 0.5 * (kshift + 1.0)) * (0.5 * math.pi) / np.maximum(nr, 1e-15)
    phi = np.mod(phi, 2.0 * math.pi)
    sth = np.sqrt(np.maximum(0.0, 1.0 - z * z))
    return sth * np.cos(phi), sth * np.sin(phi), z


# ---------------------------------------------------------------------------
# FITS BINTABLE parser
# ---------------------------------------------------------------------------


def read_shell_fits_table(path: Path) -> dict[str, np.ndarray]:
    """Read HDU 1 (`BINTABLE`) of an O'Neill et al. (2024) Local Bubble shell FITS file."""
    raw = path.read_bytes()
    offset = 0
    found_bintable = False
    row_bytes = 0
    n_rows = 0
    n_fields = 0
    col_names: dict[int, str] = {}
    col_forms: dict[int, str] = {}

    while offset + 2880 <= len(raw):
        block = raw[offset : offset + 2880].decode("ascii", errors="replace")
        offset += 2880
        cards: dict[str, str] = {}
        end_seen = False
        for i in range(0, 2880, 80):
            card = block[i : i + 80]
            key = card[:8].strip()
            if key == "END":
                end_seen = True
                break
            if "=" in card[:10]:
                val_part = card[10:].split("/")[0].strip()
                if val_part.startswith("'") and "'" in val_part[1:]:
                    val_part = val_part[1 : val_part.index("'", 1)].strip()
                cards[key] = val_part
                if key.startswith("TTYPE") and key[5:].isdigit():
                    col_names[int(key[5:])] = val_part
                elif key.startswith("TFORM") and key[5:].isdigit():
                    col_forms[int(key[5:])] = val_part

        if cards.get("XTENSION") == "BINTABLE":
            found_bintable = True
        if found_bintable:
            if "NAXIS1" in cards:
                row_bytes = int(cards["NAXIS1"])
            if "NAXIS2" in cards:
                n_rows = int(cards["NAXIS2"])
            if "TFIELDS" in cards:
                n_fields = int(cards["TFIELDS"])
        if end_seen:
            if found_bintable:
                break
            # Skip any primary-HDU payload bytes if present.
            naxis = int(cards.get("NAXIS", "0"))
            if naxis > 0:
                bitpix = abs(int(cards.get("BITPIX", "8")))
                n_elems = 1
                for ax in range(1, naxis + 1):
                    n_elems *= int(cards.get(f"NAXIS{ax}", "0"))
                payload_bytes = (n_elems * bitpix) // 8
                offset += ((payload_bytes + 2879) // 2880) * 2880

    if not found_bintable:
        raise ValueError(f"{path}: no BINTABLE extension found")
    if n_fields <= 0 or row_bytes != n_fields * 8:
        raise ValueError(f"{path}: expected 8-byte float64 columns, got NAXIS1={row_bytes}, TFIELDS={n_fields}")

    total_bytes = row_bytes * n_rows
    if offset + total_bytes > len(raw):
        raise ValueError(f"{path}: truncated BINTABLE payload")

    table = np.frombuffer(raw, dtype=">f8", count=n_rows * n_fields, offset=offset).reshape((n_rows, n_fields))
    result: dict[str, np.ndarray] = {}
    for idx in range(1, n_fields + 1):
        form = col_forms.get(idx, "")
        if form not in ("D", "1D"):
            raise ValueError(f"{path}: unsupported column format TFORM{idx}={form!r}")
        name = col_names.get(idx, f"col_{idx}")
        result[name] = table[:, idx - 1].astype(np.float64, copy=True)

    for required in ("l", "b", "d"):
        if required not in result:
            raise ValueError(f"{path}: missing required FITS column {required!r}")

    return result


# ---------------------------------------------------------------------------
# HEALPix shell aggregation & closed 2-manifold triangulation
# ---------------------------------------------------------------------------


def aggregate_shell_distances(
    l_deg: np.ndarray,
    b_deg: np.ndarray,
    d_pc: np.ndarray,
    *,
    nside_in: int = DEFAULT_NSIDE_IN,
    nside_out: int = DEFAULT_NSIDE_OUT,
) -> tuple[np.ndarray, int]:
    """Aggregate fine `nside_in` shell distances onto coarse `nside_out` HEALPix vertices.

    Returns `(positions_units, nan_input_pixels)` where `positions_units` has shape
    `(12 * nside_out^2, 3)` in units of `PC_PER_UNIT` (`100 pc`).
    """
    expected_rows = 12 * nside_in * nside_in
    if len(d_pc) != expected_rows:
        raise ValueError(f"expected {expected_rows} rows for nside_in={nside_in}, got {len(d_pc)}")
    if nside_in < nside_out:
        raise ValueError(f"nside_in ({nside_in}) must be >= nside_out ({nside_out})")

    face, fx, fy = radec_to_face_xy(l_deg, b_deg)
    ix_in = np.clip((fx * nside_in).astype(np.int64), 0, nside_in - 1)
    iy_in = np.clip((fy * nside_in).astype(np.int64), 0, nside_in - 1)
    fine_idx = face * (nside_in * nside_in) + ix_in * nside_in + iy_in
    if len(np.unique(fine_idx)) != expected_rows:
        raise ValueError("input FITS rows do not form a 1-to-1 HEALPix grid at nside_in")

    valid = np.isfinite(d_pc)
    nan_count = int(np.sum(~valid))
    if not np.all(d_pc[valid] > 0.0):
        raise ValueError("all finite rows must have a positive shell distance d > 0")

    n_verts = 12 * nside_out * nside_out
    ix_out = np.clip((fx * nside_out).astype(np.int64), 0, nside_out - 1)
    iy_out = np.clip((fy * nside_out).astype(np.int64), 0, nside_out - 1)
    coarse_idx = face * (nside_out * nside_out) + ix_out * nside_out + iy_out

    counts = np.bincount(coarse_idx[valid], minlength=n_verts)
    if np.any(counts == 0):
        empty_cells = int(np.sum(counts == 0))
        raise ValueError(f"{empty_cells} coarse HEALPix cell(s) are empty or all-NaN")

    sums = np.bincount(coarse_idx[valid], weights=d_pc[valid], minlength=n_verts)
    d_coarse = sums / counts

    coarse_faces = np.repeat(np.arange(12, dtype=np.int64), nside_out * nside_out)
    ij = np.arange(nside_out * nside_out, dtype=np.int64)
    c_ix = np.tile(ij // nside_out, 12)
    c_iy = np.tile(ij % nside_out, 12)
    ux, uy, uz = face_xy_to_vec(coarse_faces, (c_ix + 0.5) / nside_out, (c_iy + 0.5) / nside_out)
    scale = (d_coarse / PC_PER_UNIT).astype(np.float64)
    positions = np.column_stack([ux * scale, uy * scale, uz * scale]).astype(np.float32)
    return positions, nan_count


def build_healpix_triangles(nside: int, positions: np.ndarray) -> np.ndarray:
    """Build the closed, outward-oriented 2-manifold triangulation for a 12-face HEALPix grid.

    For `V = 12 * nside^2` vertices, returns `F = 24 * nside^2 - 4` triangles of `uint16` indices.
    """
    if nside < 2:
        raise ValueError(f"nside must be >= 2, got {nside}")
    n_verts = 12 * nside * nside
    if n_verts > 65535:
        raise ValueError(f"nside={nside} exceeds uint16 index limit ({n_verts} > 65535)")
    if positions.shape != (n_verts, 3):
        raise ValueError(f"positions shape {positions.shape} does not match (12 * nside^2, 3) = ({n_verts}, 3)")

    def vid(f: int, i: int, j: int) -> int:
        return f * nside * nside + i * nside + j

    tris: list[tuple[int, int, int]] = []

    def add_quad(a: int, b: int, c: int, d: int) -> None:
        tris.append((a, b, c))
        tris.append((a, c, d))

    # 1. Interior quads on each of the 12 base faces: 12 * (nside - 1)^2 quads.
    for f in range(12):
        for i in range(nside - 1):
            for j in range(nside - 1):
                add_quad(vid(f, i, j), vid(f, i + 1, j), vid(f, i + 1, j + 1), vid(f, i, j + 1))

    # 2. Seam quads across the 24 shared edges of the 12 HEALPix base faces: 24 * (nside - 1) quads.
    for c in range(4):
        c_next = (c + 1) % 4
        c_prev = (c + 3) % 4
        for k in range(nside - 1):
            # North polar cap face c (i = nside - 1) to face c_next (j = nside - 1).
            add_quad(
                vid(c, nside - 1, k),
                vid(c_next, k, nside - 1),
                vid(c_next, k + 1, nside - 1),
                vid(c, nside - 1, k + 1),
            )
            # North polar cap face c (j = 0) to equatorial face 4 + c_next (j = nside - 1).
            add_quad(
                vid(c, k, 0),
                vid(4 + c_next, k, nside - 1),
                vid(4 + c_next, k + 1, nside - 1),
                vid(c, k + 1, 0),
            )
            # North polar cap face c (i = 0) to equatorial face 4 + c (i = nside - 1).
            add_quad(
                vid(c, 0, k),
                vid(4 + c, nside - 1, k),
                vid(4 + c, nside - 1, k + 1),
                vid(c, 0, k + 1),
            )
            # Equatorial face 4 + c (i = 0) to south polar cap face 8 + c_prev (i = nside - 1).
            add_quad(
                vid(4 + c, 0, k),
                vid(8 + c_prev, nside - 1, k),
                vid(8 + c_prev, nside - 1, k + 1),
                vid(4 + c, 0, k + 1),
            )
            # Equatorial face 4 + c (j = 0) to south polar cap face 8 + c (j = nside - 1).
            add_quad(
                vid(4 + c, k, 0),
                vid(8 + c, k, nside - 1),
                vid(8 + c, k + 1, nside - 1),
                vid(4 + c, k + 1, 0),
            )
            # South polar cap face 8 + c (j = 0) to face 8 + c_next (i = 0).
            add_quad(
                vid(8 + c, k, 0),
                vid(8 + c_next, 0, k),
                vid(8 + c_next, 0, k + 1),
                vid(8 + c, k + 1, 0),
            )

    # 3. 14 rhombic-dodecahedron corners of the HEALPix base tessellation:
    #    North Pole (4-valent), South Pole (4-valent), 4 equatorial 4-valent corners,
    #    and 8 mid-latitude 3-valent corners.
    add_quad(
        vid(0, nside - 1, nside - 1),
        vid(1, nside - 1, nside - 1),
        vid(2, nside - 1, nside - 1),
        vid(3, nside - 1, nside - 1),
    )
    add_quad(
        vid(8, 0, 0),
        vid(9, 0, 0),
        vid(10, 0, 0),
        vid(11, 0, 0),
    )
    for c in range(4):
        c_next = (c + 1) % 4
        c_prev = (c + 3) % 4
        add_quad(
            vid(c, 0, 0),
            vid(4 + c, nside - 1, 0),
            vid(8 + c, nside - 1, nside - 1),
            vid(4 + c_next, 0, nside - 1),
        )
        tris.append((vid(c, nside - 1, 0), vid(c_next, 0, nside - 1), vid(4 + c_next, nside - 1, nside - 1)))
        tris.append((vid(4 + c, 0, 0), vid(8 + c_prev, nside - 1, 0), vid(8 + c, 0, nside - 1)))

    arr = np.asarray(tris, dtype=np.int64)
    v0 = positions[arr[:, 0]].astype(np.float64)
    v1 = positions[arr[:, 1]].astype(np.float64)
    v2 = positions[arr[:, 2]].astype(np.float64)
    cross = np.cross(v1 - v0, v2 - v0)
    center = v0 + v1 + v2
    inward = np.einsum("ij,ij->i", cross, center) < 0.0
    if np.any(inward):
        arr[inward, 1], arr[inward, 2] = arr[inward, 2].copy(), arr[inward, 1].copy()

    return arr.astype(np.uint16)


def compute_vertex_normals(positions: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    """Compute area-weighted outward unit vertex normals for `(positions, triangles)`."""
    pos64 = positions.astype(np.float64)
    idx = triangles.astype(np.int64)
    v0 = pos64[idx[:, 0]]
    v1 = pos64[idx[:, 1]]
    v2 = pos64[idx[:, 2]]
    face_normals = np.cross(v1 - v0, v2 - v0)

    accum = np.zeros_like(pos64)
    np.add.at(accum, idx[:, 0], face_normals)
    np.add.at(accum, idx[:, 1], face_normals)
    np.add.at(accum, idx[:, 2], face_normals)

    lengths = np.linalg.norm(accum, axis=1, keepdims=True)
    radial = pos64 / np.maximum(np.linalg.norm(pos64, axis=1, keepdims=True), 1e-12)
    normals = np.where(lengths > 1e-12, accum / np.maximum(lengths, 1e-12), radial)
    return normals.astype(np.float32)


# ---------------------------------------------------------------------------
# Deterministic glTF 2.0 binary (`.glb`) serializer
# ---------------------------------------------------------------------------


def dumps_glb(positions: np.ndarray, normals: np.ndarray, triangles: np.ndarray) -> bytes:
    """Serialize `(positions, normals, triangles)` into a self-contained glTF 2.0 binary `.glb`."""
    pos_f32 = np.ascontiguousarray(positions, dtype="<f4")
    norm_f32 = np.ascontiguousarray(normals, dtype="<f4")
    idx_u16 = np.ascontiguousarray(triangles.ravel(), dtype="<u2")

    pos_bytes = pos_f32.tobytes()
    norm_bytes = norm_f32.tobytes()
    idx_bytes = idx_u16.tobytes()

    pos_offset = 0
    pos_len = len(pos_bytes)
    norm_offset = pos_len
    norm_len = len(norm_bytes)
    idx_offset = norm_offset + norm_len
    idx_len = len(idx_bytes)

    bin_raw = pos_bytes + norm_bytes + idx_bytes
    bin_pad = (-len(bin_raw)) % 4
    if bin_pad:
        bin_raw += b"\x00" * bin_pad

    pos_min = [float(x) for x in np.min(pos_f32, axis=0)]
    pos_max = [float(x) for x in np.max(pos_f32, axis=0)]
    norm_min = [float(x) for x in np.min(norm_f32, axis=0)]
    norm_max = [float(x) for x in np.max(norm_f32, axis=0)]

    gltf_dict: dict[str, Any] = {
        "asset": {
            "version": "2.0",
            "generator": "Albedian scripts/bake_local_bubble_mesh.py",
            "copyright": "O'Neill et al. (2024, ApJ 973, 136); Harvard Dataverse doi:10.7910/DVN/INB1RB (CC0-1.0)",
        },
        "scene": 0,
        "scenes": [{"name": "LocalBubbleScene", "nodes": [0]}],
        "nodes": [{"name": "LocalBubble_ONeill2024", "mesh": 0}],
        "meshes": [
            {
                "name": "LocalBubble_ONeill2024_Shell_A0.5",
                "primitives": [
                    {
                        "attributes": {"POSITION": 0, "NORMAL": 1},
                        "indices": 2,
                        "mode": 4,
                    }
                ],
            }
        ],
        "accessors": [
            {
                "bufferView": 0,
                "byteOffset": 0,
                "componentType": 5126,  # FLOAT
                "count": int(pos_f32.shape[0]),
                "type": "VEC3",
                "min": pos_min,
                "max": pos_max,
            },
            {
                "bufferView": 1,
                "byteOffset": 0,
                "componentType": 5126,  # FLOAT
                "count": int(norm_f32.shape[0]),
                "type": "VEC3",
                "min": norm_min,
                "max": norm_max,
            },
            {
                "bufferView": 2,
                "byteOffset": 0,
                "componentType": 5123,  # UNSIGNED_SHORT
                "count": int(idx_u16.size),
                "type": "SCALAR",
                "min": [int(idx_u16.min())],
                "max": [int(idx_u16.max())],
            },
        ],
        "bufferViews": [
            {
                "buffer": 0,
                "byteOffset": pos_offset,
                "byteLength": pos_len,
                "target": 34962,  # ARRAY_BUFFER
            },
            {
                "buffer": 0,
                "byteOffset": norm_offset,
                "byteLength": norm_len,
                "target": 34962,  # ARRAY_BUFFER
            },
            {
                "buffer": 0,
                "byteOffset": idx_offset,
                "byteLength": idx_len,
                "target": 34963,  # ELEMENT_ARRAY_BUFFER
            },
        ],
        "buffers": [{"byteLength": len(bin_raw)}],
    }

    json_bytes = json.dumps(gltf_dict, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    json_pad = (-len(json_bytes)) % 4
    if json_pad:
        json_bytes += b" " * json_pad

    total_length = 12 + 8 + len(json_bytes) + 8 + len(bin_raw)
    header = struct.pack("<4sII", b"glTF", 2, total_length)
    json_chunk = struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
    bin_chunk = struct.pack("<II", len(bin_raw), 0x004E4942) + bin_raw
    return header + json_chunk + bin_chunk


# ---------------------------------------------------------------------------
# Provenance & bake entry points
# ---------------------------------------------------------------------------


def build_provenance_record(
    glb_sha256: str,
    existing_record: AssetProvenanceRecord | None = None,
) -> AssetProvenanceRecord:
    """Build or update `apps/web/public/models/PROVENANCE.json` for `local_bubble_oneill2024.glb`."""
    oneill_source = ProvenanceSource(
        id=SOURCE_ID,
        title="The Local Bubble Is a Magnetized, Multi-temperature Superbubble: 3D Dust Shell Model (A_G = 0.5 mag/kpc threshold)",
        url="https://doi.org/10.7910/DVN/INB1RB",
        version="V1 (2024-05-06)",
        retrieved="2026-11-04",
        license=ProvenanceLicense(
            spdx="CC0-1.0",
            name="Creative Commons Zero v1.0 Universal",
            url="https://creativecommons.org/publicdomain/zero/1.0/",
        ),
        credit="O'Neill, T. J., Zucker, C., Goodman, A. A., & Edenhofer, G. (2024, ApJ 973, 136); Harvard Dataverse doi:10.7910/DVN/INB1RB",
        citation="O'Neill, T. J., Zucker, C., Goodman, A. A., & Edenhofer, G. 2024, ApJ, 973, 136 (doi:10.3847/1538-4357/ad47b5)",
        doi="10.7910/DVN/INB1RB",
        sha256=INPUT.sha256,
        note=(
            "Derived from ONeill2024_LocalBubble_ShellProperties_A0.5.fits (786,432 HEALPix nside=256 "
            "directions derived from the Edenhofer et al. 2024 3D dust map). Aggregated onto a closed "
            "nside=48 HEALPix 2-manifold (27,648 vertices, 55,292 outward triangles) in heliocentric "
            "Galactic Cartesian coordinates scaled to 1 unit = 100 pc."
        ),
    )

    glb_file = ProvenanceFile(
        path=GLB_FILENAME,
        sha256=glb_sha256,
        sources=(SOURCE_ID,),
        produced_by=MadeByScript(
            method="script",
            script=PRODUCING_SCRIPT,
            command=PRODUCING_COMMAND,
        ),
    )

    other_sources = [s for s in (existing_record.sources if existing_record else ()) if s.id != SOURCE_ID]
    other_files = [f for f in (existing_record.files if existing_record else ()) if f.path != GLB_FILENAME]
    file_sets = tuple(existing_record.file_sets) if existing_record else ()

    return AssetProvenanceRecord(
        format=ASSET_PROVENANCE_FORMAT,
        format_version=ASSET_PROVENANCE_FORMAT_VERSION,
        sources=tuple([*other_sources, oneill_source]),
        files=tuple(sorted([*other_files, glb_file], key=lambda f: f.path)),
        file_sets=file_sets,
    )


def verify_input(in_dir: Path) -> Path:
    """Verify the cached Harvard Dataverse FITS file matches `INPUT.size` and `INPUT.sha256`."""
    fits_path = in_dir / INPUT.filename
    if not fits_path.is_file():
        raise FileNotFoundError(
            f"missing cached input {fits_path} (run `uv run python scripts/fetch_download.py {fits_path.relative_to(ROOT)}`)"
        )
    actual_size = fits_path.stat().st_size
    if actual_size != INPUT.size:
        raise ValueError(f"{fits_path}: expected {INPUT.size} bytes, got {actual_size}")
    actual_sha = sha256_file(fits_path)
    if actual_sha != INPUT.sha256:
        raise ValueError(f"{fits_path}: expected sha256 {INPUT.sha256}, got {actual_sha}")
    return fits_path


def _bake_to_bytes(
    in_dir: Path,
    *,
    nside_in: int = DEFAULT_NSIDE_IN,
    nside_out: int = DEFAULT_NSIDE_OUT,
    verify_input_hashes: bool = True,
) -> tuple[bytes, dict[str, Any]]:
    fits_path = verify_input(in_dir) if verify_input_hashes else (in_dir / INPUT.filename)
    cols = read_shell_fits_table(fits_path)
    positions, nan_count = aggregate_shell_distances(
        cols["l"],
        cols["b"],
        cols["d"],
        nside_in=nside_in,
        nside_out=nside_out,
    )
    triangles = build_healpix_triangles(nside_out, positions)
    normals = compute_vertex_normals(positions, triangles)
    glb_bytes = dumps_glb(positions, normals, triangles)

    radii_pc = np.linalg.norm(positions.astype(np.float64), axis=1) * PC_PER_UNIT
    summary = {
        "nsideIn": nside_in,
        "nsideOut": nside_out,
        "vertices": int(positions.shape[0]),
        "triangles": int(triangles.shape[0]),
        "nanInputPixels": nan_count,
        "minDistancePc": round(float(radii_pc.min()), 3),
        "medianDistancePc": round(float(np.median(radii_pc)), 3),
        "maxDistancePc": round(float(radii_pc.max()), 3),
        "glbBytes": len(glb_bytes),
    }
    return glb_bytes, summary


def bake_local_bubble_mesh(
    in_dir: Path = DEFAULT_IN_DIR,
    out_dir: Path = DEFAULT_OUT_DIR,
    *,
    nside_in: int = DEFAULT_NSIDE_IN,
    nside_out: int = DEFAULT_NSIDE_OUT,
    verify_input_hashes: bool = True,
) -> dict[str, Any]:
    """Bake `local_bubble_oneill2024.glb` and `PROVENANCE.json` into `out_dir`."""
    glb_bytes, summary = _bake_to_bytes(
        in_dir,
        nside_in=nside_in,
        nside_out=nside_out,
        verify_input_hashes=verify_input_hashes,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    glb_path = out_dir / GLB_FILENAME
    glb_path.write_bytes(glb_bytes)

    prov_path = out_dir / "PROVENANCE.json"
    existing: AssetProvenanceRecord | None = None
    if prov_path.is_file():
        existing = AssetProvenanceRecord.model_validate(json.loads(prov_path.read_text("utf-8")))

    glb_sha = sha256_file(glb_path)
    record = build_provenance_record(glb_sha, existing_record=existing)
    prov_path.write_text(dumps_record(record), encoding="utf-8")

    summary["glbSha256"] = glb_sha
    return summary


def verify_outputs(
    in_dir: Path = DEFAULT_IN_DIR,
    out_dir: Path = DEFAULT_OUT_DIR,
    *,
    nside_in: int = DEFAULT_NSIDE_IN,
    nside_out: int = DEFAULT_NSIDE_OUT,
    verify_input_hashes: bool = True,
) -> list[str]:
    """Re-bake in memory and verify that on-disk `.glb` and `PROVENANCE.json` match byte-for-byte."""
    errors: list[str] = []
    try:
        expected_glb, _ = _bake_to_bytes(
            in_dir,
            nside_in=nside_in,
            nside_out=nside_out,
            verify_input_hashes=verify_input_hashes,
        )
    except Exception as exc:
        return [f"input bake failed: {exc}"]

    glb_path = out_dir / GLB_FILENAME
    if not glb_path.is_file():
        errors.append(f"missing {glb_path}")
    elif glb_path.read_bytes() != expected_glb:
        errors.append(f"{glb_path}: on-disk bytes differ from fresh bake")

    prov_path = out_dir / "PROVENANCE.json"
    if not prov_path.is_file():
        errors.append(f"missing {prov_path}")
    else:
        try:
            existing = AssetProvenanceRecord.model_validate(json.loads(prov_path.read_text("utf-8")))
            expected_record = build_provenance_record(
                sha256_file(glb_path) if glb_path.is_file() else "0" * 64,
                existing_record=existing,
            )
            if prov_path.read_text("utf-8") != dumps_record(expected_record):
                errors.append(f"{prov_path}: on-disk record differs from expected record")
        except Exception as exc:
            errors.append(f"{prov_path}: invalid provenance record ({exc})")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in-dir", type=Path, default=DEFAULT_IN_DIR, help="Directory holding the O'Neill 2024 FITS file")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Output directory for the baked GLB and PROVENANCE.json")
    parser.add_argument("--nside", type=int, default=DEFAULT_NSIDE_OUT, help="Coarse HEALPix nside for the output mesh (default: 48)")
    parser.add_argument("--verify", action="store_true", help="Verify on-disk outputs against a fresh in-memory bake without writing")
    args = parser.parse_args(argv)

    if args.verify:
        errors = verify_outputs(args.in_dir, args.out_dir, nside_out=args.nside)
        if errors:
            for err in errors:
                print(f"FAIL: {err}", file=sys.stderr)
            return 1
        print(f"OK: {args.out_dir / GLB_FILENAME} and PROVENANCE.json match fresh bake")
        return 0

    summary = bake_local_bubble_mesh(args.in_dir, args.out_dir, nside_out=args.nside)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
