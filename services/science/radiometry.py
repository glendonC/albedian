"""Radiometric night-sky brightness, lunar phase/scattering model (Krisciunas & Schaefer 1991),
and wearable-sensor single-frame vs. coherent multi-frame limiting magnitude audit.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any


def _iso_to_jd(obs_utc_iso: str) -> float:
    """Convert an ISO-8601 UTC timestamp string to Julian Date."""
    cleaned = obs_utc_iso.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.timestamp() / 86400.0 + 2440587.5


def compute_lunar_phase_and_illumination(obs_utc_iso: str) -> dict[str, Any]:
    """Compute Sun-Moon elongation, selenocentric phase angle `alpha_deg`, illuminated
    fraction (`0..1`), visual lunar magnitude, and human-readable lunar phase name.
    """
    jd = _iso_to_jd(obs_utc_iso)
    d = jd - 2451545.0
    t = d / 36525.0

    # Mean solar longitude & anomaly (Meeus Ch. 25 / 47)
    sun_mean_lon = (280.46646 + 36000.76983 * t) % 360.0
    sun_mean_anom = math.radians((357.52911 + 35999.05029 * t) % 360.0)
    sun_ecl_lon = (
        sun_mean_lon
        + 1.914602 * math.sin(sun_mean_anom)
        + 0.019993 * math.sin(2.0 * sun_mean_anom)
    ) % 360.0

    # Mean lunar longitude, elongation, and anomaly (Meeus Ch. 47)
    moon_mean_lon = (218.3165 + 481267.8813 * t) % 360.0
    moon_mean_anom = math.radians((134.9634 + 477198.8675 * t) % 360.0)
    mean_elong = math.radians((297.8502 + 445267.1115 * t) % 360.0)
    moon_ecl_lon = (
        moon_mean_lon
        + 6.2888 * math.sin(moon_mean_anom)
        + 1.2740 * math.sin(2.0 * mean_elong - moon_mean_anom)
        + 0.6583 * math.sin(2.0 * mean_elong)
        + 0.2136 * math.sin(2.0 * moon_mean_anom)
    ) % 360.0

    # Signed synodic cycle angle [0, 360): 0=New, 90=First Quarter, 180=Full, 270=Last Quarter
    synodic_angle_deg = (moon_ecl_lon - sun_ecl_lon) % 360.0
    elongation_deg = 180.0 - abs(180.0 - synodic_angle_deg)
    alpha_deg = max(0.0, min(180.0, 180.0 - elongation_deg))
    illumination_fraction = 0.5 * (1.0 + math.cos(math.radians(alpha_deg)))

    # Krisciunas & Schaefer (1991) visual lunar magnitude formula
    lunar_visual_mag = -12.73 + 0.026 * abs(alpha_deg) + 4.0e-9 * (alpha_deg**4)

    if synodic_angle_deg < 22.5 or synodic_angle_deg >= 337.5:
        phase_name = "New Moon"
    elif synodic_angle_deg < 67.5:
        phase_name = "Waxing Crescent"
    elif synodic_angle_deg < 112.5:
        phase_name = "First Quarter"
    elif synodic_angle_deg < 157.5:
        phase_name = "Waxing Gibbous"
    elif synodic_angle_deg < 202.5:
        phase_name = "Full Moon"
    elif synodic_angle_deg < 247.5:
        phase_name = "Waning Gibbous"
    elif synodic_angle_deg < 292.5:
        phase_name = "Last Quarter"
    else:
        phase_name = "Waning Crescent"

    return {
        "observedAtUTC": obs_utc_iso,
        "synodicAngleDeg": round(synodic_angle_deg, 2),
        "elongation_deg": round(elongation_deg, 2),
        "elongationDeg": round(elongation_deg, 2),
        "alpha_deg": round(alpha_deg, 2),
        "alphaDeg": round(alpha_deg, 2),
        "illumination_fraction": round(illumination_fraction, 4),
        "illuminationFraction": round(illumination_fraction, 4),
        "lunar_visual_mag": round(lunar_visual_mag, 2),
        "lunarVisualMag": round(lunar_visual_mag, 2),
        "phase_name": phase_name,
        "phaseName": phase_name,
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


def compute_sensor_limiting_magnitude_audit(
    obs_utc_iso: str,
    moon_alt_deg: float,
    target_sep_deg: float = 8.0,
    target_alt_deg: float = 30.0,
    aperture_mm: float = 2.2,
    exposure_sec: float = 1.0 / 30.0,
    stack_frames: int = 9,
) -> dict[str, Any]:
    """Compute wearable-camera single-frame vs. coherent multi-frame limiting magnitude
    under Krisciunas & Schaefer (1991) sky brightness and 8-bit H.265 quantization.
    """
    ks = compute_krisciunas_schaefer_sky_brightness(
        sep_from_moon_deg=target_sep_deg,
        target_alt_deg=target_alt_deg,
        moon_alt_deg=moon_alt_deg,
        obs_utc_iso=obs_utc_iso,
    )
    sky_mag = float(ks["skyBrightnessMagArcsec2"])
    veil_loss_mag = float(ks["lunarVeilDeltaMag"])

    # Effective 5-sigma point-source limiting magnitude for a sub-3mm wearable aperture:
    # Scales with 2.5*log10(D_mm) + 1.25*log10(t_sec) + 0.5*(sky_mag - 21.6) - H.265 quantization penalty
    aperture_term = 2.5 * math.log10(max(0.5, float(aperture_mm)) / 2.2)
    exposure_term = 1.25 * math.log10(max(1e-4, float(exposure_sec)) / (1.0 / 30.0))
    sky_penalty = 0.48 * (21.6 - sky_mag)
    single_frame_lim_mag = 2.85 + aperture_term + exposure_term - sky_penalty

    n_stack = max(1, int(stack_frames))
    stack_gain_mag = 1.25 * math.log10(float(n_stack))
    coherent_stack_lim_mag = single_frame_lim_mag + stack_gain_mag

    # Solve for N frames needed to reach m_lim >= 3.0 mag (e.g. Deneb Algedi at mag 2.85)
    target_mag = 3.0
    if single_frame_lim_mag >= target_mag:
        frames_needed_mag3 = 1
    else:
        mag_deficit = target_mag - single_frame_lim_mag
        frames_needed_mag3 = min(600, max(2, int(math.ceil(10.0 ** (mag_deficit / 1.25)))))

    explanation = (
        f"At {target_sep_deg:.1f}° from the {ks['lunarPhaseName']} "
        f"({ks['illuminationFraction'] * 100.0:.0f}% illuminated, Alt {moon_alt_deg:+.1f}°), "
        f"Rayleigh/Mie scattering brightens the sky by {veil_loss_mag:.2f} mag/arcsec² "
        f"to {sky_mag:.2f} mag/arcsec². With a {aperture_mm:.1f} mm wearable aperture at "
        f"{exposure_sec * 1000.0:.1f} ms exposure, the single-frame 5σ limiting magnitude is "
        f"m_lim(1) = {single_frame_lim_mag:+.2f} mag; a {n_stack}-frame WCS-coherent stack "
        f"reaches m_lim({n_stack}) = {coherent_stack_lim_mag:+.2f} mag "
        f"(~{frames_needed_mag3} coherent frames required to recover mag 3.0 stars)."
    )

    return {
        "singleFrameLimitingMag": round(single_frame_lim_mag, 2),
        "coherentStackLimitingMag": round(coherent_stack_lim_mag, 2),
        "stackGainMag": round(stack_gain_mag, 2),
        "framesNeededForMag3Star": int(frames_needed_mag3),
        "skyBrightnessMagArcsec2": round(sky_mag, 2),
        "lunarVeilDeltaMag": round(veil_loss_mag, 2),
        "illuminationFraction": ks["illuminationFraction"],
        "lunarPhaseName": ks["lunarPhaseName"],
        "apertureMm": float(aperture_mm),
        "exposureSec": float(exposure_sec),
        "stackFrames": int(n_stack),
        "explanation": explanation,
    }
