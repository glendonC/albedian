"""Restoration and export contracts (spec restoration-for-every-upload, design section 14; REQ-RST-10): the Python
mirror of the restoration shapes in packages/contracts/src/media.ts. `SourceAsset` stays in ingest.py.

What a restoration of a stored upload records: the request, the graph of the steps that ran with every parameter
and where it came from, the attempt with what it measured and the integrity gate's result, the stored variant, and
the manifest an export carries. The job that makes them is the upload job runner's record (spec upload-jobs), not
one of these. Nothing produces these records yet: the units after restore-1 write them.

Field names, patterns, enums, lists and rules match the Zod schemas. tests/fixtures/media_contract.json is written
from `build_media_samples()` and parsed by the TypeScript tests, and each invalid sample names the refinement message
both sides refuse it with. The wire rules (camelCase keys only, finite and safe numbers, UTF-16 lengths, hashes, the
UTC instant) are `wire.py`'s. Python code builds a record by field name with `model_validate(data, by_name=True)`.

No record holds free text (REQ-RST-8.5, 9.4): a failure reason is one of the fixed sentences; a parameter's basis,
an assumption's reason and a note come from the closed lists below, where a template's number slot holds only a whole
number in its range, written without a leading zero; every id and hash has the exact form its minting code writes;
and a parameter is one row of the step parameter table, its value of that row's type and range.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import AfterValidator, BeforeValidator, Field, StrictBool, StrictFloat, StrictInt, StrictStr, StringConstraints, model_validator

from services.contracts.ingest import JOB_ID_PATTERN, AudioStreamDescriptor, DisplayOrientation
from services.contracts.wire import (
    M_CALENDAR,
    M_NOT_FINITE,
    SHA256_HEX_PATTERN,
    UTC_INSTANT_PATTERN,
    WIRE_CONFIG,
    Sha256,
    UtcInstant,
    WireInt,
    WireModel,
    WireNumber,
    wire_int,
)

# =============================================================================================
# Constants both sides share (compared through the fixture)
# =============================================================================================

VARIANT_KINDS = ("display_copy", "restoration")
PARAMETER_ORIGINS = ("measured", "requested", "default")
# Pipeline order: a graph lists its steps in this order, each at most once
GRAPH_STEP_KINDS = (
    "decode", "find_stars", "measure_noise", "register", "temporal_merge", "spatial_denoise",
    "dissolve_unconfirmed", "lift", "encode", "copy_audio",
)
# Each id as the code that mints it writes it (design section 9; services/ingest/router.py for src_, REQ-JOB-5.8 for sol_)
MEDIA_ID_PATTERNS = {
    "graph": r"^graph_[0-9a-f]{16}$",
    "variant": r"^var_[0-9a-f]{16}$",
    "attempt": r"^att_[0-9a-f]{16}$",
    "sourceAsset": r"^src_[0-9a-f]{16}$",
    "solution": r"^sol_[0-9a-f]{16}$",
}
DISCLOSURES = {
    "display_copy": "The original's own pixels, re-encoded without loss so a browser can show them. Nothing was restored.",
    "restoration": "Restored from the original's own pixels with deterministic filters. Nothing was generated.",
}
MEDIA_PATTERNS = {
    "sha256": SHA256_HEX_PATTERN,
    "utcInstant": UTC_INSTANT_PATTERN,
    # [0-9], not \d (pydantic's regex takes any Unicode digit for \d); each part 0 to 99 with no leading zero
    # [proposed: room for every version this pipeline will have, none for a year or a date]
    "semver": r"^(?:0|[1-9][0-9]?)\.(?:0|[1-9][0-9]?)\.(?:0|[1-9][0-9]?)$",
    # A stored variant is served at its own route (REQ-RST-8.6), never named by a path in the store
    "variantUri": r"^/api/media/[0-9a-f]{64}/variants/var_[0-9a-f]{16}/file$",
    # A codec as ffprobe names it [proposed: lower-case letters, digits and underscore, at most 32]
    "codecName": r"^[a-z0-9_]{1,32}$",
}
COLOUR_MODELS = ("rgb", "ycbcr")
COLOUR_BIT_DEPTHS = (8, 10, 16)
COLOUR_RANGES = ("full", "limited")
COLOUR_MATRICES = ("identity", "bt601", "bt709", "bt2020nc")
COLOUR_TRANSFERS = ("srgb", "bt709", "hlg", "pq", "icc_parametric", "icc_table")
COLOUR_PRIMARIES = ("bt709", "p3_d65", "bt2020", "icc", "unspecified")
CHROMA_SUBSAMPLINGS = ("444", "422", "420")
CHROMA_SITINGS = ("left", "center", "topleft")
ASSUMABLE_COLOUR_FIELDS = ("range", "matrix", "transfer", "primaries", "chromaSiting")
# Every value an assumable field can hold, without repeats, in first-seen order
ASSUMABLE_COLOUR_VALUES = tuple(dict.fromkeys((*COLOUR_RANGES, *COLOUR_MATRICES, *COLOUR_TRANSFERS, *COLOUR_PRIMARIES, *CHROMA_SITINGS)))
GATE_STATISTICS = ("detector", "detector_local_background_removed")
NOISE_NOT_MEASURED_REASONS = ("too_few_flat_blocks", "fine_detail_mostly_zero", "frame_too_small")
LIFT_DECIDED_BY = ("none", "target", "requested", "noise_bound", "quantisation_bound", "maximum")
ATTEMPT_STATUSES = ("ready", "rejected", "failed")
REJECTION_REASONS = ("unconfirmed_points", "star_flux_changed", "frame_times_differ", "audio_differs", "copy_differs")
# The rejections the gate itself makes; the others come before the gate (design section 10) or without one
GATE_REJECTIONS = ("unconfirmed_points", "star_flux_changed")
VARIANT_MIME_TYPES = ("image/png", "video/mp4", "video/quicktime")
# The decoder's own pixel formats the pipeline reads and writes (design section 1; restore-5's clip_io)
PIXEL_FORMATS = ("yuv420p", "yuvj420p", "yuv422p", "yuvj422p", "yuv444p", "yuvj444p", "yuv420p10le", "yuv422p10le",
                 "yuv444p10le", "rgb24", "rgb48le")
VARIANT_CONTAINERS = ("png", "mov", "mp4")
VARIANT_ENCODERS = ("png", "libx264", "libx265")
# x264's and x265's preset names (design section 10)
ENCODER_PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow", "placebo")
EXPORT_MANIFEST_FORMAT = "albedian.export-manifest"
EXPORT_MANIFEST_FORMAT_VERSION = 1
# The largest exposure a request may ask for, in EV [proposed in design section 14; the lift's own bounds decide]
EXPOSURE_EV_MAX = 6

MEDIA_ENUMS = {
    "variantKinds": list(VARIANT_KINDS),
    "parameterOrigins": list(PARAMETER_ORIGINS),
    "graphStepKinds": list(GRAPH_STEP_KINDS),
    "colourModels": list(COLOUR_MODELS),
    "colourBitDepths": list(COLOUR_BIT_DEPTHS),
    "colourRanges": list(COLOUR_RANGES),
    "colourMatrices": list(COLOUR_MATRICES),
    "colourTransfers": list(COLOUR_TRANSFERS),
    "colourPrimaries": list(COLOUR_PRIMARIES),
    "chromaSubsamplings": list(CHROMA_SUBSAMPLINGS),
    "chromaSitings": list(CHROMA_SITINGS),
    "assumableColourFields": list(ASSUMABLE_COLOUR_FIELDS),
    "assumableColourValues": list(ASSUMABLE_COLOUR_VALUES),
    "gateStatistics": list(GATE_STATISTICS),
    "noiseNotMeasuredReasons": list(NOISE_NOT_MEASURED_REASONS),
    "liftDecidedBy": list(LIFT_DECIDED_BY),
    "attemptStatuses": list(ATTEMPT_STATUSES),
    "rejectionReasons": list(REJECTION_REASONS),
    "gateRejections": list(GATE_REJECTIONS),
    "variantMimeTypes": list(VARIANT_MIME_TYPES),
    "pixelFormats": list(PIXEL_FORMATS),
    "variantContainers": list(VARIANT_CONTAINERS),
    "variantEncoders": list(VARIANT_ENCODERS),
    "encoderPresets": list(ENCODER_PRESETS),
}


# The step parameter table [proposed: the parameters design sections 1 to 10 name for each step; a unit that needs
# another adds its row here first]: each parameter's steps, its type, and its range or its values. Ranges are bounds a
# value can never pass, not the values the design proposes.
def _integer(name: str, steps: tuple[str, ...], low: int, high: int, unit: str) -> dict[str, Any]:
    return {"name": name, "steps": list(steps), "type": "integer", "min": low, "max": high, "unit": unit}


def _number(name: str, steps: tuple[str, ...], low: float, high: float, unit: str) -> dict[str, Any]:
    return {"name": name, "steps": list(steps), "type": "number", "min": low, "max": high, "unit": unit}


def _text(name: str, steps: tuple[str, ...], values: tuple[str, ...]) -> dict[str, Any]:
    return {"name": name, "steps": list(steps), "type": "text", "values": list(values)}


STEP_PARAMETERS = (
    _text("transfer", ("decode",), COLOUR_TRANSFERS),
    _text("matrix", ("decode",), COLOUR_MATRICES),
    _text("range", ("decode",), COLOUR_RANGES),
    _text("primaries", ("decode",), COLOUR_PRIMARIES),
    _text("chromaSubsampling", ("decode",), CHROMA_SUBSAMPLINGS),
    _text("chromaSiting", ("decode",), CHROMA_SITINGS),
    _text("pixelFormat", ("decode", "encode"), PIXEL_FORMATS),
    _integer("bitDepth", ("decode", "encode"), 8, 16, "bits"),
    _text("container", ("encode",), VARIANT_CONTAINERS),
    _text("encoder", ("encode",), VARIANT_ENCODERS),
    _text("preset", ("encode",), ENCODER_PRESETS),
    _integer("crf", ("encode",), 0, 51, "x264 quality"),
    _integer("zlibLevel", ("encode",), 0, 9, "zlib level"),
    _integer("tilePx", ("find_stars",), 16, 4096, "px"),
    _integer("overlapPx", ("find_stars",), 0, 1024, "px"),
    _number("confirmThresholdSnr", ("find_stars",), 0, 100, "signal to noise"),
    _number("matchRadiusPx", ("find_stars",), 0, 64, "px"),
    _number("mergeRadiusPx", ("find_stars",), 0, 64, "px"),
    _integer("footprintMaxPx", ("find_stars",), 1, 256, "px"),
    _integer("clippedGrowPx", ("find_stars",), 0, 64, "px"),
    _text("statistic", ("find_stars", "dissolve_unconfirmed"), GATE_STATISTICS),
    _integer("bins", ("measure_noise",), 3, 24, "intensity bins"),
    _integer("blockPx", ("measure_noise", "register"), 2, 4096, "px"),
    _integer("windowFrames", ("register", "temporal_merge"), 0, 30, "frames either side"),
    _number("closureMaxPx", ("register",), 0, 100, "px"),
    _number("blockResidualMaxPx", ("register",), 0, 100, "px"),
    _integer("windowPx", ("temporal_merge",), 1, 31, "px"),
    _number("toleranceFactor", ("temporal_merge",), 0, 100, "noise variances"),
    _integer("levels", ("spatial_denoise",), 1, 8, "wavelet levels"),
    _number("sigmaScale", ("spatial_denoise",), 0, 100, "noise sigmas"),
    _integer("maxPasses", ("dissolve_unconfirmed",), 0, 3, "passes"),
    _number("variantThresholdSnr", ("dissolve_unconfirmed",), 0, 100, "signal to noise"),
    _number("gainAtBlack", ("lift",), 1, 1000, "gain"),
    _integer("targetMedianCode", ("lift",), 0, 255, "code of 255"),
    _number("quantisationBoundSteps", ("lift",), 0, 256, "output code steps"),
    _number("maximumGain", ("lift",), 1, 1000, "gain"),
    _number("exposureEv", ("lift",), 0, EXPOSURE_EV_MAX, "EV"),
    _integer("streams", ("copy_audio",), 0, 64, "audio streams"),
)
PARAMETER_NAMES = tuple(parameter["name"] for parameter in STEP_PARAMETERS)
_PARAMETER_BY_NAME = {parameter["name"]: parameter for parameter in STEP_PARAMETERS}

# The format names a refusal may give (design section 1 for stills; section 10 and restore-5's clip_io for clips)
FAILURE_FORMATS = (
    "a clip with no video stream",
    "a clip with more than one video stream",
    "an interlaced clip",
    "a clip of more than 10 bits",
    "a clip with an alpha plane",
    "a clip whose pixels are not planar Y'CbCr at 8 or 10 bits",
    "an odd frame size with subsampled chroma",
    "a time base that is not one over a whole number",
    "a colour range tag other than limited or full",
    "a colour matrix other than BT.601, BT.709 or BT.2020 non-constant luminance",
    "a transfer other than sRGB, BT.709, HLG or PQ",
    "colour primaries this pipeline cannot tag",
    "a chroma siting this pipeline cannot tag",
    "a container other than QuickTime or MP4",
    "an image with an alpha plane",
    "a camera raw layout",
    "an image of more than 16 bits",
    "an ICC curve that does not increase",
)
# The capabilities a refusal may name (design sections 1 and 10; restore-5's clip_io)
FAILURE_CAPABILITIES = (
    "a display orientation on a copied stream",
    *(f"8 bit H.264 encoding of {name}" for name in ("yuv420p", "yuvj420p", "yuv422p", "yuvj422p", "yuv444p", "yuvj444p")),
    *(f"10 bit HEVC encoding of {name}" for name in ("yuv420p10le", "yuv422p10le", "yuv444p10le")),
    "reading a tiled HEIC",
)
# The acceptance criteria of the restoration spec, which a basis may cite: read from its requirements, so a criterion
# that does not exist cannot be cited, and two numbers cannot be read together as a date or a clock time
# Generated by scripts/restoration_criteria.py from the restoration spec's requirements; do not edit by hand
RESTORATION_CRITERIA = (
    "1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7",
    "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7",
    "3.1", "3.2", "3.3", "3.4", "3.5", "3.6", "3.7",
    "4.1", "4.2", "4.3", "4.4", "4.5", "4.6", "4.7", "4.8",
    "5.1", "5.2", "5.3", "5.4",
    "6.1", "6.2", "6.3", "6.4", "6.5", "6.6", "6.7", "6.8", "6.9", "6.10", "6.11", "6.12", "6.13",
    "7.1", "7.2", "7.3", "7.4", "7.5", "7.6", "7.7", "7.8", "7.9", "7.10",
    "8.1", "8.2", "8.3", "8.4", "8.5", "8.6", "8.7", "8.8", "8.9", "8.10",
    "9.1", "9.2", "9.3", "9.4", "9.5", "9.6", "9.7",
    "10.1", "10.2", "10.3", "10.4",
    "11.1", "11.2", "11.3",
    "12.1", "12.2", "12.3", "12.4",
)
# End of the generated list
# A template's slots: a text slot holds one of its values, a number slot a whole number in its range
TEMPLATE_SLOTS = {
    "format": {"type": "text", "values": list(FAILURE_FORMATS)},
    "capability": {"type": "text", "values": list(FAILURE_CAPABILITIES)},
    "section": {"type": "integer", "unit": "section of the restoration design", "min": 1, "max": 18},
    "criterion": {"type": "text", "values": list(RESTORATION_CRITERIA)},
}
# The only text a failed attempt stores (design section 11, and the runner's sentence for a stopped server)
FAILURE_SENTENCES = (
    "The file could not be decoded.",
    "This kind of file is not supported yet: {format}.",
    "This ffmpeg cannot do what this file needs: {capability}.",
    "A display copy is not made for a clip.",
    "The frame times could not be kept.",
    "An audio stream could not be copied unchanged.",
    "The original changed while the job ran.",
    "The restoration stopped at its time limit.",
    "The restoration stopped on an internal error.",
    "The server stopped before this job finished.",
)
# The plain sentences a record's notes may hold [proposed, each from the requirement named in design section 14]
NOTES = (
    "The noise could not be measured, so no noise reduction ran and the lift is held by the quantisation bound alone.",
    "The clip was recorded almost at black: there was no noise to measure, and the lift is held by the quantisation bound.",
    "The requested exposure was beyond a measured bound, so the lift stops at that bound.",
    "Registration did not hold for every frame; those frames have no temporal step.",
    "Points the original does not confirm were dissolved before the gate.",
    "Clipped regions were left out of the gate.",
    "Streams that are neither video nor audio were not copied.",
    "The lift was held by its maximum.",
)
# Why a colour field was assumed: one sentence per field and assumed value (design section 1)
ASSUMPTION_REASONS = (
    {"field": "range", "assumed": "limited", "reason": "The file does not state its range, and Y'CbCr video is taken as limited range."},
    {"field": "range", "assumed": "full", "reason": "The file does not state its range, and a JPEG or HEIC still is taken as full range."},
    {"field": "matrix", "assumed": "bt709", "reason": "The file does not state its matrix, and a frame 720 lines or taller is taken as BT.709."},
    {"field": "matrix", "assumed": "bt601",
     "reason": "The file does not state its matrix, and a frame under 720 lines or a JPEG or HEIC still is taken as BT.601."},
    {"field": "transfer", "assumed": "srgb", "reason": "The file has no ICC profile and no EXIF ColorSpace, so sRGB is assumed."},
    {"field": "transfer", "assumed": "bt709", "reason": "The clip does not state its transfer, so BT.709 is assumed."},
    {"field": "primaries", "assumed": "unspecified",
     "reason": "The file does not state its primaries, so they stay unspecified and are never converted."},
    {"field": "chromaSiting", "assumed": "left",
     "reason": "The file does not state its chroma siting, so left is assumed, the common default of H.264 and HEVC."},
)
# What a parameter's basis may say, by its origin: what on this file it was measured from, the request field, or a source
BASES = {
    "measured": [
        "Read from the file's own colour tags.",
        "Read from the file's ICC profile.",
        "Read from the file's EXIF ColorSpace.",
        "Read from the stream's own pixel format.",
        "Read from the decoded original.",
        "Measured on this file's noise table.",
        "Solved for this file from its median and its measured bounds.",
        "Counted from the asset record's audio streams.",
        "Measured on this file's confirmed stars.",
        "Measured on this clip's registered frames.",
    ],
    "requested": ["exposureEv"],
    "default": [
        "Proposed in design section {section}.",
        "Decided by the lead in design section {section}.",
        "Verified at its source in design section {section}.",
        "Required by REQ-RST-{criterion}.",
        "The detector's own floor.",
    ],
}
MEDIA_VOCABULARY = {
    "failureSentences": list(FAILURE_SENTENCES),
    "notes": list(NOTES),
    "assumptionReasons": list(ASSUMPTION_REASONS),
    "bases": BASES,
    "slots": TEMPLATE_SLOTS,
    "stepParameters": list(STEP_PARAMETERS),
}

_SLOT = re.compile(r"\{([a-z]+)\}")
_WHOLE_NUMBER = re.compile(r"0|[1-9][0-9]*")


def fills_template(text: str, template: str) -> bool:
    """Whether `text` fills `template` (slots written `{name}`): literal parts as they are, a text slot with one of its
    values, a number slot with a whole number in its range written without a leading zero. The Zod side walks the
    template the same way."""
    parts = _SLOT.split(template)

    def match(position: int, index: int) -> bool:
        if index == len(parts):
            return position == len(text)
        part = parts[index]
        if index % 2 == 0:
            return text.startswith(part, position) and match(position + len(part), index + 1)
        slot = TEMPLATE_SLOTS[part]
        if slot["type"] == "integer":
            found = _WHOLE_NUMBER.match(text, position)
            if found is None:
                return False
            value = int(found.group())
            return slot["min"] <= value <= slot["max"] and match(found.end(), index + 1)
        return any(text.startswith(value, position) and match(position + len(value), index + 1) for value in slot["values"])

    return match(0, 0)


def fills_any_template(text: str, templates: Any) -> bool:
    return any(fills_template(text, template) for template in templates)


def parameter_value_fits(parameter: dict[str, Any], value: Any) -> bool:
    """Whether `value` is of the parameter row's type and inside its range or its values. An infinity never gets here
    (the strict float refuses it), and every row's range is far inside the safe integers."""
    if parameter["type"] == "text":
        return isinstance(value, str) and value in parameter["values"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if parameter["type"] == "integer" and isinstance(value, float) and not value.is_integer():
        return False
    return parameter["min"] <= value <= parameter["max"]


def basis_fits(origin: str, basis: str) -> bool:
    return fills_any_template(basis, BASES["default"]) if origin == "default" else basis in BASES[origin]


# Every refinement's message, word for word the Zod side's
M_REQUEST_OPTION = "a display copy takes no option"
M_PARAMETER_VALUE = "a parameter's value has its row's type and range"
M_BASIS = "a parameter's basis is one its origin allows"
M_PARAMETER_ONCE = "a step names each parameter once"
M_PARAMETER_STEP = "a step holds only its kind's parameters"
M_GRAPH_KIND = "the graph is of the kind its request asks for"
M_FIRST_DECODE = "the first step is decode"
M_DISPLAY_COPY_STEPS = "a display copy is decode then encode, nothing more"
M_PIPELINE_ORDER = "steps run once each, in pipeline order"
M_LIFT_AND_ENCODE = "a restoration holds a lift and an encode"
M_RGB = "R'G'B' has no matrix and no chroma subsampling"
M_YCBCR = "Y'CbCr names its matrix"
M_SITING = "chroma siting is given exactly when chroma is subsampled"
M_BIT_DEPTH = "R'G'B' has 8 or 16 bits and Y'CbCr 8 or 10"
M_PROFILE = "a description read from an ICC profile names the profile's hash"
M_ASSUMED_ONCE = "each field is assumed once"
M_ASSUMED_VALUE = "an assumed value is the value the description holds"
M_ASSUMPTION_REASON = "an assumption's reason is the one its field and value have"
M_NOISE_COLUMNS = "every column of the noise table has one value per intensity"
M_NOISE_INCREASING = "noise table intensities strictly increase"
M_GATE_PASSED = "the gate passes exactly when no point is unconfirmed and no star changed"
M_GATE_POINTS = "unconfirmed points are points of the variant"
M_GATE_FRAMES = "frames with unconfirmed points are frames checked"
M_GATE_ANY_FRAME = "a frame is counted exactly when an unconfirmed point is"
M_GATE_FRAME_POINTS = "each frame counted holds at least one unconfirmed point"
M_GATE_STARS = "stars outside the tolerance are stars measured"
M_GATE_FLUX_GIVEN = "the flux change is given exactly when a star was measured"
M_GATE_WORST = "a star is outside the tolerance exactly when the worst change exceeds it"
M_STARS_CONFIRMED = "confirmed stars are stars found"
M_NO_LIFT = "no lift is a gain of one"
M_CLIP_NEIGHBOURS = "frames with neighbours are frames of the clip"
M_FRAMES_TOGETHER = "points confirmed on frames together need frames with neighbours"
M_NOTE = "a note is one of the fixed notes"
M_MEASURED_NOISE = "the noise is measured or the reason it is not, never both"
M_FAILURE_SENTENCE = "a failure reason is one of the fixed sentences"
M_READY_PASSED = "a ready attempt passed its gate"
M_REFUSED_IS_REJECTED = "an attempt the gate refused is rejected, never failed"
M_GATE_REJECTION = "a rejection by the gate carries the gate that refused"
M_REJECTION_NAMED = "the rejection names what the gate found"
M_NO_GATE_BEFORE = "a rejection made before or without the gate carries no gate result"
M_GATE_STARS_FOUND = "a gate measures no more stars than were found"
M_ATTEMPT_TIMES = "an attempt finishes after it starts"
M_DISCLOSURE = "the disclosure is the fixed sentence of its kind"
M_SOURCE_ASSET = "the asset id is the source's own"
M_STILL = "a still is one frame with no duration, audio or frame times"
M_CLIP_TIMES = "a clip names the hash of its frame times"
M_DISPLAY_COPY_STILL = "a display copy is a PNG of a still"
M_VARIANT_URI = "a variant is served at its own route"
M_VARIANT_COLOUR = "a PNG holds R'G'B' and a clip Y'CbCr"
M_MANIFEST_SOURCE = "the parts are of one source"
M_MANIFEST_IDS = "the variant, its graph and its attempt name each other"
M_MANIFEST_READY = "only a ready attempt is exported"
M_MANIFEST_DISCLOSURE = "the manifest carries the variant's disclosure"
M_MANIFEST_GATE = "a restoration carries its gate result and a display copy none"
M_MANIFEST_NOISE = "a restoration records its noise table or why it has none"
M_MANIFEST_FRAMES = "the measured clip has the variant's frame count"
M_STILL_GATE = "a still's gate checks one frame"
M_SOLUTION = "the solution is the source's own"


def _attempt_fit(status: str) -> str:
    return f"fields do not fit a {status} attempt"


REFINEMENT_MESSAGES = (
    M_CALENDAR, M_REQUEST_OPTION, M_PARAMETER_VALUE, M_BASIS, M_PARAMETER_ONCE, M_PARAMETER_STEP, M_GRAPH_KIND,
    M_FIRST_DECODE, M_DISPLAY_COPY_STEPS, M_PIPELINE_ORDER, M_LIFT_AND_ENCODE, M_RGB, M_YCBCR, M_SITING, M_BIT_DEPTH,
    M_PROFILE, M_ASSUMED_ONCE, M_ASSUMED_VALUE, M_ASSUMPTION_REASON, M_NOISE_COLUMNS, M_NOISE_INCREASING,
    M_GATE_PASSED, M_GATE_POINTS, M_GATE_FRAMES, M_GATE_ANY_FRAME, M_GATE_FRAME_POINTS, M_GATE_STARS,
    M_GATE_FLUX_GIVEN, M_GATE_WORST, M_STARS_CONFIRMED, M_NO_LIFT, M_CLIP_NEIGHBOURS, M_FRAMES_TOGETHER, M_NOTE,
    M_MEASURED_NOISE, M_FAILURE_SENTENCE, *(_attempt_fit(s) for s in ATTEMPT_STATUSES), M_READY_PASSED,
    M_REFUSED_IS_REJECTED, M_GATE_REJECTION, M_REJECTION_NAMED, M_NO_GATE_BEFORE, M_GATE_STARS_FOUND,
    M_ATTEMPT_TIMES, M_DISCLOSURE, M_SOURCE_ASSET, M_STILL, M_CLIP_TIMES, M_DISPLAY_COPY_STILL, M_VARIANT_URI,
    M_VARIANT_COLOUR, M_MANIFEST_SOURCE, M_MANIFEST_IDS, M_MANIFEST_READY, M_MANIFEST_DISCLOSURE, M_MANIFEST_GATE,
    M_MANIFEST_NOISE, M_MANIFEST_FRAMES, M_STILL_GATE, M_SOLUTION,
)


def variant_uri(source_sha256: str, variant_id: str) -> str:
    """Where a stored variant is served (REQ-RST-8.6): its own route under its original, never a store path."""
    return f"/api/media/{source_sha256}/variants/{variant_id}/file"


# =============================================================================================
# Field types
# =============================================================================================


def _from_list(values: Any, message: str) -> Any:
    def check(text: str) -> str:
        if text not in values:
            raise ValueError(message)
        return text

    return Annotated[StrictStr, AfterValidator(check)]


def _from_templates(templates: Any, message: str) -> Any:
    def check(text: str) -> str:
        if not fills_any_template(text, templates):
            raise ValueError(message)
        return text

    return Annotated[StrictStr, AfterValidator(check)]


def _instant(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _refuse(failures: list[str]) -> None:
    """Every rule that fails is named, as Zod reports every issue its refinement adds."""
    if failures:
        raise ValueError("; ".join(failures))


def _pattern(source: str) -> Any:
    return Annotated[str, StringConstraints(pattern=source)]


Semver = _pattern(MEDIA_PATTERNS["semver"])
GraphId = _pattern(MEDIA_ID_PATTERNS["graph"])
VariantId = _pattern(MEDIA_ID_PATTERNS["variant"])
AttemptId = _pattern(MEDIA_ID_PATTERNS["attempt"])
SourceAssetId = _pattern(MEDIA_ID_PATTERNS["sourceAsset"])
SolutionId = _pattern(MEDIA_ID_PATTERNS["solution"])
JobId = _pattern(JOB_ID_PATTERN)
Count = Annotated[WireInt, Field(ge=0)]
Positive = Annotated[WireInt, Field(gt=0)]
NonNegative = Annotated[WireNumber, Field(ge=0)]
PositiveNumber = Annotated[WireNumber, Field(gt=0)]
VariantKind = Literal[*VARIANT_KINDS]
Disclosure = Literal[*DISCLOSURES.values()]
Note = _from_list(NOTES, M_NOTE)
FailureReason = _from_templates(FAILURE_SENTENCES, M_FAILURE_SENTENCE)


class _Record(WireModel):
    """The wire base: camelCase keys only, strict, no unknown key, finite and safe numbers."""


# =============================================================================================
# The request and the graph
# =============================================================================================


class RestoreRequest(_Record):
    kind: VariantKind
    exposure_ev: Optional[Annotated[WireNumber, Field(ge=0, le=EXPOSURE_EV_MAX)]] = None

    @model_validator(mode="after")
    def _option_fits_kind(self) -> "RestoreRequest":
        _refuse([M_REQUEST_OPTION] if self.kind != "restoration" and self.exposure_ev is not None else [])
        return self


class GraphParameter(_Record):
    name: Literal[*PARAMETER_NAMES]
    value: Union[StrictBool, StrictInt, StrictFloat, StrictStr]
    origin: Literal[*PARAMETER_ORIGINS]
    # What on this file it was measured from, the request field, or the default's source
    basis: StrictStr

    @model_validator(mode="after")
    def _fits_its_row_and_origin(self) -> "GraphParameter":
        failures = []
        if not parameter_value_fits(_PARAMETER_BY_NAME[self.name], self.value):
            failures.append(M_PARAMETER_VALUE)
        if not basis_fits(self.origin, self.basis):
            failures.append(M_BASIS)
        _refuse(failures)
        return self


class GraphStep(_Record):
    kind: Literal[*GRAPH_STEP_KINDS]
    version: Semver
    parameters: Annotated[list[GraphParameter], Field(max_length=64)]

    @model_validator(mode="after")
    def _each_parameter_once_and_its_own(self) -> "GraphStep":
        failures = []
        names = [parameter.name for parameter in self.parameters]
        if len(set(names)) != len(names):
            failures.append(M_PARAMETER_ONCE)
        if any(self.kind not in _PARAMETER_BY_NAME[name]["steps"] for name in names):
            failures.append(M_PARAMETER_STEP)
        _refuse(failures)
        return self


class ProcessingGraph(_Record):
    id: GraphId
    source_sha256: Sha256
    kind: VariantKind
    pipeline_version: Semver
    request: RestoreRequest
    steps: Annotated[list[GraphStep], Field(min_length=2, max_length=16)]

    @model_validator(mode="after")
    def _steps_fit_kind(self) -> "ProcessingGraph":
        failures = []
        kinds = [step.kind for step in self.steps]
        if self.kind != self.request.kind:
            failures.append(M_GRAPH_KIND)
        if kinds[0] != "decode":
            failures.append(M_FIRST_DECODE)
        if self.kind == "display_copy":
            if len(kinds) != 2 or kinds[1] != "encode":
                failures.append(M_DISPLAY_COPY_STEPS)
        else:
            order = [GRAPH_STEP_KINDS.index(kind) for kind in kinds]
            if any(later <= earlier for earlier, later in zip(order, order[1:])):
                failures.append(M_PIPELINE_ORDER)
            if "lift" not in kinds or "encode" not in kinds:
                failures.append(M_LIFT_AND_ENCODE)
        _refuse(failures)
        return self


# =============================================================================================
# What an attempt measured, and the gate
# =============================================================================================


class ColourAssumption(_Record):
    field: Literal[*ASSUMABLE_COLOUR_FIELDS]
    assumed: Literal[*ASSUMABLE_COLOUR_VALUES]
    reason: StrictStr

    @model_validator(mode="after")
    def _reason_of_its_field_and_value(self) -> "ColourAssumption":
        entry = {"field": self.field, "assumed": self.assumed, "reason": self.reason}
        _refuse([M_ASSUMPTION_REASON] if entry not in ASSUMPTION_REASONS else [])
        return self


class ColourDescription(_Record):
    model: Literal[*COLOUR_MODELS]
    bit_depth: Annotated[Literal[*COLOUR_BIT_DEPTHS], BeforeValidator(wire_int)]
    range: Literal[*COLOUR_RANGES]
    matrix: Literal[*COLOUR_MATRICES]
    transfer: Literal[*COLOUR_TRANSFERS]
    primaries: Literal[*COLOUR_PRIMARIES]
    chroma_subsampling: Literal[*CHROMA_SUBSAMPLINGS]
    chroma_siting: Optional[Literal[*CHROMA_SITINGS]] = None
    icc_profile_sha256: Optional[Sha256] = None
    assumptions: Annotated[list[ColourAssumption], Field(max_length=5)]

    @model_validator(mode="after")
    def _fields_agree(self) -> "ColourDescription":
        failures = []
        if self.model == "rgb" and (self.matrix != "identity" or self.chroma_subsampling != "444"):
            failures.append(M_RGB)
        if self.model == "ycbcr" and self.matrix == "identity":
            failures.append(M_YCBCR)
        if (self.chroma_siting is not None) != (self.chroma_subsampling != "444"):
            failures.append(M_SITING)
        # REQ-RST-3.3: R'G'B' stills at 8 and 16 bits, Y'CbCr at 8 and 10
        if self.bit_depth not in ((8, 16) if self.model == "rgb" else (8, 10)):
            failures.append(M_BIT_DEPTH)
        if (self.transfer in ("icc_parametric", "icc_table") or self.primaries == "icc") and self.icc_profile_sha256 is None:
            failures.append(M_PROFILE)
        fields = [assumption.field for assumption in self.assumptions]
        if len(set(fields)) != len(fields):
            failures.append(M_ASSUMED_ONCE)
        held = {"range": self.range, "matrix": self.matrix, "transfer": self.transfer, "primaries": self.primaries,
                "chromaSiting": self.chroma_siting}
        if any(held[assumption.field] != assumption.assumed for assumption in self.assumptions):
            failures.append(M_ASSUMED_VALUE)
        _refuse(failures)
        return self


class NoiseSigma(_Record):
    r: list[NonNegative]
    g: list[NonNegative]
    b: list[NonNegative]


class NoiseTable(_Record):
    # Linear light, increasing
    intensities: Annotated[list[Annotated[WireNumber, Field(ge=0, le=1)]], Field(min_length=3, max_length=24)]
    sigma: NoiseSigma
    block_counts: list[Positive]

    @model_validator(mode="after")
    def _columns_line_up(self) -> "NoiseTable":
        failures = []
        bins = len(self.intensities)
        if any(len(column) != bins for column in (self.sigma.r, self.sigma.g, self.sigma.b, self.block_counts)):
            failures.append(M_NOISE_COLUMNS)
        if any(later <= earlier for earlier, later in zip(self.intensities, self.intensities[1:])):
            failures.append(M_NOISE_INCREASING)
        _refuse(failures)
        return self


class IntegrityGateResult(_Record):
    statistic: Literal[*GATE_STATISTICS]
    variant_threshold_snr: PositiveNumber
    confirm_threshold_snr: PositiveNumber
    tile_px: Positive
    match_radius_px: PositiveNumber
    frames_checked: Positive
    variant_point_count: Count
    unconfirmed_point_count: Count
    frames_with_unconfirmed: Count
    clipped_points_left_out: Count
    stars_measured: Count
    stars_outside_flux_tolerance: Count
    flux_tolerance_sigma: PositiveNumber
    # Absent when no star was measured
    worst_flux_change_sigma: Optional[NonNegative] = None
    median_flux_ratio: Optional[PositiveNumber] = None
    passed: StrictBool

    @model_validator(mode="after")
    def _counts_add_up(self) -> "IntegrityGateResult":
        failures = []
        unconfirmed, frames, outside = self.unconfirmed_point_count, self.frames_with_unconfirmed, self.stars_outside_flux_tolerance
        if self.passed != (unconfirmed == 0 and outside == 0):
            failures.append(M_GATE_PASSED)
        if unconfirmed > self.variant_point_count:
            failures.append(M_GATE_POINTS)
        if frames > self.frames_checked:
            failures.append(M_GATE_FRAMES)
        if (frames == 0) != (unconfirmed == 0):
            failures.append(M_GATE_ANY_FRAME)
        if frames > unconfirmed:
            failures.append(M_GATE_FRAME_POINTS)
        if outside > self.stars_measured:
            failures.append(M_GATE_STARS)
        measured = self.stars_measured > 0
        if (self.worst_flux_change_sigma is not None) != measured or (self.median_flux_ratio is not None) != measured:
            failures.append(M_GATE_FLUX_GIVEN)
        worst = self.worst_flux_change_sigma
        if worst is not None and (outside > 0) != (worst > self.flux_tolerance_sigma):
            failures.append(M_GATE_WORST)
        _refuse(failures)
        return self


class StarCounts(_Record):
    found: Count
    confirmed: Count

    @model_validator(mode="after")
    def _confirmed_were_found(self) -> "StarCounts":
        _refuse([M_STARS_CONFIRMED] if self.confirmed > self.found else [])
        return self


class Lift(_Record):
    gain_at_black: Annotated[WireNumber, Field(ge=1)]
    decided_by: Literal[*LIFT_DECIDED_BY]

    @model_validator(mode="after")
    def _no_lift_is_one(self) -> "Lift":
        _refuse([M_NO_LIFT] if self.decided_by == "none" and self.gain_at_black != 1 else [])
        return self


class ClipMeasurements(_Record):
    frame_count: Positive
    frames_with_neighbours: Count
    median_closure_error_px: Optional[NonNegative] = None
    exact_repeat_share: Annotated[WireNumber, Field(ge=0, le=1)]
    # Confirmed by no single frame alone
    points_confirmed_on_frames_together: Count
    # Refused: on the same sensor pixel while the scene moved
    points_fixed_to_sensor: Count
    streams_not_copied: Count

    @model_validator(mode="after")
    def _frames_of_the_clip(self) -> "ClipMeasurements":
        failures = []
        if self.frames_with_neighbours > self.frame_count:
            failures.append(M_CLIP_NEIGHBOURS)
        # REQ-RST-6.10: frames together are a frame and its registered neighbours
        if self.points_confirmed_on_frames_together > 0 and self.frames_with_neighbours == 0:
            failures.append(M_FRAMES_TOGETHER)
        _refuse(failures)
        return self


class RestorationMeasurements(_Record):
    colour: ColourDescription
    noise: Optional[NoiseTable] = None
    noise_not_measured: Optional[Literal[*NOISE_NOT_MEASURED_REASONS]] = None
    # The fine noise after over before, in the darkest quarter
    fine_noise_left: Optional[NonNegative] = None
    stars: Optional[StarCounts] = None
    dissolved_per_pass: Optional[Annotated[list[Count], Field(max_length=3)]] = None
    lift: Optional[Lift] = None
    clip: Optional[ClipMeasurements] = None
    peak_memory_bytes: Optional[Positive] = None
    notes: Annotated[list[Note], Field(max_length=8)]

    @model_validator(mode="after")
    def _noise_or_why_not(self) -> "RestorationMeasurements":
        _refuse([M_MEASURED_NOISE] if self.noise is not None and self.noise_not_measured is not None else [])
        return self


# =============================================================================================
# The attempt, the variant, the manifest
# =============================================================================================


class ProcessingAttempt(_Record):
    id: AttemptId
    source_sha256: Sha256
    graph_id: GraphId
    job_id: JobId
    status: Literal[*ATTEMPT_STATUSES]
    started_at_utc: UtcInstant = Field(alias="startedAtUTC")
    finished_at_utc: UtcInstant = Field(alias="finishedAtUTC")
    elapsed_ms: NonNegative
    cpu_ms: NonNegative
    variant_id: Optional[VariantId] = None
    measured: Optional[RestorationMeasurements] = None
    gate: Optional[IntegrityGateResult] = None
    rejection: Optional[Literal[*REJECTION_REASONS]] = None
    failure_reason: Optional[FailureReason] = None

    @model_validator(mode="after")
    def _fits_its_status(self) -> "ProcessingAttempt":
        failures = []
        variant, measured, rejection, failure = (
            self.variant_id is not None, self.measured is not None, self.rejection is not None,
            self.failure_reason is not None,
        )
        fits = {
            "ready": variant and measured and not rejection and not failure,
            "rejected": rejection and measured and not variant and not failure,
            "failed": failure and not variant and not rejection,
        }[self.status]
        if not fits:
            failures.append(_attempt_fit(self.status))
        gate = self.gate
        if self.status == "ready" and gate is not None and not gate.passed:
            failures.append(M_READY_PASSED)
        # REQ-RST-6.5, 6.6: what the gate refused is rejected; failed is for a job that could not finish
        if self.status == "failed" and gate is not None and not gate.passed:
            failures.append(M_REFUSED_IS_REJECTED)
        if self.rejection in GATE_REJECTIONS:
            if gate is None or gate.passed:
                failures.append(M_GATE_REJECTION)
            elif (self.rejection == "unconfirmed_points" and gate.unconfirmed_point_count == 0) or (
                self.rejection == "star_flux_changed" and gate.stars_outside_flux_tolerance == 0
            ):
                failures.append(M_REJECTION_NAMED)
        elif self.rejection is not None and gate is not None:
            failures.append(M_NO_GATE_BEFORE)
        stars = self.measured.stars if self.measured is not None else None
        if gate is not None and stars is not None and gate.stars_measured > stars.found:
            failures.append(M_GATE_STARS_FOUND)
        if _instant(self.finished_at_utc) < _instant(self.started_at_utc):
            failures.append(M_ATTEMPT_TIMES)
        _refuse(failures)
        return self


class VariantAudioStream(AudioStreamDescriptor):
    """An audio stream of a variant: the ingest shape, with the codec as ffprobe names it and the full packet hash.
    Strict, as every media record is: an unknown key or a Python field name is refused, not dropped."""

    model_config = WIRE_CONFIG
    codec: _pattern(MEDIA_PATTERNS["codecName"])
    sha256: Sha256


class MediaVariant(_Record):
    id: VariantId
    kind: VariantKind
    source_sha256: Sha256
    source_asset_id: SourceAssetId
    graph_id: GraphId
    attempt_id: AttemptId
    sha256: Sha256
    uri: _pattern(MEDIA_PATTERNS["variantUri"])
    byte_size: Positive
    mime_type: Literal[*VARIANT_MIME_TYPES]
    width: Positive
    height: Positive
    duration_ms: NonNegative
    frame_count: Positive
    # Of the time base and the integer frame times; a clip has it, a still not
    frame_times_sha256: Optional[Sha256] = None
    audio_streams: list[VariantAudioStream]
    colour: ColourDescription
    display_orientation: Optional[DisplayOrientation] = None
    disclosure: Disclosure
    created_at_utc: UtcInstant = Field(alias="createdAtUTC")

    @model_validator(mode="after")
    def _fits_its_kind_and_file(self) -> "MediaVariant":
        failures = []
        still = self.mime_type == "image/png"
        if self.disclosure != DISCLOSURES[self.kind]:
            failures.append(M_DISCLOSURE)
        if self.source_asset_id != f"src_{self.source_sha256[:16]}":
            failures.append(M_SOURCE_ASSET)
        if still and (self.frame_count != 1 or self.duration_ms != 0 or self.audio_streams or self.frame_times_sha256 is not None):
            failures.append(M_STILL)
        if not still and self.frame_times_sha256 is None:
            failures.append(M_CLIP_TIMES)
        if self.kind == "display_copy" and not still:
            failures.append(M_DISPLAY_COPY_STILL)
        if self.uri != variant_uri(self.source_sha256, self.id):
            failures.append(M_VARIANT_URI)
        if self.colour.model != ("rgb" if still else "ycbcr"):
            failures.append(M_VARIANT_COLOUR)
        _refuse(failures)
        return self


class ExportManifest(_Record):
    format: Literal["albedian.export-manifest"]
    format_version: Annotated[Literal[1], BeforeValidator(wire_int)]
    source_sha256: Sha256
    variant: MediaVariant
    graph: ProcessingGraph
    attempt: ProcessingAttempt
    observation_solution_id: Optional[SolutionId] = None
    disclosure: Disclosure

    @model_validator(mode="after")
    def _parts_agree(self) -> "ExportManifest":
        failures = []
        variant, graph, attempt = self.variant, self.graph, self.attempt
        if not self.source_sha256 == variant.source_sha256 == graph.source_sha256 == attempt.source_sha256:
            failures.append(M_MANIFEST_SOURCE)
        if (variant.graph_id != graph.id or attempt.graph_id != graph.id or variant.attempt_id != attempt.id
                or attempt.variant_id != variant.id or variant.kind != graph.kind):
            failures.append(M_MANIFEST_IDS)
        if attempt.status != "ready":
            failures.append(M_MANIFEST_READY)
        if self.disclosure != variant.disclosure:
            failures.append(M_MANIFEST_DISCLOSURE)
        if (attempt.gate is not None) != (graph.kind == "restoration"):
            failures.append(M_MANIFEST_GATE)
        measured = attempt.measured
        if graph.kind == "restoration" and measured is not None and (measured.noise is None) == (measured.noise_not_measured is None):
            failures.append(M_MANIFEST_NOISE)
        # REQ-RST-7.5: the variant has the original's frames, which the clip's measurements counted
        if measured is not None and measured.clip is not None and measured.clip.frame_count != variant.frame_count:
            failures.append(M_MANIFEST_FRAMES)
        if variant.mime_type == "image/png" and attempt.gate is not None and attempt.gate.frames_checked != 1:
            failures.append(M_STILL_GATE)
        # REQ-JOB-5.8: the solution of this upload is sol_ and its hash's first 16 hex characters
        if self.observation_solution_id is not None and self.observation_solution_id != f"sol_{self.source_sha256[:16]}":
            failures.append(M_SOLUTION)
        _refuse(failures)
        return self


class ExportArtifact(_Record):
    """What a session remembers of a download; the manifest is the record of how the file was made."""

    variant_id: VariantId
    variant_sha256: Sha256
    exported_at_utc: UtcInstant = Field(alias="exportedAtUTC")


MODELS = {
    "RestoreRequest": RestoreRequest,
    "GraphParameter": GraphParameter,
    "GraphStep": GraphStep,
    "ProcessingGraph": ProcessingGraph,
    "ColourDescription": ColourDescription,
    "NoiseTable": NoiseTable,
    "IntegrityGateResult": IntegrityGateResult,
    "RestorationMeasurements": RestorationMeasurements,
    "ProcessingAttempt": ProcessingAttempt,
    "MediaVariant": MediaVariant,
    "ExportManifest": ExportManifest,
    "ExportArtifact": ExportArtifact,
}


def python_names() -> dict[str, str]:
    """Each wire key of these records and the Python field name behind it, where the two differ."""
    names: dict[str, str] = {}
    seen: set[type] = set()

    def walk(model: Any) -> None:
        if model in seen:
            return
        seen.add(model)
        for name, field in model.model_fields.items():
            alias = field.alias or name
            if alias != name:
                assert names.get(alias, name) == name, alias
                names[alias] = name
            for argument in _model_types(field.annotation):
                walk(argument)

    for model in MODELS.values():
        walk(model)
    return dict(sorted(names.items()))


def _model_types(annotation: Any) -> list[Any]:
    found = []
    if isinstance(annotation, type) and issubclass(annotation, WireModel.__mro__[1]):
        found.append(annotation)
    for argument in getattr(annotation, "__args__", ()) or ():
        found.extend(_model_types(argument))
    return found


# =============================================================================================
# Shared samples for the TypeScript tests (synthetic values only: hashes typed or hashed from labels, dates in
# 2031, no upload behind any of them)
# =============================================================================================


def _label_hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _id(prefix: str, label: str, length: int = 16) -> str:
    return f"{prefix}_{_label_hash(label)[:length]}"


def _parameter(name: str, value: Any, origin: str, basis: str) -> dict[str, Any]:
    return {"name": name, "value": value, "origin": origin, "basis": basis}


def _step(kind: str, *parameters: dict[str, Any]) -> dict[str, Any]:
    return {"kind": kind, "version": "1.0.0", "parameters": list(parameters)}


TAGS = "Read from the file's own colour tags."
NOTE_DISSOLVED = NOTES[4]
NOTE_REGISTRATION = NOTES[3]
NOTE_BLACK = NOTES[1]


def build_media_samples() -> dict[str, Any]:
    still_sha = "5f2b0c9a7d31e4486a0be2f1c3d4e5f60718293a4b5c6d7e8f9a0b1c2d3e4f50"
    heic_sha = "3c4d5e6f7a8b9c0d" * 4
    clip_sha = "0a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f9"
    black_sha = "7e6f5a4b3c2d1e0f" * 4
    profile_sha = _label_hash("albedian synthetic test ICC profile")
    portrait = {"quarterTurnsClockwise": 1, "mirrored": False}
    audio = {"index": 1, "codec": "aac", "sampleRateHz": 48000, "channels": 2, "startPtsMs": 0, "durationMs": 12000,
             "sha256": _label_hash("albedian synthetic audio packets: clip")}

    requests = [{"kind": "display_copy"}, {"kind": "restoration"}, {"kind": "restoration", "exposureEv": 1.5}]
    parameters = [
        _parameter("matrix", "bt601", "measured", TAGS),
        _parameter("chromaSiting", "left", "default", "Proposed in design section 1."),
        _parameter("exposureEv", 1.5, "requested", "exposureEv"),
        _parameter("confirmThresholdSnr", 4.75, "default", "The detector's own floor."),
        _parameter("maxPasses", 3, "default", "Required by REQ-RST-6.3."),
        _parameter("bitDepth", 16, "default", "Decided by the lead in design section 8."),
        # A whole number written as a float is that integer on both sides
        _parameter("tilePx", 256.0, "default", "Proposed in design section 3."),
        _parameter("transfer", "hlg", "default", "Verified at its source in design section 1."),
    ]
    decode_heic = _step("decode", parameters[0], _parameter("transfer", "srgb", "measured", TAGS), parameters[1])
    encode_png8 = _step(
        "encode",
        _parameter("container", "png", "default", "Proposed in design section 8."),
        _parameter("bitDepth", 8, "measured", "Read from the decoded original."),
        _parameter("zlibLevel", 6, "default", "Proposed in design section 8."),
    )
    find_stars = _step("find_stars", parameters[3], _parameter("tilePx", 256, "default", "Proposed in design section 3."))
    measure_noise = _step("measure_noise", _parameter("bins", 12, "default", "Proposed in design section 4."))
    spatial = _step("spatial_denoise", _parameter("sigmaScale", 1.0, "measured", "Measured on this file's noise table."))
    dissolve = _step("dissolve_unconfirmed", parameters[4])
    solved = "Solved for this file from its median and its measured bounds."
    lift_still = _step("lift", _parameter("gainAtBlack", 4.2, "measured", solved))
    encode_png16 = _step(
        "encode",
        _parameter("container", "png", "default", "Decided by the lead in design section 8."),
        parameters[5],
    )
    decode_clip = _step("decode", _parameter("pixelFormat", "yuv420p", "measured", "Read from the stream's own pixel format."))
    register = _step("register", _parameter("windowFrames", 3, "default", "Proposed in design section 10."))
    merge = _step("temporal_merge", _parameter("windowPx", 5, "default", "Proposed in design section 10."))
    lift_clip = _step("lift", parameters[2])
    encode_h264 = _step(
        "encode",
        _parameter("encoder", "libx264", "default", "Required by REQ-RST-7.10."),
        _parameter("crf", 16, "default", "Proposed in design section 10."),
        _parameter("preset", "medium", "default", "Proposed in design section 10."),
    )
    copy_audio = _step("copy_audio", _parameter("streams", 1, "measured", "Counted from the asset record's audio streams."))

    graph_display = {"id": _id("graph", "display copy"), "sourceSha256": heic_sha, "kind": "display_copy",
                     "pipelineVersion": "1.0.0", "request": requests[0], "steps": [decode_heic, encode_png8]}
    graph_still = {"id": _id("graph", "still restoration"), "sourceSha256": still_sha, "kind": "restoration",
                   "pipelineVersion": "1.0.0", "request": requests[1],
                   "steps": [_step("decode", _parameter("transfer", "icc_parametric", "measured", "Read from the file's ICC profile.")),
                             find_stars, measure_noise, spatial, dissolve, lift_still, encode_png16]}
    graph_clip = {"id": _id("graph", "clip restoration"), "sourceSha256": clip_sha, "kind": "restoration",
                  "pipelineVersion": "1.0.0", "request": requests[2],
                  "steps": [decode_clip, find_stars, measure_noise, register, merge, spatial, dissolve, lift_clip,
                            encode_h264, copy_audio]}
    graph_black = {"id": _id("graph", "black clip restoration"), "sourceSha256": black_sha, "kind": "restoration",
                   "pipelineVersion": "1.0.0", "request": requests[1],
                   "steps": [decode_clip, find_stars, measure_noise, _step("lift", _parameter("gainAtBlack", 1.6, "measured", solved)),
                             encode_h264, copy_audio]}

    colour_heic = {"model": "ycbcr", "bitDepth": 8, "range": "full", "matrix": "bt601", "transfer": "srgb",
                   "primaries": "p3_d65", "chromaSubsampling": "420", "chromaSiting": "left",
                   "assumptions": [dict(ASSUMPTION_REASONS[7])]}
    colour_png8 = {"model": "rgb", "bitDepth": 8, "range": "full", "matrix": "identity", "transfer": "srgb",
                   "primaries": "p3_d65", "chromaSubsampling": "444", "assumptions": [dict(ASSUMPTION_REASONS[4])]}
    colour_jpeg = {"model": "rgb", "bitDepth": 8, "range": "full", "matrix": "identity", "transfer": "icc_parametric",
                   "primaries": "icc", "chromaSubsampling": "444", "iccProfileSha256": profile_sha, "assumptions": []}
    colour_png16 = {**colour_jpeg, "bitDepth": 16}
    colour_clip = {"model": "ycbcr", "bitDepth": 8, "range": "limited", "matrix": "bt2020nc", "transfer": "hlg",
                   "primaries": "bt2020", "chromaSubsampling": "420", "chromaSiting": "left", "assumptions": []}

    noise_still = {"intensities": [0.002, 0.01, 0.05, 0.2],
                   "sigma": {"r": [0.0011, 0.0019, 0.0042, 0.0081], "g": [0.0009, 0.0016, 0.0037, 0.0075],
                             "b": [0.0014, 0.0023, 0.0049, 0.0092]},
                   "blockCounts": [41, 63, 120, 30]}
    noise_clip = {"intensities": [0.001, 0.004, 0.02],
                  "sigma": {"r": [0.0007, 0.0012, 0.003], "g": [0.0006, 0.001, 0.0027], "b": [0, 0.0015, 0.0033]},
                  "blockCounts": [210, 95, 12]}

    gate_passed = {"statistic": "detector_local_background_removed", "variantThresholdSnr": 6, "confirmThresholdSnr": 4.75,
                   "tilePx": 256, "matchRadiusPx": 1.5, "framesChecked": 1, "variantPointCount": 37,
                   "unconfirmedPointCount": 0, "framesWithUnconfirmed": 0, "clippedPointsLeftOut": 2, "starsMeasured": 37,
                   "starsOutsideFluxTolerance": 0, "fluxToleranceSigma": 3, "worstFluxChangeSigma": 1.8,
                   "medianFluxRatio": 0.99, "passed": True}
    gate_unconfirmed = {**gate_passed, "variantPointCount": 38, "unconfirmedPointCount": 1, "framesWithUnconfirmed": 1,
                        "passed": False}
    gate_flux = {**gate_passed, "starsOutsideFluxTolerance": 2, "worstFluxChangeSigma": 5.1, "passed": False}
    gate_no_stars = {k: v for k, v in gate_passed.items() if k not in ("worstFluxChangeSigma", "medianFluxRatio")}
    gate_no_stars.update(variantPointCount=0, starsMeasured=0, clippedPointsLeftOut=0)
    gate_clip = {**gate_passed, "statistic": "detector", "framesChecked": 360, "variantPointCount": 1200,
                 "clippedPointsLeftOut": 31, "starsMeasured": 900, "worstFluxChangeSigma": 2.2, "medianFluxRatio": 1.01}
    # A JSON number written 1.0 is the integer 1 on both sides, and minus zero is zero
    gate_written_as_floats = {**gate_passed, "tilePx": 256.0, "framesChecked": 1.0, "variantPointCount": 37.0,
                              "clippedPointsLeftOut": -0.0, "worstFluxChangeSigma": 1.8, "variantThresholdSnr": 6.0}
    gate_black = {**gate_no_stars, "statistic": "detector", "framesChecked": 180}
    # A star whose change reaches the tolerance is within it
    gate_at_tolerance = {**gate_passed, "worstFluxChangeSigma": 3}

    measured_still = {"colour": colour_jpeg, "noise": noise_still, "fineNoiseLeft": 0.42, "stars": {"found": 40, "confirmed": 37},
                      "dissolvedPerPass": [3, 0], "lift": {"gainAtBlack": 4.2, "decidedBy": "noise_bound"},
                      "peakMemoryBytes": 734003200, "notes": [NOTE_DISSOLVED]}
    measured_display = {"colour": colour_heic, "notes": []}
    clip_counts = {"frameCount": 360, "framesWithNeighbours": 352, "medianClosureErrorPx": 0.13, "exactRepeatShare": 0.37,
                   "pointsConfirmedOnFramesTogether": 85, "pointsFixedToSensor": 4, "streamsNotCopied": 0}
    measured_clip = {"colour": colour_clip, "noise": noise_clip, "fineNoiseLeft": 0.61,
                     "stars": {"found": 1500, "confirmed": 1200}, "dissolvedPerPass": [12, 1, 0],
                     "lift": {"gainAtBlack": 2.8, "decidedBy": "requested"}, "clip": clip_counts,
                     "peakMemoryBytes": 512000000, "notes": [NOTE_REGISTRATION, NOTE_DISSOLVED]}
    measured_black = {"colour": colour_clip, "noiseNotMeasured": "fine_detail_mostly_zero",
                      "stars": {"found": 0, "confirmed": 0}, "lift": {"gainAtBlack": 1.6, "decidedBy": "quantisation_bound"},
                      "clip": {**clip_counts, "frameCount": 180, "framesWithNeighbours": 0, "exactRepeatShare": 0.96,
                               "pointsConfirmedOnFramesTogether": 0, "pointsFixedToSensor": 0},
                      "notes": [NOTE_BLACK]}

    variant_still_id, variant_display_id = _id("var", "still restoration"), _id("var", "display copy")
    variant_clip_id, variant_black_id = _id("var", "clip restoration"), _id("var", "black clip restoration")

    def attempt(label: str, graph: dict, status: str, **fields: Any) -> dict[str, Any]:
        return {"id": _id("att", label), "sourceSha256": graph["sourceSha256"], "graphId": graph["id"],
                "jobId": _id("job", label, 32), "status": status, "startedAtUTC": "2031-05-02T19:20:01Z",
                "finishedAtUTC": "2031-05-02T19:20:07.500Z", "elapsedMs": 6500, "cpuMs": 6100, **fields}

    attempts = [
        attempt("still restoration", graph_still, "ready", variantId=variant_still_id, measured=measured_still, gate=gate_passed),
        attempt("display copy", graph_display, "ready", variantId=variant_display_id, measured=measured_display),
        attempt("clip restoration", graph_clip, "ready", variantId=variant_clip_id, measured=measured_clip, gate=gate_clip),
        attempt("black clip restoration", graph_black, "ready", variantId=variant_black_id, measured=measured_black,
                gate=gate_black),
        attempt("painted star", graph_still, "rejected", measured=measured_still, gate=gate_unconfirmed,
                rejection="unconfirmed_points"),
        attempt("star flux", graph_still, "rejected", measured=measured_still, gate=gate_flux, rejection="star_flux_changed"),
        attempt("frame times", graph_clip, "rejected", measured=measured_clip, rejection="frame_times_differ"),
        attempt("audio", graph_clip, "rejected", measured=measured_clip, rejection="audio_differs"),
        attempt("copy differs", graph_display, "rejected", measured=measured_display, rejection="copy_differs"),
        attempt("time limit", graph_still, "failed", failureReason="The restoration stopped at its time limit."),
        # Refused within the clock's millisecond: it finishes when it starts
        {**attempt("no decode", graph_still, "failed", failureReason="The file could not be decoded."),
         "finishedAtUTC": "2031-05-02T19:20:01Z", "elapsedMs": 0, "cpuMs": 0},
        # The two sentences with a slot, and the runner's for a stopped server; an old solve job's id of 16 hex
        attempt("interlaced", graph_clip, "failed", failureReason="This kind of file is not supported yet: an interlaced clip."),
        {**attempt("ten bit", graph_clip, "failed", failureReason="This ffmpeg cannot do what this file needs: 10 bit HEVC encoding of yuv420p10le."),
         "jobId": _id("job", "ten bit")},
        attempt("stopped server", graph_still, "failed", failureReason="The server stopped before this job finished."),
        # A gate that passed, then a failure while linking the variant into place
        attempt("link failed", graph_still, "failed", measured=measured_still, gate=gate_passed,
                failureReason="The restoration stopped on an internal error."),
    ]

    def variant(variant_id: str, kind: str, graph: dict, attempt_record: dict, **fields: Any) -> dict[str, Any]:
        sha = graph["sourceSha256"]
        return {"id": variant_id, "kind": kind, "sourceSha256": sha, "sourceAssetId": f"src_{sha[:16]}", "graphId": graph["id"],
                "attemptId": attempt_record["id"], "sha256": _label_hash(f"albedian synthetic variant bytes: {variant_id}"),
                "uri": variant_uri(sha, variant_id), **fields, "disclosure": DISCLOSURES[kind],
                "createdAtUTC": "2031-05-02T19:20:07.500Z"}

    still_file = {"mimeType": "image/png", "durationMs": 0, "frameCount": 1, "audioStreams": []}
    variants = [
        variant(variant_still_id, "restoration", graph_still, attempts[0], byteSize=46137344, **still_file, width=4032,
                height=3024, colour=colour_png16),
        variant(variant_display_id, "display_copy", graph_display, attempts[1], byteSize=17825792, **still_file, width=3024,
                height=4032, colour=colour_png8, displayOrientation=portrait),
        variant(variant_clip_id, "restoration", graph_clip, attempts[2], byteSize=52811776, mimeType="video/quicktime",
                width=1080, height=1920, durationMs=12000, frameCount=360,
                frameTimesSha256=_label_hash("albedian synthetic frame times: clip"), audioStreams=[audio],
                colour=colour_clip, displayOrientation=portrait),
        variant(variant_black_id, "restoration", graph_black, attempts[3], byteSize=20971520, mimeType="video/mp4",
                width=1920, height=1080, durationMs=6000, frameCount=180,
                frameTimesSha256=_label_hash("albedian synthetic frame times: black clip"), audioStreams=[],
                colour=colour_clip),
    ]

    def manifest(variant_record: dict, graph: dict, attempt_record: dict, **fields: Any) -> dict[str, Any]:
        return {"format": EXPORT_MANIFEST_FORMAT, "formatVersion": EXPORT_MANIFEST_FORMAT_VERSION,
                "sourceSha256": graph["sourceSha256"], "variant": variant_record, "graph": graph, "attempt": attempt_record,
                **fields, "disclosure": variant_record["disclosure"]}

    manifests = [
        manifest(variants[0], graph_still, attempts[0], observationSolutionId=f"sol_{still_sha[:16]}"),
        manifest(variants[1], graph_display, attempts[1]),
        manifest(variants[2], graph_clip, attempts[2], observationSolutionId=f"sol_{clip_sha[:16]}"),
        manifest(variants[3], graph_black, attempts[3]),
    ]
    exports = [
        # A leap day, the last of its month; and one in a year divisible by 400
        {"variantId": variant_still_id, "variantSha256": variants[0]["sha256"], "exportedAtUTC": "2032-02-29T19:25:00Z"},
        {"variantId": variant_still_id, "variantSha256": variants[0]["sha256"], "exportedAtUTC": "2000-02-29T19:25:00Z"},
        # The first day the form can write
        {"variantId": variant_still_id, "variantSha256": variants[0]["sha256"], "exportedAtUTC": "0001-01-01T00:00:00Z"},
    ]

    valid = {
        "RestoreRequest": requests,
        "GraphParameter": parameters,
        "GraphStep": [decode_heic, encode_png8, find_stars, lift_clip, copy_audio, encode_h264],
        "ProcessingGraph": [graph_display, graph_still, graph_clip, graph_black],
        "ColourDescription": [colour_heic, colour_png8, colour_jpeg, colour_png16, colour_clip],
        "NoiseTable": [noise_still, noise_clip],
        "IntegrityGateResult": [gate_passed, gate_unconfirmed, gate_flux, gate_no_stars, gate_clip, gate_written_as_floats,
                                gate_black, gate_at_tolerance],
        "RestorationMeasurements": [measured_still, measured_display, measured_clip, measured_black],
        "ProcessingAttempt": attempts,
        "MediaVariant": variants,
        "ExportManifest": manifests,
        "ExportArtifact": exports,
    }
    return {
        "patterns": MEDIA_PATTERNS,
        "idPatterns": MEDIA_ID_PATTERNS,
        "jobIdPattern": JOB_ID_PATTERN,
        "enums": MEDIA_ENUMS,
        "disclosures": DISCLOSURES,
        "vocabulary": MEDIA_VOCABULARY,
        "messages": list(REFINEMENT_MESSAGES),
        "pythonNames": python_names(),
        "privacyProbes": list(PRIVACY_PROBES),
        "addedKeys": {"keys": list(ADDED_KEYS), "value": ADDED_KEY_VALUE, "records": _added_key_records(valid)},
        "valid": valid,
        "invalid": _invalid_samples(valid),
        "raw": _raw_samples(valid),
    }


# Keys each side adds to every object of every valid sample, embedded shapes included, beside each wire key's Python
# name: AUDIT lane B's (verdict restore-1-v2, item 1). Every such record is refused on both sides.
ADDED_KEYS = ("extra", "fileName", "constructor", "__proto__")
ADDED_KEY_VALUE = "/Users/me/Pictures/IMG_0001.MOV"


def _added_key_records(valid: dict[str, list[dict[str, Any]]]) -> int:
    """How many records the added-key tests build, so both sides are seen to walk the same ones."""
    names = python_names()

    def count(node: Any) -> int:
        if isinstance(node, dict):
            here = len(ADDED_KEYS) + sum(key in names for key in node)
            return here + sum(count(value) for value in node.values())
        if isinstance(node, list):
            return sum(count(value) for value in node)
        return 0

    return sum(count(record) for records in valid.values() for record in records)


# The forms AUDIT lane C passed through the free sentences of the first version (verdict restore-1, finding 4): paths,
# media, sidecar and record file names, dates, clock times, coordinates and degree signs. Every one is refused in every
# text slot now, as any text outside a slot's list is. All synthetic: no file, time or place of anyone.
PRIVACY_PROBES = (
    # The audit's plain control sentence: no private form, and still refused, as any text outside a slot's list is
    "The sky was clear over the hills.",
    "Saved in Users/me/Pictures", "Saved in Users／me／Pictures", "Saved in Users∕me∕Pictures",
    "Saved in Users⁄me", "Saved under ~ Pictures", "file:Users",
    "From IMG_1234.JPG", "From IMG_1234.HEIC", "From IMG_1234.CR3", "From IMG_1234.CR2", "From DSC_1234.NEF",
    "From DSC01234.ARW", "From DSCF1234.RAF", "From P1234.ORF", "From P1234.RW2", "From photo.webp", "From photo.gif",
    "From photo.bmp", "From clip.3gp", "From 00001.MTS", "From 00001.m2ts", "From VID_1234.insv", "From GL010001.LRV",
    "From IMG_1234.xmp", "From IMG_1234.AAE", "From solve.jsonl", "From records.ndjson", "From records.csv",
    "From job.yaml", "From store.sqlite", "From IMG_1234．HEIC", "From IMG_1234 .HEIC", "From IMG_1234",
    "From PXL_20311111_221500123",
    "On 2031-11-11", "On 2031:11:11", "On 2031.11.11", "On 11.11.2031", "On 20311111", "On 2031_11_11", "On 2031 11 11",
    "In 2031-11", "On 11 November 2031", "On ２０３１-１１-１１", "On 2031‐11‐11",
    "At 20311111T221500Z",
    "At 22:15", "At 22:15:30", "At 22h15", "At 22.15", "At 2215Z", "At 22∶15", "At 22：15", "At 10 15 pm",
    "At 123:45",
    "At 48.8767, 123.3933", "At -48.8767,-123.3933", "At 48.8767 123.3933", "At 48.876, 123.393", "At 48.8767;123.3933",
    "At 48.8767,\t123.3933", "At 48.8767 S 123.3933 E", "At 48,8767; 123,3933", "At +48.8767+123.3933/",
    "At +48.8767+123.3933", "At 2C5F4H8M+QX", "At 48 52 36 S 123 23 36 W",
    "At 48° S", "At 48º S", "At 48˚ S", "At 48⁰ S", "At 48∘ S", "At 48d52m S",
    # A lone surrogate, which a strict JSON reader refuses and JavaScript keeps
    "Read from the file\ud800s own colour tags.",
)


def _invalid_samples(valid: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """One or more refused samples per rule, each with one fault and, for a refinement, the message it is refused with."""
    invalid: list[dict[str, Any]] = []

    def bad(schema: str, sample: Any, reason: str, message: str | None = None) -> None:
        invalid.append({"schema": schema, "sample": sample, "reason": reason, "message": message})

    def edit(base: dict, path: list, value: Any) -> dict:
        out = copy.deepcopy(base)
        target = out
        for key in path[:-1]:
            target = target[key]
        if value is _DROP:
            del target[path[-1]]
        else:
            target[path[-1]] = value
        return out

    request, parameter, step = valid["RestoreRequest"][1], valid["GraphParameter"][0], valid["GraphStep"][0]
    graph_display, graph_still = valid["ProcessingGraph"][0], valid["ProcessingGraph"][1]
    heic, png8, jpeg, clip_colour = (valid["ColourDescription"][i] for i in (0, 1, 2, 4))
    noise, gate = valid["NoiseTable"][0], valid["IntegrityGateResult"][0]
    measured, measured_clip = valid["RestorationMeasurements"][0], valid["RestorationMeasurements"][2]
    attempts, variants = valid["ProcessingAttempt"], valid["MediaVariant"]
    ready, ready_display, rejected, failed = attempts[0], attempts[1], attempts[4], attempts[9]
    still_variant, display_variant, clip_variant = variants[0], variants[1], variants[2]
    manifest, display_manifest, clip_manifest = (valid["ExportManifest"][i] for i in (0, 1, 2))
    export = valid["ExportArtifact"][0]
    tile = valid["GraphParameter"][6]

    bad("RestoreRequest", {"kind": "display_copy", "exposureEv": 1}, "a display copy takes no option", M_REQUEST_OPTION)
    bad("RestoreRequest", {"kind": "restoration", "exposureEv": 6.5}, "more exposure than the request may ask for")
    bad("RestoreRequest", {"kind": "restoration", "exposureEv": -0.5}, "a negative exposure")
    bad("RestoreRequest", {"kind": "original"}, "an unknown kind")
    bad("RestoreRequest", {**request, "profile": "natural"}, "an unknown key: the old profile")
    bad("RestoreRequest", {"kind": "restoration", "exposureEv": None}, "null where the option may only be absent")
    bad("RestoreRequest", {"kind": "restoration", "exposure_ev": 1.5}, "the option under its Python name")
    bad("RestoreRequest", {"kind": "restoration", "exposureEv": True}, "an exposure that is a boolean")

    bad("GraphParameter", edit(parameter, ["origin"], _DROP), "a parameter with no origin")
    bad("GraphParameter", edit(parameter, ["origin"], "guessed"), "an origin that is not measured, requested or default")
    bad("GraphParameter", edit(parameter, ["name"], "zlib.level"), "a parameter name that is not in the table")
    bad("GraphParameter", edit(parameter, ["name"], "Matrix"), "a parameter name in capitals")
    bad("GraphParameter", edit(parameter, ["name"], "d20311111t221500"), "a parameter name holding a date and a time")
    bad("GraphParameter", edit(parameter, ["value"], "bt2100"), "a text value its row does not list", M_PARAMETER_VALUE)
    bad("GraphParameter", edit(parameter, ["value"], ""), "an empty text value", M_PARAMETER_VALUE)
    bad("GraphParameter", edit(parameter, ["value"], 601), "a number for a text row", M_PARAMETER_VALUE)
    bad("GraphParameter", edit(parameter, ["value"], True), "a boolean value: no row is a boolean", M_PARAMETER_VALUE)
    passes = valid["GraphParameter"][4]
    bad("GraphParameter", edit(passes, ["value"], True), "a boolean where a range holds one", M_PARAMETER_VALUE)
    bad("GraphParameter", edit(tile, ["value"], 4097), "a tile one above its row's range", M_PARAMETER_VALUE)
    bad("GraphParameter", edit(tile, ["value"], 15), "a tile one below its row's range", M_PARAMETER_VALUE)
    bad("GraphParameter", edit(tile, ["value"], 256.5), "a fractional tile", M_PARAMETER_VALUE)
    bad("GraphParameter", edit(tile, ["value"], "256"), "a tile written as text", M_PARAMETER_VALUE)
    bad("GraphParameter", edit(tile, ["value"], 2**53 + 1), "a tile past the safe integers", M_PARAMETER_VALUE)
    bad("GraphParameter", edit(parameter, ["value"], None), "a parameter with no value")
    bad("GraphParameter", edit(parameter, ["value"], [1, 2]), "a list as a value")
    bad("GraphParameter", edit(parameter, ["basis"], ""), "a parameter with no basis", M_BASIS)
    bad("GraphParameter", edit(parameter, ["basis"], "Proposed in design section 8."), "a source as the basis of a measured value",
        M_BASIS)
    bad("GraphParameter", edit(tile, ["basis"], TAGS), "a measurement as the basis of a default", M_BASIS)
    bad("GraphParameter", edit(tile, ["basis"], "Proposed in design section 19."), "a section the design does not have", M_BASIS)
    bad("GraphParameter", edit(tile, ["basis"], "Proposed in design section 0."), "section 0", M_BASIS)
    bad("GraphParameter", edit(tile, ["basis"], "Proposed in design section 08."), "a section with a leading zero", M_BASIS)
    bad("GraphParameter", edit(tile, ["basis"], "Proposed in design section 2031."), "a year in the section slot", M_BASIS)
    bad("GraphParameter", edit(tile, ["basis"], "Proposed in design section 3"), "a template without its full stop", M_BASIS)
    bad("GraphParameter", edit(tile, ["basis"], "Proposed in design section 3. "), "a template with a trailing space", M_BASIS)
    bad("GraphParameter", edit(tile, ["basis"], "Required by REQ-RST-6.14."), "a criterion past the last", M_BASIS)
    bad("GraphParameter", edit(tile, ["basis"], "Required by REQ-RST-13.1."), "a requirement past the last", M_BASIS)
    # AUDIT lane B's case: two numbers that read together as a month and day or a clock time, and name no criterion
    bad("GraphParameter", edit(tile, ["basis"], "Required by REQ-RST-11.11."), "a criterion REQ-RST-11 does not have", M_BASIS)
    bad("GraphParameter", edit(tile, ["basis"], "Required by REQ-RST-1.8."), "a criterion REQ-RST-1 does not have", M_BASIS)
    bad("GraphParameter", edit(tile, ["basis"], "Required by REQ-RST-6."), "a requirement without its criterion", M_BASIS)
    bad("GraphParameter", edit(tile, ["basis"], "Required by REQ-RST-06.3."), "a criterion with a leading zero", M_BASIS)
    bad("GraphParameter", edit(valid["GraphParameter"][2], ["basis"], "exposure"), "a request field the request does not have",
        M_BASIS)
    bad("GraphParameter", edit(parameter, ["basis"], "Measured on the file at /srv/albedian/store."), "a path in the basis", M_BASIS)

    bad("GraphStep", edit(step, ["parameters"], [*step["parameters"], {**step["parameters"][0], "basis": "Read from the decoded original."}]),
        "a parameter named twice", M_PARAMETER_ONCE)
    bad("GraphStep", edit(step, ["parameters"], [*step["parameters"], tile]), "a decode with a parameter of find_stars",
        M_PARAMETER_STEP)
    bad("GraphStep", edit(step, ["version"], "1.0"), "a version that is not major.minor.patch")
    bad("GraphStep", edit(step, ["version"], "١.٠.٠"), "a version in digits other than ASCII ones")
    bad("GraphStep", edit(step, ["version"], "2031.5.2"), "a version holding a year")
    bad("GraphStep", edit(step, ["version"], "1.05.0"), "a version part with a leading zero")
    bad("GraphStep", edit(step, ["kind"], "deblock"), "a step kind the contract does not list")
    bad("GraphStep", edit(step, ["kind"], "source_decode"), "the old operation kind")

    bad("ProcessingGraph", edit(graph_still, ["request"], {"kind": "display_copy"}), "the graph's kind differs from its request's", M_GRAPH_KIND)
    bad("ProcessingGraph", edit(graph_still, ["steps"], graph_still["steps"][1:]), "a graph whose first step is not decode", M_FIRST_DECODE)
    steps = graph_still["steps"]
    bad("ProcessingGraph", edit(graph_still, ["steps"], [steps[1], steps[0], *steps[2:]]), "a decode that is not the first step",
        M_FIRST_DECODE)
    bad("ProcessingGraph", edit(graph_display, ["steps"], [graph_display["steps"][0], graph_still["steps"][5], graph_display["steps"][1]]),
        "a display copy with a lift", M_DISPLAY_COPY_STEPS)
    bad("ProcessingGraph", edit(graph_display, ["steps"], [*graph_display["steps"], valid["GraphStep"][4]]),
        "a display copy with a step after its encode", M_DISPLAY_COPY_STEPS)
    bad("ProcessingGraph", edit(graph_still, ["steps"], [steps[0], steps[1], steps[2], steps[5], steps[3], steps[4], steps[6]]),
        "a lift before the noise reduction", M_PIPELINE_ORDER)
    bad("ProcessingGraph", edit(graph_still, ["steps"], [*steps[:6], steps[5], steps[6]]), "a lift twice", M_PIPELINE_ORDER)
    bad("ProcessingGraph", edit(graph_still, ["steps"], [*steps[:5], steps[6]]), "a restoration with no lift", M_LIFT_AND_ENCODE)
    bad("ProcessingGraph", edit(graph_still, ["steps"], steps[:6]), "a restoration with no encode", M_LIFT_AND_ENCODE)
    bad("ProcessingGraph", edit(graph_still, ["steps"], steps[:1]), "a graph of one step")
    bad("ProcessingGraph", edit(graph_still, ["id"], "graph_123"), "a malformed graph id")
    bad("ProcessingGraph", edit(graph_still, ["id"], graph_still["id"] + "\n"), "a graph id with a trailing newline")
    bad("ProcessingGraph", {**graph_still, "profileName": "natural"}, "a removed field: the profile name")
    bad("ProcessingGraph", {**graph_still, "fidelityPolicy": {"allowSyntheticSky": False}}, "a removed field: the fidelity policy")
    bad("ProcessingGraph", edit(graph_still, ["pipelineVersion"], "v1"), "a pipeline version that is not major.minor.patch")
    bad("ProcessingGraph", {k: v for k, v in graph_still.items() if k != "sourceSha256"} | {"source_sha256": graph_still["sourceSha256"]},
        "a key under its Python name")

    bad("ColourDescription", edit(jpeg, ["matrix"], "bt709"), "R'G'B' with a matrix", M_RGB)
    bad("ColourDescription", edit(png8, ["chromaSubsampling"], "420") | {"chromaSiting": "left"}, "R'G'B' with subsampled chroma", M_RGB)
    bad("ColourDescription", edit(clip_colour, ["matrix"], "identity"), "Y'CbCr with no matrix", M_YCBCR)
    bad("ColourDescription", edit(clip_colour, ["chromaSiting"], _DROP), "subsampled chroma with no siting", M_SITING)
    bad("ColourDescription", {**png8, "chromaSiting": "left"}, "a siting for chroma that is not subsampled", M_SITING)
    bad("ColourDescription", edit(png8, ["bitDepth"], 10), "R'G'B' at 10 bits", M_BIT_DEPTH)
    bad("ColourDescription", edit(clip_colour, ["bitDepth"], 16), "Y'CbCr at 16 bits", M_BIT_DEPTH)
    bad("ColourDescription", edit(jpeg, ["iccProfileSha256"], _DROP), "an ICC description with no profile hash", M_PROFILE)
    bad("ColourDescription", edit(png8, ["primaries"], "icc"), "primaries read from an ICC profile with no profile hash", M_PROFILE)
    bad("ColourDescription", edit(heic, ["chromaSiting"], "center"), "an assumption the description does not hold", M_ASSUMED_VALUE)
    bad("ColourDescription", edit(heic, ["assumptions"], [*heic["assumptions"], dict(heic["assumptions"][0])]), "a field assumed twice",
        M_ASSUMED_ONCE)
    wrong_range = dict(ASSUMPTION_REASONS[0])
    bad("ColourDescription", edit(heic, ["assumptions"], [*heic["assumptions"], wrong_range]),
        "one of two assumptions the description does not hold", M_ASSUMED_VALUE)
    bad("ColourDescription", edit(heic, ["assumptions", 0, "reason"], "Assumed at 19:30 on the night it was taken."),
        "a clock time in a reason", M_ASSUMPTION_REASON)
    bad("ColourDescription", edit(heic, ["assumptions", 0, "reason"], ASSUMPTION_REASONS[0]["reason"]),
        "the reason of another field and value", M_ASSUMPTION_REASON)
    bad("ColourDescription", edit(heic, ["assumptions", 0, "assumed"], "nearby"), "an assumed value no field can hold")
    bad("ColourDescription", edit(png8, ["bitDepth"], 12), "a bit depth the contract does not list")
    bad("ColourDescription", edit(png8, ["bitDepth"], 8.5), "a fractional bit depth")
    bad("ColourDescription", edit(png8, ["bitDepth"], True), "a bit depth that is a boolean")

    bad("NoiseTable", edit(noise, ["sigma", "g"], noise["sigma"]["g"][:-1]), "a sigma column one short", M_NOISE_COLUMNS)
    bad("NoiseTable", edit(noise, ["blockCounts"], [*noise["blockCounts"], 9]), "a block count too many", M_NOISE_COLUMNS)
    bad("NoiseTable", edit(noise, ["intensities"], [0.002, 0.05, 0.01, 0.2]), "intensities out of order", M_NOISE_INCREASING)
    bad("NoiseTable", edit(noise, ["intensities"], [0.002, 0.01, 0.01, 0.2]), "an intensity twice", M_NOISE_INCREASING)
    bad("NoiseTable", edit(noise, ["intensities", 3], 1.5), "an intensity above full scale")
    bad("NoiseTable", {"intensities": [0.1, 0.2], "sigma": {"r": [0.1, 0.1], "g": [0.1, 0.1], "b": [0.1, 0.1]}, "blockCounts": [3, 3]},
        "two bins")
    bad("NoiseTable", edit(noise, ["blockCounts", 0], 0), "a bin with no block")
    bad("NoiseTable", edit(noise, ["blockCounts", 0], 2**53), "a block count past the safe integers")

    bad("IntegrityGateResult", edit(gate, ["passed"], False), "a gate that refused nothing yet did not pass", M_GATE_PASSED)
    bad("IntegrityGateResult", {**gate, "variantPointCount": 38, "unconfirmedPointCount": 1, "framesWithUnconfirmed": 1},
        "a gate that passed with an unconfirmed point", M_GATE_PASSED)
    bad("IntegrityGateResult", {**gate, "variantPointCount": 0, "unconfirmedPointCount": 1, "framesWithUnconfirmed": 1, "passed": False},
        "more unconfirmed points than points", M_GATE_POINTS)
    bad("IntegrityGateResult", {**gate, "unconfirmedPointCount": 2, "framesWithUnconfirmed": 2, "passed": False},
        "more frames with unconfirmed points than frames checked", M_GATE_FRAMES)
    bad("IntegrityGateResult", {**gate, "unconfirmedPointCount": 1, "passed": False}, "an unconfirmed point in no frame", M_GATE_ANY_FRAME)
    bad("IntegrityGateResult", {**gate, "framesChecked": 3, "unconfirmedPointCount": 1, "framesWithUnconfirmed": 2, "passed": False},
        "more frames than unconfirmed points", M_GATE_FRAME_POINTS)
    bad("IntegrityGateResult", {**gate, "starsMeasured": 1, "starsOutsideFluxTolerance": 2, "worstFluxChangeSigma": 5.1, "passed": False},
        "more stars outside the tolerance than stars measured", M_GATE_STARS)
    bad("IntegrityGateResult", edit(gate, ["worstFluxChangeSigma"], _DROP), "stars measured with no worst change", M_GATE_FLUX_GIVEN)
    bad("IntegrityGateResult", edit(gate, ["medianFluxRatio"], _DROP), "stars measured with no median ratio", M_GATE_FLUX_GIVEN)
    bad("IntegrityGateResult", {**valid["IntegrityGateResult"][3], "worstFluxChangeSigma": 0.5}, "a worst change with no star measured",
        M_GATE_FLUX_GIVEN)
    bad("IntegrityGateResult", edit(gate, ["worstFluxChangeSigma"], 3.5), "a worst change past the tolerance with no star outside it",
        M_GATE_WORST)
    bad("IntegrityGateResult", {**gate, "starsOutsideFluxTolerance": 1, "passed": False}, "a star outside a tolerance the worst change keeps",
        M_GATE_WORST)
    bad("IntegrityGateResult", edit(gate, ["statistic"], "sep"), "a statistic the contract does not list")
    bad("IntegrityGateResult", edit(gate, ["tilePx"], 256.5), "a fractional tile")
    bad("IntegrityGateResult", edit(gate, ["framesChecked"], 0), "no frame checked")
    bad("IntegrityGateResult", edit(gate, ["variantPointCount"], True), "a count that is a boolean")
    bad("IntegrityGateResult", edit(gate, ["passed"], 1), "a pass that is a number")
    for value, reason in ((2**53, "2^53"), (2**53 + 1, "2^53 + 1"), (10**400, "an integer of 401 digits")):
        bad("IntegrityGateResult", edit(gate, ["variantPointCount"], value), f"a count of {reason}")
        bad("IntegrityGateResult", edit(gate, ["tilePx"], value), f"a tile of {reason}")

    bad("RestorationMeasurements", {**measured, "noiseNotMeasured": "too_few_flat_blocks"}, "a noise table and the reason it is missing",
        M_MEASURED_NOISE)
    bad("RestorationMeasurements", edit(measured, ["stars", "confirmed"], 41), "more stars confirmed than found", M_STARS_CONFIRMED)
    bad("RestorationMeasurements", edit(measured, ["lift"], {"gainAtBlack": 2, "decidedBy": "none"}), "no lift with a gain of two", M_NO_LIFT)
    bad("RestorationMeasurements", edit(measured, ["lift", "gainAtBlack"], 0.5), "a lift that darkens")
    clip = {"frameCount": 2, "framesWithNeighbours": 3, "exactRepeatShare": 0.5, "pointsConfirmedOnFramesTogether": 0,
            "pointsFixedToSensor": 0, "streamsNotCopied": 0}
    bad("RestorationMeasurements", {**measured, "clip": clip}, "more frames with neighbours than frames", M_CLIP_NEIGHBOURS)
    bad("RestorationMeasurements", edit(measured_clip, ["clip", "framesWithNeighbours"], 0),
        "points confirmed on frames together with no frame that had neighbours", M_FRAMES_TOGETHER)
    bad("RestorationMeasurements", edit(measured, ["dissolvedPerPass"], [3, 1, 0, 0]), "four dissolve passes")
    for note, reason in (("The original is stored as original.heic.", "a file name in a note"),
                         ("Taken at 30.0000, -40.0000, the synthetic open-ocean place.", "coordinates in a note"),
                         ("Taken on 2031:05:02.", "an EXIF date in a note"), ("Facing 30° west.", "a degree sign in a note"),
                         (NOTES[0] + " ", "a fixed note with a space after it"), (NOTES[0][:-1], "a fixed note without its full stop"),
                         ("", "an empty note")):
        bad("RestorationMeasurements", edit(measured, ["notes"], [note]), reason, M_NOTE)
    bad("RestorationMeasurements", edit(measured, ["notes"], [NOTE_DISSOLVED] * 9), "nine notes")
    bad("RestorationMeasurements", edit(measured, ["peakMemoryBytes"], 1024.5), "a fractional peak memory")
    bad("RestorationMeasurements", edit(measured, ["noiseNotMeasured"], "too_dark"), "a reason the contract does not list")
    bad("RestorationMeasurements", {**measured, "shadowSnrGainDb": 3.2}, "a removed metric: the shadow gain")
    bad("RestorationMeasurements", {**measured, "faceRegionStabilityScore": 1}, "a removed metric: the face score")

    bad("ProcessingAttempt", edit(ready, ["variantId"], _DROP), "a ready attempt with no variant", _attempt_fit("ready"))
    bad("ProcessingAttempt", edit(ready, ["measured"], _DROP), "a ready attempt that measured nothing", _attempt_fit("ready"))
    bad("ProcessingAttempt", {**ready_display, "rejection": "copy_differs"}, "a ready attempt with a rejection", _attempt_fit("ready"))
    bad("ProcessingAttempt", {**ready, "failureReason": "The file could not be decoded."}, "a ready attempt with a failure",
        _attempt_fit("ready"))
    bad("ProcessingAttempt", {**rejected, "variantId": ready["variantId"]}, "a rejected attempt with a variant", _attempt_fit("rejected"))
    bad("ProcessingAttempt", edit(rejected, ["rejection"], _DROP), "a rejected attempt with no rejection", _attempt_fit("rejected"))
    bad("ProcessingAttempt", edit(rejected, ["measured"], _DROP), "a rejected attempt that measured nothing", _attempt_fit("rejected"))
    bad("ProcessingAttempt", {**attempts[6], "failureReason": "The frame times could not be kept."}, "a rejected attempt with a failure",
        _attempt_fit("rejected"))
    bad("ProcessingAttempt", {**failed, "variantId": ready["variantId"]}, "a failed attempt with a variant", _attempt_fit("failed"))
    bad("ProcessingAttempt", edit(failed, ["failureReason"], _DROP), "a failed attempt that does not say why", _attempt_fit("failed"))
    bad("ProcessingAttempt", {**failed, "rejection": "audio_differs"}, "a failed attempt with a rejection", _attempt_fit("failed"))
    bad("ProcessingAttempt", edit(ready, ["gate"], valid["IntegrityGateResult"][1]), "a ready attempt whose gate refused", M_READY_PASSED)
    bad("ProcessingAttempt", {**failed, "measured": measured, "gate": valid["IntegrityGateResult"][1]},
        "a failed attempt whose gate refused", M_REFUSED_IS_REJECTED)
    bad("ProcessingAttempt", edit(rejected, ["gate"], _DROP), "a rejection by the gate with no gate", M_GATE_REJECTION)
    bad("ProcessingAttempt", edit(rejected, ["gate"], gate), "a rejection by a gate that passed", M_GATE_REJECTION)
    bad("ProcessingAttempt", edit(rejected, ["gate"], valid["IntegrityGateResult"][2]),
        "an unconfirmed point named where the gate found a star changed", M_REJECTION_NAMED)
    bad("ProcessingAttempt", edit(attempts[5], ["gate"], valid["IntegrityGateResult"][1]),
        "a changed star named where the gate found an unconfirmed point", M_REJECTION_NAMED)
    bad("ProcessingAttempt", {**attempts[8], "gate": gate}, "a display copy's rejection with a gate result", M_NO_GATE_BEFORE)
    bad("ProcessingAttempt", {**attempts[6], "gate": valid["IntegrityGateResult"][4]}, "a check before the gate with a gate result",
        M_NO_GATE_BEFORE)
    bad("ProcessingAttempt", edit(ready, ["measured", "stars", "found"], 36) | {"measured": {**measured, "stars": {"found": 36, "confirmed": 36}}},
        "a gate that measured more stars than were found", M_GATE_STARS_FOUND)
    bad("ProcessingAttempt", edit(ready, ["finishedAtUTC"], "2031-05-02T19:20:00Z"), "finished before it started", M_ATTEMPT_TIMES)
    bad("ProcessingAttempt", edit(ready, ["startedAtUTC"], "2031-02-30T19:20:01Z"), "a start on 30 February", M_CALENDAR)
    bad("ProcessingAttempt", edit(ready, ["startedAtUTC"], "2100-02-29T19:20:01Z"), "a leap day in a year divisible by 100 only",
        M_CALENDAR)
    bad("ProcessingAttempt", edit(ready, ["startedAtUTC"], "0000-05-02T19:20:01Z"), "a start in year 0", M_CALENDAR)
    bad("ProcessingAttempt", edit(ready, ["startedAtUTC"], "2031-05-02T19:20:01+00:00"), "UTC is written with Z")
    for reason_text, reason in (("Could not read /srv/albedian/store/5f/original.heic.", "a path in a failure reason"),
                                ("The clip from 2031-05-02 could not be read.", "a date in a failure reason"),
                                ("[mov,mp4 @ 0x7f8] moov atom not found", "an ffmpeg message as the failure reason"),
                                ("This kind of file is not supported yet: a sock.", "a format the list does not hold"),
                                ("This ffmpeg cannot do what this file needs: 10 bit HEVC encoding of yuv420p.",
                                 "a capability the list does not hold"),
                                ("The file could not be decoded", "a fixed sentence without its full stop"),
                                ("", "an empty failure reason")):
        bad("ProcessingAttempt", edit(failed, ["failureReason"], reason_text), reason, M_FAILURE_SENTENCE)
    bad("ProcessingAttempt", edit(ready, ["id"], "attempt_01"), "a malformed attempt id")
    bad("ProcessingAttempt", edit(ready, ["jobId"], "rjob_" + "1" * 16), "the old restore job id")
    bad("ProcessingAttempt", edit(ready, ["jobId"], "job_" + "1" * 15), "a job id one hex digit short")
    bad("ProcessingAttempt", edit(ready, ["status"], "superseded"), "a removed status: superseded")
    bad("ProcessingAttempt", edit(ready, ["status"], "evaluating"), "a removed status: evaluating")
    bad("ProcessingAttempt", {**ready, "cacheKey": "0123456789abcdef0123456789abcdef"}, "a removed field: the cache key")
    bad("ProcessingAttempt", {**ready, "metrics": {"shadowSnrGainDb": 3.2}}, "a removed field: the metrics")
    bad("ProcessingAttempt", {**ready, "gpuMs": 0}, "a removed field: the GPU time")
    bad("ProcessingAttempt", edit(ready, ["elapsedMs"], -1), "a negative elapsed time")

    bad("MediaVariant", edit(still_variant, ["disclosure"], DISCLOSURES["display_copy"]), "a restoration with the display copy's disclosure",
        M_DISCLOSURE)
    bad("MediaVariant", edit(display_variant, ["disclosure"], DISCLOSURES["restoration"]), "a display copy with the restoration's disclosure",
        M_DISCLOSURE)
    bad("MediaVariant", edit(still_variant, ["disclosure"], "Restored."), "a disclosure that is not one of the two")
    bad("MediaVariant", edit(still_variant, ["frameCount"], 2), "a still of two frames", M_STILL)
    bad("MediaVariant", edit(still_variant, ["durationMs"], 40), "a still with a duration", M_STILL)
    bad("MediaVariant", edit(still_variant, ["audioStreams"], clip_variant["audioStreams"]), "a still with audio", M_STILL)
    bad("MediaVariant", {**still_variant, "frameTimesSha256": clip_variant["frameTimesSha256"]}, "a still with frame times", M_STILL)
    bad("MediaVariant", edit(clip_variant, ["frameTimesSha256"], _DROP), "a clip with no frame times hash", M_CLIP_TIMES)
    clip_copy = {**clip_variant, "kind": "display_copy", "disclosure": DISCLOSURES["display_copy"]}
    bad("MediaVariant", clip_copy, "a display copy of a clip", M_DISPLAY_COPY_STILL)
    bad("MediaVariant", edit(still_variant, ["uri"], variant_uri(still_variant["sourceSha256"], display_variant["id"])),
        "a uri of another variant", M_VARIANT_URI)
    bad("MediaVariant", edit(still_variant, ["uri"], variant_uri(display_variant["sourceSha256"], still_variant["id"])),
        "a uri under another original", M_VARIANT_URI)
    bad("MediaVariant", edit(still_variant, ["colour"], clip_colour), "a PNG described as Y'CbCr", M_VARIANT_COLOUR)
    bad("MediaVariant", edit(clip_variant, ["colour"], png8), "a clip described as R'G'B'", M_VARIANT_COLOUR)
    bad("MediaVariant", edit(still_variant, ["uri"], f"/srv/albedian/store/5f/{still_variant['sourceSha256']}/variants/x/variant.png"),
        "a store path as the uri")
    bad("MediaVariant", edit(still_variant, ["uri"], f"file:///srv/albedian/{still_variant['id']}.png"), "a file uri")
    bad("MediaVariant", edit(still_variant, ["sourceAssetId"], f"src_{display_variant['sourceSha256'][:16]}"),
        "the asset id of another source", M_SOURCE_ASSET)
    for probe in ("night-clip.mov", "Night over the sea", "IMG_1234", "PXL_20311111_221500123", "20311111-221500",
                  "VID20311111221500", "a" * 128):
        bad("MediaVariant", edit(still_variant, ["sourceAssetId"], probe), f"a file stem, title or time as the asset id: {probe[:24]}")
    bad("MediaVariant", edit(clip_variant, ["audioStreams", 0, "codec"], "Users/me/Pictures/IMG_0001.MOV"), "a path as an audio codec")
    bad("MediaVariant", edit(clip_variant, ["audioStreams", 0, "codec"], "AAC"), "an audio codec in capitals")
    bad("MediaVariant", edit(clip_variant, ["audioStreams", 0, "sha256"], "Users/me/Pictures/IMG_0001.MOV"), "a path as an audio hash")
    bad("MediaVariant", edit(clip_variant, ["audioStreams", 0, "sha256"], "aa11bb22cc33dd44"), "a short audio hash")
    bad("MediaVariant", edit(clip_variant, ["audioStreams", 0, "sampleRateHz"], 48000.5), "a fractional sample rate")
    bad("MediaVariant", edit(clip_variant, ["displayOrientation", "quarterTurnsClockwise"], 1.5), "a fractional quarter turn")
    bad("MediaVariant", edit(clip_variant, ["displayOrientation", "quarterTurnsClockwise"], True), "a quarter turn that is a boolean")
    bad("MediaVariant", edit(still_variant, ["mimeType"], "image/heic"), "a type the contract does not serve")
    bad("MediaVariant", edit(still_variant, ["byteSize"], 0), "an empty file")
    bad("MediaVariant", edit(still_variant, ["byteSize"], 2**53), "a size of 2^53 bytes")
    bad("MediaVariant", edit(still_variant, ["createdAtUTC"], "2031-06-31T19:20:07Z"), "a time on 31 June", M_CALENDAR)
    for field in ("label", "summaryNote", "frameTimestampsMs", "audioStreamCopiedFromSource", "audioSyncOffsetMs"):
        bad("MediaVariant", {**still_variant, field: [0] if field == "frameTimestampsMs" else "x"}, f"a removed field: {field}")

    # The display copy's manifest is the smaller record, so the faults any manifest can have are shown on it
    bad("ExportManifest", edit(display_manifest, ["graph", "sourceSha256"], "d" * 64), "a graph of another source", M_MANIFEST_SOURCE)
    bad("ExportManifest", edit(display_manifest, ["sourceSha256"], "d" * 64), "a manifest naming another source", M_MANIFEST_SOURCE)
    bad("ExportManifest", edit(display_manifest, ["graph", "id"], "graph_" + "1" * 16), "a graph the variant does not name",
        M_MANIFEST_IDS)
    bad("ExportManifest", edit(display_manifest, ["attempt", "id"], "att_" + "2" * 16), "an attempt the variant does not name",
        M_MANIFEST_IDS)
    bad("ExportManifest", edit(display_manifest, ["attempt", "variantId"], "var_" + "3" * 16), "an attempt naming another variant",
        M_MANIFEST_IDS)
    bad("ExportManifest", edit(display_manifest, ["attempt", "sourceSha256"], "d" * 64), "an attempt of another source",
        M_MANIFEST_SOURCE)
    bad("ExportManifest", edit(display_manifest, ["variant", "graphId"], "graph_" + "1" * 16), "a variant naming another graph",
        M_MANIFEST_IDS)
    bad("ExportManifest", edit(display_manifest, ["attempt", "graphId"], "graph_" + "1" * 16), "an attempt naming another graph",
        M_MANIFEST_IDS)
    as_restoration = {**display_manifest["variant"], "kind": "restoration", "disclosure": DISCLOSURES["restoration"]}
    bad("ExportManifest", {**display_manifest, "variant": as_restoration, "disclosure": DISCLOSURES["restoration"]},
        "a restoration made by a display copy's graph", M_MANIFEST_IDS)
    rejected_attempt = edit(manifest["attempt"], ["variantId"], _DROP) | {
        "status": "rejected", "rejection": "unconfirmed_points", "gate": valid["IntegrityGateResult"][1]}
    bad("ExportManifest", {**manifest, "attempt": rejected_attempt}, "a rejected attempt exported", M_MANIFEST_READY)
    bad("ExportManifest", edit(display_manifest, ["disclosure"], DISCLOSURES["restoration"]), "a manifest with another disclosure",
        M_MANIFEST_DISCLOSURE)
    bad("ExportManifest", edit(manifest, ["attempt", "gate"], _DROP), "a restoration exported with no gate result", M_MANIFEST_GATE)
    bad("ExportManifest", edit(display_manifest, ["attempt", "gate"], valid["IntegrityGateResult"][0]), "a display copy with a gate result",
        M_MANIFEST_GATE)
    bad("ExportManifest", edit(manifest, ["attempt", "measured", "noise"], _DROP), "a restoration with no noise table and no reason",
        M_MANIFEST_NOISE)
    bad("ExportManifest", edit(clip_manifest, ["attempt", "measured", "clip", "frameCount"], 359),
        "measured clip frames other than the variant's", M_MANIFEST_FRAMES)
    bad("ExportManifest", edit(manifest, ["attempt", "gate", "framesChecked"], 50), "a still's gate that checked 50 frames", M_STILL_GATE)
    bad("ExportManifest", edit(manifest, ["observationSolutionId"], f"sol_{clip_manifest['sourceSha256'][:16]}"),
        "the solution of another source", M_SOLUTION)
    for probe in ("solution.json", "PXL_20311111_221500123", "solution-20311111T221500Z", "sol_" + "a" * 17):
        bad("ExportManifest", {**display_manifest, "observationSolutionId": probe}, f"a file name, stem or time as the solution id: {probe[:24]}")
    bad("ExportManifest", edit(clip_manifest, ["variant", "audioStreams", 0, "codec"], "Users/me/Pictures/IMG_0001.MOV"),
        "a path as the codec of an exported clip")
    bad("ExportManifest", edit(display_manifest, ["formatVersion"], 2), "a format version this contract does not read")
    bad("ExportManifest", edit(display_manifest, ["formatVersion"], True), "a format version that is a boolean")
    bad("ExportManifest", {**display_manifest, "capturedAtUTC": "2031-05-02T19:20:00Z"}, "a capture time in the manifest")
    bad("ExportManifest", {**display_manifest, "title": "Night over the sea"}, "a title in the manifest")

    bad("ExportArtifact", {**export, "sessionId": "session_01"}, "a removed field: the session id")
    bad("ExportArtifact", {**export, "epistemicSummary": {"strictFidelityEnforced": True}}, "a removed field: the epistemic summary")
    bad("ExportArtifact", {**export, "outputUri": "file:///srv/albedian/export.png"}, "a removed field: the output uri")
    bad("ExportArtifact", edit(export, ["variantSha256"], "a8f5c2d9104e8b77"), "a short hash")
    bad("ExportArtifact", edit(export, ["exportedAtUTC"], "2031-02-29T10:00:00Z"), "a day that 2031 does not have", M_CALENDAR)
    bad("ExportArtifact", edit(export, ["exportedAtUTC"], "1900-02-29T10:00:00Z"), "a leap day 1900 does not have", M_CALENDAR)

    # Every number field just past its bound
    out_of_bounds = [
        ("MediaVariant", clip_variant, ["audioStreams", 0, "index"], -1), ("MediaVariant", clip_variant, ["audioStreams", 0, "sampleRateHz"], 0),
        ("MediaVariant", clip_variant, ["audioStreams", 0, "channels"], 0), ("MediaVariant", clip_variant, ["audioStreams", 0, "durationMs"], -1),
        ("MediaVariant", clip_variant, ["displayOrientation", "quarterTurnsClockwise"], -1),
        ("MediaVariant", clip_variant, ["displayOrientation", "quarterTurnsClockwise"], 4),
        ("MediaVariant", clip_variant, ["durationMs"], -1), ("MediaVariant", clip_variant, ["width"], 0),
        ("IntegrityGateResult", gate, ["variantPointCount"], -1), ("IntegrityGateResult", gate, ["clippedPointsLeftOut"], -1),
        ("IntegrityGateResult", gate, ["variantThresholdSnr"], 0), ("IntegrityGateResult", gate, ["confirmThresholdSnr"], 0),
        ("IntegrityGateResult", gate, ["matchRadiusPx"], 0), ("IntegrityGateResult", gate, ["fluxToleranceSigma"], 0),
        # A gate that measured no flux, so only the bound refuses a zero tolerance
        ("IntegrityGateResult", valid["IntegrityGateResult"][3], ["fluxToleranceSigma"], 0),
        ("IntegrityGateResult", gate, ["worstFluxChangeSigma"], -0.5), ("IntegrityGateResult", gate, ["medianFluxRatio"], 0),
        ("NoiseTable", noise, ["intensities", 0], -0.001), ("NoiseTable", noise, ["sigma", "r", 0], -0.001),
        ("NoiseTable", noise, ["sigma", "g", 0], -0.001), ("NoiseTable", noise, ["sigma", "b", 0], -0.001),
        ("RestorationMeasurements", measured, ["fineNoiseLeft"], -0.01), ("RestorationMeasurements", measured, ["stars", "found"], -1),
        ("RestorationMeasurements", measured_clip, ["clip", "medianClosureErrorPx"], -0.01),
        ("RestorationMeasurements", measured_clip, ["clip", "exactRepeatShare"], -0.01),
        ("RestorationMeasurements", measured_clip, ["clip", "exactRepeatShare"], 1.01),
        ("RestorationMeasurements", measured_clip, ["clip", "pointsFixedToSensor"], -1),
        ("RestorationMeasurements", measured, ["dissolvedPerPass", 0], -1),
        ("ProcessingAttempt", ready, ["cpuMs"], -1),
    ]
    bad("MediaVariant", edit(clip_variant, ["audioStreams", 0, "index"], 0.5), "an audio stream index of 0.5")
    for schema, base, path, value in out_of_bounds:
        bad(schema, edit(base, path, value), f"{'.'.join(str(key) for key in path)} of {value}, past its bound")
    return invalid


_RAW_SLOT = "albedian raw number slot"


def _raw_samples(valid: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Records whose one number is written in a form only the JSON text can carry (1e400, an integer of 401 digits,
    1e0, -0.0, 1e-400): each side reads the text with its own JSON reader, then validates. `accepted` is the verdict."""
    raw: list[dict[str, Any]] = []

    def case(schema: str, base: dict, path: list, literal: str, accepted: bool, reason: str, message: str | None = None) -> None:
        record = copy.deepcopy(base)
        target = record
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = _RAW_SLOT
        text = json.dumps(record, ensure_ascii=True).replace(json.dumps(_RAW_SLOT), literal)
        raw.append({"schema": schema, "json": text, "accepted": accepted, "reason": reason, "message": message})

    gate = valid["IntegrityGateResult"][0]
    still, clip = valid["MediaVariant"][0], valid["MediaVariant"][2]
    tile = valid["GraphParameter"][6]
    huge = "1" + "0" * 400
    integer_slots = (("IntegrityGateResult", gate, ["variantPointCount"], "a count"),
                     ("MediaVariant", still, ["byteSize"], "a size"),
                     ("IntegrityGateResult", gate, ["tilePx"], "a tile size"))
    for schema, base, path, what in integer_slots:
        case(schema, base, path, "1e400", False, f"{what} of 1e400")
        case(schema, base, path, huge, False, f"{what} of an integer of 401 digits")
        case(schema, base, path, "9007199254740992", False, f"{what} of 2^53")
        case(schema, base, path, "9007199254740993", False, f"{what} of 2^53 + 1")
        case(schema, base, path, "9007199254740991", True, f"{what} of 2^53 - 1")
        case(schema, base, path, "4.096e3", True, f"{what} written 4.096e3")
        case(schema, base, path, "4096.0", True, f"{what} written 4096.0")
        case(schema, base, path, "true", False, f"{what} that is a boolean")
    case("IntegrityGateResult", gate, ["clippedPointsLeftOut"], "-0.0", True, "a count of minus zero")
    case("IntegrityGateResult", gate, ["clippedPointsLeftOut"], "1e-400", True, "a count of 1e-400, which reads as zero")
    case("MediaVariant", still, ["byteSize"], "-0.0", False, "a size of minus zero, which is no size")
    for literal, accepted, what in (("1e400", False, "1e400"), (huge, False, "an integer of 401 digits"),
                                    ("9007199254740992", False, "2^53"), ("2.56e2", True, "256 written 2.56e2"),
                                    ("256.0", True, "256 written 256.0"), ("true", False, "a boolean")):
        case("GraphParameter", tile, ["value"], literal, accepted, f"a tile parameter of {what}")
    for literal, accepted, what in (("1e400", False, "1e400"), (huge, False, "an integer of 401 digits"),
                                    ("1.5e0", True, "1.5e0"), ("-0.0", False, "minus zero, which is not positive")):
        case("IntegrityGateResult", gate, ["matchRadiusPx"], literal, accepted, f"a match radius of {what}")
    for literal, accepted, what in (("1e400", False, "1e400"), ("-1e400", False, "-1e400"), (huge, False, "an integer of 401 digits"),
                                    ("-21.333", True, "-21.333")):
        case("MediaVariant", clip, ["audioStreams", 0, "startPtsMs"], literal, accepted, f"an audio stream start of {what}")
    for literal, accepted, what in (("1.0", True, "1.0"), ("1e0", True, "1e0"), ("-0.0", True, "minus zero"),
                                    ("1e-400", True, "1e-400"), ("1.5", False, "1.5"), ("true", False, "a boolean")):
        case("MediaVariant", clip, ["displayOrientation", "quarterTurnsClockwise"], literal, accepted, f"a quarter turn of {what}")
    for literal, accepted, what in (("2.0", True, "2.0"), ("1e400", False, "1e400"), ("2.5", False, "2.5")):
        case("MediaVariant", clip, ["audioStreams", 0, "channels"], literal, accepted, f"channels of {what}")
    # An embedded integer written as a float is that integer on both sides
    case("MediaVariant", clip, ["audioStreams", 0, "index"], "1.0", True, "an audio stream index written 1.0")
    case("MediaVariant", clip, ["audioStreams", 0, "sampleRateHz"], "4.8e4", True, "a sample rate written 4.8e4")
    # 1e400 in every float field: JavaScript reads it as Infinity, Python as inf, and both refuse it. An integer of 401
    # digits is Infinity to JavaScript and too large for a double to Python, and both refuse it as not finite
    measured = valid["RestorationMeasurements"][0]
    measured_clip = valid["RestorationMeasurements"][2]
    attempt = valid["ProcessingAttempt"][0]
    for schema, base, path in (("MediaVariant", clip, ["durationMs"]), ("MediaVariant", clip, ["audioStreams", 0, "durationMs"]),
                               ("IntegrityGateResult", gate, ["variantThresholdSnr"]), ("IntegrityGateResult", gate, ["confirmThresholdSnr"]),
                               ("IntegrityGateResult", gate, ["fluxToleranceSigma"]), ("IntegrityGateResult", gate, ["worstFluxChangeSigma"]),
                               ("IntegrityGateResult", gate, ["medianFluxRatio"]), ("NoiseTable", valid["NoiseTable"][0], ["sigma", "r", 0]),
                               ("NoiseTable", valid["NoiseTable"][0], ["sigma", "g", 0]), ("NoiseTable", valid["NoiseTable"][0], ["sigma", "b", 0]),
                               ("NoiseTable", valid["NoiseTable"][0], ["intensities", 3]),
                               ("RestorationMeasurements", measured, ["fineNoiseLeft"]), ("RestorationMeasurements", measured, ["lift", "gainAtBlack"]),
                               ("RestorationMeasurements", measured_clip, ["clip", "medianClosureErrorPx"]),
                               ("RestorationMeasurements", measured_clip, ["clip", "exactRepeatShare"]),
                               ("ProcessingAttempt", attempt, ["elapsedMs"]), ("ProcessingAttempt", attempt, ["cpuMs"]),
                               ("RestoreRequest", valid["RestoreRequest"][2], ["exposureEv"])):
        where = ".".join(str(key) for key in path)
        case(schema, base, path, "1e400", False, f"{where} of 1e400")
        case(schema, base, path, huge, False, f"{where} of an integer of 401 digits", M_NOT_FINITE)
    return raw


class _Drop:
    pass


_DROP = _Drop()
