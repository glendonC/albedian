"""Colour decode and encode for restoration (`services/media/colour.py`; design section 1; REQ-RST-3.4 to 3.6):
the four transfer functions and the ICC curves.

Every formula a test compares with is written out here from its standard, independently of the module: sRGB and
the two HDR transfers from Rec. ITU-T H.273 (07/2024) Table 3, BT.709 from Rec. ITU-R BT.709-6 item 1.2, the ICC
parametric curves from ICC.1:2022 clause 10.18 (Tables 67 and 68), as design section 1 quotes them.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from services.media import colour

FORMULA_TOLERANCE = 1e-9  # P-RST-5: the standard's formula at seeded points


def srgb_decode(v: float) -> float:
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def bt709_decode(v: float) -> float:
    return v / 4.5 if v < 0.081 else ((v + 0.099) / 1.099) ** (1 / 0.45)


def hlg_decode(v: float) -> float:
    a, b, c = 0.17883277, 0.28466892, 0.55991073
    return v * v / 3 if v <= 0.5 else (math.exp((v - c) / a) + b) / 12


def pq_decode(v: float) -> float:
    c1, c2, c3, m, n = 107 / 128, 2413 / 128, 2392 / 128, 2523 / 32, 1305 / 8192
    p = v ** (1 / m)
    return (max(p - c1, 0.0) / (c2 - c3 * p)) ** (1 / n)


FORMULAS = {"srgb": srgb_decode, "bt709": bt709_decode, "hlg": hlg_decode, "pq": pq_decode}


def srgb_encode(light: float) -> float:
    return 12.92 * light if light <= 0.0031308 else 1.055 * light ** (1 / 2.4) - 0.055


def bt709_encode(light: float) -> float:
    return 4.5 * light if light < 0.018 else 1.099 * light**0.45 - 0.099


def hlg_encode(light: float) -> float:
    a, b, c = 0.17883277, 0.28466892, 0.55991073
    return math.sqrt(3 * light) if light <= 1 / 12 else a * math.log(12 * light - b) + c


def pq_encode(light: float) -> float:
    c1, c2, c3, m, n = 107 / 128, 2413 / 128, 2392 / 128, 2523 / 32, 1305 / 8192
    return ((c1 + c2 * light**n) / (1 + c3 * light**n)) ** m


ENCODE_FORMULAS = {"srgb": srgb_encode, "bt709": bt709_encode, "hlg": hlg_encode, "pq": pq_encode}


@pytest.mark.parametrize("name", colour.TRANSFERS)
@pytest.mark.parametrize("bits", [8, 10])
def test_p_rst_5_every_code_comes_back_and_decoding_rises_strictly(name: str, bits: int) -> None:
    top = 2**bits - 1
    codes = np.arange(top + 1)
    light = colour.decode_transfer(name, codes / top)
    assert np.all(np.diff(light) > 0)
    assert np.array_equal(np.round(colour.encode_transfer(name, light) * top).astype(int), codes)


@settings(max_examples=60, deadline=None)
@given(name=st.sampled_from(colour.TRANSFERS), v=st.floats(0.0, 1.0), light=st.floats(0.0, 1.0))
@example(name="srgb", v=0.04045, light=0.0031308)
@example(name="bt709", v=0.081, light=0.018)
@example(name="hlg", v=0.5, light=1 / 12)
@example(name="pq", v=0.0, light=0.0)
def test_p_rst_5_each_transfer_is_its_standards_formula(name: str, v: float, light: float) -> None:
    # Decode and encode each as their formula, the knees included (BT.709's rounded constants make its two segments
    # meet only to about 3e-4, so the round trip is asked of the codes, by the test above, not of every real value)
    assert float(colour.decode_transfer(name, np.array(v))) == pytest.approx(FORMULAS[name](v), abs=FORMULA_TOLERANCE)
    assert float(colour.encode_transfer(name, np.array(light))) == pytest.approx(ENCODE_FORMULAS[name](light), abs=FORMULA_TOLERANCE)


@pytest.mark.parametrize("name", colour.TRANSFERS)
def test_below_black_each_transfer_is_odd_and_above_peak_it_follows_its_end_slope(name: str) -> None:
    v = np.array([0.01, 0.2, 0.7])
    assert np.array_equal(colour.decode_transfer(name, -v), -colour.decode_transfer(name, v))
    assert np.array_equal(colour.encode_transfer(name, -v), -colour.encode_transfer(name, v))
    # The end slope, from the curve's own values just under 1, and the straight continuation beyond it
    step = 1e-7
    slope = (colour.decode_transfer(name, np.array(1.0)) - colour.decode_transfer(name, np.array(1.0 - step))) / step
    beyond = colour.decode_transfer(name, np.array([1.1, 1.5]))
    expected = colour.decode_transfer(name, np.array(1.0)) + slope * np.array([0.1, 0.5])
    assert beyond == pytest.approx(expected, rel=1e-5)
    # Encode undoes decode out there too, so a value past either end comes back
    for value in (-0.3, 1.3):
        assert float(colour.encode_transfer(name, colour.decode_transfer(name, np.array(value)))) == pytest.approx(value, abs=1e-9)


def test_an_unknown_transfer_is_refused() -> None:
    for function in (colour.decode_transfer, colour.encode_transfer):
        with pytest.raises(ValueError, match="a transfer this pipeline does not carry: linear"):
            function("linear", np.array(0.5))


@pytest.mark.parametrize("name", colour.TRANSFERS)
def test_no_transfer_takes_the_log_or_root_of_a_value_outside_its_domain_anywhere(name: str) -> None:
    # Each branch is evaluated only where it applies (HLG's log below 1/12 would be of a negative number), so no
    # invalid value or division by zero is met at any input, from below black to past peak
    values = np.concatenate([np.linspace(-2.0, 2.0, 4001), [0.0, 1 / 12, 0.5, 1.0], np.geomspace(1e-12, 1.0, 200)])
    with np.errstate(invalid="raise", divide="raise"):
        light = colour.decode_transfer(name, values)
        codes = colour.encode_transfer(name, values)
    assert np.isfinite(light).all() and np.isfinite(codes).all()


# ICC parametric curves: (type, parameters g, a, b, c, d, e, f as the type uses them) and the clause's formula
def icc_formula(kind: int, p: tuple[float, ...], x: float) -> float:
    g = p[0]
    if kind == 0:
        return x**g
    a, b = p[1], p[2]
    if kind == 1:
        return (a * x + b) ** g if x >= -b / a else 0.0
    if kind == 2:
        return (a * x + b) ** g + p[3] if x >= -b / a else p[3]
    c, d = p[3], p[4]
    if kind == 3:
        return (a * x + b) ** g if x >= d else c * x
    e, f = p[5], p[6]
    return (a * x + b) ** g + e if x >= d else c * x + f


# sRGB as an ICC profile writes it (type 3), and curves of each other type with values a profile could hold
ICC_CURVES = [
    (0, (2.2,)),
    (1, (2.4, 1.1, -0.1)),
    (2, (2.0, 1.2, -0.2, 0.05)),
    (3, (2.4, 1 / 1.055, 0.055 / 1.055, 1 / 12.92, 0.04045)),
    (4, (2.2, 0.95, 0.05, 0.2, 0.1, 0.01, 0.004)),
]


@pytest.mark.parametrize("kind,parameters", ICC_CURVES, ids=[f"type {k}" for k, _ in ICC_CURVES])
def test_each_icc_parametric_curve_is_its_clauses_formula_and_is_undone(kind: int, parameters: tuple[float, ...]) -> None:
    curve = colour.IccParametric(kind, parameters)
    x = np.linspace(0, 1, 1001)
    y = curve.decode(x)
    assert y == pytest.approx([icc_formula(kind, parameters, float(v)) for v in x], abs=FORMULA_TOLERANCE)
    rising = x[np.concatenate([[True], np.diff(y) > 0])]
    assert curve.encode(curve.decode(rising)) == pytest.approx(rising, abs=1e-9)


def test_an_icc_curve_that_falls_anywhere_cannot_be_undone() -> None:
    # Type 4 with a linear part that ends above where the power part starts: it drops at its knee
    falling = colour.IccParametric(4, (2.2, 0.95, 0.05, 0.3, 0.1, 0.0, 0.002))
    assert np.any(np.diff(falling.decode(np.linspace(0, 1, 1001))) < 0)
    with pytest.raises(ValueError):
        falling.encode(np.array([0.02]))


# Curves whose line part ends a hair above where the power part starts (g 1, a 1, b 0, c 1 + 1e-6): a drop of 1e-6 of
# the knee's value, far under what the curve rises between two of the check's 4097 points, so only the knee's own two
# points show it; at a knee below 0.5 and one above it
HAIR = 1 + 1e-6
KNEE_DROPS = [(3, (1.0, 1.0, 0.0, HAIR, 0.3)), (4, (1.0, 1.0, 0.0, HAIR, 0.3, 0.0, 0.0)),
              (3, (1.0, 1.0, 0.0, HAIR, 0.75)), (4, (1.0, 1.0, 0.0, HAIR, 0.75, 0.01, 0.01))]


@pytest.mark.parametrize("kind,parameters", KNEE_DROPS, ids=["type 3 at 0.3", "type 4 at 0.3", "type 3 at 0.75", "type 4 at 0.75"])
def test_a_knee_that_drops_by_less_than_the_grid_can_see_cannot_be_undone(kind: int, parameters: tuple[float, ...]) -> None:
    curve = colour.IccParametric(kind, parameters)
    grid = np.linspace(0.0, 1.0, colour.MONOTONE_CHECK_POINTS)
    assert not np.any(np.diff(curve.decode(grid)) < 0)
    knee = parameters[4]
    assert curve.decode(np.array([np.nextafter(knee, 0.0)]))[0] > curve.decode(np.array([knee]))[0]
    with pytest.raises(ValueError, match="falls somewhere"):
        curve.encode(np.array([0.5]))


def test_a_piece_that_falls_above_one_half_away_from_the_knee_cannot_be_undone() -> None:
    # Type 4 rising on its line part to d = 0.6 and falling on its power part from there to 1 (a negative)
    curve = colour.IccParametric(4, (1.0, -0.5, 1.0, 0.5, 0.6, 0.0, 0.0))
    assert curve.decode(np.array([0.5, np.nextafter(0.6, 0.0), 0.6, 1.0])) == pytest.approx([0.25, 0.3, 0.7, 0.5], abs=1e-12)
    with pytest.raises(ValueError, match="falls somewhere"):
        curve.encode(np.array([0.2]))


def test_icc_inversion_checks_both_ends_of_zero_to_one_and_bisects_sixty_times() -> None:
    assert (colour.INVERSE_STEPS, colour.MONOTONE_CHECK_POINTS) == (60, 4097)
    # Bisecting [0, 1] 60 times for y = 0 keeps low = 0 and halves high 60 times, returning midpoint 2^-61
    assert colour.IccParametric(0, (1.0,)).encode(np.array([0.0]))[0] == pytest.approx(2.0**-61, abs=2.0**-63)
    # Continuation above 1 uses the left difference over 2^-26, and 0 stays +0.0 (not negated as < 0)
    expected_at_two = 1.0 + (1.0 - ((1.0 - 2.0**-26 + 0.055) / 1.055) ** 2.4) / 2.0**-26
    assert colour.decode_transfer("srgb", np.array([2.0]))[0] == pytest.approx(expected_at_two, abs=1e-11)
    assert not np.signbit(colour.decode_transfer("srgb", np.array([0.0]))[0])
    # ICC types 1 and 2 include the knee X = -b/a in the power piece (0^0 = 1 when g = 0)
    assert colour.IccParametric(1, (0.0, 1.0, -0.5)).decode(np.array([0.5]))[0] == pytest.approx(1.0, abs=1e-15)
    assert colour.IccParametric(2, (0.0, 1.0, -0.5, 0.25)).decode(np.array([0.5]))[0] == pytest.approx(1.25, abs=1e-15)
    # Drops confined inside (0, 0.001) or (0.99, 1.0), both on a piece and at a knee, are caught
    near_zero_piece = colour.IccParametric(4, (1.0, 1.0, 0.0, -1.0, 0.0005, 0.01, 0.01))
    near_one_piece = colour.IccParametric(4, (1.0, -0.5, 1.0, 0.4, 0.995, 0.0, 0.0))
    near_zero_knee = colour.IccParametric(3, (1.0, 1.0, 0.0, HAIR, 0.0005))
    near_one_knee = colour.IccParametric(3, (1.0, 1.0, 0.0, HAIR, 0.995))
    for bad in (near_zero_piece, near_one_piece, near_zero_knee, near_one_knee):
        with pytest.raises(ValueError, match="falls somewhere"):
            bad.encode(np.array([0.5]))


@pytest.mark.parametrize("kind,parameters", [(1, (2.0, -1.0, 1.0)), (2, (2.0, -1.0, 1.0, 0.1))], ids=["type 1", "type 2"])
def test_a_curve_whose_a_is_negative_is_its_clauses_formula_too(kind: int, parameters: tuple[float, ...]) -> None:
    # -b/a is 1 here, so the power part applies at X = 1 alone, where it is 0
    x = np.linspace(0, 1, 101)
    assert colour.IccParametric(kind, parameters).decode(x) == pytest.approx(
        [icc_formula(kind, parameters, float(v)) for v in x], abs=FORMULA_TOLERANCE)


def test_a_device_value_past_one_is_read_as_one_by_every_icc_curve() -> None:
    for curve in (colour.IccParametric(0, (2.2,)), colour.IccTable((300,)), colour.IccTable((0, 1000, 65535))):
        assert curve.decode(np.array([1.5]))[0] == curve.decode(np.array([1.0]))[0]


def test_an_icc_curve_with_the_wrong_number_of_parameters_is_refused() -> None:
    with pytest.raises(ValueError):
        colour.IccParametric(3, (2.4, 1.0))
    with pytest.raises(ValueError):
        colour.IccParametric(5, (2.4,))


def test_an_icc_curve_with_a_zero_divisor_or_non_finite_values_cannot_be_used() -> None:
    with pytest.raises(ValueError, match="scale a"):
        colour.IccParametric(1, (2.2, 0.0, 0.1))
    with pytest.raises(ValueError, match="scale a"):
        colour.IccParametric(2, (2.2, 0.0, 0.1, 0.05))
    with pytest.raises(ValueError, match="falls somewhere"):
        colour.IccParametric(0, (-1.0,)).encode(np.array([0.5]))
    with pytest.raises(ValueError, match="gamma of zero"):
        colour.IccTable((0,)).encode(np.array([0.5]))


def test_an_icc_table_is_read_by_linear_interpolation_and_undone() -> None:
    # No entry is the identity, one entry a gamma in u8Fixed8, more a table of 16 bit values
    assert colour.IccTable(()).decode(np.array([0.25, 0.5])) == pytest.approx([0.25, 0.5], abs=1e-15)
    assert colour.IccTable((563,)).decode(np.array([0.5])) == pytest.approx([0.5 ** (563 / 256)], abs=1e-12)
    table = colour.IccTable((0, 1000, 30000, 65535))
    assert table.decode(np.array([0.0, 1 / 6, 1 / 3, 1.0])) == pytest.approx([0.0, 500 / 65535, 1000 / 65535, 1.0], abs=1e-12)
    y = table.decode(np.linspace(0, 1, 101))
    assert table.encode(y) == pytest.approx(np.linspace(0, 1, 101), abs=1e-9)


def test_an_icc_table_of_any_length_is_undone() -> None:
    # No entry (the identity), one (a gamma), two (a straight table) and more, each undone on 0 to 1
    x = np.linspace(0, 1, 101)
    for entries in ((), (563,), (256,), (100,), (0, 65535), (1000, 60000), (0, 1000, 30000, 65535)):
        curve = colour.IccTable(entries)
        assert curve.encode(curve.decode(x)) == pytest.approx(x, abs=1e-9), entries


def test_an_icc_table_that_does_not_rise_strictly_cannot_be_undone() -> None:
    with pytest.raises(ValueError):
        colour.IccTable((0, 1000, 1000, 65535)).encode(np.array([0.5]))


# Y'CbCr: ranges, matrices, chroma siting (design section 1)
@pytest.mark.parametrize("bits", [8, 10])
@pytest.mark.parametrize("colour_range", ["limited", "full"])
def test_every_legal_code_comes_back_through_the_range_and_the_formulas_are_the_standards(bits: int, colour_range: str) -> None:
    low, high = colour.legal_codes(bits, colour_range)
    codes = np.arange(low, high + 1)
    y, cb, cr = colour.codes_to_ycbcr(codes, codes, codes, bits, colour_range)
    if colour_range == "limited":
        # BT.709-6 item 3.4: Y' = (D / 2^(n-8) - 16) / 219, C = (D / 2^(n-8) - 128) / 224
        assert y == pytest.approx((codes / 2 ** (bits - 8) - 16) / 219, abs=1e-15)
        assert cb == pytest.approx((codes / 2 ** (bits - 8) - 128) / 224, abs=1e-15)
    else:
        # H.273 equations 36 to 38
        assert y == pytest.approx(codes / (2**bits - 1), abs=1e-15)
        assert cr == pytest.approx((codes - 2 ** (bits - 1)) / (2**bits - 1), abs=1e-15)
    back = colour.ycbcr_to_codes(y, cb, cr, bits, colour_range)
    assert all(np.array_equal(plane, codes) for plane in back)


def test_the_legal_codes_keep_the_timing_codes_free_in_limited_range() -> None:
    assert colour.legal_codes(8, "limited") == (1, 254)
    assert colour.legal_codes(10, "limited") == (4, 1019)
    assert colour.legal_codes(8, "full") == (0, 255)
    assert colour.legal_codes(10, "full") == (0, 1023)
    # A value past either end is kept to the end's code, not wrapped
    y, cb, cr = colour.ycbcr_to_codes(np.array([-0.5, 1.5]), np.array([-1.0, 1.0]), np.array([0.0, 0.0]), 8, "limited")
    assert y.tolist() == [1, 254] and cb.tolist() == [1, 254] and cr.tolist() == [128, 128]
    with pytest.raises(ValueError):
        colour.legal_codes(12, "full")
    with pytest.raises(ValueError):
        colour.legal_codes(8, "studio")


@pytest.mark.parametrize("matrix", ["bt709", "bt601", "bt2020nc"])
def test_each_matrix_is_h273s_equations_and_its_inverse(matrix: str) -> None:
    kr, kb = {"bt709": (0.2126, 0.0722), "bt601": (0.299, 0.114), "bt2020nc": (0.2627, 0.0593)}[matrix]
    rgb = np.random.default_rng(5).uniform(-0.1, 1.1, size=(4, 5, 3))
    y, cb, cr = colour.rgb_to_ycbcr(rgb, matrix)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    # H.273 equations 45 to 47
    assert y == pytest.approx(kr * r + (1 - kr - kb) * g + kb * b, abs=1e-15)
    assert cb == pytest.approx(0.5 * (b - y) / (1 - kb), abs=1e-15)
    assert cr == pytest.approx(0.5 * (r - y) / (1 - kr), abs=1e-15)
    assert colour.ycbcr_to_rgb(y, cb, cr, matrix) == pytest.approx(rgb, abs=1e-12)
    # White and black carry no chroma
    assert colour.ycbcr_to_rgb(np.array(1.0), np.array(0.0), np.array(0.0), matrix) == pytest.approx([1, 1, 1], abs=1e-15)


def test_bt2020s_own_divisors_are_the_matrix_constants() -> None:
    # BT.2020-2 Table 4 writes Cb = (B' - Y') / 1.8814 and Cr = (R' - Y') / 1.4746
    kr, kb = colour.MATRICES["bt2020nc"]
    assert 2 * (1 - kb) == pytest.approx(1.8814, abs=1e-12)
    assert 2 * (1 - kr) == pytest.approx(1.4746, abs=1e-12)


def test_an_unknown_matrix_is_refused() -> None:
    with pytest.raises(ValueError):
        colour.ycbcr_to_rgb(np.array(0.5), np.array(0.0), np.array(0.0), "identity")
    with pytest.raises(ValueError):
        colour.rgb_to_ycbcr(np.zeros((1, 1, 3)), "bt2020c")


def chroma_at(plane: np.ndarray, place_y: float, place_x: float) -> float:
    """Bilinear reading of a chroma plane at a place in chroma samples, the edge held: written per pixel."""
    rows, columns = plane.shape
    place_y, place_x = min(max(place_y, 0.0), rows - 1.0), min(max(place_x, 0.0), columns - 1.0)
    y0, x0 = int(math.floor(place_y)), int(math.floor(place_x))
    y1, x1 = min(y0 + 1, rows - 1), min(x0 + 1, columns - 1)
    ty, tx = place_y - y0, place_x - x0
    top = (1 - tx) * plane[y0, x0] + tx * plane[y0, x1]
    bottom = (1 - tx) * plane[y1, x0] + tx * plane[y1, x1]
    return float((1 - ty) * top + ty * bottom)


@pytest.mark.parametrize("subsampling", ["420", "422"])
@pytest.mark.parametrize("siting", ["left", "center", "topleft"])
def test_chroma_is_brought_up_by_linear_interpolation_at_its_siting(subsampling: str, siting: str) -> None:
    height, width = 6, 8
    down = 2 if subsampling == "420" else 1
    plane = np.random.default_rng(9).uniform(-0.5, 0.5, size=(height // down, width // 2))
    full = colour.chroma_up(plane, height, width, subsampling, siting)
    # Where each siting puts a chroma sample, in luma samples after the first of its pair (design section 1)
    across = {"left": 0.0, "center": 0.5, "topleft": 0.0}[siting]
    between = {"left": 0.5, "center": 0.5, "topleft": 0.0}[siting] if down == 2 else 0.0
    for j in range(height):
        for i in range(width):
            expected = chroma_at(plane, (j - between) / down, (i - across) / 2)
            assert full[j, i] == pytest.approx(expected, abs=1e-15)
    # A co-sited sample comes through unchanged; a flat plane stays flat
    if siting == "topleft":
        assert np.array_equal(full[::down, ::2], plane)
    assert colour.chroma_up(np.full((3, 4), 0.25), 6, 8, "420", siting) == pytest.approx(np.full((6, 8), 0.25), abs=1e-15)


def test_interpolation_weights_are_never_negative_and_sum_to_one() -> None:
    for factor, offset in ((2, 0.0), (2, 0.5), (1, 0.0)):
        weights = colour.interpolation_weights(5, 10 if factor == 2 else 5, factor, offset)
        assert np.all(weights >= 0)
        assert weights.sum(axis=1) == pytest.approx(np.ones(weights.shape[0]), abs=1e-15)
    # At factor 1 with more luma than chroma samples, the last chroma sample is held regardless of offset
    for off in (0.0, 0.5):
        k, t = colour.interpolation_taps(3, 5, 1, off)
        assert k.tolist() == [0, 1, 2, 2, 2] and t.tolist() == [0.0] * 5
    assert colour._axis_offsets("444", None) == ((1, 0.0), (1, 0.0))
    assert colour._axis_offsets("422", "left") == ((1, 0.0), (2, 0.0))


def test_full_chroma_is_passed_through_and_an_unknown_siting_is_refused() -> None:
    plane = np.arange(12.0).reshape(3, 4)
    assert np.array_equal(colour.chroma_up(plane, 3, 4, "444", None), plane)
    with pytest.raises(ValueError):
        colour.chroma_up(plane, 6, 8, "420", None)
    with pytest.raises(ValueError):
        colour.chroma_up(plane, 6, 8, "420", "bottom")


# Chroma back down (LEAD ANSWER 2026-11-12 20:59): the least squares left inverse of the interpolation's own weights
# One table of edges for every chroma test below: the smallest frames, odd sizes on each axis, and a few ordinary ones
EDGE_SIZES = [(1, 1), (1, 2), (2, 1), (2, 2), (3, 3), (5, 7), (8, 6), (9, 9), (16, 11)]
SUBSAMPLED = [(s, siting) for s in ("420", "422") for siting in ("left", "center", "topleft")]
LEFT_INVERSE_TOLERANCE = 1e-9  # the way down returns interpolated chroma to within this, in units of the plane


def chroma_shape(height: int, width: int, subsampling: str) -> tuple[int, int]:
    across, down = colour.SUBSAMPLING[subsampling]
    return math.ceil(height / down), math.ceil(width / across)


@pytest.mark.parametrize("height,width", EDGE_SIZES)
@pytest.mark.parametrize("subsampling,siting", SUBSAMPLED)
def test_chroma_brought_up_comes_back_down_to_itself(height: int, width: int, subsampling: str, siting: str) -> None:
    plane = np.random.default_rng(height * 100 + width).uniform(-0.5, 0.5, size=chroma_shape(height, width, subsampling))
    full = colour.chroma_up(plane, height, width, subsampling, siting)
    back = colour.chroma_down(full, subsampling, siting)
    assert back.shape == plane.shape
    assert np.abs(back - plane).max() <= LEFT_INVERSE_TOLERANCE


@pytest.mark.parametrize("height,width", [s for s in EDGE_SIZES if s != (1, 1)])
@pytest.mark.parametrize("subsampling,siting", SUBSAMPLED)
def test_chroma_down_is_the_least_squares_solution_on_any_plane(height: int, width: int, subsampling: str, siting: str) -> None:
    # Against the dense pseudo-inverses of the two axes' weight matrices, for a plane no interpolation made
    full = np.random.default_rng(7).uniform(-0.5, 0.5, size=(height, width))
    chroma_h, chroma_w = chroma_shape(height, width, subsampling)
    across, down = colour.SUBSAMPLING[subsampling]
    offset_x, offset_y = colour.SITING_OFFSETS[siting]
    rows = colour.interpolation_weights(chroma_h, height, down, offset_y if down > 1 else 0.0)
    columns = colour.interpolation_weights(chroma_w, width, across, offset_x)
    expected = np.linalg.pinv(rows) @ full @ np.linalg.pinv(columns).T
    assert np.abs(colour.chroma_down(full, subsampling, siting) - expected).max() <= LEFT_INVERSE_TOLERANCE


def test_chroma_down_uses_the_samples_between_the_sited_ones() -> None:
    # Co-sited chroma: a change of one sample between two sited ones moves the solution, where picking the sited
    # samples would not see it
    plane = np.random.default_rng(3).uniform(-0.4, 0.4, size=(4, 4))
    full = colour.chroma_up(plane, 8, 8, "420", "topleft")
    changed = full.copy()
    changed[4, 3] += 0.1
    moved = colour.chroma_down(changed, "420", "topleft") - colour.chroma_down(full, "420", "topleft")
    assert np.abs(moved).max() > 0.01
    assert np.array_equal(changed[::2, ::2], full[::2, ::2]), "the sited samples themselves did not change"


@pytest.mark.parametrize("siting,offset", [("co-sited", 0.0), ("centred", 0.5)])
def test_an_error_grows_on_the_way_down_by_at_most_its_stated_bound_and_reaches_it(siting: str, offset: float) -> None:
    # The largest row sum of the left inverse's magnitudes is what an error of at most one unit in every full size
    # sample can become in one subsampled sample; an error of the signs of that row reaches it exactly
    bound = 1 + 1 / math.sqrt(2) if offset == 0.0 else 2.0
    assert bound <= colour.DOWN_GAIN_PER_AXIS
    # The stated bound is the worse siting's (centred, 2), which is reached, so it is no looser than what happens
    assert colour.DOWN_GAIN_PER_AXIS == max(1 + 1 / math.sqrt(2), 2.0)
    largest = 0.0
    for luma in list(range(2, 129)) + [255, 256, 1079, 1080]:
        chroma = math.ceil(luma / 2)
        inverse = np.linalg.pinv(colour.interpolation_weights(chroma, luma, 2, offset))
        sums = np.abs(inverse).sum(axis=1)
        largest = max(largest, float(sums.max()))
        assert sums.max() <= bound + 1e-9
        worst = int(np.argmax(sums))
        error = np.sign(inverse[worst])[:, None] * np.ones((1, 3))
        grown = colour.chroma_down(np.vstack([error, np.zeros((0, 3))]).T, "422", "left" if offset == 0.0 else "center")
        assert np.abs(grown).max() <= bound + 1e-9
        assert np.abs(grown[:, worst]).max() == pytest.approx(sums.max(), abs=1e-9)
    assert largest > bound - 0.01, "the bound is reached, not far above what happens"
    # Both axes of 4:2:0, at the siting whose two axes have this offset (topleft co-sited both ways, center centred
    # both ways; left mixes the two): at most the product, and the outer product of the two worst rows' signs gives it
    rows = np.linalg.pinv(colour.interpolation_weights(5, 9, 2, offset))
    columns = np.linalg.pinv(colour.interpolation_weights(6, 11, 2, offset))
    r, c = int(np.argmax(np.abs(rows).sum(axis=1))), int(np.argmax(np.abs(columns).sum(axis=1)))
    grown = colour.chroma_down(np.outer(np.sign(rows[r]), np.sign(columns[c])), "420", "topleft" if offset == 0.0 else "center")
    assert np.abs(grown).max() <= bound * bound + 1e-9 <= colour.DOWN_GAIN_PER_AXIS**2 + 1e-9
    assert grown[r, c] == pytest.approx(np.abs(rows[r]).sum() * np.abs(columns[c]).sum(), abs=1e-9)


def test_full_chroma_passes_down_unchanged_and_an_unknown_siting_is_refused() -> None:
    plane = np.arange(12.0).reshape(3, 4)
    assert np.array_equal(colour.chroma_down(plane, "444", None), plane)
    with pytest.raises(ValueError):
        colour.chroma_down(plane, "420", None)
    with pytest.raises(ValueError):
        colour.chroma_down(plane, "422", "bottom")


@settings(max_examples=60, deadline=None)
@given(
    size=st.sampled_from(EDGE_SIZES), bits=st.sampled_from([8, 10]), colour_range=st.sampled_from(["limited", "full"]),
    matrix=st.sampled_from(["bt709", "bt601", "bt2020nc"]), layout=st.sampled_from([("444", None), *SUBSAMPLED]),
    seed=st.integers(0, 2**32 - 1), extremes=st.booleans(),
)
@example(size=(5, 7), bits=8, colour_range="limited", matrix="bt709", layout=("420", "left"), seed=0, extremes=True)
@example(size=(9, 9), bits=10, colour_range="full", matrix="bt2020nc", layout=("422", "center"), seed=1, extremes=True)
@example(size=(1, 1), bits=10, colour_range="limited", matrix="bt601", layout=("420", "topleft"), seed=2, extremes=False)
def test_p_rst_6_a_ycbcr_frame_comes_back_through_rgb(size, bits, colour_range, matrix, layout, seed, extremes) -> None:
    # Any chroma, random or at the legal codes' two ends, not only smooth chroma: luma within 1 code, chroma within 2
    height, width = size
    subsampling, siting = layout
    low, high = colour.legal_codes(bits, colour_range)
    rng = np.random.default_rng(seed)
    chroma_size = chroma_shape(height, width, subsampling)

    def codes(shape):
        return rng.choice([low, high], size=shape) if extremes else rng.integers(low, high + 1, size=shape)

    y_codes, cb_codes, cr_codes = codes((height, width)), codes(chroma_size), codes(chroma_size)
    y, cb, cr = colour.codes_to_ycbcr(y_codes, cb_codes, cr_codes, bits, colour_range)
    rgb = colour.ycbcr_to_rgb(y, colour.chroma_up(cb, height, width, subsampling, siting),
                              colour.chroma_up(cr, height, width, subsampling, siting), matrix)
    y2, cb2, cr2 = colour.rgb_to_ycbcr(rgb, matrix)
    back = colour.ycbcr_to_codes(y2, colour.chroma_down(cb2, subsampling, siting), colour.chroma_down(cr2, subsampling, siting), bits, colour_range)
    assert np.abs(back[0] - y_codes).max() <= 1
    assert max(np.abs(back[1] - cb_codes).max(), np.abs(back[2] - cr_codes).max()) <= 2


def test_where_keeping_legal_codes_makes_the_way_back_inexact() -> None:
    # A processed plane whose solved chroma lies past the legal codes is kept at the end code: the only place the
    # round trip is not exact. Interpolated chroma never goes there (its solution is the original's legal codes).
    full = np.full((4, 4), 0.6)  # past Cb's top of 0.5 in every sample
    cb = colour.chroma_down(full, "420", "left")
    _, cb_codes, _ = colour.ycbcr_to_codes(np.zeros((4, 4)), cb, cb, 8, "limited")
    assert np.all(cb_codes == colour.legal_codes(8, "limited")[1])
