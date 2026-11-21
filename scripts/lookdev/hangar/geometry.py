"""Plain arithmetic the hangar's build uses, free of bpy so the tests can check it."""

from __future__ import annotations

from itertools import combinations
from math import dist


def mean_plan_distance(centres: list[tuple[float, float, float]], up: int = 2) -> float:
    """The mean distance between every pair of points, measured in the plane across axis `up` (a lander's footpads
    seen from above: three pads on an equilateral triangle are its side apart)."""
    if len(centres) < 2:
        raise ValueError("needs two or more points")
    plan = [tuple(c[i] for i in range(3) if i != up) for c in centres]
    pairs = list(combinations(plan, 2))
    return sum(dist(a, b) for a, b in pairs) / len(pairs)
