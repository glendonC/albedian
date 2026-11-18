"""Freeze the detections the detector made on the phone photo benchmark's ci frames at one commit.

The triangle search's characterization tests (tests/science/test_triangle_dense_fields.py) pin the status each frame
reaches. They check the search, not the detector, so they read these frozen detections and keep the input they were
chosen on whatever the detector does later.

Each frame is built as that file's `_ci_frame` builds it, as the benchmark's today-api adapter runs it: the ci suite's
frame rendered over the shipped star tiles, its luma (`solvers._luma`) and the detector's default call. The named
commit's own code does all of it: the folder is a checkout of that commit, put first on the import path. Run:

    git archive ff8f2630 | tar -x -C <an empty folder>
    uv run python scripts/freeze_ci_detections.py <that folder> ff8f2630

It writes tests/fixtures/ci_frame_detections.json: the commit, this command, the detection fields in order and, per
frame, its size and its detections, one row per detection in the fields' order. Pixel positions and measures only:
the frames' synthetic moments are not copied (the tests take them from the suite).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "ci_frame_detections.json"
COMMAND = "git archive {commit} | tar -x -C <an empty folder>; uv run python scripts/freeze_ci_detections.py <that folder> {commit}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tree", type=Path, help="a checkout of the commit whose detector is frozen")
    parser.add_argument("commit", help="that commit, as recorded in the fixture")
    args = parser.parse_args()
    sys.path.insert(0, str(args.tree.resolve()))
    from research.benchmarks.phone_photos import render, solvers, suite
    from research.benchmarks.phone_photos.stars import TileCatalog
    from services.science import detector
    from services.science.solver import SourceDetection, detect_point_sources

    for module in (render, solvers, suite, detector):
        if not Path(module.__file__).resolve().is_relative_to(args.tree.resolve()):
            raise SystemExit(f"{module.__name__} came from {module.__file__}, not from {args.tree}: nothing written")
    fields = [f.name for f in dataclasses.fields(SourceDetection)]
    lines = [
        "{",
        f'  "about": {json.dumps(__doc__.splitlines()[0])},',
        f'  "commit": {json.dumps(args.commit)},',
        f'  "command": {json.dumps(COMMAND.format(commit=args.commit))},',
        f'  "fields": {json.dumps(fields)},',
        '  "frames": {',
    ]
    specs = suite.build_suite("ci")
    for index, spec in enumerate(specs):
        frame = render.render_frame(spec, TileCatalog())
        luma = solvers._luma(frame.rgb)
        rows = [json.dumps([getattr(d, name) for name in fields]) for d in detect_point_sources(luma)]
        head = f'    "{spec.frame_id}": {{"width": {luma.shape[1]}, "height": {luma.shape[0]}, "detections": ['
        tail = "]}" + ("," if index < len(specs) - 1 else "")
        lines += [head] + [f"      {row}," for row in rows[:-1]] + ([f"      {rows[-1]}"] if rows else []) + [f"    {tail}"]
        print(f"{spec.frame_id}: {len(rows)} detections")
    lines += ["  }", "}"]
    OUT.write_text("\n".join(lines) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
