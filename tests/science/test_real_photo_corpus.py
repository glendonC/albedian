"""The real-photo corpus (realsky-1): its manifest, its registry entries, its credit table and its fetch command.

`research/benchmarks/real_photos/corpus.json` lists openly licensed photographs by third parties, fetched from
Wikimedia Commons into the shared `.cache/real-photos/` and registered in `data/downloads.json` by URL, size,
SHA-256, licence and author. The photos are not in git.

The first part runs everywhere: the manifest validates, each photo has exactly one registry entry and the reverse,
licences are on the allow list, the tiers hold their minimum counts, and no manifest field holds a coordinate, a
capture time or free text about a place. The fetch command is tested against a server on 127.0.0.1 inside the test,
under tmp_path, so it makes no outside request and never touches the shared .cache.

`read_facts`, which reads a file with the upload path's own readers, is tested everywhere on small JPEGs that Pillow
writes under tmp_path, with the synthetic moments and open-ocean places the trip guard's own tests use.

The last part reads the photos where they are present (`local_dataset`): each file has the registry's size and
SHA-256, decodes at the manifest's size, and the upload path's own readers (services/ingest) give the manifest's
camera fields and its three booleans. The files' own coordinates and capture times are read only to tell whether
they are there and on which side of the equator; no test prints or stores one.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
import contextlib
import copy
from datetime import date
import hashlib
import http.client
import http.server
import io
import json
from pathlib import Path
import re
import socket
import string
import tempfile
import threading
from typing import Any
import urllib.error
from urllib.parse import quote, urlsplit
import urllib.request

from hypothesis import example, given, settings, strategies as st
from PIL import ExifTags, Image
from PIL.TiffImagePlugin import IFDRational
from pydantic import ValidationError
import pytest

from research.benchmarks.real_photos import __main__ as cli
from research.benchmarks.real_photos import corpus as rp
from services.contracts.deep_sky import DEEP_SKY_CONSTELLATIONS
from services.ingest.probe import ProbeError
from services.ingest.store import Refused

ROOT = Path(__file__).resolve().parents[2]
DOC_PATH = ROOT / "docs" / "science" / "REAL_PHOTO_CORPUS.md"
MANIFEST_TEXT = rp.MANIFEST.read_text(encoding="utf-8")
MANIFEST_JSON: dict[str, Any] = json.loads(MANIFEST_TEXT)
CORPUS = rp.load_corpus()
DOWNLOADS = rp.corpus_downloads()
BY_PATH = {entry["path"]: entry for entry in DOWNLOADS}
PHOTO_KEYS = {
    "id", "downloadPath", "tier", "kind", "mediaKind", "widthPx", "heightPx", "cameraMake", "cameraModel",
    "lensFocalLengthMm", "focalLength35mmEquivMm", "exposureTimeSec", "hasCaptureTime", "hasUtcOffset", "hasPlace",
    "hemisphere", "hemisphereSource", "statedSubject", "statedSubjectSource",
}  # fmt: skip
# The em dash and the en dash, named by code point so that this file holds neither
DASHES = (chr(0x2014), chr(0x2013))
# A date, a time of day, or a coordinate as text; the manifest may hold none
DATE_OR_TIME = re.compile(r"\d{4}[-:/.]\d{1,2}[-:/.]\d{1,2}|\d{1,2}:\d{2}|\d{8}[T_ -]?\d{4}")
COORDINATE = re.compile(r"°|\d\.\d+\s*[NSEW]\b|[+-]\d+\.\d+|\d\.\d+\D+\d+\.\d")


def variant(change) -> dict[str, Any]:
    """The tracked manifest as JSON, with one change applied to a deep copy."""
    data = copy.deepcopy(MANIFEST_JSON)
    change(data)
    return data


def refused(data: dict[str, Any]) -> str:
    """The loader's reason for refusing this manifest."""
    with pytest.raises(ValidationError) as caught:
        rp.Corpus.model_validate(data)
    return str(caught.value)


def problems_of(data: dict[str, Any]) -> str:
    """The loader's reasons for refusing this manifest; empty when it accepts it."""
    try:
        rp.Corpus.model_validate(data)
    except ValidationError as error:
        return str(error)
    return ""


def photos_of(data: dict[str, Any], tier: str) -> list[dict[str, Any]]:
    return [photo for photo in data["photos"] if photo["tier"] == tier]


# =============================================================================================
# The manifest
# =============================================================================================


def test_the_manifest_validates_round_trips_and_says_what_it_is_not() -> None:
    assert CORPUS.to_json_dict() == MANIFEST_JSON
    assert MANIFEST_TEXT == rp.dumps_corpus(CORPUS)
    assert "not ground truth" in CORPUS.notes.statedSubject
    assert "uploader" in CORPUS.notes.statedSubject
    assert "no ground truth" in CORPUS.about
    assert "not in the repository" in CORPUS.about
    assert not any(dash in MANIFEST_TEXT for dash in DASHES)


def test_a_manifest_that_no_longer_says_what_it_is_not_is_refused() -> None:
    assert "must say the corpus holds no ground truth" in refused(variant(lambda data: data.update(about="Night photos.")))
    assert "must say the corpus holds no ground truth" in refused(
        variant(lambda data: data.update(about="Night photos with no ground truth. They are in the repository."))
    )
    assert "must say the corpus holds no ground truth" in refused(
        variant(lambda data: data.update(about="Night photos, not in the repository, with ground truth."))
    )
    for words in ("Tags.", "Tags by the uploader, checked against the sky.", "Tags, not ground truth."):
        assert "must say the tags are the uploader's statement" in refused(
            variant(lambda data, words=words: data["notes"].update(statedSubject=words))
        )
    assert "notes" in refused(variant(lambda data: data["notes"].update(place="an open ocean")))
    assert "capturedAtUTC" in refused(variant(lambda data: data.update(capturedAtUTC="2031-08-30T03:01:00Z")))


def test_ids_are_unique_and_name_their_tier_and_their_download_path() -> None:
    ids = [photo.id for photo in CORPUS.photos]
    assert len(ids) == len(set(ids)) and ids == sorted(ids)
    for photo in CORPUS.photos:
        match = re.fullmatch(r"rp-([a-d])-(\d{2})", photo.id)
        assert match is not None, photo.id
        assert match.group(1).upper() == photo.tier, photo.id
        assert re.fullmatch(rf"\.cache/real-photos/{photo.id}/{photo.id}\.(jpg|png|tif|heic)", photo.downloadPath), photo.id
    first = MANIFEST_JSON["photos"][0]

    def twice(data: dict[str, Any]) -> None:
        data["photos"][1].update(id=first["id"], downloadPath=first["downloadPath"])

    def out_of_order(data: dict[str, Any]) -> None:
        data["photos"][0], data["photos"][1] = data["photos"][1], data["photos"][0]

    assert f"id {first['id']} is listed more than once" in refused(variant(twice))
    assert "the photos are not in id order" in refused(variant(out_of_order))
    assert f"downloadPath is not .cache/real-photos/{first['id']}/{first['id']}." in refused(
        variant(lambda data: data["photos"][0].update(downloadPath=".cache/real-photos/x/y.jpg"))
    )
    for elsewhere in (f"data/{first['id']}/{first['id']}.jpg", f".cache/real-photos/{first['id']}/{first['id']}.exe",
                      f".cache/real-photos/{first['id']}/../{first['id']}.jpg", f".cache/other/{first['id']}/{first['id']}.jpg"):  # fmt: skip
        assert "downloadPath is not" in refused(variant(lambda data, path=elsewhere: data["photos"][0].update(downloadPath=path)))
    other_tier = "D" if first["tier"] != "D" else "A"
    assert f"tier {other_tier} does not match the id" in refused(variant(lambda data: data["photos"][0].update(tier=other_tier)))
    assert "id" in refused(variant(lambda data: data["photos"][0].update(id="Stars over Example Town")))


def test_tiers_hold_their_minimum_counts() -> None:
    assert rp.TIER_MINIMUMS == {"A": 8, "B": 16, "C": 10, "D": 8}
    assert rp.KIND_MINIMUMS == {"moon_over_horizon": 6, "sun_over_skyline": 4}
    tiers = Counter(photo.tier for photo in CORPUS.photos)
    kinds = Counter(photo.kind for photo in CORPUS.photos)
    for tier, minimum in rp.TIER_MINIMUMS.items():
        assert tiers[tier] >= minimum, tier
    for kind, minimum in rp.KIND_MINIMUMS.items():
        assert kinds[kind] >= minimum, kind
    for photo in CORPUS.photos:
        assert photo.kind in rp.KINDS[photo.tier], photo.id
    assert len(CORPUS.photos) <= rp.MAX_PHOTOS


@pytest.mark.parametrize("tier", sorted(rp.TIER_MINIMUMS))
def test_a_tier_one_photo_short_of_its_minimum_is_refused(tier: str) -> None:
    def cut(data: dict[str, Any]) -> None:
        keep = [photo["id"] for photo in photos_of(data, tier)][: rp.TIER_MINIMUMS[tier] - 1]
        data["photos"] = [photo for photo in data["photos"] if photo["tier"] != tier or photo["id"] in keep]

    assert f"tier {tier} holds {rp.TIER_MINIMUMS[tier] - 1}" in refused(variant(cut))


@pytest.mark.parametrize("tier", sorted(rp.TIER_MINIMUMS))
def test_a_tier_holding_exactly_its_minimum_is_enough(tier: str) -> None:
    def cut(data: dict[str, Any]) -> None:
        keep = [photo["id"] for photo in photos_of(data, tier)][: rp.TIER_MINIMUMS[tier]]
        data["photos"] = [photo for photo in data["photos"] if photo["tier"] != tier or photo["id"] in keep]

    cut_to_minimum = variant(cut)
    assert len(photos_of(cut_to_minimum, tier)) == rp.TIER_MINIMUMS[tier]
    assert f"tier {tier} holds {rp.TIER_MINIMUMS[tier]} photos" not in problems_of(cut_to_minimum)


@pytest.mark.parametrize("kind", sorted(rp.KIND_MINIMUMS))
def test_a_beacon_kind_one_photo_short_of_its_minimum_is_refused(kind: str) -> None:
    def cut(data: dict[str, Any]) -> None:
        keep = [photo["id"] for photo in data["photos"] if photo["kind"] == kind][: rp.KIND_MINIMUMS[kind] - 1]
        data["photos"] = [photo for photo in data["photos"] if photo["kind"] != kind or photo["id"] in keep]

    assert f"{rp.KIND_MINIMUMS[kind] - 1} {kind}" in refused(variant(cut))


@pytest.mark.parametrize("kind", sorted(rp.KIND_MINIMUMS))
def test_a_beacon_kind_holding_exactly_its_minimum_is_enough(kind: str) -> None:
    def cut(data: dict[str, Any]) -> None:
        keep = [photo["id"] for photo in data["photos"] if photo["kind"] == kind][: rp.KIND_MINIMUMS[kind]]
        data["photos"] = [photo for photo in data["photos"] if photo["kind"] != kind or photo["id"] in keep]

    cut_to_minimum = variant(cut)
    assert sum(photo["kind"] == kind for photo in cut_to_minimum["photos"]) == rp.KIND_MINIMUMS[kind]
    assert f"{kind} photos, fewer" not in problems_of(cut_to_minimum)


@pytest.mark.parametrize(("count", "accepted"), [(60, True), (61, False)])
def test_the_corpus_holds_at_most_sixty_photos(count: int, accepted: bool) -> None:
    assert rp.MAX_PHOTOS == 60 and len(MANIFEST_JSON["photos"]) <= rp.MAX_PHOTOS

    def pad(data: dict[str, Any]) -> None:
        last = photos_of(data, "D")[-1]
        for number in range(99, 99 - (count - len(data["photos"])), -1):
            extra = copy.deepcopy(last)
            extra["id"] = f"rp-d-{number}"
            extra["downloadPath"] = f".cache/real-photos/{extra['id']}/{extra['id']}.jpg"
            extra["statedSubjectSource"] = f"https://commons.wikimedia.org/w/index.php?curid={number}"
            data["photos"].append(extra)
        data["photos"].sort(key=lambda photo: photo["id"])

    padded = variant(pad)
    assert len(padded["photos"]) == count
    assert (problems_of(padded) == "") == accepted
    if not accepted:
        assert f"{count} photos, more than {rp.MAX_PHOTOS}" in problems_of(padded)


def test_a_kind_from_another_tier_is_refused() -> None:
    def swap(data: dict[str, Any]) -> None:
        photos_of(data, "A")[0]["kind"] = "fireworks"

    assert "kind fireworks is not a tier A kind" in refused(variant(swap))


def test_clean_wide_field_photos_carry_a_wide_focal_length_and_span_both_hemispheres() -> None:
    wide = [photo for photo in CORPUS.photos if photo.tier == "A"]
    for photo in wide:
        assert photo.lensFocalLengthMm is not None and photo.lensFocalLengthMm <= rp.WIDE_LENS_MAX_MM, photo.id
        assert photo.cameraMake and photo.cameraModel, photo.id
    assert {photo.hemisphere for photo in wide} >= {"north", "south"}

    def drop_focal(data: dict[str, Any]) -> None:
        del photos_of(data, "A")[0]["lensFocalLengthMm"]

    def long_lens(data: dict[str, Any]) -> None:
        photos_of(data, "A")[0]["lensFocalLengthMm"] = rp.WIDE_LENS_MAX_MM + 0.5

    def all_north(data: dict[str, Any]) -> None:
        for photo in photos_of(data, "A"):
            photo.update(hemisphere="north", hemisphereSource="uploader_statement", hasPlace=False)

    assert rp.WIDE_LENS_MAX_MM == 35.0
    assert "tier A needs lensFocalLengthMm" in refused(variant(drop_focal))
    assert "tier A needs a lens of at most 35 mm" in refused(variant(long_lens))
    assert "tier A has no south photo" in refused(variant(all_north))
    # Exactly at the limit is still wide
    rp.Corpus.model_validate(variant(lambda data: photos_of(data, "A")[0].update(lensFocalLengthMm=rp.WIDE_LENS_MAX_MM)))


def test_phone_photos_span_the_named_makers_two_more_and_both_hemispheres() -> None:
    phones = [photo for photo in CORPUS.photos if photo.tier == "B"]
    for photo in phones:
        assert photo.cameraMake and photo.cameraModel, photo.id
    makers = {photo.cameraMake.lower() for photo in phones}
    assert makers >= set(rp.REQUIRED_PHONE_MAKERS) == {"apple", "google", "samsung"}
    assert len(makers - set(rp.REQUIRED_PHONE_MAKERS)) >= rp.OTHER_PHONE_MAKERS_MINIMUM == 2
    assert {photo.hemisphere for photo in phones} >= {"north", "south"}

    def relabel(maker: str):
        def change(data: dict[str, Any]) -> None:
            for photo in photos_of(data, "B"):
                if photo["cameraMake"].lower() == maker:
                    photo["cameraMake"] = "Fairphone"

        return change

    def one_other_maker(data: dict[str, Any]) -> None:
        for photo in photos_of(data, "B"):
            if photo["cameraMake"].lower() not in rp.REQUIRED_PHONE_MAKERS:
                photo["cameraMake"] = "Fairphone"

    def no_camera(data: dict[str, Any]) -> None:
        del photos_of(data, "B")[0]["cameraModel"]

    def all_south(data: dict[str, Any]) -> None:
        for photo in photos_of(data, "B"):
            photo.update(hemisphere="south", hemisphereSource="uploader_statement", hasPlace=False)

    def two_other_makers(data: dict[str, Any]) -> None:
        others = [photo for photo in photos_of(data, "B") if photo["cameraMake"].lower() not in rp.REQUIRED_PHONE_MAKERS]
        for index, photo in enumerate(others):
            photo["cameraMake"] = ("Fairphone", "Nokia")[index % 2]

    for maker in rp.REQUIRED_PHONE_MAKERS:
        assert f"tier B has no photo from {maker}" in refused(variant(relabel(maker)))
    assert "tier B has 1 makers besides" in refused(variant(one_other_maker))
    assert problems_of(variant(two_other_makers)) == "", "exactly two other makers are enough"
    assert "tier B needs cameraMake and cameraModel" in refused(variant(no_camera))
    assert "tier B has no north photo" in refused(variant(all_south))


def test_stated_subjects_use_the_closed_vocabulary_and_name_their_page() -> None:
    # The 88 IAU constellations, checked against the abbreviations the deep-sky contract holds (OpenNGC splits Serpens)
    abbreviations = set(rp.IAU_CONSTELLATIONS.values())
    assert len(rp.IAU_CONSTELLATIONS) == len(abbreviations) == 88
    assert abbreviations == (set(DEEP_SKY_CONSTELLATIONS) - {"Se1", "Se2"}) | {"Ser"}
    assert rp.SUBJECT_TAGS == set(rp.IAU_CONSTELLATIONS) | {
        "milky_way", "moon", "sun", "planet", "horizon", "clouds", "city_lights", "aircraft", "no_sky"
    }  # fmt: skip
    for photo in CORPUS.photos:
        assert set(photo.statedSubject) <= rp.SUBJECT_TAGS, photo.id
        assert len(set(photo.statedSubject)) == len(photo.statedSubject), photo.id
        assert re.fullmatch(r"https://commons\.wikimedia\.org/w/index\.php\?curid=[1-9]\d*", photo.statedSubjectSource), photo.id
    sources = [photo.statedSubjectSource for photo in CORPUS.photos]
    assert len(sources) == len(set(sources)), "each photo is one Commons page"
    for tag in ("Southern Cross", "crux", "Mount Example", "stars", ""):
        assert "statedSubject" in refused(variant(lambda data, tag=tag: data["photos"][0].update(statedSubject=[tag]))), tag
    titled = "https://commons.wikimedia.org/wiki/File:Stars_over_Example_Town.jpg"
    assert "statedSubjectSource" in refused(variant(lambda data: data["photos"][0].update(statedSubjectSource=titled)))
    assert "a tag is listed twice" in refused(variant(lambda data: data["photos"][0].update(statedSubject=["Crux", "moon", "Crux"])))
    rp.Corpus.model_validate(variant(lambda data: data["photos"][0].update(statedSubject=["Crux", "moon", "Ursa Major", "no_sky"])))
    rp.Corpus.model_validate(variant(lambda data: data["photos"][0].update(statedSubject=[])))
    first_page = MANIFEST_JSON["photos"][0]["statedSubjectSource"]
    assert f"page {first_page} is listed more than once" in refused(
        variant(lambda data: data["photos"][1].update(statedSubjectSource=first_page))
    )


@pytest.mark.parametrize("tail", ["&title=File:Stars_over_Example_Town.jpg", "#Example_Town", "/Example_Town", " Example Town", "\n"])
def test_a_page_link_with_anything_after_the_page_id_is_refused(tail: str) -> None:
    """A title, a fragment or any other text after the id could carry a place name into the manifest."""
    link = MANIFEST_JSON["photos"][0]["statedSubjectSource"] + tail
    assert "statedSubjectSource" in refused(variant(lambda data: data["photos"][0].update(statedSubjectSource=link)))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("latDeg", 30.0),
        ("lonDeg", -40.0),
        ("location", {"latDeg": 30.0, "lonDeg": -40.0}),
        ("capturedAtUTC", "2031-08-30T03:01:00Z"),
        ("localDateTime", "2031-08-30T03:01:00"),
        ("place", "an open ocean"),
        ("title", "Stars over an open ocean"),
        ("description", "Taken from a ship"),
    ],
)
def test_a_field_for_a_coordinate_a_capture_time_or_place_text_is_refused(field: str, value: object) -> None:
    assert field in refused(variant(lambda data: data["photos"][0].update({field: value})))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cameraModel", "2031:08:30 03:01:00"),
        ("cameraModel", "2031-08-30"),
        ("cameraModel", "20310830 0301"),
        ("cameraModel", "30.0000, -40.0000"),
        ("cameraModel", "30.0000 -40.0000"),
        ("cameraModel", "X 30.5 40.5"),
        ("cameraModel", "Cam -40.0000"),
        ("cameraModel", "Cam +30.5"),
        ("cameraMake", "Cam -40.5 S"),
        ("cameraMake", "Taken at 30.5 N 40.5 W"),
        ("cameraMake", "Open Ocean Town, Example Province"),
        ("cameraMake", "30"),
        ("cameraModel", "Canon: EOS"),
        ("cameraModel", "Canon, EOS"),
        ("cameraModel", ""),
        ("lensFocalLengthMm", -40.0),
        ("lensFocalLengthMm", 0),
        ("focalLength35mmEquivMm", -30.5),
        ("exposureTimeSec", -40.0),
        ("exposureTimeSec", 3601),
        ("widthPx", -40),
        ("heightPx", 30.5),
        ("hemisphere", "30 N"),
        ("hemisphereSource", "a map"),
        ("mediaKind", "audio"),
        ("hasPlace", "30.0, -40.0"),
        ("hasPlace", 1),
        ("hasCaptureTime", 1),
        ("hasUtcOffset", 0),
        ("widthPx", True),
        ("exposureTimeSec", True),
        ("statedSubject", "Crux"),
    ],
)
def test_a_value_that_could_hide_a_coordinate_a_time_or_a_place_is_refused(field: str, value: object) -> None:
    assert field in refused(variant(lambda data: data["photos"][0].update({field: value})))


def test_the_booleans_and_the_hemisphere_must_agree_with_each_other() -> None:
    placed = next(photo["id"] for photo in MANIFEST_JSON["photos"] if photo["hasPlace"])
    stated = next(photo["id"] for photo in MANIFEST_JSON["photos"] if photo.get("hemisphereSource") == "uploader_statement")
    unknown = next(photo["id"] for photo in MANIFEST_JSON["photos"] if "hemisphere" not in photo)
    timed = next(photo["id"] for photo in MANIFEST_JSON["photos"] if photo["hasCaptureTime"] and not photo["hasUtcOffset"])

    def change(ident: str, **fields: object):
        def apply(data: dict[str, Any]) -> None:
            photo = next(photo for photo in data["photos"] if photo["id"] == ident)
            for field, value in fields.items():
                if value is None:
                    photo.pop(field, None)
                else:
                    photo[field] = value

        return apply

    exactly = "hemisphereSource is file_metadata exactly when hasPlace"
    assert f"{placed}: {exactly}" in refused(variant(change(placed, hasPlace=False)))
    assert f"{placed}: {exactly}" in refused(variant(change(placed, hemisphereSource="uploader_statement")))
    assert f"{stated}: {exactly}" in refused(variant(change(stated, hasPlace=True)))
    assert f"{stated}: {exactly}" in refused(variant(change(stated, hemisphereSource="file_metadata")))
    assert f"{unknown}: {exactly}" in refused(variant(change(unknown, hasPlace=True)))
    together = "hemisphere and hemisphereSource come together"
    assert f"{stated}: {together}" in refused(variant(change(stated, hemisphereSource=None)))
    assert f"{stated}: {together}" in refused(variant(change(stated, hemisphere=None)))
    assert f"{unknown}: {together}" in refused(variant(change(unknown, hemisphere="north")))
    assert f"{unknown}: {together}" in refused(variant(change(unknown, hemisphereSource="uploader_statement")))
    assert f"{timed}: hasUtcOffset without hasCaptureTime" in refused(variant(change(timed, hasUtcOffset=True, hasCaptureTime=False)))
    # The side of the equator may be stated either way, and a time may come with or without its offset
    other = {"north": "south", "south": "north"}
    for ident in (placed, stated):
        side = next(photo["hemisphere"] for photo in MANIFEST_JSON["photos"] if photo["id"] == ident)
        flipped = problems_of(variant(change(ident, hemisphere=other[side])))
        assert exactly not in flipped and together not in flipped
    assert "hasUtcOffset" not in problems_of(variant(change(timed, hasUtcOffset=True)))


def test_no_manifest_field_holds_a_coordinate_a_capture_time_or_place_text() -> None:
    """Every string is an id, a path, a word from a closed list, a page id link or the file's own camera name, and
    every number is a pixel size, a focal length or an exposure time: there is no field a place or a time fits in."""
    assert set(MANIFEST_JSON) == {"about", "notes", "photos"}
    for photo in MANIFEST_JSON["photos"]:
        assert set(photo) <= PHOTO_KEYS, photo["id"]
        closed = {
            "tier": set(rp.TIER_MINIMUMS),
            "kind": set(rp.KINDS[photo["tier"]]),
            "mediaKind": {"image", "video"},
            "hemisphere": {"north", "south"},
            "hemisphereSource": {"file_metadata", "uploader_statement"},
        }
        for field, value in photo.items():
            if field in closed:
                assert value in closed[field], (photo["id"], field)
            elif field in ("cameraMake", "cameraModel"):
                assert rp.CAMERA_TEXT.fullmatch(value), (photo["id"], field)
                assert not DATE_OR_TIME.search(value) and not COORDINATE.search(value), (photo["id"], field)
            elif field in ("widthPx", "heightPx"):
                assert type(value) is int and value > 0, (photo["id"], field)
            elif field in ("lensFocalLengthMm", "focalLength35mmEquivMm", "exposureTimeSec"):
                assert type(value) in (int, float) and value > 0, (photo["id"], field)
            elif field in ("hasCaptureTime", "hasUtcOffset", "hasPlace"):
                assert type(value) is bool, (photo["id"], field)
            elif field == "statedSubject":
                assert set(value) <= rp.SUBJECT_TAGS, photo["id"]
            else:
                assert field in ("id", "downloadPath", "statedSubjectSource"), (photo["id"], field)
        assert ("hemisphere" in photo) == ("hemisphereSource" in photo), photo["id"]
        # A file that holds a place gives the hemisphere itself; only a file without one leans on the uploader
        assert (photo.get("hemisphereSource") == "file_metadata") == photo["hasPlace"], photo["id"]
        if photo["hasUtcOffset"]:
            assert photo["hasCaptureTime"], photo["id"]
    strings = [
        text
        for photo in MANIFEST_JSON["photos"]
        for value in photo.values()
        for text in (value if isinstance(value, list) else [value])
        if isinstance(text, str)
    ]
    assert len(strings) > 6 * len(MANIFEST_JSON["photos"])
    for text in strings:
        assert not DATE_OR_TIME.search(text) and not COORDINATE.search(text), text
    numbers = [value for photo in MANIFEST_JSON["photos"] for value in photo.values() if type(value) in (int, float)]
    assert all(value > 0 for value in numbers), "a coordinate south or west of zero would be negative"


# =============================================================================================
# The registry and the credit table
# =============================================================================================


def test_each_photo_has_exactly_one_registry_entry_and_the_reverse() -> None:
    assert sorted(entry["path"] for entry in DOWNLOADS) == sorted(photo.downloadPath for photo in CORPUS.photos)
    assert len(BY_PATH) == len(DOWNLOADS) == len(CORPUS.photos)
    assert rp.registry_problems(CORPUS, DOWNLOADS) == []
    one = copy.deepcopy(DOWNLOADS[0])
    assert rp.registry_problems(CORPUS, DOWNLOADS[1:]) == [f"{one['path']}: no registry entry"]
    assert rp.registry_problems(CORPUS, [*DOWNLOADS, one]) == [f"{one['path']}: 2 registry entries"]
    stray = {**one, "path": ".cache/real-photos/rp-z-99/rp-z-99.jpg"}
    assert rp.registry_problems(CORPUS, [*DOWNLOADS, stray]) == [f"{stray['path']}: a registry entry with no photo in the manifest"]


def test_registry_entries_are_commons_originals_within_the_size_limits_with_author_page_and_retrieval_date() -> None:
    assert (rp.MAX_PHOTO_BYTES, rp.MAX_CORPUS_BYTES) == (40_000_000, 500_000_000)
    assert sum(entry["bytes"] for entry in DOWNLOADS) <= rp.MAX_CORPUS_BYTES
    urls = [entry["url"] for entry in DOWNLOADS]
    assert len(urls) == len(set(urls))
    for entry in DOWNLOADS:
        assert entry["fetch"] == "scripts/fetch_download.py", entry["path"]
        assert entry["url"].startswith("https://upload.wikimedia.org/wikipedia/commons/"), entry["path"]
        assert "/thumb/" not in entry["url"], entry["path"]
        assert entry["bytes"] <= rp.MAX_PHOTO_BYTES, entry["path"]
        credit = rp.credit(entry)
        assert credit.author.strip() == credit.author and credit.author, entry["path"]
        assert credit.page.startswith("https://commons.wikimedia.org/wiki/File:"), entry["path"]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", credit.retrieved), entry["path"]
    one = DOWNLOADS[0]

    def problems(**changes: object) -> list[str]:
        return rp.registry_problems(CORPUS, [{**one, **changes}, *DOWNLOADS[1:]])

    assert problems(bytes=rp.MAX_PHOTO_BYTES + 1) == [f"{one['path']}: larger than {rp.MAX_PHOTO_BYTES} bytes"]
    assert problems(bytes=rp.MAX_PHOTO_BYTES) == [], "exactly 40 MB is allowed"
    total = sum(entry["bytes"] for entry in DOWNLOADS)
    too_much = f"the corpus is larger than {rp.MAX_CORPUS_BYTES} bytes"
    assert too_much not in problems(bytes=one["bytes"] + rp.MAX_CORPUS_BYTES - total), "exactly 500 MB is allowed"
    assert problems(bytes=one["bytes"] + rp.MAX_CORPUS_BYTES - total + 1)[-1] == too_much
    assert problems(bytes=rp.MAX_CORPUS_BYTES) == [
        f"{one['path']}: larger than {rp.MAX_PHOTO_BYTES} bytes",
        f"the corpus is larger than {rp.MAX_CORPUS_BYTES} bytes",
    ]
    thumb = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Example.jpg/640px-Example.jpg"
    assert problems(url=thumb) == [f"{one['path']}: the URL is not a Commons original"]
    assert problems(url="https://example.org/Example.jpg") == [f"{one['path']}: the URL is not a Commons original"]
    for elsewhere in ("https://example.org/wikipedia/commons/a/ab/Example.jpg",
                      "https://upload.wikimedia.org.example.org/wikipedia/commons/a/ab/Example.jpg",
                      "http://upload.wikimedia.org/wikipedia/commons/a/ab/Example.jpg",
                      "https://upload.wikimedia.org/wikipedia/commons/archive/a/ab/20310830030100%21Example.jpg",
                      "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Example.jpg",
                      "https://upload.wikimedia.org/wikipedia/en/a/ab/Example.jpg",
                      "https://upload.wikimedia.org/wikipedia/commons/a/ab/Example.jpg#x"):  # fmt: skip
        assert f"{one['path']}: the URL is not a Commons original" in problems(url=elsewhere), elsewhere
    assert problems(fetch="scripts/other.py") == [f"{one['path']}: fetched by scripts/other.py"]
    assert problems(source="Somebody, somewhere") == [f"{one['path']}: the source does not name the author, the page and the retrieval date"]
    # A file name that carries a date or a time of day would put a capture time into the registry
    commons = "https://upload.wikimedia.org/wikipedia/commons/a/ab/"
    for name in ("IMG_20310830_030100.jpg", "Stars_2031-08-30.jpg", "Stars_30.08.2031.jpg", "Stars_30_August_2031.jpg",
                 "Stars_August_30%2C_2031.jpg", "Stars_03.01.59.jpg", "Stars_310830_030100.jpg"):  # fmt: skip
        assert problems(url=commons + name) == [f"{one['path']}: the URL or the source carries a date or a time of day"], name
    for name in ("Stars_2031.jpg", "Stars_(54078655928).jpg", "Stars%2C_2031%2C_01.jpg", "Stars_4.0.jpg", "Stars_May.jpg"):
        assert problems(url=commons + name) == [], name
    dated_page = re.sub(r"(\.\w+), retrieved", r"_2031-08-30\1, retrieved", one["source"])
    assert dated_page != one["source"]
    assert problems(source=dated_page) == [f"{one['path']}: the URL or the source carries a date or a time of day"]
    # So would an author credit that carries one
    for moment in ("2031-08-30", "30 August 2031", "03:01"):
        dated_author = one["source"].replace(". Wikimedia Commons file page ", f" {moment}. Wikimedia Commons file page ")
        assert rp.credit({**one, "source": dated_author}).author == f"{rp.credit(one).author} {moment}"
        assert problems(source=dated_author) == [f"{one['path']}: the URL or the source carries a date or a time of day"], moment


MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
COMMONS_FOLDER = "https://upload.wikimedia.org/wikipedia/commons/a/ab/"
DATED = "the URL or the source carries a date or a time of day"


def _problems_with_name(name: str) -> list[str]:
    """What the registry check says when the first photo's file is named `name` on Commons."""
    one = DOWNLOADS[0]
    return rp.registry_problems(CORPUS, [{**one, "url": COMMONS_FOLDER + quote(name)}, *DOWNLOADS[1:]])


@given(
    day=st.dates(min_value=date(1990, 1, 1), max_value=date(2099, 12, 31)),
    separator=st.sampled_from(["-", "_", ".", " ", ""]),
    prefix=st.sampled_from(["Stars_", "IMG_", "Moon over the sea ", "PXL_", ""]),
    suffix=st.sampled_from(["", "_030159", " (2)", "_edit"]),
)
@example(day=date(2031, 8, 30), separator="", prefix="IMG_", suffix="_030159")
@example(day=date(1990, 1, 1), separator="-", prefix="", suffix="")
@example(day=date(2099, 12, 31), separator=" ", prefix="Moon over the sea ", suffix=" (2)")
def test_property_a_file_name_with_a_year_month_day_date_is_refused(day: date, separator: str, prefix: str, suffix: str) -> None:
    name = f"{prefix}{day.year}{separator}{day.month:02d}{separator}{day.day:02d}{suffix}.jpg"
    assert _problems_with_name(name) == [f"{DOWNLOADS[0]['path']}: {DATED}"], name


@given(
    day=st.dates(min_value=date(1990, 1, 1), max_value=date(2099, 12, 31)),
    short=st.booleans(),
    space=st.sampled_from([" ", "_"]),
    form=st.sampled_from(["day month year", "month day, year", "day.month.year", "day-month-year"]),
)
@example(day=date(2031, 8, 30), short=False, space="_", form="month day, year")
@example(day=date(2031, 5, 3), short=True, space=" ", form="day month year")
@example(day=date(2031, 8, 30), short=False, space=" ", form="day.month.year")
def test_property_a_file_name_with_a_written_or_day_first_date_is_refused(day: date, short: bool, space: str, form: str) -> None:
    month = MONTHS[day.month - 1][:3] if short else MONTHS[day.month - 1]
    name = {
        "day month year": f"Stars{space}{day.day}{space}{month}{space}{day.year}.jpg",
        "month day, year": f"Stars{space}{month}{space}{day.day},{space}{day.year}.jpg",
        "day.month.year": f"Stars{space}{day.day:02d}.{day.month:02d}.{day.year}.jpg",
        "day-month-year": f"Stars{space}{day.day:02d}-{day.month:02d}-{day.year}.jpg",
    }[form]
    assert _problems_with_name(name) == [f"{DOWNLOADS[0]['path']}: {DATED}"], name


OPEN_TERMS = st.sampled_from(["BY", "BY-SA"])
VERSIONS = st.sampled_from(["1.0", "2.0", "2.1", "2.5", "3.0", "4.0"])
PORTS = st.one_of(st.just(""), st.sampled_from([" de", " us", " fr", " au", " jp", " ca", " nl"]))


@given(terms=OPEN_TERMS, version=VERSIONS, port=PORTS)
@example(terms="BY-SA", version="3.0", port=" us")
@example(terms="BY", version="2.1", port=" jp")
def test_property_every_cc_by_and_by_sa_version_is_allowed_and_links_only_to_itself(terms: str, version: str, port: str) -> None:
    licence = f"CC {terms} {version}{port}"
    assert rp.licence_allowed(licence)
    link = f"https://creativecommons.org/licenses/{terms.lower()}/{version}{port.replace(' ', '/')}"
    assert rp.licence_link_matches(licence, link) and rp.licence_link_matches(licence, link + "/deed.en")
    other_terms = "by" if terms == "BY-SA" else "by-sa"
    assert not rp.licence_link_matches(licence, link.replace(f"/{terms.lower()}/", f"/{other_terms}/"))
    other_version = "3.0" if version != "3.0" else "4.0"
    assert not rp.licence_link_matches(licence, link.replace(f"/{version}", f"/{other_version}"))
    # A port the licence does not name, and another host, are other licences' links
    assert not rp.licence_link_matches(licence, link + "/xx")
    assert not rp.licence_link_matches(licence, link.replace("https://creativecommons.org", "https://example.org"))


@given(
    terms=OPEN_TERMS,
    version=VERSIONS,
    port=PORTS,
    closed=st.sampled_from(["-NC", "-ND", "-NC-ND", "-NC-SA", "-ND-NC"]),
    where=st.sampled_from(["after BY", "after the terms", "after the version", "as a port"]),
)
@example(terms="BY", version="4.0", port="", closed="-NC", where="after BY")
@example(terms="BY-SA", version="4.0", port="", closed="-ND", where="as a port")
@example(terms="BY", version="2.0", port=" de", closed="-NC-ND", where="after the version")
def test_property_a_noncommercial_or_noderivatives_licence_is_never_allowed(
    terms: str, version: str, port: str, closed: str, where: str
) -> None:
    licence = {
        "after BY": f"CC BY{closed}{terms[2:]} {version}{port}",
        "after the terms": f"CC {terms}{closed} {version}{port}",
        "after the version": f"CC {terms} {version}{closed}{port}",
        "as a port": f"CC {terms} {version} {closed[1:3].lower()}",
    }[where]
    assert not rp.licence_allowed(licence), licence
    assert _problems_with_licence(licence) == [f"{DOWNLOADS[0]['path']}: licence {licence!r} is not on the allow list"]


def _problems_with_licence(licence: str) -> list[str]:
    one = DOWNLOADS[0]
    return rp.registry_problems(CORPUS, [{**one, "licence": licence}, *DOWNLOADS[1:]])


@pytest.mark.parametrize(
    "licence",
    ["CC0", "Public domain", "CC BY 2.0", "CC BY 2.5", "CC BY 3.0", "CC BY 4.0", "CC BY-SA 2.0", "CC BY-SA 3.0",
     "CC BY-SA 4.0", "CC BY-SA 3.0 de", "CC BY 2.0 fr", "CC BY-SA 2.5 ca", "CC BY-SA 3.0 us", "CC BY-SA 2.1 jp"],
)  # fmt: skip
def test_an_open_licence_is_allowed(licence: str) -> None:
    assert rp.licence_allowed(licence)


@pytest.mark.parametrize(
    "licence",
    ["CC BY-NC 4.0", "CC BY-NC-SA 4.0", "CC BY-ND 2.0", "CC BY-NC-ND 3.0", "CC BY-SA", "CC BY", "GFDL", "GFDL 1.2",
     "Copyrighted free use", "Attribution", "FAL", "cc by 4.0", "CC BY 4.0 NC", "CC BY-SA 4.0 or later NC", " CC0",
     "CC0 ", "CC BY 4.0\nCC BY-NC 4.0", "No restrictions", "Fair use", "", "CC BY 4.0 nc", "CC BY-SA 4.0 nd",
     "CC BY 3.7", "CC BY-SA 5.0", "CC BY 4.0 deu", "CC BY 4", "CC BY-SA 4.0 DE", "Public domain?", "CC0 1.0 NC",
     "CC SA 4.0", "CC BY-SA-NC 4.0", "CC BY ", "CC BY-SA ", "CC BY  de", "CC BY-SA 4.0 ", "CC BY 4.0 d", "CC0 1.0",
     "Public domain mark"],
)  # fmt: skip
def test_any_other_licence_is_refused(licence: str) -> None:
    assert not rp.licence_allowed(licence)
    assert _problems_with_licence(licence) == [f"{DOWNLOADS[0]['path']}: licence {licence!r} is not on the allow list"]


def test_every_licence_is_on_the_allow_list_and_its_link_is_that_licence() -> None:
    for entry in DOWNLOADS:
        assert rp.licence_allowed(entry["licence"]), entry["path"]
        assert rp.licence_link_matches(entry["licence"], entry["licenceUrl"]), entry["path"]
    assert rp.licence_link_matches("CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0")
    assert rp.licence_link_matches("CC BY 2.0", "https://creativecommons.org/licenses/by/2.0/")
    assert rp.licence_link_matches("CC BY-SA 3.0 de", "https://creativecommons.org/licenses/by-sa/3.0/de/deed.en")
    assert rp.licence_link_matches("CC0", "https://creativecommons.org/publicdomain/zero/1.0/deed.en")
    assert not rp.licence_link_matches("CC BY 4.0", "https://creativecommons.org/licenses/by-sa/4.0")
    assert not rp.licence_link_matches("CC BY-SA 4.0", "https://creativecommons.org/licenses/by-nc-sa/4.0")
    assert not rp.licence_link_matches("CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/3.0")
    assert not rp.licence_link_matches("CC BY 4.0", "https://example.org/licenses/by/4.0")
    assert not rp.licence_link_matches("CC0", "https://creativecommons.org/licenses/by/4.0")
    one = DOWNLOADS[0]
    wrong = "https://creativecommons.org/licenses/by-nc-nd/4.0/"
    assert rp.registry_problems(CORPUS, [{**one, "licenceUrl": wrong}, *DOWNLOADS[1:]]) == [
        f"{one['path']}: the licence link is not that of {one['licence']}"
    ]


def test_the_doc_credits_every_photo_and_says_what_the_corpus_is_not() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")
    assert not any(dash in doc for dash in DASHES)
    rows = rp.credit_rows(CORPUS, DOWNLOADS)
    assert len(rows) == len(CORPUS.photos)
    for row in rows:
        assert row in doc.splitlines(), row
    for photo, row in zip(CORPUS.photos, rows, strict=True):
        entry = BY_PATH[photo.downloadPath]
        assert row.startswith(f"| `{photo.id}` | {rp.credit(entry).author} | [{entry['licence']}]({entry['licenceUrl']}) | ")
        assert photo.statedSubjectSource in row
    tiers = Counter(photo.tier for photo in CORPUS.photos)
    for tier, count in sorted(tiers.items()):
        assert re.search(rf"^\| {tier}, [^|]+\| {rp.TIER_MINIMUMS[tier]} \| {count} \|", doc, re.M), tier
    for required in (
        "no ground truth",
        "not in the repository",
        "uv run python -m research.benchmarks.real_photos fetch",
        "uv run python -m research.benchmarks.real_photos status",
        "data/downloads.json",
        "uploader's statement",
        f"{len(CORPUS.photos)} photos",
        # What the doc says of the fetch command follows the command's own numbers
        "only for an entry whose URL is a Commons original on `https://upload.wikimedia.org`",
        "a user or password part, a port, another spelling of the host",
        "plain http on `127.0.0.1` with a port",
        f"is taken as {cli.RETRY_AFTER_DEFAULT_S} s",
        f"plus {cli.RETRY_MARGIN_S} s, never longer than {cli.MAX_WAIT_S} s",
        f"at most {cli.MAX_RATE_LIMIT_WAITS} times in one run",
        f"asks for more than {cli.MAX_WAIT_S} s",
        "prints only the HTTP status code",
    ):
        assert required in doc, required
    assert not DATE_OR_TIME.search(re.sub(r"retrieved on \d{4}-\d{2}-\d{2}", "", doc))


# =============================================================================================
# The fetch and status commands
# =============================================================================================

PAYLOADS = {f"/photo-{n}.jpg": f"real photo corpus test payload {n}\n".encode() * (100 + n) for n in range(1, 12)}
# Words that only the test server says: in a header of every answer, in the reason phrase of every 429, 404 and
# redirect, in the answer that is not HTTP and in every redirect target. No log may show them.
SERVER_WORDS = "words-only-the-server-said"
NOT_HTTP = f"NOT-HTTP {SERVER_WORDS}\r\n\r\n".encode()
# Redirect targets that urllib gives up on before it opens a connection
REDIRECTS = {
    "/to-another-scheme.jpg": f"gopher://127.0.0.1/{SERVER_WORDS}",
    "/to-no-address.jpg": f"http://[{SERVER_WORDS}",
    "/to-no-port.jpg": f"http://127.0.0.1:{SERVER_WORDS}/",
}


class _Recorder(http.server.BaseHTTPRequestHandler):
    """Serves PAYLOADS and records each request's path and User-Agent. While `refusals` holds a count for a path it
    answers 429 instead, with `retry_after` as its Retry-After header when that is not None. /not-http.jpg gets an
    answer that is not HTTP, and each path of REDIRECTS a redirect to its target."""

    requests: list[tuple[str, str | None]] = []
    refusals: dict[str, int] = {}
    retry_after: str | None = "7"

    def do_GET(self) -> None:  # noqa: N802 (the http.server API)
        kind = type(self)
        kind.requests.append((self.path, self.headers.get("User-Agent")))
        if self.path == "/not-http.jpg":
            self.wfile.write(NOT_HTTP)
            return
        if self.path in REDIRECTS:
            self.send_response(302, f"Found {SERVER_WORDS}")
            self.send_header("Location", REDIRECTS[self.path])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if kind.refusals.get(self.path, 0) > 0:
            kind.refusals[self.path] -= 1
            self.send_response(429, f"Too Many {SERVER_WORDS}")
            if kind.retry_after is not None:
                self.send_header("Retry-After", kind.retry_after)
            self.send_header("X-Note", SERVER_WORDS)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = PAYLOADS.get(self.path, b"")
        self.send_response(200 if body else 404, None if body else f"Not {SERVER_WORDS}")
        self.send_header("X-Note", SERVER_WORDS)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 (the http.server API)
        return


class Wire:
    """What a run tried to reach: `connections` holds each (host, port) http.client was told to connect to, in its
    own reading of the URL, and `lookups` each name handed to the system resolver. `ports` holds the ports of
    127.0.0.1 that are let through; None lets every port of 127.0.0.1 through."""

    def __init__(self, ports: set[int] | None) -> None:
        self.connections: list[tuple[str, int]] = []
        self.lookups: list[str] = []
        self.ports = ports


@contextlib.contextmanager
def loopback_only(ports: set[int] | None = None) -> Iterator[Wire]:
    """Record every connection http.client is told to open and every name handed to the resolver, and cut each one
    that is not for 127.0.0.1 (on one of `ports`, when given). So no test here can send anything off the machine,
    whatever the command under test decides, and a host that only looks like 127.0.0.1 shows up under its own name."""
    wire = Wire(ports)
    connect, getaddrinfo, gethostbyname = http.client.HTTPConnection.connect, socket.getaddrinfo, socket.gethostbyname

    def recording_connect(self: http.client.HTTPConnection) -> None:
        wire.connections.append((self.host, self.port))
        if self.host != "127.0.0.1" or (wire.ports is not None and self.port not in wire.ports):
            raise OSError("this test lets no request leave the machine")
        connect(self)

    def recording_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        wire.lookups.append(host.decode("latin-1") if isinstance(host, bytes) else str(host))
        if wire.lookups[-1] != "127.0.0.1":
            raise OSError("this test hands no name to the resolver")
        return getaddrinfo(host, *args, **kwargs)

    def recording_gethostbyname(host: Any) -> Any:
        wire.lookups.append(str(host))
        if wire.lookups[-1] != "127.0.0.1":
            raise OSError("this test hands no name to the resolver")
        return gethostbyname(host)

    http.client.HTTPConnection.connect = recording_connect  # type: ignore[method-assign]
    socket.getaddrinfo, socket.gethostbyname = recording_getaddrinfo, recording_gethostbyname
    try:
        yield wire
    finally:
        http.client.HTTPConnection.connect = connect  # type: ignore[method-assign]
        socket.getaddrinfo, socket.gethostbyname = getaddrinfo, gethostbyname


@pytest.fixture
def wire() -> Iterator[Wire]:
    with loopback_only() as wire:
        yield wire


@pytest.fixture
def server(wire: Wire) -> Iterator[str]:
    _Recorder.requests, _Recorder.refusals, _Recorder.retry_after = [], {}, "7"
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def fake_downloads(server: str, count: int = 3) -> list[dict[str, Any]]:
    return [
        {
            "path": f".cache/real-photos/rp-a-{n:02d}/rp-a-{n:02d}.jpg",
            "fetch": "scripts/fetch_download.py",
            "url": f"{server}/photo-{n}.jpg",
            "bytes": len(PAYLOADS[f"/photo-{n}.jpg"]),
            "sha256": hashlib.sha256(PAYLOADS[f"/photo-{n}.jpg"]).hexdigest(),
            "licence": "CC0",
            "licenceUrl": "https://creativecommons.org/publicdomain/zero/1.0/",
            "source": "test payload",
        }
        for n in range(1, count + 1)
    ]


def log_lines(out: io.StringIO) -> list[str]:
    """The lines a command printed, once it is sure that none of the server's own words is among them."""
    assert SERVER_WORDS not in out.getvalue()
    return out.getvalue().splitlines()


def closed_port() -> int:
    """A loopback port that nothing listens on: bound once, then closed."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


FETCHED = "fetched, size and sha256 match"
PHOTO = [f".cache/real-photos/rp-a-{n:02d}/rp-a-{n:02d}.jpg" for n in range(0, 12)]  # PHOTO[n] is fake_downloads' nth path


def test_fetch_gets_each_missing_photo_through_the_registry_fetch_six_seconds_apart(tmp_path: Path, server: str) -> None:
    downloads = fake_downloads(server)
    assert [download["path"] for download in downloads] == PHOTO[1:4]
    present = tmp_path / downloads[1]["path"]
    present.parent.mkdir(parents=True)
    present.write_bytes(PAYLOADS["/photo-2.jpg"])
    waits: list[float] = []
    out = io.StringIO()
    assert cli.fetch_all(downloads, root=tmp_path, sleep=waits.append, out=out) == 0
    assert _Recorder.requests == [("/photo-1.jpg", "Albedian/0.1 (research prototype)"), ("/photo-3.jpg", "Albedian/0.1 (research prototype)")]
    assert waits == [cli.SECONDS_BETWEEN_REQUESTS] and cli.SECONDS_BETWEEN_REQUESTS == 6.0
    for download in downloads:
        assert (tmp_path / download["path"]).read_bytes() == PAYLOADS[f"/photo-{download['path'][-5]}.jpg"]
    assert log_lines(out) == [
        f"{PHOTO[1]}: {FETCHED}",
        f"{PHOTO[3]}: {FETCHED}",
        "3 of 3 photos present and matching their checksum (2 fetched, 0 failed)",
    ]
    # A second run makes no request and waits for nothing
    _Recorder.requests, waits[:] = [], []
    assert cli.fetch_all(downloads, root=tmp_path, sleep=waits.append, out=io.StringIO()) == 0
    assert _Recorder.requests == [] and waits == []


def test_fetch_keeps_nothing_that_differs_from_the_registry_and_goes_on_to_the_rest(tmp_path: Path, server: str) -> None:
    downloads = fake_downloads(server)
    downloads[0]["sha256"] = "0" * 64
    downloads[1]["url"] = f"{server}/missing.jpg"
    waits: list[float] = []
    out = io.StringIO()
    assert cli.fetch_all(downloads, root=tmp_path, sleep=waits.append, out=out) == 1
    assert [path for path, _ in _Recorder.requests] == ["/photo-1.jpg", "/missing.jpg", "/photo-3.jpg"]
    assert waits == [6.0, 6.0]
    assert sorted(path.name for path in tmp_path.rglob("*") if path.is_file()) == ["rp-a-03.jpg"]
    assert log_lines(out) == [
        f"{PHOTO[1]}: not fetched: refused by scripts/fetch_download.py, or the size or SHA-256 differs from the registry",
        f"{PHOTO[2]}: not fetched: HTTP 404",
        f"{PHOTO[3]}: {FETCHED}",
        "1 of 3 photos present and matching their checksum (1 fetched, 2 failed)",
    ]


def test_fetch_writes_nothing_a_server_said_but_the_status_code(tmp_path: Path, server: str) -> None:
    """An answer that is not HTTP, a redirect that urllib gives up on, a 404 and no answer at all each end as one plain
    line: no traceback, none of the server's own words, and the run goes on to the next photo."""
    downloads = fake_downloads(server, count=7)
    odd = ["/not-http.jpg", "/to-another-scheme.jpg", "/to-no-address.jpg", "/to-no-port.jpg", "/missing.jpg"]
    for download, route in zip(downloads, odd):
        download["url"] = server + route
    downloads[5]["url"] = f"http://127.0.0.1:{closed_port()}/photo-6.jpg"
    waits: list[float] = []
    out = io.StringIO()
    assert cli.fetch_all(downloads, root=tmp_path, sleep=waits.append, out=out) == 1
    assert [path for path, _ in _Recorder.requests] == [*odd, "/photo-7.jpg"]
    assert waits == [6.0] * 6
    assert log_lines(out) == [
        f"{PHOTO[1]}: not fetched: BadStatusLine",
        f"{PHOTO[2]}: not fetched: HTTP 302",
        f"{PHOTO[3]}: not fetched: ValueError",
        f"{PHOTO[4]}: not fetched: InvalidURL",
        f"{PHOTO[5]}: not fetched: HTTP 404",
        f"{PHOTO[6]}: not fetched: ConnectionRefusedError",
        f"{PHOTO[7]}: {FETCHED}",
        "1 of 7 photos present and matching their checksum (1 fetched, 6 failed)",
    ]
    assert sorted(path.name for path in tmp_path.rglob("*") if path.is_file()) == ["rp-a-07.jpg"]


DOT = chr(0x3002)  # the ideographic full stop, which the resolver's own name handling reads as a dot
REFUSED = "the URL is not an original on https://upload.wikimedia.org"


def urls_outside_the_rule(port: int) -> list[str]:
    """URLs that `fetch` must refuse before any request; the loopback ones carry the port of a server on 127.0.0.1.
    Many are a Commons original or that server to one reader of URLs and something else to another."""
    original = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Example.jpg"
    photo = f"http://127.0.0.1:{port}/photo-1.jpg"
    other_digits = "".join(chr(0x0966 + int(digit)) for digit in str(port))  # the same port in Devanagari digits
    return [
        # another host, or another scheme or kind of file on the right one
        "https://example.org/wikipedia/commons/a/ab/Example.jpg",
        "https://upload.wikimedia.org.example.org/wikipedia/commons/a/ab/Example.jpg",
        "https://commons.wikimedia.org/wiki/File:Example.jpg",
        "http://upload.wikimedia.org/wikipedia/commons/a/ab/Example.jpg",
        "ftp://upload.wikimedia.org/wikipedia/commons/a/ab/Example.jpg",
        "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Example.jpg/640px-Example.jpg",
        "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Example.jpg",
        f"{original}/x",
        f"https://127.0.0.1:{port}/photo-1.jpg",
        f"ftp://127.0.0.1:{port}/photo-1.jpg",
        f"http://127.0.0.2:{port}/photo-1.jpg",
        f"http://127.0.0.1.example.org:{port}/photo-1.jpg",
        # a user or password part: http.client takes it for part of the host name, urlsplit drops it
        "https://someone@upload.wikimedia.org/wikipedia/commons/a/ab/Example.jpg",
        "https://upload.wikimedia.org@example.org/wikipedia/commons/a/ab/Example.jpg",
        f"http://someone.example.invalid@127.0.0.1:{port}/photo-1.jpg",
        f"http://user:secret@127.0.0.1:{port}/photo-1.jpg",
        f"http://@127.0.0.1:{port}/photo-1.jpg",
        f"http://127.0.0.1@127.0.0.1:{port}/photo-1.jpg",
        f"http://someone@[::1]:{port}/photo-1.jpg",
        f"http://someone@localhost:{port}/photo-1.jpg",
        f"http://127.0.0.1:{port}@example.org/photo-1.jpg",
        # a port the rule does not expect, or one that is not spelled in plain digits
        "https://upload.wikimedia.org:443/wikipedia/commons/a/ab/Example.jpg",
        "https://upload.wikimedia.org:8443/wikipedia/commons/a/ab/Example.jpg",
        "https://upload.wikimedia.org:/wikipedia/commons/a/ab/Example.jpg",
        "http://127.0.0.1/photo-1.jpg",
        "http://127.0.0.1:/photo-1.jpg",
        "http://127.0.0.1:0/photo-1.jpg",
        f"http://127.0.0.1:0{port}/photo-1.jpg",
        f"http://127.0.0.1:+{port}/photo-1.jpg",
        f"http://127.0.0.1:-{port}/photo-1.jpg",
        f"http://127.0.0.1: {port}/photo-1.jpg",
        f"http://127.0.0.1:{str(port)[0]}_{str(port)[1:]}/photo-1.jpg",
        f"http://127.0.0.1:{other_digits}/photo-1.jpg",
        "http://127.0.0.1:65536/photo-1.jpg",
        f"http://127.0.0.1:{port + 65536}/photo-1.jpg",
        f"http://127.0.0.1:{port}:{port}/photo-1.jpg",
        # another spelling of the host: a name, IPv6, an address in another notation, an escaped or foreign dot
        f"http://localhost:{port}/photo-1.jpg",
        f"http://[::1]:{port}/photo-1.jpg",
        f"http://[::ffff:127.0.0.1]:{port}/photo-1.jpg",
        f"http://[0:0:0:0:0:0:0:1]:{port}/photo-1.jpg",
        f"http://[127.0.0.1]:{port}/photo-1.jpg",
        f"http://127.1:{port}/photo-1.jpg",
        f"http://2130706433:{port}/photo-1.jpg",
        f"http://0x7f.0.0.1:{port}/photo-1.jpg",
        f"http://0177.0.0.1:{port}/photo-1.jpg",
        f"http://127.000.000.001:{port}/photo-1.jpg",
        f"http://127.0.0.1.:{port}/photo-1.jpg",
        f"http://127%2E0%2E0%2E1:{port}/photo-1.jpg",
        f"http://%31%32%37.0.0.1:{port}/photo-1.jpg",
        f"http://127.0.0.1%3A{port}/photo-1.jpg",
        f"http://127{DOT}0{DOT}0{DOT}1:{port}/photo-1.jpg",
        f"http://{chr(0x2460)}{chr(0x2461)}{chr(0x2466)}.0.0.1:{port}/photo-1.jpg",
        "https://upload%2Ewikimedia%2Eorg/wikipedia/commons/a/ab/Example.jpg",
        f"https://upload{DOT}wikimedia{DOT}org/wikipedia/commons/a/ab/Example.jpg",
        "https://UPLOAD.wikimedia.org/wikipedia/commons/a/ab/Example.jpg",
        "https://upload.wikimedia.org./wikipedia/commons/a/ab/Example.jpg",
        "HTTPS://upload.wikimedia.org/wikipedia/commons/a/ab/Example.jpg",
        f"HTTP://127.0.0.1:{port}/photo-1.jpg",
        # a backslash, a space, a control character, a fragment or a wrapper: one reader keeps it, another does not
        "https://upload.wikimedia.org\\@example.org/wikipedia/commons/a/ab/Example.jpg",
        "https://upload.wikimedia.org/wikipedia/commons/a/ab/Example.jpg\\@example.org",
        "https://upload.wikimedia.org/wikipedia/commons/a/ab/Exam\tple.jpg",
        "https://upload.wikimedia.org/wikipedia/commons/a/ab/Exam ple.jpg",
        "https://upload.wikimedia.org/wikipedia/commons/a/ab/Example.jpg\n",
        "https://upload.wikimedia.org/wikipedia/commons/a/ab/Example@2x.jpg",
        f"{original}#x",
        f" {original}",
        f"<URL:{original}>",
        f"http://127.0.0.1\\@example.org:{port}/photo-1.jpg",
        f"http://example.org\\@127.0.0.1:{port}/photo-1.jpg",
        f"http://127.0.0.1:{port}\\photo-1.jpg",
        f"http://127.0.0.1\t:{port}/photo-1.jpg",
        f"{photo}\\",
        f"{photo}@example.org",
        f"{photo}?x=@example.org",
        f"{photo}?x=\\",
        f"{photo}#x",
        f"{photo} ",
        f" {photo}",
        f"<URL:{photo}>",
        f"URL:{photo}",
        f"{photo}{chr(0xE9)}",
        f"{photo}%",
        f"{photo}%zz",
        f"http://127.0.0.1:{port}/photo 1.jpg",
        # no path, or no host where one is expected
        f"http://127.0.0.1:{port}",
        f"http://127.0.0.1:{port}?photo-1.jpg",
        f"http:127.0.0.1:{port}/photo-1.jpg",
        f"http:/127.0.0.1:{port}/photo-1.jpg",
        "http:///photo-1.jpg",
        "http://[",
        "",
    ]


def urls_inside_the_rule(port: int) -> list[tuple[str, tuple[str, int]]]:
    """Plainly spelled URLs that `fetch` asks for, each with the host and port its request goes to."""
    commons, loopback = ("upload.wikimedia.org", 443), ("127.0.0.1", port)
    return [
        ("https://upload.wikimedia.org/wikipedia/commons/a/ab/Example.jpg", commons),
        ("https://upload.wikimedia.org/wikipedia/commons/0/9f/Example_%28one%29%2C_two.JPG?utm_source=x&utm_campaign=y", commons),
        ("https://upload.wikimedia.org/wikipedia/commons/f/f0/Ex'am(p)le!,~two;=$&+:*.jpeg", commons),
        (f"http://127.0.0.1:{port}/photo-1.jpg", loopback),
        (f"http://127.0.0.1:{port}/", loopback),
        (f"http://127.0.0.1:{port}/a/b//c.jpg?x=1&y=%20/?", loopback),
        (f"http://127.0.0.1:{port}/~!$&'()*+,;=:._-%41%e9", loopback),
        ("http://127.0.0.1:1/photo-1.jpg", ("127.0.0.1", 1)),
        ("http://127.0.0.1:65535/photo-1.jpg", ("127.0.0.1", 65535)),
    ]


def test_fetch_refuses_before_any_request_a_url_that_is_not_a_commons_original(tmp_path: Path, server: str, wire: Wire) -> None:
    """The registry tests hold the tracked file to Commons originals; the command holds whatever it is handed to the
    same rule, read the way the request itself would be. Plain http on 127.0.0.1 with a port, where this test's
    server listens, is the one other URL it asks for."""
    port = int(server.rsplit(":", 1)[1])
    wire.ports = {port}
    first, last = fake_downloads(server, count=2)
    turned_away = [
        {**first, "path": f".cache/real-photos/rp-d-{n:02d}/rp-d-{n:02d}.jpg", "url": url}
        for n, url in enumerate(urls_outside_the_rule(port), start=1)
    ]
    downloads = [first, *turned_away, last]
    waits: list[float] = []
    out = io.StringIO()
    assert cli.fetch_all(downloads, root=tmp_path, sleep=waits.append, out=out) == 1
    assert wire.connections == [("127.0.0.1", port)] * 2, "one connection for each of the two entries on this server"
    assert set(wire.lookups) == {"127.0.0.1"}, "no other name reached the resolver"
    assert [path for path, _ in _Recorder.requests] == ["/photo-1.jpg", "/photo-2.jpg"]
    assert waits == [6.0], "an entry that is refused makes no request, so nothing is paced for it"
    assert log_lines(out) == [
        f"{first['path']}: {FETCHED}",
        *(f"{entry['path']}: not fetched: {REFUSED}" for entry in turned_away),
        f"{last['path']}: {FETCHED}",
        f"2 of {len(downloads)} photos present and matching their checksum (2 fetched, {len(turned_away)} failed)",
    ]
    assert sorted(path.name for path in tmp_path.rglob("*") if path.is_file()) == ["rp-a-01.jpg", "rp-a-02.jpg"]


@pytest.mark.parametrize(
    "url", [None, 5, 5.5, True, b"http://127.0.0.1:8123/photo-1.jpg", ["http://127.0.0.1:8123/photo-1.jpg"], {"url": "x"}], ids=repr
)
def test_fetch_refuses_a_url_that_is_not_text(tmp_path: Path, url: object) -> None:
    out = io.StringIO()
    with loopback_only(ports=set()) as wire:
        entry = {**fake_downloads("http://127.0.0.1:8123", count=1)[0], "url": url}
        assert cli.fetch_all([entry], root=tmp_path, sleep=lambda seconds: None, out=out) == 1
    assert (wire.connections, wire.lookups) == ([], [])
    assert log_lines(out)[0] == f"{PHOTO[1]}: not fetched: {REFUSED}"


def test_fetch_asks_for_a_plainly_spelled_url_at_its_own_host_and_port(tmp_path: Path) -> None:
    """The other side of the rule: these are asked for, each at exactly one host and port (every connection is cut)."""
    inside = urls_inside_the_rule(8123)
    downloads = [
        {**fake_downloads("http://127.0.0.1:8123", count=1)[0], "path": f".cache/real-photos/rp-d-{n:02d}/rp-d-{n:02d}.jpg", "url": url}
        for n, (url, _) in enumerate(inside, start=1)
    ]
    out = io.StringIO()
    with loopback_only(ports=set()) as wire:
        assert cli.fetch_all(downloads, root=tmp_path, sleep=lambda seconds: None, out=out) == 1
    assert wire.connections == [target for _, target in inside] and wire.lookups == []
    assert log_lines(out)[:-1] == [f"{entry['path']}: not fetched: OSError" for entry in downloads]


def test_fetch_asks_only_upload_wikimedia_org_for_every_photo_of_the_corpus(tmp_path: Path, wire: Wire) -> None:
    """The command's own rule lets every real entry through, to that host alone (each connection is cut off here)."""
    waits: list[float] = []
    out = io.StringIO()
    assert cli.fetch_all(DOWNLOADS, root=tmp_path, sleep=waits.append, out=out) == 1
    assert wire.connections == [("upload.wikimedia.org", 443)] * len(DOWNLOADS) and wire.lookups == []
    assert log_lines(out) == [
        *(f"{entry['path']}: not fetched: OSError" for entry in DOWNLOADS),
        f"0 of {len(DOWNLOADS)} photos present and matching their checksum (0 fetched, {len(DOWNLOADS)} failed)",
    ]
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []


# Generated URLs. No server listens for these: every connection and every lookup is cut and only recorded.
URL_CHARACTER = r"[A-Za-z0-9._~!$&'()*+,;=:-]|%[0-9A-Fa-f]{2}"
URL_TAIL = rf"(?:/(?:{URL_CHARACTER})*)+(?:\?(?:{URL_CHARACTER}|[/?])*)?"
MADE_UP_ENTRY = {
    "path": ".cache/real-photos/rp-a-01/rp-a-01.jpg", "fetch": "scripts/fetch_download.py", "bytes": 1, "sha256": "0" * 64,
    "licence": "CC0", "licenceUrl": "https://creativecommons.org/publicdomain/zero/1.0/", "source": "test payload",
}  # fmt: skip


def target_by_the_text(url: object) -> tuple[str, int] | None:
    """The test's own statement of the rule, read from the text alone: the host and port a request for this URL may
    go to, or None when `fetch` must refuse it."""
    if not isinstance(url, str):
        return None
    if rp.COMMONS_ORIGINAL.fullmatch(url) and re.fullmatch(rf"https://upload\.wikimedia\.org{URL_TAIL}", url):
        return ("upload.wikimedia.org", 443)
    loopback = re.fullmatch(rf"http://127\.0\.0\.1:([1-9][0-9]{{0,4}}){URL_TAIL}", url)
    if loopback and int(loopback[1]) <= 65535:
        return ("127.0.0.1", int(loopback[1]))
    return None


def reached(url: object) -> tuple[list[tuple[str, int]], list[str], str]:
    """Hand `fetch` one made-up entry with this URL while every connection and lookup is cut: the (host, port) pairs
    it tried, the names it gave the resolver, and what it printed about the entry."""
    out = io.StringIO()
    with loopback_only(ports=set()) as wire, tempfile.TemporaryDirectory() as root:
        code = cli.fetch_all([{**MADE_UP_ENTRY, "url": url}], root=Path(root), sleep=lambda seconds: None, out=out)
        kept = [path for path in Path(root).rglob("*") if path.is_file()]
    lines = out.getvalue().splitlines()
    assert code == 1 and kept == [] and len(lines) == 2 and lines[0].startswith(f"{MADE_UP_ENTRY['path']}: not fetched: ")
    return wire.connections, wire.lookups, lines[0].split(": not fetched: ", 1)[1]


def check_where_fetch_goes(url: object) -> None:
    """`fetch` opens a connection exactly when the text of the URL says it may, and then only to the host and port
    the text names. Where it does, three readings agree: http.client's (the connection), the test's (the text) and
    urlsplit's, which also finds no user or password."""
    connections, lookups, said = reached(url)
    target = target_by_the_text(url)
    assert lookups == [], ascii(url)
    if target is None:
        assert (connections, said) == ([], REFUSED), ascii(url)
    else:
        assert (connections, said) == ([target], "OSError"), ascii(url)
        split = urlsplit(url)
        assert (split.hostname, split.port or 443, split.username, split.password) == (*target, None, None), ascii(url)


def with_examples(urls: list[Any]):
    """Each of these URLs as an explicit example of a property test, ahead of the generated ones."""

    def decorate(test):
        for url in reversed(urls):
            test = example(url=url)(test)
        return test

    return decorate


PLAIN = string.ascii_letters + string.digits + "._~!$&'()*+,;=:-"
PIECE = st.one_of(st.text(alphabet=PLAIN, max_size=6), st.sampled_from(["%20", "%2F", "%2e", "%41", "%C3%A9", "%00"]))
SEGMENT = st.lists(PIECE, max_size=3).map("".join)
QUERY = st.one_of(st.just(""), st.lists(st.one_of(PIECE, st.sampled_from(["/", "?"])), max_size=3).map(lambda pieces: "?" + "".join(pieces)))
LOOPBACK_TAIL = st.tuples(st.lists(SEGMENT, min_size=1, max_size=3), QUERY).map(
    lambda tail: "".join(f"/{segment}" for segment in tail[0]) + tail[1]
)
HEX = st.sampled_from("0123456789abcdef")
COMMONS_QUERY = st.one_of(st.just(""), st.text(alphabet=string.ascii_letters + string.digits + "_.=&-", max_size=12).map(lambda query: f"?{query}"))
COMMONS_TAIL = st.tuples(HEX, HEX, HEX, SEGMENT.filter(bool), COMMONS_QUERY).map(
    lambda tail: f"/wikipedia/commons/{tail[0]}/{tail[1]}{tail[2]}/{tail[3]}{tail[4]}"
)
PLAIN_PORT = st.integers(min_value=1, max_value=65535).map(str)
PLAIN_URLS = st.one_of(
    st.tuples(PLAIN_PORT, LOOPBACK_TAIL).map(lambda parts: f"http://127.0.0.1:{parts[0]}{parts[1]}"),
    COMMONS_TAIL.map(lambda tail: f"https://upload.wikimedia.org{tail}"),
)
# One edit away from a plain URL: a few characters anywhere in it replaced by text that readers of URLs treat specially
SPECIAL = [
    "@", ":", "/", "\\", "?", "#", "%", "%2E", "%2e", "%40", "%3A", "%2F", "%00", ".", "..", " ", "\t", "\n", "\r", chr(0), chr(0x7F),
    "[", "]", "<", ">", "0", "1", "+", "-", "_", DOT, chr(0xFF0E), chr(0x2460), chr(0xE9), chr(0x0661), "localhost", "example.org",
    "127.0.0.1", "upload.wikimedia.org", "someone@", "user:secret@", "URL:", "x",
]  # fmt: skip


def edited(url: str, position: int, text: str, cut: int) -> str:
    """The URL with `cut` characters at a position replaced by `text` (an insertion when `cut` is 0)."""
    start = position % (len(url) + 1)
    return url[:start] + text + url[start + cut :]


ONE_EDIT_AWAY = st.builds(
    edited,
    PLAIN_URLS,
    st.one_of(st.integers(min_value=0, max_value=40), st.integers(min_value=0, max_value=400)),
    st.one_of(st.sampled_from(SPECIAL), st.text(max_size=3)),
    st.integers(min_value=0, max_value=3),
)
# Put together from parts: each part is the plain one three times in four and otherwise an odd one (a user part, a
# host in another spelling, a port in another form, an odd tail), so most of these URLs are one or two parts away
# from a plain URL and some are plain
ODD_USERS = st.one_of(
    st.sampled_from(["someone@", "user:secret@", "@", ":@", "127.0.0.1@", "upload.wikimedia.org@", "someone.example.invalid@",
                     "a%40b@", "x\\@", "a@b@", "%2F@", "127.0.0.1:80@"]),
    st.text(alphabet=PLAIN + "%", max_size=10).map(lambda text: f"{text}@"),
)  # fmt: skip
ODD_HOSTS = st.sampled_from([
    "127.0.0.1", "upload.wikimedia.org",
    "localhost", "LOCALHOST", "[::1]", "[::ffff:127.0.0.1]", "[0:0:0:0:0:0:0:1]", "[::1%25lo0]", "[127.0.0.1]", "::1",
    "127.1", "2130706433", "0x7f.0.0.1", "0177.0.0.1", "127.000.000.001", "127.0.0.1.", ".127.0.0.1", "127.0.0.2",
    "127%2E0%2E0%2E1", "127.0.0%2e1", "%31%32%37.0.0.1", "127.0.0.1%00", "127.0.0.1%2F", "127.0.0.1%3A80",
    f"127{DOT}0{DOT}0{DOT}1", f"{chr(0x2460)}{chr(0x2461)}{chr(0x2466)}.0.0.1", "127.0.0.1\\", "127.0.0.1\\.example.org",
    "127.0.0.1\t", "127.0.0.1 ", "127.0.0.1.example.org", "example.org", "example.org\\@127.0.0.1", "127.0.0.1#.example.org",
    "UPLOAD.wikimedia.org", "upload.wikimedia.org.", "upload%2Ewikimedia%2Eorg", f"upload{DOT}wikimedia{DOT}org",
    "upload.wikimedia.org.example.org", "upload.wikimedia.org\\.example.org", "upload.wikimedia.org%2F.example.org",
    "commons.wikimedia.org", "",
])  # fmt: skip
ODD_PORTS = st.one_of(
    st.just(""),
    PLAIN_PORT.map(lambda port: f":{port}"),
    st.sampled_from([":", ":0", ":00", ":65536", ":99999", ":443", ":80", ":8443", ":-1", ":+80", ": 80", ":80 ", ":8_0", ":080",
                     ":%38%30", ":80:80", ":80\\", ":80\t", ":0x50", ":8e1", f":{chr(0x0968)}{chr(0x0966)}", f":{chr(0xFF18)}{chr(0xFF10)}"]),
    st.tuples(st.sampled_from(["0", "+", "-", " ", "00"]), PLAIN_PORT).map(lambda parts: f":{parts[0]}{parts[1]}"),
)  # fmt: skip
ODD_TAILS = st.one_of(
    LOOPBACK_TAIL,
    COMMONS_TAIL,
    st.sampled_from(["", "?x", "#x", "\\x.jpg", "/x\\y.jpg", "/x@example.org", "/x y.jpg", "/x\ty.jpg", "/x.jpg\n", "/x.jpg#f", "/%",
                     "/%zz", f"/x{chr(0xE9)}", "/wikipedia/commons/thumb/a/ab/E.jpg/1px-E.jpg", "/wikipedia/en/a/ab/E.jpg",
                     "/wikipedia/commons/a/ab/x\\@example.org", "/wikipedia/commons/a/ab/E.jpg/x"]),
)  # fmt: skip
ODD_SCHEMES = st.sampled_from(["http", "https", "HTTP", "Https", "ftp", "file", "", "http ", " https", "ht\ttps", "https+http",
                               "javascript", "URL:http"])  # fmt: skip
ODD_SLASHES = st.sampled_from([":", ":/", ":///", "//", ":\\\\", "://\\", " ://"])
ODD_WRAPS = st.sampled_from([" {}", "{} ", "{}\n", "\t{}", "<{}>", "<URL:{}>", "URL:{}", "{}#", "{}#x"])


@st.composite
def put_together(draw: st.DrawFn) -> str:
    on_this_machine = draw(st.booleans())

    def part(plain: st.SearchStrategy[str], odd: st.SearchStrategy[str]) -> str:
        return draw(odd if draw(st.integers(min_value=0, max_value=3)) == 0 else plain)

    scheme = part(st.just("http" if on_this_machine else "https"), ODD_SCHEMES)
    slashes = part(st.just("://"), ODD_SLASHES)
    user = part(st.just(""), ODD_USERS)
    host = part(st.just("127.0.0.1" if on_this_machine else "upload.wikimedia.org"), ODD_HOSTS)
    port = part(PLAIN_PORT.map(lambda port: f":{port}") if on_this_machine else st.just(""), ODD_PORTS)
    tail = part(LOOPBACK_TAIL if on_this_machine else COMMONS_TAIL, ODD_TAILS)
    return part(st.just("{}"), ODD_WRAPS).format(scheme + slashes + user + host + port + tail)


def test_the_tables_of_urls_agree_with_the_rule_as_the_text_states_it() -> None:
    assert [url for url in urls_outside_the_rule(8123) if target_by_the_text(url) is not None] == []
    assert [url for url, target in urls_inside_the_rule(8123) if target_by_the_text(url) != target] == []


@settings(deadline=None, max_examples=200)
@given(url=PLAIN_URLS)
@with_examples([url for url, _ in urls_inside_the_rule(8123)])
def test_property_a_plainly_spelled_url_is_asked_for_at_its_own_host_and_port(url: str) -> None:
    assert target_by_the_text(url) is not None, ascii(url)
    check_where_fetch_goes(url)


@settings(deadline=None, max_examples=400)
@given(url=st.one_of(ONE_EDIT_AWAY, put_together()))
@with_examples(urls_outside_the_rule(8123))
def test_property_no_url_outside_the_rule_ever_reaches_a_connection(url: str) -> None:
    """Whatever the URL (a user part, an odd port, IPv6, an escaped dot, a backslash), a connection is opened only
    where the text of a plainly spelled URL says, and nothing else reaches http.client or the resolver."""
    check_where_fetch_goes(url)


def test_fetch_asks_for_the_first_eight_photos_six_seconds_apart_and_for_the_rest_seventy(tmp_path: Path, server: str) -> None:
    """Commons let eight originals through 6 s apart and refused the ninth; 70 s apart it refused none."""
    assert (cli.SECONDS_BETWEEN_REQUESTS, cli.FAST_REQUESTS, cli.SLOW_SECONDS_BETWEEN_REQUESTS) == (6.0, 8, 70.0)
    downloads = fake_downloads(server, count=11)
    present = tmp_path / downloads[4]["path"]
    present.parent.mkdir(parents=True)
    present.write_bytes(PAYLOADS["/photo-5.jpg"])
    waits: list[float] = []
    out = io.StringIO()
    assert cli.fetch_all(downloads, root=tmp_path, sleep=waits.append, out=out) == 0
    # Ten requests (the fifth photo is there already): seven fast pauses lead to the eighth request, then two slow ones
    assert [path for path, _ in _Recorder.requests] == [f"/photo-{n}.jpg" for n in (1, 2, 3, 4, 6, 7, 8, 9, 10, 11)]
    assert waits == [6.0] * 7 + [70.0] * 2
    assert log_lines(out)[-1] == "11 of 11 photos present and matching their checksum (10 fetched, 0 failed)"


def waiting(seconds: int) -> str:
    """The one line a 429 that is waited for leaves: the status code and the command's own wait in whole seconds."""
    return f"HTTP 429; waiting {seconds} s, then one request every 70 s"


def test_fetch_waits_as_long_as_a_429_asks_then_goes_on_at_the_slow_pace(tmp_path: Path, server: str) -> None:
    downloads = fake_downloads(server)
    _Recorder.refusals = {"/photo-1.jpg": 1, "/photo-3.jpg": 2}
    waits: list[float] = []
    out = io.StringIO()
    assert cli.fetch_all(downloads, root=tmp_path, sleep=waits.append, out=out) == 0
    assert [path for path, _ in _Recorder.requests] == [
        "/photo-1.jpg", "/photo-1.jpg", "/photo-2.jpg", "/photo-3.jpg", "/photo-3.jpg", "/photo-3.jpg"
    ]  # fmt: skip
    # The wait the server asks for plus the margin stands in for the pause; afterwards every pause is the slow one
    assert waits == [37, 70.0, 70.0, 37, 37]
    assert log_lines(out) == [
        f"{PHOTO[1]}: {waiting(37)}",
        f"{PHOTO[1]}: {FETCHED}",
        f"{PHOTO[2]}: {FETCHED}",
        f"{PHOTO[3]}: {waiting(37)}",
        f"{PHOTO[3]}: {waiting(37)}",
        f"{PHOTO[3]}: {FETCHED}",
        "3 of 3 photos present and matching their checksum (3 fetched, 0 failed)",
    ]
    assert sorted(path.name for path in tmp_path.rglob("*") if path.is_file()) == ["rp-a-01.jpg", "rp-a-02.jpg", "rp-a-03.jpg"]
    assert (cli.RETRY_MARGIN_S, cli.RETRY_AFTER_DEFAULT_S, cli.MAX_WAIT_S) == (30, 600, 900)


def refused_once(tmp_path: Path, server: str, header: str | None, count: int = 3) -> tuple[int, list[float], list[str]]:
    """Run fetch on `count` made-up photos while the server answers the second with 429 and this Retry-After."""
    _Recorder.refusals, _Recorder.retry_after = {"/photo-2.jpg": 1}, header
    waits: list[float] = []
    out = io.StringIO()
    code = cli.fetch_all(fake_downloads(server, count), root=tmp_path, sleep=waits.append, out=out)
    return code, waits, log_lines(out)


# Superscript two and one half are the digits of another kind that a header's bytes can carry (str.isdigit takes the first)
NOT_SECONDS = [None, "Wed, 21 Oct 2031 07:28:00 GMT", "soon", "", "-5", "+5", "7.5", "7 s", "1e2", "0x10", chr(0xB2), "7" + chr(0xB2), chr(0xBD)]


@pytest.mark.parametrize("header", NOT_SECONDS)
def test_fetch_waits_ten_minutes_when_a_429_names_no_plain_run_of_ascii_digits(tmp_path: Path, server: str, header: str | None) -> None:
    code, waits, lines = refused_once(tmp_path, server, header)
    assert code == 0
    assert waits == [6.0, 630, 70.0]
    assert lines == [
        f"{PHOTO[1]}: {FETCHED}",
        f"{PHOTO[2]}: {waiting(630)}",
        f"{PHOTO[2]}: {FETCHED}",
        f"{PHOTO[3]}: {FETCHED}",
        "3 of 3 photos present and matching their checksum (3 fetched, 0 failed)",
    ]


@pytest.mark.parametrize(("header", "wait"), [("0", 30), ("7", 37), ("0007", 37), ("870", 900), ("871", 900), ("900", 900)])
def test_fetch_waits_what_a_429_asks_plus_the_margin_and_never_longer_than_fifteen_minutes(
    tmp_path: Path, server: str, header: str, wait: int
) -> None:
    code, waits, lines = refused_once(tmp_path, server, header)
    assert code == 0
    assert waits == [6.0, wait, 70.0]
    assert lines[1] == f"{PHOTO[2]}: {waiting(wait)}" and len(lines) == 5
    assert cli.MAX_WAIT_S == 900


@pytest.mark.parametrize(
    "header",
    ["901", "999999999", "9" * 400, "9" * 5000, "0" * 400 + "901"],
    ids=["901", "nine nines", "400 digits", "5000 digits", "400 zeros and 901"],
)
def test_fetch_stops_without_waiting_when_a_429_asks_for_more_than_fifteen_minutes(tmp_path: Path, server: str, header: str) -> None:
    downloads = fake_downloads(server, count=4)
    present = tmp_path / downloads[3]["path"]
    present.parent.mkdir(parents=True)
    present.write_bytes(PAYLOADS["/photo-4.jpg"])
    code, waits, lines = refused_once(tmp_path, server, header, count=4)
    # One good photo and one refusal: no wait is made for it, the third photo is not asked for and the fourth was there
    assert waits == [6.0]
    assert [path for path, _ in _Recorder.requests] == ["/photo-1.jpg", "/photo-2.jpg"]
    assert code == 1
    assert lines == [
        f"{PHOTO[1]}: {FETCHED}",
        f"{PHOTO[2]}: not fetched: HTTP 429",
        "stopped: the server asks for a wait longer than 900 s; 2 of 4 photos are still missing; run fetch again later",
        "2 of 4 photos present and matching their checksum (1 fetched, 1 failed)",
    ]
    assert sorted(path.name for path in tmp_path.rglob("*") if path.is_file()) == ["rp-a-01.jpg", "rp-a-04.jpg"]
    # A later run goes on from where this one stopped
    _Recorder.requests, _Recorder.refusals, waits = [], {}, []
    assert cli.fetch_all(downloads, root=tmp_path, sleep=waits.append, out=io.StringIO()) == 0
    assert [path for path, _ in _Recorder.requests] == ["/photo-2.jpg", "/photo-3.jpg"] and waits == [6.0]


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("0", 0), ("7", 7), ("0007", 7), ("900", 900), ("0" * 5000 + "900", 900),
        ("901", None), ("1000", None), ("999999999", None), ("9" * 400, None), ("9" * 5000, None),
        (None, 600), ("", 600), ("soon", 600), ("Wed, 21 Oct 2031 07:28:00 GMT", 600), ("-5", 600), ("+5", 600), ("7.5", 600),
        (" 7", 600), ("7 ", 600), ("\t7", 600), ("7\n", 600), ("7 s", 600), ("7,7", 600),
        (chr(0xB2), 600), (chr(0x667), 600), (chr(0xFF17), 600), ("7" + chr(0x667), 600), (chr(0x667) * 5000, 600),
        (7, 600), (7.5, 600), (b"7", 600),
    ],
    ids=lambda value: ascii(value) if len(ascii(value)) < 40 else f"{ascii(value)[:9]} and so on to {len(value)}",
)  # fmt: skip
def test_only_a_plain_run_of_ascii_digits_is_read_as_a_number_of_seconds(value: object, seconds: int | None) -> None:
    """0x667 is the Arabic-Indic seven and 0xFF17 the fullwidth seven: int() reads both as 7. No value may raise."""
    asked = cli.asked_wait_s(value)
    assert asked == seconds and (asked is None or type(asked) is int)


def test_fetch_stops_after_six_waits_and_asks_for_nothing_more(tmp_path: Path, server: str) -> None:
    assert cli.MAX_RATE_LIMIT_WAITS == 6
    downloads = fake_downloads(server)
    _Recorder.refusals = {"/photo-2.jpg": 99}
    waits: list[float] = []
    out = io.StringIO()
    assert cli.fetch_all(downloads, root=tmp_path, sleep=waits.append, out=out) == 1
    # One good photo, then seven refusals of the second: six waited for, the seventh ends the run before the third
    assert [path for path, _ in _Recorder.requests] == ["/photo-1.jpg"] + ["/photo-2.jpg"] * 7
    assert waits == [6.0] + [37] * 6
    assert log_lines(out) == [
        f"{PHOTO[1]}: {FETCHED}",
        *[f"{PHOTO[2]}: {waiting(37)}"] * 6,
        f"{PHOTO[2]}: not fetched: HTTP 429",
        "stopped: the server answered HTTP 429 again after 6 waits; 2 of 3 photos are still missing; run fetch again later",
        "1 of 3 photos present and matching their checksum (1 fetched, 1 failed)",
    ]
    assert sorted(path.name for path in tmp_path.rglob("*") if path.is_file()) == ["rp-a-01.jpg"]
    # A later run goes on from where this one stopped
    _Recorder.requests, _Recorder.refusals, waits[:] = [], {}, []
    assert cli.fetch_all(downloads, root=tmp_path, sleep=waits.append, out=io.StringIO()) == 0
    assert [path for path, _ in _Recorder.requests] == ["/photo-2.jpg", "/photo-3.jpg"] and waits == [6.0]


def test_a_429_that_carries_no_headers_at_all_is_waited_for_ten_minutes_each_time(
    tmp_path: Path, wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    """urllib hands over the headers of every answer; an error built without them must not break the command either."""

    def urlopen(request: urllib.request.Request, *args: Any, **kwargs: Any) -> Any:
        raise urllib.error.HTTPError(request.full_url, 429, SERVER_WORDS, None, None)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    downloads = fake_downloads(f"http://127.0.0.1:{closed_port()}", count=2)
    waits: list[float] = []
    out = io.StringIO()
    assert cli.fetch_all(downloads, root=tmp_path, sleep=waits.append, out=out) == 1
    assert waits == [630] * 6 and wire.connections == []
    assert log_lines(out) == [
        *[f"{PHOTO[1]}: {waiting(630)}"] * 6,
        f"{PHOTO[1]}: not fetched: HTTP 429",
        "stopped: the server answered HTTP 429 again after 6 waits; 2 of 2 photos are still missing; run fetch again later",
        "0 of 2 photos present and matching their checksum (0 fetched, 1 failed)",
    ]


def test_status_counts_the_photos_that_are_present_and_match_their_checksum(tmp_path: Path, server: str) -> None:
    downloads = fake_downloads(server)
    out = io.StringIO()
    assert cli.status(downloads, root=tmp_path, out=out) == 1
    assert out.getvalue().splitlines()[-1] == "0 of 3 photos present and matching their checksum (3 absent, 0 different)"
    for n, body in ((1, PAYLOADS["/photo-1.jpg"]), (2, PAYLOADS["/photo-2.jpg"][:-1] + b"x"), (3, PAYLOADS["/photo-3.jpg"][:-1])):
        target = tmp_path / downloads[n - 1]["path"]
        target.parent.mkdir(parents=True)
        target.write_bytes(body)
    out = io.StringIO()
    assert cli.status(downloads, root=tmp_path, out=out) == 1
    lines = out.getvalue().splitlines()
    assert lines[-1] == "1 of 3 photos present and matching their checksum (0 absent, 2 different)"
    assert [line for line in lines if "different" in line][:2] == [
        ".cache/real-photos/rp-a-02/rp-a-02.jpg: different from the registry",
        ".cache/real-photos/rp-a-03/rp-a-03.jpg: different from the registry",
    ]
    (tmp_path / downloads[1]["path"]).write_bytes(PAYLOADS["/photo-2.jpg"])
    (tmp_path / downloads[2]["path"]).write_bytes(PAYLOADS["/photo-3.jpg"])
    out = io.StringIO()
    assert cli.status(downloads, root=tmp_path, out=out) == 0
    assert out.getvalue().splitlines()[-1] == "3 of 3 photos present and matching their checksum (0 absent, 0 different)"
    assert _Recorder.requests == [], "status makes no request"


def test_the_commands_take_only_the_corpus_entries_of_the_registry(capsys: pytest.CaptureFixture[str]) -> None:
    """The registry also lists downloads of several gigabytes; the corpus commands must never fetch those."""
    everything = json.loads(rp.REGISTRY.read_text(encoding="utf-8"))["downloads"]
    assert len(everything) > len(DOWNLOADS) > 0
    assert [entry for entry in everything if entry["path"].startswith(".cache/real-photos/")] == DOWNLOADS
    code = cli.main(["status"])
    last = capsys.readouterr().out.splitlines()[-1]
    match = re.fullmatch(r"(\d+) of (\d+) photos present and matching their checksum \((\d+) absent, (\d+) different\)", last)
    assert match is not None, last
    assert int(match.group(2)) == len(CORPUS.photos)
    assert code == (0 if int(match.group(1)) == len(CORPUS.photos) else 1)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["delete"])
    assert stopped.value.code == 2


# =============================================================================================
# Reading a file: made-up JPEGs written by Pillow under tmp_path (no real photo, no real place or time)
# =============================================================================================

# The synthetic moments and open-ocean places the trip guard's own tests use
NORTH_ATLANTIC = {"GPSLatitudeRef": "N", "GPSLatitude": (30.0, 0.0, 0.0), "GPSLongitudeRef": "W", "GPSLongitude": (40.0, 0.0, 0.0)}
INDIAN_OCEAN = {"GPSLatitudeRef": "S", "GPSLatitude": (20.0, 0.0, 0.0), "GPSLongitudeRef": "E", "GPSLongitude": (60.0, 0.0, 0.0)}
MADE_UP_CAMERA = {"Make": "Synthetic", "Model": "Synthetic Phone 1"}
MADE_UP_EXPOSURE = {"FocalLength": IFDRational(425, 100), "FocalLengthIn35mmFilm": 26, "ExposureTime": IFDRational(1, 2)}


def made_up_jpeg(path: Path, tags: dict[str, Any] | None = None, exif_ifd: dict[str, Any] | None = None,
                 gps: dict[str, Any] | None = None) -> Path:  # fmt: skip
    """A 64 by 48 JPEG with the given EXIF, as Pillow writes it."""
    exif = Image.Exif()
    for name, value in (tags or {}).items():
        exif[getattr(ExifTags.Base, name)] = value
    for ifd_id, names, values in ((ExifTags.IFD.Exif, ExifTags.Base, exif_ifd), (ExifTags.IFD.GPSInfo, ExifTags.GPS, gps)):
        if values:
            ifd = exif.get_ifd(ifd_id)
            for name, value in values.items():
                ifd[getattr(names, name)] = value
    Image.new("RGB", (64, 48), (10, 12, 30)).save(path, "JPEG", exif=exif)
    return path


def test_read_facts_gives_nothing_a_file_does_not_hold(tmp_path: Path) -> None:
    path = made_up_jpeg(tmp_path / "bare.jpg")
    facts = rp.read_facts(path)
    assert facts == {
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "extension": "jpg",
        "mediaKind": "image",
        "widthPx": 64,
        "heightPx": 48,
        "cameraMake": None,
        "cameraModel": None,
        "lensFocalLengthMm": None,
        "focalLength35mmEquivMm": None,
        "exposureTimeSec": None,
        "hasCaptureTime": False,
        "hasUtcOffset": False,
        "hasPlace": False,
        "hemisphere": None,
    }


def test_read_facts_reads_the_camera_and_a_time_without_its_offset(tmp_path: Path) -> None:
    path = made_up_jpeg(tmp_path / "timed.jpg", MADE_UP_CAMERA, {"DateTimeOriginal": "2031:08:30 03:01:00", **MADE_UP_EXPOSURE})
    facts = rp.read_facts(path)
    assert (facts["cameraMake"], facts["cameraModel"]) == ("Synthetic", "Synthetic Phone 1")
    assert facts["lensFocalLengthMm"] == pytest.approx(4.25) and facts["focalLength35mmEquivMm"] == pytest.approx(26.0)
    assert facts["exposureTimeSec"] == pytest.approx(0.5)
    assert (facts["hasCaptureTime"], facts["hasUtcOffset"], facts["hasPlace"], facts["hemisphere"]) == (True, False, False, None)


@pytest.mark.parametrize(("gps", "hemisphere"), [(NORTH_ATLANTIC, "north"), (INDIAN_OCEAN, "south")], ids=["north", "south"])
def test_read_facts_tells_the_side_of_the_equator_from_the_latitude(tmp_path: Path, gps: dict[str, Any], hemisphere: str) -> None:
    zoned = {"DateTimeOriginal": "2031:03:05 04:10:00", "OffsetTimeOriginal": "+04:00"}
    facts = rp.read_facts(made_up_jpeg(tmp_path / "placed.jpg", MADE_UP_CAMERA, zoned, gps))
    assert (facts["hasCaptureTime"], facts["hasUtcOffset"], facts["hasPlace"], facts["hemisphere"]) == (True, True, True, hemisphere)
    # A place without a time is still a place, and the facts hold no coordinate and no time
    placed_only = rp.read_facts(made_up_jpeg(tmp_path / "placed-only.jpg", gps=gps))
    assert (placed_only["hasCaptureTime"], placed_only["hasUtcOffset"], placed_only["hasPlace"], placed_only["hemisphere"]) == (
        False, False, True, hemisphere
    )  # fmt: skip
    for value in facts.values():
        assert value not in (30.0, -30.0, 40.0, -40.0, 20.0, -20.0, 60.0, -60.0) or isinstance(value, bool)
        assert not (isinstance(value, str) and DATE_OR_TIME.search(value))


def test_read_facts_refuses_what_the_upload_path_refuses(tmp_path: Path) -> None:
    text = tmp_path / "notes.txt"
    text.write_bytes(b"not a photo")
    with pytest.raises(Refused):
        rp.read_facts(text)
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(made_up_jpeg(tmp_path / "whole.jpg").read_bytes()[:200])
    with pytest.raises(ProbeError):
        rp.read_facts(broken)


# =============================================================================================
# The photos themselves, where they are in the shared .cache
# =============================================================================================

PRESENT = [
    pytest.param(photo, marks=pytest.mark.local_dataset(photo.downloadPath), id=photo.id) for photo in CORPUS.photos
]


@pytest.fixture(scope="module")
def facts_of() -> dict[str, dict[str, Any]]:
    """What the upload path's readers say of each file, read once per file."""
    return {}


def _facts(cache: dict[str, dict[str, Any]], photo: rp.Photo) -> dict[str, Any]:
    if photo.id not in cache:
        cache[photo.id] = rp.read_facts(ROOT / photo.downloadPath)
    return cache[photo.id]


@pytest.mark.parametrize("photo", PRESENT)
def test_each_present_photo_has_the_registry_size_and_sha256(photo: rp.Photo, facts_of: dict[str, dict[str, Any]]) -> None:
    entry = BY_PATH[photo.downloadPath]
    path = ROOT / photo.downloadPath
    assert path.stat().st_size == entry["bytes"]
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1 << 22):
            digest.update(block)
    assert digest.hexdigest() == entry["sha256"]
    # The reader that fills the manifest sees the same bytes
    facts = _facts(facts_of, photo)
    assert (facts["bytes"], facts["sha256"]) == (entry["bytes"], entry["sha256"])


@pytest.mark.parametrize("photo", PRESENT)
def test_each_present_photo_decodes_at_the_manifest_size(photo: rp.Photo, facts_of: dict[str, dict[str, Any]]) -> None:
    facts = _facts(facts_of, photo)
    assert (facts["mediaKind"], facts["widthPx"], facts["heightPx"]) == (photo.mediaKind, photo.widthPx, photo.heightPx)
    assert photo.downloadPath.endswith("." + facts["extension"])
    with Image.open(ROOT / photo.downloadPath) as image:
        image.load()
        assert image.size == (photo.widthPx, photo.heightPx)


@pytest.mark.parametrize("photo", PRESENT)
def test_the_ingest_readers_give_the_manifest_camera_fields_and_booleans(photo: rp.Photo, facts_of: dict[str, dict[str, Any]]) -> None:
    facts = _facts(facts_of, photo)
    assert (facts["cameraMake"], facts["cameraModel"]) == (photo.cameraMake, photo.cameraModel)
    for field in ("lensFocalLengthMm", "focalLength35mmEquivMm", "exposureTimeSec"):
        stated = getattr(photo, field)
        if stated is None:
            assert facts[field] is None, field
        else:
            # The manifest keeps six significant digits of what the file's rational gives
            assert facts[field] == pytest.approx(stated, rel=1e-5), field
    assert (facts["hasCaptureTime"], facts["hasUtcOffset"], facts["hasPlace"]) == (
        photo.hasCaptureTime, photo.hasUtcOffset, photo.hasPlace
    )  # fmt: skip
    # Only the side of the equator is compared; the latitude itself is never printed or kept
    assert facts["hemisphere"] == (photo.hemisphere if photo.hemisphereSource == "file_metadata" else None)
