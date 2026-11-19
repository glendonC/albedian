"""Which solution an upload serves after a solve from a person's alignment (align-2v; the lead's 12:32 answer: the
newest succeeded solve's, except that a calibrated solution is never replaced by one that is not calibrated).

Read narrowly [proposed]: the exception is the aligned solve's. A solve from a person's alignment whose gate refused
never takes the place of a calibrated solution the upload already serves; one that calibrated does; a plain solve
keeps today's rule, the newest succeeded one (a producer revision's re-solve is the truth of that revision).

Covers, through the runner with a fake solve that publishes a calibrated or a predicted-only solution:
- plain calibrated, then aligned predicted-only: the plain calibrated one stays served, and the manifest names it
- plain calibrated, then aligned calibrated: the aligned one is served
- plain predicted-only, then aligned predicted-only: the newest (the aligned one) is served
- plain calibrated, then plain predicted-only (a re-solve at a raised revision), then aligned predicted-only: the
  newest, never the older calibrated one brought back
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from services.ingest.job_store import JobStore
from services.ingest.runner import JobContext, JobKind, KindResult, Producer
from tests.integration.test_job_runner import BOUND_S, Script, solve_kind, world  # noqa: F401

FIXTURE = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "observation_solution_contract.json")
                     .read_text(encoding="utf-8"))["valid"]
ALIGNMENT = {"format": "albedian.person-alignment", "formatVersion": 1, "source": "person", "centerAltDeg": 41.5,
             "centerAzDeg": 128.25, "rollDeg": -6.0, "fovDiagonalDeg": 68.5}


def _payload(sha: str, sample: str) -> dict[str, Any]:
    fixture = FIXTURE[sample]
    text = json.dumps(fixture).replace(fixture["sourceSha256"], sha).replace(fixture["sourceAssetId"], f"src_{sha[:16]}")
    out = json.loads(text)
    out["id"] = f"sol_{sha[:16]}"
    return out


def _publishing(outcomes: list[str]) -> Callable[[JobContext], KindResult]:
    """A solve body publishing the next sample of `outcomes` for each job it runs, in order."""
    def body(ctx: JobContext) -> KindResult:
        partial = ctx.partial("solution.json")
        partial.write_text(json.dumps(_payload(ctx.sha256, outcomes.pop(0))), encoding="utf-8")
        ctx.publish(partial, ctx.solution_path())
        return KindResult("succeeded", {"solutionId": f"sol_{ctx.sha256[:16]}"})

    return body


def _run(world: dict[str, Any], sha: str, outcomes: list[str], requests: list[dict[str, Any] | None]) -> list[str]:
    script = Script()
    world["kinds"]["solve"] = JobKind(**{**solve_kind(script, _publishing(outcomes)).__dict__})
    runner = world["runner"]()
    ids = []
    for request in requests:
        job, created = runner.start(sha, "solve", request)
        assert created
        assert runner.wait(job["id"], BOUND_S)["state"] == "succeeded"
        ids.append(job["id"])
    return ids


def _served(world: dict[str, Any], sha: str) -> tuple[str, str | None]:
    store: JobStore = world["store"]
    solution = store.read_solution(sha)
    return solution["status"], store.served_solve(sha)["id"]


def test_an_aligned_solve_that_did_not_calibrate_never_replaces_a_calibrated_one(world: dict[str, Any]) -> None:
    sha = world["shas"][0]
    plain, aligned = _run(world, sha, ["calibrated", "predicted_only_with_a_persons_alignment"], [None, dict(ALIGNMENT)])
    assert _served(world, sha) == ("calibrated", plain)
    assert world["store"].newest_solution_id(sha) == f"sol_{sha[:16]}"


def test_an_aligned_solve_that_calibrated_is_served(world: dict[str, Any]) -> None:
    sha = world["shas"][1]
    _, aligned = _run(world, sha, ["calibrated", "calibrated_with_a_persons_alignment"], [None, dict(ALIGNMENT)])
    assert _served(world, sha) == ("calibrated", aligned)


def test_with_no_calibrated_solution_the_newest_is_served(world: dict[str, Any]) -> None:
    sha = world["shas"][2]
    _, aligned = _run(world, sha, ["predicted_only", "predicted_only_with_a_persons_alignment"], [None, dict(ALIGNMENT)])
    assert _served(world, sha) == ("predicted_only", aligned)


def test_a_plain_re_solve_is_never_undone_by_an_aligned_one(world: dict[str, Any]) -> None:
    sha = world["shas"][0]
    store: JobStore = world["store"]
    (first,) = _run(world, sha, ["calibrated"], [None])
    # A producer revision raise makes the plain solve run again; this time its gate refuses
    outcomes = ["predicted_only", "predicted_only_with_a_persons_alignment"]
    kind = solve_kind(Script(), _publishing(outcomes))
    world["kinds"]["solve"] = JobKind(**{**kind.__dict__, "producer": Producer(kind.producer.name, kind.producer.revision + 1)})
    runner = world["runner"]()
    again, created = runner.start(sha, "solve")
    assert created and runner.wait(again["id"], BOUND_S)["state"] == "succeeded"
    assert store.served_solve(sha)["id"] == again["id"] != first
    # A third, aligned, that did not calibrate: no calibrated solution is served before it, so it is the newest and
    # served; the older calibrated one is never brought back
    job, created = runner.start(sha, "solve", dict(ALIGNMENT))
    assert created and runner.wait(job["id"], BOUND_S)["state"] == "succeeded"
    assert _served(world, sha) == ("predicted_only", job["id"])
