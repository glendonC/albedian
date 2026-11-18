"""Lunar phase (from services/science/ephemeris.py, on ERFA) and the Krisciunas & Schaefer (1991) moonlit sky
brightness model, predicted for a stated target.

No sensor limiting magnitude is estimated: its coefficients would have to come from a measured camera.
"""

from __future__ import annotations

import math
from typing import Any

from services.science.ephemeris import moon_phase


def compute_lunar_phase_and_illumination(obs_utc_iso: str) -> dict[str, Any]:
    """The Moon's phase from the Earth's centre at `obs_utc_iso` (services/science/ephemeris.py `moon_phase`, on
    ERFA): the synodic angle, the Sun-Moon elongation, the selenocentric phase angle `alpha_deg`, the illuminated
    fraction (0 to 1), Krisciunas and Schaefer's (1991) visual lunar magnitude at that phase angle, and the phase
    name (None within the synodic angle's bound of a sector's edge). Raises EphemerisOutOfRange outside the
    ephemeris' span.
    """
    phase = moon_phase(obs_utc_iso)
    alpha_deg = phase.phase_angle_deg
    # Krisciunas & Schaefer (1991) visual lunar magnitude formula
    lunar_visual_mag = -12.73 + 0.026 * abs(alpha_deg) + 4.0e-9 * (alpha_deg**4)
    return {
        "observedAtUTC": obs_utc_iso,
        "synodicAngleDeg": round(phase.synodic_angle_deg, 2),
        "elongation_deg": round(phase.elongation_deg, 2),
        "elongationDeg": round(phase.elongation_deg, 2),
        "alpha_deg": round(alpha_deg, 2),
        "alphaDeg": round(alpha_deg, 2),
        "illumination_fraction": round(phase.illuminated_fraction, 4),
        "illuminationFraction": round(phase.illuminated_fraction, 4),
        "lunar_visual_mag": round(lunar_visual_mag, 2),
        "lunarVisualMag": round(lunar_visual_mag, 2),
        "phase_name": phase.phase_name,
        "phaseName": phase.phase_name,
    }


def _mag_arcsec2_to_nanolamberts(mag_arcsec2: float) -> float:
    """Convert surface brightness in V mag/arcsec^2 to nanoLamberts (K&S 1991 Eq. 1)."""
    return 34.08 * math.exp(20.7233 - 0.92104 * float(mag_arcsec2))


def _nanolamberts_to_mag_arcsec2(b_nl: float) -> float:
    """Convert surface brightness in nanoLamberts to V mag/arcsec^2 (K&S 1991 Eq. 2)."""
    safe_b = max(float(b_nl), 1e-6)
    return (20.7233 - math.log(safe_b / 34.08)) / 0.92104


def _krisciunas_airmass(alt_deg: float) -> float:
    """Compute optical path airmass X(Z) = (1 - 0.96 * sin^2(Z))^(-0.5) (K&S 1991 Eq. 3)."""
    clamped_alt = max(0.0, min(90.0, float(alt_deg)))
    z_rad = math.radians(90.0 - clamped_alt)
    sin_z = math.sin(z_rad)
    return 1.0 / math.sqrt(max(0.04, 1.0 - 0.96 * sin_z * sin_z))


def compute_krisciunas_schaefer_sky_brightness(
    sep_from_moon_deg: float,
    target_alt_deg: float,
    moon_alt_deg: float,
    obs_utc_iso: str,
    k_ext: float = 0.20,
    dark_sky_zenith_mag_arcsec2: float = 21.6,
) -> dict[str, Any]:
    """Implement the Krisciunas & Schaefer (1991, PASP 103, 1033) Rayleigh + Mie
    lunar sky brightness model in nanoLamberts and V mag/arcsec^2.
    """
    phase_info = compute_lunar_phase_and_illumination(obs_utc_iso)
    alpha_deg = float(phase_info["alpha_deg"])
    illum_frac = float(phase_info["illumination_fraction"])

    x_target = _krisciunas_airmass(target_alt_deg)
    b_zenith_nl = _mag_arcsec2_to_nanolamberts(dark_sky_zenith_mag_arcsec2)
    # Dark-sky brightness at target zenith distance Z (K&S 1991 Eq. 2)
    b_dark_nl = b_zenith_nl * (10.0 ** (-0.4 * k_ext * (x_target - 1.0))) * x_target
    dark_sky_mag_at_target = _nanolamberts_to_mag_arcsec2(b_dark_nl)

    if moon_alt_deg > 0.0:
        x_moon = _krisciunas_airmass(moon_alt_deg)
        rho = max(0.75, min(180.0, float(sep_from_moon_deg)))
        rho_rad = math.radians(rho)

        # Illuminance I* outside atmosphere (K&S 1991 Eq. 20)
        i_star = 10.0 ** (-0.4 * (3.84 + 0.026 * abs(alpha_deg) + 4.0e-9 * (alpha_deg**4)))

        # Rayleigh + Mie scattering function f(rho) (K&S 1991 Eqs. 16-19)
        f_rayleigh = (10.0**5.36) * (1.06 + math.cos(rho_rad) ** 2)
        if rho >= 10.0:
            f_mie = 10.0 ** (6.15 - rho / 40.0)
        else:
            f_mie = 6.2e7 * (rho ** (-2.0))
        f_total = f_rayleigh + f_mie

        # Scattered lunar surface brightness in nanoLamberts (K&S 1991 Eq. 15)
        b_moon_nl = (
            f_total
            * i_star
            * (10.0 ** (-0.4 * k_ext * x_moon))
            * (1.0 - 10.0 ** (-0.4 * k_ext * x_target))
        )
    else:
        b_moon_nl = 0.0

    b_total_nl = b_dark_nl + b_moon_nl
    sky_mag = _nanolamberts_to_mag_arcsec2(b_total_nl)
    lunar_veil_delta_mag = max(0.0, dark_sky_mag_at_target - sky_mag)

    return {
        "skyBrightnessMagArcsec2": round(sky_mag, 3),
        "darkSkyMagArcsec2": round(dark_sky_mag_at_target, 3),
        "lunarVeilDeltaMag": round(lunar_veil_delta_mag, 3),
        "skyBrightnessNanoLamberts": round(b_total_nl, 2),
        "lunarScatteredNanoLamberts": round(b_moon_nl, 2),
        "airmass": round(x_target, 3),
        "illuminationFraction": round(illum_frac, 4),
        "lunarPhaseName": phase_info["phaseName"],
        "sepFromMoonDeg": round(float(sep_from_moon_deg), 2),
    }


def predict_sky_brightness(
    obs_utc_iso: str,
    moon_alt_deg: float,
    target_alt_deg: float,
    sep_from_moon_deg: float,
    target: str,
) -> dict[str, Any]:
    """The Krisciunas & Schaefer (1991) V-band sky brightness at a stated target, labelled `predicted`.

    No sensor or limiting magnitude is modelled: nothing has measured one. The model's site values (V-band
    extinction and dark-sky zenith brightness, `compute_krisciunas_schaefer_sky_brightness`' defaults) are
    assumptions, and the output says so.
    """
    sky = compute_krisciunas_schaefer_sky_brightness(
        sep_from_moon_deg=sep_from_moon_deg,
        target_alt_deg=target_alt_deg,
        moon_alt_deg=moon_alt_deg,
        obs_utc_iso=obs_utc_iso,
    )
    return {
        **sky,
        "target": target,
        "targetAltDeg": round(float(target_alt_deg), 2),
        "moonAltDeg": round(float(moon_alt_deg), 2),
        "evidenceClass": "predicted",
        "method": "Krisciunas and Schaefer (1991, PASP 103, 1033) moonlit sky brightness model",
        "limitations": [
            "Predicted for the time and place; not measured in any image.",
            "Assumes a V-band extinction of 0.20 mag per airmass and a dark-sky zenith of 21.6 mag/arcsec2, "
            "site values the model needs and nothing measured here; light pollution is not modelled.",
        ],
    }
