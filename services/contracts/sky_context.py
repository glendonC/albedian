"""The sky for a time and place: the Python mirror of packages/contracts/src/sky-context.ts.

`POST /api/science/sky-context` takes a request (the moment, the place, and two optional filters) and answers the
bright stars, the solar system bodies, the constellation figures' edges and the predicted sky brightness for it. Both
sides are judged by tests/fixtures/sky_context_contract.json, which tests/integration/test_sky_context_contract.py
writes and the Vitest file and the iPhone app's reader read.

Everything in the reply is `predicted`: computed from catalog rows and an ephemeris for the time and place sent,
never measured in any image. The positions follow one convention, stated once in the reply (`convention`):
topocentric from the place at sea level, azimuth from true north through east, altitude apparent and airless (no
refraction applied), as services/science/catalog.py `icrs_to_altaz` computes them.

The request travels in the body, never in the URL. `magLimit` keeps only the stars whose `mag` is at most it (a body
has no magnitude in this reply and is kept); `aboveHorizonOnly` keeps only the stars and bodies whose altitude is 0 or
more, and then only the edges whose two stars are kept. The reply states both as `filters`, so a reader knows what was
left out. Strict by the wire rules (services/contracts/wire.py): unknown keys and Python field names are refused at
every level.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Optional

from pydantic import AfterValidator, BeforeValidator, ConfigDict, Field, model_validator

from services.contracts.wire import WIRE_CONFIG, UtcInstant, WireModel, WireNumber, utf16_units, wire_int

SKY_CONTEXT_FORMAT = "albedian.sky-context"
SKY_CONTEXT_FORMAT_VERSION = 1
# The ephemeris's span (services/science/catalog.py `require_ephemeris_range`): from the first instant of 1800 up to,
# not including, the first of 2051
SKY_CONTEXT_EPHEMERIS_FROM = "1800-01-01T00:00:00Z"
SKY_CONTEXT_EPHEMERIS_UNTIL = "2051-01-01T00:00:00Z"
# [proposed] A magnitude, sent or held, lies in -30 to 30: the Sun is -26.7, and no star of these catalogs is near 30
SKY_CONTEXT_MAG_MIN, SKY_CONTEXT_MAG_MAX = -30.0, 30.0
# [proposed] A name or an id is at most 64 UTF-16 units; a sentence (a method, a limitation, a citation) at most 1024
SKY_CONTEXT_NAME_MAX_UTF16 = 64
SKY_CONTEXT_TEXT_MAX_UTF16 = 1024
# [proposed] At most this many of each: the route sends 71 stars, 6 bodies and 72 edges today
SKY_CONTEXT_STARS_MAX = 512
SKY_CONTEXT_BODIES_MAX = 16
SKY_CONTEXT_EDGES_MAX = 1024
SKY_CONTEXT_NOTES_MAX = 16
# [proposed] A proper motion is at most 20,000 mas/yr in size (Barnard's Star, the fastest, moves about 10,400); a
# radial velocity at most 2,000 km/s; a BP-RP colour from -2 to 10 magnitudes
SKY_CONTEXT_PROPER_MOTION_MAX = 20_000.0
SKY_CONTEXT_RADIAL_VELOCITY_MAX = 2_000.0
SKY_CONTEXT_BP_RP_MIN, SKY_CONTEXT_BP_RP_MAX = -2.0, 10.0

SKY_CONTEXT_CONVENTION = {"frame": "topocentric_sea_level", "azimuth": "true_north_through_east", "altitude": "apparent_airless"}
SKY_CONTEXT_PROVIDERS = {"Hipparcos": "Hipparcos 2007", "Gaia DR3": "Gaia DR3"}
SKY_CONTEXT_SOURCE_ID_PATTERNS = {"Hipparcos": r"^HIP [1-9][0-9]{0,5}$", "Gaia DR3": r"^Gaia DR3 [1-9][0-9]{0,19}$"}
SKY_CONTEXT_STAR_DISTANCE_METHODS = ("geometric_parallax", "photogeometric_bailer_jones_edr3", "geometric_bailer_jones_edr3")
SKY_CONTEXT_INTERVALS = ("16th to 84th percentile", "span of the two percentile intervals")
SKY_CONTEXT_MOON_PHASES = (
    "New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous", "Full Moon", "Waning Gibbous", "Last Quarter",
    "Waning Crescent",
)  # fmt: skip
SKY_CONTEXT_ID_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
# A body's name: one capital and lower-case ASCII letters (Moon, Jupiter), so its lower case is the same everywhere
SKY_CONTEXT_BODY_NAME_PATTERN = r"^[A-Z][a-z]{1,31}$"


class _Strict(WireModel):
    """A strict record whose errors never repeat the value refused: a request holds a place."""

    model_config = ConfigDict(**WIRE_CONFIG, hide_input_in_errors=True)

    def to_wire_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)


# The contract's own rules, worded the same on both sides
SKY_CONTEXT_RULES = {
    "insideEphemerisSpan": "The moment lies from 1800 to 2050, the only years the sky can be worked out for.",
    "aboveHorizonIsAltitude": "aboveHorizon is true exactly when altDeg is 0 or more.",
    "aboveHorizonOnlyHeld": "With aboveHorizonOnly, every star and body has an altDeg of 0 or more.",
    "magLimitHeld": "With a magLimit, every star's mag is at most the magLimit.",
    "idsOnce": "Each star's id and each body's id is held once.",
    "edgeJoinsHeldStars": "Each constellation edge joins two different stars the reply holds.",
    "releaseOfProvider": "A star's releaseVersion is its provider's: Hipparcos 2007 for Hipparcos, Gaia DR3 for Gaia DR3.",
    "sourceIdOfProvider": "A star's catalogSourceId is in its provider's form: HIP and a number, or Gaia DR3 and a number.",
    "distanceInOrder": "A distance's lower is at most its median, and its median at most its upper.",
    "lightTravelOfDistance": "lightTravel.distancePc is the item's own median distance.",
    "astrophysicsOfGaia": "Only a Gaia DR3 star has astrophysics, under its own catalogSourceId.",
    "astrophysicsHoldsAValue": "Astrophysics holds a temperature or a luminosity.",
    "estimateInterval": "An estimate's lower, upper and interval come together, with lower at most its value and its value at most upper.",
    "radiusFromBoth": "A radius is given only with the temperature and the luminosity it is derived from.",
    "positionIsSkyPosition": "A body's raDeg, decDeg, altDeg and azDeg are its skyPosition's.",
    "distanceIsInferred": "A body's distancePc is its inferredDistancePc median.",
    "idsOfName": "A body's id and its evidence's id are made from its name.",
}  # fmt: skip

def _text(maximum: int) -> Any:
    """1 to `maximum` UTF-16 units, with no lone surrogate and no control character (C0, DEL or C1)."""

    def check(text: str) -> str:
        if not 1 <= utf16_units(text) <= maximum:
            raise ValueError(f"holds from 1 to {maximum} UTF-16 code units")
        if any(0xD800 <= ord(char) <= 0xDFFF for char in text):
            raise ValueError("holds a lone surrogate")
        if any(ord(char) <= 0x1F or 0x7F <= ord(char) <= 0x9F for char in text):
            raise ValueError("holds a control character")
        return text

    return Annotated[str, AfterValidator(check)]


Name = _text(SKY_CONTEXT_NAME_MAX_UTF16)
Text = _text(SKY_CONTEXT_TEXT_MAX_UTF16)
Id = Annotated[Name, Field(pattern=SKY_CONTEXT_ID_PATTERN)]
Notes = Annotated[list[Text], Field(min_length=1, max_length=SKY_CONTEXT_NOTES_MAX)]
Latitude = Annotated[WireNumber, Field(ge=-90.0, le=90.0)]
Longitude = Annotated[WireNumber, Field(ge=-180.0, le=180.0)]
Angle360 = Annotated[WireNumber, Field(ge=0.0, le=360.0)]
Magnitude = Annotated[WireNumber, Field(ge=SKY_CONTEXT_MAG_MIN, le=SKY_CONTEXT_MAG_MAX)]
def _is(expected: bool) -> Any:
    """A strict boolean that must be `expected`: Literal[True] alone would take the number 1, which equals True."""

    def check(value: bool) -> bool:
        if value is not expected:
            raise ValueError(f"is {str(expected).lower()}")
        return value

    return Annotated[bool, AfterValidator(check)]


AlwaysTrue, AlwaysFalse = _is(True), _is(False)
Positive = Annotated[WireNumber, Field(gt=0.0)]
NonNegative = Annotated[WireNumber, Field(ge=0.0)]
# Three numbers, x, y and z in parsecs (a strict tuple would refuse the list JSON reads)
Cartesian = Annotated[list[WireNumber], Field(min_length=3, max_length=3)]
ProperMotion = Annotated[WireNumber, Field(ge=-SKY_CONTEXT_PROPER_MOTION_MAX, le=SKY_CONTEXT_PROPER_MOTION_MAX)]


def _rule(key: str) -> ValueError:
    return ValueError(SKY_CONTEXT_RULES[key])


def _in_span(moment: str) -> bool:
    """Whether a wire UTC instant lies in the ephemeris's span: the form is fixed width, so text order is time order."""
    return SKY_CONTEXT_EPHEMERIS_FROM <= moment[:19] + "Z" < SKY_CONTEXT_EPHEMERIS_UNTIL


class SkyContextRequest(_Strict):
    observed_at_utc: UtcInstant = Field(alias="observedAtUTC")
    lat_deg: Latitude
    lon_deg: Longitude
    mag_limit: Optional[Magnitude] = None
    above_horizon_only: Optional[bool] = None

    @model_validator(mode="after")
    def _in_the_ephemeris_span(self) -> SkyContextRequest:
        if not _in_span(self.observed_at_utc):
            raise _rule("insideEphemerisSpan")
        return self


class SkyContextConvention(_Strict):
    frame: Literal["topocentric_sea_level"]
    azimuth: Literal["true_north_through_east"]
    altitude: Literal["apparent_airless"]


class SkyContextFilters(_Strict):
    mag_limit: Optional[Magnitude] = None
    above_horizon_only: bool


class ObserverLocation(_Strict):
    lat_deg: Latitude
    lon_deg: Longitude


class DistanceInterval(_Strict):
    lower: Positive
    median: Positive
    upper: Positive

    @model_validator(mode="after")
    def _in_order(self) -> Any:
        if not self.lower <= self.median <= self.upper:
            raise _rule("distanceInOrder")
        return self


class StarDistance(DistanceInterval):
    method: Literal[SKY_CONTEXT_STAR_DISTANCE_METHODS]  # type: ignore[valid-type]


class BodyDistance(DistanceInterval):
    method: Literal["ephemeris_au"]


class LightTravel(_Strict):
    distance_pc: Positive
    light_years: Positive
    light_minutes: Positive
    light_seconds: Positive
    description: Name
    formatted: Name


class Estimate(_Strict):
    value: Positive
    method: Text
    lower: Optional[Positive] = None
    upper: Optional[Positive] = None
    interval: Optional[Literal[SKY_CONTEXT_INTERVALS]] = None  # type: ignore[valid-type]

    @model_validator(mode="after")
    def _interval(self) -> Any:
        held = [self.lower is not None, self.upper is not None, self.interval is not None]
        if any(held) and not all(held):
            raise _rule("estimateInterval")
        if all(held) and not self.lower <= self.value <= self.upper:  # type: ignore[operator]
            raise _rule("estimateInterval")
        return self


class DerivedEstimate(Estimate):
    derived: AlwaysTrue


class Astrophysics(_Strict):
    catalog_source_id: Name
    limitations: Notes
    citations: Notes
    teff_k: Optional[Estimate] = None
    luminosity_solar: Optional[Estimate] = None
    radius_solar: Optional[DerivedEstimate] = None

    @model_validator(mode="after")
    def _values(self) -> Astrophysics:
        if self.teff_k is None and self.luminosity_solar is None:
            raise _rule("astrophysicsHoldsAValue")
        if self.radius_solar is not None and (self.teff_k is None or self.luminosity_solar is None):
            raise _rule("radiusFromBoth")
        return self


class SkyContextStar(_Strict):
    id: Id
    evidence_class: Literal["predicted"]
    common_name: Name
    bayer_designation: Name
    constellation: Name
    provider: Literal["Hipparcos", "Gaia DR3"]
    release_version: Literal["Hipparcos 2007", "Gaia DR3"]
    catalog_source_id: Name
    ra_deg: Angle360
    dec_deg: Latitude
    alt_deg: Latitude
    az_deg: Angle360
    above_horizon: bool
    mag: Magnitude
    proper_motion_ra_mas_yr: ProperMotion
    proper_motion_dec_mas_yr: ProperMotion
    distance_pc: StarDistance
    cartesian_parsec_icrs: Cartesian = Field(alias="cartesianParsecICRS")
    light_travel: LightTravel
    bp_rp: Optional[Annotated[WireNumber, Field(ge=SKY_CONTEXT_BP_RP_MIN, le=SKY_CONTEXT_BP_RP_MAX)]] = None
    radial_velocity_km_s: Optional[
        Annotated[WireNumber, Field(ge=-SKY_CONTEXT_RADIAL_VELOCITY_MAX, le=SKY_CONTEXT_RADIAL_VELOCITY_MAX)]
    ] = None
    astrophysics: Optional[Astrophysics] = None

    @model_validator(mode="after")
    def _rules(self) -> SkyContextStar:
        if self.above_horizon != (self.alt_deg >= 0.0):
            raise _rule("aboveHorizonIsAltitude")
        if SKY_CONTEXT_PROVIDERS[self.provider] != self.release_version:
            raise _rule("releaseOfProvider")
        if not re.fullmatch(SKY_CONTEXT_SOURCE_ID_PATTERNS[self.provider], self.catalog_source_id):
            raise _rule("sourceIdOfProvider")
        if self.light_travel.distance_pc != self.distance_pc.median:
            raise _rule("lightTravelOfDistance")
        if self.astrophysics is not None and (
            self.provider != "Gaia DR3" or self.astrophysics.catalog_source_id != self.catalog_source_id
        ):
            raise _rule("astrophysicsOfGaia")
        return self


class BodySkyPosition(_Strict):
    ra_deg: Angle360
    dec_deg: Latitude
    uncertainty_arcsec: NonNegative
    alt_deg: Latitude
    az_deg: Angle360


class BodyEvidence(_Strict):
    id: Id
    evidence_class: Literal["predicted"]
    verification_method: Text
    distance_uncertainty_note: Text
    limitations: Notes
    verified_by_independent_holdout: AlwaysFalse


class SkyContextBody(_Strict):
    id: Id
    evidence_class: Literal["predicted"]
    name: Annotated[str, Field(pattern=SKY_CONTEXT_BODY_NAME_PATTERN)]
    title: Name
    subtitle: Name
    sky_position: BodySkyPosition
    inferred_distance_pc: BodyDistance
    cartesian_parsec_icrs: Cartesian = Field(alias="cartesianParsecICRS")
    evidence: BodyEvidence
    ra_deg: Angle360
    dec_deg: Latitude
    alt_deg: Latitude
    az_deg: Angle360
    above_horizon: bool
    distance_au: Positive
    distance_pc: Positive
    light_travel: LightTravel

    @model_validator(mode="after")
    def _rules(self) -> SkyContextBody:
        position = self.sky_position
        if (self.ra_deg, self.dec_deg, self.alt_deg, self.az_deg) != (position.ra_deg, position.dec_deg, position.alt_deg, position.az_deg):
            raise _rule("positionIsSkyPosition")
        if self.above_horizon != (self.alt_deg >= 0.0):
            raise _rule("aboveHorizonIsAltitude")
        if self.distance_pc != self.inferred_distance_pc.median:
            raise _rule("distanceIsInferred")
        if self.light_travel.distance_pc != self.distance_pc:
            raise _rule("lightTravelOfDistance")
        stem = self.name.lower()
        if self.id != f"finding-predicted-{stem}" or self.evidence.id != f"ev-ephem-{stem}":
            raise _rule("idsOfName")
        return self


class ConstellationEdge(_Strict):
    constellation: Name
    from_star_id: Id
    to_star_id: Id


class PredictedSkyBrightness(_Strict):
    sky_brightness_mag_arcsec2: WireNumber
    dark_sky_mag_arcsec2: WireNumber
    lunar_veil_delta_mag: NonNegative
    sky_brightness_nano_lamberts: NonNegative
    lunar_scattered_nano_lamberts: NonNegative
    airmass: Annotated[WireNumber, Field(ge=1.0)]
    illumination_fraction: Annotated[WireNumber, Field(ge=0.0, le=1.0)]
    lunar_phase_name: Literal[SKY_CONTEXT_MOON_PHASES]  # type: ignore[valid-type]
    sep_from_moon_deg: Annotated[WireNumber, Field(ge=0.0, le=180.0)]
    target: Literal["zenith"]
    target_alt_deg: Latitude
    moon_alt_deg: Latitude
    evidence_class: Literal["predicted"]
    method: Text
    limitations: Notes


class SkyContextReply(_Strict):
    format: Literal["albedian.sky-context"]
    format_version: Annotated[Literal[1], BeforeValidator(wire_int)]
    observed_at_utc: UtcInstant = Field(alias="observedAtUTC")
    observer_location: ObserverLocation
    filters: SkyContextFilters
    convention: SkyContextConvention
    stars: Annotated[list[SkyContextStar], Field(max_length=SKY_CONTEXT_STARS_MAX)]
    ephemerides: Annotated[list[SkyContextBody], Field(max_length=SKY_CONTEXT_BODIES_MAX)]
    constellation_edges: Annotated[list[ConstellationEdge], Field(max_length=SKY_CONTEXT_EDGES_MAX)]
    predicted_sky_brightness: PredictedSkyBrightness

    @model_validator(mode="after")
    def _rules(self) -> SkyContextReply:
        if not _in_span(self.observed_at_utc):
            raise _rule("insideEphemerisSpan")
        star_ids = [star.id for star in self.stars]
        body_ids = [body.id for body in self.ephemerides]
        if len(set(star_ids)) != len(star_ids) or len(set(body_ids)) != len(body_ids):
            raise _rule("idsOnce")
        held = set(star_ids)
        if any(edge.from_star_id == edge.to_star_id or not {edge.from_star_id, edge.to_star_id} <= held for edge in self.constellation_edges):
            raise _rule("edgeJoinsHeldStars")
        if self.filters.above_horizon_only and any(item.alt_deg < 0.0 for item in [*self.stars, *self.ephemerides]):
            raise _rule("aboveHorizonOnlyHeld")
        limit = self.filters.mag_limit
        if limit is not None and any(star.mag > limit for star in self.stars):
            raise _rule("magLimitHeld")
        return self


MODELS = {"SkyContextRequest": SkyContextRequest, "SkyContextReply": SkyContextReply}
