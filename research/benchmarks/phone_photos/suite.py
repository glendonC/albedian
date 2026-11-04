"""Seeded frame specs: the full benchmark suite and the small subset CI runs in seconds.

A `FrameSpec` holds every choice behind a frame (camera, lens, moment, place, pointing, sky,
ground, sensor, processing), drawn from a seed, so a frame can be rebuilt exactly from its spec.
The ranges sampled here are the benchmark's parameters; labels in comments say where each comes
from ([inference] for engineering judgement, [open] where nothing grounds the range).

Positives look at a real sky: scored on whether a solver recovers the true camera. Hard negatives
come in two kinds. Those with a real sky behind them (overcast, aircraft, the Moon in haze) keep
their true camera, so a claim that matches the truth is a correct solve and only a wrong claim is
false. Those with no sky at all (a screen, random dots, a dark frame) count any claim as false.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone

import numpy as np

from research.benchmarks.phone_photos import scene as sc
from research.benchmarks.phone_photos.astrometry import ObservationFrame
from research.benchmarks.phone_photos.cameras import CAMERAS, SENSOR
from research.benchmarks.phone_photos.geometry import BrownConrady, KannalaBrandt, intrinsics_for_diagonal_fov

DEFAULT_SEED = 20261104
PHONE_NATIVE_LONG_SIDE = 4032  # a 12 MP output, the size phone night modes deliver [inference]
NAMED_FIELDS = {
    "orion": (83.8, -1.2),
    "taurus": (61.0, 20.0),
    "cygnus": (305.0, 40.0),
    "scorpius": (250.0, -30.0),
    "ursa-major": (165.0, 56.0),
    "cassiopeia": (14.0, 60.0),
    "crux": (187.0, -60.0),
    "sagittarius": (283.0, -26.0),
}


@dataclass(frozen=True)
class SensorSpec:
    read_noise_e: float
    full_well_e: float
    quantum_efficiency: float
    dark_e_per_s: float
    hot_pixel_fraction: float
    hot_pixel_e_per_s: float
    psf_fwhm_native_px: float
    psf_corner_growth: float
    vignette_power: float


@dataclass(frozen=True)
class IspSpec:
    sky_level: float  # linear level the night mode lifts the median scene to
    black_clip: float
    denoise_luma: float  # Gaussian sigma, px
    denoise_chroma: float
    sharpen: float
    jpeg_quality: int


@dataclass(frozen=True)
class FrameSpec:
    frame_id: str
    kind: str  # "positive" or "negative"
    category: str  # a named field or "random" for positives; the negative's type otherwise
    seed: int
    camera: str
    width: int
    height: int
    binning: float
    fov_deg: float
    distortion: tuple[str, tuple[float, ...]]
    utc: str
    lat_deg: float
    lon_deg: float
    az_deg: float
    alt_deg: float
    roll_deg: float
    exposure_s: float
    mag_limit: float
    pupil_mm: float
    natural_zenith_mag: float
    extinction: float
    artificial_zenith_mag: float | None
    domes: tuple[tuple[float, float, float, float], ...]
    artificial_temperature_k: float
    skyline: tuple[str, float, float]
    cloud_fraction: float
    cloud_depth: float
    aircraft: bool
    sensor: SensorSpec
    isp: IspSpec
    transfer: str
    has_sky_truth: bool

    def utc_datetime(self, offset_s: float = 0.0) -> datetime:
        return datetime.fromisoformat(self.utc) + timedelta(seconds=offset_s)

    def metadata(self) -> dict:
        """What the file's own metadata would say: the solver gets this, as an upload would."""
        spec = CAMERAS[self.camera]
        return {
            "utc": self.utc,
            "latDeg": self.lat_deg,
            "lonDeg": self.lon_deg,
            "camera": spec.label,
            "exposureS": self.exposure_s,
        }

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "FrameSpec":
        values = dict(data)
        values["distortion"] = (values["distortion"][0], tuple(values["distortion"][1]))
        values["domes"] = tuple(tuple(d) for d in values["domes"])
        values["skyline"] = tuple(values["skyline"])
        values["sensor"] = SensorSpec(**values["sensor"])
        values["isp"] = IspSpec(**values["isp"])
        return cls(**values)


# =============================================================================================
# Sampling
# =============================================================================================


def _uniform(rng: np.random.Generator, bounds: tuple[float, float]) -> float:
    return float(rng.uniform(bounds[0], bounds[1]))


def _sensor(rng: np.random.Generator) -> SensorSpec:
    return SensorSpec(
        read_noise_e=_uniform(rng, SENSOR.read_noise_e),
        full_well_e=_uniform(rng, SENSOR.full_well_e),
        quantum_efficiency=_uniform(rng, SENSOR.quantum_efficiency),
        dark_e_per_s=_uniform(rng, SENSOR.dark_current_e_per_s),
        hot_pixel_fraction=float(10.0 ** rng.uniform(math.log10(SENSOR.hot_pixel_fraction[0]), math.log10(SENSOR.hot_pixel_fraction[1]))),
        hot_pixel_e_per_s=_uniform(rng, SENSOR.hot_pixel_e_per_s),
        psf_fwhm_native_px=_uniform(rng, SENSOR.psf_fwhm_native_px),
        psf_corner_growth=_uniform(rng, SENSOR.psf_corner_growth),
        vignette_power=float(rng.uniform(1.0, 2.5)),  # cos^n relative illumination [inference]
    )


def _isp(rng: np.random.Generator) -> IspSpec:
    return IspSpec(
        sky_level=float(rng.uniform(0.04, 0.2)),
        black_clip=float(rng.uniform(0.0, 0.03)),
        denoise_luma=float(rng.uniform(0.0, 0.8)),
        denoise_chroma=float(rng.uniform(0.5, 2.0)),
        sharpen=float(rng.uniform(0.0, 0.6)),
        jpeg_quality=int(rng.integers(75, 96)),
    )


def _distortion(rng: np.random.Generator, correction: str) -> tuple[str, tuple[float, ...]]:
    # Residual distortion after a phone's correction is small; an uncorrected ultra wide is a
    # fisheye [inference: no maker publishes either]
    if correction == "either" and rng.random() < 0.3:
        return (KannalaBrandt.model, (float(rng.uniform(-0.04, 0.02)), float(rng.uniform(-0.01, 0.01)), 0.0, 0.0))
    if correction == "unknown":  # [open]
        return (BrownConrady.model, (float(rng.uniform(-0.15, 0.02)), float(rng.uniform(-0.02, 0.02)), 0.0, 0.0, 0.0))
    k1 = 0.05 if correction == "either" else 0.03
    return (BrownConrady.model, (float(rng.uniform(-k1, k1)), float(rng.uniform(-0.01, 0.01)), 0.0,
                                 float(rng.uniform(-5e-4, 5e-4)), float(rng.uniform(-5e-4, 5e-4))))


def _night(rng: np.random.Generator, need=None, tries: int = 600) -> tuple[ObservationFrame, float, float]:
    """A dark moment (Sun below -18 degrees) somewhere inhabited; `need(frame)` may veto it."""
    for _ in range(tries):
        lat = float(rng.uniform(-45.0, 62.0))
        lon = float(rng.uniform(-180.0, 180.0))
        day = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=float(rng.uniform(0.0, 730.0 * 86400.0)))
        frame = ObservationFrame(utc=day.replace(microsecond=0), lat_deg=round(lat, 4), lon_deg=round(lon, 4))
        if sc.sun_altitude_deg(frame) > -18.0:
            continue
        if need is None or need(frame):
            return frame, lat, lon
    raise RuntimeError("No dark moment found for this frame's constraints.")


def _sky(rng: np.random.Generator, light: str) -> dict:
    """Natural sky (Krisciunas and Schaefer's dark-sky range [inference]) plus artificial light."""
    sky = {
        "natural_zenith_mag": float(rng.uniform(21.3, 22.0)),
        "extinction": float(rng.uniform(0.12, 0.35)),
        "artificial_zenith_mag": None,
        "domes": (),
        "artificial_temperature_k": float(rng.choice([2000.0, 2700.0, 4000.0])),
    }
    if light != "none":
        bounds = {"rural": (20.5, 22.5), "suburban": (19.0, 20.5), "urban": (17.5, 19.0)}[light]
        sky["artificial_zenith_mag"] = float(rng.uniform(*bounds))
        sky["domes"] = tuple(
            (float(rng.uniform(0, 360)), float(rng.uniform(10, 40)), float(rng.uniform(1, 20)), float(rng.uniform(3, 15)))
            for _ in range(int(rng.integers(0, 4)))
        )
    return sky


def _mag_limit(spec: FrameSpec, cap: float) -> float:
    """Faintest magnitude worth rendering: a star whose peak reaches a fifth of the sky noise."""
    rate = math.pi * (spec.pupil_mm / 20.0) ** 2 * 1.0e6 * spec.sensor.quantum_efficiency
    pixel_arcsec = spec.fov_deg * 3600.0 / math.hypot(spec.width, spec.height)
    sky_flux = 10.0 ** (-0.4 * spec.natural_zenith_mag)
    if spec.artificial_zenith_mag is not None:
        sky_flux += 10.0 ** (-0.4 * spec.artificial_zenith_mag)
    background = 1.3 * sky_flux * pixel_arcsec**2 * rate * spec.exposure_s + spec.sensor.dark_e_per_s * spec.binning**2 * spec.exposure_s
    noise = math.sqrt(background + (spec.sensor.read_noise_e * spec.binning) ** 2)
    sigma = max(spec.sensor.psf_fwhm_native_px / spec.binning, 0.6) / 2.3548
    peak_fraction = min(1.0, 1.0 / (2.0 * math.pi * sigma * sigma))
    limit = -2.5 * math.log10(0.2 * noise / (peak_fraction * rate * spec.exposure_s))
    return float(np.clip(limit, 4.0, cap))


def _frame_spec(
    rng: np.random.Generator,
    *,
    frame_id: str,
    kind: str,
    category: str,
    camera_key: str,
    size: tuple[int, int],
    exposure_s: float,
    light: str,
    skyline: str,
    mag_cap: float,
    has_sky_truth: bool = True,
    cloud_fraction: float = 0.0,
    cloud_depth: float = 0.0,
    aircraft: bool = False,
    pointing: str = "random",
) -> FrameSpec:
    camera = CAMERAS[camera_key]
    width, height = size
    native_long = camera.video_size[1] if camera.video_size else PHONE_NATIVE_LONG_SIDE
    fov = camera.diagonal_fov_deg if camera.diagonal_fov_deg is not None else _uniform(rng, camera.fov_open_range_deg)
    for _ in range(20):
        distortion = _distortion(rng, camera.lens_correction)
        model = BrownConrady(*distortion[1]) if distortion[0] == BrownConrady.model else KannalaBrandt(*distortion[1])
        try:
            intrinsics_for_diagonal_fov(width, height, fov, model)
            break
        except ValueError:
            continue
    roll = float(rng.normal(0.0, 2.0)) if exposure_s >= 10.0 else float(rng.uniform(-15.0, 15.0))
    if pointing in NAMED_FIELDS:
        ra, dec = NAMED_FIELDS[pointing]

        def high_enough(frame: ObservationFrame) -> bool:
            return 25.0 <= float(frame.altaz(ra, dec)[1][0]) <= 75.0

        frame, lat, lon = _night(rng, high_enough)
        az, alt = (float(v[0]) for v in frame.altaz(ra, dec))
        az, alt = (az + float(rng.uniform(-4.0, 4.0))) % 360.0, alt + float(rng.uniform(-4.0, 4.0))
    elif pointing == "moon":

        def moon_up(frame: ObservationFrame) -> bool:
            moon = sc.moon_state(frame)
            return moon.body.alt_deg >= 15.0 and moon.illuminated_fraction >= 0.4

        frame, lat, lon = _night(rng, moon_up, tries=3000)
        moon = sc.moon_state(frame)
        az = (moon.body.az_deg + float(rng.uniform(-fov / 6.0, fov / 6.0))) % 360.0
        alt = float(np.clip(moon.body.alt_deg + float(rng.uniform(-fov / 8.0, fov / 8.0)), 8.0, 85.0))
    else:
        frame, lat, lon = _night(rng)
        az, alt = float(rng.uniform(0.0, 360.0)), float(rng.uniform(15.0, 80.0))
    sky = _sky(rng, light)
    base_alt = float(rng.uniform(-1.0, 3.0))
    amplitude = {"open": 0.0, "hills": float(rng.uniform(2.0, 10.0)), "trees": float(rng.uniform(0.5, 3.0)), "buildings": float(rng.uniform(0.0, 2.0))}[skyline]
    spec = FrameSpec(
        frame_id=frame_id,
        kind=kind,
        category=category,
        seed=int(rng.integers(0, 2**31 - 1)),
        camera=camera_key,
        width=width,
        height=height,
        binning=native_long / max(width, height),
        fov_deg=float(fov),
        distortion=distortion,
        utc=frame.utc.isoformat(),
        lat_deg=frame.lat_deg,
        lon_deg=frame.lon_deg,
        az_deg=az,
        alt_deg=alt,
        roll_deg=roll,
        exposure_s=exposure_s,
        mag_limit=0.0,
        pupil_mm=_uniform(rng, camera.aperture_mm_range),
        skyline=(skyline, base_alt, amplitude),
        cloud_fraction=cloud_fraction,
        cloud_depth=cloud_depth,
        aircraft=aircraft,
        sensor=_sensor(rng),
        isp=_isp(rng),
        transfer="hlg" if camera.role == "head-worn" else "srgb",
        has_sky_truth=has_sky_truth,
        **sky,
    )
    return replace(spec, mag_limit=_mag_limit(spec, mag_cap))


# =============================================================================================
# Suites
# =============================================================================================


def _ci(seed: int) -> list[FrameSpec]:
    rng = np.random.default_rng([seed, 1])
    landscape, portrait = (480, 360), (360, 480)
    plan = [
        ("positive", "orion", "iphone-18-pro-main", landscape, 3.0, "none", "open", {}),
        ("positive", "random", "pixel-11-pro-ultrawide", landscape, 10.0, "suburban", "hills", {}),
        ("positive", "cygnus", "galaxy-s25-ultra-wide", landscape, 30.0, "rural", "trees", {}),
        ("positive", "random", "head-worn-video", portrait, 1.0 / 30.0, "rural", "buildings", {}),
        ("negative", "overcast", "iphone-18-pro-main", landscape, 3.0, "urban", "buildings", {"cloud_fraction": 1.0, "cloud_depth": 8.0}),
        ("negative", "moon", "galaxy-s25-ultra-wide", landscape, 1.0, "rural", "open", {"cloud_fraction": 1.0, "cloud_depth": 4.0, "pointing": "moon"}),
        ("negative", "screen", "pixel-11-pro-wide", landscape, 0.5, "none", "open", {"has_sky_truth": False}),
        ("negative", "random-dots", "iphone-18-pro-ultra-wide", landscape, 3.0, "rural", "open", {"has_sky_truth": False}),
    ]
    return [_from_plan(rng, f"ci-{i:02d}", row, mag_cap=8.0) for i, row in enumerate(plan)]


def _from_plan(rng: np.random.Generator, frame_id: str, row: tuple, *, mag_cap: float) -> FrameSpec:
    kind, category, camera, size, exposure, light, skyline, extra = row
    extra = dict(extra)
    pointing = extra.pop("pointing", category if category in NAMED_FIELDS else "random")
    return _frame_spec(rng, frame_id=frame_id, kind=kind, category=category, camera_key=camera, size=size,
                       exposure_s=exposure, light=light, skyline=skyline, mag_cap=mag_cap, pointing=pointing, **extra)


def _full(seed: int, positives_per_camera: int = 30, negatives_per_type: int = 10) -> list[FrameSpec]:
    rng = np.random.default_rng([seed, 2])
    specs: list[FrameSpec] = []
    phones = [key for key, cam in CAMERAS.items() if cam.role != "head-worn"]
    lights = ["none", "rural", "suburban", "urban"]
    skylines = ["open", "hills", "trees", "buildings"]
    for camera in [*phones, "head-worn-video"]:
        for i in range(positives_per_camera):
            worn = camera == "head-worn-video"
            size = (1440, 1920) if worn else ((1512, 2016) if rng.random() < 0.3 else (2016, 1512))
            exposure = 1.0 / 30.0 if worn else float(rng.choice([1.0, 3.0, 10.0, 30.0]))
            category = str(rng.choice(list(NAMED_FIELDS))) if rng.random() < 1.0 / 3.0 else "random"
            cloudy = rng.random() < 0.25
            specs.append(_from_plan(
                rng, f"pos-{len(specs):03d}",
                ("positive", category, camera, size, exposure, str(rng.choice(lights)), str(rng.choice(skylines)),
                 {"cloud_fraction": float(rng.uniform(0.1, 0.6)) if cloudy else 0.0,
                  "cloud_depth": float(rng.uniform(1.0, 4.0)) if cloudy else 0.0,
                  "aircraft": bool(rng.random() < 0.1)}),
                mag_cap=12.0,
            ))
    negatives = {
        "overcast": lambda: {"cloud_fraction": 1.0, "cloud_depth": float(rng.uniform(6.0, 10.0))},
        "aircraft": lambda: {"cloud_fraction": 1.0, "cloud_depth": float(rng.uniform(5.0, 8.0)), "aircraft": True},
        "moon": lambda: {"cloud_fraction": 1.0, "cloud_depth": float(rng.uniform(3.0, 6.0)), "pointing": "moon"},
        "screen": lambda: {"has_sky_truth": False},
        "random-dots": lambda: {"has_sky_truth": False},
        "dark-frame": lambda: {"has_sky_truth": False},
    }
    for category, extra in negatives.items():
        for _ in range(negatives_per_type):
            camera = str(rng.choice(phones))
            exposure = float(rng.choice([0.5, 1.0, 3.0, 10.0]))
            specs.append(_from_plan(
                rng, f"neg-{len(specs):03d}",
                ("negative", category, camera, (2016, 1512), exposure, str(rng.choice(["rural", "suburban", "urban"])),
                 str(rng.choice(skylines)), extra()),
                mag_cap=12.0,
            ))
    return specs


def build_suite(name: str, seed: int = DEFAULT_SEED) -> list[FrameSpec]:
    if name == "ci":
        return _ci(seed)
    if name == "full":
        return _full(seed)
    raise ValueError(f"Unknown suite {name!r}; use 'ci' or 'full'.")
