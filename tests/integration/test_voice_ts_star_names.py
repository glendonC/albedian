"""The app's parser holds the backend's star names through a generated TypeScript table.

packages/observation/src/generated/catalogStarNames.ts is written by `uv run python -m services.voice.names --write-ts`
from the same function the backend's name table comes from, so the two parsers name the same stars by the same
finding ids. The checked-in file must equal what the generator writes, byte for byte: a change to names.json, the
pinned catalog or the aliases fails here until the table is regenerated.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.voice import intent, names

REGENERATE = "uv run python -m services.voice.names --write-ts"


def test_the_checked_in_table_is_exactly_what_the_generator_writes() -> None:
    assert names.TS_TABLE_PATH.read_text(encoding="utf-8") == names.render_ts_table(), f"regenerate with: {REGENERATE}"


def test_the_table_is_the_backend_star_table() -> None:
    table = names.star_name_table()
    assert {spoken: finding_id for spoken, finding_id, _shown in table} == intent._STAR_NAMES
    assert [spoken for spoken, _, _ in table] == sorted(spoken for spoken, _, _ in table)


def test_each_name_shows_its_iau_or_pinned_name() -> None:
    shown = {spoken: name for spoken, _, name in names.star_name_table()}
    assert shown["alpha centauri"] == "Rigil Kentaurus"
    assert shown["canopus"] == "Canopus"
    for star in names.named_stars():
        if shown[star.name.lower()] != star.name:
            # A pinned star keeps its pinned name, as the backend keeps the pinned meaning (Gienah)
            assert intent._STAR_NAMES[star.name.lower()] != star.finding_id, star.name


def test_the_header_names_the_source_its_hash_and_the_command() -> None:
    header = names.render_ts_table().split("\n\n", 1)[0]
    digest = hashlib.sha256(names.NAMES_PATH.read_bytes()).hexdigest()
    assert digest in header
    assert REGENERATE in header
    assert "apps/web/public/catalog/stars/gaia-dr3/names.json" in header
    assert "PROVENANCE.json" in header


def test_the_command_writes_the_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "catalogStarNames.ts"
    monkeypatch.setattr(names, "TS_TABLE_PATH", target)
    assert names.main(["--write-ts"]) == 0
    assert target.read_text(encoding="utf-8") == names.render_ts_table()


def test_a_pinned_name_keeps_its_pinned_star_over_an_iau_row(monkeypatch: pytest.MonkeyPatch) -> None:
    # No pinned name meets another star's IAU name today (Gienah became Aljanah); a made-up pinned Polaris shows
    # the rule the backend keeps: the pinned meaning wins, as intent.py builds its table
    made_up = SimpleNamespace(common_name="Polaris", id="star-made-up")
    monkeypatch.setattr(names, "ALL_SKY_CATALOG_STARS", (made_up,))
    table = {spoken: (finding_id, shown) for spoken, finding_id, shown in names.star_name_table.__wrapped__()}
    assert table["polaris"] == ("finding-star-made-up", "Polaris")
    assert table["polaris australis"][0].startswith("finding-gaia-dr3-")
