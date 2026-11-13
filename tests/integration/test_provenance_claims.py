"""Every claim of the 24 producedBy.changes sentences, counted in the committed file (provenance-changes-1).

The sentences say how each file a script made differs from its sources, and the Credits page shows them to a person.
Each row below is one claim, in the sentence's own words, of one of two kinds, named at the start of its id:
- [source] compares the committed file with its source, value for value (the source in the shared .cache, so the row
  carries the dataset marker and is skipped on CI as the suite's other dataset rows are);
- [form] holds the value's form only: a type, a range, a set of labels, or agreement with another committed file. A
  form row does not know whether a value is right: a file wrong in a value of the right form passes it.
The files themselves are held by their digests in the records (scripts/verify_provenance.py); these rows hold what the
sentences say about them. Each row also names a wrong copy of its file, made here by code, under which its check must
fail.

The sentences were settled by a count of every claim, once, in the unit's report (count_claims.py beside it); a step
the file cannot show (a filter, an average whose inputs are not kept) is not a row here.
"""

from __future__ import annotations

import copy
import csv
import gzip
import hashlib
import importlib.util
import io
import json
import math
import re
import struct
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "apps" / "web" / "public"
CACHE = ROOT / ".cache"
Image.MAX_IMAGE_PIXELS = None

LINES_SRC = ".cache/d3celestial/constellations.lines.json"
BOUNDS_SRC = ".cache/d3celestial/constellations.bounds.json"
NGC_SRC = ".cache/m2/openngc_v20260501_NGC.csv"
ADDENDUM_SRC = ".cache/m2/openngc_v20260501_addendum.csv"
LUNAR_SRC = ".cache/m2/5MKLEcatalog.txt"
SOLAR_SRC = ".cache/m2/5MKSEcatalog.txt"
EXO_SRC = ".cache/m2/pscomppars.csv"
SHELL_SRC = ".cache/dust/oneill2024/ONeill2024_LocalBubble_ShellProperties_A0.5.fits"
BRIGHT_SRC = ".cache/hipparcos-bright-3d/hipparcos_bright_3d.csv"
HYG_SRC = ".cache/star-designations/hygdata_v41.csv"
NEIGHBOUR_SRC = ".cache/star-designations/Hipparcos2BestNeighbour.csv.gz"
UCD_DATA = ".cache/unicode/ucd/15.1.0/UnicodeData.txt"
UCD_CORE = ".cache/unicode/ucd/15.1.0/DerivedCoreProperties.txt"


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=None)
def bake(name: str):
    return _module(name)


@lru_cache(maxsize=None)
def _json(rel: str) -> Any:
    return json.loads((PUBLIC / rel).read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _bytes(rel: str) -> bytes:
    return (PUBLIC / rel).read_bytes()


@lru_cache(maxsize=1)
def _tiles() -> dict[str, np.ndarray]:
    from services.science.phone_photo_solver import _get_tile_index

    return dict(_get_tile_index().data)


class Files:
    """One test's view of the committed files: each read once and copied, so a wrong copy never reaches another test."""

    def __init__(self) -> None:
        self.held: dict[Any, Any] = {}

    def json(self, rel: str) -> Any:
        if rel not in self.held:
            self.held[rel] = copy.deepcopy(_json(rel))
        return self.held[rel]

    def bytes(self, rel: str) -> bytes:
        return self.held.get(("bytes", rel)) or _bytes(rel)

    def set_bytes(self, rel: str, data: bytes) -> None:
        self.held[("bytes", rel)] = data

    def tiles(self) -> dict[str, np.ndarray]:
        if "tiles" not in self.held:
            self.held["tiles"] = dict(_tiles())
        return self.held["tiles"]


@dataclass(frozen=True)
class Claim:
    file: str
    words: str
    check: Callable[[Files], None]
    wrong: Callable[[Files], None]
    cache: tuple[str, ...] = field(default=())

    @property
    def kind(self) -> str:
        """"source" for a row that compares with the source in the cache, "form" for one that holds the form only."""
        return "source" if self.cache else "form"


CLAIMS: list[Claim] = []


def claim(file: str, words: str, *, cache: tuple[str, ...] = ()):
    def register(check: Callable[[Files], None]):
        def with_wrong(wrong: Callable[[Files], None]):
            CLAIMS.append(Claim(file, words, check, wrong, cache))
            return wrong

        check.wrong = with_wrong  # type: ignore[attr-defined]
        return check

    return register


def decimals(x: float) -> int:
    text = repr(float(x))
    return 0 if "e" in text or "." not in text else len(text.split(".")[1].rstrip("0"))


def image(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def tiny(fmt: str, **save: Any) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 4), (9, 9, 9)).save(buffer, fmt, **save)
    return buffer.getvalue()


def webp_chunk(data: bytes) -> str:
    return data[12:16].decode("ascii")


def glb(data: bytes) -> tuple[dict[str, Any], bytes]:
    length = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20 : 20 + length])
    binary_start = 20 + length + 8
    return gltf, data[binary_start:]


def glb_with(gltf: dict[str, Any], data: bytes) -> bytes:
    """`data` with its JSON chunk replaced by `gltf` (padded with spaces to a multiple of four)."""
    old_length = struct.unpack_from("<I", data, 12)[0]
    text = json.dumps(gltf).encode()
    text += b" " * (-len(text) % 4)
    body = struct.pack("<I4s", len(text), b"JSON") + text + data[20 + old_length :]
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def positions(data: bytes) -> np.ndarray:
    gltf, binary = glb(data)
    accessor = gltf["accessors"][gltf["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
    view = gltf["bufferViews"][accessor["bufferView"]]
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    return np.frombuffer(binary, dtype="<f4", count=accessor["count"] * 3, offset=start).reshape(-1, 3)


def glb_scaled_vertex(data: bytes, index: int, scale: float) -> bytes:
    """`data` with one vertex moved along its own direction."""
    gltf, binary = glb(data)
    accessor = gltf["accessors"][gltf["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
    view = gltf["bufferViews"][accessor["bufferView"]]
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0) + 12 * index
    x, y, z = struct.unpack_from("<3f", binary, start)
    patched = bytearray(binary)
    struct.pack_into("<3f", patched, start, x * scale, y * scale, z * scale)
    return data[: len(data) - len(binary)] + bytes(patched)


# =============================================================================================
# Sources in the shared .cache, read once
# =============================================================================================


@lru_cache(maxsize=None)
def _source_json(rel: str) -> Any:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _semicolon_rows(rel: str) -> list[dict[str, str]]:
    with open(ROOT / rel, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


@lru_cache(maxsize=None)
def _eclipse_lines(rel: str) -> list[str]:
    lines = (ROOT / rel).read_text(encoding="latin-1").splitlines()
    return [ln for ln in lines if re.match(r"^\s*\d+\s+(-?\d+\s+)?-?\d+\s+[A-Z][a-z]{2}\s+\d+\s", ln)]


@lru_cache(maxsize=1)
def _exo_rows() -> list[dict[str, str]]:
    with open(ROOT / EXO_SRC, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(line for line in fh if not line.startswith("#")))


@lru_cache(maxsize=1)
def _shell() -> dict[str, np.ndarray]:
    return bake("bake_local_bubble_mesh").read_shell_fits_table(ROOT / SHELL_SRC)


@lru_cache(maxsize=1)
def _archive() -> dict[int, dict[str, str]]:
    with open(ROOT / BRIGHT_SRC, encoding="utf-8", newline="") as fh:
        return {int(r["hip"]): r for r in csv.DictReader(fh)}


@lru_cache(maxsize=1)
def _hyg() -> tuple[Any, Any]:
    return bake("bake_star_designations").hyg_designations((ROOT / HYG_SRC).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _best() -> dict[int, int]:
    with gzip.open(ROOT / NEIGHBOUR_SRC, "rt", encoding="utf-8", newline="") as fh:
        return bake("bake_star_designations").best_neighbours(fh.read())


@lru_cache(maxsize=None)
def _tile_rows(wanted: frozenset[int]) -> tuple[dict[int, Any], dict[int, Any]]:
    """Every Hipparcos row of the committed tiles by HIP number, and the Gaia rows whose source id is wanted, every layer."""
    return bake("bake_star_designations").tile_rows(PUBLIC / "catalog" / "stars" / "gaia-dr3", set(wanted))


def _gaia_tile_rows() -> dict[int, Any]:
    return _tile_rows(frozenset(_best().values()))[1]


def _sexagesimal(text: str, *, hours: bool) -> float:
    text = text.strip()
    sign = -1.0 if text.startswith("-") else 1.0
    whole, minutes, seconds = (float(part) for part in text.lstrip("+-").split(":"))
    return round(sign * (whole + minutes / 60.0 + seconds / 3600.0) * (15.0 if hours else 1.0), 6)


def _blank_float(text: str | None) -> float | None:
    return None if text is None or not text.strip() else float(text)


# =============================================================================================
# The constellations (scripts/bake_constellations_d3celestial.py)
# =============================================================================================

for _name, _src, _key, _kept in (("lines.json", LINES_SRC, "polylines", "every figure and point kept in order"),
                                 ("bounds.json", BOUNDS_SRC, "ring", "every boundary and corner kept in order")):
    _rel = f"catalog/constellations/{_name}"

    def _points(c: dict[str, Any], key: str = _key) -> list[list[float]]:
        return [p for line in c[key] for p in line] if key == "polylines" else c[key]

    def _check_kept(f: Files, rel: str = _rel, src: str = _src, key: str = _key) -> None:
        # Entry by entry and point by point in the source's order: its code (the two halves of Serpens told apart at a
        # right ascension of 250 degrees), its rank as a number, and each point with its longitude as a right ascension
        expected = []
        for feature in _source_json(src)["features"]:
            parts = [[[lon if lon >= 0 else round(lon + 360, 4), lat] for lon, lat in part] for part in feature["geometry"]["coordinates"]]
            ras = [p[0] for part in parts for p in part]
            code = feature["id"] if feature["id"] != "Ser" else ("Se1" if max(ras) < 250.0 else "Se2" if min(ras) > 250.0 else "Ser")
            body = {"polylines": parts} if key == "polylines" else {"ring": parts[0]}
            expected.append({"code": code, "rank": int(feature["properties"]["rank"]), **body})
        assert f.json(rel)["constellations"] == expected

    for _how, _make in (
        ("a point dropped", lambda c, key=_key: (c[0][key][0] if key == "polylines" else c[0][key]).pop()),
        ("two constellations swapped", lambda c: c.__setitem__(slice(0, 2), [c[1], c[0]])),
        ("one figure's points reversed" if _key == "polylines" else "one ring's corners reversed",
         lambda c, key=_key: (c[3][key][0] if key == "polylines" else c[3][key]).reverse()),
    ):
        def _wrong_kept(f: Files, rel: str = _rel, make=_make) -> None:
            make(f.json(rel)["constellations"])

        claim(_rel, f"{_kept}: longitudes from -180 to 180 become right ascensions from 0 to 360 (wrong copy: {_how})",
              cache=(_src,))(_check_kept).wrong(_wrong_kept)

    def _check_range(f: Files, rel: str = _rel, points=_points) -> None:
        ras = [p[0] for c in f.json(rel)["constellations"] for p in points(c)]
        assert 0.0 <= min(ras) and max(ras) < 360.0 and max(decimals(r) for r in ras) <= 4

    def _wrong_range(f: Files, rel: str = _rel, points=_points) -> None:
        points(f.json(rel)["constellations"][0])[0][0] = -0.1234

    claim(_rel, "right ascensions from 0 to 360, rounded back to the source's four decimals")(_check_range).wrong(_wrong_range)

    def _wrong_decimals(f: Files, rel: str = _rel, points=_points) -> None:
        points(f.json(rel)["constellations"][0])[0][0] += 0.00001

    claim(_rel, "rounded back to the source's four decimals (a fifth decimal)")(_check_range).wrong(_wrong_decimals)

    def _check_rank(f: Files, rel: str = _rel) -> None:
        assert all(isinstance(c["rank"], int) and not isinstance(c["rank"], bool) for c in f.json(rel)["constellations"])

    def _wrong_rank(f: Files, rel: str = _rel) -> None:
        f.json(rel)["constellations"][0]["rank"] = "1"

    claim(_rel, "each rank becomes a number")(_check_rank).wrong(_wrong_rank)

    def _check_serpens(f: Files, rel: str = _rel) -> None:
        codes = [c["code"] for c in f.json(rel)["constellations"]]
        assert codes.count("Se1") == 1 and codes.count("Se2") == 1 and "Ser" not in codes and len(codes) == 89

    def _wrong_serpens(f: Files, rel: str = _rel) -> None:
        next(c for c in f.json(rel)["constellations"] if c["code"] == "Se2")["code"] = "Ser"

    claim(_rel, "the two halves of Serpens get codes of their own")(_check_serpens).wrong(_wrong_serpens)

    def _check_stated(f: Files, rel: str = _rel, key: str = _key) -> None:
        d = f.json(rel)
        assert str(d.get("release", "")).startswith("d3-celestial ")
        assert (d.get("equinox") == "J2000") if key == "polylines" else (d.get("vertexEquinox") == "J2000" and d.get("edgeEquinox") == "B1875")

    def _wrong_stated(f: Files, rel: str = _rel, key: str = _key) -> None:
        del f.json(rel)["equinox" if key == "polylines" else "edgeEquinox"]

    claim(_rel, "the release and the equinox (of the corners and of the edges) are stated")(_check_stated).wrong(_wrong_stated)

CONSTELLATION_NOTICE = "catalog/constellations/LICENSE"


@claim(CONSTELLATION_NOTICE, "the notice is d3-celestial's licence text at the pinned commit, written unchanged "
       "(equal to the script's copy of it; that copy against d3-celestial's repository is not counted here)")
def _constellation_notice(f: Files) -> None:
    assert f.bytes(CONSTELLATION_NOTICE).decode("utf-8") == bake("bake_constellations_d3celestial").NOTICE_TEXT


@_constellation_notice.wrong
def _(f: Files) -> None:
    f.set_bytes(CONSTELLATION_NOTICE, f.bytes(CONSTELLATION_NOTICE).replace(b"Olaf", b"Olav", 1))

# =============================================================================================
# The deep-sky catalogue (scripts/bake_deepsky_openngc.py)
# =============================================================================================

NGC = "catalog/deepsky/openngc.json"
NGC_COLUMNS = {"name": "Name", "type": "Type", "raDeg": "RA", "decDeg": "Dec", "constellation": "Const",
               "majorAxisArcmin": "MajAx", "minorAxisArcmin": "MinAx", "positionAngleDeg": "PosAng", "bMag": "B-Mag",
               "vMag": "V-Mag", "surfaceBrightness": "SurfBr", "messier": "M", "ngc": "NGC", "ic": "IC",
               "identifiers": "Identifiers", "commonNames": "Common names"}


def _ngc_source() -> list[dict[str, str]]:
    return _semicolon_rows(NGC_SRC) + _semicolon_rows(ADDENDUM_SRC)


@claim(NGC, "both of the catalogue's tables kept row for row", cache=(NGC_SRC, ADDENDUM_SRC))
def _ngc_rows(f: Files) -> None:
    d = f.json(NGC)
    assert [r[0] for r in d["rows"]] == [s["Name"] for s in _ngc_source()]


@_ngc_rows.wrong
def _(f: Files) -> None:
    f.json(NGC)["rows"].pop(5)


@claim(NGC, "right ascension from hours, minutes and seconds and declination from degrees, minutes and seconds, both to "
       "degrees rounded to six decimals", cache=(NGC_SRC, ADDENDUM_SRC))
def _ngc_positions(f: Files) -> None:
    d = f.json(NGC)
    ra, de = d["columns"].index("raDeg"), d["columns"].index("decDeg")
    for row, source in zip(d["rows"], _ngc_source(), strict=True):
        assert row[ra] == (_sexagesimal(source["RA"], hours=True) if source["RA"].strip() else None), source["Name"]
        assert row[de] == (_sexagesimal(source["Dec"], hours=False) if source["Dec"].strip() else None), source["Name"]


@_ngc_positions.wrong
def _(f: Files) -> None:
    d = f.json(NGC)
    row = next(r for r in d["rows"] if r[d["columns"].index("decDeg")] is not None)
    row[d["columns"].index("decDeg")] += 10.0


@claim(NGC, "Messier numbers and measurements are read as numbers and empty cells are left empty", cache=(NGC_SRC, ADDENDUM_SRC))
def _ngc_numbers(f: Files) -> None:
    d = f.json(NGC)
    at = {c: d["columns"].index(c) for c in ("majorAxisArcmin", "minorAxisArcmin", "positionAngleDeg", "bMag", "vMag", "surfaceBrightness")}
    m = d["columns"].index("messier")
    for row, source in zip(d["rows"], _ngc_source(), strict=True):
        for column, i in at.items():
            assert row[i] == _blank_float(source[NGC_COLUMNS[column]]), (source["Name"], column)
        assert row[m] == (int(source["M"]) if source["M"].strip() else None), source["Name"]
        assert row[m] is None or (isinstance(row[m], int) and not isinstance(row[m], bool))


@_ngc_numbers.wrong
def _(f: Files) -> None:
    d = f.json(NGC)
    row = next(r for r in d["rows"] if r[d["columns"].index("bMag")] is None)
    row[d["columns"].index("bMag")] = 0.0


@claim(NGC, "lists of other names are split at their commas, each name trimmed and empty ones dropped", cache=(NGC_SRC, ADDENDUM_SRC))
def _ngc_lists(f: Files) -> None:
    d = f.json(NGC)
    for column in ("ngc", "ic", "identifiers", "commonNames"):
        i = d["columns"].index(column)
        for row, source in zip(d["rows"], _ngc_source(), strict=True):
            assert row[i] == [x.strip() for x in source[NGC_COLUMNS[column]].split(",") if x.strip()], (source["Name"], column)


@_ngc_lists.wrong
def _(f: Files) -> None:
    d = f.json(NGC)
    row = next(r for r in d["rows"] if r[d["columns"].index("identifiers")])
    row[d["columns"].index("identifiers")][0] = " " + row[d["columns"].index("identifiers")][0]


@claim(NGC, "the columns the app does not use (near-infrared magnitudes, parallax, proper motion, radial velocity, redshift, "
       "Hubble type, central stars, notes and sources) are left out")
def _ngc_columns(f: Files) -> None:
    columns = f.json(NGC)["columns"]
    assert columns == list(NGC_COLUMNS)
    left_out = re.compile(r"(?i)(^[jhk]mag|^[jhk]-?mag|parallax|^pax|pm|propermotion|radvel|radialvelocity|redshift|hubble|cstar|centralstar|note|source)")
    assert not [c for c in columns if left_out.search(c)], columns


@_ngc_columns.wrong
def _(f: Files) -> None:
    d = f.json(NGC)
    d["columns"].append("parallaxMas")
    for row in d["rows"]:
        row.append(None)


@claim(NGC, "the object types are named")
def _ngc_types(f: Files) -> None:
    d = f.json(NGC)
    used = {r[d["columns"].index("type")] for r in d["rows"]}
    assert used <= set(d["types"]) and all(d["types"][t] for t in used)


@_ngc_types.wrong
def _(f: Files) -> None:
    d = f.json(NGC)
    d["rows"][0][d["columns"].index("type")] = "NotAType"


# =============================================================================================
# The eclipses (scripts/bake_eclipses.py)
# =============================================================================================

for _kind, _src in (("lunar", LUNAR_SRC), ("solar", SOLAR_SRC)):
    _rel = f"catalog/eclipses/{_kind}.json"

    def _check_kept(f: Files, rel: str = _rel, src: str = _src) -> None:
        d = f.json(rel)
        numbers = [int(ln.split()[0]) for ln in _eclipse_lines(src)]
        assert [r[d["columns"].index("catalogNumber")] for r in d["rows"]] == numbers

    def _wrong_kept(f: Files, rel: str = _rel) -> None:
        f.json(rel)["rows"].pop(7)

    claim(_rel, "every eclipse in the catalogue kept", cache=(_src,))(_check_kept).wrong(_wrong_kept)

    def _check_dates(f: Files, rel: str = _rel) -> None:
        d = f.json(rel)
        at = {c: d["columns"].index(c) for c in ("year", "month", "day", "calendar")}
        for r in d["rows"]:
            assert r[at["month"]] in range(1, 13)
            assert r[at["calendar"]] == ("julian" if (r[at["year"]], r[at["month"]], r[at["day"]]) < (1582, 10, 15) else "gregorian")

    def _wrong_month(f: Files, rel: str = _rel) -> None:
        d = f.json(rel)
        d["rows"][3][d["columns"].index("month")] = "Jun"

    def _wrong_calendar(f: Files, rel: str = _rel) -> None:
        d = f.json(rel)
        d["rows"][3][d["columns"].index("calendar")] = "gregorian"

    claim(_rel, "month names become numbers")(_check_dates).wrong(_wrong_month)
    claim(_rel, "each date is marked as Julian or Gregorian")(_check_dates).wrong(_wrong_calendar)

    def _check_seconds(f: Files, rel: str = _rel) -> None:
        d = f.json(rel)
        t = d["columns"].index("greatestEclipseTdSeconds")
        assert all(isinstance(r[t], int) and 0 <= r[t] < 86400 for r in d["rows"])

    def _wrong_seconds(f: Files, rel: str = _rel) -> None:
        d = f.json(rel)
        d["rows"][0][d["columns"].index("greatestEclipseTdSeconds")] = "14:12:01"

    claim(_rel, "the time of greatest eclipse becomes seconds")(_check_seconds).wrong(_wrong_seconds)

    def _check_type(f: Files, rel: str = _rel, kind: str = _kind) -> None:
        d = f.json(rel)
        letters = {"lunar": {"N", "P", "T"}, "solar": {"A", "H", "P", "T"}}[kind]
        t, q = d["columns"].index("eclipseType"), d["columns"].index("typeQualifier")
        assert all(r[t] in letters and (r[q] is None or (isinstance(r[q], str) and len(r[q]) == 1)) for r in d["rows"])

    def _wrong_type(f: Files, rel: str = _rel) -> None:
        d = f.json(rel)
        d["rows"][0][d["columns"].index("eclipseType")] += "+"

    claim(_rel, "each eclipse type is split into its letter and its qualifier")(_check_type).wrong(_wrong_type)

    if _kind == "lunar":
        def _check_zenith(f: Files, rel: str = _rel) -> None:
            d = f.json(rel)
            la, lo = d["columns"].index("zenithLatitudeDeg"), d["columns"].index("zenithLongitudeDeg")
            assert all(isinstance(r[la], int) and isinstance(r[lo], int) and -90 <= r[la] <= 90 and -180 <= r[lo] <= 180 for r in d["rows"])

        def _wrong_zenith(f: Files, rel: str = _rel) -> None:
            d = f.json(rel)
            d["rows"][0][d["columns"].index("zenithLatitudeDeg")] = "9N"

        claim(_rel, "zenith latitudes and longitudes become signed whole degrees")(_check_zenith).wrong(_wrong_zenith)

        def _check_durations(f: Files, rel: str = _rel) -> None:
            d = f.json(rel)
            t = d["columns"].index("eclipseType")
            pen, par, tot = (d["columns"].index(c) for c in ("penumbralDurationMinutes", "partialDurationMinutes", "totalDurationMinutes"))
            for r in d["rows"]:
                assert r[pen] is not None
                assert (r[par] is None) == (r[t] == "N") and (r[tot] is None) == (r[t] != "T")

        def _wrong_durations(f: Files, rel: str = _rel) -> None:
            d = f.json(rel)
            row = next(r for r in d["rows"] if r[d["columns"].index("eclipseType")] == "T")
            row[d["columns"].index("totalDurationMinutes")] = None

        claim(_rel, "durations the table leaves out become empty")(_check_durations).wrong(_wrong_durations)
    else:
        def _check_missing(f: Files, rel: str = _rel, src: str = _src) -> None:
            d = f.json(rel)
            w, c = d["columns"].index("pathWidthKm"), d["columns"].index("centralDurationSeconds")
            for r, line in zip(d["rows"], _eclipse_lines(src), strict=True):
                fields = line.split()
                assert (r[w] is None) == (len(fields) == 17 or fields[-2] == "-"), (r[0], fields[-2:])
                assert (r[c] is None) == (len(fields) == 17 or fields[-1] == "-"), (r[0], fields[-2:])

        def _wrong_missing(f: Files, rel: str = _rel) -> None:
            d = f.json(rel)
            row = next(r for r in d["rows"] if r[d["columns"].index("pathWidthKm")] is None)
            row[d["columns"].index("pathWidthKm")] = 0

        claim(_rel, "values the table leaves out become empty", cache=(_src,))(_check_missing).wrong(_wrong_missing)

        def _check_latlon(f: Files, rel: str = _rel) -> None:
            d = f.json(rel)
            la, lo = d["columns"].index("latitudeDeg"), d["columns"].index("longitudeDeg")
            assert all(isinstance(r[la], (int, float)) and -90 <= r[la] <= 90 and -180 <= r[lo] <= 180 for r in d["rows"])

        def _wrong_latlon(f: Files, rel: str = _rel) -> None:
            d = f.json(rel)
            d["rows"][0][d["columns"].index("latitudeDeg")] = "33.3S"

        claim(_rel, "latitudes and longitudes become signed degrees")(_check_latlon).wrong(_wrong_latlon)

# =============================================================================================
# The exoplanets (scripts/bake_exoplanets.py)
# =============================================================================================

EXO = "catalog/exoplanets/pscomppars.json"


@claim(EXO, "every planet kept", cache=(EXO_SRC,))
def _exo_rows_kept(f: Files) -> None:
    assert [r[0] for r in f.json(EXO)["rows"]] == [s["pl_name"].strip() for s in _exo_rows()]


@_exo_rows_kept.wrong
def _(f: Files) -> None:
    f.json(EXO)["rows"].pop(11)


@claim(EXO, "in 21 columns: eight about the planet, its star and its system, and thirteen values")
def _exo_columns(f: Files) -> None:
    d = f.json(EXO)
    spec = bake("bake_exoplanets").VALUE_COLUMN_SPECS
    assert d["columns"][8:] == [s.contract_name for s in spec] and len(d["columns"]) == 8 + len(spec) == 21
    assert all(isinstance(c, list) or c is None for r in d["rows"] for c in r[8:])


@_exo_columns.wrong
def _(f: Files) -> None:
    d = f.json(EXO)
    d["columns"].append("plOrbitalInclination")


def _exo_cells(d: dict[str, Any]):
    spec = bake("bake_exoplanets").VALUE_COLUMN_SPECS
    for row, source in zip(d["rows"], _exo_rows(), strict=True):
        for s, cell in zip(spec, row[8:], strict=True):
            yield s, cell, source


@claim(EXO, "a value the archive leaves blank is left empty, with nothing beside it", cache=(EXO_SRC,))
def _exo_blank(f: Files) -> None:
    for s, cell, source in _exo_cells(f.json(EXO)):
        assert (cell is None) == (not source[s.val_col].strip()), (source["pl_name"], s.contract_name)
        assert cell is None or cell[0] == float(source[s.val_col])


@_exo_blank.wrong
def _(f: Files) -> None:
    d = f.json(EXO)
    i = next(i for i, r in enumerate(d["rows"]) if r[8] is None)
    d["rows"][i][8] = [1.0, None, None, 0, 0, "measured"]


@claim(EXO, "each value with the uncertainties the archive gives", cache=(EXO_SRC,))
def _exo_errors(f: Files) -> None:
    for s, cell, source in _exo_cells(f.json(EXO)):
        if cell is not None:
            assert (cell[1], cell[2]) == (_blank_float(source[s.err1_col]), _blank_float(source[s.err2_col])), (source["pl_name"], s.contract_name)


@_exo_errors.wrong
def _(f: Files) -> None:
    d = f.json(EXO)
    cell = next(c for r in d["rows"] for c in r[8:] if c is not None and c[1] is not None)
    cell[1] = None


@claim(EXO, "its limit flag where the archive gives one", cache=(EXO_SRC,))
def _exo_limits(f: Files) -> None:
    for s, cell, source in _exo_cells(f.json(EXO)):
        if cell is not None:
            expected = None if s.lim_col is None else bake("bake_exoplanets").parse_limit_flag(source[s.lim_col])
            assert cell[3] == expected, (source["pl_name"], s.contract_name)


@_exo_limits.wrong
def _(f: Files) -> None:
    d = f.json(EXO)
    d["rows"][0][d["columns"].index("raDeg")][3] = 0


@claim(EXO, "each reference's web link is turned into a title and a secure address and listed once")
def _exo_references(f: Files) -> None:
    d = f.json(EXO)
    refs = d["references"]
    assert all(r["url"].startswith("https://") and r["label"] for r in refs)
    assert len({(r["refstr"], r["url"]) for r in refs}) == len(refs)
    used = {c[4] for r in d["rows"] for c in r[8:] if c is not None}
    assert used == set(range(len(refs)))


@_exo_references.wrong
def _(f: Files) -> None:
    d = f.json(EXO)
    d["references"].append(dict(d["references"][0]))


@claim(EXO, "each value is marked as measured, calculated or modelled from its reference")
def _exo_basis(f: Files) -> None:
    assert {c[5] for r in f.json(EXO)["rows"] for c in r[8:] if c is not None} <= {"measured", "calculated", "modelled"}


@_exo_basis.wrong
def _(f: Files) -> None:
    next(c for c in f.json(EXO)["rows"][0][8:] if c is not None)[5] = "estimated"


@claim(EXO, "a note on the equilibrium temperature is added")
def _exo_note(f: Files) -> None:
    notes = f.json(EXO)["notes"]
    assert list(notes) == ["equilibriumTemperatureK"] and "equilibrium" in notes["equilibriumTemperatureK"].lower()


@_exo_note.wrong
def _(f: Files) -> None:
    f.json(EXO)["notes"].clear()


# =============================================================================================
# The Local Bubble mesh and splats (scripts/bake_local_bubble_mesh.py, bake_local_bubble_splats.py)
# =============================================================================================

MESH = "models/local_bubble_oneill2024.glb"
SPLATS = "models/lb_splats.json"


@claim(MESH, "the directions with no distance are left out, the shell's distance in the others averaged over coarser sky "
       "cells, one point for each, in units of 100 parsecs", cache=(SHELL_SRC,))
def _mesh_points(f: Files) -> None:
    shell = _shell()
    assert int(np.sum(~np.isfinite(shell["d"]))) > 0
    expected, _ = bake("bake_local_bubble_mesh").aggregate_shell_distances(shell["l"], shell["b"], shell["d"])
    written = positions(f.bytes(MESH)).astype(np.float64)
    assert written.shape == expected.shape == (12 * 48 * 48, 3) and np.all(np.isfinite(written))
    np.testing.assert_allclose(written, expected, rtol=1e-6, atol=1e-6)


@_mesh_points.wrong
def _(f: Files) -> None:
    f.set_bytes(MESH, glb_scaled_vertex(f.bytes(MESH), 100, 1.01))


@claim(MESH, "the points are joined into a closed surface with smooth shading")
def _mesh_surface(f: Files) -> None:
    gltf, _ = glb(f.bytes(MESH))
    primitive = gltf["meshes"][0]["primitives"][0]
    indices = gltf["accessors"][primitive["indices"]]["count"]
    vertices = gltf["accessors"][primitive["attributes"]["POSITION"]]["count"]
    assert "NORMAL" in primitive["attributes"] and indices % 3 == 0
    # A closed triangulated surface: Euler's V - E + F = 2 with E = 3F / 2
    faces = indices // 3
    assert vertices - 3 * faces // 2 + faces == 2


@_mesh_surface.wrong
def _(f: Files) -> None:
    gltf, _ = glb(f.bytes(MESH))
    del gltf["meshes"][0]["primitives"][0]["attributes"]["NORMAL"]
    f.set_bytes(MESH, glb_with(gltf, f.bytes(MESH)))


@claim(SPLATS, "the table's rows with no values are left out, and for each cell of the mesh the rows in that cell are "
       "averaged into one splat, placed at the mesh point; its opacity comes from how much light the dust absorbs",
       cache=(SHELL_SRC,))
def _splat_cells(f: Files) -> None:
    splats = bake("bake_local_bubble_splats")
    shell = _shell()
    cells = splats.coarse_cell_means({**shell, "Ag": shell["Ag"]})
    rows = np.asarray(f.json(SPLATS), dtype=np.float64)
    mesh = positions(f.bytes(MESH)).astype(np.float64)
    assert rows.shape == (mesh.shape[0], 14)
    np.testing.assert_allclose(rows[:, :3], mesh, rtol=1e-6, atol=1e-6)
    np.testing.assert_allclose(rows[:, 13], np.round(1.0 - np.power(10.0, -0.4 * cells["ag_mag"]), 4), atol=1e-9)


@_splat_cells.wrong
def _(f: Files) -> None:
    f.json(SPLATS)[7][13] += 0.01


@claim(SPLATS, "its size through the shell is the shell's mean thickness along its normal, given as the spread of an even "
       "slab (the length divided by the square root of 12)", cache=(SHELL_SRC,))
def _splat_through(f: Files) -> None:
    splats = bake("bake_local_bubble_splats")
    cells = splats.coarse_cell_means(_shell())
    rows = np.asarray(f.json(SPLATS), dtype=np.float64)
    expected = np.round(cells["thick_normal_pc"] / splats.PC_PER_UNIT / math.sqrt(12.0), 4)
    np.testing.assert_allclose(rows[:, 8], expected, atol=1e-9)


@_splat_through.wrong
def _(f: Files) -> None:
    rows = f.json(SPLATS)
    rows[3][8] = round(rows[3][8] * math.sqrt(12.0), 4)


@claim(SPLATS, "its size across is the side of the cell as the same spread, so most splats are longer through the shell than across")
def _splat_across(f: Files) -> None:
    rows = np.asarray(f.json(SPLATS), dtype=np.float64)
    mesh = positions(f.bytes(MESH)).astype(np.float64)
    side = np.sqrt(4.0 * math.pi / mesh.shape[0]) * np.linalg.norm(mesh, axis=1) / math.sqrt(12.0)
    np.testing.assert_allclose(rows[:, 6], np.round(side, 4), atol=1e-9)
    assert np.array_equal(rows[:, 6], rows[:, 7]) and np.sum(rows[:, 8] > rows[:, 6]) > rows.shape[0] / 2


@_splat_across.wrong
def _(f: Files) -> None:
    for row in f.json(SPLATS):
        row[6], row[8] = row[8], row[6]


@claim(SPLATS, "its grey colour is the app's choice")
def _splat_grey(f: Files) -> None:
    grey = bake("bake_local_bubble_splats").NEUTRAL_GREY
    assert all(row[3:6] == [grey, grey, grey] for row in f.json(SPLATS))


@_splat_grey.wrong
def _(f: Files) -> None:
    f.json(SPLATS)[0][4] = 0.5


# =============================================================================================
# The Moon (scripts/bake_moon_mesh.py, bake_moon_maps.py)
# =============================================================================================

MOON = "models/nasa_lro_moon_3d.glb"
MOON_COLOUR = "textures/moon_svs4720_color_8k.jpg"
MOON_NORMAL = "textures/moon_svs4720_lola_normal_16ppd.webp"


@claim(MOON, "a sphere with a point at every degree of latitude and longitude, at the Moon's true scale")
def _moon_sphere(f: Files) -> None:
    p = positions(f.bytes(MOON)).astype(np.float64)
    assert p.shape == (181 * 361, 3)
    # True scale: 10 units are 1737.4 km (the bake's REFERENCE_RADIUS_M and UNITS_AT_REFERENCE), heights within LOLA's
    km = np.linalg.norm(p, axis=1) / 10.0 * 1737.4
    assert 1720.0 < km.min() and km.max() < 1755.0, (km.min(), km.max())


@_moon_sphere.wrong
def _(f: Files) -> None:
    f.set_bytes(MOON, glb_scaled_vertex(f.bytes(MOON), 0, 0.5))


@claim(MOON, "with texture coordinates and smooth shading")
def _moon_attributes(f: Files) -> None:
    gltf, _ = glb(f.bytes(MOON))
    assert {"POSITION", "NORMAL", "TEXCOORD_0"} <= set(gltf["meshes"][0]["primitives"][0]["attributes"])


@_moon_attributes.wrong
def _(f: Files) -> None:
    gltf, _ = glb(f.bytes(MOON))
    del gltf["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"]
    f.set_bytes(MOON, glb_with(gltf, f.bytes(MOON)))


@claim(MOON_COLOUR, "reduced from 16 to 8 bits a colour and saved as JPEG")
def _moon_colour(f: Files) -> None:
    im = image(f.bytes(MOON_COLOUR))
    assert im.format == "JPEG" and im.mode == "RGB" and im.size == (8192, 4096)


@_moon_colour.wrong
def _(f: Files) -> None:
    f.set_bytes(MOON_COLOUR, tiny("PNG"))


@claim(MOON_NORMAL, "stored as colours in a lossless image")
def _moon_normal(f: Files) -> None:
    data = f.bytes(MOON_NORMAL)
    assert image(data).format == "WEBP" and webp_chunk(data) == "VP8L" and image(data).mode == "RGB"


@_moon_normal.wrong
def _(f: Files) -> None:
    f.set_bytes(MOON_NORMAL, tiny("WEBP", lossless=False, quality=80))


# =============================================================================================
# The Earth (scripts/bake_earth_maps.py, scripts/lookdev/bake_earth_textures.py)
# =============================================================================================

DAY = "textures/earth_day_bluemarble_2004-03_base_8k.jpg"
HEIGHT = "textures/earth_height_etopo2022_4k.png"
WATER = "textures/earth_water_natural_earth_4k.png"
NIGHT = "textures/earth_night_blackmarble_2016_8k.jpg"


@claim(DAY, "resized and saved as JPEG")
def _day(f: Files) -> None:
    im = image(f.bytes(DAY))
    # The source is NASA's 21,600 by 10,800 pixel image (scripts/bake_earth_maps.py, its record's source)
    assert im.format == "JPEG" and im.size == (8192, 4096)


@_day.wrong
def _(f: Files) -> None:
    f.set_bytes(DAY, tiny("PNG"))


@lru_cache(maxsize=None)
def _array(data: bytes) -> np.ndarray:
    return np.asarray(image(data))


@claim(HEIGHT, "each pixel holds a height to the nearest metre, stored exactly across two colour channels")
def _height(f: Files) -> None:
    a = _array(f.bytes(HEIGHT))
    assert a.ndim == 3 and a.shape[2] == 3 and not a[..., 2].any()
    metres = a[..., 0].astype(np.int64) * 256 + a[..., 1] - 11000
    assert -11000 < metres.min() and metres.max() < 9000


@_height.wrong
def _(f: Files) -> None:
    a = _array(f.bytes(HEIGHT)).copy()
    a[5, 5, 2] = 1
    buffer = io.BytesIO()
    Image.fromarray(a, "RGB").save(buffer, "PNG")
    f.set_bytes(HEIGHT, buffer.getvalue())


@claim(WATER, "each pixel holds how much of it lies in the ocean or in a lake, as a shade from 0 for land to 255 for water")
def _water(f: Files) -> None:
    im = image(f.bytes(WATER))
    a = _array(f.bytes(WATER))
    assert im.mode == "L" and a.min() == 0 and a.max() == 255 and ((a > 0) & (a < 255)).any()


@_water.wrong
def _(f: Files) -> None:
    buffer = io.BytesIO()
    Image.new("L", (16, 8), 0).save(buffer, "PNG")
    f.set_bytes(WATER, buffer.getvalue())


@claim(NIGHT, "the faint base layer turns black, the brightest city lights stay as they are; saved as JPEG")
def _night(f: Files) -> None:
    im = image(f.bytes(NIGHT))
    lum = np.asarray(im.convert("L"))
    assert im.format == "JPEG" and np.mean(lum <= 2) > 0.5 and lum.max() == 255


@_night.wrong
def _(f: Files) -> None:
    f.set_bytes(NIGHT, tiny("JPEG"))


# =============================================================================================
# The stars (scripts/bake_bright_stars_3d.py, bake_star_designations.py, bake_catalog_star_tiles.py)
# =============================================================================================

BRIGHT = "catalog/stars/hipparcos-bright-3d/stars.json"
DESIGNATIONS = "catalog/stars/designations/designations.json"
LINKS = "catalog/stars/designation-links/links.json"
MERGE = "catalog/stars/gaia-dr3/bright-star-merge.json"
NAMES = "catalog/stars/gaia-dr3/names.json"
TILES = "catalog/stars/gaia-dr3/SHA256SUMS"


def _col(d: dict[str, Any], name: str) -> int:
    return d["columns"].index(name)


@claim(BRIGHT, "the bright stars the star tiles take from Hipparcos", cache=(BRIGHT_SRC,))
def _bright_rows(f: Files) -> None:
    d = f.json(BRIGHT)
    use_hip = sorted(x["hip"] for x in _json(MERGE)["decisions"] if x["action"] == "use_hipparcos")
    assert [r[0] for r in d["rows"]] == use_hip == sorted(_archive())


@_bright_rows.wrong
def _(f: Files) -> None:
    f.json(BRIGHT)["rows"].pop(2)


@claim(BRIGHT, "with the V magnitude of the 1997 catalogue where it gives one", cache=(BRIGHT_SRC,))
def _bright_v(f: Files) -> None:
    d = f.json(BRIGHT)
    for r in d["rows"]:
        assert r[_col(d, "vMag")] == _blank_float(_archive()[r[0]]["vmag"]), r[0]


@_bright_v.wrong
def _(f: Files) -> None:
    d = f.json(BRIGHT)
    d["rows"][4][_col(d, "vMag")] += 0.01


@claim(BRIGHT, "the positions are left at the catalogue's epoch and no distance is worked out")
def _bright_epoch(f: Files) -> None:
    d = f.json(BRIGHT)
    assert d["epochJulianYear"] == 1991.25 and not [c for c in d["columns"] if re.search(r"(?i)dist|parsec|pc$", c)]


@_bright_epoch.wrong
def _(f: Files) -> None:
    d = f.json(BRIGHT)
    d["columns"].append("distancePc")


@claim(BRIGHT, "its Gaia counterpart and its name only where it has one")
def _bright_counterpart(f: Files) -> None:
    d = f.json(BRIGHT)
    decisions = {x["hip"]: x for x in _json(MERGE)["decisions"] if x["action"] == "use_hipparcos"}
    names = {n["hip"]: n["name"] for n in _json(NAMES)["names"] if n.get("hip") is not None}
    for r in d["rows"]:
        assert r[_col(d, "gaiaSourceId")] == decisions[r[0]]["gaiaSourceId"], r[0]
        assert r[_col(d, "name")] == names.get(r[0]), r[0]
    assert any(r[_col(d, "gaiaSourceId")] is None for r in d["rows"]) and any(r[_col(d, "name")] is None for r in d["rows"])


@_bright_counterpart.wrong
def _(f: Files) -> None:
    d = f.json(BRIGHT)
    next(r for r in d["rows"] if r[_col(d, "gaiaSourceId")] is None)[_col(d, "gaiaSourceId")] = "1234567890"


@claim(BRIGHT, "each star carries the reason the tiles use Hipparcos for it and its place in the tiles")
def _bright_reason(f: Files) -> None:
    d = f.json(BRIGHT)
    decisions = {x["hip"]: x for x in _json(MERGE)["decisions"] if x["action"] == "use_hipparcos"}
    tiles = f.tiles()
    hip_rows = {int(s): i for i, s in enumerate(tiles["sid"]) if tiles["hip"][i]}
    for r in d["rows"]:
        assert r[_col(d, "hipparcosReason")] == decisions[r[0]]["reason"], r[0]
        assert r[0] in hip_rows and None not in r[_col(d, "tileLayer") : _col(d, "tileIndex") + 1]


@_bright_reason.wrong
def _(f: Files) -> None:
    d = f.json(BRIGHT)
    d["rows"][0][_col(d, "hipparcosReason")] = "a reason nobody gave"


@claim(BRIGHT, "three columns the archive gives (the error of B-V, a multiplicity flag and a CCDM number) are left out",
       cache=(BRIGHT_SRC,))
def _bright_left_out(f: Files) -> None:
    d = f.json(BRIGHT)
    archive_columns = set(next(iter(_archive().values())))
    assert {"e_b_v", "multflag", "ccdm"} <= archive_columns
    assert not [c for c in d["columns"] if re.search(r"(?i)e_?b_?v|bminusverror|mult|ccdm", c)]
    assert len(d["columns"]) == len(set(d["columns"])) == 22


@_bright_left_out.wrong
def _(f: Files) -> None:
    d = f.json(BRIGHT)
    d["columns"].append("bMinusVError")


@claim(DESIGNATIONS, "the rows of the HYG catalogue that carry a Bayer or Flamsteed designation and a Hipparcos number, each "
       "with eight of its columns as HYG gives them", cache=(HYG_SRC,))
def _designation_rows(f: Files) -> None:
    d = f.json(DESIGNATIONS)
    kept, _ = _hyg()
    assert [r[0] for r in d["rows"]] == sorted(x.hip for x in kept) and len(d["columns"]) == 8
    by_hip = {x.hip: x for x in kept}
    for r in d["rows"]:
        x = by_hip[r[0]]
        assert (r[1], r[2], r[3], r[4], r[5], r[6], r[7]) == (
            x.flamsteed, x.bayer, x.constellation, x.hd, x.hr, x.hyg_id, x.v_mag
        ), r[0]


@_designation_rows.wrong
def _(f: Files) -> None:
    d = f.json(DESIGNATIONS)
    d["rows"][0][3] = "Xxx"


@claim(DESIGNATIONS, "each with eight of its columns as HYG gives them (wrong copy: a Bayer letter changed)", cache=(HYG_SRC,))
def _designation_bayer(f: Files) -> None:
    _designation_rows(f)


@_designation_bayer.wrong
def _(f: Files) -> None:
    row = next(r for r in f.json(DESIGNATIONS)["rows"] if r[2])
    row[2] = "Alp" if not row[2].startswith("Alp") else "Bet"


@claim(DESIGNATIONS, "the rows with no Hipparcos number, and one whose combined designation disagrees with its own parts, are "
       "listed apart with their reason", cache=(HYG_SRC,))
def _designation_aside(f: Files) -> None:
    d = f.json(DESIGNATIONS)
    _, aside = _hyg()
    assert sorted(tuple(r) for r in d["setAside"]) == sorted(tuple(a) for a in aside)
    assert all(r[2] for r in d["setAside"]) and Counter(r[2] for r in d["setAside"]) == Counter(d["counts"]["setAside"])


@_designation_aside.wrong
def _(f: Files) -> None:
    f.json(DESIGNATIONS)["setAside"].pop()


@claim(LINKS, "for each star of the designations file, the place in the star tiles of its own Hipparcos entry, or else of the "
       "Gaia star the early third release pairs with it", cache=(NEIGHBOUR_SRC,))
def _links_place(f: Files) -> None:
    d = f.json(LINKS)
    best = _best()
    hipparcos, gaia = _tile_rows(frozenset(best.values()))
    for r in d["rows"]:
        if r[6] == "hipparcos-row":
            row = hipparcos.get(r[5])
            assert r[0] == "hipparcos-2007" and int(r[1]) == r[5] and row is not None, r
        else:
            row = gaia.get(int(r[1]))
            assert r[6] == "gaia-best-neighbour" and int(r[1]) == best[r[5]] and row is not None, r
        assert (row.layer, row.pix, row.index) == (r[2], r[3], r[4]), r


@_links_place.wrong
def _(f: Files) -> None:
    d = f.json(LINKS)
    row = next(r for r in d["rows"] if r[6] == "gaia-best-neighbour")
    row[1] = str(int(row[1]) + 1)


@claim(LINKS, "kept only when that Gaia star holds at least half the star's light", cache=(HYG_SRC, NEIGHBOUR_SRC))
def _links_half(f: Files) -> None:
    designations = bake("bake_star_designations")
    d = f.json(LINKS)
    v = {x.hip: x.v_mag for x in _hyg()[0]}
    gaia = _gaia_tile_rows()
    checked = 0
    for r in d["rows"]:
        if r[6] == "gaia-best-neighbour":
            row = gaia[int(r[1])]
            v_row = designations.v_from_gaia(row.mag, row.bp_rp)
            assert v_row is not None and v_row - v[r[5]] <= designations.HALF_LIGHT_MAG, r
            checked += 1
    assert checked > 0


@_links_half.wrong
def _(f: Files) -> None:
    d = f.json(LINKS)
    hip = next(a[0] for a in d["setAside"] if a[1] == "fainter-than-half-light")
    gid = _best()[hip]
    row = _gaia_tile_rows()[gid]
    d["rows"].append(["gaia-dr3", str(gid), row.layer, row.pix, row.index, hip, "gaia-best-neighbour"])


@claim(LINKS, "the stars that find no place are listed apart, each with its reason")
def _links_aside(f: Files) -> None:
    d = f.json(LINKS)
    linked = {r[5] for r in d["rows"]}
    assert all(a[1] for a in d["setAside"]) and not linked & {a[0] for a in d["setAside"]}
    assert linked | {a[0] for a in d["setAside"]} == {r[0] for r in _json(DESIGNATIONS)["rows"]}


@_links_aside.wrong
def _(f: Files) -> None:
    f.json(LINKS)["setAside"].pop(0)


@claim(LINKS, "the HYG rows the designations file sets aside are in neither list")
def _links_neither(f: Files) -> None:
    d = f.json(LINKS)
    aside = {r[1] for r in _json(DESIGNATIONS)["setAside"] if r[1] is not None}
    assert aside and not aside & ({r[5] for r in d["rows"]} | {a[0] for a in d["setAside"]})


@_links_neither.wrong
def _(f: Files) -> None:
    hip = next(r[1] for r in _json(DESIGNATIONS)["setAside"] if r[1] is not None)
    f.json(LINKS)["setAside"].append([hip, "no-best-neighbour"])


def _bright_parallax() -> dict[int, float]:
    d = _json(BRIGHT)
    return {r[0]: r[_col(d, "parallaxMas")] for r in d["rows"]}


@claim(TILES, "the brightest stars taken from Hipparcos instead, their distances worked out from their parallaxes where the "
       "parallax is above zero; a value a source lacks stays missing")
def _tiles_hipparcos(f: Files) -> None:
    tiles = f.tiles()
    plx = _bright_parallax()
    hip = np.flatnonzero(tiles["hip"])
    assert sorted(int(tiles["sid"][i]) for i in hip) == sorted(plx)
    for i in hip:
        has = int(tiles["dist_kind"][i]) != 0
        assert has == (plx[int(tiles["sid"][i])] > 0), int(tiles["sid"][i])
        assert has == bool(np.isfinite(tiles["med_pc"][i])), int(tiles["sid"][i])
        if has:
            assert abs(tiles["med_pc"][i] - 1000.0 / plx[int(tiles["sid"][i])]) <= 1e-3 * tiles["med_pc"][i]


@_tiles_hipparcos.wrong
def _(f: Files) -> None:
    tiles = f.tiles()
    i = next(i for i in np.flatnonzero(tiles["hip"]) if _bright_parallax()[int(tiles["sid"][i])] <= 0)
    kinds = tiles["dist_kind"].copy()
    kinds[i] = 3
    med = tiles["med_pc"].copy()
    med[i] = 100.0
    tiles["dist_kind"], tiles["med_pc"] = kinds, med


@claim(TILES, "every Gaia star brighter than magnitude 12")
def _tiles_gaia(f: Files) -> None:
    tiles = f.tiles()
    gaia = ~tiles["hip"].astype(bool)
    manifest = f.json("catalog/stars/gaia-dr3/manifest.json")
    assert gaia.any() and float(np.max(tiles["mag"][gaia])) < 12.0
    assert manifest["magnitudeLimit"] == 12.0 and all(layer["magMax"] <= 12.0 for layer in manifest["layers"])


@_tiles_gaia.wrong
def _(f: Files) -> None:
    f.json("catalog/stars/gaia-dr3/manifest.json")["layers"][-1]["magMax"] = 12.5


@claim(TILES, "the Gaia entries the Hipparcos stars replace dropped")
def _tiles_dropped(f: Files) -> None:
    dropped = frozenset(int(x["gaiaSourceId"]) for x in f.json(MERGE)["decisions"] if x["action"] == "drop_gaia")
    assert dropped and not _tile_rows(dropped)[1]
    assert f.json("catalog/stars/gaia-dr3/manifest.json")["counts"]["gaiaDropped"] == len(dropped)


@_tiles_dropped.wrong
def _(f: Files) -> None:
    # A Gaia entry kept in the tiles, written into the merge record as dropped
    kept = next(x for x in f.json(MERGE)["decisions"] if x["action"] == "keep_gaia")
    kept["action"] = "drop_gaia"


@claim(TILES, "with its distance estimate (the photogeometric one where there is one, else the geometric one)")
def _tiles_distance_kind(f: Files) -> None:
    from services.science import catalog_tiles as ct

    tiles = f.tiles()
    gaia = ~tiles["hip"].astype(bool)
    kinds = Counter(int(k) for k in tiles["dist_kind"][gaia])
    assert set(kinds) <= {ct.DIST_KIND_BJ_PHOTOGEO, ct.DIST_KIND_BJ_GEO, ct.DIST_KIND_NONE}, kinds
    assert kinds[ct.DIST_KIND_BJ_PHOTOGEO] > kinds[ct.DIST_KIND_BJ_GEO] > 0
    # A Hipparcos star's distance is never one of Bailer-Jones's
    assert set(int(k) for k in tiles["dist_kind"][~gaia]) <= {ct.DIST_KIND_PARALLAX_INVERSE, ct.DIST_KIND_NONE}


@_tiles_distance_kind.wrong
def _(f: Files) -> None:
    tiles = f.tiles()
    kinds = tiles["dist_kind"].copy()
    kinds[int(np.flatnonzero(~tiles["hip"].astype(bool))[0])] = 3
    tiles["dist_kind"] = kinds


@claim(TILES, "star names added")
def _tiles_names(f: Files) -> None:
    names = f.json(NAMES)["names"]
    wanted = frozenset(int(n["catalogId"]) for n in names if n["catalog"] == "gaia-dr3")
    hipparcos, gaia = _tile_rows(wanted)
    assert names
    for n in names:
        row = hipparcos.get(int(n["catalogId"])) if n["catalog"] == "hipparcos-2007" else gaia.get(int(n["catalogId"]))
        assert row is not None and (row.layer, row.pix, row.index) == (n["layer"], n["pix"], n["index"]), n["name"]


@_tiles_names.wrong
def _(f: Files) -> None:
    f.json(NAMES)["names"][0]["index"] += 1


# =============================================================================================
# The sky panorama (scripts/bake_starless_panorama.py, scripts/register_sky_panorama.py)
# =============================================================================================

STARLESS = "textures/eso_milkyway_diffuse_6k.webp"
PHOTO = "textures/eso_milkyway_panorama.jpg"
REGISTRATION = "textures/eso0932a_registration.json"


@claim(STARLESS, "saved as a lossless image of the same size")
def _starless(f: Files) -> None:
    data = f.bytes(STARLESS)
    assert webp_chunk(data) == "VP8L" and image(data).size == image(f.bytes(PHOTO)).size


@_starless.wrong
def _(f: Files) -> None:
    f.set_bytes(STARLESS, tiny("WEBP", lossless=False, quality=80))


@claim(STARLESS, "every catalogue star down to magnitude 7 is masked")
def _starless_mask(f: Files) -> None:
    record = f.json("textures/PROVENANCE.json")
    note = next(e for e in record["files"] if e["path"] == "eso_milkyway_diffuse_6k.webp")["note"]
    found = re.search(r"catalog stars to magnitude (\d+(?:\.\d+)?)", note)
    assert found and float(found.group(1)) == bake("bake_starless_panorama").MASK_MAG == 7.0


@_starless_mask.wrong
def _(f: Files) -> None:
    entry = next(e for e in f.json("textures/PROVENANCE.json")["files"] if e["path"] == "eso_milkyway_diffuse_6k.webp")
    entry["note"] = entry["note"].replace("catalog stars to magnitude 7", "catalog stars to magnitude 6")


@claim(REGISTRATION, "worked out from the panorama, with the photograph itself left untouched")
def _registration_photo(f: Files) -> None:
    d = f.json(REGISTRATION)
    assert d["image"]["sha256"] == hashlib.sha256(f.bytes(PHOTO)).hexdigest()
    assert image(f.bytes(PHOTO)).size == (d["image"]["width"], d["image"]["height"])


@_registration_photo.wrong
def _(f: Files) -> None:
    f.set_bytes(PHOTO, f.bytes(PHOTO)[:-1] + b"\x00")


@claim(REGISTRATION, "a list of bright named stars, copied from the catalogue, with where the fit puts each and where it was found")
def _registration_names(f: Files) -> None:
    by_name = {n["name"]: n for n in _json(NAMES)["names"]}
    stars = f.json(REGISTRATION)["checkStars"]
    assert stars
    for s in stars:
        n = by_name[s["name"]]
        assert (s["raDeg"], s["decDeg"]) == (n["raDeg"], n["decDeg"]), s["name"]
        assert all(isinstance(s[k], float) for k in ("modelX", "modelY", "measuredX", "measuredY"))


@_registration_names.wrong
def _(f: Files) -> None:
    f.json(REGISTRATION)["checkStars"][0]["raDeg"] += 0.5


@claim(REGISTRATION, "checked on stars held back from the fit")
def _registration_held(f: Files) -> None:
    fit = f.json(REGISTRATION)["fit"]
    assert fit.get("heldOut") and fit.get("inSample") and fit["heldOut"] != fit["inSample"]


@_registration_held.wrong
def _(f: Files) -> None:
    del f.json(REGISTRATION)["fit"]["heldOut"]


# =============================================================================================
# Unicode (scripts/generate_letters_and_digits.py)
# =============================================================================================

NOTICE = "notices/unicode/UNICODE-LICENSE.txt"
LETTERS = "notices/unicode/letters-and-digits.json"


@claim(NOTICE, "the notice is the Unicode licence's own text, written unchanged (equal to the script's copy of it; that copy "
       "against Unicode's page is not counted here)")
def _notice(f: Files) -> None:
    own = re.search(r'UNICODE_LICENSE = """(.*?)"""', (ROOT / "scripts" / "generate_letters_and_digits.py").read_text(encoding="utf-8"), re.S)
    assert own and f.bytes(NOTICE).decode("utf-8") == own.group(1)


@_notice.wrong
def _(f: Files) -> None:
    f.set_bytes(NOTICE, f.bytes(NOTICE).replace(b"UNICODE LICENSE V3", b"UNICODE LICENSE V2", 1))


@claim(LETTERS, "the letters and digits of Unicode 15.1, less the characters that never show, as ranges", cache=(UCD_DATA, UCD_CORE))
def _letters(f: Files) -> None:
    d = f.json(LETTERS)
    categories = {}
    first = None
    for line in (ROOT / UCD_DATA).read_text(encoding="utf-8").splitlines():
        fields = line.split(";")
        cp, name, category = int(fields[0], 16), fields[1], fields[2]
        if name.endswith(", First>"):
            first = cp
            continue
        span = range(first, cp + 1) if name.endswith(", Last>") else range(cp, cp + 1)
        first = None
        if category[0] in "LN":
            categories.update(dict.fromkeys(span, category))
    ignorable = set()
    for line in (ROOT / UCD_CORE).read_text(encoding="utf-8").splitlines():
        body = line.split("#")[0].strip()
        if body and body.split(";")[1].strip() == "Default_Ignorable_Code_Point":
            low, _, high = body.split(";")[0].strip().partition("..")
            ignorable.update(range(int(low, 16), int(high or low, 16) + 1))
    expected = sorted(set(categories) - ignorable)
    written = [cp for a, b in d["ranges"] for cp in range(a, b + 1)]
    assert d["unicodeVersion"] == "15.1.0" and written == expected and d["count"] == len(expected)


@_letters.wrong
def _(f: Files) -> None:
    d = f.json(LETTERS)
    a, b = d["ranges"][0]
    d["ranges"][0] = [a, b - 1]


@claim(LETTERS, "with the places where three other versions differ")
def _letters_differ(f: Files) -> None:
    d = f.json(LETTERS)
    assert sorted(d["differsFrom"]) == ["15.0.0", "16.0.0", "17.0.0"] and all(d["differsFrom"].values())


@_letters_differ.wrong
def _(f: Files) -> None:
    f.json(LETTERS)["differsFrom"].pop("17.0.0")


# =============================================================================================
# The rows, and that every script-made file has some
# =============================================================================================


def _params() -> list[Any]:
    return [
        pytest.param(c, id=f"[{c.kind}] {c.file} :: {c.words[:60]}", marks=[pytest.mark.local_dataset(*c.cache)] if c.cache else [])
        for c in CLAIMS
    ]


@pytest.mark.parametrize("row", _params())
def test_each_claim_holds_in_its_committed_file(row: Claim) -> None:
    row.check(Files())


@pytest.mark.parametrize("row", _params())
def test_each_claim_fails_on_its_wrong_file(row: Claim) -> None:
    files = Files()
    row.wrong(files)
    with pytest.raises(AssertionError):
        row.check(files)


def test_every_script_made_file_has_its_claims_here() -> None:
    made = set()
    for record in sorted(PUBLIC.rglob("PROVENANCE.json")):
        data = json.loads(record.read_text(encoding="utf-8"))
        for entry in data["files"] + data["fileSets"]:
            if entry["producedBy"]["method"] == "script":
                made.add(str((record.parent / (entry.get("path") or entry["checksums"])).relative_to(PUBLIC)))
    assert made == {c.file for c in CLAIMS} and len(made) == 24
