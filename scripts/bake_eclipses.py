#!/usr/bin/env python3
"""Bake the Five Millennium Catalogs of Solar and Lunar Eclipses (data-6a).

Inputs, in `.cache/m2/` (registered in `data/downloads.json`) and pinned by size and SHA-256:
- `5MKSEcatalog.txt`: Fred Espenak and Jean Meeus, Five Millennium Catalog of Solar Eclipses:
  -1999 to +3000 (NASA GSFC; header states NASA/TP-2008-214170, landing page states
  NASA/TP-2009-214174), 11,898 rows, Latin-1;
- `5MKLEcatalog.txt`: Fred Espenak and Jean Meeus, Five Millennium Catalog of Lunar Eclipses:
  -1999 to +3000 (NASA GSFC, NASA/TP-2009-214173), 12,064 rows, ASCII/Latin-1.

Outputs in `apps/web/public/catalog/eclipses/`, validated by `services/contracts/eclipses.py`
and `services/contracts/provenance.py`:
- `solar.json`: `SolarEclipseCatalog` (`evidenceClass: "predicted"`, 11,898 rows of 21 columns);
- `lunar.json`: `LunarEclipseCatalog` (`evidenceClass: "predicted"`, 12,064 rows of 20 columns);
- `PROVENANCE.json`: `AssetProvenanceRecord` pinning both files and their upstream sources.

Usage, from the repository root:
    uv run python scripts/bake_eclipses.py
    uv run python scripts/bake_eclipses.py --verify
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.contracts.eclipses import (  # noqa: E402
    ECLIPSE_CATALOG_FORMAT,
    ECLIPSE_CATALOG_FORMAT_VERSION,
    ECLIPSE_EVIDENCE_CLASS,
    LUNAR_ECLIPSE_COLUMNS,
    LUNAR_ECLIPSE_QUALIFIERS,
    LUNAR_ECLIPSE_TYPES,
    SOLAR_ECLIPSE_COLUMNS,
    SOLAR_ECLIPSE_QUALIFIERS,
    SOLAR_ECLIPSE_TYPES,
    LunarEclipseCatalog,
    SolarEclipseCatalog,
    eclipse_calendar_of,
)
from services.contracts.provenance import AssetProvenanceRecord, dumps_record  # noqa: E402

DEFAULT_CACHE = ROOT_DIR / ".cache" / "m2"
DEFAULT_OUT = ROOT_DIR / "apps" / "web" / "public" / "catalog" / "eclipses"
SOLAR_OUT_NAME = "solar.json"
LUNAR_OUT_NAME = "lunar.json"
CREDIT = "Eclipse Predictions by Fred Espenak and Jean Meeus (NASA's GSFC)"


@dataclass(frozen=True)
class EclipseInput:
    filename: str
    url: str
    bytes: int
    sha256: str


INPUTS: dict[str, EclipseInput] = {
    "solar": EclipseInput(
        filename="5MKSEcatalog.txt",
        url="https://eclipse.gsfc.nasa.gov/5MCSE/5MKSEcatalog.txt",
        bytes=1_380_993,
        sha256="c07e7b5e13af61a1d74eb3bee1a1824a197dc1ca393a3ac2a91c9e73bc2e0c58",
    ),
    "lunar": EclipseInput(
        filename="5MKLEcatalog.txt",
        url="https://eclipse.gsfc.nasa.gov/5MCLE/5MKLEcatalog.txt",
        bytes=1_400_382,
        sha256="d47586fc9c1c59338f234b3c6634c31744739887169f58d411ac766f3861fcf2",
    ),
}

MONTHS: dict[str, int] = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}

_HMS_RE = re.compile(r"\A(\d{2}):(\d{2}):(\d{2})\Z")
_DUR_RE = re.compile(r"\A(\d{2})m(\d{2})s\Z")
_FLOAT_COORD_RE = re.compile(r"\A(\d+\.\d+)([NSEW])\Z")
_INT_COORD_RE = re.compile(r"\A(\d+)([NSEW])\Z")


def _parse_month(token: str) -> int:
    if token not in MONTHS:
        raise ValueError(f"unknown month {token!r}")
    return MONTHS[token]


def _parse_td_seconds(token: str) -> int:
    match = _HMS_RE.fullmatch(token)
    if match is None:
        raise ValueError(f"invalid time {token!r}")
    hours, minutes, seconds = (int(part) for part in match.groups())
    if hours > 23 or minutes > 59 or seconds > 59:
        raise ValueError(f"invalid time {token!r}")
    return hours * 3600 + minutes * 60 + seconds


def _parse_duration_seconds(token: str) -> int | None:
    if token == "-":
        return None
    match = _DUR_RE.fullmatch(token)
    if match is None:
        raise ValueError(f"invalid central duration {token!r}")
    minutes, seconds = (int(part) for part in match.groups())
    if seconds > 59:
        raise ValueError(f"invalid central duration {token!r}")
    return minutes * 60 + seconds


def _parse_float_coordinate(token: str, positive: str, negative: str) -> float:
    match = _FLOAT_COORD_RE.fullmatch(token)
    if match is None or match.group(2) not in (positive, negative):
        raise ValueError(f"invalid coordinate {token!r}")
    value = float(match.group(1))
    return -value if match.group(2) == negative and value != 0.0 else value


def _parse_int_coordinate(token: str, positive: str, negative: str) -> int:
    match = _INT_COORD_RE.fullmatch(token)
    if match is None or match.group(2) not in (positive, negative):
        raise ValueError(f"invalid coordinate {token!r}")
    value = int(match.group(1))
    return -value if match.group(2) == negative and value != 0 else value


def _parse_type(token: str, allowed_types: tuple[str, ...], allowed_qualifiers: tuple[str, ...]) -> tuple[str, str | None]:
    if len(token) == 1 and token in allowed_types:
        return token, None
    if len(token) == 2 and token[0] in allowed_types and token[1] in allowed_qualifiers:
        return token[0], token[1]
    raise ValueError(f"unknown eclipse type {token!r}")


def parse_solar_line(line: str) -> list[Any]:
    """Parse one data line of 5MKSEcatalog.txt into the 21-element tuple of SolarEclipseRow."""
    tokens = line.split()
    if len(tokens) == 17:
        width_tok, dur_tok = "-", "-"
    elif len(tokens) == 19:
        width_tok, dur_tok = tokens[17], tokens[18]
    else:
        raise ValueError(f"expected 17 or 19 fields in solar line, got {len(tokens)}: {line!r}")

    cat_num = int(tokens[0])
    plate = int(tokens[1])
    year = int(tokens[2])
    month = _parse_month(tokens[3])
    day = int(tokens[4])
    calendar = eclipse_calendar_of(year, month, day)
    if calendar is None:
        raise ValueError(f"date {year}-{month:02d}-{day:02d} does not exist in NASA's eclipse calendar")
    td_seconds = _parse_td_seconds(tokens[5])
    delta_t = int(tokens[6])
    lunation = int(tokens[7])
    saros = int(tokens[8])
    ecl_type, qualifier = _parse_type(tokens[9], SOLAR_ECLIPSE_TYPES, SOLAR_ECLIPSE_QUALIFIERS)
    qle = tokens[10]
    gamma = float(tokens[11])
    magnitude = float(tokens[12])
    lat_deg = _parse_float_coordinate(tokens[13], "N", "S")
    lon_deg = _parse_float_coordinate(tokens[14], "E", "W")
    sun_alt = int(tokens[15])
    sun_azm = int(tokens[16])
    path_width = None if width_tok == "-" else int(width_tok)
    central_dur = _parse_duration_seconds(dur_tok)

    return [
        cat_num,
        plate,
        year,
        month,
        day,
        calendar,
        td_seconds,
        delta_t,
        lunation,
        saros,
        ecl_type,
        qualifier,
        qle,
        gamma,
        magnitude,
        lat_deg,
        lon_deg,
        sun_alt,
        sun_azm,
        path_width,
        central_dur,
    ]


def parse_lunar_line(line: str) -> list[Any]:
    """Parse one data line of 5MKLEcatalog.txt into the 20-element tuple of LunarEclipseRow."""
    tokens = line.split()
    if len(tokens) != 18:
        raise ValueError(f"expected 18 fields in lunar line, got {len(tokens)}: {line!r}")

    cat_num = int(tokens[0])
    year = int(tokens[1])
    month = _parse_month(tokens[2])
    day = int(tokens[3])
    calendar = eclipse_calendar_of(year, month, day)
    if calendar is None:
        raise ValueError(f"date {year}-{month:02d}-{day:02d} does not exist in NASA's eclipse calendar")
    td_seconds = _parse_td_seconds(tokens[4])
    delta_t = int(tokens[5])
    lunation = int(tokens[6])
    saros = int(tokens[7])
    ecl_type, qualifier = _parse_type(tokens[8], LUNAR_ECLIPSE_TYPES, LUNAR_ECLIPSE_QUALIFIERS)
    qse = tokens[9]
    gamma = float(tokens[10])
    pen_mag = float(tokens[11])
    umb_mag = float(tokens[12])
    pen_dur = float(tokens[13])
    par_dur = None if tokens[14] == "-" else float(tokens[14])
    tot_dur = None if tokens[15] == "-" else float(tokens[15])
    zenith_lat = _parse_int_coordinate(tokens[16], "N", "S")
    zenith_lon = _parse_int_coordinate(tokens[17], "E", "W")

    return [
        cat_num,
        year,
        month,
        day,
        calendar,
        td_seconds,
        delta_t,
        lunation,
        saros,
        ecl_type,
        qualifier,
        qse,
        gamma,
        pen_mag,
        umb_mag,
        pen_dur,
        par_dur,
        tot_dur,
        zenith_lat,
        zenith_lon,
    ]


def parse_solar_catalog(text: str) -> SolarEclipseCatalog:
    """Parse the full text of 5MKSEcatalog.txt and validate against SolarEclipseCatalog."""
    lines = text.splitlines()
    if (
        len(lines) < 11
        or lines[0] != "Title: Five Millennium Catalog of Solar Eclipses: -1999 to +3000 (2000 BCE to 3000CE)"
        or lines[2] != "Source: NASA Technical Publication NASA/TP-2008-214170"
        or "\xa1" not in lines[9]
    ):
        raise ValueError("5MKSEcatalog.txt header does not match NASA/TP-2008-214170 format")
    rows = [parse_solar_line(line) for line in lines[10:] if line.strip()]
    return SolarEclipseCatalog.model_validate(
        {
            "format": ECLIPSE_CATALOG_FORMAT,
            "formatVersion": ECLIPSE_CATALOG_FORMAT_VERSION,
            "kind": "solar",
            "evidenceClass": ECLIPSE_EVIDENCE_CLASS,
            "columns": list(SOLAR_ECLIPSE_COLUMNS),
            "rows": rows,
        }
    )


def parse_lunar_catalog(text: str) -> LunarEclipseCatalog:
    """Parse the full text of 5MKLEcatalog.txt and validate against LunarEclipseCatalog."""
    lines = text.splitlines()
    if (
        len(lines) < 14
        or lines[0] != "Title: Five Millennium Catalog of Lunar Eclipses: -1999 to +3000 (2000 BCE to 3000CE)"
        or lines[2] != "Source: NASA Technical Publication NASA/TP-2009-214173"
    ):
        raise ValueError("5MKLEcatalog.txt header does not match NASA/TP-2009-214173 format")
    rows = [parse_lunar_line(line) for line in lines[13:] if line.strip()]
    return LunarEclipseCatalog.model_validate(
        {
            "format": ECLIPSE_CATALOG_FORMAT,
            "formatVersion": ECLIPSE_CATALOG_FORMAT_VERSION,
            "kind": "lunar",
            "evidenceClass": ECLIPSE_EVIDENCE_CLASS,
            "columns": list(LUNAR_ECLIPSE_COLUMNS),
            "rows": rows,
        }
    )


def build_solar_catalog(path: Path) -> SolarEclipseCatalog:
    return parse_solar_catalog(path.read_text(encoding="latin-1"))


def build_lunar_catalog(path: Path) -> LunarEclipseCatalog:
    return parse_lunar_catalog(path.read_text(encoding="latin-1"))


def dumps_catalog(catalog: SolarEclipseCatalog | LunarEclipseCatalog) -> str:
    """Single-line compact JSON with a final newline."""
    return json.dumps(catalog.to_contract_dict(), ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n"


def build_provenance_record(out_dir: Path) -> AssetProvenanceRecord:
    solar_sha = hashlib.sha256((out_dir / SOLAR_OUT_NAME).read_bytes()).hexdigest()
    lunar_sha = hashlib.sha256((out_dir / LUNAR_OUT_NAME).read_bytes()).hexdigest()
    return AssetProvenanceRecord.model_validate(
        {
            "format": "albedian.asset-provenance",
            "formatVersion": 1,
            "sources": [
                {
                    "id": "nasa-gsfc-5mkse",
                    "title": "Five Millennium Catalog of Solar Eclipses: -1999 to +3000",
                    "url": INPUTS["solar"].url,
                    "version": "2008 Oct 07 (header NASA/TP-2008-214170; web page NASA/TP-2009-214174, 2009 Jan)",
                    "retrieved": "2026-11-05",
                    "license": {
                        "spdx": "LicenseRef-NASA-Eclipse-Permission",
                        "name": "NASA Eclipse Web Site Reproduction Permission",
                        "url": "https://eclipse.gsfc.nasa.gov/SEcat5/SEcatkey.html",
                        "conditions": (
                            'NASA\'s catalog key states: \'Permission is freely granted to reproduce this data when '
                            'accompanied by an acknowledgment: "Eclipse Predictions by Fred Espenak and Jean Meeus '
                            "(NASA's GSFC)\"'."
                        ),
                    },
                    "credit": CREDIT,
                    "citation": (
                        "Espenak, F. and Meeus, J., Five Millennium Catalog of Solar Eclipses: -1999 to +3000 "
                        "(2000 BCE to 3000 CE), NASA/TP-2009-214174 (file header: NASA/TP-2008-214170), "
                        "Goddard Space Flight Center, Greenbelt, MD, 2009."
                    ),
                    "acknowledgement": CREDIT,
                    "sha256": INPUTS["solar"].sha256,
                    "note": (
                        "The ASCII file header names NASA/TP-2008-214170 (dated 2008 Oct 07), whereas the NASA "
                        "publication page https://eclipse.gsfc.nasa.gov/SEpubs/5MKSE.html names NASA/TP-2009-214174 "
                        "(published 2009 January). The errata page https://eclipse.gsfc.nasa.gov/5MCSE/5MKSEerrata.html "
                        "notes that tabulated values of delta T and longitude on pages A-56 to A-150 were incorrect "
                        "in the hard copy only and corrected in the PDF. Sun coordinates use VSOP87 (Bretagnon and "
                        "Francou, 1988) and Moon coordinates use ELP-2000/82 (Chapront-Touze and Chapront, 1983) with "
                        "lunar secular acceleration n-dot = -25.858 arcsec/cy^2 (Chapront, Chapront-Touze, and "
                        "Francou, 2002). The largest uncertainty in the eclipse predictions is fluctuations in Earth's "
                        "rotation expressed as delta T: pre-1950s from empirical fits to historical records by "
                        "Morrison and Stephenson (2004), 1955-present from published observations, and future values "
                        "extrapolated from the long-term tidal trend."
                    ),
                },
                {
                    "id": "nasa-gsfc-5mkle",
                    "title": "Five Millennium Catalog of Lunar Eclipses: -1999 to +3000",
                    "url": INPUTS["lunar"].url,
                    "version": "2011 May 23 (NASA/TP-2009-214173, published 2009 Jan)",
                    "retrieved": "2026-11-05",
                    "license": {
                        "spdx": "LicenseRef-NASA-Eclipse-Permission",
                        "name": "NASA Eclipse Web Site Reproduction Permission",
                        "url": "https://eclipse.gsfc.nasa.gov/LEcat5/LEcatkey.html",
                        "conditions": (
                            'NASA\'s catalog key states: \'Permission is freely granted to reproduce this data when '
                            'accompanied by an acknowledgment: "Eclipse Predictions by Fred Espenak and Jean Meeus '
                            "(NASA's GSFC)\"'."
                        ),
                    },
                    "credit": CREDIT,
                    "citation": (
                        "Espenak, F. and Meeus, J., Five Millennium Catalog of Lunar Eclipses: -1999 to +3000 "
                        "(2000 BCE to 3000 CE), NASA/TP-2009-214173, Goddard Space Flight Center, Greenbelt, MD, 2009."
                    ),
                    "acknowledgement": CREDIT,
                    "sha256": INPUTS["lunar"].sha256,
                    "note": (
                        "Earth's penumbral and umbral shadow sizes are calculated with Danjon's enlargement method; "
                        "Sun and Moon coordinates use VSOP87 and ELP-2000/82 with n-dot = -25.858 arcsec/cy^2. "
                        "Where the catalog key lists '*' for a total penumbral lunar eclipse, the ASCII catalog "
                        "file writes 'x' (141 rows, all with penumbral magnitude >= 1). As for solar eclipses, "
                        "the largest uncertainty is delta T: pre-1950s from Morrison and Stephenson (2004), "
                        "1955-2006 from published observations, and post-2006 extrapolated from the tidal trend."
                    ),
                },
            ],
            "files": [
                {
                    "path": SOLAR_OUT_NAME,
                    "sha256": solar_sha,
                    "sources": ["nasa-gsfc-5mkse"],
                    "producedBy": {
                        "method": "script",
                        "script": "scripts/bake_eclipses.py",
                        "command": "uv run python scripts/bake_eclipses.py",
                    },
                    "note": (
                        "All 11,898 solar eclipses from -1999 to +3000 across all 21 columns of 5MKSEcatalog.txt, "
                        "with evidenceClass 'predicted'. Dates use the Julian calendar through 1582 October 4 and "
                        "the Gregorian calendar from 1582 October 15, with astronomical year numbering (year 0 = 1 BCE)."
                    ),
                },
                {
                    "path": LUNAR_OUT_NAME,
                    "sha256": lunar_sha,
                    "sources": ["nasa-gsfc-5mkle"],
                    "producedBy": {
                        "method": "script",
                        "script": "scripts/bake_eclipses.py",
                        "command": "uv run python scripts/bake_eclipses.py",
                    },
                    "note": (
                        "All 12,064 lunar eclipses from -1999 to +3000 across all 20 columns of 5MKLEcatalog.txt, "
                        "with evidenceClass 'predicted'. Dates use the Julian calendar through 1582 October 4 and "
                        "the Gregorian calendar from 1582 October 15, with astronomical year numbering (year 0 = 1 BCE)."
                    ),
                },
            ],
            "fileSets": [],
        }
    )


def check_inputs(cache: Path) -> None:
    for name, source in INPUTS.items():
        path = cache / source.filename
        if not path.is_file():
            raise SystemExit(f"[eclipses] missing {path}; run: uv run python scripts/fetch_download.py .cache/m2/{source.filename}")
        raw = path.read_bytes()
        if len(raw) != source.bytes:
            raise SystemExit(f"[eclipses] {name}: {path.name} has {len(raw)} bytes, expected {source.bytes}")
        digest = hashlib.sha256(raw).hexdigest()
        if digest != source.sha256:
            raise SystemExit(f"[eclipses] {name}: {path.name} has sha256 {digest}, expected {source.sha256}")


def verify_outputs(cache: Path, out_dir: Path) -> list[str]:
    check_inputs(cache)
    errors: list[str] = []
    solar_text = dumps_catalog(build_solar_catalog(cache / INPUTS["solar"].filename))
    lunar_text = dumps_catalog(build_lunar_catalog(cache / INPUTS["lunar"].filename))
    for name, expected in ((SOLAR_OUT_NAME, solar_text), (LUNAR_OUT_NAME, lunar_text)):
        target = out_dir / name
        if not target.is_file() or target.read_text(encoding="utf-8") != expected:
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
                print(f"[eclipses] {err}", file=sys.stderr)
            return 1
        print("[eclipses] solar.json, lunar.json and PROVENANCE.json match a fresh bake")
        return 0

    check_inputs(args.cache)
    args.out.mkdir(parents=True, exist_ok=True)
    solar = build_solar_catalog(args.cache / INPUTS["solar"].filename)
    lunar = build_lunar_catalog(args.cache / INPUTS["lunar"].filename)
    (args.out / SOLAR_OUT_NAME).write_text(dumps_catalog(solar), encoding="utf-8")
    (args.out / LUNAR_OUT_NAME).write_text(dumps_catalog(lunar), encoding="utf-8")
    record = build_provenance_record(args.out)
    (args.out / "PROVENANCE.json").write_text(dumps_record(record), encoding="utf-8")
    for name in (SOLAR_OUT_NAME, LUNAR_OUT_NAME, "PROVENANCE.json"):
        size = (args.out / name).stat().st_size
        print(f"[eclipses] {name}: {size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
