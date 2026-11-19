"""The field of view a phone solve starts from: the 35 mm tag scaled to the stored frame (SOLVER fov-1).

A 35 mm equivalent focal length names the whole sensor. A phone's sensor is 4:3; a frame of another shape (the 16:9
or taller modes, a square crop) is the largest part of it of that shape, so its diagonal is shorter than the sensor's
and its field of view narrower than the tag's. On public photos the tag's own field of view was 8 to 10 percent wider
than astrometry.net's on three 16:9 or taller phone frames, and the frame's 0.5 to 2 percent [tested, fov-1 report;
the values below are those photos' file tags, not the photos]. A camera whose 35 mm equivalent is under 1.9 times its
focal length (full frame, APS-C) is taken as 3:2, the film frame's own shape.
"""

from __future__ import annotations

import math

from hypothesis import given, settings, strategies as st
import pytest

from services.science import phone_photo_solver as pps


def _tangent_half(fov_deg: float) -> float:
    return math.tan(math.radians(fov_deg) / 2.0)


def test_a_four_by_three_phone_frame_keeps_the_tags_field_of_view() -> None:
    for width, height in ((4032, 3024), (3024, 4032), (4000, 3000)):
        assert pps.frame_diagonal_fov_deg(24.0, width, height, 6.765) == pytest.approx(
            pps.diagonal_fov_deg_from_35mm_equivalent(24.0), abs=1e-9
        )


@pytest.mark.parametrize(
    ("focal_35mm", "width", "height", "focal_mm", "expected", "astrometry_net"),
    [
        # rp-b-22, rp-b-12, rp-b-15: public phone photos, 16:9 or taller [tested: their tags; astrometry.net's
        # diagonal from the realsky2-full records]
        (27.0, 4032, 2268, 4.38, 72.66, 71.5),
        (26.0, 4000, 2252, 5.4, 74.75, 73.2),
        (24.0, 1800, 4000, 5.24, 76.67, 76.2),
    ],
)
def test_a_sixteen_by_nine_or_taller_phone_frame_is_a_crop_of_its_sensor(
    focal_35mm: float, width: int, height: int, focal_mm: float, expected: float, astrometry_net: float
) -> None:
    fov = pps.frame_diagonal_fov_deg(focal_35mm, width, height, focal_mm)
    assert fov == pytest.approx(expected, abs=0.05)
    tag = pps.diagonal_fov_deg_from_35mm_equivalent(focal_35mm)
    # Closer to the independent solver's than the tag's own, and within 2.5 percent of it
    assert abs(fov - astrometry_net) < abs(tag - astrometry_net)
    assert abs(fov / astrometry_net - 1.0) < 0.025


def test_a_square_phone_frame_is_the_sensors_short_side_squared() -> None:
    fov = pps.frame_diagonal_fov_deg(26.0, 3024, 3024, 4.25)
    # The square's diagonal over the 4:3 sensor's: sqrt(2) / (5 / 3)
    expected = 2.0 * math.atan(_tangent_half(pps.diagonal_fov_deg_from_35mm_equivalent(26.0)) * math.sqrt(2.0) * 3.0 / 5.0)
    assert fov == pytest.approx(math.degrees(expected), abs=1e-9)


def test_a_three_by_two_frame_from_a_large_sensor_keeps_the_tags_field_of_view() -> None:
    # A Nikon D3400's 18 mm at 27 mm equivalent, 6000 x 4000: the film frame's own shape
    assert pps.frame_diagonal_fov_deg(27.0, 6000, 4000, 18.0) == pytest.approx(
        pps.diagonal_fov_deg_from_35mm_equivalent(27.0), abs=1e-9
    )
    # The same frame from a phone (crop factor about 6) is a crop of a 4:3 sensor
    phone = pps.frame_diagonal_fov_deg(27.0, 6000, 4000, 4.38)
    assert phone < pps.diagonal_fov_deg_from_35mm_equivalent(27.0) - 2.0


def test_without_a_focal_length_the_frame_is_taken_as_a_phones() -> None:
    assert pps.frame_diagonal_fov_deg(27.0, 4032, 2268, None) == pytest.approx(
        pps.frame_diagonal_fov_deg(27.0, 4032, 2268, 4.38), abs=1e-9
    )


@pytest.mark.parametrize(
    "bad",
    [(0.0, 4032, 3024, 4.0), (float("nan"), 4032, 3024, 4.0), (24.0, 0, 3024, 4.0), (24.0, 4032, -1, 4.0),
     (24.0, 4032, 3024, 0.0), (24.0, 4032, 3024, float("inf"))],
)
def test_a_tag_or_a_frame_that_is_not_finite_and_positive_is_refused(bad) -> None:
    with pytest.raises(ValueError):
        pps.frame_diagonal_fov_deg(*bad)


@settings(max_examples=300, deadline=None)
@given(
    focal_35mm=st.floats(min_value=10.0, max_value=300.0),
    width=st.integers(min_value=64, max_value=12000),
    height=st.integers(min_value=64, max_value=12000),
    focal_mm=st.one_of(st.none(), st.floats(min_value=1.0, max_value=200.0)),
)
def test_pbt_the_frames_field_of_view_is_never_wider_than_the_tags_and_turns_with_the_frame(
    focal_35mm: float, width: int, height: int, focal_mm: float | None
) -> None:
    fov = pps.frame_diagonal_fov_deg(focal_35mm, width, height, focal_mm)
    tag = pps.diagonal_fov_deg_from_35mm_equivalent(focal_35mm)
    assert 0.0 < fov <= tag + 1e-9
    # A frame turned a quarter turn is the same crop of the same sensor
    assert fov == pytest.approx(pps.frame_diagonal_fov_deg(focal_35mm, height, width, focal_mm), abs=1e-9)
    # The frame spans the sensor along one side at least, so its diagonal is never shorter than that side's share
    long_px, short_px = max(width, height), min(width, height)
    aspect = 1.5 if focal_mm is not None and focal_35mm / focal_mm < 1.9 else 4.0 / 3.0
    assert _tangent_half(fov) >= _tangent_half(tag) / math.hypot(aspect, 1.0) - 1e-9
    if math.isclose(long_px / short_px, aspect, rel_tol=1e-12):
        assert fov == pytest.approx(tag, abs=1e-9)


def test_the_jobs_hint_is_the_frames_for_a_still_and_the_tags_for_a_clip() -> None:
    camera = {"model": "Pixel 4 XL", "focalLengthMm": 4.38, "focalLength35mmMm": 27.0}
    assert pps.camera_fov_hint_deg(camera, (4032, 2268)) == pytest.approx(pps.frame_diagonal_fov_deg(27.0, 4032, 2268, 4.38))
    # A clip's frame is not measured here: its tag may describe the video's own crop, so the tag stands as it is
    assert pps.camera_fov_hint_deg(camera, None) == pytest.approx(pps.diagonal_fov_deg_from_35mm_equivalent(27.0))
    # A large sensor's 3:2 frame is its whole sensor, so its hint is the tag's: the focal length decides it
    camera_3x2 = {"model": "NIKON D3400", "focalLengthMm": 18.0, "focalLength35mmMm": 27.0}
    assert pps.camera_fov_hint_deg(camera_3x2, (6000, 4000)) == pytest.approx(pps.diagonal_fov_deg_from_35mm_equivalent(27.0))
    # A focal length that is not a positive number leaves the sensor taken as a phone's
    for focal in (None, 0.0, -4.0, True, "4.38", float("nan")):
        assert pps.camera_fov_hint_deg({**camera, "focalLengthMm": focal}, (4032, 2268)) == pytest.approx(
            pps.frame_diagonal_fov_deg(27.0, 4032, 2268, None)
        )


@pytest.mark.parametrize(
    "camera",
    [None, "Pixel", {}, {"model": "Synthetic"}, {"focalLength35mmMm": True}, {"focalLength35mmMm": "26"},
     {"focalLength35mmMm": 0.0}, {"focalLength35mmMm": float("inf")},
     {"model": "iphone-18-pro-main", "focalLength35mmMm": 24.0}],
)
def test_no_hint_without_a_usable_tag_or_for_a_camera_with_a_published_field(camera) -> None:
    assert pps.camera_fov_hint_deg(camera, (4032, 3024)) is None


# =============================================================================================
# Through the upload and solve routes: a 16:9 phone frame tagged for its 4:3 sensor (synthetic, no real sky)
# =============================================================================================

# The frame's own field of view: a 26 mm phone's 4:3 sensor seen through a 16:9 mode, outside the generic search's list
# (82 to 123 degrees), so a solve that started from the tag's 79.5 degrees could not be rescued by the generic pass
FRAME_FOV_DEG = 74.7


def _deg_to_dms(value: float) -> tuple[float, float, float]:
    a = abs(value)
    d = math.floor(a)
    m = math.floor((a - d) * 60.0)
    return (float(d), float(m), round(((a - d) * 60.0 - m) * 60.0, 4))


def _sixteen_by_nine_upload() -> tuple[bytes, float]:
    """A CI suite frame rendered 16:9 at FRAME_FOV_DEG, as a PNG whose EXIF names a phone, the 35 mm equivalent of its
    whole 4:3 sensor, its moment and its place (the frame's own, synthetic)."""
    import dataclasses
    import io

    import numpy as np
    from PIL import Image

    from research.benchmarks.phone_photos import render as phone_render, stars as phone_stars, suite as phone_suite

    spec = phone_suite.build_suite("ci")[0]
    width, height = 480, 270
    spec = dataclasses.replace(spec, width=width, height=height, fov_deg=FRAME_FOV_DEG)
    frame = phone_render.render_frame(spec, phone_stars.TileCatalog())
    sensor_tan = _tangent_half(FRAME_FOV_DEG) * math.hypot(width, width * 0.75) / math.hypot(width, height)
    focal_35mm = round(pps.FULL_FRAME_DIAGONAL_MM / (2.0 * sensor_tan), 1)
    exif = Image.Exif()
    exif[0x010F] = "Synthetic"
    exif[0x0110] = "Synthetic Phone 1"
    exif_ifd = exif.get_ifd(0x8769)
    exif_ifd[0x9003] = spec.utc[:19].replace("-", ":").replace("T", " ")
    exif_ifd[0x9011] = "+00:00"
    exif_ifd[0xA405] = int(round(focal_35mm))
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = ("N" if spec.lat_deg >= 0 else "S"), _deg_to_dms(spec.lat_deg)
    gps[3], gps[4] = ("E" if spec.lon_deg >= 0 else "W"), _deg_to_dms(spec.lon_deg)
    buf = io.BytesIO()
    Image.fromarray(np.asarray(frame.rgb), mode="RGB").save(buf, format="PNG", exif=exif.tobytes(), compress_level=1)
    return buf.getvalue(), float(int(round(focal_35mm)))


def test_a_sixteen_by_nine_phone_frame_tagged_for_its_sensor_calibrates_through_the_routes(tmp_path) -> None:
    import hashlib
    import time

    from fastapi.testclient import TestClient

    from services.api import app
    from services.ingest import router as ingest_router
    from services.ingest.store import IngestStore

    body, focal_35mm = _sixteen_by_nine_upload()
    # The tag's own field of view is the whole sensor's, about 6 percent wider than the frame's
    assert pps.diagonal_fov_deg_from_35mm_equivalent(focal_35mm) > FRAME_FOV_DEG * 1.05
    assert pps.frame_diagonal_fov_deg(focal_35mm, 480, 270) == pytest.approx(FRAME_FOV_DEG, rel=0.01)
    sha = hashlib.sha256(body).hexdigest()
    store = IngestStore(tmp_path / "store", max_bytes=10_000_000)
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        client = TestClient(app, base_url="http://127.0.0.1")
        posted = client.post("/api/ingest", content=body, headers={"content-type": "image/png"})
        assert posted.status_code == 201, posted.text
        asset = posted.json()["sourceAsset"]
        assert asset["cameraMetadata"]["focalLength35mmMm"] == focal_35mm and asset.get("observerLocation")
        job = client.post(f"/api/ingest/{sha}/solve").json()
        deadline = time.monotonic() + 300.0
        while job.get("state") in ("queued", "running") and time.monotonic() < deadline:
            time.sleep(0.05)
            job = client.get(f"/api/ingest/jobs/{job['id']}").json()
        assert job["state"] == "succeeded", job
        solution = client.get(f"/api/ingest/{sha}/solution").json()
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)
    assert solution["status"] == "calibrated", solution.get("warnings")
    assert solution["quality"]["passedIndependentVerification"] is True
    assert solution["quality"]["heldOutInliers"] >= 12
