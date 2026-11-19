"""The hangar's recipes: one entry per real NASA craft or suit, read by build_craft.py (inside Blender) and by
write_hangar.py (the provenance record and the facts file). Plain data, no bpy, so both interpreters import it.

Every source is NASA's own public-domain model from github.com/nasa/NASA-3D-Resources, kept in the shared
`.cache/hangar/nasa3d/` (registered in data/downloads.json) and pinned by size and SHA-256. Every fact names the NASA or
JPL page it was read on, with a short quote; the build never makes a number up.

Frames. A recipe gives the craft's forward (its direction of travel) and up as vectors in the frame Blender imports
the source in (Z up). The build turns the craft so forward is glTF +Z (three.js Object3D.lookAt points +Z at its
target) and up is glTF +Y, and scales it so one unit is one metre, from one dimension NASA states.
"""

from __future__ import annotations

NASA3D_REPO = "https://github.com/nasa/NASA-3D-Resources"
NASA3D_RAW = "https://raw.githubusercontent.com/nasa/NASA-3D-Resources/master/3D%20Models"
NASA_GUIDELINES_URL = "https://www.nasa.gov/nasa-brand-center/images-and-media/"
CACHE = ".cache/hangar/nasa3d"

# Size budget per file, bytes, for every craft: LOD0 is the close view (the hangar card, the start of the flight),
# LOD1 the chase camera (about 320 px across on a laptop), LOD2 the far or phone view.
BUDGET_BYTES = {"lod0": 4_000_000, "lod1": 1_500_000, "lod2": 600_000, "picker": 60_000}
# Texture edge per level; never above the source's own texture edge (the build takes the smaller).
TEXTURE_EDGE = {"lod0": 2048, "lod1": 1024, "lod2": 512}
# Triangle share of the cleaned source kept per level (Blender's collapse decimation).
LOD_RATIO = {"lod0": 1.0, "lod1": 0.5, "lod2": 0.18}
PICKER_EDGE = 176  # the picker row's picture is 44 px; four times that for dense screens

CRAFTS: dict[str, dict] = {
    "voyager": {
        "title": "Voyager",
        "kind": "craft",
        "source": {
            "id": "nasa3d-voyager-probe-b",
            "title": "Voyager Probe (B), NASA 3D Resources",
            "folder": "Voyager Probe (B)",
            "file": "Voyager Probe (B).glb",
            "bytes": 1_720_864,
            "sha256": "bd86ded828dd3f459293aee4ffc3cd0998d8db67439317c8299650a1174c3289",
            "picture": "Voyager Probe (B).png",
            "picture_bytes": 417_970,
            "picture_sha256": "66d9fc3028a3f92413519bd413aa1a8705790d0f7786c11f62b1b166bd1ee964",
            "local": "voyager",
        },
        # The source's unit cube at the root has no material and is not part of the craft.
        "drop_objects": ["_root"],
        # The high-gain dish faces Earth (+Z in the import frame, its concave side up), so the craft travels along -Z.
        "forward": (0.0, 0.0, -1.0),
        "up": (1.0, 0.0, 0.0),
        # Scale from the dish: the material that holds it and the dish's true diameter.
        "scale_from": {
            "material": "tex_02_AO",
            "axis": "x",
            "metres": 3.7,
            "page": "https://science.nasa.gov/mission/voyager/spacecraft/",
            "quote": "via the 3.7 meter high-gain antenna (HGA)",
        },
        # Material roles: a role for each source material, and for the photo atlas the colour rules in build_craft.py.
        "materials": {
            "tex_01": {
                "role": "photo_atlas",
                # NASA's logotype on a white label (the photo NASA took of the hardware), in the atlas's pixels.
                "paint_out": [{"box": (834, 446, 872, 478), "what": "NASA logotype and small print on a white label"}],
            },
            "tex_02_AO": {"role": "painted", "base": (0.80, 0.80, 0.78), "roughness": 0.55, "occlusion_from_source": True},
            "tex_02_AO_dark": {"role": "painted", "base": (0.06, 0.06, 0.065), "roughness": 0.45, "occlusion_from_source": True},
        },
        # The hero shot frames this many metres around the origin (the dish's centre).
        "body_radius_m": 2.2,
    },
}

# The hangar as the picker lists it: one entry per real craft or suit, each naming the model it is drawn with (two
# twins may share one). travel is the sourced speed for the true-travel-time readout, or None with the reason the
# design record words (D26 item 5) for a craft that never travels between stars. Every fact carries the page it was
# read on and a short quote; the words a person reads on the card are the design lane's, not these keys.
HANGAR: list[dict] = [
    {
        "id": "voyager-1",
        "name": "Voyager 1",
        "model": "voyager",
        "travel": {
            "speedKmS": 17.0,
            "relativeTo": "Sun",
            "asOf": "2024-08-21",
            "page": "https://science.nasa.gov/mission/voyager/voyager-1/",
            "quote": "38,026.79 mph (17.0 km/second) relative to the Sun",
        },
        "facts": [
            {"key": "launch", "value": "1977-09-05", "page": "https://science.nasa.gov/mission/voyager/voyager-1/", "quote": "Sept. 5, 1977 / 12:56:01 UT"},
            {"key": "interstellarSpace", "value": "2012-08", "page": "https://science.nasa.gov/mission/voyager/voyager-1/", "quote": "crossed into interstellar space in August 2012"},
            {"key": "massKg", "value": 721.9, "page": "https://science.nasa.gov/mission/voyager/voyager-1/", "quote": "Spacecraft Mass 1,592 pounds (721.9 kilograms)"},
            {"key": "power", "value": "3 radioisotope thermoelectric generators", "page": "https://science.nasa.gov/mission/voyager/voyager-1/", "quote": "3 Multi-Hundred Watt Radioisotope Thermoelectric Generators (MHW-RTGs)"},
            {"key": "dishDiameterM", "value": 3.7, "page": "https://science.nasa.gov/mission/voyager/spacecraft/", "quote": "via the 3.7 meter high-gain antenna (HGA)"},
        ],
    },
    {
        "id": "voyager-2",
        "name": "Voyager 2",
        "model": "voyager",
        "travel": {
            "speedKmS": 15.4,
            "relativeTo": "Sun",
            "asOf": "2017-11-05",
            "page": "https://science.nasa.gov/mission/voyager/voyager-2/",
            "quote": "9.6 miles per second (15.4 kilometers per second) relative to the Sun",
        },
        "facts": [
            {"key": "launch", "value": "1977-08-20", "page": "https://science.nasa.gov/mission/voyager/voyager-2/", "quote": "Aug. 20, 1977 / 14:29:44 UT"},
            {"key": "dishDiameterM", "value": 3.7, "page": "https://science.nasa.gov/mission/voyager/spacecraft/", "quote": "via the 3.7 meter high-gain antenna (HGA)"},
        ],
    },
]
