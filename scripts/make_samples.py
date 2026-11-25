#!/usr/bin/env python3
"""Make each sample night ready on this machine: the setup step of first-use-sample-one, whose code is
services/samples/make.py (the laptop's server runs the same step at its own start in a field run).

Usage, from the repository root:
    uv run python scripts/make_samples.py
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.samples.make import (  # noqa: E402,F401  (the names the setup's tests read)
    SampleNotReady,
    current_producer,
    fetch_photo,
    make_all,
    make_ready,
    producer_path,
    solve_photo,
)


def main() -> int:
    for sample, said in make_all(ROOT).items():
        print(f"{sample}: {said}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
