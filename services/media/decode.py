"""Decoding a stored still for restoration (spec restoration-for-every-upload, design section 1; REQ-RST-2.1,
REQ-RST-3): the stored samples, the colour description with its assumptions, and the decode step's parameters.

- 8 bit JPEG, PNG and TIFF through Pillow, mode RGB, as the solve job reads them (`services/ingest/jobs.py`); a grey
  image becomes three equal channels; an image with transparency is refused.
- 16 bit PNG and TIFF through ffmpeg as `rgb48le` (or `gray16le`, made three equal channels), since Pillow drops 16
  bit RGB to 8 (M17).
- HEIC through ffmpeg: each tile decoded in its own pixel format, placed plane by plane on the grid and cropped to
  the stored size, as the solve job's tile path does for grey (`jobs.py:241-308`); then the matrix and the chroma
  interpolation of design section 1 give R'G'B' at 8 bits, which is what the display copy keeps ("re-encoded").

The transfer of a still comes from its ICC profile's tone curves (for a HEIC, the primary item's `colr`), else EXIF
ColorSpace 1 as sRGB, else sRGB as a recorded assumption. Primaries are never converted. Every refusal is one of the
contract's fixed sentences, so no file name, path or decoder message reaches a record.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import struct
import subprocess
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np
from PIL import Image, TiffImagePlugin, UnidentifiedImageError

from services.contracts.media import ASSUMPTION_REASONS
from services.ingest.jobs import DECODE_TIMEOUT_SEC, SolveJobError, _pick_tile_grid as _solve_pick_tile_grid
from services.ingest.metadata import HeifError, read_heif
from services.ingest.probe import RAW_TIFF_TAGS
from services.ingest.store import SNIFF_BYTES, Refused, sniff
from services.media import colour

# EXIF and TIFF tag numbers [tested on Pillow 13 ExifTags and TiffTags, and services/ingest/probe.py]
EXIF_IFD, EXIF_COLOR_SPACE, EXIF_SRGB = 0x8769, 0xA001, 1
TIFF_NEW_SUBFILE_TYPE, TIFF_BITS_PER_SAMPLE, TIFF_PHOTOMETRIC = 254, 258, 262  # [tested on Pillow 13 TiffTags]
# DNG CFA 32803 and LinearRaw 34892 photometric tags [proposed: camera raw layouts refused per design section 1]
TIFF_RAW_PHOTOMETRICS = (32803, 34892)

UNDECODABLE = "The file could not be decoded."
TILED_HEIC = "This ffmpeg cannot do what this file needs: reading a tiled HEIC."
TIME_LIMIT = "The restoration stopped at its time limit."
INTERNAL_ERROR = "The restoration stopped on an internal error."


def unsupported(what: str) -> str:
    return f"This kind of file is not supported yet: {what}."


class DecodeRefusal(Exception):
    """A still this pipeline will not decode, with the contract's fixed sentence for it."""

    def __init__(self, sentence: str) -> None:
        super().__init__(sentence)
        self.sentence = sentence


# HEIF nclx and ffprobe tag values, by the colour description's names [verified in design section 1: H.273 Table 2
# (primaries 1, 9, 12), Table 3 (transfers 1, 6, 14, 15 BT.709; 13 sRGB; 16 PQ; 18 HLG), Table 4 (matrices 1, 5, 6,
# 9); value 2 is "unspecified" in each; inference: ffprobe's names for the same values]
NCLX_PRIMARIES = {1: "bt709", 9: "bt2020", 12: "p3_d65"}
NCLX_TRANSFERS = {1: "bt709", 6: "bt709", 14: "bt709", 15: "bt709", 13: "srgb", 16: "pq", 18: "hlg"}  # [verified in design section 1: H.273 Table 3]
NCLX_MATRICES = {1: "bt709", 5: "bt601", 6: "bt601", 9: "bt2020nc"}  # [verified in design section 1: H.273 Table 4]
UNSPECIFIED = 2  # [verified in design section 1: H.273 value 2 is unspecified]
STREAM_MATRICES = {"bt709": "bt709", "smpte170m": "bt601", "bt470bg": "bt601", "bt2020nc": "bt2020nc"}
STREAM_RANGES = {"pc": "full", "tv": "limited"}
STREAM_SITINGS = {"left": "left", "center": "center", "topleft": "topleft"}
UNTAGGED = {None, "", "unknown", "unspecified"}
# The pixel formats a HEIC tile may have: (bits, chroma subsampling) [proposed in design sections 1 and 14: PIXEL_FORMATS]
TILE_FORMATS = {
    "yuv420p": (8, "420"), "yuvj420p": (8, "420"), "yuv422p": (8, "422"), "yuvj422p": (8, "422"),
    "yuv444p": (8, "444"), "yuvj444p": (8, "444"), "yuv420p10le": (10, "420"), "yuv422p10le": (10, "422"),
    "yuv444p10le": (10, "444"),
}

MEASURED_FROM_TAGS = "Read from the file's own colour tags."
MEASURED_FROM_ICC = "Read from the file's ICC profile."
MEASURED_FROM_EXIF = "Read from the file's EXIF ColorSpace."
MEASURED_FROM_FORMAT = "Read from the stream's own pixel format."
ASSUMED_IN_DESIGN = "Proposed in design section 1."


@dataclass(frozen=True)
class Field:
    """One colour field's value and where it came from: measured with its basis, or assumed with its reason."""

    value: str
    basis: str
    assumed: bool = False


@dataclass(frozen=True)
class DecodedStill:
    # H x W x 3, uint8 or uint16: the decoded pixels a display copy holds (for a HEIC, R'G'B' after the matrix)
    samples: np.ndarray
    # The original's own colour description, and the description of `samples` (R'G'B', what a variant carries)
    source_colour: dict[str, Any]
    colour: dict[str, Any]
    # The decode step's parameters, each with its origin and basis
    parameters: list[dict[str, Any]]
    icc_profile: Optional[bytes]
    # Whether the samples are not the stored samples (a HEIC's Y'CbCr made R'G'B')
    reencoded: bool


def _assumption(field: str, value: str) -> dict[str, str]:
    reason = next(entry["reason"] for entry in ASSUMPTION_REASONS if entry["field"] == field and entry["assumed"] == value)
    return {"field": field, "assumed": value, "reason": reason}


def _run(command: list[str]) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(command, capture_output=True, timeout=DECODE_TIMEOUT_SEC, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise DecodeRefusal(TIME_LIMIT) from None
    except OSError:
        raise DecodeRefusal(INTERNAL_ERROR) from None
    if proc.returncode < 0:
        raise DecodeRefusal(INTERNAL_ERROR)
    return proc


def _ffprobe_has_stream_groups() -> bool:
    """Whether this ffprobe knows `-show_stream_groups`, checked the way `test_ci_ffmpeg.py` checks it."""
    proc = _run(["ffprobe", "-v", "error", "-hide_banner", "-h"])
    if proc.returncode != 0:
        raise DecodeRefusal(INTERNAL_ERROR)
    return b"-show_stream_groups" in proc.stdout


def _probe(path: Path, *sections: str) -> dict[str, Any]:
    proc = _run(["ffprobe", "-v", "error", *sections, "-of", "json", str(path)])
    if proc.returncode != 0:
        raise DecodeRefusal(UNDECODABLE)
    try:
        report = json.loads(proc.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise DecodeRefusal(UNDECODABLE) from None
    return report if isinstance(report, dict) else {}


def _raw_frame(path: Path, pixel_format: str, stream: Optional[int] = None) -> bytes:
    """One frame of the file (or of one stream of it) as raw samples in `pixel_format`, stored pixels only."""
    mapping = ["-map", f"0:{stream}"] if stream is not None else []
    proc = _run(["ffmpeg", "-v", "error", "-nostdin", "-noautorotate", "-i", str(path), *mapping, "-frames:v", "1",
                 "-f", "rawvideo", "-pix_fmt", pixel_format, "-"])
    if proc.returncode != 0 or not proc.stdout:
        raise DecodeRefusal(UNDECODABLE)
    return proc.stdout


# =============================================================================================
# The transfer and primaries of a still
# =============================================================================================


def _exif_colour_space(exif: Image.Exif) -> Optional[int]:
    value = exif.get_ifd(EXIF_IFD).get(EXIF_COLOR_SPACE)
    return value if isinstance(value, int) else None


def _transfer_and_primaries(icc_profile: Optional[bytes], exif_colour_space: Optional[int]) -> tuple[Field, Field]:
    if icc_profile:
        try:
            kind, _ = colour.icc_tone_curves(icc_profile)
        except colour.IccError:
            raise DecodeRefusal(UNDECODABLE) from None
        return Field(kind, MEASURED_FROM_ICC), Field("icc", MEASURED_FROM_ICC)
    if exif_colour_space == EXIF_SRGB:
        # EXIF ColorSpace 1 is sRGB, whose primaries are BT.709's
        return Field("srgb", MEASURED_FROM_EXIF), Field("bt709", MEASURED_FROM_EXIF)
    return Field("srgb", ASSUMED_IN_DESIGN, assumed=True), Field("unspecified", ASSUMED_IN_DESIGN, assumed=True)


def _description(model: str, bits: int, fields: dict[str, Field], subsampling: str, icc_profile: Optional[bytes]) -> dict[str, Any]:
    """A ColourDescription (contract section 14) from the fields, its assumptions those fields that were assumed."""
    siting = fields.get("chromaSiting")
    record = {
        "model": model,
        "bitDepth": bits,
        "range": fields["range"].value,
        "matrix": fields["matrix"].value,
        "transfer": fields["transfer"].value,
        "primaries": fields["primaries"].value,
        "chromaSubsampling": subsampling,
        "assumptions": [_assumption(name, field.value) for name, field in fields.items() if field.assumed],
    }
    if siting is not None:
        record["chromaSiting"] = siting.value
    if icc_profile:
        record["iccProfileSha256"] = hashlib.sha256(icc_profile).hexdigest()
    return record


def _parameters(fields: dict[str, Field], subsampling: str, pixel_format: str, bits: int) -> list[dict[str, Any]]:
    """The decode step's parameters: each colour field, measured with its basis or a default with the design's."""
    rows = [
        {"name": name, "value": field.value, "origin": "default" if field.assumed else "measured", "basis": field.basis}
        for name, field in fields.items()
    ]
    rows += [
        {"name": "chromaSubsampling", "value": subsampling, "origin": "measured", "basis": MEASURED_FROM_FORMAT},
        {"name": "pixelFormat", "value": pixel_format, "origin": "measured", "basis": MEASURED_FROM_FORMAT},
        {"name": "bitDepth", "value": bits, "origin": "measured", "basis": MEASURED_FROM_FORMAT},
    ]
    return rows


def _rgb_still(samples: np.ndarray, bits: int, icc_profile: Optional[bytes], exif_colour_space: Optional[int], pixel_format: str) -> DecodedStill:
    transfer, primaries = _transfer_and_primaries(icc_profile, exif_colour_space)
    fields = {
        "transfer": transfer,
        "primaries": primaries,
        "matrix": Field("identity", MEASURED_FROM_FORMAT),
        "range": Field("full", MEASURED_FROM_FORMAT),
    }
    description = _description("rgb", bits, fields, "444", icc_profile)
    return DecodedStill(samples, description, description, _parameters(fields, "444", pixel_format, bits), icc_profile, False)


# =============================================================================================
# JPEG, PNG and TIFF
# =============================================================================================

ALPHA_MODES = {"RGBA", "LA", "PA", "RGBa", "La"}
EIGHT_BIT_MODES = {"1", "L", "P", "RGB"}
SIXTEEN_BIT_GREY_MODES = {"I;16", "I;16B", "I;16L", "I;16N"}
DEEP_MODES = {"I", "F"}


def _stored_depth(path: Path, image: Image.Image, family: str) -> int:
    """The stored bits per sample: Pillow reports a 16 bit RGB PNG or TIFF as 8 bit RGB (M17), so the depth is read
    from the file's own header (PNG IHDR byte 24; TIFF BitsPerSample)."""
    if family == "png":
        with path.open("rb") as handle:
            head = handle.read(25)
        return head[24] if len(head) == 25 else 0
    if family == "tiff":
        bits = image.tag_v2.get(TIFF_BITS_PER_SAMPLE, (1,))
        bits = bits if isinstance(bits, tuple) else (bits,)
        return max(int(b) for b in bits) if bits else 0
    return 8


def _sixteen_bit(path: Path, width: int, height: int) -> np.ndarray:
    streams = [s for s in _probe(path, "-show_streams").get("streams", []) if s.get("codec_type") == "video"]
    if len(streams) != 1:
        raise DecodeRefusal(UNDECODABLE)
    stored = streams[0].get("pix_fmt")
    if stored in ("rgba64be", "rgba64le", "ya16be", "ya16le"):
        raise DecodeRefusal(unsupported("an image with an alpha plane"))
    if isinstance(stored, str) and stored.startswith("bayer_"):
        raise DecodeRefusal(unsupported("a camera raw layout"))
    if stored in ("rgb48be", "rgb48le"):
        raw, channels, pixel_format = _raw_frame(path, "rgb48le"), 3, "rgb48le"
    elif stored in ("gray16be", "gray16le"):
        raw, channels, pixel_format = _raw_frame(path, "gray16le"), 1, "gray16le"
    else:
        raise DecodeRefusal(UNDECODABLE)
    if len(raw) != width * height * channels * 2:
        raise DecodeRefusal(UNDECODABLE)
    samples = np.frombuffer(raw, dtype="<u2").reshape(height, width, channels).astype(np.uint16)
    return np.repeat(samples, 3, axis=2) if channels == 1 else samples


def _tiff_is_camera_raw(path: Path) -> bool:
    """Whether a TIFF's first IFD marks a camera raw layout, read before `Image.open` because Pillow refuses CFA and
    LinearRaw photometrics as unidentified images before returning `image`."""
    with path.open("rb") as handle:
        ifd = TiffImagePlugin.ImageFileDirectory_v2(ifh=handle.read(8))
        handle.seek(ifd.next)
        ifd.load(handle)
    return (
        any(tag in ifd for tag in RAW_TIFF_TAGS)
        or bool(int(ifd.get(TIFF_NEW_SUBFILE_TYPE, 0)) & 1)
        or ifd.get(TIFF_PHOTOMETRIC) in TIFF_RAW_PHOTOMETRICS
    )


def _decode_pillow_family(path: Path, family: str) -> DecodedStill:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            warnings.simplefilter("ignore", UserWarning)
            if family == "tiff" and _tiff_is_camera_raw(path):
                raise DecodeRefusal(unsupported("a camera raw layout"))
            with Image.open(path) as image:
                image.load()
                width, height = image.size
                mode = image.mode
                icc_profile = image.info.get("icc_profile") or None
                exif_colour_space = _exif_colour_space(image.getexif())
                if mode in ALPHA_MODES or "transparency" in image.info:
                    raise DecodeRefusal(unsupported("an image with an alpha plane"))
                if mode in DEEP_MODES:
                    raise DecodeRefusal(unsupported("an image of more than 16 bits"))
                depth = _stored_depth(path, image, family)
                if depth > 16:
                    raise DecodeRefusal(unsupported("an image of more than 16 bits"))
                if depth == 16 or mode in SIXTEEN_BIT_GREY_MODES:
                    samples, bits, pixel_format = _sixteen_bit(path, width, height), 16, "rgb48le"
                elif mode in EIGHT_BIT_MODES:
                    samples, bits, pixel_format = np.asarray(image.convert("RGB"), dtype=np.uint8).copy(), 8, "rgb24"
                else:
                    raise DecodeRefusal(UNDECODABLE)
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError, SyntaxError, struct.error):
        raise DecodeRefusal(UNDECODABLE) from None
    if samples.shape != (height, width, 3):
        raise DecodeRefusal(UNDECODABLE)
    return _rgb_still(samples, bits, icc_profile, exif_colour_space, pixel_format)


# =============================================================================================
# HEIC
# =============================================================================================


def _pick_tile_grid(groups: list[Any], width: int, height: int) -> Optional[dict[str, Any]]:
    """The primary tile grid among ffprobe's stream groups, imported from the solve job (design section 2)."""
    try:
        return _solve_pick_tile_grid(groups, width, height)
    except SolveJobError:
        raise DecodeRefusal(UNDECODABLE) from None


def _planes(raw: bytes, width: int, height: int, bits: int, subsampling: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    across, down = colour.SUBSAMPLING[subsampling]
    chroma_w, chroma_h = math.ceil(width / across), math.ceil(height / down)
    dtype = np.uint8 if bits == 8 else np.dtype("<u2")
    size = dtype.itemsize if isinstance(dtype, np.dtype) else 1
    luma_bytes, chroma_bytes = width * height * size, chroma_w * chroma_h * size
    if len(raw) != luma_bytes + 2 * chroma_bytes:
        raise DecodeRefusal(UNDECODABLE)
    luma = np.frombuffer(raw[:luma_bytes], dtype=dtype).reshape(height, width)
    cb = np.frombuffer(raw[luma_bytes : luma_bytes + chroma_bytes], dtype=dtype).reshape(chroma_h, chroma_w)
    cr = np.frombuffer(raw[luma_bytes + chroma_bytes :], dtype=dtype).reshape(chroma_h, chroma_w)
    return luma.astype(np.int64), cb.astype(np.int64), cr.astype(np.int64)


def _place_tiles(path: Path, grid: dict[str, Any], streams: dict[int, dict], width: int, height: int,
                 pixel_format: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Each tile decoded in its own pixel format and placed plane by plane on the grid, then cropped to the stored
    size. Offsets must fall on whole chroma samples, so no chroma sample is split between tiles or by the crop."""
    bits, subsampling = TILE_FORMATS[pixel_format]
    across, down = colour.SUBSAMPLING[subsampling]
    tiles = grid.get("subcomponents") or []
    coded_w, coded_h = int(grid.get("coded_width", 0)), int(grid.get("coded_height", 0))
    left, top = int(grid.get("horizontal_offset", 0)), int(grid.get("vertical_offset", 0))
    if not tiles or left + width > coded_w or top + height > coded_h or left % across or top % down:
        raise DecodeRefusal(UNDECODABLE)
    luma = np.full((coded_h, coded_w), -1, dtype=np.int64)
    cb = np.full((math.ceil(coded_h / down), math.ceil(coded_w / across)), -1, dtype=np.int64)
    cr = cb.copy()
    for tile in tiles:
        stream = streams.get(int(tile["stream_index"]))
        if stream is None or stream.get("pix_fmt") != pixel_format:
            raise DecodeRefusal(UNDECODABLE)
        tile_w, tile_h = int(stream["width"]), int(stream["height"])
        x0, y0 = int(tile["tile_horizontal_offset"]), int(tile["tile_vertical_offset"])
        if x0 % across or y0 % down:
            raise DecodeRefusal(UNDECODABLE)
        planes = _planes(_raw_frame(path, pixel_format, int(tile["stream_index"])), tile_w, tile_h, bits, subsampling)
        for canvas, plane, (sx, sy) in zip((luma, cb, cr), planes, ((1, 1), (across, down), (across, down))):
            px, py = x0 // sx, y0 // sy
            w, h = min(plane.shape[1], canvas.shape[1] - px), min(plane.shape[0], canvas.shape[0] - py)
            canvas[py : py + h, px : px + w] = plane[:h, :w]
    crop = (luma[top : top + height, left : left + width],
            cb[top // down : math.ceil((top + height) / down), left // across : math.ceil((left + width) / across)],
            cr[top // down : math.ceil((top + height) / down), left // across : math.ceil((left + width) / across)])
    if any(np.any(plane < 0) for plane in crop):
        raise DecodeRefusal(UNDECODABLE)
    return crop


def _heif_fields(info: Any, stream: dict[str, Any], subsampling: str, exif_colour_space: Optional[int]) -> tuple[dict[str, Field], Optional[bytes]]:
    """The colour fields of a HEIC: the primary item's colr (an ICC profile for transfer and primaries, nclx for any
    field it specifies), then the tile stream's own tags, then the design's assumptions."""
    colr = info.colour or {}
    icc_profile = colr.get("icc")
    nclx = colr.get("nclx")
    transfer, primaries = _transfer_and_primaries(icc_profile, exif_colour_space)
    if nclx is not None and not icc_profile:
        if nclx["transfer"] != UNSPECIFIED:
            if nclx["transfer"] not in NCLX_TRANSFERS:
                raise DecodeRefusal(unsupported("a transfer other than sRGB, BT.709, HLG or PQ"))
            transfer = Field(NCLX_TRANSFERS[nclx["transfer"]], MEASURED_FROM_TAGS)
        if nclx["primaries"] != UNSPECIFIED:
            if nclx["primaries"] not in NCLX_PRIMARIES:
                raise DecodeRefusal(unsupported("colour primaries this pipeline cannot tag"))
            primaries = Field(NCLX_PRIMARIES[nclx["primaries"]], MEASURED_FROM_TAGS)
    if nclx is not None and nclx["matrix"] != UNSPECIFIED:
        if nclx["matrix"] not in NCLX_MATRICES:
            raise DecodeRefusal(unsupported("a colour matrix other than BT.601, BT.709 or BT.2020 non-constant luminance"))
        matrix = Field(NCLX_MATRICES[nclx["matrix"]], MEASURED_FROM_TAGS)
    elif stream.get("color_space") not in UNTAGGED:
        if stream["color_space"] not in STREAM_MATRICES:
            raise DecodeRefusal(unsupported("a colour matrix other than BT.601, BT.709 or BT.2020 non-constant luminance"))
        matrix = Field(STREAM_MATRICES[stream["color_space"]], MEASURED_FROM_TAGS)
    else:
        matrix = Field("bt601", ASSUMED_IN_DESIGN, assumed=True)
    if nclx is not None:
        colour_range = Field("full" if nclx["fullRange"] else "limited", MEASURED_FROM_TAGS)
    elif stream.get("color_range") not in UNTAGGED:
        if stream["color_range"] not in STREAM_RANGES:
            raise DecodeRefusal(unsupported("a colour range tag other than limited or full"))
        colour_range = Field(STREAM_RANGES[stream["color_range"]], MEASURED_FROM_TAGS)
    else:
        colour_range = Field("full", ASSUMED_IN_DESIGN, assumed=True)
    fields = {"transfer": transfer, "primaries": primaries, "matrix": matrix, "range": colour_range}
    if subsampling != "444":
        siting = stream.get("chroma_location")
        if siting in UNTAGGED:
            fields["chromaSiting"] = Field("left", ASSUMED_IN_DESIGN, assumed=True)
        elif siting in STREAM_SITINGS:
            fields["chromaSiting"] = Field(STREAM_SITINGS[siting], MEASURED_FROM_TAGS)
        else:
            raise DecodeRefusal(unsupported("a chroma siting this pipeline cannot tag"))
    return fields, icc_profile


def _decode_heif(path: Path) -> DecodedStill:
    try:
        info = read_heif(path.read_bytes())
    except HeifError:
        raise DecodeRefusal(UNDECODABLE) from None
    width, height = info.width, info.height
    # Without stream groups (ffprobe before 7.1) a grid cannot be told from a single image, and ffmpeg's own
    # composition of a grid turns it (jobs.py:246-252), so the photo is refused rather than decoded on a guess
    proc = _run(["ffprobe", "-v", "error", "-show_stream_groups", "-show_streams", "-of", "json", str(path)])
    if proc.returncode != 0:
        raise DecodeRefusal(UNDECODABLE if _ffprobe_has_stream_groups() else TILED_HEIC)
    try:
        report = json.loads(proc.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise DecodeRefusal(UNDECODABLE) from None
    if not isinstance(report, dict) or not isinstance(report.get("stream_groups"), list):
        raise DecodeRefusal(TILED_HEIC)
    streams = {int(s["index"]): s for s in report.get("streams", []) if s.get("codec_type") == "video"}
    grid = _pick_tile_grid(report["stream_groups"], width, height)
    if grid is not None:
        first = streams.get(int((grid.get("subcomponents") or [{}])[0].get("stream_index", -1)))
    else:
        sized = [s for s in streams.values() if (s.get("width"), s.get("height")) == (width, height)]
        if len(sized) > 1:
            sized = [s for s in sized if (s.get("disposition") or {}).get("default") == 1]
        first = sized[0] if len(sized) == 1 else None
    if first is None:
        raise DecodeRefusal(UNDECODABLE)
    pixel_format = first.get("pix_fmt")
    if pixel_format not in TILE_FORMATS:
        raise DecodeRefusal(UNDECODABLE)
    bits, subsampling = TILE_FORMATS[pixel_format]
    if grid is not None:
        y_codes, cb_codes, cr_codes = _place_tiles(path, grid, streams, width, height, pixel_format)
    else:
        y_codes, cb_codes, cr_codes = _planes(_raw_frame(path, pixel_format, int(first["index"])), width, height, bits, subsampling)
    exif_colour_space = None
    if info.exif:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                exif = Image.Exif()
                exif.load(info.exif)
                exif_colour_space = _exif_colour_space(exif)
        except (OSError, SyntaxError, ValueError, KeyError, TypeError, IndexError):
            exif_colour_space = None
    fields, icc_profile = _heif_fields(info, first, subsampling, exif_colour_space)
    y, cb, cr = colour.codes_to_ycbcr(y_codes, cb_codes, cr_codes, bits, fields["range"].value)
    siting = fields["chromaSiting"].value if "chromaSiting" in fields else None
    cb, cr = (colour.chroma_up(plane, height, width, subsampling, siting) for plane in (cb, cr))
    rgb = colour.ycbcr_to_rgb(y, cb, cr, fields["matrix"].value)
    samples = np.rint(np.clip(rgb, 0.0, 1.0) * 255).astype(np.uint8)
    source = _description("ycbcr", bits, fields, subsampling, icc_profile)
    output_fields = {"transfer": fields["transfer"], "primaries": fields["primaries"],
                     "matrix": Field("identity", MEASURED_FROM_FORMAT), "range": Field("full", MEASURED_FROM_FORMAT)}
    output = _description("rgb", 8, output_fields, "444", icc_profile)
    return DecodedStill(samples, source, output, _parameters(fields, subsampling, pixel_format, bits), icc_profile, True)


# =============================================================================================
# Entry points
# =============================================================================================


def decode_still(path: Path) -> DecodedStill:
    """The stored still at `path`, decoded at its stored size by its own colour tags, or `DecodeRefusal`."""
    with path.open("rb") as handle:
        head = handle.read(SNIFF_BYTES)
    try:
        family = sniff(head).family
    except Refused:
        raise DecodeRefusal(UNDECODABLE) from None
    if family == "heif":
        return _decode_heif(path)
    if family in ("jpeg", "png", "tiff"):
        return _decode_pillow_family(path, family)
    raise DecodeRefusal("A display copy is not made for a clip.")


def read_png(data: bytes) -> np.ndarray:
    """The samples of a written PNG, H x W x 3: an 8 bit one through Pillow, a 16 bit one through ffmpeg as
    `rgb48le` (M17), so the copy is read back by the same readers the original was."""
    if len(data) < 25 or data[24] not in (8, 16):
        raise DecodeRefusal(UNDECODABLE)
    if data[24] == 8:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    return np.asarray(image.convert("RGB"), dtype=np.uint8).copy()
        except Image.DecompressionBombError:
            raise DecodeRefusal(UNDECODABLE) from None
    with tempfile.TemporaryDirectory(prefix="albedian-png-") as scratch:
        path = Path(scratch) / "copy.png"
        path.write_bytes(data)
        width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        return _sixteen_bit(path, width, height)


def linear_light(still: DecodedStill) -> np.ndarray:
    """The samples as linear light, float32 H x W x 3, by the transfer the colour description names: the ICC tone
    curves channel by channel, or the transfer function of design section 1."""
    top = 255.0 if still.samples.dtype == np.uint8 else 65535.0
    code = still.samples.astype(np.float64) / top
    transfer = still.colour["transfer"]
    if transfer in ("icc_parametric", "icc_table"):
        _, curves = colour.icc_tone_curves(still.icc_profile or b"")
        light = np.stack([curve.decode(code[..., channel]) for channel, curve in enumerate(curves)], axis=-1)
    else:
        light = colour.decode_transfer(transfer, code)
    return light.astype(np.float32)
