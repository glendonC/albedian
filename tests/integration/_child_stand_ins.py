"""Stand-ins a solve test has the group's spawned child run in place of one step, so the test acts on a signal from
the child's own step and never on a sleep (detect-overlap-1 v2). Each marks that it has begun, in a file named by the
upload's hash in the system's temporary folder, then waits to be killed; the call's bound ends it otherwise. Only the
standard library, so the child imports it at once."""

from __future__ import annotations

from pathlib import Path
import tempfile
import time
from typing import Any


def started(sha256: str) -> Path:
    """The file a stand-in makes when it begins, for the upload of `sha256`."""
    return Path(tempfile.gettempdir()) / f"albedian-child-step-{sha256}.started"


def detection_that_waits(image: Any, source_asset_id: str, key: str) -> None:
    """In place of `_prepare_phone_detections`, whose key is the upload's hash."""
    started(key).touch()
    time.sleep(600.0)


def chain_that_waits(solve_image: Any, detections: Any, width: int, height: int, **chain: Any) -> None:
    """In place of `_solve_chain`."""
    started(chain["sha256"]).touch()
    time.sleep(600.0)
