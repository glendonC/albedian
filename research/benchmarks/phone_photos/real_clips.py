"""Today's solver on the owner's real clips, run on this machine only.

Personal data. The clips in data/uploads are the owner's (faces, audio, places). This module decodes
them locally with ffmpeg into memory: it never writes a frame, crop or value next to them, never
writes one anywhere, and never sends anything to any service. Its public summary carries only, per
clip, a neutral id (A, B, C in content-hash order), the capture format (codec, bit depth, transfer,
orientation), the statuses and the detection counts. Times, places, file names, frame sizes and the
device stay local.

The solver is today's, exactly as the API runs it (see solvers.py), on the original pixels: the
decoded luma plane, as ffmpeg's gray conversion of the 8-bit coded frame gives it. A claim is called
plausible only if every star it matched stands above the horizon at the clip's own time and place.
That is a weak sanity check, not a verification: real frames have no ground truth yet.

    uv run python -m research.benchmarks.phone_photos.real_clips [--frames 8] [--stack 5] [--timeout 180]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from research.benchmarks.phone_photos import solvers
from research.benchmarks.phone_photos.astrometry import ObservationFrame

ROOT = Path(__file__).resolve().parents[3]
UPLOADS_DIR = ROOT / "data" / "uploads"
VIDEO_SUFFIXES = {".mov", ".mp4", ".m4v"}
TRANSFERS = {"arib-std-b67": "HLG (ARIB STD-B67)", "smpte2084": "PQ (SMPTE ST 2084)", "bt709": "BT.709", "iec61966-2-1": "sRGB"}
CODECS = {"hevc": "HEVC", "h264": "H.264", "av1": "AV1", "prores": "ProRes"}


@dataclass(frozen=True)
class Clip:
    label: str
    path: Path
    sha256: str
    codec: str
    pix_fmt: str
    transfer: str
    width: int
    height: int
    duration_s: float
    metadata: dict  # the file's own time and place, for the solver's call; never published

    def capture_format(self) -> dict:
        """The public description of the clip: nothing that could identify it or place it."""
        depth = 10 if "10" in self.pix_fmt else (12 if "12" in self.pix_fmt else 8)
        return {
            "codec": CODECS.get(self.codec, self.codec.upper()),
            "bitDepth": depth,
            "transfer": TRANSFERS.get(self.transfer, self.transfer or "unspecified"),
            "orientation": "portrait" if self.height > self.width else "landscape",
        }


def find_clip_paths(uploads_dir: Path = UPLOADS_DIR) -> list[Path]:
    if not uploads_dir.exists():
        return []
    return sorted(p for p in uploads_dir.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata(tags: dict) -> dict:
    """Time and place from the file's own tags; whatever is missing stays missing."""
    out: dict = {"utc": None, "latDeg": None, "lonDeg": None}
    created = tags.get("com.apple.quicktime.creationdate") or tags.get("creation_time")
    if created:
        out["utc"] = datetime.fromisoformat(created.replace("Z", "+00:00")).isoformat()
    match = re.match(r"^([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", tags.get("com.apple.quicktime.location.ISO6709", ""))
    if match:
        out["latDeg"], out["lonDeg"] = float(match.group(1)), float(match.group(2))
    return out


def find_clips(uploads_dir: Path = UPLOADS_DIR) -> list[Clip]:
    """The distinct clips (identical files counted once), labelled A, B, C in content-hash order."""
    by_hash: dict[str, Path] = {}
    for path in find_clip_paths(uploads_dir):
        by_hash.setdefault(_sha256(path), path)
    clips = []
    for index, (digest, path) in enumerate(sorted(by_hash.items())):
        probe = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
            capture_output=True, text=True, check=True,
        ).stdout)
        video = next(s for s in probe["streams"] if s.get("codec_type") == "video")
        clips.append(Clip(
            label=chr(ord("A") + index),
            path=path,
            sha256=digest,
            codec=str(video.get("codec_name", "")),
            pix_fmt=str(video.get("pix_fmt", "")),
            transfer=str(video.get("color_transfer", "")),
            width=int(video["width"]),
            height=int(video["height"]),
            duration_s=float(probe["format"]["duration"]),
            metadata=_metadata(probe["format"].get("tags", {})),
        ))
    return clips


def decode_luma(clip: Clip, time_s: float, count: int = 1) -> list[np.ndarray]:
    """`count` consecutive frames from `time_s`, as 8-bit luma planes, decoded into memory only."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, time_s):.3f}", "-i", str(clip.path), "-frames:v", str(count),
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True,
    ).stdout
    frames = np.frombuffer(raw, dtype=np.uint8)
    size = clip.width * clip.height
    return [frames[i * size : (i + 1) * size].reshape(clip.height, clip.width) for i in range(len(frames) // size)]


def is_plausible(clip: Clip, outcome: solvers.SolverOutcome) -> bool:
    """Every matched star stands above the horizon at the clip's time and place."""
    if not outcome.matches or clip.metadata["utc"] is None or clip.metadata["latDeg"] is None:
        return False
    frame = ObservationFrame(utc=datetime.fromisoformat(clip.metadata["utc"]), lat_deg=clip.metadata["latDeg"], lon_deg=clip.metadata["lonDeg"])
    _, alt = frame.altaz(np.array([m[2] for m in outcome.matches]), np.array([m[3] for m in outcome.matches]))
    return bool(np.all(alt > 0.0))


@dataclass
class FrameResult:
    clip: str
    time_s: float
    stacked: int  # frames averaged before solving; 1 for a single frame
    status: str
    claimed: bool
    plausible: bool
    detections: int
    held_out_inliers: int
    elapsed_s: float
    error: str | None


def _solve(clip: Clip, luma: np.ndarray, timeout_s: float) -> solvers.SolverOutcome:
    rgb = np.repeat(luma[:, :, None], 3, axis=2)
    return solvers.run_solver("today-api", solvers.SolverInput(rgb, clip.metadata), timeout_s)


def run_clip(clip: Clip, *, frames: int = 8, stack: int = 5, timeout_s: float = 180.0) -> list[FrameResult]:
    """Today's solver on `frames` single frames spread over the clip, and on one `stack`-frame
    average from the middle (aligned by today's stacker), each under a time limit."""
    if clip.metadata["utc"] is None or clip.metadata["latDeg"] is None:
        return [FrameResult(clip.label, 0.0, 1, "missing-metadata", False, False, 0, 0, 0.0, "the API needs a time and a place")]
    results = []
    for k in range(frames):
        time_s = clip.duration_s * (k + 0.5) / frames
        outcome = _solve(clip, decode_luma(clip, time_s)[0].astype(np.float64), timeout_s)
        results.append(FrameResult(clip.label, round(time_s, 2), 1, outcome.status, outcome.claimed,
                                   outcome.claimed and is_plausible(clip, outcome), outcome.detections,
                                   outcome.held_out_inliers, outcome.elapsed_s, outcome.error))
    if stack > 1:
        from services.science.solver import stack_video_frames_for_calibration

        time_s = clip.duration_s / 2.0
        planes = [p.astype(np.float64) / 255.0 for p in decode_luma(clip, time_s, stack)]
        composite = np.asarray(stack_video_frames_for_calibration(planes)) * 255.0
        outcome = _solve(clip, composite, timeout_s)
        results.append(FrameResult(clip.label, round(time_s, 2), len(planes), outcome.status, outcome.claimed,
                                   outcome.claimed and is_plausible(clip, outcome), outcome.detections,
                                   outcome.held_out_inliers, outcome.elapsed_s, outcome.error))
    return results


def public_summary(clips: list[Clip], results: list[FrameResult]) -> list[dict]:
    """Per clip: neutral id, capture format, statuses and detection counts. Nothing finer."""
    out = []
    for clip in clips:
        mine = [r for r in results if r.clip == clip.label]
        counts = [r.detections for r in mine if r.status not in ("timeout", "error", "missing-metadata")]
        statuses: dict[str, int] = {}
        for r in mine:
            statuses[r.status] = statuses.get(r.status, 0) + 1
        out.append({
            "clip": clip.label,
            "captureFormat": clip.capture_format(),
            "runs": len(mine),
            "statuses": statuses,
            "claims": sum(r.claimed for r in mine),
            "implausibleClaims": sum(r.claimed and not r.plausible for r in mine),
            "detections": {"min": min(counts), "median": int(np.median(counts)), "max": max(counts)} if counts else None,
        })
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Today's solver on the owner's clips, locally. Personal data: prints only to this terminal.")
    parser.add_argument("--frames", type=int, default=8)
    parser.add_argument("--stack", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args(argv)
    clips = find_clips()
    if not clips:
        print("No clips in data/uploads; nothing to run.")
        return 0
    results: list[FrameResult] = []
    for clip in clips:
        for r in run_clip(clip, frames=args.frames, stack=args.stack, timeout_s=args.timeout):
            results.append(r)
            print(f"clip {r.clip} t={r.time_s:6.2f}s stack={r.stacked}: {r.status}, claimed {r.claimed}, plausible {r.plausible}, "
                  f"{r.detections} detections, {r.held_out_inliers} held-out inliers, {r.elapsed_s:.1f} s{', ' + r.error if r.error else ''}", flush=True)
    print(json.dumps({"frames": "real, no ground truth", "solver": "today-api", "clips": public_summary(clips, results)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
