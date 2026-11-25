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


def max_plan_distance(points: list[tuple[float, float, float]], up: int = 2) -> float:
    """The largest distance between two points, measured in the plane across axis `up` (a rotor's blades seen along
    its mast: tip to tip, whatever angle the blades stand at). Only the points of the plan's convex hull can be the
    farthest pair, so the hull is found first (Andrew's monotone chain) and its points compared pairwise."""
    plan = sorted({tuple(p[i] for i in range(3) if i != up) for p in points})
    if len(plan) < 2:
        raise ValueError("needs two or more distinct points across the axis")

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in plan:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(plan):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]
    return max(dist(a, b) for a, b in combinations(hull, 2))


def by_part_at(recipe: dict, level: str) -> bool:
    """Whether a recipe decimates `level` part by part: `decimate_by_part` is True for every level, or names the levels
    (the station's lod0 keeps one ratio over its whole mesh; its lod1 and lod2 go part by part after their small parts
    are left out, since part by part over all of its parts at lod0 ran one core for over 46 minutes)."""
    by_part = recipe.get("decimate_by_part")
    return by_part is True or (isinstance(by_part, (list, tuple)) and level in by_part)
