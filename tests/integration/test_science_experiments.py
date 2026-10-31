"""Integration and Hypothesis property-based verification suite for Albedian's
scientific astrometry, plate solver, proper-motion propagation, astronomy-aware
media restoration runtime, FFmpeg audio-preserving export, and FastAPI service.

Covers:
- PBT Suite #7 (`test_pbt_scientific_coordinates_and_distances`)
- EXP-01 (`test_exp01_consumer_photo_plate_solving_and_negative_rejection`)
- EXP-02 (`test_exp02_low_light_video_temporal_stack_calibration`)
- EXP-03 (`test_exp03_catalog_alignment_and_false_artifact_rejection`)
- EXP-04 (`test_exp04_astronomy_aware_point_protection_vs_conventional_denoise`)
- EXP-08 (`test_exp08_proper_motion_and_historical_epoch_1026ce`)
- EXP-10 (`test_exp10_video_restoration_and_ffmpeg_audio_preserving_export`)
- FastAPI service endpoints (`test_fastapi_endpoints`)
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess

from fastapi.testclient import TestClient
from hypothesis import given, settings, strategies as st
import numpy as np

from services.api import app
from services.media.runtime import MediaExperimentRuntime, compute_file_sha256
from services.science.catalog import (
    ALL_SKY_CATALOG_STARS,
    CONSTELLATION_EDGES,
    PINNED_CATALOG_STARS,
    STAR_BY_ID,
    angular_separation_deg,
    bailer_jones_cartesian_pc,
    compute_light_travel_time_description,
    get_star_by_name,
    icrs_to_altaz,
    propagate_star_epoch,
    ra_dec_to_unit_vector,
    unit_vector_to_ra_dec,
)
from services.science.ephemeris import compute_solar_system_ephemerides
from services.science.solver import (
    WCSModel,
    detect_point_sources,
    render_synthetic_starfield,
    solve_and_verify_field,
    stack_video_frames_for_calibration,
)


# ---------------------------------------------------------------------------
# PBT Suite #7: Scientific Coordinates, WCS Inversion & Bailer-Jones Bounds
# ---------------------------------------------------------------------------


@settings(max_examples=120, deadline=None)
@given(
    ra_deg=st.floats(min_value=0.0, max_value=359.9999, allow_nan=False, allow_infinity=False),
    dec_deg=st.floats(min_value=-89.5, max_value=89.5, allow_nan=False, allow_infinity=False),
    dx_px=st.floats(min_value=-360.0, max_value=360.0, allow_nan=False, allow_infinity=False),
    dy_px=st.floats(min_value=-220.0, max_value=220.0, allow_nan=False, allow_infinity=False),
    k1=st.floats(min_value=-0.08, max_value=0.08, allow_nan=False, allow_infinity=False),
    k2=st.floats(min_value=-0.03, max_value=0.03, allow_nan=False, allow_infinity=False),
    epoch_year=st.floats(min_value=-2000.0, max_value=5000.0, allow_nan=False, allow_infinity=False),
    star_idx=st.integers(min_value=0, max_value=len(PINNED_CATALOG_STARS) - 1),
)
def test_pbt_scientific_coordinates_and_distances(
    ra_deg: float,
    dec_deg: float,
    dx_px: float,
    dy_px: float,
    k1: float,
    k2: float,
    epoch_year: float,
    star_idx: int,
) -> None:
    """PBT Suite #7: Verify spherical unit-vector round-trip, WCS forward/inverse round-trip,
    and Bailer-Jones distance interval invariants across catalog stars and epochs.
    """
    # 1. Unit vector norm & spherical round-trip
    vec = ra_dec_to_unit_vector(ra_deg, dec_deg)
    norm = math.sqrt(vec[0] ** 2 + vec[1] ** 2 + vec[2] ** 2)
    assert abs(norm - 1.0) < 1e-9

    ra_rt, dec_rt = unit_vector_to_ra_dec(vec)
    ang_err = angular_separation_deg(ra_deg, dec_deg, ra_rt, dec_rt)
    assert ang_err < 1e-7

    # 2. WCS pixel_to_world <-> world_to_pixel round-trip with radial distortion
    wcs = WCSModel(
        crval_ra_deg=84.0,
        crval_dec_deg=5.0,
        crpix_x=480.0,
        crpix_y=270.0,
        scale_deg_per_px=0.045,
        rotation_deg=12.5,
        k1=k1,
        k2=k2,
        parity=-1,
        width=960,
        height=540,
    )
    x_in = 480.0 + dx_px
    y_in = 270.0 + dy_px
    w_ra, w_dec = wcs.pixel_to_world(x_in, y_in)
    x_out, y_out = wcs.world_to_pixel(w_ra, w_dec)
    assert math.hypot(x_out - x_in, y_out - y_in) < 1e-5

    # 3. Bailer-Jones distance ordering and epoch propagation invariants
    assert len(PINNED_CATALOG_STARS) >= 16
    base_star = PINNED_CATALOG_STARS[star_idx]
    assert 0.0 < base_star.lower_pc <= base_star.median_pc <= base_star.upper_pc

    prop_star = propagate_star_epoch(base_star, epoch_year)
    assert 0.0 < prop_star.lower_pc <= prop_star.median_pc <= prop_star.upper_pc
    cx, cy, cz = bailer_jones_cartesian_pc(prop_star)
    dist_3d = math.sqrt(cx * cx + cy * cy + cz * cz)
    assert abs(dist_3d - prop_star.median_pc) < 1e-6


# ---------------------------------------------------------------------------
# EXP-01: Consumer Photo Plate Solving & Independent WCS Verification
# ---------------------------------------------------------------------------


def test_exp01_consumer_photo_plate_solving_and_negative_rejection() -> None:
    """EXP-01: Verify blind/hinted plate solving on a synthetic Orion/Taurus consumer
    frame with independent held-out residual verification, and deterministic negative
    rejection on clipped black and random noise frames.
    """
    width, height = 960, 540
    frame, true_wcs, projected_stars = render_synthetic_starfield(
        width=width,
        height=height,
        read_noise_sigma=1.8,
        flux_scale=1100.0,
        seed=42,
    )
    assert len(projected_stars) >= 11

    detections = detect_point_sources(frame, snr_threshold=4.0)
    assert len(detections) >= 10

    # Blind solve + held-out verification
    solution = solve_and_verify_field(
        detections=detections,
        width=width,
        height=height,
        obs_utc_iso="2026-10-19T22:14:00Z",
        observer_lat=31.149,
        observer_lon=-3.972,
    )
    assert solution.status == "calibrated"
    assert solution.quality.passed_independent_verification is True
    assert solution.held_out_inliers >= 6
    assert solution.median_residual_px <= 1.5
    assert solution.p95_residual_px <= 3.0
    assert solution.wcs is not None
    assert angular_separation_deg(
        solution.wcs.crval_ra_deg,
        solution.wcs.crval_dec_deg,
        true_wcs.crval_ra_deg,
        true_wcs.crval_dec_deg,
    ) < 0.25

    matched_findings = [f for f in solution.findings if f.evidence_class == "catalog_matched"]
    assert len(matched_findings) >= 10
    matched_names = {f.common_name for f in matched_findings}
    assert {"Betelgeuse", "Rigel", "Bellatrix", "Alnilam", "Alnitak", "Mintaka"}.issubset(
        matched_names
    )

    # Negative Fixture 1: Near-black clipped frame
    black_frame = np.zeros((height, width), dtype=np.float64)
    black_dets = detect_point_sources(black_frame, snr_threshold=3.5)
    assert len(black_dets) == 0
    black_sol = solve_and_verify_field(black_dets, width=width, height=height)
    assert black_sol.status in ("predicted_only", "unresolved")
    assert black_sol.status != "calibrated"
    assert black_sol.held_out_inliers == 0

    # Negative Fixture 2: Pure random Gaussian sensor noise frame
    rng = np.random.default_rng(999)
    noise_frame = rng.normal(loc=20.0, scale=3.0, size=(height, width))
    noise_dets = detect_point_sources(noise_frame, snr_threshold=3.5)
    noise_sol = solve_and_verify_field(noise_dets, width=width, height=height)
    assert noise_sol.status in ("predicted_only", "unresolved")
    assert noise_sol.status != "calibrated"
    assert all(f.evidence_class != "catalog_matched" for f in noise_sol.findings)


# ---------------------------------------------------------------------------
# EXP-02: Low-Light Video Temporal Stack Calibration
# ---------------------------------------------------------------------------


def test_exp02_low_light_video_temporal_stack_calibration() -> None:
    """EXP-02: Generate 10 ultra-low-SNR shifted video frames where any single frame
    detects < 4 stars (`unresolved`), while `stack_video_frames_for_calibration` boosts
    SNR by > 2.2x and achieves a verified `calibrated` WCS solution.
    """
    width, height = 640, 360
    wcs = WCSModel(
        crval_ra_deg=82.0,
        crval_dec_deg=9.0,
        crpix_x=width / 2.0,
        crpix_y=height / 2.0,
        scale_deg_per_px=0.118,
        rotation_deg=2.0,
        k1=-0.02,
        k2=0.0,
        parity=-1,
        width=width,
        height=height,
    )

    # Foreground horizon mask along bottom 18 rows
    sky_mask = np.ones((height, width), dtype=bool)
    sky_mask[-18:, :] = False

    # Generate 10 drifted ultra-low-SNR frames where only 2-3 brightest stars exceed gate in 1 frame
    frames: list[np.ndarray] = []
    for idx in range(10):
        dy = idx * 0.8
        dx = -idx * 1.1
        frame_i, _, _ = render_synthetic_starfield(
            width=width,
            height=height,
            wcs=wcs,
            psf_sigma_px=1.15,
            background_level=30.0,
            read_noise_sigma=5.4,
            flux_scale=92.0,
            seed=200 + idx,
            sky_mask=sky_mask,
            shift_yx=(dy, dx),
        )
        frames.append(frame_i)

    # Verify single frame detects < 4 stars and resolves to "unresolved"
    single_dets = detect_point_sources(frames[0], sky_mask=sky_mask, snr_threshold=4.8)
    assert len(single_dets) < 4
    single_sol = solve_and_verify_field(single_dets, width=width, height=height)
    assert single_sol.status == "unresolved"

    # Align and stack the 10 frames
    stack_result = stack_video_frames_for_calibration(frames, sky_mask=sky_mask)
    assert stack_result.snr_boost_factor > 2.2

    stacked_dets = detect_point_sources(
        stack_result.composite_luma, sky_mask=sky_mask, snr_threshold=4.8
    )
    assert len(stacked_dets) >= 10

    stacked_sol = solve_and_verify_field(stacked_dets, width=width, height=height)
    assert stacked_sol.status == "calibrated"
    assert stacked_sol.held_out_inliers >= 6
    assert stacked_sol.median_residual_px <= 2.0


# ---------------------------------------------------------------------------
# EXP-03: Catalog Alignment & False Artifact Rejection
# ---------------------------------------------------------------------------


def test_exp03_catalog_alignment_and_false_artifact_rejection() -> None:
    """EXP-03: Inject 12 synthetic single-pixel hot pixels into a valid starfield and
    verify that 3-sigma outlier + impulse PSF rejection prevents any hot pixel from
    receiving a `catalog_matched` identity.
    """
    width, height = 960, 540
    frame, _, _ = render_synthetic_starfield(
        width=width,
        height=height,
        read_noise_sigma=1.6,
        flux_scale=1100.0,
        seed=303,
    )

    hot_pixel_coords = [
        (75, 65),
        (140, 120),
        (230, 88),
        (310, 410),
        (420, 195),
        (515, 460),
        (610, 95),
        (690, 340),
        (745, 210),
        (810, 445),
        (860, 130),
        (905, 385),
    ]
    assert len(hot_pixel_coords) == 12
    corrupted = frame.copy()
    for hx, hy in hot_pixel_coords:
        corrupted[hy, hx] += 220.0

    detections = detect_point_sources(corrupted, snr_threshold=3.8)
    solution = solve_and_verify_field(detections, width=width, height=height)

    assert solution.status == "calibrated"
    assert solution.held_out_inliers >= 6
    assert solution.median_residual_px <= 1.5

    # Verify that zero injected hot pixels received `catalog_matched`
    catalog_matched = [f for f in solution.findings if f.evidence_class == "catalog_matched"]
    assert len(catalog_matched) >= 10
    for finding in catalog_matched:
        assert finding.source_detection is not None
        fx = finding.source_detection.x_px
        fy = finding.source_detection.y_px
        for hx, hy in hot_pixel_coords:
            assert math.hypot(fx - hx, fy - hy) > 3.5, (
                f"Hot pixel at ({hx}, {hy}) was falsely assigned catalog_matched ({finding.title})"
            )


# ---------------------------------------------------------------------------
# EXP-04: Astronomy-Aware Point Protection vs. Conventional Denoise
# ---------------------------------------------------------------------------


def test_exp04_astronomy_aware_point_protection_vs_conventional_denoise() -> None:
    """EXP-04: Verify that `star_protected` restoration retains >= 0.90 of faint star
    peak flux while `conventional_denoise` attenuates faint stars significantly, and
    neither invents synthetic catalog stars.
    """
    width, height = 640, 360
    noisy_frame, _, _ = render_synthetic_starfield(
        width=width,
        height=height,
        read_noise_sigma=3.8,
        flux_scale=620.0,
        seed=404,
    )
    fg_mask = np.zeros((height, width), dtype=bool)
    fg_mask[-40:, 180:460] = True

    runtime = MediaExperimentRuntime()
    conv_res = runtime.execute_restoration_branch(
        [noisy_frame],
        profile="conventional_denoise",
        preserve_stars=False,
        foreground_mask=fg_mask,
    )
    star_res = runtime.execute_restoration_branch(
        [noisy_frame],
        profile="star_protected",
        preserve_stars=True,
        foreground_mask=fg_mask,
    )

    assert star_res.point_source_retention_ratio >= 0.90
    assert conv_res.point_source_retention_ratio < 0.55
    assert star_res.point_source_retention_ratio > conv_res.point_source_retention_ratio + 0.35
    assert star_res.shadow_snr_gain_db > 4.0
    assert star_res.false_point_artifact_count == 0
    assert conv_res.false_point_artifact_count == 0
    assert star_res.allow_synthetic_sky is False


# ---------------------------------------------------------------------------
# EXP-08: Proper Motion & Historical Epoch Propagation (1026 CE)
# ---------------------------------------------------------------------------


def test_exp08_proper_motion_and_historical_epoch_1026ce() -> None:
    """EXP-08: Verify proper-motion propagation to 1026 CE for high-proper-motion nearby
    stars (Sirius, Procyon, Aldebaran) vs distant Orion belt supergiants (Alnilam, Mintaka, Alnitak).
    """
    target_year = 1026.0
    high_pm_names = ("Sirius", "Procyon", "Aldebaran")
    belt_supergiants = ("Alnilam", "Mintaka", "Alnitak")

    high_shifts_arcsec: dict[str, float] = {}
    for name in high_pm_names:
        s_now = get_star_by_name(name)
        s_1026 = propagate_star_epoch(s_now, target_year)
        sep_arcsec = (
            angular_separation_deg(s_now.ra_deg, s_now.dec_deg, s_1026.ra_deg, s_1026.dec_deg)
            * 3600.0
        )
        high_shifts_arcsec[name] = sep_arcsec
        assert 0.0 < s_1026.lower_pc <= s_1026.median_pc <= s_1026.upper_pc

    belt_shifts_arcsec: dict[str, float] = {}
    for name in belt_supergiants:
        s_now = get_star_by_name(name)
        s_1026 = propagate_star_epoch(s_now, target_year)
        sep_arcsec = (
            angular_separation_deg(s_now.ra_deg, s_now.dec_deg, s_1026.ra_deg, s_1026.dec_deg)
            * 3600.0
        )
        belt_shifts_arcsec[name] = sep_arcsec

    # Sirius and Procyon move by > 1100 arcsec (> 18 arcmin) over 974 years; Aldebaran by > 190 arcsec
    assert high_shifts_arcsec["Sirius"] > 1200.0
    assert high_shifts_arcsec["Procyon"] > 1100.0
    assert high_shifts_arcsec["Aldebaran"] > 180.0

    # Distant Orion Belt supergiants move by < 5 arcseconds over the same 974-year span
    for name, shift in belt_shifts_arcsec.items():
        assert shift < 5.0, f"Expected small shift for {name}, got {shift} arcsec"


# ---------------------------------------------------------------------------
# EXP-10: Video Restoration & FFmpeg Audio-Preserving Remux Export
# ---------------------------------------------------------------------------


def test_exp10_video_restoration_and_ffmpeg_audio_preserving_export(tmp_path: Path) -> None:
    """EXP-10: Synthesize a test dark video clip with an AAC audio tone track via ffmpeg,
    run temporal restoration, export via `export_video_with_audio_remux`, probe with
    `ffprobe`, and verify video + audio streams, duration drift <= 35 ms, and SHA-256 manifest.
    """
    fps = 12
    num_frames = 12  # 1.000 second clip
    duration_sec = num_frames / float(fps)
    source_mp4 = tmp_path / "source_dark_with_audio.mp4"
    exported_mp4 = tmp_path / "restored_star_protected.mp4"

    # Synthesize source MP4 with a 440 Hz sine audio stream using ffmpeg
    gen_cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s=320x180:r={fps}:d={duration_sec}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:sample_rate=48000:duration={duration_sec}",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        str(source_mp4),
    ]
    subprocess.run(gen_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    source_sha_before = compute_file_sha256(source_mp4)

    # Generate 12 low-light starfield frames and restore with temporal star protection
    raw_frames = [
        render_synthetic_starfield(
            width=320,
            height=180,
            read_noise_sigma=3.5,
            flux_scale=750.0,
            seed=500 + i,
            shift_yx=(i * 0.4, -i * 0.5),
        )[0]
        for i in range(num_frames)
    ]

    runtime = MediaExperimentRuntime()
    restored = runtime.execute_restoration_branch(
        raw_frames, profile="temporal", preserve_stars=True
    )
    assert restored.point_source_retention_ratio >= 0.90
    assert restored.shadow_snr_gain_db > 3.0

    export_manifest = runtime.export_video_with_audio_remux(
        frames=restored.restored_frames,
        fps=fps,
        output_path=exported_mp4,
        audio_source_path=source_mp4,
    )

    # Source file must remain completely untouched (SourceAsset immutability)
    assert compute_file_sha256(source_mp4) == source_sha_before
    assert export_manifest.source_sha256 == source_sha_before
    assert Path(export_manifest.output_path).exists()
    assert len(export_manifest.output_sha256) == 64
    assert export_manifest.audio_preserved is True
    assert "video" in export_manifest.stream_types
    assert "audio" in export_manifest.stream_types
    assert export_manifest.duration_drift_ms <= 35.0
    assert export_manifest.audio_sync_offset_ms <= 35.0

    # Independently verify the exported MP4 with ffprobe
    probe_out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,duration",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(exported_mp4),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    probe_json = json.loads(probe_out.stdout.decode("utf-8"))
    codec_types = {s["codec_type"] for s in probe_json["streams"]}
    assert {"video", "audio"}.issubset(codec_types)


# ---------------------------------------------------------------------------
# FastAPI Service Endpoints Verification
# ---------------------------------------------------------------------------


def test_fastapi_endpoints() -> None:
    """Verify `/api/health`, `/api/science/solve`, `/api/science/ephemeris`,
    `/api/media/restore`, and `/api/voice/command` via FastAPI TestClient.
    """
    client = TestClient(app)

    # 1. GET /api/health
    r_health = client.get("/api/health")
    assert r_health.status_code == 200
    h_data = r_health.json()
    assert h_data["status"] == "ok"
    assert h_data["catalogStarCount"] >= 16

    # 2. POST /api/science/solve
    r_solve = client.post(
        "/api/science/solve",
        json={
            "width": 960,
            "height": 540,
            "observedAtUTC": "2026-10-19T22:14:00Z",
            "latDeg": 31.149,
            "lonDeg": -3.972,
        },
    )
    assert r_solve.status_code == 200
    s_data = r_solve.json()
    assert s_data["status"] == "calibrated"
    assert s_data["held_out_inliers"] >= 6
    assert s_data["median_residual_px"] <= 1.5

    # 3. POST /api/science/ephemeris
    r_ephem = client.post(
        "/api/science/ephemeris",
        json={
            "observedAtUTC": "2026-10-19T22:14:00Z",
            "latDeg": 31.149,
            "lonDeg": -3.972,
            "bodies": ["Jupiter", "Uranus", "Saturn", "Mars", "Venus", "Moon"],
        },
    )
    assert r_ephem.status_code == 200
    e_data = r_ephem.json()
    assert len(e_data["findings"]) == 6
    assert all(f["evidenceClass"] == "predicted" for f in e_data["findings"])
    alt_deg, az_deg = icrs_to_altaz(88.7929, 7.4071, 31.149, -3.972, "2026-10-19T22:14:00Z")
    assert -90.0 <= alt_deg <= 90.0
    assert 0.0 <= az_deg < 360.0

    # 4. POST /api/media/restore
    r_restore = client.post(
        "/api/media/restore",
        json={"profile": "star_protected", "preserveStars": True},
    )
    assert r_restore.status_code == 200
    m_data = r_restore.json()
    assert m_data["status"] == "ready"
    assert m_data["allowSyntheticSky"] is False
    assert m_data["point_source_retention_ratio"] >= 0.90

    # 5. POST /api/voice/command (valid command, stale revision, and unsafe synthetic rejection)
    r_voice_ok = client.post(
        "/api/voice/command",
        json={
            "utterance": "Take me into 3D space toward Betelgeuse",
            "context": {
                "sessionId": "s-1",
                "sourceAssetId": "asset-1",
                "viewRevision": 4,
                "calibrationState": "calibrated",
            },
        },
    )
    assert r_voice_ok.status_code == 200
    v_ok = r_voice_ok.json()
    assert v_ok["status"] == "completed"
    assert v_ok["newStateVersion"] == 5
    assert v_ok["proposedAction"]["type"] == "space.enter"

    r_voice_stale = client.post(
        "/api/voice/command",
        json={
            "utterance": "Return to original video",
            "context": {"viewRevision": 7, "calibrationState": "calibrated"},
            "proposedAction": {
                "type": "source.return",
                "requestId": "req-stale",
                "expectedViewRevision": 5,
                "restoreAudio": True,
            },
        },
    )
    assert r_voice_stale.status_code == 200
    assert r_voice_stale.json()["status"] == "rejected"
    assert r_voice_stale.json()["reason"] == "stale_revision"

    # 6. GET /api/science/sky-context (All-Sky 70+ stars, ephemerides, and constellation edges)
    assert len(ALL_SKY_CATALOG_STARS) >= 65
    for st_entry in ALL_SKY_CATALOG_STARS:
        assert 0.0 < st_entry.lower_pc <= st_entry.median_pc <= st_entry.upper_pc
    for c_name, sid_a, sid_b in CONSTELLATION_EDGES:
        assert len(c_name) > 0
        assert sid_a in STAR_BY_ID, f"Missing star_id_a {sid_a} in STAR_BY_ID"
        assert sid_b in STAR_BY_ID, f"Missing star_id_b {sid_b} in STAR_BY_ID"

    lt_fomalhaut = compute_light_travel_time_description(get_star_by_name("Fomalhaut").median_pc)
    assert "light-years" in str(lt_fomalhaut["description"])
    assert 24.5 <= float(lt_fomalhaut["lightYears"]) <= 25.6

    r_sky = client.get(
        "/api/science/sky-context",
        params={
            "obs_utc_iso": "2026-10-20T22:21:02Z",
            "lat_deg": 31.2021,
            "lon_deg": -3.9458,
        },
    )
    assert r_sky.status_code == 200
    sky_data = r_sky.json()
    assert len(sky_data["stars"]) >= 65
    assert len(sky_data["ephemerides"]) == 6
    assert len(sky_data["constellationEdges"]) >= 50
    assert all("lightTravel" in s for s in sky_data["stars"])
    assert all(e["evidenceClass"] == "predicted" for e in sky_data["ephemerides"])

    # 7. POST /api/science/solve-horizon
    r_horizon = client.post(
        "/api/science/solve-horizon",
        json={
            "width": 1440,
            "height": 780,
            "observedAtUTC": "2026-10-20T22:21:02Z",
            "latDeg": 31.2021,
            "lonDeg": -3.9458,
        },
    )
    assert r_horizon.status_code == 200
    h_sol = r_horizon.json()
    assert h_sol["status"] in ("calibrated", "predicted_only", "approximate")
    assert "wcs" in h_sol
    assert len(h_sol["findings"]) >= 10

    # 8. Expanded All-Sky Voice Commands & Scientific Explanation
    for utterance_text, expected_type, expected_target_substr in [
        ("Take me to Fomalhaut", "space.enter", "fomalhaut"),
        ("Select the Moon", "finding.select", "moon"),
        ("Where is Saturn?", "finding.describe", "saturn"),
        ("How far away is Deneb Algedi?", "finding.describe", "deneb-algedi"),
    ]:
        r_vc = client.post(
            "/api/voice/command",
            json={
                "utterance": utterance_text,
                "context": {
                    "sessionId": "s-frontier",
                    "sourceAssetId": "asset-camp",
                    "viewRevision": 10,
                    "calibrationState": "calibrated",
                },
            },
        )
        assert r_vc.status_code == 200
        vc_data = r_vc.json()
        assert vc_data["status"] == "completed"
        assert vc_data["proposedAction"]["type"] == expected_type
        assert expected_target_substr in vc_data["proposedAction"]["findingId"]
        assert len(vc_data.get("scientificExplanation", "")) > 20

    # 9. GET /api/media/discover (Dynamic Media Discovery without hardcoded filenames)
    r_disc = client.get("/api/media/discover")
    assert r_disc.status_code == 200
    disc_data = r_disc.json()
    assert disc_data["count"] >= 3
    assert len(disc_data["items"]) == disc_data["count"]
    for item in disc_data["items"]:
        assert len(item["sha256"]) == 64
        assert item["width"] > 0 and item["height"] > 0
        assert "observerLocation" in item
        assert -90.0 <= item["observerLocation"]["latDeg"] <= 90.0
        assert -180.0 <= item["observerLocation"]["lonDeg"] <= 180.0
        assert item["observationSolution"] is not None
        assert item["observationSolution"]["status"] in (
            "calibrated",
            "approximate",
            "predicted_only",
        )
        assert len(item["ephemerides"]) >= 6

