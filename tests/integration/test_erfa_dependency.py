"""The ERFA dependency (`pyerfa`): the installed library gives the values ERFA's own test suite publishes.

ERFA (Essential Routines for Fundamental Astronomy, github.com/liberfa/erfa) is the BSD 3-Clause release of the
IAU's SOFA routines; `pyerfa` wraps it for numpy. docs/architecture/THIRD_PARTY.md records its version, its licence
and what it is used for.

Covers:
- `erfa.moon98` (the Moon, Meeus 1998 chapter 47 in full) and `erfa.plan94` (the planets, Simon et al. 1994) return
  the vectors of ERFA's own tests `t_moon98` and `t_plan94` (src/t_erfa_c.c at tag v2.0.1, read at source
  2026-11-11), within the 1e-11 that file allows
- The installed distribution is `pyerfa` under the BSD 3-Clause licence, needs nothing but numpy, and is the version
  that uv.lock pins and docs/architecture/THIRD_PARTY.md records
"""

from __future__ import annotations

from importlib import metadata
from pathlib import Path
import re

import erfa
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
THIRD_PARTY = REPO_ROOT / "docs" / "architecture" / "THIRD_PARTY.md"
# ERFA's own tolerance for these vectors: the `1e-11` argument of each `vvd` call in t_moon98 and t_plan94, an
# absolute difference in au or au per day
ERFA_TEST_TOLERANCE = 1e-11


def test_moon98_and_plan94_return_the_vectors_of_erfas_own_tests() -> None:
    # t_moon98: eraMoon98(2400000.5, 43999.9), geocentric GCRS position (au) and velocity (au/day)
    moon = erfa.moon98(2400000.5, 43999.9)
    np.testing.assert_allclose(
        moon["p"],
        [-0.2601295959971044180e-2, 0.6139750944302742189e-3, 0.2640794528229828909e-3],
        rtol=0,
        atol=ERFA_TEST_TOLERANCE,
    )
    np.testing.assert_allclose(
        moon["v"],
        [-0.1244321506649895021e-3, -0.5219076942678119398e-3, -0.1716132214378462047e-3],
        rtol=0,
        atol=ERFA_TEST_TOLERANCE,
    )
    # t_plan94, case 4: eraPlan94(2400000.5, 43999.9, 1), Mercury, heliocentric J2000 position (au) and velocity
    # (au/day)
    mercury = erfa.plan94(2400000.5, 43999.9, 1)
    np.testing.assert_allclose(
        mercury["p"],
        [0.2945293959257430832, -0.2452204176601049596, -0.1615427700571978153],
        rtol=0,
        atol=ERFA_TEST_TOLERANCE,
    )
    np.testing.assert_allclose(
        mercury["v"],
        [0.1413867871404614441e-1, 0.1946548301104706582e-1, 0.8929809783898904786e-2],
        rtol=0,
        atol=ERFA_TEST_TOLERANCE,
    )


def test_the_installed_distribution_is_pyerfa_under_bsd_3_clause_and_needs_only_numpy() -> None:
    dist = metadata.distribution("pyerfa")
    licence = " ".join(
        [dist.metadata.get("License-Expression") or "", dist.metadata.get("License") or ""]
        + [c for c in dist.metadata.get_all("Classifier") or [] if c.startswith("License ::")]
    )
    assert "BSD" in licence and ("3-Clause" in licence or "BSD License" in licence), licence
    required = sorted(
        re.split(r"[<>=!~;\s\[]", requirement, maxsplit=1)[0].lower()
        for requirement in dist.requires or []
        if "extra ==" not in requirement
    )
    assert required == ["numpy"], required


def test_the_installed_version_is_the_one_the_lock_pins_and_the_record_names() -> None:
    installed = metadata.version("pyerfa")
    lock = (REPO_ROOT / "uv.lock").read_text()
    pinned = re.search(r'\[\[package\]\]\nname = "pyerfa"\nversion = "([^"]+)"', lock)
    assert pinned is not None and pinned.group(1) == installed
    record = THIRD_PARTY.read_text()
    assert f"`pyerfa` {installed}" in record
    assert f"ERFA {erfa.version.erfa_version}" in record
