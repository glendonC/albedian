"""The observation solution family: the Python mirror of `ObservationSolutionSchema` and the schemas it is built from
in packages/contracts/src/astronomy.ts (`CelestialFinding`, `ScientificEvidence`, `SourceDetection`, `SkyPosition`,
the WCS and `PoseEstimate`; `CatalogReference` keeps its own mirror, services/contracts/catalog_reference.py).

`parse_observation_solution` judges a solution as the Zod schema does: the same fields, the same bounds, the same
rules between fields, refused at the same paths in the same words. Three things follow from Zod reading a JSON
document as JavaScript does:
- a number is finite, and never a boolean or text; 4.0 is the integer 4; an integer too large for a float is refused,
  since JavaScript reads it as Infinity;
- an optional field is left out, never null, and a key the contract does not name is dropped, not refused, but by
  the two closed records of what the detector measured (`DetectionQuality`, `StarProfile`), which refuse it;
- a refusal names the field and the rule, never the value, so no capture time or place reaches a log.

Each field's own rule is one object (a `NumberRule`, `TextRule`, `ChoiceRule` or `FlagRule`) that the model checks
with, that the dataclasses which write solutions (services/science/wcs_models.py) check with when they are built, and
that `field_table()` writes into tests/fixtures/observation_solution_contract.json, where the Zod schemas are held to
it at every edge (tests/integration/test_observation_solution_contract.py and
packages/contracts/src/__tests__/observation-solution-contract.test.ts).

services/science imports this module, so this module reads the held-out gate numbers from
services/science/solver.py only when it needs them (`held_out_gate`).
"""

from __future__ import annotations

import math
import numbers
import re
import types
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any, Optional, Union, get_args, get_origin, get_type_hints

from pydantic import BaseModel, ConfigDict, Field, GetCoreSchemaHandler, ValidationError, ValidatorFunctionWrapHandler, model_validator
from pydantic.alias_generators import to_camel
from pydantic_core import InitErrorDetails, PydanticCustomError, core_schema

from services.contracts.catalog_reference import (
    CATALOG_DISTANCE_METHODS,
    CATALOG_OBJECT_TYPES,
    CATALOG_PROVIDERS,
    CatalogDistance,
    CatalogReference,
)
from services.contracts.designation import BAYER_DESIGNATION_PATTERN, FLAMSTEED_NUMBER_MAX, FLAMSTEED_NUMBER_MIN
from services.contracts.wire import MAX_SAFE_INTEGER
from services.contracts.ingest import SHA256_HEX_PATTERN, ZOD_DATETIME_PATTERN
from services.contracts.render_layer import EVIDENCE_CLASSES
from services.contracts.wire import real_calendar_day

SOLUTION_STATUSES = ("pending", "calibrated", "approximate", "predicted_only", "unresolved", "ambiguous")
# How an approximate pose was lined up, and the beacon each method lines up from (POSE_METHOD_BEACONS in the contract)
POSE_METHOD_BEACONS = {
    "moon_camera_profile_level_head": "moon",
    "moon_horizon": "moon",
    "sun_horizon": "sun",
}
# A pose from the phone's pointing record has no beacon (ios-capture-companion design component 6)
DEVICE_ATTITUDE = "device_attitude"
POSE_METHODS = (*POSE_METHOD_BEACONS, DEVICE_ATTITUDE)
POSE_BEACONS = ("moon", "sun")
# How a solve used the phone's pointing record, in this order, and where a solution's time and place came from
POINTING_USES = ("search_hint", "pose_estimate", "moment")
MOMENT_SOURCES = ("file", "record")
# Where a pointing hint came from: the phone's pointing record, or a person's alignment of the predicted sky (align-1b)
POINTING_HINT_SOURCES = ("record", "person")
# The outside programs a calibration's first candidate may come from (anet-stage-zero), and an astrometry.net index
# file named by its file name alone (index-4117.fits, index-5206-03.fits), never a path
CANDIDATE_PROGRAMS = ("astrometry.net",)
INDEX_FILE_PATTERN = r"^index-[0-9]{4}(-[0-9]{2})?\.fits$"
# The program's own version line as one word (0.97): letters, digits and . + _ -, so never a path or a sentence
VERSION_LINE_PATTERN = r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}$"
MIN_SWEPT_WCS = 2
MAX_SWEPT_WCS = 27
# Rounding room when a swept WCS's field of view is compared with the nominal one's, in degrees [proposed: a
# billionth of a degree covers the last digits of an arctangent; one pixel more of image width moves a field of view
# by about the pixel scale, millions of times more]. SWEPT_FOV_TOLERANCE_DEG in the contract.
SWEPT_FOV_TOLERANCE_DEG = 1e-9
# What the detector measured on the frame's own pixels (quality.detection; services/science/detector.py, REALSKY's,
# fills it). The star profile's bounds are literals, the same in the contract, and REALSKY's fill imports them, so a
# width the fit leaves exactly at a bound is taken on every machine (the lead's ruling of 07:16 on 2026-11-13): the
# narrowest width 1 / sqrt(2 ln 20) px written as its double, a Gaussian whose four nearest pixels hold 5 % of its
# peak (detector.py's PROFILE_MIN_SIGMA_PX); the widest the fit's own bound; the fewest and most stars the profile is
# measured from (PROFILE_MIN_STARS, PROFILE_MAX_STARS there); and the angle's bound, pi / 2, written as its double
# (the detector folds the angle into above -pi / 2 and at most pi / 2).
PROFILE_MIN_SIGMA_PX = 0.4085389826536349
PROFILE_MAX_SIGMA_PX = 8.0
PROFILE_MIN_STARS = 10
PROFILE_MAX_STARS = 400
PROFILE_ANGLE_LIMIT_RAD = 1.5707963267948966
# What every detection's snr counts, and the measured conditions under which the detector's false rate is not
# controlled, in the one order a list of them is written in. "quantised" left with realsky-4's second version (measured
# to have no cause once the background is on a grid) and "noise_tail" stays out until its meaning is settled (the
# lead's rulings of 2026-11-13): both are refused
DETECTION_STATISTICS = ("matched", "whitened")
NOISE_LIMITS = ("near_black_clip",)

# The contract's own sentences (packages/contracts/src/astronomy.ts), word for word
LEFT_OUT_NEVER_NULL = "No field is null: one with no value is left out."
REQUIRED = "Required"
PLACEHOLDER_HASH = "A SHA-256 of 64 zeros is a placeholder, not a hash."
NAMES_ITS_SOLUTION = "Observed and catalog-matched findings belong to an ObservationSolution and name its id."
NAMES_ITS_RECORDING = "Observed and catalog-matched evidence rests on a recording and names its SHA-256."
OBSERVED_IS_MEASURED = "An observed finding is a measurement in the pixels and carries the source detection that measured it."
OBSERVED_NAMES_NO_ENTRY = "An observed finding names no catalog entry: a finding matched to the catalog is catalog_matched."
OBSERVED_HAS_NO_DISTANCE = "An observed finding holds no catalog distance: a point measured in the pixels has none."
A_MATCH_IS_OF_A_DETECTION = "A catalog_matched finding carries the source detection that was matched to the catalog."
A_MATCH_NAMES_ITS_ENTRY = "A catalog_matched finding carries the catalog entry it was matched to and names it in its evidence."
A_MATCH_HOLDS_ITS_RESIDUALS = (
    "A catalog_matched finding's evidence holds the match's residual in pixels and in arcseconds: the class stands on a "
    "held-out residual check."
)
NO_DETECTION = "A predicted or simulated finding was not measured in the pixels, so it carries no source detection."
ONLY_A_MATCH_HAS_A_RESIDUAL = (
    "Only a catalog_matched finding holds a residual: a residual is a measured difference between a detection and a catalog star."
)
ONLY_A_MATCH_IS_HELD_OUT = "Only a catalog_matched finding is verified by independent held-out stars."
# The fields of a finding the class table places, by their path in the finding (the contract's FINDING_FIELDS). A
# finding holds a field when it is there, and holds the held-out flag when it is true (the flag is always there); the
# sky position is each class's own shape, required of every class but observed
FINDING_FIELDS = (
    "observationSolutionId", "sourceDetection", "catalogRef", "inferredDistancePc", "cartesianParsecICRS",
    "evidence.sourceAssetSha256", "evidence.wcsSolutionId", "evidence.catalogReferenceId", "evidence.pixelResidualPx",
    "evidence.angularResidualArcsec", "evidence.distanceUncertaintyNote", "evidence.verifiedByIndependentHoldout",
)
_NOT_MEASURED_MAY = [
    "observationSolutionId", "catalogRef", "inferredDistancePc", "cartesianParsecICRS", "evidence.sourceAssetSha256",
    "evidence.wcsSolutionId", "evidence.catalogReferenceId", "evidence.distanceUncertaintyNote",
]
_NOT_MEASURED_MUST_NOT = {
    "sourceDetection": NO_DETECTION,
    "evidence.pixelResidualPx": ONLY_A_MATCH_HAS_A_RESIDUAL,
    "evidence.angularResidualArcsec": ONLY_A_MATCH_HAS_A_RESIDUAL,
    "evidence.verifiedByIndependentHoldout": ONLY_A_MATCH_IS_HELD_OUT,
}
_NAME_THEIRS = (
    'ScientificEvidenceSchema, of sourceAssetSha256: "Absent only for a prediction made for a time and place with no '
    'recording behind it; observed and catalog-matched findings must name theirs (CelestialFindingSchema)." '
    'celestialFindingVariant, of observationSolutionId: "Absent only for a standalone prediction or simulation made '
    'for a time and place with no ObservationSolution; observed and catalog-matched findings, and every finding '
    'inside an ObservationSolution, name theirs."'
)
_NOT_MEASURED_DOC = 'CelestialFindingSchema: "A predicted or simulated finding was not measured in the pixels, so it carries no sourceDetection."'
# What a finding of each evidence class must hold, may hold and must not hold: the contract's FINDING_CLASS_EVIDENCE
# (packages/contracts/src/astronomy.ts), the same table, written from AGENTS.md section 1 and the contract's field docs,
# and where those leave a cell unclear from the lead's rulings of 2026-11-12, which each row cites; the shared fixture
# carries it
FINDING_CLASS_EVIDENCE: dict[str, dict[str, Any]] = {
    "observed": {
        "source": [
            'AGENTS.md section 1: "observed: Measured directly from original pixels (centroid, flux, SNR, frame/time)."',
            'CelestialFindingSchema, as this unit writes it (stricter than the doc on main, which asks the '
            'sourceDetection only of an observed finding without a verified WCS): "catalog_matched, predicted and '
            'simulated findings always carry a sky position. An observed finding is a pixel measurement: it carries '
            'one only when a verified WCS maps its pixels to the sky (checked by ObservationSolutionSchema), and '
            'always the sourceDetection that measured it."',
            'catalog_matched, the class that holds a catalog entry: "Associated with an external astronomical '
            'catalog (Gaia DR3, HIP, JPL) only after an independently verified WCS calibration and held-out residual '
            'check."',
            _NAME_THEIRS,
            "lead's ruling, 2026-11-12: an observed finding holds no catalog distance and no distance note (a point measured in the "
            "pixels has no distance), and is never verified by held-out stars (only a catalog match is).",
            "lead's ruling, 2026-11-12, the observed Moon: its residuals are may here and a rule of the solution: an observed finding "
            "holds one only in an approximate solution, the status of a pose estimate, where it is the pose fit's residual "
            "(services/science/horizon_solver.py:224-225); the pose record holds no fit residual and no solution names its beacon "
            "finding, so both belong in the pose record, an open gap for its unit.",
        ],
        "must": {"observationSolutionId": NAMES_ITS_SOLUTION, "sourceDetection": OBSERVED_IS_MEASURED,
                 "evidence.sourceAssetSha256": NAMES_ITS_RECORDING},
        "mustNot": {"catalogRef": OBSERVED_NAMES_NO_ENTRY, "inferredDistancePc": OBSERVED_HAS_NO_DISTANCE,
                    "cartesianParsecICRS": OBSERVED_HAS_NO_DISTANCE, "evidence.catalogReferenceId": OBSERVED_NAMES_NO_ENTRY,
                    "evidence.distanceUncertaintyNote": OBSERVED_HAS_NO_DISTANCE,
                    "evidence.verifiedByIndependentHoldout": ONLY_A_MATCH_IS_HELD_OUT},
        "may": ["evidence.wcsSolutionId", "evidence.pixelResidualPx", "evidence.angularResidualArcsec"],
    },
    "catalog_matched": {
        "source": [
            'AGENTS.md section 1: "catalog_matched: Associated with an external astronomical catalog (Gaia DR3, HIP, JPL) only after an '
            'independently verified WCS calibration and held-out residual check."',
            'ObservationSolutionSchema: "A catalog_matched finding needs a verified WCS: catalog association follows held-out verification."',
            _NAME_THEIRS,
            "lead's ruling, 2026-11-12: a catalog match holds both residuals, in pixels and in arcseconds: the class exists only after "
            "a held-out residual check, and a per-finding claim needs its own numbers.",
        ],
        "must": {"observationSolutionId": NAMES_ITS_SOLUTION, "sourceDetection": A_MATCH_IS_OF_A_DETECTION,
                 "catalogRef": A_MATCH_NAMES_ITS_ENTRY, "evidence.sourceAssetSha256": NAMES_ITS_RECORDING,
                 "evidence.catalogReferenceId": A_MATCH_NAMES_ITS_ENTRY, "evidence.pixelResidualPx": A_MATCH_HOLDS_ITS_RESIDUALS,
                 "evidence.angularResidualArcsec": A_MATCH_HOLDS_ITS_RESIDUALS},
        "mustNot": {},
        "may": ["inferredDistancePc", "cartesianParsecICRS", "evidence.wcsSolutionId", "evidence.distanceUncertaintyNote",
                "evidence.verifiedByIndependentHoldout"],
    },
    "predicted": {
        "source": [
            'AGENTS.md section 1: "predicted: Computed from ephemeris/catalog + time/location/pose assumptions; never rendered as if '
            'recovered from camera pixels."',
            _NOT_MEASURED_DOC,
            "ObservationSolutionSchema: a solution with a predicted finding needs observedAtUTC and observerLocation.",
            "lead's ruling, 2026-11-12: a prediction holds no residual (a residual is a measured difference between a detection and a "
            "catalog star) and is never verified by held-out stars.",
            "lead's ruling, 2026-11-12: what a prediction was computed from (a catalog entry, or the ephemeris with its edition) has no "
            "structured field yet, so its catalog entry and a distance with no entry stay may: an open gap for a later unit.",
        ],
        "must": {},
        "mustNot": dict(_NOT_MEASURED_MUST_NOT),
        "may": list(_NOT_MEASURED_MAY),
    },
    "simulated": {
        "source": [
            'AGENTS.md section 1: "simulated: Catalog-geometry 3D navigation, modeled bodies and scenes, alternative '
            'viewpoints, or historical epoch models; must display explicit disclosure that the layer or view is '
            'drawn or computed by the app and not measured in the source recording, with each layer naming its own '
            'source and limits."',
            _NOT_MEASURED_DOC,
            "lead's ruling, 2026-11-12: a simulated finding needs no new field (the disclosure a person reads is the screen's fixed "
            "sentence); it holds no detection, no residual and no held-out verification, and the limitation every class holds.",
        ],
        "must": {},
        "mustNot": dict(_NOT_MEASURED_MUST_NOT),
        "may": list(_NOT_MEASURED_MAY),
    },
}
# The rules between a finding's fields that hold for every class (the contract's FINDING_EVIDENCE_RULES). A catalog
# reference id names the entry the finding carries: one that names another entry, or one where the finding carries
# none, is refused on every class (lead's ruling, 2026-11-12: it points at nothing)
FINDING_EVIDENCE_RULES = {
    "ownClass": "A finding's class is the class its evidence names.",
    "sameEntry": "A finding's catalog reference id names the catalog entry the finding carries.",
}

# The flight rule (flight-rule-0, DECIDED-BY-LEAD 24 Nov 12:55), the same as packages/contracts/src/astronomy.ts
# `flightRefusal`: a finding may be flown to when it is catalog_matched, or predicted with a catalog reference, and its
# distance interval is a usable one whose method is a star catalog's. An ephemeris distance never qualifies, and the
# Sun, the Moon and the planets are refused as beyond the flight's reach. The finding is only read: nothing upgrades it.
# The distance methods a star catalog gives: an inverse parallax, or a Bailer-Jones 2021 prior
STAR_CATALOG_DISTANCE_METHODS: tuple[str, ...] = (
    "geometric_parallax",
    "photogeometric_bailer_jones_edr3",
    "geometric_bailer_jones_edr3",
)
# The Sun, the Moon and the planets Mercury to Neptune, as their finding ids name them (finding-predicted-<key>, and
# finding-observed-moon for the Moon's spot): services/science/ephemeris.py BODIES, in its order
SOLAR_SYSTEM_BODY_KEYS: tuple[str, ...] = ("sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn", "uranus", "neptune")
_SOLAR_SYSTEM_BODY_ID = re.compile(rf"^finding-(?:predicted|observed)-(?:{'|'.join(SOLAR_SYSTEM_BODY_KEYS)})$")
_SOLAR_SYSTEM_OBJECT_TYPES = frozenset({"planet", "moon"})
EPHEMERIS_DISTANCE_METHOD = "ephemeris_au"
FLIGHT_CLASSES = ("catalog_matched", "predicted")


def is_solar_system_body_id(finding_id: Any) -> bool:
    """Whether a finding id names the Sun, the Moon or a planet (the voice backend holds only ids)."""
    return isinstance(finding_id, str) and _SOLAR_SYSTEM_BODY_ID.fullmatch(finding_id) is not None


def _usable_distance(distance: Any) -> bool:
    """A distance interval the flight can stand on, as packages/evidence `formatDistanceWithUncertainty` accepts one:
    finite numbers, a median and a lower bound above 0, and an upper bound not below the median."""
    if not isinstance(distance, Mapping):
        return False
    lower, median, upper = distance.get("lower"), distance.get("median"), distance.get("upper")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (lower, median, upper)):
        return False
    return median > 0 and lower > 0 and upper >= median


def flight_refusal(finding: Mapping[str, Any]) -> str | None:
    """Why `finding` (a CelestialFinding in its wire form) cannot be flown to, as a refusal kind of the voice contract
    (`no_catalog_distance`, `beyond_solar_system_only`), or None when it can."""
    distance = finding.get("inferredDistancePc")
    reference = finding.get("catalogRef")
    method = distance.get("method") if isinstance(distance, Mapping) else None
    object_type = reference.get("objectType") if isinstance(reference, Mapping) else None
    if is_solar_system_body_id(finding.get("id")) or method == EPHEMERIS_DISTANCE_METHOD or object_type in _SOLAR_SYSTEM_OBJECT_TYPES:
        return "beyond_solar_system_only"
    klass = finding.get("evidenceClass")
    if klass not in FLIGHT_CLASSES or (klass == "predicted" and not isinstance(reference, Mapping)):
        return "no_catalog_distance"
    if not _usable_distance(distance) or method not in STAR_CATALOG_DISTANCE_METHODS:
        return "no_catalog_distance"
    return None


def _flight_sample(about: str, refusal: str | None, finding_id: str, klass: str, *, reference: dict[str, Any] | None = None,
                   distance: dict[str, Any] | None = None) -> dict[str, Any]:
    finding: dict[str, Any] = {"id": finding_id, "evidenceClass": klass, "title": about.split(":")[0]}
    if reference is not None:
        finding["catalogRef"] = reference
    if distance is not None:
        finding["inferredDistancePc"] = distance
    return {"about": about, "finding": finding, "refusal": refusal}


def flight_rule_samples() -> list[dict[str, Any]]:
    """The findings both languages judge alike: one of each verdict for each class, and each edge of the rule."""
    star = {"objectType": "star"}
    deep = {"objectType": "deep_sky"}
    bj = {"lower": 240.0, "median": 260.0, "upper": 280.0, "method": "photogeometric_bailer_jones_edr3"}
    plx = {"lower": 7.9, "median": 8.0, "upper": 8.1, "method": "geometric_parallax"}
    bjg = {"lower": 99.0, "median": 100.0, "upper": 101.0, "method": "geometric_bailer_jones_edr3"}
    au = {"lower": 2.0e-5, "median": 2.1e-5, "upper": 2.2e-5, "method": EPHEMERIS_DISTANCE_METHOD}
    return [
        _flight_sample("Rigel: matched, Bailer-Jones", None, "finding-star-rigel", "catalog_matched", reference=star, distance=bj),
        _flight_sample("Sirius: matched, parallax", None, "finding-star-sirius", "catalog_matched", reference=star, distance=plx),
        _flight_sample("Zosma: predicted, parallax", None, "finding-predicted-star-zosma", "predicted", reference=star, distance=plx),
        _flight_sample("Gaia DR3 1: predicted, Bailer-Jones geometric", None, "finding-predicted-gaia-1", "predicted", reference=star, distance=bjg),
        _flight_sample("Vega: matched, no distance", "no_catalog_distance", "finding-star-vega", "catalog_matched", reference=star),
        _flight_sample("Tarazed: predicted, no distance", "no_catalog_distance", "finding-predicted-star-tarazed", "predicted", reference=star),
        _flight_sample("Altair: predicted, a distance but no catalog row", "no_catalog_distance", "finding-predicted-star-altair", "predicted", distance=bj),
        _flight_sample("Deneb: predicted, an unknown method", "no_catalog_distance", "finding-predicted-star-deneb", "predicted",
                       reference=star, distance={**bj, "method": "inverse_parallax"}),
        _flight_sample("Spica: matched, the upper bound below the median", "no_catalog_distance", "finding-star-spica", "catalog_matched",
                       reference=star, distance={**bj, "upper": 250.0}),
        _flight_sample("Mimosa: matched, a lower bound of 0", "no_catalog_distance", "finding-star-mimosa", "catalog_matched",
                       reference=star, distance={**bj, "lower": 0.0}),
        _flight_sample("Pearl Cluster: predicted deep sky", "no_catalog_distance", "finding-openngc-NGC3766", "predicted", reference=deep),
        _flight_sample("Star 3: observed in the pixels", "no_catalog_distance", "finding-obs-3", "observed"),
        _flight_sample("Grid: simulated, a catalog distance", "no_catalog_distance", "finding-scene-grid", "simulated", reference=star, distance=bj),
        _flight_sample("Moon: predicted, no distance", "beyond_solar_system_only", "finding-predicted-moon", "predicted"),
        _flight_sample("Sun: predicted", "beyond_solar_system_only", "finding-predicted-sun", "predicted"),
        _flight_sample("Jupiter: predicted, an ephemeris distance", "beyond_solar_system_only", "finding-predicted-jupiter", "predicted", distance=au),
        _flight_sample("Moon: observed spot", "beyond_solar_system_only", "finding-observed-moon", "observed"),
        _flight_sample("Neptune: matched by its catalog type", "beyond_solar_system_only", "finding-jpl-899", "catalog_matched",
                       reference={"objectType": "planet"}, distance=au),
        _flight_sample("Odd: predicted star with an ephemeris distance", "beyond_solar_system_only", "finding-predicted-star-odd", "predicted",
                       reference=star, distance=au),
        _flight_sample("Moon: matched by its catalog type alone", "beyond_solar_system_only", "finding-jpl-301", "catalog_matched",
                       reference={"objectType": "moon"}, distance=plx),
        _flight_sample("Mars: predicted by its catalog type alone", "beyond_solar_system_only", "finding-jpl-499", "predicted",
                       reference={"objectType": "planet"}, distance=bj),
    ]


def flight_rule_table() -> dict[str, Any]:
    """The rule as the shared fixture carries it, for the Zod side to judge the same samples alike."""
    return {
        "starCatalogDistanceMethods": list(STAR_CATALOG_DISTANCE_METHODS),
        "solarSystemBodyKeys": list(SOLAR_SYSTEM_BODY_KEYS),
        "flightClasses": list(FLIGHT_CLASSES),
        "samples": flight_rule_samples(),
    }
BEACON_OF_ITS_METHOD = (
    "A pose estimate's beacon is the one its method lines up from: the moon for moon_camera_profile_level_head and "
    "moon_horizon, the sun for sun_horizon."
)
POSE_BELONGS = "A pose estimate belongs only to an approximate solution with a WCS: it states how far that WCS may be off."
APPROXIMATE_NEEDS_POSE = "An approximate solution states how far its WCS may be off, so it carries a pose estimate."
BEACON_TERMS = (
    "A pose estimate lined up from a beacon names the beacon and its centroid's uncertainty; one from the device "
    "attitude names neither."
)
HINT_RECORDING = "A pointing hint names the recording the solution solved: its sourceAssetSha256 is the solution's sourceSha256."
HINT_POSE = (
    "A pointing hint is used as the pose estimate exactly when the solution's pose estimate is lined up from the "
    "device attitude."
)
HINT_USES = "A pointing hint lists each use at most once, in the order search_hint, pose_estimate, moment."
HINT_MOMENT_USE = "A pointing hint says moment exactly when the solution's time or its place came from the record."
HINT_CENTRE = (
    "A pointing hint gives the hinted centre and its uncertainty exactly when it was used as a search hint or the "
    "pose."
)
# The hint's fields that only a heading gives: the hinted centre and its stated uncertainty
HINT_CENTRE_FIELDS = (("centerAltDeg", "center_alt_deg"), ("centerAzDeg", "center_az_deg"), ("uncertaintyDeg", "uncertainty_deg"))
HINT_PERSON_POSE = "A pointing hint gives the roll and the field of view exactly when a person aligned it."
# The fields only a person's alignment gives: the roll and the field of view the person left the drawn sky at
HINT_PERSON_FIELDS = (("rollDeg", "roll_deg"), ("fovDiagonalDeg", "fov_diagonal_deg"))
HINT_PERSON_USE = (
    "A person's alignment is used only as a search hint and lends no time or place of its own: it lists moment "
    "exactly when the photo's pointing record lent the time or the place."
)
# What a person's alignment is used as: a search hint, and with it the moment exactly when the photo's pointing record
# lent the time or the place of the sky the person lined up (lentalign-0)
PERSON_POINTING_USES = ("search_hint",)
PERSON_POINTING_USES_LENT = ("search_hint", "moment")
HINT_OFFSET = (
    "A pointing hint gives the verified centre's offset from its h"
    "inted centre exactly on a calibrated solution whose hint gives one."
)
HINT_MOMENT = (
    "A pointing hint is lined up in the sky of a time and a place, so a solution with one has observedAtUTC and "
    "observerLocation."
)
SWEPT_ON_THE_NOMINAL_CRPIX = (
    "Every swept WCS of a pose estimate has the nominal WCS's crpixX and crpixY: all of them describe the same frame."
)
SWEPT_OF_THE_NOMINAL_SIZE = (
    "Every swept WCS of a pose estimate has the nominal WCS's image size: at its scale the same fields of view, "
    "at a larger scale none smaller, at a smaller scale none larger."
)
PENDING_HAS_NO_QUALITY = "A pending solution has no quality: no solver has run, so nothing was counted."
PENDING_NAMES_NO_SOLVER = "A pending solution names no solver: none has run."
CANDIDATE_SOURCE_CALIBRATED = (
    "A candidate's source is named only on a calibrated solution, whose WCS was refined from that candidate and "
    "passed the held-out check."
)
CANDIDATE_STAGES_SOLVED = "A pending solution names no candidate stage: no solver has run, so no stage was set to ask."
CANDIDATE_SOURCE_IN_STAGES = (
    "A solution that names a candidate's source names that program among its candidate stages."
)
NAMES_ITS_SOLVER = "Only a pending solution, or an unresolved one no solver reached, names no solver."
REPORTS_ITS_QUALITY = "A calibrated or approximate solution reports the quality it was judged on."
RESIDUAL_IS_REPORTED = "Held-out stars were paired, so their residual is reported."
NO_RESIDUAL_TO_REPORT = "No held-out star was paired, so there is no residual to report."
INLIERS_ARE_PAIRS = "Held-out inliers are held-out pairs inside the inlier gate: there cannot be more of them than pairs."
PASSES_WITH_A_WCS = "Held-out verification passes only with a WCS and the held-out residuals it was judged on."
NAMES_ITS_RECORDING_UNLESS_PENDING = "Only a pending solution with no findings names no recording."
NEEDS_ITS_TIME = "An approximate or predicted_only solution, or any solution with a predicted finding, needs observedAtUTC."
NEEDS_ITS_PLACE = "An approximate or predicted_only solution, or any solution with a predicted finding, needs observerLocation."
INSTANT_ON_A_REAL_DAY = "A UTC instant falls on a real calendar day, in a year from 1 to 9999."
FINDING_NAMES_ITS_SOLUTION = "A finding inside an ObservationSolution must name its ObservationSolution id."
MATCH_NEEDS_A_VERIFIED_WCS = "A catalog_matched finding needs a verified WCS: catalog association follows held-out verification."
OBSERVED_NEEDS_ITS_SKY_POSITION = "An observed finding in a solution with a verified WCS needs the sky position that WCS gives it."
OBSERVED_HAS_NO_SKY_POSITION = "An observed finding has no sky position without a verified WCS."
OBSERVED_RESIDUAL_OF_A_POSE = "An observed finding holds a residual only in an approximate solution, where it is the pose fit's residual."
WHITENED_HAS_ITS_PROFILE = "A detection scored by the whitened statistic holds the star profile it was built for."
NOISE_LIMITS_ONCE_IN_ORDER = "A detection lists each noise limit at most once, in the contract's order."
PROFILE_MINOR_AT_MOST_MAJOR = "A star profile's narrower width is at most its wider one."


def unrecognized_keys(keys: list[str]) -> str:
    """Zod's own sentence for keys a closed record does not name (z.object().strict()), which the parser repeats."""
    return "Unrecognized key(s) in object: " + ", ".join(f"'{key}'" for key in keys)


def held_out_gate() -> tuple[int, float]:
    """The fewest held-out inliers and the largest median held-out residual, in pixels, that a passed verification
    stands for: the plate solver's own two numbers, read where they are defined. Read when called, not when this
    module loads: services/science/solver.py imports the solution dataclasses, which import this module."""
    from services.science.solver import MAX_HELD_OUT_MEDIAN_RESIDUAL_PX, MIN_HELD_OUT_INLIERS

    return MIN_HELD_OUT_INLIERS, MAX_HELD_OUT_MEDIAN_RESIDUAL_PX


def passes_at_the_gate_only(least: int, most: float) -> str:
    """The contract's sentence for a verification marked as passed below the held-out gate."""
    return (
        f"Held-out verification passes only with at least {least} held-out inliers and a median held-out residual "
        f"of at most {most:g} px."
    )


def only_calibrated_passes(status: str) -> str:
    return f"Only a calibrated solution passes held-out verification, not a {status} one."


# =============================================================================================
# The rule each field follows on its own
# =============================================================================================

NOT_FINITE = [{"$number": "NaN"}, {"$number": "Infinity"}, {"$number": "-Infinity"}]
# A whole number JSON can write and no float can hold: JavaScript reads it as Infinity, so it is refused here
BEYOND_ANY_FLOAT = 10**400
WRONG_TYPES_FOR_A_NUMBER: list[Any] = ["1", True, False, None, [], {}]
WRONG_TYPES_FOR_TEXT: list[Any] = [0, True, None, [], {}]


class _Rule:
    """One field's own rule. As Annotated metadata it is the field's validator; `table_entry` is the same rule for
    the fixture both sides of the contract read."""

    name: str

    def problem(self, value: object) -> str | None:  # pragma: no cover - each rule defines its own
        raise NotImplementedError

    def accepted(self, value: Any) -> Any:
        """`value` as the model keeps it, or the refusal the parser reports at the field."""
        problem = self.problem(value)
        if problem is not None:
            raise PydanticCustomError("contract_field", problem)
        return value

    def __get_pydantic_core_schema__(self, source: Any, handler: GetCoreSchemaHandler) -> core_schema.CoreSchema:
        return core_schema.no_info_plain_validator_function(self.accepted)


def _bound_text(bound: int | float) -> str:
    return str(bound) if isinstance(bound, int) else f"{bound:g}"


@dataclass(frozen=True)
class NumberRule(_Rule):
    """A number as the contract's z.number() chain takes one from JSON: finite, inside its bounds, a whole number
    when `integer`, and never a boolean or text."""

    name: str
    ge: float | None = None
    gt: float | None = None
    le: float | None = None
    integer: bool = False

    def problem(self, value: object, *, any_real: bool = False) -> str | None:
        """Why `value` is outside the rule, or None. `any_real` also takes the numbers a solver computes with
        (NumPy's), which a JSON document never holds."""
        if isinstance(value, bool) or not isinstance(value, numbers.Real if any_real else (int, float)):
            return "This value is not a number."
        try:
            number = float(value)
        except OverflowError:
            # An integer beyond the largest float: JavaScript reads it as Infinity, and the schema refuses that
            return "This number is beyond the largest a float holds."
        if not math.isfinite(number):
            return "This number is not finite."
        if self.integer and not number.is_integer():
            return "This number is not a whole number."
        if self.ge is not None and not number >= self.ge:
            return f"This number is below {_bound_text(self.ge)}."
        if self.gt is not None and not number > self.gt:
            return f"This number is not above {_bound_text(self.gt)}."
        if self.le is not None and not number <= self.le:
            return f"This number is above {_bound_text(self.le)}."
        return None

    def accepted(self, value: Any) -> Any:
        super().accepted(value)
        if self.integer:
            return int(value)
        number = float(value)
        return 0.0 if number == 0.0 else number

    def table_entry(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"type": "number"}
        if self.integer:
            entry["integer"] = True
        for key in ("ge", "gt", "le"):
            if getattr(self, key) is not None:
                entry[key] = getattr(self, key)
        low = self.ge if self.ge is not None else self.gt
        if self.integer:
            # The smallest and largest whole numbers the rule's own bounds take; every sample is made from them, so
            # the table agrees with the rule's judgment for lower bounds alone, upper bounds alone, narrow spans, or none
            bounds = ([math.ceil(self.ge)] if self.ge is not None else []) + ([math.floor(self.gt) + 1] if self.gt is not None else [])
            lo = max(bounds) if bounds else None
            hi = None if self.le is None else math.floor(self.le)
            anchor = -3 if lo is None and hi is None else (hi - 2 if lo is None else lo)
            pair = [anchor + 0.5, anchor + 1.5]
            fractions = [0.5, 1.5] if any(x.is_integer() for x in pair) else pair
            if hi is None:
                accept: list[Any] = [anchor, float(min(anchor + 2, 9007199254740991)), 9007199254740991]
            elif lo is None:
                accept = [hi, float(max(hi - 2, -9007199254740991)), -9007199254740991]
            else:
                accept = [lo, float(min(lo + 2, hi)), hi]
            refuse: list[Any] = (
                [hi + 1, float(hi + 1), *reversed(fractions)]
                if lo is None and hi is not None
                else ([] if lo is None else [lo - 1]) + fractions + ([] if hi is None else [hi + 1, float(hi + 1)])
            )
        else:
            lowest = self.ge if self.ge is not None else math.nextafter(self.gt, math.inf) if self.gt is not None else -1e308
            highest = self.le if self.le is not None else 1e308
            inside = lowest + 0.75 * (highest - lowest) if low is not None and self.le is not None else (low or 0.0) + 1.5
            accept = [lowest, inside, int(inside) if int(inside) > (low if low is not None else -math.inf) else inside, highest]
            if low == 0.0 and self.ge is not None:
                accept.append(-0.0)
            refuse = []
            if self.ge is not None:
                refuse.append(math.nextafter(self.ge, -math.inf))
            if self.gt is not None:
                refuse += [self.gt, math.nextafter(self.gt, -math.inf)] + ([-0.0] if self.gt == 0.0 else [])
            if self.le is not None:
                refuse.append(math.nextafter(self.le, math.inf))
        return {**entry, "accept": accept, "refuse": [*refuse, *NOT_FINITE, BEYOND_ANY_FLOAT, *WRONG_TYPES_FOR_A_NUMBER]}


def _any_day(_: str) -> str | None:
    return None


def _real_day(value: str) -> str | None:
    try:
        real_calendar_day(value)
    except ValueError:
        return INSTANT_ON_A_REAL_DAY
    return None


@dataclass(frozen=True)
class TextRule(_Rule):
    """Text as the contract's z.string() chain takes it: text only, at least `min_length` long, in `pattern` when
    there is one. `placeholder` is one text the pattern takes and the contract still refuses, with its sentence;
    `day_rule` holds a time the pattern takes to the wire module's real calendar day, from year 1 to 9999."""

    name: str
    min_length: int = 0
    pattern: str | None = None
    form: str | None = None
    placeholder: tuple[str, str] | None = None
    day_rule: Callable[[str], str | None] = _any_day
    accept: tuple[str, ...] = ()
    refuse: tuple[str, ...] = ()

    def problem(self, value: object) -> str | None:
        if not isinstance(value, str):
            return "This value is not text."
        if len(value) < self.min_length:
            return "This text is empty."
        # fullmatch: under match, a pattern's $ would also take a line break at the end
        if self.pattern is not None and _compiled(self.pattern).fullmatch(value) is None:
            return f"This text is not {self.form}."
        if self.placeholder is not None and _compiled(self.placeholder[0]).fullmatch(value) is not None:
            return self.placeholder[1]
        return self.day_rule(value)

    def table_entry(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"type": "text", "minLength": self.min_length}
        if self.pattern is not None:
            entry["pattern"] = self.name
        accept = list(self.accept) if self.pattern is not None else ["x", "A longer text, with spaces."] + ([""] if self.min_length == 0 else [])
        refuse = [*self.refuse, *([""] if self.min_length > 0 and self.pattern is None else [])]
        return {**entry, "accept": accept, "refuse": [*refuse, *WRONG_TYPES_FOR_TEXT]}


@dataclass(frozen=True)
class ChoiceRule(_Rule):
    """One of the values the contract lists (z.enum, z.literal or a union of literals). A listed number is taken as
    JavaScript compares one: 1.0 is 1, and true is not. `also_refused` names values a reader might expect the rule to
    take, each tried at the field's edges and refused there."""

    name: str
    values: tuple[Any, ...]
    also_refused: tuple[Any, ...] = ()

    def problem(self, value: object) -> str | None:
        if isinstance(value, bool) or not isinstance(value, (str, int, float)) or value not in self.values:
            return f"This value is not one of {', '.join(str(listed) for listed in self.values)}."
        return None

    def accepted(self, value: Any) -> Any:
        super().accepted(value)
        return self.values[self.values.index(value)]

    def table_entry(self) -> dict[str, Any]:
        first = self.values[0]
        if isinstance(first, str):
            # Accepting one listed value in place of another is a rule between fields, so none is tried on its own
            accept: list[Any] = []
            refuse: list[Any] = ["", first.swapcase(), f"{first} ", *self.also_refused, *WRONG_TYPES_FOR_TEXT]
        else:
            accept = [*self.values, float(first)]
            refuse = [0, 2, 1.5, -1.5, *NOT_FINITE[:2], "1", True, None, [], {}]
        return {"type": "choice", "values": list(self.values), "accept": accept, "refuse": refuse}


@dataclass(frozen=True)
class FlagRule(_Rule):
    """True or false, as z.boolean() takes it: never 0, 1 or text."""

    name: str

    def problem(self, value: object) -> str | None:
        return None if isinstance(value, bool) else "This value is not true or false."

    def table_entry(self) -> dict[str, Any]:
        return {"type": "flag", "accept": [True, False], "refuse": [0, 1, "true", None, [], {}]}


@dataclass(frozen=True)
class Items:
    """How many items a list holds, for the field table; the list's own Field carries the same two numbers."""

    least: int | None = None
    most: int | None = None


@lru_cache(maxsize=None)
def _compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def _listed(item: Any, least: int | None = None, most: int | None = None) -> Any:
    """A JSON array of `item` (z.array), with its length bounds when it has any."""
    return Annotated[list[item], Field(min_length=least, max_length=most), Items(least, most)]


NUMBER = NumberRule("number")
NON_NEGATIVE = NumberRule("nonNegative", ge=0.0)
POSITIVE = NumberRule("positive", gt=0.0)
DEGREES_0_TO_360 = NumberRule("degrees0to360", ge=0.0, le=360.0)
DEGREES_90 = NumberRule("degrees90", ge=-90.0, le=90.0)
DEGREES_180 = NumberRule("degrees180", ge=-180.0, le=180.0)
FIELD_OF_VIEW = NumberRule("fieldOfView", gt=0.0, le=175.0)
# A pointing hint's stated uncertainty and a verified centre's offset from it: angles on the sky, at most half a turn
HINT_UNCERTAINTY = NumberRule("hintUncertaintyDeg", gt=0.0, le=180.0)
SKY_OFFSET = NumberRule("skyOffsetDeg", ge=0.0, le=180.0)
DISTORTION = NumberRule("distortion", ge=-0.25, le=0.25)
COUNT = NumberRule("count", ge=0.0, le=MAX_SAFE_INTEGER, integer=True)
POSITIVE_COUNT = NumberRule("positiveCount", gt=0.0, le=MAX_SAFE_INTEGER, integer=True)
TEXT = TextRule("text")
NON_EMPTY_TEXT = TextRule("nonEmptyText", min_length=1)
INDEX_FILE = TextRule(
    "indexFile",
    pattern=INDEX_FILE_PATTERN,
    form="an astrometry.net index file's name, never a path",
    accept=("index-4117.fits", "index-4107.fits", "index-5206-03.fits"),
    refuse=("/opt/astrometry/index-4117.fits", "index-4117", "index-41170.fits", "index-4117.fits\n", "4117.fits", ""),
)
VERSION_LINE = TextRule(
    "versionLine",
    pattern=VERSION_LINE_PATTERN,
    form="a program's version as one word, never a path",
    accept=("0.97", "0.97-stand-in", "0.98+git.1", "2"),
    refuse=("/opt/homebrew/bin/solve-field", "0.97 (built here)", "C:\\astrometry", "-0.97", ".97", "", "0.97\n"),
)
SHA256 = TextRule(
    "sha256",
    pattern=SHA256_HEX_PATTERN,
    form="a SHA-256 of 64 lowercase hex digits",
    placeholder=("^0+$", PLACEHOLDER_HASH),
    accept=("0123456789abcdef" * 4, "0" * 63 + "1"),
    refuse=("0" * 64, "0123456789ABCDEF" * 4, "0123456789abcdef" * 3 + "0123456789abcde", "0123456789abcdef" * 4 + "0",
            "0123456789abcdef" * 4 + "\n", "0123456789abcdeg" * 4, ""),
)
# The year 2030 in full-width digits, which are digits to Unicode and not to the contract's pattern
FULLWIDTH_2030 = "".join(chr(0xFF10 + digit) for digit in (2, 0, 3, 0))
# Zod's own pattern for z.string().datetime(): a calendar day of the years 0000 to 9999, seconds and their fraction
# optional, then Z. Year 0000 is no year to Python's dates, so the time is also held to the wire module's real day,
# from year 1 to 9999, on both sides. The examples are synthetic moments and forms, none of them a recording's
UTC_INSTANT = TextRule(
    "utcInstant",
    pattern=ZOD_DATETIME_PATTERN,
    form="a UTC date and time ending in Z",
    day_rule=_real_day,
    accept=("2030-01-01T02:00:00Z", "2030-01-01T02:00Z", "2030-01-01T02:00:00.5Z", "2030-01-01T02:00:00.123456789Z",
            "2032-02-29T23:59:59Z", "0001-01-01T00:00:00Z", "9999-12-31T23:59:59.999Z"),
    refuse=("0000-02-29T00:00:00Z", "0000-12-31T23:59:59.999Z", "2030-01-01T02:00:00+00:00", "2030-01-01T02:00:00", "2030-01-01 02:00:00Z", "2031-02-29T00:00:00Z",
            "2031-02-30T00:00:00Z", "2031-04-31T00:00:00Z", "2030-13-01T00:00:00Z", "2030-01-01T24:00:00Z",
            "2030-01-01T02:60:00Z", "2030-01-01T02:00:60Z", "2030-01-01T02:00:00Z\n", " 2030-01-01T02:00:00Z",
            "2030-01-01t02:00:00z", f"{FULLWIDTH_2030}-01-01T02:00:00Z", "2030-01-01T02:00:00.Z", "2030-1-1T02:00:00Z", ""),
)
FLAG = FlagRule("boolean")
SOLUTION_STATUS = ChoiceRule("solutionStatus", SOLUTION_STATUSES)
EVIDENCE_CLASS = ChoiceRule("evidenceClass", tuple(EVIDENCE_CLASSES))
POSE_METHOD = ChoiceRule("poseMethod", POSE_METHODS)
POSE_BEACON = ChoiceRule("poseBeacon", POSE_BEACONS)
POINTING_USE = ChoiceRule("pointingUse", POINTING_USES)
MOMENT_SOURCE = ChoiceRule("momentSource", MOMENT_SOURCES)
POINTING_HINT_SOURCE = ChoiceRule("pointingHintSource", POINTING_HINT_SOURCES)
CANDIDATE_PROGRAM = ChoiceRule("candidateProgram", CANDIDATE_PROGRAMS)
PROJECTION = ChoiceRule("projection", ("TAN",))
PARITY = ChoiceRule("parity", (1, -1))
CATALOG_PROVIDER = ChoiceRule("catalogProvider", tuple(CATALOG_PROVIDERS))
CATALOG_OBJECT_TYPE = ChoiceRule("catalogObjectType", tuple(CATALOG_OBJECT_TYPES))
CATALOG_DISTANCE_METHOD = ChoiceRule("catalogDistanceMethod", tuple(CATALOG_DISTANCE_METHODS))
# The detector's measures: observed and nothing else, one of its two statistics, its one limit and never the
# quantised flag or the noise tail, and the star profile's literal bounds (either width alone is held to both ends; their order is the detection's
# rule)
DETECTION_EVIDENCE_CLASS = ChoiceRule("detectionEvidenceClass", ("observed",), also_refused=("catalog_matched", "predicted", "simulated"))
DETECTION_STATISTIC = ChoiceRule("detectionStatistic", DETECTION_STATISTICS)
NOISE_LIMIT = ChoiceRule("noiseLimit", NOISE_LIMITS, also_refused=("quantised", "noise_tail"))
PROFILE_SIGMA = NumberRule("profileSigmaPx", ge=PROFILE_MIN_SIGMA_PX, le=PROFILE_MAX_SIGMA_PX)
PROFILE_ANGLE = NumberRule("profileAngleRad", gt=-PROFILE_ANGLE_LIMIT_RAD, le=PROFILE_ANGLE_LIMIT_RAD)
PROFILE_STARS = NumberRule("profileStars", ge=PROFILE_MIN_STARS, le=PROFILE_MAX_STARS, integer=True)

Number = Annotated[float, NUMBER]
NonNegative = Annotated[float, NON_NEGATIVE]
Positive = Annotated[float, POSITIVE]
Degrees0To360 = Annotated[float, DEGREES_0_TO_360]
Degrees90 = Annotated[float, DEGREES_90]
Degrees180 = Annotated[float, DEGREES_180]
FieldOfView = Annotated[float, FIELD_OF_VIEW]
Distortion = Annotated[float, DISTORTION]
Count = Annotated[int, COUNT]
PositiveCount = Annotated[int, POSITIVE_COUNT]
Text = Annotated[str, TEXT]
NonEmptyText = Annotated[str, NON_EMPTY_TEXT]
Sha256 = Annotated[str, SHA256]
UtcInstant = Annotated[str, UTC_INSTANT]
Flag = Annotated[bool, FLAG]

Issue = tuple[tuple[Any, ...], str]


def _refusal(title: str, issues: list[Issue]) -> ValidationError:
    """The issues as one refusal, each at its own path under the object it was found in; no value is kept."""
    return ValidationError.from_exception_data(
        title,
        [InitErrorDetails(type=PydanticCustomError("contract_rule", message), loc=path, input=None) for path, message in issues],
        hide_input=True,
    )


@lru_cache(maxsize=None)
def _wire_names(model: type[BaseModel]) -> frozenset[str]:
    return frozenset(field.alias or name for name, field in model.model_fields.items())


@lru_cache(maxsize=None)
def _optional_wire_names(model: type[BaseModel]) -> frozenset[str]:
    return frozenset(str(field.alias) for field in model.model_fields.values() if not field.is_required())


class _Contract(BaseModel):
    """A loose z.object under the contract's camelCase names: unknown keys dropped, an optional field left out and
    never null, and no input value kept in a refusal."""

    model_config = ConfigDict(alias_generator=to_camel, extra="ignore", frozen=True, hide_input_in_errors=True, strict=True)

    def _cross_field_issues(self) -> list[Issue]:
        return []

    @model_validator(mode="wrap")
    @classmethod
    def _wire_object(cls, data: Any, handler: ValidatorFunctionWrapHandler) -> Any:
        if not isinstance(data, dict):
            return handler(data)
        optional = _optional_wire_names(cls)
        issues: list[Issue] = [((key,), LEFT_OUT_NEVER_NULL) for key, value in data.items() if value is None and key in optional]
        if issubclass(cls, _Closed):
            unknown = [key for key in data if key not in _wire_names(cls)]
            if unknown:
                issues.append(((), unrecognized_keys(unknown)))
        if not issues:
            built = handler(data)
            cross = built._cross_field_issues()
            if cross:
                raise _refusal(cls.__name__, cross)
            return built
        try:
            handler(data)
        except ValidationError as exc:
            prior: list[Issue] = [(err["loc"], err["msg"]) for err in exc.errors()]
            raise _refusal(cls.__name__, [*prior, *issues]) from None
        raise _refusal(cls.__name__, issues)

    def to_contract_dict(self) -> dict[str, Any]:
        """The contract dict this was parsed from: wire names, and no key for a field that was left out."""
        return self.model_dump(by_alias=True, exclude_none=True)


class _Closed(_Contract):
    """A closed z.object (`.strict()`): a key the record does not name, a field's Python name among them, is refused
    at the record itself in Zod's words, as the wire rules ask; the older records of the family still drop one."""


class SourceDetection(_Contract):
    asset_id: NonEmptyText
    frame_time_sec: NonNegative
    x_px: NonNegative
    y_px: NonNegative
    flux: Positive
    snr: Positive
    fwhm_px: NonNegative
    centroid_uncertainty_px: Positive


class SkyPosition(_Contract):
    ra_deg: Degrees0To360
    dec_deg: Degrees90
    uncertainty_arcsec: NonNegative
    alt_deg: Optional[Degrees90] = None
    az_deg: Optional[Degrees0To360] = None


class ScientificEvidence(_Contract):
    id: NonEmptyText
    evidence_class: Annotated[str, EVIDENCE_CLASS]
    verification_method: NonEmptyText
    # Absent only for a prediction made for a time and place with no recording behind it
    source_asset_sha256: Optional[Sha256] = None
    wcs_solution_id: Optional[Text] = None
    catalog_reference_id: Optional[Text] = None
    # The two residuals: on a catalog match, its detection's distance from its catalog star under the WCS, a held-out
    # residual only where verified_by_independent_holdout says so; on an observed finding, only in an approximate
    # solution, the pose fit's own residual, which checks nothing (the solution's rule). Neither is agreement unless
    # that flag is true.
    angular_residual_arcsec: Optional[NonNegative] = None
    pixel_residual_px: Optional[NonNegative] = None
    distance_uncertainty_note: Optional[Text] = None
    limitations: _listed(Text, 1)
    verified_by_independent_holdout: Flag


class InferredDistance(_Contract):
    """A finding's distance interval in parsecs, and the method that gives it (`inferredDistancePc`)."""

    lower: Positive
    median: Positive
    upper: Positive
    method: NonEmptyText


class CelestialFinding(_Contract):
    """One finding, under its evidence class. `catalog_matched`, `predicted` and `simulated` findings carry a sky
    position; an `observed` one carries its detection, and a sky position only under a verified WCS (a rule of the
    solution); a `predicted` or `simulated` one carries no detection."""

    id: NonEmptyText
    observation_solution_id: Optional[NonEmptyText] = None
    evidence_class: Annotated[str, EVIDENCE_CLASS]
    title: NonEmptyText
    subtitle: NonEmptyText
    source_detection: Optional[SourceDetection] = None
    catalog_ref: Optional[CatalogReference] = None
    sky_position: Optional[SkyPosition] = None
    inferred_distance_pc: Optional[InferredDistance] = None
    cartesian_parsec_icrs: Optional[_listed(Number, 3, 3)] = Field(default=None, alias="cartesianParsecICRS")
    evidence: ScientificEvidence

    @model_validator(mode="after")
    def _rules(self) -> CelestialFinding:
        issues = _finding_issues(self)
        if issues:
            raise _refusal("CelestialFinding", issues)
        return self


class WCSCalibrationModel(_Contract):
    projection: Annotated[str, PROJECTION]
    crval_ra_deg: Degrees0To360
    crval_dec_deg: Degrees90
    crpix_x: Number
    crpix_y: Number
    scale_deg_per_px: Positive
    rotation_deg: Number
    radial_distortion_k1: Distortion
    radial_distortion_k2: Distortion
    parity: Annotated[int, PARITY]
    fov_horizontal_deg: FieldOfView
    fov_vertical_deg: FieldOfView


class PoseEstimate(_Contract):
    """An approximate pointing and its uncertainty, lined up from a beacon and stated assumptions, never checked
    against stars."""

    method: Annotated[str, POSE_METHOD]
    # Present exactly for the methods that line up from a beacon, as is the beacon's uncertainty below
    beacon: Optional[Annotated[str, POSE_BEACON]] = None
    center_alt_deg: Degrees90
    center_az_deg: Degrees0To360
    roll_deg: Degrees180
    fov_diagonal_deg: FieldOfView
    fov_source: NonEmptyText
    assumptions: _listed(NonEmptyText, 1)
    swept_wcs: _listed(WCSCalibrationModel, MIN_SWEPT_WCS, MAX_SWEPT_WCS)
    max_center_offset_deg: NonNegative
    beacon_centroid_uncertainty_deg: Optional[NonNegative] = None

    @model_validator(mode="after")
    def _rules(self) -> PoseEstimate:
        issues = pose_beacon_issues(self.method, self.beacon, self.beacon_centroid_uncertainty_deg)
        if issues:
            raise _refusal("PoseEstimate", issues)
        return self


class CandidateSource(_Contract):
    """Where a calibration's first candidate came from when an outside program gave it (`candidateSource`,
    anet-stage-zero): the program, its own version line, and the index file that matched, by file name. The WCS is
    the app's own association, refinement and held-out check's; no time, place or path is in it."""

    program: Annotated[str, CANDIDATE_PROGRAM]
    version: Annotated[str, VERSION_LINE]
    index_file: Annotated[str, INDEX_FILE]


class PointingHint(_Contract):
    """How a solve used a pointing hint (`pointingHint`): where it came from (the phone's pointing record, or a person's
    alignment of the predicted sky, align-1b), the photo, what it was used as, the hinted frame centre in altitude and
    azimuth with its stated uncertainty, a person's roll and field of view, the verified centre's offset from it on a
    calibrated solution, and where the solution's time and place came from. Never a measurement of the sky."""

    source: Annotated[str, POINTING_HINT_SOURCE]
    source_asset_sha256: Sha256
    used_as: _listed(Annotated[str, POINTING_USE], 1, len(POINTING_USES))
    # Present exactly when the hint was used as a search hint or the pose: only a heading gives a centre
    center_alt_deg: Optional[Degrees90] = None
    center_az_deg: Optional[Degrees0To360] = None
    uncertainty_deg: Optional[Annotated[float, HINT_UNCERTAINTY]] = None
    verified_center_offset_deg: Optional[Annotated[float, SKY_OFFSET]] = None
    # Present exactly when a person aligned the hint (align-1b)
    roll_deg: Optional[Degrees180] = None
    fov_diagonal_deg: Optional[FieldOfView] = None
    time_from: Annotated[str, MOMENT_SOURCE]
    place_from: Annotated[str, MOMENT_SOURCE]

    @model_validator(mode="after")
    def _rules(self) -> PointingHint:
        issues = hint_centre_issues(self) + hint_person_issues(self)
        if not listed_once_each_in_order(self.used_as, POINTING_USES):
            issues.insert(0, (("usedAs",), HINT_USES))
        if issues:
            raise _refusal("PointingHint", issues)
        return self


def hint_centre_issues(hint: Any) -> list[Issue]:
    """The hinted centre and its uncertainty are given exactly when the hint was a search hint or the pose."""
    centred = hint_is_centred(hint.used_as)
    return [((wire,), HINT_CENTRE) for wire, name in HINT_CENTRE_FIELDS if (getattr(hint, name) is not None) != centred]


def hint_person_issues(hint: Any) -> list[Issue]:
    """A person's alignment gives its roll and field of view, and only a person's; it is only a search hint and lends no
    time or place of its own (align-1b). It says moment exactly when the photo's pointing record lent the time or the
    place of the sky the person lined up, which timeFrom and placeFrom name (lentalign-0)."""
    person = hint.source == "person"
    issues = [((wire,), HINT_PERSON_POSE) for wire, name in HINT_PERSON_FIELDS if (getattr(hint, name) is not None) != person]
    lent = hint.time_from == "record" or hint.place_from == "record"
    if person and tuple(hint.used_as) != (PERSON_POINTING_USES_LENT if lent else PERSON_POINTING_USES):
        issues.append((("usedAs",), HINT_PERSON_USE))
    return issues


def hint_is_centred(used_as: Any) -> bool:
    """A hint has a hinted centre exactly when it was a search hint or the pose; a moment-only one has none."""
    return "search_hint" in used_as or "pose_estimate" in used_as


class ObserverLocation(_Contract):
    """Where a solution's recording was made (`observerLocation`)."""

    lat_deg: Degrees90
    lon_deg: Degrees180
    place_name: Optional[Text] = None


class SolverIdentity(_Contract):
    """Which solver produced a solution, and against which catalog or model (`solver`)."""

    name: NonEmptyText
    version: NonEmptyText
    catalog_release: NonEmptyText


class StarProfile(_Closed):
    """The frame's measured star (`quality.detection.starProfile`): an elliptical Gaussian fitted to the median of the
    frame's bright, isolated, unsaturated stars. Widths in pixels, the angle of the wider axis from +x toward +y in
    radians (it means nothing when the widths are equal), and how many stars it was measured from. That the narrower
    width is at most the wider is the detection's rule."""

    sigma_major_px: Annotated[float, PROFILE_SIGMA]
    sigma_minor_px: Annotated[float, PROFILE_SIGMA]
    angle_rad: Annotated[float, PROFILE_ANGLE]
    stars: Annotated[int, PROFILE_STARS]


class DetectionQuality(_Closed):
    """What the detector measured on the pixels the solution's detections were measured on (`quality.detection`):
    the statistic every detection's snr counts, the noise limits it measured (none raised is an empty list), and the
    star profile a whitened statistic was built for. It says itself that it was observed."""

    evidence_class: Annotated[str, DETECTION_EVIDENCE_CLASS]
    statistic: Annotated[str, DETECTION_STATISTIC]
    noise_limits: _listed(Annotated[str, NOISE_LIMIT])
    star_profile: Optional[StarProfile] = None

    def _cross_field_issues(self) -> list[Issue]:
        profile = self.star_profile
        widths = None if profile is None else (profile.sigma_major_px, profile.sigma_minor_px)
        return detection_issues(self.statistic, self.noise_limits, widths)


class SolutionQuality(_Contract):
    """What the solver measured (`quality`). The rules between these fields are the solution's; the detection keeps
    its own."""

    detected_candidate_count: Count
    matched_inlier_count: Count
    held_out_pair_count: Count
    held_out_inliers: Count
    median_residual_px: Optional[NonNegative] = None
    p95_residual_px: Optional[NonNegative] = None
    median_residual_arcsec: Optional[NonNegative] = None
    passed_independent_verification: Flag
    detection: Optional[DetectionQuality] = None


class ObservationSolution(_Contract):
    """A solved, or not yet solved, recording: its status, WCS, quality, findings and warnings."""

    id: NonEmptyText
    source_asset_id: NonEmptyText
    # Absent only for a pending solution with no findings
    source_sha256: Optional[Sha256] = None
    frame_time_sec: NonNegative
    revision: PositiveCount
    status: Annotated[str, SOLUTION_STATUS]
    observed_at_utc: Optional[UtcInstant] = Field(default=None, alias="observedAtUTC")
    observer_location: Optional[ObserverLocation] = None
    solver: Optional[SolverIdentity] = None
    wcs: Optional[WCSCalibrationModel] = None
    quality: Optional[SolutionQuality] = None
    findings: _listed(CelestialFinding)
    warnings: _listed(Text)
    pose_estimate: Optional[PoseEstimate] = None
    pointing_hint: Optional[PointingHint] = None
    candidate_source: Optional[CandidateSource] = None
    # The outside programs a solve was set to ask for a first candidate once its own searches calibrated nothing
    # (anet-setting-record-one): the opt-in stage on and usable, the program found and an index file in the person's
    # folder. Present on every solve made with the stage on, whatever its status, and absent whenever it was off, so a
    # record made with it off is the record made before the field; any solution that names a candidateSource also names
    # that program here (anet-contract-tie-one). With the stage on, a stored solution that did not calibrate and does
    # not name the program is solved again; a calibrated one never is. Never on a pending solution, which no solver has
    # run on. No path, time or place is in it. It is a setting, not an event: no word a person reads may say on its
    # strength that the outside program was tried; only candidateSource says a program gave the candidate (the lead's
    # decision of 2026-11-25).
    candidate_stages: Optional[_listed(Annotated[str, CANDIDATE_PROGRAM], 1, len(CANDIDATE_PROGRAMS))] = None

    @property
    def has_verified_wcs(self) -> bool:
        """Whether the WCS passed independent held-out verification (`hasVerifiedWcs` in the contract)."""
        return self.wcs is not None and self.quality is not None and self.quality.passed_independent_verification

    @model_validator(mode="after")
    def _rules(self) -> ObservationSolution:
        issues = _solution_issues(self)
        if issues:
            raise _refusal("ObservationSolution", issues)
        return self


# =============================================================================================
# The rules between fields, as the contract's superRefine blocks state them
# =============================================================================================


def _wire_field(record: Any, path: str) -> Any:
    """The value at a finding's wire path (`evidence.catalogReferenceId`), in a parsed record (read through each
    model's own wire names) or in a wire dict; None when a field on the way is left out."""
    value: Any = record
    for wire_name in path.split("."):
        if value is None:
            return None
        if isinstance(value, Mapping):
            value = value.get(wire_name)
        else:
            names = {field.alias or name: name for name, field in type(value).model_fields.items()}
            value = getattr(value, names[wire_name])
    return value


def _holds(finding: Any, path: str) -> bool:
    """Whether a finding holds a field of FINDING_FIELDS: it is there, and for the held-out flag, it is true."""
    value = _wire_field(finding, path)
    return value is True if path == "evidence.verifiedByIndependentHoldout" else value is not None


def finding_class_issues(table: Mapping[str, Mapping[str, Any]], finding: Any) -> list[Issue]:
    """What a finding claims and has no evidence for, by `table` (FINDING_CLASS_EVIDENCE, or another table a test
    makes), as the contract's findingClassIssues finds it and in its order: each `must` field not held, each `mustNot`
    field held, evidence naming another class, and a catalog reference id naming another entry than the one carried, or
    naming one where none is carried. `finding` is a parsed record or a wire dict."""
    evidence_class = _wire_field(finding, "evidenceClass")
    own = table[evidence_class]
    issues: list[Issue] = []
    for path, sentence in own["must"].items():
        if not _holds(finding, path):
            issues.append((tuple(path.split(".")), sentence))
    for path, sentence in own["mustNot"].items():
        if _holds(finding, path):
            issues.append((tuple(path.split(".")), sentence))
    if _wire_field(finding, "evidence.evidenceClass") != evidence_class:
        issues.append((("evidence", "evidenceClass"), FINDING_EVIDENCE_RULES["ownClass"]))
    named = _wire_field(finding, "evidence.catalogReferenceId")
    if named is not None and named != _wire_field(finding, "catalogRef.id"):
        issues.append((("evidence", "catalogReferenceId"), FINDING_EVIDENCE_RULES["sameEntry"]))
    return issues


def _finding_issues(finding: CelestialFinding) -> list[Issue]:
    if finding.evidence_class != "observed" and finding.sky_position is None:
        # Only an observed finding's sky position is optional; Zod stops at a missing required field
        return [(("skyPosition",), REQUIRED)]
    return finding_class_issues(FINDING_CLASS_EVIDENCE, finding)


def _of_another_image_size(swept: WCSCalibrationModel, nominal: WCSCalibrationModel) -> bool:
    """Whether no single image size gives both WCS their fields of view. The contract's WCS carries no width or
    height, so this is all it can tell: one image at one scale has one field of view, and a larger scale never a
    smaller one. A swept WCS of another size whose fields of view move the same way as its scale is not caught here;
    the dataclass that writes solutions holds both sizes and compares them (services/science/wcs_models.py)."""
    for mine, theirs in (
        (swept.fov_horizontal_deg, nominal.fov_horizontal_deg),
        (swept.fov_vertical_deg, nominal.fov_vertical_deg),
    ):
        wider_by = mine - theirs
        if swept.scale_deg_per_px == nominal.scale_deg_per_px:
            if abs(wider_by) > SWEPT_FOV_TOLERANCE_DEG:
                return True
        elif swept.scale_deg_per_px > nominal.scale_deg_per_px:
            if wider_by < -SWEPT_FOV_TOLERANCE_DEG:
                return True
        elif wider_by > SWEPT_FOV_TOLERANCE_DEG:
            return True
    return False


def listed_once_each_in_order(limits: Any, order: tuple[str, ...] = NOISE_LIMITS) -> bool:
    """Whether `limits` lists each value at most once and in `order`'s order (true for none at all); every value is one
    of `order`'s. The detection's noise limits are held to it with NOISE_LIMITS, so a later limit enters by that list
    alone; the fixture's noiseLimitOrderCases hold it, on both sides, to an order of two values."""
    rank = {value: index for index, value in enumerate(order)}
    return all(limit in rank for limit in limits) and all(
        rank[earlier] < rank[later] for earlier, later in zip(limits, limits[1:])
    )


def detection_issues(statistic: str, noise_limits: Any, widths: tuple[float, float] | None) -> list[Issue]:
    """The detection's rules between its fields, at their paths under it: a whitened statistic holds its star profile,
    each noise limit is listed once in NOISE_LIMITS' order, and the narrower width is at most the wider (`widths` is
    the profile's (wider, narrower), None without one). Pure, so the records that write a detection
    (services/science/wcs_models.py) judge by the same function as the parser."""
    issues: list[Issue] = []
    if statistic == "whitened" and widths is None:
        issues.append((("starProfile",), WHITENED_HAS_ITS_PROFILE))
    if not listed_once_each_in_order(noise_limits):
        issues.append((("noiseLimits",), NOISE_LIMITS_ONCE_IN_ORDER))
    if widths is not None and widths[1] > widths[0]:
        issues.append((("starProfile", "sigmaMinorPx"), PROFILE_MINOR_AT_MOST_MAJOR))
    return issues


def pose_beacon_issues(method: str, beacon: Any, beacon_uncertainty: Any) -> list[Issue]:
    """A beacon method names its beacon and the beacon's uncertainty, and the beacon is the method's; the device
    attitude names neither."""
    issues: list[Issue] = []
    lines_up_from = POSE_METHOD_BEACONS.get(method)
    for key, value in (("beacon", beacon), ("beaconCentroidUncertaintyDeg", beacon_uncertainty)):
        if (value is None) != (lines_up_from is None):
            issues.append(((key,), BEACON_TERMS))
    if lines_up_from is not None and beacon is not None and beacon != lines_up_from:
        issues.append((("beacon",), BEACON_OF_ITS_METHOD))
    return issues


def pointing_hint_issues(
    status: str, source_sha256: Any, has_time: bool, has_place: bool, pose_method: Any, hint: Any
) -> list[Issue]:
    """The rules between a pointing hint and its solution: the hint's photo is the solution's, it is used as the pose
    exactly when the pose is the device attitude's, it states the verified offset exactly on a calibrated solution
    whose hint gives a centre,
    and a solution with a hint has a time and a place."""
    # Each issue is named where it is: at a field the rule needs when that field is missing, else at the hint's own
    issues: list[Issue] = []
    device_pose = pose_method == DEVICE_ATTITUDE
    if hint is None:
        if device_pose:
            issues.append((("pointingHint",), HINT_POSE))
        return issues
    if source_sha256 is None:
        issues.append((("sourceSha256",), HINT_RECORDING))
    elif hint.source_asset_sha256 != source_sha256:
        issues.append((("pointingHint", "sourceAssetSha256"), HINT_RECORDING))
    if "pose_estimate" in hint.used_as and pose_method is None:
        issues.append((("poseEstimate",), HINT_POSE))
    elif ("pose_estimate" in hint.used_as) != device_pose:
        issues.append((("pointingHint", "usedAs"), HINT_POSE))
    if ("moment" in hint.used_as) != (hint.time_from == "record" or hint.place_from == "record"):
        issues.append((("pointingHint", "usedAs"), HINT_MOMENT_USE))
    if (hint.verified_center_offset_deg is not None) != (status == "calibrated" and hint_is_centred(hint.used_as)):
        issues.append((("pointingHint", "verifiedCenterOffsetDeg"), HINT_OFFSET))
    if not has_time:
        issues.append((("observedAtUTC",), HINT_MOMENT))
    if not has_place:
        issues.append((("observerLocation",), HINT_MOMENT))
    return issues


def names_candidate_program(candidate_source: Any, candidate_stages: Any) -> bool:
    """Whether `candidate_stages` names the program of `candidate_source` (True when no `candidate_source` is carried).
    CANDIDATE_PROGRAMS holds one program today; checking membership here and in packages/contracts/src/astronomy.ts
    (`namesCandidateProgram`) keeps the rule true when another program enters CANDIDATE_PROGRAMS."""
    if candidate_source is None:
        return True
    program = candidate_source.get("program") if isinstance(candidate_source, Mapping) else candidate_source.program
    return candidate_stages is not None and program in candidate_stages


def _solution_issues(solution: ObservationSolution) -> list[Issue]:
    issues: list[Issue] = []
    status, quality, pose, nominal = solution.status, solution.quality, solution.pose_estimate, solution.wcs
    # REQ-SKY-5.4: an approximate solution says how far its WCS may be off
    if pose is None and status == "approximate":
        issues.append((("poseEstimate",), APPROXIMATE_NEEDS_POSE))
    issues += pointing_hint_issues(
        status, solution.source_sha256, solution.observed_at_utc is not None, solution.observer_location is not None,
        None if pose is None else pose.method, solution.pointing_hint,
    )
    if pose is not None:
        if status != "approximate" or nominal is None:
            issues.append((("poseEstimate",), POSE_BELONGS))
        if nominal is not None:
            for index, swept in enumerate(pose.swept_wcs):
                if swept.crpix_x != nominal.crpix_x or swept.crpix_y != nominal.crpix_y:
                    issues.append((("poseEstimate", "sweptWcs", index), SWEPT_ON_THE_NOMINAL_CRPIX))
                if _of_another_image_size(swept, nominal):
                    issues.append((("poseEstimate", "sweptWcs", index), SWEPT_OF_THE_NOMINAL_SIZE))
    # anet-stage-zero: an outside program's candidate is named only where it became a verified calibration
    if solution.candidate_source is not None and status != "calibrated":
        issues.append((("candidateSource",), CANDIDATE_SOURCE_CALIBRATED))
    # anet-setting-record-one: a stage is named only on a solve some solver ran
    if solution.candidate_stages is not None and status == "pending":
        issues.append((("candidateStages",), CANDIDATE_STAGES_SOLVED))
    # anet-contract-tie-one: a record naming a candidate's program names that program among its stages
    if not names_candidate_program(solution.candidate_source, solution.candidate_stages):
        issues.append((("candidateStages",), CANDIDATE_SOURCE_IN_STAGES))
    if status == "pending":
        if quality is not None:
            issues.append((("quality",), PENDING_HAS_NO_QUALITY))
        if solution.solver is not None:
            issues.append((("solver",), PENDING_NAMES_NO_SOLVER))
    else:
        if solution.solver is None and status != "unresolved":
            issues.append((("solver",), NAMES_ITS_SOLVER))
        if quality is None and status in ("calibrated", "approximate"):
            issues.append((("quality",), REPORTS_ITS_QUALITY))
    if quality is not None:
        paired = quality.held_out_pair_count > 0
        for key, residual in (
            ("medianResidualPx", quality.median_residual_px),
            ("p95ResidualPx", quality.p95_residual_px),
            ("medianResidualArcsec", quality.median_residual_arcsec),
        ):
            if (residual is not None) != paired:
                issues.append((("quality", key), RESIDUAL_IS_REPORTED if paired else NO_RESIDUAL_TO_REPORT))
        if quality.held_out_inliers > quality.held_out_pair_count:
            issues.append((("quality", "heldOutInliers"), INLIERS_ARE_PAIRS))
        if quality.passed_independent_verification:
            at_the_flag = ("quality", "passedIndependentVerification")
            if nominal is None or quality.median_residual_px is None:
                issues.append((at_the_flag, PASSES_WITH_A_WCS))
            if status != "calibrated":
                issues.append((at_the_flag, only_calibrated_passes(status)))
            # The flag stands for the held-out gate: it is never true below the gate's own numbers
            least, most = held_out_gate()
            if quality.held_out_inliers < least or (quality.median_residual_px is not None and quality.median_residual_px > most):
                issues.append((at_the_flag, passes_at_the_gate_only(least, most)))
    if solution.source_sha256 is None and not (status == "pending" and len(solution.findings) == 0):
        issues.append((("sourceSha256",), NAMES_ITS_RECORDING_UNLESS_PENDING))
    needs_time_and_place = status in ("approximate", "predicted_only") or any(
        finding.evidence_class == "predicted" for finding in solution.findings
    )
    if needs_time_and_place and solution.observed_at_utc is None:
        issues.append((("observedAtUTC",), NEEDS_ITS_TIME))
    if needs_time_and_place and solution.observer_location is None:
        issues.append((("observerLocation",), NEEDS_ITS_PLACE))
    # Pixels reach the sky and the catalog only through a verified WCS
    verified = solution.has_verified_wcs
    for index, finding in enumerate(solution.findings):
        if finding.observation_solution_id is None:
            issues.append((("findings", index, "observationSolutionId"), FINDING_NAMES_ITS_SOLUTION))
        if finding.evidence_class == "catalog_matched" and not verified:
            issues.append((("findings", index, "evidenceClass"), MATCH_NEEDS_A_VERIFIED_WCS))
        if finding.evidence_class == "observed" and (finding.sky_position is not None) != verified:
            issues.append((
                ("findings", index, "skyPosition"),
                OBSERVED_NEEDS_ITS_SKY_POSITION if verified else OBSERVED_HAS_NO_SKY_POSITION,
            ))
        # The lead's ruling of 2026-11-12 on the observed Moon: an observed finding holds the pose fit's residual only
        # in an approximate solution, the status a pose estimate has (the contract's own rule, word for word)
        if finding.evidence_class == "observed" and solution.status != "approximate":
            for wire, value in (("pixelResidualPx", finding.evidence.pixel_residual_px),
                                ("angularResidualArcsec", finding.evidence.angular_residual_arcsec)):
                if value is not None:
                    issues.append((("findings", index, "evidence", wire), OBSERVED_RESIDUAL_OF_A_POSE))
    return issues


# =============================================================================================
# Parsing, and the field table both sides of the contract read
# =============================================================================================


def parse_observation_solution(payload: Any) -> ObservationSolution:
    """`payload` (a contract dict, as stored or as `to_contract_dict` writes one) as an `ObservationSolution`, or a
    `pydantic.ValidationError` (a `ValueError`) naming each path and rule the contract refuses it on."""
    return ObservationSolution.model_validate(payload)


def parse_celestial_finding(payload: Any) -> CelestialFinding:
    """`payload` as a finding on its own, outside a solution (`CelestialFindingSchema`)."""
    return CelestialFinding.model_validate(payload)


def issue_paths(error: ValidationError) -> list[str]:
    """Where a refusal's issues are, as the contract's own dotted paths (`findings.0.skyPosition`), once each."""
    return sorted({".".join(str(key) for key in issue["loc"]) for issue in error.errors(include_url=False, include_input=False)})


def issue_messages(error: ValidationError) -> list[str]:
    return [issue["msg"] for issue in error.errors(include_url=False, include_input=False)]


FAMILY: dict[str, type[BaseModel]] = {
    "ObservationSolution": ObservationSolution,
    "CelestialFinding": CelestialFinding,
    "SourceDetection": SourceDetection,
    "SkyPosition": SkyPosition,
    "ScientificEvidence": ScientificEvidence,
    "InferredDistance": InferredDistance,
    "CatalogReference": CatalogReference,
    "CatalogDistance": CatalogDistance,
    "WCSCalibrationModel": WCSCalibrationModel,
    "PoseEstimate": PoseEstimate,
    "PointingHint": PointingHint,
    "CandidateSource": CandidateSource,
    "ObserverLocation": ObserverLocation,
    "SolverIdentity": SolverIdentity,
    "SolutionQuality": SolutionQuality,
    "DetectionQuality": DetectionQuality,
    "StarProfile": StarProfile,
}
_SCHEMA_NAMES = {model: name for name, model in FAMILY.items()}
# The catalog reference's own mirror states its bounds in pydantic's words, so its rows are named here; the tests
# hold that mirror and the Zod schema to them
_CATALOG_REFERENCE_RULES: dict[str, dict[str, _Rule]] = {
    "CatalogReference": {
        "id": NON_EMPTY_TEXT, "provider": CATALOG_PROVIDER, "releaseVersion": NON_EMPTY_TEXT,
        "catalogSourceId": NON_EMPTY_TEXT, "commonName": NON_EMPTY_TEXT,
        "bayerDesignation": TextRule(
            "bayerDesignation", pattern=BAYER_DESIGNATION_PATTERN, form="a Bayer designation in its long form",
            accept=("Alpha Orionis", "Alpha2 Capricorni", "Alpha Canis Majoris", "Omega9 Vulpeculae", "Lambda Orionis"),
            refuse=("Alpha Ori", "alpha Orionis", "Lamda Orionis", "Alpha Orionis\n", " Alpha Orionis", "Alpha10 Orionis",
                    "HR 1713", "bet Ori", "\u03b1 Ori", ""),
        ),
        "flamsteedNumber": NumberRule("flamsteedNumber", ge=FLAMSTEED_NUMBER_MIN, le=FLAMSTEED_NUMBER_MAX, integer=True),
        "constellation": NON_EMPTY_TEXT, "objectType": CATALOG_OBJECT_TYPE, "coordinateEpoch": NON_EMPTY_TEXT,
        "raDeg": DEGREES_0_TO_360, "decDeg": DEGREES_90, "apparentMagnitude": NUMBER, "colorIndexBpRp": NUMBER,
        "spectralType": TEXT, "properMotionRaMasYr": NUMBER, "properMotionDecMasYr": NUMBER,
        "radialVelocityKmS": NUMBER, "parallaxMas": NUMBER, "parallaxErrorMas": NON_NEGATIVE, "citation": NON_EMPTY_TEXT,
    },
    "CatalogDistance": {"lower": POSITIVE, "median": POSITIVE, "upper": POSITIVE, "method": CATALOG_DISTANCE_METHOD},
}


def _described(annotation: Any) -> tuple[dict[str, Any], _Rule | None]:
    """A field's table entry and its rule, read from its annotation."""
    origin = get_origin(annotation)
    if origin is Annotated:
        inner, *metadata = get_args(annotation)
        for item in metadata:
            if isinstance(item, _Rule):
                return {"rule": item.name}, item
        entry, rule = _described(inner)
        for item in metadata:
            if isinstance(item, Items):
                entry.update({key: value for key, value in (("minItems", item.least), ("maxItems", item.most)) if value is not None})
        return entry, rule
    if origin in (Union, types.UnionType):
        (inner,) = [argument for argument in get_args(annotation) if argument is not type(None)]
        return _described(inner)
    if origin is list:
        (item,) = get_args(annotation)
        entry, rule = _described(item)
        return {**entry, "array": True}, rule
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return {"schema": _SCHEMA_NAMES[annotation]}, None
    raise TypeError(f"No table entry for {annotation!r}.")


@lru_cache(maxsize=None)
def _fields(schema: str) -> dict[str, tuple[dict[str, Any], _Rule | None]]:
    model = FAMILY[schema]
    hints = get_type_hints(model, include_extras=True)
    fields: dict[str, tuple[dict[str, Any], _Rule | None]] = {}
    for name, field in model.model_fields.items():
        wire = field.alias or name
        named = _CATALOG_REFERENCE_RULES.get(schema, {}).get(wire)
        entry, rule = ({"rule": named.name}, named) if named is not None else _described(hints[name])
        fields[wire] = ({**entry, **({} if field.is_required() else {"optional": True})}, rule)
    return fields


def field_rule(schema: str, wire_name: str) -> Any:
    """The rule the field `wire_name` of `schema` follows on its own: the one object the parser checks it with."""
    rule = _fields(schema)[wire_name][1]
    if rule is None:
        raise KeyError(f"{schema}.{wire_name} holds an object, not a value with a rule of its own.")
    return rule


def field_table() -> dict[str, Any]:
    """Every field of every schema in the family and the rule it follows, with values at each rule's edges, as the
    fixture both sides read carries them."""
    fields = {schema: {wire: entry for wire, (entry, _) in _fields(schema).items()} for schema in FAMILY}
    rules: dict[str, _Rule] = {}
    for schema in FAMILY:
        for _, rule in _fields(schema).values():
            if rule is not None and rules.setdefault(rule.name, rule) != rule:
                raise ValueError(f"Two rules are named {rule.name}.")
    return {
        "rules": {name: rules[name].table_entry() for name in sorted(rules)},
        "fields": fields,
        # The name each field has in Python where it differs from its wire name. A wire record that carries one is
        # carrying a key the contract does not name: it is dropped like any other, never read as the field
        "pythonNames": {
            schema: {field.alias or name: name for name, field in model.model_fields.items() if (field.alias or name) != name}
            for schema, model in FAMILY.items()
        },
        # What an object or a list is never: each is refused at the field itself
        "structureRefusals": {"object": [0, "x", True, None, []], "array": [0, "x", True, None, {}]},
        # The records that refuse a key they do not name, at the record (z.object().strict()); the rest drop one
        "closedSchemas": sorted(schema for schema, model in FAMILY.items() if issubclass(model, _Closed)),
    }
