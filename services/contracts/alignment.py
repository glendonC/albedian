"""A person's alignment of the predicted sky over their photo: the Python mirror of packages/contracts/src/alignment.ts.

What the screen sends with "Solve from here" (D24, line it up yourself): the pose the person left the drawn sky at,
the frame centre's altitude and azimuth, its roll and its diagonal field of view, with source "person". It is the
person's, never a measurement: the solver takes it only as a search hint, searching within the contract's stated
uncertainty of the centre (`PERSON_ALIGNMENT_UNCERTAINTY_DEG`, [tested] by align-2 on public photos),
and the unchanged held-out gate alone decides whether the photo becomes calibrated. A refused gate keeps the alignment
as the person's, predicted, never observed or catalog matched.

The pose is in the convention of the predicted sky the person aligned (sky-context's, `SKY_CONTEXT_CONVENTION`):
topocentric at sea level, azimuth from true north through east, altitude apparent and airless; the roll is the turn of
the upright frame (the frame as displayed), positive turning the picture clockwise, as a pose estimate's roll is.

Both sides are judged by tests/fixtures/person_alignment_contract.json, which
tests/integration/test_person_alignment_contract.py writes. Strict by the wire rules (services/contracts/wire.py):
unknown keys and Python field names are refused, and a refusal never repeats the value it refuses.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, ConfigDict, Field

from services.contracts.sky_context import SKY_CONTEXT_CONVENTION
from services.contracts.wire import WIRE_CONFIG, WireModel, wire_int, wire_number

PERSON_ALIGNMENT_FORMAT = "albedian.person-alignment"
PERSON_ALIGNMENT_FORMAT_VERSION = 1
PERSON_ALIGNMENT_SOURCE = "person"
# The predicted sky's own convention, and the roll's
PERSON_ALIGNMENT_CONVENTION = {**SKY_CONTEXT_CONVENTION, "roll": "upright_frame_positive_clockwise"}
# How far from the person's centre the solver searches, in degrees [tested, align-2 on 19 November: the 13 public photos
# with a place that no star search matched, the person simulated from astrometry.net's solution; on the four a search
# at the right field of view matches, an alignment 0 to 10 degrees off gave a correct solve in 24 of 26 tries, 15 and
# 20 degrees off none; no wrong
# solve among 80 calibrated results, and none of 76 wrong alignments or random skies calibrated;
# .lead/kiro/reports/SOLVER/align-2-measure.md]
PERSON_ALIGNMENT_UNCERTAINTY_DEG = 10.0
# The widest diagonal field of view taken, as a pose estimate's
PERSON_ALIGNMENT_FOV_MAX_DEG = 175.0


def _number(**bounds: float) -> Any:
    """A wire number held to `bounds`, the bounds inside the before validator, so the API's description names them."""
    return Annotated[float, Field(**bounds), BeforeValidator(wire_number)]


class PersonAlignment(WireModel):
    """The pose a person aligned the predicted sky to, as "Solve from here" sends it (D24)."""

    model_config = ConfigDict(**WIRE_CONFIG, hide_input_in_errors=True)

    format: Literal["albedian.person-alignment"]
    format_version: Annotated[Literal[1], BeforeValidator(wire_int)]
    source: Literal["person"]
    center_alt_deg: _number(ge=-90.0, le=90.0)
    center_az_deg: _number(ge=0.0, lt=360.0)
    roll_deg: _number(ge=-180.0, le=180.0)
    fov_diagonal_deg: _number(gt=0.0, le=PERSON_ALIGNMENT_FOV_MAX_DEG)

    def to_wire_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")
