"""Sparse heliocentric Galactic Cartesian brick packing, ray-marching, two-level hierarchy, and output."""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

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


def _ray_brick_samples(
    build: BrickBuild,
    lon_deg: float,
    lat_deg: float,
    *,
    inner_pc: float,
    outer_pc: float,
    step_pc: float | None = None,
) -> tuple[float, np.ndarray, np.ndarray, np.ndarray]:
    """Step a heliocentric Galactic ray through the voxel grid and group samples by brick key."""
    step = step_pc or build.voxel_pc / 4.0
    t = np.arange(inner_pc + step / 2.0, outer_pc, step)
    lon, lat = math.radians(lon_deg), math.radians(lat_deg)
    direction = np.array([math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)])
    idx = np.floor((t[:, None] * direction[None, :] + build.half_extent_pc) / build.voxel_pc).astype(np.int64)
    inside = np.all((idx >= 0) & (idx < build.n_voxels), axis=1)
    idx = idx[inside]
    keys, local = idx // build.brick, idx % build.brick
    unique, inverse = np.unique(keys, axis=0, return_inverse=True)
    return step, local, unique, inverse.ravel()


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
    step, local, unique, inverse = _ray_brick_samples(
        build, lon_deg, lat_deg, inner_pc=inner_pc, outer_pc=outer_pc, step_pc=step_pc
    )
    total = 0.0
    for u, key in enumerate(map(tuple, unique)):
        data = build.bricks.get(key)
        if data is None or build.peaks[key] <= threshold:
            continue
        rows = local[inverse == u]
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
    step, local, unique, inverse = _ray_brick_samples(
        build, lon_deg, lat_deg, inner_pc=inner_pc, outer_pc=outer_pc, step_pc=step_pc
    )
    total = 0.0
    for u, key in enumerate(map(tuple, unique)):
        data = build.bricks.get(key)
        if data is None:
            continue
        rows = local[inverse == u]
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


__all__ = [
    "Sampler",
    "BrickBuild",
    "build_bricks",
    "brick_column",
    "TwoLevel",
    "two_level",
    "two_level_column",
    "write_bricks",
]
