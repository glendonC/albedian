# Third-party code in the Python services

The Python services install their dependencies from `pyproject.toml` and the pinned `uv.lock`. This file records a
dependency's licence, what it is used for and where the licence was read. It starts with `pyerfa`; the
dependencies added before it are listed only in those two files, and their licences are not yet recorded here
[open].

## `pyerfa` 2.0.1.5, with ERFA 2.0.1 inside it

| | `pyerfa` (the Python wrapper) | ERFA (the C library its wheel contains) |
|---|---|---|
| Version | 2.0.1.5 | ERFA 2.0.1, matching SOFA release 2023-10-11 |
| Licence | BSD 3-Clause | BSD 3-Clause, with SOFA's naming conditions |
| Copyright | 2011-2023, PyERFA Developers | 2013-2021 NumFOCUS Foundation (its `LICENSE`); 2013-2023 in its source file headers |
| Read at | `LICENSE.rst` in the installed distribution's `dist-info`, and its `METADATA` | `LICENSE` at tag v2.0.1 of github.com/liberfa/erfa, read 2026-11-11 |
| Needs | numpy only; no data file and no download at run time | nothing |

What it is: ERFA (Essential Routines for Fundamental Astronomy) is the IAU's SOFA library renamed, "derived, with
permission", as its licence says. It holds the IAU's reference routines for time scales, precession and nutation,
aberration, Earth rotation and refraction, and three analytic ephemerides: the Moon (`eraMoon98`, Meeus 1998), the
planets (`eraPlan94`, Simon et al. 1994) and the Earth (`eraEpv00`, a simplified VSOP2000) [verified: the licence
texts and the function notes as `pyerfa` 2.0.1.5 ships them].

What Albedian uses it for: the predicted positions of the Sun, the Moon and the planets and the sky transforms in
`services/science/` (unit ephemeris-2). At this commit only `tests/integration/test_erfa_dependency.py` imports
it: it checks the installed library against values from ERFA's own test file, `src/t_erfa_c.c` [tested].

What the licences ask [verified: both licence texts]:
- Keep the copyright notices, the conditions and the disclaimer with any copy of the source.
- Reproduce them in the documentation of any binary copy. Albedian ships no binary of the Python services
  [inference], so none carries them yet.
- Do not use the names of the PyERFA team, the SOFA Board or the IAU to endorse Albedian.
- ERFA asks that its SOFA heritage be acknowledged as "a library derived from SOFA", not as SOFA itself.

## `tzfpy` 2.1.2, with the tzf-dist boundary data release 2026f inside it

| | `tzfpy` (the Python binding and the Rust code inside its wheel) | The time zone boundary data inside its wheel |
|---|---|---|
| Version | 2.1.2 | data release 2026f (`tzfpy.data_version()`), the lite build: borders simplified to within about 111 m |
| Licence | MIT (tzfpy, tzf-rs, tzf-dist's code; the Rust crates its NOTICE lists are MIT, Apache-2.0, Unlicense or Unicode-3.0) | ODbL 1.0 |
| Copyright | 2022 ringsaturn | timezone-boundary-builder (github.com/evansiroky/timezone-boundary-builder), built from OpenStreetMap |
| Read at | `LICENSE` and `NOTICE` in the installed distribution's `dist-info/licenses`, its `METADATA`; `LICENSE` of github.com/ringsaturn/tzfpy and tzf-rs, read 2026-11-11 | `NOTICE` (above), tzfpy's README ("The data is licensed under the ODbL license, same as timezone-boundary-builder"), and the ODbL text in github.com/ringsaturn/tzf-rel's `LICENSE` and timezone-boundary-builder's `DATA_LICENSE`, read 2026-11-11 |
| Needs | no other package; no data file and no network call at run time [tested] | nothing |

What it is: a point-in-polygon lookup of the IANA time zone at a latitude and longitude, over boundaries that
timezone-boundary-builder assembles from OpenStreetMap, with territorial waters and the nautical zones (`Etc/GMT+N`)
at sea. `get_tzs` is exact against the simplified polygons and names every zone a shared border point lies on.

What Albedian uses it for: the time zone at a photo's own GPS position, the first of the two sources that must agree
before a capture time without an offset tag gets one (unit capture-time-1, `services/ingest/metadata.py`). Only the
offset it leads to is stored; the zone's name is not.

Why this one [tested for unit capture-time-1, counts and seconds only]: it and `timezonefinder` 9.2.0 (MIT code,
ODbL data in the separate `timezonefinder-data`) both gave every observatory in `tests/integration/test_tzfpy_dependency.py`
its zone and a nautical zone at every open-ocean point, never none (both poles included), and the same zone at
19,939 of 20,000 random points; the 61 others lie where borders and coasts differ between their builds. `tzfpy`
installs as one wheel of about 3 MB (4.7 MB unpacked) with no other package, has abi3 wheels for CPython 3.10 and up
on Linux x86_64 (CI's 3.12 and 3.14) and macOS arm64 (3.13 and 3.14 in this laptop's worktrees), and took about 1.1 microseconds per
lookup. `timezonefinder` needs `timezonefinder-data` (33 MB), numpy, cffi and flatbuffers, has no macOS wheel (it
built from source here), and took about 8.9 microseconds per lookup.

What the licences ask [verified: the MIT texts, and ODbL sections 4.2 to 4.4; the applications are inference, not
legal advice]:
- MIT: keep the copyright notice and the permission notice with any copy of the code. Albedian ships no binary of
  the Python services [inference], so it copies neither.
- ODbL, installing the package and calling it: that is Use of the Database, not Publicly Conveying it, so section
  4.2 (convey only under the ODbL, with its text and notices) applies only if Albedian redistributes the package or
  its data, for example inside a built app [inference].
- ODbL section 4.3: an offset worked out from the data is a Produced Work. Using it needs no notice; if Albedian
  Publicly Uses one (a record, a page or a picture shown to people outside the owner's control), it must carry a
  notice such as "Contains information from timezone-boundary-builder, which is made available here under the Open
  Database License (ODbL)", with links to the database and to the licence text [inference: the records stay on the
  owner's machine today, so none needs it yet].
- ODbL section 4.4 (share alike) binds a Derivative Database that is Publicly Used. Albedian changes no boundary and
  builds no database from them [inference], so it has none.
