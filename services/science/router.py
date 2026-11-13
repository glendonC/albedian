"""Science routes: sky context, radiometry audit, plate and horizon solves, and ephemerides."""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Mapping, Sequence
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
import numpy as np
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from services.contracts.astronomy import NUMBER, UTC_INSTANT, field_rule
from services.contracts.media import MEDIA_ID_PATTERNS
from services.contracts.wire import MAX_SAFE_INTEGER
from services.science.catalog import (
    ALL_SKY_CATALOG_STARS,
    CONSTELLATION_EDGES,
    _iso_to_julian_date,
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
from services.science.wcs_models import (
    ContractRefusal,
    contract_utc_instant,
    require_contract_value,
    require_source_sha256,
)

router = APIRouter()


def _time_refused(accepted: str) -> HTTPException:
    """The 422 for a time a route cannot use. It names the field and the forms the route takes, and holds nothing
    the request sent: raised as it is, where a validation error would be answered with the value in it."""
    return HTTPException(status_code=422, detail=f"observedAtUTC must be {accepted}.")


def _a_time_the_sky_code_reads(value: str) -> str:
    """Refuse at the door a time the sky code cannot read, which it would otherwise raise on deep in a route."""
    try:
        _iso_to_julian_date(value)
    except (ValueError, OverflowError):
        raise _time_refused(
            "an ISO 8601 date and time, ending in Z or with an offset such as +00:00; one with neither is read as UTC"
        ) from None
    return value


_AN_INSTANT = "an ISO 8601 date and time that names an instant: ending in Z or with an offset such as +00:00"


def _a_time_that_names_an_instant(value: str) -> str:
    """Refuse at the door a time a solution cannot be written with: its capture time is written as a UTC instant,
    so the time sent must name one, ending in Z or with an offset. One with no zone names none."""
    try:
        _iso_to_julian_date(value)
    except (ValueError, OverflowError):
        raise _time_refused(_AN_INSTANT) from None
    if UTC_INSTANT.problem(contract_utc_instant(value)) is not None:
        raise _time_refused(_AN_INSTANT)
    return value


# A time a client sends: for the routes that compute for a moment, and for the two that write a solution
MomentFromClient = Annotated[str, AfterValidator(_a_time_the_sky_code_reads)]
InstantFromClient = Annotated[str, AfterValidator(_a_time_that_names_an_instant)]


def _refused(refusal: ContractRefusal) -> HTTPException:
    """The 422 for a value the contract refuses: the refusal's own sentence, which names the field and the rule and
    never the value. Raised as it is, where a validation error would be answered with the value in it. Only what
    the client sent is answered so; a record the solver itself builds and the contract refuses is the solver's
    fault, is not caught, and answers 500."""
    return HTTPException(status_code=422, detail=str(refusal))


def _held_to(rule: Any, named: str) -> AfterValidator:
    """A request field held, at the door, to the contract rule its value meets in the solution record that will
    carry it, under the name the client knows it by."""

    def at_the_door(value: Any) -> Any:
        try:
            require_contract_value(value, rule, named)
        except ContractRefusal as refusal:
            raise _refused(refusal) from None
        return value

    return AfterValidator(at_the_door)


# The place a client sends is the solution's observer location. A hint meets no record: it is held to a finite
# number, which the search needs of it
LatitudeFromClient = Annotated[float, _held_to(field_rule("ObserverLocation", "latDeg"), "latDeg")]
LongitudeFromClient = Annotated[float, _held_to(field_rule("ObserverLocation", "lonDeg"), "lonDeg")]
HintRaFromClient = Annotated[float, _held_to(NUMBER, "hintRaDeg")]
HintDecFromClient = Annotated[float, _held_to(NUMBER, "hintDecDeg")]


def _measured_in(frame: np.ndarray, asset_id: str) -> list[SourceDetection]:
    """What the detector measures in the frame a client sent. Like a detection sent outright, one the contract
    refuses is the client's to mend: a pixel that is infinite, or so large that the detector's sums pass beyond any
    number, gives a position or a flux that is no number. The refusal names the detection's field and its rule,
    never a pixel. A frame the detector measures nothing refused in is taken as it is."""
    try:
        return detect_point_sources(frame, asset_id=asset_id)
    except ContractRefusal as refusal:
        raise HTTPException(
            status_code=422, detail=f"image_luma gives a detection no record takes. {refusal}"
        ) from None


# A source asset's id in the one form its minting code gives it (services/ingest/router.py, `_asset`): src_ and the
# first 16 hex digits of the file's SHA-256, the pattern the media contracts hold their records to. The solve routes
# take no other form (the lead's ruling of 2026-11-11: an id has the exact pattern its minting code produces)
SOURCE_ASSET_ID_PATTERN = MEDIA_ID_PATTERNS["sourceAsset"]
# The largest frame size taken: the largest whole number every reader of JSON holds exactly, 2**53 - 1
LARGEST_SIZE_PX = MAX_SAFE_INTEGER


class SolveRequest(BaseModel):
    """Request payload for `POST /api/science/solve`."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    # The image's size: taken from image_luma's own shape, and required with detections
    width: int | None = Field(default=None, gt=0, le=LARGEST_SIZE_PX)
    height: int | None = Field(default=None, gt=0, le=LARGEST_SIZE_PX)
    detections: list[dict[str, Any]] | None = None
    image_luma: list[list[float]] | None = None
    hint_ra_deg: HintRaFromClient | None = Field(default=None, alias="hintRaDeg")
    hint_dec_deg: HintDecFromClient | None = Field(default=None, alias="hintDecDeg")
    obs_utc_iso: InstantFromClient = Field(alias="observedAtUTC")
    observer_lat: LatitudeFromClient = Field(alias="latDeg")
    observer_lon: LongitudeFromClient = Field(alias="lonDeg")
    # The recording the input comes from, and its SHA-256: the client hashed the file, the server never sees it
    source_asset_id: str = Field(alias="sourceAssetId", pattern=SOURCE_ASSET_ID_PATTERN)
    source_sha256: str = Field(alias="sourceSha256")

    @field_validator("source_sha256")
    @classmethod
    def _real_sha256(cls, value: str) -> str:
        try:
            return require_source_sha256(value)
        except ContractRefusal as refusal:
            raise _refused(refusal) from None


# What each field of a solve request must be, under the name a client knows it by: the words of the routes' own
# answer to a request that fails validation
_A_SOLVE_REQUEST_FIELD_MUST_BE = {
    "width": f"a whole number of pixels from 1 to {LARGEST_SIZE_PX}",
    "height": f"a whole number of pixels from 1 to {LARGEST_SIZE_PX}",
    "detections": "a list of detections, each an object of its measured values",
    "image_luma": "the frame's rows, each a list of numbers",
    "hintRaDeg": "a number",
    "hintDecDeg": "a number",
    "observedAtUTC": _AN_INSTANT,
    "latDeg": "a number",
    "lonDeg": "a number",
    "sourceAssetId": "a source asset's id as it is minted: src_ and 16 lowercase hex digits",
    "sourceSha256": "the source asset's SHA-256 as 64 lowercase hex digits",
}
_WIRE_NAMES = {name: info.alias or name for name, info in SolveRequest.model_fields.items()}
_NOT_JSON = "The request body is not valid JSON."
_NOT_AN_OBJECT = "The request body must be a JSON object, sent as application/json."


def _what_is_wrong(errors: Sequence[Mapping[str, Any]]) -> str:
    """A solve request that failed validation, said plainly: one sentence for each field at fault, in the order the
    request model lists them, with the field's name and what it must be. Only the place and the kind of each error
    are read. Its message and its input are left out, since both can hold the value that was sent."""
    sentences: list[str] = []
    for error in errors:
        where = error["loc"]
        if len(where) < 2 or not isinstance(where[1], str):
            # The body as a whole: it could not be read as JSON, or it is no object
            sentence = _NOT_JSON if error["type"] == "json_invalid" else _NOT_AN_OBJECT
        else:
            name = _WIRE_NAMES.get(where[1], where[1])
            must_be = _A_SOLVE_REQUEST_FIELD_MUST_BE.get(name, "a value this route takes")
            sentence = f"{name} is missing: send {must_be}." if error["type"] == "missing" else f"{name} must be {must_be}."
        if sentence not in sentences:
            sentences.append(sentence)
    return " ".join(sentences)


class _AnsweredInASentence(APIRoute):
    """A solve route's own answer to a request that fails validation: 422 and a plain sentence for each field at
    fault (`_what_is_wrong`). FastAPI's own answer sends the value back, and for a value JSON cannot carry (an
    infinity, a NaN, a lone surrogate) it cannot be written at all, which ends in a 500. A body FastAPI could not
    read at all, which it answers with a 400 of its own, is answered here in the same way."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def answer(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as error:
                raise HTTPException(status_code=422, detail=_what_is_wrong(error.errors())) from None
            except StarletteHTTPException as error:
                # The two routes raise no 400 of their own: this one is FastAPI's, for a body it could not decode.
                # It raises Starlette's class, which FastAPI's own HTTPException extends
                if error.status_code != 400:
                    raise
                raise HTTPException(status_code=422, detail=_NOT_JSON) from None

        return answer


class SolveRefusal(BaseModel):
    """The body of a solve route's 422: what is wrong with the request, in plain sentences that hold no value sent."""

    detail: str


def _solve_route(path: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """`router.post(path)` for a route that writes a solution, which answers a request that fails validation itself
    and says so in its API description, where FastAPI would describe its own list of errors."""

    def register(route: Callable[..., Any]) -> Callable[..., Any]:
        router.add_api_route(
            path, route, methods=["POST"], route_class_override=_AnsweredInASentence,
            responses={422: {"model": SolveRefusal, "description": "The request is refused"}},
        )
        return route

    return register


def _frame_sent(image_luma: list[list[float]], least_rows: int) -> np.ndarray:
    """The frame a client sent, as an array. A list with no row, a row with no pixel, or rows of different lengths
    is no frame, and NumPy or a solver would raise on it deep in the route: it is refused here. The horizon route
    asks for two rows, since it looks for the horizon down them; the plate solve takes one."""
    if len(image_luma) < least_rows or not image_luma[0] or any(len(row) != len(image_luma[0]) for row in image_luma):
        raise HTTPException(
            status_code=422,
            detail=(
                f"image_luma must be at least {('one row', 'two rows')[least_rows - 1]} of pixels, each row as long as "
                "the first and at least one pixel long."
            ),
        )
    return np.asarray(image_luma, dtype=np.float64)


class EphemerisRequest(BaseModel):
    """Request payload for `POST /api/science/ephemeris`."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    obs_utc_iso: MomentFromClient = Field(alias="observedAtUTC")
    lat_deg: float = Field(alias="latDeg")
    lon_deg: float = Field(alias="lonDeg")
    bodies: list[str] = Field(
        default_factory=lambda: ["Jupiter", "Uranus", "Saturn", "Mars", "Venus", "Moon"]
    )


class SkyContextRequest(BaseModel):
    """Request payload for `POST /api/science/sky-context`. The time and place travel in the body, never in the
    URL, whose query string an access log prints."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    obs_utc_iso: MomentFromClient = Field(alias="observedAtUTC")
    lat_deg: float = Field(alias="latDeg", ge=-90.0, le=90.0)
    lon_deg: float = Field(alias="lonDeg", ge=-180.0, le=180.0)


class RadiometryAuditRequest(SkyContextRequest):
    """Request payload for `POST /api/science/radiometry-audit`: a time and place, and the target asked about."""

    target_sep_deg: float = Field(alias="targetSepDeg", ge=0.5, le=180.0)
    target_alt_deg: float = Field(alias="targetAltDeg", ge=0.0, le=90.0)


_NO_INPUT_DETAIL = "Send {inputs}; the API does not substitute synthetic input for a missing observation."


def _within_ephemeris_range(obs_utc_iso: str) -> None:
    """A 422 naming the field and the span when the moment lies outside the ephemeris' 1800 to 2050. The library's
    own sentence starts with the moment it was given: the answer names the field there, and holds no time sent."""
    try:
        require_ephemeris_range(obs_utc_iso)
    except EphemerisOutOfRange as exc:
        raise HTTPException(status_code=422, detail=str(exc).replace(obs_utc_iso, "observedAtUTC")) from exc


def _measured(detection: dict[str, Any], *keys: str) -> Any:
    """Return a detection measurement by any of its accepted keys, as it was sent; a missing one is a client error.
    Whether it is a number inside the contract's bounds is for the detection record to say."""
    for key in keys:
        if detection.get(key) is not None:
            return detection[key]
    raise HTTPException(status_code=422, detail=f"Each detection needs a measured `{keys[-1]}`.")


@router.post("/api/science/sky-context")
def post_science_sky_context(req: SkyContextRequest) -> dict[str, Any]:
    """Compute full 360-degree topocentric sky context for all 70+ navigational stars,
    Solar System ephemerides, and constellation stick-figure edges.
    """
    obs_utc_iso, lat_deg, lon_deg = req.obs_utc_iso, req.lat_deg, req.lon_deg
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


@router.post("/api/science/radiometry-audit")
def post_science_radiometry_audit(req: RadiometryAuditRequest) -> dict[str, Any]:
    """Return the Krisciunas & Schaefer (1991) sky brightness predicted at the stated target (its altitude and
    its separation from the Moon). No sensor limiting magnitude is estimated."""
    obs_utc_iso, lat_deg, lon_deg = req.obs_utc_iso, req.lat_deg, req.lon_deg
    target_sep_deg, target_alt_deg = req.target_sep_deg, req.target_alt_deg
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


@_solve_route("/api/science/solve")
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
            # The record holds each value the client measured to the contract's rule for it, and its refusal is
            # the client's answer
            try:
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
            except ContractRefusal as refusal:
                raise _refused(refusal) from None
    elif req.image_luma is not None:
        arr = _frame_sent(req.image_luma, 1)
        height, width = int(arr.shape[0]), int(arr.shape[1])
        if (req.width is not None and req.width != width) or (req.height is not None and req.height != height):
            raise HTTPException(
                status_code=422,
                detail=f"width and height disagree with image_luma, which is {width} x {height} px.",
            )
        parsed_dets = _measured_in(arr, req.source_asset_id)
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


@_solve_route("/api/science/solve-horizon")
def post_science_solve_horizon(req: SolveRequest) -> dict[str, Any]:
    """Fit a camera pose to a detected celestial beacon (the Moon) and the horizon, for the given time and place."""
    if req.image_luma is None:
        raise HTTPException(status_code=422, detail=_NO_INPUT_DETAIL.format(inputs="image_luma"))
    # The pose rests on the predicted Moon, so the moment must lie in the ephemeris' span
    _within_ephemeris_range(req.obs_utc_iso)
    arr = _frame_sent(req.image_luma, 2)

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
