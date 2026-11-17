"""OpenNGC objects inside a verified solved frame, as predicted findings.

A deep-sky object's catalog centre (OpenNGC v20260501 as `apps/web/public/catalog/deepsky/openngc.json` holds it,
J2000, CC BY-SA 4.0, credit OpenNGC and Mattia Verga) is projected through the solution's WCS, only when the
solution is calibrated and its WCS passed held-out verification. An object whose centre lands inside the frame can
become a `predicted` finding: nothing about it is measured in the image, not its light, its centre or its outline,
so it is never `observed` or `catalog_matched`.

Re-exports the public deep-sky catalog, selection, and WCS projection modules behind a stable facade.
"""

from services.science.deep_sky_catalog import (
    DUPLICATE_TYPE,
    LIMITATIONS,
    NONEXISTENT_TYPE,
    OPENNGC_CITATION,
    OPENNGC_LICENCE_NOTE,
    OPENNGC_PATH,
    OPENNGC_PROVIDER,
    POSITION_PRECISION_ARCSEC,
    VERIFICATION_METHOD,
    _DESIGNATION,
    _candidate_rows,
    catalog_reference,
    display_name,
    duplicated_name,
    finding_id,
    openngc_table,
)
from services.science.deep_sky_projection import (
    DeepSkyPrediction,
    _project,
    predict_deep_sky_findings,
)
from services.science.deep_sky_selection import (
    MIN_MAJOR_AXIS_PX,
    decision_magnitude,
    faintest_matched_magnitude,
    keep_reason,
)

__all__ = [
    "DUPLICATE_TYPE",
    "DeepSkyPrediction",
    "LIMITATIONS",
    "MIN_MAJOR_AXIS_PX",
    "NONEXISTENT_TYPE",
    "OPENNGC_CITATION",
    "OPENNGC_LICENCE_NOTE",
    "OPENNGC_PATH",
    "OPENNGC_PROVIDER",
    "POSITION_PRECISION_ARCSEC",
    "VERIFICATION_METHOD",
    "catalog_reference",
    "decision_magnitude",
    "display_name",
    "duplicated_name",
    "faintest_matched_magnitude",
    "finding_id",
    "keep_reason",
    "openngc_table",
    "predict_deep_sky_findings",
]
