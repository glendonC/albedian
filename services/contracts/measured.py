"""What `GET /api/ingest/jobs/{job_id}/measured` serves (REQ-JOB-2.8, wait-detections-one): the detections a running
solve measured, published when its detect stage ended, each as an observed finding's `sourceDetection` holds it
(observed evidence of the photo's own pixels: no sky claim). The mirror of packages/contracts/src/measured.ts."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints

from services.contracts.astronomy import SourceDetection
from services.contracts.wire import SHA256_HEX_PATTERN, WireModel

# More than any detector run keeps (its candidates are capped far below this) [proposed]
MEASURED_DETECTIONS_MAX = 100_000


class MeasuredDetections(WireModel):
    source_asset_sha256: Annotated[str, StringConstraints(pattern=SHA256_HEX_PATTERN)]
    detections: Annotated[list[SourceDetection], Field(max_length=MEASURED_DETECTIONS_MAX)]
