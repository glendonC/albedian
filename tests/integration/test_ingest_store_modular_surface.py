"""Characterization and property-based surface tests for `services.ingest.store`.

Pins the public module path, constants, frozen dataclasses, exception attributes,
all 19 callable signatures, behavioral invariants, edge-table bounds, race recovery,
permission enforcement, streaming partial writes, compatibility with the frontend
refusal-sentence reader (`apps/web/src/components/__tests__/homeRefusal.test.tsx`),
and Hypothesis streaming properties. When sibling `store_*.py` modules are present,
also verifies explicit `__all__` exports, zero stdlib namespace leakage, single-location
`Refused(...)` authoring in `store.py`, and a strictly acyclic sibling import graph.
"""

from __future__ import annotations

import ast
import asyncio
from collections.abc import AsyncIterator
import dataclasses
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import re
import tempfile
import time

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from services.ingest import store

REPO_ROOT = Path(__file__).resolve().parents[2]
INGEST_DIR = REPO_ROOT / "services" / "ingest"

EXPECTED_CONSTANTS = {
    "ROOT_ENV",
    "MAX_BYTES_ENV",
    "MAX_STILL_BYTES_ENV",
    "DEFAULT_ROOT",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_STILL_BYTES",
    "STALE_PARTIAL_SEC",
    "SHA256_PATTERN",
    "SNIFF_BYTES",
    "PRIVATE_FILE_MODE",
    "PRIVATE_FOLDER_MODE",
    "HEIF_STILL_BRANDS",
    "HEIF_GENERIC_BRANDS",
    "QUICKTIME_BRANDS",
    "MP4_BRANDS",
    "DECLARABLE",
    "STILL_FAMILIES",
}

EXPECTED_CLASSES = {
    "Refused",
    "FileType",
    "Received",
    "IngestStore",
}

EXPECTED_FUNCTIONS = {
    "sniff",
    "check_declared",
}

EXPECTED_SEMI_PRIVATE_HELPERS = {
    "_too_large",
    "_fsync_dir",
}

EXPECTED_PUBLIC_SYMBOLS = EXPECTED_CONSTANTS | EXPECTED_CLASSES | EXPECTED_FUNCTIONS

_PK = inspect.Parameter.POSITIONAL_OR_KEYWORD
_EMPTY = inspect.Parameter.empty

EXPECTED_TOP_CALLABLE_PARAMS: dict[str, list[tuple[str, object, object]]] = {
    "Refused": [("status", _PK, _EMPTY), ("reason", _PK, _EMPTY)],
    "FileType": [
        ("mime_type", _PK, _EMPTY),
        ("extension", _PK, _EMPTY),
        ("family", _PK, _EMPTY),
        ("media_kind", _PK, _EMPTY),
    ],
    "Received": [
        ("partial", _PK, _EMPTY),
        ("sha256", _PK, _EMPTY),
        ("byte_size", _PK, _EMPTY),
        ("file_type", _PK, _EMPTY),
    ],
    "sniff": [("head", _PK, _EMPTY)],
    "check_declared": [("file_type", _PK, _EMPTY), ("declared", _PK, _EMPTY)],
    "_too_large": [("limit", _PK, _EMPTY), ("kind", _PK, _EMPTY)],
    "_fsync_dir": [("folder", _PK, _EMPTY)],
}

EXPECTED_INGEST_STORE_METHODS: dict[str, list[tuple[str, object, object]]] = {
    "__init__": [
        ("self", _PK, _EMPTY),
        ("root", _PK, _EMPTY),
        ("max_bytes", _PK, 2 * 1024**3),
        ("max_still_bytes", _PK, 256 * 1024**2),
    ],
    "_private_folder": [("self", _PK, _EMPTY), ("folder", _PK, _EMPTY)],
    "commit": [("self", _PK, _EMPTY), ("received", _PK, _EMPTY)],
    "discard": [("self", _PK, _EMPTY), ("received", _PK, _EMPTY)],
    "folder": [("self", _PK, _EMPTY), ("sha256", _PK, _EMPTY)],
    "from_environment": [],
    "limit_for": [("self", _PK, _EMPTY), ("media_kind", _PK, _EMPTY)],
    "original": [("self", _PK, _EMPTY), ("sha256", _PK, _EMPTY)],
    "read_record": [("self", _PK, _EMPTY), ("sha256", _PK, _EMPTY)],
    "receive": [
        ("self", _PK, _EMPTY),
        ("chunks", _PK, _EMPTY),
        ("declared", _PK, _EMPTY),
        ("declared_length", _PK, None),
    ],
    "sweep_partials": [("self", _PK, _EMPTY), ("now", _PK, None)],
    "write_record": [
        ("self", _PK, _EMPTY),
        ("sha256", _PK, _EMPTY),
        ("record", _PK, _EMPTY),
    ],
}

# Mirrors apps/web/src/components/__tests__/homeRefusal.test.tsx:136-138
_REFUSED_CALL_RE = re.compile(r'Refused\(\s*(\d{3}),\s*f?"([^"]+)"')


async def _iter_chunks(items: list[bytes]) -> AsyncIterator[bytes]:
    for item in items:
        yield item


def _ftyp_box(major: bytes, compatibles: list[bytes] = [], declared_size: int | None = None, tail: bytes = b"") -> bytes:
    assert len(major) == 4
    body = b"ftyp" + major + b"\x00\x00\x02\x00" + b"".join(compatibles)
    total = (4 + len(body)) if declared_size is None else declared_size
    return total.to_bytes(4, "big") + body + tail


# =============================================================================================
# 1. Public surface, constants, types, callable signatures, and modular DAG checks
# =============================================================================================


def test_module_path_and_complete_exported_symbol_set() -> None:
    module_path = Path(store.__file__).resolve()
    assert module_path == INGEST_DIR / "store.py"
    assert module_path.is_file()

    assert len(EXPECTED_PUBLIC_SYMBOLS) == 23
    for symbol in EXPECTED_PUBLIC_SYMBOLS | EXPECTED_SEMI_PRIVATE_HELPERS:
        assert hasattr(store, symbol), f"missing symbol on services.ingest.store: {symbol}"


def test_public_constants_preserve_exact_values_and_types() -> None:
    assert store.ROOT_ENV == "ALBEDIAN_INGEST_DIR"
    assert store.MAX_BYTES_ENV == "ALBEDIAN_INGEST_MAX_BYTES"
    assert store.MAX_STILL_BYTES_ENV == "ALBEDIAN_INGEST_MAX_STILL_BYTES"
    assert store.DEFAULT_ROOT == REPO_ROOT / "data" / "ingest"
    assert isinstance(store.DEFAULT_ROOT, Path)

    assert store.DEFAULT_MAX_BYTES == 2 * 1024**3
    assert isinstance(store.DEFAULT_MAX_BYTES, int)
    assert store.DEFAULT_MAX_STILL_BYTES == 256 * 1024**2
    assert isinstance(store.DEFAULT_MAX_STILL_BYTES, int)
    assert store.STALE_PARTIAL_SEC == 24 * 3600
    assert store.SNIFF_BYTES == 64
    assert store.PRIVATE_FILE_MODE == 0o400
    assert store.PRIVATE_FOLDER_MODE == 0o700
    assert store.SHA256_PATTERN.pattern == r"[0-9a-f]{64}"

    assert store.HEIF_STILL_BRANDS == {b"heic", b"heix", b"heim", b"heis"}
    assert store.HEIF_GENERIC_BRANDS == {b"mif1"}
    assert store.QUICKTIME_BRANDS == {b"qt  "}
    assert store.MP4_BRANDS == {
        b"isom",
        b"iso2",
        b"iso4",
        b"iso5",
        b"iso6",
        b"mp41",
        b"mp42",
        b"avc1",
        b"M4V ",
        b"mmp4",
    }
    assert store.DECLARABLE == {
        "jpeg": {"image/jpeg", "image/jpg", "image/pjpeg"},
        "png": {"image/png"},
        "tiff": {"image/tiff", "image/tif"},
        "heif": {"image/heic", "image/heif"},
        "isobmff-video": {"video/quicktime", "video/mp4", "video/x-m4v"},
    }
    assert store.STILL_FAMILIES == ("jpeg", "png", "tiff", "heif")


def test_refused_exception_and_frozen_dataclasses(tmp_path: Path) -> None:
    err = store.Refused(415, "not supported")
    assert isinstance(err, Exception)
    assert (err.status, err.reason, str(err)) == (415, "not supported", "not supported")

    too_big_clip = store._too_large(1024, None)
    assert (too_big_clip.status, too_big_clip.reason) == (413, "larger than the 1024-byte limit")
    too_big_still = store._too_large(512, "still")
    assert (too_big_still.status, too_big_still.reason) == (413, "larger than the 512-byte limit for a still")

    assert dataclasses.is_dataclass(store.FileType)
    assert dataclasses.is_dataclass(store.Received)
    ft = store.FileType("image/jpeg", "jpg", "jpeg", "image")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ft.extension = "png"  # type: ignore[misc]

    rec = store.Received(tmp_path / "x.part", "ab" * 32, 42, ft)
    with pytest.raises(dataclasses.FrozenInstanceError):
        rec.byte_size = 0  # type: ignore[misc]


@pytest.mark.parametrize("name", sorted(EXPECTED_TOP_CALLABLE_PARAMS))
def test_top_level_callable_signatures(name: str) -> None:
    sig = inspect.signature(getattr(store, name))
    actual = [(p.name, p.kind, p.default) for p in sig.parameters.values()]
    assert actual == EXPECTED_TOP_CALLABLE_PARAMS[name]


@pytest.mark.parametrize("method_name", sorted(EXPECTED_INGEST_STORE_METHODS))
def test_ingest_store_method_signatures(method_name: str) -> None:
    member = getattr(store.IngestStore, method_name)
    sig = inspect.signature(member)
    actual = [(p.name, p.kind, p.default) for p in sig.parameters.values()]
    assert actual == EXPECTED_INGEST_STORE_METHODS[method_name]
    if method_name == "receive":
        assert inspect.iscoroutinefunction(member)


def test_store_source_preserves_frontend_refusal_sentence_contract() -> None:
    source = (INGEST_DIR / "store.py").read_text(encoding="utf-8")
    matches = _REFUSED_CALL_RE.findall(source)
    assert len(matches) >= 5
    statuses = {int(code) for code, _ in matches}
    assert statuses == {413, 415, 422}
    reasons = [reason for _, reason in matches]
    for reason in reasons:
        assert reason == reason.strip()
        assert reason[0].islower()
        assert not reason.endswith(".")


def test_modular_facade_exports_and_acyclic_sibling_graph_when_split() -> None:
    sibling_files = sorted(INGEST_DIR.glob("store_*.py"))
    if hasattr(store, "__all__") or sibling_files:
        assert hasattr(store, "__all__")
        assert len(store.__all__) == len(set(store.__all__))
        assert set(store.__all__) == EXPECTED_PUBLIC_SYMBOLS
        public_attrs = {n for n in dir(store) if not n.startswith("_")}
        assert public_attrs == EXPECTED_PUBLIC_SYMBOLS

    if not sibling_files:
        return

    sibling_stems = {p.stem for p in sibling_files}
    edges: dict[str, set[str]] = {stem: set() for stem in sibling_stems}

    for path in sibling_files:
        stem = path.stem
        text = path.read_text(encoding="utf-8")
        # All Refused(status, reason) authoring stays in store.py for homeRefusal.test.tsx parity
        assert _REFUSED_CALL_RE.findall(text) == [], f"{stem}.py must not hide Refused(...) reasons from store.py"
        mod = importlib.import_module(f"services.ingest.{stem}")
        assert hasattr(mod, "__all__"), f"{stem}.py must declare explicit __all__"
        assert len(mod.__all__) == len(set(mod.__all__))

        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module_name = node.module or ""
                if node.level == 1 and module_name == "store":
                    pytest.fail(f"{stem} must not import back from facade store.py")
                if module_name == "services.ingest.store":
                    pytest.fail(f"{stem} must not import back from facade services.ingest.store")
                target = None
                if node.level == 1 and module_name.startswith("store_"):
                    target = module_name
                elif module_name.startswith("services.ingest.store_"):
                    target = module_name.removeprefix("services.ingest.")
                if target is not None:
                    assert target in sibling_stems
                    assert target != stem
                    edges[stem].add(target)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "services.ingest.store"
                    if alias.name.startswith("services.ingest.store_"):
                        target = alias.name.removeprefix("services.ingest.")
                        assert target in sibling_stems
                        edges[stem].add(target)

    # Topological cycle check via three-color DFS
    state: dict[str, int] = {stem: 0 for stem in sibling_stems}

    def visit(node_name: str) -> None:
        state[node_name] = 1
        for dep in edges[node_name]:
            assert state[dep] != 1, f"cycle detected involving {node_name} -> {dep}"
            if state[dep] == 0:
                visit(dep)
        state[node_name] = 2

    for stem in sorted(sibling_stems):
        if state[stem] == 0:
            visit(stem)


# =============================================================================================
# 2. Sniffing & Content-Type edge tables across every format, brand, and box boundary
# =============================================================================================


SNIFF_ACCEPT_ROWS = [
    ("jpeg_minimal", b"\xff\xd8\xff", store.FileType("image/jpeg", "jpg", "jpeg", "image")),
    ("png_exact_8", b"\x89PNG\r\n\x1a\n", store.FileType("image/png", "png", "png", "image")),
    ("tiff_le", b"II*\x00", store.FileType("image/tiff", "tif", "tiff", "image")),
    ("tiff_be", b"MM\x00*", store.FileType("image/tiff", "tif", "tiff", "image")),
    ("ftyp_min_12_qt", b"\x00\x00\x00\x0cftypqt  ", store.FileType("video/quicktime", "mov", "isobmff-video", "video")),
    ("heif_major_heic", _ftyp_box(b"heic"), store.FileType("image/heic", "heic", "heif", "image")),
    ("heif_major_heix", _ftyp_box(b"heix"), store.FileType("image/heic", "heic", "heif", "image")),
    ("heif_major_heim", _ftyp_box(b"heim"), store.FileType("image/heic", "heic", "heif", "image")),
    ("heif_major_heis", _ftyp_box(b"heis"), store.FileType("image/heic", "heic", "heif", "image")),
    ("heif_mif1_compat_first", _ftyp_box(b"mif1", [b"heic", b"mif1"]), store.FileType("image/heic", "heic", "heif", "image")),
    ("heif_mif1_compat_second", _ftyp_box(b"mif1", [b"miaf", b"heic"]), store.FileType("image/heic", "heic", "heif", "image")),
    ("mp4_isom", _ftyp_box(b"isom"), store.FileType("video/mp4", "mp4", "isobmff-video", "video")),
    ("mp4_iso2", _ftyp_box(b"iso2"), store.FileType("video/mp4", "mp4", "isobmff-video", "video")),
    ("mp4_iso4", _ftyp_box(b"iso4"), store.FileType("video/mp4", "mp4", "isobmff-video", "video")),
    ("mp4_iso5", _ftyp_box(b"iso5"), store.FileType("video/mp4", "mp4", "isobmff-video", "video")),
    ("mp4_iso6", _ftyp_box(b"iso6"), store.FileType("video/mp4", "mp4", "isobmff-video", "video")),
    ("mp4_mp41", _ftyp_box(b"mp41"), store.FileType("video/mp4", "mp4", "isobmff-video", "video")),
    ("mp4_mp42", _ftyp_box(b"mp42"), store.FileType("video/mp4", "mp4", "isobmff-video", "video")),
    ("mp4_avc1", _ftyp_box(b"avc1"), store.FileType("video/mp4", "mp4", "isobmff-video", "video")),
    ("mp4_M4V", _ftyp_box(b"M4V "), store.FileType("video/mp4", "mp4", "isobmff-video", "video")),
    ("mp4_mmp4", _ftyp_box(b"mmp4"), store.FileType("video/mp4", "mp4", "isobmff-video", "video")),
]


@pytest.mark.parametrize(("label", "head", "expected"), SNIFF_ACCEPT_ROWS, ids=[r[0] for r in SNIFF_ACCEPT_ROWS])
def test_sniff_accept_table(label: str, head: bytes, expected: store.FileType) -> None:
    assert label
    assert store.sniff(head) == expected


SNIFF_REJECT_ROWS = [
    ("empty", b"", "not a JPEG, PNG, TIFF, HEIC, QuickTime or MP4 file"),
    ("jpeg_truncated_2", b"\xff\xd8", "not a JPEG, PNG, TIFF, HEIC, QuickTime or MP4 file"),
    ("png_truncated_7", b"\x89PNG\r\n\x1a", "not a JPEG, PNG, TIFF, HEIC, QuickTime or MP4 file"),
    ("tiff_swapped_magic", b"II\x00*", "not a JPEG, PNG, TIFF, HEIC, QuickTime or MP4 file"),
    ("ftyp_len_11_too_short", b"\x00\x00\x00\x0bftypqt ", "not a JPEG, PNG, TIFF, HEIC, QuickTime or MP4 file"),
    (
        "mif1_without_heic_compat",
        _ftyp_box(b"mif1", [b"avif", b"miaf"]),
        "an ISO base media file with brand 'mif1', which is not supported",
    ),
    (
        "mif1_heic_after_declared_box_end",
        _ftyp_box(b"mif1", [b"miaf"], declared_size=20, tail=b"heic"),
        "an ISO base media file with brand 'mif1', which is not supported",
    ),
    (
        "mif1_truncated_3byte_compat",
        b"\x00\x00\x00\x14ftypmif1\x00\x00\x00\x00hei",
        "an ISO base media file with brand 'mif1', which is not supported",
    ),
    (
        "mif1_unaligned_box_size_19_with_trailing_c",
        b"\x00\x00\x00\x13ftypmif1\x00\x00\x00\x00heic",
        "an ISO base media file with brand 'mif1', which is not supported",
    ),
    (
        "unsupported_latin1_brand",
        _ftyp_box(b"av\xff1"),
        f"an ISO base media file with brand {'av\xff1'!r}, which is not supported",
    ),
]


@pytest.mark.parametrize(("label", "head", "expected_reason"), SNIFF_REJECT_ROWS, ids=[r[0] for r in SNIFF_REJECT_ROWS])
def test_sniff_reject_edge_table(label: str, head: bytes, expected_reason: str) -> None:
    assert label
    with pytest.raises(store.Refused) as exc_info:
        store.sniff(head)
    assert exc_info.value.status == 415
    assert exc_info.value.reason == expected_reason


def test_check_declared_accepts_every_declarable_alias_and_rejects_mismatches() -> None:
    sample_types = {
        "jpeg": store.FileType("image/jpeg", "jpg", "jpeg", "image"),
        "png": store.FileType("image/png", "png", "png", "image"),
        "tiff": store.FileType("image/tiff", "tif", "tiff", "image"),
        "heif": store.FileType("image/heic", "heic", "heif", "image"),
        "isobmff-video": store.FileType("video/mp4", "mp4", "isobmff-video", "video"),
    }
    for missing in (None, ""):
        with pytest.raises(store.Refused) as exc_info:
            store.check_declared(sample_types["jpeg"], missing)
        assert (exc_info.value.status, exc_info.value.reason) == (415, "the upload must declare its Content-Type")

    for family, aliases in store.DECLARABLE.items():
        ft = sample_types[family]
        for alias in aliases:
            store.check_declared(ft, alias)
            store.check_declared(ft, f"  {alias.upper()} ; charset=binary ; boundary=xyz ")
        for other_family, other_ft in sample_types.items():
            if other_family == family:
                continue
            for alias in aliases:
                with pytest.raises(store.Refused) as exc_info:
                    store.check_declared(other_ft, alias)
                assert exc_info.value.status == 415
                assert exc_info.value.reason == f"declared as {alias}, but the bytes are {other_ft.mime_type}"


# =============================================================================================
# 3. Store bounds, environment loading, stale partial sweeping, and hash-folder edge table
# =============================================================================================


@pytest.mark.parametrize(("max_bytes", "max_still_bytes"), [(0, 10), (-1, 10), (10, 0), (10, -5)])
def test_store_rejects_non_positive_limits(tmp_path: Path, max_bytes: int, max_still_bytes: int) -> None:
    with pytest.raises(ValueError, match="the size limits must be positive"):
        store.IngestStore(tmp_path / "bad", max_bytes=max_bytes, max_still_bytes=max_still_bytes)


def test_store_limit_clamping_limit_for_and_environment_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Smallest positive bounds (1 byte) and clamping when max_still_bytes > max_bytes
    tiny = store.IngestStore(tmp_path / "tiny", max_bytes=1, max_still_bytes=1)
    assert (tiny.max_bytes, tiny.max_still_bytes) == (1, 1)
    assert not (tmp_path / "tiny").exists(), "opening a non-existent root does not create it eagerly"

    clamped = store.IngestStore(tmp_path / "clamped", max_bytes=500, max_still_bytes=2000)
    assert (clamped.max_bytes, clamped.max_still_bytes) == (500, 500)
    assert clamped.limit_for("image") == 500
    assert clamped.limit_for("video") == 500

    distinct = store.IngestStore(tmp_path / "distinct", max_bytes=2000, max_still_bytes=500)
    assert distinct.limit_for("image") == 500
    assert distinct.limit_for("video") == 2000

    # Unset and empty environment variables both fall back to defaults
    for var in (store.ROOT_ENV, store.MAX_BYTES_ENV, store.MAX_STILL_BYTES_ENV):
        monkeypatch.delenv(var, raising=False)
    unset_store = store.IngestStore.from_environment()
    assert (unset_store.root, unset_store.max_bytes, unset_store.max_still_bytes) == (
        store.DEFAULT_ROOT,
        store.DEFAULT_MAX_BYTES,
        store.DEFAULT_MAX_STILL_BYTES,
    )

    for var in (store.ROOT_ENV, store.MAX_BYTES_ENV, store.MAX_STILL_BYTES_ENV):
        monkeypatch.setenv(var, "")
    empty_env_store = store.IngestStore.from_environment()
    assert (empty_env_store.root, empty_env_store.max_bytes, empty_env_store.max_still_bytes) == (
        store.DEFAULT_ROOT,
        store.DEFAULT_MAX_BYTES,
        store.DEFAULT_MAX_STILL_BYTES,
    )


def test_sweep_partials_exact_cutoff_symlinks_dirs_and_vanishing_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "sweep"
    s = store.IngestStore(root, max_bytes=10_000)
    assert s.sweep_partials(now=1_000_000.0) == 0

    partial_dir = root / ".partial"
    partial_dir.mkdir(parents=True)
    now = 1_000_000.0
    cutoff = now - store.STALE_PARTIAL_SEC

    at_cutoff = partial_dir / "exact.part"
    before_cutoff = partial_dir / "older.part"
    keep_txt = partial_dir / "older.txt"
    subdir_part = partial_dir / "folder.part"
    outside_target = tmp_path / "outside.bin"
    symlink_part = partial_dir / "link.part"

    for p in (at_cutoff, before_cutoff, keep_txt, outside_target):
        p.write_bytes(b"x")
    subdir_part.mkdir()
    symlink_part.symlink_to(outside_target)

    os.utime(at_cutoff, (cutoff, cutoff))
    os.utime(before_cutoff, (cutoff - 1.0, cutoff - 1.0))
    os.utime(keep_txt, (cutoff - 100.0, cutoff - 100.0))
    os.utime(subdir_part, (cutoff - 100.0, cutoff - 100.0))
    os.utime(outside_target, (cutoff - 100.0, cutoff - 100.0))
    if os.utime in os.supports_follow_symlinks:
        os.utime(symlink_part, (cutoff - 100.0, cutoff - 100.0), follow_symlinks=False)

    removed = s.sweep_partials(now=now)
    assert removed == 1
    assert not before_cutoff.exists()
    assert at_cutoff.exists(), "st_mtime == cutoff must NOT be swept (strict < cutoff)"
    assert keep_txt.exists()
    assert subdir_part.is_dir()
    assert symlink_part.is_symlink() and outside_target.exists()

    # Concurrent FileNotFoundError during scandir iteration is ignored cleanly
    stale_two = partial_dir / "stale2.part"
    stale_two.write_bytes(b"y")
    os.utime(stale_two, (cutoff - 5.0, cutoff - 5.0))
    real_unlink = os.unlink

    def unlink_vanishing_once(target_path: str | os.PathLike[str]) -> None:
        real_unlink(target_path)
        raise FileNotFoundError(target_path)

    monkeypatch.setattr(os, "unlink", unlink_vanishing_once)
    assert s.sweep_partials(now=now) == 0
    assert not stale_two.exists()


HASH_EDGE_ROWS = [
    ("len_63", "a" * 63, False),
    ("len_64_lower_hex", "0123456789abcdef" * 4, True),
    ("len_65", "a" * 65, False),
    ("trailing_newline", "a" * 64 + "\n", False),
    ("leading_space", " " + "a" * 63, False),
    ("upper_hex", "a" * 63 + "F", False),
    ("non_hex_g", "a" * 63 + "g", False),
]


@pytest.mark.parametrize(("label", "candidate", "valid"), HASH_EDGE_ROWS, ids=[r[0] for r in HASH_EDGE_ROWS])
def test_folder_and_original_hash_edge_table(tmp_path: Path, label: str, candidate: str, valid: bool) -> None:
    assert label
    s = store.IngestStore(tmp_path / "hashes", max_bytes=10_000)
    if not valid:
        with pytest.raises(ValueError, match="not a SHA-256 hash"):
            s.folder(candidate)
        return
    expected = s.root / candidate[:2] / candidate
    assert s.folder(candidate) == expected
    assert s.original(candidate) is None
    expected.mkdir(parents=True)
    (expected / "record.json").write_text("{}", encoding="utf-8")
    assert s.original(candidate) is None
    (expected / "original.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    assert s.original(candidate) == expected / "original.png"


# =============================================================================================
# 4. Streaming receive boundaries, partial os.write loop, error cleanup, and commit/record races
# =============================================================================================


def test_receive_declared_length_and_streaming_exact_cap_boundaries(tmp_path: Path) -> None:
    s = store.IngestStore(tmp_path / "bounds", max_bytes=200, max_still_bytes=100)
    still_exact = b"\x89PNG\r\n\x1a\n" + b"p" * (100 - 8)
    still_over = still_exact + b"!"
    clip_exact = _ftyp_box(b"qt  ") + b"v" * (200 - len(_ftyp_box(b"qt  ")))
    clip_over = clip_exact + b"!"

    # Declared length right at inside bound succeeds; one past bound fails before creating .partial
    with pytest.raises(store.Refused) as exc_still_decl:
        asyncio.run(s.receive(_iter_chunks([still_exact]), "IMAGE/PNG; q=1", declared_length=101))
    assert (exc_still_decl.value.status, exc_still_decl.value.reason) == (413, "larger than the 100-byte limit for a still")
    assert not (s.root / ".partial").exists()

    with pytest.raises(store.Refused) as exc_clip_decl:
        asyncio.run(s.receive(_iter_chunks([clip_exact]), "video/quicktime", declared_length=201))
    assert (exc_clip_decl.value.status, exc_clip_decl.value.reason) == (413, "larger than the 200-byte limit")
    assert not (s.root / ".partial").exists()

    got_still = asyncio.run(s.receive(_iter_chunks([b"", still_exact[:30], b"", still_exact[30:]]), "image/png", declared_length=100))
    assert got_still.byte_size == 100 and got_still.sha256 == hashlib.sha256(still_exact).hexdigest()
    s.discard(got_still)
    s.discard(got_still)  # idempotent discard

    got_clip = asyncio.run(s.receive(_iter_chunks([clip_exact[:70], clip_exact[70:]]), "video/quicktime", declared_length=200))
    assert got_clip.byte_size == 200 and got_clip.sha256 == hashlib.sha256(clip_exact).hexdigest()
    s.discard(got_clip)

    # Streaming 1 byte over still cap and 1 byte over clip cap
    with pytest.raises(store.Refused) as exc_still_stream:
        asyncio.run(s.receive(_iter_chunks([still_over[:70], still_over[70:]]), "image/png"))
    assert (exc_still_stream.value.status, exc_still_stream.value.reason) == (
        413,
        "larger than the 100-byte limit for a still",
    )

    with pytest.raises(store.Refused) as exc_clip_stream:
        asyncio.run(s.receive(_iter_chunks([clip_over[:70], clip_over[70:]]), "video/quicktime"))
    assert (exc_clip_stream.value.status, exc_clip_stream.value.reason) == (
        413,
        "larger than the 200-byte limit for a clip",
    )

    # Short valid upload (< SNIFF_BYTES) and empty stream ([b"", b""])
    short_jpeg = b"\xff\xd8\xff\xd9"
    got_short = asyncio.run(s.receive(_iter_chunks([short_jpeg]), "image/jpeg"))
    assert got_short.byte_size == 4 and got_short.file_type.extension == "jpg"
    s.discard(got_short)

    with pytest.raises(store.Refused) as exc_empty:
        asyncio.run(s.receive(_iter_chunks([b"", b""]), "image/jpeg"))
    assert (exc_empty.value.status, exc_empty.value.reason) == (422, "the upload is empty")
    assert list((s.root / ".partial").iterdir()) == []


def test_receive_handles_short_os_writes_and_cleans_partial_on_stream_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = store.IngestStore(tmp_path / "shortwrite", max_bytes=10_000)
    payload = b"\x89PNG\r\n\x1a\n" + bytes(range(120))
    real_write = os.write

    def write_at_most_seven_bytes(fd: int, buf: bytes | memoryview) -> int:
        view = memoryview(buf)
        return real_write(fd, view[:7])

    monkeypatch.setattr(os, "write", write_at_most_seven_bytes)
    received = asyncio.run(s.receive(_iter_chunks([payload]), "image/png"))
    assert received.partial.read_bytes() == payload
    assert received.sha256 == hashlib.sha256(payload).hexdigest()
    s.discard(received)

    async def failing_stream() -> AsyncIterator[bytes]:
        yield b"\x89PNG\r\n\x1a\nhello"
        raise RuntimeError("socket dropped mid-upload")

    with pytest.raises(RuntimeError, match="socket dropped mid-upload"):
        asyncio.run(s.receive(failing_stream(), "image/png"))
    assert list((s.root / ".partial").iterdir()) == []


def test_commit_and_write_record_handle_concurrent_link_races_and_preserve_utf8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = store.IngestStore(tmp_path / "races", max_bytes=10_000)
    payload = b"\xff\xd8\xff\xe0" + b"race-payload"
    received = asyncio.run(s.receive(_iter_chunks([payload]), "image/jpeg"))
    real_link = os.link

    def concurrent_commit_link(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        # Simulate another worker linking original.jpg right after s.original() returned None
        Path(dst).write_bytes(payload)
        os.chmod(dst, store.PRIVATE_FILE_MODE)
        real_link(src, dst)

    monkeypatch.setattr(os, "link", concurrent_commit_link)
    target, already = s.commit(received)
    assert already is True
    assert target == s.folder(received.sha256) / "original.jpg"
    assert not received.partial.exists()
    monkeypatch.setattr(os, "link", real_link)

    # Record UTF-8 preservation (ensure_ascii=False) and missing-record None
    sha = received.sha256
    assert s.read_record(sha) is None
    written = s.write_record(sha, {"title": "Orion \u03b1", "count": 1})
    assert written == {"title": "Orion \u03b1", "count": 1}
    raw_json = (s.folder(sha) / "record.json").read_text(encoding="utf-8")
    assert "\u03b1" in raw_json
    assert s.read_record(sha) == {"title": "Orion \u03b1", "count": 1}


@settings(max_examples=50, deadline=None)
@given(
    family=st.sampled_from(["jpeg", "png", "tiff", "heif", "qt", "mp4"]),
    body=st.binary(min_size=0, max_size=220),
    split_points=st.lists(st.integers(min_value=0, max_value=240), max_size=8),
)
@example(family="heif", body=b"", split_points=[0, 1, 64])
@example(family="tiff", body=b"abc", split_points=[2, 2, 4])
@example(family="mp4", body=b"\x00" * 80, split_points=[12, 64, 65])
def test_pbt_chunk_partition_invariance_across_all_supported_families(
    family: str, body: bytes, split_points: list[int]
) -> None:
    headers_and_types = {
        "jpeg": (b"\xff\xd8\xff\xe0", "image/pjpeg", "jpg", "image"),
        "png": (b"\x89PNG\r\n\x1a\n", "image/png", "png", "image"),
        "tiff": (b"MM\x00*", "image/tif", "tif", "image"),
        "heif": (_ftyp_box(b"mif1", [b"heic"]), "image/heif", "heic", "image"),
        "qt": (_ftyp_box(b"qt  "), "video/quicktime", "mov", "video"),
        "mp4": (_ftyp_box(b"mp42"), "video/x-m4v", "mp4", "video"),
    }
    magic, declared, ext, kind = headers_and_types[family]
    data = magic + body
    cuts = sorted({0, len(data), *(min(len(data), p) for p in split_points)})
    chunks = [data[a:b] for a, b in zip(cuts[:-1], cuts[1:])]
    chunks_with_empties = [b""] + [part for c in chunks for part in (c, b"")]

    with tempfile.TemporaryDirectory() as tmp_dir:
        s = store.IngestStore(Path(tmp_dir) / "store", max_bytes=10_000, max_still_bytes=5_000)
        received = asyncio.run(s.receive(_iter_chunks(chunks_with_empties), declared))
        assert received.sha256 == hashlib.sha256(data).hexdigest()
        assert received.byte_size == len(data)
        assert (received.file_type.extension, received.file_type.media_kind) == (ext, kind)
        stored_path, already = s.commit(received)
        assert not already
        assert stored_path.read_bytes() == data
        assert stored_path.stat().st_mode & 0o777 == store.PRIVATE_FILE_MODE
