"""Scoring: what each solver claimed on each frame, judged against the frame's truth.

Every claim is judged on two independent measures:

- the field error: where the claimed WCS puts the sky seen at a grid of pixels (on the sky where
  there is sky), against where the truth puts it; and
- match precision: the share of the solver's catalog matches whose detection lies on that star's
  true pixel, within max(4 px, 2 FWHM, 1.5 trail lengths).

Outcomes:

- `correct`: a claim whose median field error is at most 3 px, the same bound the product's own
  held-out gate promises;
- `imprecise`: a claim that names the right stars (match precision at least one half) but places
  the field worse than 3 px;
- `wrong`: a claim on the wrong stars;
- `false-solve`: a claim on a frame with no sky behind it (screen, random dots, dark frame);
- `unresolved`, `timeout`, `error`: no claim.

A false claim is any claim that is not `correct`. On hard negatives with a real sky behind them a
correct claim is not false, only unexpected. The Track 1 bar is zero false claims on the hard
negatives. Results are simulated and are never evidence that a real photo solves.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np

from research.benchmarks.phone_photos import solvers
from research.benchmarks.phone_photos.render import FrameTruth, RenderedFrame

OUTCOMES = ("correct", "imprecise", "wrong", "false-solve", "unresolved", "timeout", "error")
CORRECT_MEDIAN_PX = 3.0


@dataclass
class FrameScore:
    frame_id: str
    kind: str
    category: str
    camera: str
    status: str
    outcome: str
    claimed: bool
    false_solve: bool
    field_median_px: float | None
    field_p95_px: float | None
    match_precision: float | None
    matches: int
    detections: int
    held_out_inliers: int
    self_median_residual_px: float | None
    elapsed_s: float
    rendered_stars: int
    error: str | None = None


def score_frame(truth: FrameTruth, outcome: solvers.SolverOutcome, camera: str = "") -> FrameScore:
    median = p95 = precision = None
    if not outcome.claimed:
        verdict = outcome.status if outcome.status in ("timeout", "error") else "unresolved"
    elif truth.camera_truth is None:
        verdict = "false-solve"
    else:
        errors = solvers.field_errors_px(truth.camera_truth, outcome.wcs, truth.sky_mask) if outcome.wcs is not None else np.array([math.inf])
        median, p95 = float(np.median(errors)), float(np.percentile(errors, 95.0))
        if outcome.matches:
            det = np.array([(m[0], m[1]) for m in outcome.matches])
            tx, ty = truth.camera_truth.icrs_to_pixel(np.array([m[2] for m in outcome.matches]), np.array([m[3] for m in outcome.matches]))
            tolerance = max(4.0, 2.0 * truth.psf_fwhm_px, 1.5 * truth.trail_px)
            with np.errstate(invalid="ignore"):
                precision = float(np.mean(np.hypot(tx - det[:, 0], ty - det[:, 1]) <= tolerance))
        else:
            precision = 0.0
        if median <= CORRECT_MEDIAN_PX:
            verdict = "correct"
        elif precision >= 0.5:
            verdict = "imprecise"
        else:
            verdict = "wrong"
    return FrameScore(
        frame_id=truth.frame_id,
        kind=truth.kind,
        category=truth.category,
        camera=camera,
        status=outcome.status,
        outcome=verdict,
        claimed=outcome.claimed,
        false_solve=outcome.claimed and verdict != "correct",
        field_median_px=median if median is None or math.isfinite(median) else None,
        field_p95_px=p95 if p95 is None or math.isfinite(p95) else None,
        match_precision=precision,
        matches=len(outcome.matches),
        detections=outcome.detections,
        held_out_inliers=outcome.held_out_inliers,
        self_median_residual_px=outcome.self_median_residual_px,
        elapsed_s=outcome.elapsed_s,
        rendered_stars=int(truth.star_x.size),
        error=outcome.error,
    )


@dataclass
class Report:
    frames: list[FrameScore]
    summary: dict


def _counts(scores: list[FrameScore]) -> dict:
    out = {name: sum(1 for s in scores if s.outcome == name) for name in OUTCOMES}
    out["count"] = len(scores)
    out["falseClaims"] = sum(1 for s in scores if s.false_solve)
    return out


def summarize(scores: list[FrameScore], solver_name: str) -> dict:
    positives = [s for s in scores if s.kind == "positive"]
    negatives = [s for s in scores if s.kind == "negative"]
    claimed = [s for s in scores if s.claimed and s.field_median_px is not None]
    times = np.array([s.elapsed_s for s in scores]) if scores else np.zeros(1)
    by_category: dict[str, dict] = {}
    for name in sorted({s.category for s in scores}):
        by_category[name] = _counts([s for s in scores if s.category == name])
    by_camera: dict[str, dict] = {}
    for name in sorted({s.camera for s in scores}):
        by_camera[name] = _counts([s for s in positives if s.camera == name])
    pos = _counts(positives)
    pos["solveRate"] = (pos["correct"] / pos["count"]) if pos["count"] else None
    neg = _counts(negatives)
    neg["falseSolves"] = neg["falseClaims"]
    return {
        "evidenceClass": "simulated",
        "disclosure": "Simulated frames rendered from catalog stars; never evidence that a real photo solves.",
        "solver": solver_name,
        "frames": len(scores),
        "positives": pos,
        "negatives": neg,
        "byCategory": by_category,
        "byCamera": by_camera,
        "timeS": {"median": float(np.median(times)), "p95": float(np.percentile(times, 95.0)), "max": float(np.max(times))},
        "claims": {
            "count": len(claimed),
            "selfReportedMedianPx": [s.self_median_residual_px for s in claimed],
            "trueFieldMedianPx": [s.field_median_px for s in claimed],
        },
    }


def run_frames(frames: list[RenderedFrame], solver_name: str, timeout_s: float) -> Report:
    """Run a solver over rendered frames in this process and score every frame."""
    scores = []
    for frame in frames:
        outcome = solvers.run_solver(solver_name, solvers.SolverInput(frame.rgb, frame.truth.metadata), timeout_s)
        scores.append(score_frame(frame.truth, outcome, camera=frame.spec.camera))
    return Report(frames=scores, summary=summarize(scores, solver_name))


def to_markdown(summary: dict) -> str:
    """A small table of outcomes, labelled simulated."""
    pos, neg = summary["positives"], summary["negatives"]
    lines = [
        f"Solver `{summary['solver']}` on {summary['frames']} simulated frames ({summary['evidenceClass']}).",
        "",
        "| outcome | positives | hard negatives |",
        "|---|---|---|",
    ]
    for name in OUTCOMES:
        lines.append(f"| {name} | {pos.get(name, 0)} | {neg.get(name, 0)} |")
    rate = "n/a" if pos.get("solveRate") is None else f"{100.0 * pos['solveRate']:.1f}%"
    lines += [
        "",
        f"Solve rate on positives: {rate}. False claims: {pos['falseClaims']} on positives, {neg['falseSolves']} on hard negatives.",
        f"Time per frame: median {summary['timeS']['median']:.2f} s, p95 {summary['timeS']['p95']:.2f} s, max {summary['timeS']['max']:.2f} s.",
    ]
    return "\n".join(lines)


def score_to_dict(score: FrameScore) -> dict:
    return asdict(score)


# Worker entry points for parallel runs; they live here, not in __main__, so spawned processes import them
_WORKER_CATALOG = None


def init_worker() -> None:
    from research.benchmarks.phone_photos.stars import TileCatalog

    global _WORKER_CATALOG
    _WORKER_CATALOG = TileCatalog()


def run_one(task: tuple[dict, str, float, str | None]) -> dict:
    """Render one frame from its spec, run a solver on it and score it (in a worker process)."""
    import json
    import time
    from pathlib import Path

    from research.benchmarks.phone_photos import render, suite

    spec_dict, solver_name, timeout_s, frames_dir = task
    spec = suite.FrameSpec.from_dict(spec_dict)
    start = time.perf_counter()
    frame = render.render_frame(spec, _WORKER_CATALOG)
    render_s = time.perf_counter() - start
    outcome = solvers.run_solver(solver_name, solvers.SolverInput(frame.rgb, frame.truth.metadata), timeout_s)
    scored = score_frame(frame.truth, outcome, camera=spec.camera)
    if frames_dir is not None:
        folder = Path(frames_dir)
        (folder / f"{spec.frame_id}.jpg").write_bytes(frame.jpeg_bytes)
        (folder / f"{spec.frame_id}.truth.json").write_text(json.dumps(frame.truth.summary(), indent=1), encoding="utf-8")
    return {"score": score_to_dict(scored), "renderS": render_s, "spec": spec_dict, "truth": frame.truth.summary()}
