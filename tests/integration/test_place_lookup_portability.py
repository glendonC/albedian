"""A host without the place lookup's package still starts and says why (unit capture-time-5, portability).

The place lookup (tzfpy, a compiled package with wheels for some platforms only) is imported when a place is first
looked up, not when the API starts. On a host it does not install on, the API still answers, and a capture time that
would be checked against the zone at its GPS position names the cause instead: `place_lookup_unavailable`, a label
both contracts take. The case runs in a child interpreter of the test's own, where tzfpy is made unimportable before
anything is imported, so nothing of it is hidden from the rest of the run.

The zone database's own case (`time_zone_data_unavailable`, and the tzdata package carried beside the system's) is in
test_capture_time_from_gps.py, beside the label's other tests.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
# A child interpreter here imports the API and builds three capture times: a few seconds; this bound ends a hung one
CHILD_BOUND_SEC = 60

WITHOUT_THE_PLACE_LOOKUP = """
import importlib.abc
import json
import sys

sys.path.insert(0, sys.argv[1])
kind = sys.argv[2]
attempts = []


class BlockPlaceLookup(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "tzfpy" or fullname.startswith("tzfpy."):
            attempts.append(fullname)
            if kind == "missing":
                raise ModuleNotFoundError("No module named 'tzfpy'", name="tzfpy")
            raise ImportError("tzfpy shared object cannot load on this host")
        return None


sys.meta_path.insert(0, BlockPlaceLookup())
from fastapi.testclient import TestClient

from services.api import create_app
from services.contracts.ingest import CaptureTimeHint
import services.ingest.metadata  # noqa: F401
from tests.integration.test_capture_time_from_gps import FIX, LOCAL, OCEAN, capture

health = TestClient(create_app()).get("/api/health")
attempts_at_startup = list(attempts)
loaded_at_startup = "tzfpy" in sys.modules
hints = {"zoneless": capture(LOCAL, OCEAN, FIX), "tagged": capture(LOCAL, OCEAN, FIX, tag="-03:00"),
         "no_fix": capture(LOCAL, OCEAN, None)}
for hint in hints.values():
    CaptureTimeHint.model_validate(hint)
print(json.dumps({"health": health.status_code, "attempts_at_startup": attempts_at_startup,
                  "loaded_at_startup": loaded_at_startup, "attempts_on_lookup": list(attempts),
                  "loaded_after": "tzfpy" in sys.modules,
                  "hints": {name: {"offset": hint["utcOffsetMinutes"], "basis": hint["source"]["basis"]}
                            for name, hint in hints.items()}}))
"""

WITH_THE_PLACE_LOOKUP = """
import importlib.abc
import json
import sys

sys.path.insert(0, sys.argv[1])
attempts = []


class NotePlaceLookup(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "tzfpy" or fullname.startswith("tzfpy."):
            attempts.append(fullname)
        return None


sys.meta_path.insert(0, NotePlaceLookup())
from fastapi.testclient import TestClient

from services.api import create_app
import services.ingest.metadata  # noqa: F401
from tests.integration.test_capture_time_from_gps import FIX, LOCAL, OCEAN, capture

health = TestClient(create_app()).get("/api/health")
loaded_at_startup = "tzfpy" in sys.modules
attempts_at_startup = list(attempts)
hint = capture(LOCAL, OCEAN, FIX)
print(json.dumps({"health": health.status_code, "loaded_at_startup": loaded_at_startup,
                  "attempts_at_startup": attempts_at_startup, "loaded_after_lookup": "tzfpy" in sys.modules,
                  "offset": hint["utcOffsetMinutes"], "place_basis": hint["source"]["basis"]["placeTimeZone"]}))
"""


@pytest.mark.parametrize("kind", ["missing", "unusable"])
def test_without_the_place_lookup_the_api_starts_and_the_label_names_the_cause(kind: str, tmp_path: Path) -> None:
    script = tmp_path / "without_the_place_lookup.py"
    script.write_text(WITHOUT_THE_PLACE_LOOKUP)
    done = subprocess.run([sys.executable, "-I", str(script), str(ROOT), kind], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=CHILD_BOUND_SEC)
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout.splitlines()[-1])
    # Importing metadata, creating the app, and serving /api/health do not touch tzfpy at all
    assert seen["health"] == 200
    assert seen["attempts_at_startup"] == []
    assert seen["loaded_at_startup"] is False
    # Each GPS lookup attempts to import tzfpy, fails cleanly, and leaves it unloaded
    assert seen["attempts_on_lookup"] == ["tzfpy", "tzfpy", "tzfpy"]
    assert seen["loaded_after"] is False
    # Nothing is derived from the place; an offset tag still wins, and each GPS source still says what it saw
    assert seen["hints"]["zoneless"] == {"offset": None,
                                         "basis": {"placeTimeZone": "place_lookup_unavailable", "gpsTimeStamp": "unchecked"}}
    assert seen["hints"]["no_fix"] == {"offset": None, "basis": {"placeTimeZone": "place_lookup_unavailable",
                                                                 "gpsTimeStamp": "no_usable_time_stamp"}}
    assert seen["hints"]["tagged"]["offset"] == -180
    assert seen["hints"]["tagged"]["basis"]["placeTimeZone"] == "place_lookup_unavailable"


def test_with_the_place_lookup_the_same_photo_is_checked_against_its_zone(tmp_path: Path) -> None:
    """The neighbour: in a normal environment tzfpy stays unloaded when the API starts, imports on the first lookup,
    and checks the same zoneless photo against its zone."""
    script = tmp_path / "with_the_place_lookup.py"
    script.write_text(WITH_THE_PLACE_LOOKUP)
    done = subprocess.run([sys.executable, "-I", str(script), str(ROOT)], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=CHILD_BOUND_SEC)
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout.splitlines()[-1])
    assert seen == {
        "health": 200,
        "loaded_at_startup": False,
        "attempts_at_startup": [],
        "loaded_after_lookup": True,
        "offset": -180,
        "place_basis": "agrees",
    }
