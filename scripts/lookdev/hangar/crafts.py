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
        "changes": "photo",
    },
    "jwst": {
        "title": "James Webb Space Telescope",
        "kind": "craft",
        "source": {
            "id": "nasa3d-james-webb-space-telescope-a",
            "title": "James Webb Space Telescope (A), NASA 3D Resources",
            "folder": "James Webb Space Telescope (A)",
            "file": "James Webb Space Telescope (A).glb",
            "bytes": 4_710_016,
            "sha256": "ea4a53f71f1e7477d8b90e30d1299b2cb33faea18acdcaf831dc9d3ebf1b7750",
            "picture": "James Webb Space Telescope (A).png",
            "picture_bytes": 518_892,
            "picture_sha256": "a53474485946d90d84fce0bc8af85ec79ed6d2615420ede3a3ef08c480290c10",
            "local": "jwst",
        },
        "drop_objects": [],
        # The primary mirror faces +X in the import frame (its secondary mirror stands in front of it there) and the
        # sunshield lies below it, the Sun's side being -Z: forward is where the telescope looks, up its cold side.
        "forward": (1.0, 0.0, 0.0),
        "up": (0.0, 0.0, 1.0),
        # Scale from the sunshield's width; NASA's model is shorter than NASA's 21.197 m length (hangar-2 report).
        "scale_from": {
            "material": "James_Webb_Space_Telescope:shld_silv1",
            "axis": "y",
            "metres": 14.162,
            "page": "https://science.nasa.gov/mission/webb/fact-sheet/",
            "quote": "Size of Sun Shield 21.197 m x 14.162 m (69.5 ft x 46.5 ft)",
        },
        # By the names NASA's model gives its materials, first match wins; base colours are linear RGB. Gold is gold's
        # own reflectance; the sunshield's sun side is silvery with a pink cast, its cold side silver, as NASA's
        # photographs and its own render of this model show.
        "material_rules": [
            {"match": ["omnimirror"], "role": "nasa_flat"},
            {"match": ["mirror_blinn"], "role": "pbr", "base": (1.0, 0.766, 0.336), "metallic": 1.0, "roughness": 0.12},
            {"match": ["edge_gold"], "role": "pbr", "base": (1.0, 0.766, 0.336), "metallic": 1.0, "roughness": 0.3},
            {"match": ["sacells"], "role": "pbr", "base": (0.02, 0.025, 0.06), "metallic": 0.0, "roughness": 0.15},
            {"match": ["ssm"], "role": "pbr", "base": (0.91, 0.91, 0.92), "metallic": 1.0, "roughness": 0.06},
            {"match": ["pink", "pnk", "pmli"], "role": "pbr", "base": (0.86, 0.70, 0.80), "metallic": 1.0, "roughness": 0.36},
            {"match": ["silv", "smli"], "role": "pbr", "base": (0.90, 0.90, 0.91), "metallic": 1.0, "roughness": 0.34},
            {"match": ["black", "blk"], "role": "pbr", "base": (0.025, 0.025, 0.025), "metallic": 0.0, "roughness": 0.55},
            {"match": ["graphite", "carbonfiber"], "role": "pbr", "base": (0.04, 0.04, 0.04), "metallic": 0.0, "roughness": 0.35},
            {"match": ["drkalum"], "role": "pbr", "base": (0.45, 0.47, 0.5), "metallic": 1.0, "roughness": 0.4},
            {"match": ["alum", "alu"], "role": "pbr", "base": (0.91, 0.92, 0.92), "metallic": 1.0, "roughness": 0.35},
            {"match": ["whitepaint"], "role": "pbr", "base": (0.85, 0.85, 0.85), "metallic": 0.0, "roughness": 0.6},
            {"match": [""], "role": "nasa_flat"},
        ],
        "planar_deg": 1.0,
        "smooth_deg": 30.0,
        "lod_ratio": {"lod0": 0.17, "lod1": 0.06, "lod2": 0.022},
        "body_radius_m": 7.0,
        "changes": "named",
    },
    "pioneer10": {
        "title": "Pioneer 10",
        "kind": "craft",
        "source": {
            "id": "nasa3d-pioneer-10",
            "title": "Pioneer 10, NASA 3D Resources",
            "folder": "Pioneer 10",
            "file": "Pioneer 10.glb",
            "bytes": 2_049_840,
            "sha256": "81443a7fa01cca0dda878cd27692b3307f9c332c4eb8e55a844499d7b350573c",
            "picture": "Pioneer 10.png",
            "picture_bytes": 996_987,
            "picture_sha256": "46a4bd3dc507930f9f3df5fda41b63d65d8b52023968414b5f559c9424a3b5ea",
            "local": "pioneer10",
        },
        "drop_objects": [],
        # The dish faces +Y in the import frame (its feed struts reach forward there, the equipment box sits behind
        # it at -Y) and faces Earth, so the craft travels along -Y; the magnetometer boom hangs along -Z.
        "forward": (0.0, -1.0, 0.0),
        "up": (0.0, 0.0, 1.0),
        "scale_from": {
            "material": "dish_AO",
            "axis": "x",
            "metres": 2.74,
            "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1972-012A",
            "quote": "2.74-m-diameter parabolic dish antenna that was 46 cm deep",
        },
        "materials": {
            "Pioneer_tex_01": {"role": "photo_atlas"},
            "pioneer_tex_02": {"role": "photo_atlas"},
            "foil_gold": {"role": "photo_atlas"},
            "foil_silver": {"role": "photo_atlas"},
            "Tex_black": {"role": "photo_atlas"},
            "dish_AO": {"role": "painted", "base": (0.80, 0.80, 0.78), "roughness": 0.55, "occlusion_from_source": True},
            "other_AO": {"role": "painted", "base": (0.62, 0.62, 0.62), "roughness": 0.5, "occlusion_from_source": True},
            "RTG_AO_new": {"role": "painted", "base": (0.10, 0.10, 0.10), "roughness": 0.5, "occlusion_from_source": True},
            "Pioneer_silver_01": {"role": "pbr", "base": (0.80, 0.80, 0.80), "metallic": 1.0, "roughness": 0.35},
            "Pioneer_silver_02": {"role": "pbr", "base": (0.80, 0.80, 0.80), "metallic": 1.0, "roughness": 0.35},
            "norm_trans": {"role": "nasa_flat"},
        },
        "body_radius_m": 1.8,
        "changes": "photo",
    },
    "emu": {
        "title": "Extravehicular Mobility Unit",
        "kind": "suit",
        "source": {
            "id": "nasa3d-extravehicular-mobility-unit",
            "title": "Extravehicular Mobility Unit, NASA 3D Resources",
            "folder": "Extravehicular Mobility Unit",
            "file": "Extravehicular Mobility Unit.glb",
            "url": "https://assets.science.nasa.gov/content/dam/science/cds/3d/resources/model/extravehicular-mobility-unit/Extravehicular%20Mobility%20Unit.glb",
            "page": "https://science.nasa.gov/3d-resources/extravehicular-mobility-unit/",
            "bytes": 3_448_904,
            "sha256": "4dd79f1f8e02ab39bc15d87a7bbeda1f437e1103ab7661f7797340e0c114f372",
            # NASA's preview of this model is a screenshot of its modelling program, so the picker's picture is
            # rendered from the rebuilt model instead.
            "picture": None,
            "picker_render_reason": "screenshot",
            "local": "emu",
        },
        "drop_objects": [],
        # Second copies of parts NASA's file holds twice: the same faces again under these materials, one of them
        # wearing the flag texture by mistake.
        "drop_materials": [
            "EMU:emusuit21_emusuit5_blinn1SG",
            "EMU:emusuit21_emusuit5_blinn3SG",
            "EMU:emusuit21_emusuit5_jetpack2SG",
            "EMU:emusuit21_emusuit5_lambert5SG",
        ],
        # The suit stands up +Z and faces +Y in the import frame (its visor and chest controls there, the backpack at
        # -Y): it moves the way it faces, so the chase camera behind it sees the backpack.
        "forward": (0.0, 1.0, 0.0),
        "up": (0.0, 0.0, 1.0),
        # NASA's model is built in metres (glTF's unit): 2.00 m suited, inside the fit NASA states up to 6 ft 2 in.
        "scale_from": {
            "native": True,
            "page": "https://www.nasa.gov/wp-content/uploads/2015/07/2019_07_ea_emu.pdf",
            "quote": "95th percentile male (6'2\" tall",
        },
        "materials": {
            "EMU:emusuit21_lambert8SG": {"role": "pbr", "base": (0.93, 0.93, 0.93), "metallic": 0.0, "roughness": 1.0},
            "EMU:emusuit21_lambert9SG": {"role": "textured", "metallic": 0.0, "roughness": 0.9},
            "EMU:emusuit21_lambert7SG": {"role": "textured", "metallic": 0.0, "roughness": 0.7},
            # The visor and the helmet's gold parts: NASA's file gives them gold but matte, the visor part see-through;
            # seen from outside the visor is a gold film.
            "EMU:emusuit21_blinn1SG": {"role": "pbr", "base": (1.0, 0.766, 0.336), "metallic": 1.0, "roughness": 0.1},
            "EMU:emusuit21_blinn3SG": {"role": "pbr", "base": (1.0, 0.766, 0.336), "metallic": 1.0, "roughness": 0.1},
            "EMU:emusuit21_emusuit4_justbody_blinn3SG": {"role": "pbr", "base": (1.0, 0.766, 0.336), "metallic": 1.0, "roughness": 0.1},
        },
        "material_rules": [{"match": [""], "role": "nasa_flat"}],
        "weld_m": 0.0001,
        "smooth_deg": 40.0,
        # NASA's file keeps its faces apart at shared corners (each quad with its own vertices), which left the
        # fabric and visor blocky and the decimation streaked: welded within 0.1 mm first.
        "lod_ratio": {"lod0": 0.45, "lod1": 0.15, "lod2": 0.05},
        "body_radius_m": 1.1,
        "changes": "suit",
    },
    "newhorizons": {
        "title": "New Horizons",
        "kind": "craft",
        "source": {
            "id": "nasa3d-new-horizons",
            "title": "New Horizons, NASA 3D Resources",
            "folder": "New Horizons",
            "file": "New_Horizons.glb",
            "url": "https://assets.science.nasa.gov/content/dam/science/psd/solar/2023/09/n/New_Horizons.glb",
            "page": "https://science.nasa.gov/resource/new-horizons-3d-model/",
            "bytes": 3_293_532,
            "sha256": "cf152d8cea17c4a83711041cb0d29ca1d980bfafa084ada2656569ea92cdd73f",
            # NASA's own render of this model, the preview its resource page shows.
            "picture": "spacecraft_th_new_horizons.jpg",
            "picture_url": "https://assets.science.nasa.gov/content/dam/science/psd/solar/2023/09/s/spacecraft_th_new_horizons.jpg",
            "picture_bytes": 14_212,
            "picture_sha256": "cd5fa2e0638249d975cf8817842a049585ba081f3e362deb458e9eff9b72da5b",
            "local": "newhorizons",
        },
        # The source's unit cube at the root (a see-through helper) is not part of the craft.
        "drop_objects": ["_root"],
        # The dish faces +Z in the import frame (the equipment box below it) and faces Earth, so the craft travels
        # along -Z; the RTG reaches along -Y, so +Y is up and the RTG hangs below.
        "forward": (0.0, 0.0, -1.0),
        "up": (0.0, 1.0, 0.0),
        "scale_from": {
            "material": "AO_dish",
            "axis": "x",
            "metres": 2.1,
            "page": "https://www.nasa.gov/wp-content/uploads/2015/03/139889main_presskit12_05.pdf",
            "quote": "A 2.1-meter (83-inch) diameter antenna dish",
        },
        "materials": {
            "tex_01": {"role": "photo_atlas"},
            "tex_02": {"role": "photo_atlas"},
            "foil_gold": {"role": "keep"},
            "AO_dish": {"role": "painted", "base": (0.80, 0.80, 0.78), "roughness": 0.55, "occlusion_from_source": True},
            "AO_1": {"role": "painted", "base": (0.10, 0.10, 0.10), "roughness": 0.5, "occlusion_from_source": True},
        },
        "body_radius_m": 1.6,
        "changes": "photo",
    },
    "z2": {
        "title": "Z-2",
        "kind": "suit",
        "source": {
            "id": "nasa3d-z2-spacesuit",
            "title": "Z2 Spacesuit, NASA 3D Resources",
            "folder": "Z2 Spacesuit",
            "file": "Z2 Spacesuit.glb",
            "page": "https://science.nasa.gov/3d-resources/z2-spacesuit/",
            "bytes": 16_933_148,
            "sha256": "4199762c27f8e730030a7bf9f1a453c203457e84c33b36247c0237a74f9a0c2a",
            # NASA's own render of this model shows the NASA insignia on the shoulder, which the rebuild covers.
            "picture": None,
            "picker_render_reason": "insignia",
            "local": "z2",
        },
        "drop_objects": [],
        # The suit stands up +Z and faces -Y in the import frame (its visor there): it moves the way it faces.
        "forward": (0.0, -1.0, 0.0),
        "up": (0.0, 0.0, 1.0),
        # NASA's model is built in metres (glTF's unit): 1.94 m tall.
        "scale_from": {
            "native": True,
            "page": "https://ntrs.nasa.gov/api/citations/20140009911/downloads/20140009911.pdf",
            "quote": "fit crewmembers who are sized in the medium ISS EMU HUT",
        },
        "materials": {
            "Baked": {
                "role": "baked_maps",
                "color": "Color",
                "bump": "Bump",
                "specular": "spec",
                "bump_strength": 6.0,
                # In the colour map's 4096 pixels: the NASA insignia and an emblem patch, filled from a plain patch
                # of the suit's own fabric, and in unused space the Advanced Concepts Lab's logo and credit line.
                "paint_out": [
                    {"box": (54, 3512, 214, 3678), "what": "an emblem patch", "fill_from": (1560, 3810, 1640, 3890)},
                    {"box": (208, 3450, 352, 3598), "what": "the NASA insignia", "fill_from": (1560, 3810, 1640, 3890)},
                    {"box": (3940, 2530, 4010, 3735), "what": "a credit line in unused texture space"},
                    {"box": (3830, 3720, 4096, 4012), "what": "a logo in unused texture space"},
                ],
            },
        },
        # The insignia and the emblem are decals of their own, shaped like the marks: their faces come out too.
        "drop_decals": [
            {"box": (54, 3512, 214, 3678), "texture_edge": 4096, "what": "an emblem patch"},
            {"box": (208, 3450, 352, 3598), "texture_edge": 4096, "what": "the NASA insignia"},
        ],
        "lod_ratio": {"lod0": 1.0, "lod1": 0.4, "lod2": 0.15},
        "body_radius_m": 0.8,
        "changes": "baked",
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
    {
        # Webb never travels between stars: no speed, and the design record's no-travel line (D26 item 5).
        "id": "jwst",
        "name": "James Webb Space Telescope",
        "model": "jwst",
        "travel": None,
        "facts": [
            {"key": "distanceFromEarthMillionKm", "value": 1.5, "page": "https://science.nasa.gov/mission/webb/", "quote": "1.5 million kilometers (1 million miles) away from the Earth"},
            {"key": "operatesAt", "value": "Sun-Earth L2", "page": "https://science.nasa.gov/mission/webb/", "quote": "1.5 million kilometers (1 million miles) away from the Earth"},
            {"key": "launch", "value": "2021-12-25", "page": "https://science.nasa.gov/mission/webb/fact-sheet/", "quote": "December 25, 2021 07:20am EST"},
            {"key": "primaryMirrorDiameterM", "value": 6.5, "page": "https://science.nasa.gov/mission/webb/fact-sheet/", "quote": "Diameter of Primary Mirror 6.5 m (21.3 ft) approximately"},
            {"key": "sunshieldLengthM", "value": 21.197, "page": "https://science.nasa.gov/mission/webb/fact-sheet/", "quote": "Size of Sun Shield 21.197 m x 14.162 m (69.5 ft x 46.5 ft)"},
            {"key": "sunshieldWidthM", "value": 14.162, "page": "https://science.nasa.gov/mission/webb/fact-sheet/", "quote": "Size of Sun Shield 21.197 m x 14.162 m (69.5 ft x 46.5 ft)"},
            {"key": "massKg", "value": 6200, "page": "https://science.nasa.gov/mission/webb/fact-sheet/", "quote": "Approx 6200 kg, including observatory, on-orbit consumables and launch vehicle adaptor"},
        ],
    },
    {
        "id": "pioneer-10",
        "name": "Pioneer 10",
        "model": "pioneer10",
        "travel": {
            "speedKmS": 12.24,
            "relativeTo": "Sun",
            "asOf": "2001-06-03",
            "page": "https://science.nasa.gov/missions/pioneer-10-update/",
            "quote": "Speed relative to the Sun: 12.24 km/sec (27,380 mph)",
        },
        "facts": [
            {"key": "launch", "value": "1972-03-02", "page": "https://science.nasa.gov/mission/pioneer-10/", "quote": "March 2, 1972 / 01:49:04 UT"},
            {"key": "lastSignal", "value": "2003-01-23", "page": "https://science.nasa.gov/mission/pioneer-10/", "quote": "Jan. 23, 2003: Pioneer 10's last signal is received on Earth"},
            {"key": "massKg", "value": 258, "page": "https://science.nasa.gov/mission/pioneer-10/", "quote": "Spacecraft Mass 569 pounds (258 kilograms)"},
            {"key": "heading", "value": "Aldebaran", "page": "https://science.nasa.gov/mission/pioneer-10/", "quote": "generally heading in the direction of the red star Aldebaran"},
            {"key": "dishDiameterM", "value": 2.74, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1972-012A", "quote": "2.74-m-diameter parabolic dish antenna that was 46 cm deep"},
        ],
    },
    {
        # A spacesuit is worn near a spacecraft, never flown between stars: no speed (D26 item 5).
        "id": "astronaut-emu",
        "name": "Extravehicular Mobility Unit",
        "model": "emu",
        "travel": None,
        "facts": [
            {"key": "massKg", "value": 145, "page": "https://www.nasa.gov/wp-content/uploads/2015/07/2019_07_ea_emu.pdf", "quote": "weighs approximately 319 lb (145 kg) on Earth"},
            {"key": "fits", "value": "5 ft to 6 ft 2 in", "page": "https://www.nasa.gov/wp-content/uploads/2015/07/2019_07_ea_emu.pdf", "quote": "95th percentile male (6'2\" tall"},
            {"key": "spacewalkHours", "value": "six to eight", "page": "https://www.nasa.gov/suits-and-rovers/spacesuits/", "quote": "each excursion typically six to eight hours in duration"},
            {"key": "firstUsed", "value": "Space Shuttle Program, 1980s", "page": "https://www.nasa.gov/suits-and-rovers/spacesuits/", "quote": "debuted during the Space Shuttle Program in the 1980s"},
        ],
    },
    {
        "id": "new-horizons",
        "name": "New Horizons",
        "model": "newhorizons",
        # NASA's mission page gives the speed as a distance a year, with no date; the km/s is that figure converted
        # (one mile 1.609344 km, one year 365.25 days), and the test checks the arithmetic.
        "travel": {
            "speedKmS": 15.3,
            "relativeTo": "Sun",
            "asOf": None,
            "page": "https://science.nasa.gov/mission/new-horizons/",
            "quote": "speeding away from the Earth and Sun at a rate of about 300 million miles per year",
            "derivedFrom": {"quoted": "300 million miles per year", "milesPerYear": 300_000_000},
        },
        "facts": [
            {"key": "launch", "value": "2006-01-19", "page": "https://science.nasa.gov/mission/new-horizons/", "quote": "Jan. 19, 2006 / 19:00:00 UT"},
            {"key": "massKg", "value": 478, "page": "https://science.nasa.gov/mission/new-horizons/", "quote": "Spacecraft Mass 1,054 pounds (478 kilograms)"},
            {"key": "dishDiameterM", "value": 2.1, "page": "https://www.nasa.gov/wp-content/uploads/2015/03/139889main_presskit12_05.pdf", "quote": "A 2.1-meter (83-inch) diameter antenna dish"},
        ],
    },
    {
        # A spacesuit is worn near a spacecraft, never flown between stars: no speed (D26 item 5).
        "id": "z2",
        "name": "Z-2",
        "model": "z2",
        "travel": None,
        "facts": [
            {"key": "what", "value": "NASA prototype spacesuit", "page": "https://photojournal.jpl.nasa.gov/catalog/PIA23978", "quote": "the Z-2, a prototype spacesuit"},
            {"key": "helmetInches", "value": "13 by 11", "page": "https://ntrs.nasa.gov/api/citations/20140009911/downloads/20140009911.pdf", "quote": "a 13” x 11” elliptical dome"},
        ],
    },
]
