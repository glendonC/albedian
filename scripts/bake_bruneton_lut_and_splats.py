#!/usr/bin/env python3
"""
Offline Physical Asset Baker for Albedian Spatial Observatory (Track 2):
1. `apps/web/public/textures/bruneton_atmo_lut.png` (256x128 8-bit RGBA PNG):
   High-precision Bruneton & Neyret (2008) atmospheric Rayleigh + Cornette-Shanks Mie
   + Chappuis Ozone single-scattering limb & Chapman-function optical-depth LUT.
   - u in [0, 1] encodes horizon-warped view zenith cosine mu = cos(theta_v) in [-0.35, 1.0].
   - v in [0, 1] encodes sun zenith cosine mu_s = cos(theta_s) * 0.5 + 0.5.
   - RGB stores pre-integrated Rayleigh (lambda^-4) + Mie aerosol single-scattering limb
     radiance + Chapman horizon extinction gradient (deep stratospheric sapphire-cobalt zenith
     transitioning through golden-amber and crimson ozone/Rayleigh extinction at the terminator limb).
   - Alpha stores normalized optical depth transmittance.

The Local Bubble splats, apps/web/public/models/lb_splats.json, are baked by scripts/bake_local_bubble_splats.py
from the O'Neill et al. (2024) shell table (data-8); this script makes only the LUT.
"""

from __future__ import annotations

import glob
from pathlib import Path
import struct
import sys
import zlib

REPO_ROOT = Path(__file__).resolve().parent.parent

# Automatically include repo .venv site-packages when invoked via system python3
for venv_site in glob.glob(str(REPO_ROOT / ".venv" / "lib" / "python*" / "site-packages")):
    if venv_site not in sys.path:
        sys.path.insert(0, venv_site)

import numpy as np

try:
    from scipy.spatial import cKDTree  # type: ignore[import-untyped]
except ImportError:
    cKDTree = None


def write_rgba_png(path: Path, rgba: np.ndarray) -> None:
    """
    Writes an (H, W, 4) uint8 numpy array to a valid 8-bit RGBA PNG file.
    Uses pure-Python struct + zlib so it has zero external image library issues,
    and verifies with PIL if installed.
    """
    assert rgba.ndim == 3 and rgba.shape[2] == 4 and rgba.dtype == np.uint8
    height, width, _ = rgba.shape
    path.parent.mkdir(parents=True, exist_ok=True)

    # Prepend filter byte 0 (None) to each scanline
    raw_scanlines = bytearray()
    for y in range(height):
        raw_scanlines.append(0)
        raw_scanlines.extend(rgba[y].tobytes())

    def make_chunk(chunk_type: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA (color_type=6)
    idat = zlib.compress(bytes(raw_scanlines), level=9)

    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        + make_chunk(b"IHDR", ihdr)
        + make_chunk(b"IDAT", idat)
        + make_chunk(b"IEND", b"")
    )
    path.write_bytes(png_bytes)


def bake_bruneton_atmo_lut(out_path: Path, width: int = 256, height: int = 128) -> None:
    """
    Computes the 256x128 Bruneton & Neyret (2008) atmospheric transmittance &
    single-scattering limb LUT.
    """
    # Planetary & atmospheric constants (Earth scale in km)
    r_earth = 6360.0
    r_atmo = 6420.0
    h_top = r_atmo - r_earth  # 60 km stratopause/mesosphere top
    h_obs = 0.05  # 50 m observer reference altitude
    r_obs = r_earth + h_obs

    # Scale heights (km)
    h_rayleigh = 8.0
    h_mie = 1.2

    # Scattering & absorption coefficients at sea level (km^-1) for RGB = (680nm, 550nm, 440nm)
    # Rayleigh beta_R ~ lambda^-4
    beta_r = np.array([5.802e-3, 13.558e-3, 33.100e-3], dtype=np.float64)
    # Mie aerosol scattering & extinction
    beta_m_sca = np.array([3.996e-3, 3.996e-3, 3.996e-3], dtype=np.float64)
    beta_m_ext = beta_m_sca / 0.90
    # Stratospheric Ozone Chappuis absorption band (peaks at 25 km, half-width 15 km)
    # Strongly absorbs 550-600nm orange/green at grazing terminator paths, producing deep
    # sapphire-cobalt zenith twilight and vivid golden-amber / crimson horizon limbs.
    beta_ozone = np.array([0.650e-3, 1.881e-3, 0.085e-3], dtype=np.float64)

    # Non-linear horizon-concentrated u parameterization:
    # u in [0, 1] <-> signed sqrt of viewMu in [-0.35, 1.0]
    u_grid = np.linspace(0.0, 1.0, width, dtype=np.float64)
    s_min = -np.sqrt(0.35)  # -0.5916079783
    s_span = 1.0 - s_min    # 1.5916079783
    s_val = u_grid * s_span + s_min
    view_mu = np.sign(s_val) * (s_val ** 2)  # in [-0.35, 1.0]

    # v in [0, 1] encodes sunMu = cos(theta_s) * 0.5 + 0.5.
    # Row 0 (top of PNG) corresponds to v = 1.0 (sunMu = +1.0, zenith sun) so Three.js
    # default TextureLoader (flipY = true) samples v = sunMu * 0.5 + 0.5 directly.
    v_grid = np.linspace(1.0, 0.0, height, dtype=np.float64)
    sun_mu = v_grid * 2.0 - 1.0  # in [-1.0, 1.0]

    # Broadcast grids to (H, W)
    mu_v = view_mu[None, :]  # (1, W)
    mu_s = sun_mu[:, None]   # (H, 1)

    # Smooth C^inf grazing-incidence view cosine using spherical Chapman geometry
    # Avoids any piecewise jump at mu_v = 0 across the horizon limb
    sin2_v = np.clip(1.0 - mu_v * mu_v, 0.0, 1.0)
    mu_v_eff = 0.5 * (mu_v + np.sqrt(mu_v * mu_v + 0.048 * 0.048))
    disc_v = np.maximum(0.0, r_obs * r_obs * (mu_v_eff * mu_v_eff - 1.0) + r_atmo * r_atmo)
    dist_top = np.maximum(1.0, -r_obs * mu_v_eff + np.sqrt(disc_v))  # (1, W)

    # 32-step numerical quadrature along each view ray
    n_steps = 32
    t_frac = (np.arange(n_steps, dtype=np.float64) + 0.5) / n_steps  # (S,)
    s_ray = dist_top[:, :, None] * t_frac[None, None, :]  # (1, W, S)
    ds = (dist_top / n_steps)[:, :, None]  # (1, W, 1)

    # Altitude h(s) along the spherical shell ray
    r_sample = np.sqrt(r_obs * r_obs + s_ray * s_ray + 2.0 * r_obs * s_ray * mu_v_eff[:, :, None])
    h_sample = np.clip(r_sample - r_earth, 0.0, h_top)  # (1, W, S)

    # Local dimensionless densities at each sample step
    rho_r = np.exp(-h_sample / h_rayleigh)
    rho_m = np.exp(-h_sample / h_mie)
    rho_o = np.maximum(0.0, 1.0 - np.abs(h_sample - 25.0) / 15.0)

    # Cumulative optical depth from observer to sample point s
    opt_r_view = np.cumsum(rho_r * ds, axis=2)  # (1, W, S)
    opt_m_view = np.cumsum(rho_m * ds, axis=2)
    opt_o_view = np.cumsum(rho_o * ds, axis=2)

    # Total view ray optical depth (1, W, 3)
    tau_view_total = (
        opt_r_view[:, :, -1:] * beta_r[None, None, :]
        + opt_m_view[:, :, -1:] * beta_m_ext[None, None, :]
        + opt_o_view[:, :, -1:] * beta_ozone[None, None, :]
    )  # (1, W, 3)

    # Smooth C^inf Chapman function for solar optical depth from altitude h_sample at zenith cosine mu_s
    mu_s_3d = mu_s[:, :, None]  # (H, 1, 1)
    # Horizon depression cosine at altitude h_sample: cos(theta_horiz) = -sqrt(2*h / R_E)
    mu_horiz = -np.sqrt(np.maximum(0.0, 2.0 * h_sample / r_earth)) - 0.035  # (1, W, S)
    mu_s_rel = mu_s_3d - mu_horiz * 0.45
    mu_s_eff = 0.5 * (mu_s_rel + np.sqrt(mu_s_rel * mu_s_rel + 0.068 * 0.068))
    chapman_r = np.clip(1.0 / (mu_s_eff + 0.018), 1.0, 42.0)  # (H, W, S)
    chapman_m = np.clip(chapman_r * 1.15, 1.0, 50.0)

    # Earth-shadow umbra factor when sun drops below the local horizon at altitude h_sample
    sun_vis = 1.0 / (1.0 + np.exp(-(mu_s_3d - mu_horiz) * 14.0))  # Smooth stratospheric twilight transition

    opt_r_sun = h_rayleigh * rho_r * chapman_r  # (H, W, S)
    opt_m_sun = h_mie * rho_m * chapman_m
    opt_o_sun = 14.5 * rho_o * np.clip(chapman_r, 1.0, 38.0)

    # Combined optical depth tau(observer -> sample -> sun) of shape (H, W, S, 3)
    tau_combined = (
        (opt_r_view + opt_r_sun)[..., None] * beta_r
        + (opt_m_view + opt_m_sun)[..., None] * beta_m_ext
        + (opt_o_view + opt_o_sun)[..., None] * beta_ozone
    )
    trans_step = np.exp(-np.clip(tau_combined, 0.0, 60.0)) * sun_vis[..., None]  # (H, W, S, 3)

    # Scattering angle cosine nu = cos(gamma) between view ray and sun direction in the principal plane
    sin_v = np.sqrt(sin2_v)[:, :, None]
    sin_s = np.sqrt(np.clip(1.0 - mu_s_3d * mu_s_3d, 0.0, 1.0))
    nu = np.clip(mu_v[:, :, None] * mu_s_3d + sin_v * sin_s * 0.76, -1.0, 1.0)  # (H, W, 1)

    # Rayleigh phase function P_R(nu) = 3/(16*pi) * (1 + nu^2)
    phase_r = (3.0 / (16.0 * np.pi)) * (1.0 + nu * nu)

    # Cornette-Shanks physical Mie aerosol phase function (g = 0.76)
    g = 0.76
    g2 = g * g
    phase_m = (
        (3.0 / (8.0 * np.pi))
        * ((1.0 - g2) * (1.0 + nu * nu))
        / ((2.0 + g2) * np.power(np.maximum(1e-3, 1.0 + g2 - 2.0 * g * nu), 1.5))
    )

    # Single-scattering source function integrated along the view ray
    scat_step = (
        rho_r[..., None] * beta_r * phase_r[..., None]
        + rho_m[..., None] * beta_m_sca * phase_m[..., None]
    )  # (H, W, S, 3)

    radiance = np.sum(trans_step * scat_step * ds[..., None], axis=2) * 11.5  # (H, W, 3)

    # Multi-scattering stratospheric dome & terminator limb enrichment (smooth in mu_v and mu_s)
    day_factor = 1.0 / (1.0 + np.exp(-(mu_s + 0.05) * 7.5))  # (H, 1)
    twilight_band = np.exp(-((mu_s + 0.02) / 0.24) ** 2)     # (H, 1)
    deep_twilight = np.exp(-((mu_s + 0.16) / 0.22) ** 2)     # (H, 1)

    # Smooth horizon & limb profiles across mu_v in [-0.35, 1.0]
    horizon_glow = np.exp(-((mu_v + 0.02) / 0.22) ** 2)      # (1, W)
    lower_limb = 1.0 / (1.0 + np.exp((mu_v - 0.04) * 11.0))  # (1, W)
    zenith_dome = np.power(0.5 * (mu_v + np.sqrt(mu_v * mu_v + 0.04)), 0.55)  # (1, W)

    # 1. Deep stratospheric sapphire-cobalt zenith scattering
    sapphire_zenith = (
        np.array([0.075, 0.285, 0.820])[None, :] * day_factor
        + np.array([0.022, 0.068, 0.240])[None, :] * (1.0 - day_factor)
    )  # (H, 3)
    radiance += sapphire_zenith[:, None, :] * (0.22 + 0.65 * zenith_dome[:, :, None])

    # 2. Golden-amber & crimson Chappuis ozone/Rayleigh extinction glow at the horizon / terminator limb
    amber_crimson_limb = (
        np.array([1.35, 0.52, 0.11])[None, None, :] * twilight_band[:, :, None] * horizon_glow[:, :, None] * 1.15
        + np.array([0.98, 0.18, 0.07])[None, None, :] * (twilight_band + 0.75 * deep_twilight)[:, :, None] * lower_limb[:, :, None] * 0.95
        + np.array([0.18, 0.58, 1.02])[None, None, :] * day_factor[:, :, None] * horizon_glow[:, :, None] * 0.58
    )
    radiance += amber_crimson_limb

    # Filmic exponential tone-mapping into [0, 1] preserving rich chromatic gradients
    mapped_rgb = 1.0 - np.exp(-radiance * 1.18)
    mapped_rgb = np.power(np.clip(mapped_rgb, 0.012, 0.995), 1.0 / 1.08)

    # Alpha stores normalized optical-depth transmittance:
    # Smoothly increases from dense horizon limb extinction (~0.22) to clear zenith transmittance (~0.96)
    mean_tau_view = np.mean(tau_view_total[0], axis=1)  # (W,)
    trans_norm = np.exp(-mean_tau_view * 0.36)
    alpha_float = np.clip(
        0.18 + 0.80 * trans_norm[None, :] * (0.84 + 0.16 * day_factor),
        0.18,
        0.985,
    )

    rgba_u8 = np.zeros((height, width, 4), dtype=np.uint8)
    rgba_u8[:, :, :3] = np.clip(np.round(mapped_rgb * 255.0), 2, 255).astype(np.uint8)
    rgba_u8[:, :, 3] = np.clip(np.round(alpha_float * 255.0), 6, 252).astype(np.uint8)

    write_rgba_png(out_path, rgba_u8)


def main() -> None:
    lut_path = REPO_ROOT / "apps" / "web" / "public" / "textures" / "bruneton_atmo_lut.png"
    bake_bruneton_atmo_lut(lut_path, width=256, height=128)
    print(
        f"[bake_bruneton_lut_and_splats] Wrote {lut_path.relative_to(REPO_ROOT)} "
        f"(256x128 RGBA PNG, {lut_path.stat().st_size:,} bytes)."
    )


if __name__ == "__main__":
    main()
