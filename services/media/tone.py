"""The lift (spec restoration-for-every-upload, design section 6; REQ-RST-4.4 to 4.6, 5.3).

`f(x) = asinh(x / beta) / asinh(1 / beta)` on each channel in linear light, the asinh stretch of Lupton et al. 2004
[verified in design section 6: the arXiv abstract, astro-ph/0312483]: linear near black with slope
`1 / (beta asinh(1 / beta))`, never clipping, 0 to 0 and 1 to 1, with an exact inverse. Its one parameter is solved
per file, by bisection on log(beta), as the largest of the target, the noise bound, the quantisation bound and the
maximum; the record names the one that decided. Takes the transfer's slope and decode as arguments, so this module
reads no file and knows no transfer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

STEP_VERSION = "1.0.0"
# Without a request the frame's median goes to the light of code 43 of 255 [proposed: design section 6, the median of
# the medians of the five real night photos, 15, 36, 43, 53 and 56 of 255]
TARGET_MEDIAN_CODE = 43
# The scale of that code: from black to peak in 255 steps [proposed: design section 6, as the photos' 8 bit codes]
TARGET_CODE_STEPS = 255
# One code step of the original at the dark level becomes at most this many output steps [proposed: design section
# 6, a first value to be set from pictures, ruling 10 of the requirements]
QUANTISATION_BOUND_STEPS = 4.0
# The largest gain at black, six stops [proposed: design section 6]
MAXIMUM_GAIN = 64.0
# The dark level is the median luminance of this darkest share of the frame [proposed: design section 6, "the
# darkest quarter"]
DARK_SHARE = 0.25
# The search range for beta: a gain at black of practically 1 at the top and far above any bound at the bottom
# [proposed: gain_at_black(1e12) - 1 is under 1e-24 and gain_at_black(1e-12) is about 3.6e10]
BETA_LOW, BETA_HIGH = 1e-12, 1e12
# Halvings of a bracket in log(beta): 55 nats to under 1e-28 of one [proposed: far finer than any recorded digit]
BISECTION_STEPS = 100
# Points of the scan from the weakest lift to the strongest that brackets a bound's edge [proposed: 0.028 nats of
# log(beta) apart, so a bound broken over less than that is the most a scan can miss; tested by P-RST-8]
SCAN_POINTS = 2000

# The closed list of the media contract's notes (REQ-RST-4.3 to 4.5)
NOTE_REQUEST_HELD = "The requested exposure was beyond a measured bound, so the lift stops at that bound."
NOTE_MAXIMUM = "The lift was held by its maximum."
NOTE_NOISE_NOT_MEASURED = ("The noise could not be measured, so no noise reduction ran and the lift is held by the quantisation "
                           "bound alone.")
SOLVED_BASIS = "Solved for this file from its median and its measured bounds."
DEFAULT_BASIS = "Proposed in design section 6."
# What decided the gain at black, and the origin and basis the lift step records for it: a parameter's origin and basis
# name what decided its value and nothing else (request restore-3-v2, item 1). A request is recorded as requested, by
# the request's own field; the maximum as the design's default; the target (the frame's median), a measured bound
# (the noise left after noise reduction, the quantisation of the dark level) and no lift for a median at or above the
# target as solved for this file
GAIN_RECORDS = {
    "requested": ("requested", "exposureEv"),
    "maximum": ("default", DEFAULT_BASIS),
    "target": ("measured", SOLVED_BASIS),
    "noise_bound": ("measured", SOLVED_BASIS),
    "quantisation_bound": ("measured", SOLVED_BASIS),
    "none": ("measured", SOLVED_BASIS),
}

Slope = Callable[[np.ndarray], np.ndarray]


def curve(x: np.ndarray, beta: float) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    if math.isinf(beta):
        return x.copy()
    return np.arcsinh(x / beta) / math.asinh(1 / beta)


def inverse(y: np.ndarray, beta: float) -> np.ndarray:
    y = np.asarray(y, dtype=np.float64)
    if math.isinf(beta):
        return y.copy()
    return beta * np.sinh(y * math.asinh(1 / beta))


def slope(x: np.ndarray, beta: float) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    if math.isinf(beta):
        return np.ones_like(x)
    return 1 / (beta * np.sqrt(1 + (x / beta) ** 2) * math.asinh(1 / beta))


def gain_at_black(beta: float) -> float:
    return 1.0 if math.isinf(beta) else 1 / (beta * math.asinh(1 / beta))


def dark_level(luminance: np.ndarray) -> float:
    """The median luminance of the darkest quarter of the frame."""
    values = np.asarray(luminance, dtype=np.float64).ravel()
    return float(np.median(values[values <= np.percentile(values, 100 * DARK_SHARE)]))


def _code_ratio(dark: float, beta: float, encode_slope: Slope) -> float:
    """How many code steps one step of the original at the dark level becomes: E'(f(d)) f'(d) / E'(d)."""
    lifted = float(curve(np.array([dark]), beta)[0])
    return float(encode_slope(np.array(lifted)) * slope(np.array(dark), beta) / encode_slope(np.array(dark)))


def quantisation_ratio(dark: float, beta: float, encode_slope: Slope) -> float:
    return _code_ratio(dark, beta, encode_slope)


def noise_ratio(dark: float, beta: float, encode_slope: Slope, original_noise: float, working_noise: float) -> float:
    """The noise left after reduction, lifted and encoded, over the original's noise in its own codes:
    E'(f(d)) f'(d) s_r / (E'(d) s_o). At most 1 is the noise bound."""
    return _code_ratio(dark, beta, encode_slope) * working_noise / original_noise


def _code_ratios(dark: float, betas: np.ndarray, encode_slope: Slope) -> np.ndarray:
    """E'(f(d)) f'(d) / E'(d) for each beta of an array."""
    lifted = np.arcsinh(dark / betas) / np.arcsinh(1 / betas)
    slopes = 1 / (betas * np.sqrt(1 + (dark / betas) ** 2) * np.arcsinh(1 / betas))
    return np.asarray(encode_slope(lifted), dtype=np.float64) * slopes / float(encode_slope(np.array(dark)))


def _weakest_break(ratios: Callable[[np.ndarray], np.ndarray], bound: float) -> float:
    """The smallest beta from which every weaker lift keeps the ratio within `bound`. The ratio is not monotone in
    beta (a strong lift compresses the dark level, so its slope there falls again), so a scan from the weakest lift
    to the strongest finds the first that breaks it, and bisection on log(beta) its edge; BETA_LOW when no lift in
    the range breaks it, infinity when even the weakest does."""
    grid = np.exp(np.linspace(math.log(BETA_HIGH), math.log(BETA_LOW), SCAN_POINTS))
    broken = np.nonzero(ratios(grid) > bound)[0]
    if broken.size == 0:
        return BETA_LOW
    if broken[0] == 0:
        return math.inf
    high, low = float(grid[broken[0] - 1]), float(grid[broken[0]])
    for _ in range(BISECTION_STEPS):
        middle = math.sqrt(low * high)
        if float(ratios(np.array([middle]))[0]) <= bound:
            high = middle
        else:
            low = middle
    return high


def _smallest_beta(holds: Callable[[float], bool]) -> float:
    """The smallest beta (the strongest lift) for which `holds`, by bisection on log(beta); infinity when it does not
    hold even at the top of the range."""
    low, high = BETA_LOW, BETA_HIGH
    if not holds(high):
        return math.inf
    if holds(low):
        return low
    for _ in range(BISECTION_STEPS):
        middle = math.sqrt(low * high)
        if holds(middle):
            high = middle
        else:
            low = middle
    return high


@dataclass(frozen=True)
class LiftSolution:
    beta: float
    decided_by: str
    dark: float
    median: float
    exposure_ev: float | None
    notes: list[str] = field(default_factory=list)

    def record(self) -> dict:
        """The attempt's `Lift` record, camelCase as the contract writes it."""
        return {"gainAtBlack": gain_at_black(self.beta), "decidedBy": self.decided_by}

    def parameters(self) -> list[dict]:
        """The `lift` step's parameters, as the graph records them; the gain at black's origin and basis are its
        decider's row of GAIN_RECORDS."""
        origin, basis = GAIN_RECORDS[self.decided_by]
        found = [
            {"name": "gainAtBlack", "value": gain_at_black(self.beta), "origin": origin, "basis": basis},
            {"name": "targetMedianCode", "value": TARGET_MEDIAN_CODE, "origin": "default", "basis": DEFAULT_BASIS},
            {"name": "quantisationBoundSteps", "value": QUANTISATION_BOUND_STEPS, "origin": "default", "basis": DEFAULT_BASIS},
            {"name": "maximumGain", "value": MAXIMUM_GAIN, "origin": "default", "basis": DEFAULT_BASIS},
        ]
        if self.exposure_ev is not None:
            found.append({"name": "exposureEv", "value": self.exposure_ev, "origin": "requested", "basis": "exposureEv"})
        return found


def solve_lift(luminance: np.ndarray, *, encode_slope: Slope, decode: Slope, original_noise: float | None,
               working_noise: float | None, exposure_ev: float | None = None) -> LiftSolution:
    """beta for a frame's linear luminance. `encode_slope` is the derivative of the file's transfer encode, code over
    light on 0 to 1, and `decode` its decode, light from code. `original_noise` (s_o, the noise table at the dark
    level) and `working_noise` (s_r, measured at the same pixels after noise reduction) are both None when the noise
    could not be measured; then the noise bound does not apply (REQ-RST-4.3)."""
    values = np.asarray(luminance, dtype=np.float64)
    median = float(np.median(values))
    dark = dark_level(values)
    if exposure_ev is not None:
        wanted = 2.0**exposure_ev
        aim, aimed_by = (math.inf if wanted <= 1 else _smallest_beta(lambda beta: gain_at_black(beta) <= wanted)), "requested"
    else:
        target = float(np.asarray(decode(np.array(TARGET_MEDIAN_CODE / TARGET_CODE_STEPS))))
        aim = math.inf if median >= target else _smallest_beta(lambda beta: float(curve(np.array([median]), beta)[0]) <= target)
        aimed_by = "target" if not math.isinf(aim) else "none"
    if aimed_by == "none":
        return LiftSolution(math.inf, "none", dark, median, exposure_ev)
    ratios = lambda betas: _code_ratios(dark, betas, encode_slope)
    bounds = [
        ("quantisation_bound", _weakest_break(ratios, QUANTISATION_BOUND_STEPS)),
        ("maximum", _smallest_beta(lambda beta: gain_at_black(beta) <= MAXIMUM_GAIN)),
    ]
    if original_noise is not None and working_noise is not None:
        bounds.insert(0, ("noise_bound", _weakest_break(lambda betas: ratios(betas) * working_noise / original_noise, 1.0)))
    beta, decided_by = aim, aimed_by
    for name, bound in bounds:
        # A bound decides only where it holds the lift weaker than what was aimed at: one equal to the aim (both no lift
        # at all) leaves the aim to decide, so no note says a request was beyond a bound it met. Two bounds tie only at
        # BETA_LOW, under the maximum's own bound, so which of them comes first never decides.
        if bound > beta:
            beta, decided_by = bound, name
    notes = []
    if decided_by == "maximum":
        notes.append(NOTE_MAXIMUM)
    elif exposure_ev is not None and decided_by != "requested":
        notes.append(NOTE_REQUEST_HELD)
    # Said only where it is what happened: no noise was given (the job runs no noise reduction without a measured
    # table, REQ-RST-4.3) and the quantisation bound decided. Not written by the noise step, which cannot know what
    # holds the lift (request restore-3-v2, item 1)
    if original_noise is None and working_noise is None and decided_by == "quantisation_bound":
        notes.append(NOTE_NOISE_NOT_MEASURED)
    return LiftSolution(beta, decided_by, dark, median, exposure_ev, notes)


def apply_lift(frame: np.ndarray, beta: float) -> np.ndarray:
    """The same strictly increasing curve on every sample of every channel."""
    return curve(frame, beta)


def undo_lift(frame: np.ndarray, beta: float) -> np.ndarray:
    return inverse(frame, beta)
