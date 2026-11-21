"""The words for a time or a place the iPhone's record lent the solve, as C13 decides them.

.lead/uiux/UIUX-pointingwords-DECIDED-BY-LEAD.md (decided by the lead 2026-11-19 07:40): what the iPhone recorded,
said as a person would say it; each warning in two sentences, the first shown on screen and the second kept in the
record (C5's rule); "arcseconds" written out and a thousands comma in metres. Item 8 (the star's place caution) is
C24's since 19 Nov and is not pinned here. A lent time beside the file's own place, in an item's first limitation,
is C13's last section (UIUX-pointingwords-READY.md, ruled 21 Nov at SCIENCE-INGEST's question, decided by the lead
at 13:34).
"""

from __future__ import annotations

import pytest

from services.ingest import jobs as ingest_jobs
from services.science import predicted_sky as ps
from services.science.predicted_sky import LentMoment, lent_moment_sentence


def _arcsec(metres: float) -> str:
    return f"{ps.place_uncertainty_arcsec(metres):.1f}"


def test_the_records_time_and_place_are_said_as_a_person_would() -> None:
    assert ps.RECORD_TIME_WORDS == "the time your iPhone recorded"
    assert ps.RECORD_PLACE_WORDS == "where your iPhone was"


def test_a_lent_time_and_place_are_said_in_c13s_two_sentences() -> None:
    assert _arcsec(8.0) == "0.3"
    assert lent_moment_sentence(LentMoment("record", "record", 8.0, "full")) == (
        "The photo's file has no time or place, so the sky is predicted for the time your iPhone recorded, by its own "
        "clock, and where it was, to within 8 m. That can move the predicted sky by up to 0.3 arcseconds."
    )


def test_a_lent_time_alone_is_one_sentence() -> None:
    assert lent_moment_sentence(LentMoment("record", "file")) == (
        "The photo's file has no time, so the sky is predicted for the time your iPhone recorded, by its own clock."
    )


def test_a_lent_place_alone_says_its_accuracy_and_what_it_can_move() -> None:
    assert lent_moment_sentence(LentMoment("file", "record", 8.0, "full")) == (
        "The photo's file has no place, so the sky is predicted for where your iPhone was, to within 8 m. That can "
        "move the predicted sky by up to 0.3 arcseconds."
    )


@pytest.mark.parametrize(("time_from", "opening"), [
    ("record", "The photo's file has no time or place, so the sky is predicted for the time your iPhone recorded, by "
               "its own clock, and where it was"),
    ("file", "The photo's file has no place, so the sky is predicted for where your iPhone was"),
])
def test_a_rough_place_says_the_person_allowed_it_with_a_thousands_comma(time_from: str, opening: str) -> None:
    assert lent_moment_sentence(LentMoment(time_from, "record", 5000.0, "reduced")) == (
        f"{opening}, to within 5,000 m, the rough location you allowed. That can move the predicted sky by up to "
        f"{_arcsec(5000.0)} arcseconds."
    )


@pytest.mark.parametrize(("time_from", "sentence"), [
    ("record", "The photo's file has no time or place, so the sky is predicted for the time your iPhone recorded, by "
               "its own clock, and where it was (it gave no accuracy)."),
    ("file", "The photo's file has no place, so the sky is predicted for where your iPhone was (it gave no accuracy)."),
])
def test_a_place_with_no_stated_accuracy_states_no_bound(time_from: str, sentence: str) -> None:
    # Reached only by a direct call (the pointing contract requires an allowed place's accuracy); with no accuracy
    # there is no bound to state, so no second sentence and no number (the code said "up to 0.0 arcsec")
    assert lent_moment_sentence(LentMoment(time_from, "record", None, None)) == sentence


@pytest.mark.parametrize(("time_said", "lent", "said"), [
    ("the recorded capture time", LentMoment("record", "record", 8.0, "full"),
     "the time your iPhone recorded and where it was (within 8 m)"),
    ("the recorded capture time", LentMoment("file", "record", 8.0, "full"),
     "the recorded capture time and where your iPhone was (within 8 m)"),
    ("the recorded capture time", LentMoment("record", "record", 5000.0, "reduced"),
     "the time your iPhone recorded and where it was (within 5,000 m)"),
    ("the recorded capture time", LentMoment("record", "record", None, None),
     "the time your iPhone recorded and where it was (it gave no accuracy)"),
    ("the recorded capture time", None, "the recorded capture time and place"),
])
def test_each_predicted_items_first_limitation_names_what_the_iphone_lent(time_said: str, lent, said: str) -> None:
    assert ps._moment_said(time_said, lent) == said


def test_a_lent_time_beside_the_files_place_never_reads_as_the_iphones_place() -> None:
    # C13's last section (ruled 21 Nov, decided by the lead at 13:34): item 7's own pairs, and no "(within ...)"
    assert ps._moment_said("the recorded capture time", LentMoment("record", "file")) == (
        "the time your iPhone recorded and the photo's recorded place"
    )


def test_a_record_of_another_photo_is_said_plainly() -> None:
    assert ingest_jobs.RECORD_NOT_USED == "The iPhone record stored with this photo belongs to another photo, so it was not used."


def test_a_moment_the_record_lent_nothing_of_has_no_sentence() -> None:
    with pytest.raises(ValueError, match="lends the time, the place or both"):
        lent_moment_sentence(LentMoment("file", "file"))
