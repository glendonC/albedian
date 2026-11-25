"""Half-side copies of the two sky pictures the app's loader keeps, for a phone's memory (sky-half-side-1).

The Real sky layer keeps ESO's panorama (6000 x 3000) and the flight keeps the starless glow baked from it (6000 x 3000);
decoded at four bytes a pixel each takes 72 MB before mipmaps. A phone takes these copies instead, at 3000 x 1500
(18 MB each): the same pictures, the same projection (so eso0932a_registration.json applies unchanged), each pixel the
Lanczos-filtered mean of the four it replaces. The panorama is written as a JPEG at quality 94 with no chroma
subsampling, as ESO's own file is; the glow as a lossless WebP, as its bake writes the full-size one.

    uv run python scripts/make_sky_half_side.py           # write both copies and their entries in the record
    uv run python scripts/make_sky_half_side.py --verify  # remake in memory and compare with the committed files
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from services.contracts.provenance import AssetProvenanceRecord, dumps_record  # noqa: E402

TEXTURES = REPO_ROOT / "apps" / "web" / "public" / "textures"
RECORD_PATH = TEXTURES / "PROVENANCE.json"
COMMAND = "uv run python scripts/make_sky_half_side.py"

# How each copy differs from its source, written into its record as producedBy.changes
CHANGES_PANORAMA = (
    "Scaled to half its width and height (3000 by 1500) with a Lanczos filter, the same picture and projection, and "
    "saved again as a JPEG at quality 94 with no chroma subsampling, for a phone's memory."
)
CHANGES_GLOW = (
    "The starless glow scaled to half its width and height (3000 by 1500) with a Lanczos filter, the same picture and "
    "projection, and saved again as a lossless WebP, for a phone's memory."
)

COPIES = (
    {
        "source": "eso_milkyway_panorama.jpg",
        "output": "eso_milkyway_panorama_3k.jpg",
        "sources": ["eso0932a"],
        "changes": CHANGES_PANORAMA,
    },
    {
        "source": "eso_milkyway_diffuse_6k.webp",
        "output": "eso_milkyway_diffuse_3k.webp",
        "sources": ["eso0932a", "gaia-dr3", "hipparcos-2007"],
        "changes": CHANGES_GLOW,
    },
)


def half_side(source: Path, output: str) -> bytes:
    with Image.open(source) as im:
        rgb = im.convert("RGB")
        w, h = rgb.size
        small = rgb.resize((w // 2, h // 2), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    if output.endswith(".jpg"):
        small.save(buffer, "JPEG", quality=94, subsampling=0, optimize=True)
    else:
        small.save(buffer, "WEBP", lossless=True, quality=100, method=6)
    return buffer.getvalue()


def record_entry(copy: dict, data: bytes) -> dict:
    source = TEXTURES / copy["source"]
    with Image.open(io.BytesIO(data)) as im:
        w, h = im.size
    return {
        "path": copy["output"],
        "sha256": hashlib.sha256(data).hexdigest(),
        "sources": copy["sources"],
        "producedBy": {"method": "script", "script": "scripts/make_sky_half_side.py", "command": COMMAND, "changes": copy["changes"]},
        "note": (
            f"Made from this folder's {copy['source']} ({source.stat().st_size} bytes, SHA-256 "
            f"{hashlib.sha256(source.read_bytes()).hexdigest()}) at {w} by {h}; the phone's loader keeps it in place of "
            "the full-size picture. Same projection, so eso0932a_registration.json applies unchanged; the credit is "
            "the source's."
        ),
    }


def updated_record(record_text: str, entries: list[dict]) -> str:
    """The folder's record with these entries replaced in place (or added after the rest) and every other kept."""
    record = json.loads(record_text)
    by_path = {e["path"]: e for e in entries}
    files, seen = [], set()
    for existing in record["files"]:
        if existing["path"] in by_path:
            files.append(by_path[existing["path"]])
            seen.add(existing["path"])
        else:
            files.append(existing)
    files += [e for e in entries if e["path"] not in seen]
    record["files"] = files
    return dumps_record(AssetProvenanceRecord.model_validate(record))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verify", action="store_true", help="remake in memory and compare with the committed files")
    args = parser.parse_args()
    made = {c["output"]: half_side(TEXTURES / c["source"], c["output"]) for c in COPIES}
    entries = [record_entry(c, made[c["output"]]) for c in COPIES]
    record_text = updated_record(RECORD_PATH.read_text(encoding="utf-8"), entries)
    if args.verify:
        bad = [name for name, data in made.items() if not (TEXTURES / name).is_file() or (TEXTURES / name).read_bytes() != data]
        if RECORD_PATH.read_text(encoding="utf-8") != record_text:
            bad.append(RECORD_PATH.name)
        print("sky half-side copies verified" if not bad else f"differs: {', '.join(bad)}")
        return 1 if bad else 0
    for name, data in made.items():
        (TEXTURES / name).write_bytes(data)
        print(f"{name}: {len(data)} bytes")
    RECORD_PATH.write_text(record_text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
