"""Physical radii, Bruneton 2017 sea-level atmospheric coefficients, and source paths for Earth-at-night look-dev."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "data" / "lookdev" / "sources"
EARTH_RADIUS_KM = 6371.0
EARTH_RADIUS_M = 6_371_000.0
MOON_RADIUS = 1737.4 / EARTH_RADIUS_KM

# Sea-level coefficients in 1/m (Bruneton 2017), converted to 1/(Earth radius) in volume shaders
RAYLEIGH_PER_M = (5.802e-6, 13.558e-6, 33.1e-6)
RAYLEIGH_SCALE_HEIGHT_M = 8_000.0
MIE_SCATTER_PER_M = 3.996e-6
MIE_ABSORB_PER_M = 4.40e-6
MIE_SCALE_HEIGHT_M = 1_200.0
OZONE_ABSORB_PER_M = (0.650e-6, 1.881e-6, 0.085e-6)
OZONE_CENTER_M = 25_000.0
OZONE_HALF_WIDTH_M = 15_000.0
ATMOSPHERE_TOP_M = 110_000.0
AIRGLOW_ALTITUDE_M = 95_000.0
AIRGLOW_SIGMA_M = 5_000.0
AIRGLOW_COLOR = (0.30, 1.0, 0.25, 1.0)  # oxygen 557.7 nm, in linear sRGB

__all__ = [
    "AIRGLOW_ALTITUDE_M",
    "AIRGLOW_COLOR",
    "AIRGLOW_SIGMA_M",
    "ATMOSPHERE_TOP_M",
    "EARTH_RADIUS_KM",
    "EARTH_RADIUS_M",
    "MIE_ABSORB_PER_M",
    "MIE_SCALE_HEIGHT_M",
    "MIE_SCATTER_PER_M",
    "MOON_RADIUS",
    "OZONE_ABSORB_PER_M",
    "OZONE_CENTER_M",
    "OZONE_HALF_WIDTH_M",
    "RAYLEIGH_PER_M",
    "RAYLEIGH_SCALE_HEIGHT_M",
    "REPO_ROOT",
    "SRC",
]
