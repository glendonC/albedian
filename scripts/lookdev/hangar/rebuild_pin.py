"""The hangar's rebuild pin (ASSETS lane, rebuild-when-recipe-moves-one): scripts/lookdev/hangar/rebuild_pin.json.

Rebuilding every craft in Blender, to show that the committed models still follow their recipes byte for byte, took
about ten minutes of every whole gate on the development laptop (617 s for the forty at a load of 4 to 6, the station
341 s of it), for models that did not change. So each craft's recipe has a digest, and the pin holds, from the last
rebuild that came out byte for byte as shipped, that digest beside the SHA-256 of each file the rebuild wrote (checking
the forty against it took 0.84 s):
- the recipe, part by part: build_craft.py's bytes and those of every module of this repository it imports (read from
  its source, so a module it comes to import joins the recipe), the value of each name it imports from crafts.py (of
  CRAFTS, the craft's own entry, which pins NASA's source by its size and SHA-256; build_craft.py refuses any other
  file), and the Blender the models are built with (BUILT_WITH);
- judge(), which tests/integration/test_hangar_assets.py runs for every craft: the shipped files against their SHA-256
  in PROVENANCE.json, the recipe against the pin and, while the recipe is unmoved, the files against the pin. The craft
  is rebuilt in Blender only when its recipe has moved from the pin, and it then fails until it is rebuilt and
  repinned, or when ALBEDIAN_HANGAR_REBUILD_ALL=1, which rebuilds every craft. A rebuild that cannot run on this
  machine fails: nothing here skips;
- this script, which writes a craft's pin only after rebuilding it and finding every file byte for byte as shipped.

From the repo root, after rebuilding a craft whose recipe moved (build_craft.py into its folder, then write_hangar.py):
    uv run python scripts/lookdev/hangar/rebuild_pin.py --craft voyager [--craft cassini ...]
    uv run python scripts/lookdev/hangar/rebuild_pin.py --all
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.lookdev.hangar import crafts  # noqa: E402

HANGAR_DIR = REPO_ROOT / "apps" / "web" / "public" / "models" / "hangar"
RECORD_PATH = REPO_ROOT / "apps" / "web" / "public" / "models" / "PROVENANCE.json"
PIN_PATH = REPO_ROOT / "scripts" / "lookdev" / "hangar" / "rebuild_pin.json"
BUILD_SCRIPT = "scripts/lookdev/hangar/build_craft.py"
THIS_SCRIPT = "scripts/lookdev/hangar/rebuild_pin.py"
CRAFTS_MODULE = "scripts.lookdev.hangar.crafts"
# The Blender the committed models were built with; another version may write other bytes from the same recipe.
BUILT_WITH = "Blender 5.2.3"
SWITCH = "ALBEDIAN_HANGAR_REBUILD_ALL"
LEVELS = ("lod0", "lod1", "lod2", "picker")
# the station's build, its occlusion cast from the 3,108,229 vertices of its lod0, took 446 s at a load of 6
BUILD_TIMEOUT_S = 1200
PIN_FORMAT = "albedian-hangar-rebuild-pin"
PIN_ABOUT = (
    "For each hangar craft, from its last rebuild in Blender that came out byte for byte as shipped: its recipe's "
    "digest, part by part, and the SHA-256 of each file the rebuild wrote. Written only by "
    f"{THIS_SCRIPT}, which rebuilds a craft before it pins it; read by tests/integration/test_hangar_assets.py, which "
    f"rebuilds a craft only when its recipe has moved from this pin, or every craft when {SWITCH}=1."
)
PINNED = "pinned: its files are as recorded and its recipe as pinned; not rebuilt"


class CannotRebuild(Exception):
    """The rebuild could not run, or the build did not finish: the craft fails, it is never passed or skipped."""


@dataclass
class Verdict:
    """The path a craft's check took and what it found wrong (empty when it passes)."""

    path: str
    problems: list[str] = field(default_factory=list)
    rebuilt: bool = False


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def shipped_names(craft: str) -> list[str]:
    return [f"{craft}_{level}.webp" if level == "picker" else f"{craft}_{level}.glb" for level in LEVELS]


def shipped_digests(craft: str, hangar_dir: Path = HANGAR_DIR) -> dict[str, str]:
    """The SHA-256 of each of the craft's files present in the folder (a missing one is left out)."""
    folder = hangar_dir / craft
    return {name: _sha((folder / name).read_bytes()) for name in shipped_names(craft) if (folder / name).is_file()}


def canonical(value: object) -> str:
    """One text for one recipe value: a tuple is not a list, an int not a float nor a bool, and a dict keeps its order
    (a reorder moves the pin and rebuilds, never the other way round). A kind it cannot write is refused."""
    kind = type(value)
    if value is None or kind in (bool, int, float):
        return repr(value)
    if kind is str:
        return json.dumps(value)
    if kind is list:
        return "[" + ",".join(canonical(v) for v in value) + "]"
    if kind is tuple:
        return "(" + ",".join(canonical(v) for v in value) + ")"
    if kind is dict:
        return "{" + ",".join(canonical(k) + ":" + canonical(v) for k, v in value.items()) + "}"
    raise TypeError(f"a recipe value of kind {kind.__name__} has no canonical form")


def _ours(module: str) -> bool:
    head = module.split(".")[0]
    return (REPO_ROOT / head).is_dir() or (REPO_ROOT / f"{head}.py").is_file()


def _module_file(module: str, root: Path, required: bool = True) -> str | None:
    """The file of one module of this repository under root, None for a namespace package (it has no code)."""
    rel = module.replace(".", "/")
    if (root / f"{rel}.py").is_file():
        return f"{rel}.py"
    if (root / rel / "__init__.py").is_file():
        return f"{rel}/__init__.py"
    if (root / rel).is_dir() or not required:
        return None
    raise ValueError(f"the build imports {module}, which is not a file of the repository under {root}")


def _repo_imports(source: bytes, rel: str) -> list[tuple[str, list[str] | None]]:
    """Each import of a module of this repository in one file, anywhere in it: (module, the names taken, or None)."""
    found: list[tuple[str, list[str] | None]] = []
    for node in ast.walk(ast.parse(source, filename=rel)):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                raise ValueError(f"{rel}: a relative import, which the recipe does not follow")
            if node.module and _ours(node.module):
                found.append((node.module, [a.name for a in node.names]))
        elif isinstance(node, ast.Import):
            found.extend((a.name, None) for a in node.names if _ours(a.name))
    return found


def recipe_parts(craft: str, root: Path = REPO_ROOT) -> dict[str, str]:
    """The digest of each input of one craft's build, by name; the scripts are read under root."""
    parts: dict[str, str] = {}
    todo = [BUILD_SCRIPT]
    while todo:
        rel = todo.pop(0)
        if rel in parts:
            continue
        source = (root / rel).read_bytes()
        parts[rel] = _sha(source)
        for module, names in _repo_imports(source, rel):
            if module == CRAFTS_MODULE and names is not None and "*" not in names:
                for name in names:
                    value = getattr(crafts, name)
                    key = f"crafts.{name}"
                    if name == "CRAFTS":
                        value, key = value[craft], f"crafts.CRAFTS[{craft!r}]"
                    parts[key] = _sha(canonical(value).encode("utf-8"))
                continue
            own = _module_file(module, root)
            if own is not None:
                todo.append(own)
            for name in names or []:
                sub = _module_file(f"{module}.{name}", root, required=False)
                if sub is not None:
                    todo.append(sub)
    parts["tool"] = BUILT_WITH
    return dict(sorted(parts.items()))


def moved_from_pin(craft: str, pin: Mapping, root: Path = REPO_ROOT) -> list[str]:
    """The inputs of the craft's recipe whose digest is not the pinned one; ["no pin"] for a craft not pinned."""
    pinned = pin.get("crafts", {}).get(craft)
    if pinned is None:
        return ["no pin"]
    now, was = recipe_parts(craft, root), pinned["recipe"]
    return sorted(k for k in now.keys() | was.keys() if now.get(k) != was.get(k))


def blender_version() -> str | None:
    blender = shutil.which("blender")
    if blender is None:
        return None
    out = subprocess.run([blender, "--version"], capture_output=True, text=True, timeout=120).stdout
    return out.splitlines()[0] if out else None


def rebuild_inputs(craft: str) -> list[str]:
    """The NASA files the build reads for one craft, as the shared cache holds them and data/downloads.json lists them
    (a craft whose picker is rendered from the build reads no NASA picture)."""
    src = crafts.CRAFTS[craft]["source"]
    return [f"{crafts.CACHE}/{src['local']}/{src[key]}" for key in ("file", "picture") if src.get(key) is not None]


def rebuild(craft: str, out: Path, root: Path = REPO_ROOT) -> dict[str, str]:
    """Build the craft again into out with the Blender the hangar was built with; the SHA-256 of each file written."""
    version = blender_version()
    if version is None or not version.startswith(BUILT_WITH):
        raise CannotRebuild(f"needs {BUILT_WITH} (found {version})")
    missing = [p for p in rebuild_inputs(craft) if not (root / p).is_file()]
    if missing:
        raise CannotRebuild(f"needs the NASA source files in the shared cache, as data/downloads.json lists them: {', '.join(missing)}")
    out.mkdir(parents=True, exist_ok=True)
    cmd = [shutil.which("blender"), "-b", "--factory-startup", "-P", str(root / BUILD_SCRIPT), "--", "--craft", craft, "--out", str(out)]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=BUILD_TIMEOUT_S, cwd=root)
    except subprocess.TimeoutExpired:
        raise CannotRebuild(f"the build ran past its {BUILD_TIMEOUT_S} s bound") from None
    if done.returncode != 0:
        raise CannotRebuild(f"build_craft.py exited {done.returncode}: {done.stderr[-2000:]}")
    return {name: _sha((out / name).read_bytes()) for name in shipped_names(craft) if (out / name).is_file()}


def rebuild_all_switch(environ: Mapping[str, str] = os.environ) -> bool:
    """ALBEDIAN_HANGAR_REBUILD_ALL: 1 rebuilds every craft; unset, empty or 0 rebuilds only those whose recipe moved."""
    value = environ.get(SWITCH, "")
    if value in ("", "0"):
        return False
    if value == "1":
        return True
    raise ValueError(f"{SWITCH} is 1 to rebuild every craft, or unset, empty or 0; not {value!r}")


def read_pin(path: Path = PIN_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def dumps_pin(pin: Mapping) -> str:
    return json.dumps(pin, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def judge(
    craft: str,
    *,
    rebuild_all: bool,
    out: Path,
    pin: Mapping | None = None,
    root: Path = REPO_ROOT,
    hangar_dir: Path = HANGAR_DIR,
    record: Mapping | None = None,
    rebuild_fn: Callable[[str, Path], dict[str, str]] | None = None,
) -> Verdict:
    """Check one craft against its record and its pin, rebuilding it only when its recipe moved or on the switch."""
    pin = read_pin() if pin is None else pin
    record = json.loads(RECORD_PATH.read_text(encoding="utf-8")) if record is None else record
    rebuild_fn = rebuild if rebuild_fn is None else rebuild_fn
    names = shipped_names(craft)
    shipped = shipped_digests(craft, hangar_dir)
    prefix = f"hangar/{craft}/"
    recorded = {f["path"][len(prefix) :]: f["sha256"] for f in record["files"] if f["path"].startswith(prefix)}
    problems: list[str] = []
    for name in names:
        if name not in shipped:
            problems.append(f"{name} is missing from {hangar_dir / craft}")
        elif recorded.get(name) != shipped[name]:
            problems.append(f"{name}: its SHA-256 is not the one PROVENANCE.json records for it")
    moved = moved_from_pin(craft, pin, root)
    if not moved:
        pinned_files = pin["crafts"][craft]["files"]
        off = [name for name in names if name in shipped and pinned_files.get(name) != shipped[name]]
        if off or sorted(pinned_files) != sorted(names):
            problems.append(
                f"{', '.join(off) or 'the pinned files'}: not what its recipe, unmoved since the pin, built at the pin; "
                f"rebuild it (build_craft.py, then write_hangar.py) and repin it with: uv run python {THIS_SCRIPT} --craft {craft}"
            )
        if not rebuild_all:
            return Verdict(PINNED, problems, rebuilt=False)
    why = "it has no pin" if moved == ["no pin"] else f"its recipe moved from the pin ({', '.join(moved)})" if moved else f"{SWITCH}=1"
    repin = f"then {'pin' if moved == ['no pin'] else 'repin'} it with: uv run python {THIS_SCRIPT} --craft {craft}"
    try:
        built = rebuild_fn(craft, out)
    except CannotRebuild as error:
        problems.append(f"{why}, and it cannot be rebuilt here: {error}")
        if moved:
            problems.append(f"{why}: rebuild it on a machine with {BUILT_WITH} and NASA's source, {repin}")
        return Verdict(f"not rebuilt: {why}; {error}", problems, rebuilt=False)
    differs = [name for name in names if name not in shipped or built.get(name) != shipped[name]]
    problems.extend(f"{name}: rebuilt from its recipe, it differs from the shipped file" for name in differs)
    if moved:
        done = "the rebuild matches the shipped files" if not differs else "rebuild it (build_craft.py, then write_hangar.py)"
        problems.append(f"{why}: {done}; {repin}")
    return Verdict(f"rebuilt: {why}", problems, rebuilt=True)


def write_pins(
    names: Iterable[str],
    *,
    pin_path: Path = PIN_PATH,
    rebuild_fn: Callable[[str, Path], dict[str, str]] | None = None,
    root: Path = REPO_ROOT,
    hangar_dir: Path = HANGAR_DIR,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Rebuild each craft and pin it only when every file came out byte for byte as shipped; the pinned and the
    refused, each refusal with its reason. The pin file is written after each craft pinned, the others' kept."""
    rebuild_fn = rebuild if rebuild_fn is None else rebuild_fn
    pin = read_pin(pin_path) if pin_path.is_file() else {}
    pin = {"about": PIN_ABOUT, "crafts": dict(pin.get("crafts", {})), "format": PIN_FORMAT, "formatVersion": 1}
    pinned: list[str] = []
    refused: list[tuple[str, str]] = []
    for craft in names:
        before = recipe_parts(craft, root)
        with tempfile.TemporaryDirectory(prefix=f"albedian-rebuild-{craft}-") as tmp:
            try:
                built = rebuild_fn(craft, Path(tmp))
            except CannotRebuild as error:
                refused.append((craft, str(error)))
                continue
        shipped = shipped_digests(craft, hangar_dir)
        off = [name for name in shipped_names(craft) if name not in shipped or built.get(name) != shipped[name]]
        if off:
            refused.append((craft, f"rebuilt from its recipe, these differ from the shipped files: {', '.join(off)}"))
            continue
        if recipe_parts(craft, root) != before:
            refused.append((craft, "its recipe moved while it was rebuilt"))
            continue
        pin["crafts"][craft] = {"recipe": before, "files": shipped}
        pin_path.write_text(dumps_pin(pin), encoding="utf-8")
        pinned.append(craft)
    return pinned, refused


def main() -> int:
    ap = argparse.ArgumentParser(description="Rebuild hangar crafts and pin each one whose rebuild matches as shipped.")
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--craft", action="append", choices=sorted(crafts.CRAFTS))
    which.add_argument("--all", action="store_true")
    args = ap.parse_args()

    def timed(craft: str, out: Path) -> dict[str, str]:
        start = time.monotonic()
        try:
            return rebuild(craft, out)
        finally:
            print(f"{craft}: rebuilt in {time.monotonic() - start:.1f} s", flush=True)

    pinned, refused = write_pins(sorted(crafts.CRAFTS) if args.all else args.craft, rebuild_fn=timed)
    for craft in pinned:
        print(f"pinned {craft}")
    for craft, why in refused:
        print(f"refused {craft}: {why}")
    return 1 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
