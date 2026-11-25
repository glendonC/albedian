"""voice-show-restored-one (FIELD's walk): "show the restored version" is a restore in the backend's parser too, as
the one grammar both parsers read has it (packages/observation/src/__tests__/voiceParser.showRestored.test.ts), and a
question about whether a photo was restored stays a question."""

from __future__ import annotations

import pytest

from services.voice.intent import parse_voice_intent

YEAR = 2101
CONTEXT = {"viewRevision": 4, "sourceAssetId": "src_0123456789abcdef"}


@pytest.mark.parametrize(
    "words",
    ["show the restored version", "Show me the restored version", "show the restored photo", "show the restored clip",
     "show the restoration", "show me the restoration", "the restored version", "restored version please",
     "let me see the restored version", "see the restored one"],
)
def test_asking_to_be_shown_the_restored_version_is_a_restore(words: str) -> None:
    read = parse_voice_intent(words, CONTEXT, current_year=YEAR)
    assert read.action is not None and read.action["type"] == "media.restore", (words, read)


@pytest.mark.parametrize("words", ["is it restored", "has it been restored", "was that restored", "have you restored it",
                                   "I already restored it"])
def test_a_question_about_a_restoration_is_no_restore(words: str) -> None:
    read = parse_voice_intent(words, CONTEXT, current_year=YEAR)
    assert read.action is None or read.action["type"] != "media.restore", (words, read)


def test_the_original_beside_the_restored_version_is_read_as_a_compare() -> None:
    # The backend reads it by the compare rule and, as for every compare, refuses for want of the recording's variants,
    # which only the app holds (services/voice/intent.py media.compare); never as a restore
    read = parse_voice_intent("compare the original with the restored version", CONTEXT, current_year=YEAR)
    assert read.action is None and read.reason == "missing_source", read
    assert read.explanation.startswith("Comparing needs the recording's variants"), read
