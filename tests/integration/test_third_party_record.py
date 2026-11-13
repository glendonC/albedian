"""docs/architecture/THIRD_PARTY.md's rows for `pyerfa` say what the installed package says of itself.

Covers:
- The Version row names the installed pyerfa, ERFA and SOFA versions (`erfa.version`; SOFA's release date as
  YYYY-MM-DD)
- The Licence row's pyerfa cell is the installed distribution's own licence (its `License` metadata), and the ERFA
  cell names the same BSD 3-Clause licence
- The Copyright row's ERFA cell names the years and holder the installed ERFA routines' own notes print
- The Needs row says numpy only, as the distribution's requirements do
"""

from __future__ import annotations

from importlib import metadata
from pathlib import Path
import re

import erfa

RECORD = Path(__file__).resolve().parents[2] / "docs" / "architecture" / "THIRD_PARTY.md"


def _row(name: str) -> list[str]:
    """The cells of the record's table row named `name`, after the name."""
    for line in RECORD.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and cells[0] == name:
            return cells[1:]
    raise AssertionError(f"THIRD_PARTY.md has no {name} row")


def test_the_version_row_is_the_installed_package() -> None:
    pyerfa_cell, erfa_cell = _row("Version")
    sofa = erfa.version.sofa_version
    assert pyerfa_cell == metadata.version("pyerfa")
    assert erfa_cell.startswith(f"ERFA {erfa.version.erfa_version},")
    assert f"SOFA release {sofa[:4]}-{sofa[4:6]}-{sofa[6:]}" in erfa_cell


def test_the_licence_and_copyright_rows_are_the_installed_packages_own() -> None:
    pyerfa_cell, erfa_cell = _row("Licence")
    installed = metadata.distribution("pyerfa").metadata["License"]
    assert installed.startswith(pyerfa_cell), (installed, pyerfa_cell)
    assert erfa_cell.startswith(pyerfa_cell)
    _, erfa_copyright = _row("Copyright")
    printed = re.search(r"Copyright \(C\) (\d{4}-\d{4}), (NumFOCUS Foundation)\.", erfa.moon98.__doc__)
    assert printed is not None and f"{printed.group(1)} in its source file headers" in erfa_copyright
    assert printed.group(2) in erfa_copyright


def test_the_needs_row_is_the_distributions_requirements() -> None:
    pyerfa_cell, _ = _row("Needs")
    required = sorted(
        re.split(r"[<>=!~;\s\[]", requirement, maxsplit=1)[0].lower()
        for requirement in metadata.distribution("pyerfa").requires or []
        if "extra ==" not in requirement
    )
    assert required == ["numpy"] and pyerfa_cell.startswith("numpy only")


def test_every_stored_horizons_fixture_is_named_with_jpls_citation_and_its_retrieval_dates() -> None:
    """Each JPL Horizons fixture under tests/fixtures is named in the record, which gives JPL's citation in the
    Horizons manual's form and each date the fixture's answers were retrieved, as JPL writes it (YYYY-Mon-DD) and as
    the about page's form (Year Month Day)."""
    import gzip
    import json
    from datetime import date

    text = RECORD.read_text(encoding="utf-8")
    fixtures = sorted((RECORD.parents[2] / "tests" / "fixtures").glob("horizons*.json.gz"))
    assert fixtures
    assert "Giorgini, JD and JPL Solar System Dynamics Group, NASA/JPL Horizons On-Line Ephemeris System," in text
    assert "< https://ssd.jpl.nasa.gov/horizons/ >" in text and "JPL states no licence for Horizons output" in text
    for fixture in fixtures:
        assert f"`tests/fixtures/{fixture.name}`" in text, fixture.name
        requests = json.loads(gzip.decompress(fixture.read_bytes()).decode("utf-8"))["requests"]
        for day in sorted({date.fromisoformat(r["retrievedUtc"][:10]) for r in requests}):
            assert f"data retrieved {day.strftime('%Y-%b-%d')}" in text, (fixture.name, day)
            assert f"Data retrieved on {day.year} {day.strftime('%B')} {day.day}." in text, (fixture.name, day)
            assert day.isoformat() in text, (fixture.name, day)
