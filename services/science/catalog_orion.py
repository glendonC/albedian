"""Pinned J2000.0 Orion, Taurus, and Winter Hexagon benchmark stars (18 stars)."""

from __future__ import annotations

from services.science.catalog_model import CatalogStar, make_star

PINNED_CATALOG_STARS: tuple[CatalogStar, ...] = (
    make_star(
        "star-betelgeuse", "Betelgeuse", "Alpha Orionis", "Orion", "HIP 27989",
        88.792939, 7.407064, 0.42, 2.05, "M1-M2 Ia-ab", 27.54, 11.30, 21.91,
        5.95, 0.85, 145.0, 168.0, 197.0, distance_method="geometric_parallax",
    ),
    make_star(
        "star-rigel", "Rigel", "Beta Orionis", "Orion", "HIP 24436",
        78.634467, -8.201638, 0.13, -0.03, "B8 Ia", 1.31, 0.50, 17.80,
        3.78, 0.34, 240.0, 264.0, 292.0, distance_method="geometric_parallax",
    ),
    make_star(
        "star-bellatrix", "Bellatrix", "Gamma Orionis", "Orion", "Gaia DR3 3238925077526700288",
        81.282764, 6.349703, 1.64, -0.22, "B2 III", -8.11, -12.88, 18.20,
        12.92, 0.52, 75.0, 77.4, 80.0, provider="Gaia DR3", release_version="DR3-2022",
    ),
    make_star(
        "star-alnilam", "Alnilam", "Epsilon Orionis", "Orion", "HIP 26311",
        84.053389, -1.201919, 1.69, -0.18, "B0 Ia", 1.44, -0.78, 27.30,
        2.43, 0.91, 340.0, 412.0, 520.0, distance_method="geometric_parallax",
    ),
    make_star(
        "star-alnitak", "Alnitak", "Zeta Orionis", "Orion", "HIP 26727",
        85.189694, -1.942572, 1.77, -0.21, "O9.5 Ib", 3.19, 2.03, 18.50,
        4.43, 0.64, 200.0, 226.0, 260.0, distance_method="geometric_parallax",
    ),
    make_star(
        "star-mintaka", "Mintaka", "Delta Orionis", "Orion", "HIP 25930",
        83.001667, -0.299094, 2.23, -0.22, "O9.5 II", 0.64, -0.69, 18.50,
        4.71, 0.58, 195.0, 212.0, 235.0, distance_method="geometric_parallax",
    ),
    make_star(
        "star-saiph", "Saiph", "Kappa Orionis", "Orion", "HIP 27366",
        86.939120, -9.669605, 2.06, -0.17, "B0.5 Ia", 1.46, -1.28, 20.60,
        5.04, 0.22, 190.0, 198.0, 208.0, distance_method="geometric_parallax",
    ),
    make_star(
        "star-aldebaran", "Aldebaran", "Alpha Tauri", "Taurus", "HIP 21421",
        68.980163, 16.509302, 0.86, 1.54, "K5+ III", 63.45, -188.94, 54.26,
        48.94, 0.77, 20.1, 20.4, 20.8, distance_method="geometric_parallax",
    ),
    make_star(
        "star-sirius", "Sirius", "Alpha Canis Majoris", "Canis Major", "HIP 32349",
        101.287155, -16.716116, -1.46, 0.00, "A1 V", -546.01, -1223.07, -5.50,
        379.21, 1.58, 2.63, 2.64, 2.65, distance_method="geometric_parallax",
    ),
    make_star(
        "star-procyon", "Procyon", "Alpha Canis Minoris", "Canis Minor", "HIP 37279",
        114.825493, 5.224993, 0.34, 0.42, "F5 IV-V", -714.59, -1036.80, -3.20,
        284.56, 1.26, 3.50, 3.51, 3.53, distance_method="geometric_parallax",
    ),
    make_star(
        "star-elnath", "Elnath", "Beta Tauri", "Taurus", "Gaia DR3 3441200894883211520",
        81.572971, 28.607450, 1.65, -0.13, "B7 III", 22.76, -173.58, 9.20,
        24.36, 0.34, 40.5, 41.0, 41.6, provider="Gaia DR3", release_version="DR3-2022",
    ),
    make_star(
        "star-meissa", "Meissa", "Lambda Orionis", "Orion", "Gaia DR3 3337961910939109888",
        83.784492, 9.934156, 3.39, -0.18, "O8 III", -0.45, -2.62, 30.10,
        2.97, 0.21, 315.0, 337.0, 362.0, provider="Gaia DR3", release_version="DR3-2022",
    ),
    make_star(
        "star-hatsya", "Hatsya", "Iota Orionis", "Orion", "HIP 26241",
        83.858258, -5.909900, 2.77, -0.24, "O9 III", 1.45, -0.60, 28.90,
        2.43, 0.35, 360.0, 412.0, 480.0, distance_method="geometric_parallax",
    ),
    make_star(
        "star-capella", "Capella", "Alpha Aurigae", "Auriga", "HIP 24608",
        79.172328, 45.997991, 0.08, 0.80, "G3 III + G0 III", 75.25, -426.89, 29.90,
        76.20, 0.46, 13.0, 13.1, 13.2, distance_method="geometric_parallax",
    ),
    make_star(
        "star-pollux", "Pollux", "Beta Geminorum", "Gemini", "HIP 37826",
        116.328958, 28.026199, 1.14, 1.00, "K0 III", -626.55, -45.80, 3.23,
        96.54, 0.27, 10.30, 10.36, 10.42, distance_method="geometric_parallax",
    ),
    make_star(
        "star-castor", "Castor", "Alpha Geminorum", "Gemini", "HIP 36850",
        113.649428, 31.888276, 1.58, 0.03, "A1 V + A2 Vm", -191.45, -145.19, 6.00,
        64.12, 0.55, 15.4, 15.6, 15.8, distance_method="geometric_parallax",
    ),
    make_star(
        "star-tabit", "Tabit", "Pi3 Orionis", "Orion", "Gaia DR3 3231668644154719872",
        72.460047, 6.961275, 3.19, 0.61, "F6 V", 463.97, 11.83, 24.20,
        124.40, 0.14, 8.01, 8.04, 8.07, provider="Gaia DR3", release_version="DR3-2022",
    ),
    make_star(
        "star-tianguan", "Tianguan", "Zeta Tauri", "Taurus", "Gaia DR3 3400357429947348864",
        84.411189, 21.142544, 3.01, -0.16, "B2 IIIpe", 1.22, -23.51, 20.20,
        7.35, 0.22, 130.0, 136.0, 143.0, provider="Gaia DR3", release_version="DR3-2022",
    ),
)
