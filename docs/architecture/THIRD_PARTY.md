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
