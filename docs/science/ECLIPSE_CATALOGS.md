# Five Millennium Eclipse Catalogs

`apps/web/public/catalog/eclipses/` ships the complete solar and lunar eclipse tables from Fred
Espenak and Jean Meeus's Five Millennium Catalogs (`-1999` to `+3000`, corresponding to `2000 BCE`
to `3000 CE`), published by NASA's Goddard Space Flight Center:

- `solar.json`: all `11,898` solar eclipses (`4,200` partial, `3,956` annular, `3,173` total, and
  `569` hybrid), validated by `SolarEclipseCatalogSchema` in `packages/contracts/src/eclipses.ts`
  and `SolarEclipseCatalog` in `services/contracts/eclipses.py`.
- `lunar.json`: all `12,064` lunar eclipses (`4,378` penumbral, including `141` total penumbral;
  `4,207` partial; and `3,479` total), validated by `LunarEclipseCatalogSchema` and
  `LunarEclipseCatalog`.
- `PROVENANCE.json`: asset provenance record validated by `AssetProvenanceRecordSchema`.

Every entry in both catalogues is an ephemeris prediction (`predicted`, carrying
`evidenceClass: "predicted"` in the JSON envelope), never a direct measurement from a camera
recording (`observed`).

## Upstream Sources and Technical Publication Numbers

| Catalogue | Input File | Bytes | SHA-256 | Publication Identifiers |
|---|---|---:|---|---|
| Solar (`solar.json`) | `https://eclipse.gsfc.nasa.gov/5MCSE/5MKSEcatalog.txt` | `1,380,993` | `c07e7b5e13af61a1d74eb3bee1a1824a197dc1ca393a3ac2a91c9e73bc2e0c58` | File header (`2008 Oct 07`): `NASA/TP-2008-214170`; NASA publication page (`SEpubs/5MKSE.html`, `2009 January`): `NASA/TP-2009-214174`; companion Canon: `NASA/TP-2006-214141` |
| Lunar (`lunar.json`) | `https://eclipse.gsfc.nasa.gov/5MCLE/5MKLEcatalog.txt` | `1,400,382` | `d47586fc9c1c59338f234b3c6634c31744739887169f58d411ac766f3861fcf2` | File header (`2011 May 23`) and publication page (`SEpubs/5MKLE.html`, `2009 January`): `NASA/TP-2009-214173` |

### Publication Identifier Conflict and Errata

The solar ASCII file `5MKSEcatalog.txt` carries `Source: NASA Technical Publication NASA/TP-2008-214170`
and `Date: 2008 Oct 07` in its header, while the NASA publication page
(`https://eclipse.gsfc.nasa.gov/SEpubs/5MKSE.html`) and PDF (`TP2009-214174.pdf`) identify the same
Five Millennium Catalog of Solar Eclipses as `NASA/TP-2009-214174` (published January 2009). Both
identifiers are recorded in `PROVENANCE.json`.

NASA's errata page (`https://eclipse.gsfc.nasa.gov/5MCSE/5MKSEerrata.html`) states that tabulated
values of `delta T` and longitude on pages `A-56 to A-150` were misprinted in the hard-copy edition
of `NASA/TP-2008-214170` only, and that all corrections are included in the electronic edition.

### Terms and Required Credit

Both NASA catalog keys (`SEcat5/SEcatkey.html` and `LEcat5/LEcatkey.html`) grant permission under
`LicenseRef-NASA-Eclipse-Permission`:

> Permission is freely granted to reproduce this data when accompanied by an acknowledgment:
> "Eclipse Predictions by Fred Espenak and Jean Meeus (NASA's GSFC)"

## Calendar and Year Numbering Conventions

1. **Julian and Gregorian calendars:** Dates through `1582 October 4` use the Julian calendar
   (`calendar: "julian"`). Dates from `1582 October 15` onward use the Gregorian calendar
   (`calendar: "gregorian"`). The ten dates skipped by the Gregorian calendar reform (`1582 October 5`
   through `1582 October 14`) do not exist and are rejected by `eclipseCalendarOf` /
   `eclipse_calendar_of`.
2. **Astronomical year numbering:** Years are numbered astronomically and include year `0`. Year `0`
   corresponds to `1 BCE`, year `-1` to `2 BCE`, and year `-1999` to `2000 BCE`. Julian leap years
   follow `year % 4 == 0` under astronomical numbering (`0`, `-4`, `-8`, ..., `-1996` are leap
   years).

## Ephemerides and Delta T Uncertainty

Both catalogues compute Sun coordinates from VSOP87 (Bretagnon and Francou, 1988) and Moon
coordinates from ELP-2000/82 (Chapront-Touze and Chapront, 1983), adopting a lunar secular
acceleration of `n-dot = -25.858 arcsec/cy^2` from Apollo lunar laser ranging (Chapront,
Chapront-Touze, and Francou, 2002). Lunar eclipse shadow sizes use Danjon's enlargement method to
account for Earth's atmosphere.

Instant of greatest eclipse is tabulated in Terrestrial Dynamical Time (`greatestEclipseTdSeconds`,
seconds since `00:00:00 TD` on the given calendar date). Converting between Dynamical Time (`TD`)
and Universal Time (`UT = TD - delta T`) depends on `deltaTSeconds` (`delta T`), which represents
the accumulated clock drift from fluctuations in Earth's rotation. As NASA's catalog keys state,
`delta T` is the largest source of uncertainty in historic and far-future eclipse predictions,
especially for the geographic longitude of solar eclipse paths:

1. **Pre-1950s:** `delta T` is evaluated from empirical polynomial fits to historical eclipse and
   occultation records derived by Morrison and Stephenson (2004).
2. **Modern observational era (`1955-present` in the solar key, `1955-2006` in the lunar key):**
   `delta T` is taken from published astrometric and atomic-time observations.
3. **Future epochs:** `delta T` is extrapolated from measured values weighted by the long-term
   parabolic trend from lunar tidal friction.

## Schema and Columns

Both JSON files share the top-level envelope:

```json
{
  "format": "albedian.eclipse-catalog",
  "formatVersion": 1,
  "kind": "solar",
  "evidenceClass": "predicted",
  "columns": ["..."],
  "rows": [[...]]
}
```

### Solar Eclipse Columns (`solar.json`, 21 columns)

| Index | Column | Type | Units / Domain | Definition |
|---:|---|---|---|---|
| 0 | `catalogNumber` | `int` | `1..11898` | Sequential number in the Five Millennium Catalog and Canon |
| 1 | `canonPlate` | `int` | `1..595` | Plate number in the Five Millennium Canon of Solar Eclipses (`20` eclipses per plate) |
| 2 | `year` | `int` | `-1999..3000` | Astronomical year at greatest eclipse (`0 = 1 BCE`) |
| 3 | `month` | `int` | `1..12` | Calendar month at greatest eclipse |
| 4 | `day` | `int` | `1..31` | Calendar day at greatest eclipse |
| 5 | `calendar` | `"julian" \| "gregorian"` | Julian to `1582-10-04`, Gregorian from `1582-10-15` | Calendar convention used for the date |
| 6 | `greatestEclipseTdSeconds` | `int` | `0..86399` s | Dynamical Time (`TD`) of greatest eclipse since `00:00:00 TD` |
| 7 | `deltaTSeconds` | `int` | s | `TD - UT` adopted for the eclipse |
| 8 | `lunationNumber` | `int` | synodic months | New Moons since `2000 Jan 06` (`Brown Lunation Number = lunationNumber + 953`) |
| 9 | `sarosNumber` | `int` | series id | Saros series number |
| 10 | `eclipseType` | `"P" \| "A" \| "T" \| "H"` | code | `P` partial, `A` annular, `T` total, `H` hybrid (annular/total) |
| 11 | `typeQualifier` | `string \| null` | `"m" \| "n" \| "s" \| "+" \| "-" \| "2" \| "3" \| "b" \| "e" \| null` | `m` middle of Saros; `n`/`s` central with no northern/southern limit; `+`/`-` non-central with no northern/southern limit; `2` hybrid path begins total and ends annular; `3` hybrid path begins annular and ends total; `b`/`e` Saros series begins/ends (partial only) |
| 12 | `quincenaLunarEclipse` | `string` | `^[-npt]{2}$` | Lunar eclipse a fortnight before (first char) and after (second char): `n` penumbral, `p` partial, `t` total, `-` none |
| 13 | `gamma` | `float` | Earth equatorial radii | Shadow cone axis distance from Earth's center at greatest eclipse (positive north, negative south) |
| 14 | `magnitude` | `float` | `>= 0` | Fraction of solar diameter obscured (or Moon/Sun diameter ratio for `A`, `T`, `H`) |
| 15 | `latitudeDeg` | `float` | `-90..90` deg | Geographic latitude of greatest eclipse (positive north, negative south) |
| 16 | `longitudeDeg` | `float` | `-180..180` deg | Geographic longitude of greatest eclipse (positive east, negative west) |
| 17 | `sunAltitudeDeg` | `int` | `0..90` deg | Sun altitude at greatest eclipse (`0` for all partial `P` and non-central `+`/`-` eclipses) |
| 18 | `sunAzimuthDeg` | `int` | `0..360` deg | Sun azimuth at greatest eclipse |
| 19 | `pathWidthKm` | `int \| null` | `>= 0` km | Umbral or antumbral path width at greatest eclipse (`null` for partial eclipses or where blank/`-` in the catalogue) |
| 20 | `centralDurationSeconds` | `int \| null` | `>= 0` s | Central line duration of totality or annularity at greatest eclipse (`null` for partial or non-central eclipses) |

### Lunar Eclipse Columns (`lunar.json`, 20 columns)

| Index | Column | Type | Units / Domain | Definition |
|---:|---|---|---|---|
| 0 | `catalogNumber` | `int` | `1..12064` | Sequential number in the Five Millennium Catalog and Canon |
| 1 | `year` | `int` | `-1999..3000` | Astronomical year at greatest eclipse (`0 = 1 BCE`) |
| 2 | `month` | `int` | `1..12` | Calendar month at greatest eclipse |
| 3 | `day` | `int` | `1..31` | Calendar day at greatest eclipse |
| 4 | `calendar` | `"julian" \| "gregorian"` | Julian to `1582-10-04`, Gregorian from `1582-10-15` | Calendar convention used for the date |
| 5 | `greatestEclipseTdSeconds` | `int` | `0..86399` s | Dynamical Time (`TD`) of greatest eclipse since `00:00:00 TD` |
| 6 | `deltaTSeconds` | `int` | s | `TD - UT` adopted for the eclipse |
| 7 | `lunationNumber` | `int` | synodic months | Full Moons since `2000 Jan 06` |
| 8 | `sarosNumber` | `int` | series id | Saros series number |
| 9 | `eclipseType` | `"N" \| "P" \| "T"` | code | `N` penumbral, `P` partial (umbral), `T` total (umbral) |
| 10 | `typeQualifier` | `string \| null` | `"m" \| "+" \| "-" \| "x" \| "b" \| "e" \| null` | `m` middle of Saros; `+`/`-` central total eclipse (Moon center passes north/south of shadow axis); `x` total penumbral eclipse (written `x` in `5MKLEcatalog.txt` where `LEcatkey.html` lists `*`; `141` rows, all with `penumbralMagnitude >= 1`); `b`/`e` Saros series begins/ends (penumbral only) |
| 11 | `quincenaSolarEclipse` | `string` | `^[-apth]{2}$` | Solar eclipse a fortnight before (first char) and after (second char): `p` partial, `a` annular, `t` total, `h` hybrid, `-` none |
| 12 | `gamma` | `float` | Earth equatorial radii | Moon center distance from Earth's shadow cone axis at greatest eclipse |
| 13 | `penumbralMagnitude` | `float` | `> 0` | Fraction of the Moon's diameter immersed in the penumbra at greatest eclipse |
| 14 | `umbralMagnitude` | `float` | finite (`<= 0` for `N`, `(0, 1)` for `P`, `>= 1` for `T`) | Fraction of the Moon's diameter immersed in the umbra at greatest eclipse |
| 15 | `penumbralDurationMinutes` | `float` | `> 0` min | Duration of the penumbral phase (`P4 - P1`) |
| 16 | `partialDurationMinutes` | `float \| null` | `> 0` min | Duration of the partial umbral phase (`U4 - U1`; `null` for penumbral eclipses) |
| 17 | `totalDurationMinutes` | `float \| null` | `> 0` min | Duration of the total umbral phase (`U3 - U2`; `null` for penumbral and partial eclipses) |
| 18 | `zenithLatitudeDeg` | `int` | `-90..90` deg | Geographic latitude where the Moon is in the zenith at greatest eclipse |
| 19 | `zenithLongitudeDeg` | `int` | `-180..180` deg | Geographic longitude where the Moon is in the zenith at greatest eclipse |

## Rebuilding and Verifying

Fetch the upstream catalogues into `.cache/m2/` (if not already cached) and run the bake:

```bash
uv run python scripts/fetch_download.py .cache/m2/5MKSEcatalog.txt .cache/m2/5MKLEcatalog.txt
uv run python scripts/bake_eclipses.py
uv run python scripts/bake_eclipses.py --verify
```
