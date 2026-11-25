"""first-use-sample-three (DECIDED-BY-LEAD 25 Nov 08:00): the laptop's server makes the sample at its own start when it
is not there yet, in the background, by the setup step itself (`scripts/make_samples.py` started as its own process),
only when `ALBEDIAN_SAMPLES_AT_START` is `1` (the field run sets it, scripts/field.mjs), so a test or CI never fetches
or solves; the server starts and serves while it runs, and stops it with itself if it is still running."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

from fastapi.testclient import TestClient
import pytest

from services.api import create_app
from services.samples import make


class _Step:
    def __init__(self, running: bool) -> None:
        self.running, self.stopped = running, False

    def poll(self) -> Any:
        return None if self.running else 0

    def terminate(self) -> None:
        self.stopped = True


def test_nothing_starts_unless_the_field_run_asks() -> None:
    started: list[Any] = []
    for environ in ({}, {"ALBEDIAN_SAMPLES_AT_START": "0"}, {"ALBEDIAN_SAMPLES_AT_START": "yes"}):
        assert make.start_making_samples(environ=environ, popen=lambda *a, **k: started.append(a)) is None, environ
    assert started == []


def test_asked_it_starts_the_setup_step_itself_and_does_not_wait() -> None:
    calls: list[tuple[Any, dict[str, Any]]] = []
    step = _Step(running=True)

    def popen(args: Any, **kwargs: Any) -> _Step:
        calls.append((args, kwargs))
        return step

    assert make.start_making_samples(environ={"ALBEDIAN_SAMPLES_AT_START": "1"}, popen=popen) is step
    [(args, kwargs)] = calls
    assert args == [sys.executable, str(make.ROOT / "scripts" / "make_samples.py")]
    assert kwargs["cwd"] == make.ROOT
    assert kwargs["stdout"] == kwargs["stderr"] == kwargs["stdin"] == subprocess.DEVNULL


def test_a_step_that_cannot_start_is_logged_by_its_kind(caplog: pytest.LogCaptureFixture) -> None:
    def popen(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError("/private/path/python")

    assert make.start_making_samples(environ={"ALBEDIAN_SAMPLES_AT_START": "1"}, popen=popen) is None
    assert [r.getMessage() for r in caplog.records if r.name == "albedian.samples"] == ["sample not started: FileNotFoundError"]


@pytest.mark.parametrize("running", [True, False])
def test_the_server_starts_it_and_stops_it_with_itself(monkeypatch: pytest.MonkeyPatch, running: bool) -> None:
    step = _Step(running)
    monkeypatch.setattr(make, "start_making_samples", lambda *a, **k: step)
    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
        assert client.get("/api/health").status_code == 200
        assert not step.stopped
    assert step.stopped is running
