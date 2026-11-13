"""The photo path end to end, as pure functions (REQ-RST-4.7, 4.8, 5.4, 6.2, 8.9; P-RST-2, P-RST-10, P-RST-12),
and on the corpus photos the design measured (REQ-RST-12; design M5, M7, M12 and M13)."""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import numpy as np
import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from photo_frames import DESIGN_PHOTOS, LUMA, PHOTO_PATHS, REPO_ROOT, photo_crop, run_photo_path, star_field
from services.contracts import media as contracts
from services.media import noise, spatial, stars, tone

MODULES = (stars, noise, spatial, tone)
# A constant names its source (REQ-RST-4.7): a label of the repository's own (design.md, "Labels") in its comment
SOURCE_MARKERS = ("[proposed", "[verified", "[tested", "[decided")
# The gate's flux tolerance, in empty-aperture sigma (design section 7) [proposed there]; restore-4 owns it
FLUX_TOLERANCE_SIGMA = 3.0


def numeric_constants(module) -> list[tuple[str, int, str]]:
    """(name, line, comment above or beside it) for every module-level assignment holding a number literal."""
    source = Path(module.__file__).read_text()
    comments: dict[int, str] = {}
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            comments[token.start[0]] = token.string
    found = []
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        if not any(isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool)
                   for n in ast.walk(node.value)):
            continue
        target = node.targets[0] if isinstance(node, ast.Assign) else node.target
        line = node.lineno
        above = []
        number = line - 1
        while number in comments:
            above.insert(0, comments[number])
            number -= 1
        beside = [comments[n] for n in range(line, node.end_lineno + 1) if n in comments]
        found.append((getattr(target, "id", "?"), line, " ".join(above + beside)))
    return found


def test_every_numeric_constant_names_its_source() -> None:
    unsourced = []
    for module in MODULES:
        constants = numeric_constants(module)
        assert constants, module.__name__
        for name, line, comment in constants:
            if not any(marker in comment for marker in SOURCE_MARKERS):
                unsourced.append(f"{module.__name__}:{line} {name}")
    assert unsourced == []


DESIGN = REPO_ROOT / ".kiro/specs/restoration-for-every-upload/design.md"
WORDS = {"Twelve": 12, "quarter": 0.25, "half": 0.5}
# Each constant the design states, with the words around its value there (the value is the group in brackets)
DESIGN_VALUES = [
    (stars, "TILE_PX", r"Tiles of (\d+) px"), (stars, "OVERLAP_PX", r"with (\d+) px overlap"),
    (stars, "MATCH_RADIUS_PX", r"a verified detection within ([\d.]+) px"),
    (stars, "MERGE_RADIUS_PX", r"overlapping tiles within ([\d.]+) px are merged"), (stars, "CLIPPED_GROW_PX", r"grown by (\d+) px"),
    (stars, "FOOTPRINT_MAX_PX", r"at most (\d+) px \[proposed cap"), (stars, "FOOTPRINT_EDGE_PX", r"with a (\d+) px cosine edge"),
    (stars, "APERTURE_MIN_PX", r"\(at least (\d+) px\)"), (stars, "ANNULUS_INNER_PX", r"annulus from (\d+) to \d+ px beyond"),
    (stars, "ANNULUS_OUTER_PX", r"annulus from \d+ to (\d+) px beyond"), (stars, "EMPTY_APERTURES", r"the first (\d+) points of a fixed lattice"),
    (stars, "EMPTY_CLEARANCE_PX", r"lattice that are (\d+) px clear"),
    (noise, "BLOCK_PX", r"Blocks of (\d+) by"), (noise, "BINS", r"(Twelve) intensity bins"),
    (noise, "MIN_BLOCKS_PER_BIN", r"fewer than (\d+) blocks is dropped"), (noise, "MIN_BINS", r"fewer than (\d+) bins survive"),
    (noise, "ENERGY_PERCENTILE", r"the (\d+)th percentile of the block energies"), (noise, "MIN_SIDE_PX", r"a side is under (\d+) px"),
    (noise, "STABILISER_POINTS", r"trapezoid rule on (\d+) points"),
    (spatial, "LEVELS", r"`L = (\d+)` \[proposed\]: taps reach"), (spatial, "RANGE_SIGMAS", r"`k_r = (\d+)`"), (spatial, "WEIGHT_FLOOR", r"The floor of ([\de.-]+) on"),
    (tone, "TARGET_MEDIAN_CODE", r"for the code (\d+)/\d+ of the way"), (tone, "TARGET_CODE_STEPS", r"for the code \d+/(\d+) of the way"),
    (tone, "QUANTISATION_BOUND_STEPS", r"<= (\d+)`: one code step"), (tone, "MAXIMUM_GAIN", r"`g0 <= (\d+)`"),
    (tone, "DARK_SHARE", r"median luminance of the darkest (quarter)"),
]


@pytest.mark.parametrize("module,name,words", DESIGN_VALUES, ids=[f"{m.__name__.split('.')[-1]}.{n}" for m, n, _ in DESIGN_VALUES])
def test_each_constant_the_design_states_has_its_value(module, name: str, words: str) -> None:
    found = set(re.findall(words, DESIGN.read_text()))
    assert len(found) == 1, f"the design states {name}, and alike wherever it does"
    value = found.pop()
    stated = WORDS[value] if value in WORDS else float(value)
    assert getattr(module, name) == stated


def test_the_rounding_noise_and_the_wavelet_kernel_are_the_designs() -> None:
    text = DESIGN.read_text()
    assert "`[1, 4, 6, 4, 1] / 16`" in text and noise.B3_TAPS == tuple(tap / 16 for tap in (1, 4, 6, 4, 1))
    assert "divided by\n   `sqrt(12)`" in text and noise.ROUNDING_SIGMA == 1 / np.sqrt(12)
    # The noise is measured only while more than half of a channel's fine detail is not exactly zero (REQ-RST-4.3)
    sentence = r"more\s+than\s+half\s+of\s+a\s+channel's\s+fine\s+detail\s+is\s+exactly\s+zero"
    assert re.search(sentence, (DESIGN.parent / "requirements.md").read_text())
    assert noise.ZERO_SHARE_MAX == WORDS["half"]


def test_the_mad_factor_is_the_normal_distributions() -> None:
    from scipy.stats import norm

    for module in (stars, spatial):
        assert module.MAD_TO_SIGMA == pytest.approx(1 / norm.ppf(0.75), abs=1e-4)


def test_the_path_draws_no_random_number(monkeypatch: pytest.MonkeyPatch) -> None:
    for module in MODULES:
        assert "random" not in Path(module.__file__).read_text(), module.__name__
    scene = star_field(31, size=160, count=8)

    def refuse(*_args, **_kwargs):
        raise AssertionError("the restoration path drew a random number")

    for name in ("default_rng", "random", "normal", "uniform", "standard_normal", "seed", "rand", "randn"):
        monkeypatch.setattr(np.random, name, refuse)
    run_photo_path(scene.codes, scene.linear, scene.code_steps)


@settings(max_examples=4, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=st.integers(0, 2**16), exposure_ev=st.one_of(st.none(), st.floats(0, 3)))
@example(seed=3, exposure_ev=None)
def test_p_rst_10_the_same_input_and_request_give_the_same_output(seed, exposure_ev) -> None:
    scene = star_field(seed, size=160, count=10)
    first = run_photo_path(scene.codes, scene.linear, scene.code_steps, exposure_ev=exposure_ev)
    second = run_photo_path(scene.codes.copy(), scene.linear.copy(), scene.code_steps, exposure_ev=exposure_ev)
    # Two runs in one process on one machine: equal arrays, not a value pinned across platforms
    assert np.array_equal(first.lifted, second.lifted)
    assert first.lift == second.lift and first.field.stars == second.field.stars


def flux_changes(seed: int, footprints: bool) -> list[float]:
    """For every confirmed star clear of the border, |F_v - F_o| in empty-aperture sigma of the original, with the
    variant's lift undone (design section 7's flux rule)."""
    scene = star_field(seed, size=256, count=20, noise_sigma=0.002, peaks=(0.01, 0.1))
    result = run_photo_path(scene.codes, scene.linear, scene.code_steps, footprints=footprints)
    original = scene.linear @ LUMA
    variant = tone.undo_lift(result.lifted, result.lift.beta) @ LUMA
    changes = []
    margin = stars.FOOTPRINT_MAX_PX + stars.ANNULUS_OUTER_PX + 2
    for star in result.field.stars:
        if not (margin <= star.x_px < 256 - margin and margin <= star.y_px < 256 - margin):
            continue
        sigma = stars.empty_aperture_sigma(original, star.radius_px, result.field.footprint, result.field.clipped)
        before = stars.aperture_flux(original, star.x_px, star.y_px, star.radius_px)
        after = stars.aperture_flux(variant, star.x_px, star.y_px, star.radius_px)
        changes.append(abs(after - before) / sigma)
    return changes


@settings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=st.integers(0, 2**16))
@example(seed=21)
def test_p_rst_12_every_confirmed_stars_flux_is_kept_within_the_tolerance(seed) -> None:
    changes = flux_changes(seed, footprints=True)
    assert changes, "no confirmed star to measure"
    assert max(changes) <= FLUX_TOLERANCE_SIGMA


def test_p_rst_12_without_footprints_a_stars_flux_is_not_kept() -> None:
    # The mutant of P-RST-12: the same path with its footprints switched off
    assert max(flux_changes(21, footprints=False)) > FLUX_TOLERANCE_SIGMA


def path_parameters(result) -> list[dict]:
    return [*stars.parameters(), *noise.parameters(), *spatial.parameters(), *result.lift.parameters()]


@settings(max_examples=4, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=st.integers(0, 2**16))
@example(seed=5)
def test_p_rst_2_every_parameter_has_an_origin_and_a_basis_and_no_default_follows_the_noise(seed) -> None:
    quiet = star_field(seed, size=192, count=8, noise_sigma=0.001)
    loud = star_field(seed, size=192, count=8, noise_sigma=0.002)
    first = path_parameters(run_photo_path(quiet.codes, quiet.linear, quiet.code_steps))
    second = path_parameters(run_photo_path(loud.codes, loud.linear, loud.code_steps))
    for parameter in first + second:
        contracts.GraphParameter.model_validate(parameter)
        assert parameter["origin"] and parameter["basis"]
    defaults = lambda parameters: {p["name"]: p["value"] for p in parameters if p["origin"] == "default"}
    assert defaults(first) == defaults(second)
    # A measured parameter follows what its basis names: the gain at black follows the frame's median
    darker = star_field(seed, size=192, count=8, sky=0.002, noise_sigma=0.001)
    measured = lambda parameters: {p["name"]: p["value"] for p in parameters if p["origin"] == "measured"}
    assert measured(first)["gainAtBlack"] != measured(path_parameters(run_photo_path(darker.codes, darker.linear,
                                                                                       darker.code_steps)))["gainAtBlack"]


@pytest.mark.local_dataset(*PHOTO_PATHS)
@pytest.mark.parametrize("photo", DESIGN_PHOTOS)
def test_the_design_photos_go_through_the_photo_path(photo: str) -> None:
    codes, linear = photo_crop(REPO_ROOT / f".cache/real-photos/{photo}/{photo}.jpg")
    result = run_photo_path(codes, linear, 255)
    field = result.field
    contracts.StarCounts.model_validate(field.counts())
    radii = [star.radius_px for star in field.stars]
    print(f"{photo}: noise {result.measurement.reason or 'measured'}, stars found {field.found} confirmed "
          f"{field.confirmed}, clipped left out {field.clipped_left_out}, footprint radius median "
          f"{np.median(radii) if radii else 'none'}, fine noise left {result.fine_noise_left}, lift "
          f"{result.lift.record()}")
    assert result.measurement.reason is None, result.measurement.reason
    contracts.NoiseTable.model_validate(result.measurement.table())
    assert 0 < result.fine_noise_left < 1
    assert 0 < field.confirmed <= field.found
    assert all(1 <= radius <= stars.FOOTPRINT_MAX_PX for radius in radii)
    contracts.Lift.model_validate(result.lift.record())


REQUIREMENTS = REPO_ROOT / ".kiro/specs/restoration-for-every-upload/requirements.md"


STEP_OF_MODULE = {stars: "find_stars", noise: "measure_noise", spatial: "spatial_denoise"}


def listed_defaults(entry: str) -> dict[str, tuple[str, str]]:
    """Each default P-RST-2 lists, by name: the step whose bullet lists it ("Defaults of `<step>`: ...") and the reason
    in the parentheses after its group of names ("`a` and `b` (why)"), one level of parentheses inside allowed."""
    found: dict[str, tuple[str, str]] = {}
    for step, body in re.findall(r"- Defaults of `(\w+)`:(.*?)(?=\n  - |\Z)", entry, re.S):
        for names, reason in re.findall(r"((?:`\w+`(?:,\s*|\s+and\s+)?)+)\s*\(((?:[^()]|\([^()]*\))*)\)", body):
            for name in re.findall(r"`(\w+)`", names):
                assert name not in found, f"{name} is listed twice"
                found[name] = (step, " ".join(reason.split()))
    return found


def test_p_rst_2_every_default_is_listed_in_the_spec_under_its_step_with_its_reason() -> None:
    text = REQUIREMENTS.read_text()
    entry = text[text.index("- **P-RST-2**"): text.index("- **P-RST-3**")]
    scene = star_field(5, size=192, count=8)
    result = run_photo_path(scene.codes, scene.linear, scene.code_steps)
    stepped = [(STEP_OF_MODULE[module], p) for module in STEP_OF_MODULE for p in module.parameters()]
    stepped += [("lift", p) for p in result.lift.parameters()]
    # The gain at black is a default only where the maximum decides it, and then it is `maximumGain`, listed itself
    defaults = {p["name"]: step for step, p in stepped if p["origin"] == "default" and p["name"] != "gainAtBlack"}
    listed = listed_defaults(entry)
    # Every default is listed under its own step, nothing is listed that is not a default, and each has a reason of
    # its own (verdict restore-3, a note: a reason deleted, or a default listed under another step, passed before)
    assert {name: step for name, (step, _) in listed.items()} == defaults
    for name, (_, reason) in listed.items():
        assert len(reason.split()) >= 3, f"{name} has no reason of its own: {reason!r}"
    # The gain at black is no default of the design's but where its maximum decides; its entry says what decides it
    assert "`gainAtBlack`" in entry and "maximum" in entry[entry.index("`gainAtBlack`"): entry.index("- Defaults of")]


@pytest.mark.local_dataset(*PHOTO_PATHS)
def test_p_rst_2_the_gain_at_black_takes_more_than_one_value_over_the_design_photos() -> None:
    gains = []
    for photo in DESIGN_PHOTOS:
        codes, linear = photo_crop(REPO_ROOT / f".cache/real-photos/{photo}/{photo}.jpg")
        gains.append(run_photo_path(codes, linear, 255).lift.record()["gainAtBlack"])
    print("gainAtBlack over the five photos:", " ".join(f"{g:.3f}" for g in gains))
    assert len({round(g, 9) for g in gains}) > 1
