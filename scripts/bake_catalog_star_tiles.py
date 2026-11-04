#!/usr/bin/env python3
"""Bake Albedian's catalog star tiles from Gaia DR3, Bailer-Jones (2021) and Hipparcos 2007.

Pulls every Gaia DR3 source brighter than the release's magnitude limit (G 12 for
`gaia-dr3`) with Bailer-Jones distance medians and bounds, BP-RP, proper motion and radial
velocity, swaps in Hipparcos 2007 entries where Gaia handles bright stars poorly, attaches
IAU WGSN proper names by HIP number, and writes HEALPix tiles layered by magnitude under
`apps/web/public/catalog/stars/<release>/` with a manifest of releases and citations. Astro
tiles beside the core and detail tiles carry DR3 GSP-Phot temperature and extinction, FLAME
luminosity (each with its 16th and 84th percentiles), RUWE and the parallax error; a value DR3
lacks stays missing.

Everything this script writes is catalog data (`evidenceKind: catalog-derived`). None of
it is measured from a user's recording.

Usage:
    uv run python scripts/bake_catalog_star_tiles.py              # fetch into the cache and bake
    uv run python scripts/bake_catalog_star_tiles.py --offline    # bake from the cache only
    uv run python scripts/bake_catalog_star_tiles.py --verify     # rebake from the cache and compare
                                                                  # against the committed SHA256SUMS
    uv run python scripts/bake_catalog_star_tiles.py --fetch-only # fill the cache, do not bake

Archive responses are cached under `.cache/catalog-star-bake/<release>/` so a rebake is
deterministic and offline. Releases are pinned in `RELEASES`; adding Gaia DR4 means adding
an entry there (new tables, citations, release id) and pointing the web client's
`PINNED_STAR_CATALOG_RELEASE` at the new folder once it is baked and reviewed.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.contracts.provenance import (  # noqa: E402
    ASSET_PROVENANCE_RECORD_FILENAME,
    AssetProvenanceRecord,
    dumps_record,
)
from services.science import catalog_tiles as ct  # noqa: E402

DEFAULT_OUT_DIR = ROOT_DIR / "apps" / "web" / "public" / "catalog" / "stars"
DEFAULT_CACHE_DIR = ROOT_DIR / ".cache" / "catalog-star-bake"
GAIA_TAP_SYNC = "https://gea.esac.esa.int/tap-server/tap/sync"
# Every archive request names the project and nothing personal
USER_AGENT = "Albedian/0.1 (research prototype)"
# The Gaia archive truncates synchronous results at 50,000 rows without an error, so every
# chunk is planned below this size from an exact count query and checked against it.
CHUNK_ROW_TARGET = 45_000
FETCH_WORKERS = 4
MANIFEST_FORMAT = "albedian.catalog-star-tiles"
# Manifest format 2 lists astro tiles; every tile header stays at ct.TILE_FORMAT_VERSION (1)
MANIFEST_FORMAT_VERSION = 2

ESA_ARCHIVE_TERMS = (
    "Data hosted in the ESA Space Science Archives are distributed under the CC BY-NC 3.0 IGO "
    "licence. Commercial use needs prior authorisation from ESA (data.licences@esa.int)."
)
ESA_TERMS_URL = "https://www.cosmos.esa.int/web/esdc/terms-and-conditions"


@dataclass(frozen=True)
class LayerSpec:
    layer: int
    mag_min: float | None
    mag_max: float
    order: int

    @property
    def position_bits(self) -> int:
        # 16-bit sub-pixel offsets keep positions within 0.5 arcsec from order 3 down
        return 16 if self.order >= 3 else 32


@dataclass(frozen=True)
class ReleaseConfig:
    release_id: str
    title: str
    tap_sync: str
    gaia_table: str
    distance_table: str
    astro_table: str
    hipparcos_table: str
    best_neighbour_table: str
    epoch: float
    hipparcos_epoch: float
    mag_limit: float
    hipparcos_mag_limit: float
    gaia_poor_brighter_than: float
    duplicate_radius_arcsec: float
    duplicate_mag_limit: float
    star_names_url: str
    layers: tuple[LayerSpec, ...]
    sources: tuple[dict, ...]


GAIA_ACKNOWLEDGEMENT = (
    "This work has made use of data from the European Space Agency (ESA) mission Gaia "
    "(https://www.cosmos.esa.int/gaia), processed by the Gaia Data Processing and Analysis "
    "Consortium (DPAC, https://www.cosmos.esa.int/web/gaia/dpac/consortium). Funding for the "
    "DPAC has been provided by national institutions, in particular the institutions "
    "participating in the Gaia Multilateral Agreement."
)

RELEASES: dict[str, ReleaseConfig] = {
    "gaia-dr3": ReleaseConfig(
        release_id="gaia-dr3",
        title=(
            "Gaia DR3 stars to G 12 with Bailer-Jones (2021) distances and DR3 temperatures, luminosities "
            "and extinction; bright end from Hipparcos 2007"
        ),
        tap_sync=GAIA_TAP_SYNC,
        gaia_table="gaiadr3.gaia_source",
        distance_table="external.gaiaedr3_distance",
        astro_table="gaiadr3.astrophysical_parameters",
        hipparcos_table="public.hipparcos_newreduction",
        best_neighbour_table="gaiadr3.hipparcos2_best_neighbour",
        epoch=2016.0,
        hipparcos_epoch=1991.25,
        mag_limit=12.0,
        hipparcos_mag_limit=6.0,
        gaia_poor_brighter_than=3.0,
        duplicate_radius_arcsec=10.0,
        duplicate_mag_limit=7.5,
        star_names_url="https://www.pas.rochester.edu/~emamajek/WGSN/IAU-CSN.txt",
        layers=(
            LayerSpec(0, None, 6.5, 0),
            LayerSpec(1, 6.5, 8.0, 1),
            LayerSpec(2, 8.0, 9.5, 2),
            LayerSpec(3, 9.5, 10.5, 3),
            LayerSpec(4, 10.5, 11.25, 3),
            LayerSpec(5, 11.25, 12.0, 3),
        ),
        sources=(
            {
                "id": "gaia-dr3",
                "title": "Gaia Data Release 3",
                "provider": "ESA Gaia mission, Gaia DPAC",
                "table": "gaiadr3.gaia_source",
                "archive": "https://gea.esac.esa.int/archive/",
                "citation": "Gaia Collaboration, Vallenari, A., et al. 2023, A&A 674, A1",
                "doi": "10.1051/0004-6361/202243940",
                "missionCitation": "Gaia Collaboration, Prusti, T., et al. 2016, A&A 595, A1",
                "missionDoi": "10.1051/0004-6361/201629272",
                "acknowledgement": GAIA_ACKNOWLEDGEMENT,
                "license": ESA_ARCHIVE_TERMS,
                "licenseUrl": "https://www.cosmos.esa.int/web/gaia-users/license",
                "fields": [
                    "source_id",
                    "ra",
                    "dec",
                    "phot_g_mean_mag",
                    "bp_rp",
                    "pmra",
                    "pmdec",
                    "radial_velocity",
                    "teff_gspphot",
                    "teff_gspphot_lower",
                    "teff_gspphot_upper",
                    "azero_gspphot",
                    "azero_gspphot_lower",
                    "azero_gspphot_upper",
                    "ag_gspphot",
                    "ruwe",
                    "parallax_error",
                ],
                "note": (
                    "Temperature and extinction are GSP-Phot results (Andrae et al., "
                    "doi:10.1051/0004-6361/202243462): teff_gspphot and azero_gspphot (A0 at 547.7 nm) with their "
                    "16th and 84th percentiles, and ag_gspphot (extinction in G)."
                ),
            },
            {
                "id": "gaia-dr3-astrophysical-parameters",
                "title": "Gaia DR3 astrophysical parameters: FLAME luminosity",
                "provider": "ESA Gaia mission, Gaia DPAC (Apsis)",
                "table": "gaiadr3.astrophysical_parameters",
                "archive": "https://gea.esac.esa.int/archive/",
                "citation": (
                    "Creevey, O. L., et al., Gaia Data Release 3: Astrophysical parameters inference system (Apsis) I, "
                    "methods and content overview; Fouesneau, M., et al., Gaia Data Release 3: Apsis II, stellar parameters"
                ),
                "doi": "10.1051/0004-6361/202243688",
                "acknowledgement": GAIA_ACKNOWLEDGEMENT,
                "license": ESA_ARCHIVE_TERMS,
                "licenseUrl": "https://www.cosmos.esa.int/web/gaia-users/license",
                "fields": ["lum_flame", "lum_flame_lower", "lum_flame_upper"],
                "note": (
                    "FLAME luminosity from the G magnitude, the GSP-Phot extinction in G and the parallax or distance, "
                    "with its 16th and 84th percentiles. Apsis II: doi:10.1051/0004-6361/202243919."
                ),
            },
            {
                "id": "bailer-jones-2021",
                "title": (
                    "Estimating Distances from Parallaxes. V. Geometric and Photogeometric Distances "
                    "to 1.47 Billion Stars in Gaia Early Data Release 3"
                ),
                "provider": "Bailer-Jones et al., retrieved from the ESA Gaia Archive",
                "table": "external.gaiaedr3_distance",
                "archive": "https://gea.esac.esa.int/archive/",
                "citation": (
                    "Bailer-Jones, C. A. L., Rybizki, J., Fouesneau, M., Demleitner, M., & Andrae, R. "
                    "2021, AJ 161, 147"
                ),
                "doi": "10.3847/1538-3881/abd806",
                "license": "Retrieved from the ESA Gaia Archive; " + ESA_ARCHIVE_TERMS,
                "licenseUrl": ESA_TERMS_URL,
                "fields": [
                    "r_med_photogeo",
                    "r_lo_photogeo",
                    "r_hi_photogeo",
                    "r_med_geo",
                    "r_lo_geo",
                    "r_hi_geo",
                ],
                "note": (
                    "Photogeometric median and 16th/84th percentile bounds are used when present, "
                    "geometric otherwise. Gaia DR3 astrometry is identical to EDR3, so source_id joins directly."
                ),
            },
            {
                "id": "hipparcos-2007",
                "title": "Hipparcos, the New Reduction of the Raw Data",
                "provider": "ESA Hipparcos mission, retrieved from the ESA Gaia Archive",
                "table": "public.hipparcos_newreduction",
                "archive": "https://gea.esac.esa.int/archive/",
                "citation": "van Leeuwen, F. 2007, A&A 474, 653",
                "doi": "10.1051/0004-6361:20078357",
                "license": ESA_ARCHIVE_TERMS,
                "licenseUrl": ESA_TERMS_URL,
                "fields": ["hip", "ra", "dec", "plx", "e_plx", "pm_ra", "pm_de", "hp_mag", "b_v"],
                "note": (
                    "Used for stars Gaia DR3 handles poorly. Positions propagated from J1991.25 to J2016.0 "
                    "with Hipparcos proper motions; distance is 1000/parallax with bounds from parallax "
                    "plus or minus its formal error; magnitude is Hp and colour is B-V."
                ),
            },
            {
                "id": "gaia-dr3-hipparcos2-best-neighbour",
                "title": "Gaia DR3 to Hipparcos-2 best-neighbour crossmatch",
                "provider": "Gaia DPAC",
                "table": "gaiadr3.hipparcos2_best_neighbour",
                "archive": "https://gea.esac.esa.int/archive/",
                "citation": "Marrese, P. M., Marinoni, S., Fabrizio, M., & Altavilla, G. 2019, A&A 621, A144",
                "doi": "10.1051/0004-6361/201834142",
                "license": ESA_ARCHIVE_TERMS,
                "licenseUrl": ESA_TERMS_URL,
                "fields": ["source_id", "original_ext_source_id"],
            },
            {
                "id": "iau-csn",
                "title": "IAU Catalog of Star Names (IAU-CSN)",
                "provider": "IAU Division C Working Group on Star Names (WGSN)",
                "url": "https://www.pas.rochester.edu/~emamajek/WGSN/IAU-CSN.txt",
                "officialUrl": "https://www.iau.org/public/themes/naming_stars/",
                "citation": "IAU Working Group on Star Names, IAU Catalog of Star Names",
                "license": "IAU products are released under Creative Commons Attribution; cite the IAU.",
                "note": "Names attach to catalog stars by HIP number, through the Gaia best-neighbour crossmatch for Gaia sources.",
            },
        ),
    ),
}


# =============================================================================================
# Archive access with a deterministic local cache
# =============================================================================================


class TapError(RuntimeError):
    pass


def tap_csv(client: httpx.Client, url: str, query: str) -> str:
    form = {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": query}
    last: Exception | None = None
    for attempt in range(4):
        try:
            response = client.post(url, data=form, timeout=600.0)
            text = response.text
            if response.status_code != 200 or text.lstrip().startswith("<"):
                raise TapError(f"TAP error {response.status_code}: {text[:600]}")
            return text
        except (httpx.HTTPError, TapError) as exc:
            last = exc
            time.sleep(5 * 2**attempt)
    raise TapError(f"query failed after retries: {query[:200]}") from last


def cached_text(path: Path, fetch, *, offline: bool) -> str:
    if path.exists():
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    if offline:
        raise SystemExit(f"--offline: missing cache file {path}")
    text = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    # mtime=0 keeps the cached gzip bytes identical across fetches of identical data
    with open(tmp, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as fh:
        fh.write(text.encode("utf-8"))
    tmp.replace(path)
    return text


def source_id_range(order: int, pix: int) -> tuple[int, int]:
    shift = 35 + 2 * (12 - order)
    return pix << shift, ((pix + 1) << shift) - 1


def count_query(cfg: ReleaseConfig, order: int, parent: tuple[int, int] | None) -> str:
    where = f"phot_g_mean_mag < {cfg.mag_limit}"
    if parent is not None:
        lo, hi = source_id_range(*parent)
        where += f" AND source_id BETWEEN {lo} AND {hi}"
    return (
        f"SELECT hpx, COUNT(*) AS n FROM (SELECT GAIA_HEALPIX_INDEX({order}, source_id) AS hpx "
        f"FROM {cfg.gaia_table} WHERE {where}) AS t GROUP BY hpx ORDER BY hpx"
    )


def gaia_chunk_query(cfg: ReleaseConfig, order: int, pix: int) -> str:
    lo, hi = source_id_range(order, pix)
    return (
        "SELECT g.source_id, g.ra, g.dec, g.phot_g_mean_mag, g.bp_rp, g.pmra, g.pmdec, g.radial_velocity, "
        "d.r_med_photogeo, d.r_lo_photogeo, d.r_hi_photogeo, d.r_med_geo, d.r_lo_geo, d.r_hi_geo "
        f"FROM {cfg.gaia_table} AS g LEFT OUTER JOIN {cfg.distance_table} AS d ON d.source_id = g.source_id "
        f"WHERE g.source_id BETWEEN {lo} AND {hi} AND g.phot_g_mean_mag < {cfg.mag_limit}"
    )


# GSP-Phot astrophysical parameters (each with its 16th and 84th percentiles), astrometric
# quality and two flags, from gaia_source; FLAME luminosity, radius, mass and age from
# astrophysical_parameters. The cache keeps all of them; the tiles encode a subset.
GAIA_ASTRO_COLUMNS = tuple(
    f"{name}_gspphot{suffix}"
    for name in ("teff", "logg", "mh", "distance", "azero", "ag", "ebpminrp")
    for suffix in ("", "_lower", "_upper")
) + ("libname_gspphot", "parallax", "parallax_error", "ruwe", "phot_variable_flag", "non_single_star")
FLAME_COLUMNS = tuple(
    f"{name}_flame{suffix}" for name in ("lum", "radius", "mass", "age") for suffix in ("", "_lower", "_upper")
) + ("evolstage_flame", "flags_flame")
ASTRO_TEXT_COLUMNS = frozenset({"libname_gspphot", "phot_variable_flag", "flags_flame"})


def gaia_astro_chunk_query(cfg: ReleaseConfig, order: int, pix: int) -> str:
    """The base chunk's sources (same WHERE clause) with their GSP-Phot, FLAME and quality columns."""
    lo, hi = source_id_range(order, pix)
    columns = ["g.source_id", *(f"g.{c}" for c in GAIA_ASTRO_COLUMNS), *(f"ap.{c}" for c in FLAME_COLUMNS)]
    return (
        f"SELECT {', '.join(columns)} "
        f"FROM {cfg.gaia_table} AS g LEFT OUTER JOIN {cfg.astro_table} AS ap ON ap.source_id = g.source_id "
        f"WHERE g.source_id BETWEEN {lo} AND {hi} AND g.phot_g_mean_mag < {cfg.mag_limit}"
    )


def hipparcos_query(cfg: ReleaseConfig) -> str:
    return (
        "SELECT h.hip, h.ra, h.dec, h.plx, h.e_plx, h.pm_ra, h.pm_de, h.hp_mag, h.b_v, "
        "x.source_id AS gaia_source_id, g.phot_g_mean_mag AS gaia_g "
        f"FROM {cfg.hipparcos_table} AS h "
        f"LEFT OUTER JOIN {cfg.best_neighbour_table} AS x ON x.original_ext_source_id = h.hip "
        f"LEFT OUTER JOIN {cfg.gaia_table} AS g ON g.source_id = x.source_id "
        f"WHERE h.hp_mag < {cfg.hipparcos_mag_limit} ORDER BY h.hip"
    )


def best_neighbour_query(cfg: ReleaseConfig, hips: list[int]) -> str:
    values = ", ".join(str(h) for h in sorted(set(hips)))
    return (
        "SELECT original_ext_source_id AS hip, source_id "
        f"FROM {cfg.best_neighbour_table} WHERE original_ext_source_id IN ({values}) ORDER BY hip"
    )


def parse_counts(text: str) -> dict[int, int]:
    return {int(r["hpx"]): int(r["n"]) for r in csv.DictReader(io.StringIO(text))}


def plan_chunks(cfg: ReleaseConfig, client: httpx.Client | None, cache: Path, offline: bool) -> list[tuple[int, int, int]]:
    """Split the sky into source_id ranges that each return fewer rows than the TAP cap."""

    def counts_for(order: int, parent: tuple[int, int] | None) -> dict[int, int]:
        name = f"counts-o{order}" + (f"-in-o{parent[0]}-p{parent[1]}" if parent else "") + ".csv.gz"
        return parse_counts(
            cached_text(cache / name, lambda: tap_csv(client, cfg.tap_sync, count_query(cfg, order, parent)), offline=offline)
        )

    chunks: list[tuple[int, int, int]] = []

    def visit(order: int, counts: dict[int, int]) -> None:
        for pix in sorted(counts):
            n = counts[pix]
            if n < CHUNK_ROW_TARGET:
                chunks.append((order, pix, n))
            else:
                visit(order + 1, counts_for(order + 1, (order, pix)))

    base = counts_for(3, None)
    # Merge order-3 pixels into their order-2 parent when the parent still fits one request
    grouped: dict[int, dict[int, int]] = {}
    for pix, n in base.items():
        grouped.setdefault(pix >> 2, {})[pix] = n
    for parent in sorted(grouped):
        children = grouped[parent]
        total = sum(children.values())
        if total < CHUNK_ROW_TARGET:
            chunks.append((2, parent, total))
        else:
            visit(3, children)
    return chunks


GAIA_COLUMNS = (
    "ra",
    "dec",
    "phot_g_mean_mag",
    "bp_rp",
    "pmra",
    "pmdec",
    "radial_velocity",
    "r_med_photogeo",
    "r_lo_photogeo",
    "r_hi_photogeo",
    "r_med_geo",
    "r_lo_geo",
    "r_hi_geo",
)


def _float(value: str) -> float:
    return float(value) if value not in ("", "null", "NaN", "nan") else np.nan


def parse_gaia_chunk(text: str) -> dict[str, np.ndarray]:
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    idx = {name: header.index(name) for name in ("source_id",) + GAIA_COLUMNS}
    rows = list(reader)
    out = {"source_id": np.array([int(r[idx["source_id"]]) for r in rows], dtype=np.int64)}
    for name in GAIA_COLUMNS:
        out[name] = np.array([_float(r[idx[name]]) for r in rows], dtype=np.float64)
    return out


def fetch_gaia(cfg: ReleaseConfig, client: httpx.Client | None, cache: Path, offline: bool) -> tuple[dict[str, np.ndarray], list[dict]]:
    chunks = plan_chunks(cfg, client, cache, offline)
    print(f"[bake] {len(chunks)} Gaia chunks planned, {sum(c[2] for c in chunks):,} sources expected")

    def load(chunk: tuple[int, int, int]) -> tuple[tuple[int, int, int], dict[str, np.ndarray]]:
        order, pix, expected = chunk
        path = cache / "gaia" / f"o{order}-p{pix}.csv.gz"
        text = cached_text(path, lambda: tap_csv(client, cfg.tap_sync, gaia_chunk_query(cfg, order, pix)), offline=offline)
        parsed = parse_gaia_chunk(text)
        got = len(parsed["source_id"])
        if got != expected:
            path.unlink(missing_ok=True)
            raise TapError(f"chunk o{order}-p{pix}: expected {expected} rows, archive returned {got}")
        return chunk, parsed

    started = time.time()
    results: list[tuple[tuple[int, int, int], dict[str, np.ndarray]]] = []
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        for i, result in enumerate(pool.map(load, chunks), start=1):
            results.append(result)
            if i % 25 == 0 or i == len(chunks):
                print(f"[bake]   {i}/{len(chunks)} chunks ready ({time.time() - started:.0f}s)")
    merged = {key: np.concatenate([r[1][key] for r in results]) for key in results[0][1]}
    order = np.argsort(merged["source_id"], kind="stable")
    merged = {k: v[order] for k, v in merged.items()}
    if len(np.unique(merged["source_id"])) != len(merged["source_id"]):
        raise TapError("duplicate Gaia source_id across chunks")
    chunk_log = [{"order": c[0], "pix": c[1], "rows": c[2]} for c, _ in results]
    return merged, chunk_log


def parse_gaia_astro_chunk(text: str) -> dict[str, np.ndarray]:
    """Numbers as float64 with NaN where the archive has no value; flags as strings, empty when absent."""
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    expected = ["source_id", *GAIA_ASTRO_COLUMNS, *FLAME_COLUMNS]
    if header != expected:
        raise ValueError(f"unexpected astrophysical chunk columns: {header}")
    rows = list(reader)
    out = {"source_id": np.array([int(r[0]) for r in rows], dtype=np.int64)}
    for i, name in enumerate(expected[1:], start=1):
        if name in ASTRO_TEXT_COLUMNS:
            out[name] = np.array([r[i] for r in rows], dtype=str)
        else:
            out[name] = np.array([_float(r[i]) for r in rows], dtype=np.float64)
    return out


def fetch_gaia_astro(
    cfg: ReleaseConfig,
    client: httpx.Client | None,
    cache: Path,
    offline: bool,
    chunks: list[tuple[int, int, int]],
    *,
    workers: int = FETCH_WORKERS,
    keep_going: bool = False,
) -> dict[str, np.ndarray] | None:
    """GSP-Phot, FLAME and quality columns for the sources of every base chunk, sorted by source_id.

    With `keep_going`, a chunk the archive fails to deliver is reported and skipped so the others
    still reach the cache; the result is then None and a rerun fetches only what is missing.
    """

    def load(chunk: tuple[int, int, int]) -> tuple[tuple[int, int, int], dict[str, np.ndarray], int]:
        order, pix, expected = chunk
        path = cache / "gaia-ap" / f"o{order}-p{pix}.csv.gz"
        text = cached_text(path, lambda: tap_csv(client, cfg.tap_sync, gaia_astro_chunk_query(cfg, order, pix)), offline=offline)
        parsed = parse_gaia_astro_chunk(text)
        got = len(parsed["source_id"])
        if got != expected:
            path.unlink(missing_ok=True)
            raise TapError(f"astrophysical chunk o{order}-p{pix}: expected {expected} rows, archive returned {got}")
        return chunk, parsed, path.stat().st_size

    started = time.time()
    results: dict[tuple[int, int, int], dict[str, np.ndarray]] = {}
    failures: list[str] = []
    cached_bytes = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(load, chunk): chunk for chunk in chunks}
        for i, future in enumerate(as_completed(futures), start=1):
            chunk = futures[future]
            try:
                _, parsed, size = future.result()
            except (TapError, httpx.HTTPError) as exc:
                if not keep_going:
                    raise
                failures.append(f"o{chunk[0]}-p{chunk[1]}: {type(exc).__name__}: {str(exc)[:160]}")
                continue
            results[chunk] = parsed
            cached_bytes += size
            if i % 10 == 0 or i == len(chunks):
                print(
                    f"[bake]   astrophysical {i}/{len(chunks)} chunks done, {len(results)} ready, {cached_bytes / 1e6:.1f} MB "
                    f"cached ({time.time() - started:.0f}s)",
                    flush=True,
                )
    if failures:
        for line in failures:
            print(f"[bake]   astrophysical chunk {line}", flush=True)
        print(f"[bake]   {len(failures)} of {len(chunks)} astrophysical chunks failed; rerun to fetch them", flush=True)
        return None
    # Chunk order, then source_id order: the result does not depend on which chunk finished first
    merged = {key: np.concatenate([results[c][key] for c in chunks]) for key in results[chunks[0]]}
    order = np.argsort(merged["source_id"], kind="stable")
    merged = {k: v[order] for k, v in merged.items()}
    if len(np.unique(merged["source_id"])) != len(merged["source_id"]):
        raise TapError("duplicate Gaia source_id across astrophysical chunks")
    return merged


def fetch_archive(cfg: ReleaseConfig, cache_root: Path, workers: int = FETCH_WORKERS) -> int:
    """Fill the archive cache (base chunks, then GSP-Phot and FLAME chunks) without baking.

    Chunks already cached are read, not fetched, so a rerun after a failure resumes.
    """
    started = time.time()
    cache = cache_root / cfg.release_id
    cache.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers={"User-Agent": USER_AGENT}) as client:
        gaia, _ = fetch_gaia(cfg, client, cache, offline=False)
        chunks = plan_chunks(cfg, client, cache, offline=False)
        astro = fetch_gaia_astro(cfg, client, cache, offline=False, chunks=chunks, workers=workers, keep_going=True)
    if astro is None:
        return 1
    if not np.array_equal(astro["source_id"], gaia["source_id"]):
        raise TapError("astrophysical chunks do not hold exactly the base chunks' sources")
    present = {
        name: int(np.count_nonzero(np.isfinite(astro[name])))
        for name in ("teff_gspphot", "azero_gspphot", "ag_gspphot", "parallax_error", "ruwe", "lum_flame", "radius_flame", "mass_flame", "age_flame")
    }
    print(f"[fetch] {len(astro['source_id']):,} sources with values present: {present} ({time.time() - started:.0f}s)")
    return 0


def parse_hipparcos(text: str) -> dict[str, np.ndarray]:
    rows = list(csv.DictReader(io.StringIO(text)))
    out = {
        "hip": np.array([int(r["hip"]) for r in rows], dtype=np.int64),
        "gaia_source_id": np.array([int(r["gaia_source_id"]) if r["gaia_source_id"] else 0 for r in rows], dtype=np.int64),
    }
    for name in ("ra", "dec", "plx", "e_plx", "pm_ra", "pm_de", "hp_mag", "b_v", "gaia_g"):
        out[name] = np.array([_float(r[name]) for r in rows], dtype=np.float64)
    if len(np.unique(out["hip"])) != len(out["hip"]):
        raise TapError("Hipparcos query returned a HIP number twice")
    return out


# =============================================================================================
# IAU WGSN star names
# =============================================================================================


@dataclass(frozen=True)
class StarName:
    name: str
    name_ascii: str
    designation: str
    bayer: str
    constellation: str
    component: str
    hip: int | None


def parse_iau_csn(text: str) -> tuple[list[StarName], str]:
    lines = text.splitlines()
    header = next(line for line in lines if line.startswith("#Name/ASCII"))
    # Left-hand columns are fixed width; tokens repeat ("ID", "#"), so search left to right
    starts, cursor = [], 0
    for token in ("#Name/ASCII", "Name/Diacritics", "Designation", "ID", "ID", "Con", "#", "WDS_J"):
        cursor = header.index(token, cursor)
        starts.append(cursor)
        cursor += len(token)
    updated = next((line.split("Last updated", 1)[1].split("(")[0].strip() for line in lines if "Last updated" in line), "")
    names: list[StarName] = []
    for line in lines:
        if not line.strip() or line.startswith("#") or line.startswith("$"):
            continue
        fields = [line[starts[i] : starts[i + 1]].strip() for i in range(7)]
        tail = line[starts[7] :].split()
        if len(tail) < 8:
            raise ValueError(f"unexpected IAU-CSN row: {line!r}")
        hip_text = tail[3]
        names.append(
            StarName(
                name=fields[1],
                name_ascii=fields[0],
                designation=fields[2],
                bayer=fields[3] if fields[3] != "_" else "",
                constellation=fields[5],
                component=fields[6] if fields[6] != "_" else "",
                hip=int(hip_text) if hip_text.isdigit() else None,
            )
        )
    return names, updated


# =============================================================================================
# Bake
# =============================================================================================


@dataclass
class StarTable:
    catalog_hipparcos: np.ndarray  # bool
    ident: np.ndarray  # int64: Gaia source_id or HIP
    ra: np.ndarray
    dec: np.ndarray
    mag: np.ndarray
    color: np.ndarray
    distance: np.ndarray
    distance_lo: np.ndarray
    distance_hi: np.ndarray
    distance_kind: np.ndarray  # uint8
    pm_ra: np.ndarray
    pm_dec: np.ndarray
    rv: np.ndarray
    named: np.ndarray  # bool
    # ASTRO_TABLE_COLUMNS keys: Gaia DR3 values for Gaia entries, NaN for Hipparcos entries
    astro: dict[str, np.ndarray]


# Star table astro field -> archive column, for the values the astro tiles carry
ASTRO_TABLE_COLUMNS = {
    "teff": "teff_gspphot",
    "teff_lower": "teff_gspphot_lower",
    "teff_upper": "teff_gspphot_upper",
    "lum": "lum_flame",
    "lum_lower": "lum_flame_lower",
    "lum_upper": "lum_flame_upper",
    "a0": "azero_gspphot",
    "a0_lower": "azero_gspphot_lower",
    "a0_upper": "azero_gspphot_upper",
    "ag": "ag_gspphot",
    "ruwe": "ruwe",
    "parallax_error": "parallax_error",
}


def build_star_table(
    cfg: ReleaseConfig,
    gaia: dict[str, np.ndarray],
    hip: dict[str, np.ndarray],
    named_keys: set[tuple[bool, int]],
    astro: dict[str, np.ndarray],
) -> tuple[StarTable, list[ct.BrightMergeDecision], dict]:
    if not np.array_equal(astro["source_id"], gaia["source_id"]):
        raise ValueError("astrophysical rows do not line up with the Gaia rows")
    g_mag = gaia["phot_g_mean_mag"]
    bright = np.flatnonzero(g_mag < cfg.duplicate_mag_limit)
    hip_ra, hip_dec = ct.propagate_linear(hip["ra"], hip["dec"], hip["pm_ra"], hip["pm_de"], cfg.epoch - cfg.hipparcos_epoch)
    use_hip, drop_bright, decisions = ct.merge_bright_stars(
        gaia_source_id=gaia["source_id"][bright],
        gaia_vec=ct.radec_to_vec(gaia["ra"][bright], gaia["dec"][bright]),
        gaia_mag=g_mag[bright],
        hip=hip["hip"],
        hip_vec_gaia_epoch=ct.radec_to_vec(hip_ra, hip_dec),
        hip_mag=hip["hp_mag"],
        hip_best_neighbour=hip["gaia_source_id"],
        hip_best_neighbour_mag=hip["gaia_g"],
        gaia_poor_brighter_than=cfg.gaia_poor_brighter_than,
        gaia_mag_limit=cfg.mag_limit,
        duplicate_radius_arcsec=cfg.duplicate_radius_arcsec,
        duplicate_mag_limit=cfg.duplicate_mag_limit,
    )
    keep = np.ones(len(g_mag), dtype=bool)
    keep[bright[drop_bright]] = False

    photo = np.isfinite(gaia["r_med_photogeo"])
    geo = ~photo & np.isfinite(gaia["r_med_geo"])
    g_dist = np.where(photo, gaia["r_med_photogeo"], np.where(geo, gaia["r_med_geo"], np.nan))
    g_lo = np.where(photo, gaia["r_lo_photogeo"], np.where(geo, gaia["r_lo_geo"], np.nan))
    g_hi = np.where(photo, gaia["r_hi_photogeo"], np.where(geo, gaia["r_hi_geo"], np.nan))
    g_kind = np.where(photo, ct.DIST_KIND_BJ_PHOTOGEO, np.where(geo, ct.DIST_KIND_BJ_GEO, ct.DIST_KIND_NONE)).astype(np.uint8)

    h = {k: v[use_hip] for k, v in hip.items()}
    h_ra, h_dec = hip_ra[use_hip], hip_dec[use_hip]
    plx, e_plx = h["plx"], h["e_plx"]
    with np.errstate(divide="ignore", invalid="ignore"):
        h_ok = np.isfinite(plx) & (plx > 0)
        h_dist = np.where(h_ok, 1000.0 / plx, np.nan)
        h_lo = np.where(h_ok, 1000.0 / (plx + e_plx), np.nan)
        h_hi = np.where(h_ok & (plx - e_plx > 0), 1000.0 / (plx - e_plx), np.nan)
    h_kind = np.where(h_ok, ct.DIST_KIND_PARALLAX_INVERSE, ct.DIST_KIND_NONE).astype(np.uint8)

    n_g, n_h = int(keep.sum()), int(use_hip.sum())
    catalog_hip = np.concatenate([np.zeros(n_g, dtype=bool), np.ones(n_h, dtype=bool)])
    ident = np.concatenate([gaia["source_id"][keep], h["hip"]])
    named_gaia = np.array(sorted(i for c, i in named_keys if not c), dtype=np.int64)
    named_hip = np.array(sorted(i for c, i in named_keys if c), dtype=np.int64)
    named = np.where(catalog_hip, np.isin(ident, named_hip), np.isin(ident, named_gaia))
    table = StarTable(
        catalog_hipparcos=catalog_hip,
        ident=ident,
        ra=np.concatenate([gaia["ra"][keep], h_ra]),
        dec=np.concatenate([gaia["dec"][keep], h_dec]),
        mag=np.concatenate([g_mag[keep], h["hp_mag"]]),
        color=np.concatenate([gaia["bp_rp"][keep], h["b_v"]]),
        distance=np.concatenate([g_dist[keep], h_dist]),
        distance_lo=np.concatenate([g_lo[keep], h_lo]),
        distance_hi=np.concatenate([g_hi[keep], h_hi]),
        distance_kind=np.concatenate([g_kind[keep], h_kind]),
        pm_ra=np.concatenate([gaia["pmra"][keep], h["pm_ra"]]),
        pm_dec=np.concatenate([gaia["pmdec"][keep], h["pm_de"]]),
        rv=np.concatenate([gaia["radial_velocity"][keep], np.full(n_h, np.nan)]),
        named=named,
        # Hipparcos entries get no Gaia astrophysics: missing, never borrowed from a neighbour
        astro={field: np.concatenate([astro[column][keep], np.full(n_h, np.nan)]) for field, column in ASTRO_TABLE_COLUMNS.items()},
    )
    summary = {
        "gaiaSources": n_g,
        "hipparcosStars": n_h,
        "gaiaDropped": int((~keep).sum()),
        "withDistance": int(np.isfinite(table.distance).sum()),
        "withRadialVelocity": int(np.isfinite(table.rv).sum()),
        "withProperMotion": int(np.isfinite(table.pm_ra).sum()),
        "withColor": int(np.isfinite(table.color).sum()),
        "withTemperature": int(np.isfinite(table.astro["teff"]).sum()),
        "withLuminosity": int(np.isfinite(table.astro["lum"]).sum()),
        "withExtinction": int(np.isfinite(table.astro["a0"]).sum()),
        "withRuwe": int(np.isfinite(table.astro["ruwe"]).sum()),
        "withParallaxError": int(np.isfinite(table.astro["parallax_error"]).sum()),
    }
    return table, decisions, summary


def layer_of(cfg: ReleaseConfig, mag: np.ndarray) -> np.ndarray:
    layer = np.full(mag.shape, -1, dtype=np.int64)
    for spec in cfg.layers:
        lo = -np.inf if spec.mag_min is None else spec.mag_min
        layer[(mag >= lo) & (mag < spec.mag_max)] = spec.layer
    if np.any(layer < 0):
        raise ValueError("star outside every layer magnitude range")
    return layer


def write_tiles(cfg: ReleaseConfig, table: StarTable, out: Path) -> tuple[list[dict], dict[tuple[bool, int], tuple[int, int, int]]]:
    face, x, y = ct.radec_to_face_xy(table.ra, table.dec)
    layers = layer_of(cfg, table.mag)
    mag_code = ct.encode_mag(table.mag)
    color_code = ct.encode_color(table.color)
    dist_code = ct.encode_distance(table.distance)
    lo_code, hi_code = ct.encode_distance_bounds(table.distance, table.distance_lo, table.distance_hi)
    flags = (
        table.distance_kind.astype(np.uint8)
        | np.where(table.catalog_hipparcos, ct.FLAG_MAG_BAND_HP | ct.FLAG_COLOR_B_V | ct.FLAG_CATALOG_HIPPARCOS, 0).astype(np.uint8)
        | np.where(table.named, ct.FLAG_NAMED, 0).astype(np.uint8)
    )
    pm_ra16, pm_dec16, rv16 = ct.encode_f16(table.pm_ra), ct.encode_f16(table.pm_dec), ct.encode_f16(table.rv)
    id_hi = (table.ident >> 32).astype(np.uint32)
    id_lo = (table.ident & 0xFFFFFFFF).astype(np.uint32)
    true_vec = ct.radec_to_vec(table.ra, table.dec)
    a = table.astro
    astro_codes = {
        "teff": ct.encode_teff(a["teff"]),
        "teff_lower": ct.encode_teff(a["teff_lower"]),
        "teff_upper": ct.encode_teff(a["teff_upper"]),
        "log_lum": ct.encode_luminosity(a["lum"]),
        "log_lum_lower": ct.encode_luminosity(a["lum_lower"]),
        "log_lum_upper": ct.encode_luminosity(a["lum_upper"]),
        "a0": ct.encode_extinction(a["a0"]),
        "a0_lower": ct.encode_extinction(a["a0_lower"]),
        "a0_upper": ct.encode_extinction(a["a0_upper"]),
        "ag": ct.encode_extinction(a["ag"]),
        "ruwe_f16": ct.encode_f16(a["ruwe"]),
        "parallax_error_f16": ct.encode_f16(a["parallax_error"]),
    }

    layer_reports: list[dict] = []
    locations: dict[tuple[bool, int], tuple[int, int, int]] = {}
    for spec in cfg.layers:
        sel = np.flatnonzero(layers == spec.layer)
        pix, u, v = ct.face_xy_to_nest(spec.order, face[sel], x[sel], y[sel])
        # Brightest first inside each tile, ties broken by catalog then identifier
        order = np.lexsort((table.ident[sel], table.catalog_hipparcos[sel], mag_code[sel], pix))
        sel, pix, u, v = sel[order], pix[order], u[order], v[order]
        n_tiles = 12 * 4**spec.order
        counts = np.bincount(pix, minlength=n_tiles)
        starts = np.concatenate([[0], np.cumsum(counts)])
        pos_u = ct.quantize_unit(u, spec.position_bits)
        pos_v = ct.quantize_unit(v, spec.position_bits)
        layer_dir = out / f"L{spec.layer}"
        layer_dir.mkdir(parents=True, exist_ok=True)
        core_sizes, detail_sizes, astro_sizes, max_err = [], [], [], 0.0
        for p in np.flatnonzero(counts):
            a, b = starts[p], starts[p + 1]
            s = sel[a:b]
            core = ct.encode_core_tile(
                layer=spec.layer,
                order=spec.order,
                pix=int(p),
                position_bits=spec.position_bits,
                pos_u=pos_u[a:b],
                pos_v=pos_v[a:b],
                mag_code=mag_code[s],
                color_code=color_code[s],
                distance_code=dist_code[s],
                flags=flags[s],
            )
            detail = ct.encode_detail_tile(
                layer=spec.layer,
                order=spec.order,
                pix=int(p),
                id_hi=id_hi[s],
                id_lo=id_lo[s],
                pm_ra_f16=pm_ra16[s],
                pm_dec_f16=pm_dec16[s],
                rv_f16=rv16[s],
                lower_code=lo_code[s],
                upper_code=hi_code[s],
            )
            # Read every tile back through the decoder before it is written
            h, cols = ct.decode_core_tile(core)
            decoded = ct.decode_core_directions(h, cols)
            max_err = max(max_err, float(ct.angular_separation_arcsec(decoded, true_vec[s]).max()))
            if not (np.array_equal(cols["mag"], mag_code[s]) and np.array_equal(cols["distance"], dist_code[s])):
                raise AssertionError(f"tile L{spec.layer}/{p} failed its read-back check")
            _, dcols = ct.decode_detail_tile(detail)
            ids = (dcols["idHi"].astype(np.int64) << 32) | dcols["idLo"].astype(np.int64)
            if not np.array_equal(ids, table.ident[s]):
                raise AssertionError(f"tile L{spec.layer}/{p} failed its identifier read-back check")
            astro = ct.encode_astro_tile(
                layer=spec.layer, order=spec.order, pix=int(p), **{name: codes[s] for name, codes in astro_codes.items()}
            )
            _, acols = ct.decode_astro_tile(astro)
            for column, name in (("teff", "teff"), ("logLum", "log_lum"), ("a0", "a0"), ("parallaxError", "parallax_error_f16")):
                if not np.array_equal(acols[column], astro_codes[name][s]):
                    raise AssertionError(f"tile L{spec.layer}/{p} failed its astro read-back check on {column}")
            (layer_dir / f"{p}.core.bin").write_bytes(core)
            (layer_dir / f"{p}.detail.bin").write_bytes(detail)
            (layer_dir / f"{p}.astro.bin").write_bytes(astro)
            core_sizes.append(len(core))
            detail_sizes.append(len(detail))
            astro_sizes.append(len(astro))
            for k in np.flatnonzero(table.named[s]):
                star = s[k]
                locations[(bool(table.catalog_hipparcos[star]), int(table.ident[star]))] = (spec.layer, int(p), int(k))
        layer_reports.append(
            {
                "layer": spec.layer,
                "magMin": spec.mag_min,
                "magMax": spec.mag_max,
                "order": spec.order,
                "positionBits": spec.position_bits,
                "stars": int(len(sel)),
                "nonEmptyTiles": int(np.count_nonzero(counts)),
                "coreBytes": int(sum(core_sizes)),
                "detailBytes": int(sum(detail_sizes)),
                "astroBytes": int(sum(astro_sizes)),
                "coreTileBytes": _size_stats(core_sizes),
                "detailTileBytes": _size_stats(detail_sizes),
                "astroTileBytes": _size_stats(astro_sizes),
                "maxPositionErrorArcsec": round(max_err, 4),
                "tileStarCounts": [int(c) for c in counts],
            }
        )
        print(
            f"[bake] L{spec.layer} order {spec.order}: {len(sel):,} stars in {np.count_nonzero(counts)} tiles, "
            f"core {sum(core_sizes) / 1e6:.2f} MB, detail {sum(detail_sizes) / 1e6:.2f} MB, "
            f"astro {sum(astro_sizes) / 1e6:.2f} MB, max position error {max_err:.3f} arcsec"
        )
    return layer_reports, locations


def _size_stats(sizes: list[int]) -> dict[str, int]:
    if not sizes:
        return {"min": 0, "median": 0, "max": 0}
    return {"min": int(min(sizes)), "median": int(np.median(sizes)), "max": int(max(sizes))}


def resolve_names(
    names: list[StarName],
    used_hip: set[int],
    hip_to_gaia: dict[int, int],
    gaia_kept: set[int],
) -> tuple[dict[tuple[bool, int], list[StarName]], dict[str, int]]:
    resolved: dict[tuple[bool, int], list[StarName]] = {}
    stats = {"names": len(names), "resolved": 0, "noHip": 0, "notInCatalogSubset": 0}
    for entry in names:
        if entry.hip is None:
            stats["noHip"] += 1
            continue
        if entry.hip in used_hip:
            key = (True, entry.hip)
        elif entry.hip in hip_to_gaia and hip_to_gaia[entry.hip] in gaia_kept:
            key = (False, hip_to_gaia[entry.hip])
        else:
            stats["notInCatalogSubset"] += 1
            continue
        resolved.setdefault(key, []).append(entry)
        stats["resolved"] += 1
    return resolved, stats


def dumps_manifest(manifest: dict) -> str:
    """Pretty JSON with the long per-tile count arrays kept on one line each."""
    arrays: dict[str, list[int]] = {}
    for layer in manifest["layers"]:
        token = f"@@tiles-{layer['layer']}@@"
        arrays[token] = layer["tileStarCounts"]
        layer["tileStarCounts"] = token
    text = json.dumps(manifest, indent=2, ensure_ascii=False)
    for token, values in arrays.items():
        text = text.replace(f'"{token}"', json.dumps(values, separators=(",", ":")))
    for layer in manifest["layers"]:
        layer["tileStarCounts"] = arrays[f"@@tiles-{layer['layer']}@@"]
    return text + "\n"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# Provenance record (docs/architecture/ASSET_PROVENANCE.md). The bake replaces the whole release
# folder, so it writes the folder's record itself: one file set, SHA256SUMS, naming every source.
ESA_ARCHIVE_LICENCE = {
    "spdx": "CC-BY-NC-3.0-IGO",
    "name": "Creative Commons Attribution-NonCommercial 3.0 IGO",
    "url": "https://creativecommons.org/licenses/by-nc/3.0/igo/",
    "conditions": (
        "Data hosted in the ESA Space Science Archives are distributed under this licence; commercial use "
        "needs ESA's prior authorisation (https://www.cosmos.esa.int/web/esdc/terms-and-conditions)."
    ),
}
IAU_CSN_TERMS = {
    "spdx": "LicenseRef-IAU-Creative-Commons-Attribution",
    "name": "Creative Commons Attribution, as the IAU states it, with no version given",
    "url": "https://www.pas.rochester.edu/~emamajek/WGSN/IAU-CSN.txt",
    "conditions": (
        "The file's header says all IAU-produced products are released under Creative Commons Attribution, "
        "free to use as long as the source is mentioned, and asks users to cite the official IAU version."
    ),
}
# Per manifest source id: the edition, the credit line to display, and the licence
RECORD_TERMS = {
    "gaia-dr3": ("DR3", "ESA/Gaia/DPAC", ESA_ARCHIVE_LICENCE),
    "gaia-dr3-astrophysical-parameters": ("DR3", "ESA/Gaia/DPAC", ESA_ARCHIVE_LICENCE),
    "bailer-jones-2021": ("EDR3 distances (2021)", "Bailer-Jones et al. 2021", ESA_ARCHIVE_LICENCE),
    "hipparcos-2007": ("2007 new reduction", "ESA Hipparcos", ESA_ARCHIVE_LICENCE),
    "gaia-dr3-hipparcos2-best-neighbour": ("DR3", "ESA/Gaia/DPAC", ESA_ARCHIVE_LICENCE),
    "iau-csn": (None, "IAU Working Group on Star Names (WGSN)", IAU_CSN_TERMS),
}


def provenance_record(cfg: ReleaseConfig, checksums_sha256: str, *, names_updated: str) -> AssetProvenanceRecord:
    """The release folder's record: SHA256SUMS pinned by its SHA-256, and every manifest source."""
    sources = []
    for source in cfg.sources:
        version, credit, licence = RECORD_TERMS[source["id"]]
        note = " ".join(
            part
            for part in (
                f"Table {source['table']}." if "table" in source else None,
                (
                    f"Mission citation: {source['missionCitation']} (doi:{source['missionDoi']})."
                    if "missionCitation" in source
                    else None
                ),
                source.get("note"),
            )
            if part
        )
        entry = {
            "id": source["id"],
            "title": source["title"],
            "url": source.get("archive") or source["url"],
            "version": version or f"Last updated {names_updated}",
            "license": licence,
            "credit": credit,
            "citation": source.get("citation"),
            "doi": source.get("doi"),
            "acknowledgement": source.get("acknowledgement"),
            "note": note or None,
        }
        sources.append({key: value for key, value in entry.items() if value is not None})
    return AssetProvenanceRecord.model_validate(
        {
            "format": "albedian.asset-provenance",
            "formatVersion": 1,
            "sources": sources,
            "files": [],
            "fileSets": [
                {
                    "checksums": "SHA256SUMS",
                    "sha256": checksums_sha256,
                    "sources": [source["id"] for source in cfg.sources],
                    "producedBy": {
                        "method": "script",
                        "script": "scripts/bake_catalog_star_tiles.py",
                        "command": f"uv run python scripts/bake_catalog_star_tiles.py --release {cfg.release_id}",
                    },
                    "note": (
                        "Every file of the release except this record. The values are catalog values "
                        "(evidenceKind catalog-derived in manifest.json), never observed."
                    ),
                }
            ],
        }
    )


def write_checksums_and_record(cfg: ReleaseConfig, folder: Path, *, names_updated: str) -> None:
    """Write SHA256SUMS over every file in the folder, then the record that pins it."""
    skip = {folder / "SHA256SUMS", folder / ASSET_PROVENANCE_RECORD_FILENAME}
    files = sorted(p for p in folder.rglob("*") if p.is_file() and p not in skip)
    sums = "".join(f"{sha256_file(p)}  {p.relative_to(folder).as_posix()}\n" for p in files)
    (folder / "SHA256SUMS").write_text(sums, encoding="utf-8")
    record = provenance_record(cfg, sha256_file(folder / "SHA256SUMS"), names_updated=names_updated)
    (folder / ASSET_PROVENANCE_RECORD_FILENAME).write_text(dumps_record(record), encoding="utf-8")


def bake(cfg: ReleaseConfig, out_root: Path, cache_root: Path, offline: bool) -> Path:
    started = time.time()
    cache = cache_root / cfg.release_id
    cache.mkdir(parents=True, exist_ok=True)
    client = None if offline else httpx.Client(headers={"User-Agent": USER_AGENT})
    try:
        gaia, chunk_log = fetch_gaia(cfg, client, cache, offline)
        astro = fetch_gaia_astro(cfg, client, cache, offline, plan_chunks(cfg, client, cache, offline))
        hip_text = cached_text(cache / "hipparcos-bright.csv.gz", lambda: tap_csv(client, cfg.tap_sync, hipparcos_query(cfg)), offline=offline)
        hip = parse_hipparcos(hip_text)
        names_path = cache / "IAU-CSN.txt.gz"
        names_text = cached_text(names_path, lambda: client.get(cfg.star_names_url, timeout=60.0).raise_for_status().text, offline=offline)
        names, names_updated = parse_iau_csn(names_text)
        named_hips = [n.hip for n in names if n.hip is not None]
        bn_text = cached_text(
            cache / "named-hip-best-neighbour.csv.gz",
            lambda: tap_csv(client, cfg.tap_sync, best_neighbour_query(cfg, named_hips)),
            offline=offline,
        )
    finally:
        if client is not None:
            client.close()
    print(f"[bake] archive data ready: {len(gaia['source_id']):,} Gaia rows, {len(hip['hip']):,} Hipparcos rows ({time.time() - started:.0f}s)")

    # HEALPix check: Gaia encodes its level-12 index in source_id. Disagreement can only come
    # from stars that moved across a pixel edge between source creation and DR3, so it must
    # vanish at coarse orders and stay rare at fine ones.
    hp_check = {}
    for order in (12, 6, 3):
        mine = ct.radec_to_nest(order, gaia["ra"], gaia["dec"])
        agree = float(np.mean(mine == ct.gaia_source_id_healpix(gaia["source_id"], order)))
        hp_check[f"order{order}Agreement"] = round(agree, 6)
    if hp_check["order12Agreement"] < 0.99 or hp_check["order3Agreement"] < 0.9999:
        raise AssertionError(f"HEALPix implementation disagrees with Gaia source_id encoding: {hp_check}")
    print(f"[bake] HEALPix check against Gaia source_id: {hp_check}")

    hip_to_gaia = {int(r["hip"]): int(r["source_id"]) for r in csv.DictReader(io.StringIO(bn_text))}
    # First pass decides the merge, second pass marks named stars (names depend on the merge)
    table, decisions, summary = build_star_table(cfg, gaia, hip, set(), astro)
    # Hipparcos stars with no best-neighbour row whose Gaia counterpart the merge found
    for decision in decisions:
        if decision.action == "keep_gaia" and decision.hip is not None and decision.gaia_source_id is not None:
            hip_to_gaia.setdefault(decision.hip, decision.gaia_source_id)
    used_hip = set(table.ident[table.catalog_hipparcos].tolist())
    gaia_kept = set(table.ident[~table.catalog_hipparcos].tolist())
    resolved, name_stats = resolve_names(names, used_hip, hip_to_gaia, gaia_kept)
    table, decisions, summary = build_star_table(cfg, gaia, hip, set(resolved), astro)

    final = out_root / cfg.release_id
    staging = Path(tempfile.mkdtemp(prefix=f".{cfg.release_id}-", dir=out_root))
    try:
        layer_reports, locations = write_tiles(cfg, table, staging)
        name_rows = []
        for key, entries in resolved.items():
            layer, pix, index = locations[key]
            star = np.flatnonzero((table.catalog_hipparcos == key[0]) & (table.ident == key[1]))[0]
            for entry in entries:
                name_rows.append(
                    {
                        "name": entry.name,
                        "nameAscii": entry.name_ascii,
                        "designation": entry.designation,
                        "bayer": entry.bayer,
                        "constellation": entry.constellation,
                        "component": entry.component,
                        "hip": entry.hip,
                        "catalog": "hipparcos-2007" if key[0] else "gaia-dr3",
                        "catalogId": str(key[1]),
                        "layer": layer,
                        "pix": pix,
                        "index": index,
                        "raDeg": round(float(table.ra[star]), 6),
                        "decDeg": round(float(table.dec[star]), 6),
                        "mag": round(float(table.mag[star]), 3),
                        "magBand": "Hp" if key[0] else "G",
                    }
                )
        name_rows.sort(key=lambda r: (r["nameAscii"], r["catalogId"]))
        names_doc = {
            "format": MANIFEST_FORMAT + ".names",
            "releaseId": cfg.release_id,
            "evidenceKind": "catalog-derived",
            "source": "iau-csn",
            "sourceUpdated": names_updated,
            "sourceSha256": hashlib.sha256(names_text.encode("utf-8")).hexdigest(),
            "names": name_rows,
        }
        (staging / "names.json").write_text(json.dumps(names_doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        merge_doc = {
            "format": MANIFEST_FORMAT + ".bright-star-merge",
            "releaseId": cfg.release_id,
            "decisions": [
                {"action": d.action, "hip": d.hip, "gaiaSourceId": None if d.gaia_source_id is None else str(d.gaia_source_id), "reason": d.reason}
                for d in decisions
            ],
        }
        (staging / "bright-star-merge.json").write_text(json.dumps(merge_doc, indent=1) + "\n", encoding="utf-8")

        manifest = {
            "format": MANIFEST_FORMAT,
            "formatVersion": MANIFEST_FORMAT_VERSION,
            "releaseId": cfg.release_id,
            "title": cfg.title,
            "evidenceKind": "catalog-derived",
            "disclosure": (
                "Catalog data from Gaia DR3 and Hipparcos 2007 with Bailer-Jones (2021) distance estimates. "
                "Positions, brightness, distances, temperatures, luminosities and extinction come from these "
                "catalogs, not from your recording."
            ),
            "frame": "ICRS",
            "epochJulianYear": cfg.epoch,
            "magnitudeLimit": cfg.mag_limit,
            "magnitudeBands": {"G": "Gaia G (Gaia DR3 sources)", "Hp": "Hipparcos Hp (Hipparcos 2007 entries)"},
            "counts": {
                "stars": int(len(table.ident)),
                **summary,
                "namedStars": len(resolved),
                "names": len(name_rows),
            },
            "tiling": {
                "scheme": "healpix",
                "ordering": "nested",
                "positionEncoding": "healpix-face-subpixel",
                "tilePath": "L{layer}/{pix}.{kind}.bin",
                "kinds": ["core", "detail", "astro"],
            },
            "layers": layer_reports,
            "encoding": {
                "byteOrder": "little-endian",
                "magic": ct.TILE_MAGIC.decode(),
                "headerBytes": ct.TILE_HEADER_BYTES,
                "core": ["posU", "posV", "mag:u16", "color:i16", "distance:u16", "flags:u8"],
                "detail": ["idHi:u32", "idLo:u32", "pmRa:f16", "pmDec:f16", "radialVelocity:f16", "distanceLower:u8", "distanceUpper:u8"],
                "position": "HEALPix face coordinate inside the tile pixel, (code + 0.5) / 2^positionBits",
                "magnitude": {"offset": ct.MAG_OFFSET, "scale": ct.MAG_SCALE, "decode": "code / 1000 - 2"},
                "color": {"scale": ct.COLOR_SCALE, "missing": ct.COLOR_MISSING, "decode": "code / 1000"},
                "distance": {
                    "log10Min": ct.DIST_LOG10_MIN,
                    "log10Step": ct.DIST_LOG10_STEP,
                    "missing": ct.DIST_MISSING,
                    "decode": "10^(log10Min + (code - 1) * log10Step) parsec",
                },
                "distanceBounds": {
                    "missing": ct.BOUND_MISSING,
                    "log10FractionMin": ct.BOUND_LOG10_FRAC_MIN,
                    "lowerLog10Span": ct.BOUND_LOWER_LOG10_SPAN,
                    "upperLog10Span": ct.BOUND_UPPER_LOG10_SPAN,
                    "decode": "fraction = 10^(log10FractionMin + (code - 1) / 254 * span); lower = median * (1 - fraction), upper = median * (1 + fraction)",
                },
                "astro": {
                    "kind": ct.KIND_ASTRO,
                    "bytesPerStar": ct.ASTRO_STRIDE,
                    "columns": [
                        "teff:u16",
                        "teffLower:u16",
                        "teffUpper:u16",
                        "logLum:i16",
                        "logLumLower:i16",
                        "logLumUpper:i16",
                        "a0:u16",
                        "a0Lower:u16",
                        "a0Upper:u16",
                        "ag:u16",
                        "ruwe:f16",
                        "parallaxError:f16",
                    ],
                    "teff": {"unit": "K", "missing": ct.TEFF_MISSING, "decode": "code kelvin"},
                    "logLum": {"scale": ct.LOG_LUM_SCALE, "missing": ct.LOG_LUM_MISSING, "decode": "10^(code / 1000) solar luminosities"},
                    "extinction": {"scale": ct.EXTINCTION_SCALE, "missing": ct.EXTINCTION_MISSING, "decode": "code / 1000 mag"},
                    "fields": {
                        "teff": "teff_gspphot; teffLower and teffUpper are its 16th and 84th percentiles",
                        "logLum": "lum_flame; logLumLower and logLumUpper are its 16th and 84th percentiles",
                        "a0": "azero_gspphot, monochromatic extinction A0 at 547.7 nm, with its percentiles",
                        "ag": "ag_gspphot, extinction in the Gaia G band",
                        "ruwe": "ruwe",
                        "parallaxError": "parallax_error, mas",
                    },
                    "missing": "Gaia DR3 gives no value; never filled. Hipparcos entries carry none.",
                },
                "float16": "IEEE 754 binary16; NaN marks a missing value",
                "units": {
                    "pmRa": "mas/yr (includes cos dec)",
                    "pmDec": "mas/yr",
                    "radialVelocity": "km/s",
                    "distance": "pc",
                    "teff": "K",
                    "luminosity": "Lsun",
                    "extinction": "mag",
                    "parallaxError": "mas",
                },
                "flags": {
                    "distanceKindMask": ct.FLAG_DIST_KIND_MASK,
                    "distanceKinds": {
                        str(ct.DIST_KIND_NONE): "none",
                        str(ct.DIST_KIND_BJ_PHOTOGEO): "bailer-jones-2021-photogeometric",
                        str(ct.DIST_KIND_BJ_GEO): "bailer-jones-2021-geometric",
                        str(ct.DIST_KIND_PARALLAX_INVERSE): "hipparcos-2007-parallax-inverse",
                    },
                    "magnitudeBandHp": ct.FLAG_MAG_BAND_HP,
                    "colorBMinusV": ct.FLAG_COLOR_B_V,
                    "catalogHipparcos": ct.FLAG_CATALOG_HIPPARCOS,
                    "named": ct.FLAG_NAMED,
                },
                "tileOrder": "brightest first, ties by catalog then identifier",
            },
            "sources": list(cfg.sources),
            "selection": {
                "gaia": f"{cfg.gaia_table} with phot_g_mean_mag < {cfg.mag_limit}, left join {cfg.distance_table} on source_id; no other quality cuts",
                "gaiaQueryTemplate": gaia_chunk_query(cfg, 0, 0).replace(
                    "BETWEEN {} AND {}".format(*source_id_range(0, 0)), "BETWEEN {lo} AND {hi}"
                ),
                "gaiaChunks": len(chunk_log),
                "gaiaAstroQueryTemplate": gaia_astro_chunk_query(cfg, 0, 0).replace(
                    "BETWEEN {} AND {}".format(*source_id_range(0, 0)), "BETWEEN {lo} AND {hi}"
                ),
                "gaiaAstro": (
                    f"every source of the Gaia selection, left join {cfg.astro_table} on source_id, in the same chunks "
                    "as gaiaQueryTemplate; the cache keeps more columns than the astro tiles encode"
                ),
                "hipparcosQuery": hipparcos_query(cfg),
                "brightStars": {
                    "rule": (
                        "A Hipparcos star replaces Gaia when it has no Gaia DR3 best neighbour (and no unsaturated Gaia "
                        f"source within {cfg.duplicate_radius_arcsec:g} arcsec agrees within 1 mag, or sits within "
                        f"{ct.SAME_STAR_RADIUS_ARCSEC:g} arcsec no more than 1 mag fainter), when its best neighbour is brighter "
                        f"than G {cfg.gaia_poor_brighter_than:g} or outside the G < {cfg.mag_limit:g} subset, or when a "
                        f"Gaia source brighter than G {cfg.gaia_poor_brighter_than:g} within {cfg.duplicate_radius_arcsec:g} "
                        "arcsec matches the Hipparcos magnitude better than the best neighbour (the crossmatch picked a "
                        f"companion). Gaia sources within {ct.SAME_STAR_RADIUS_ARCSEC:g} arcsec of a Hipparcos-supplied star, "
                        f"or within {cfg.duplicate_radius_arcsec:g} arcsec and agreeing within 1.5 mag or brighter than G "
                        f"{cfg.gaia_poor_brighter_than:g}, are removed as duplicates; fainter companions stay. Remaining Gaia "
                        "sources brighter than G "
                        f"{cfg.gaia_poor_brighter_than:g} are removed."
                    ),
                    "hipparcosMagnitudeLimit": cfg.hipparcos_mag_limit,
                    "gaiaPoorBrighterThan": cfg.gaia_poor_brighter_than,
                    "duplicateRadiusArcsec": cfg.duplicate_radius_arcsec,
                    "sameStarRadiusArcsec": ct.SAME_STAR_RADIUS_ARCSEC,
                    "duplicateSearchMagnitudeLimit": cfg.duplicate_mag_limit,
                    "hipparcosEpoch": cfg.hipparcos_epoch,
                    "log": "bright-star-merge.json",
                },
                "names": {**name_stats, "sourceUpdated": names_updated},
                "healpixCheck": hp_check,
            },
            "files": {"names": "names.json", "brightStarMerge": "bright-star-merge.json", "checksums": "SHA256SUMS"},
            "bake": {
                "script": "scripts/bake_catalog_star_tiles.py",
                "command": f"uv run python scripts/bake_catalog_star_tiles.py --release {cfg.release_id}",
                "verify": f"uv run python scripts/bake_catalog_star_tiles.py --release {cfg.release_id} --verify",
            },
        }
        (staging / "manifest.json").write_text(dumps_manifest(manifest), encoding="utf-8")
        write_checksums_and_record(cfg, staging, names_updated=names_updated)

        if final.exists():
            shutil.rmtree(final)
        staging.rename(final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    total_core = sum(layer["coreBytes"] for layer in layer_reports)
    total_detail = sum(layer["detailBytes"] for layer in layer_reports)
    total_astro = sum(layer["astroBytes"] for layer in layer_reports)
    shown = final.relative_to(ROOT_DIR) if final.is_relative_to(ROOT_DIR) else final
    print(
        f"[bake] wrote {shown}: {len(table.ident):,} stars, {len(name_rows)} names, core {total_core / 1e6:.1f} MB "
        f"+ detail {total_detail / 1e6:.1f} MB + astro {total_astro / 1e6:.1f} MB in {time.time() - started:.0f}s"
    )
    return final


def verify(cfg: ReleaseConfig, out_root: Path, cache_root: Path) -> int:
    committed = out_root / cfg.release_id / "SHA256SUMS"
    if not committed.exists():
        print(f"[verify] no committed checksums at {committed}")
        return 1
    committed_record = committed.parent / ASSET_PROVENANCE_RECORD_FILENAME
    with tempfile.TemporaryDirectory(prefix="albedian-star-verify-") as tmp:
        rebuilt = bake(cfg, Path(tmp), cache_root, offline=True)
        expected = committed.read_text(encoding="utf-8")
        actual = (rebuilt / "SHA256SUMS").read_text(encoding="utf-8")
        record_matches = committed_record.exists() and committed_record.read_bytes() == (
            rebuilt / ASSET_PROVENANCE_RECORD_FILENAME
        ).read_bytes()
    if not record_matches:
        print(f"[verify] {committed_record.name} is missing or differs from the rebake's record")
    if expected == actual and record_matches:
        print(
            f"[verify] rebake from cache matches {committed.relative_to(ROOT_DIR)} ({len(expected.splitlines())} files) "
            f"and {committed_record.name}"
        )
        return 0
    if expected == actual:
        return 1
    exp, act = set(expected.splitlines()), set(actual.splitlines())
    for line in sorted(exp ^ act)[:20]:
        print(("[verify] missing  " if line in exp else "[verify] new      ") + line)
    print(f"[verify] {len(exp - act)} committed entries differ from the rebake")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--release", default="gaia-dr3", choices=sorted(RELEASES))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--offline", action="store_true", help="bake from cached archive responses only")
    parser.add_argument("--verify", action="store_true", help="rebake from the cache and compare with committed checksums")
    parser.add_argument("--fetch-only", action="store_true", help="fill the archive cache and stop before baking")
    parser.add_argument("--workers", type=int, default=FETCH_WORKERS, help="concurrent archive queries for --fetch-only")
    args = parser.parse_args()
    cfg = RELEASES[args.release]
    if args.fetch_only:
        return fetch_archive(cfg, args.cache, args.workers)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.verify:
        return verify(cfg, args.out, args.cache)
    bake(cfg, args.out, args.cache, args.offline)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
