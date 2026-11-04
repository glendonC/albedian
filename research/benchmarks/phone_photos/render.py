"""Render one simulated frame: scene light through optics, sensor and image processing to a JPEG.

Each frame is a pure function of its `FrameSpec`: every random draw comes from the spec's seed,
so the same spec always gives the same JPEG bytes on the same software versions.

The chain [inference: a generic phone pipeline; makers publish none of their processing]:

1. Light per pixel in V-band flux: the sky's surface brightness times each pixel's solid angle,
   catalog stars, the Moon (lit by the Sun at its true phase), lamps, aircraft, screens.
2. Optics: a two-part Gaussian point-spread function (a core whose width grows toward the corners,
   and a faint wide halo), glare and a mirrored ghost around very bright sources, vignetting
   cos(theta)^n, atmospheric extinction and cloud.
3. Sensor: electrons through the pupil at a quantum efficiency, per Bayer channel by each source's
   colour temperature; Earth-rotation trails over the exposure; shot noise, read noise, dark
   current, hot pixels, full well, 12-bit quantization. The sensor works at the output pixel size:
   an output pixel gathers `binning` x `binning` native pixels.
4. Processing: bilinear demosaic, white balance, lens-shading correction (which lifts corner noise),
   a night-mode brightening, black clip, tone curve (sRGB, or HLG for the head-worn video),
   chroma and luma smoothing, sharpening, 8-bit JPEG with 4:2:0 chroma subsampling (Pillow).
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from PIL import Image
from scipy import ndimage, special

from research.benchmarks.phone_photos import scene as sc
from research.benchmarks.phone_photos.astrometry import ObservationFrame, julian_year
from research.benchmarks.phone_photos.geometry import (
    BrownConrady,
    Camera,
    CameraTruth,
    KannalaBrandt,
    enu_from_altaz,
    intrinsics_for_diagonal_fov,
)
from research.benchmarks.phone_photos.stars import TileCatalog
from research.benchmarks.phone_photos.suite import FrameSpec

# Photons from a V = 0 star per second per cm^2 across a phone's visible band [inference: about
# 1000 photons s^-1 cm^-2 A^-1 at 550 nm over an effective 1000 A band]
PHOTONS_V0_PER_S_CM2 = 1.0e6
SIDEREAL_RATE_RAD_S = 7.2921150e-5
WHITE_K = 6500.0
GLARE_FRACTION = 0.02
GHOST_FRACTION = 0.004
BRIGHT_FOR_GLARE_MAG = 0.5


@lru_cache(maxsize=4096)
def channel_tint(temperature_k: float) -> tuple[float, float, float]:
    """Relative R, G, B response to a blackbody, through Gaussian channels at 600, 535 and 465 nm
    (60 nm sigma), normalized so the green response is 1 and white (6500 K) is grey [inference]."""

    def response(t: float) -> np.ndarray:
        lam = np.linspace(380e-9, 720e-9, 171)
        planck = 1.0 / (lam**5 * (np.exp(1.4388e-2 / (lam * t)) - 1.0))
        return np.array([float(np.sum(planck * np.exp(-0.5 * ((lam - c) / 60e-9) ** 2))) for c in (600e-9, 535e-9, 465e-9)])

    rgb = response(float(temperature_k)) / response(WHITE_K)
    return (float(rgb[0] / rgb[1]), 1.0, float(rgb[2] / rgb[1]))


def _tints(temperatures: np.ndarray) -> np.ndarray:
    """(n, 3) channel tints for temperatures rounded to 50 K."""
    rounded = np.round(np.asarray(temperatures, dtype=np.float64) / 50.0) * 50.0
    unique, inverse = np.unique(rounded, return_inverse=True)
    table = np.array([channel_tint(float(t)) for t in unique])
    return table[inverse].reshape(-1, 3)


@dataclass
class FrameTruth:
    """What the benchmark knows about a frame. Simulated, always."""

    frame_id: str
    kind: str
    category: str
    camera_truth: CameraTruth | None
    sky_mask: np.ndarray | None
    star_ra: np.ndarray
    star_dec: np.ndarray
    star_x: np.ndarray
    star_y: np.ndarray
    star_v_mag: np.ndarray
    star_electrons: np.ndarray
    full_well_electrons: float
    exposure_s: float
    trail_px: float
    psf_fwhm_px: float
    metadata: dict
    evidence_class: str = "simulated"

    def summary(self) -> dict:
        return {
            "frameId": self.frame_id,
            "kind": self.kind,
            "category": self.category,
            "evidenceClass": self.evidence_class,
            "cameraTruth": self.camera_truth.to_dict() if self.camera_truth is not None else None,
            "renderedStars": int(self.star_x.size),
            "trailPx": self.trail_px,
            "psfFwhmPx": self.psf_fwhm_px,
            "metadata": self.metadata,
        }


@dataclass
class RenderedFrame:
    spec: FrameSpec
    jpeg_bytes: bytes
    rgb: np.ndarray
    truth: FrameTruth
    linear: np.ndarray | None = field(default=None, repr=False)


# =============================================================================================
# Building blocks
# =============================================================================================


def camera_truth_for(spec: FrameSpec) -> CameraTruth:
    model, coefficients = spec.distortion
    distortion = BrownConrady(*coefficients) if model == BrownConrady.model else KannalaBrandt(*coefficients)
    camera = Camera(intrinsics_for_diagonal_fov(spec.width, spec.height, spec.fov_deg, distortion), distortion)
    frame = ObservationFrame(utc=spec.utc_datetime(), lat_deg=spec.lat_deg, lon_deg=spec.lon_deg)
    return CameraTruth(camera, frame, az_deg=spec.az_deg, alt_deg=spec.alt_deg, roll_deg=spec.roll_deg)


def _electrons_per_v0(spec: FrameSpec) -> float:
    """Electrons per second from a V = 0 source through the pupil at the sensor's efficiency."""
    area_cm2 = math.pi * (spec.pupil_mm / 20.0) ** 2
    return PHOTONS_V0_PER_S_CM2 * area_cm2 * spec.sensor.quantum_efficiency


def _psf_sigma(spec: FrameSpec, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Core Gaussian sigma in output pixels, growing quadratically toward the corners."""
    r = np.hypot(x - (spec.width - 1) / 2.0, y - (spec.height - 1) / 2.0) / (0.5 * math.hypot(spec.width, spec.height))
    fwhm = spec.sensor.psf_fwhm_native_px / spec.binning * (1.0 + spec.sensor.psf_corner_growth * r * r)
    return np.maximum(fwhm, 0.6) / 2.3548


def _deposit_points(image: np.ndarray, x: np.ndarray, y: np.ndarray, flux: np.ndarray, sigma: np.ndarray) -> None:
    """Add pixel-integrated circular Gaussians (exact over each pixel's area) to `image` in place."""
    if x.size == 0:
        return
    h, w = image.shape
    radius = int(math.ceil(4.0 * float(np.max(sigma)))) + 1
    offsets = np.arange(-radius, radius + 1)
    for start in range(0, x.size, 20000):
        sl = slice(start, start + 20000)
        xs, ys, fs, ss = x[sl], y[sl], flux[sl], sigma[sl]
        px = np.round(xs).astype(np.int64)[:, None] + offsets[None, :]
        py = np.round(ys).astype(np.int64)[:, None] + offsets[None, :]
        scale = (math.sqrt(2.0) * ss)[:, None]
        wx = 0.5 * (special.erf((px + 0.5 - xs[:, None]) / scale) - special.erf((px - 0.5 - xs[:, None]) / scale))
        wy = 0.5 * (special.erf((py + 0.5 - ys[:, None]) / scale) - special.erf((py - 0.5 - ys[:, None]) / scale))
        weights = (wy[:, :, None] * wx[:, None, :]) * fs[:, None, None]
        rows = np.broadcast_to(py[:, :, None], weights.shape)
        cols = np.broadcast_to(px[:, None, :], weights.shape)
        keep = (rows >= 0) & (rows < h) & (cols >= 0) & (cols < w) & (weights > 0.0)
        image += np.bincount(rows[keep] * w + cols[keep], weights[keep], minlength=h * w).reshape(h, w)


def _cic_deposit(deposit: np.ndarray, x: np.ndarray, y: np.ndarray, flux: np.ndarray) -> None:
    """Cloud-in-cell deposit: bilinear weights that keep each source's centroid exactly."""
    if x.size == 0:
        return
    h, w = deposit.shape
    x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    fx, fy = x - x0, y - y0
    for dx, dy, weight in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)), (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
        cols, rows = x0 + dx, y0 + dy
        keep = (rows >= 0) & (rows < h) & (cols >= 0) & (cols < w)
        deposit += np.bincount(rows[keep] * w + cols[keep], (flux * weight)[keep], minlength=h * w).reshape(h, w)


def _glare_and_ghost(images: list[np.ndarray], x: float, y: float, flux_rgb: tuple[float, ...], spec: FrameSpec, radius_px: float) -> None:
    """Glare around a very bright source and its ghost mirrored through the image centre [inference]."""
    h, w = images[0].shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    r0 = max(2.0, 2.0 * radius_px)
    glare = (1.0 + ((xx - x) ** 2 + (yy - y) ** 2) / r0**2) ** -1.5
    glare /= glare.sum()
    gx, gy = (spec.width - 1) - x, (spec.height - 1) - y
    ghost = np.exp(-((xx - gx) ** 2 + (yy - gy) ** 2) / (2.0 * max(1.5, radius_px) ** 2))
    ghost /= max(float(ghost.sum()), 1e-12)
    for channel, flux in zip(images, flux_rgb):
        channel += flux * (GLARE_FRACTION * glare + GHOST_FRACTION * ghost)


def _render_moon(images: list[np.ndarray], spec: FrameSpec, truth: CameraTruth, moon: sc.MoonState, enu_grid: np.ndarray,
                 transmission: np.ndarray, rate: float) -> None:
    """The lit part of the Moon's disk, its glare and ghost, if the Moon falls in or near the frame."""
    centre = moon.body.enu
    mx, my = truth.enu_to_pixel(centre[None, :])
    if not (np.isfinite(mx[0]) and -0.2 * spec.width < mx[0] < 1.2 * spec.width and -0.2 * spec.height < my[0] < 1.2 * spec.height):
        return
    radius = math.radians(moon.angular_radius_deg)
    e1 = np.cross([0.0, 0.0, 1.0], centre)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(centre, e1)
    with np.errstate(invalid="ignore"):
        d1, d2 = enu_grid @ e1 / radius, enu_grid @ e2 / radius
        inside = (enu_grid @ centre > 0.0) & (d1 * d1 + d2 * d2 < 1.0)
    phase = math.radians(moon.phase_angle_deg)
    sun_dir = moon.sun_enu - centre * (moon.sun_enu @ centre)
    s1, s2 = float(sun_dir @ e1), float(sun_dir @ e2)
    norm = math.hypot(s1, s2) or 1.0
    s3d = (math.sin(phase) * s1 / norm, math.sin(phase) * s2 / norm, math.cos(phase))
    normal_z = np.sqrt(np.clip(1.0 - d1 * d1 - d2 * d2, 0.0, 1.0))
    lit = inside & ((d1 * s3d[0] + d2 * s3d[1] + normal_z * s3d[2]) > 0.0)
    extinction = 10.0 ** (-0.4 * spec.extinction * float(sc.ks_airmass(np.array(moon.body.alt_deg))))
    flux = 10.0 ** (-0.4 * moon.v_mag) * extinction * rate * spec.exposure_s
    tint = channel_tint(4100.0)
    lit_shape = lit.reshape(spec.height, spec.width).astype(np.float64)
    disk = float(lit_shape.sum())
    if disk > 0.0:
        # Each lit pixel carries its share of the Moon's light, dimmed by the cloud in front of it
        lit_map = lit_shape / disk * transmission
        for channel, t in zip(images, tint):
            channel += lit_map * flux * t
        through = float(lit_map.sum())
    else:
        # The disk lies just outside the frame: its glare still reaches in, through the cloud at its edge
        through = float(transmission[int(np.clip(my[0], 0, spec.height - 1)), int(np.clip(mx[0], 0, spec.width - 1))])
    radius_px = radius / math.radians(spec.fov_deg) * math.hypot(spec.width, spec.height)
    _glare_and_ghost(images, float(mx[0]), float(my[0]), tuple(flux * through * t for t in tint), spec, radius_px)


# =============================================================================================
# Frames
# =============================================================================================


def render_frame(spec: FrameSpec, catalog: TileCatalog, *, ideal: bool = False) -> RenderedFrame:
    """Render a frame from its spec.

    `ideal` skips noise and processing and returns, as `linear`, the green-channel electrons of
    the stars' PSF cores alone (no sky, halos or other light), for checking the truth against
    where the renderer put each star."""
    rng = np.random.default_rng([spec.seed, 101])
    h, w = spec.height, spec.width
    rate = _electrons_per_v0(spec)
    t = spec.exposure_s
    images = [np.zeros((h, w)) for _ in range(3)]
    halos = [np.zeros((h, w)) for _ in range(3)]
    star_layer = np.zeros((h, w))
    truth_cam: CameraTruth | None = None
    sky_mask: np.ndarray | None = None
    stars = {name: np.zeros(0) for name in ("ra", "dec", "x", "y", "v", "e")}
    trail_px = 0.0
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    off_axis = np.zeros((h, w))
    halo_sigma = 1.0

    if spec.has_sky_truth:
        truth_cam = camera_truth_for(spec)
        rays = truth_cam.camera.pixel_to_ray(xx.ravel(), yy.ravel())
        enu = rays @ truth_cam.rotation
        with np.errstate(invalid="ignore"):
            off_axis = np.arccos(np.clip(rays[:, 2], -1.0, 1.0)).reshape(h, w)
            alt = np.degrees(np.arcsin(np.clip(enu[:, 2], -1.0, 1.0))).reshape(h, w)
            az = (np.degrees(np.arctan2(enu[:, 0], enu[:, 1])) % 360.0).reshape(h, w)
        reachable = np.isfinite(alt)
        alt_safe = np.where(reachable, alt, -90.0)
        az_safe = np.where(reachable, az, 0.0)
        skyline = sc.Skyline(spec.skyline[0], spec.seed, spec.skyline[1], spec.skyline[2])
        sky_mask = reachable & ~skyline.ground(az_safe, alt_safe)
        # Solid angle of each pixel from the spread of neighbouring rays, in arcsec^2
        grid = np.nan_to_num(enu.reshape(h, w, 3))
        solid = np.linalg.norm(np.cross(np.gradient(grid, axis=1), np.gradient(grid, axis=0)), axis=-1) * (180.0 / math.pi * 3600.0) ** 2
        glow = sc.SkyGlow(spec.natural_zenith_mag, spec.extinction, spec.artificial_zenith_mag, spec.domes, spec.artificial_temperature_k)
        moon = sc.moon_state(truth_cam.frame)
        separation = np.degrees(np.arccos(np.clip(np.nan_to_num(enu) @ moon.body.enu, -1.0, 1.0))).reshape(h, w)
        tau = sc.cloud_cover((h, w), spec.seed, spec.cloud_fraction) * spec.cloud_depth / 4.0
        transmission = np.exp(-tau)
        natural = glow.natural_flux(alt_safe)
        artificial = glow.artificial_flux(alt_safe, az_safe)
        moonlit = glow.moon_flux(alt_safe, separation, moon if moon.body.alt_deg > 0.0 else None)
        # Clouds reflect the town's light and the Moon's, brighter where thicker [inference]
        zenith_art = 0.0 if spec.artificial_zenith_mag is None else 10.0 ** (-0.4 * spec.artificial_zenith_mag)
        cloud_light = (1.0 - transmission) * (3.0 * zenith_art + 0.5 * 10.0 ** (-0.4 * spec.natural_zenith_mag) + 2.0 * moonlit)
        cool = np.where(sky_mask, (natural + moonlit) * transmission, 0.15 * (natural + moonlit))
        warm = np.where(sky_mask, artificial + cloud_light, 0.15 * artificial)
        for channel, tint_cool, tint_warm in zip(images, channel_tint(5000.0), channel_tint(spec.artificial_temperature_k)):
            channel += np.where(reachable, (cool * tint_cool + warm * tint_warm) * solid * rate * t, 0.0)

        # Stars: a cone over the frame, moved to the date, seen through the true camera
        centre_ra, centre_dec = truth_cam.pixel_to_icrs(np.array([(w - 1) / 2.0]), np.array([(h - 1) / 2.0]))
        cone = catalog.cone(float(centre_ra[0]), float(centre_dec[0]), min(spec.fov_deg / 2.0 + 3.0, 89.0),
                            mag_limit=spec.mag_limit, epoch_jyear=julian_year(truth_cam.frame.jd_utc))
        if len(cone):
            sx, sy = truth_cam.icrs_to_pixel(cone.ra_deg, cone.dec_deg)
            near = np.isfinite(sx) & (sx > -6) & (sx < w + 5) & (sy > -6) & (sy < h + 5)
            col = np.clip(np.round(np.nan_to_num(sx)).astype(int), 0, w - 1)
            row = np.clip(np.round(np.nan_to_num(sy)).astype(int), 0, h - 1)
            near &= sky_mask[row, col] & (alt_safe[row, col] > 0.0)
            idx = np.flatnonzero(near)
            star_alt = alt_safe[row[idx], col[idx]]
            extinction = 10.0 ** (-0.4 * spec.extinction * sc.ks_airmass(star_alt))
            vignette = np.cos(off_axis[row[idx], col[idx]]) ** spec.sensor.vignette_power
            electrons = 10.0 ** (-0.4 * cone.v_mag[idx]) * extinction * transmission[row[idx], col[idx]] * vignette * rate * t
            tints = _tints(cone.temperature_k[idx])
            sigma = _psf_sigma(spec, sx[idx], sy[idx])
            halo_sigma = 3.0 * float(np.median(sigma)) + 1.0 if idx.size else 1.0
            # Earth rotation over the exposure: the truth sampled at sub-steps of the shutter time
            trail_px = SIDEREAL_RATE_RAD_S * t / math.radians(spec.fov_deg) * math.hypot(w, h)
            steps = int(min(64, max(1, math.ceil(2.0 * trail_px))))
            for k in range(steps):
                offset = 0.0 if steps == 1 else (k / (steps - 1) - 0.5) * t
                frame_k = ObservationFrame(utc=spec.utc_datetime(offset), lat_deg=spec.lat_deg, lon_deg=spec.lon_deg)
                px, py = CameraTruth(truth_cam.camera, frame_k, spec.az_deg, spec.alt_deg, spec.roll_deg).icrs_to_pixel(
                    cone.ra_deg[idx], cone.dec_deg[idx])
                good = np.isfinite(px)
                for c in range(3):
                    share = electrons[good] * tints[good, c] / steps
                    _deposit_points(images[c], px[good], py[good], share * (1.0 - GLARE_FRACTION), sigma[good])
                    _cic_deposit(halos[c], px[good], py[good], share * GLARE_FRACTION)
                    if ideal and c == 1:
                        _deposit_points(star_layer, px[good], py[good], share * (1.0 - GLARE_FRACTION), sigma[good])
            stars = {"ra": cone.ra_deg[idx], "dec": cone.dec_deg[idx], "x": sx[idx], "y": sy[idx], "v": cone.v_mag[idx], "e": electrons}
            in_frame = (stars["x"] >= -0.5) & (stars["x"] <= w - 0.5) & (stars["y"] >= -0.5) & (stars["y"] <= h - 0.5)
            stars = {name: values[in_frame] for name, values in stars.items()}
        if moon.body.alt_deg > -1.0:
            _render_moon(images, spec, truth_cam, moon, enu, transmission, rate)
        for light in skyline.lights():
            lx, ly = truth_cam.enu_to_pixel(enu_from_altaz(light.az_deg, light.alt_deg))
            if np.isfinite(lx[0]) and 0 <= lx[0] < w and 0 <= ly[0] < h:
                flux = 10.0 ** (-0.4 * light.v_mag) * rate * t
                tint = channel_tint(light.temperature_k)
                for channel, tk in zip(images, tint):
                    _deposit_points(channel, lx, ly, np.array([flux * tk]), np.array([0.9]))
                if light.v_mag < BRIGHT_FOR_GLARE_MAG:
                    _glare_and_ghost(images, float(lx[0]), float(ly[0]), tuple(flux * tk for tk in tint), spec, 1.0)
    elif spec.category == "screen":
        radiance = sc.screen_radiance((h, w), spec.seed, kind="dots" if spec.seed % 2 == 0 else "interface")
        for channel in images:
            channel += radiance * rate * t
    elif spec.category == "random-dots":
        glow = sc.SkyGlow(spec.natural_zenith_mag, spec.extinction, spec.artificial_zenith_mag, spec.domes, spec.artificial_temperature_k)
        fake_alt = 20.0 + 50.0 * (1.0 - yy / max(h - 1, 1))
        per_px = (glow.natural_flux(fake_alt) + glow.artificial_flux(fake_alt, np.zeros_like(fake_alt))) * (spec.fov_deg * 3600.0 / math.hypot(w, h)) ** 2
        dx, dy, dv = sc.random_dot_field(spec.seed, w, h, int(np.random.default_rng([spec.seed, 3]).integers(40, 300)))
        for channel in images:
            channel += per_px * rate * t
            _deposit_points(channel, dx, dy, 10.0 ** (-0.4 * dv) * rate * t, _psf_sigma(spec, dx, dy))
    # A dark frame gets no light at all

    for light in (sc.aircraft_lights(spec.seed, w, h) if spec.aircraft else []):
        samples = 48
        for k in range(samples):
            tau_s = (k + 0.5) / samples * t
            if light.flash_hz > 0.0 and (tau_s * light.flash_hz) % 1.0 > light.duty:
                continue
            frac = tau_s / t
            lx = np.array([light.x0 + frac * (light.x1 - light.x0)])
            ly = np.array([light.y0 + frac * (light.y1 - light.y0)])
            flux = 10.0 ** (-0.4 * light.v_mag) * rate * t / samples
            for channel, tk in zip(images, channel_tint(light.temperature_k)):
                _deposit_points(channel, lx, ly, np.array([flux * tk]), np.array([0.9]))

    for channel, halo in zip(images, halos):
        if halo.any():
            channel += ndimage.gaussian_filter(halo, halo_sigma, mode="constant")
        np.nan_to_num(channel, copy=False)
    with np.errstate(invalid="ignore"):
        vignette_map = np.where(np.isfinite(off_axis), np.cos(np.nan_to_num(off_axis)) ** spec.sensor.vignette_power, 0.0)
    full_well = spec.sensor.full_well_e * spec.binning**2
    truth = FrameTruth(
        frame_id=spec.frame_id, kind=spec.kind, category=spec.category, camera_truth=truth_cam, sky_mask=sky_mask,
        star_ra=stars["ra"], star_dec=stars["dec"], star_x=stars["x"], star_y=stars["y"], star_v_mag=stars["v"],
        star_electrons=stars["e"], full_well_electrons=full_well, exposure_s=t, trail_px=trail_px,
        psf_fwhm_px=spec.sensor.psf_fwhm_native_px / spec.binning, metadata=spec.metadata(),
    )
    if ideal:
        return RenderedFrame(spec=spec, jpeg_bytes=b"", rgb=np.zeros((h, w, 3), dtype=np.uint8), truth=truth, linear=star_layer)

    # Sensor: a Bayer mosaic (RGGB) of electrons, with shot noise, dark current, hot pixels, read noise
    mosaic = np.empty((h, w))
    mosaic[0::2, 0::2] = images[0][0::2, 0::2]
    mosaic[0::2, 1::2] = images[1][0::2, 1::2]
    mosaic[1::2, 0::2] = images[1][1::2, 0::2]
    mosaic[1::2, 1::2] = images[2][1::2, 1::2]
    dark = spec.sensor.dark_e_per_s * spec.binning**2 * t
    hot = rng.random((h, w)) < spec.sensor.hot_pixel_fraction * spec.binning**2
    signal = np.clip(mosaic, 0.0, None) + dark + hot * spec.sensor.hot_pixel_e_per_s * t
    electrons = rng.poisson(signal).astype(np.float64) + rng.normal(0.0, spec.sensor.read_noise_e * spec.binning, (h, w))
    electrons = np.clip(electrons, 0.0, full_well)
    gain = full_well / 4000.0
    raw = np.round(electrons / gain) * gain - dark
    rgb = _process(raw, vignette_map, spec)
    buffer = io.BytesIO()
    Image.fromarray(rgb).save(buffer, format="JPEG", quality=int(spec.isp.jpeg_quality), subsampling=2)
    jpeg = buffer.getvalue()
    decoded = np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB"), dtype=np.uint8)
    return RenderedFrame(spec=spec, jpeg_bytes=jpeg, rgb=decoded, truth=truth)


def _process(raw: np.ndarray, vignette_map: np.ndarray, spec: FrameSpec) -> np.ndarray:
    """Demosaic, white balance, shading correction, brightening, tone curve, smoothing, sharpening."""
    h, w = raw.shape
    masks = np.zeros((3, h, w))
    masks[0, 0::2, 0::2] = 1.0
    masks[1, 0::2, 1::2] = 1.0
    masks[1, 1::2, 0::2] = 1.0
    masks[2, 1::2, 1::2] = 1.0
    kernel = np.array([[0.25, 0.5, 0.25], [0.5, 1.0, 0.5], [0.25, 0.5, 0.25]])
    planes = np.stack([
        ndimage.convolve(raw * masks[c], kernel, mode="mirror") / ndimage.convolve(masks[c], kernel, mode="mirror")
        for c in range(3)
    ])
    balance = channel_tint(4500.0)
    for c in range(3):
        planes[c] /= balance[c]
    planes /= np.maximum(vignette_map, 0.05)[None, :, :]
    luma = 0.2126 * planes[0] + 0.7152 * planes[1] + 0.0722 * planes[2]
    level = max(float(np.median(luma)), 1e-3 * float(np.percentile(luma, 99.9)), 1e-9)
    # Night modes lift the scene, but hold the brightest half percent below clipping [inference]
    gain = min(spec.isp.sky_level / level, 0.9 / max(float(np.percentile(luma, 99.5)), 1e-12))
    linear = np.clip(planes * gain, 0.0, None)
    linear = np.clip((linear - spec.isp.black_clip) / (1.0 - spec.isp.black_clip), 0.0, 1.0)
    if spec.transfer == "hlg":
        a, b, c = 0.17883277, 0.28466892, 0.55991073
        coded = np.where(linear <= 1.0 / 12.0, np.sqrt(3.0 * linear), a * np.log(np.maximum(12.0 * linear - b, 1e-12)) + c)
    else:
        coded = np.where(linear <= 0.0031308, 12.92 * linear, 1.055 * np.power(linear, 1.0 / 2.4) - 0.055)
    y = 0.299 * coded[0] + 0.587 * coded[1] + 0.114 * coded[2]
    cb, cr = coded[2] - y, coded[0] - y
    if spec.isp.denoise_chroma > 0.0:
        cb = ndimage.gaussian_filter(cb, spec.isp.denoise_chroma)
        cr = ndimage.gaussian_filter(cr, spec.isp.denoise_chroma)
    if spec.isp.denoise_luma > 0.0:
        y = ndimage.gaussian_filter(y, spec.isp.denoise_luma)
    if spec.isp.sharpen > 0.0:
        y = y + spec.isp.sharpen * (y - ndimage.gaussian_filter(y, 1.0))
    r = y + cr
    b = y + cb
    g = (y - 0.299 * r - 0.114 * b) / 0.587
    return np.clip(np.round(np.stack([r, g, b], axis=-1) * 255.0), 0, 255).astype(np.uint8)
