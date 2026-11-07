"""Science routes: sky context, radiometry audit, plate and horizon solves, and ephemerides."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.science.catalog import (
    ALL_SKY_CATALOG_STARS,
    CONSTELLATION_EDGES,
    catalog_cartesian_pc,
    compute_light_travel_time_description,
    icrs_to_altaz,
)
from services.science.ephemeris import (
    EphemerisOutOfRange,
    compute_solar_system_ephemerides,
    require_ephemeris_range,
)
from services.science.stellar_parameters import gaia_stellar_parameters
from services.science.radiometry import compute_lunar_phase_and_illumination, predict_sky_brightness
from services.science.solver import (
    SourceDetection,
    detect_point_sources,
    solve_and_verify_field,
    solve_horizon_and_beacon_pose,
)
from services.science.wcs_models import require_source_sha256

router = APIRouter()


class SolveRequest(BaseModel):
    """Request payload for `POST /api/science/solve`."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    # The image's size: taken from image_luma's own shape, and required with detections
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    detections: list[dict[str, Any]] | None = None
    image_luma: list[list[float]] | None = None
    hint_ra_deg: float | None = Field(default=None, alias="hintRaDeg")
    hint_dec_deg: float | None = Field(default=None, alias="hintDecDeg")
    obs_utc_iso: str = Field(alias="observedAtUTC")
    observer_lat: float = Field(alias="latDeg")
    observer_lon: float = Field(alias="lonDeg")
    # The recording the input comes from, and its SHA-256: the client hashed the file, the server never sees it
    source_asset_id: str = Field(alias="sourceAssetId", min_length=1)
    source_sha256: str = Field(alias="sourceSha256")

    @field_validator("source_sha256")
    @classmethod
    def _real_sha256(cls, value: str) -> str:
        return require_source_sha256(value)


class EphemerisRequest(BaseModel):
    """Request payload for `POST /api/science/ephemeris`."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    obs_utc_iso: str = Field(alias="observedAtUTC")
    lat_deg: float = Field(alias="latDeg")
    lon_deg: float = Field(alias="lonDeg")
    bodies: list[str] = Field(
        default_factory=lambda: ["Jupiter", "Uranus", "Saturn", "Mars", "Venus", "Moon"]
    )


_NO_INPUT_DETAIL = "Send {inputs}; the API does not substitute synthetic input for a missing observation."


def _within_ephemeris_range(obs_utc_iso: str) -> None:
    """A 422 naming the span when the moment lies outside the ephemeris' 1800 to 2050."""
    try:
        require_ephemeris_range(obs_utc_iso)
    except EphemerisOutOfRange as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _measured(detection: dict[str, Any], *keys: str) -> float:
    """Return a detection measurement by any of its accepted keys; a missing one is a client error."""
    for key in keys:
        if detection.get(key) is not None:
            return float(detection[key])
    raise HTTPException(status_code=422, detail=f"Each detection needs a measured `{keys[-1]}`.")


@router.get("/api/science/sky-context")
def get_science_sky_context(
    obs_utc_iso: str = Query(...),
    lat_deg: float = Query(..., ge=-90.0, le=90.0),
    lon_deg: float = Query(..., ge=-180.0, le=180.0),
) -> dict[str, Any]:
    """Compute full 360-degree topocentric sky context for all 70+ navigational stars,
    Solar System ephemerides, and constellation stick-figure edges.
    """
    _within_ephemeris_range(obs_utc_iso)
    stars_out: list[dict[str, Any]] = []
    for st in ALL_SKY_CATALOG_STARS:
        alt_deg, az_deg = icrs_to_altaz(st.ra_deg, st.dec_deg, lat_deg, lon_deg, obs_utc_iso)
        cx, cy, cz = catalog_cartesian_pc(st)
        light_travel = compute_light_travel_time_description(st.median_pc)
        star_entry: dict[str, Any] = {
            "id": st.id,
            "commonName": st.common_name,
            "bayerDesignation": st.bayer_designation,
            "constellation": st.constellation,
            "provider": st.provider,
            "catalogSourceId": st.catalog_source_id,
            "raDeg": float(st.ra_deg),
            "decDeg": float(st.dec_deg),
            "altDeg": float(alt_deg),
            "azDeg": float(az_deg),
            "aboveHorizon": bool(alt_deg >= 0.0),
            "mag": float(st.mag),
            "properMotionRaMasYr": float(st.pm_ra_mas_yr),
            "properMotionDecMasYr": float(st.pm_dec_mas_yr),
            "distancePc": {
                "lower": float(st.lower_pc),
                "median": float(st.median_pc),
                "upper": float(st.upper_pc),
                "method": st.distance_method,
            },
            "cartesianParsecICRS": [float(cx), float(cy), float(cz)],
            "lightTravel": light_travel,
        }
        if st.bp_rp is not None:
            star_entry["bpRp"] = float(st.bp_rp)
        if st.spectral_type is not None:
            star_entry["spectralType"] = st.spectral_type
        if st.rv_km_s is not None:
            star_entry["radialVelocityKmS"] = float(st.rv_km_s)
        stars_out.append(star_entry)
        # Gaia DR3 catalog values only: none for a Hipparcos-only star or a value its row lacks
        astrophysics = gaia_stellar_parameters(st)
        if astrophysics is not None:
            stars_out[-1]["astrophysics"] = astrophysics

    ephem_items = compute_solar_system_ephemerides(
        obs_utc_iso=obs_utc_iso,
        lat_deg=lat_deg,
        lon_deg=lon_deg,
        bodies=("Moon", "Saturn", "Uranus", "Jupiter", "Mars", "Venus"),
    )
    ephem_out: list[dict[str, Any]] = []
    for item in ephem_items:
        # A prediction for a time and place: no recording stands behind it, so it names none
        d = item.to_contract_dict(source_sha256=None)
        d["name"] = item.name
        d["raDeg"] = float(item.ra_deg)
        d["decDeg"] = float(item.dec_deg)
        d["altDeg"] = float(item.alt_deg)
        d["azDeg"] = float(item.az_deg)
        d["aboveHorizon"] = bool(item.alt_deg >= 0.0)
        d["distanceAu"] = float(item.distance_au)
        d["distancePc"] = float(item.distance_pc)
        d["lightTravel"] = compute_light_travel_time_description(item.distance_pc)
        d["evidenceClass"] = "predicted"
        d["evidence_class"] = "predicted"
        ephem_out.append(d)

    edges_out = [
        {"constellation": c_name, "fromStarId": sid_a, "toStarId": sid_b}
        for c_name, sid_a, sid_b in CONSTELLATION_EDGES
    ]
    # The bodies asked for above include the Moon; the sky brightness is predicted at the zenith, a stated target
    moon_alt_deg = float(next(e for e in ephem_items if e.name == "Moon").alt_deg)
    sky_brightness = predict_sky_brightness(
        obs_utc_iso, moon_alt_deg=moon_alt_deg, target_alt_deg=90.0, sep_from_moon_deg=90.0 - moon_alt_deg,
        target="zenith",
    )

    return {
        "observedAtUTC": obs_utc_iso,
        "observerLocation": {"latDeg": lat_deg, "lonDeg": lon_deg},
        "stars": stars_out,
        "ephemerides": ephem_out,
        "constellationEdges": edges_out,
        "predictedSkyBrightness": sky_brightness,
    }


@router.get("/api/science/radiometry-audit")
def get_science_radiometry_audit(
    obs_utc_iso: str = Query(...),
    lat_deg: float = Query(..., ge=-90.0, le=90.0),
    lon_deg: float = Query(..., ge=-180.0, le=180.0),
    target_sep_deg: float = Query(..., ge=0.5, le=180.0),
    target_alt_deg: float = Query(..., ge=0.0, le=90.0),
) -> dict[str, Any]:
    """Return the Krisciunas & Schaefer (1991) sky brightness predicted at the stated target (its altitude and
    its separation from the Moon). No sensor limiting magnitude is estimated."""
    _within_ephemeris_range(obs_utc_iso)
    (moon,) = compute_solar_system_ephemerides(
        obs_utc_iso=obs_utc_iso, lat_deg=lat_deg, lon_deg=lon_deg, bodies=("Moon",)
    )
    moon_alt = float(moon.alt_deg)
    return {
        "observedAtUTC": obs_utc_iso,
        "observerLocation": {"latDeg": lat_deg, "lonDeg": lon_deg},
        "moonAltDeg": round(moon_alt, 2),
        "lunarPhase": compute_lunar_phase_and_illumination(obs_utc_iso),
        "predictedSkyBrightness": predict_sky_brightness(
            obs_utc_iso,
            moon_alt_deg=moon_alt,
            target_alt_deg=target_alt_deg,
            sep_from_moon_deg=target_sep_deg,
            target=f"altitude {target_alt_deg:g} deg, {target_sep_deg:g} deg from the Moon",
        ),
    }


@router.post("/api/science/solve")
def post_science_solve(req: SolveRequest) -> dict[str, Any]:
    """Run point-source extraction, triangle-matching plate solving, and held-out WCS verification."""
    if req.detections is not None:
        if req.width is None or req.height is None:
            raise HTTPException(
                status_code=422, detail="Send width and height: the size of the image the detections were measured in."
            )
        width, height = req.width, req.height
        parsed_dets: list[SourceDetection] = []
        for d in req.detections:
            parsed_dets.append(
                SourceDetection(
                    x_px=_measured(d, "x_px", "xPx"),
                    y_px=_measured(d, "y_px", "yPx"),
                    flux=_measured(d, "flux"),
                    snr=_measured(d, "snr"),
                    fwhm_px=_measured(d, "fwhm_px", "fwhmPx"),
                    centroid_uncertainty_px=_measured(d, "centroid_uncertainty_px", "centroidUncertaintyPx"),
                    asset_id=req.source_asset_id,
                    # Without a measured ratio only the FWHM half of the hot-pixel test applies
                    **(
                        {"peak_to_total_ratio": _measured(d, "peak_to_total_ratio", "peakToTotalRatio")}
                        if d.get("peak_to_total_ratio", d.get("peakToTotalRatio")) is not None
                        else {}
                    ),
                )
            )
    elif req.image_luma is not None:
        arr = np.asarray(req.image_luma, dtype=np.float64)
        height, width = int(arr.shape[0]), int(arr.shape[1])
        if (req.width is not None and req.width != width) or (req.height is not None and req.height != height):
            raise HTTPException(
                status_code=422,
                detail=f"width and height disagree with image_luma, which is {width} x {height} px.",
            )
        parsed_dets = detect_point_sources(arr, asset_id=req.source_asset_id)
    else:
        raise HTTPException(status_code=422, detail=_NO_INPUT_DETAIL.format(inputs="detections or image_luma"))

    solution = solve_and_verify_field(
        detections=parsed_dets,
        width=width,
        height=height,
        hint_ra_deg=req.hint_ra_deg,
        hint_dec_deg=req.hint_dec_deg,
        obs_utc_iso=req.obs_utc_iso,
        observer_lat=req.observer_lat,
        observer_lon=req.observer_lon,
        source_asset_id=req.source_asset_id,
        source_sha256=req.source_sha256,
    )
    payload = solution.to_contract_dict()
    payload["held_out_inliers"] = solution.held_out_inliers
    # Residuals the solution did not measure stay out, as in the contract's quality block
    if solution.median_residual_px is not None:
        payload["median_residual_px"] = solution.median_residual_px
    if solution.p95_residual_px is not None:
        payload["p95_residual_px"] = solution.p95_residual_px
    return payload


@router.post("/api/science/solve-horizon")
def post_science_solve_horizon(req: SolveRequest) -> dict[str, Any]:
    """Fit a camera pose to a detected celestial beacon (the Moon) and the horizon, for the given time and place."""
    if req.image_luma is None:
        raise HTTPException(status_code=422, detail=_NO_INPUT_DETAIL.format(inputs="image_luma"))
    # The pose rests on the predicted Moon, so the moment must lie in the ephemeris' span
    _within_ephemeris_range(req.obs_utc_iso)
    arr = np.asarray(req.image_luma, dtype=np.float64)

    solution = solve_horizon_and_beacon_pose(
        image=arr,
        obs_utc_iso=req.obs_utc_iso,
        observer_lat=req.observer_lat,
        observer_lon=req.observer_lon,
        source_asset_id=req.source_asset_id,
        source_sha256=req.source_sha256,
    )
    return solution.to_contract_dict()


@router.post("/api/science/ephemeris")
def post_science_ephemeris(req: EphemerisRequest) -> dict[str, Any]:
    """Compute analytical ephemerides for Solar System bodies with explicit `predicted` evidence class."""
    _within_ephemeris_range(req.obs_utc_iso)
    try:
        ephem = compute_solar_system_ephemerides(
            obs_utc_iso=req.obs_utc_iso,
            lat_deg=req.lat_deg,
            lon_deg=req.lon_deg,
            bodies=tuple(req.bodies),
        )
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    findings_dicts = []
    for item in ephem:
        # A prediction for a time and place: no recording stands behind it, so it names none
        d = item.to_contract_dict(source_sha256=None)
        d["evidence_class"] = "predicted"
        d["name"] = item.name
        findings_dicts.append(d)

    return {
        "observedAtUTC": req.obs_utc_iso,
        "observerLocation": {"latDeg": req.lat_deg, "lonDeg": req.lon_deg},
        "evidenceClass": "predicted",
        "findings": findings_dicts,
    }
