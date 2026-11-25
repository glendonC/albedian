"""body-identity-one (AUDIT's word, LEAD ANSWER 19:47): an answer about the Moon or the Sun gives the body its article,
"The Moon was 32.2 degrees up", "the Moon" after the class words; a planet takes none."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from services.voice.answers import answer_question

ROOT = Path(__file__).resolve().parents[2]
SOLUTIONS = json.loads((ROOT / "tests" / "fixtures" / "python_contract_solutions.json").read_text(encoding="utf-8"))
HORIZON = SOLUTIONS["horizon_approximate"]


def test_the_moon_takes_its_article() -> None:
    answer = answer_question("where is the Moon", HORIZON, None)
    assert answer["sentence"] == "Predicted, not measured: the Moon was 32.2 degrees up, at azimuth 224.3 degrees.", answer


def test_the_sun_takes_its_article_and_a_planet_none() -> None:
    sky = copy.deepcopy(HORIZON)
    moon = next(f for f in sky["findings"] if f["id"] == "finding-predicted-moon")
    sun = {**copy.deepcopy(moon), "id": "finding-predicted-sun", "title": "Sun"}
    sun["evidence"] = {**sun["evidence"], "id": "ev-ephem-sun"}
    jupiter = {**copy.deepcopy(moon), "id": "finding-predicted-jupiter", "title": "Jupiter"}
    jupiter["evidence"] = {**jupiter["evidence"], "id": "ev-ephem-jupiter"}
    sky["findings"] = [f for f in sky["findings"] if f["id"] != "finding-predicted-moon"] + [sun, jupiter]
    said_sun = answer_question("where is the Sun", sky, None)["sentence"]
    assert said_sun.startswith("Predicted, not measured: the Sun was "), said_sun
    said_jupiter = answer_question("where is Jupiter", sky, None)["sentence"]
    assert said_jupiter.startswith("Predicted, not measured: Jupiter was "), said_jupiter
