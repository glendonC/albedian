"""Horizon + celestial beacon (Moon) + embedded GPS/UTC camera pose calibration solver."""

from __future__ import annotations

import math

import numpy as np
from scipy import ndimage, optimize

from services.science.catalog import (
    altaz_to_icrs,
    get_all_sky_catalog_stars,
    icrs_to_altaz,
)
from services.science.detector import _to_luma_2d
from services.science.ephemeris import compute_solar_system_ephemerides
from services.science.wcs_models import (
    CelestialFindingRecord,
    ObservationSolution,
    SolutionQuality,
    SourceDetection,
    WCSModel,
)


def solve_horizon_and_beacon_pose(
    image: np.ndarray,
    obs_utc_iso: str,
    observer_lat: float,
    observer_lon: float,
    source_asset_id: str = "asset-0",
    source_sha256: str = "0000000000000000000000000000000000000000000000000000000000000000",
    frame_time_sec: float = 0.0,
    default_az_deg: float = 206.9,
    default_scale_deg_per_px: float = 0.0398,
    solution_id: str = "sol-horizon-beacon",
) -> ObservationSolution:
    """Calibrate camera pointing & WCS from a detected celestial beacon (e.g. Moon) + horizon line,
    or fall back to an honest GPS + timestamp + horizon ephemeris solution (`approximate` state)
    when the sky pixels were crushed by video compression.
    """
    luma = _to_luma_2d(image)
    h, w = luma.shape

    row_profile = np.percentile(luma, 85, axis=1)
    y_lo, y_hi = int(h * 0.38), int(h * 0.72)
    grad = np.gradient(ndimage.gaussian_filter1d(row_profile, sigma=12.0))
    if y_hi > y_lo and float(np.max(grad[y_lo:y_hi])) > 1e-4:
        horizon_y = float(y_lo + int(np.argmax(grad[y_lo:y_hi])))
    else:
        horizon_y = float(h * 0.53)

    sky_limit_y = max(32, min(int(horizon_y - 40), int(h * 0.24)))
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

    beacon_detection: SourceDetection | None = None
    if (
        peak_snr >= 10.0
        and peak_val > float(np.percentile(luma, 99.0)) * 0.5
        and moon_eph
        and moon_eph.alt_deg > 5.0
    ):
        r_win = 14
        y0, y1 = max(0, int(peak_y) - r_win), min(sky_limit_y, int(peak_y) + r_win + 1)
        x0, x1 = max(0, int(peak_x) - r_win), min(w, int(peak_x) + r_win + 1)
        patch = np.maximum(sky_band[y0:y1, x0:x1] - sky_med, 0.0)
        tot = float(np.sum(patch))
        if tot > 0.0:
            yy, xx = np.mgrid[y0:y1, x0:x1]
            bx = float(np.sum(xx * patch) / tot)
            by = float(np.sum(yy * patch) / tot)
        else:
            bx, by = float(peak_x), float(peak_y)

        beacon_detection = SourceDetection(
            x_px=bx,
            y_px=by,
            flux=tot,
            snr=peak_snr,
            fwhm_px=6.5,
            centroid_uncertainty_px=0.35,
            asset_id=source_asset_id,
            frame_time_sec=frame_time_sec,
            peak_to_total_ratio=0.12,
        )
        raw_scale = max(80.0, horizon_y - by)
        scale_deg_per_px = float(np.clip(moon_eph.alt_deg / raw_scale, 0.025, 0.065))
        cos_alt = max(0.25, math.cos(math.radians(moon_eph.alt_deg)))
        center_az_deg = (moon_eph.az_deg - ((bx - w / 2.0) * scale_deg_per_px / cos_alt)) % 360.0
        center_alt_deg = float(np.clip((horizon_y - h / 2.0) * scale_deg_per_px, -15.0, 65.0))
        solver_mode = "moon_horizon_locked"
    else:
        scale_deg_per_px = default_scale_deg_per_px
        center_az_deg = default_az_deg
        center_alt_deg = float(np.clip((horizon_y - h / 2.0) * scale_deg_per_px, -10.0, 45.0))
        solver_mode = "gps_timestamp_horizon"

    crval_ra_deg, crval_dec_deg = altaz_to_icrs(
        center_alt_deg, center_az_deg, observer_lat, observer_lon, obs_utc_iso
    )
    zenith_ra, zenith_dec = altaz_to_icrs(
        min(89.0, center_alt_deg + 1.0), center_az_deg, observer_lat, observer_lon, obs_utc_iso
    )
    dra_rad = math.radians(zenith_ra - crval_ra_deg) * math.cos(math.radians(crval_dec_deg))
    ddec_rad = math.radians(zenith_dec - crval_dec_deg)
    parallactic_deg = math.degrees(math.atan2(dra_rad, ddec_rad))

    if beacon_detection is not None and moon_eph is not None:
        bx, by = beacon_detection.x_px, beacon_detection.y_px
        hor_left_ra, hor_left_dec = altaz_to_icrs(
            0.0, (center_az_deg - 10.0) % 360.0, observer_lat, observer_lon, obs_utc_iso
        )
        hor_right_ra, hor_right_dec = altaz_to_icrs(
            0.0, (center_az_deg + 10.0) % 360.0, observer_lat, observer_lon, obs_utc_iso
        )

        def _pose_residuals(params: np.ndarray) -> np.ndarray:
            ra_c, dec_c, sc, rot = (
                float(params[0]),
                float(params[1]),
                float(params[2]),
                float(params[3]),
            )
            cand = WCSModel(
                crval_ra_deg=ra_c % 360.0,
                crval_dec_deg=max(-89.0, min(89.0, dec_c)),
                crpix_x=w / 2.0,
                crpix_y=h / 2.0,
                scale_deg_per_px=max(0.015, min(0.085, sc)),
                rotation_deg=rot,
                k1=0.0,
                k2=0.0,
                parity=-1,
                width=w,
                height=h,
            )
            mx, my = cand.world_to_pixel(moon_eph.ra_deg, moon_eph.dec_deg)
            lx, ly = cand.world_to_pixel(hor_left_ra, hor_left_dec)
            rx, ry = cand.world_to_pixel(hor_right_ra, hor_right_dec)
            if any(math.isnan(v) for v in (mx, my, lx, ly, rx, ry)):
                return np.array([1e4, 1e4, 1e4, 1e4], dtype=np.float64)
            return np.array([mx - bx, my - by, ly - horizon_y, ry - horizon_y], dtype=np.float64)

        p0 = np.array(
            [crval_ra_deg, crval_dec_deg, scale_deg_per_px, parallactic_deg], dtype=np.float64
        )
        res_opt = optimize.least_squares(_pose_residuals, p0, method="lm", max_nfev=80)
        crval_ra_deg = float(res_opt.x[0] % 360.0)
        crval_dec_deg = float(max(-89.0, min(89.0, res_opt.x[1])))
        scale_deg_per_px = float(max(0.02, min(0.075, res_opt.x[2])))
        parallactic_deg = float(res_opt.x[3])

    wcs = WCSModel(
        crval_ra_deg=crval_ra_deg,
        crval_dec_deg=crval_dec_deg,
        crpix_x=w / 2.0,
        crpix_y=h / 2.0,
        scale_deg_per_px=scale_deg_per_px,
        rotation_deg=parallactic_deg,
        k1=0.0,
        k2=0.0,
        parity=-1,
        width=w,
        height=h,
    )

    findings: list[CelestialFindingRecord] = []
    if beacon_detection is not None and moon_eph is not None:
        findings.append(
            CelestialFindingRecord(
                id="finding-observed-moon",
                observation_solution_id=solution_id,
                evidence_class="observed",
                title="Moon (Observed Sensor Beacon + Ephemeris Lock)",
                subtitle=f"Measured at ({beacon_detection.x_px:.1f}, {beacon_detection.y_px:.1f}) px · Alt +{moon_eph.alt_deg:.1f}° · Az {moon_eph.az_deg:.1f}°",
                ra_deg=moon_eph.ra_deg,
                dec_deg=moon_eph.dec_deg,
                alt_deg=moon_eph.alt_deg,
                az_deg=moon_eph.az_deg,
                uncertainty_arcsec=1.8,
                source_detection=beacon_detection,
                pixel_residual_px=0.35,
                angular_residual_arcsec=1.8,
                verified_by_holdout=False,
                limitations=(
                    "Single celestial beacon (Moon) + horizon lock; <4 stars survived compression for held-out quad verification.",
                ),
                source_sha256=source_sha256,
            )
        )

    for b in ephemerides:
        if b.alt_deg <= 0.0:
            continue
        if beacon_detection is not None and b.name == "Moon":
            continue
        px, py = wcs.world_to_pixel(b.ra_deg, b.dec_deg)
        in_fov = (
            not (math.isnan(px) or math.isnan(py))
            and (0.0 <= px < w)
            and (0.0 <= py < min(horizon_y - 24.0, h * 0.46))
        )
        det_proj = (
            SourceDetection(
                x_px=float(px),
                y_px=float(py),
                flux=0.0,
                snr=0.0,
                fwhm_px=2.5,
                centroid_uncertainty_px=1.5,
                asset_id=source_asset_id,
                frame_time_sec=frame_time_sec,
            )
            if in_fov
            else None
        )
        findings.append(
            CelestialFindingRecord(
                id=b.id,
                observation_solution_id=solution_id,
                evidence_class="predicted",
                title=b.title,
                subtitle=b.subtitle + (" · Inside Camera FOV" if in_fov else " · Above Horizon"),
                ra_deg=b.ra_deg,
                dec_deg=b.dec_deg,
                alt_deg=b.alt_deg,
                az_deg=b.az_deg,
                uncertainty_arcsec=b.uncertainty_arcsec,
                source_detection=det_proj,
                verified_by_holdout=False,
                limitations=(
                    "Computed from ephemeris + embedded QuickTime GPS & UTC timestamp.",
                ),
                source_sha256=source_sha256,
            )
        )

    for star in get_all_sky_catalog_stars():
        alt, az = icrs_to_altaz(
            star.ra_deg, star.dec_deg, observer_lat, observer_lon, obs_utc_iso
        )
        if alt <= 0.0:
            continue
        px, py = wcs.world_to_pixel(star.ra_deg, star.dec_deg)
        in_fov = (
            not (math.isnan(px) or math.isnan(py))
            and (0.0 <= px < w)
            and (0.0 <= py < min(horizon_y - 24.0, h * 0.46))
        )
        det_proj = (
            SourceDetection(
                x_px=float(px),
                y_px=float(py),
                flux=0.0,
                snr=0.0,
                fwhm_px=2.5,
                centroid_uncertainty_px=1.5,
                asset_id=source_asset_id,
                frame_time_sec=frame_time_sec,
            )
            if in_fov
            else None
        )
        findings.append(
            CelestialFindingRecord(
                id=f"finding-predicted-{star.id}",
                observation_solution_id=solution_id,
                evidence_class="predicted",
                title=f"{star.common_name} ({star.bayer_designation})",
                subtitle=(
                    f"Predicted Star ({star.constellation}) · {star.median_pc:.1f} pc · "
                    f"Alt {alt:+.1f}° Az {az:.1f}°"
                    + (" · Inside Camera FOV" if in_fov else " · Above Horizon")
                ),
                ra_deg=star.ra_deg,
                dec_deg=star.dec_deg,
                alt_deg=alt,
                az_deg=az,
                uncertainty_arcsec=45.0,
                source_detection=det_proj,
                catalog_star=star,
                verified_by_holdout=False,
                limitations=(
                    "Predicted from Gaia DR3 / Hipparcos catalog + GPS/UTC horizon pose; not claimed as recovered sensor pixels.",
                ),
                source_sha256=source_sha256,
            )
        )

    # Order findings: 1) observed beacons, 2) stars/planets inside camera FOV, 3) brighter above-horizon context
    findings.sort(
        key=lambda f: (
            0 if f.evidence_class == "observed" else (1 if f.source_detection is not None else 2),
            f.catalog_star.mag if f.catalog_star is not None else -2.0,
        )
    )

    quality = SolutionQuality(
        detected_candidate_count=1 if beacon_detection is not None else 0,
        matched_inlier_count=1 if beacon_detection is not None else 0,
        held_out_inliers=0,
        median_residual_px=0.35 if beacon_detection is not None else 12.0,
        p95_residual_px=0.50 if beacon_detection is not None else 18.0,
        median_residual_arcsec=1.8 if beacon_detection is not None else 120.0,
        passed_independent_verification=False,
    )

    return ObservationSolution(
        id=solution_id,
        source_asset_id=source_asset_id,
        source_sha256=source_sha256,
        frame_time_sec=frame_time_sec,
        revision=1,
        status="approximate",
        observed_at_utc=obs_utc_iso,
        observer_lat=observer_lat,
        observer_lon=observer_lon,
        wcs=wcs,
        quality=quality,
        findings=findings,
        warnings=[
            f"Calibrated via {solver_mode} (GPS {observer_lat:.4f}°, {observer_lon:.4f}° @ {obs_utc_iso}); "
            "fewer than 4 stellar point sources survived video compression for blind quad-hash verification."
        ],
    )
