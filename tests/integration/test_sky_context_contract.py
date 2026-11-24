"""The sky-context contract: services/contracts/sky_context.py against the shared fixture
tests/fixtures/sky_context_contract.json, which this file writes and packages/contracts/src/__tests__/sky-context.test.ts
(and the iPhone app's reader) parse, so a rule that changes on one side only fails a test.

Regenerate the fixture with ALBEDIAN_UPDATE_SKY_CONTEXT_FIXTURE=1 uv run pytest tests/integration/test_sky_context_contract.py

The base reply is real: rows of the route's own reply (services/science/router.py) for a synthetic moment,
2031-03-20T12:00:00Z, at an open North Pacific point, 20 N 140 W, which no capture was made at. Six stars, two bodies,
the four edges joining those stars, the sky brightness and the Sun (skycontext-3, from the same ephemeris), with the
keys this contract drops removed and the ones it adds written in. test_the_base_rows_are_the_catalogs_own checks each
star against its catalog row and the Sun against the ephemeris.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest
from hypothesis import example, given, settings, strategies as st
from pydantic import ValidationError

from services.contracts import sky_context as sc

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "sky_context_contract.json"
UPDATE_FIXTURE_ENV = "ALBEDIAN_UPDATE_SKY_CONTEXT_FIXTURE"

# Written out here, the decisions both sides must hold (not read from the module under test)
FORMAT, FORMAT_VERSION = "albedian.sky-context", 1
SPAN = ("1800-01-01T00:00:00Z", "2051-01-01T00:00:00Z")
LIMITS = {"magMin": -30.0, "magMax": 30.0, "nameMaxUtf16": 64, "textMaxUtf16": 1024, "starsMax": 512, "bodiesMax": 16,
          "edgesMax": 1024, "notesMax": 16, "properMotionMax": 20_000.0, "radialVelocityMax": 2_000.0, "bpRpMin": -2.0,
          "bpRpMax": 10.0}  # fmt: skip
CONVENTION = {"frame": "topocentric_sea_level", "azimuth": "true_north_through_east", "altitude": "apparent_airless"}
PROVIDERS = {"Hipparcos": "Hipparcos 2007", "Gaia DR3": "Gaia DR3"}
# The Sun's USNO bands, brightest first, and where each begins: day above sunrise and sunset's -50 arcminutes, then each
# twilight down to and including its floor; night below the last
SUN_BANDS = ("day", "civil_twilight", "nautical_twilight", "astronomical_twilight", "night")
SUN_BAND_EDGES = {"dayAboveDeg": -50.0 / 60.0, "civilTwilightFromDeg": -6.0, "nauticalTwilightFromDeg": -12.0,
                  "astronomicalTwilightFromDeg": -18.0}  # fmt: skip
RULES = {
    "insideEphemerisSpan": "The moment lies from 1800 to 2050, the only years the sky can be worked out for.",
    "aboveHorizonIsAltitude": "aboveHorizon is true exactly when altDeg is 0 or more.",
    "aboveHorizonOnlyHeld": "With aboveHorizonOnly, every star and body has an altDeg of 0 or more.",
    "magLimitHeld": "With a magLimit, every star's mag is at most the magLimit.",
    "idsOnce": "Each star's id and each body's id is held once.",
    "edgeJoinsHeldStars": "Each constellation edge joins two different stars the reply holds.",
    "releaseOfProvider": "A star's releaseVersion is its provider's: Hipparcos 2007 for Hipparcos, Gaia DR3 for Gaia DR3.",
    "sourceIdOfProvider": "A star's catalogSourceId is in its provider's form: HIP and a number, or Gaia DR3 and a number.",
    "distanceInOrder": "A distance's lower is at most its median, and its median at most its upper.",
    "lightTravelOfDistance": "lightTravel.distancePc is the item's own median distance.",
    "astrophysicsOfGaia": "Only a Gaia DR3 star has astrophysics, under its own catalogSourceId.",
    "astrophysicsHoldsAValue": "Astrophysics holds a temperature or a luminosity.",
    "estimateInterval": "An estimate's lower, upper and interval come together, with lower at most its value and its value at most upper.",
    "radiusFromBoth": "A radius is given only with the temperature and the luminosity it is derived from.",
    "positionIsSkyPosition": "A body's raDeg, decDeg, altDeg and azDeg are its skyPosition's.",
    "distanceIsInferred": "A body's distancePc is its inferredDistancePc median.",
    "idsOfName": "A body's id and its evidence's id are made from its name.",
    "sunBandOfAltitude": "The Sun's band is the USNO band of its altDeg: day above -50 arcminutes, civil twilight down to -6 degrees, nautical down to -12, astronomical down to -18, night below.",
}  # fmt: skip
MODELS = {"request": sc.SkyContextRequest, "reply": sc.SkyContextReply}
# The base reply, one row per line (see the module docstring for where it comes from)
BASE_REPLY: dict[str, Any] = json.loads(r"""
{
 "format": "albedian.sky-context",
 "formatVersion": 1,
 "observedAtUTC": "2031-03-20T12:00:00Z",
 "observerLocation": {"latDeg": 20.0, "lonDeg": -140.0},
 "filters": {"aboveHorizonOnly": false},
 "convention": {"frame": "topocentric_sea_level", "azimuth": "true_north_through_east", "altitude": "apparent_airless"},
 "stars": [
  {"id": "star-castor", "evidenceClass": "predicted", "commonName": "Castor", "bayerDesignation": "Alpha Geminorum", "constellation": "Gemini", "provider": "Hipparcos", "catalogSourceId": "HIP 36850", "raDeg": 113.649428, "decDeg": 31.888276, "altDeg": -0.48459988532498244, "azDeg": 304.34250949618337, "aboveHorizon": false, "mag": 1.58, "properMotionRaMasYr": -191.45, "properMotionDecMasYr": -145.19, "distancePc": {"lower": 14.734050390452335, "median": 15.595757953836555, "upper": 16.564518800728838, "method": "geometric_parallax"}, "cartesianParsecICRS": [-5.311905368388362, 12.12993674414313, 8.238686915706255], "lightTravel": {"distancePc": 15.595757953836555, "lightYears": 50.86655921709295, "lightMinutes": 26753775.48582221, "lightSeconds": 1605226529.1493325, "description": "50.9 light-years", "formatted": "50.9 light-years"}, "releaseVersion": "Hipparcos 2007"},
  {"id": "star-vega", "evidenceClass": "predicted", "commonName": "Vega", "bayerDesignation": "Alpha Lyrae", "constellation": "Lyra", "provider": "Hipparcos", "catalogSourceId": "HIP 91262", "raDeg": 279.234735, "decDeg": 38.783689, "altDeg": 34.184578636435006, "azDeg": 56.012173349003916, "aboveHorizon": true, "mag": 0.03, "properMotionRaMasYr": 200.94, "properMotionDecMasYr": 286.23, "distancePc": {"lower": 7.657554177195803, "median": 7.678722260615834, "upper": 7.700007700007702, "method": "geometric_parallax"}, "cartesianParsecICRS": [0.9605810211407515, -5.90810970574253, 4.809812819893541], "lightTravel": {"distancePc": 7.678722260615834, "lightYears": 25.04464237886816, "lightMinutes": 13172480.105589498, "lightSeconds": 790348806.3353698, "description": "25.0 light-years", "formatted": "25.0 light-years"}, "releaseVersion": "Hipparcos 2007"},
  {"id": "star-sheliak", "evidenceClass": "predicted", "commonName": "Sheliak", "bayerDesignation": "Beta Lyrae", "constellation": "Lyra", "provider": "Gaia DR3", "catalogSourceId": "Gaia DR3 2090687795056051328", "raDeg": 282.519979, "decDeg": 33.362667, "altDeg": 31.337021375038695, "azDeg": 62.341909127197255, "aboveHorizon": true, "mag": 3.52, "properMotionRaMasYr": 1.55, "properMotionDecMasYr": -4.46, "distancePc": {"lower": 263.0956, "median": 276.21762, "upper": 289.68326, "method": "photogeometric_bailer_jones_edr3"}, "cartesianParsecICRS": [50.010875583599294, -225.2128100380065, 151.9021925997481], "lightTravel": {"distancePc": 276.21762, "lightYears": 900.9013839611508, "lightMinutes": 473838091.9082069, "lightSeconds": 28430285514.492413, "description": "901 light-years", "formatted": "901 light-years"}, "bpRp": 0.21071863, "radialVelocityKmS": -29.482485, "astrophysics": {"catalogSourceId": "Gaia DR3 2090687795056051328", "limitations": ["GSP-Phot and FLAME values are model fits to Gaia's BP/RP spectra, photometry and parallax, not measurements of this star; their papers describe their reach and limits."], "teffK": {"value": 17700.0, "method": "Gaia DR3 GSP-Phot", "lower": 14991.0, "upper": 18256.0, "interval": "16th to 84th percentile"}, "citations": ["Gaia DR3 GSP-Phot (Andrae et al. 2023, doi:10.1051/0004-6361/202243462)"]}, "releaseVersion": "Gaia DR3"},
  {"id": "star-sulafat", "evidenceClass": "predicted", "commonName": "Sulafat", "bayerDesignation": "Gamma Lyrae", "constellation": "Lyra", "provider": "Gaia DR3", "catalogSourceId": "Gaia DR3 2043823929421342848", "raDeg": 284.735929, "decDeg": 32.689557, "altDeg": 29.423806085548513, "azDeg": 62.91437270893213, "aboveHorizon": true, "mag": 3.25, "properMotionRaMasYr": -3.05, "properMotionDecMasYr": 1.08, "distancePc": {"lower": 193.15596, "median": 203.35674, "upper": 211.9433, "method": "photogeometric_bailer_jones_edr3"}, "cartesianParsecICRS": [43.53368942088882, -165.5176208145176, 109.83031816732016], "lightTravel": {"distancePc": 203.35674, "lightYears": 663.2609769928071, "lightMinutes": 348848743.4591368, "lightSeconds": 20930924607.548206, "description": "663 light-years", "formatted": "663 light-years"}, "bpRp": 0.23184443, "radialVelocityKmS": -19.554564, "releaseVersion": "Gaia DR3"},
  {"id": "star-phecda", "evidenceClass": "predicted", "commonName": "Phecda", "bayerDesignation": "Gamma Ursae Majoris", "constellation": "Ursa Major", "provider": "Hipparcos", "catalogSourceId": "HIP 58001", "raDeg": 178.457697, "decDeg": 53.69476, "altDeg": 45.19212129093408, "azDeg": 327.9649229312413, "aboveHorizon": true, "mag": 2.44, "properMotionRaMasYr": 107.91, "properMotionDecMasYr": 11.02, "distancePc": {"lower": 25.246149962130776, "median": 25.50369803621525, "upper": 25.76655501159495, "method": "geometric_parallax"}, "cartesianParsecICRS": [-15.094934573869933, 0.406427624871837, 20.552770623903164], "lightTravel": {"distancePc": 25.50369803621525, "lightYears": 83.1819376944657, "lightMinutes": 43750371.94978118, "lightSeconds": 2625022316.986871, "description": "83.2 light-years", "formatted": "83.2 light-years"}, "releaseVersion": "Hipparcos 2007"},
  {"id": "star-megrez", "evidenceClass": "predicted", "commonName": "Megrez", "bayerDesignation": "Delta Ursae Majoris", "constellation": "Ursa Major", "provider": "Gaia DR3", "catalogSourceId": "Gaia DR3 1575287046603605888", "raDeg": 183.856553, "decDeg": 57.032617, "altDeg": 45.594104393957686, "azDeg": 334.3941439424924, "aboveHorizon": true, "mag": 3.31, "properMotionRaMasYr": 104.11, "properMotionDecMasYr": 7.3, "distancePc": {"lower": 24.690784, "median": 24.79358, "upper": 24.907433, "method": "photogeometric_bailer_jones_edr3"}, "cartesianParsecICRS": [-13.461160944696132, -0.9074354693681093, 20.801329671593596], "lightTravel": {"distancePc": 24.79358, "lightYears": 80.86584243015166, "lightMinutes": 42532198.48456257, "lightSeconds": 2551931909.0737543, "description": "80.9 light-years", "formatted": "80.9 light-years"}, "bpRp": 0.26148772, "radialVelocityKmS": -12.387454, "astrophysics": {"catalogSourceId": "Gaia DR3 1575287046603605888", "limitations": ["GSP-Phot and FLAME values are model fits to Gaia's BP/RP spectra, photometry and parallax, not measurements of this star; their papers describe their reach and limits."], "teffK": {"value": 8298.0, "method": "Gaia DR3 GSP-Phot", "lower": 8288.0, "upper": 8337.0, "interval": "16th to 84th percentile"}, "luminositySolar": {"value": 23.12064790175595, "method": "Gaia DR3 FLAME", "lower": 22.803420720004187, "upper": 23.38837238659355, "interval": "16th to 84th percentile"}, "radiusSolar": {"value": 2.326514608421498, "method": "Derived: Stefan-Boltzmann from the GSP-Phot temperature and FLAME luminosity, Tsun 5772 K", "derived": true, "lower": 2.2889327962643184, "upper": 2.345595717485358, "interval": "span of the two percentile intervals"}, "citations": ["Gaia DR3 GSP-Phot (Andrae et al. 2023, doi:10.1051/0004-6361/202243462)", "Gaia DR3 FLAME (Creevey et al. 2023, doi:10.1051/0004-6361/202243688; Fouesneau et al. 2023, doi:10.1051/0004-6361/202243919)"]}, "releaseVersion": "Gaia DR3"}
 ],
 "ephemerides": [
  {"id": "finding-predicted-mars", "evidenceClass": "predicted", "name": "Mars", "title": "Mars", "subtitle": "Predicted Planet \u00b7 0.796 AU \u00b7 Alt +51.9\u00b0", "skyPosition": {"raDeg": 228.80467622638065, "decDeg": -16.27174365079576, "uncertaintyArcsec": 74.96350729431806, "altDeg": 51.92244879490417, "azDeg": 162.05934338563966}, "inferredDistancePc": {"lower": 3.856748175246234e-06, "median": 3.858028659572971e-06, "upper": 3.859309143899709e-06, "method": "ephemeris_au"}, "cartesianParsecICRS": [-2.4392223175475414e-06, -2.7867600915540048e-06, -1.080993901826618e-06], "evidence": {"id": "ev-ephem-mars", "evidenceClass": "predicted", "verificationMethod": "ERFA eraPlan94 (Simon et al. 1994) for the planet and eraEpv00 (simplified VSOP2000) for the Earth and the Sun, with light time", "distanceUncertaintyNote": "Topocentric distance 0.795776 AU, between 0.795511 and 0.796040 AU from the method's error bounds", "limitations": ["Predicted from the given time and place with ERFA's analytic theories; not measured in any image.", "Seen from the given place at sea level (topocentric): its parallax is applied, and its right ascension and declination are astrometric (light time only).", "Its altitude and azimuth are apparent and airless: refraction at 1010 hPa and 10 degrees C (Saemundsson 1986) would lift it to +51.94 degrees, and the real air differs most near the horizon.", "Its uncertainty, 75.0 arcsec, adds the method's documented worst error (60.4 arcsec here), 0.00 for light deflection applied as for a star, 13.5 for UT1 - UTC taken as 0 (within 0.9 s), 0.0 for a place up to 9 km above sea level, 1 for polar motion, and 0.0 for the time scale.", "The moment is UTC, later than the leap seconds announced so far (to mid 2027), so TT may be off by up to 8 s of leap seconds not yet known (ERFA calls the year dubious); UT1 is taken as UTC, within 0.9 s, if the IERS keeps it so."], "verifiedByIndependentHoldout": false}, "raDeg": 228.80467622638065, "decDeg": -16.27174365079576, "altDeg": 51.92244879490417, "azDeg": 162.05934338563966, "aboveHorizon": true, "distanceAu": 0.7957755339625638, "distancePc": 3.858028659572971e-06, "lightTravel": {"distancePc": 3.858028659572971e-06, "lightYears": 1.2583206526691068e-05, "lightMinutes": 6.618263304778434, "lightSeconds": 397.09579828670604, "description": "6.6 light-minutes", "formatted": "6.6 light-minutes"}},
  {"id": "finding-predicted-moon", "evidenceClass": "predicted", "name": "Moon", "title": "Moon", "subtitle": "Predicted Moon \u00b7 0.003 AU \u00b7 Alt -20.7\u00b0", "skyPosition": {"raDeg": 326.4343270956605, "decDeg": -8.714301238766776, "uncertaintyArcsec": 42.59469242363787, "altDeg": -20.717217486977177, "azDeg": 91.82611362125431}, "inferredDistancePc": {"lower": 1.2128846666647029e-08, "median": 1.2130165663817831e-08, "upper": 1.2131484660988636e-08, "method": "ephemeris_au"}, "cartesianParsecICRS": [9.990812682628542e-09, -6.629255563029191e-09, -1.8378116476778323e-09], "evidence": {"id": "ev-ephem-moon", "evidenceClass": "predicted", "verificationMethod": "Meeus (1998) chapter 47 in full, as ERFA's eraMoon98, for the Moon and ERFA eraEpv00 for the Earth, with light time", "distanceUncertaintyNote": "Topocentric distance 0.002502 AU, between 0.002502 and 0.002502 AU from the method's error bounds", "limitations": ["Predicted from the given time and place with ERFA's analytic theories; not measured in any image.", "Seen from the given place at sea level (topocentric): its parallax is applied, and its right ascension and declination are astrometric (light time only).", "Its altitude and azimuth are apparent and airless: refraction at 1010 hPa and 10 degrees C (Saemundsson 1986) would lift it to -20.07 degrees, and the real air differs most near the horizon.", "Its uncertainty, 42.6 arcsec, adds the method's documented worst error (18.2 arcsec here), 0.01 for light deflection applied as for a star, 13.8 for UT1 - UTC taken as 0 (within 0.9 s), 5.0 for a place up to 9 km above sea level, 1 for polar motion, and 4.7 for the time scale.", "The moment is UTC, later than the leap seconds announced so far (to mid 2027), so TT may be off by up to 8 s of leap seconds not yet known (ERFA calls the year dubious); UT1 is taken as UTC, within 0.9 s, if the IERS keeps it so."], "verifiedByIndependentHoldout": false}, "raDeg": 326.4343270956605, "decDeg": -8.714301238766776, "altDeg": -20.717217486977177, "azDeg": 91.82611362125431, "aboveHorizon": false, "distanceAu": 0.002502026270392566, "distancePc": 1.2130165663817831e-08, "lightTravel": {"distancePc": 1.2130165663817831e-08, "lightYears": 3.95633089381174e-08, "lightMinutes": 0.020808717969092227, "lightSeconds": 1.2485230781455336, "description": "1.25 light-seconds", "formatted": "1.25 light-seconds"}}
 ],
 "constellationEdges": [
  {"constellation": "Lyra", "fromStarId": "star-vega", "toStarId": "star-sheliak"},
  {"constellation": "Lyra", "fromStarId": "star-sheliak", "toStarId": "star-sulafat"},
  {"constellation": "Lyra", "fromStarId": "star-sulafat", "toStarId": "star-vega"},
  {"constellation": "Ursa Major", "fromStarId": "star-phecda", "toStarId": "star-megrez"}
 ],
 "predictedSkyBrightness": {"skyBrightnessMagArcsec2": 21.6, "darkSkyMagArcsec2": 21.6, "lunarVeilDeltaMag": 0.0, "skyBrightnessNanoLamberts": 78.07, "lunarScatteredNanoLamberts": 0.0, "airmass": 1.0, "illuminationFraction": 0.0888, "lunarPhaseName": "Waning Crescent", "sepFromMoonDeg": 110.72, "target": "zenith", "targetAltDeg": 90.0, "moonAltDeg": -20.72, "evidenceClass": "predicted", "method": "Krisciunas and Schaefer (1991, PASP 103, 1033) moonlit sky brightness model", "limitations": ["Predicted for the time and place; not measured in any image.", "Assumes a V-band extinction of 0.20 mag per airmass and a dark-sky zenith of 21.6 mag/arcsec2, site values the model needs and nothing measured here; light pollution is not modelled."]},
 "sun": {"altDeg": -47.73332301004936, "band": "night", "evidenceClass": "predicted", "method": "ERFA eraEpv00 (simplified VSOP2000) for the Earth and the Sun, with light time", "limitations": ["Predicted from the given time and place with ERFA's analytic theories; not measured in any image.", "Seen from the given place at sea level (topocentric): its parallax is applied, and its right ascension and declination are astrometric (light time only).", "Its altitude and azimuth are apparent and airless: refraction at 1010 hPa and 10 degrees C (Saemundsson 1986) would lift it to -47.09 degrees, and the real air differs most near the horizon.", "Its uncertainty, 14.9 arcsec, adds the method's documented worst error (0.0 arcsec here), 0.00 for light deflection applied as for a star, 13.5 for UT1 - UTC taken as 0 (within 0.9 s), 0.0 for a place up to 9 km above sea level, 1 for polar motion, and 0.3 for the time scale.", "The moment is UTC, later than the leap seconds announced so far (to mid 2027), so TT may be off by up to 8 s of leap seconds not yet known (ERFA calls the year dubious); UT1 is taken as UTC, within 0.9 s, if the IERS keeps it so."]}
}
""")


# A reply the route made at a moment the Moon's geocentric phase sits within its bound of a sector's edge, so its name is
# null (skycontext-1b, the read of skycontext-2 v2): at 0 N 0 E (open ocean), with magLimit -30 and aboveHorizonOnly
EDGE_MOMENT = "1932-11-23T03:21:22.768Z"
BASE_EDGE: dict[str, Any] = json.loads(r"""
{
 "format": "albedian.sky-context",
 "formatVersion": 1,
 "observedAtUTC": "1932-11-23T03:21:22.768Z",
 "observerLocation": {"latDeg": 0.0, "lonDeg": 0.0},
 "filters": {"aboveHorizonOnly": true, "magLimit": -30.0},
 "convention": {"frame": "topocentric_sea_level", "azimuth": "true_north_through_east", "altitude": "apparent_airless"},
 "stars": [
 ],
 "ephemerides": [
  {"id": "finding-predicted-jupiter", "evidenceClass": "predicted", "title": "Jupiter", "subtitle": "Predicted Planet \u00b7 5.655 AU \u00b7 Alt +30.8\u00b0", "skyPosition": {"raDeg": 172.1970714236331, "decDeg": 4.551810187353775, "uncertaintyArcsec": 79.25434559123974, "altDeg": 30.775821476405497, "azDeg": 84.26851705021758}, "inferredDistancePc": {"lower": 2.740126743704818e-05, "median": 2.741386678324187e-05, "upper": 2.7426466129435565e-05, "method": "ephemeris_au"}, "cartesianParsecICRS": [-2.707437577525056e-05, 3.710137988926465e-06, 2.175579116691124e-06], "evidence": {"id": "ev-ephem-jupiter", "evidenceClass": "predicted", "verificationMethod": "ERFA eraPlan94 (Simon et al. 1994) for the planet and eraEpv00 (simplified VSOP2000) for the Earth and the Sun, with light time", "distanceUncertaintyNote": "Topocentric distance 5.654516 AU, between 5.651917 and 5.657115 AU from the method's error bounds", "limitations": ["Predicted from the given time and place with ERFA's analytic theories; not measured in any image.", "Seen from the given place at sea level (topocentric): its parallax is applied, and its right ascension and declination are astrometric (light time only).", "Its altitude and azimuth are apparent and airless: refraction at 1010 hPa and 10 degrees C (Saemundsson 1986) would lift it to +30.80 degrees, and the real air differs most near the horizon.", "Its uncertainty, 79.3 arcsec, adds the method's documented worst error (78.2 arcsec here), 0.01 for light deflection applied as for a star, 0.0 for the Earth's rotation, with the moment taken as UT1, 0.0 for a place up to 9 km above sea level, 1 for polar motion, and 0.0 for the time scale.", "A moment before 1960, when there was no UTC, is taken as UT1, and TT - UT1 comes from Espenak and Meeus (2006), within 0.3 s."], "verifiedByIndependentHoldout": false}, "name": "Jupiter", "raDeg": 172.1970714236331, "decDeg": 4.551810187353775, "altDeg": 30.775821476405497, "azDeg": 84.26851705021758, "aboveHorizon": true, "distanceAu": 5.654515920529096, "distancePc": 2.741386678324187e-05, "lightTravel": {"distancePc": 2.741386678324187e-05, "lightYears": 8.94120748877252e-05, "lightMinutes": 47.027174907947945, "lightSeconds": 2821.6304944768767, "description": "47.0 light-minutes", "formatted": "47.0 light-minutes"}},
  {"id": "finding-predicted-mars", "evidenceClass": "predicted", "title": "Mars", "subtitle": "Predicted Planet \u00b7 1.364 AU \u00b7 Alt +44.0\u00b0", "skyPosition": {"raDeg": 157.88462404722438, "decDeg": 11.483435324593255, "uncertaintyArcsec": 37.64697111462634, "altDeg": 44.0240889709815, "azDeg": 73.43598241069904}, "inferredDistancePc": {"lower": 6.610195393266662e-06, "median": 6.611485216772477e-06, "upper": 6.6127750402782915e-06, "method": "ephemeris_au"}, "cartesianParsecICRS": [-6.002452953864493e-06, 2.439220022318871e-06, 1.3162450345445366e-06], "evidence": {"id": "ev-ephem-mars", "evidenceClass": "predicted", "verificationMethod": "ERFA eraPlan94 (Simon et al. 1994) for the planet and eraEpv00 (simplified VSOP2000) for the Earth and the Sun, with light time", "distanceUncertaintyNote": "Topocentric distance 1.363717 AU, between 1.363451 and 1.363983 AU from the method's error bounds", "limitations": ["Predicted from the given time and place with ERFA's analytic theories; not measured in any image.", "Seen from the given place at sea level (topocentric): its parallax is applied, and its right ascension and declination are astrometric (light time only).", "Its altitude and azimuth are apparent and airless: refraction at 1010 hPa and 10 degrees C (Saemundsson 1986) would lift it to +44.04 degrees, and the real air differs most near the horizon.", "Its uncertainty, 37.6 arcsec, adds the method's documented worst error (36.6 arcsec here), 0.00 for light deflection applied as for a star, 0.0 for the Earth's rotation, with the moment taken as UT1, 0.0 for a place up to 9 km above sea level, 1 for polar motion, and 0.0 for the time scale.", "A moment before 1960, when there was no UTC, is taken as UT1, and TT - UT1 comes from Espenak and Meeus (2006), within 0.3 s."], "verifiedByIndependentHoldout": false}, "name": "Mars", "raDeg": 157.88462404722438, "decDeg": 11.483435324593255, "altDeg": 44.0240889709815, "azDeg": 73.43598241069904, "aboveHorizon": true, "distanceAu": 1.3637167172431168, "distancePc": 6.611485216772477e-06, "lightTravel": {"distancePc": 6.611485216772477e-06, "lightYears": 2.1563780695196105e-05, "lightMinutes": 11.341686094445343, "lightSeconds": 680.5011656667206, "description": "11.3 light-minutes", "formatted": "11.3 light-minutes"}},
  {"id": "finding-predicted-moon", "evidenceClass": "predicted", "title": "Moon", "subtitle": "Predicted Moon \u00b7 0.002 AU \u00b7 Alt +28.0\u00b0", "skyPosition": {"raDeg": 175.10690844650009, "decDeg": 1.6104873656922154, "uncertaintyArcsec": 24.647758916203372, "altDeg": 27.971179912627477, "azDeg": 87.75427314824722}, "inferredDistancePc": {"lower": 1.1992335281762433e-08, "median": 1.1993654278933237e-08, "upper": 1.199497327610404e-08, "method": "ephemeris_au"}, "cartesianParsecICRS": [-1.1945224085718357e-08, 1.022616081205795e-09, 3.370769274318575e-10], "evidence": {"id": "ev-ephem-moon", "evidenceClass": "predicted", "verificationMethod": "Meeus (1998) chapter 47 in full, as ERFA's eraMoon98, for the Moon and ERFA eraEpv00 for the Earth, with light time", "distanceUncertaintyNote": "Topocentric distance 0.002474 AU, between 0.002474 and 0.002474 AU from the method's error bounds", "limitations": ["Predicted from the given time and place with ERFA's analytic theories; not measured in any image.", "Seen from the given place at sea level (topocentric): its parallax is applied, and its right ascension and declination are astrometric (light time only).", "Its altitude and azimuth are apparent and airless: refraction at 1010 hPa and 10 degrees C (Saemundsson 1986) would lift it to +28.00 degrees, and the real air differs most near the horizon.", "Its uncertainty, 24.6 arcsec, adds the method's documented worst error (18.5 arcsec here), 0.01 for light deflection applied as for a star, 0.0 for the Earth's rotation, with the moment taken as UT1, 5.0 for a place up to 9 km above sea level, 1 for polar motion, and 0.2 for the time scale.", "A moment before 1960, when there was no UTC, is taken as UT1, and TT - UT1 comes from Espenak and Meeus (2006), within 0.3 s."], "verifiedByIndependentHoldout": false}, "name": "Moon", "raDeg": 175.10690844650009, "decDeg": 1.6104873656922154, "altDeg": 27.971179912627477, "azDeg": 87.75427314824722, "aboveHorizon": true, "distanceAu": 0.0024738687760388223, "distancePc": 1.1993654278933237e-08, "lightTravel": {"distancePc": 1.1993654278933237e-08, "lightYears": 3.9118068350029703e-08, "lightMinutes": 0.020574539229381624, "lightSeconds": 1.2344723537628974, "description": "1.23 light-seconds", "formatted": "1.23 light-seconds"}}
 ],
 "constellationEdges": [
 ],
 "predictedSkyBrightness": {"skyBrightnessMagArcsec2": 21.127, "darkSkyMagArcsec2": 21.6, "lunarVeilDeltaMag": 0.473, "skyBrightnessNanoLamberts": 120.64, "lunarScatteredNanoLamberts": 42.57, "airmass": 1.0, "illuminationFraction": 0.3098, "lunarPhaseName": null, "sepFromMoonDeg": 62.03, "target": "zenith", "targetAltDeg": 90.0, "moonAltDeg": 27.97, "evidenceClass": "predicted", "method": "Krisciunas and Schaefer (1991, PASP 103, 1033) moonlit sky brightness model", "limitations": ["Predicted for the time and place; not measured in any image.", "Assumes a V-band extinction of 0.20 mag per airmass and a dark-sky zenith of 21.6 mag/arcsec2, site values the model needs and nothing measured here; light pollution is not modelled."]}
}
""")

BASE_REQUEST = {"observedAtUTC": "2031-03-20T12:00:00Z", "latDeg": 20.0, "lonDeg": -140.0}
# Where each base row sits, by id, so a sample names the row it changes
STAR = {star["id"]: i for i, star in enumerate(BASE_REPLY["stars"])}
BODY = {body["name"]: i for i, body in enumerate(BASE_REPLY["ephemerides"])}
VEGA, SHELIAK, SULAFAT, PHECDA, MEGREZ, CASTOR = (STAR[f"star-{name}"] for name in ("vega", "sheliak", "sulafat", "phecda", "megrez", "castor"))
MARS, MOON = BODY["Mars"], BODY["Moon"]
_DROP = object()


def _apply(document: Any, changes: list[dict[str, Any]]) -> Any:
    """The document with each change made in turn: a value set at a path (at a list's length, appended), or dropped."""
    out = copy.deepcopy(document)
    for change in changes:
        path = change["path"]
        if not path:
            out = copy.deepcopy(change["value"])
            continue
        target = out
        for key in path[:-1]:
            target = target[key]
        last = path[-1]
        if change.get("drop"):
            del target[last]
        elif isinstance(target, list) and last == len(target):
            target.append(copy.deepcopy(change["value"]))
        else:
            target[last] = copy.deepcopy(change["value"])
    return out


def _set(path: list[Any], value: Any) -> dict[str, Any]:
    return {"path": path, "drop": True} if value is _DROP else {"path": path, "value": value}


def _kept(reply: dict[str, Any], keep: Any, filters: dict[str, Any]) -> dict[str, Any]:
    """The reply with only the stars and bodies `keep` keeps, the edges between kept stars, and the filters stated."""
    out = copy.deepcopy(reply)
    out["stars"] = [star for star in out["stars"] if keep(star)]
    out["ephemerides"] = [body for body in out["ephemerides"] if "mag" in body or keep(body)]
    held = {star["id"] for star in out["stars"]}
    out["constellationEdges"] = [edge for edge in out["constellationEdges"] if {edge["fromStarId"], edge["toStarId"]} <= held]
    out["filters"] = filters
    return out


def _above(item: dict[str, Any]) -> bool:
    return item["altDeg"] >= 0.0


ABOVE_ONLY = _kept(BASE_REPLY, _above, {"aboveHorizonOnly": True})
MAG_TWO = _kept(BASE_REPLY, lambda item: "mag" not in item or item["mag"] <= 2.0, {"magLimit": 2.0, "aboveHorizonOnly": False})
MAG_TWO["ephemerides"] = copy.deepcopy(BASE_REPLY["ephemerides"])
EMPTY = {**copy.deepcopy(BASE_REPLY), "stars": [], "ephemerides": [], "constellationEdges": []}
# A reply made before the Sun was added (skycontext-3), as an older laptop still answers
NO_SUN = {key: copy.deepcopy(value) for key, value in BASE_REPLY.items() if key != "sun"}
# A star without a Bayer designation in the shipped tables omits bayerDesignation rather than inventing one (skycontext-5a)
NO_BAYER = _apply(BASE_REPLY, [_set(["stars", SULAFAT, "bayerDesignation"], _DROP)])
BASES: dict[str, tuple[str, Any]] = {"request": ("request", BASE_REQUEST), "reply": ("reply", BASE_REPLY)}


def valid_samples() -> list[dict[str, Any]]:
    requests = [
        ("the request at its base", BASE_REQUEST),
        ("a request with both filters", {**BASE_REQUEST, "magLimit": 2.0, "aboveHorizonOnly": True}),
        ("a request with aboveHorizonOnly false", {**BASE_REQUEST, "aboveHorizonOnly": False}),
        ("a request with milliseconds", {**BASE_REQUEST, "observedAtUTC": "2031-03-20T12:00:00.250Z"}),
        ("a request at the span's first instant", {**BASE_REQUEST, "observedAtUTC": SPAN[0]}),
        ("a request at the span's last millisecond", {**BASE_REQUEST, "observedAtUTC": "2050-12-31T23:59:59.999Z"}),
        ("a request at a pole and the antimeridian", {**BASE_REQUEST, "latDeg": -90.0, "lonDeg": 180.0}),
    ]
    replies = [
        ("the reply at its base: six stars, two bodies, four edges", BASE_REPLY),
        ("a reply with aboveHorizonOnly: the stars and the body below the horizon left out, and their edges", ABOVE_ONLY),
        ("a reply with a magLimit of 2: the fainter stars left out, and their edges, the bodies kept", MAG_TWO),
        ("a reply with no star, no body and no edge", EMPTY),
        ("a reply without the Sun, as one made before the Sun was added", NO_SUN),
        ("a reply with a star that has no Bayer designation", NO_BAYER),
    ]
    return [{"name": name, "kind": "request", "body": body} for name, body in requests] + [
        {"name": name, "kind": "reply", "body": body} for name, body in replies
    ]


def _star(i: int, *path: Any) -> list[Any]:
    return ["stars", i, *path]


def _body(i: int, *path: Any) -> list[Any]:
    return ["ephemerides", i, *path]


ASTRO_OF_MEGREZ = BASE_REPLY["stars"][MEGREZ]["astrophysics"]
# (name, base, changes, the rule it breaks or None)
FAULTS: list[tuple[str, str, list[dict[str, Any]], str | None]] = [
    ("a request with an unknown key", "request", [_set(["unknownKey"], 1)], None),
    ("a request with observedAtUTC under its Python name", "request", [_set(["observedAtUTC"], _DROP), _set(["observed_at_utc"], BASE_REQUEST["observedAtUTC"])], None),
    ("a request with no latDeg", "request", [_set(["latDeg"], _DROP)], None),
    ("a request with a null magLimit", "request", [_set(["magLimit"], None)], None),
    ("a request with aboveHorizonOnly as text", "request", [_set(["aboveHorizonOnly"], "true")], None),
    ("a request with a moment without Z", "request", [_set(["observedAtUTC"], "2031-03-20T12:00:00")], None),
    ("a request with a moment with an offset", "request", [_set(["observedAtUTC"], "2031-03-20T12:00:00+00:00")], None),
    ("a request before the span", "request", [_set(["observedAtUTC"], "1799-12-31T23:59:59.999Z")], "insideEphemerisSpan"),
    ("a request after the span", "request", [_set(["observedAtUTC"], SPAN[1])], "insideEphemerisSpan"),
    ("today's reply: its snake keys kept, no format and no convention", "reply", [
        _set(["format"], _DROP), _set(["formatVersion"], _DROP), _set(["filters"], _DROP), _set(["convention"], _DROP),
        _set(_star(VEGA, "lightTravel", "light_years"), BASE_REPLY["stars"][VEGA]["lightTravel"]["lightYears"])], None),
    ("a body with today's evidence_class key", "reply", [_set(_body(MARS, "evidence_class"), "predicted")], None),
    ("another format", "reply", [_set(["format"], "albedian.sky")], None),
    ("format version 2", "reply", [_set(["formatVersion"], 2)], None),
    ("an azimuth from south", "reply", [_set(["convention", "azimuth"], "south_through_west")], None),
    ("refraction applied", "reply", [_set(["convention", "altitude"], "apparent_refracted")], None),
    ("no filters", "reply", [_set(["filters"], _DROP)], None),
    ("filters without aboveHorizonOnly", "reply", [_set(["filters"], {})], None),
    ("a star said to be observed", "reply", [_set(_star(VEGA, "evidenceClass"), "observed")], None),
    ("a star with no evidence class", "reply", [_set(_star(VEGA, "evidenceClass"), _DROP)], None),
    ("a body said to be observed", "reply", [_set(_body(MARS, "evidenceClass"), "observed")], None),
    ("a body's evidence said to be catalog matched", "reply", [_set(_body(MARS, "evidence", "evidenceClass"), "catalog_matched")], None),
    ("a body said verified by a held-out check", "reply", [_set(_body(MARS, "evidence", "verifiedByIndependentHoldout"), True)], None),
    ("the brightness said to be observed", "reply", [_set(["predictedSkyBrightness", "evidenceClass"], "observed")], None),
    ("the Sun said to be observed", "reply", [_set(["sun", "evidenceClass"], "observed")], None),
    ("the Sun with no evidence class", "reply", [_set(["sun", "evidenceClass"], _DROP)], None),
    ("the Sun with a band no definition names", "reply", [_set(["sun", "band"], "dusk")], None),
    ("the Sun with no band", "reply", [_set(["sun", "band"], _DROP)], None),
    ("the Sun with no method", "reply", [_set(["sun", "method"], _DROP)], None),
    ("the Sun with no limitations", "reply", [_set(["sun", "limitations"], [])], None),
    ("the Sun with an azimuth this contract does not carry", "reply", [_set(["sun", "azDeg"], 90.0)], None),
    ("the Sun under its Python name", "reply", [_set(["sun", "altDeg"], _DROP), _set(["sun", "alt_deg"], BASE_REPLY["sun"]["altDeg"])], None),
    ("a null Sun", "reply", [_set(["sun"], None)], None),
    ("a null Bayer designation", "reply", [_set(_star(VEGA, "bayerDesignation"), None)], None),
    ("a star with no releaseVersion", "reply", [_set(_star(VEGA, "releaseVersion"), _DROP)], None),
    ("a star from another catalog", "reply", [_set(_star(VEGA, "provider"), "Tycho-2")], None),
    ("a star with a body's distance method", "reply", [_set(_star(VEGA, "distancePc", "method"), "ephemeris_au")], None),
    ("a body with a star's distance method", "reply", [_set(_body(MARS, "inferredDistancePc", "method"), "geometric_parallax")], None),
    ("a star with a spectral type no catalog row carries", "reply", [_set(_star(VEGA, "spectralType"), "A0V")], None),
    ("a star with two cartesian numbers", "reply", [_set(_star(VEGA, "cartesianParsecICRS"), BASE_REPLY["stars"][VEGA]["cartesianParsecICRS"][:2])], None),
    ("a star with no distance", "reply", [_set(_star(VEGA, "distancePc"), _DROP), _set(_star(VEGA, "cartesianParsecICRS"), _DROP), _set(_star(VEGA, "lightTravel"), _DROP)], None),
    ("a star id with a capital", "reply", [_set(_star(VEGA, "id"), "star-Vega")], None),
    ("a body's name in lower case", "reply", [_set(_body(MARS, "name"), "mars")], None),
    ("a star's name with a control character", "reply", [_set(_star(VEGA, "commonName"), "Ve\u0007ga")], None),
    ("a star's name with a lone surrogate", "reply", [_set(_star(VEGA, "commonName"), "Vega\ud800")], None),
    ("an empty limitation list", "reply", [_set(["predictedSkyBrightness", "limitations"], [])], None),
    ("a phase with another name", "reply", [_set(["predictedSkyBrightness", "lunarPhaseName"], "Blue Moon")], None),
    ("a phase name left out", "reply", [_set(["predictedSkyBrightness", "lunarPhaseName"], _DROP)], None),
    ("the brightness at another target", "reply", [_set(["predictedSkyBrightness", "target"], "horizon")], None),
    ("a radius not said to be derived", "reply", [_set(_star(MEGREZ, "astrophysics", "radiusSolar", "derived"), False)], None),
    ("an interval of another kind", "reply", [_set(_star(MEGREZ, "astrophysics", "teffK", "interval"), "one sigma")], None),
    ("a reply before the span", "reply", [_set(["observedAtUTC"], "1799-12-31T23:59:59.999Z")], "insideEphemerisSpan"),
    ("a star above the horizon said to be below", "reply", [_set(_star(VEGA, "aboveHorizon"), False)], "aboveHorizonIsAltitude"),
    ("a star below the horizon said to be above", "reply", [_set(_star(CASTOR, "aboveHorizon"), True)], "aboveHorizonIsAltitude"),
    ("a body below the horizon said to be above", "reply", [_set(_body(MOON, "aboveHorizon"), True)], "aboveHorizonIsAltitude"),
    ("aboveHorizonOnly with a star and a body below", "reply", [_set(["filters", "aboveHorizonOnly"], True)], "aboveHorizonOnlyHeld"),
    ("aboveHorizonOnly with a body below", "reply", [_set(["ephemerides", len(ABOVE_ONLY["ephemerides"])], BASE_REPLY["ephemerides"][MOON])], "aboveHorizonOnlyHeld"),
    ("aboveHorizonOnly with a star half a degree below", "reply", [_set(["stars", len(ABOVE_ONLY["stars"])], BASE_REPLY["stars"][CASTOR])], "aboveHorizonOnlyHeld"),
    ("a magLimit with fainter stars", "reply", [_set(["filters", "magLimit"], 2.0)], "magLimitHeld"),
    ("a star held twice", "reply", [_set(["stars", len(BASE_REPLY["stars"])], BASE_REPLY["stars"][VEGA])], "idsOnce"),
    ("a body held twice", "reply", [_set(["ephemerides", len(BASE_REPLY["ephemerides"])], BASE_REPLY["ephemerides"][MARS])], "idsOnce"),
    ("an edge to a star not held", "reply", [_set(["constellationEdges", 0, "toStarId"], "star-deneb")], "edgeJoinsHeldStars"),
    ("an edge from a star to itself", "reply", [_set(["constellationEdges", 0, "toStarId"], BASE_REPLY["constellationEdges"][0]["fromStarId"])], "edgeJoinsHeldStars"),
    ("a Hipparcos star of the Gaia release", "reply", [_set(_star(VEGA, "releaseVersion"), "Gaia DR3")], "releaseOfProvider"),
    ("a Gaia star of the Hipparcos release", "reply", [_set(_star(MEGREZ, "releaseVersion"), "Hipparcos 2007")], "releaseOfProvider"),
    ("a Hipparcos star with a Gaia id", "reply", [_set(_star(VEGA, "catalogSourceId"), BASE_REPLY["stars"][MEGREZ]["catalogSourceId"])], "sourceIdOfProvider"),
    ("a Hipparcos id of 0", "reply", [_set(_star(VEGA, "catalogSourceId"), "HIP 0")], "sourceIdOfProvider"),
    ("a Gaia id with a leading zero", "reply", [_set(_star(MEGREZ, "catalogSourceId"), "Gaia DR3 0123"), _set(_star(MEGREZ, "astrophysics", "catalogSourceId"), "Gaia DR3 0123")], "sourceIdOfProvider"),
    ("a star's lower distance past its median", "reply", [_set(_star(VEGA, "distancePc", "lower"), BASE_REPLY["stars"][VEGA]["distancePc"]["median"] * 1.01)], "distanceInOrder"),
    ("a star's median distance past its upper", "reply", [_set(_star(VEGA, "distancePc", "upper"), BASE_REPLY["stars"][VEGA]["distancePc"]["median"] * 0.99)], "distanceInOrder"),
    ("a body's lower distance past its median", "reply", [_set(_body(MARS, "inferredDistancePc", "lower"), BASE_REPLY["ephemerides"][MARS]["inferredDistancePc"]["median"] * 1.01)], "distanceInOrder"),
    ("a star's light travel for another distance", "reply", [_set(_star(VEGA, "lightTravel", "distancePc"), 1.0)], "lightTravelOfDistance"),
    ("a body's light travel for another distance", "reply", [_set(_body(MARS, "lightTravel", "distancePc"), 1.0)], "lightTravelOfDistance"),
    ("astrophysics on a Hipparcos star", "reply", [_set(_star(VEGA, "astrophysics"), {**ASTRO_OF_MEGREZ, "catalogSourceId": BASE_REPLY["stars"][VEGA]["catalogSourceId"]})], "astrophysicsOfGaia"),
    ("astrophysics under another star's id", "reply", [_set(_star(SHELIAK, "astrophysics", "catalogSourceId"), BASE_REPLY["stars"][MEGREZ]["catalogSourceId"])], "astrophysicsOfGaia"),
    ("astrophysics with no temperature and no luminosity", "reply", [_set(_star(SHELIAK, "astrophysics", "teffK"), _DROP)], "astrophysicsHoldsAValue"),
    ("an estimate with bounds and no interval", "reply", [_set(_star(MEGREZ, "astrophysics", "teffK", "interval"), _DROP)], "estimateInterval"),
    ("an estimate with an interval and no lower", "reply", [_set(_star(MEGREZ, "astrophysics", "teffK", "lower"), _DROP)], "estimateInterval"),
    ("an estimate below its lower", "reply", [_set(_star(MEGREZ, "astrophysics", "teffK", "lower"), ASTRO_OF_MEGREZ["teffK"]["value"] + 1.0)], "estimateInterval"),
    ("an estimate above its upper", "reply", [_set(_star(MEGREZ, "astrophysics", "teffK", "upper"), ASTRO_OF_MEGREZ["teffK"]["value"] - 0.5)], "estimateInterval"),
    ("a radius without the luminosity", "reply", [_set(_star(MEGREZ, "astrophysics", "luminositySolar"), _DROP)], "radiusFromBoth"),
    ("a body's right ascension not its sky position's", "reply", [_set(_body(MARS, "raDeg"), BASE_REPLY["ephemerides"][MARS]["raDeg"] + 0.001)], "positionIsSkyPosition"),
    ("a body's altitude not its sky position's", "reply", [_set(_body(MARS, "skyPosition", "altDeg"), BASE_REPLY["ephemerides"][MARS]["altDeg"] + 0.001)], "positionIsSkyPosition"),
    ("a body's distance not its inferred median", "reply", [_set(_body(MARS, "distancePc"), BASE_REPLY["ephemerides"][MARS]["distancePc"] * 1.01)], "distanceIsInferred"),
    ("a body's id of another name", "reply", [_set(_body(MARS, "id"), "finding-predicted-venus")], "idsOfName"),
    ("a body's evidence id of another name", "reply", [_set(_body(MARS, "evidence", "id"), "ev-ephem-venus")], "idsOfName"),
    ("the Sun deep below the horizon said to be day", "reply", [_set(["sun", "band"], "day")], "sunBandOfAltitude"),
    ("the Sun high in the sky said to be night", "reply", [_set(["sun", "altDeg"], 30.0)], "sunBandOfAltitude"),
    ("the Sun in civil twilight said to be nautical", "reply", [_set(["sun", "altDeg"], -3.0), _set(["sun", "band"], "nautical_twilight")], "sunBandOfAltitude"),
]  # fmt: skip


def _faults_base(base: str) -> Any:
    return {"reply-above": ABOVE_ONLY, "reply-edge": BASE_EDGE}.get(base) or BASES[base][1]


# The aboveHorizonOnly fault that appends the Moon starts from the reply with aboveHorizonOnly
_FAULT_BASES = {"aboveHorizonOnly with a body below": "reply-above", "aboveHorizonOnly with a star half a degree below": "reply-above"}


def invalid_samples() -> list[dict[str, Any]]:
    out = []
    for name, base, changes, rule in FAULTS:
        sample: dict[str, Any] = {"name": name, "kind": base, "base": _FAULT_BASES.get(name, base), "changes": changes}
        if rule is not None:
            sample["message"] = RULES[rule]
        out.append(sample)
    return out


LONG = "a" * 64
# The bounds, one table: (name, base, the changes inside, the changes outside)
EDGES: list[tuple[str, str, list[dict[str, Any]], list[dict[str, Any]]]] = [
    ("latDeg at 90", "request", [_set(["latDeg"], 90.0)], [_set(["latDeg"], 90.000001)]),
    ("latDeg at -90", "request", [_set(["latDeg"], -90.0)], [_set(["latDeg"], -90.000001)]),
    ("lonDeg at 180", "request", [_set(["lonDeg"], 180.0)], [_set(["lonDeg"], 180.000001)]),
    ("lonDeg at -180", "request", [_set(["lonDeg"], -180.0)], [_set(["lonDeg"], -180.000001)]),
    ("magLimit at 30", "request", [_set(["magLimit"], 30.0)], [_set(["magLimit"], 30.000001)]),
    ("magLimit at -30", "request", [_set(["magLimit"], -30.0)], [_set(["magLimit"], -30.000001)]),
    ("the request at the span's start", "request", [_set(["observedAtUTC"], SPAN[0])], [_set(["observedAtUTC"], "1799-12-31T23:59:59.999Z")]),
    ("the request at the span's end", "request", [_set(["observedAtUTC"], "2050-12-31T23:59:59.999Z")], [_set(["observedAtUTC"], SPAN[1])]),
    ("the reply at the span's start", "reply", [_set(["observedAtUTC"], SPAN[0])], [_set(["observedAtUTC"], "1799-12-31T23:59:59.999Z")]),
    ("the reply at the span's end", "reply", [_set(["observedAtUTC"], "2050-12-31T23:59:59.999Z")], [_set(["observedAtUTC"], SPAN[1])]),
    ("a star at altitude 0 above the horizon", "reply", [_set(_star(CASTOR, "altDeg"), 0.0), _set(_star(CASTOR, "aboveHorizon"), True)],
     [_set(_star(CASTOR, "altDeg"), -0.000001), _set(_star(CASTOR, "aboveHorizon"), True)]),
    ("a star's altitude at 90", "reply", [_set(_star(VEGA, "altDeg"), 90.0)], [_set(_star(VEGA, "altDeg"), 90.000001)]),
    ("a star's right ascension at 0", "reply", [_set(_star(VEGA, "raDeg"), 0.0)], [_set(_star(VEGA, "raDeg"), -0.000001)]),
    ("a star's right ascension at 360", "reply", [_set(_star(VEGA, "raDeg"), 360.0)], [_set(_star(VEGA, "raDeg"), 360.000001)]),
    ("a star's azimuth at 360", "reply", [_set(_star(VEGA, "azDeg"), 360.0)], [_set(_star(VEGA, "azDeg"), 360.000001)]),
    ("a star's declination at -90", "reply", [_set(_star(VEGA, "decDeg"), -90.0)], [_set(_star(VEGA, "decDeg"), -90.000001)]),
    ("a star's mag at 30", "reply", [_set(_star(VEGA, "mag"), 30.0)], [_set(_star(VEGA, "mag"), 30.000001)]),
    ("a star's mag at the magLimit", "reply", [_set(["filters", "magLimit"], 3.52)], [_set(["filters", "magLimit"], 3.519999)]),
    ("a star's proper motion at 20,000 mas/yr", "reply", [_set(_star(VEGA, "properMotionRaMasYr"), -20_000.0)], [_set(_star(VEGA, "properMotionRaMasYr"), -20_000.001)]),
    ("a star's radial velocity at 2,000 km/s", "reply", [_set(_star(SHELIAK, "radialVelocityKmS"), 2_000.0)], [_set(_star(SHELIAK, "radialVelocityKmS"), 2_000.001)]),
    ("a star's BP-RP at -2", "reply", [_set(_star(SHELIAK, "bpRp"), -2.0)], [_set(_star(SHELIAK, "bpRp"), -2.000001)]),
    ("a star's BP-RP at 10", "reply", [_set(_star(SHELIAK, "bpRp"), 10.0)], [_set(_star(SHELIAK, "bpRp"), 10.000001)]),
    ("a distance's lower at its median", "reply", [_set(_star(VEGA, "distancePc", "lower"), BASE_REPLY["stars"][VEGA]["distancePc"]["median"])],
     [_set(_star(VEGA, "distancePc", "lower"), 0.0)]),
    ("an estimate's lower at its value", "reply", [_set(_star(MEGREZ, "astrophysics", "teffK", "lower"), ASTRO_OF_MEGREZ["teffK"]["value"])],
     [_set(_star(MEGREZ, "astrophysics", "teffK", "lower"), ASTRO_OF_MEGREZ["teffK"]["value"] + 0.000001)]),
    ("a Hipparcos id of six digits", "reply", [_set(_star(VEGA, "catalogSourceId"), "HIP 999999")], [_set(_star(VEGA, "catalogSourceId"), "HIP 1000000")]),
    ("a star's name of 64 units", "reply", [_set(_star(VEGA, "commonName"), LONG)], [_set(_star(VEGA, "commonName"), LONG + "a")]),
    ("a star's name of 64 units with an astral character", "reply", [_set(_star(VEGA, "commonName"), LONG[:62] + "\U0001f30c")],
     [_set(_star(VEGA, "commonName"), LONG[:63] + "\U0001f30c")]),
    ("an empty name", "reply", [_set(_star(VEGA, "commonName"), "V")], [_set(_star(VEGA, "commonName"), "")]),
    ("a star's Bayer designation of 64 units", "reply", [_set(_star(VEGA, "bayerDesignation"), LONG)], [_set(_star(VEGA, "bayerDesignation"), LONG + "a")]),
    ("an empty Bayer designation", "reply", [_set(_star(VEGA, "bayerDesignation"), "A")], [_set(_star(VEGA, "bayerDesignation"), "")]),
    ("a limitation of 1024 units", "reply", [_set(["predictedSkyBrightness", "limitations", 0], "a" * 1024)],
     [_set(["predictedSkyBrightness", "limitations", 0], "a" * 1025)]),
    ("16 limitations", "reply", [_set(["predictedSkyBrightness", "limitations"], ["a limit"] * 16)], [_set(["predictedSkyBrightness", "limitations"], ["a limit"] * 17)]),
    ("a body's uncertainty at 0", "reply", [_set(_body(MARS, "skyPosition", "uncertaintyArcsec"), 0.0)], [_set(_body(MARS, "skyPosition", "uncertaintyArcsec"), -0.000001)]),
    ("the airmass at 1", "reply", [_set(["predictedSkyBrightness", "airmass"], 1.0)], [_set(["predictedSkyBrightness", "airmass"], 0.999999)]),
    ("the illuminated fraction at 1", "reply", [_set(["predictedSkyBrightness", "illuminationFraction"], 1.0)], [_set(["predictedSkyBrightness", "illuminationFraction"], 1.000001)]),
    ("the illuminated fraction at 0", "reply", [_set(["predictedSkyBrightness", "illuminationFraction"], 0.0)], [_set(["predictedSkyBrightness", "illuminationFraction"], -0.000001)]),
    ("the separation from the Moon at 180", "reply", [_set(["predictedSkyBrightness", "sepFromMoonDeg"], 180.0)], [_set(["predictedSkyBrightness", "sepFromMoonDeg"], 180.000001)]),
    ("the lunar veil at 0", "reply", [_set(["predictedSkyBrightness", "lunarVeilDeltaMag"], 0.0)], [_set(["predictedSkyBrightness", "lunarVeilDeltaMag"], -0.000001)]),
    ("the phase name at a sector's edge, null", "reply-edge", [], [_set(["predictedSkyBrightness", "lunarPhaseName"], "Between Moons")]),
    ("the Sun just above -50 arcminutes, day", "reply", [_set(["sun", "altDeg"], -0.833333), _set(["sun", "band"], "day")],
     [_set(["sun", "altDeg"], -50.0 / 60.0), _set(["sun", "band"], "day")]),
    ("the Sun at -50 arcminutes, civil twilight", "reply", [_set(["sun", "altDeg"], -50.0 / 60.0), _set(["sun", "band"], "civil_twilight")],
     [_set(["sun", "altDeg"], -0.833333), _set(["sun", "band"], "civil_twilight")]),
    ("the Sun at -6 degrees, civil twilight", "reply", [_set(["sun", "altDeg"], -6.0), _set(["sun", "band"], "civil_twilight")],
     [_set(["sun", "altDeg"], -6.000001), _set(["sun", "band"], "civil_twilight")]),
    ("the Sun at -12 degrees, nautical twilight", "reply", [_set(["sun", "altDeg"], -12.0), _set(["sun", "band"], "nautical_twilight")],
     [_set(["sun", "altDeg"], -12.000001), _set(["sun", "band"], "nautical_twilight")]),
    ("the Sun at -18 degrees, astronomical twilight", "reply", [_set(["sun", "altDeg"], -18.0), _set(["sun", "band"], "astronomical_twilight")],
     [_set(["sun", "altDeg"], -18.000001), _set(["sun", "band"], "astronomical_twilight")]),
    ("the Sun just below -18 degrees, night", "reply", [_set(["sun", "altDeg"], -18.000001), _set(["sun", "band"], "night")],
     [_set(["sun", "altDeg"], -18.0), _set(["sun", "band"], "night")]),
    ("the Sun's altitude at 90", "reply", [_set(["sun", "altDeg"], 90.0), _set(["sun", "band"], "day")],
     [_set(["sun", "altDeg"], 90.000001), _set(["sun", "band"], "day")]),
    ("the Sun's altitude at -90", "reply", [_set(["sun", "altDeg"], -90.0)], [_set(["sun", "altDeg"], -90.000001)]),
    ("the Sun's method of 1024 units", "reply", [_set(["sun", "method"], "a" * 1024)], [_set(["sun", "method"], "a" * 1025)]),
]  # fmt: skip


def edge_samples() -> list[dict[str, Any]]:
    out = []
    for name, base, inside, outside in EDGES:
        kind = "request" if base == "request" else "reply"
        out.append({"name": f"{name}: inside", "kind": kind, "base": base, "verdict": "accepted", "changes": inside})
        out.append({"name": f"{name}: outside", "kind": kind, "base": base, "verdict": "refused", "changes": outside})
    return out


def build() -> dict[str, Any]:
    return {
        "format": FORMAT, "formatVersion": FORMAT_VERSION, "ephemerisSpan": {"from": SPAN[0], "until": SPAN[1]},
        "limits": LIMITS, "convention": CONVENTION, "providers": PROVIDERS, "sunBands": list(SUN_BANDS),
        "sunBandEdges": SUN_BAND_EDGES, "rules": RULES,
        "bases": {"request": BASE_REQUEST, "reply": BASE_REPLY, "reply-above": ABOVE_ONLY, "reply-edge": BASE_EDGE},
        "valid": valid_samples(), "invalid": invalid_samples(), "edges": edge_samples(),
    }  # fmt: skip


def resolved(sample: dict[str, Any]) -> Any:
    return _apply(_faults_base(sample["base"]), sample["changes"]) if "changes" in sample else sample["body"]


def accepts(kind: str, body: Any) -> bool:
    try:
        MODELS[kind].model_validate(body)
    except ValidationError:
        return False
    return True


def test_the_fixture_is_current() -> None:
    text = json.dumps(build(), indent=1, ensure_ascii=True) + "\n"
    if os.environ.get(UPDATE_FIXTURE_ENV) == "1":
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == text, f"regenerate with {UPDATE_FIXTURE_ENV}=1"


def test_the_decisions_are_the_modules() -> None:
    assert (sc.SKY_CONTEXT_FORMAT, sc.SKY_CONTEXT_FORMAT_VERSION) == (FORMAT, FORMAT_VERSION)
    assert (sc.SKY_CONTEXT_EPHEMERIS_FROM, sc.SKY_CONTEXT_EPHEMERIS_UNTIL) == SPAN
    held = {"magMin": sc.SKY_CONTEXT_MAG_MIN, "magMax": sc.SKY_CONTEXT_MAG_MAX, "nameMaxUtf16": sc.SKY_CONTEXT_NAME_MAX_UTF16,
            "textMaxUtf16": sc.SKY_CONTEXT_TEXT_MAX_UTF16, "starsMax": sc.SKY_CONTEXT_STARS_MAX, "bodiesMax": sc.SKY_CONTEXT_BODIES_MAX,
            "edgesMax": sc.SKY_CONTEXT_EDGES_MAX, "notesMax": sc.SKY_CONTEXT_NOTES_MAX, "properMotionMax": sc.SKY_CONTEXT_PROPER_MOTION_MAX,
            "radialVelocityMax": sc.SKY_CONTEXT_RADIAL_VELOCITY_MAX, "bpRpMin": sc.SKY_CONTEXT_BP_RP_MIN, "bpRpMax": sc.SKY_CONTEXT_BP_RP_MAX}  # fmt: skip
    assert held == LIMITS
    assert sc.SKY_CONTEXT_CONVENTION == CONVENTION and sc.SKY_CONTEXT_PROVIDERS == PROVIDERS
    assert sc.SKY_CONTEXT_RULES == RULES
    assert sc.SKY_CONTEXT_SUN_BANDS == SUN_BANDS
    assert sc.SKY_CONTEXT_SUN_BAND_EDGES == SUN_BAND_EDGES


def test_the_ephemeris_span_is_the_sky_codes_own() -> None:
    from services.science.ephemeris import EphemerisOutOfRange, require_ephemeris_range

    for moment in (SPAN[0], "2050-12-31T23:59:59.999Z"):
        require_ephemeris_range(moment)
    for moment in ("1799-12-31T23:59:59.999Z", SPAN[1]):
        with pytest.raises(EphemerisOutOfRange):
            require_ephemeris_range(moment)


def test_the_base_rows_are_the_catalogs_own() -> None:
    """Each base star is its catalog row, and each body one the route asks the ephemeris for: no value is made up."""
    from services.science.catalog import ALL_SKY_CATALOG_STARS
    from services.science.ephemeris import DEFAULT_BODIES

    rows = {star.id: star for star in ALL_SKY_CATALOG_STARS}
    for star in BASE_REPLY["stars"]:
        row = rows[star["id"]]
        assert (star["commonName"], star["bayerDesignation"], star["constellation"], star["provider"], star["releaseVersion"],
                star["catalogSourceId"], star["raDeg"], star["decDeg"], star["mag"], star["distancePc"]["median"]) == (
            row.common_name, row.bayer_designation, row.constellation, row.provider, row.release_version, row.catalog_source_id,
            row.ra_deg, row.dec_deg, row.mag, row.median_pc)  # fmt: skip
    assert {body["name"] for body in BASE_REPLY["ephemerides"]} <= set(DEFAULT_BODIES)
    assert BASE_REPLY["observedAtUTC"] == BASE_REQUEST["observedAtUTC"]
    assert BASE_REPLY["observerLocation"] == {"latDeg": BASE_REQUEST["latDeg"], "lonDeg": BASE_REQUEST["lonDeg"]}


def test_the_base_sun_is_the_ephemeris_own() -> None:
    """The base reply's Sun is the ephemeris's for the base moment and place, within a nanodegree (the platforms' last
    bits may differ), with its band from the science code's own sky_phase and its method and limitations unchanged."""
    from services.science.ephemeris import compute_solar_system_ephemerides
    from services.science.predicted_sky import sky_phase

    (sun,) = compute_solar_system_ephemerides(BASE_REQUEST["observedAtUTC"], BASE_REQUEST["latDeg"], BASE_REQUEST["lonDeg"], bodies=("Sun",))
    held = BASE_REPLY["sun"]
    assert abs(held["altDeg"] - float(sun.alt_deg)) < 1e-9
    assert held["band"] == sky_phase(float(sun.alt_deg)).band == "night"
    evidence = sun.to_contract_dict(source_sha256=None)["evidence"]
    assert (held["evidenceClass"], held["method"], held["limitations"]) == ("predicted", evidence["verificationMethod"], evidence["limitations"])


def test_the_sun_bands_are_the_science_codes_own() -> None:
    """The contract's band edges are the science code's constants, and its band function names each altitude as
    services/science/predicted_sky.py sky_phase does, at every edge and its neighbours."""
    from services.science import pose_estimate, predicted_sky

    assert SUN_BAND_EDGES == {"dayAboveDeg": pose_estimate.SUNRISE_SUNSET_ALT_DEG, "civilTwilightFromDeg": predicted_sky.CIVIL_TWILIGHT_ALT_DEG,
                              "nauticalTwilightFromDeg": predicted_sky.NAUTICAL_TWILIGHT_ALT_DEG,
                              "astronomicalTwilightFromDeg": predicted_sky.ASTRONOMICAL_TWILIGHT_ALT_DEG}  # fmt: skip
    for edge in SUN_BAND_EDGES.values():
        for alt in (edge, edge - 1e-6, edge + 1e-6, edge - 1e-12, edge + 1e-12):
            assert sc.sun_band_of(alt) == predicted_sky.sky_phase(alt).band, alt
    for alt in (-90.0, -45.0, 0.0, 45.0, 90.0):
        assert sc.sun_band_of(alt) == predicted_sky.sky_phase(alt).band, alt


@settings(max_examples=200, deadline=None)
@given(alt=st.floats(min_value=-90.0, max_value=90.0, allow_nan=False))
def test_property_the_contracts_sun_band_is_sky_phases(alt: float) -> None:
    from services.science.predicted_sky import sky_phase

    assert sc.sun_band_of(alt) == sky_phase(alt).band


def _expected_band(alt: float) -> str:
    """The band as the rule is written here, not by the module."""
    if alt > SUN_BAND_EDGES["dayAboveDeg"]:
        return "day"
    if alt >= SUN_BAND_EDGES["civilTwilightFromDeg"]:
        return "civil_twilight"
    if alt >= SUN_BAND_EDGES["nauticalTwilightFromDeg"]:
        return "nautical_twilight"
    return "astronomical_twilight" if alt >= SUN_BAND_EDGES["astronomicalTwilightFromDeg"] else "night"


_SUN_ALTITUDES = [*SUN_BAND_EDGES.values(), *(edge + d for edge in SUN_BAND_EDGES.values() for d in (-1e-6, 1e-6)), -90.0, 90.0, 0.0, -47.7]


@settings(max_examples=200, deadline=None)
@given(alt=st.one_of(st.sampled_from(_SUN_ALTITUDES), st.floats(min_value=-90.0, max_value=90.0, allow_nan=False)),
       band=st.sampled_from([*SUN_BANDS, "dusk"]))  # fmt: skip
def test_property_generated_suns_match_the_band_rule(alt: float, band: str) -> None:
    reply = _apply(BASE_REPLY, [_set(["sun", "altDeg"], alt), _set(["sun", "band"], band)])
    assert accepts("reply", reply) is (band == _expected_band(alt))


def test_the_sun_stays_whatever_the_filters_say() -> None:
    """The Sun is its own block, not a body of ephemerides: aboveHorizonOnly never asks it above the horizon, which is
    when the capture coach needs it."""
    assert ABOVE_ONLY["sun"] == BASE_REPLY["sun"] and BASE_REPLY["sun"]["altDeg"] < 0.0
    assert accepts("reply", ABOVE_ONLY)


@pytest.mark.parametrize("sample", valid_samples(), ids=lambda sample: sample["name"])
def test_every_valid_sample_is_accepted_unchanged(sample: dict[str, Any]) -> None:
    model = MODELS[sample["kind"]].model_validate(sample["body"])
    assert model.to_wire_dict() == sample["body"]
    assert MODELS[sample["kind"]].model_validate_json(json.dumps(sample["body"])).to_wire_dict() == sample["body"]


@pytest.mark.parametrize("sample", invalid_samples(), ids=lambda sample: sample["name"])
def test_every_invalid_sample_is_refused_with_its_rule(sample: dict[str, Any]) -> None:
    body = resolved(sample)
    with pytest.raises(ValidationError) as exc_info:
        MODELS[sample["kind"]].model_validate(body)
    if "message" in sample:
        assert [err["msg"] for err in exc_info.value.errors()] == [f"Value error, {sample['message']}"]
    else:
        assert all(not err["msg"].startswith("Value error, ") or err["msg"][13:] not in RULES.values() for err in exc_info.value.errors())


@pytest.mark.parametrize("sample", edge_samples(), ids=lambda sample: sample["name"])
def test_each_bound_holds_at_its_edges(sample: dict[str, Any]) -> None:
    assert accepts(sample["kind"], resolved(sample)) == (sample["verdict"] == "accepted")


def test_a_request_is_never_a_reply_and_a_reply_never_a_request() -> None:
    for sample in valid_samples():
        assert not accepts("reply" if sample["kind"] == "request" else "request", sample["body"]), sample["name"]


def test_each_list_holds_at_most_its_stated_number() -> None:
    star = BASE_REPLY["stars"][VEGA]
    many = [{**star, "id": f"star-n{i}"} for i in range(sc.SKY_CONTEXT_STARS_MAX + 1)]
    assert accepts("reply", {**EMPTY, "stars": many[:-1]})
    assert not accepts("reply", {**EMPTY, "stars": many})
    names = ["Mars" + "s" * i for i in range(sc.SKY_CONTEXT_BODIES_MAX + 1)]
    body = BASE_REPLY["ephemerides"][MARS]
    bodies = [{**body, "name": n, "id": f"finding-predicted-{n.lower()}", "evidence": {**body["evidence"], "id": f"ev-ephem-{n.lower()}"}} for n in names]
    assert accepts("reply", {**EMPTY, "ephemerides": bodies[:-1]})
    assert not accepts("reply", {**EMPTY, "ephemerides": bodies})
    two = [BASE_REPLY["stars"][VEGA], BASE_REPLY["stars"][SHELIAK]]
    edge = {"constellation": "Lyra", "fromStarId": two[0]["id"], "toStarId": two[1]["id"]}
    assert accepts("reply", {**EMPTY, "stars": two, "constellationEdges": [edge] * sc.SKY_CONTEXT_EDGES_MAX})
    assert not accepts("reply", {**EMPTY, "stars": two, "constellationEdges": [edge] * (sc.SKY_CONTEXT_EDGES_MAX + 1)})


def test_a_refused_request_never_repeats_the_place_it_was_sent() -> None:
    sentinel = 91.23456789
    for body in ({**BASE_REQUEST, "latDeg": sentinel}, {**BASE_REQUEST, "lonDeg": sentinel * 2}, {**BASE_REQUEST, "unknownKey": sentinel}):
        with pytest.raises(ValidationError) as exc_info:
            sc.SkyContextRequest.model_validate(body)
        assert str(sentinel) not in str(exc_info.value) and "input_value" not in str(exc_info.value)


def test_every_python_field_name_is_refused_where_its_wire_key_goes() -> None:
    """Each two-word key of the base request and reply, renamed to its Python field name, is refused."""
    def renamed(node: Any, model: Any) -> list[Any]:
        out = []
        if isinstance(node, dict) and model is not None:
            for name, field in model.model_fields.items():
                alias = field.alias
                if alias in node and alias != name:
                    out.append({name if key == alias else key: value for key, value in node.items()})
        return out

    cases = renamed(BASE_REQUEST, sc.SkyContextRequest) + renamed(BASE_REPLY, sc.SkyContextReply)
    star = BASE_REPLY["stars"][VEGA]
    cases += [{**BASE_REPLY, "stars": [variant, *BASE_REPLY["stars"][1:]]} for variant in renamed(star, sc.SkyContextStar)]
    body = BASE_REPLY["ephemerides"][MARS]
    cases += [{**BASE_REPLY, "ephemerides": [variant, *BASE_REPLY["ephemerides"][1:]]} for variant in renamed(body, sc.SkyContextBody)]
    cases += [{**BASE_REPLY, "sun": variant} for variant in renamed(BASE_REPLY["sun"], sc.SkyContextSun)]
    assert len(cases) > 20
    for case in cases:
        assert not accepts("request" if "stars" not in case else "reply", case)


# ---------------------------------------------------------------------------------------------------------------------
# Generated samples, judged by the rules as written here, not by the module
# ---------------------------------------------------------------------------------------------------------------------


def _expected_request_valid(candidate: dict[str, Any]) -> bool:
    if not {"observedAtUTC", "latDeg", "lonDeg"} <= set(candidate) <= {"observedAtUTC", "latDeg", "lonDeg", "magLimit", "aboveHorizonOnly"}:
        return False

    def number(value: Any, low: float, high: float) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high

    moment = candidate["observedAtUTC"]
    return (
        isinstance(moment, str) and moment in MOMENTS_IN_SPAN
        and number(candidate["latDeg"], -90, 90) and number(candidate["lonDeg"], -180, 180)
        and ("magLimit" not in candidate or number(candidate["magLimit"], -30, 30))
        and ("aboveHorizonOnly" not in candidate or isinstance(candidate["aboveHorizonOnly"], bool))
    )  # fmt: skip


MOMENTS_IN_SPAN = (SPAN[0], "2031-03-20T12:00:00Z", "2031-03-20T12:00:00.250Z", "2050-12-31T23:59:59.999Z")
MOMENTS_OUT = (SPAN[1], "1799-12-31T23:59:59.999Z", "2031-02-30T00:00:00Z", "2031-03-20T12:00:00", "0000-01-01T00:00:00Z")
_REQUEST_STRATEGY = st.fixed_dictionaries(
    {
        "observedAtUTC": st.sampled_from([*MOMENTS_IN_SPAN, *MOMENTS_OUT, None]),
        "latDeg": st.sampled_from([0, 20.0, 90, 90.000001, -90, -90.5, True, "20", None]),
        "lonDeg": st.sampled_from([0, -140.0, 180, -180, 180.000001, -181, False, "0"]),
    },
    optional={
        "magLimit": st.sampled_from([2, 2.5, -30, 30, 30.000001, -31, True, None, "2"]),
        "aboveHorizonOnly": st.sampled_from([True, False, 1, 0, "true", None]),
        "extraKey": st.just(1),
    },
)


@settings(max_examples=150, deadline=None)
@given(candidate=_REQUEST_STRATEGY)
@example(candidate=BASE_REQUEST)
@example(candidate={**BASE_REQUEST, "magLimit": 30.0, "aboveHorizonOnly": False})
@example(candidate={**BASE_REQUEST, "observedAtUTC": SPAN[1]})
def test_property_generated_requests_match_the_contract(candidate: dict[str, Any]) -> None:
    assert accepts("request", candidate) is _expected_request_valid(candidate)


def _expected_filtered_reply_valid(reply: dict[str, Any]) -> bool:
    """The reply-level rules alone, for a reply whose every row is one of the base's, unchanged but for aboveHorizon."""
    items = [*reply["stars"], *reply["ephemerides"]]
    if any(item["aboveHorizon"] != (item["altDeg"] >= 0) for item in items):
        return False
    ids = [star["id"] for star in reply["stars"]]
    bodies = [body["id"] for body in reply["ephemerides"]]
    if len(set(ids)) != len(ids) or len(set(bodies)) != len(bodies):
        return False
    if any(edge["fromStarId"] not in ids or edge["toStarId"] not in ids for edge in reply["constellationEdges"]):
        return False
    filters = reply["filters"]
    if filters["aboveHorizonOnly"] and any(item["altDeg"] < 0 for item in items):
        return False
    return "magLimit" not in filters or all(star["mag"] <= filters["magLimit"] for star in reply["stars"])


_STAR_PICKS = st.lists(st.sampled_from(range(len(BASE_REPLY["stars"]))), max_size=len(BASE_REPLY["stars"]) + 1)
_BODY_PICKS = st.lists(st.sampled_from(range(len(BASE_REPLY["ephemerides"]))), max_size=len(BASE_REPLY["ephemerides"]) + 1)
_EDGE_PICKS = st.lists(st.sampled_from(range(len(BASE_REPLY["constellationEdges"]))), max_size=len(BASE_REPLY["constellationEdges"]))


@settings(max_examples=200, deadline=None)
@given(
    stars=_STAR_PICKS, bodies=_BODY_PICKS, edges=_EDGE_PICKS, flips=st.sets(st.sampled_from(range(9)), max_size=2),
    above_only=st.booleans(), mag_limit=st.sampled_from([None, -1.0, 0.03, 1.58, 2.0, 3.52, 30.0]),
)  # fmt: skip
@example(stars=list(range(6)), bodies=[0, 1], edges=list(range(4)), flips=set(), above_only=False, mag_limit=None)
@example(stars=[VEGA, SHELIAK], bodies=[MARS], edges=[], flips=set(), above_only=True, mag_limit=3.52)
def test_property_filtered_replies_match_the_reply_level_rules(
    stars: list[int], bodies: list[int], edges: list[int], flips: set[int], above_only: bool, mag_limit: float | None
) -> None:
    reply = copy.deepcopy(BASE_REPLY)
    reply["stars"] = [copy.deepcopy(BASE_REPLY["stars"][i]) for i in stars]
    reply["ephemerides"] = [copy.deepcopy(BASE_REPLY["ephemerides"][i]) for i in bodies]
    reply["constellationEdges"] = [copy.deepcopy(BASE_REPLY["constellationEdges"][i]) for i in edges]
    for flip in flips:
        items = [*reply["stars"], *reply["ephemerides"]]
        if flip < len(items):
            items[flip]["aboveHorizon"] = not items[flip]["aboveHorizon"]
    reply["filters"] = {"aboveHorizonOnly": above_only, **({} if mag_limit is None else {"magLimit": mag_limit})}
    assert accepts("reply", reply) is _expected_filtered_reply_valid(reply)


def test_the_phase_name_is_null_by_design_at_a_sectors_edge_and_the_contract_takes_it() -> None:
    """skycontext-1b: the science code gives no phase name within its bound of a sector's edge; the reply carries the
    null as it is, unchanged through the contract, and only a null, never another word, stands for it."""
    from services.science.ephemeris import moon_phase

    assert moon_phase(EDGE_MOMENT).phase_name is None
    assert BASE_EDGE["observedAtUTC"] == EDGE_MOMENT and BASE_EDGE["predictedSkyBrightness"]["lunarPhaseName"] is None
    assert BASE_EDGE["observerLocation"] == {"latDeg": 0.0, "lonDeg": 0.0}
    model = sc.SkyContextReply.model_validate(BASE_EDGE)
    assert model.to_wire_dict() == BASE_EDGE
    assert sc.SkyContextReply.model_validate_json(json.dumps(BASE_EDGE)).to_wire_dict() == BASE_EDGE
    assert all(item["evidenceClass"] == "predicted" for item in BASE_EDGE["ephemerides"])


def test_a_star_without_a_bayer_designation_is_accepted_and_null_is_not() -> None:
    """skycontext-5a: a star that has no Bayer designation in the shipped tables omits bayerDesignation; null and empty
    strings stay refused."""
    assert "bayerDesignation" not in NO_BAYER["stars"][SULAFAT]
    model = sc.SkyContextReply.model_validate(NO_BAYER)
    assert model.to_wire_dict() == NO_BAYER
    assert not accepts("reply", _apply(BASE_REPLY, [_set(["stars", SULAFAT, "bayerDesignation"], None)]))
    assert not accepts("reply", _apply(BASE_REPLY, [_set(["stars", SULAFAT, "bayerDesignation"], "")]))


def test_a_null_anywhere_else_in_a_reply_is_still_refused() -> None:
    for path in (["predictedSkyBrightness", "airmass"], ["predictedSkyBrightness", "target"], ["observerLocation", "latDeg"],
                 ["stars", 0, "bpRp"], ["stars", 0, "bayerDesignation"], ["filters", "magLimit"]):
        assert not accepts("reply", _apply(BASE_REPLY, [_set(path, None)])), path


def test_a_null_is_refused_at_its_own_key_and_every_other_fault_still_shows() -> None:
    """The request refuses a null where it sits (the wire module's single refusal at the body would hide the rest)."""
    cases = [
        ({"magLimit": None}, {("magLimit",)}),
        ({"aboveHorizonOnly": None, "latDeg": 91.0}, {("aboveHorizonOnly",), ("latDeg",)}),
        ({"latDeg": None, "lonDeg": None}, {("latDeg",), ("lonDeg",)}),
        ({"observedAtUTC": None, "unknownKey": None}, {("observedAtUTC",), ("unknownKey",)}),
    ]
    for changes, places in cases:
        with pytest.raises(ValidationError) as exc_info:
            sc.SkyContextRequest.model_validate({**BASE_REQUEST, **changes})
        assert {err["loc"][:1] for err in exc_info.value.errors()} == places, changes
