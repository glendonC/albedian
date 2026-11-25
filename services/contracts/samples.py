"""The sample night's record (first-use-sample-one, DECIDED-BY-LEAD 25 Nov 03:22 and 03:43): a public photo the app
opens with no photo of the person's own, named as the corpus photos are (its entry in data/downloads.json), with its
credit and licence, said to be a sample, and where on this machine the app's own solve of it is kept with its hash
beside it. The mirror of packages/contracts/src/samples.ts; the record is data/samples.json."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from services.contracts.wire import SHA256_HEX_PATTERN, WireInt, WireModel, wire_length

SAMPLES_PATH = Path(__file__).resolve().parents[2] / "data" / "samples.json"
SAMPLE_ID_PATTERN = r"^night-[a-z0-9-]{1,40}$"
# The photo comes as the corpus photos come, kept in the shared cache git ignores; the solution only under .cache/samples
PHOTO_PATH_PATTERN = r"^\.cache/real-photos/[a-z0-9-]{1,40}/[a-z0-9-]{1,40}\.(jpg|jpeg|png)$"
SOLUTION_PATH_PATTERN = r"^\.cache/samples/[a-z0-9-]{1,40}/[a-z0-9.-]{1,60}$"
# Printable ASCII only, so both languages read an address alike (no \s: JavaScript and the Rust regex hold different
# spaces); lengths in UTF-16 code units, as Zod counts them (AUDIT, first-use-sample-zero version two)
HTTPS_URL = r"^https://[!-~]{1,300}$"

_Text = wire_length(1, 200)


class SamplePhoto(WireModel):
    download_path: Annotated[str, StringConstraints(pattern=PHOTO_PATH_PATTERN)]
    sha256: Annotated[str, StringConstraints(pattern=SHA256_HEX_PATTERN)]
    bytes: Annotated[WireInt, Field(ge=1)]
    mime_type: Literal["image/jpeg", "image/png"]


class SampleCredit(WireModel):
    author: _Text
    licence: Literal["CC0", "CC BY 4.0", "CC BY-SA 4.0"]
    licence_url: Annotated[str, StringConstraints(pattern=HTTPS_URL)]
    source_url: Annotated[str, StringConstraints(pattern=HTTPS_URL)]


class SampleSolution(WireModel):
    path: Annotated[str, StringConstraints(pattern=SOLUTION_PATH_PATTERN)]
    sha256_path: Annotated[str, StringConstraints(pattern=SOLUTION_PATH_PATTERN)]


class SampleNight(WireModel):
    id: Annotated[str, StringConstraints(pattern=SAMPLE_ID_PATTERN)]
    # Said to be a sample wherever it shows
    kind: Literal["sample"]
    title: _Text
    photo: SamplePhoto
    credit: SampleCredit
    solution: SampleSolution


class SampleRecord(WireModel):
    about: wire_length(1, 2000)
    samples: Annotated[list[SampleNight], Field(min_length=1, max_length=8)]


def load_samples(path: Path = SAMPLES_PATH) -> list[SampleNight]:
    """The tracked sample nights, checked by their contract."""
    return SampleRecord.model_validate_json(path.read_text(encoding="utf-8")).samples
