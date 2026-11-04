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

2. Enriched Anisotropic 3D Gaussian Splats:
   - `apps/web/public/models/lb_splats.json` (14,281 splats, Harvard CfA Local Bubble Chimney)
   - `apps/web/public/models/nasa_nebula_splats.json` (19,297 splats, Multi-Wavelength Nebula)
   Each splat is enriched to a 14-tuple rounded to 4 decimal places:
     [x, y, z, r, g, b, sx, sy, sz, qx, qy, qz, qw, opacity]
   Positions come from the source models; orientation and footprint from the local 3D k-NN
   PCA frame. Colours and opacities are presentation values, not measured emission.
"""

from __future__ import annotations

import glob
import json
import os
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


def rotmat_to_quat_batch(rot: np.ndarray) -> np.ndarray:
    """
    Vectorized conversion of (N, 3, 3) proper rotation matrices (det = +1)
    to normalized (N, 4) quaternions [qx, qy, qz, qw] with qw >= 0.
    """
    n = rot.shape[0]
    q = np.zeros((n, 4), dtype=np.float64)
    tr = rot[:, 0, 0] + rot[:, 1, 1] + rot[:, 2, 2]

    m0 = tr > 0.0
    if np.any(m0):
        s = np.sqrt(tr[m0] + 1.0) * 2.0
        q[m0, 3] = 0.25 * s
        q[m0, 0] = (rot[m0, 2, 1] - rot[m0, 1, 2]) / s
        q[m0, 1] = (rot[m0, 0, 2] - rot[m0, 2, 0]) / s
        q[m0, 2] = (rot[m0, 1, 0] - rot[m0, 0, 1]) / s

    m1 = (~m0) & (rot[:, 0, 0] > rot[:, 1, 1]) & (rot[:, 0, 0] > rot[:, 2, 2])
    if np.any(m1):
        s = np.sqrt(1.0 + rot[m1, 0, 0] - rot[m1, 1, 1] - rot[m1, 2, 2]) * 2.0
        q[m1, 3] = (rot[m1, 2, 1] - rot[m1, 1, 2]) / s
        q[m1, 0] = 0.25 * s
        q[m1, 1] = (rot[m1, 0, 1] + rot[m1, 1, 0]) / s
        q[m1, 2] = (rot[m1, 0, 2] + rot[m1, 2, 0]) / s

    m2 = (~m0) & (~m1) & (rot[:, 1, 1] > rot[:, 2, 2])
    if np.any(m2):
        s = np.sqrt(1.0 + rot[m2, 1, 1] - rot[m2, 0, 0] - rot[m2, 2, 2]) * 2.0
        q[m2, 3] = (rot[m2, 0, 2] - rot[m2, 2, 0]) / s
        q[m2, 0] = (rot[m2, 0, 1] + rot[m2, 1, 0]) / s
        q[m2, 1] = 0.25 * s
        q[m2, 2] = (rot[m2, 1, 2] + rot[m2, 2, 1]) / s

    m3 = (~m0) & (~m1) & (~m2)
    if np.any(m3):
        s = np.sqrt(1.0 + rot[m3, 2, 2] - rot[m3, 0, 0] - rot[m3, 1, 1]) * 2.0
        q[m3, 3] = (rot[m3, 1, 0] - rot[m3, 0, 1]) / s
        q[m3, 0] = (rot[m3, 0, 2] + rot[m3, 2, 0]) / s
        q[m3, 1] = (rot[m3, 1, 2] + rot[m3, 2, 1]) / s
        q[m3, 2] = 0.25 * s

    norms = np.linalg.norm(q, axis=1, keepdims=True)
    q = np.where(norms > 1e-8, q / np.maximum(norms, 1e-8), np.array([0.0, 0.0, 0.0, 1.0]))
    # Canonical hemisphere qw >= 0
    neg_w = q[:, 3] < 0.0
    q[neg_w] = -q[neg_w]
    return q


def compute_local_pca_frame(xyz: np.ndarray, k: int = 16) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Computes 3D k-NN local neighborhood spacing, eigenvalues (N, 3), and eigenvectors (N, 3, 3)
    for point cloud xyz (N, 3).
    """
    n = xyz.shape[0]
    if cKDTree is not None:
        tree = cKDTree(xyz)
        dists, idx = tree.query(xyz, k=min(k, n))
        mean_spacing = np.mean(dists[:, 1:], axis=1)
        neighbors = xyz[idx]  # (N, k, 3)
    else:
        # Fast contiguous window fallback if scipy is unavailable
        half = k // 2
        indices = (np.arange(n)[:, None] + np.arange(-half, half)[None, :]) % n
        neighbors = xyz[indices]
        diffs = neighbors - xyz[:, None, :]
        mean_spacing = np.mean(np.linalg.norm(diffs[:, 1:], axis=2), axis=1)

    centered = neighbors - np.mean(neighbors, axis=1, keepdims=True)
    cov = np.einsum("nki,nkj->nij", centered, centered) / max(1, k - 1)
    cov += np.eye(3)[None, :, :] * 1e-6
    evals, evecs = np.linalg.eigh(cov)  # evals ascending: lambda_0 <= lambda_1 <= lambda_2
    return mean_spacing, evals, evecs


# The Local Bubble model gives the shape of the bubble's surface but no dust density or emission
# point by point, so every splat gets one presentation colour (the average of the palette this
# replaced) and an opacity Spark's 8-bit encoding stores as its smallest step, 1/255.
LOCAL_BUBBLE_RGB = (0.87, 0.61, 0.43)
LOCAL_BUBBLE_OPACITY = 0.0039


def enrich_local_bubble_splats(lb_path: Path) -> int:
    """
    Rebuilds the attributes of `lb_splats.json` (14,281 splats) from its positions alone, as
    14-tuples: [x, y, z, r, g, b, sx, sy, sz, qx, qy, qz, qw, opacity].

    The positions are every other vertex of `local_bubble_oneill2024.glb`, a Local Bubble mesh of unrecorded origin, named for O'Neill et al. (2024),
    y up, at about 2.2x. Each splat lies in the surface, oriented
    by the local k-NN PCA frame and sized by the local sample spacing so the splats tile it;
    colour and opacity are the uniform presentation values above.
    """
    raw = json.loads(lb_path.read_text(encoding="utf-8"))
    xyz = np.array(raw, dtype=np.float64)[:, :3]
    n = xyz.shape[0]

    mean_spacing, evals, evecs = compute_local_pca_frame(xyz, k=16)

    # Sample density normalized to [0, 1]; denser samples get smaller footprints
    inv_spacing = 1.0 / np.maximum(0.02, mean_spacing)
    p5, p95 = np.percentile(inv_spacing, [5.0, 95.0])
    density_norm = np.clip((inv_spacing - p5) / max(1e-6, p95 - p5), 0.0, 1.0)

    x, y, z = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    r_3d = np.maximum(1e-4, np.linalg.norm(xyz, axis=1))
    radial_unit = xyz / r_3d[:, None]

    # Frame per splat: u_z is the surface normal (the minor PCA axis turned outward, blended toward
    # radial to steady noisy normals), u_x the major PCA axis projected into the surface
    t_pca = evecs[:, :, 2]
    n_pca = evecs[:, :, 0]
    dot_nr = np.sum(n_pca * radial_unit, axis=1, keepdims=True)
    n_out = np.where(dot_nr >= 0.0, n_pca, -n_pca)
    u_z = 0.65 * n_out + 0.35 * radial_unit
    u_z /= np.maximum(1e-8, np.linalg.norm(u_z, axis=1, keepdims=True))

    u_x = t_pca - np.sum(t_pca * u_z, axis=1, keepdims=True) * u_z
    ux_norm = np.linalg.norm(u_x, axis=1, keepdims=True)
    # Fallback tangent if the major axis was parallel to u_z
    swirl = np.stack([-y, x, np.zeros_like(z)], axis=1)
    swirl /= np.maximum(1e-8, np.linalg.norm(swirl, axis=1, keepdims=True))
    u_x = np.where(ux_norm > 1e-5, u_x / np.maximum(1e-8, ux_norm), swirl)
    u_y = np.cross(u_z, u_x)
    u_y /= np.maximum(1e-8, np.linalg.norm(u_y, axis=1, keepdims=True))

    rot = np.stack([u_x, u_y, u_z], axis=2)  # Columns are basis vectors (N, 3, 3)
    quats = rotmat_to_quat_batch(rot)

    # Footprints: elongated along the major axis by the local anisotropy, thin across the surface
    aniso = np.clip(np.sqrt(evals[:, 2] / np.maximum(1e-5, evals[:, 0])), 1.25, 3.20)
    base_scale = 0.095 + 0.045 * (1.0 - 0.55 * density_norm)
    sx = np.clip(base_scale * (1.35 + 0.28 * (aniso - 1.25)), 0.105, 0.245)
    sy = np.clip(base_scale, 0.075, 0.165)
    sz = np.clip(base_scale * (0.48 + 0.12 * (1.0 - density_norm)), 0.042, 0.092)

    rgb = np.tile(np.array(LOCAL_BUBBLE_RGB), (n, 1))
    opacity = np.full(n, LOCAL_BUBBLE_OPACITY)

    enriched = np.column_stack([xyz, rgb, sx, sy, sz, quats, opacity])
    rounded = np.round(enriched, 4).tolist()
    lb_path.write_text(json.dumps(rounded, separators=(",", ":")), encoding="utf-8")
    return n


def enrich_nebula_splats(neb_path: Path) -> int:
    """
    Enriches `nasa_nebula_splats.json` (19,297 splats, positions matching the vertices of
    `nasa_pillars_3d.glb`) to 14-tuples: [x, y, z, r, g, b, sx, sy, sz, qx, qy, qz, qw, opacity].
    The colours and opacities are a presentation palette laid out by radius and density; the
    emission lines named below are what the palette imitates, not measurements.
    """
    raw = json.loads(neb_path.read_text(encoding="utf-8"))
    arr = np.array(raw, dtype=np.float64)
    xyz = arr[:, :3]
    n = xyz.shape[0]

    mean_spacing, evals, evecs = compute_local_pca_frame(xyz, k=16)

    inv_spacing = 1.0 / np.maximum(0.015, mean_spacing)
    p5, p95 = np.percentile(inv_spacing, [5.0, 95.0])
    density_norm = np.clip((inv_spacing - p5) / max(1e-6, p95 - p5), 0.0, 1.0)

    x, y, z = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    r_3d = np.maximum(1e-4, np.linalg.norm(xyz, axis=1))
    radial_unit = xyz / r_3d[:, None]

    # Align splats with elongated ionization pillars (swept back radially in dense knots)
    # and tangential shock fronts along the expanding Strömgren shell
    t_pca = evecs[:, :, 2]
    dot_tr = np.sum(t_pca * radial_unit, axis=1, keepdims=True)
    t_oriented = np.where(dot_tr >= 0.0, t_pca, -t_pca)

    # Dense globule pillars elongate along the swept-back radial wind direction;
    # outer shell wisps follow the local PCA filament tangent
    pillar_mix = (0.22 + 0.38 * density_norm)[:, None]
    u_x = (1.0 - pillar_mix) * t_oriented + pillar_mix * radial_unit
    u_x /= np.maximum(1e-8, np.linalg.norm(u_x, axis=1, keepdims=True))

    n_pca = evecs[:, :, 0]
    dot_nr = np.sum(n_pca * radial_unit, axis=1, keepdims=True)
    n_out = np.where(dot_nr >= 0.0, n_pca, -n_pca)
    u_z = n_out - np.sum(n_out * u_x, axis=1, keepdims=True) * u_x
    uz_norm = np.linalg.norm(u_z, axis=1, keepdims=True)
    fallback_z = np.cross(u_x, np.stack([-y, x, np.zeros_like(z)], axis=1))
    fallback_z /= np.maximum(1e-8, np.linalg.norm(fallback_z, axis=1, keepdims=True))
    u_z = np.where(uz_norm > 1e-5, u_z / np.maximum(1e-8, uz_norm), fallback_z)
    u_y = np.cross(u_z, u_x)
    u_y /= np.maximum(1e-8, np.linalg.norm(u_y, axis=1, keepdims=True))

    rot = np.stack([u_x, u_y, u_z], axis=2)
    quats = rotmat_to_quat_batch(rot)

    # Multi-scale anisotropic covariance scales (sx, sy, sz)
    aniso = np.clip(np.sqrt(evals[:, 2] / np.maximum(1e-5, evals[:, 0])), 1.30, 3.60)
    idx_mod = (np.arange(n, dtype=np.float64) % 9.0) / 8.0
    # Dense ionization cores & bright rims are tight and crisp; tenuous outer envelope is broader
    radial_norm = np.clip((r_3d - 2.6) / 3.8, 0.0, 1.0)
    base_s = 0.017 + 0.009 * (1.0 - 0.65 * density_norm) + 0.006 * radial_norm + 0.005 * idx_mod
    sx = np.clip(base_s * (1.45 + 0.38 * (aniso - 1.30)), 0.022, 0.068)
    sy = np.clip(base_s * (0.95 + 0.18 * idx_mod), 0.014, 0.038)
    sz = np.clip(base_s * (0.62 + 0.15 * (1.0 - density_norm)), 0.010, 0.026)

    # Presentation palette by radius, imitating photoionization zones and dark dust lanes:
    # Inner zone (r_3d ~ 2.6..3.8): [O III] 500.7nm doubly-ionized teal-cyan & H-beta core
    # Mid zone (r_3d ~ 3.8..4.9): Photo-dissociation shock front (golden-amber PAH + coral H-alpha rim)
    # Outer zone (r_3d ~ 4.9..6.5): H-alpha 656.3nm + [N II] crimson-magenta filaments & dark dust lanes
    oiii_weight = np.exp(-((r_3d - 3.0) / 1.15) ** 2)
    pdr_rim_weight = np.exp(-((r_3d - 4.15) / 0.85) ** 2)
    halpha_weight = 1.0 / (1.0 + np.exp(-(r_3d - 4.35) * 2.2))

    # Azimuthal / 3D turbulence modulation for rich spectral filament contrast
    turb = 0.5 + 0.5 * np.sin(x * 0.92 - y * 1.14 + z * 1.28)
    z_band = np.clip(0.5 - 0.12 * z + 0.22 * turb, 0.15, 0.95)

    col_oiii = np.stack(
        [
            0.36 + 0.22 * turb,
            0.84 + 0.12 * (1.0 - turb),
            0.78 + 0.18 * z_band,
        ],
        axis=1,
    )
    col_pdr = np.stack(
        [
            0.94 + 0.05 * turb,
            0.66 + 0.16 * (1.0 - turb),
            0.34 + 0.22 * z_band,
        ],
        axis=1,
    )
    col_halpha = np.stack(
        [
            0.92 + 0.07 * turb,
            0.32 + 0.18 * (1.0 - halpha_weight),
            0.52 + 0.28 * z_band,
        ],
        axis=1,
    )

    w_sum = np.maximum(1e-5, oiii_weight + pdr_rim_weight + halpha_weight)[:, None]
    emission_rgb = (
        col_oiii * oiii_weight[:, None]
        + col_pdr * pdr_rim_weight[:, None]
        + col_halpha * halpha_weight[:, None]
    ) / w_sum

    # Self-shielded dark dust lanes in dense outer/back-side Bok globule knots
    shadow_knot = np.clip((density_norm - 0.52) * 2.1, 0.0, 1.0) * np.clip((r_3d - 3.9) / 2.2, 0.0, 1.0)
    # Bright photo-ionized rim highlight on the inner side of dense knots
    bright_rim = np.clip((density_norm - 0.45) * 1.8, 0.0, 1.0) * pdr_rim_weight

    tau_ext = shadow_knot * 1.15
    ext_factors = np.stack(
        [
            np.exp(-tau_ext * 0.58),
            np.exp(-tau_ext * 0.92),
            np.exp(-tau_ext * 1.18),
        ],
        axis=1,
    )
    rgb = emission_rgb * ext_factors + np.array([0.14, 0.10, 0.06])[None, :] * bright_rim[:, None]
    rgb = np.clip(rgb, 0.06, 0.995)

    # Volumetric opacity: dense ionization cores & dark dust pillars have deeper optical depth;
    # tenuous outer filaments fade softly into the background starfield
    opacity = (
        (0.024 + 0.026 * density_norm + 0.012 * bright_rim)
        * (1.0 - 0.48 * radial_norm)
    )
    opacity = np.clip(opacity, 0.0120, 0.0580)

    enriched = np.column_stack([xyz, rgb, sx, sy, sz, quats, opacity])
    rounded = np.round(enriched, 4).tolist()
    neb_path.write_text(json.dumps(rounded, separators=(",", ":")), encoding="utf-8")
    return n


def main() -> None:
    lut_path = REPO_ROOT / "apps" / "web" / "public" / "textures" / "bruneton_atmo_lut.png"
    lb_path = REPO_ROOT / "apps" / "web" / "public" / "models" / "lb_splats.json"
    neb_path = REPO_ROOT / "apps" / "web" / "public" / "models" / "nasa_nebula_splats.json"

    bake_bruneton_atmo_lut(lut_path, width=256, height=128)
    lb_count = enrich_local_bubble_splats(lb_path)
    neb_count = enrich_nebula_splats(neb_path)

    print(
        f"[bake_bruneton_lut_and_splats] Wrote {lut_path.relative_to(REPO_ROOT)} "
        f"(256x128 RGBA PNG, {lut_path.stat().st_size:,} bytes), "
        f"enriched {lb_path.relative_to(REPO_ROOT)} ({lb_count:,}x14 splats), "
        f"and {neb_path.relative_to(REPO_ROOT)} ({neb_count:,}x14 splats)."
    )


if __name__ == "__main__":
    main()
