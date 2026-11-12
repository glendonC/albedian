"""The real-photo corpus: its tracked manifest (corpus.json), the loader and the rules both files must keep.

The manifest lists photographs by third parties, fetched from Wikimedia Commons under open licences. The photos are
not in git: `data/downloads.json` holds each file's URL, size, SHA-256, licence and author, and the files live in the
shared `.cache/real-photos/`. The manifest holds no ground truth, no coordinate, no capture time and no free text:
every string is an id, a path, a word from a closed list, a page id link or the file's own camera name, and every
number is a pixel size, a focal length or an exposure time. A photo's place and time stay in the file's own metadata;
`read_facts` reads only whether they are there, and on which side of the equator the place lies.

`load_corpus` refuses a manifest that breaks a rule, naming each break. `registry_problems` lists what the registry
entries of the corpus break. Sources of the limits: the minimum counts, the makers, the two size limits and the
licence allow list are the ones set for this corpus when it was built (unit realsky-1, written out in
docs/science/REAL_PHOTO_CORPUS.md); the camera number ranges are the ingest reader's own
(`services/ingest/metadata.py`, `CAMERA_LIMITS`); the wide-lens limit is [proposed] here.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Literal, NamedTuple, Optional, Union
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, field_validator, model_validator

from services.ingest.metadata import CAMERA_LIMITS

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = Path(__file__).with_name("corpus.json")
REGISTRY = ROOT / "data" / "downloads.json"
CACHE_PREFIX = ".cache/real-photos/"
FETCH_SCRIPT = "scripts/fetch_download.py"

# The least each tier must hold, and within tier C the least of each beacon
TIER_MINIMUMS: dict[str, int] = {"A": 8, "B": 16, "C": 10, "D": 8}
KIND_MINIMUMS: dict[str, int] = {"moon_over_horizon": 6, "sun_over_skyline": 4}
KINDS: dict[str, tuple[str, ...]] = {
    "A": ("camera_wide_field",),
    "B": ("phone_night_sky",),
    "C": ("moon_over_horizon", "sun_over_skyline"),
    "D": ("overcast", "city_street", "fireworks", "aircraft_trails", "dark_interior", "screen"),
}
# "Up to about 60 photos and 500 MB in all", each file at most 40 MB; MB read as 10**6 bytes, the smaller reading
MAX_PHOTOS = 60
MAX_PHOTO_BYTES = 40_000_000
MAX_CORPUS_BYTES = 500_000_000
# [proposed] A tier A lens is at most 35 mm long: the usual upper edge of a wide lens on a 36 mm wide sensor. A
# smaller sensor sees less at the same length, so this bounds the field from above only for full-frame cameras.
WIDE_LENS_MAX_MM = 35.0
# Tier B holds iPhone, Pixel and Samsung photos and at least two other makers, compared without case
REQUIRED_PHONE_MAKERS: tuple[str, ...] = ("apple", "google", "samsung")
OTHER_PHONE_MAKERS_MINIMUM = 2
# The tiers that must hold photos from both sides of the equator, and the tiers whose files must name their camera
BOTH_HEMISPHERE_TIERS: tuple[str, ...] = ("A", "B")
CAMERA_NAMED_TIERS: tuple[str, ...] = ("A", "B")
# The extension the ingest store gives each kind of file it accepts (services/ingest/store.py, sniff)
EXTENSIONS: dict[str, tuple[str, ...]] = {"image": ("jpg", "png", "tif", "heic"), "video": ("mov", "mp4")}

# The 88 constellations of the International Astronomical Union, each with the IAU's three-letter abbreviation.
# Names are the IAU's Latin names in ASCII (Bootes without its diaeresis). A test compares the abbreviations with
# the list the deep-sky contract holds.
IAU_CONSTELLATIONS: dict[str, str] = {
    "Andromeda": "And", "Antlia": "Ant", "Apus": "Aps", "Aquarius": "Aqr", "Aquila": "Aql", "Ara": "Ara",
    "Aries": "Ari", "Auriga": "Aur", "Bootes": "Boo", "Caelum": "Cae", "Camelopardalis": "Cam", "Cancer": "Cnc",
    "Canes Venatici": "CVn", "Canis Major": "CMa", "Canis Minor": "CMi", "Capricornus": "Cap", "Carina": "Car",
    "Cassiopeia": "Cas", "Centaurus": "Cen", "Cepheus": "Cep", "Cetus": "Cet", "Chamaeleon": "Cha",
    "Circinus": "Cir", "Columba": "Col", "Coma Berenices": "Com", "Corona Australis": "CrA",
    "Corona Borealis": "CrB", "Corvus": "Crv", "Crater": "Crt", "Crux": "Cru", "Cygnus": "Cyg", "Delphinus": "Del",
    "Dorado": "Dor", "Draco": "Dra", "Equuleus": "Equ", "Eridanus": "Eri", "Fornax": "For", "Gemini": "Gem",
    "Grus": "Gru", "Hercules": "Her", "Horologium": "Hor", "Hydra": "Hya", "Hydrus": "Hyi", "Indus": "Ind",
    "Lacerta": "Lac", "Leo": "Leo", "Leo Minor": "LMi", "Lepus": "Lep", "Libra": "Lib", "Lupus": "Lup",
    "Lynx": "Lyn", "Lyra": "Lyr", "Mensa": "Men", "Microscopium": "Mic", "Monoceros": "Mon", "Musca": "Mus",
    "Norma": "Nor", "Octans": "Oct", "Ophiuchus": "Oph", "Orion": "Ori", "Pavo": "Pav", "Pegasus": "Peg",
    "Perseus": "Per", "Phoenix": "Phe", "Pictor": "Pic", "Pisces": "Psc", "Piscis Austrinus": "PsA",
    "Puppis": "Pup", "Pyxis": "Pyx", "Reticulum": "Ret", "Sagitta": "Sge", "Sagittarius": "Sgr", "Scorpius": "Sco",
    "Sculptor": "Scl", "Scutum": "Sct", "Serpens": "Ser", "Sextans": "Sex", "Taurus": "Tau", "Telescopium": "Tel",
    "Triangulum": "Tri", "Triangulum Australe": "TrA", "Tucana": "Tuc", "Ursa Major": "UMa", "Ursa Minor": "UMi",
    "Vela": "Vel", "Virgo": "Vir", "Volans": "Vol", "Vulpecula": "Vul",
}  # fmt: skip
OTHER_SUBJECTS: tuple[str, ...] = ("milky_way", "moon", "sun", "planet", "horizon", "clouds", "city_lights", "aircraft", "no_sky")
SUBJECT_TAGS: frozenset[str] = frozenset(IAU_CONSTELLATIONS) | frozenset(OTHER_SUBJECTS)

# A camera maker or model as a file writes it: letters, digits and a few signs, at most 64 characters
CAMERA_TEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._+/()-]{0,63}")
# What a camera name may never hold: a date, a time of day, a signed decimal or two decimals, as a coordinate would
NOT_CAMERA_TEXT = re.compile(
    r"\d{4}[-:/.]\d{1,2}[-:/.]\d{1,2}|\d{1,2}:\d{2}|(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])"
    r"|[+-]\d+\.\d|\d\.\d+\D+\d+\.\d"
)
PAGE_ID_LINK = re.compile(r"https://commons\.wikimedia\.org/w/index\.php\?curid=[1-9][0-9]*")

# A licence as a Commons page states it: CC0, public domain, CC BY or CC BY-SA in any version, with or without a
# two-letter country port. NonCommercial and NoDerivatives never match: they are written into the name before the
# version (CC BY-NC 4.0), and nc and nd are refused as ports.
LICENCE = re.compile(r"CC0|Public domain|CC (?P<terms>BY(?:-SA)?) (?P<version>1\.0|2\.0|2\.1|2\.5|3\.0|4\.0)(?: (?P<port>(?!n[cd])[a-z]{2}))?")
CC_LINK = re.compile(
    r"https://creativecommons\.org/licenses/(?P<terms>by|by-sa)/(?P<version>\d\.\d)(?:/(?P<port>[a-z]{2}))?"
    r"(?:/(?:deed\.[A-Za-z_-]+|legalcode)?)?"
)
CC0_LINK = re.compile(r"https://creativecommons\.org/publicdomain/zero/1\.0(?:/(?:deed\.[A-Za-z_-]+|legalcode)?)?")
PUBLIC_DOMAIN_LINK = re.compile(
    r"https://creativecommons\.org/publicdomain/mark/1\.0(?:/(?:deed\.[A-Za-z_-]+)?)?"
    r"|https://commons\.wikimedia\.org/wiki/Template:PD-[A-Za-z0-9_-]+"
)
# The original file as Commons serves it (never a thumbnail or an archived version), with the query the API adds
COMMONS_ORIGINAL = re.compile(r"https://upload\.wikimedia\.org/wikipedia/commons/[0-9a-f]/[0-9a-f]{2}/[^/?#]+(?:\?[A-Za-z0-9_.=&%-]*)?")
SOURCE = re.compile(
    r"Photograph by (?P<author>[^|\n]+)\. Wikimedia Commons file page (?P<page>https://commons\.wikimedia\.org/wiki/File:\S+),"
    r" retrieved (?P<retrieved>\d{4}-\d{2}-\d{2})\."
)
# A date or a time of day in a file name or a credit, which would put a capture time into the registry
MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE_IN_NAME = re.compile(
    r"(?<!\d)(?:19|20)\d{2}[-_. ]?(?:0[1-9]|1[0-2])[-_. ]?(?:0[1-9]|[12]\d|3[01])(?!\d)"
    r"|(?<!\d)(?:0[1-9]|[12]\d|3[01])[-_. ](?:0[1-9]|1[0-2])[-_. ](?:19|20)\d{2}(?!\d)"
    r"|(?<!\d)[0-2]\d[.:h][0-5]\d(?:[.:][0-5]\d)?(?!\d)"
    r"|(?<!\d)\d{6}[-_ ]\d{6}(?!\d)"
    rf"|(?<![a-z]){MONTH}\.?[ _]+\d{{1,2}}(?:st|nd|rd|th)?,?[ _]+(?:19|20)\d{{2}}"
    rf"|(?<!\d)\d{{1,2}}(?:st|nd|rd|th)?[ _]+(?:of[ _]+)?{MONTH}\.?,?[ _]+(?:19|20)\d{{2}}",
    re.I,
)

Number = Union[StrictInt, StrictFloat]


class Credit(NamedTuple):
    author: str
    page: str
    retrieved: str


class Photo(BaseModel):
    """One photo: where its registry entry puts it, its tier and kind, and what its own file says."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: StrictStr = Field(pattern=r"^rp-[a-d]-[0-9]{2}$")
    downloadPath: StrictStr
    tier: Literal["A", "B", "C", "D"]
    kind: Literal[
        "camera_wide_field", "phone_night_sky", "moon_over_horizon", "sun_over_skyline", "overcast", "city_street",
        "fireworks", "aircraft_trails", "dark_interior", "screen",
    ]  # fmt: skip
    mediaKind: Literal["image", "video"]
    widthPx: StrictInt = Field(gt=0)
    heightPx: StrictInt = Field(gt=0)
    cameraMake: Optional[StrictStr] = None
    cameraModel: Optional[StrictStr] = None
    lensFocalLengthMm: Optional[Number] = Field(default=None, gt=0, le=CAMERA_LIMITS["focalLengthMm"])
    focalLength35mmEquivMm: Optional[Number] = Field(default=None, gt=0, le=CAMERA_LIMITS["focalLength35mmMm"])
    exposureTimeSec: Optional[Number] = Field(default=None, gt=0, le=CAMERA_LIMITS["exposureTimeSec"])
    hasCaptureTime: StrictBool
    hasUtcOffset: StrictBool
    hasPlace: StrictBool
    hemisphere: Optional[Literal["north", "south"]] = None
    hemisphereSource: Optional[Literal["file_metadata", "uploader_statement"]] = None
    statedSubject: tuple[StrictStr, ...]
    statedSubjectSource: StrictStr

    @field_validator("cameraMake", "cameraModel")
    @classmethod
    def _camera_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if not CAMERA_TEXT.fullmatch(value) or not re.search(r"[A-Za-z]", value) or NOT_CAMERA_TEXT.search(value):
            raise ValueError("not a camera maker or model as a file writes it")
        return value

    @field_validator("statedSubject")
    @classmethod
    def _closed_vocabulary(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = [tag for tag in value if tag not in SUBJECT_TAGS]
        if unknown:
            raise ValueError(f"not in the closed vocabulary: {unknown}")
        if len(set(value)) != len(value):
            raise ValueError("a tag is listed twice")
        return value

    @field_validator("statedSubjectSource")
    @classmethod
    def _page_id_link(cls, value: str) -> str:
        if not PAGE_ID_LINK.fullmatch(value):
            raise ValueError("not a Commons page id link, which carries no title")
        return value

    @model_validator(mode="after")
    def _rules(self) -> Photo:
        problems = []
        extension = self.downloadPath.rsplit(".", 1)[-1]
        if self.downloadPath != f"{CACHE_PREFIX}{self.id}/{self.id}.{extension}" or extension not in EXTENSIONS[self.mediaKind]:
            problems.append(f"downloadPath is not {CACHE_PREFIX}{self.id}/{self.id}.<{'|'.join(EXTENSIONS[self.mediaKind])}>")
        if self.id[3].upper() != self.tier:
            problems.append(f"tier {self.tier} does not match the id")
        if self.kind not in KINDS[self.tier]:
            problems.append(f"kind {self.kind} is not a tier {self.tier} kind")
        if self.tier in CAMERA_NAMED_TIERS and (self.cameraMake is None or self.cameraModel is None):
            problems.append(f"tier {self.tier} needs cameraMake and cameraModel")
        if self.tier == "A":
            if self.lensFocalLengthMm is None:
                problems.append("tier A needs lensFocalLengthMm")
            elif self.lensFocalLengthMm > WIDE_LENS_MAX_MM:
                problems.append(f"tier A needs a lens of at most {WIDE_LENS_MAX_MM:g} mm")
        if (self.hemisphere is None) != (self.hemisphereSource is None):
            problems.append("hemisphere and hemisphereSource come together")
        if self.hasPlace != (self.hemisphereSource == "file_metadata"):
            problems.append("hemisphereSource is file_metadata exactly when hasPlace")
        if self.hasUtcOffset and not self.hasCaptureTime:
            problems.append("hasUtcOffset without hasCaptureTime")
        if problems:
            raise ValueError(f"{self.id}: " + "; ".join(problems))
        return self


class Notes(BaseModel):
    """What the manifest's fields mean, in the manifest itself."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tiers: StrictStr
    fileFacts: StrictStr
    hemisphere: StrictStr
    statedSubject: StrictStr

    @field_validator("statedSubject")
    @classmethod
    def _says_it_is_a_statement(cls, value: str) -> str:
        if "uploader" not in value or "not ground truth" not in value:
            raise ValueError("must say the tags are the uploader's statement, not ground truth")
        return value


class Corpus(BaseModel):
    """The manifest: what it is, what its fields mean, and the photos in id order."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    about: StrictStr
    notes: Notes
    photos: tuple[Photo, ...]

    @field_validator("about")
    @classmethod
    def _says_what_it_is_not(cls, value: str) -> str:
        if "no ground truth" not in value or "not in the repository" not in value:
            raise ValueError("must say the corpus holds no ground truth and that the photos are not in the repository")
        return value

    @model_validator(mode="after")
    def _rules(self) -> Corpus:
        problems = []
        ids = [photo.id for photo in self.photos]
        problems += [f"id {ident} is listed more than once" for ident, count in sorted(Counter(ids).items()) if count > 1]
        if ids != sorted(ids):
            problems.append("the photos are not in id order")
        pages = Counter(photo.statedSubjectSource for photo in self.photos)
        problems += [f"page {page} is listed more than once" for page, count in sorted(pages.items()) if count > 1]
        if len(self.photos) > MAX_PHOTOS:
            problems.append(f"{len(self.photos)} photos, more than {MAX_PHOTOS}")
        tiers = Counter(photo.tier for photo in self.photos)
        for tier, minimum in TIER_MINIMUMS.items():
            if tiers[tier] < minimum:
                problems.append(f"tier {tier} holds {tiers[tier]} photos, fewer than its minimum of {minimum}")
        kinds = Counter(photo.kind for photo in self.photos)
        for kind, minimum in KIND_MINIMUMS.items():
            if kinds[kind] < minimum:
                problems.append(f"tier C holds {kinds[kind]} {kind} photos, fewer than its minimum of {minimum}")
        for tier in BOTH_HEMISPHERE_TIERS:
            sides = {photo.hemisphere for photo in self.photos if photo.tier == tier}
            problems += [f"tier {tier} has no {side} photo" for side in ("north", "south") if side not in sides]
        makers = {photo.cameraMake.lower() for photo in self.photos if photo.tier == "B" and photo.cameraMake}
        problems += [f"tier B has no photo from {maker}" for maker in REQUIRED_PHONE_MAKERS if maker not in makers]
        others = len(makers - set(REQUIRED_PHONE_MAKERS))
        if others < OTHER_PHONE_MAKERS_MINIMUM:
            problems.append(
                f"tier B has {others} makers besides {', '.join(REQUIRED_PHONE_MAKERS)}, fewer than {OTHER_PHONE_MAKERS_MINIMUM}"
            )
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def to_json_dict(self) -> dict[str, Any]:
        """The manifest as JSON; a field a file does not give is left out."""
        return self.model_dump(mode="json", exclude_none=True)


def dumps_corpus(corpus: Corpus) -> str:
    """The manifest file's exact text."""
    return json.dumps(corpus.to_json_dict(), indent=2, ensure_ascii=False) + "\n"


def load_corpus(path: Path = MANIFEST) -> Corpus:
    """The validated manifest; pydantic's ValidationError names every rule it breaks."""
    return Corpus.model_validate(json.loads(path.read_text(encoding="utf-8")))


def corpus_downloads(registry: Path = REGISTRY) -> list[dict[str, Any]]:
    """The registry's entries under .cache/real-photos/, in the registry's order."""
    entries = json.loads(registry.read_text(encoding="utf-8"))["downloads"]
    return [entry for entry in entries if entry["path"].startswith(CACHE_PREFIX)]


def licence_allowed(licence: str) -> bool:
    """True for CC0, public domain, CC BY and CC BY-SA in any version, written as a Commons page states them."""
    return LICENCE.fullmatch(licence) is not None


def licence_link_matches(licence: str, url: str) -> bool:
    """True when the link is the deed of that very licence: the same terms, version and port."""
    stated = LICENCE.fullmatch(licence)
    if stated is None:
        return False
    if licence == "CC0":
        return CC0_LINK.fullmatch(url) is not None
    if licence == "Public domain":
        return PUBLIC_DOMAIN_LINK.fullmatch(url) is not None
    link = CC_LINK.fullmatch(url)
    return link is not None and (link["terms"], link["version"], link["port"]) == (
        stated["terms"].lower(), stated["version"], stated["port"]
    )  # fmt: skip


def credit(entry: dict[str, Any]) -> Credit:
    """The author, the Commons file page and the retrieval date a registry entry's source names."""
    match = SOURCE.fullmatch(entry["source"])
    if match is None:
        raise ValueError(f"{entry['path']}: the source does not name the author, the page and the retrieval date")
    return Credit(match["author"], match["page"], match["retrieved"])


def registry_problems(corpus: Corpus, downloads: list[dict[str, Any]]) -> list[str]:
    """What the corpus entries of the registry break: one entry per photo and the reverse, Commons originals fetched
    by the registry's script, the size limits, an allowed licence with its own link, a credit, and no date in a name."""
    problems = []
    listed = Counter(entry["path"] for entry in downloads)
    paths = [photo.downloadPath for photo in corpus.photos]
    for path in paths:
        if listed[path] == 0:
            problems.append(f"{path}: no registry entry")
        elif listed[path] > 1:
            problems.append(f"{path}: {listed[path]} registry entries")
    for entry in downloads:
        path = entry["path"]
        if path not in paths:
            problems.append(f"{path}: a registry entry with no photo in the manifest")
        if entry["fetch"] != FETCH_SCRIPT:
            problems.append(f"{path}: fetched by {entry['fetch']}")
        if not COMMONS_ORIGINAL.fullmatch(entry["url"]):
            problems.append(f"{path}: the URL is not a Commons original")
        if entry["bytes"] > MAX_PHOTO_BYTES:
            problems.append(f"{path}: larger than {MAX_PHOTO_BYTES} bytes")
        if not licence_allowed(entry["licence"]):
            problems.append(f"{path}: licence {entry['licence']!r} is not on the allow list")
        elif not licence_link_matches(entry["licence"], entry["licenceUrl"]):
            problems.append(f"{path}: the licence link is not that of {entry['licence']}")
        source = SOURCE.fullmatch(entry["source"])
        if source is None:
            problems.append(f"{path}: the source does not name the author, the page and the retrieval date")
        named = [unquote(entry["url"].split("?", 1)[0])] + ([source["author"], unquote(source["page"])] if source else [])
        if any(DATE_IN_NAME.search(text) for text in named):
            problems.append(f"{path}: the URL or the source carries a date or a time of day")
    if sum(entry["bytes"] for entry in downloads) > MAX_CORPUS_BYTES:
        problems.append(f"the corpus is larger than {MAX_CORPUS_BYTES} bytes")
    return problems


def credit_rows(corpus: Corpus, downloads: list[dict[str, Any]]) -> list[str]:
    """One Markdown table row per photo, in manifest order: id, author, licence with its link, and the file page."""
    by_path = {entry["path"]: entry for entry in downloads}
    rows = []
    for photo in corpus.photos:
        entry = by_path[photo.downloadPath]
        page_id = photo.statedSubjectSource.rsplit("=", 1)[-1]
        rows.append(
            f"| `{photo.id}` | {credit(entry).author} | [{entry['licence']}]({entry['licenceUrl']}) |"
            f" [{page_id}]({photo.statedSubjectSource}) |"
        )
    return rows


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1 << 22):
            digest.update(block)
    return digest.hexdigest()


def read_facts(path: Path) -> dict[str, Any]:
    """What the upload path's own readers say of a file: `sniff` for its type, `probe_media` for its size and the
    router's `_hints` for its metadata, exactly as `POST /api/ingest` calls them. The file's position and capture
    time are read only to say whether they are there and on which side of the equator; neither is returned."""
    from services.ingest.probe import probe_media
    from services.ingest.router import _hints
    from services.ingest.store import SNIFF_BYTES, Received, sniff

    with path.open("rb") as handle:
        file_type = sniff(handle.read(SNIFF_BYTES))
    facts = probe_media(path, file_type)
    size, digest = path.stat().st_size, sha256_of(path)
    hints = _hints(Received(partial=path, sha256=digest, byte_size=size, file_type=file_type), facts)
    camera = hints.get("camera") or {}
    time = hints.get("captureTime")
    place = hints.get("location")
    return {
        "bytes": size,
        "sha256": digest,
        "extension": file_type.extension,
        "mediaKind": facts.media_kind,
        "widthPx": facts.width,
        "heightPx": facts.height,
        "cameraMake": camera.get("make"),
        "cameraModel": camera.get("model"),
        "lensFocalLengthMm": camera.get("focalLengthMm"),
        "focalLength35mmEquivMm": camera.get("focalLength35mmMm"),
        "exposureTimeSec": camera.get("exposureTimeSec"),
        "hasCaptureTime": time is not None,
        "hasUtcOffset": time is not None and time["utcOffsetMinutes"] is not None and time["source"].get("basis", {}).get("offsetSource") != "place_time_zone_agrees_with_gps_time_stamp",
        "hasPlace": place is not None,
        "hemisphere": None if place is None else ("south" if place["latDeg"] < 0 else "north"),
    }
