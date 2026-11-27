"""Make each sample night ready on this machine (first-use-sample-one, DECIDED-BY-LEAD 25 Nov 03:43): the photo
fetched from its own recorded source (its entry in data/downloads.json, by scripts/fetch_download.py, size and
SHA-256 checked), then the app's own solve of it, through the real upload path on a temporary store (never
data/uploads), written whole to the cache with its SHA-256 and the producer that made it beside it. A sample already
whole, made by this revision of the solve chain, is kept as it is; one made by another revision is made again, so the
sample shows the solve an upload would get now.

Run by `uv run python scripts/make_samples.py` (the setup), and by the laptop's server at its own start in the
background when `ALBEDIAN_SAMPLES_AT_START` is `1` (the field run sets it; first-use-sample-three, DECIDED-BY-LEAD 25
Nov 08:00), so the sample is made where the setup has not made it. The solve is the app's own, so it takes as long as
an upload's; nothing here is ever cut or tracked.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Optional

from pydantic import ValidationError

from services.contracts.astronomy import parse_observation_solution
from services.contracts.samples import SampleNight, load_samples
# The solve chain's producer, read at load (its import sets the warnings its child processes inherit)
from services.ingest.jobs import SOLVE_PRODUCER

ROOT = Path(__file__).resolve().parents[2]
AT_START_ENV = "ALBEDIAN_SAMPLES_AT_START"


class SampleNotReady(Exception):
    """A sample whose photo is not the recorded one, or whose solve could not be made."""


class SampleAltered(Exception):
    """A kept solve whose bytes are not the ones the SHA-256 kept beside it names."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_photo(night: SampleNight, root: Path) -> None:
    """The corpus fetch of the one photo, from its own recorded source."""
    subprocess.run([sys.executable, str(root / "scripts" / "fetch_download.py"), night.photo.download_path], cwd=root, check=True)


def current_producer() -> dict[str, Any]:
    """The solve chain's own name and revision, as a solve job records them."""
    return {"name": SOLVE_PRODUCER.name, "revision": SOLVE_PRODUCER.revision}


def producer_path(night: SampleNight) -> str:
    """Where the producer of the kept solve is written, beside it."""
    return str(Path(night.solution.path).with_suffix(".producer.json"))


def solve_photo(photo: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """The app's own solve of a photo, through the real upload path on a temporary store, and its job's producer."""
    from fastapi.testclient import TestClient

    from services.api import create_app
    from services.ingest import router as ingest_router
    from services.ingest import runner as job_runner
    from services.ingest.store import IngestStore

    with tempfile.TemporaryDirectory() as folder:
        store = IngestStore(Path(folder) / "store", max_bytes=256 * 1024 * 1024)
        app = create_app()
        app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
        client = TestClient(app, base_url="http://127.0.0.1:8000")
        try:
            stored = client.post("/api/ingest", content=photo.read_bytes(), headers={"content-type": "image/jpeg", "x-albedian-filename": photo.name})
            if stored.status_code not in (200, 201):
                raise SampleNotReady("the sample photo was not stored")
            sha = stored.json()["sourceAsset"]["sha256"]
            job = client.post(f"/api/ingest/{sha}/solve?wait=false").json()
            while job["state"] in ("queued", "running"):
                time.sleep(1.0)
                job = client.get(f"/api/ingest/jobs/{job['id']}").json()
            solution = client.get(f"/api/ingest/{sha}/solution")
            if job["state"] != "succeeded" or solution.status_code != 200:
                raise SampleNotReady("the sample's solve did not finish")
            return solution.json(), job["producer"]
        finally:
            job_runner.runner_for(store).shutdown()


def kept_solve(night: SampleNight, root: Path = ROOT) -> Optional[dict[str, Any]]:
    """The app's own solve of the sample kept on this machine, the one rule the solution route, the voice answers and
    this setup all hold (sample-solution-producer-one, from AUDIT's audit of main 927612bd3): the solution, its SHA-256
    and its producer beside it, none a link to elsewhere; whole by that hash; made by this revision of the solve chain;
    taken by the solution contract; named for this night's photo. None when any of that is not so (not made here yet,
    made by another revision, being made again, or refused); SampleAltered when its bytes are not the ones its hash
    names."""
    path, kept, made_by = root / night.solution.path, root / night.solution.sha256_path, root / producer_path(night)
    if not all(p.is_file() and not p.is_symlink() for p in (path, kept, made_by)):
        return None
    if _sha256(path) != kept.read_text(encoding="utf-8").strip():
        raise SampleAltered("the kept solve is not the one its hash names")
    try:
        if json.loads(made_by.read_text(encoding="utf-8")) != current_producer():
            return None
        solution = json.loads(path.read_text(encoding="utf-8"))
        parsed = parse_observation_solution(solution)
    except (ValidationError, ValueError, TypeError, RecursionError):
        return None
    if parsed.source_sha256 != night.photo.sha256 or parsed.source_asset_id != f"src_{night.photo.sha256[:16]}":
        return None
    return solution


def _kept(night: SampleNight, root: Path) -> bool:
    try:
        return kept_solve(night, root) is not None
    except SampleAltered:
        return False


def make_ready(
    night: SampleNight,
    root: Path = ROOT,
    *,
    fetch: Callable[[SampleNight, Path], None] = fetch_photo,
    solve: Callable[[Path], tuple[dict[str, Any], dict[str, Any]]] = solve_photo,
) -> str:
    """'kept' when the sample's solve is already whole on this machine, 'made' once it has been made now."""
    if _kept(night, root):
        return "kept"
    photo = root / night.photo.download_path
    if not photo.is_file():
        fetch(night, root)
    if not photo.is_file() or photo.stat().st_size != night.photo.bytes or _sha256(photo) != night.photo.sha256:
        raise SampleNotReady("the sample photo on this machine is not the one its record names")
    # The old producer goes before the solve and the new one after everything else, so while the solve is made again
    # nothing serves the old one or a half-recorded new one: the route says it is not on this machine yet
    (root / producer_path(night)).unlink(missing_ok=True)
    solution, producer = solve(photo)
    text = (json.dumps(solution) + "\n").encode("utf-8")
    path = root / night.solution.path
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_bytes(text)
    os.replace(part, path)
    (root / night.solution.sha256_path).write_text(hashlib.sha256(text).hexdigest() + "\n", encoding="utf-8")
    (root / producer_path(night)).write_text(json.dumps({"name": producer["name"], "revision": producer["revision"]}) + "\n", encoding="utf-8")
    return "made"


def make_all(root: Path = ROOT) -> dict[str, str]:
    """Each sample night made ready, by its id: 'kept' or 'made'."""
    return {night.id: make_ready(night, root) for night in load_samples(root / "data" / "samples.json")}


def start_making_samples(root: Path = ROOT, environ: Any = os.environ, popen: Callable[..., Any] = subprocess.Popen) -> Any:
    """The laptop's server makes the samples at its own start, in the background, when `ALBEDIAN_SAMPLES_AT_START` is
    `1` (first-use-sample-three): it starts the setup step itself, `scripts/make_samples.py`, as its own process (the
    solve forks children of its own, which a second thread in the server would make fragile) and does not wait for it.
    None, starting nothing, otherwise, so a test or CI never fetches or solves. The routes answer once the sample is
    made, which is when the pill shows; the server runs on whatever the step does."""
    if environ.get(AT_START_ENV) != "1":
        return None
    try:
        return popen([sys.executable, str(root / "scripts" / "make_samples.py")], cwd=root,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as error:
        logging.getLogger("albedian.samples").error("sample not started: %s", type(error).__name__)
        return None
