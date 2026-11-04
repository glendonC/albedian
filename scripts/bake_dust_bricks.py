#!/usr/bin/env python3
"""Pilot: resample 3D dust maps onto sparse Cartesian bricks for a ray-marched volume.

Reads the Edenhofer et al. (2024) dust map, extinction density on HEALPix sky pixels (nside 256,
nested, Galactic) times 516 log-spaced distance shells from 68.6 to 1248 pc, and resamples it onto
heliocentric Galactic Cartesian grids (x toward l = 0, y toward l = 90 deg, z toward b = 90 deg).
Voxels are grouped into cubic bricks; a brick is kept when its peak density clears a threshold.
The pilot measures wall time, peak memory, bricks and bytes per voxel size and threshold, and
checks the result against the map itself: rays from the Sun stay in one HEALPix pixel, so the
map's own column along a ray is exact, and the bricks must reproduce it. It also bricks the
O'Neill et al. (2024) Local Bubble shell cube, which is already Cartesian at 2 pc.

Everything here is a measurement pilot. Output stays in `.cache/dust/bricks-pilot/`; nothing is
written under `apps/web/public/`.

Units: both maps give extinction density in ZGR23 units per parsec (Zhang, Green and Rix 2023).
A_V at 540 nm is 2.8 times the ZGR23 extinction (Edenhofer et al. 2024, section 7); other bands
need the ZGR23 extinction curve. Inside 68.6 pc the Edenhofer map has no 3D density (it gives an
integrated column per direction instead), so the bricks leave that sphere empty.

Usage:
    uv run python scripts/bake_dust_bricks.py                    # 8 and 4 pc, report only
    uv run python scripts/bake_dust_bricks.py --voxel 8 4 2      # add 2 pc
    uv run python scripts/bake_dust_bricks.py --write            # also write bricks to the cache
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import resource
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.science import catalog_tiles as ct  # noqa: E402

DUST_CACHE = ROOT_DIR / ".cache" / "dust"
EDENHOFER_PATH = DUST_CACHE / "edenhofer2024" / "mean_and_std_healpix.fits"
ONEILL_SHELL_PATH = DUST_CACHE / "oneill2024" / "ONeill2024_LocalBubble_Shell_xyz.fits"
DEFAULT_OUT = DUST_CACHE / "bricks-pilot"
A_V_PER_ZGR23 = 2.8  # Edenhofer et al. (2024), section 7: A_V at 540 nm

FITS_BLOCK = 2880
_FITS_DTYPES = {8: ">u1", 16: ">i2", 32: ">i4", 64: ">i8", -32: ">f4", -64: ">f8"}


# =============================================================================================
# FITS (headers and big-endian data, read directly; no astropy)
# =============================================================================================


@dataclass(frozen=True)
class FitsHdu:
    header: dict[str, str]
    data_offset: int
    # numpy order, slowest axis first: (NAXISn, ..., NAXIS1)
    shape: tuple[int, ...]
    bitpix: int


def _card_value(card: str) -> str:
    raw = card[10:].lstrip()
    if raw.startswith("'"):
        text, i = [], 1
        while i < len(raw):
            if raw[i] == "'":
                if raw[i + 1 : i + 2] == "'":
                    text.append("'")
                    i += 2
                    continue
                break
            text.append(raw[i])
            i += 1
        return "".join(text).rstrip()
    return raw.split("/", 1)[0].strip()


def read_fits_hdus(path: Path) -> list[FitsHdu]:
    """Every HDU of a FITS file: its header keywords, where its data starts, its shape."""
    hdus: list[FitsHdu] = []
    size = Path(path).stat().st_size
    with open(path, "rb") as fh:
        offset = 0
        while offset < size:
            fh.seek(offset)
            cards: list[str] = []
            while True:
                block = fh.read(FITS_BLOCK)
                if len(block) < FITS_BLOCK:
                    raise ValueError(f"{path}: header runs past the end of the file")
                ended = False
                for i in range(0, FITS_BLOCK, 80):
                    card = block[i : i + 80].decode("ascii")
                    if card[:8].rstrip() == "END":
                        ended = True
                        break
                    cards.append(card)
                if ended:
                    break
            header_bytes = math.ceil((len(cards) + 1) * 80 / FITS_BLOCK) * FITS_BLOCK
            header = {card[:8].rstrip(): _card_value(card) for card in cards if card[8:10] == "= "}
            bitpix = int(header["BITPIX"])
            naxis = int(header["NAXIS"])
            axes = [int(header[f"NAXIS{k}"]) for k in range(1, naxis + 1)]
            count = int(np.prod(axes)) if naxis else 0
            data_bytes = abs(bitpix) // 8 * int(header.get("GCOUNT", "1")) * (int(header.get("PCOUNT", "0")) + count)
            hdus.append(FitsHdu(header, offset + header_bytes, tuple(reversed(axes)), bitpix))
            offset += header_bytes + math.ceil(data_bytes / FITS_BLOCK) * FITS_BLOCK
    return hdus


def _named(hdus: list[FitsHdu], name: str) -> FitsHdu:
    for hdu in hdus:
        if hdu.header.get("EXTNAME") == name:
            return hdu
    raise ValueError(f"no HDU named {name!r}")


def fits_image(path: Path, hdu: FitsHdu) -> np.ndarray:
    return np.memmap(path, dtype=_FITS_DTYPES[hdu.bitpix], mode="r", offset=hdu.data_offset, shape=hdu.shape)


def fits_float_column(path: Path, hdu: FitsHdu) -> np.ndarray:
    """The single 32-bit float column of a one-column binary table."""
    h = hdu.header
    if h.get("XTENSION") != "BINTABLE" or h.get("TFIELDS") != "1" or h.get("TFORM1") != "E" or h.get("NAXIS1") != "4":
        raise ValueError(f"expected a one-column float table, got {h}")
    return np.fromfile(path, dtype=">f4", count=int(h["NAXIS2"]), offset=hdu.data_offset).astype(np.float64)


# =============================================================================================
# The HEALPix times distance map
# =============================================================================================


@dataclass(frozen=True)
class HealpixDustMap:
    mean: np.ndarray  # (shells, pixels), ZGR23 extinction per pc
    std: np.ndarray
    boundaries_pc: np.ndarray  # shells + 1 shell edges
    order: int  # HEALPix order; nside = 2**order


def load_healpix_dust_map(path: Path) -> HealpixDustMap:
    hdus = read_fits_hdus(path)
    mean_h, std_h = _named(hdus, "MEAN"), _named(hdus, "STD.")
    if mean_h.header.get("ORDERING") != "NEST":
        raise ValueError("expected nested HEALPix ordering")
    nside = int(mean_h.header["NSIDE"])
    order = nside.bit_length() - 1
    if 2**order != nside:
        raise ValueError(f"nside {nside} is not a power of two")
    boundaries = fits_float_column(path, _named(hdus, "RADIAL PIXEL BOUNDARIES"))
    mean, std = fits_image(path, mean_h), fits_image(path, std_h)
    if mean.shape != (len(boundaries) - 1, 12 * nside * nside) or std.shape != mean.shape:
        raise ValueError(f"map shape {mean.shape} does not match {len(boundaries) - 1} shells at nside {nside}")
    return HealpixDustMap(mean=mean, std=std, boundaries_pc=boundaries, order=order)


def sample_map(dust: HealpixDustMap, x: np.ndarray, y: np.ndarray, z: np.ndarray, *, field: str = "mean") -> np.ndarray:
    """Density at Galactic heliocentric points (pc): the shell and pixel holding each point.

    NaN where the map has no 3D density: inside its innermost shell edge and beyond its outermost.
    """
    x, y, z = (np.asarray(a, dtype=np.float64) for a in (x, y, z))
    d = np.sqrt(x * x + y * y + z * z)
    edges = dust.boundaries_pc
    out = np.full(d.shape, np.nan)
    inside = (d >= edges[0]) & (d < edges[-1])
    if np.any(inside):
        lon = np.degrees(np.arctan2(y[inside], x[inside])) % 360.0
        lat = np.degrees(np.arcsin(np.clip(z[inside] / d[inside], -1.0, 1.0)))
        pix = ct.radec_to_nest(dust.order, lon, lat)
        shell = np.searchsorted(edges, d[inside], side="right") - 1
        out[inside] = (dust.mean if field == "mean" else dust.std)[shell, pix]
    return out


def map_columns(dust: HealpixDustMap, lon_deg, lat_deg, *, outer_pc: float | None = None) -> np.ndarray:
    """The map's own columns from its inner edge outward along directions (ZGR23 units).

    A ray from the Sun stays in one HEALPix pixel, so each column is exact: every shell's density
    times the length of the ray inside it.
    """
    pix = ct.radec_to_nest(dust.order, np.atleast_1d(lon_deg), np.atleast_1d(lat_deg))
    lo, hi = dust.boundaries_pc[:-1], dust.boundaries_pc[1:]
    if outer_pc is not None:
        hi = np.minimum(hi, outer_pc)
    lengths = np.clip(hi - lo, 0.0, None)
    unique, inverse = np.unique(pix, return_inverse=True)
    return (lengths @ np.asarray(dust.mean[:, unique], dtype=np.float64))[inverse.ravel()]


def map_column(dust: HealpixDustMap, lon_deg: float, lat_deg: float, *, outer_pc: float | None = None) -> float:
    return float(map_columns(dust, [lon_deg], [lat_deg], outer_pc=outer_pc)[0])


# =============================================================================================
# Bricks
# =============================================================================================


Sampler = Callable[[np.ndarray, np.ndarray, np.ndarray], np.ndarray]


@dataclass
class BrickBuild:
    """Cubic bricks of a voxel grid centred on the Sun, every brick the map reaches."""

    voxel_pc: float
    brick: int
    n_voxels: int  # per axis, a whole number of bricks
    # float16 density per kept brick, indexed [i, j, k] along x, y, z; key (bx, by, bz)
    bricks: dict[tuple[int, int, int], np.ndarray] = field(default_factory=dict)
    peaks: dict[tuple[int, int, int], float] = field(default_factory=dict)
    seconds: float = 0.0

    @property
    def half_extent_pc(self) -> float:
        return self.n_voxels * self.voxel_pc / 2.0

    def axis(self) -> np.ndarray:
        return (np.arange(self.n_voxels) + 0.5) * self.voxel_pc - self.half_extent_pc

    def voxel_centers(self, key: tuple[int, int, int]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        a, b = self.axis(), self.brick
        bx, by, bz = key
        return np.meshgrid(a[bx * b : (bx + 1) * b], a[by * b : (by + 1) * b], a[bz * b : (bz + 1) * b], indexing="ij")

    def kept(self, threshold: float) -> list[tuple[int, int, int]]:
        return [key for key, peak in self.peaks.items() if peak > threshold]


def build_bricks(sample: Sampler, *, voxel_pc: float, outer_radius_pc: float, brick: int = 16) -> BrickBuild:
    """Sample every voxel centre inside the bricks that can reach the map; keep the touched bricks.

    A brick is touched when any voxel centre in it has data (the sampler returns NaN elsewhere).
    Its values are stored as float16 with NaN as 0, and its peak is kept so a threshold can be
    applied afterwards without resampling.
    """
    started = time.time()
    n_bricks = math.ceil(2.0 * outer_radius_pc / (voxel_pc * brick))
    build = BrickBuild(voxel_pc=voxel_pc, brick=brick, n_voxels=n_bricks * brick)
    a = build.axis()
    reach = outer_radius_pc + math.sqrt(3) * voxel_pc * brick  # a brick farther than this cannot touch
    for bz in range(n_bricks):
        z = a[bz * brick : (bz + 1) * brick]
        for by in range(n_bricks):
            y = a[by * brick : (by + 1) * brick]
            if math.hypot(float(np.min(np.abs(y))), float(np.min(np.abs(z)))) > reach:
                continue
            X, Y, Z = np.meshgrid(a, y, z, indexing="ij")
            values = sample(X, Y, Z)
            for bx in range(n_bricks):
                block = values[bx * brick : (bx + 1) * brick]
                finite = np.isfinite(block)
                if not np.any(finite):
                    continue
                key = (bx, by, bz)
                build.bricks[key] = np.where(finite, block, 0.0).astype(np.float16)
                build.peaks[key] = float(np.max(block[finite]))
    build.seconds = time.time() - started
    return build


def brick_column(
    build: BrickBuild,
    lon_deg: float,
    lat_deg: float,
    *,
    inner_pc: float,
    outer_pc: float,
    threshold: float = -math.inf,
    step_pc: float | None = None,
) -> float:
    """Column along one direction through the bricks, nearest voxel per sample (ZGR23 units).

    Bricks whose peak is at or below `threshold` count as empty.
    """
    step = step_pc or build.voxel_pc / 4.0
    t = np.arange(inner_pc + step / 2.0, outer_pc, step)
    lon, lat = math.radians(lon_deg), math.radians(lat_deg)
    direction = np.array([math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)])
    idx = np.floor((t[:, None] * direction[None, :] + build.half_extent_pc) / build.voxel_pc).astype(np.int64)
    inside = np.all((idx >= 0) & (idx < build.n_voxels), axis=1)
    idx = idx[inside]
    keys, local = idx // build.brick, idx % build.brick
    total = 0.0
    unique, inverse = np.unique(keys, axis=0, return_inverse=True)
    for u, key in enumerate(map(tuple, unique)):
        data = build.bricks.get(key)
        if data is None or build.peaks[key] <= threshold:
            continue
        rows = local[inverse.ravel() == u]
        total += float(np.sum(data[rows[:, 0], rows[:, 1], rows[:, 2]].astype(np.float64)))
    return total * step


@dataclass
class TwoLevel:
    """A coarse base (the mean of each factor-cubed block of fine voxels) on every touched brick,
    plus the fine residual of the bricks where it matters.

    The base keeps the diffuse dust a peak threshold would drop: its mean over each block equals the
    fine mean, so a ray loses only the variation inside blocks of the bricks whose residual is cut.
    """

    factor: int
    base: dict[tuple[int, int, int], np.ndarray]  # float64 (brick / factor,)*3 per touched brick
    residual_peaks: dict[tuple[int, int, int], float]


def two_level(build: BrickBuild, factor: int) -> TwoLevel:
    if build.brick % factor:
        raise ValueError("factor must divide the brick size")
    m = build.brick // factor
    base, peaks = {}, {}
    for key, data in build.bricks.items():
        fine = data.astype(np.float64)
        coarse = fine.reshape(m, factor, m, factor, m, factor).mean(axis=(1, 3, 5))
        up = np.repeat(np.repeat(np.repeat(coarse, factor, axis=0), factor, axis=1), factor, axis=2)
        base[key] = coarse
        peaks[key] = float(np.max(np.abs(fine - up)))
    return TwoLevel(factor=factor, base=base, residual_peaks=peaks)


def two_level_column(
    build: BrickBuild,
    levels: TwoLevel,
    lon_deg: float,
    lat_deg: float,
    *,
    inner_pc: float,
    outer_pc: float,
    residual_threshold: float,
    step_pc: float | None = None,
) -> float:
    """Column through base plus kept residuals: fine values where a brick's residual peak exceeds
    the threshold, the coarse base elsewhere (ZGR23 units)."""
    step = step_pc or build.voxel_pc / 4.0
    t = np.arange(inner_pc + step / 2.0, outer_pc, step)
    lon, lat = math.radians(lon_deg), math.radians(lat_deg)
    direction = np.array([math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)])
    idx = np.floor((t[:, None] * direction[None, :] + build.half_extent_pc) / build.voxel_pc).astype(np.int64)
    idx = idx[np.all((idx >= 0) & (idx < build.n_voxels), axis=1)]
    keys, local = idx // build.brick, idx % build.brick
    total = 0.0
    unique, inverse = np.unique(keys, axis=0, return_inverse=True)
    for u, key in enumerate(map(tuple, unique)):
        data = build.bricks.get(key)
        if data is None:
            continue
        rows = local[inverse.ravel() == u]
        if levels.residual_peaks[key] > residual_threshold:
            total += float(np.sum(data[rows[:, 0], rows[:, 1], rows[:, 2]].astype(np.float64)))
        else:
            c = rows // levels.factor
            total += float(np.sum(levels.base[key][c[:, 0], c[:, 1], c[:, 2]]))
    return total * step


def write_bricks(build: BrickBuild, out_dir: Path, *, threshold: float, meta: dict) -> dict:
    """Kept bricks as one float16 file in key order, plus an index; returns the index."""
    out_dir.mkdir(parents=True, exist_ok=True)
    keys = sorted(build.kept(threshold), key=lambda k: (k[2], k[1], k[0]))
    blob = b"".join(build.bricks[k].astype("<f2").tobytes(order="F") for k in keys)
    (out_dir / "bricks.f16").write_bytes(blob)
    index = {
        "format": "albedian.dust-bricks-pilot",
        "voxelPc": build.voxel_pc,
        "brick": build.brick,
        "nVoxels": build.n_voxels,
        "halfExtentPc": build.half_extent_pc,
        "threshold": threshold,
        "frame": "heliocentric Galactic Cartesian: x toward l = 0, y toward l = 90 deg, z toward b = 90 deg",
        "layout": "per brick, float16 little-endian, x fastest then y then z; bricks in (bz, by, bx) order",
        "units": "ZGR23 extinction per pc; A_V at 540 nm = 2.8 times",
        "bricks": [list(k) for k in keys],
        "bricksSha256": hashlib.sha256(blob).hexdigest(),
        **meta,
    }
    (out_dir / "index.json").write_text(json.dumps(index) + "\n", encoding="utf-8")
    return index


# =============================================================================================
# Pilot runner
# =============================================================================================


def peak_rss_gb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / 1e9 if sys.platform == "darwin" else rss * 1024 / 1e9  # bytes on macOS, KiB on Linux


def random_directions(n: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    return rng.uniform(0.0, 360.0, n), np.degrees(np.arcsin(rng.uniform(-1.0, 1.0, n)))


def pilot_map(dust: HealpixDustMap, voxels: list[float], brick: int, thresholds: list[float], rays: int, out: Path | None) -> list[dict]:
    inner, outer = float(dust.boundaries_pc[0]), float(dust.boundaries_pc[-1])
    lon, lat = random_directions(rays, seed=0)
    truth = map_columns(dust, lon, lat)
    results = []
    for voxel in voxels:
        build = build_bricks(lambda x, y, z: sample_map(dust, x, y, z), voxel_pc=voxel, outer_radius_pc=outer, brick=brick)
        rss = peak_rss_gb()
        print(f"[dust] {voxel:g} pc: {len(build.bricks):,} bricks touch the map, built in {build.seconds:.0f} s, peak RSS {rss:.1f} GB", flush=True)
        full = np.array([brick_column(build, a, b, inner_pc=inner, outer_pc=outer) for a, b in zip(lon, lat, strict=True)])
        rows = []
        for threshold in [-math.inf, *thresholds]:
            kept = build.kept(threshold)
            cols = full if threshold == -math.inf else np.array(
                [brick_column(build, a, b, inner_pc=inner, outer_pc=outer, threshold=threshold) for a, b in zip(lon, lat, strict=True)]
            )
            rel = np.abs(cols - truth) / truth
            dropped_av = (full - cols) * A_V_PER_ZGR23
            row = {
                "threshold": None if threshold == -math.inf else threshold,
                "bricks": len(kept),
                "megabytes": len(kept) * brick**3 * 2 / 1e6,
                "columnRelErrorMedian": float(np.median(rel)),
                "columnRelErrorP95": float(np.percentile(rel, 95)),
                "columnRelErrorMax": float(rel.max()),
                "droppedAvMedianMag": float(np.median(dropped_av)),
                "droppedAvMaxMag": float(dropped_av.max()),
            }
            rows.append(row)
            print(
                f"[dust]   threshold {row['threshold']}: {row['bricks']:,} bricks, {row['megabytes']:.0f} MB float16; column error "
                f"median {row['columnRelErrorMedian']:.3%}, p95 {row['columnRelErrorP95']:.3%}; A_V dropped median "
                f"{row['droppedAvMedianMag']:.4f}, max {row['droppedAvMaxMag']:.4f} mag",
                flush=True,
            )
            if out is not None and row["threshold"] is not None:
                write_bricks(build, out / f"edenhofer2024-{voxel:g}pc-t{threshold:g}", threshold=threshold, meta={"source": "Edenhofer et al. 2024"})
        two_level_rows = []
        factor = 4
        levels = two_level(build, factor)
        base_mb = len(levels.base) * (brick // factor) ** 3 * 2 / 1e6
        for threshold in [math.inf, *thresholds]:
            kept = sum(1 for p in levels.residual_peaks.values() if p > threshold)
            cols = np.array(
                [two_level_column(build, levels, a, b, inner_pc=inner, outer_pc=outer, residual_threshold=threshold) for a, b in zip(lon, lat, strict=True)]
            )
            rel = np.abs(cols - truth) / truth
            row = {
                "baseVoxelPc": voxel * factor,
                "residualThreshold": None if threshold == math.inf else threshold,
                "detailBricks": kept,
                "megabytes": base_mb + kept * brick**3 * 2 / 1e6,
                "columnRelErrorMedian": float(np.median(rel)),
                "columnRelErrorP95": float(np.percentile(rel, 95)),
                "columnRelErrorMax": float(rel.max()),
            }
            two_level_rows.append(row)
            print(
                f"[dust]   base {voxel * factor:g} pc + detail where residual > {row['residualThreshold']}: {kept:,} detail bricks, "
                f"{row['megabytes']:.0f} MB; column error median {row['columnRelErrorMedian']:.3%}, p95 {row['columnRelErrorP95']:.3%}",
                flush=True,
            )
        results.append(
            {
                "voxelPc": voxel,
                "brick": brick,
                "touched": len(build.bricks),
                "seconds": round(build.seconds, 1),
                "peakRssGb": round(rss, 2),
                "rows": rows,
                "twoLevel": two_level_rows,
            }
        )
        del build, levels
    return results


def pilot_shell(path: Path, brick: int, thresholds: list[float]) -> dict:
    """The O'Neill shell cube is already Cartesian at 2 pc: count the bricks each threshold keeps."""
    started = time.time()
    hdu = _named(read_fits_hdus(path), "SHELL")
    cube = fits_image(path, hdu)  # (z, y, x)
    n = cube.shape[0]
    nb = math.ceil(n / brick)
    peaks = []
    for bz in range(nb):
        slab = np.asarray(cube[bz * brick : (bz + 1) * brick], dtype=np.float32)
        for by in range(nb):
            for bx in range(nb):
                block = slab[:, by * brick : (by + 1) * brick, bx * brick : (bx + 1) * brick]
                finite = block[np.isfinite(block)]
                peaks.append(float(finite.max()) if finite.size else -math.inf)
    peaks = np.array(peaks)
    rows = [{"threshold": t, "bricks": int((peaks > t).sum()), "megabytes": int((peaks > t).sum()) * brick**3 * 2 / 1e6} for t in thresholds]
    result = {"cube": list(cube.shape), "voxelPc": float(hdu.header["CDELT1"]), "brick": brick, "bricksTotal": int(len(peaks)), "seconds": round(time.time() - started, 1), "rows": rows}
    print(f"[dust] O'Neill shell {cube.shape} at {result['voxelPc']:g} pc: {len(peaks):,} bricks of {brick}^3; " + ", ".join(f"> {r['threshold']:g}: {r['bricks']:,} ({r['megabytes']:.0f} MB)" for r in rows), flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--voxel", type=float, nargs="+", default=[8.0, 4.0])
    parser.add_argument("--brick", type=int, default=16)
    parser.add_argument("--thresholds", type=float, nargs="+", default=[1e-4, 3e-4, 1e-3, 3e-3])
    parser.add_argument("--rays", type=int, default=1000)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--write", action="store_true", help="write the bricks of each threshold under --out")
    args = parser.parse_args()
    started = time.time()
    dust = load_healpix_dust_map(EDENHOFER_PATH)
    summary = {
        "command": "uv run python scripts/bake_dust_bricks.py " + " ".join(sys.argv[1:]),
        "edenhofer": pilot_map(dust, args.voxel, args.brick, args.thresholds, args.rays, args.out if args.write else None),
        "oneillShell": pilot_shell(ONEILL_SHELL_PATH, args.brick, args.thresholds),
        "seconds": round(time.time() - started, 1),
        "peakRssGb": round(peak_rss_gb(), 2),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"[dust] summary in {args.out / 'summary.json'} ({summary['seconds']:.0f} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
