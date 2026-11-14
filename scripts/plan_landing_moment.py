#!/usr/bin/env python3
"""Record the landing moment: one real hour when, seen from beyond the Moon, the Earth stands in
front of the centre of the Milky Way.

The landing (scene 01 on the WebGPU host) looks back past the Moon's far side at the Earth with the
galactic centre behind it, so the Moon, seen from the Earth's centre, must lie near the opposite
point of the sky, the galactic anti-centre (l = 180 deg, b = 0). JPL Horizons gives the geocentric
Moon and Sun for the moment and, as check values for the renderer's rotation models
(apps/web/src/spatial/bodyOrientation.ts), the sub-observer and sub-solar points of the Moon seen
from the Earth's centre and of the Earth seen from the Moon's centre.

Requests go one at a time to the Horizons API with the project's research User-Agent and nothing
personal, and are cached under .cache/horizons/ (file name: the first 24 hex digits of the SHA-256
of the query URL), so a rerun makes no request. --offline refuses to make any.

Usage:
  uv run python scripts/plan_landing_moment.py [--moment "2025-03-07 11:00"] [--cache DIR]
  uv run python scripts/plan_landing_moment.py --verify [--cache DIR]
      rebuilds the moment file from the cache alone and compares it with the committed one
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.landing_moment.horizons_client import (  # noqa: E402
    API,
    OBSERVER_QUANTITIES,
    USER_AGENT,
    fetch,
    observer,
    query_url,
    vectors,
)
from scripts.landing_moment.horizons_parser import (  # noqa: E402
    ephemeris_source,
    header_value,
    observer_checks,
    table,
)
from scripts.landing_moment.moment_planner import (  # noqa: E402
    DEFAULT_CACHE,
    DEFAULT_MOMENT,
    FORMAT,
    ICRS_TO_GALACTIC,
    OUT,
    galactic_anticentre_separation_deg,
    plan,
)

__all__ = [
    "REPO_ROOT",
    "API",
    "USER_AGENT",
    "DEFAULT_MOMENT",
    "DEFAULT_CACHE",
    "OUT",
    "FORMAT",
    "OBSERVER_QUANTITIES",
    "ICRS_TO_GALACTIC",
    "query_url",
    "fetch",
    "table",
    "vectors",
    "observer",
    "galactic_anticentre_separation_deg",
    "observer_checks",
    "header_value",
    "ephemeris_source",
    "plan",
    "main",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--moment", default=DEFAULT_MOMENT, help="UTC, 'YYYY-MM-DD HH:MM' (default %(default)s)")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--offline", action="store_true", help="use the cache only; make no request")
    parser.add_argument("--verify", action="store_true", help="rebuild from the cache and compare with the file")
    args = parser.parse_args()

    record = plan(args.moment, args.cache, offline=args.offline or args.verify)
    text = json.dumps(record, indent=2) + "\n"
    if args.verify:
        if OUT.read_text() != text:
            raise SystemExit(f"{OUT.relative_to(REPO_ROOT)} differs from the rebuild from the cache")
        print(f"{OUT.relative_to(REPO_ROOT)} matches the rebuild from the cache")
        return
    OUT.write_text(text)
    print(
        f"wrote {OUT.relative_to(REPO_ROOT)}: {record['utc']}, Moon {record['moonFromGalacticAnticentreDeg']} deg "
        f"from the galactic anti-centre, {np.linalg.norm(record['moonKm']):,.0f} km from the Earth's centre"
    )


if __name__ == "__main__":
    main()
