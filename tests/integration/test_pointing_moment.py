"""The pointing record's time and place in the solve (pointing-moment; ios-capture-companion design component 6).

A photo from the companion app carries no GPS and may hold no recorded time. When its pointing record is stored, the
solve job reads it at its start: where the file holds no recorded time it takes the record's
`capturedAtDeviceClockUTC`, where it holds no place the record's allowed place, and hands both to the solve and to the
predicted sky. The solution's `pointingHint` then says `moment`, with `timeFrom` and `placeFrom`, and gives no centre
(the record is used here for its moment only). The record lends nothing when the file holds both, or when the
solution would still lack a time or a place. The receipt never changes. A stored record that is not a record of this
photo is not used, with one warning that holds none of its values, and the job still succeeds. Nothing from the
record is labelled observed or catalog matched, and no value of it reaches a warning or a log line.

Synthetic photos and records only [simulated]: the records are the pointing contract's own samples, with this
photo's hash.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import logging
import math
import os
import shutil
from pathlib import Path
import time
from typing import Any, Iterator

from fastapi.testclient import TestClient
import numpy as np
from PIL import Image
import pytest

from services.api import app
from services.contracts.jobs import UploadJob
from services.ingest import jobs as ingest_jobs
from services.ingest import router as ingest_router
from services.ingest.store import IngestStore
from services.ingest.store_fs import hash_folder
from services.ingest.store_pointing import POINTING_FILENAME
from services.science.predicted_sky import NO_PLACE, NO_TIME_OR_PLACE
from services.science.solver import render_synthetic_starfield

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "pointing_contract.json"
SAMPLES = {sample["name"]: sample["record"] for sample in json.loads(FIXTURE.read_text(encoding="utf-8"))["valid"]}
# The contract's first sample: a measured heading and an allowed place, at the plate-solver fixtures' synthetic moment
# and open-ocean place; they point at nobody
WITH_PLACE = "measured_heading_and_place"
NO_PLACE_RECORD = "place_not_allowed"
RECORD_MOMENT = SAMPLES[WITH_PLACE]["capturedAtDeviceClockUTC"]
RECORD_PLACE = {"latDeg": SAMPLES[WITH_PLACE]["location"]["latDeg"], "lonDeg": SAMPLES[WITH_PLACE]["location"]["lonDeg"]}
# The file's own time, an hour before the record's, so the test can tell which one the solution took
EXIF_TIME, EXIF_OFFSET, FILE_MOMENT = "2030:01:01 01:00:00", "+00:00", "2030-01-01T01:00:00Z"
# The record's values as text, in the forms a leak would take
LEAKS = (RECORD_MOMENT, RECORD_MOMENT[:10], RECORD_MOMENT[11:16], "-40.0", "212.5", "45.5")
FILE_PLACE = {"latDeg": 10.0, "lonDeg": -20.0}
CENTRE_FIELDS = ("centerAltDeg", "centerAzDeg", "uncertaintyDeg", "verifiedCenterOffsetDeg")


@pytest.fixture
def store(tmp_path: Path) -> IngestStore:
    return IngestStore(tmp_path / "store", max_bytes=20_000_000)


@pytest.fixture
def client(store: IngestStore) -> Iterator[TestClient]:
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


def _few_stars(h: int = 120, w: int = 160, n: int = 5) -> np.ndarray:
    """Five point sources on a dark field: too few for the plate solver [simulated]."""
    yy, xx = np.mgrid[0:h, 0:w]
    luma = np.full((h, w), 6.0)
    for idx in range(n):
        cx, cy = 14.0 + (idx * 37) % (w - 28), 12.0 + (idx * 23) % (h - 24)
        luma += 200.0 * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * 1.4**2))
    return luma


def _png(luma: np.ndarray, *, time: bool = False, place: bool = False, model: str | None = None) -> bytes:
    """A PNG with no GPS, as the companion app writes, and its own recorded time only when `time`; with `place`, a
    GPS place of its own, another open-ocean point than the record's."""
    exif = Image.Exif()
    if model is not None:
        exif[0x0110] = model
    if time:
        sub = exif.get_ifd(0x8769)
        sub[0x9003], sub[0x9011] = EXIF_TIME, EXIF_OFFSET
    if place:
        gps = exif.get_ifd(0x8825)
        gps[1], gps[2], gps[3], gps[4] = "N", (10.0, 0.0, 0.0), "W", (20.0, 0.0, 0.0)
    buf = io.BytesIO()
    Image.fromarray(np.clip(np.round(luma), 0, 255).astype(np.uint8), mode="L").save(
        buf, format="PNG", exif=exif.tobytes(), compress_level=1
    )
    return buf.getvalue()


def _upload(client: TestClient, body: bytes) -> tuple[str, dict[str, Any]]:
    resp = client.post("/api/ingest", content=body, headers={"content-type": "image/png"})
    assert resp.status_code == 201, resp.text
    return hashlib.sha256(body).hexdigest(), resp.json()["sourceAsset"]


def _record(sha: str, name: str = WITH_PLACE) -> bytes:
    record = copy.deepcopy(SAMPLES[name])
    record["sourceAssetSha256"] = sha
    return json.dumps(record).encode("utf-8")


def _put(client: TestClient, sha: str, body: bytes) -> None:
    resp = client.put(f"/api/ingest/{sha}/pointing", content=body, headers={"content-type": "application/json"})
    assert resp.status_code == 201, resp.text


def _solve(client: TestClient, sha: str) -> tuple[dict[str, Any], dict[str, Any]]:
    # The start answers at once (upload jobs REQ-JOB-7.5); the job route is followed to its end, as the web does
    job = client.post(f"/api/ingest/{sha}/solve").json()
    deadline = time.monotonic() + 300.0
    while isinstance(job, dict) and job.get("state") in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.02)
        job = client.get(f"/api/ingest/jobs/{job['id']}").json()
    UploadJob.model_validate(job)
    assert job["state"] == "succeeded", job
    return job, client.get(f"/api/ingest/{sha}/solution").json()


def _by_class(solution: dict[str, Any], evidence_class: str) -> list[dict[str, Any]]:
    return [f for f in solution["findings"] if f["evidenceClass"] == evidence_class]


def _assert_nothing_from_the_record_is_measured(solution: dict[str, Any]) -> None:
    # The observed findings are the detector's pixels alone, and no finding is matched to a catalog by the record
    assert all("skyPosition" not in f for f in _by_class(solution, "observed"))
    assert len(_by_class(solution, "observed")) == solution["quality"]["detectedCandidateCount"]
    for warning in solution["warnings"]:
        for value in LEAKS:
            assert value not in warning, (value, warning)


def test_a_photo_with_no_time_and_no_place_takes_both_from_its_record(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    sha, asset = _upload(client, _png(_few_stars()))
    assert "capturedAtUTC" not in asset and "observerLocation" not in asset
    _put(client, sha, _record(sha))
    with caplog.at_level(logging.DEBUG):
        job, solution = _solve(client, sha)
    assert solution["status"] == "predicted_only"
    assert solution["observedAtUTC"] == RECORD_MOMENT and solution["observerLocation"] == RECORD_PLACE
    assert solution["pointingHint"] == {
        "sourceAssetSha256": sha, "source": "record", "usedAs": ["moment"], "timeFrom": "record", "placeFrom": "record",
    }
    assert _by_class(solution, "predicted") and not _by_class(solution, "catalog_matched")
    _assert_nothing_from_the_record_is_measured(solution)
    # The receipt is the file's own and does not change
    assert client.get(f"/api/ingest/{sha}").json()["sourceAsset"] == asset
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for value in LEAKS:
        assert value not in logged, value
    assert RECORD_MOMENT not in json.dumps(job)


def test_the_files_own_time_stands_and_the_record_lends_only_its_place(client: TestClient) -> None:
    sha, asset = _upload(client, _png(_few_stars(), time=True))
    assert asset["capturedAtUTC"] == FILE_MOMENT and "observerLocation" not in asset
    _put(client, sha, _record(sha))
    _, solution = _solve(client, sha)
    assert solution["status"] == "predicted_only"
    assert solution["observedAtUTC"] == FILE_MOMENT and solution["observerLocation"] == RECORD_PLACE
    assert solution["pointingHint"]["timeFrom"] == "file" and solution["pointingHint"]["placeFrom"] == "record"
    assert solution["pointingHint"]["usedAs"] == ["moment"]
    _assert_nothing_from_the_record_is_measured(solution)


def test_a_photo_that_solves_by_its_stars_takes_only_the_records_place_and_says_no_offset(client: TestClient) -> None:
    frame, _, _ = render_synthetic_starfield(width=960, height=540, read_noise_sigma=1.8, flux_scale=1100.0, seed=42)
    sha, _ = _upload(client, _png(frame, time=True))
    _put(client, sha, _record(sha))
    _, solution = _solve(client, sha)
    assert solution["status"] == "calibrated"
    assert solution["observerLocation"] == RECORD_PLACE and solution["observedAtUTC"] == FILE_MOMENT
    hint = solution["pointingHint"]
    assert hint["usedAs"] == ["moment"] and (hint["timeFrom"], hint["placeFrom"]) == ("file", "record")
    # A moment-only hint has no hinted centre, so no offset of the verified centre from one
    assert not set(CENTRE_FIELDS) & set(hint)
    assert _by_class(solution, "catalog_matched") and _by_class(solution, "predicted")


def test_a_file_with_its_own_time_and_place_takes_nothing_from_its_record(client: TestClient) -> None:
    sha, asset = _upload(client, _png(_few_stars(), time=True, place=True))
    assert asset["observerLocation"] == FILE_PLACE
    _put(client, sha, _record(sha))
    _, solution = _solve(client, sha)
    assert solution["status"] == "predicted_only" and "pointingHint" not in solution
    assert solution["observedAtUTC"] == FILE_MOMENT and solution["observerLocation"] == FILE_PLACE


def test_a_record_with_no_place_lends_nothing_to_a_photo_with_no_place(client: TestClient) -> None:
    sha, _ = _upload(client, _png(_few_stars()))
    _put(client, sha, _record(sha, NO_PLACE_RECORD))
    _, solution = _solve(client, sha)
    # Its time alone would give no sky and leave a time the solution could not say came from the record
    assert solution["status"] == "unresolved" and "pointingHint" not in solution
    assert "observedAtUTC" not in solution and "observerLocation" not in solution
    assert solution["warnings"][-1] == NO_TIME_OR_PLACE


def test_a_file_with_its_own_time_and_no_place_keeps_saying_so_beside_a_record_with_no_place(client: TestClient) -> None:
    sha, _ = _upload(client, _png(_few_stars(), time=True))
    _put(client, sha, _record(sha, NO_PLACE_RECORD))
    _, solution = _solve(client, sha)
    assert solution["status"] == "unresolved" and "pointingHint" not in solution
    assert solution["observedAtUTC"] == FILE_MOMENT and solution["warnings"][-1] == NO_PLACE


def test_a_photo_with_no_record_is_solved_as_before(client: TestClient) -> None:
    sha, _ = _upload(client, _png(_few_stars()))
    _, solution = _solve(client, sha)
    assert "pointingHint" not in solution and solution["warnings"][-1] == NO_TIME_OR_PLACE
    assert ingest_jobs.RECORD_NOT_USED not in solution["warnings"]


def _write_raw(store: IngestStore, sha: str, body: bytes) -> None:
    # Past the route, as a record kept by an earlier version or a damaged store would be
    held, stored = store.write_pointing(sha, body)
    assert stored and held == body


def _link_in_place(store: IngestStore, sha: str, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.json"
    target.write_bytes(b"{}")
    os.symlink(target, hash_folder(store.root, sha) / POINTING_FILENAME)


@pytest.mark.parametrize(
    "spoil",
    [
        pytest.param(lambda store, sha, _: _write_raw(store, sha, b'{"format": "albedian.pointing-record"}'), id="not_a_record"),
        pytest.param(lambda store, sha, _: _write_raw(store, sha, b"\xff\xfe not text"), id="not_json"),
        pytest.param(lambda store, sha, _: _write_raw(store, sha, b"[" * 100_000), id="nested_past_the_reader"),
        pytest.param(lambda store, sha, _: _write_raw(store, sha, _record("ab" * 32)), id="another_photos_record"),
        pytest.param(_link_in_place, id="a_link_in_its_place"),
    ],
)
def test_a_stored_record_that_is_not_this_photos_is_not_used_and_the_solve_still_succeeds(
    client: TestClient, store: IngestStore, tmp_path: Path, spoil: Any, caplog: pytest.LogCaptureFixture
) -> None:
    sha, _ = _upload(client, _png(_few_stars()))
    spoil(store, sha, tmp_path)
    with caplog.at_level(logging.DEBUG):
        _, solution = _solve(client, sha)
    assert "pointingHint" not in solution and "observedAtUTC" not in solution
    assert solution["warnings"].count(ingest_jobs.RECORD_NOT_USED) == 1
    # The predicted sky's reason stays the last warning
    assert solution["warnings"][-1] == NO_TIME_OR_PLACE
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for value in LEAKS:
        assert value not in logged and value not in json.dumps(solution["warnings"]), value


# ---- v2: the words say where the moment came from, and the place's accuracy is in every uncertainty ----

from services.science import predicted_sky  # noqa: E402

FILE_MOMENT_WORDS = "for the recorded capture time and place"


def _record_with(sha: str, **location: Any) -> bytes:
    record = json.loads(_record(sha))
    record["location"].update(location)
    return json.dumps(record).encode("utf-8")


def _predicted(solution: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for f in solution["findings"] if f["evidenceClass"] == "predicted"]


def test_every_predicted_item_says_the_time_and_place_came_from_the_phones_record(client: TestClient) -> None:
    sha, _ = _upload(client, _png(_few_stars()))
    _put(client, sha, _record(sha))
    _, solution = _solve(client, sha)
    predicted = _predicted(solution)
    assert predicted
    for finding in predicted:
        text = " ".join(finding["evidence"]["limitations"])
        # Both lent: C13 item 7, "for the time your iPhone recorded and where it was (within 8 m)"
        assert predicted_sky.RECORD_TIME_WORDS + " and where it was (within 8 m)" in text, text
        # Nothing in the item, its method included, says the time or the place was the file's own
        everything = json.dumps(finding)
        for words in (FILE_MOMENT_WORDS, "for the recorded time and place", "recorded place at sea level"):
            assert words not in everything, (words, finding["id"])
    # A deep-sky item's method names the record too (its words are built in the predicted sky)
    method = "OpenNGC catalog position placed for " + predicted_sky.RECORD_TIME_WORDS
    assert any(method in json.dumps(f) for f in predicted)
    sentence = predicted_sky.lent_moment_sentence(predicted_sky.LentMoment("record", "record", 8.0, "full"))
    assert sentence in solution["warnings"] and solution["warnings"][-1] != sentence


def test_a_files_own_time_is_still_the_recorded_one_beside_the_records_place(client: TestClient) -> None:
    sha, _ = _upload(client, _png(_few_stars(), time=True))
    _put(client, sha, _record(sha))
    _, solution = _solve(client, sha)
    for finding in _predicted(solution):
        text = " ".join(finding["evidence"]["limitations"])
        assert "the recorded capture time and " + predicted_sky.RECORD_PLACE_WORDS in text, text
        assert predicted_sky.RECORD_TIME_WORDS not in text


def test_a_reduced_accuracy_place_is_carried_with_its_own_uncertainty(client: TestClient, tmp_path: Path) -> None:
    # The same photo twice, beside the fixture's 8 m record and the same record at reduced accuracy, 5 km
    body = _png(_few_stars())
    sha, _ = _upload(client, body)
    _put(client, sha, _record(sha))
    _, exact = _solve(client, sha)
    app.dependency_overrides.pop(ingest_router.get_ingest_store, None)
    other = IngestStore(tmp_path / "second", max_bytes=20_000_000)
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: other
    sha2, _ = _upload(client, body)
    _put(client, sha2, _record_with(sha2, horizontalAccuracyM=5000.0, accuracyAuthorization="reduced"))
    _, reduced = _solve(client, sha2)
    gap = predicted_sky.place_uncertainty_arcsec(5000.0) - predicted_sky.place_uncertainty_arcsec(8.0)
    assert gap > 150.0
    a, b = _predicted(exact), _predicted(reduced)
    assert [f["id"] for f in a] == [f["id"] for f in b] and a
    for x, y in zip(a, b):
        assert y["skyPosition"]["uncertaintyArcsec"] - x["skyPosition"]["uncertaintyArcsec"] == pytest.approx(gap, abs=1e-6)
    sentence = predicted_sky.lent_moment_sentence(predicted_sky.LentMoment("record", "record", 5000.0, "reduced"))
    assert sentence in reduced["warnings"] and "5,000 m" in sentence and "the rough location you allowed" in sentence


def test_a_places_accuracy_bounds_the_turn_of_the_vertical_and_the_moons_parallax() -> None:
    # 1 km off tilts the local vertical by 1 km over the Earth's mean radius (6,371 km) and moves the Moon by at most
    # 1 km over its nearest distance (356,400 km), in arcseconds
    arcsec_per_rad = 180.0 * 3600.0 / math.pi
    assert predicted_sky.place_uncertainty_arcsec(1000.0) == pytest.approx(
        (1000.0 / 6_371_000.0 + 1000.0 / 356_400_000.0) * arcsec_per_rad, rel=1e-12
    )
    assert predicted_sky.place_uncertainty_arcsec(0.0) == 0.0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_a_sky_lined_up_from_the_moon_says_its_time_and_place_came_from_the_record(client: TestClient) -> None:
    # The head-worn Moon fixture of test_ingest_predicted_sky (a saturated disk, the glasses' camera profile), its
    # moment and open-ocean place given by the record instead of the file [simulated]
    from services.science.camera_profiles import META_RAY_BAN_DISPLAY_MODEL

    height, width = 448, 336
    yy, xx = np.mgrid[0:height, 0:width]
    frame = np.full((height, width), 4.0)
    frame[(xx - 200.0) ** 2 + (yy - 150.0) ** 2 <= 5.5**2] = 255.0
    sha, asset = _upload(client, _png(frame, model=META_RAY_BAN_DISPLAY_MODEL))
    assert "capturedAtUTC" not in asset and "observerLocation" not in asset
    record = json.loads(_record(sha))
    record["capturedAtDeviceClockUTC"] = "2031-08-30T03:01:00Z"
    record["location"].update(latDeg=30.0, lonDeg=-40.0)
    _put(client, sha, json.dumps(record).encode("utf-8"))
    _, solution = _solve(client, sha)
    assert solution["status"] == "approximate", solution["warnings"]
    lent = predicted_sky.LentMoment("record", "record", 8.0, "full")
    assert predicted_sky.lent_moment_sentence(lent) in solution["warnings"]
    for finding in _predicted(solution):
        assert "and where it was (within 8 m)" in " ".join(finding["evidence"]["limitations"])
        assert FILE_MOMENT_WORDS not in json.dumps(finding)
