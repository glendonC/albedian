"""Pinned J2000.0 Orion, Taurus, and Winter Hexagon benchmark stars (18 stars)."""

from __future__ import annotations

from services.science.catalog_model import CatalogStar, gaia_dr3_star, hipparcos_star

PINNED_CATALOG_STARS: tuple[CatalogStar, ...] = (
    hipparcos_star(
        "star-betelgeuse", "Betelgeuse", "Alpha Orionis", "Orion", 27989,
        88.792939, 7.407064, 0.42, 27.54, 11.30,
        6.55, 0.83,
    ),
    hipparcos_star(
        "star-rigel", "Rigel", "Beta Orionis", "Orion", 24436,
        78.634467, -8.201638, 0.13, 1.31, 0.50,
        3.78, 0.34,
    ),
    hipparcos_star(
        "star-bellatrix", "Bellatrix", "Gamma Orionis", "Orion", 25336,
        81.282764, 6.349703, 1.64, -8.11, -12.88,
        12.92, 0.52,
    ),
    hipparcos_star(
        "star-alnilam", "Alnilam", "Epsilon Orionis", "Orion", 26311,
        84.053389, -1.201919, 1.69, 1.44, -0.78,
        1.65, 0.45,
    ),
    hipparcos_star(
        "star-alnitak", "Alnitak", "Zeta Orionis", "Orion", 26727,
        85.189694, -1.942572, 1.77, 3.19, 2.03,
        4.43, 0.64,
    ),
    hipparcos_star(
        "star-mintaka", "Mintaka", "Delta Orionis", "Orion", 25930,
        83.001667, -0.299094, 2.23, 0.64, -0.69,
        4.71, 0.58,
    ),
    hipparcos_star(
        "star-saiph", "Saiph", "Kappa Orionis", "Orion", 27366,
        86.939120, -9.669605, 2.06, 1.46, -1.28,
        5.04, 0.22,
    ),
    hipparcos_star(
        "star-aldebaran", "Aldebaran", "Alpha Tauri", "Taurus", 21421,
        68.980163, 16.509302, 0.86, 63.45, -188.94,
        48.94, 0.77,
    ),
    hipparcos_star(
        "star-sirius", "Sirius", "Alpha Canis Majoris", "Canis Major", 32349,
        101.287155, -16.716116, -1.46, -546.01, -1223.07,
        379.21, 1.58,
    ),
    hipparcos_star(
        "star-procyon", "Procyon", "Alpha Canis Minoris", "Canis Minor", 37279,
        114.825493, 5.224993, 0.34, -714.59, -1036.80,
        284.56, 1.26,
    ),
    hipparcos_star(
        "star-elnath", "Elnath", "Beta Tauri", "Taurus", 25428,
        81.572971, 28.607450, 1.65, 22.76, -173.58,
        24.36, 0.34,
    ),
    gaia_dr3_star(
        "star-meissa", "Meissa", "Lambda Orionis", "Orion", 3337991583943473280,
        83.784492, 9.934156, 3.39, -0.14241743, -0.45, -2.62, None,
        337.5872, 386.42804, 464.1803,
    ),
    hipparcos_star(
        "star-hatysa", "Hatysa", "Iota Orionis", "Orion", 26241,
        83.858258, -5.909900, 2.77, 1.45, -0.60,
        1.4, 0.22,
    ),
    hipparcos_star(
        "star-capella", "Capella", "Alpha Aurigae", "Auriga", 24608,
        79.172328, 45.997991, 0.08, 75.25, -426.89,
        76.2, 0.46,
    ),
    hipparcos_star(
        "star-pollux", "Pollux", "Beta Geminorum", "Gemini", 37826,
        116.328958, 28.026199, 1.14, -626.55, -45.80,
        96.54, 0.27,
    ),
    hipparcos_star(
        "star-castor", "Castor", "Alpha Geminorum", "Gemini", 36850,
        113.649428, 31.888276, 1.58, -191.45, -145.19,
        64.12, 3.75,
    ),
    gaia_dr3_star(
        "star-tabit", "Tabit", "Pi3 Orionis", "Orion", 3288921720025503360,
        72.460047, 6.961275, 3.19, 0.8058183, 463.97, 11.83, None,
        8.010779, 8.02372, 8.037924,
    ),
    hipparcos_star(
        "star-tianguan", "Tianguan", "Zeta Tauri", "Taurus", 26451,
        84.411189, 21.142544, 3.01, 1.22, -23.51,
        7.33, 0.82,
    ),
)
