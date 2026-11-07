#!/usr/bin/env python3
"""Bake the NASA Exoplanet Archive Planetary Systems Composite Parameters table (data-6b).

Input, in `.cache/m2/` (registered in `data/downloads.json`) and pinned by size and SHA-256:
- `pscomppars.csv`: frozen TAP query snapshot (`select * from pscomppars`, retrieved
  `2026-11-05T00:36:15Z`), 6,451 rows, 82,923,300 bytes, SHA-256
  `17862c7b7df4a9de1813367071b312523f7f42ed1a2284ce879cd8ee2a707386`.

Outputs in `apps/web/public/catalog/exoplanets/`, validated by `services/contracts/exoplanets.py`
and `services/contracts/provenance.py`:
- `pscomppars.json`: `ExoplanetTable` (`evidenceKind: "catalog-derived"`, 6,451 rows of 21 columns
  and 1,838 cited references);
- `PROVENANCE.json`: `AssetProvenanceRecord` pinning `pscomppars.json` and the frozen upstream
  snapshot, TAP query, acknowledgement, citations, and composite-table caveats.

Usage, from the repository root:
    uv run python scripts/bake_exoplanets.py
    uv run python scripts/bake_exoplanets.py --verify
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import hashlib
import html
import io
import json
import math
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlsplit

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.contracts.exoplanets import (  # noqa: E402
    EXOPLANET_CALCULATED_REFSTR,
    EXOPLANET_COLUMNS,
    EXOPLANET_EVIDENCE_KIND,
    EXOPLANET_MASS_PROVENANCES,
    EXOPLANET_MODELLED_COLUMNS,
    EXOPLANET_TABLE_FORMAT,
    EXOPLANET_TABLE_FORMAT_VERSION,
    ExoplanetTable,
)
from services.contracts.provenance import AssetProvenanceRecord, dumps_record  # noqa: E402

DEFAULT_CACHE = ROOT_DIR / ".cache" / "m2"
DEFAULT_OUT = ROOT_DIR / "apps" / "web" / "public" / "catalog" / "exoplanets"
OUT_NAME = "pscomppars.json"

ARCHIVE_HOST = "https://exoplanetarchive.ipac.caltech.edu"
ALLOWED_RELATIVE_HREFS: tuple[str, ...] = ("/docs/pscp_calc.html",)
LEGACY_HTTP_HOSTS: tuple[str, ...] = (
    "www.cfa.harvard.edu",
    "vizier.u-strasbg.fr",
    "www.nature.com",
)

ACKNOWLEDGEMENT = (
    "This research has made use of the NASA Exoplanet Archive, which is operated by the California Institute of "
    "Technology, under contract with the National Aeronautics and Space Administration under the Exoplanet "
    "Exploration Program."
)

EQT_NOTE = (
    "Equilibrium temperatures in PSCompPars come either from the cited discovery or characterization paper "
    "(basis 'measured', evaluated under that paper's own Bond albedo and heat-redistribution assumptions) or "
    "from the archive's blackbody calculation T_eq = T_eff * sqrt(R_* / (2 a)) assuming zero Bond albedo "
    "(A_B = 0) and full heat redistribution across the planetary surface (basis 'calculated', citing "
    "CALCULATED_VALUE at https://exoplanetarchive.ipac.caltech.edu/docs/pscp_calc.html; 863 of the 1,090 "
    "Calculated Value T_eq rows in pscomppars.csv carry non-null uncertainties in err1 and err2 despite "
    "pscp_calc.html stating that calculated values do not report uncertainties)."
)


@dataclass(frozen=True)
class ExoplanetInput:
    filename: str
    url: str
    retrieved: str
    bytes: int
    sha256: str


INPUT = ExoplanetInput(
    filename="pscomppars.csv",
    url="https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+*+from+pscomppars&format=csv",
    retrieved="2026-11-05T00:36:15Z",
    bytes=82_923_300,
    sha256="17862c7b7df4a9de1813367071b312523f7f42ed1a2284ce879cd8ee2a707386",
)


@dataclass(frozen=True)
class ValueColumnSpec:
    contract_name: str
    val_col: str
    err1_col: str
    err2_col: str
    lim_col: str | None
    ref_col: str


VALUE_COLUMN_SPECS: tuple[ValueColumnSpec, ...] = (
    ValueColumnSpec("orbitalPeriodDays", "pl_orbper", "pl_orbpererr1", "pl_orbpererr2", "pl_orbperlim", "pl_orbper_reflink"),
    ValueColumnSpec("semiMajorAxisAu", "pl_orbsmax", "pl_orbsmaxerr1", "pl_orbsmaxerr2", "pl_orbsmaxlim", "pl_orbsmax_reflink"),
    ValueColumnSpec("radiusEarth", "pl_rade", "pl_radeerr1", "pl_radeerr2", "pl_radelim", "pl_rade_reflink"),
    ValueColumnSpec("massEarth", "pl_bmasse", "pl_bmasseerr1", "pl_bmasseerr2", "pl_bmasselim", "pl_bmasse_reflink"),
    ValueColumnSpec("eccentricity", "pl_orbeccen", "pl_orbeccenerr1", "pl_orbeccenerr2", "pl_orbeccenlim", "pl_orbeccen_reflink"),
    ValueColumnSpec("equilibriumTemperatureK", "pl_eqt", "pl_eqterr1", "pl_eqterr2", "pl_eqtlim", "pl_eqt_reflink"),
    ValueColumnSpec("hostEffectiveTemperatureK", "st_teff", "st_tefferr1", "st_tefferr2", "st_tefflim", "st_teff_reflink"),
    ValueColumnSpec("hostRadiusSolar", "st_rad", "st_raderr1", "st_raderr2", "st_radlim", "st_rad_reflink"),
    ValueColumnSpec("hostMassSolar", "st_mass", "st_masserr1", "st_masserr2", "st_masslim", "st_mass_reflink"),
    ValueColumnSpec("distancePc", "sy_dist", "sy_disterr1", "sy_disterr2", "sy_distlim", "sy_dist_reflink"),
    ValueColumnSpec("raDeg", "ra", "raerr1", "raerr2", None, "ra_reflink"),
    ValueColumnSpec("decDeg", "dec", "decerr1", "decerr2", None, "dec_reflink"),
    ValueColumnSpec("vMagnitude", "sy_vmag", "sy_vmagerr1", "sy_vmagerr2", "sy_vmaglim", "sy_vmag_reflink"),
)

IDENTITY_CSV_COLUMNS: tuple[str, ...] = (
    "pl_name",
    "hostname",
    "sy_snum",
    "sy_pnum",
    "discoverymethod",
    "disc_year",
    "disc_facility",
    "pl_bmassprov",
)

REQUIRED_CSV_COLUMNS: tuple[str, ...] = IDENTITY_CSV_COLUMNS + tuple(
    col
    for spec in VALUE_COLUMN_SPECS
    for col in (spec.val_col, spec.err1_col, spec.err2_col, spec.lim_col, spec.ref_col)
    if col is not None
)

_ANCHOR_RE = re.compile(
    r'\A<a\s+refstr=(?:"([^"]+)"|([^\s>]+))\s+href=([^\s>]+)\s+target=[^>]+>(.*?)</a>\Z',
    re.DOTALL,
)


def parse_reference_anchor(raw: str) -> tuple[str, str, str]:
    """Parse an HTML `<a refstr=... href=... target=...>label</a>` cell into `(refstr, label, https_url)`."""
    match = _ANCHOR_RE.fullmatch(raw.strip())
    if match is None:
        raise ValueError(f"invalid reference anchor: {raw!r}")
    refstr = (match.group(1) or match.group(2)).strip()
    href = match.group(3).strip().strip('"').strip("'")
    label = " ".join(html.unescape(match.group(4)).split())
    if not label:
        raise ValueError(f"empty reference label in anchor: {raw!r}")

    if href.startswith("/"):
        if href not in ALLOWED_RELATIVE_HREFS:
            raise ValueError(f"unknown relative reference href: {href!r}")
        url = ARCHIVE_HOST + href
    elif href.startswith("http://"):
        parts = urlsplit(href)
        if parts.netloc not in LEGACY_HTTP_HOSTS:
            raise ValueError(f"unapproved plain http reference host {parts.netloc!r} in {href!r}")
        url = "https://" + href[len("http://") :]
    elif href.startswith("https://"):
        url = href
    else:
        raise ValueError(f"reference URL must use https:// (or an approved relative/legacy link), got {href!r}")
    return refstr, label, url


def parse_limit_flag(raw: str) -> int | None:
    """Parse a PSCompPars limit column (`""`, `"0"`, `"1"`, `"-1"`, or scientific notation like `"0.00000000000000e+00"`)."""
    token = raw.strip()
    if not token:
        return None
    try:
        numeric = float(token)
    except ValueError as exc:
        raise ValueError(f"invalid limit flag {raw!r}") from exc
    if not math.isfinite(numeric) or not numeric.is_integer():
        raise ValueError(f"limit flag must be an integer in {{-1, 0, 1}}, got {raw!r}")
    flag = int(numeric)
    if flag not in (-1, 0, 1):
        raise ValueError(f"limit flag must be in {{-1, 0, 1}}, got {raw!r}")
    return flag


def _parse_optional_float(raw: str) -> float | None:
    token = raw.strip()
    if not token:
        return None
    val = float(token)
    if not math.isfinite(val):
        raise ValueError(f"non-finite float {raw!r}")
    return val


def _basis_for(contract_col: str, refstr: str) -> str:
    if refstr == EXOPLANET_CALCULATED_REFSTR:
        return "modelled" if contract_col in EXOPLANET_MODELLED_COLUMNS else "calculated"
    return "measured"


def parse_exoplanet_csv(text: str) -> ExoplanetTable:
    """Parse the full text of `pscomppars.csv` and validate against `ExoplanetTable`."""
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ValueError("CSV has no header row")
    missing = [col for col in REQUIRED_CSV_COLUMNS if col not in reader.fieldnames]
    if missing:
        raise ValueError(f"pscomppars CSV is missing required columns: {missing}")

    references: list[dict[str, str]] = []
    ref_index_by_pair: dict[tuple[str, str], int] = {}
    rows: list[list[Any]] = []

    for csv_row in reader:
        name = csv_row["pl_name"].strip()
        host_name = csv_row["hostname"].strip()
        star_count = int(csv_row["sy_snum"].strip())
        planet_count = int(csv_row["sy_pnum"].strip())
        disc_method = csv_row["discoverymethod"].strip()
        disc_year = int(csv_row["disc_year"].strip())
        disc_facility = csv_row["disc_facility"].strip()
        mass_prov_raw = csv_row["pl_bmassprov"].strip()
        mass_prov: str | None = mass_prov_raw if mass_prov_raw else None
        if mass_prov is not None and mass_prov not in EXOPLANET_MASS_PROVENANCES:
            raise ValueError(f"unknown mass provenance {mass_prov!r} on {name!r}")

        row_out: list[Any] = [
            name,
            host_name,
            star_count,
            planet_count,
            disc_method,
            disc_year,
            disc_facility,
            mass_prov,
        ]

        for spec in VALUE_COLUMN_SPECS:
            val = _parse_optional_float(csv_row[spec.val_col])
            if val is None:
                row_out.append(None)
                continue
            err1 = _parse_optional_float(csv_row[spec.err1_col])
            err2 = _parse_optional_float(csv_row[spec.err2_col])
            limit = None if spec.lim_col is None else parse_limit_flag(csv_row[spec.lim_col])
            refstr, label, url = parse_reference_anchor(csv_row[spec.ref_col])
            pair = (refstr, url)
            ref_idx = ref_index_by_pair.get(pair)
            if ref_idx is None:
                ref_idx = len(references)
                ref_index_by_pair[pair] = ref_idx
                references.append({"refstr": refstr, "label": label, "url": url})
            elif references[ref_idx]["label"] != label:
                raise ValueError(
                    f"reference {pair!r} has inconsistent labels: {references[ref_idx]['label']!r} vs {label!r}"
                )
            basis = _basis_for(spec.contract_name, refstr)
            row_out.append([val, err1, err2, limit, ref_idx, basis])

        rows.append(row_out)

    return ExoplanetTable.model_validate(
        {
            "format": EXOPLANET_TABLE_FORMAT,
            "formatVersion": EXOPLANET_TABLE_FORMAT_VERSION,
            "evidenceKind": EXOPLANET_EVIDENCE_KIND,
            "columns": list(EXOPLANET_COLUMNS),
            "notes": {"equilibriumTemperatureK": EQT_NOTE},
            "references": references,
            "rows": rows,
        }
    )


def build_exoplanet_table(path: Path) -> ExoplanetTable:
    return parse_exoplanet_csv(path.read_text(encoding="utf-8"))


def dumps_table(table: ExoplanetTable) -> str:
    """Single-line compact JSON with a final newline."""
    return json.dumps(table.to_contract_dict(), ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n"


def build_provenance_record(out_dir: Path) -> AssetProvenanceRecord:
    out_sha = hashlib.sha256((out_dir / OUT_NAME).read_bytes()).hexdigest()
    return AssetProvenanceRecord.model_validate(
        {
            "format": "albedian.asset-provenance",
            "formatVersion": 1,
            "sources": [
                {
                    "id": "nasa-exoplanet-archive-pscomppars",
                    "title": "NASA Exoplanet Archive Planetary Systems Composite Parameters (PSCompPars) Table",
                    "url": INPUT.url,
                    "version": f"Frozen TAP snapshot (select * from pscomppars, retrieved {INPUT.retrieved}, 6,451 rows)",
                    "retrieved": "2026-11-05",
                    "license": {
                        "spdx": "LicenseRef-NASA-Exoplanet-Archive-Acknowledgement",
                        "name": "NASA Exoplanet Archive Acknowledgement and Citation Policy",
                        "url": "https://exoplanetarchive.ipac.caltech.edu/docs/acknowledge.html",
                        "conditions": (
                            "Publications and applications using the NASA Exoplanet Archive must include the "
                            "standard archive acknowledgement verbatim, cite the archive overview paper "
                            "(Christiansen et al. 2025, PSJ, doi:10.3847/PSJ/ade3c2) and table DOI "
                            "(doi:10.26133/NEA13 for PSCompPars; companion Planetary Systems table is "
                            "doi:10.26133/NEA12), and cite the original literature references for individual "
                            "parameter values."
                        ),
                    },
                    "credit": "NASA Exoplanet Archive / Caltech / IPAC",
                    "citation": (
                        "Christiansen, J. L., et al. 2025, The NASA Exoplanet Archive and Exoplanet Follow-up "
                        "Observing Program: Data, Tools, and Usage, PSJ, doi:10.3847/PSJ/ade3c2; NASA Exoplanet "
                        "Archive Planetary Systems Composite Parameters (PSCompPars) table, doi:10.26133/NEA13 "
                        "(companion to Planetary Systems table, doi:10.26133/NEA12)."
                    ),
                    "doi": "10.26133/NEA13",
                    "acknowledgement": ACKNOWLEDGEMENT,
                    "sha256": INPUT.sha256,
                    "note": (
                        f"Frozen TAP query snapshot retrieved at {INPUT.retrieved} via `{INPUT.url}` "
                        f"({INPUT.bytes:,} bytes, SHA-256 `{INPUT.sha256}`). Upstream TAP queries against "
                        "`pscomppars` change over time as new planets and parameter solutions are ingested. "
                        "PSCompPars is a composite table whose columns are drawn from multiple published "
                        "references and archive calculations and are therefore not self-consistent across "
                        "a row. Values citing `CALCULATED_VALUE` resolve `/docs/pscp_calc.html` to "
                        "`https://exoplanetarchive.ipac.caltech.edu/docs/pscp_calc.html`: planetary radius "
                        "(`radiusEarth`) and mass (`massEarth`) use the Chen and Kipping (2017, ApJ 834, 17) "
                        "probabilistic mass-radius relation (recorded with basis `modelled`), while orbital "
                        "semi-major axis, equilibrium temperature (assuming zero Bond albedo and full heat "
                        "redistribution), and other calculated quantities use standard astrophysical relations "
                        "(recorded with basis `calculated`). Note that 863 of the 1,090 `Calculated Value` "
                        "`pl_eqt` (`Teq`) rows in `pscomppars.csv` carry non-null uncertainties (`err1`, `err2`) "
                        "despite `pscp_calc.html` stating that calculated values do not report uncertainties. "
                        "Numeric limit flags `-1`, `0`, `1` are preserved from `*_lim` on populated value cells "
                        "(raw `*_lim` columns also contain `'9'` when the value cell is blank); the exact "
                        "mapping of `-1` and `1` to lower vs. upper limits is not defined in "
                        "`API_PS_columns.html` [open]. Legacy plain `http://` reference URLs on "
                        "`www.cfa.harvard.edu`, `vizier.u-strasbg.fr`, and `www.nature.com` are normalized "
                        "to `https://`."
                    ),
                }
            ],
            "files": [
                {
                    "path": OUT_NAME,
                    "sha256": out_sha,
                    "sources": ["nasa-exoplanet-archive-pscomppars"],
                    "producedBy": {
                        "method": "script",
                        "script": "scripts/bake_exoplanets.py",
                        "command": "uv run python scripts/bake_exoplanets.py",
                    },
                    "note": (
                        "All 6,451 rows and 21 columns (8 identity columns and 13 value columns) of the "
                        "2026-11-05T00:36:15Z PSCompPars snapshot, with 1,838 deduplicated literature and "
                        "calculation references and `evidenceKind: 'catalog-derived'` (distinguishing this "
                        "static catalog table from `catalog_matched` observation findings). Each value cell "
                        "carries `[value, err1, err2, limit, ref, basis]` with `basis` in "
                        "`{'measured', 'calculated', 'modelled'}`."
                    ),
                }
            ],
            "fileSets": [],
        }
    )


def check_inputs(cache: Path) -> None:
    path = cache / INPUT.filename
    if not path.is_file():
        raise SystemExit(f"[exoplanets] missing {path}; run: uv run python scripts/fetch_download.py .cache/m2/{INPUT.filename}")
    raw = path.read_bytes()
    if len(raw) != INPUT.bytes:
        raise SystemExit(f"[exoplanets] {path.name} has {len(raw)} bytes, expected {INPUT.bytes}")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != INPUT.sha256:
        raise SystemExit(f"[exoplanets] {path.name} has sha256 {digest}, expected {INPUT.sha256}")


def verify_outputs(cache: Path, out_dir: Path) -> list[str]:
    check_inputs(cache)
    errors: list[str] = []
    expected_text = dumps_table(build_exoplanet_table(cache / INPUT.filename))
    target = out_dir / OUT_NAME
    if not target.is_file() or target.read_text(encoding="utf-8") != expected_text:
        errors.append(f"{target} differs from a fresh bake")
    if not errors:
        record_text = dumps_record(build_provenance_record(out_dir))
        record_path = out_dir / "PROVENANCE.json"
        if not record_path.is_file() or record_path.read_text(encoding="utf-8") != record_text:
            errors.append(f"{record_path} differs from a fresh bake")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--verify", action="store_true", help="check committed outputs byte-for-byte against a fresh bake")
    args = parser.parse_args(argv)

    if args.verify:
        errors = verify_outputs(args.cache, args.out)
        if errors:
            for err in errors:
                print(f"[exoplanets] {err}", file=sys.stderr)
            return 1
        print("[exoplanets] pscomppars.json and PROVENANCE.json match a fresh bake")
        return 0

    check_inputs(args.cache)
    args.out.mkdir(parents=True, exist_ok=True)
    table = build_exoplanet_table(args.cache / INPUT.filename)
    (args.out / OUT_NAME).write_text(dumps_table(table), encoding="utf-8")
    record = build_provenance_record(args.out)
    (args.out / "PROVENANCE.json").write_text(dumps_record(record), encoding="utf-8")
    for name in (OUT_NAME, "PROVENANCE.json"):
        size = (args.out / name).stat().st_size
        print(f"[exoplanets] {name}: {size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
