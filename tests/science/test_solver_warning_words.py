"""The solver's warnings a person reads, in the design lane's plain words (C16, .lead/uiux/UIUX-solvewords-READY.md,
decided by the lead 2026-11-19 12:45; FIELD walk-0900 item 5): the no-match result shows the solve's warnings
verbatim, so a refused gate and a first match too small to check each say what happened, with no engine words.

Covers both solvers' refused-gate warning and the phone solver's too-few-stars warning: the sentences as decided, the
old engine words gone from both modules, and the plate solver writing its sentence on a refused gate.
"""

from __future__ import annotations

from pathlib import Path

from services.science import phone_photo_solver, solver

GATE_REFUSED = "A possible match was found, but the stars held back to check it did not land where it put them, so it was not kept."
TOO_FEW = "The first possible match held too few stars to be checked."
ROOT = Path(__file__).resolve().parents[2]


def test_both_solvers_say_a_refused_gate_in_the_decided_words() -> None:
    assert phone_photo_solver.GATE_REFUSED_WARNING == GATE_REFUSED
    assert solver.GATE_REFUSED_WARNING == GATE_REFUSED


def test_the_phone_solver_says_a_first_match_too_small_to_check_in_the_decided_words() -> None:
    assert phone_photo_solver.TOO_FEW_STARS_WARNING == TOO_FEW


def test_no_engine_words_are_left_in_the_warnings_a_person_reads() -> None:
    for module in ("services/science/phone_photo_solver.py", "services/science/solver.py"):
        text = (ROOT / module).read_text(encoding="utf-8")
        for old in ("did not pass coincidence-weighed held-out and dual-path verification gate",
                    "did not pass independent held-out calibration gate",
                    "Initial WCS hypothesis matched too few stars for refinement"):
            assert old not in text, (module, old)
