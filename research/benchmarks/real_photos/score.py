"""Judging each claim on a real photo against the independent check (astrometry.net, `oracle.py`).

A claim is a `calibrated` solution: the product says its catalog_matched findings are verified stars. The rule is the
simulated benchmark's (`research/benchmarks/phone_photos/score.py`), read and restated here, not imported: a real
photo has no truth model, so the check's WCS stands where the rendered truth stood there, and only its match measure
carries over. For each `catalog_matched` finding, the pixel the check's WCS gives that star (`wcs-rd2xy`) is compared
with the finding's measured pixel; the star is confirmed when they lie within max(4 px, 2 FWHM), FWHM being that
detection's own.

Every finding is judged by itself, against the catalog stars the check's WCS places near its detection:

- `confirmed`: the named star lies within tolerance of the detection;
- `wrong_star`: the named star does not, and another catalog star does: the claim names the wrong star;
- `misplaced`: no catalog star lies within tolerance, the named star is the nearest one, within twice the tolerance
  [proposed]: the right star, placed badly;
- `no_star`: no catalog star within tolerance, and the named one is not the nearest or lies further than that.

A `wrong_star` or `no_star` finding is a false claim of the product, whatever the claim's outcome; such claims head
the summary. What the check cannot tell: it judges a position, not a name. A finding is confirmed when the star it
names lies where the check puts that star; if two catalog stars lie within tolerance of one detection, the check
cannot say which of them the detection is.

Outcomes of a claim as a whole:

- `correct`: the median offset over the claim's catalog_matched findings is at most 3 px, the bound the simulated
  benchmark uses for its field error and the product's own held-out gate promises (MAX_HELD_OUT_MEDIAN_PX);
- `imprecise`: the median is over 3 px, but at least half of the findings are confirmed stars;
- `wrong`: fewer than half are confirmed, or the claim names no star at all;
- `false_solve`: any claim on a tier D photo (no solvable sky), whatever the check says;
- `unchecked`: a claim the check cannot judge, because the check did not solve that photo (or solved a frame of
  another size). Never counted as correct;
- `unresolved`, `timeout` (the solve stopped at its time bound), `error` (the upload or the job failed): no claim.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import median
from typing import Any, Optional, Sequence

OUTCOMES = ("correct", "imprecise", "wrong", "false_solve", "unchecked", "unresolved", "timeout", "error")
CLAIM_OUTCOMES = ("correct", "imprecise", "wrong", "false_solve", "unchecked")
FINDING_VERDICTS = ("confirmed", "misplaced", "wrong_star", "no_star")
FALSE_FINDINGS = ("wrong_star", "no_star")
# From research/benchmarks/phone_photos/score.py: CORRECT_MEDIAN_PX, the half of matches for imprecise, and the
# match tolerance max(4 px, 2 FWHM) (its third term, 1.5 trail lengths, is for rendered trails, which a still lacks)
CORRECT_MEDIAN_PX = 3.0
MIN_CONFIRMED_SHARE = 0.5
TOLERANCE_FLOOR_PX = 4.0
TOLERANCE_FWHM_FACTOR = 2.0
# [proposed] How far, in tolerances, the named star may lie and still be the right star placed badly
MISPLACED_FACTOR = 2.0
# The tier whose photos hold no solvable sky (docs/science/REAL_PHOTO_CORPUS.md)
NO_SKY_TIER = "D"
# The two paths a claim comes from: the product's upload path, and the GPS-time diagnostic, which is not the product
CLAIM_PATHS = ("upload", "diagnostic gps time")


@dataclass(frozen=True)
class MatchedFinding:
    """One catalog_matched finding of a claim: where it was measured and which star it names."""

    x_px: float
    y_px: float
    fwhm_px: float
    ra_deg: float
    dec_deg: float
    star_id: str


@dataclass(frozen=True)
class Claim:
    """What the product produced for one photo, as far as judging needs it."""

    status: Optional[str]
    matches: tuple[MatchedFinding, ...] = ()
    stopped_at_bound: bool = False
    error: Optional[str] = None
    width_px: Optional[int] = None
    height_px: Optional[int] = None

    @property
    def claimed(self) -> bool:
        return self.error is None and self.status == "calibrated"


@dataclass
class Judgement:
    outcome: str
    claimed: bool
    check_solved: bool
    matches_judged: int = 0
    matches_confirmed: int = 0
    confirmed_share: Optional[float] = None
    median_offset_px: Optional[float] = None
    worst_offset_px: Optional[float] = None
    # Each finding by itself: its star, its offset from the check's place for that star, its tolerance, the distance
    # from its detection to the nearest other catalog star, and its verdict (FINDING_VERDICTS)
    findings: list[dict[str, Any]] = field(default_factory=list)
    finding_counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(FINDING_VERDICTS, 0))
    # The star ids of the findings that are false claims: a wrong star, or no star where the detection is
    false_findings: list[str] = field(default_factory=list)
    reason: Optional[str] = None
    # The check's own median distance between its paired sources and index stars: how finely it can judge
    check_median_residual_px: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def tolerance_px(fwhm_px: float) -> float:
    """max(4 px, 2 FWHM); a FWHM that is not a finite positive number counts as none."""
    fwhm = fwhm_px if isinstance(fwhm_px, (int, float)) and math.isfinite(fwhm_px) and fwhm_px > 0 else 0.0
    return max(TOLERANCE_FLOOR_PX, TOLERANCE_FWHM_FACTOR * fwhm)


def _finite_or_inf(value: float) -> float:
    return value if isinstance(value, (int, float)) and math.isfinite(value) else math.inf


def finding_verdict(offset_px: float, tolerance: float, nearest_other_px: float) -> str:
    """One finding's verdict from the named star's offset and the nearest other catalog star's distance, both from the
    detection and both placed by the check's WCS (NaN or infinity: none there)."""
    offset, other = _finite_or_inf(offset_px), _finite_or_inf(nearest_other_px)
    if offset <= tolerance:
        return "confirmed"
    if other <= tolerance:
        return "wrong_star"
    if offset <= MISPLACED_FACTOR * tolerance and offset <= other:
        return "misplaced"
    return "no_star"


def judge(
    tier: str,
    claim: Claim,
    check_solved: bool,
    check_pixels: Optional[Sequence[tuple[float, float]]] = None,
    check_size: Optional[tuple[int, int]] = None,
    other_star_px: Optional[Sequence[float]] = None,
) -> Judgement:
    """The outcome of one claim, and the verdict of each of its findings. On a photo the check solved,
    `check_pixels[i]` is where the check's WCS puts the star of `claim.matches[i]` (NaN on the far side) and
    `other_star_px[i]` how far the nearest other catalog star lies from that finding's detection."""
    if claim.error is not None:
        return Judgement("error", False, check_solved, reason=claim.error)
    if not claim.claimed:
        return Judgement("timeout" if claim.stopped_at_bound else "unresolved", False, check_solved)
    judgement = Judgement("unchecked", True, check_solved)
    same_frame = check_size is None or claim.width_px is None or (claim.width_px, claim.height_px) == tuple(check_size)
    if check_solved and same_frame:
        if check_pixels is None or len(check_pixels) != len(claim.matches):
            raise ValueError("a claim on a photo the check solved needs one check pixel per catalog_matched finding")
        if other_star_px is None or len(other_star_px) != len(claim.matches):
            raise ValueError("a claim on a photo the check solved needs the nearest other catalog star of each finding")
        offsets = []
        for finding, (x, y), other in zip(claim.matches, check_pixels, other_star_px):
            offset = _finite_or_inf(math.hypot(x - finding.x_px, y - finding.y_px))
            offsets.append(offset)
            tolerance, nearest = tolerance_px(finding.fwhm_px), _finite_or_inf(other)
            verdict = finding_verdict(offset, tolerance, nearest)
            judgement.finding_counts[verdict] += 1
            judgement.findings.append(
                {
                    "starId": finding.star_id,
                    "offsetPx": offset if math.isfinite(offset) else None,
                    "tolerancePx": tolerance,
                    "nearestOtherPx": nearest if math.isfinite(nearest) else None,
                    "verdict": verdict,
                }
            )
            if verdict in FALSE_FINDINGS:
                judgement.false_findings.append(finding.star_id)
        judgement.matches_judged = len(offsets)
        judgement.matches_confirmed = judgement.finding_counts["confirmed"]
        if offsets:
            judgement.confirmed_share = judgement.matches_confirmed / len(offsets)
            mid = median(offsets)
            judgement.median_offset_px = mid if math.isfinite(mid) else None
            worst = max(offsets)
            judgement.worst_offset_px = worst if math.isfinite(worst) else None
    if tier == NO_SKY_TIER:
        judgement.outcome = "false_solve"
        judgement.reason = "a claim on a tier D photo, which holds no solvable sky"
    elif not check_solved:
        judgement.reason = "the check did not solve this photo, so the claim cannot be judged"
    elif not same_frame:
        judgement.reason = "the check solved a frame of another size than the claim's"
    elif not claim.matches:
        judgement.outcome = "wrong"
        judgement.reason = "a calibrated claim with no catalog_matched finding names no star the check can confirm"
    elif judgement.median_offset_px is not None and judgement.median_offset_px <= CORRECT_MEDIAN_PX:
        judgement.outcome = "correct"
    elif judgement.confirmed_share is not None and judgement.confirmed_share >= MIN_CONFIRMED_SHARE:
        judgement.outcome = "imprecise"
    else:
        judgement.outcome = "wrong"
    return judgement


NeighbourFinder = Callable[[Any, Claim, Path], Sequence[float]]


def judge_with_check(tier: str, claim: Claim, record: Any, scratch_root: Path, nearest_other: Optional[NeighbourFinder] = None) -> Judgement:
    """Judge a claim against an oracle record, turning each named star into a pixel with the check's own WCS.
    `nearest_other(record, claim, scratch_root)` gives each finding's nearest other catalog star (measure.py's, from
    the repository's star tiles placed by the same WCS); it is needed for a claim on a photo the check solved."""
    from research.benchmarks.real_photos import oracle

    solved = bool(record is not None and record.solved)
    pixels = None
    size = None
    others = None
    if solved and claim.claimed:
        wcs_file = oracle.wcs_path(record)
        pixels = oracle.sky_to_pixel(wcs_file, [m.ra_deg for m in claim.matches], [m.dec_deg for m in claim.matches], scratch_root)
        size = (record.image_width_px, record.image_height_px)
        others = list(nearest_other(record, claim, scratch_root)) if nearest_other is not None else None
    judgement = judge(tier, claim, solved, pixels, size, others)
    judgement.check_median_residual_px = record.check_median_residual_px if solved else None
    return judgement


def claim_from_solution(solution: Optional[dict[str, Any]], *, stopped_at_bound: bool = False, error: Optional[str] = None) -> Claim:
    """A Claim from a stored solution summary (the `matches` list measure.py keeps for each catalog_matched finding)."""
    if solution is None:
        return Claim(status=None, error=error or "no solution")
    matches = tuple(
        MatchedFinding(m["xPx"], m["yPx"], m.get("fwhmPx", 0.0), m["raDeg"], m["decDeg"], m["starId"]) for m in solution.get("matches", [])
    )
    return Claim(
        status=solution.get("status"),
        matches=matches,
        stopped_at_bound=stopped_at_bound,
        error=error,
        width_px=solution.get("widthPx"),
        height_px=solution.get("heightPx"),
    )


# -------------------------------------------------------------------------------------------------
# Summaries
# -------------------------------------------------------------------------------------------------


def _seconds(values: list[float]) -> dict[str, Optional[float]]:
    return {"median": round(median(values), 1) if values else None, "worst": round(max(values), 1) if values else None}


def tier_row(frames: list[dict[str, Any]]) -> dict[str, Any]:
    """One tier's line of the outcome table."""
    outcomes = [frame["judgement"]["outcome"] for frame in frames]
    statuses: dict[str, int] = {}
    settings: dict[str, int] = {}
    for frame in frames:
        status = frame.get("solution", {}).get("status") if frame.get("solution") else None
        key = status if status is not None else f"job {frame.get('job', {}).get('state', 'not run')}"
        statuses[key] = statuses.get(key, 0) + 1
        check = frame.get("oracle") or {}
        if check.get("solved"):
            label = (check.get("settings") or {}).get("label", "unknown")
            settings[label] = settings.get(label, 0) + 1
    return {
        "photos": len(frames),
        "checkSolved": sum(1 for frame in frames if (frame.get("oracle") or {}).get("solved")),
        "checkSettings": dict(sorted(settings.items())),
        "statuses": dict(sorted(statuses.items())),
        "outcomes": {name: outcomes.count(name) for name in OUTCOMES},
        "claims": sum(1 for name in outcomes if name in CLAIM_OUTCOMES),
        "seconds": _seconds([frame["seconds"]["solve"] for frame in frames if (frame.get("seconds") or {}).get("solve") is not None]),
    }


def _judged_claims(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every claim, the upload path's first and then the GPS-time diagnostic's, each with its photo and path."""
    claims = [{"photoId": frame["photoId"], "path": "upload", **frame["judgement"]} for frame in frames if frame["judgement"]["claimed"]]
    for frame in frames:
        gps = ((frame.get("diagnostics") or {}).get("gpsTime") or {}).get("judgement") or {}
        if gps.get("claimed"):
            claims.append({"photoId": frame["photoId"], "path": "diagnostic gps time", **gps})
    return claims


def summarize(frames: list[dict[str, Any]]) -> dict[str, Any]:
    tiers = sorted({frame["tier"] for frame in frames})
    claims = _judged_claims(frames)
    flagged = [item for item in claims if item["outcome"] in ("wrong", "false_solve")]
    totals = dict.fromkeys(FINDING_VERDICTS, 0)
    by_path = {path: dict.fromkeys(FINDING_VERDICTS, 0) for path in CLAIM_PATHS}
    false_findings = []
    for item in claims:
        for verdict, count in (item.get("finding_counts") or {}).items():
            totals[verdict] += count
            by_path[item["path"]][verdict] += count
        verdicts = [(finding["starId"], finding["verdict"]) for finding in item.get("findings") or []]
        wrong = [star for star, verdict in verdicts if verdict == "wrong_star"]
        absent = [star for star, verdict in verdicts if verdict == "no_star"]
        if wrong or absent:
            false_findings.append({"photoId": item["photoId"], "path": item["path"], "wrong_star": wrong, "no_star": absent})
    return {
        "photos": len(frames),
        "byTier": {tier: tier_row([frame for frame in frames if frame["tier"] == tier]) for tier in tiers},
        "all": tier_row(frames),
        "claims": claims,
        "findings": totals,
        "findingsByPath": by_path,
        "falseFindings": false_findings,
        "wrongOrFalseSolve": flagged,
    }


def _counts_text(counts: dict[str, Any]) -> str:
    return f"misplaced {counts.get('misplaced', 0)}, wrong star {counts.get('wrong_star', 0)}, no star {counts.get('no_star', 0)}"


def to_markdown(summary: dict[str, Any], label: str) -> str:
    """The claims that name a wrong star or a star that is not there first, then the outcome table per tier, labelled
    with the run; counts, ids and seconds only."""
    heads = summary["falseFindings"]
    lines = [
        f"Run `{label}`: {summary['photos']} real photos through the upload path, each claim judged by astrometry.net.",
        "",
        f"Claims that name a wrong star or a star that is not there: {len(heads)}"
        + (f" ({', '.join(item['photoId'] for item in heads)})." if heads else "."),
    ]
    for item in heads:
        lines.append(f"- {item['photoId']} ({item['path']}): wrong star {', '.join(item['wrong_star']) or 'none'}; no star {', '.join(item['no_star']) or 'none'}")
    totals, by_path = summary["findings"], summary["findingsByPath"]
    paths = "; ".join(
        f"{name} confirmed {by_path[path]['confirmed']}, {_counts_text(by_path[path])}"
        for path, name in zip(CLAIM_PATHS, ("upload path", "GPS time diagnostic, not the product path"))
    )
    lines += [
        "",
        f"Findings judged one by one, over the claims of both paths: confirmed {totals['confirmed']}, {_counts_text(totals)} ({paths}).",
        "",
        "| tier | photos | solved by the check | our statuses | correct | imprecise | wrong | false solves | unchecked | stopped at the bound | median s | worst s |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    rows = [(tier, row) for tier, row in summary["byTier"].items()] + [("all", summary["all"])]
    for tier, row in rows:
        statuses = ", ".join(f"{name} {count}" for name, count in row["statuses"].items())
        o = row["outcomes"]
        lines.append(
            f"| {tier} | {row['photos']} | {row['checkSolved']} | {statuses} | {o['correct']} | {o['imprecise']} | {o['wrong']}"
            f" | {o['false_solve']} | {o['unchecked']} | {o['timeout']} | {row['seconds']['median']} | {row['seconds']['worst']} |"
        )
    lines += ["", "Settings the check solved with, per tier:"]
    for tier, row in rows:
        settings = ", ".join(f"{name} {count}" for name, count in row["checkSettings"].items()) or "none"
        lines.append(f"- {tier}: {settings}")
    lines += ["", f"Claims: {len(summary['claims'])}."]
    for item in summary["claims"]:
        median_px = "n/a" if item["median_offset_px"] is None else f"{item['median_offset_px']:.2f} px"
        worst_px = "n/a" if item["worst_offset_px"] is None else f"{item['worst_offset_px']:.2f} px"
        lines.append(
            f"- {item['photoId']} ({item['path']}): {item['outcome']}, {item['matches_confirmed']} of {item['matches_judged']}"
            f" confirmed ({_counts_text(item.get('finding_counts') or {})}), median offset {median_px}, worst {worst_px},"
            f" check residual {item['check_median_residual_px']} px"
        )
    flagged = summary["wrongOrFalseSolve"]
    lines += ["", f"Wrong or false-solve claims: {len(flagged)}."]
    for item in flagged:
        lines.append(f"- {item['photoId']} ({item['path']}): {item['outcome']}, {item['matches_confirmed']} of {item['matches_judged']} confirmed")
    return "\n".join(lines) + "\n"
