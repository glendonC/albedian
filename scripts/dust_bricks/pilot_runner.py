"""Dust-brick measurement pilot runner, resource telemetry, and CLI orchestration."""

from __future__ import annotations

import argparse
import json
import math
import resource
import sys
import time
from pathlib import Path

import numpy as np

from scripts.dust_bricks.fits_io import fits_image, named_hdu, read_fits_hdus
from scripts.dust_bricks.healpix_map import HealpixDustMap, load_healpix_dust_map, map_columns, sample_map
from scripts.dust_bricks.sparse_bricks import (
    brick_column,
    build_bricks,
    two_level,
    two_level_column,
    write_bricks,
)

ROOT_DIR = Path(__file__).resolve().parents[2]
DUST_CACHE = ROOT_DIR / ".cache" / "dust"
EDENHOFER_PATH = DUST_CACHE / "edenhofer2024" / "mean_and_std_healpix.fits"
ONEILL_SHELL_PATH = DUST_CACHE / "oneill2024" / "ONeill2024_LocalBubble_Shell_xyz.fits"
DEFAULT_OUT = DUST_CACHE / "bricks-pilot"
A_V_PER_ZGR23 = 2.8  # Edenhofer et al. (2024), section 7: A_V at 540 nm


def _float16_megabytes(brick_count: int, side: int) -> float:
    return brick_count * side**3 * 2 / 1e6


def peak_rss_gb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / 1e9 if sys.platform == "darwin" else rss * 1024 / 1e9  # bytes on macOS, KiB on Linux


def random_directions(n: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    return rng.uniform(0.0, 360.0, n), np.degrees(np.arcsin(rng.uniform(-1.0, 1.0, n)))


def pilot_map(
    dust: HealpixDustMap,
    voxels: list[float],
    brick: int,
    thresholds: list[float],
    rays: int,
    out: Path | None,
) -> list[dict]:
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
                "megabytes": _float16_megabytes(len(kept), brick),
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
        base_mb = _float16_megabytes(len(levels.base), brick // factor)
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
                "megabytes": base_mb + _float16_megabytes(kept, brick),
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
    hdu = named_hdu(read_fits_hdus(path), "SHELL")
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
    rows = [{"threshold": t, "bricks": int((peaks > t).sum()), "megabytes": _float16_megabytes(int((peaks > t).sum()), brick)} for t in thresholds]
    result = {
        "cube": list(cube.shape),
        "voxelPc": float(hdu.header["CDELT1"]),
        "brick": brick,
        "bricksTotal": int(len(peaks)),
        "seconds": round(time.time() - started, 1),
        "rows": rows,
    }
    print(
        f"[dust] O'Neill shell {cube.shape} at {result['voxelPc']:g} pc: {len(peaks):,} bricks of {brick}^3; "
        + ", ".join(f"> {r['threshold']:g}: {r['bricks']:,} ({r['megabytes']:.0f} MB)" for r in rows),
        flush=True,
    )
    return result


def build_arg_parser(*, default_out: Path = DEFAULT_OUT, description: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--voxel", type=float, nargs="+", default=[8.0, 4.0])
    parser.add_argument("--brick", type=int, default=16)
    parser.add_argument("--thresholds", type=float, nargs="+", default=[1e-4, 3e-4, 1e-3, 3e-3])
    parser.add_argument("--rays", type=int, default=1000)
    parser.add_argument("--out", type=Path, default=default_out)
    parser.add_argument("--write", action="store_true", help="write the bricks of each threshold under --out")
    return parser


def run_pilot(
    argv: list[str] | None = None,
    *,
    edenhofer_path: Path = EDENHOFER_PATH,
    oneill_shell_path: Path = ONEILL_SHELL_PATH,
    default_out: Path = DEFAULT_OUT,
    description: str | None = None,
) -> int:
    parser = build_arg_parser(default_out=default_out, description=description)
    args = parser.parse_args(argv)
    raw_argv = sys.argv[1:] if argv is None else list(argv)
    started = time.time()
    dust = load_healpix_dust_map(edenhofer_path)
    summary = {
        "command": "uv run python scripts/bake_dust_bricks.py " + " ".join(raw_argv),
        "edenhofer": pilot_map(dust, args.voxel, args.brick, args.thresholds, args.rays, args.out if args.write else None),
        "oneillShell": pilot_shell(oneill_shell_path, args.brick, args.thresholds),
        "seconds": round(time.time() - started, 1),
        "peakRssGb": round(peak_rss_gb(), 2),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"[dust] summary in {args.out / 'summary.json'} ({summary['seconds']:.0f} s)")
    return 0


def main() -> int:
    return run_pilot(description=__doc__)


__all__ = [
    "ROOT_DIR",
    "DUST_CACHE",
    "EDENHOFER_PATH",
    "ONEILL_SHELL_PATH",
    "DEFAULT_OUT",
    "A_V_PER_ZGR23",
    "peak_rss_gb",
    "random_directions",
    "pilot_map",
    "pilot_shell",
    "build_arg_parser",
    "run_pilot",
    "main",
]
