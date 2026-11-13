"""Asset provenance records: the Python mirror of `AssetProvenanceRecordSchema` in
packages/contracts/src/provenance.ts.

Every file the web app serves from `apps/web/public/` carries a provenance record: where it came
from, which version or when it was retrieved, under which licence, with what credit line, its
SHA-256, and the script that made it or that it was downloaded as is (roadmap Track 2 M1). One
record file, `PROVENANCE.json`, sits in each asset folder and describes the files beside it. A
file set pins a `SHA256SUMS` list (one `<sha256>  <relative path>` line per file) so a bake
output such as the catalog star tiles is covered without listing every tile in the record.

Bake scripts write records with `dumps_record`; `scripts/verify_provenance.py` checks them
against the files. Field names, limits and rules match the Zod schema;
tests/fixtures/provenance_contract.json is written from this module and parsed by the
TypeScript tests, so a change on either side fails one of them.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

ASSET_PROVENANCE_FORMAT = "albedian.asset-provenance"
ASSET_PROVENANCE_FORMAT_VERSION = 1
ASSET_PROVENANCE_RECORD_FILENAME = "PROVENANCE.json"

# SPDX License List identifiers a record may name, each checked at https://spdx.org/licenses/
# (License List 3.29.0, 2026-09-16; ODbL-1.0 and BSD-3-Clause on their pages on 2026-11-04; OFL-1.1, "SIL Open
# Font License 1.1", on its page on 2026-11-13).
# Terms without a listed identifier use the SPDX form LicenseRef-<name> with a URL to the
# terms. Add an identifier only after checking it there, on both sides of the contract.
SPDX_LICENSE_IDS: tuple[str, ...] = (
    "CC-BY-4.0",
    "CC-BY-SA-4.0",
    "CC-BY-NC-4.0",
    "CC-BY-NC-3.0-IGO",
    "CC0-1.0",
    "ODbL-1.0",
    "MIT",
    "Apache-2.0",
    "BSD-3-Clause",
    "OFL-1.1",
)

# Whitespace and invisible format characters, listed out so both regex engines agree: Unicode
# White_Space plus the zero-width, bidirectional-control and invisible-operator characters, the
# Mongolian vowel separator and the byte order mark. JavaScript's \s and Rust's \s (which
# Pydantic uses) disagree on U+0085 and U+FEFF, so no pattern here relies on \s or \S.
BLANK_CHARACTERS = r"\t\n\u000b\f\r \u0085\u00a0\u1680\u180e\u2000-\u200f\u2028-\u202f\u205f-\u2064\u3000\ufeff"

PROVENANCE_ID_PATTERN = r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$"
# Relative POSIX path inside the record's folder: no leading slash, no empty, "." or ".."
# segment, no hidden file, no backslash
PROVENANCE_PATH_PATTERN = r"^[A-Za-z0-9_-][A-Za-z0-9._-]*(?:/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
HTTP_URL_PATTERN = rf"^https?://[^{BLANK_CHARACTERS}/]+[^{BLANK_CHARACTERS}]*$"
DOI_PATTERN = rf"^10\.[0-9]{{4,9}}/[^{BLANK_CHARACTERS}]+$"
ISO_DATE_PATTERN = r"^(?:19|20|21)[0-9]{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])$"
LICENSE_REF_PATTERN = r"^LicenseRef-[A-Za-z0-9.-]+$"
# Text that neither starts nor ends with a blank character, so a rewrite never changes its bytes
TEXT_PATTERN = rf"^[^{BLANK_CHARACTERS}](?:[\s\S]*[^{BLANK_CHARACTERS}])?$"

# Lengths count Unicode code points on both sides (Python's len; [...text].length in TypeScript)
SHORT_TEXT_MAX_LENGTH = 300
CREDIT_MAX_LENGTH = 200
TEXT_MAX_LENGTH = 4000
URL_MAX_LENGTH = 2000
PATH_MAX_LENGTH = 400

SourceId = Annotated[str, StringConstraints(pattern=PROVENANCE_ID_PATTERN)]
RelativePath = Annotated[str, StringConstraints(max_length=PATH_MAX_LENGTH, pattern=PROVENANCE_PATH_PATTERN)]
Sha256 = Annotated[str, StringConstraints(pattern=SHA256_PATTERN)]
HttpUrl = Annotated[str, StringConstraints(max_length=URL_MAX_LENGTH, pattern=HTTP_URL_PATTERN)]
Doi = Annotated[str, StringConstraints(pattern=DOI_PATTERN)]
IsoDate = Annotated[str, StringConstraints(pattern=ISO_DATE_PATTERN)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=SHORT_TEXT_MAX_LENGTH, pattern=TEXT_PATTERN)]
Credit = Annotated[str, StringConstraints(min_length=1, max_length=CREDIT_MAX_LENGTH, pattern=TEXT_PATTERN)]
Text = Annotated[str, StringConstraints(min_length=1, max_length=TEXT_MAX_LENGTH, pattern=TEXT_PATTERN)]


def _omitted_not_null(data: Any, names: tuple[str, ...]) -> Any:
    # Zod's .optional() rejects null, so the wire format leaves an unknown value out
    if isinstance(data, dict):
        for name in names:
            if name in data and data[name] is None:
                raise ValueError(f"{name} is omitted when unknown, never null.")
    return data


class ProvenanceLicense(BaseModel):
    """The licence a source is distributed under, with a link to its terms."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # An identifier from SPDX_LICENSE_IDS, or LicenseRef-<name> for terms the SPDX list lacks
    spdx: str
    name: ShortText
    url: HttpUrl
    # Obligations beyond the licence text, such as an authorisation needed for commercial use
    conditions: Text | None = None

    @model_validator(mode="before")
    @classmethod
    def _optional_fields(cls, data: Any) -> Any:
        return _omitted_not_null(data, ("conditions",))

    @field_validator("spdx")
    @classmethod
    def _listed_or_reference(cls, value: str) -> str:
        if value in SPDX_LICENSE_IDS or re.fullmatch(LICENSE_REF_PATTERN, value):
            return value
        raise ValueError(f"{value!r} is neither a pinned SPDX License List id nor a LicenseRef- id.")


class ProvenanceSource(BaseModel):
    """One upstream dataset, image or program that assets in the folder come from."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: SourceId
    title: ShortText
    # Where the bytes were retrieved: a download URL, an archive service or a landing page
    url: HttpUrl
    # The release or edition as the provider names it
    version: ShortText | None = None
    # The date the bytes were fetched, YYYY-MM-DD
    retrieved: IsoDate | None = None
    license: ProvenanceLicense
    # The credit line the licence asks to display beside the asset
    credit: Credit
    citation: Text | None = None
    doi: Doi | None = None
    acknowledgement: Text | None = None
    # SHA-256 of the downloaded input, when it is kept
    sha256: Sha256 | None = None
    note: Text | None = None

    @model_validator(mode="before")
    @classmethod
    def _optional_fields(cls, data: Any) -> Any:
        return _omitted_not_null(
            data, ("version", "retrieved", "citation", "doi", "acknowledgement", "sha256", "note")
        )

    @field_validator("retrieved")
    @classmethod
    def _real_date(cls, value: str | None) -> str | None:
        if value is not None:
            date.fromisoformat(value)  # rejects 2026-02-30
        return value

    @model_validator(mode="after")
    def _version_or_retrieval_date(self) -> ProvenanceSource:
        if self.version is None and self.retrieved is None:
            raise ValueError(f"Source {self.id} needs a version or a retrieval date.")
        return self


class DownloadedAsIs(BaseModel):
    """The asset is the provider's file, byte for byte."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    method: Literal["downloaded-as-is"]


class MadeByScript(BaseModel):
    """The asset was made by a script in this repository."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    method: Literal["script"]
    # Repository-relative path of the script
    script: RelativePath
    command: ShortText | None = None

    @model_validator(mode="before")
    @classmethod
    def _optional_fields(cls, data: Any) -> Any:
        return _omitted_not_null(data, ("command",))


Producer = Annotated[Union[DownloadedAsIs, MadeByScript], Field(discriminator="method")]


def _unique_references(sources: tuple[str, ...]) -> tuple[str, ...]:
    if len(set(sources)) != len(sources):
        raise ValueError("An entry names the same source more than once.")
    return sources


class ProvenanceFile(BaseModel):
    """One asset file beside the record, pinned by its SHA-256."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    path: RelativePath
    sha256: Sha256
    sources: tuple[SourceId, ...] = Field(min_length=1)
    produced_by: Producer = Field(alias="producedBy")
    note: Text | None = None

    @model_validator(mode="before")
    @classmethod
    def _optional_fields(cls, data: Any) -> Any:
        return _omitted_not_null(data, ("note",))

    @field_validator("sources")
    @classmethod
    def _unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_references(value)


class ProvenanceFileSet(BaseModel):
    """Every file a SHA256SUMS list beside the record names, with the list itself pinned."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    checksums: RelativePath
    sha256: Sha256
    sources: tuple[SourceId, ...] = Field(min_length=1)
    produced_by: Producer = Field(alias="producedBy")
    note: Text | None = None

    @model_validator(mode="before")
    @classmethod
    def _optional_fields(cls, data: Any) -> Any:
        return _omitted_not_null(data, ("note",))

    @field_validator("sources")
    @classmethod
    def _unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_references(value)


class AssetProvenanceRecord(BaseModel):
    """The `PROVENANCE.json` of one asset folder; serialized with the contract's camelCase names."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    format: Literal["albedian.asset-provenance"]
    format_version: Literal[1] = Field(alias="formatVersion")
    sources: tuple[ProvenanceSource, ...] = Field(min_length=1)
    files: tuple[ProvenanceFile, ...]
    file_sets: tuple[ProvenanceFileSet, ...] = Field(alias="fileSets")

    @field_validator("format_version", mode="before")
    @classmethod
    def _integer_version(cls, value: Any) -> Any:
        # JSON true is not the number 1, as Zod's literal(1) sees it
        if isinstance(value, bool):
            raise ValueError("formatVersion is a number.")
        return value

    @model_validator(mode="after")
    def _references_and_paths(self) -> AssetProvenanceRecord:
        defined = [s.id for s in self.sources]
        if len(set(defined)) != len(defined):
            raise ValueError("A source id appears more than once.")
        entries = [*self.files, *self.file_sets]
        if not entries:
            raise ValueError("A record describes at least one file or file set.")
        referenced = {ref for entry in entries for ref in entry.sources}
        undefined = sorted(referenced - set(defined))
        if undefined:
            raise ValueError(f"Entries name undefined sources: {undefined}.")
        unused = [s for s in defined if s not in referenced]
        if unused:
            raise ValueError(f"Sources no entry names: {unused}.")
        paths = [f.path for f in self.files] + [s.checksums for s in self.file_sets]
        if len(set(paths)) != len(paths):
            raise ValueError("A path appears more than once.")
        if ASSET_PROVENANCE_RECORD_FILENAME in paths:
            raise ValueError(f"{ASSET_PROVENANCE_RECORD_FILENAME} is the record, not an asset it describes.")
        return self

    def to_contract_dict(self) -> dict[str, Any]:
        """The record as `AssetProvenanceRecordSchema` expects it: camelCase keys, unknown optional fields left out."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


def dumps_record(record: AssetProvenanceRecord) -> str:
    """The record file's exact text: two-space JSON in contract key order, one final newline."""
    return json.dumps(record.to_contract_dict(), indent=2, ensure_ascii=False) + "\n"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


_SUMS_LINE = re.compile(r"([0-9a-f]{64})  (.+)")


def parse_sha256sums(text: str) -> list[tuple[str, str]]:
    """Read a SHA256SUMS list as the bake scripts write it: `<sha256>  <relative path>` per line.

    Lower-case hex, two spaces, a path inside the list's folder, every line ending in a newline,
    no blank line and no path twice. Anything else raises ValueError.
    """
    if not text.endswith("\n"):
        raise ValueError("A SHA256SUMS list ends with a newline.")
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for number, line in enumerate(text[:-1].split("\n"), start=1):
        match = _SUMS_LINE.fullmatch(line)
        if match is None:
            raise ValueError(f"line {number} is not '<sha256>  <path>': {line[:80]!r}")
        digest, rel = match.groups()
        if not re.fullmatch(PROVENANCE_PATH_PATTERN, rel) or len(rel) > PATH_MAX_LENGTH:
            raise ValueError(f"line {number} names a path outside the list's folder: {rel!r}")
        if rel in seen:
            raise ValueError(f"line {number} repeats {rel!r}")
        seen.add(rel)
        entries.append((digest, rel))
    return entries
