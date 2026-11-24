"""Black bars round a picture, the one rule stills and clips share (photo-bars-one) [decided 2026-11-24 by the lead:
DECIDED-BY-LEAD at 13:25 on RESTORE-PHOTO's black-bar probe, and at 13:36 on RESTORE-CLIP-bars-rule-QUESTION].

A bar is a run of whole edge rows (top and bottom) or whole edge columns (left and right), each one code at or below
black in every measure frame, on both opposite edges, where the row or column just past each run opens the picture:
the median of its samples over the measure frames is above black. A black band on one edge only is no bar, and nor is
a black sky that reaches the edge: the public sample clip's sky is black in its first 168 and last 120 columns, and
cutting them left a darkest quarter on black that met C36 [tested 2026-11-24, RESTORE-CLIP letterbox-1]. The cost the
lead accepted: a pad against a picture whose own edge is black sky is not found, so that picture is measured whole.
The measure, the restoration and the gate read the picture inside the bars, and the stored result takes its bars back
from the original. Moved here from RESTORE-CLIP's `clip_frames.find_bars` and `Bars` (letterbox-1, `8a168a8d`) so the
rule is written once. Pure functions on arrays.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class Bars:
    """How many rows or columns of bars lie at each edge, in samples of the full-resolution plane; none for most
    pictures. `down` and `across` divide them for a plane stored at a coarser grid (a clip's chroma)."""

    top: int = 0
    bottom: int = 0
    left: int = 0
    right: int = 0

    def __bool__(self) -> bool:
        return any((self.top, self.bottom, self.left, self.right))

    def slices(self, shape: Sequence[int], down: int = 1, across: int = 1) -> tuple[slice, slice]:
        """The rows and columns of the picture inside the bars, in an array of `shape`."""
        rows, columns = shape[0], shape[1]
        return (slice(self.top // down, rows - self.bottom // down),
                slice(self.left // across, columns - self.right // across))

    def crop(self, array: np.ndarray, down: int = 1, across: int = 1) -> np.ndarray:
        """The picture inside the bars, as its own contiguous array."""
        array = np.asarray(array)
        return np.ascontiguousarray(array[self.slices(array.shape, down, across)])

    def put_back(self, whole: np.ndarray, picture: np.ndarray, down: int = 1, across: int = 1) -> np.ndarray:
        """A copy of `whole` with `picture` in place of what lies inside the bars."""
        out = np.array(whole, copy=True)
        out[self.slices(out.shape, down, across)] = picture
        return out


def _edge_run(flat: np.ndarray) -> int:
    """How many leading entries of `flat` are true."""
    return int(flat.size if flat.all() else np.argmin(flat))


def find_bars(planes: Sequence[np.ndarray], black: float, *, down: int = 1, across: int = 1) -> Bars:
    """The bars (`Bars`) of a picture from each measure frame's plane of codes (H x W, the same shape for every frame),
    or none. `black` is the code of nominal black; `down` and `across` count bars toward the edge to the grid of a
    plane stored that much coarser, so every plane's bars are whole samples of its own."""
    if not planes:
        return Bars()
    codes = [np.asarray(plane) for plane in planes]
    if any(plane.ndim != 2 or plane.shape != codes[0].shape for plane in codes):
        raise ValueError("every measure frame's plane must be one H x W array of the same shape")
    rows = np.logical_and.reduce([(c.max(axis=1) == c.min(axis=1)) & (c.max(axis=1) <= black) for c in codes])
    columns = np.logical_and.reduce([(c.max(axis=0) == c.min(axis=0)) & (c.max(axis=0) <= black) for c in codes])
    height, width = codes[0].shape

    def opens(index: int, axis: int) -> bool:
        line = np.concatenate([c[index] if axis == 0 else c[:, index] for c in codes])
        return bool(np.median(line) > black)

    def bars_on(flat: np.ndarray, axis: int, size: int, factor: int) -> tuple[int, int]:
        first, last = _edge_run(flat), _edge_run(flat[::-1])
        if not (0 < first and 0 < last and first + last < size and opens(first, axis) and opens(size - 1 - last, axis)):
            return 0, 0
        first, last = first - first % factor, last - last % factor
        return (first, last) if first and last else (0, 0)

    top, bottom = bars_on(rows, 0, height, down)
    left, right = bars_on(columns, 1, width, across)
    return Bars(top, bottom, left, right)
