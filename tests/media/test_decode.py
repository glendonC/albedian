"""Decoding a stored still (`services/media/decode.py`; design section 1; REQ-RST-2.1, REQ-RST-3).

Covers:
- 8 bit JPEG, PNG and TIFF through Pillow; grey as three equal channels; a palette without transparency as RGB.
- 16 bit RGB and grey PNG and TIFF through ffmpeg, back to every generated sample.
- Refusals with the contract's fixed sentences: transparency, more than 16 bits, CMYK, a clip, a file of no known
  type.
- The transfer from an ICC profile's tone curves (parametric or table, RGB or grey), else EXIF ColorSpace 1, else
  sRGB assumed with the contract's reason; the records the decode writes are valid contract records.
- HEIC: the sips grey grids decode to the solve job's own grey placement; the sips colour fixtures come back within
  1 code of their bands and agree with ffmpeg's own conversion; a turned `irot` leaves the stored pixels; an nclx
  property decides what it specifies; a ffprobe that cannot report tile grids refuses every HEIC.
- Reading a written PNG back, and linear light by the described transfer.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import ExifTags, Image, TiffImagePlugin

from services.contracts.media import ColourDescription, GraphStep
from services.ingest import jobs as ingest_jobs
from services.media import colour, decode, png16

# Every test here starts ffmpeg or ffprobe: each has its own bound (child_bounds.py)
from child_bounds import bounded  # noqa: E402,F401

ROOT = Path(__file__).resolve().parents[2]
INGEST_FIXTURES = ROOT / "tests" / "fixtures" / "ingest"
MEDIA_FIXTURES = ROOT / "tests" / "fixtures" / "media"
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
BANDS = [(200, 30, 30), (30, 200, 30), (30, 30, 200), (220, 220, 40), (40, 220, 220), (128, 128, 128)]
# How far the colour fixtures' band middles come back from their colour [tested on ffmpeg 8.1.1: 1 code for the
# single image, 4 for the grid, whose own encoding is lossier: ffmpeg's own composition of it is 5 codes off]
HEIC_BAND_CODES = {"sips-colour-single-96x64.heic": 1, "sips-colour-grid-1100x700.heic": 4}
# Inside a band, at least this far from its edges, the chroma of the next band does not reach [proposed: the
# interpolation reaches one chroma sample, two luma samples, and HEVC's own filters a few more]
BAND_MARGIN_PX = 6
# Against ffmpeg's own composition and conversion of the same image to rgb24, inside the bands [tested on 8.1.1 arm64
# and CI's pinned n8.1.3 x86-64: 1 on arm64 and 2 on x86-64 on the single image, 3 on arm64 and 4 on x86-64 on the
# grid; its chroma interpolation and libswscale integer rounding are not this pipeline's]
FFMPEG_CONVERSION_CODES = {"sips-colour-single-96x64.heic": 2, "sips-colour-grid-1100x700.heic": 4}


def _grids_reported() -> bool:
    proc = subprocess.run(["ffprobe", "-hide_banner", "-h"], capture_output=True)
    return proc.returncode == 0 and b"-show_stream_groups" in proc.stdout


GRIDS = _grids_reported()


def decoded_heic(path: Path) -> decode.DecodedStill | None:
    """The decoded HEIC, or None after checking the refusal where this ffprobe cannot report tile grids (before 7.1,
    as on CI's distro ffmpeg), so each HEIC test checks whichever case holds here."""
    if not GRIDS:
        with pytest.raises(decode.DecodeRefusal) as refusal:
            decode.decode_still(path)
        assert refusal.value.sentence == decode.TILED_HEIC
        return None
    return decode.decode_still(path)


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def saved(image: Image.Image, fmt: str, **options) -> bytes:
    out = io.BytesIO()
    image.save(out, fmt, **options)
    return out.getvalue()


def ffmpeg_still(tmp_path: Path, samples: np.ndarray, pixel_format: str, name: str) -> Path:
    """A still written by ffmpeg from raw samples (ffmpeg writes 16 bit PNG and TIFF, which Pillow cannot)."""
    raw = tmp_path / f"{name}.raw"
    raw.write_bytes(samples.tobytes())
    out = tmp_path / name
    height, width = samples.shape[:2]
    subprocess.run([FFMPEG, "-v", "error", "-nostdin", "-f", "rawvideo", "-pix_fmt", pixel_format, "-s", f"{width}x{height}",
                    "-i", str(raw), "-frames:v", "1", str(out)], check=True, capture_output=True)
    return out


def icc_profile(curves: dict[bytes, bytes]) -> bytes:
    """A synthetic profile: a 128 byte header (its size, `acsp` at 36), the tag table, the tag bodies (ICC.1:2022
    clause 7.3), with nothing in it but the tone curves the decoder reads."""
    count = len(curves)
    offset = 128 + 4 + 12 * count
    table, bodies = b"", b""
    for signature, body in curves.items():
        table += signature + (offset + len(bodies)).to_bytes(4, "big") + len(body).to_bytes(4, "big")
        bodies += body + bytes(-len(body) % 4)
    size = offset + len(bodies)
    header = size.to_bytes(4, "big") + bytes(32) + b"acsp" + bytes(128 - 40)
    return header + count.to_bytes(4, "big") + table + bodies


def para(function: int, *values: float) -> bytes:
    return b"para" + bytes(4) + function.to_bytes(2, "big") + bytes(2) + b"".join(round(v * 65536).to_bytes(4, "big", signed=True) for v in values)


def curv(*entries: int) -> bytes:
    return b"curv" + bytes(4) + len(entries).to_bytes(4, "big") + b"".join(e.to_bytes(2, "big") for e in entries)


SRGB_PARA = para(3, 2.4, 1 / 1.055, 0.055 / 1.055, 1 / 12.92, 0.04045)


def assert_valid_records(still: decode.DecodedStill) -> None:
    ColourDescription.model_validate(still.source_colour)
    ColourDescription.model_validate(still.colour)
    GraphStep.model_validate({"kind": "decode", "version": "1.0.0", "parameters": still.parameters})
    assert still.colour["model"] == "rgb"


# =============================================================================================
# 8 bit stills through Pillow
# =============================================================================================


def test_an_8_bit_png_decodes_to_its_samples_with_srgb_and_unspecified_primaries_assumed(tmp_path: Path) -> None:
    rgb = np.random.default_rng(1).integers(0, 256, size=(5, 7, 3), dtype=np.uint8)
    still = decode.decode_still(write(tmp_path, "a.png", saved(Image.fromarray(rgb), "PNG")))
    assert still.samples.dtype == np.uint8 and np.array_equal(still.samples, rgb)
    assert {k: v for k, v in still.colour.items() if k != "assumptions"} == {
        "model": "rgb", "bitDepth": 8, "range": "full", "matrix": "identity", "transfer": "srgb",
        "primaries": "unspecified", "chromaSubsampling": "444",
    }
    assert [(a["field"], a["assumed"]) for a in still.colour["assumptions"]] == [("transfer", "srgb"), ("primaries", "unspecified")]
    origins = {p["name"]: (p["value"], p["origin"]) for p in still.parameters}
    assert origins["transfer"] == ("srgb", "default") and origins["pixelFormat"] == ("rgb24", "measured")
    assert still.icc_profile is None and not still.reencoded
    assert_valid_records(still)


def test_a_jpeg_with_exif_colour_space_1_is_srgb_measured_and_bt709_primaries(tmp_path: Path) -> None:
    exif = Image.Exif()
    exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.ColorSpace] = 1
    rgb = np.full((8, 8, 3), (90, 140, 200), dtype=np.uint8)
    data = saved(Image.fromarray(rgb), "JPEG", exif=exif, quality=95)
    still = decode.decode_still(write(tmp_path, "a.jpg", data))
    with Image.open(io.BytesIO(data)) as image:
        assert np.array_equal(still.samples, np.asarray(image.convert("RGB")))
    assert (still.colour["transfer"], still.colour["primaries"], still.colour["assumptions"]) == ("srgb", "bt709", [])
    bases = {p["name"]: p["basis"] for p in still.parameters}
    assert bases["transfer"] == bases["primaries"] == "Read from the file's EXIF ColorSpace."
    assert_valid_records(still)


@pytest.mark.parametrize("fmt,name", [("PNG", "g.png"), ("TIFF", "g.tif"), ("JPEG", "g.jpg")])
def test_a_grey_still_is_three_equal_channels(tmp_path: Path, fmt: str, name: str) -> None:
    grey = np.random.default_rng(2).integers(0, 256, size=(6, 9), dtype=np.uint8)
    data = saved(Image.fromarray(grey, "L"), fmt)
    still = decode.decode_still(write(tmp_path, name, data))
    with Image.open(io.BytesIO(data)) as image:
        stored = np.asarray(image)
    assert np.array_equal(still.samples, np.repeat(stored[..., None], 3, axis=2))


def test_a_palette_png_without_transparency_is_its_palettes_colours(tmp_path: Path) -> None:
    palette = Image.new("P", (4, 3))
    palette.putpalette([10, 20, 30, 200, 100, 50] + [0] * 762)
    palette.putdata([0, 1, 1, 0] * 3)
    still = decode.decode_still(write(tmp_path, "p.png", saved(palette, "PNG")))
    assert still.samples[0, :, :].tolist() == [[10, 20, 30], [200, 100, 50], [200, 100, 50], [10, 20, 30]]


def test_an_8_bit_tiff_decodes_to_its_samples(tmp_path: Path) -> None:
    rgb = np.random.default_rng(3).integers(0, 256, size=(4, 6, 3), dtype=np.uint8)
    still = decode.decode_still(write(tmp_path, "a.tif", saved(Image.fromarray(rgb), "TIFF")))
    assert np.array_equal(still.samples, rgb)


# =============================================================================================
# 16 bit stills through ffmpeg
# =============================================================================================


def test_a_16_bit_png_comes_back_to_every_sample(tmp_path: Path) -> None:
    rgb = np.random.default_rng(4).integers(0, 65536, size=(5, 6, 3), dtype=np.uint16)
    still = decode.decode_still(write(tmp_path, "a.png", png16.write_png16(rgb)))
    assert still.samples.dtype == np.uint16 and np.array_equal(still.samples, rgb)
    assert still.colour["bitDepth"] == 16
    assert {p["name"]: p["value"] for p in still.parameters}["pixelFormat"] == "rgb48le"
    assert_valid_records(still)


def test_a_16_bit_tiff_written_by_ffmpeg_comes_back_to_every_sample(tmp_path: Path) -> None:
    rgb = np.random.default_rng(5).integers(0, 65536, size=(4, 7, 3), dtype=np.uint16)
    still = decode.decode_still(ffmpeg_still(tmp_path, rgb.astype("<u2"), "rgb48le", "a.tif"))
    assert np.array_equal(still.samples, rgb)


def test_a_16_bit_grey_png_is_three_equal_channels(tmp_path: Path) -> None:
    grey = np.random.default_rng(6).integers(0, 65536, size=(5, 4), dtype=np.uint16)
    still = decode.decode_still(ffmpeg_still(tmp_path, grey.astype("<u2"), "gray16le", "g.png"))
    assert np.array_equal(still.samples, np.repeat(grey[..., None], 3, axis=2))


# =============================================================================================
# Refusals
# =============================================================================================

ALPHA = "This kind of file is not supported yet: an image with an alpha plane."
DEEP = "This kind of file is not supported yet: an image of more than 16 bits."
RAW = "This kind of file is not supported yet: a camera raw layout."


def raw_tiff(tag: int, value, offset: int = 8) -> bytes:
    tags = TiffImagePlugin.ImageFileDirectory_v2()
    tags[tag] = value
    if offset != 8:
        header = b"II*\x00" + offset.to_bytes(4, "little")
        return header + bytes(offset - 8) + tags.tobytes(offset)
    return saved(Image.new("RGB", (3, 3), (1, 2, 3)), "TIFF", tiffinfo=tags)


def transparent_palette() -> Image.Image:
    image = Image.new("P", (3, 3))
    image.putpalette([0, 0, 0, 255, 255, 255] + [0] * 762)
    image.info["transparency"] = 0
    return image


@pytest.mark.parametrize(
    "make,name,sentence",
    [
        (lambda tmp: write(tmp, "a.png", saved(Image.new("RGBA", (3, 3), (1, 2, 3, 4)), "PNG")), "rgba png", ALPHA),
        (lambda tmp: write(tmp, "a.png", saved(Image.new("LA", (3, 3), (1, 2)), "PNG")), "grey and alpha png", ALPHA),
        (lambda tmp: write(tmp, "a.png", saved(transparent_palette(), "PNG", transparency=0)), "palette with transparency", ALPHA),
        (lambda tmp: ffmpeg_still(tmp, np.zeros((2, 2, 4), "<u2"), "rgba64le", "a.png"), "16 bit rgba png", ALPHA),
        (lambda tmp: write(tmp, "a.tif", saved(Image.fromarray(np.zeros((2, 2), np.float32), "F"), "TIFF")), "float tiff", DEEP),
        (lambda tmp: write(tmp, "a.tif", raw_tiff(330, (0,), offset=16)), "subifds tiff", RAW),
        (lambda tmp: write(tmp, "a.tif", raw_tiff(50706, b"\x01\x04\x00\x00")), "dng tiff", RAW),
        (lambda tmp: write(tmp, "a.tif", raw_tiff(254, 1)), "reduced subfile tiff", RAW),
        (lambda tmp: write(tmp, "a.tif", raw_tiff(262, 32803)), "cfa photometric tiff", RAW),
        (lambda tmp: write(tmp, "a.tif", raw_tiff(262, 34892)), "linear raw photometric tiff", RAW),
        (lambda tmp: write(tmp, "a.jpg", saved(Image.new("CMYK", (3, 3), (1, 2, 3, 4)), "JPEG")), "cmyk jpeg", decode.UNDECODABLE),
        (lambda tmp: write(tmp, "a.mp4", b"\x00\x00\x00\x18ftypmp42" + bytes(16)), "clip", "A display copy is not made for a clip."),
        (lambda tmp: write(tmp, "a.bin", b"not a picture at all"), "unknown bytes", decode.UNDECODABLE),
        (lambda tmp: write(tmp, "a.png", b"\x89PNG\r\n\x1a\n" + bytes(40)), "broken png", decode.UNDECODABLE),
        (lambda tmp: write(tmp, "a.tif", b"II*\x00"), "short tiff", decode.UNDECODABLE),
    ],
    ids=lambda value: value if isinstance(value, str) and " " in value else None,
)
def test_what_this_pipeline_does_not_decode_is_refused_with_the_contracts_sentence(tmp_path: Path, make, name: str, sentence: str) -> None:
    with pytest.raises(decode.DecodeRefusal) as refusal:
        decode.decode_still(make(tmp_path))
    assert refusal.value.sentence == sentence
    if name == "reduced subfile tiff":
        page_tiff = write(tmp_path, "page.tif", raw_tiff(254, 2))
        assert decode.decode_still(page_tiff).samples.shape == (3, 3, 3)


# =============================================================================================
# The transfer from an ICC profile
# =============================================================================================


@pytest.mark.parametrize(
    "curves,transfer",
    [
        ({b"rTRC": SRGB_PARA, b"gTRC": SRGB_PARA, b"bTRC": SRGB_PARA}, "icc_parametric"),
        ({b"rTRC": curv(), b"gTRC": curv(563), b"bTRC": curv(0, 30000, 65535)}, "icc_table"),
        ({b"kTRC": para(0, 2.2)}, "icc_parametric"),
    ],
    ids=["parametric", "tables", "grey kTRC"],
)
def test_a_profiles_tone_curves_give_the_transfer_and_its_bytes_are_kept(tmp_path: Path, curves: dict, transfer: str) -> None:
    profile = icc_profile(curves)
    rgb = np.random.default_rng(7).integers(0, 256, size=(3, 4, 3), dtype=np.uint8)
    still = decode.decode_still(write(tmp_path, "a.png", saved(Image.fromarray(rgb), "PNG", icc_profile=profile)))
    assert still.icc_profile == profile
    assert (still.colour["transfer"], still.colour["primaries"]) == (transfer, "icc")
    assert still.colour["iccProfileSha256"] == hashlib.sha256(profile).hexdigest()
    assert still.colour["assumptions"] == []
    assert_valid_records(still)
    # Linear light is each channel through its own curve
    kind, read = colour.icc_tone_curves(profile)
    expected = np.stack([read[c].decode(rgb[..., c] / 255) for c in range(3)], axis=-1).astype(np.float32)
    light = decode.linear_light(still)
    # The two sides reach the curves through arrays of another layout, so a math library may round their last
    # float64 bit apart; that moves the float32 result at most one step, 2**-23 at 1 (LANE item 7)
    assert light.dtype == np.float32 and light.shape == expected.shape
    assert np.abs(light.astype(np.float64) - expected).max() <= 2.0**-23


@pytest.mark.parametrize(
    "curves",
    [
        {b"rTRC": SRGB_PARA, b"gTRC": curv(256), b"bTRC": SRGB_PARA},
        {b"desc": b"desc" + bytes(8)},
        {b"rTRC": para(9, 1.0), b"gTRC": SRGB_PARA, b"bTRC": SRGB_PARA},
        {b"rTRC": b"XYZ " + bytes(16), b"gTRC": SRGB_PARA, b"bTRC": SRGB_PARA},
    ],
    ids=["mixed kinds", "no tone curves", "unknown function type", "not a curve"],
)
def test_a_profile_whose_curves_cannot_be_read_is_refused(tmp_path: Path, curves: dict) -> None:
    data = saved(Image.new("RGB", (2, 2)), "PNG", icc_profile=icc_profile(curves))
    with pytest.raises(decode.DecodeRefusal) as refusal:
        decode.decode_still(write(tmp_path, "a.png", data))
    assert refusal.value.sentence == decode.UNDECODABLE


def test_a_cut_profile_is_refused_by_the_reader() -> None:
    whole = icc_profile({b"kTRC": para(0, 2.2)})
    for cut in (whole[:100], whole[:140], whole[:-6]):
        with pytest.raises(colour.IccError):
            colour.icc_tone_curves(cut)
    # A table curve cut short, and a parametric curve without all its parameters
    with pytest.raises(colour.IccError):
        colour.icc_tone_curves(icc_profile({b"kTRC": curv(1, 2, 3)[:-2]}))
    with pytest.raises(colour.IccError):
        colour.icc_tone_curves(icc_profile({b"kTRC": para(3, 2.4, 1.0)}))


def test_linear_light_of_an_assumed_srgb_still_is_the_srgb_decode(tmp_path: Path) -> None:
    rgb = np.arange(256, dtype=np.uint8).reshape(16, 16, 1).repeat(3, axis=2)
    still = decode.decode_still(write(tmp_path, "a.png", saved(Image.fromarray(rgb), "PNG")))
    light = decode.linear_light(still)
    assert light.dtype == np.float32
    assert np.array_equal(light, colour.decode_transfer("srgb", rgb / 255).astype(np.float32))


# =============================================================================================
# HEIC
# =============================================================================================

GREY_FIXTURES = ["sips-grid-1024x768.heic", "sips-grid-1100x700.heic", "sips-grid-4032x3024.heic", "sips-single-480x360.heic"]


@pytest.mark.parametrize("name", GREY_FIXTURES)
def test_a_grey_heic_decodes_to_the_solve_jobs_own_tile_placement(name: str) -> None:
    path = INGEST_FIXTURES / name
    still = decoded_heic(path)
    if still is None:
        return
    height, width = still.samples.shape[:2]
    grid = ingest_jobs._heif_tile_grid(path, width, height)
    grey = (ingest_jobs._decode_heif_tile_grid(path, grid, width, height) if grid is not None
            else ingest_jobs._decode_with_ffmpeg(path, width, height, 1)[0])
    # The fixtures' chroma is the middle code everywhere, so R', G' and B' are the stored luma
    assert np.array_equal(still.samples, np.repeat(grey.astype(np.uint8)[..., None], 3, axis=2))
    assert still.reencoded
    assert (still.source_colour["model"], still.source_colour["chromaSubsampling"]) == ("ycbcr", "420")
    assert still.colour["transfer"] == "icc_parametric" and still.icc_profile is not None
    assert_valid_records(still)


def band_middles(width: int) -> list[slice]:
    edges = [round(i * width / len(BANDS)) for i in range(len(BANDS) + 1)]
    return [slice(edges[i] + BAND_MARGIN_PX, edges[i + 1] - BAND_MARGIN_PX) for i in range(len(BANDS))]


@pytest.mark.parametrize("name", ["sips-colour-single-96x64.heic", "sips-colour-grid-1100x700.heic"])
def test_a_colour_heic_comes_back_to_its_bands_and_agrees_with_ffmpegs_own_conversion(tmp_path: Path, name: str) -> None:
    path = MEDIA_FIXTURES / name
    still = decoded_heic(path)
    if still is None:
        return
    height, width = still.samples.shape[:2]
    assert (width, height) == tuple(int(n) for n in name.split("-")[-1].split(".")[0].split("x"))
    samples = still.samples.astype(int)
    for band, columns in zip(BANDS, band_middles(width)):
        assert np.abs(samples[:, columns] - np.array(band)).max() <= HEIC_BAND_CODES[name]
    # sips writes nclx BT.601, full range, transfer and primaries unspecified; the tiles say chroma sits left
    assert {k: v for k, v in still.source_colour.items() if k != "assumptions"} == {
        "model": "ycbcr", "bitDepth": 8, "range": "full", "matrix": "bt601", "transfer": "srgb",
        "primaries": "unspecified", "chromaSubsampling": "420", "chromaSiting": "left",
    }
    assert [a["field"] for a in still.source_colour["assumptions"]] == ["transfer", "primaries"]
    assert_valid_records(still)
    # ffmpeg's own composition (unturned, so its grid composition keeps the stored pixels) and Y'CbCr to RGB
    proc = subprocess.run([FFMPEG, "-v", "error", "-nostdin", "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
                           "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
    theirs = np.frombuffer(proc.stdout, np.uint8).reshape(height, width, 3).astype(int)
    for columns in band_middles(width):
        assert np.abs(samples[:, columns] - theirs[:, columns]).max() <= FFMPEG_CONVERSION_CODES[name]


def with_nclx(data: bytes, primaries: int, transfer: int, matrix: int, full: bool) -> bytes:
    at = data.index(b"colrnclx") + 8
    return data[:at] + primaries.to_bytes(2, "big") + transfer.to_bytes(2, "big") + matrix.to_bytes(2, "big") + bytes([0x80 if full else 0]) + data[at + 7 :]


@pytest.mark.parametrize(
    "nclx,expected",
    [
        ((1, 1, 1, True), {"primaries": "bt709", "transfer": "bt709", "matrix": "bt709", "range": "full"}),
        ((12, 13, 5, False), {"primaries": "p3_d65", "transfer": "srgb", "matrix": "bt601", "range": "limited"}),
        ((9, 16, 9, True), {"primaries": "bt2020", "transfer": "pq", "matrix": "bt2020nc", "range": "full"}),
        ((9, 18, 9, False), {"primaries": "bt2020", "transfer": "hlg", "matrix": "bt2020nc", "range": "limited"}),
    ],
)
def test_an_nclx_property_decides_each_field_it_specifies(tmp_path: Path, nclx: tuple, expected: dict) -> None:
    data = with_nclx((MEDIA_FIXTURES / "sips-colour-single-96x64.heic").read_bytes(), *nclx)
    still = decoded_heic(write(tmp_path, "a.heic", data))
    if still is None:
        return
    assert {k: still.source_colour[k] for k in expected} == expected
    assert still.source_colour["assumptions"] == []
    assert {p["name"]: p["basis"] for p in still.parameters}["matrix"] == "Read from the file's own colour tags."
    assert_valid_records(still)


@pytest.mark.parametrize(
    "nclx,sentence",
    [
        ((1, 1, 0, True), "a colour matrix other than BT.601, BT.709 or BT.2020 non-constant luminance"),
        ((1, 4, 1, True), "a transfer other than sRGB, BT.709, HLG or PQ"),
        ((5, 1, 1, True), "colour primaries this pipeline cannot tag"),
    ],
)
def test_an_nclx_value_this_pipeline_does_not_carry_is_refused(tmp_path: Path, nclx: tuple, sentence: str) -> None:
    data = with_nclx((MEDIA_FIXTURES / "sips-colour-single-96x64.heic").read_bytes(), *nclx)
    with pytest.raises(decode.DecodeRefusal) as refusal:
        decode.decode_still(write(tmp_path, "a.heic", data))
    assert refusal.value.sentence == (decode.unsupported(sentence) if GRIDS else decode.TILED_HEIC)


@pytest.mark.parametrize("quarter_turns", [1, 2, 3])
def test_a_turned_grid_keeps_its_stored_pixels(tmp_path: Path, quarter_turns: int) -> None:
    data = (MEDIA_FIXTURES / "sips-colour-grid-1100x700.heic").read_bytes()
    marker = b"\x00\x00\x00\x09irot"
    assert data.count(marker) == 1
    at = data.index(marker) + len(marker)
    turned = decoded_heic(write(tmp_path, "t.heic", data[:at] + bytes([quarter_turns]) + data[at + 1 :]))
    if turned is None:
        return
    assert np.array_equal(turned.samples, decode.decode_still(MEDIA_FIXTURES / "sips-colour-grid-1100x700.heic").samples)


def test_a_ffprobe_without_tile_grids_refuses_every_heic(monkeypatch: pytest.MonkeyPatch) -> None:
    real = decode._run

    def without_groups(command: list[str]) -> subprocess.CompletedProcess:
        if "-show_stream_groups" in command:
            return subprocess.CompletedProcess(command, 1, b"", b"Unrecognized option")
        if "-h" in command:
            return subprocess.CompletedProcess(command, 0, b"Hyper fast Audio and Video encoder", b"")
        return real(command)

    monkeypatch.setattr(decode, "_run", without_groups)
    for path in (MEDIA_FIXTURES / "sips-colour-single-96x64.heic", INGEST_FIXTURES / "sips-grid-1024x768.heic"):
        with pytest.raises(decode.DecodeRefusal) as refusal:
            decode.decode_still(path)
        assert refusal.value.sentence == decode.TILED_HEIC


def test_a_heic_whose_boxes_cannot_be_read_is_refused(tmp_path: Path) -> None:
    data = (MEDIA_FIXTURES / "sips-colour-single-96x64.heic").read_bytes()
    with pytest.raises(decode.DecodeRefusal) as refusal:
        decode.decode_still(write(tmp_path, "a.heic", data[:40]))
    assert refusal.value.sentence == decode.UNDECODABLE


# =============================================================================================
# Reading a written PNG back
# =============================================================================================


def test_a_written_png_reads_back_through_the_originals_readers() -> None:
    eight = np.random.default_rng(8).integers(0, 256, size=(3, 5, 3), dtype=np.uint8)
    assert np.array_equal(decode.read_png(saved(Image.fromarray(eight), "PNG")), eight)
    sixteen = np.random.default_rng(9).integers(0, 65536, size=(4, 3, 3), dtype=np.uint16)
    assert np.array_equal(decode.read_png(png16.write_png16(sixteen)), sixteen)
    with pytest.raises(decode.DecodeRefusal):
        decode.read_png(b"\x89PNG")
