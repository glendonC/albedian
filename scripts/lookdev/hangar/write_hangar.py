"""Write the hangar's facts file and provenance record from the craft files build_craft.py made (ASSETS lane).

From the repo root, after `blender -b --factory-startup -P scripts/lookdev/hangar/build_craft.py -- --craft <id> --out
apps/web/public/models/hangar/<id>` for each built craft:
    uv run python scripts/lookdev/hangar/write_hangar.py            # write hangar.json and the record's entries
    uv run python scripts/lookdev/hangar/write_hangar.py --verify   # check both are what the files say, and the budgets

Writes:
- apps/web/public/models/hangar/hangar.json: for each built model its files (bytes, triangles, texture edge), its true
  size in metres in the glTF frame (+Z its direction of travel, +Y up, one unit one metre), the dimension its scale
  comes from with NASA's page, and the picker's entries (HANGAR in crafts.py) with each sourced speed and fact and the
  page it was read on;
- the hangar's entries in the models folder's shared record, apps/web/public/models/PROVENANCE.json, whose Local Bubble
  and Moon entries their own bakes keep: every hangar file, its SHA-256 and sources (NASA's model and picture, and
  each page a fact or a scale comes from), under NASA's media guidelines.
Everything drawn from these files is simulated in the evidence classes: the app draws the craft, nothing is measured.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.lookdev.hangar.crafts import (  # noqa: E402
    BUDGET_BYTES,
    CRAFTS,
    HANGAR,
    LOD_RATIO,
    NASA3D_RAW,
    NASA3D_REPO,
    NASA_GUIDELINES_URL,
    PICKER_EDGE,
    TEXTURE_EDGE,
)
from services.contracts.provenance import AssetProvenanceRecord, dumps_record, sha256_file  # noqa: E402

HANGAR_DIR = REPO_ROOT / "apps" / "web" / "public" / "models" / "hangar"
# The models folder's shared record; this script owns the entries under hangar/ and these two sources.
RECORD_PATH = REPO_ROOT / "apps" / "web" / "public" / "models" / "PROVENANCE.json"
# The sources the two other bakes of that record pin in place: scripts/bake_moon_mesh.py writes its own first and
# scripts/bake_local_bubble_mesh.py its own last (each module's SOURCE_ID).
MOON_SOURCE_ID = "nasa-svs-4720-ldem-16"
BUBBLE_SOURCE_ID = "oneill-2024-local-bubble"
MODELS_SOURCE = "nasa-3d-resources"
PAGES_SOURCE = "nasa-science-mission-pages"
BUILD_SCRIPT = "scripts/lookdev/hangar/build_craft.py"
THIS_SCRIPT = "scripts/lookdev/hangar/write_hangar.py"
RETRIEVED = "2026-11-19"
LEVELS = ("lod0", "lod1", "lod2")

FACTS_CHANGES = (
    "Written from the built craft files and from facts read on NASA's pages, not copied: each model's file sizes, "
    "triangle counts and true size in metres measured from its own glb, and each speed and fact typed from the NASA "
    "page named beside it, with a short quote from that page."
)

NASA_LICENSE = {
    "spdx": "LicenseRef-NASA-Media-Guidelines",
    "name": "NASA Images and Media Usage Guidelines",
    "url": NASA_GUIDELINES_URL,
    "conditions": (
        "NASA content, 3D models included, is generally not subject to copyright in the United States. NASA should "
        "be acknowledged as the source, no use may imply NASA's endorsement, and the NASA insignia and logotype are "
        "not in the public domain, so none is shown."
    ),
}


def glb_json(path: Path) -> dict:
    data = path.read_bytes()
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2 or length != len(data):
        raise SystemExit(f"{path} is not a glTF 2.0 binary")
    chunk_len, chunk_type = struct.unpack_from("<I4s", data, 12)
    if chunk_type != b"JSON":
        raise SystemExit(f"{path} has no JSON chunk first")
    return json.loads(data[20 : 20 + chunk_len])


def glb_stats(path: Path) -> dict:
    """Triangles and the bounds in metres, from the accessors (glTF requires POSITION's min and max)."""
    gltf = glb_json(path)
    for node in gltf.get("nodes", []):
        if any(k in node for k in ("matrix", "rotation", "scale", "translation")):
            raise SystemExit(f"{path}: node {node.get('name')} carries a transform; the build applies every transform")
    tris = 0
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for mesh in gltf["meshes"]:
        for prim in mesh["primitives"]:
            if prim.get("mode", 4) != 4:
                raise SystemExit(f"{path}: a primitive is not triangles")
            pos = gltf["accessors"][prim["attributes"]["POSITION"]]
            count = gltf["accessors"][prim["indices"]]["count"] if "indices" in prim else pos["count"]
            tris += count // 3
            lo = [min(a, b) for a, b in zip(lo, pos["min"])]
            hi = [max(a, b) for a, b in zip(hi, pos["max"])]
    exts = sorted(set(gltf.get("extensionsUsed", [])))
    return {"triangles": tris, "min": [round(x, 3) for x in lo], "max": [round(x, 3) for x in hi], "extensions": exts}


def built_models() -> list[str]:
    return [m for m in CRAFTS if (HANGAR_DIR / m / f"{m}_lod0.glb").is_file()]


def build_facts(models: list[str]) -> dict:
    out_models = {}
    for m in models:
        recipe = CRAFTS[m]
        files = {}
        bounds = None
        for level in LEVELS:
            p = HANGAR_DIR / m / f"{m}_{level}.glb"
            st = glb_stats(p)
            files[level] = {
                "path": f"{m}/{m}_{level}.glb",
                "bytes": p.stat().st_size,
                "triangles": st["triangles"],
                "textureEdgeMax": TEXTURE_EDGE[level],
                "budgetBytes": {**BUDGET_BYTES, **recipe.get("budget_bytes", {})}[level],
            }
            if level == "lod0":
                bounds = {"min": st["min"], "max": st["max"]}
        pic = HANGAR_DIR / m / f"{m}_picker.webp"
        files["picker"] = {"path": f"{m}/{m}_picker.webp", "bytes": pic.stat().st_size, "edgePx": PICKER_EDGE, "budgetBytes": BUDGET_BYTES["picker"]}
        sf = recipe["scale_from"]
        scale_from = (
            {"native": True, "page": sf["page"], "quote": sf["quote"]}
            if sf.get("native")
            else {"part": sf["material"], "metres": sf["metres"], "page": sf["page"], "quote": sf["quote"]}
        )
        out_models[m] = {
            "title": recipe["title"],
            "kind": recipe["kind"],
            "files": files,
            "boundsM": bounds,
            "bodyRadiusM": recipe["body_radius_m"],
            "lodTriangleShare": recipe.get("lod_ratio", LOD_RATIO),
            "scaleFrom": scale_from,
        }
    entries = [e for e in HANGAR if e["model"] in out_models]
    return {
        "format": "albedian.hangar",
        "formatVersion": 1,
        "evidenceClass": "simulated",
        "frame": {"forward": "+Z", "up": "+Y", "unit": "metre", "origin": "the centre of the part the scale is taken from"},
        "levels": {"lod0": "close view", "lod1": "chase camera", "lod2": "far or phone view"},
        "models": out_models,
        "entries": entries,
    }


def _quoted(name: str) -> str:
    return name.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def build_record(models: list[str]) -> dict:
    """The hangar's entries in the models record: two sources (NASA's 3D Resources, NASA's Science pages), so the
    Credits page maps the whole hangar with two lines however many craft it holds; each file's note pins its input."""
    sources = {
        MODELS_SOURCE: {
            "id": MODELS_SOURCE,
            "title": "NASA 3D Resources",
            "url": NASA3D_REPO,
            "retrieved": RETRIEVED,
            "license": NASA_LICENSE,
            "credit": "NASA",
            "note": "NASA's own models and its renders of them; the repository's README says the assets are free and without copyright. Each file built from it names its input's address, size and SHA-256 in its note.",
        },
        PAGES_SOURCE: {
            "id": PAGES_SOURCE,
            "title": "NASA Science mission pages",
            "url": "https://science.nasa.gov/",
            "retrieved": RETRIEVED,
            "license": NASA_LICENSE,
            "credit": "NASA",
            "note": "Each speed, size and date in hangar/hangar.json names the NASA page it was read on, with a short quote from that page.",
        },
    }
    files = []
    for m in models:
        src = CRAFTS[m]["source"]
        base = f"{NASA3D_RAW}/{_quoted(src['folder'])}/"
        model_url = src.get("url") or f"{base}{_quoted(src['file'])}"
        command = f"blender -b --factory-startup -P {BUILD_SCRIPT} -- --craft {m} --out apps/web/public/models/hangar/{m}"
        scaled = (
            "kept at the metre scale NASA's model is built in"
            if CRAFTS[m]["scale_from"].get("native")
            else "scaled from the dimension hangar/hangar.json names with its NASA page"
        )
        model_note = f"Built from {model_url} ({src['bytes']} bytes, SHA-256 {src['sha256']}); {scaled}."
        for level in LEVELS:
            files.append(
                {
                    "path": f"hangar/{m}/{m}_{level}.glb",
                    "sha256": sha256_file(HANGAR_DIR / m / f"{m}_{level}.glb"),
                    "sources": [MODELS_SOURCE, PAGES_SOURCE],
                    "producedBy": {"method": "script", "script": BUILD_SCRIPT, "command": command, "changes": BUILD_SENTENCES[f"lod0_{CRAFTS[m]['changes']}" if level == "lod0" else level]},
                    "note": model_note,
                }
            )
        if src.get("picture"):
            picker_sentence = BUILD_SENTENCES["picker"]
            picture_url = src.get("picture_url") or f"{base}{_quoted(src['picture'])}"
            picker_note = f"From NASA's render {picture_url} ({src['picture_bytes']} bytes, SHA-256 {src['picture_sha256']})."
        else:
            picker_sentence = BUILD_SENTENCES["picker_render"]
            picker_note = f"Rendered from this folder's lod0 build of {model_url}."
        files.append(
            {
                "path": f"hangar/{m}/{m}_picker.webp",
                "sha256": sha256_file(HANGAR_DIR / m / f"{m}_picker.webp"),
                "sources": [MODELS_SOURCE],
                "producedBy": {"method": "script", "script": BUILD_SCRIPT, "command": command, "changes": picker_sentence},
                "note": picker_note,
            }
        )
    files.append(
        {
            "path": "hangar/hangar.json",
            "sha256": sha256_file(HANGAR_DIR / "hangar.json"),
            "sources": [MODELS_SOURCE, PAGES_SOURCE],
            "producedBy": {"method": "script", "script": THIS_SCRIPT, "command": f"uv run python {THIS_SCRIPT}", "changes": FACTS_CHANGES},
        }
    )
    # The models folder's record is shared with the Local Bubble and Moon bakes: keep their entries as they are.
    existing = json.loads(RECORD_PATH.read_text(encoding="utf-8"))
    others_sources = [s for s in existing["sources"] if s["id"] not in sources]
    others_files = [f for f in existing["files"] if not f["path"].startswith("hangar/")]
    first = [s for s in others_sources if s["id"] == MOON_SOURCE_ID]
    last = [s for s in others_sources if s["id"] == BUBBLE_SOURCE_ID]
    middle = [s for s in others_sources if s not in first and s not in last]
    record = {
        "format": "albedian.asset-provenance",
        "formatVersion": 1,
        # The Moon bake keeps its source first and the Local Bubble bake keeps its own last, so the hangar's go after
        # the Moon's, then any other source as the record has it, then the bubble's; every bake sorts the files by
        # path: any of the three rewrites the record to the same text.
        "sources": first + sorted(sources.values(), key=lambda s: s["id"]) + middle + last,
        "files": sorted(others_files + files, key=lambda f: f["path"]),
        "fileSets": existing["fileSets"],
    }
    AssetProvenanceRecord.model_validate(record)
    return record


def _build_changes() -> dict[str, str]:
    """build_craft.py's CHANGES constant, read from its source (that file imports bpy, so it is not imported here)."""
    import ast

    tree = ast.parse((REPO_ROOT / BUILD_SCRIPT).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CHANGES" for t in node.targets):
            return ast.literal_eval(node.value)
    raise SystemExit(f"{BUILD_SCRIPT} has no CHANGES")


BUILD_SENTENCES = _build_changes()


def over_budget(facts: dict) -> list[str]:
    bad = []
    for m, model in facts["models"].items():
        for level, f in model["files"].items():
            if f["bytes"] > f["budgetBytes"]:
                bad.append(f"{m} {level}: {f['bytes']} bytes over its budget of {f['budgetBytes']}")
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args(argv)
    models = built_models()
    if not models:
        print("no built craft in", HANGAR_DIR)
        return 1
    facts = build_facts(models)
    facts_text = json.dumps(facts, indent=2, ensure_ascii=False) + "\n"
    bad = over_budget(facts)
    if bad:
        print("\n".join(bad))
        return 1
    facts_path = HANGAR_DIR / "hangar.json"
    record_path = RECORD_PATH
    if args.verify:
        ok = facts_path.is_file() and facts_path.read_text(encoding="utf-8") == facts_text
        if not ok:
            print("hangar.json differs from what the files say; run without --verify")
            return 1
        record_text = dumps_record(AssetProvenanceRecord.model_validate(build_record(models)))
        if record_path.read_text(encoding="utf-8") != record_text:
            print("PROVENANCE.json differs from the files; run without --verify")
            return 1
        print(f"hangar verified: {len(models)} models, {len(facts['entries'])} entries")
        return 0
    facts_path.write_text(facts_text, encoding="utf-8")
    record_path.write_text(dumps_record(AssetProvenanceRecord.model_validate(build_record(models))), encoding="utf-8")
    for m, model in facts["models"].items():
        sizes = ", ".join(f"{k} {v['bytes']} B" + (f" {v['triangles']} tris" if "triangles" in v else "") for k, v in model["files"].items())
        print(f"{m}: {sizes}; bounds {model['boundsM']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
