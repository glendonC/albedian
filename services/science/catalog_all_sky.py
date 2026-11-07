"""Extended all-sky navigational stars (53 stars) and constellation stick-figure edges (71 edges)."""

from __future__ import annotations

from services.science.catalog_model import CatalogStar, gaia_dr3_star, hipparcos_star

ALL_SKY_EXTRA_STARS: tuple[CatalogStar, ...] = (
    hipparcos_star(
        "star-deneb-algedi", "Deneb Algedi", "Delta Capricorni", "Capricornus", 107556,
        326.760184, -16.127289, 2.85, 261.71, -296.68,
        84.27, 0.19,
    ),
    hipparcos_star(
        "star-dabih", "Dabih", "Beta Capricorni", "Capricornus", 100345,
        305.252822, -14.781480, 3.05, 48.36, 14.02,
        9.98, 1.09,
    ),
    hipparcos_star(
        "star-sadalsuud", "Sadalsuud", "Beta Aquarii", "Aquarius", 106278,
        322.889719, -5.571175, 2.87, 22.79, -6.21,
        6.07, 0.22,
    ),
    hipparcos_star(
        "star-sadalmelik", "Sadalmelik", "Alpha Aquarii", "Aquarius", 109074,
        331.445982, -0.319851, 2.94, 18.27, -9.39,
        6.23, 0.19,
    ),
    hipparcos_star(
        "star-fomalhaut", "Fomalhaut", "Alpha Piscis Austrini", "Piscis Austrinus", 113368,
        344.412693, -29.622237, 1.16, 328.95, -164.67,
        129.81, 0.47,
    ),
    hipparcos_star(
        "star-altair", "Altair", "Alpha Aquilae", "Aquila", 97649,
        297.695827, 8.868321, 0.76, 536.23, 385.29,
        194.95, 0.57,
    ),
    hipparcos_star(
        "star-vega", "Vega", "Alpha Lyrae", "Lyra", 91262,
        279.234735, 38.783689, 0.03, 200.94, 286.23,
        130.23, 0.36,
    ),
    hipparcos_star(
        "star-deneb", "Deneb", "Alpha Cygni", "Cygnus", 102098,
        310.357980, 45.280339, 1.25, 1.56, 1.55,
        2.31, 0.32,
    ),
    hipparcos_star(
        "star-enif", "Enif", "Epsilon Pegasi", "Pegasus", 107315,
        326.046490, 9.875009, 2.39, 30.02, -1.38,
        4.73, 0.17,
    ),
    hipparcos_star(
        "star-markab", "Markab", "Alpha Pegasi", "Pegasus", 113963,
        346.190223, 15.205264, 2.48, 60.40, -41.30,
        24.46, 0.19,
    ),
    hipparcos_star(
        "star-scheat", "Scheat", "Beta Pegasi", "Pegasus", 113881,
        345.943573, 28.082789, 2.42, 187.65, 136.93,
        16.64, 0.15,
    ),
    hipparcos_star(
        "star-algenib", "Algenib", "Gamma Pegasi", "Pegasus", 1067,
        3.308969, 15.183596, 2.84, 1.93, -9.28,
        8.33, 0.53,
    ),
    hipparcos_star(
        "star-alpheratz", "Alpheratz", "Alpha Andromedae", "Andromeda", 677,
        2.096916, 29.090431, 2.06, 135.68, -162.95,
        33.62, 0.35,
    ),
    hipparcos_star(
        "star-mirach", "Mirach", "Beta Andromedae", "Andromeda", 5447,
        17.433015, 35.620558, 2.05, 175.90, -112.20,
        16.52, 0.56,
    ),
    hipparcos_star(
        "star-almach", "Almach", "Gamma1 Andromedae", "Andromeda", 9640,
        30.974804, 42.329725, 2.10, 43.08, -50.85,
        8.3, 1.04,
    ),
    hipparcos_star(
        "star-diphda", "Diphda", "Beta Ceti", "Cetus", 3419,
        10.897379, -17.986606, 2.02, 232.55, 31.99,
        33.86, 0.16,
    ),
    hipparcos_star(
        "star-mirfak", "Mirfak", "Alpha Persei", "Perseus", 15863,
        51.080709, 49.861179, 1.79, 23.75, -26.23,
        6.44, 0.17,
    ),
    hipparcos_star(
        "star-algol", "Algol", "Beta Persei", "Perseus", 14576,
        47.042215, 40.955648, 2.12, 2.99, -1.66,
        36.27, 1.4,
    ),
    hipparcos_star(
        "star-polaris", "Polaris", "Alpha Ursae Minoris", "Ursa Minor", 11767,
        37.954561, 89.264109, 1.98, 44.48, -11.85,
        7.54, 0.11,
    ),
    gaia_dr3_star(
        "star-algedi", "Algedi", "Alpha2 Capricorni", "Capricornus", 6880093650314169984,
        304.513571, -12.544849, 3.57, 1.1736479, 62.61, 2.66, None,
        33.15436, 33.364403, 33.55109,
    ),
    gaia_dr3_star(
        "star-nashira", "Nashira", "Gamma Capricorni", "Capricornus", 6838023243053666688,
        325.022699, -16.662359, 3.68, 0.47295427, 187.84, -22.29, None,
        50.639584, 52.203842, 54.124126,
    ),
    gaia_dr3_star(
        "star-skat", "Skat", "Delta Aquarii", "Aquarius", 2404579570000684544,
        343.663237, -15.820817, 3.27, 0.27975655, -42.02, -21.25, 10.962601,
        41.809383, 43.848095, 46.423626,
    ),
    gaia_dr3_star(
        "star-albali", "Albali", "Epsilon Aquarii", "Aquarius", 6903096911032851584,
        311.918971, -9.495778, 3.77, 0.058475256, 31.89, -35.61, -19.815142,
        72.58999, 74.371826, 76.3026,
    ),
    hipparcos_star(
        "star-sadr", "Sadr", "Gamma Cygni", "Cygnus", 100453,
        305.557091, 40.256679, 2.23, 2.41, -0.92,
        1.78, 0.27,
    ),
    hipparcos_star(
        "star-albireo", "Albireo", "Beta1 Cygni", "Cygnus", 95947,
        292.680335, 27.959681, 3.08, -7.17, -6.15,
        7.51, 0.33,
    ),
    hipparcos_star(
        "star-gienah-cygni", "Gienah", "Epsilon Cygni", "Cygnus", 102488,
        311.552842, 33.970257, 2.48, 355.66, 330.60,
        44.86, 0.12,
    ),
    hipparcos_star(
        "star-fawaris", "Fawaris", "Delta Cygni", "Cygnus", 97165,
        296.243661, 45.130812, 2.87, 42.98, 46.15,
        19.77, 0.48,
    ),
    gaia_dr3_star(
        "star-sheliak", "Sheliak", "Beta Lyrae", "Lyra", 2090687795056051328,
        282.519979, 33.362667, 3.52, 0.21071863, 1.55, -4.46, -29.482485,
        263.0956, 276.21762, 289.68326,
    ),
    gaia_dr3_star(
        "star-sulafat", "Sulafat", "Gamma Lyrae", "Lyra", 2043823929421342848,
        284.735929, 32.689557, 3.25, 0.23184443, -3.05, 1.08, -19.554564,
        193.15596, 203.35674, 211.9433,
    ),
    hipparcos_star(
        "star-tarazed", "Tarazed", "Gamma Aquilae", "Aquila", 97278,
        296.564912, 10.613261, 2.72, 15.72, -3.08,
        8.26, 0.17,
    ),
    gaia_dr3_star(
        "star-alshain", "Alshain", "Beta Aquilae", "Aquila", 4296708789290712064,
        298.828305, 6.406763, 3.71, 1.1103241, 45.28, -481.92, None,
        13.570887, 13.596004, 13.619923,
    ),
    hipparcos_star(
        "star-menkar", "Menkar", "Alpha Ceti", "Cetus", 14135,
        45.569888, 4.089739, 2.54, -10.41, -76.85,
        13.09, 0.44,
    ),
    hipparcos_star(
        "star-hamal", "Hamal", "Alpha Arietis", "Aries", 9884,
        31.793363, 23.462423, 2.01, 190.23, -148.08,
        49.56, 0.25,
    ),
    hipparcos_star(
        "star-sheratan", "Sheratan", "Beta Arietis", "Aries", 8903,
        28.660046, 20.808031, 2.64, 98.74, -110.48,
        55.6, 0.58,
    ),
    hipparcos_star(
        "star-menkalinan", "Menkalinan", "Beta Aurigae", "Auriga", 28360,
        89.882179, 44.947433, 1.90, -56.44, -0.95,
        40.21, 0.23,
    ),
    hipparcos_star(
        "star-alhena", "Alhena", "Gamma Geminorum", "Gemini", 31681,
        99.427960, 16.399280, 1.93, -2.04, -66.92,
        29.84, 2.23,
    ),
    hipparcos_star(
        "star-adhara", "Adhara", "Epsilon Canis Majoris", "Canis Major", 33579,
        104.656453, -28.972086, 1.50, 3.24, 1.33,
        8.05, 0.14,
    ),
    hipparcos_star(
        "star-wezen", "Wezen", "Delta Canis Majoris", "Canis Major", 34444,
        107.097850, -26.393200, 1.83, -2.75, 3.33,
        2.03, 0.38,
    ),
    hipparcos_star(
        "star-mirzam", "Mirzam", "Beta Canis Majoris", "Canis Major", 30324,
        95.674939, -17.955919, 1.98, -3.23, -0.78,
        6.62, 0.22,
    ),
    hipparcos_star(
        "star-kochab", "Kochab", "Beta Ursae Minoris", "Ursa Minor", 72607,
        222.676357, 74.155504, 2.07, -32.61, 11.42,
        24.91, 0.12,
    ),
    gaia_dr3_star(
        "star-pherkad", "Pherkad", "Gamma Ursae Minoris", "Ursa Minor", 1696877570753454208,
        230.182150, 71.834017, 3.00, 0.4629395, -18.15, 17.50, None,
        146.82521, 152.76784, 159.62297,
    ),
    hipparcos_star(
        "star-dubhe", "Dubhe", "Alpha Ursae Majoris", "Ursa Major", 54061,
        165.931965, 61.751035, 1.79, -134.11, -34.70,
        26.54, 0.48,
    ),
    hipparcos_star(
        "star-merak", "Merak", "Beta Ursae Majoris", "Ursa Major", 53910,
        165.460319, 56.382426, 2.37, 81.43, 33.49,
        40.9, 0.16,
    ),
    hipparcos_star(
        "star-phecda", "Phecda", "Gamma Ursae Majoris", "Ursa Major", 58001,
        178.457697, 53.694760, 2.44, 107.91, 11.02,
        39.21, 0.4,
    ),
    gaia_dr3_star(
        "star-megrez", "Megrez", "Delta Ursae Majoris", "Ursa Major", 1575287046603605888,
        183.856553, 57.032617, 3.31, 0.26148772, 104.11, 7.30, -12.387454,
        24.690784, 24.79358, 24.907433,
    ),
    hipparcos_star(
        "star-alioth", "Alioth", "Epsilon Ursae Majoris", "Ursa Major", 62956,
        193.507290, 55.959823, 1.77, 111.91, -8.24,
        39.51, 0.2,
    ),
    hipparcos_star(
        "star-mizar", "Mizar", "Zeta Ursae Majoris", "Ursa Major", 65378,
        200.981429, 54.925362, 2.23, 119.01, -25.97,
        38.01, 1.71,
    ),
    hipparcos_star(
        "star-alkaid", "Alkaid", "Eta Ursae Majoris", "Ursa Major", 67301,
        206.885157, 49.313267, 1.86, -121.17, -14.91,
        31.38, 0.24,
    ),
    hipparcos_star(
        "star-schedar", "Schedar", "Alpha Cassiopeiae", "Cassiopeia", 3179,
        10.126838, 56.537331, 2.24, 50.88, -32.13,
        14.29, 0.15,
    ),
    hipparcos_star(
        "star-caph", "Caph", "Beta Cassiopeiae", "Cassiopeia", 746,
        2.294522, 59.149781, 2.28, 523.50, -179.72,
        59.58, 0.38,
    ),
    hipparcos_star(
        "star-gamma-cas", "Tsih", "Gamma Cassiopeiae", "Cassiopeia", 4427,
        14.177216, 60.716740, 2.47, 25.17, -3.92,
        5.94, 0.12,
    ),
    hipparcos_star(
        "star-ruchbah", "Ruchbah", "Delta Cassiopeiae", "Cassiopeia", 6686,
        21.453965, 60.235284, 2.68, 296.53, -49.18,
        32.81, 0.14,
    ),
    gaia_dr3_star(
        "star-segin", "Segin", "Epsilon Cassiopeiae", "Cassiopeia", 511974502720839040,
        28.598857, 63.670101, 3.37, -0.027085543, 37.98, -16.33, None,
        135.42078, 140.3659, 145.70161,
    ),
)

CONSTELLATION_EDGES: tuple[tuple[str, str, str], ...] = (
    # Orion
    ("Orion", "star-betelgeuse", "star-meissa"),
    ("Orion", "star-meissa", "star-bellatrix"),
    ("Orion", "star-betelgeuse", "star-alnitak"),
    ("Orion", "star-bellatrix", "star-mintaka"),
    ("Orion", "star-alnitak", "star-alnilam"),
    ("Orion", "star-alnilam", "star-mintaka"),
    ("Orion", "star-alnitak", "star-saiph"),
    ("Orion", "star-mintaka", "star-rigel"),
    ("Orion", "star-saiph", "star-rigel"),
    ("Orion", "star-alnilam", "star-hatsya"),
    ("Orion", "star-bellatrix", "star-tabit"),
    # Taurus
    ("Taurus", "star-aldebaran", "star-elnath"),
    ("Taurus", "star-aldebaran", "star-tianguan"),
    # Auriga
    ("Auriga", "star-capella", "star-menkalinan"),
    ("Auriga", "star-menkalinan", "star-elnath"),
    ("Auriga", "star-capella", "star-elnath"),
    # Gemini
    ("Gemini", "star-castor", "star-pollux"),
    ("Gemini", "star-pollux", "star-alhena"),
    # Canis Major
    ("Canis Major", "star-sirius", "star-mirzam"),
    ("Canis Major", "star-sirius", "star-wezen"),
    ("Canis Major", "star-wezen", "star-adhara"),
    # Capricornus
    ("Capricornus", "star-algedi", "star-dabih"),
    ("Capricornus", "star-dabih", "star-nashira"),
    ("Capricornus", "star-nashira", "star-deneb-algedi"),
    ("Capricornus", "star-algedi", "star-deneb-algedi"),
    # Aquarius
    ("Aquarius", "star-albali", "star-sadalsuud"),
    ("Aquarius", "star-sadalsuud", "star-sadalmelik"),
    ("Aquarius", "star-sadalsuud", "star-skat"),
    ("Aquarius", "star-sadalmelik", "star-skat"),
    # Piscis Austrinus
    ("Piscis Austrinus", "star-fomalhaut", "star-skat"),
    # Pegasus (Great Square + Enif)
    ("Pegasus", "star-enif", "star-markab"),
    ("Pegasus", "star-markab", "star-scheat"),
    ("Pegasus", "star-markab", "star-algenib"),
    ("Pegasus", "star-scheat", "star-alpheratz"),
    ("Pegasus", "star-algenib", "star-alpheratz"),
    # Andromeda
    ("Andromeda", "star-alpheratz", "star-mirach"),
    ("Andromeda", "star-mirach", "star-almach"),
    # Perseus
    ("Perseus", "star-mirfak", "star-algol"),
    ("Perseus", "star-mirfak", "star-almach"),
    # Aries
    ("Aries", "star-hamal", "star-sheratan"),
    # Cetus
    ("Cetus", "star-diphda", "star-menkar"),
    # Cygnus (Northern Cross)
    ("Cygnus", "star-deneb", "star-sadr"),
    ("Cygnus", "star-sadr", "star-albireo"),
    ("Cygnus", "star-sadr", "star-gienah-cygni"),
    ("Cygnus", "star-sadr", "star-fawaris"),
    # Lyra
    ("Lyra", "star-vega", "star-sheliak"),
    ("Lyra", "star-sheliak", "star-sulafat"),
    ("Lyra", "star-sulafat", "star-vega"),
    # Aquila
    ("Aquila", "star-tarazed", "star-altair"),
    ("Aquila", "star-altair", "star-alshain"),
    # Summer Triangle (Navigational Asterism)
    ("Summer Triangle", "star-vega", "star-deneb"),
    ("Summer Triangle", "star-deneb", "star-altair"),
    ("Summer Triangle", "star-altair", "star-vega"),
    # Winter Hexagon (Navigational Asterism)
    ("Winter Hexagon", "star-rigel", "star-aldebaran"),
    ("Winter Hexagon", "star-aldebaran", "star-capella"),
    ("Winter Hexagon", "star-capella", "star-pollux"),
    ("Winter Hexagon", "star-pollux", "star-procyon"),
    ("Winter Hexagon", "star-procyon", "star-sirius"),
    ("Winter Hexagon", "star-sirius", "star-rigel"),
    # Cassiopeia (The W)
    ("Cassiopeia", "star-caph", "star-schedar"),
    ("Cassiopeia", "star-schedar", "star-gamma-cas"),
    ("Cassiopeia", "star-gamma-cas", "star-ruchbah"),
    ("Cassiopeia", "star-ruchbah", "star-segin"),
    # Ursa Major (Big Dipper)
    ("Ursa Major", "star-dubhe", "star-merak"),
    ("Ursa Major", "star-merak", "star-phecda"),
    ("Ursa Major", "star-phecda", "star-megrez"),
    ("Ursa Major", "star-megrez", "star-dubhe"),
    ("Ursa Major", "star-megrez", "star-alioth"),
    ("Ursa Major", "star-alioth", "star-mizar"),
    ("Ursa Major", "star-mizar", "star-alkaid"),
    # Ursa Minor
    ("Ursa Minor", "star-polaris", "star-kochab"),
    ("Ursa Minor", "star-kochab", "star-pherkad"),
)
