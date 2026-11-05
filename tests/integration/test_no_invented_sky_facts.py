"""The sky context and discovery state only what a catalog or a published model gives.

No hand-typed deep-sky table or canned notes; a star's temperature, luminosity and radius only from its Gaia DR3
row; and a sky brightness predicted for a stated target instead of a limiting magnitude from coefficients nobody
measured.

Covers:
- No output lists the hand-typed deep-sky objects or a canned note
- A Hipparcos-only star carries no temperature, luminosity or radius
- A Gaia DR3 star's temperature and luminosity are its astro tile row's, with the row's percentiles and citations,
  and its radius is derived from them by Stefan-Boltzmann and says so
- A FLAME luminosity outside its own percentiles keeps its value but no interval, and the radius then no span
- The sky brightness is predicted at a stated target with no limiting magnitude, and the audit route needs its
  target
- Discovery predicts the sky brightness at the zenith for a clip's moment and lists no deep-sky objects
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from fastapi.testclient import TestClient
import numpy as np
import pytest

from services.api import app
from services.science import catalog_tiles as ct

TILES = Path(__file__).resolve().parents[2] / "apps" / "web" / "public" / "catalog" / "stars" / "gaia-dr3"
# A synthetic moment and place that point at nobody (as in test_frontier_upgrades)
MOMENT = {"obs_utc_iso": "2030-11-01T22:32:00Z", "lat_deg": 30.0, "lon_deg": -40.0}


def _sky_context() -> dict:
    response = TestClient(app).get("/api/science/sky-context", params=MOMENT)
    assert response.status_code == 200
    return response.json()


def _astro_row(source_id: int) -> dict[str, np.ndarray]:
    """The astro columns of the layer-0 tile row that carries `source_id`, read straight from the tiles."""
    for detail_path in sorted((TILES / "L0").glob("*.detail.bin")):
        _, detail = ct.decode_detail_tile(detail_path.read_bytes())
        ids = (detail["idHi"].astype(np.int64) << 32) | detail["idLo"].astype(np.int64)
        hits = np.flatnonzero(ids == source_id)
        if len(hits):
            astro_path = detail_path.with_name(detail_path.name.replace(".detail.", ".astro."))
            _, astro = ct.decode_astro_tile(astro_path.read_bytes())
            return {key: values[hits[0]] for key, values in astro.items()}
    raise AssertionError(f"no layer-0 row carries Gaia DR3 {source_id}")


def test_no_output_lists_hand_typed_objects_or_canned_notes() -> None:
    body = _sky_context()
    text = json.dumps(body)
    assert "deepSkyObjects" not in body
    for canned in ("Helix Nebula", "debris disk", "core-collapse", "systemNotes", "evolutionaryStage"):
        assert canned not in text, canned


def test_a_hipparcos_only_star_carries_no_stellar_values() -> None:
    hipparcos = [star for star in _sky_context()["stars"] if star["provider"] == "Hipparcos"]
    assert len(hipparcos) == 59
    assert all("astrophysics" not in star for star in hipparcos)


def test_a_gaia_star_carries_its_tile_row_and_a_derived_radius() -> None:
    tabit = next(star for star in _sky_context()["stars"] if star["commonName"] == "Tabit")
    row = _astro_row(int(tabit["catalogSourceId"].removeprefix("Gaia DR3 ")))
    astro = tabit["astrophysics"]
    # The codec decodes arrays, so each value goes in as an array of one
    teff = [float(ct.decode_teff(np.array([row[key]]))[0]) for key in ("teff", "teffLower", "teffUpper")]
    lum = [float(ct.decode_luminosity(np.array([row[key]]))[0]) for key in ("logLum", "logLumLower", "logLumUpper")]
    assert [astro["teffK"][key] for key in ("value", "lower", "upper")] == teff
    assert [astro["luminositySolar"][key] for key in ("value", "lower", "upper")] == pytest.approx(lum)
    radius = astro["radiusSolar"]
    assert radius["derived"] is True and "Stefan-Boltzmann" in radius["method"]
    assert radius["value"] == pytest.approx(math.sqrt(lum[0]) * (5772.0 / teff[0]) ** 2)
    # The span's ends pair the percentiles the way they bound the radius: the lowest luminosity with the highest
    # temperature for the lower end, and the reverse for the upper
    assert radius["lower"] == pytest.approx(math.sqrt(lum[1]) * (5772.0 / teff[2]) ** 2)
    assert radius["upper"] == pytest.approx(math.sqrt(lum[2]) * (5772.0 / teff[1]) ** 2)
    assert radius["lower"] <= radius["value"] <= radius["upper"]
    assert any("Andrae" in c for c in astro["citations"]) and any("Creevey" in c for c in astro["citations"])


def test_the_sky_brightness_is_predicted_for_a_stated_target_without_a_limiting_magnitude() -> None:
    body = _sky_context()
    sky = body["predictedSkyBrightness"]
    assert (sky["target"], sky["targetAltDeg"], sky["evidenceClass"]) == ("zenith", 90.0, "predicted")
    assert any("not measured" in note for note in sky["limitations"])
    assert any(note.startswith("Assumes") for note in sky["limitations"])
    assert "LimitingMag" not in json.dumps(body) and "apertureMm" not in json.dumps(body)
    client = TestClient(app)
    # The target is required in full: no default separation from the Moon and no default altitude
    for partial in ({}, {"target_alt_deg": 50.0}, {"target_sep_deg": 40.0}):
        assert client.get("/api/science/radiometry-audit", params={**MOMENT, **partial}).status_code == 422, partial
    stated = client.get(
        "/api/science/radiometry-audit", params={**MOMENT, "target_sep_deg": 40.0, "target_alt_deg": 50.0}
    ).json()
    assert stated["predictedSkyBrightness"]["target"] == "altitude 50 deg, 40 deg from the Moon"
    assert "sensorAudit" not in stated


def test_a_luminosity_outside_its_own_percentiles_keeps_no_interval(monkeypatch) -> None:
    from services.science import stellar_parameters as sp
    from services.science.catalog import get_all_sky_catalog_stars

    tabit = next(star for star in get_all_sky_catalog_stars() if star.common_name == "Tabit")
    source_id = int(tabit.catalog_source_id.removeprefix("Gaia DR3 "))
    # As 61 rows of the release are: FLAME's lower bound above its own value
    inverted = {"teff": 6154.0, "teffLower": 6150.0, "teffUpper": 6158.0, "lum": 2.85, "lumLower": 3.0, "lumUpper": 3.2}
    monkeypatch.setattr(sp, "_astro_rows_by_source_id", lambda: {source_id: inverted})
    astro = sp.gaia_stellar_parameters(tabit)
    assert astro["luminositySolar"] == {"value": 2.85, "method": "Gaia DR3 FLAME"}
    assert "lower" not in astro["radiusSolar"] and "upper" not in astro["radiusSolar"]
    assert any("do not bracket" in note for note in astro["limitations"])


def test_discovery_predicts_the_zenith_sky_and_lists_no_deep_sky_objects(tmp_path, monkeypatch) -> None:
    from PIL import Image

    from services.media import discovery

    (tmp_path / "uploads").mkdir()
    still = tmp_path / "uploads" / "synthetic-still.png"
    Image.fromarray(np.zeros((90, 160), dtype=np.uint8)).save(still)
    # A still's probe with tags placing it at the synthetic moment and place above
    probe = {
        "streams": [{"codec_type": "video", "width": 160, "height": 90, "codec_name": "png"}],
        "format": {
            "tags": {"location": "+30.0000-040.0000/", "com.apple.quicktime.creationdate": MOMENT["obs_utc_iso"]},
        },
    }
    monkeypatch.setattr(discovery, "_probe_media_metadata", lambda path: probe)
    item = discovery.discover_uploaded_media(tmp_path / "uploads", tmp_path / "public", tmp_path / "artifacts")[0]
    payload = item.to_contract_dict()
    assert payload["predictedSkyBrightness"]["target"] == "zenith"
    assert payload["predictedSkyBrightness"]["evidenceClass"] == "predicted"
    assert not {"deepSkyObjects", "radiometryAudit"} & payload.keys()
