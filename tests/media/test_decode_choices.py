"""The decoder's choices, each with ffprobe's and ffmpeg's answers given by fakes, so every branch is reached without
a file that would need it: which tile grid and which stream a HEIC is decoded from, the colour fields a HEIC's own
stream tags give when no nclx does, a still's stored depth, the 16 bit path's formats and refusals, a tool that
fails, a deep mode, and a tile's 10 bit planes. No test here starts a child; each still has the file's bound.
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import ExifTags, Image

from services.ingest import jobs as ingest_jobs
from services.ingest.metadata import HeifInfo
from services.ingest.store import sniff as store_sniff
from services.media import colour, decode

from child_bounds import bounded  # noqa: E402,F401


def refusal_of(function, *args) -> str | None:
    """The decoder's sentence when the call is refused, None when it returns."""
    try:
        function(*args)
    except decode.DecodeRefusal as refusal:
        return refusal.sentence
    return None


# =============================================================================================
# Which tile grid: as the solve job picks it
# =============================================================================================


def grid(width: int, height: int, default: int | None = None, kind: str = "Tile Grid", second: tuple | None = None) -> dict:
    components = [{"width": width, "height": height, "name": f"{width}x{height}"}]
    if second is not None:
        components.append({"width": second[0], "height": second[1], "name": "second"})
    group = {"type": kind, "components": components}
    if default is not None:
        group["disposition"] = {"default": default}
    return group


# (what the groups are, the groups, the name of the component picked, or None for no grid, or "refused")
GRID_CASES = [
    ("no groups", [], None),
    ("groups that are not tile grids, or hold no components", ["not a group", grid(4, 4, kind="Other"),
                                                               {"type": "Tile Grid", "components": []}], None),
    ("one grid of the stored size", [grid(4, 4)], "4x4"),
    ("one grid of the stored size beside one of another", [grid(2, 2), grid(4, 4)], "4x4"),
    ("only a grid of another size", [grid(2, 2)], "refused"),
    ("two of the stored size, the default one", [grid(4, 4, default=0), {**grid(4, 4, default=1), "components": [{"width": 4, "height": 4, "name": "default"}]}], "default"),
    ("two of the stored size, one without a disposition", [grid(4, 4), {**grid(4, 4, default=1), "components": [{"width": 4, "height": 4, "name": "default"}]}], "default"),
    ("two of the stored size, neither default", [grid(4, 4, default=0), grid(4, 4, default=0)], "refused"),
    ("two of the stored size, both default", [grid(4, 4, default=1), grid(4, 4, default=1)], "refused"),
    ("a grid's first component is its size, a second one is not read", [grid(4, 4, second=(2, 2))], "4x4"),
    ("a grid whose second component only has the stored size", [grid(2, 2, second=(4, 4))], "refused"),
]


@pytest.mark.parametrize("groups,picked", [case[1:] for case in GRID_CASES], ids=[case[0] for case in GRID_CASES])
def test_the_tile_grid_is_the_one_the_solve_job_picks(groups: list, picked: str | None) -> None:
    try:
        ours = decode._pick_tile_grid(groups, 4, 4)
    except decode.DecodeRefusal as refusal:
        assert refusal.sentence == decode.UNDECODABLE
        ours = "refused"
    try:
        theirs = ingest_jobs._pick_tile_grid(groups, 4, 4)
    except ingest_jobs.SolveJobError:
        theirs = "refused"
    name = ours if ours in (None, "refused") else ours["name"]
    assert name == picked
    assert ours == theirs


def test_decode_imports_the_solve_jobs_tile_grid_picker_instead_of_copying_it(monkeypatch) -> None:
    called = []

    def sentinel(groups: list, width: int, height: int):
        called.append((width, height))
        return {"name": "from-solve-job"}

    monkeypatch.setattr(decode, "_solve_pick_tile_grid", sentinel, raising=False)
    assert decode._pick_tile_grid([], 4, 4) == {"name": "from-solve-job"}
    assert called == [(4, 4)]


# =============================================================================================
# A HEIC decoded through fakes of read_heif, ffprobe and ffmpeg
# =============================================================================================


def planes(pixel_format: str, width: int, height: int, y: int = 128, cb: int = 128, cr: int = 128) -> bytes:
    """One frame's raw planes in `pixel_format`, each plane one value."""
    bits, subsampling = decode.TILE_FORMATS[pixel_format]
    across, down = colour.SUBSAMPLING[subsampling]
    chroma = math.ceil(width / across) * math.ceil(height / down)
    return np.array([y] * (width * height) + [cb] * chroma + [cr] * chroma, np.uint8 if bits == 8 else "<u2").tobytes()


def video(index: int, size: tuple[int, int] = (2, 2), default: int | None = None, pix_fmt: str = "yuv444p", **tags) -> dict:
    stream = {"index": index, "codec_type": "video", "width": size[0], "height": size[1], "pix_fmt": pix_fmt, **tags}
    if default is not None:
        stream["disposition"] = {"default": default}
    return stream


class Fakes:
    """read_heif, ffprobe and ffmpeg answered for `_decode_heif`: the HEIF boxes give a 2 x 2 still with `colour` and
    `exif`; ffprobe prints `report` and ends with `returncode`; each frame ffmpeg is asked for is `frame`, or planes of
    the stored size in the asked format. `asked` holds each frame's (pixel format, stream)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, report: dict, *, colour_boxes: dict | None = None,
                 exif: bytes | None = None, frame: bytes | None = None, returncode: int = 0,
                 has_groups: bool = False, help_returncode: int = 0) -> None:
        self.asked: list[tuple[str, int | None]] = []
        self.path = tmp_path / "boxes.heic"
        self.path.write_bytes(b"read by the fake")
        monkeypatch.setattr(decode, "read_heif", lambda data: HeifInfo(2, 2, exif, None, colour_boxes))
        help_bytes = b"-show_stream_groups\n" if has_groups else b"Hyper fast Audio and Video encoder\n"

        def run(command: list[str]) -> subprocess.CompletedProcess:
            if "-h" in command:
                return subprocess.CompletedProcess(command, help_returncode, help_bytes, b"")
            return subprocess.CompletedProcess(command, returncode, json.dumps(report).encode(), b"")

        monkeypatch.setattr(decode, "_run", run)

        def raw(path: Path, pixel_format: str, stream: int | None = None) -> bytes:
            self.asked.append((pixel_format, stream))
            return frame if frame is not None else planes(pixel_format, 2, 2)

        monkeypatch.setattr(decode, "_raw_frame", raw)

    def decode(self) -> decode.DecodedStill:
        return decode._decode_heif(self.path)


# (what the streams are, ffprobe's video and other streams, the stream decoded, or None when refused)
SINGLE_CASES = [
    ("one video stream of the stored size", [video(0)], 0),
    ("a thumbnail beside it", [video(0, size=(1, 1)), video(1)], 1),
    ("a stream of another kind of the stored size, marked default", [video(0, default=0), {**video(1, default=1), "codec_type": "data"}], 0),
    ("two of the stored size, the default one", [video(0, default=0), video(1, default=1)], 1),
    ("two of the stored size, one without a disposition", [video(0), video(1, default=1)], 1),
    ("two of the stored size, neither default", [video(0, default=0), video(1, default=0)], None),
    ("two of the stored size, both default", [video(0, default=1), video(1, default=1)], None),
    ("none of the stored size", [video(0, size=(1, 1))], None),
]


@pytest.mark.parametrize("streams,chosen", [case[1:] for case in SINGLE_CASES], ids=[case[0] for case in SINGLE_CASES])
def test_a_heic_without_a_grid_is_decoded_from_its_one_stream_of_the_stored_size(monkeypatch, tmp_path, streams, chosen) -> None:
    fakes = Fakes(monkeypatch, tmp_path, {"stream_groups": [], "streams": streams})
    if chosen is None:
        assert refusal_of(fakes.decode) == decode.UNDECODABLE
        assert fakes.asked == []
    else:
        fakes.decode()
        assert fakes.asked == [("yuv444p", chosen)]


def test_a_heic_stream_in_a_pixel_format_no_tile_may_have_is_refused(monkeypatch, tmp_path) -> None:
    fakes = Fakes(monkeypatch, tmp_path, {"stream_groups": [], "streams": [video(0, pix_fmt="rgb24")]})
    assert refusal_of(fakes.decode) == decode.UNDECODABLE


def test_a_failing_heic_probe_names_whether_ffprobe_lacks_grids_or_the_file_is_broken(monkeypatch, tmp_path) -> None:
    without_groups = Fakes(monkeypatch, tmp_path, {"stream_groups": [], "streams": [video(0)]}, returncode=1, has_groups=False)
    assert refusal_of(without_groups.decode) == decode.TILED_HEIC
    with_groups = Fakes(monkeypatch, tmp_path, {"stream_groups": [], "streams": [video(0)]}, returncode=1, has_groups=True)
    assert refusal_of(with_groups.decode) == decode.UNDECODABLE
    help_failed = Fakes(monkeypatch, tmp_path, {"stream_groups": [], "streams": [video(0)]}, returncode=1, help_returncode=1)
    assert refusal_of(help_failed.decode) == "The restoration stopped on an internal error."
    missing_key = Fakes(monkeypatch, tmp_path, {"streams": [video(0)]})
    assert refusal_of(missing_key.decode) == decode.TILED_HEIC


def test_a_heics_malformed_exif_block_leaves_the_still_readable_with_assumed_srgb(monkeypatch, tmp_path) -> None:
    for broken in (b"bad", b"II*\x00\xff\xff\xff\xff"):
        still = Fakes(monkeypatch, tmp_path, {"stream_groups": [], "streams": [video(0)]}, exif=broken).decode()
        assert {p["name"]: (p["value"], p["basis"]) for p in still.parameters}["transfer"] == ("srgb", decode.ASSUMED_IN_DESIGN)


def exif_colour_space_1() -> bytes:
    exif = Image.Exif()
    exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.ColorSpace] = 1
    block = exif.tobytes()
    return block[6:] if block.startswith(b"Exif\x00\x00") else block


def test_a_heics_exif_colour_space_gives_its_transfer_and_without_exif_the_design_assumes_it(monkeypatch, tmp_path) -> None:
    report = {"stream_groups": [], "streams": [video(0)]}
    with_exif = Fakes(monkeypatch, tmp_path, report, exif=exif_colour_space_1()).decode()
    assert {p["name"]: (p["value"], p["basis"]) for p in with_exif.parameters}["transfer"] == ("srgb", decode.MEASURED_FROM_EXIF)
    without = Fakes(monkeypatch, tmp_path, report).decode()
    assert {p["name"]: (p["value"], p["basis"]) for p in without.parameters}["transfer"] == ("srgb", decode.ASSUMED_IN_DESIGN)


def test_a_heic_at_444_has_no_chroma_siting_and_one_at_420_has_one(monkeypatch, tmp_path) -> None:
    full = Fakes(monkeypatch, tmp_path, {"stream_groups": [], "streams": [video(0)]}).decode()
    assert "chromaSiting" not in full.source_colour
    halved = Fakes(monkeypatch, tmp_path, {"stream_groups": [], "streams": [video(0, pix_fmt="yuv420p", chroma_location="center")]}).decode()
    assert halved.source_colour["chromaSiting"] == "center"


def test_a_heic_colour_past_the_rgb_cube_is_held_at_its_face_not_wrapped(monkeypatch, tmp_path) -> None:
    # Full range Y' 255 with Cr 255 asks for R' above 1 (BT.601: R' = Y' + 1.402 (Cr - 0.5)); it is held at 255
    nclx = {"primaries": 1, "transfer": 13, "matrix": 6, "fullRange": True}
    fakes = Fakes(monkeypatch, tmp_path, {"stream_groups": [], "streams": [video(0)]}, colour_boxes={"nclx": nclx},
                  frame=planes("yuv444p", 2, 2, y=255, cb=128, cr=255))
    still = fakes.decode()
    assert still.samples[..., 0].tolist() == [[255, 255], [255, 255]]


# =============================================================================================
# A HEIC's colour fields from its tile stream's own tags, when no nclx gives them
# =============================================================================================


def fields_of(stream: dict, subsampling: str = "420", boxes: dict | None = None) -> dict[str, tuple[str, str]] | str:
    """`_heif_fields` for a stream's tags: each field's (value, basis), or the refusal's sentence."""
    try:
        fields, _ = decode._heif_fields(SimpleNamespace(colour=boxes), stream, subsampling, None)
    except decode.DecodeRefusal as refusal:
        return refusal.sentence
    return {name: (field.value, field.basis) for name, field in fields.items()}


TAGS, DESIGN = decode.MEASURED_FROM_TAGS, decode.ASSUMED_IN_DESIGN
MATRIX_REFUSED = decode.unsupported("a colour matrix other than BT.601, BT.709 or BT.2020 non-constant luminance")


@pytest.mark.parametrize("tag,expected", [
    ("bt709", ("bt709", TAGS)), ("smpte170m", ("bt601", TAGS)), ("bt470bg", ("bt601", TAGS)), ("bt2020nc", ("bt2020nc", TAGS)),
    (None, ("bt601", DESIGN)), ("unknown", ("bt601", DESIGN)), ("", ("bt601", DESIGN)), ("unspecified", ("bt601", DESIGN)),
    ("fcc", MATRIX_REFUSED),
])
def test_a_heics_matrix_comes_from_its_streams_tag_when_no_nclx_gives_one(tag: str | None, expected) -> None:
    found = fields_of({"color_space": tag})
    assert (found if isinstance(found, str) else found["matrix"]) == expected


@pytest.mark.parametrize("tag,expected", [
    ("pc", ("full", TAGS)), ("tv", ("limited", TAGS)), (None, ("full", DESIGN)), ("unknown", ("full", DESIGN)),
    ("jpeg", decode.unsupported("a colour range tag other than limited or full")),
])
def test_a_heics_range_comes_from_its_streams_tag_when_no_nclx_gives_one(tag: str | None, expected) -> None:
    found = fields_of({"color_range": tag})
    assert (found if isinstance(found, str) else found["range"]) == expected


@pytest.mark.parametrize("subsampling,tag,expected", [
    ("420", "center", ("center", TAGS)), ("422", "topleft", ("topleft", TAGS)), ("420", "left", ("left", TAGS)),
    ("420", None, ("left", DESIGN)), ("420", "unspecified", ("left", DESIGN)),
    ("420", "bottom", decode.unsupported("a chroma siting this pipeline cannot tag")),
    ("444", "center", None), ("444", "bottom", None),
])
def test_a_heics_chroma_siting_is_read_only_where_its_chroma_is_halved(subsampling: str, tag: str | None, expected) -> None:
    found = fields_of({"chroma_location": tag}, subsampling)
    assert (found if isinstance(found, str) else found.get("chromaSiting")) == expected


def test_an_nclx_decides_range_and_matrix_over_the_streams_tags_and_an_unspecified_matrix_leaves_them_to_decide() -> None:
    stream = {"color_space": "bt709", "color_range": "tv"}
    decided = fields_of(stream, boxes={"nclx": {"primaries": 1, "transfer": 1, "matrix": 9, "fullRange": True}})
    assert (decided["matrix"], decided["range"]) == (("bt2020nc", TAGS), ("full", TAGS))
    left = fields_of(stream, boxes={"nclx": {"primaries": 1, "transfer": 1, "matrix": decode.UNSPECIFIED, "fullRange": False}})
    assert (left["matrix"], left["range"]) == (("bt709", TAGS), ("limited", TAGS))


def test_an_icc_profile_decides_transfer_and_primaries_over_an_nclx_beside_it() -> None:
    profile = bytes(128) + (1).to_bytes(4, "big") + b"kTRC" + (144).to_bytes(4, "big") + (14).to_bytes(4, "big") + b"curv" + bytes(4) + (1).to_bytes(4, "big") + (256).to_bytes(2, "big")
    found = fields_of({}, boxes={"icc": profile, "nclx": {"primaries": 9, "transfer": 16, "matrix": 9, "fullRange": True}})
    assert (found["transfer"], found["primaries"]) == (("icc_table", decode.MEASURED_FROM_ICC), ("icc", decode.MEASURED_FROM_ICC))
    assert found["matrix"] == ("bt2020nc", TAGS)


def test_every_nclx_transfer_of_the_bt709_family_is_read_as_bt709() -> None:
    # H.273 Table 3: 1 (BT.709), 6 (BT.601), 14 and 15 (BT.2020 at 10 and 12 bits) share one curve
    for code in (1, 6, 14, 15):
        found = fields_of({}, boxes={"nclx": {"primaries": 1, "transfer": code, "matrix": 1, "fullRange": True}})
        assert found["transfer"] == ("bt709", TAGS), code
    assert decode.NCLX_TRANSFERS == {1: "bt709", 6: "bt709", 14: "bt709", 15: "bt709", 13: "srgb", 16: "pq", 18: "hlg"}


def test_each_tile_format_carries_the_depth_and_subsampling_its_name_says() -> None:
    for name, (bits, subsampling) in decode.TILE_FORMATS.items():
        assert bits == (10 if name.endswith("p10le") else 8), name
        assert subsampling == name.removeprefix("yuvj").removeprefix("yuv")[:3], name
    assert len(decode.TILE_FORMATS) == 9


# =============================================================================================
# A still's stored depth, and the 16 bit path's formats and refusals
# =============================================================================================


def test_the_stored_depth_is_the_headers_own(tmp_path: Path) -> None:
    short = tmp_path / "short.png"
    short.write_bytes(bytes(24))
    # A PNG cut before IHDR's depth byte has no depth; TIFF's BitsPerSample is 1 when absent (TIFF 6.0), the deepest
    # sample's when several are given, and may be one number; a JPEG is 8 bits
    assert decode._stored_depth(short, None, "png") == 0
    assert decode._stored_depth(short, SimpleNamespace(tag_v2={}), "tiff") == 1
    assert decode._stored_depth(short, SimpleNamespace(tag_v2={decode.TIFF_BITS_PER_SAMPLE: (8, 16, 8)}), "tiff") == 16
    assert decode._stored_depth(short, SimpleNamespace(tag_v2={decode.TIFF_BITS_PER_SAMPLE: 16}), "tiff") == 16
    assert decode._stored_depth(short, SimpleNamespace(tag_v2={decode.TIFF_BITS_PER_SAMPLE: ()}), "tiff") == 0
    assert decode._stored_depth(short, None, "jpeg") == 8


class SixteenBit:
    """`_sixteen_bit` with ffprobe's streams given and each frame ffmpeg is asked for of the right size (or `frame`)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, streams: list[dict], frame: bytes | None = None) -> None:
        self.asked: list[str] = []
        monkeypatch.setattr(decode, "_probe", lambda path, *sections: {"streams": streams})

        def raw(path: Path, pixel_format: str, stream: int | None = None) -> bytes:
            self.asked.append(pixel_format)
            channels = 3 if pixel_format == "rgb48le" else 1
            return frame if frame is not None else np.arange(2 * 2 * channels, dtype="<u2").tobytes()

        monkeypatch.setattr(decode, "_raw_frame", raw)

    def read(self) -> np.ndarray | str:
        try:
            return decode._sixteen_bit(Path("not read"), 2, 2)
        except decode.DecodeRefusal as refusal:
            return refusal.sentence


@pytest.mark.parametrize("stored,read", [("rgb48be", "rgb48le"), ("rgb48le", "rgb48le"), ("gray16be", "gray16le"), ("gray16le", "gray16le")])
def test_a_16_bit_still_is_read_in_its_own_layout(monkeypatch, stored: str, read: str) -> None:
    reader = SixteenBit(monkeypatch, [{"codec_type": "audio"}, {"codec_type": "video", "pix_fmt": stored}])
    samples = reader.read()
    assert reader.asked == [read]
    assert samples.shape == (2, 2, 3) and samples.dtype == np.uint16
    if read == "gray16le":
        assert samples[..., 0].tolist() == [[0, 1], [2, 3]] and (samples[..., 0] == samples[..., 2]).all()
    else:
        assert samples.reshape(-1).tolist() == list(range(12))


@pytest.mark.parametrize("streams,frame,sentence", [
    ([{"codec_type": "video", "pix_fmt": "rgba64le"}], None, decode.unsupported("an image with an alpha plane")),
    ([{"codec_type": "video", "pix_fmt": "ya16be"}], None, decode.unsupported("an image with an alpha plane")),
    ([{"codec_type": "video", "pix_fmt": "bayer_rggb16le"}], None, decode.unsupported("a camera raw layout")),
    ([{"codec_type": "video", "pix_fmt": "yuv444p16le"}], None, decode.UNDECODABLE),
    ([{"codec_type": "video", "pix_fmt": "rgb48le"}, {"codec_type": "video", "pix_fmt": "rgb48le"}], None, decode.UNDECODABLE),
    ([], None, decode.UNDECODABLE),
    ([{"codec_type": "video", "pix_fmt": "rgb48le"}], bytes(23), decode.UNDECODABLE),
    ([{"codec_type": "video", "pix_fmt": "rgb48le"}], bytes(25), decode.UNDECODABLE),
], ids=["rgba64le", "ya16be", "bayer", "another format", "two streams", "none", "one byte short", "one byte more"])
def test_a_16_bit_still_the_path_cannot_read_is_refused_before_its_samples_are_kept(monkeypatch, streams, frame, sentence) -> None:
    assert SixteenBit(monkeypatch, streams, frame).read() == sentence


def test_a_16_bit_reading_of_the_wrong_shape_is_refused(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "deep.png"
    Image.new("RGB", (3, 2)).save(path)
    monkeypatch.setattr(decode, "_stored_depth", lambda path, image, family: 16)
    monkeypatch.setattr(decode, "_sixteen_bit", lambda path, width, height: np.zeros((3, 2, 3), np.uint16))
    assert refusal_of(decode.decode_still, path) == decode.UNDECODABLE


@pytest.mark.parametrize("depth", [17, 24, 32])
def test_a_header_depth_past_16_is_refused_whatever_mode_pillow_reports(monkeypatch, tmp_path: Path, depth: int) -> None:
    path = tmp_path / "rgb.png"
    Image.new("RGB", (2, 2)).save(path)
    monkeypatch.setattr(decode, "_stored_depth", lambda path, image, family: depth)
    assert refusal_of(decode.decode_still, path) == decode.unsupported("an image of more than 16 bits")


@pytest.mark.parametrize("mode", ["I", "F"])
def test_a_deep_mode_is_refused_by_its_mode_whatever_depth_the_header_gives(monkeypatch, tmp_path: Path, mode: str) -> None:
    path = tmp_path / "deep.tiff"
    Image.new(mode, (2, 2)).save(path)
    monkeypatch.setattr(decode, "_stored_depth", lambda path, image, family: 8)
    assert refusal_of(decode.decode_still, path) == decode.unsupported("an image of more than 16 bits")


# =============================================================================================
# A tool that fails, EXIF's ColorSpace, and a tile's 10 bit planes
# =============================================================================================


@pytest.mark.parametrize("returncode,stdout,expected", [
    (1, b'{"streams": []}', decode.UNDECODABLE), (0, b"[1, 2]", {}), (0, b'{"streams": []}', {"streams": []}),
])
def test_ffprobes_report_is_its_json_object_and_a_failing_ffprobe_is_refused(monkeypatch, returncode, stdout, expected) -> None:
    monkeypatch.setattr(decode, "_run", lambda command: subprocess.CompletedProcess(command, returncode, stdout, b""))
    try:
        found = decode._probe(Path("not read"), "-show_streams")
    except decode.DecodeRefusal as refusal:
        found = refusal.sentence
    assert found == expected


@pytest.mark.parametrize("returncode,stdout,expected", [(1, b"frame", decode.UNDECODABLE), (0, b"", decode.UNDECODABLE), (0, b"frame", b"frame")])
def test_a_frame_is_ffmpegs_output_only_when_it_succeeds_with_some(monkeypatch, returncode, stdout, expected) -> None:
    monkeypatch.setattr(decode, "_run", lambda command: subprocess.CompletedProcess(command, returncode, stdout, b""))
    try:
        found = decode._raw_frame(Path("not read"), "rgb24")
    except decode.DecodeRefusal as refusal:
        found = refusal.sentence
    assert found == expected


def test_exifs_colour_space_is_read_only_as_a_number() -> None:
    assert decode._exif_colour_space(SimpleNamespace(get_ifd=lambda tag: {decode.EXIF_COLOR_SPACE: 1})) == 1
    assert decode._exif_colour_space(SimpleNamespace(get_ifd=lambda tag: {decode.EXIF_COLOR_SPACE: "1"})) is None
    assert decode._exif_colour_space(SimpleNamespace(get_ifd=lambda tag: {})) is None


def test_a_10_bit_tiles_planes_are_read_two_bytes_a_sample_and_to_the_byte() -> None:
    raw = np.array([1023, 0, 512, 64] + [100] + [900], "<u2").tobytes()
    luma, cb, cr = decode._planes(raw, 2, 2, 10, "420")
    assert (luma.tolist(), cb.tolist(), cr.tolist()) == ([[1023, 0], [512, 64]], [[100]], [[900]])
    assert refusal_of(decode._planes, raw[:-1], 2, 2, 10, "420") == decode.UNDECODABLE
    assert refusal_of(decode._planes, raw + b"\x00", 2, 2, 10, "420") == decode.UNDECODABLE


def test_a_grid_without_its_offsets_is_cropped_from_its_corner_and_one_without_its_coded_size_is_refused() -> None:
    streams = {0: {"pix_fmt": "yuv444p", "width": 2, "height": 2}}
    tile = {"stream_index": 0, "tile_horizontal_offset": 0, "tile_vertical_offset": 0}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(decode, "_raw_frame", lambda path, pixel_format, stream=None: planes("yuv444p", 2, 2, y=7))
        luma, _, _ = decode._place_tiles(Path("not read"), {"coded_width": 2, "coded_height": 2, "subcomponents": [tile]}, streams, 2, 2, "yuv444p")
        assert luma.tolist() == [[7, 7], [7, 7]]
        assert refusal_of(decode._place_tiles, Path("not read"), {"subcomponents": [tile]}, streams, 1, 1, "yuv444p") == decode.UNDECODABLE
        assert refusal_of(decode._place_tiles, Path("not read"), {"coded_height": 2, "subcomponents": [tile]}, streams, 1, 2, "yuv444p") == decode.UNDECODABLE
        assert refusal_of(decode._place_tiles, Path("not read"), {"coded_width": 2, "subcomponents": [tile]}, streams, 2, 1, "yuv444p") == decode.UNDECODABLE
        # Missing stream index or mixed tile pixel format in _place_tiles
        assert refusal_of(decode._place_tiles, Path("not read"), {"coded_width": 2, "coded_height": 2, "subcomponents": [tile]}, {}, 2, 2, "yuv444p") == decode.UNDECODABLE
        assert refusal_of(decode._place_tiles, Path("not read"), {"coded_width": 2, "coded_height": 2, "subcomponents": [tile]},
                          {0: {"pix_fmt": "yuv420p", "width": 2, "height": 2}}, 2, 2, "yuv444p") == decode.UNDECODABLE
        # A 3 x 4 yuv420p tile covers 2 x 2 chroma on a 4 x 4 canvas while leaving luma column 3 uncovered
        patch.setattr(decode, "_raw_frame", lambda path, pixel_format, stream=None: planes("yuv420p", 3, 4, y=7))
        assert refusal_of(decode._place_tiles, Path("not read"), {"coded_width": 4, "coded_height": 4, "subcomponents": [tile]},
                          {0: {"pix_fmt": "yuv420p", "width": 3, "height": 4}}, 4, 4, "yuv420p") == decode.UNDECODABLE


def test_heic_grid_first_tile_and_read_png_ihdr_width_and_height_full_four_bytes(monkeypatch, tmp_path: Path) -> None:
    # A grid whose subcomponents lack stream_index (even when streams 0 and -2 exist) or whose first tile differs from the second
    empty_sub = {"stream_groups": [{"type": "Tile Grid", "components": [{"width": 2, "height": 2, "coded_width": 2, "coded_height": 2, "subcomponents": [{}]}]}],
                 "streams": [video(0), video(-2)]}
    assert refusal_of(Fakes(monkeypatch, tmp_path, empty_sub).decode) == decode.UNDECODABLE
    mixed_tiles = {
        "stream_groups": [{"type": "Tile Grid", "components": [{"width": 4, "height": 2, "coded_width": 4, "coded_height": 2,
            "subcomponents": [{"stream_index": 0, "tile_horizontal_offset": 0, "tile_vertical_offset": 0},
                              {"stream_index": 1, "tile_horizontal_offset": 2, "tile_vertical_offset": 0}]}]}],
        "streams": [video(0, pix_fmt="yuv420p"), video(1, pix_fmt="yuv444p")],
    }
    assert refusal_of(Fakes(monkeypatch, tmp_path, mixed_tiles).decode) == decode.UNDECODABLE

    # read_png reads all four big-endian bytes of width (16..20) and height (20..24) in IHDR
    seen: list[tuple[int, int]] = []
    monkeypatch.setattr(decode, "_sixteen_bit", lambda path, w, h: seen.append((w, h)) or np.zeros((1, 1, 3), np.uint16))
    ihdr_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (0x01020304).to_bytes(4, "big") + (0x05060708).to_bytes(4, "big") + bytes([16])
    decode.read_png(ihdr_data)
    assert seen == [(0x01020304, 0x05060708)]


def test_a_still_is_sniffed_from_the_bytes_the_store_sniffed_it_from(monkeypatch, tmp_path: Path) -> None:
    # A HEIF whose ftyp names heic only in its last compatible brand, at bytes 60 to 64: the store's sniff of its
    # first SNIFF_BYTES calls it a HEIC, so the decoder's must too
    head = (64).to_bytes(4, "big") + b"ftyp" + b"mif1" + bytes(4) + b"mif1" * 11 + b"heic"
    assert len(head) == 64 and store_sniff(head).family == "heif"
    path = tmp_path / "late-brand.heic"
    path.write_bytes(head + bytes(100))
    monkeypatch.setattr(decode, "_decode_heif", lambda path: "read as a HEIC")
    assert decode.decode_still(path) == "read as a HEIC"


def test_a_16_bit_stills_linear_light_is_its_codes_over_65535_through_its_transfer() -> None:
    samples = np.array([[[0, 32768, 65535]]], np.uint16)
    still = decode.DecodedStill(samples, {"transfer": "srgb"}, {"transfer": "srgb"}, [], None, False)
    expected = colour.decode_transfer("srgb", samples.astype(np.float64) / 65535.0)
    assert np.abs(decode.linear_light(still).astype(np.float64) - expected).max() <= 2.0**-23
    assert decode.linear_light(still)[0, 0, 2] == pytest.approx(1.0, abs=2.0**-23)


def test_the_decoders_time_limit_is_the_solve_jobs_own() -> None:
    # Each ffprobe or ffmpeg call on a still may take as long as the solve job lets its own decode take
    assert decode.DECODE_TIMEOUT_SEC == ingest_jobs.DECODE_TIMEOUT_SEC == 60


def test_each_tool_failure_names_its_true_cause_and_a_decompression_bomb_is_refused(monkeypatch, tmp_path: Path) -> None:
    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 60)

    monkeypatch.setattr(subprocess, "run", timed_out)
    assert refusal_of(decode._run, ["ffprobe"]) == "The restoration stopped at its time limit."

    def missing(*args, **kwargs):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(subprocess, "run", missing)
    assert refusal_of(decode._run, ["ffprobe"]) == "The restoration stopped on an internal error."

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], -9, b"", b""))
    assert refusal_of(decode._run, ["ffprobe"]) == "The restoration stopped on an internal error."

    path = tmp_path / "bomb.png"
    Image.new("RGB", (2, 2)).save(path)
    data = path.read_bytes()

    def bomb(*args, **kwargs):
        raise Image.DecompressionBombError("too large")

    monkeypatch.setattr(Image, "open", bomb)
    assert refusal_of(decode.decode_still, path) == decode.UNDECODABLE
    assert refusal_of(decode.read_png, data) == decode.UNDECODABLE


# (pixel format, the coded canvas, a tile's size, the crop's corner, the stored size): square and wider than tall
# tiles, tiles clipped at the canvas's right and bottom edges, crops away from the corner, and each subsampling
TILINGS = [
    ("yuv420p", (8, 8), (4, 4), (2, 2), (6, 4)),
    ("yuv420p", (6, 4), (4, 2), (0, 0), (6, 4)),
    ("yuv420p", (10, 6), (6, 4), (2, 2), (8, 4)),
    ("yuv422p", (6, 3), (4, 2), (2, 1), (4, 2)),
    ("yuv444p10le", (5, 3), (2, 2), (1, 1), (4, 2)),
]


@pytest.mark.parametrize("pixel_format,coded,tile,corner,size", TILINGS, ids=[f"{t[0]} {t[1]} by {t[2]}" for t in TILINGS])
def test_tiles_placed_and_cropped_are_the_coded_canvass_own_crop(pixel_format: str, coded: tuple, tile: tuple, corner: tuple,
                                                                 size: tuple) -> None:
    bits, subsampling = decode.TILE_FORMATS[pixel_format]
    across, down = colour.SUBSAMPLING[subsampling]
    rng = np.random.default_rng(12)
    columns, rows = math.ceil(coded[0] / tile[0]), math.ceil(coded[1] / tile[1])
    width, height = columns * tile[0], rows * tile[1]
    luma = rng.integers(0, 2**bits, size=(height, width))
    cb, cr = (rng.integers(0, 2**bits, size=(height // down, width // across)) for _ in range(2))
    frames, subcomponents = {}, []
    for r in range(rows):
        for c in range(columns):
            x0, y0 = c * tile[0], r * tile[1]
            parts = [luma[y0:y0 + tile[1], x0:x0 + tile[0]]] + [
                plane[y0 // down:(y0 + tile[1]) // down, x0 // across:(x0 + tile[0]) // across] for plane in (cb, cr)]
            frames[len(frames)] = b"".join(part.astype(np.uint8 if bits == 8 else "<u2").tobytes() for part in parts)
            subcomponents.append({"stream_index": len(subcomponents), "tile_horizontal_offset": x0, "tile_vertical_offset": y0})
    grid = {"coded_width": coded[0], "coded_height": coded[1], "horizontal_offset": corner[0], "vertical_offset": corner[1],
            "subcomponents": subcomponents}
    streams = {i: {"pix_fmt": pixel_format, "width": tile[0], "height": tile[1]} for i in frames}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(decode, "_raw_frame", lambda path, fmt, stream=None: frames[stream])
        placed = decode._place_tiles(Path("not read"), grid, streams, size[0], size[1], pixel_format)
    (left, top), (w, h) = corner, size
    chroma = (slice(top // down, math.ceil((top + h) / down)), slice(left // across, math.ceil((left + w) / across)))
    expected = (luma[top:top + h, left:left + w], cb[chroma], cr[chroma])
    for name, got, want in zip(("luma", "cb", "cr"), placed, expected):
        assert got.tolist() == want.tolist(), name
