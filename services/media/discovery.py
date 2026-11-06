"""Generic dynamic media discovery service (`services/media/discovery.py`).

Recursively scans `data/uploads/` for any image or video file without hardcoded
filenames, probes container/QuickTime metadata via `ffprobe`, computes content-addressed
SHA-256 digests, links any rendered variants in `apps/web/public/` or `data/artifacts/`,
and computes each item's `ObservationSolution` and Solar System ephemerides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import subprocess
from typing import Any

import numpy as np

from services.media.models import compute_file_sha256
from services.science.coherent_stack import integrate_wcs_coherent_star_trajectories
from services.science.ephemeris import compute_solar_system_ephemerides, require_ephemeris_range
from services.science.horizon_solver import solve_horizon_and_beacon_pose
from services.science.optical_flow import compute_video_pose_trajectory
from services.science.radiometry import predict_sky_brightness
from services.science.wcs_models import ObservationSolution

SUPPORTED_MEDIA_EXTENSIONS = {".mov", ".mp4", ".m4v", ".png", ".jpg", ".jpeg"}
ISO6709_PATTERN = re.compile(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)(?:([+-]\d+(?:\.\d+)?))?")
# ffmpeg's showinfo filter logs each frame it passes with ffmpeg's own timestamp for it
SHOWINFO_PTS_TIME = re.compile(r"\bpts_time:\s*(-?\d+(?:\.\d+)?)")
# Under -loglevel level+info ffmpeg tags each line with its level; these are its error lines
FFMPEG_ERROR_TAG = re.compile(r"\[(?:error|fatal|panic)\] ")

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
    # As ffprobe reports them: None when it gives none (a still has no frame rate)
    duration_sec: float | None
    fps: float | None
    video_codec: str
    has_audio: bool
    audio_codec: str | None
    camera_model: str | None
    location_iso6709: str | None
    observer_lat: float | None
    observer_lon: float | None
    observer_elevation_m: float | None
    captured_at_utc: str | None
    aliases: list[str] = field(default_factory=list)
    variants: dict[str, str] = field(default_factory=dict)
    artifact_report_path: str | None = None
    observation_solution: ObservationSolution | None = None
    ephemerides: list[dict[str, Any]] = field(default_factory=list)
    pose_trajectory: list[dict[str, Any]] = field(default_factory=list)
    # Krisciunas & Schaefer sky brightness predicted at the zenith for the place and the moment of the frame decoded
    # (the capture moment plus the frame's time in the clip), like the ephemerides and the solution
    predicted_sky_brightness: dict[str, Any] = field(default_factory=dict)
    coherent_star_audit: list[dict[str, Any]] = field(default_factory=list)
    # ffmpeg's reason when no frame could be decoded: the item then has no pose, trajectory or coherent audit
    decode_error: str | None = None
    # Why a capture moment got no ephemerides and no pose: outside the ephemeris' 1800 to 2050
    ephemeris_error: str | None = None

    def to_contract_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "filename": self.filename,
            "relativePath": self.relative_path,
            "sha256": self.sha256,
            "mediaType": self.media_type,
            "mimeType": self.mime_type,
            "width": int(self.width),
            "height": int(self.height),
            "videoCodec": self.video_codec,
            "hasAudio": bool(self.has_audio),
            "audioCodec": self.audio_codec,
            "cameraModel": self.camera_model,
            "locationIso6709": self.location_iso6709,
            "observerLocation": (
                {
                    "latDeg": float(self.observer_lat),
                    "lonDeg": float(self.observer_lon),
                    "elevationM": (
                        float(self.observer_elevation_m)
                        if self.observer_elevation_m is not None
                        else None
                    ),
                }
                if self.observer_lat is not None and self.observer_lon is not None
                else None
            ),
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
            "poseTrajectory": list(self.pose_trajectory),
            "predictedSkyBrightness": dict(self.predicted_sky_brightness),
            "coherentStarAudit": list(self.coherent_star_audit),
        }
        if self.duration_sec is not None:
            payload["durationSec"] = float(self.duration_sec)
        if self.fps is not None:
            payload["fps"] = float(self.fps)
        if self.decode_error is not None:
            payload["decodeError"] = self.decode_error
        if self.ephemeris_error is not None:
            payload["ephemerisError"] = self.ephemeris_error
        return payload


def parse_iso6709_location(
    raw_iso6709: str | None,
) -> tuple[float, float, float | None] | None:
    """Parse an Apple QuickTime ISO-6709 string (e.g. `+30.1250-040.2500+12.000/`) into (lat, lon, elev).

    Returns None when the file carries no parseable location; elevation is None when absent.
    """
    if not raw_iso6709:
        return None
    match = ISO6709_PATTERN.search(raw_iso6709.strip())
    if not match:
        return None
    lat = float(match.group(1))
    lon = float(match.group(2))
    elev = float(match.group(3)) if match.group(3) is not None else None
    return (lat, lon, elev)


def normalize_creation_utc(raw_date: str | None) -> str | None:
    """Normalize QuickTime creation date into an ISO-8601 UTC timestamp (`...Z`), or None when absent or unparseable."""
    if not raw_date:
        return None
    try:
        cleaned = raw_date.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        dt_utc = dt.astimezone(timezone.utc)
        return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def _moment_plus(utc_iso: str, seconds: float) -> str:
    """`utc_iso`, as normalize_creation_utc writes it, plus `seconds`: the moment of a frame that many seconds into a
    clip. In the same form, with milliseconds only when the moment has a fraction of a second."""
    moment = datetime.strptime(utc_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) + timedelta(seconds=seconds)
    if moment.microsecond == 0:
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


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


class FrameDecodeError(RuntimeError):
    """ffmpeg could not decode a frame of the file; the message is ffmpeg's own."""


def _extract_representative_luma(
    file_path: Path, width: int, height: int
) -> tuple[np.ndarray, list[dict[str, Any]], float]:
    """Decode up to 8 frames (1 fps at up to 480 px wide) to pick the frame with the strongest
    upper-sky signal and compute the 1 fps pose trajectory, then decode that picked frame again at
    its native size in 16-bit gray (`gray16le`) as a 2D float64 luminance array in [0, 1].

    Also returns the selected frame's time in the clip, in seconds from the start of its video stream: ffmpeg's own
    timestamp for that frame (showinfo's pts_time after the fps filter), 0 for a still.

    Raises FrameDecodeError when ffmpeg decodes no frame, gives no time for the frames it decodes, or the native
    decode does not return that same frame at `(height, width)`: nothing stands in for the pixels or for when they
    were recorded.
    """
    scale_w = min(480, max(64, width))
    scale_h = max(36, int(round(height * (scale_w / float(max(width, 1))))))
    is_vid = file_path.suffix.lower() in {".mov", ".mp4", ".m4v"}
    vf = (f"fps=1,scale={scale_w}:{scale_h}" if is_vid else f"scale={scale_w}:{scale_h}") + ",showinfo"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        # showinfo logs at the info level; the level tags keep ffmpeg's error lines apart from it
        "-loglevel",
        "level+info",
        "-i",
        str(file_path),
        "-frames:v",
        "8" if is_vid else "1",
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
    log = proc.stderr.decode("utf-8", errors="replace").splitlines()
    if proc.returncode == 0 and len(proc.stdout) >= frame_bytes:
        n_frames = len(proc.stdout) // frame_bytes
        frame_times = [float(match.group(1)) for line in log if (match := SHOWINFO_PTS_TIME.search(line))]
        if len(frame_times) < n_frames:
            raise FrameDecodeError(
                f"ffmpeg decoded {n_frames} frame(s) but gave a time for {len(frame_times)}: no time for the frames"
            )
        frames = (
            np.frombuffer(proc.stdout[: n_frames * frame_bytes], dtype=np.uint8)
            .reshape((n_frames, scale_h, scale_w))
            .astype(np.float64)
            / 255.0
        )
        sky_h = max(8, int(scale_h * 0.22))
        best_idx = int(
            np.argmax([float(np.max(f[:sky_h, :]) - np.median(f[:sky_h, :])) for f in frames])
        )
        picked_time = frame_times[best_idx]
        native_vf = (f"fps=1,select=eq(n\\,{best_idx})" if is_vid else f"select=eq(n\\,{best_idx})") + ",showinfo"
        native_proc = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "level+info",
                "-i",
                str(file_path),
                "-frames:v",
                "1",
                "-vf",
                native_vf,
                "-f",
                "rawvideo",
                "-pix_fmt",
                "gray16le",
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        native_bytes = width * height * 2
        native_log = native_proc.stderr.decode("utf-8", errors="replace").splitlines()
        if native_proc.returncode != 0 or len(native_proc.stdout) != native_bytes:
            native_errors = [
                FFMPEG_ERROR_TAG.sub("", line, count=1).strip()
                for line in native_log
                if FFMPEG_ERROR_TAG.search(line)
            ]
            raise FrameDecodeError(
                native_errors[0]
                if native_errors
                else (
                    f"ffmpeg returned {len(native_proc.stdout)} bytes, expected one "
                    f"{width} x {height} gray16le frame ({native_bytes} bytes)"
                )
            )
        native_times = [float(match.group(1)) for line in native_log if (match := SHOWINFO_PTS_TIME.search(line))]
        if native_times != [picked_time]:
            raise FrameDecodeError(
                f"native decode of frame {best_idx} gave time {native_times}, expected [{picked_time}]"
            )
        rep_luma = (
            np.frombuffer(native_proc.stdout, dtype="<u2")
            .reshape((height, width))
            .astype(np.float64)
            / 65535.0
        )

        raw_traj = compute_video_pose_trajectory(list(frames), fps=1.0)
        sx = float(width) / float(max(scale_w, 1))
        sy = float(height) / float(max(scale_h, 1))
        scaled_traj = [
            {
                **step,
                "duPx": round(float(step["duPx"]) * sx, 3),
                "dvPx": round(float(step["dvPx"]) * sy, 3),
            }
            for step in raw_traj
        ]
        return rep_luma, scaled_traj, picked_time
    # ffmpeg's first error line, without its level tag, as it reads under -v error
    errors = [FFMPEG_ERROR_TAG.sub("", line, count=1).strip() for line in log if FFMPEG_ERROR_TAG.search(line)]
    raise FrameDecodeError(
        errors[0] if errors else f"ffmpeg returned {len(proc.stdout)} bytes, less than one {scale_w} x {scale_h} frame"
    )


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
                    artifact_report_path = report_file.relative_to(artifacts_dir).as_posix()
                    break

    return variants, artifact_report_path


def discover_uploaded_media(
    uploads_dir: str | Path = "data/uploads",
    public_dir: str | Path = "apps/web/public",
    artifacts_dir: str | Path = "data/artifacts",
) -> list[DiscoveredMediaItem]:
    """The items `discover_uploaded_media_report` finds; files it could not read are left out."""
    return discover_uploaded_media_report(uploads_dir, public_dir, artifacts_dir)[0]


def discover_uploaded_media_report(
    uploads_dir: str | Path = "data/uploads",
    public_dir: str | Path = "apps/web/public",
    artifacts_dir: str | Path = "data/artifacts",
) -> tuple[list[DiscoveredMediaItem], list[dict[str, str]]]:
    """Recursively scan `uploads_dir` for all image/video files, deduplicate by content
    SHA-256, probe metadata via `ffprobe`, link rendered variants, and compute sky solutions.

    Returns the items and the files skipped, each with the reason: a file ffprobe cannot read, or that has no
    picture size, is never listed with a size, rate or duration it does not have.
    """
    up_root = Path(uploads_dir)
    pub_root = Path(public_dir)
    art_root = Path(artifacts_dir)

    if not up_root.exists():
        return [], []

    files_by_sha: dict[str, list[Path]] = {}
    for file_path in sorted(up_root.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in SUPPORTED_MEDIA_EXTENSIONS:
            continue
        digest = compute_file_sha256(file_path)
        files_by_sha.setdefault(digest, []).append(file_path)

    discovered_items: list[DiscoveredMediaItem] = []
    skipped: list[dict[str, str]] = []

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
        if not probe:
            skipped.append({"filename": primary.name, "reason": "ffprobe could not read the file"})
            continue
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

        if not video_stream.get("width") or not video_stream.get("height"):
            skipped.append({"filename": primary.name, "reason": "ffprobe reported no picture size"})
            continue
        width = int(video_stream["width"])
        height = int(video_stream["height"])
        video_codec = str(video_stream.get("codec_name") or "unknown")

        ext = primary.suffix.lower()
        is_image = ext in {".png", ".jpg", ".jpeg"}
        # Only what ffprobe reports: a still spans no time and has no frame rate (ffprobe's image default is not one)
        raw_duration = fmt.get("duration") or video_stream.get("duration")
        duration_sec: float | None = 0.0 if is_image else (float(raw_duration) if raw_duration not in (None, "N/A") else None)
        fps: float | None = None
        num_s, _, den_s = str(video_stream.get("r_frame_rate") or "").partition("/")
        if not is_image and num_s and float(num_s) > 0.0 and float(den_s or 1.0) > 0.0:
            fps = float(num_s) / float(den_s or 1.0)
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
        location = parse_iso6709_location(raw_iso6709)
        lat_deg, lon_deg, elev_m = location if location is not None else (None, None, None)

        # The moment the clip was recorded is QuickTime's com.apple.quicktime.creationdate. Its creation_time is the
        # file's own time, which may be when the file was written or exported: no capture moment, so a file with
        # only that gets no ephemerides, pose or sky prediction, and discovery reports no file time
        captured_at_utc = normalize_creation_utc(tags.get("com.apple.quicktime.creationdate"))
        camera_model = (
            tags.get("com.apple.quicktime.model")
            or tags.get("model")
        )

        variants, artifact_report_path = _discover_matching_variants(
            primary, aliases, sha256, pub_root, art_root
        )

        asset_id = f"asset-{primary.stem}"
        solution_id = f"sol-{sha256[:10]}"

        # The representative frame comes from the original file only: a rendered variant never stands in for it
        decode_error: str | None = None
        luma: np.ndarray | None
        frame_time_sec: float | None = None
        try:
            luma, pose_trajectory, frame_time_sec = _extract_representative_luma(primary, width=width, height=height)
        except FrameDecodeError as exc:
            # No frame: no pose, trajectory or coherent audit; the predictions for the moment need no pixels
            luma, pose_trajectory, decode_error = None, [], str(exc)
        # Every prediction is for the moment of the frame decoded: the recorded start of the clip plus the frame's
        # time in it [inference: QuickTime's creationdate marks the start of recording]. Without a frame, the start.
        frame_moment = (
            _moment_plus(captured_at_utc, frame_time_sec)
            if captured_at_utc is not None and frame_time_sec is not None
            else captured_at_utc
        )
        # The sky solution, ephemerides and sky brightness all need the real capture time and place;
        # a file without them gets none rather than a borrowed moment
        solution: ObservationSolution | None = None
        ephemerides: list[dict[str, Any]] = []
        sky_brightness: dict[str, Any] = {}
        coherent_audit: list[dict[str, Any]] = []
        # A moment outside the ephemeris' 1800 to 2050 gets no predictions and no pose (which rests on the Moon)
        ephemeris_error: str | None = None
        if frame_moment is not None:
            try:
                require_ephemeris_range(frame_moment)
            except ValueError as exc:
                ephemeris_error = str(exc)
        if lat_deg is not None and lon_deg is not None and frame_moment is not None and ephemeris_error is None:
            if luma is not None and frame_time_sec is not None:
                solution = solve_horizon_and_beacon_pose(
                    image=luma,
                    obs_utc_iso=frame_moment,
                    observer_lat=lat_deg,
                    observer_lon=lon_deg,
                    source_asset_id=asset_id,
                    source_sha256=sha256,
                    frame_time_sec=frame_time_sec,
                    solution_id=solution_id,
                )
            ephem_objs = compute_solar_system_ephemerides(
                obs_utc_iso=frame_moment,
                lat_deg=lat_deg,
                lon_deg=lon_deg,
                **({"observation_solution_id": solution_id} if solution is not None else {}),
            )
            ephemerides = [b.to_contract_dict(source_sha256=sha256) for b in ephem_objs]
            # The ephemerides include the Moon; the sky brightness is predicted at the zenith, a stated target
            moon_alt = float(next(b for b in ephem_objs if b.name == "Moon").alt_deg)
            sky_brightness = predict_sky_brightness(
                frame_moment, moon_alt_deg=moon_alt, target_alt_deg=90.0, sep_from_moon_deg=90.0 - moon_alt,
                target="zenith",
            )
            coherent_audit = (
                integrate_wcs_coherent_star_trajectories(
                    frames=[luma],
                    wcs=solution.wcs,
                    pose_trajectory=pose_trajectory,
                    # The frame is a native-size decode of the recording, but the horizon pose is never held-out verified
                    wcs_verified=solution.has_verified_wcs,
                    original_pixels=True,
                    max_stars=6,
                )
                if solution is not None and solution.wcs is not None
                else []
            )

        # Relative to the uploads folder, so no answer carries an absolute path
        rel_path = primary.relative_to(up_root).as_posix()

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
            fps=round(fps, 3) if fps is not None else None,
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
            pose_trajectory=pose_trajectory,
            predicted_sky_brightness=sky_brightness,
            coherent_star_audit=coherent_audit,
            decode_error=decode_error,
            ephemeris_error=ephemeris_error,
        )
        _DISCOVERY_CACHE[cache_key] = {"item": item}
        discovered_items.append(item)

    discovered_items.sort(key=lambda item: (item.captured_at_utc or "", item.filename))
    return discovered_items, skipped
