"""Camera pose from the horizon and one celestial beacon (the Moon), for a given time and place."""

from __future__ import annotations

import math

import numpy as np
from scipy import ndimage, optimize

from services.science.catalog import (
    _iso_to_julian_date,
    altaz_to_icrs,
    get_all_sky_catalog_stars,
    icrs_to_altaz,
    propagate_star_epoch,
)
from services.science.detector import _to_luma_2d
from services.science.ephemeris import compute_solar_system_ephemerides, moon_parallax_shift_arcsec
from services.science.wcs_models import (
    CelestialFindingRecord,
    ObservationSolution,
    SolutionQuality,
    SourceDetection,
    WCSModel,
)


# The horizon fit is exactly determined (four constraints, four pose parameters), so a pose that
# cannot reproduce its own constraints within this tolerance is rejected instead of reported
MAX_POSE_FIT_RESIDUAL_PX = 3.0
# A predicted star, carried to the date by its proper motion: the horizon transform leaves out nutation and annual
# aberration (at most 37 arcsec, tests/integration/test_sky_transforms.py) and UT1 - UTC (at most 14 arcsec), and
# the pinned positions sit within 3 arcsec of their catalog rows (test_pinned_catalog_matches_tiles.py)
PREDICTED_STAR_UNCERTAINTY_ARCSEC = 55.0


def solve_horizon_and_beacon_pose(
    image: np.ndarray,
    obs_utc_iso: str,
    observer_lat: float,
    observer_lon: float,
    source_asset_id: str = "asset-0",
    source_sha256: str = "0000000000000000000000000000000000000000000000000000000000000000",
    frame_time_sec: float = 0.0,
    solution_id: str = "sol-horizon-beacon",
) -> ObservationSolution:
    """Calibrate camera pointing & WCS from a detected celestial beacon (e.g. Moon) + horizon line.

    The pose needs both: the beacon fixes where the camera pointed and the horizon fixes its tilt
    and scale. Without either, or when one pose cannot reproduce both, camera pointing is unknown
    and the result is `predicted_only`: the sky predicted for the given time and place, with no WCS and no
    pixel positions.
    """
    luma = _to_luma_2d(image)
    h, w = luma.shape

    row_profile = np.percentile(luma, 85, axis=1)
    y_lo, y_hi = int(h * 0.38), int(h * 0.72)
    grad = np.gradient(ndimage.gaussian_filter1d(row_profile, sigma=12.0))
    horizon_y: float | None = None
    if y_hi > y_lo and float(np.max(grad[y_lo:y_hi])) > 1e-4:
        horizon_y = float(y_lo + int(np.argmax(grad[y_lo:y_hi])))

    sky_limit_y = int(h * 0.24) if horizon_y is None else min(int(horizon_y - 40), int(h * 0.24))
    sky_limit_y = max(32, sky_limit_y)
    sky_band = luma[:sky_limit_y, :]
    sky_med = float(np.median(sky_band))
    sky_mad = float(np.median(np.abs(sky_band - sky_med))) * 1.4826
    sky_sigma = max(sky_mad, 1e-4)

    smoothed_sky = ndimage.gaussian_filter(sky_band, sigma=2.5)
    peak_y, peak_x = np.unravel_index(np.argmax(smoothed_sky), smoothed_sky.shape)
    peak_val = float(smoothed_sky[peak_y, peak_x])
    peak_snr = (peak_val - sky_med) / sky_sigma

    ephemerides = compute_solar_system_ephemerides(
        obs_utc_iso=obs_utc_iso,
        lat_deg=observer_lat,
        lon_deg=observer_lon,
        observation_solution_id=solution_id,
    )
    moon_eph = next((b for b in ephemerides if b.name == "Moon"), None)
    moon_up = moon_eph is not None and moon_eph.alt_deg > 5.0
    bright_source = peak_snr >= 10.0 and peak_val > float(np.percentile(luma, 99.0)) * 0.5

    beacon_detection: SourceDetection | None = None
    if moon_up and bright_source:
        r_win = 14
        y0, y1 = max(0, int(peak_y) - r_win), min(sky_limit_y, int(peak_y) + r_win + 1)
        x0, x1 = max(0, int(peak_x) - r_win), min(w, int(peak_x) + r_win + 1)
        patch = np.maximum(sky_band[y0:y1, x0:x1] - sky_med, 0.0)
        tot = float(np.sum(patch))
        if tot > 0.0:
            yy, xx = np.mgrid[y0:y1, x0:x1]
            bx = float(np.sum(xx * patch) / tot)
            by = float(np.sum(yy * patch) / tot)
            # Width from the blob's second moments (2.355 sigma is the Gaussian FWHM); the centroid
            # uncertainty is the usual FWHM / (2.355 * SNR) statistical estimate
            sigma_sq = float(np.sum(((xx - bx) ** 2 + (yy - by) ** 2) * patch) / (2.0 * tot))
            fwhm_px = 2.355 * math.sqrt(max(sigma_sq, 0.0))
            beacon_detection = SourceDetection(
                x_px=bx,
                y_px=by,
                flux=tot,
                snr=peak_snr,
                fwhm_px=fwhm_px,
                centroid_uncertainty_px=fwhm_px / (2.355 * peak_snr),
                asset_id=source_asset_id,
                frame_time_sec=frame_time_sec,
                peak_to_total_ratio=float(np.max(patch)) / tot,
            )

    wcs: WCSModel | None = None
    fit_residuals_px: np.ndarray | None = None
    pose_rejection: str | None = None
    if not moon_up:
        pose_rejection = "The Moon is not above 5° altitude at this time and place"
    elif beacon_detection is None:
        pose_rejection = "No compact bright source found in the upper sky"
    elif horizon_y is None:
        pose_rejection = "No horizon line found"
    else:
        bx, by = beacon_detection.x_px, beacon_detection.y_px
        raw_scale = max(80.0, horizon_y - by)
        scale0 = float(np.clip(moon_eph.alt_deg / raw_scale, 0.025, 0.065))
        cos_alt = max(0.25, math.cos(math.radians(moon_eph.alt_deg)))
        center_az_deg = (moon_eph.az_deg - ((bx - w / 2.0) * scale0 / cos_alt)) % 360.0
        center_alt_deg = float(np.clip((horizon_y - h / 2.0) * scale0, -15.0, 65.0))

        crval_ra0, crval_dec0 = altaz_to_icrs(
            center_alt_deg, center_az_deg, observer_lat, observer_lon, obs_utc_iso
        )
        zenith_ra, zenith_dec = altaz_to_icrs(
            min(89.0, center_alt_deg + 1.0), center_az_deg, observer_lat, observer_lon, obs_utc_iso
        )
        dra_rad = math.radians(zenith_ra - crval_ra0) * math.cos(math.radians(crval_dec0))
        ddec_rad = math.radians(zenith_dec - crval_dec0)
        parallactic0 = math.degrees(math.atan2(dra_rad, ddec_rad))

        hor_left_ra, hor_left_dec = altaz_to_icrs(
            0.0, (center_az_deg - 10.0) % 360.0, observer_lat, observer_lon, obs_utc_iso
        )
        hor_right_ra, hor_right_dec = altaz_to_icrs(
            0.0, (center_az_deg + 10.0) % 360.0, observer_lat, observer_lon, obs_utc_iso
        )

        def _wcs_for(params: np.ndarray, scale_lo: float, scale_hi: float) -> WCSModel:
            return WCSModel(
                crval_ra_deg=float(params[0]) % 360.0,
                crval_dec_deg=max(-89.0, min(89.0, float(params[1]))),
                crpix_x=w / 2.0,
                crpix_y=h / 2.0,
                scale_deg_per_px=max(scale_lo, min(scale_hi, float(params[2]))),
                rotation_deg=float(params[3]),
                k1=0.0,
                k2=0.0,
                parity=-1,
                width=w,
                height=h,
            )

        def _constraint_residuals(cand: WCSModel) -> np.ndarray:
            mx, my = cand.world_to_pixel(moon_eph.ra_deg, moon_eph.dec_deg)
            lx, ly = cand.world_to_pixel(hor_left_ra, hor_left_dec)
            rx, ry = cand.world_to_pixel(hor_right_ra, hor_right_dec)
            return np.array([mx - bx, my - by, ly - horizon_y, ry - horizon_y], dtype=np.float64)

        def _pose_residuals(params: np.ndarray) -> np.ndarray:
            res = _constraint_residuals(_wcs_for(params, 0.015, 0.085))
            return res if np.all(np.isfinite(res)) else np.full(4, 1e4, dtype=np.float64)

        p0 = np.array([crval_ra0, crval_dec0, scale0, parallactic0], dtype=np.float64)
        res_opt = optimize.least_squares(_pose_residuals, p0, method="lm", max_nfev=80)

        # Residuals are measured on the WCS that is actually returned, after its scale clamp
        candidate = _wcs_for(res_opt.x, 0.02, 0.075)
        residuals = np.abs(_constraint_residuals(candidate))
        if not np.all(np.isfinite(residuals)):
            pose_rejection = "The Moon and horizon do not project into one camera pose"
        elif float(np.max(residuals)) > MAX_POSE_FIT_RESIDUAL_PX:
            pose_rejection = (
                f"The Moon and horizon do not fit one camera pose "
                f"(largest residual {float(np.max(residuals)):.1f} px)"
            )
        else:
            wcs = candidate
            fit_residuals_px = residuals

    def _in_camera_field(ra_deg: float, dec_deg: float) -> bool:
        """Whether a predicted object falls in the frame's sky, only when a pose exists. Its pixel
        position is not stored as a detection: nothing was measured there, and the WCS can project it."""
        if wcs is None or horizon_y is None:
            return False
        px, py = wcs.world_to_pixel(ra_deg, dec_deg)
        return (
            not (math.isnan(px) or math.isnan(py))
            and (0.0 <= px < w)
            and (0.0 <= py < min(horizon_y - 24.0, h * 0.46))
        )

    in_field_ids: set[str] = set()

    findings: list[CelestialFindingRecord] = []
    moon_observed = wcs is not None and beacon_detection is not None and moon_eph is not None
    if moon_observed:
        moon_residual_px = float(math.hypot(fit_residuals_px[0], fit_residuals_px[1]))
        findings.append(
            CelestialFindingRecord(
                id="finding-observed-moon",
                observation_solution_id=solution_id,
                evidence_class="observed",
                title="Moon (Observed Sensor Beacon + Ephemeris Lock)",
                subtitle=f"Measured at ({beacon_detection.x_px:.1f}, {beacon_detection.y_px:.1f}) px · pose fit residual {moon_residual_px:.2f} px",
                source_detection=beacon_detection,
                pixel_residual_px=moon_residual_px,
                angular_residual_arcsec=moon_residual_px * wcs.scale_deg_per_px * 3600.0,
                verified_by_holdout=False,
                limitations=(
                    "Single celestial beacon (Moon) + horizon lock; no stars were held out for verification.",
                    "Without a verified WCS the detection has no sky position; the predicted Moon carries the ephemeris position.",
                    "The light centroid of a partly lit or saturated Moon is offset from the disk center; that bias is not modeled.",
                ),
                source_sha256=source_sha256,
            )
        )

    for b in ephemerides:
        if b.alt_deg <= 0.0:
            continue
        in_field = _in_camera_field(b.ra_deg, b.dec_deg)
        if in_field:
            in_field_ids.add(b.id)
        findings.append(
            CelestialFindingRecord(
                id=b.id,
                observation_solution_id=solution_id,
                evidence_class="predicted",
                title=b.title,
                subtitle=b.subtitle + (" · Inside Camera FOV" if in_field else " · Above Horizon"),
                ra_deg=b.ra_deg,
                dec_deg=b.dec_deg,
                alt_deg=b.alt_deg,
                az_deg=b.az_deg,
                uncertainty_arcsec=b.uncertainty_arcsec,
                verified_by_holdout=False,
                limitations=b.limitations,
                source_sha256=source_sha256,
            )
        )

    star_limitation = (
        "Predicted from its catalog position, carried to the date, for the given time and place and the horizon "
        "pose; not claimed as recovered sensor pixels."
        if wcs is not None
        else "Predicted from its catalog position, carried to the date, for the given time and place; camera pointing "
        "is unknown, so not placed in the frame."
    )
    year = 2000.0 + (_iso_to_julian_date(obs_utc_iso) - 2451545.0) / 365.25
    for catalog_star in get_all_sky_catalog_stars():
        star = propagate_star_epoch(catalog_star, year)
        alt, az = icrs_to_altaz(
            star.ra_deg, star.dec_deg, observer_lat, observer_lon, obs_utc_iso
        )
        if alt <= 0.0:
            continue
        in_field = _in_camera_field(star.ra_deg, star.dec_deg)
        if in_field:
            in_field_ids.add(f"finding-predicted-{star.id}")
        findings.append(
            CelestialFindingRecord(
                id=f"finding-predicted-{star.id}",
                observation_solution_id=solution_id,
                evidence_class="predicted",
                title=f"{star.common_name} ({star.bayer_designation})",
                subtitle=(
                    f"Predicted Star ({star.constellation}) · {star.median_pc:.1f} pc · "
                    f"Alt {alt:+.1f}° Az {az:.1f}°"
                    + (" · Inside Camera FOV" if in_field else " · Above Horizon")
                ),
                ra_deg=star.ra_deg,
                dec_deg=star.dec_deg,
                alt_deg=alt,
                az_deg=az,
                uncertainty_arcsec=PREDICTED_STAR_UNCERTAINTY_ARCSEC,
                catalog_star=star,
                verified_by_holdout=False,
                limitations=(star_limitation,),
                source_sha256=source_sha256,
            )
        )

    # Order findings: 1) observed beacons, 2) stars/planets inside camera FOV, 3) brighter above-horizon context
    findings.sort(
        key=lambda f: (
            0 if f.evidence_class == "observed" else (1 if f.id in in_field_ids else 2),
            f.catalog_star.mag if f.catalog_star is not None else -2.0,
        )
    )

    moment = f"{observer_lat:.4f}°, {observer_lon:.4f}° @ {obs_utc_iso}"
    if wcs is not None and fit_residuals_px is not None and beacon_detection is not None and horizon_y is not None:
        median_px = float(np.median(fit_residuals_px))
        quality = SolutionQuality(
            detected_candidate_count=1,
            matched_inlier_count=1,
            held_out_inliers=0,
            median_residual_px=median_px,
            p95_residual_px=float(np.percentile(fit_residuals_px, 95.0)),
            median_residual_arcsec=median_px * wcs.scale_deg_per_px * 3600.0,
            passed_independent_verification=False,
        )
        status = "approximate"
        warnings = [
            f"Pose from the Moon at ({beacon_detection.x_px:.1f}, {beacon_detection.y_px:.1f}) px and the "
            f"horizon at row {horizon_y:.0f} ({moment}). Four constraints fit four pose parameters, so "
            "near-zero residuals do not measure accuracy, and no stars were held out for verification."
            + (
                f" The Moon's predicted position is geocentric: from this place it appears about "
                f"{moon_parallax_shift_arcsec(moon_eph.distance_au, moon_eph.alt_deg)[1]:,.0f} arcsec lower, "
                "and the pose carries that offset."
                if moon_eph is not None
                else ""
            )
        ]
    else:
        quality = SolutionQuality(
            detected_candidate_count=1 if beacon_detection is not None else 0,
            matched_inlier_count=0,
            held_out_inliers=0,
            median_residual_px=999.0,
            p95_residual_px=999.0,
            median_residual_arcsec=9999.0,
            passed_independent_verification=False,
        )
        status = "predicted_only"
        warning = (
            f"{pose_rejection}, so camera pointing is unknown. Showing the sky predicted from {moment}, "
            "not placed in the frame."
        )
        if beacon_detection is not None:
            warning += (
                f" A bright source at ({beacon_detection.x_px:.0f}, {beacon_detection.y_px:.0f}) px "
                "was not confirmed as the Moon."
            )
        warnings = [warning]

    return ObservationSolution(
        id=solution_id,
        source_asset_id=source_asset_id,
        source_sha256=source_sha256,
        frame_time_sec=frame_time_sec,
        revision=1,
        status=status,
        observed_at_utc=obs_utc_iso,
        observer_lat=observer_lat,
        observer_lon=observer_lon,
        wcs=wcs,
        quality=quality,
        findings=findings,
        warnings=warnings,
    )
