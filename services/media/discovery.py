"""Generic dynamic media discovery service (`services/media/discovery.py`).

Recursively scans `data/uploads/` for any image or video file without hardcoded
filenames, probes container/QuickTime metadata via `ffprobe`, computes content-addressed
SHA-256 digests, links any rendered variants in `apps/web/public/` or `data/artifacts/`,
and computes each item's `ObservationSolution` and Solar System ephemerides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
from typing import Any

import numpy as np

from services.media.models import compute_file_sha256
from services.science.ephemeris import compute_solar_system_ephemerides
from services.science.horizon_solver import solve_horizon_and_beacon_pose
from services.science.wcs_models import ObservationSolution

SUPPORTED_MEDIA_EXTENSIONS = {".mov", ".mp4", ".m4v", ".png", ".jpg", ".jpeg"}
ISO6709_PATTERN = re.compile(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)(?:([+-]\d+(?:\.\d+)?))?")

_DISCOVERY_CACHE: dict[str, dict[str, Any]] = {}


@dataclass(frozen=True)
class DiscoveredMediaItem:
    """Dynamically discovered source media asset with probed metadata and sky solution."""

    id: str
    filename: str
    relative_path: str
    absolute_path: str
    sha256: str
    media_type: str
    mime_type: str
    width: int
    height: int
    duration_sec: float
    fps: float
    video_codec: str
    has_audio: bool
    audio_codec: str | None
    camera_model: str | None
    location_iso6709: str | None
    observer_lat: float
    observer_lon: float
    observer_elevation_m: float
    captured_at_utc: str
    aliases: list[str] = field(default_factory=list)
    variants: dict[str, str] = field(default_factory=dict)
    artifact_report_path: str | None = None
    observation_solution: ObservationSolution | None = None
    ephemerides: list[dict[str, Any]] = field(default_factory=list)

    def to_contract_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "relativePath": self.relative_path,
            "sha256": self.sha256,
            "mediaType": self.media_type,
            "mimeType": self.mime_type,
            "width": int(self.width),
            "height": int(self.height),
            "durationSec": float(self.duration_sec),
            "fps": float(self.fps),
            "videoCodec": self.video_codec,
            "hasAudio": bool(self.has_audio),
            "audioCodec": self.audio_codec,
            "cameraModel": self.camera_model,
            "locationIso6709": self.location_iso6709,
            "observerLocation": {
                "latDeg": float(self.observer_lat),
                "lonDeg": float(self.observer_lon),
                "elevationM": float(self.observer_elevation_m),
            },
            "capturedAtUTC": self.captured_at_utc,
            "aliases": list(self.aliases),
            "variants": dict(self.variants),
            "artifactReportPath": self.artifact_report_path,
            "observationSolution": (
                self.observation_solution.to_contract_dict()
                if self.observation_solution is not None
                else None
            ),
            "ephemerides": list(self.ephemerides),
        }


def parse_iso6709_location(
    raw_iso6709: str | None,
    default_lat: float = 30.0,
    default_lon: float = -40.0,
    default_elev_m: float = 700.0,
) -> tuple[float, float, float]:
    """Parse an Apple QuickTime ISO-6709 string (e.g. `+30.0-040.0000+705.200/`) into (lat, lon, elev)."""
    if not raw_iso6709:
        return (default_lat, default_lon, default_elev_m)
    match = ISO6709_PATTERN.search(raw_iso6709.strip())
    if not match:
        return (default_lat, default_lon, default_elev_m)
    lat = float(match.group(1))
    lon = float(match.group(2))
    elev = float(match.group(3)) if match.group(3) is not None else default_elev_m
    return (lat, lon, elev)


def normalize_creation_utc(raw_date: str | None) -> str:
    """Normalize QuickTime creation date into an ISO-8601 UTC timestamp (`...Z`)."""
    if not raw_date:
        return "2030-01-01T02:00:00Z"
    try:
        cleaned = raw_date.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        dt_utc = dt.astimezone(timezone.utc)
        return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return "2030-01-01T02:00:00Z"


def _probe_media_metadata(file_path: Path) -> dict[str, Any]:
    """Run `ffprobe` on `file_path` to extract stream geometry, duration, and QuickTime tags."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(file_path),
    ]
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        return {}
    try:
        return json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}


def _extract_representative_luma(file_path: Path, width: int, height: int) -> np.ndarray:
    """Decode up to 5 representative frames (1 fps) and select the frame with the strongest
    upper-sky signal as a 2D float64 luminance array in [0, 1].
    """
    scale_w = min(480, max(64, width))
    scale_h = max(36, int(round(height * (scale_w / float(max(width, 1))))))
    is_vid = file_path.suffix.lower() in {".mov", ".mp4", ".m4v"}
    vf = f"fps=1,scale={scale_w}:{scale_h}" if is_vid else f"scale={scale_w}:{scale_h}"
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(file_path),
        "-frames:v",
        "5" if is_vid else "1",
        "-vf",
        vf,
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "pipe:1",
    ]
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    frame_bytes = scale_w * scale_h
    if proc.returncode == 0 and len(proc.stdout) >= frame_bytes:
        n_frames = len(proc.stdout) // frame_bytes
        frames = (
            np.frombuffer(proc.stdout[: n_frames * frame_bytes], dtype=np.uint8)
            .reshape((n_frames, scale_h, scale_w))
            .astype(np.float64)
            / 255.0
        )
        sky_h = max(8, int(scale_h * 0.42))
        best_idx = int(
            np.argmax([float(np.max(f[:sky_h, :]) - np.median(f[:sky_h, :])) for f in frames])
        )
        arr_small = frames[best_idx]
        y_idx = np.linspace(0, scale_h - 1, height).astype(np.intp)
        x_idx = np.linspace(0, scale_w - 1, width).astype(np.intp)
        return arr_small[np.ix_(y_idx, x_idx)]
    return np.full((height, width), 0.03, dtype=np.float64)


def _discover_matching_variants(
    primary_path: Path,
    aliases: list[str],
    sha256: str,
    public_dir: Path,
    artifacts_dir: Path,
) -> tuple[dict[str, str], str | None]:
    """Match pre-rendered web variants in `public_dir` and experiment reports in `artifacts_dir`."""
    variants: dict[str, str] = {}
    candidate_names = [primary_path.stem] + [Path(a).stem for a in aliases]
    tokens: set[str] = set()
    for name in candidate_names:
        tokens.add(name.lower())
        for num_match in re.findall(r"\d{3,}", name):
            tokens.add(num_match)

    if public_dir.exists():
        for pub_file in sorted(public_dir.rglob("*")):
            if not pub_file.is_file():
                continue
            stem_lower = pub_file.stem.lower()
            ext_lower = pub_file.suffix.lower()
            if ext_lower not in {".mp4", ".png", ".jpg", ".jpeg"}:
                continue
            stem_parts = set(re.split(r"[_-]", stem_lower))
            if any(
                tok in stem_parts or stem_lower.startswith(f"{tok}_") or stem_lower == tok
                for tok in tokens
            ):
                rel_web = "/" + pub_file.relative_to(public_dir).as_posix()
                suffix_tag = stem_lower.split("_")[-1] if "_" in stem_lower else "default"
                key = f"{suffix_tag}_{ext_lower.lstrip('.')}"
                variants[key] = rel_web

    artifact_report_path: str | None = None
    sha_prefix = sha256[:10]
    if artifacts_dir.exists():
        for art_dir in sorted(artifacts_dir.iterdir()):
            if not art_dir.is_dir():
                continue
            if sha_prefix in art_dir.name or any(
                art_dir.name.lower().startswith(c.lower()) for c in candidate_names
            ):
                report_file = art_dir / "experiment_report.json"
                if report_file.exists():
                    artifact_report_path = str(report_file)
                    break

    return variants, artifact_report_path


def discover_uploaded_media(
    uploads_dir: str | Path = "data/uploads",
    public_dir: str | Path = "apps/web/public",
    artifacts_dir: str | Path = "data/artifacts",
) -> list[DiscoveredMediaItem]:
    """Recursively scan `uploads_dir` for all image/video files, deduplicate by content
    SHA-256, probe metadata via `ffprobe`, link rendered variants, and compute sky solutions.
    """
    up_root = Path(uploads_dir)
    pub_root = Path(public_dir)
    art_root = Path(artifacts_dir)

    if not up_root.exists():
        return []

    files_by_sha: dict[str, list[Path]] = {}
    for file_path in sorted(up_root.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in SUPPORTED_MEDIA_EXTENSIONS:
            continue
        digest = compute_file_sha256(file_path)
        files_by_sha.setdefault(digest, []).append(file_path)

    discovered_items: list[DiscoveredMediaItem] = []

    for sha256, paths in files_by_sha.items():
        # Prefer human-readable kebab-case filenames without spaces/underscores as primary
        paths_sorted = sorted(
            paths,
            key=lambda p: (
                "_" in p.stem,
                p.suffix.isupper(),
                len(p.name),
                p.name,
            ),
        )
        primary = paths_sorted[0]
        aliases = [p.name for p in paths_sorted[1:]]

        cache_key = f"{sha256}:{primary.stat().st_mtime_ns}"
        if cache_key in _DISCOVERY_CACHE:
            discovered_items.append(_DISCOVERY_CACHE[cache_key]["item"])
            continue

        probe = _probe_media_metadata(primary)
        streams = probe.get("streams", [])
        fmt = probe.get("format", {})
        tags = fmt.get("tags", {}) or {}

        video_stream = next(
            (s for s in streams if s.get("codec_type") == "video"),
            {},
        )
        audio_stream = next(
            (s for s in streams if s.get("codec_type") == "audio"),
            None,
        )

        width = int(video_stream.get("width") or 1920)
        height = int(video_stream.get("height") or 1080)
        duration_sec = float(
            fmt.get("duration") or video_stream.get("duration") or 0.0
        )
        video_codec = str(video_stream.get("codec_name") or "unknown")

        r_frame_rate = str(video_stream.get("r_frame_rate") or "30/1")
        if "/" in r_frame_rate:
            num_s, den_s = r_frame_rate.split("/", 1)
            fps = float(num_s) / max(float(den_s), 1.0)
        else:
            fps = float(r_frame_rate or 30.0)

        ext = primary.suffix.lower()
        is_image = ext in {".png", ".jpg", ".jpeg"}
        media_type = "image" if is_image else "video"
        mime_type = (
            f"image/{'jpeg' if ext in {'.jpg', '.jpeg'} else 'png'}"
            if is_image
            else ("video/quicktime" if ext == ".mov" else "video/mp4")
        )

        raw_iso6709 = (
            tags.get("com.apple.quicktime.location.ISO6709")
            or tags.get("location")
        )
        lat_deg, lon_deg, elev_m = parse_iso6709_location(raw_iso6709)

        raw_creation = (
            tags.get("com.apple.quicktime.creationdate")
            or tags.get("creation_time")
        )
        captured_at_utc = normalize_creation_utc(raw_creation)
        camera_model = (
            tags.get("com.apple.quicktime.model")
            or tags.get("model")
        )

        variants, artifact_report_path = _discover_matching_variants(
            primary, aliases, sha256, pub_root, art_root
        )

        asset_id = f"asset-{primary.stem}"
        solution_id = f"sol-{sha256[:10]}"

        # Decode representative sky frame directly from primary media (or PNG fallback)
        frame_source_path = primary
        if not primary.exists() and "orig_png" in variants:
            cand_png = pub_root / variants["orig_png"].lstrip("/")
            if cand_png.exists():
                frame_source_path = cand_png

        luma = _extract_representative_luma(frame_source_path, width=width, height=height)
        solution = solve_horizon_and_beacon_pose(
            image=luma,
            obs_utc_iso=captured_at_utc,
            observer_lat=lat_deg,
            observer_lon=lon_deg,
            source_asset_id=asset_id,
            source_sha256=sha256,
            frame_time_sec=0.0,
            solution_id=solution_id,
        )
        ephemerides = [
            b.to_contract_dict()
            for b in compute_solar_system_ephemerides(
                obs_utc_iso=captured_at_utc,
                lat_deg=lat_deg,
                lon_deg=lon_deg,
                observation_solution_id=solution_id,
            )
        ]

        try:
            rel_path = primary.relative_to(Path.cwd()).as_posix()
        except ValueError:
            rel_path = str(primary)

        item = DiscoveredMediaItem(
            id=asset_id,
            filename=primary.name,
            relative_path=rel_path,
            absolute_path=str(primary.resolve()),
            sha256=sha256,
            media_type=media_type,
            mime_type=mime_type,
            width=width,
            height=height,
            duration_sec=duration_sec,
            fps=round(fps, 3),
            video_codec=video_codec,
            has_audio=audio_stream is not None,
            audio_codec=audio_stream.get("codec_name") if audio_stream else None,
            camera_model=camera_model,
            location_iso6709=raw_iso6709,
            observer_lat=lat_deg,
            observer_lon=lon_deg,
            observer_elevation_m=elev_m,
            captured_at_utc=captured_at_utc,
            aliases=aliases,
            variants=variants,
            artifact_report_path=artifact_report_path,
            observation_solution=solution,
            ephemerides=ephemerides,
        )
        _DISCOVERY_CACHE[cache_key] = {"item": item}
        discovered_items.append(item)

    discovered_items.sort(key=lambda item: (item.captured_at_utc, item.filename))
    return discovered_items
