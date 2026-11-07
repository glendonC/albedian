"""Integration and Hypothesis property-based verification suite for Albedian's
scientific astrometry, plate solver, proper-motion propagation, astronomy-aware
media restoration runtime, FFmpeg audio-preserving export, and FastAPI service.

Covers:
- PBT Suite #7 (`test_pbt_scientific_coordinates_and_distances`), and a broken catalog distance refused in
  the star's own distance's words (`test_a_broken_distance_is_refused_in_the_stars_own_words`)
- EXP-01 (`test_exp01_consumer_photo_plate_solving_and_negative_rejection`)
- EXP-02 (`test_exp02_low_light_video_temporal_stack_calibration`)
- EXP-03 (`test_exp03_catalog_alignment_and_false_artifact_rejection`)
- EXP-04 (`test_exp04_astronomy_aware_point_protection_vs_conventional_denoise`)
- EXP-08 (`test_exp08_proper_motion_and_historical_epoch_1026ce`)
- EXP-10 (`test_exp10_video_restoration_and_ffmpeg_audio_preserving_export`)
- FastAPI service endpoints (`test_fastapi_endpoints`), and media discovery over the owner's clips
  (`test_media_discovery_reads_the_owners_clips_without_hardcoded_filenames`, marked owner_media)
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import subprocess

from fastapi.testclient import TestClient
from hypothesis import given, settings, strategies as st
import numpy as np
import pytest

from services.api import app
from services.media.runtime import MediaExperimentRuntime, compute_file_sha256
from services.science.catalog import (
    ALL_SKY_CATALOG_STARS,
    CONSTELLATION_EDGES,
    PINNED_CATALOG_STARS,
    STAR_BY_ID,
    angular_separation_deg,
    catalog_cartesian_pc,
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

# Synthetic moments, passed explicitly because the solvers have no default place or time. The place points
# at nobody: the open North Atlantic, about 1,321 km from Flores in the Azores. PLATE_MOMENT has Orion high
# on a moonless night; MOON_MOMENT has the Moon at 33 degrees altitude, azimuth 222 degrees (repo ephemeris).
PLATE_MOMENT = {"obs_utc_iso": "2030-01-01T02:00:00Z", "observer_lat": 30.0, "observer_lon": -40.0}


def _sources(frame: np.ndarray) -> dict[str, str]:
    """The id and SHA-256 a recording would carry, here of the synthetic frame the test builds."""
    digest = hashlib.sha256(np.ascontiguousarray(frame).tobytes()).hexdigest()
    return {"source_asset_id": f"synthetic-{digest[:12]}", "source_sha256": digest}


def _request_sources(frame: np.ndarray) -> dict[str, str]:
    sources = _sources(frame)
    return {"sourceAssetId": sources["source_asset_id"], "sourceSha256": sources["source_sha256"]}
MOON_MOMENT = {"obs_utc_iso": "2030-11-01T22:32:00Z", "observer_lat": 30.0, "observer_lon": -40.0}


def _as_request(moment: dict) -> dict:
    """The same moment under the API's field names."""
    return {"observedAtUTC": moment["obs_utc_iso"], "latDeg": moment["observer_lat"], "lonDeg": moment["observer_lon"]}


# ---------------------------------------------------------------------------
# PBT Suite #7: Scientific Coordinates, WCS Inversion & Catalog Distance Bounds
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
    and catalog distance interval invariants across catalog stars and epochs.
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

    # 3. Catalog distance ordering and epoch propagation invariants
    assert len(PINNED_CATALOG_STARS) >= 16
    base_star = PINNED_CATALOG_STARS[star_idx]
    assert 0.0 < base_star.lower_pc <= base_star.median_pc <= base_star.upper_pc

    prop_star = propagate_star_epoch(base_star, epoch_year)
    assert 0.0 < prop_star.lower_pc <= prop_star.median_pc <= prop_star.upper_pc
    cx, cy, cz = catalog_cartesian_pc(prop_star)
    dist_3d = math.sqrt(cx * cx + cy * cy + cz * cz)
    assert abs(dist_3d - prop_star.median_pc) < 1e-6


@pytest.mark.parametrize(
    "break_bounds",
    [
        lambda star: {"lower_pc": 0.0},
        lambda star: {"lower_pc": star.median_pc * 2.0},
        lambda star: {"upper_pc": star.median_pc / 2.0},
    ],
    ids=["lower-zero", "lower-above-median", "upper-below-median"],
)
@pytest.mark.parametrize("name", ["Rigel", "Tabit"])
def test_a_broken_distance_is_refused_in_the_stars_own_words(name: str, break_bounds) -> None:
    # Rigel's distance is 1000/parallax from Hipparcos 2007 and Tabit's the Bailer-Jones et al. (2021) estimate
    # from Gaia DR3: a broken interval is refused in the star's own distance's words, not one method's for both
    star = get_star_by_name(name)
    with pytest.raises(ValueError) as refused:
        catalog_cartesian_pc(replace(star, **break_bounds(star)))
    message = str(refused.value)
    assert name in message and star.distance_source in message
    assert ("Bailer-Jones" in message) is (star.provider == "Gaia DR3")


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
        **PLATE_MOMENT,
        **_sources(frame),
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
    black_sol = solve_and_verify_field(black_dets, width=width, height=height, **PLATE_MOMENT, **_sources(black_frame))
    assert black_sol.status in ("predicted_only", "unresolved")
    assert black_sol.status != "calibrated"
    assert black_sol.held_out_inliers == 0

    # Negative Fixture 2: Pure random Gaussian sensor noise frame
    rng = np.random.default_rng(999)
    noise_frame = rng.normal(loc=20.0, scale=3.0, size=(height, width))
    noise_dets = detect_point_sources(noise_frame, snr_threshold=3.5)
    noise_sol = solve_and_verify_field(noise_dets, width=width, height=height, **PLATE_MOMENT, **_sources(noise_frame))
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
    single_sol = solve_and_verify_field(single_dets, width=width, height=height, **PLATE_MOMENT, **_sources(frames[0]))
    assert single_sol.status == "unresolved"

    # Align and stack the 10 frames
    stack_result = stack_video_frames_for_calibration(frames, sky_mask=sky_mask)
    assert stack_result.snr_boost_factor > 2.2

    stacked_dets = detect_point_sources(
        stack_result.composite_luma, sky_mask=sky_mask, snr_threshold=4.8
    )
    assert len(stacked_dets) >= 10

    stacked_sol = solve_and_verify_field(
        stacked_dets, width=width, height=height, **PLATE_MOMENT, **_sources(stack_result.composite_luma)
    )
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
    solution = solve_and_verify_field(detections, width=width, height=height, **PLATE_MOMENT, **_sources(corrupted))

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

    # Every injected hot pixel that was detected as an observed source is explicitly named a hot-pixel impulse
    hot_findings = [
        f
        for f in solution.findings
        if f.evidence_class == "observed"
        and f.source_detection is not None
        and any(math.hypot(f.source_detection.x_px - hx, f.source_detection.y_px - hy) <= 2.0 for hx, hy in hot_pixel_coords)
    ]
    assert len(hot_findings) >= 6
    for f in hot_findings:
        assert f.limitations[0].startswith("Skipped by catalog matching as a hot-pixel impulse (")
    assert any("skipped as hot-pixel impulse(s)" in w for w in solution.warnings)

    # Single-pixel impulse on a noiseless background has r2_moment == 0 and reports fwhm_px == 0.0 (no 0.55 floor),
    # and a high-peak-ratio compact source with non-zero wings reports its second-moment FWHM without the 0.85 cap
    clean_bg = np.full((64, 64), 20.0, dtype=np.float64)
    rng_bg = np.random.default_rng(7)
    clean_bg += rng_bg.normal(0.0, 0.2, size=clean_bg.shape)
    impulse_img = clean_bg.copy()
    impulse_img[32, 32] += 120.0
    impulse_dets = detect_point_sources(impulse_img, snr_threshold=4.0)
    assert len(impulse_dets) == 1
    assert impulse_dets[0].fwhm_px < 0.55

    # Peak holds > 68% of positive 5x5 flux while outer ring (r=2) gives second-moment FWHM > 0.85 px
    wing_img = clean_bg.copy()
    wing_img[32, 32] += 200.0
    for dy, dx in ((-2, 0), (2, 0), (0, -2), (0, 2)):
        wing_img[32 + dy, 32 + dx] += 18.0
    wing_dets = detect_point_sources(wing_img, snr_threshold=4.0)
    assert len(wing_dets) == 1
    assert wing_dets[0].peak_to_total_ratio > 0.68
    assert wing_dets[0].fwhm_px > 0.85

    synth_doc = render_synthetic_starfield.__doc__ or ""
    assert "realistic" not in synth_doc.lower()
    assert "-0.14" in synth_doc


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
    assert export_manifest.duration_drift_ms is not None and export_manifest.duration_drift_ms <= 35.0
    assert export_manifest.audio_sync_offset_ms is not None and export_manifest.audio_sync_offset_ms < 1.0

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

    # 2. POST /api/science/solve (the test sends its own synthetic detections; the API invents none)
    synth_frame, _, _ = render_synthetic_starfield(width=960, height=540)
    solve_moment = {**_as_request(PLATE_MOMENT), **_request_sources(synth_frame)}
    assert client.post("/api/science/solve", json={"width": 960, "height": 540, **solve_moment}).status_code == 422
    synth_dets = [
        {
            "xPx": d.x_px,
            "yPx": d.y_px,
            "flux": d.flux,
            "snr": d.snr,
            "fwhmPx": d.fwhm_px,
            "centroidUncertaintyPx": d.centroid_uncertainty_px,
            "peakToTotalRatio": d.peak_to_total_ratio,
        }
        for d in detect_point_sources(synth_frame)
    ]
    unmeasured = [{k: v for k, v in synth_dets[0].items() if k != "snr"}]
    assert client.post(
        "/api/science/solve", json={"width": 960, "height": 540, "detections": unmeasured, **solve_moment}
    ).status_code == 422
    r_solve = client.post(
        "/api/science/solve",
        json={"width": 960, "height": 540, "detections": synth_dets, **solve_moment},
    )
    assert r_solve.status_code == 200
    s_data = r_solve.json()
    assert s_data["status"] == "calibrated"
    assert s_data["held_out_inliers"] >= 6
    assert s_data["median_residual_px"] <= 1.5

    # 3. POST /api/science/ephemeris
    r_ephem = client.post(
        "/api/science/ephemeris",
        json={**_as_request(PLATE_MOMENT), "bodies": ["Jupiter", "Uranus", "Saturn", "Mars", "Venus", "Moon"]},
    )
    assert r_ephem.status_code == 200
    e_data = r_ephem.json()
    assert len(e_data["findings"]) == 6
    assert all(f["evidenceClass"] == "predicted" for f in e_data["findings"])
    alt_deg, az_deg = icrs_to_altaz(
        88.7929, 7.4071, PLATE_MOMENT["observer_lat"], PLATE_MOMENT["observer_lon"], PLATE_MOMENT["obs_utc_iso"]
    )
    assert -90.0 <= alt_deg <= 90.0
    assert 0.0 <= az_deg < 360.0

    # 4. POST /api/media/restore (explicit synthetic frame; the API no longer makes one up)
    assert client.post(
        "/api/media/restore", json={"profile": "star_protected", "preserveStars": True, "sourceAssetId": "synthetic-restore"}
    ).status_code == 422
    restore_frame, _, _ = render_synthetic_starfield(width=320, height=180, read_noise_sigma=3.2, seed=101)
    r_restore = client.post(
        "/api/media/restore",
        json={
            "profile": "star_protected", "preserveStars": True, "image_luma": restore_frame.tolist(),
            "sourceAssetId": "synthetic-restore",
        },
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
    # The backend applies nothing: a checked action is proposed for the app's reducer, with no state version
    assert v_ok["status"] == "proposed"
    assert "newStateVersion" not in v_ok
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
            "obs_utc_iso": MOON_MOMENT["obs_utc_iso"],
            "lat_deg": MOON_MOMENT["observer_lat"],
            "lon_deg": MOON_MOMENT["observer_lon"],
        },
    )
    assert r_sky.status_code == 200
    sky_data = r_sky.json()
    assert len(sky_data["stars"]) >= 65
    assert len(sky_data["ephemerides"]) == 6
    assert len(sky_data["constellationEdges"]) >= 50
    assert all("lightTravel" in s for s in sky_data["stars"])
    assert all(e["evidenceClass"] == "predicted" for e in sky_data["ephemerides"])

    # 7. POST /api/science/solve-horizon. The test draws its own narrow portrait frame (Moon blob
    # above a foreground at 55% height) so the Moon's height above the horizon implies a lens
    # scale inside the solver's range; the API no longer plants a Moon in a blank frame.
    horizon_frame = np.zeros((1792, 64), dtype=np.float64)
    horizon_frame[int(1792 * 0.55) :, :] = 40.0
    horizon_frame[118:126, 28:36] = 240.0
    horizon_moment = {**_as_request(MOON_MOMENT), **_request_sources(horizon_frame)}
    assert client.post(
        "/api/science/solve-horizon", json={"width": 64, "height": 1792, **horizon_moment}
    ).status_code == 422
    r_horizon = client.post(
        "/api/science/solve-horizon",
        json={"width": 64, "height": 1792, "image_luma": horizon_frame.tolist(), **horizon_moment},
    )
    assert r_horizon.status_code == 200
    h_sol = r_horizon.json()
    assert h_sol["status"] == "approximate"
    assert "wcs" in h_sol
    # The pose's fit residual stays on the Moon it was fitted to: no star was held out, so no held-out residual
    assert "medianResidualPx" not in h_sol["quality"]
    assert h_sol["quality"]["passedIndependentVerification"] is False
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
                    "sourceAssetId": "asset-synthetic",
                    "viewRevision": 10,
                    "calibrationState": "calibrated",
                },
            },
        )
        assert r_vc.status_code == 200
        vc_data = r_vc.json()
        assert vc_data["status"] == "proposed"
        assert vc_data["proposedAction"]["type"] == expected_type
        assert expected_target_substr in vc_data["proposedAction"]["findingId"]
        assert len(vc_data.get("scientificExplanation", "")) > 20


@pytest.mark.owner_media
def test_media_discovery_reads_the_owners_clips_without_hardcoded_filenames() -> None:
    """GET /api/media/discover over the owner's clips in data/uploads, which are not in git."""
    client = TestClient(app)
    r_disc = client.get("/api/media/discover")
    assert r_disc.status_code == 200
    disc_data = r_disc.json()
    assert disc_data["count"] >= 3
    # Flags with fixed messages where a failing assert would print a clip's item, place or ephemerides
    listed, count = len(disc_data["items"]), disc_data["count"]
    assert listed == count, "the item list and the count disagree"
    for item in disc_data["items"]:
        assert len(item["sha256"]) == 64
        assert item["width"] > 0 and item["height"] > 0
        located = "observerLocation" in item
        assert located, "a real clip has no observer location"
        lat_ok = -90.0 <= item["observerLocation"]["latDeg"] <= 90.0
        assert lat_ok, "a real clip's latitude is out of range"
        lon_ok = -180.0 <= item["observerLocation"]["lonDeg"] <= 180.0
        assert lon_ok, "a real clip's longitude is out of range"
        assert item["observationSolution"] is not None
        assert item["observationSolution"]["status"] in (
            "calibrated",
            "approximate",
            "predicted_only",
        )
        # Discovery fits the horizon and the Moon; it never runs the plate solver, so it never names it
        assert item["observationSolution"]["solver"]["name"] == "AlbedianHorizonMoonPose"
        predicted = len(item["ephemerides"])
        assert predicted >= 6, "a real clip has fewer than six ephemerides"
        assert {e["evidence"]["sourceAssetSha256"] for e in item["ephemerides"]} == {item["sha256"]}

