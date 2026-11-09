"""Names a stargazer says under a southern sky, read against the baked IAU star-name table.

Every expected id below is a real row of `apps/web/public/catalog/stars/gaia-dr3/names.json` (IAU-CSN
matched to Hipparcos 2007 and Gaia DR3), in the form the phone-photo solver gives its findings.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from hypothesis import example, given, settings, strategies as st

from services.voice import claude, names
from services.voice.intent import check_voice_command, parse_voice_intent

YEAR = 2026

# Made-up values that point at no real person, place, time or file. Vega is selected, so any request
# that wrongly falls through to the selection shows up as Vega.
CONTEXT: dict[str, Any] = {
    "sessionId": "s-made-up",
    "sourceAssetId": "src_made-up",
    "viewRevision": 4,
    "calibrationState": "calibrated",
    "selectedFindingId": "finding-star-vega",
}

# name said: the finding id of its row (catalog and id read from names.json)
SOUTHERN_STARS = {
    "Canopus": "finding-hip-30438",
    "Achernar": "finding-hip-7588",
    "Acrux": "finding-hip-60718",
    "Mimosa": "finding-hip-62434",
    "Gacrux": "finding-hip-61084",
    "Imai": "finding-hip-59747",
    "Ginan": "finding-gaia-dr3-6058271964884299520",
    "Rigil Kentaurus": "finding-hip-71683",
    "Toliman": "finding-hip-71681",
    "Hadar": "finding-hip-68702",
    "Alpha Centauri": "finding-hip-71683",
    "Beta Centauri": "finding-hip-68702",
    "Agena": "finding-hip-68702",
    "Alpha Crucis": "finding-hip-60718",
    "Beta Crucis": "finding-hip-62434",
    "Alpha Carinae": "finding-hip-30438",
    "Alpha Eridani": "finding-hip-7588",
}

UNBAKED = {
    "the Large Magellanic Cloud": "the Large Magellanic Cloud",
    "the LMC": "the Large Magellanic Cloud",
    "the Small Magellanic Cloud": "the Small Magellanic Cloud",
    "the SMC": "the Small Magellanic Cloud",
    "Omega Centauri": "Omega Centauri",
    "47 Tucanae": "47 Tucanae",
    "47 Tuc": "47 Tucanae",
    "Eta Carinae": "Eta Carinae",
    "the Carina Nebula": "the Carina Nebula",
    "the Jewel Box": "the Jewel Box",
    "the Coalsack": "the Coalsack",
}

VERBS = ("select {}", "what is {}", "take me to {}")


def _rows() -> list[dict[str, Any]]:
    return json.loads(names.NAMES_PATH.read_text(encoding="utf-8"))["names"]


def test_the_expected_ids_are_rows_of_the_baked_table() -> None:
    by_name = {row["name"]: row for row in _rows()}
    for name in ("Canopus", "Achernar", "Acrux", "Mimosa", "Gacrux", "Imai", "Ginan", "Rigil Kentaurus", "Hadar"):
        row = by_name[name]
        prefix = "finding-hip-" if row["catalog"] == "hipparcos-2007" else "finding-gaia-dr3-"
        assert SOUTHERN_STARS[name] == f"{prefix}{row['catalogId']}"


@pytest.mark.parametrize("said", SOUTHERN_STARS, ids=SOUTHERN_STARS)
def test_a_southern_star_resolves_to_its_row_for_select_describe_and_fly(said: str) -> None:
    expected = SOUTHERN_STARS[said]
    kinds = {"select {}": "finding.select", "what is {}": "finding.describe", "take me to {}": "space.enter"}
    for template, action_type in kinds.items():
        intent = parse_voice_intent(template.format(said), CONTEXT, current_year=YEAR)
        assert intent.action is not None, template.format(said)
        assert (intent.action["type"], intent.action["findingId"]) == (action_type, expected), template.format(said)


def test_every_iau_name_in_the_table_resolves_to_its_own_row() -> None:
    """Every one: the pinned catalog takes no IAU name from another star (epsilon Cygni is Aljanah, and Gienah
    is gamma Corvi's), so each name selects its own row."""
    stars = names.named_stars()
    assert len(stars) == len(_rows()) > 400
    conflicts = set()
    for star in stars:
        intent = parse_voice_intent(f"select {star.name}", CONTEXT, current_year=YEAR)
        assert intent.action is not None, star.name
        if intent.action["findingId"] != star.finding_id:
            conflicts.add((star.name, intent.action["findingId"]))
    assert conflicts == set()


def test_a_pinned_star_keeps_its_own_finding_id() -> None:
    by_name = {star.name: star for star in names.named_stars()}
    assert by_name["Vega"].finding_id == "finding-star-vega"
    assert by_name["Sirius"].finding_id == "finding-star-sirius"


@pytest.mark.parametrize("said", UNBAKED, ids=UNBAKED)
def test_a_southern_object_with_no_baked_row_is_refused_and_never_becomes_the_selection(said: str) -> None:
    label = UNBAKED[said]
    for template in VERBS:
        utterance = template.format(said)
        reply = check_voice_command(utterance, CONTEXT, None)
        assert reply["status"] == "rejected", (utterance, reply)
        assert reply["reason"] == "no_grounded_answer", utterance
        assert label.lower() in reply["explanation"].lower(), utterance
        assert "vega" not in json.dumps(reply).lower(), utterance


def test_no_unbaked_name_is_also_a_name_in_the_table() -> None:
    spoken = {name for name, _ in names.spoken_names()}
    for aliases in names.UNBAKED_OBJECTS.values():
        assert not spoken & set(aliases)


@pytest.mark.parametrize("said", ["the Southern Cross", "Crux"])
def test_the_southern_cross_asks_which_of_its_five_stars(said: str) -> None:
    for template in ("select {}", "what is {}", "take me to {}"):
        reply = check_voice_command(template.format(said), CONTEXT, None)
        assert reply["status"] == "needs_input", (template.format(said), reply)
        assert [choice["label"] for choice in reply["choices"]] == ["Acrux", "Mimosa", "Gacrux", "Imai", "Ginan"]
        assert [choice["id"] for choice in reply["choices"]] == [
            SOUTHERN_STARS[name] for name in ("Acrux", "Mimosa", "Gacrux", "Imai", "Ginan")
        ]


def test_the_claude_tool_prompt_and_allowed_ids_hold_the_southern_stars() -> None:
    for name in ("Canopus", "Achernar", "Acrux", "Rigil Kentaurus", "Hadar", "Ginan"):
        assert f"{name}: {SOUTHERN_STARS[name]}" in claude.SYSTEM_PROMPT
        assert SOUTHERN_STARS[name] in claude.CATALOG_FINDING_IDS
    for label in names.UNBAKED_OBJECTS:
        assert label.removeprefix("the ") in claude.SYSTEM_PROMPT


def test_a_model_call_naming_a_southern_star_composes_an_action() -> None:
    from services.voice.intent import ActionContext

    action = claude.compose_action(
        "finding_select", {"findingId": "finding-hip-30438"}, ActionContext.from_dict(CONTEXT), "req-1", 4,
        current_year=YEAR,
    )
    assert action is not None and action["findingId"] == "finding-hip-30438"


VOICE_PARSER_TS = names.NAMES_PATH.parents[6] / "packages" / "observation" / "src" / "voiceParser.ts"


def _ts_block(name: str) -> str:
    source = VOICE_PARSER_TS.read_text(encoding="utf-8")
    start = source.index(f"export const {name}")
    return source[start : source.index("};", start)]


def test_every_alias_and_cross_star_lands_on_a_row_of_the_table() -> None:
    iau = {row["name"]: row for row in _rows()}
    assert set(names.SPOKEN_ALIASES.values()) <= set(iau)
    assert [(iau[n]["bayer"], iau[n]["constellation"]) for n in names.SOUTHERN_CROSS_STARS] == [
        ("alf", "Cru"), ("bet", "Cru"), ("gam", "Cru"), ("del", "Cru"), ("eps", "Cru"),
    ]


def test_the_typescript_parser_holds_the_same_aliases_unbaked_objects_and_cross_stars() -> None:
    import re

    ts_aliases = dict(re.findall(r"^\s*'?([a-z ]+?)'?: '([^']+)',$", _ts_block("SPOKEN_STAR_ALIASES"), re.M))
    assert ts_aliases == names.SPOKEN_ALIASES
    ts_unbaked = {
        label: tuple(re.findall(r"'([^']+)'", spoken))
        for label, spoken in re.findall(r"^\s*'([^']+)': \[([^\]]*)\],$", _ts_block("UNBAKED_OBJECTS"), re.M)
    }
    assert ts_unbaked == names.UNBAKED_OBJECTS
    source = VOICE_PARSER_TS.read_text(encoding="utf-8")
    cross = re.search(r"export const SOUTHERN_CROSS_STARS = \[([^\]]*)\]", source)
    assert cross is not None and tuple(re.findall(r"'([^']+)'", cross.group(1))) == names.SOUTHERN_CROSS_STARS


# The names a whole-word search tries: every star the parsers know, with "a" and "a b" for the shortest cases.
WHOLE_WORD_NAMES = sorted({spoken for spoken, _ in names.spoken_names()} | {"moon", "a", "a b", "47 tucanae"})


def _whole_word_reference(name: str, text: str) -> bool:
    """The plain reading of a whole word: the name, with no letter or digit just before or just after it."""
    start = text.find(name)
    while start != -1:
        end = start + len(name)
        before = text[start - 1] if start > 0 else ""
        after = text[end] if end < len(text) else ""
        if not (before.isascii() and before.isalnum()) and not (after.isascii() and after.isalnum()):
            return True
        start = text.find(name, start + 1)
    return False


@settings(max_examples=2000, deadline=None)
@given(
    name=st.sampled_from(WHOLE_WORD_NAMES),
    prefix=st.text(alphabet="ab9 -'.,é", max_size=6),
    suffix=st.text(alphabet="ab9 -'.,é", max_size=6),
    embed=st.booleans(),
)
@example(name="moon", prefix="honey", suffix="", embed=True)
@example(name="a b", prefix="", suffix="a b", embed=True)
def test_whole_word_matches_exactly_where_the_name_stands_alone(name: str, prefix: str, suffix: str, embed: bool) -> None:
    text = prefix + (name if embed else "") + suffix
    assert (names.whole_word(name).search(text) is not None) == _whole_word_reference(name, text)


def test_a_long_utterance_is_scanned_for_every_name_quickly() -> None:
    # Every name over 1,000,000 characters takes about 0.1 s with the name first and took about 3 s with the
    # look-behind first; the bound leaves room for a loaded machine without letting the per-position scan back in.
    patterns = [names.whole_word(name) for name in WHOLE_WORD_NAMES]
    text = "9" * 1_000_000
    start = time.perf_counter()
    assert not any(pattern.search(text) for pattern in patterns)
    assert time.perf_counter() - start < 1.0


# Each object voice cannot reach yet, and the row of the baked OpenNGC table that holds it (None: no table does).
# apps/web/public/catalog/deepsky/openngc.json (data lane, CC BY-SA 4.0); read only.
DEEP_SKY_ROWS = {
    "the Large Magellanic Cloud": "ESO056-115",
    "the Small Magellanic Cloud": "NGC0292",
    "the Magellanic Clouds": None,
    "Omega Centauri": "NGC5139",
    "47 Tucanae": "NGC0104",
    "the Carina Nebula": "NGC3372",
    "Eta Carinae": None,
    "the Jewel Box": "NGC4755",
    "the Coalsack": "C099",
}
OPENNGC_PATH = names.NAMES_PATH.parents[2] / "deepsky" / "openngc.json"


def _openngc_rows() -> dict[str, dict[str, Any]]:
    table = json.loads(OPENNGC_PATH.read_text(encoding="utf-8"))
    return {row[0]: dict(zip(table["columns"], row)) for row in table["rows"]}


def test_each_object_voice_cannot_reach_names_its_deep_sky_row_or_none() -> None:
    assert names.UNBAKED_DEEP_SKY_ROWS == DEEP_SKY_ROWS
    assert set(DEEP_SKY_ROWS) == set(names.UNBAKED_OBJECTS)


def test_each_listed_row_is_in_the_openngc_table_under_one_of_the_spoken_names() -> None:
    rows = _openngc_rows()
    for label, row_name in DEEP_SKY_ROWS.items():
        if row_name is None:
            continue
        common = " | ".join(rows[row_name]["commonNames"]).lower()
        assert any(names.whole_word(spoken).search(common) for spoken in names.UNBAKED_OBJECTS[label]), (label, common)


def test_no_baked_table_holds_an_object_listed_as_having_no_row() -> None:
    # Fails loudly when a data table gains one of these, so its refusal can say where it is
    openngc = _openngc_rows().values()
    held = {name.lower() for row in openngc for name in (row["name"], *row["commonNames"], *row["identifiers"])}
    held |= {name.lower() for row in _rows() for name in (row["name"], row["nameAscii"])}
    for label, row_name in DEEP_SKY_ROWS.items():
        if row_name is None:
            assert not held & set(names.UNBAKED_OBJECTS[label]), label


def test_the_refusal_says_where_the_object_is_and_that_voice_cannot_reach_it() -> None:
    assert names.unbaked_refusal("Omega Centauri") == (
        "Omega Centauri is in the app's deep-sky catalogue (OpenNGC NGC5139), but voice does not reach "
        "deep-sky objects yet, so it cannot be selected, described or flown to."
    )
    assert names.unbaked_refusal("the Coalsack") == (
        "The Coalsack is in the app's deep-sky catalogue (OpenNGC C099), but voice does not reach "
        "deep-sky objects yet, so it cannot be selected, described or flown to."
    )
    assert names.unbaked_refusal("Eta Carinae") == (
        "Eta Carinae has no named row in the app's catalogues yet, so it cannot be selected, described or "
        "flown to by name."
    )
    for label in names.UNBAKED_OBJECTS:
        assert "not in the app's baked data" not in names.unbaked_refusal(label)


def test_the_model_prompt_no_longer_says_these_objects_are_missing_from_the_data() -> None:
    assert "Not in the app's data yet" not in claude.SYSTEM_PROMPT
    assert "Voice cannot reach these yet, so call no tool when the user names one:" in claude.SYSTEM_PROMPT


def test_the_typescript_parser_names_the_same_deep_sky_rows() -> None:
    import re

    ts_rows = {
        label: (None if row == "null" else row.strip("'"))
        for label, row in re.findall(r"^\s*'([^']+)': ('[^']+'|null),$", _ts_block("UNBAKED_DEEP_SKY_ROWS"), re.M)
    }
    assert ts_rows == DEEP_SKY_ROWS
