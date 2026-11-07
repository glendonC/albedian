# NASA Exoplanet Archive Planetary Systems Composite Parameters (`pscomppars`)

## Overview and Scientific Evidence Class

Albedian ships a validated, self-contained JSON bake of the NASA Exoplanet Archive's Planetary Systems Composite Parameters (`PSCompPars`) table in `apps/web/public/catalog/exoplanets/pscomppars.json`, paired with `apps/web/public/catalog/exoplanets/PROVENANCE.json` and produced deterministically by `scripts/bake_exoplanets.py`.

Every entry in `pscomppars.json` carries the contract envelope field `evidenceKind: "catalog-derived"` (`packages/contracts/src/exoplanets.ts` and `services/contracts/exoplanets.py`). This distinguishes the static baked catalog table (`catalog-derived`) from `catalog_matched` scientific evidence, which is reserved for findings in a user's source recording after an independently verified WCS calibration and held-out residual check. Because `PSCompPars` is a composite table assembled from multiple literature sources and archive calculations rather than a single homogeneous orbital solution, every numerical value cell also carries its own per-value `basis` (`"measured"`, `"calculated"`, or `"modelled"`) and an integer index into the table's deduplicated `references` array.

## Upstream Snapshot and Provenance

- **Table**: NASA Exoplanet Archive Planetary Systems Composite Parameters (`pscomppars`), DOI `10.26133/NEA13` (companion to the Planetary Systems `PS` table, DOI `10.26133/NEA12`).
- **TAP Query**: `https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+*+from+pscomppars&format=csv` (`select * from pscomppars`).
- **Frozen Retrieval Timestamp**: `2026-11-05T00:36:15Z`.
- **Cached Input**: `.cache/m2/pscomppars.csv` (`82,923,300` bytes, SHA-256 `17862c7b7df4a9de1813367071b312523f7f42ed1a2284ce879cd8ee2a707386`), registered in `data/downloads.json`. Because live TAP queries against `pscomppars` return updated rows as new planets and parameter sets are ingested upstream, `data/downloads.json` and `scripts/bake_exoplanets.py` pin the exact byte size and SHA-256 of the `2026-11-05T00:36:15Z` snapshot.
- **Row and Reference Counts**: `6,451` confirmed exoplanets and `1,838` unique `(refstr, url)` references cited across the 13 value columns.

## Required Acknowledgement and Citations

In accordance with `https://exoplanetarchive.ipac.caltech.edu/docs/acknowledge.html`, any display or export derived from this table must carry the archive's required acknowledgement verbatim:

> "This research has made use of the NASA Exoplanet Archive, which is operated by the California Institute of Technology, under contract with the National Aeronautics and Space Administration under the Exoplanet Exploration Program."

Primary citations:

- **Archive Overview Paper**: Christiansen, J. L., et al. 2025, *The NASA Exoplanet Archive and Exoplanet Follow-up Observing Program: Data, Tools, and Usage*, The Planetary Science Journal, DOI `10.3847/PSJ/ade3c2`.
- **Composite Table DOI**: NASA Exoplanet Archive Planetary Systems Composite Parameters (`PSCompPars`), DOI `10.26133/NEA13` (companion to the Planetary Systems `PS` table, DOI `10.26133/NEA12`).
- **Original Literature References**: Each numerical cell in `pscomppars.json` indexes into `references` (`refstr`, `label`, `url`) so the UI and inspectors can cite the original discovery or characterization paper for every displayed parameter.

## Schema and Column Mapping

`pscomppars.json` validates against `exoplanetTableSchema` (`packages/contracts/src/exoplanets.ts`) and `ExoplanetTable` (`services/contracts/exoplanets.py`). Each row is a 21-element tuple in the exact order of `EXOPLANET_COLUMNS`:

### Identity Columns (Columns 0 to 7)

| Index | Contract Column | Upstream CSV Column | Type and Constraints |
| --- | --- | --- | --- |
| 0 | `name` | `pl_name` | Trimmed non-empty string, unique across all 6,451 rows |
| 1 | `hostName` | `hostname` | Trimmed non-empty string |
| 2 | `starCount` | `sy_snum` | Positive integer (`>= 1`) |
| 3 | `planetCount` | `sy_pnum` | Positive integer (`>= 1`) |
| 4 | `discoveryMethod` | `discoverymethod` | Trimmed non-empty string (11 distinct methods in snapshot) |
| 5 | `discoveryYear` | `disc_year` | Integer in `[1980, 2100]` |
| 6 | `discoveryFacility` | `disc_facility` | Trimmed non-empty string |
| 7 | `massProvenance` | `pl_bmassprov` | `"Mass"`, `"Msini"`, `"M-R relationship"`, `"Msin(i)/sin(i)"`, or `null` |

In the `2026-11-05T00:36:15Z` snapshot, `massProvenance` has 3,035 `"M-R relationship"`, 2,477 `"Mass"`, 924 `"Msini"`, and 15 `"Msin(i)/sin(i)"` rows. Discovery methods comprise 4,785 `"Transit"`, 1,204 `"Radial Velocity"`, 292 `"Microlensing"`, 98 `"Imaging"`, 29 `"Transit Timing Variations"`, 17 `"Eclipse Timing Variations"`, 9 `"Orbital Brightness Modulation"`, 8 `"Pulsar Timing"`, 6 `"Astrometry"`, 2 `"Pulsation Timing Variations"`, and 1 `"Disk Kinematics"`.

### Value Columns (Columns 8 to 20)

Each value column is either `null` (when the upstream value cell is blank) or a 6-tuple `[value, err1, err2, limit, ref, basis]`:

- `value`: finite float (`> 0` for periods, semi-major axes, radii, masses, temperatures, and distances; `[0, 1)` for eccentricity; `[0, 360)` for right ascension; `[-90, 90]` for declination; finite float for V magnitude).
- `err1`: upper uncertainty (`>= 0` or `null`).
- `err2`: lower uncertainty (`<= 0` or `null`).
- `limit`: `-1`, `0`, `1`, or `null` (when the upstream table provides no limit column, as for `ra` and `dec`, or leaves the limit blank). Upstream scientific-notation limit flags such as `0.00000000000000e+00` and `1.00000000000000e+00` are parsed to exact integers `0` and `1`. Raw `*_lim` columns in `pscomppars.csv` also contain `'9'` on rows where the corresponding parameter value cell is blank (which become `null` value cells in `pscomppars.json`). Because `API_PS_columns.html` states only that `*_lim` is a limit flag without defining the numeric values `-1`, `0`, `1` (or `'9'`), the exact directional semantics of `-1` and `1` are marked `[open]`.
- `ref`: zero-based integer index into `references`.
- `basis`: `"measured"`, `"calculated"`, or `"modelled"`.

| Index | Contract Column | Value Column | Uncertainty Columns | Limit Column | Reference Column |
| --- | --- | --- | --- | --- | --- |
| 8 | `orbitalPeriodDays` | `pl_orbper` | `pl_orbpererr1`, `pl_orbpererr2` | `pl_orbperlim` | `pl_orbper_reflink` |
| 9 | `semiMajorAxisAu` | `pl_orbsmax` | `pl_orbsmaxerr1`, `pl_orbsmaxerr2` | `pl_orbsmaxlim` | `pl_orbsmax_reflink` |
| 10 | `radiusEarth` | `pl_rade` | `pl_radeerr1`, `pl_radeerr2` | `pl_radelim` | `pl_rade_reflink` |
| 11 | `massEarth` | `pl_bmasse` | `pl_bmasseerr1`, `pl_bmasseerr2` | `pl_bmasselim` | `pl_bmasse_reflink` |
| 12 | `eccentricity` | `pl_orbeccen` | `pl_orbeccenerr1`, `pl_orbeccenerr2` | `pl_orbeccenlim` | `pl_orbeccen_reflink` |
| 13 | `equilibriumTemperatureK` | `pl_eqt` | `pl_eqterr1`, `pl_eqterr2` | `pl_eqtlim` | `pl_eqt_reflink` |
| 14 | `hostEffectiveTemperatureK` | `st_teff` | `st_tefferr1`, `st_tefferr2` | `st_tefflim` | `st_teff_reflink` |
| 15 | `hostRadiusSolar` | `st_rad` | `st_raderr1`, `st_raderr2` | `st_radlim` | `st_rad_reflink` |
| 16 | `hostMassSolar` | `st_mass` | `st_masserr1`, `st_masserr2` | `st_masslim` | `st_mass_reflink` |
| 17 | `distancePc` | `sy_dist` | `sy_disterr1`, `sy_disterr2` | `sy_distlim` | `sy_dist_reflink` |
| 18 | `raDeg` | `ra` | `raerr1`, `raerr2` | `null` (none in CSV) | `ra_reflink` |
| 19 | `decDeg` | `dec` | `decerr1`, `decerr2` | `null` (none in CSV) | `dec_reflink` |
| 20 | `vMagnitude` | `sy_vmag` | `sy_vmagerr1`, `sy_vmagerr2` | `sy_vmaglim` | `sy_vmag_reflink` |

Right ascension (`raDeg`) and declination (`decDeg`) retain their small astrometric uncertainties (`raerr1`/`raerr2` and `decerr1`/`decerr2`, typically `10^-9` to `10^-7` degrees from Gaia/TIC) so that every value column in `pscomppars.json` uses a uniform 6-tuple representation.

## Reference Normalization and Deduplication

Each `*_reflink` cell in `pscomppars.csv` is an HTML anchor of the form `<a refstr=... href=... target=...>Label</a>`. During the bake:

1. **Relative Archive Links**: The relative URL `/docs/pscp_calc.html` used by `CALCULATED_VALUE` is resolved against the archive host to `https://exoplanetarchive.ipac.caltech.edu/docs/pscp_calc.html`.
2. **Legacy HTTP Links**: Plain `http://` reference URLs on the archive's three legacy external hosts (`www.cfa.harvard.edu`, `vizier.u-strasbg.fr`, and `www.nature.com`) are normalized to `https://`. Any other non-`https://` URL or malformed anchor is rejected by the bake and contract validator.
3. **Deduplication by `(refstr, url)`**: In `pscomppars.csv`, 131 `refstr` identifiers are shared across two distinct ADS papers by the same first author and year (for example, `BONOMO_ET_AL__2017` and `MACIEJEWSKI_ET_AL__2014`). References are therefore deduplicated by the pair `(refstr, url)` in order of first citation across the 13 value columns, and uncited references from unused CSV columns are omitted.

## Composite-Table Caveats and Value Bases

As documented in `https://exoplanetarchive.ipac.caltech.edu/docs/pscp_calc.html`, `PSCompPars` fills missing parameters by combining measurements from multiple papers with archive-computed fallback values. Consequently, parameters across a single row are not self-consistent and must not be treated as a single joint fit.

The contract distinguishes three value bases:

- **`measured`**: Values citing a literature paper (`refstr != "CALCULATED_VALUE"`).
- **`calculated`**: Values citing `CALCULATED_VALUE` in columns other than radius and mass (`orbitalPeriodDays`, `semiMajorAxisAu`, `equilibriumTemperatureK`, `hostRadiusSolar`, etc.), computed from standard astrophysical equations. For `equilibriumTemperatureK`, the archive computes $T_{\mathrm{eq}} = T_{\mathrm{eff}} \sqrt{R_\star / (2a)}$ assuming zero Bond albedo ($A_B = 0$) and full heat redistribution across the planet; 863 of the 1,090 `Calculated Value` `pl_eqt` (`Teq`) cells in `pscomppars.csv` carry non-null uncertainties (`err1`, `err2`) despite `pscp_calc.html` stating that calculated values do not report uncertainties, while literature-measured `equilibriumTemperatureK` cells reflect whatever albedo and redistribution assumptions the cited authors adopted. This distinction and uncertainty caveat are carried in `notes.equilibriumTemperatureK` and `PROVENANCE.json`.
- **`modelled`**: Planetary radii (`radiusEarth`) and masses (`massEarth`) citing `CALCULATED_VALUE`, which the archive estimates from the empirical probabilistic mass-radius relation of Chen and Kipping (2017, ApJ 834, 17, *Forecasting the Masses of Worlds*). Every row whose `massEarth` has `basis == "modelled"` carries `massProvenance == "M-R relationship"`, and every row whose `massEarth` has `basis == "measured"` carries `"Mass"`, `"Msini"`, or `"Msin(i)/sin(i)"` (67 rows in the snapshot have a non-null `massProvenance` while `massEarth` itself is `null`).
