"""FastAPI backend for Albedian Scientific Astrometry, Media Restoration Runtime, and Voice Intent Service."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from services.media.runtime import MediaExperimentRuntime
from services.science.catalog import (
    ALL_SKY_CATALOG_STARS,
    CONSTELLATION_EDGES,
    PINNED_CATALOG_STARS,
    bailer_jones_cartesian_pc,
    compute_light_travel_time_description,
    icrs_to_altaz,
)
from services.science.ephemeris import compute_solar_system_ephemerides
from services.science.solver import (
    SourceDetection,
    detect_point_sources,
    render_synthetic_starfield,
    solve_and_verify_field,
    solve_horizon_and_beacon_pose,
)
from services.voice.intent import parse_voice_intent, validate_and_execute_voice_command

app = FastAPI(
    title="Albedian Observation & Media Runtime API",
    version="0.2.0",
    description="Deterministic scientific astrometry, astronomy-aware media restoration, and voice action validation.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_media_runtime = MediaExperimentRuntime()


class SolveRequest(BaseModel):
    """Request payload for `POST /api/science/solve`."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    width: int = Field(default=960, gt=0)
    height: int = Field(default=540, gt=0)
    detections: list[dict[str, Any]] | None = None
    image_luma: list[list[float]] | None = None
    hint_ra_deg: float | None = Field(default=None, alias="hintRaDeg")
    hint_dec_deg: float | None = Field(default=None, alias="hintDecDeg")
    obs_utc_iso: str = Field(default="2026-10-19T22:14:00Z", alias="observedAtUTC")
    observer_lat: float = Field(default=31.149, alias="latDeg")
    observer_lon: float = Field(default=-3.972, alias="lonDeg")
    source_asset_id: str = Field(default="asset-demo-orion", alias="sourceAssetId")


class EphemerisRequest(BaseModel):
    """Request payload for `POST /api/science/ephemeris`."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    obs_utc_iso: str = Field(default="2026-10-19T22:14:00Z", alias="observedAtUTC")
    lat_deg: float = Field(default=31.149, alias="latDeg")
    lon_deg: float = Field(default=-3.972, alias="lonDeg")
    bodies: list[str] = Field(
        default_factory=lambda: ["Jupiter", "Uranus", "Saturn", "Mars", "Venus", "Moon"]
    )


class RestoreRequest(BaseModel):
    """Request payload for `POST /api/media/restore`."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    profile: str = Field(default="star_protected")
    preserve_stars: bool = Field(default=True, alias="preserveStars")
    frames: list[list[list[float]]] | None = None
    image_luma: list[list[float]] | None = None
    source_asset_id: str = Field(default="asset-demo-orion", alias="sourceAssetId")


class VoiceCommandRequest(BaseModel):
    """Request payload for `POST /api/voice/command`."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    utterance: str
    context: dict[str, Any] = Field(default_factory=dict)
    proposed_action: dict[str, Any] | None = Field(default=None, alias="proposedAction")


@app.get("/api/health")
def get_health() -> dict[str, Any]:
    """Return service health and pinned catalog summary."""
    return {
        "status": "ok",
        "service": "albedian-observation-media-runtime",
        "version": "0.2.0",
        "catalogStarCount": len(PINNED_CATALOG_STARS),
        "allSkyCatalogStarCount": len(ALL_SKY_CATALOG_STARS),
        "constellationEdgeCount": len(CONSTELLATION_EDGES),
        "evidenceClasses": ["observed", "catalog_matched", "predicted", "simulated"],
    }


@app.get("/api/science/sky-context")
def get_science_sky_context(
    obs_utc_iso: str = Query(default="2026-10-20T22:21:02Z"),
    lat_deg: float = Query(default=31.2021, ge=-90.0, le=90.0),
    lon_deg: float = Query(default=-3.9458, ge=-180.0, le=180.0),
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

    return {
        "observedAtUTC": obs_utc_iso,
        "observerLocation": {"latDeg": lat_deg, "lonDeg": lon_deg},
        "stars": stars_out,
        "ephemerides": ephem_out,
        "constellationEdges": edges_out,
    }


@app.post("/api/science/solve")
def post_science_solve(req: SolveRequest) -> dict[str, Any]:
    """Run point-source extraction, geometric quad plate solving, and held-out WCS verification."""
    if req.detections is not None:
        parsed_dets: list[SourceDetection] = []
        for d in req.detections:
            parsed_dets.append(
                SourceDetection(
                    x_px=float(d.get("x_px", d.get("xPx", 0.0))),
                    y_px=float(d.get("y_px", d.get("yPx", 0.0))),
                    flux=float(d.get("flux", 100.0)),
                    snr=float(d.get("snr", 10.0)),
                    fwhm_px=float(d.get("fwhm_px", d.get("fwhmPx", 2.4))),
                    centroid_uncertainty_px=float(
                        d.get("centroid_uncertainty_px", d.get("centroidUncertaintyPx", 0.15))
                    ),
                    asset_id=req.source_asset_id,
                )
            )
    elif req.image_luma is not None:
        arr = np.asarray(req.image_luma, dtype=np.float64)
        parsed_dets = detect_point_sources(arr, asset_id=req.source_asset_id)
    else:
        frame, _, _ = render_synthetic_starfield(width=req.width, height=req.height)
        parsed_dets = detect_point_sources(frame, asset_id=req.source_asset_id)

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


@app.post("/api/science/solve-horizon")
def post_science_solve_horizon(req: SolveRequest) -> dict[str, Any]:
    """Calibrate camera pose from a detected celestial beacon (e.g. Moon) + horizon line + GPS/UTC."""
    if req.image_luma is not None:
        arr = np.asarray(req.image_luma, dtype=np.float64)
    else:
        arr = np.zeros((req.height, req.width), dtype=np.float64)
        arr[int(req.height * 0.55) :, :] = 40.0
        # Place synthetic Moon beacon in upper right
        arr[118:126, 930:938] = 240.0

    solution = solve_horizon_and_beacon_pose(
        image=arr,
        obs_utc_iso=req.obs_utc_iso,
        observer_lat=req.observer_lat,
        observer_lon=req.observer_lon,
        source_asset_id=req.source_asset_id,
    )
    return solution.to_contract_dict()


@app.post("/api/science/ephemeris")
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


@app.post("/api/media/restore")
def post_media_restore(req: RestoreRequest) -> dict[str, Any]:
    """Execute an astronomy-aware media restoration branch and return honest signal metrics."""
    if req.frames is not None:
        frame_arrays = [np.asarray(f, dtype=np.float64) for f in req.frames]
    elif req.image_luma is not None:
        frame_arrays = [np.asarray(req.image_luma, dtype=np.float64)]
    else:
        synth_frame, _, _ = render_synthetic_starfield(
            width=320, height=180, read_noise_sigma=3.2, seed=101
        )
        frame_arrays = [synth_frame]

    try:
        res = _media_runtime.execute_restoration_branch(
            frames=frame_arrays,
            profile=req.profile,
            preserve_stars=req.preserve_stars,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    contract_dict = res.to_contract_dict(source_asset_id=req.source_asset_id)
    contract_dict["profile"] = res.profile
    contract_dict["sourceSha256"] = res.source_sha256
    contract_dict["outputSha256"] = res.output_sha256
    contract_dict["allowSyntheticSky"] = False
    contract_dict["shadow_snr_gain_db"] = res.shadow_snr_gain_db
    contract_dict["point_source_retention_ratio"] = res.point_source_retention_ratio
    contract_dict["false_point_artifact_count"] = res.false_point_artifact_count
    contract_dict["temporal_flicker_index"] = res.temporal_flicker_index
    return contract_dict


@app.post("/api/voice/command")
def post_voice_command(req: VoiceCommandRequest) -> dict[str, Any]:
    """Parse natural-language voice intent and validate against session revision & epistemic gates."""
    action = (
        req.proposed_action
        if req.proposed_action is not None
        else parse_voice_intent(req.utterance, req.context)
    )
    outcome = validate_and_execute_voice_command(
        utterance=req.utterance,
        context=req.context,
        proposed_action=action,
    )
    outcome["proposedAction"] = action
    return outcome
