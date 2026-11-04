"""Science routes: sky context, radiometry audit, plate and horizon solves, and ephemerides."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from services.science.catalog import (
    ALL_SKY_CATALOG_STARS,
    CONSTELLATION_EDGES,
    bailer_jones_cartesian_pc,
    compute_light_travel_time_description,
    icrs_to_altaz,
)
from services.science.deep_sky import (
    DEEP_SKY_CATALOG,
    compute_3d_nearest_neighbors,
    compute_stellar_astrophysics,
)
from services.science.ephemeris import compute_solar_system_ephemerides
from services.science.radiometry import (
    compute_krisciunas_schaefer_sky_brightness,
    compute_lunar_phase_and_illumination,
    compute_sensor_limiting_magnitude_audit,
)
from services.science.solver import (
    SourceDetection,
    detect_point_sources,
    solve_and_verify_field,
    solve_horizon_and_beacon_pose,
)

router = APIRouter()


class SolveRequest(BaseModel):
    """Request payload for `POST /api/science/solve`."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    width: int = Field(default=960, gt=0)
    height: int = Field(default=540, gt=0)
    detections: list[dict[str, Any]] | None = None
    image_luma: list[list[float]] | None = None
    hint_ra_deg: float | None = Field(default=None, alias="hintRaDeg")
    hint_dec_deg: float | None = Field(default=None, alias="hintDecDeg")
    obs_utc_iso: str = Field(alias="observedAtUTC")
    observer_lat: float = Field(alias="latDeg")
    observer_lon: float = Field(alias="lonDeg")
    source_asset_id: str = Field(default="asset-demo-orion", alias="sourceAssetId")


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
    stars_out: list[dict[str, Any]] = []
    for st in ALL_SKY_CATALOG_STARS:
        alt_deg, az_deg = icrs_to_altaz(st.ra_deg, st.dec_deg, lat_deg, lon_deg, obs_utc_iso)
        cx, cy, cz = bailer_jones_cartesian_pc(st)
        light_travel = compute_light_travel_time_description(st.median_pc)
        stars_out.append(
            {
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
                "bpRp": float(st.bp_rp),
                "spectralType": st.spectral_type,
                "properMotionRaMasYr": float(st.pm_ra_mas_yr),
                "properMotionDecMasYr": float(st.pm_dec_mas_yr),
                "radialVelocityKmS": float(st.rv_km_s),
                "distancePc": {
                    "lower": float(st.lower_pc),
                    "median": float(st.median_pc),
                    "upper": float(st.upper_pc),
                    "method": st.distance_method,
                },
                "cartesianParsecICRS": [float(cx), float(cy), float(cz)],
                "lightTravel": light_travel,
                "astrophysics": compute_stellar_astrophysics(
                    st.mag, st.bp_rp, st.median_pc, st.spectral_type, st.common_name
                ),
                "nearestNeighbors3D": compute_3d_nearest_neighbors(st.id, k=3),
            }
        )

    ephem_items = compute_solar_system_ephemerides(
        obs_utc_iso=obs_utc_iso,
        lat_deg=lat_deg,
        lon_deg=lon_deg,
        bodies=("Moon", "Saturn", "Uranus", "Jupiter", "Mars", "Venus"),
    )
    ephem_out: list[dict[str, Any]] = []
    for item in ephem_items:
        d = item.to_contract_dict()
        d["name"] = item.name
        d["raDeg"] = float(item.ra_deg)
        d["decDeg"] = float(item.dec_deg)
        d["altDeg"] = float(item.alt_deg)
        d["azDeg"] = float(item.az_deg)
        d["aboveHorizon"] = bool(item.alt_deg >= 0.0)
        d["distanceAu"] = float(item.distance_au)
        d["distancePc"] = float(item.distance_pc)
        d["apparentMagnitude"] = float(item.apparent_magnitude)
        d["lightTravel"] = compute_light_travel_time_description(item.distance_pc)
        d["evidenceClass"] = "predicted"
        d["evidence_class"] = "predicted"
        ephem_out.append(d)

    edges_out = [
        {"constellation": c_name, "fromStarId": sid_a, "toStarId": sid_b}
        for c_name, sid_a, sid_b in CONSTELLATION_EDGES
    ]
    moon_item = next((e for e in ephem_items if e.name == "Moon"), None)
    moon_alt_deg = float(moon_item.alt_deg) if moon_item is not None else -15.0
    radiometry_audit = compute_sensor_limiting_magnitude_audit(
        obs_utc_iso=obs_utc_iso,
        moon_alt_deg=moon_alt_deg,
        target_sep_deg=8.2,
        target_alt_deg=max(10.0, moon_alt_deg if moon_alt_deg > 0.0 else 30.0),
    )
    deep_sky_out = [
        dso.to_contract_dict(obs_utc_iso=obs_utc_iso, lat_deg=lat_deg, lon_deg=lon_deg)
        for dso in DEEP_SKY_CATALOG
    ]

    return {
        "observedAtUTC": obs_utc_iso,
        "observerLocation": {"latDeg": lat_deg, "lonDeg": lon_deg},
        "stars": stars_out,
        "ephemerides": ephem_out,
        "constellationEdges": edges_out,
        "deepSkyObjects": deep_sky_out,
        "radiometryAudit": radiometry_audit,
    }


@router.get("/api/science/radiometry-audit")
def get_science_radiometry_audit(
    obs_utc_iso: str = Query(...),
    lat_deg: float = Query(..., ge=-90.0, le=90.0),
    lon_deg: float = Query(..., ge=-180.0, le=180.0),
    target_sep_deg: float = Query(default=8.2, ge=0.5, le=180.0),
    target_alt_deg: float = Query(default=32.0, ge=0.0, le=90.0),
    stack_frames: int = Query(default=9, ge=1, le=600),
) -> dict[str, Any]:
    """Return Krisciunas & Schaefer (1991) lunar sky brightness and sensor limiting magnitude audit."""
    ephem_items = compute_solar_system_ephemerides(
        obs_utc_iso=obs_utc_iso, lat_deg=lat_deg, lon_deg=lon_deg, bodies=("Moon",)
    )
    moon_alt = float(ephem_items[0].alt_deg) if ephem_items else -15.0
    lunar_phase = compute_lunar_phase_and_illumination(obs_utc_iso)
    ks_sky = compute_krisciunas_schaefer_sky_brightness(
        sep_from_moon_deg=target_sep_deg,
        target_alt_deg=target_alt_deg,
        moon_alt_deg=moon_alt,
        obs_utc_iso=obs_utc_iso,
    )
    audit = compute_sensor_limiting_magnitude_audit(
        obs_utc_iso=obs_utc_iso,
        moon_alt_deg=moon_alt,
        target_sep_deg=target_sep_deg,
        target_alt_deg=target_alt_deg,
        stack_frames=stack_frames,
    )
    return {
        "observedAtUTC": obs_utc_iso,
        "observerLocation": {"latDeg": lat_deg, "lonDeg": lon_deg},
        "moonAltDeg": round(moon_alt, 2),
        "lunarPhase": lunar_phase,
        "krisciunasSchaeferSky": ks_sky,
        "sensorAudit": audit,
    }


@router.post("/api/science/solve")
def post_science_solve(req: SolveRequest) -> dict[str, Any]:
    """Run point-source extraction, geometric quad plate solving, and held-out WCS verification."""
    if req.detections is not None:
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
        parsed_dets = detect_point_sources(arr, asset_id=req.source_asset_id)
    else:
        raise HTTPException(status_code=422, detail=_NO_INPUT_DETAIL.format(inputs="detections or image_luma"))

    solution = solve_and_verify_field(
        detections=parsed_dets,
        width=req.width,
        height=req.height,
        hint_ra_deg=req.hint_ra_deg,
        hint_dec_deg=req.hint_dec_deg,
        obs_utc_iso=req.obs_utc_iso,
        observer_lat=req.observer_lat,
        observer_lon=req.observer_lon,
        source_asset_id=req.source_asset_id,
    )
    payload = solution.to_contract_dict()
    payload["held_out_inliers"] = solution.held_out_inliers
    payload["median_residual_px"] = solution.median_residual_px
    payload["p95_residual_px"] = solution.p95_residual_px
    return payload


@router.post("/api/science/solve-horizon")
def post_science_solve_horizon(req: SolveRequest) -> dict[str, Any]:
    """Calibrate camera pose from a detected celestial beacon (e.g. Moon) + horizon line + GPS/UTC."""
    if req.image_luma is None:
        raise HTTPException(status_code=422, detail=_NO_INPUT_DETAIL.format(inputs="image_luma"))
    arr = np.asarray(req.image_luma, dtype=np.float64)

    solution = solve_horizon_and_beacon_pose(
        image=arr,
        obs_utc_iso=req.obs_utc_iso,
        observer_lat=req.observer_lat,
        observer_lon=req.observer_lon,
        source_asset_id=req.source_asset_id,
    )
    return solution.to_contract_dict()


@router.post("/api/science/ephemeris")
def post_science_ephemeris(req: EphemerisRequest) -> dict[str, Any]:
    """Compute analytical ephemerides for Solar System bodies with explicit `predicted` evidence class."""
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
        d = item.to_contract_dict()
        d["evidence_class"] = "predicted"
        d["name"] = item.name
        findings_dicts.append(d)

    return {
        "observedAtUTC": req.obs_utc_iso,
        "observerLocation": {"latDeg": req.lat_deg, "lonDeg": req.lon_deg},
        "evidenceClass": "predicted",
        "findings": findings_dicts,
    }
