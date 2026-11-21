"""A person's alignment of a sky drawn for the moment the photo's pointing record lent (lentalign-1; AUDIT's sweep of
21 November, finding two).

A photo whose time and place only its stored pointing record gives gets a predicted sky from the plain solve, so the
reveal offers Line it up yourself. The aligned start must read the lent moment as the plain solve does: the route
accepts the alignment (C15's no-time sentence stays for a photo whose record lends nothing), and the aligned solve's
pointing hint says the time and the place came from the record (lentalign-0's contract), never from the file, and every
predicted item and the lent-moment sentence name the record as the plain solve's do. The person's hint stays a search
hint only, never a pose, and the record's attitude is never used as a pose for it.

Synthetic photos and records only [simulated], the pointing-moment tests' own.
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi.testclient import TestClient

from services.contracts.jobs import UploadJob
from services.science import predicted_sky
from tests.integration.test_pointing_moment import (  # noqa: F401 - the fixtures are used by name
    LEAKS,
    NO_PLACE_RECORD,
    RECORD_MOMENT,
    RECORD_PLACE,
    _few_stars,
    _png,
    _put,
    _record,
    _solve,
    _upload,
    client,
    store,
)

ALIGNMENT = {"format": "albedian.person-alignment", "formatVersion": 1, "source": "person", "centerAltDeg": 41.5,
             "centerAzDeg": 128.25, "rollDeg": -6.0, "fovDiagonalDeg": 68.5}


def _start_aligned(client: TestClient, sha: str) -> Any:
    return client.post(f"/api/ingest/{sha}/solve", content=json.dumps(ALIGNMENT), headers={"content-type": "application/json"})


def _follow(client: TestClient, job: dict[str, Any]) -> dict[str, Any]:
    deadline = time.monotonic() + 300.0
    while job.get("state") in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.02)
        job = client.get(f"/api/ingest/jobs/{job['id']}").json()
    UploadJob.model_validate(job)
    assert job["state"] == "succeeded", job
    return job


def _predicted(solution: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for f in solution["findings"] if f["evidenceClass"] == "predicted"]


def test_an_alignment_of_a_sky_the_record_lent_is_accepted(client: TestClient) -> None:
    sha, asset = _upload(client, _png(_few_stars()))
    assert "capturedAtUTC" not in asset and "observerLocation" not in asset
    _put(client, sha, _record(sha))
    answer = _start_aligned(client, sha)
    assert answer.status_code == 202, answer.text
    assert answer.json()["request"] == ALIGNMENT
    _follow(client, answer.json())


def test_an_alignment_is_still_refused_when_the_record_lends_no_place(client: TestClient) -> None:
    # A record with no place lends nothing (a time alone gives no sky), so C15's sentence stands, as with no record
    sha, _ = _upload(client, _png(_few_stars()))
    _put(client, sha, _record(sha, NO_PLACE_RECORD))
    answer = _start_aligned(client, sha)
    assert answer.status_code == 422
    assert answer.json()["detail"] == "Your photo does not say when or where it was taken, so its sky cannot be lined up by hand."
    bare, _ = _upload(client, _png(_few_stars(n=6)))
    assert _start_aligned(client, bare).status_code == 422


def test_the_aligned_solve_says_the_time_and_the_place_came_from_the_record(client: TestClient) -> None:
    sha, _ = _upload(client, _png(_few_stars()))
    _put(client, sha, _record(sha))
    _follow(client, _start_aligned(client, sha).json())
    solution = client.get(f"/api/ingest/{sha}/solution").json()
    assert solution["observedAtUTC"] == RECORD_MOMENT and solution["observerLocation"] == RECORD_PLACE
    hint = solution["pointingHint"]
    assert hint["source"] == "person" and hint["usedAs"] == ["search_hint", "moment"]
    assert (hint["timeFrom"], hint["placeFrom"]) == ("record", "record")
    assert (hint["rollDeg"], hint["fovDiagonalDeg"]) == (ALIGNMENT["rollDeg"], ALIGNMENT["fovDiagonalDeg"])
    # The person's hint is never the pose, and the record's attitude is not used as one for an aligned solve
    assert "poseEstimate" not in solution or solution["poseEstimate"]["method"] != "device_attitude"
    predicted = _predicted(solution)
    assert predicted
    for finding in predicted:
        text = " ".join(finding["evidence"]["limitations"])
        # Both lent, in C13's words (item 7), as the plain solve's items say them (test_pointing_moment)
        assert predicted_sky.RECORD_TIME_WORDS + " and where it was (within 8 m)" in text, finding["id"]
        for words in ("for the recorded time and place", "recorded place at sea level"):
            assert words not in json.dumps(finding), (words, finding["id"])
    sentence = predicted_sky.lent_moment_sentence(predicted_sky.LentMoment("record", "record", 8.0, "full"))
    assert sentence in solution["warnings"]
    # Nothing from the record is measured, and no value of it reaches a warning
    assert not [f for f in solution["findings"] if f["evidenceClass"] == "catalog_matched"]
    for warning in solution["warnings"]:
        for value in LEAKS:
            assert value not in warning, (value, warning)


def test_the_aligned_solve_of_a_photo_with_its_own_moment_still_says_the_file(client: TestClient) -> None:
    # The file's own time and place: the record lends nothing, so the person's hint says file, as align-1b had it
    sha, _ = _upload(client, _png(_few_stars(), time=True, place=True))
    _put(client, sha, _record(sha))
    _follow(client, _start_aligned(client, sha).json())
    hint = client.get(f"/api/ingest/{sha}/solution").json()["pointingHint"]
    assert hint["usedAs"] == ["search_hint"] and (hint["timeFrom"], hint["placeFrom"]) == ("file", "file")
