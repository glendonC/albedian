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
            # NASA's render is a 14 kB picture on white, which the picker shows as a white square: rendered instead.
            "picker_render_reason": "white",
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
        # NASA's model units read as metres (glTF's unit): 1.94 m tall; the size is the model's own, not one NASA states.
        "scale_from": {
            "native": True,
            "size": "model",
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
                    {"box": (3940, 1740, 4010, 3735), "what": "a credit line in unused texture space"},
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
    "hubble": {
        "title": "Hubble Space Telescope",
        "kind": "craft",
        "source": {
            "id": "nasa3d-hubble-space-telescope-a",
            "title": "Hubble Space Telescope (A), NASA 3D Resources",
            "folder": "Hubble Space Telescope (A)",
            "file": "Hubble Space Telescope (A).glb",
            "bytes": 1_694_988,
            "sha256": "e5ba4de15c7d359ac8fa1ab7e286aff42dec09c0fadae3db99252587f39fa384",
            "picture": "Hubble Space Telescope (A).png",
            "picture_bytes": 673_364,
            "picture_sha256": "2c400cfa23cf6602202db163dea3c26e5b601aecb0ea50fb7d3407a1cb21c4ea",
            "local": "hubble-a",
        },
        "drop_objects": [],
        # NASA's model is in inches along +Z, the aperture door's end at +Z: forward is where the telescope looks.
        # (NASA's "Hubble Space Telescope (B)" arrives with its parts scattered and no materials, in NASA's own render
        # of it too, so (A) is used.)
        "forward": (0.0, 0.0, 1.0),
        "up": (0.0, 1.0, 0.0),
        "scale_from": {
            "material": "hbltel_1",
            "axis": "z",
            "metres": 13.3,
            "page": "https://science.nasa.gov/missions/hubble/a-nasa-refresher-on-hubble-facts-for-the-25th-anniversary/",
            "quote": "43.5 feet (13.3 m)",
        },
        "materials": {
            "hbltel_1": {"role": "photo_atlas"},
            "hbltel_2": {"role": "photo_atlas"},
            "hbltel_3": {"role": "photo_atlas"},
            "hbltel_4": {"role": "photo_atlas"},
            "hbltel_wfc_1": {"role": "photo_atlas"},
        },
        "body_radius_m": 7.0,
        "changes": "photo",
    },
    "apollo_lm": {
        "title": "Apollo Lunar Module",
        "kind": "craft",
        "source": {
            "id": "nasa3d-apollo-lunar-module",
            "title": "Apollo Lunar Module, NASA 3D Resources",
            "folder": "Apollo Lunar Module",
            "file": "Apollo Lunar Module.glb",
            "bytes": 716_840,
            "sha256": "379101dfcee399267addf771709107c35e826b0b9e5233272f123e4c8a585c4e",
            "picture": "Apollo Lunar Module.png",
            "picture_bytes": 1_124_858,
            "picture_sha256": "ceaaee11e4f5475462257287e73f3dcd547e3eab87810e48ffe51a7c66dcaf1f",
            "local": "apollo-lm",
        },
        "drop_objects": [],
        # Standing up +Z; its front (the ascent stage's windows and hatch, the flag panels) faces -Y.
        "forward": (0.0, -1.0, 0.0),
        "up": (0.0, 0.0, 1.0),
        # Its four legs lie on the import frame's axes, so the model's whole width is footpad end to footpad end.
        "scale_from": {
            "material": None,
            "axis": "x",
            "metres": 9.4,
            "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1969-059C",
            "quote": "The distance between the ends of the footpads on opposite landing legs was 9.4 m.",
        },
        # NASA's flat colours, except the parts NASA's model colours as gold foil, which become foil, and the two
        # photo panels (the flag and the words on the descent stage) handled as photo textures.
        "materials": {
            "blinn1SG.002": {"role": "pbr", "base": (1.0, 0.766, 0.336), "metallic": 1.0, "roughness": 0.32},
            "blinn9SG.001": {"role": "pbr", "base": (0.95, 0.62, 0.25), "metallic": 1.0, "roughness": 0.38},
            "blinn12SG.001": {"role": "photo_atlas"},
            "blinn8SG.001": {"role": "photo_atlas"},
            "blinn5SG.002": {"role": "photo_atlas"},
        },
        "material_rules": [{"match": [""], "role": "nasa_flat"}],
        "weld_m": 0.0001,
        "smooth_deg": 35.0,
        "lod_ratio": {"lod0": 0.6, "lod1": 0.2, "lod2": 0.07},
        "body_radius_m": 4.0,
        "changes": "flat",
    },
    "gemini": {
        "title": "Gemini",
        "kind": "craft",
        "source": {
            "id": "nasa3d-gemini",
            "title": "Gemini, NASA 3D Resources",
            "folder": "Gemini",
            "file": "Gemini.glb",
            "bytes": 1_387_812,
            "sha256": "e580e90ac2eb13d029e262e9d638c32980d7f8a48a6830d272ce080576110758",
            "picture": "Gemini.png",
            "picture_bytes": 196_026,
            "picture_sha256": "ff2de99df4f58b53b7d76d46442fdfe4420b8c1efc115ad70902c97b0529312a",
            "local": "gemini",
        },
        "drop_objects": [],
        # Standing nose up +Z on its adapter; its windows and hatches face -Y, made the top.
        "forward": (0.0, 0.0, 1.0),
        "up": (0.0, -1.0, 0.0),
        "scale_from": {
            "material": "lambert2SG",
            "axis": "x",
            "metres": 3.048,
            "quoted_cm": 304.8,
            "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1965-024A",
            "quote": "304.8 cm in diameter at the base",
        },
        "materials": {
            "lambert2SG": {"role": "photo_atlas"},
            "blinn3SG": {"role": "photo_atlas"},
            "blinn5SG": {"role": "photo_atlas"},
            "blinn6SG": {"role": "photo_atlas"},
            "blinn7SG": {"role": "photo_atlas"},
            "blinn2SG": {"role": "pbr", "base": (1.0, 0.766, 0.336), "metallic": 1.0, "roughness": 0.35},
        },
        "material_rules": [{"match": [""], "role": "nasa_flat"}],
        "weld_m": 0.0001,
        "smooth_deg": 35.0,
        "lod_ratio": {"lod0": 0.6, "lod1": 0.2, "lod2": 0.07},
        "body_radius_m": 3.0,
        "changes": "flat",
    },
    # The four suits NASA publishes in metres; each faces +Y in the import frame (visor and chest fittings
    # there). Mercury, Mark III and ACES export most materials as glass; Mark III's NASA insignia is a decal.
    "mercury_suit": {
        "title": "Mercury spacesuit",
        "kind": "suit",
        "source": {
            "id": "nasa3d-mercury-suit",
            "title": "Mercury Spacesuit, NASA 3D Resources",
            "folder": "Mercury Spacesuit",
            "file": "Mercury Spacesuit.glb",
            "page": "https://science.nasa.gov/3d-resources/mercury-spacesuit/",
            "bytes": 513884,
            "sha256": "406c835b4c91bb3f26e52ce5b79a7a1348cb2a22042744325e4be7826ecd7985",
            "local": "mercury-suit",
            "picture": "Mercury Spacesuit.png",
            "picture_bytes": 180249,
            "picture_sha256": "7e04bf7624c5f95090a4d0a42eeee3630e40d47f2ba3f25b3a70e2b544aef13a"
        },
        "drop_objects": [],
        "forward": [
            0.0,
            1.0,
            0.0
        ],
        "up": [
            0.0,
            0.0,
            1.0
        ],
        "scale_from": {
            "native": True,
            "size": "model",
            "page": "https://science.nasa.gov/3d-resources/mercury-spacesuit/",
            "quote": "NASA's first spacesuits were developed for the Mercury program."
        },
        "material_rules": [
            {
                "match": [
                    ""
                ],
                "role": "nasa_flat"
            }
        ],
        "weld_m": 0.0001,
        "smooth_deg": 40.0,
        "lod_ratio": {
            "lod0": 0.45,
            "lod1": 0.15,
            "lod2": 0.05
        },
        "body_radius_m": 0.8,
        "changes": "suit_flat"
    },
    "mark3": {
        "title": "Mark III spacesuit",
        "kind": "suit",
        "source": {
            "id": "nasa3d-mark3",
            "title": "Mark III Spacesuit, NASA 3D Resources",
            "folder": "Mark III Spacesuit",
            "file": "Mark III Spacesuit.glb",
            "page": "https://science.nasa.gov/3d-resources/mark-iii-spacesuit/",
            "bytes": 894092,
            "sha256": "885a4a83bd9d18f3886c8201845e384c0a9acb0cb487fb2897846d9f0438ab51",
            "local": "mark3",
            "picture": None,
            "picker_render_reason": "insignia"
        },
        "drop_objects": [],
        "forward": [
            0.0,
            1.0,
            0.0
        ],
        "up": [
            0.0,
            0.0,
            1.0
        ],
        "scale_from": {
            "native": True,
            "size": "model",
            "page": "https://science.nasa.gov/3d-resources/mark-iii-spacesuit/",
            "quote": "Both the JSC Mark III and the ARC AX-5 suit have been designed to operate at a pressure of 8.3 psi."
        },
        "material_rules": [
            {
                "match": [
                    ""
                ],
                "role": "nasa_flat"
            }
        ],
        "weld_m": 0.0001,
        "smooth_deg": 40.0,
        "lod_ratio": {
            "lod0": 0.45,
            "lod1": 0.15,
            "lod2": 0.05
        },
        "body_radius_m": 0.8,
        "changes": "suit_flat",
        "drop_materials": [
            "MKIII:MarkIII_8sizedwithoutskeleton_lambert5SG"
        ]
    },
    "aces": {
        "title": "Advanced Crew Escape Suit",
        "kind": "suit",
        "source": {
            "id": "nasa3d-aces",
            "title": "Advanced Crew Escape Suit, NASA 3D Resources",
            "folder": "Advanced Crew Escape Suit",
            "file": "Advanced Crew Escape Suit.glb",
            "page": "https://science.nasa.gov/3d-resources/advanced-crew-escape-suit/",
            "bytes": 858408,
            "sha256": "6017772d557c30ea86e2e3a7651ad2089499f3d0c577b983467650616bfaebeb",
            "local": "aces",
            "picture": "Advanced Crew Escape Suit.png",
            "picture_bytes": 1063336,
            "picture_sha256": "f28f8d53377124e7e62c73f8bd90a0efcdee8a5a7955ce591c4ef676d0f22d66"
        },
        "drop_objects": [],
        "forward": [
            0.0,
            1.0,
            0.0
        ],
        "up": [
            0.0,
            0.0,
            1.0
        ],
        "scale_from": {
            "native": True,
            "size": "model",
            "page": "https://science.nasa.gov/3d-resources/advanced-crew-escape-suit/",
            "quote": "The Advanced Crew Escape Suit (ACES) is a full pressure vessel providing an atmosphere of protection."
        },
        "material_rules": [
            {
                "match": [
                    ""
                ],
                "role": "nasa_flat"
            }
        ],
        "weld_m": 0.0001,
        "smooth_deg": 40.0,
        "lod_ratio": {
            "lod0": 0.45,
            "lod1": 0.15,
            "lod2": 0.05
        },
        "body_radius_m": 0.8,
        "changes": "suit_flat"
    },
    "gemini_suit": {
        "title": "Gemini spacesuit",
        "kind": "suit",
        "source": {
            "id": "nasa3d-gemini-suit",
            "title": "Gemini Spacesuit, NASA 3D Resources",
            "folder": "Gemini Spacesuit",
            "file": "Gemini Spacesuit.glb",
            "page": "https://science.nasa.gov/3d-resources/gemini-spacesuit/",
            "bytes": 2876516,
            "sha256": "972096ada3f5f4bfb54cd636e103f096f4ecf8d5bbc7dae40778c0185fa82266",
            "local": "gemini-suit",
            "picture": "Gemini Spacesuit.png",
            "picture_bytes": 207773,
            "picture_sha256": "cc7a7d556a4f58026be956a92ab1b786c05336a8ab58d8f1b40a1b26794627b7"
        },
        "drop_objects": [],
        "forward": [
            0.0,
            1.0,
            0.0
        ],
        "up": [
            0.0,
            0.0,
            1.0
        ],
        "scale_from": {
            "native": True,
            "size": "model",
            "page": "https://science.nasa.gov/3d-resources/gemini-spacesuit/",
            "quote": "The Gemini spacesuit had a combination of a pressure bladder and a link-net restraint layer that made the whole suit flexible when pressurized."
        },
        "material_rules": [
            {
                "match": [
                    ""
                ],
                "role": "nasa_flat"
            }
        ],
        "weld_m": 0.0001,
        "smooth_deg": 40.0,
        "lod_ratio": {
            "lod0": 0.45,
            "lod1": 0.15,
            "lod2": 0.05
        },
        "body_radius_m": 0.8,
        "changes": "suit_flat"
    },
    "shuttle": {
        "title": "Space Shuttle",
        "kind": "craft",
        "source": {
            "id": "nasa3d-space-shuttle-d",
            "title": "Space Shuttle (D), NASA 3D Resources",
            "folder": "Space Shuttle (D)",
            "file": "Space Shuttle (D).glb",
            "page": "https://science.nasa.gov/3d-resources/space-shuttle-d/",
            "bytes": 2_471_932,
            "sha256": "d0015681ff092d36062fdfed78e2bd1ca0f86e90862840d25bae11c686ce3549",
            # NASA's own render of this model shows the NASA insignia on the wing, which the rebuild covers.
            "picture": None,
            "picker_render_reason": "insignia",
            "local": "shuttle",
        },
        "drop_objects": [],
        # The nose is +X in the import frame, the tail fin points -Y and the wings span Z.
        "forward": (1.0, 0.0, 0.0),
        "up": (0.0, -1.0, 0.0),
        # The model's whole length, nose to tail, to NASA's 122 feet.
        "scale_from": {
            "material": None,
            "axis": "x",
            "metres": 37.1856,
            "quoted_ft": 122,
            "page": "https://www.nasa.gov/reference/the-space-shuttle/",
            "quote": "Length 122 feet",
        },
        "materials": {
            "wingtop": {"role": "photo_atlas", "paint_out": [{"box": (36, 130, 96, 206), "what": "the NASA insignia on the wing"}]},
            "side prt": {"role": "photo_atlas", "paint_out": [{"box": (88, 38, 114, 62), "what": "the NASA insignia on the side"}]},
            "side stb": {"role": "photo_atlas", "paint_out": [{"box": (86, 38, 110, 62), "what": "the NASA insignia on the side"}]},
            "nose top": {"role": "photo_atlas"},
            "belly": {"role": "photo_atlas"},
            "fusolage aft eng": {"role": "photo_atlas"},
            "shut-bay": {"role": "photo_atlas"},
            "tail": {"role": "photo_atlas"},
            "OMS pod stb": {"role": "photo_atlas"},
            "OMS pod prt": {"role": "photo_atlas"},
            "wing flap top": {"role": "photo_atlas"},
        },
        "material_rules": [{"match": [""], "role": "nasa_flat"}],
        "weld_m": 0.0001,
        "smooth_deg": 30.0,
        "lod_ratio": {"lod0": 1.0, "lod1": 0.5, "lod2": 0.18},
        # Eleven 640 px photo textures: at 512 and 256 px the chase and far levels stay within their budgets.
        "texture_edge": {"lod1": 512, "lod2": 256},
        "body_radius_m": 14.0,
        "changes": "flat",
    },
    "iss": {
        "title": "International Space Station",
        "kind": "craft",
        "source": {
            "id": "nasa3d-international-space-station-d-igoal",
            "title": "International Space Station (ISS) (D) (IGOAL), NASA 3D Resources",
            "folder": "International Space Station (ISS) (D) (IGOAL)",
            "file": "International Space Station (ISS).glb",
            "bytes": 95_875_648,
            "sha256": "57cecfaf332efb2127d07796c4b65ca570d95a11516c8e046feb5a12efee491d",
            "picture": "International Space Station (ISS) (D) (IGOAL).png",
            "picture_bytes": 1_628_716,
            "picture_sha256": "cb4800bf76b9ab76c44fd0ecea7ec47c4230bd3293b8bda6626ebb8d1128493a",
            "local": "iss",
        },
        "drop_objects": [],
        # The pressurised modules line up along X in the import frame and the truss along Y: forward along the
        # modules, up +Z, so the chase camera sees the station's length ahead and the arrays to either side.
        "forward": (1.0, 0.0, 0.0),
        "up": (0.0, 0.0, 1.0),
        # The model's whole extent along the truss to NASA's end-to-end figure.
        "scale_from": {
            "material": None,
            "axis": "y",
            "metres": 109,
            "page": "https://www.nasa.gov/international-space-station/space-station-facts-and-figures/",
            "quote": "The space station is 356 feet (109 meters) end-to-end",
        },
        # Sixty parts, each with NASA's colour, metal and normal maps up to 4096 px; several modules' maps carry NASA,
        # ESA and JAXA marks. Each part takes the mean of its maps (no mark survives an average), keeping NASA's alpha
        # where it cuts a lattice.
        "material_rules": [{"match": [""], "role": "maps_mean"}],
        "strip_unused_layers": True,
        "smooth_deg": 30.0,
        # Thousands of small parts set a floor under decimation: the far levels leave out those under a size.
        "lod_ratio": {"lod0": 0.03, "lod1": 0.008, "lod2": 0.0025},
        "drop_parts_under_m": {"lod1": 0.5, "lod2": 3.0},
        "budget_bytes": {"lod0": 8_000_000},
        "texture_edge": {"lod1": 256, "lod2": 128},
        "body_radius_m": 30.0,
        "changes": "mean",
    },
    "parker": {
        "title": "Parker Solar Probe",
        "kind": "craft",
        "source": {
            "id": "nasa3d-parker-solar-probe",
            "title": "Parker Solar Probe, NASA 3D Resources",
            "folder": "Parker Solar Probe",
            "file": "Parker Solar Probe.glb",
            "bytes": 443_572,
            "sha256": "4d26e84efd29a78c5d07e474f6272bb111668b58da5a7555e305a65c72ddb1c5",
            "picture": "Parker Solar Probe.png",
            "picture_bytes": 604_383,
            "picture_sha256": "c62a1851d27fcb6985722a3cd7249fde654fe72f385362c770cc4a935513724e",
            "local": "parker",
        },
        # The source's unit cube at the root has no material and is not part of the craft.
        "drop_objects": ["_root"],
        # The heat shield's two flat faces lie across +Y in the import frame (the front one highest, at the top of the
        # model), the bus and the magnetometer boom behind it along -Y, the solar wings along X. Flown shield first,
        # as it meets the Sun; the wings stay level, so +Z is up.
        "forward": (0.0, 1.0, 0.0),
        "up": (0.0, 0.0, 1.0),
        # Scale from the heat shield's front face: the textured faces of that material lying in the shield's plane.
        # NASA's model is close to metres already (that face 2.25 by 2.33 units, the shield 0.125 units thick
        # against NSSDC's 11.43 cm), so the scale is near one.
        "scale_from": {
            "material": "PSP",
            "band": {"axis": "y", "min": 1.47, "max": 1.49},
            "axis": "z",
            "metres": 2.3,
            "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2018-065A",
            "quote": "the end of the TPS is 2.3 m in diameter.",
        },
        "materials": {
            "foil_silver": {"role": "pbr", "base": (0.80, 0.80, 0.80), "metallic": 1.0, "roughness": 0.35},
            # Solar cells, panels and the shield's white face in one texture: none of them metal.
            "PSP": {"role": "textured", "metallic": 0.0, "roughness": 0.45},
        },
        "material_rules": [{"match": [""], "role": "nasa_flat"}],
        "weld_m": 0.0001,
        "smooth_deg": 35.0,
        "body_radius_m": 1.6,
        "changes": "foil",
    },
    "juno": {
        "title": "Juno",
        "kind": "craft",
        "source": {
            "id": "nasa3d-juno-a",
            "title": "Juno (A), NASA 3D Resources",
            "folder": "Juno (A)",
            "file": "Juno (A).glb",
            "bytes": 8_999_028,
            "sha256": "13f6bd48ff830a1890b21ef6e0182bb1043926feebb77af9733e7374b050367a",
            "picture": "Juno (A).png",
            "picture_bytes": 877_228,
            "picture_sha256": "50103ebfff182051bb6c01f0bd25af3c4e2e1cc5453fec6b9cceff6d165c18c6",
            "local": "juno",
        },
        "drop_objects": [],
        # The dish's top surface is in NASA's file twice, 7 mm apart, the second copy under this material.
        "drop_materials": ["new"],
        # The high-gain dish faces +Z in the import frame and faces Earth, so the craft is flown along -Z, as Voyager
        # is; the three solar wings lie in the XY plane, so +Y is up.
        "forward": (0.0, 0.0, -1.0),
        "up": (0.0, 1.0, 0.0),
        # Scale from the bus: the silver foil of its hexagonal bottom deck (the faces of that material in a slab across
        # z; the top deck's slab also holds foil on the wing booms), 3.4065 units corner to corner (a regular hexagon,
        # 2.95 across the flats), to NSSDC's 3.5 m diameter; the wings then span 19.1 m, NASA's "approximately 20 m".
        "scale_from": {
            "material": "foil_silver",
            "band": {"axis": "z", "min": -1.39, "max": -1.36},
            "axis": "y",
            "metres": 3.5,
            "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2011-040A",
            "quote": "The spacecraft is built around a hexagonal cylinder bus measuring 3.5 m in diameter by 3.5 m high.",
        },
        "materials": {
            "foil_gold": {"role": "photo_atlas"},
            "foil_silver": {"role": "photo_atlas"},
            "foil_ant": {"role": "photo_atlas"},
            "black_krinkle": {"role": "photo_atlas"},
            "tex_01": {"role": "photo_atlas"},
            # The solar cells, under cover glass: no metalness, fairly smooth.
            "solar_panels": {"role": "textured", "metallic": 0.0, "roughness": 0.25},
            "shiny_panels": {"role": "textured", "metallic": 0.0, "roughness": 0.25},
        },
        # A perforated panel NASA cuts with its texture's alpha: the mean of its map, the alpha kept as a mask.
        "material_rules": [{"match": ["tex_01_a"], "role": "maps_mean"}, {"match": [""], "role": "nasa_flat"}],
        "weld_m": 0.0001,
        "smooth_deg": 35.0,
        # Its foil and panel maps each get their own metal, roughness and relief maps: smaller far levels keep the budget.
        "texture_edge": {"lod1": 512, "lod2": 256},
        "body_radius_m": 2.0,
        "changes": "atlas",
    },
    "dawn": {
        "title": "Dawn",
        "kind": "craft",
        "source": {
            "id": "nasa3d-dawn",
            "title": "Dawn, NASA 3D Resources",
            "folder": "Dawn",
            "file": "Dawn.glb",
            "bytes": 927_020,
            "sha256": "411249aed791994b6a2014894ad6bd7ca0445dbcf6d5342af9edc5df988f04b7",
            "picture": "Dawn.png",
            "picture_bytes": 1_118_264,
            "picture_sha256": "2a5c7d395136787354d2155f2a826888c596b912d55fd4ec597a8ad588bcc673",
            "local": "dawn",
        },
        # The source's unit cube at the root has no material and is not part of the craft.
        "drop_objects": ["_root"],
        # The ion thrusters' rings sit at -Z in the import frame and push the craft along +Z; the dish faces -Y, so -Y
        # is up and the dish rides on top.
        "forward": (0.0, 0.0, 1.0),
        "up": (0.0, -1.0, 0.0),
        # The model's whole width along X is the two wings tip to tip: 19.6985 units to NSSDC's 19.7 m. NASA's model
        # draws the dish 1.2407 units across against NSSDC's 1.52 m, so the dish is not true to size at this scale.
        "scale_from": {
            "material": None,
            "axis": "x",
            "metres": 19.7,
            "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2007-043A",
            "quote": "Two solar panel wings extend 19.7 m tip-to-tip",
        },
        "materials": {
            # Black Kapton blanket and silver foil, as NASA's model names them (NASA gives both a grey ramp).
            "black_krinkle": {"role": "pbr", "base": (0.03, 0.03, 0.03), "metallic": 0.0, "roughness": 0.6},
            "foil_silver": {"role": "pbr", "base": (0.80, 0.80, 0.80), "metallic": 1.0, "roughness": 0.35},
            "foil_silver_2": {"role": "pbr", "base": (0.80, 0.80, 0.80), "metallic": 1.0, "roughness": 0.35},
            "foil_gold": {"role": "photo_atlas"},
            "tex_01": {
                "role": "photo_atlas",
                # A maker's two-line label printed on a photographed panel, illegible at the source's size.
                "paint_out": [{"box": (376, 345, 420, 359), "what": "a printed label"}],
            },
        },
        "material_rules": [{"match": [""], "role": "nasa_flat"}],
        "weld_m": 0.0001,
        "smooth_deg": 35.0,
        "body_radius_m": 1.5,
        "changes": "dawn",
    },
    "messenger": {
        "title": "MESSENGER",
        "kind": "craft",
        "source": {
            "id": "nasa3d-messenger",
            "title": "MErcury Surface, Space ENvironment, GEochemistry, and Ranging (MESSENGER), NASA 3D Resources",
            "folder": "MErcury Surface, Space ENvironment, GEochemistry, and Ranging (MESSENGER)",
            "file": "MErcury Surface, Space ENvironment, GEochemistry, and Ranging (MESSENGER).glb",
            "bytes": 2_673_396,
            "sha256": "c0268bfed0910e68a54c45925aea8edfc6e479d8131a849de7949252e01b9875",
            "picture": "MErcury Surface, Space ENvironment, GEochemistry, and Ranging (MESSENGER).png",
            "picture_bytes": 1_015_487,
            "picture_sha256": "f78316f866e36c3de93d0475bad86682d8bd05be2e02432b60a3f188b7d629f1",
            "local": "messenger",
        },
        # The source's unit cube at the root has no material and is not part of the craft.
        "drop_objects": ["_root"],
        # The sunshade stands at +X in the import frame, the magnetometer boom reaching back along -X, the wings along
        # Y: flown shade first, as it meets the Sun, with +Z up.
        "forward": (1.0, 0.0, 0.0),
        "up": (0.0, 0.0, 1.0),
        # Scale from the sunshade's height: 2.6395 units to NSSDC's 2.5 m; its width, 2.1130 units, is then 2.00 m,
        # NSSDC's 2 m, an independent check.
        "scale_from": {
            "material": "AO_heatshield",
            "axis": "z",
            "metres": 2.5,
            "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2004-030A",
            "quote": "a semi-cylindrical thermal shade (roughly 2.5 meters tall and 2 meters wide)",
        },
        "materials": {
            "Mess_tex_01": {
                "role": "photo_atlas",
                # A small dark decal beside the flag, illegible at the source's size.
                "paint_out": [{"box": (200, 160, 231, 208), "what": "a small decal"}],
            },
            "Mess_tex_02": {"role": "photo_atlas"},
            "Mess_tex_black": {"role": "photo_atlas"},
            "Mess_tex_darkgrey": {"role": "photo_atlas"},
            "foil_gold": {"role": "photo_atlas"},
            "foil_silver": {"role": "pbr", "base": (0.80, 0.80, 0.80), "metallic": 1.0, "roughness": 0.35},
            # The sunshade is white ceramic cloth; NASA's baked shading moves to the occlusion slot.
            "AO_heatshield": {"role": "painted", "base": (0.85, 0.85, 0.83), "roughness": 0.8, "occlusion_from_source": True},
            "AO_other": {"role": "painted", "base": (0.62, 0.62, 0.62), "roughness": 0.5, "occlusion_from_source": True},
            "AO_other2": {"role": "painted", "base": (0.62, 0.62, 0.62), "roughness": 0.5, "occlusion_from_source": True},
        },
        "material_rules": [{"match": [""], "role": "nasa_flat"}],
        "body_radius_m": 1.5,
        "changes": "photo",
    },
    "spitzer": {
        "title": "Spitzer Space Telescope",
        "kind": "craft",
        "source": {
            "id": "nasa3d-spitzer-space-telescope",
            "title": "Spitzer Space Telescope, NASA 3D Resources",
            "folder": "Spitzer Space Telescope",
            "file": "Spitzer Space Telescope.glb",
            "bytes": 251_388,
            "sha256": "fa0b7c89fd2c8632d981d3a0ad471dc70620dda4e663788409b251e0f6e6f558",
            "picture": "Spitzer Space Telescope.png",
            "picture_bytes": 394_932,
            "picture_sha256": "b2891a22d44f6bab013c81a5cc2c51481788a26ec0d0337a11da40f0b256fa9a",
            "local": "spitzer",
        },
        "drop_objects": [],
        # The telescope's tube points along +Z in the import frame and its solar panel stands on the +Y side: flown
        # tube first, panel on top.
        "forward": (0.0, 0.0, 1.0),
        "up": (0.0, 1.0, 0.0),
        # Scale from the telescope's mirror, NASA's "Mirror" part, 0.8740 units across, to NSSDC's 85 cm aperture.
        "scale_from": {
            "material": "Mirror.001",
            "axis": "x",
            "metres": 0.85,
            "quoted_cm": 85,
            "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2003-038A",
            "quote": "The telescope has an 85 cm diameter aperture.",
        },
        # NASA's model names each part's material by what it is: the metals, foils, mirror and cells get physically
        # based materials of that kind; the rest keep NASA's own colour.
        "material_rules": [
            {"match": ["edges"], "role": "nasa_flat"},
            {"match": ["solar cells"], "role": "pbr", "base": (0.02, 0.025, 0.06), "metallic": 0.0, "roughness": 0.15},
            {"match": ["mirror"], "role": "pbr", "base": (0.56, 0.57, 0.58), "metallic": 1.0, "roughness": 0.08},
            {"match": ["gold", "brass"], "role": "pbr", "base": (1.0, 0.766, 0.336), "metallic": 1.0, "roughness": 0.3},
            {"match": ["copper"], "role": "pbr", "base": (0.95, 0.64, 0.54), "metallic": 1.0, "roughness": 0.35},
            {"match": ["foil silver", "silver core"], "role": "pbr", "base": (0.90, 0.90, 0.91), "metallic": 1.0, "roughness": 0.34},
            {"match": ["chrome", "brushed metal", "bolts"], "role": "pbr", "base": (0.91, 0.92, 0.92), "metallic": 1.0, "roughness": 0.3},
            {"match": [""], "role": "nasa_flat"},
        ],
        "smooth_deg": 35.0,
        "body_radius_m": 2.2,
        "changes": "metals",
    },
    "osirisrex": {
        "title": "OSIRIS-REx",
        "kind": "craft",
        "source": {
            "id": "nasa3d-osiris-rex",
            "title": "OSIRIS-REx, NASA 3D Resources",
            "folder": "Origins, Spectral Interpretation, Resource Identification, and Security - Regolith Explorer (OSIRIS-REx)",
            "file": "OSIRIS-REx.glb",
            "bytes": 1_281_308,
            "sha256": "ef8e0429ee4dd8e918908923d5efdc6d3576b9216cc8e16533b50dd28c196bca",
            "picture": "OSIRIS-REx.png",
            "picture_bytes": 941_876,
            "picture_sha256": "9b02b5f685379bb8a7770b2e1c2033d443e78dfd4910f095e6f0f923455af63a",
            "local": "osirisrex",
        },
        "drop_objects": [],
        # The high-gain dish faces +X in the import frame and faces Earth, so the craft is flown along -X, as Voyager is;
        # the sample return capsule sits on top at +Z and the wings reach along Y.
        "forward": (-1.0, 0.0, 0.0),
        "up": (0.0, 0.0, 1.0),
        # Scale from the dish's white face, NASA's "White-sm" part, 8.3365 units across, to NSSDC's 2 m.
        "scale_from": {
            "material": "White-sm.001",
            "axis": "y",
            "metres": 2,
            "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2016-055A",
            "quote": "Primary communication is via a 2 meter high gain directional dish antenna.",
        },
        # NASA's model names each part's colour and finish: gold foil, bronze, silver and solar cells become physically
        # based materials of that kind in NASA's own colours where NASA gives one; the rest keep NASA's own colour.
        "material_rules": [
            {"match": ["solarfaces"], "role": "pbr", "base": (0.02, 0.025, 0.06), "metallic": 0.0, "roughness": 0.15},
            {"match": ["goldfoil", "tpp-botplates"], "role": "pbr", "base": (1.0, 0.766, 0.336), "metallic": 1.0, "roughness": 0.3},
            {"match": ["bronze", "silver-puck"], "role": "pbr", "base": (0.692, 0.609, 0.384), "metallic": 1.0, "roughness": 0.35},
            {"match": ["silver"], "role": "pbr", "base": (0.90, 0.90, 0.91), "metallic": 1.0, "roughness": 0.34},
            {"match": [""], "role": "nasa_flat"},
        ],
        # NASA's file keeps every triangle apart (553,221 vertices for 184,407 triangles): welded within 0.1 mm first.
        "weld_m": 0.0001,
        "smooth_deg": 35.0,
        "lod_ratio": {"lod0": 0.7, "lod1": 0.2, "lod2": 0.06},
        "body_radius_m": 1.6,
        "changes": "foils",
    },
    "mro": {
        "title": "Mars Reconnaissance Orbiter",
        "kind": "craft",
        "source": {
            "id": "nasa3d-mro-a",
            "title": "Mars Reconnaissance Orbiter (MRO) (A), NASA 3D Resources",
            "folder": "Mars Reconnaissance Orbiter (MRO) (A)",
            "file": "Mars Reconnaissance Orbiter (MRO) (A).glb",
            "bytes": 2_652_460,
            "sha256": "b195c566d227dec8e42ca2fafcc2a8cc26a127e04ed577e4b9515199d2002d9e",
            "picture": "Mars Reconnaissance Orbiter (MRO) (A).png",
            "picture_bytes": 1_026_954,
            "picture_sha256": "529bc6463d2b3436ba1d0d692db24afa83dc4dc822fde893647edd621b0f1bb9",
            "local": "mro",
        },
        "drop_objects": [],
        # The high-gain dish stands on its mast at -X in the import frame and faces Earth, so the craft is flown along
        # +X, away from it, as Voyager is; the dish rides high at +Z and the wings reach along Y.
        "forward": (1.0, 0.0, 0.0),
        "up": (0.0, 0.0, 1.0),
        # Scale from the dish, NASA's "cover" part (rendered alone to be sure it is the dish), 23.8687 units across, to
        # NSSDC's 3 m.
        "scale_from": {
            "material": "cover",
            "axis": "y",
            "metres": 3,
            "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2005-029A",
            "quote": "primarily through a 3 m diameter steerable high-gain dish antenna",
        },
        # Every part samples NASA's one texture (gold foil, the silver dish, panels): one photo atlas for all of them;
        # the solar cells keep the texture with no metalness.
        "material_rules": [
            {"match": ["solar"], "role": "textured", "metallic": 0.0, "roughness": 0.25},
            {"match": ["cover", "default", "foil", "lens", "parts", "pole", "rocket", "tower"], "role": "photo_atlas"},
            {"match": [""], "role": "nasa_flat"},
        ],
        "smooth_deg": 35.0,
        "lod_ratio": {"lod0": 0.5, "lod1": 0.15, "lod2": 0.05},
        "body_radius_m": 2.5,
        "changes": "mro",
    },
    "stardust": {
        "title": "Stardust",
        "kind": "craft",
        "source": {
            "id": "nasa3d-stardust",
            "title": "Stardust, NASA 3D Resources",
            "folder": "Stardust",
            "file": "Stardust.glb",
            "bytes": 3_800_544,
            "sha256": "1293ce064d66ae0ae288a4278ec2801b22a1a8e4858fe881382c6b1451ab9741",
            "picture": "Stardust.png",
            "picture_bytes": 748_987,
            "picture_sha256": "71f48b85a613db2c2c6c270769b11cf14ecc2b00debd41f03038b02cfe97f468",
            "local": "stardust",
        },
        "drop_objects": [],
        # The two solar wings reach along Y in the import frame, their cells facing +Z. NASA's file marks no front, so the
        # craft is flown along +X with its wings level and +Z up (a choice of view, not a sourced attitude).
        "forward": (1.0, 0.0, 0.0),
        "up": (0.0, 0.0, 1.0),
        # Scale from the wings: NASA's "shiny_panels" part, 4.7175 units tip to tip, to NSSDC's 4.8 m. A check: the
        # silver disc on the sample capsule, 0.8102 units across, is then 0.82 m against NSSDC's 0.8 m capsule.
        "scale_from": {
            "material": "shiny_panels",
            "axis": "y",
            "metres": 4.8,
            "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1999-003A",
            "quote": "Two long (4.8 m tip-to-tip) rectangular solar arrays",
        },
        "materials": {
            "Stardust_tex_01": {"role": "photo_atlas"},
            "Stardust_tex_02": {"role": "photo_atlas"},
            "Stardust_tex_03": {"role": "photo_atlas"},
            "gold_foil": {"role": "photo_atlas"},
            "gold_foil2": {"role": "photo_atlas"},
            "foil_silver.002": {"role": "pbr", "base": (0.80, 0.80, 0.80), "metallic": 1.0, "roughness": 0.35},
            # The wings' cells under cover glass: NASA's texture kept, no metalness.
            "shiny_panels": {"role": "textured", "metallic": 0.0, "roughness": 0.25},
        },
        "material_rules": [{"match": [""], "role": "nasa_flat"}],
        "smooth_deg": 35.0,
        "body_radius_m": 1.4,
        "changes": "photo_foil",
    },
    "ds1": {
        "title": "Deep Space 1",
        "kind": "craft",
        "source": {
            "id": "nasa3d-deep-space-1",
            "title": "Deep Space 1, NASA 3D Resources",
            "folder": "Deep Space 1",
            "file": "Deep Space 1.glb",
            "bytes": 934_676,
            "sha256": "86751b676520496d6508c31e3d4baeeb7aca01331fb1e1ad819bb50584c07c12",
            "picture": "Deep Space 1.png",
            "picture_bytes": 1_507_333,
            "picture_sha256": "721db235a2d806417872c954d4f9403a2bc69877dcac86186cb0b39688f8aea4",
            "local": "ds1",
        },
        "drop_objects": [],
        # The ion engine's gold-blanketed end is at -Z in the import frame and pushes the craft along +Z; the wings reach
        # along X, so +Y is up and the wings stay level.
        "forward": (0.0, 0.0, 1.0),
        "up": (0.0, 1.0, 0.0),
        # The model's whole width along X is the wings deployed, 11.8004 units, to NSSDC's "roughly 11.75 m" (NASA's
        # model is in metres at a scale of 0.9957).
        "scale_from": {
            "material": None,
            "axis": "x",
            "metres": 11.75,
            "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1998-061A",
            "quote": "span roughly 11.75 m when deployed",
        },
        "materials": {
            "DS1_tex_01": {
                "role": "photo_atlas",
                # The maker's two-line label on the photographed circuit panel Dawn's texture also holds, illegible.
                "paint_out": [{"box": (526, 346, 570, 360), "what": "a printed label"}],
            },
            "foil_gold": {"role": "photo_atlas"},
            "foil_silver": {"role": "pbr", "base": (0.80, 0.80, 0.80), "metallic": 1.0, "roughness": 0.35},
            "black_krinkle": {"role": "pbr", "base": (0.03, 0.03, 0.03), "metallic": 0.0, "roughness": 0.6},
            # The wings' cells under cover glass: NASA's texture kept, no metalness.
            "shiny_panel": {"role": "textured", "metallic": 0.0, "roughness": 0.25},
        },
        "material_rules": [{"match": [""], "role": "nasa_flat"}],
        "smooth_deg": 35.0,
        "body_radius_m": 1.4,
        "changes": "photo_foil_label",
    },
    "mgs": {
        "title": "Mars Global Surveyor",
        "kind": "craft",
        "source": {
            "id": "nasa3d-mars-global-surveyor",
            "title": "Mars Global Surveyor, NASA 3D Resources",
            "folder": "Mars Global Surveyor",
            "file": "Mars Global Surveyor.glb",
            "bytes": 1_921_268,
            "sha256": "8812c97f2c147e409e883d5196377d596df84de87c08c1453e9e3cfaf082052f",
            "picture": "Mars Global Surveyor.png",
            "picture_bytes": 475_903,
            "picture_sha256": "8746797697a48c34c2082b48cd48cb2961fc65ad2ec69a3acbc19a052d705c84",
            "local": "mgs",
        },
        "drop_objects": [],
        # The high-gain dish on its boom faces +X in the import frame, toward Earth, so the craft is flown along -X, as
        # Voyager is; the wings reach along Y and the body stands along Z, so +Z is up.
        "forward": (-1.0, 0.0, 0.0),
        "up": (0.0, 0.0, 1.0),
        # Scale from the dish, NASA's silver foil disc on the boom's end, 1.2826 units across, to NSSDC's 1.5 m. A check:
        # each wing's width, 1.5630 units, is then 1.83 m against NSSDC's panels "3.5 x 1.9 m".
        "scale_from": {
            "material": "foil_silver",
            "axis": "y",
            "metres": 1.5,
            "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1996-062A",
            "quote": "A 1.5 meter diameter parabolic high gain dish antenna",
        },
        "materials": {
            "tex": {
                "role": "photo_atlas",
                # A small label stuck on a photographed dark panel, illegible at the source's size.
                "paint_out": [{"box": (918, 897, 954, 916), "what": "a small label"}],
            },
            "black": {"role": "photo_atlas"},
            "dark grey": {"role": "photo_atlas"},
            "foil_gold": {"role": "photo_atlas"},
            "foil_silver": {"role": "pbr", "base": (0.80, 0.80, 0.80), "metallic": 1.0, "roughness": 0.35},
            "AO": {"role": "painted", "base": (0.62, 0.62, 0.62), "roughness": 0.5, "occlusion_from_source": True},
            # The wings' cells under cover glass: NASA's texture kept, no metalness.
            "shiny_panel": {"role": "textured", "metallic": 0.0, "roughness": 0.25},
        },
        "material_rules": [{"match": [""], "role": "nasa_flat"}],
        "body_radius_m": 1.5,
        "changes": "photo",
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
    {
        # Hubble orbits Earth and never travels between stars: no speed (D26 item 5).
        "id": "hubble",
        "name": "Hubble Space Telescope",
        "model": "hubble",
        "travel": None,
        "facts": [
            {"key": "launch", "value": "1990-04-24", "page": "https://science.nasa.gov/mission/hubble/", "quote": "APR 24, 1990"},
            {"key": "lengthM", "value": 13.3, "page": "https://science.nasa.gov/missions/hubble/a-nasa-refresher-on-hubble-facts-for-the-25th-anniversary/", "quote": "43.5 feet (13.3 m)"},
            {"key": "primaryMirrorDiameterM", "value": 2.4, "page": "https://science.nasa.gov/mission/hubble/overview/hubble-by-the-numbers/", "quote": "Primary mirror diameter 7.8 feet (2.4 m)"},
            {"key": "altitudeKm", "value": 483, "page": "https://science.nasa.gov/mission/hubble/overview/hubble-by-the-numbers/", "quote": "approximately 300 miles (483 km)"},
        ],
    },
    {
        # The Lunar Module flew between Moon orbit and the Moon's surface, never between stars: no speed.
        "id": "apollo-lm",
        "name": "Apollo Lunar Module",
        "model": "apollo_lm",
        "travel": None,
        "facts": [
            {"key": "firstLanding", "value": "1969-07-20", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1969-059C", "quote": "on 20 July 1969"},
            {"key": "footpadSpanM", "value": 9.4, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1969-059C", "quote": "The distance between the ends of the footpads on opposite landing legs was 9.4 m."},
            {"key": "descentStageWidthM", "value": 4.2, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1969-059C", "quote": "an octagonal prism 4.2 meters across and 1.7 m thick"},
            {"key": "ascentStageHeightM", "value": 2.8, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1969-059C", "quote": "approximately 2.8 m high"},
            {"key": "massKg", "value": 15103, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1969-059C", "quote": "The spacecraft mass of 15103 kg was the total mass of the LM ascent and descent stages including propellants (fuel and oxidizer)."},
        ],
    },
    {
        # Gemini flew in Earth orbit, never between stars: no speed.
        "id": "gemini",
        "name": "Gemini",
        "model": "gemini",
        "travel": None,
        "facts": [
            {"key": "firstCrewedLaunch", "value": "1965-03-23", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1965-024A", "quote": "Launch Date: 1965-03-23"},
            {"key": "baseDiameterCm", "value": 304.8, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1965-024A", "quote": "304.8 cm in diameter at the base"},
            {"key": "heightCm", "value": 573.6, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1965-024A", "quote": "giving a total height of 573.6 cm for the Gemini spacecraft"},
            {"key": "massKg", "value": 3236.9, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1965-024A", "quote": "Mass: 3236.9 kg"},
        ],
    },
    # A spacesuit is worn near a spacecraft, never flown between stars: no speed (D26 item 5).
    {
        "id": "mercury-suit",
        "name": "Mercury spacesuit",
        "model": "mercury_suit",
        "travel": None,
        "facts": [
            {
                "key": "what",
                "value": "NASA's first spacesuits, for the Mercury program",
                "page": "https://science.nasa.gov/3d-resources/mercury-spacesuit/",
                "quote": "NASA's first spacesuits were developed for the Mercury program."
            }
        ]
    },
    # A spacesuit is worn near a spacecraft, never flown between stars: no speed (D26 item 5).
    {
        "id": "mark-iii",
        "name": "Mark III spacesuit",
        "model": "mark3",
        "travel": None,
        "facts": [
            {
                "key": "pressurePsi",
                "value": 8.3,
                "page": "https://science.nasa.gov/3d-resources/mark-iii-spacesuit/",
                "quote": "Both the JSC Mark III and the ARC AX-5 suit have been designed to operate at a pressure of 8.3 psi."
            }
        ]
    },
    # A spacesuit is worn near a spacecraft, never flown between stars: no speed (D26 item 5).
    {
        "id": "aces",
        "name": "Advanced Crew Escape Suit",
        "model": "aces",
        "travel": None,
        "facts": [
            {
                "key": "what",
                "value": "a full pressure suit",
                "page": "https://science.nasa.gov/3d-resources/advanced-crew-escape-suit/",
                "quote": "The Advanced Crew Escape Suit (ACES) is a full pressure vessel providing an atmosphere of protection."
            }
        ]
    },
    # A spacesuit is worn near a spacecraft, never flown between stars: no speed (D26 item 5).
    {
        "id": "gemini-suit",
        "name": "Gemini spacesuit",
        "model": "gemini_suit",
        "travel": None,
        "facts": [
            {
                "key": "firstUSSpacewalk",
                "value": "1965-06-03",
                "page": "https://www.nasa.gov/suits-and-rovers/",
                "quote": "followed by NASA astronaut Ed White on June 3, 1965, during the Gemini 4 mission"
            }
        ]
    },
    {
        # The orbiter flew in Earth orbit, never between stars: no speed.
        "id": "shuttle",
        "name": "Space Shuttle",
        "model": "shuttle",
        "travel": None,
        "facts": [
            {"key": "firstLaunch", "value": "1981-04-12", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1981-034A", "quote": "Launch Date: 1981-04-12"},
            {"key": "lengthFt", "value": 122, "page": "https://www.nasa.gov/reference/the-space-shuttle/", "quote": "Length 122 feet"},
            {"key": "wingspanFt", "value": 78, "page": "https://www.nasa.gov/reference/the-space-shuttle/", "quote": "Wingspan 78 feet"},
        ],
    },
    {
        # The station orbits Earth, never travels between stars: no speed.
        "id": "iss",
        "name": "International Space Station",
        "model": "iss",
        "travel": None,
        "facts": [
            {"key": "lengthM", "value": 109, "page": "https://www.nasa.gov/international-space-station/space-station-facts-and-figures/", "quote": "The space station is 356 feet (109 meters) end-to-end"},
            {"key": "massKg", "value": "419,725", "page": "https://www.nasa.gov/international-space-station/space-station-facts-and-figures/", "quote": "Mass: 925,335 pounds (419,725 kilograms)"},
            {"key": "orbitMinutes", "value": 90, "page": "https://www.nasa.gov/international-space-station/space-station-facts-and-figures/", "quote": "orbiting Earth about every 90 minutes"},
            {"key": "occupiedSince", "value": "2000-11", "page": "https://www.nasa.gov/international-space-station/space-station-facts-and-figures/", "quote": "The space station has been continuously occupied since November 2000."},
        ],
    },
    {
        "id": "parker-solar-probe",
        "name": "Parker Solar Probe",
        "model": "parker",
        # Parker orbits the Sun: its speed at closest approach, its fastest, is a speed around the Sun and gives no trip
        # time to a star, so no travel (the design record's D35); NASA's figure stays a fact, in NASA's own words.
        "travel": None,
        "facts": [
            {"key": "speedAtClosestApproachKph", "value": "700,000", "page": "https://science.nasa.gov/mission/parker-solar-probe/", "quote": "At closest approach, Parker Solar Probe hurtles around the Sun at approximately 430,000 mph (700,000 kph)."},
            {"key": "launch", "value": "2018-08-12", "page": "https://science.nasa.gov/mission/parker-solar-probe/", "quote": "Aug. 12, 2018 / 7:31 UTC"},
            {"key": "massKg", "value": 685, "page": "https://science.nasa.gov/mission/parker-solar-probe/", "quote": "1,510 pounds (685 kilograms) at launch"},
            {"key": "heatShieldDiameterM", "value": 2.3, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2018-065A", "quote": "the end of the TPS is 2.3 m in diameter."},
            {"key": "heatShieldThicknessCm", "value": 11.43, "page": "https://science.nasa.gov/mission/parker-solar-probe/", "quote": "4.5-inch-thick (11.43 cm) carbon-composite shield"},
            {"key": "power", "value": "solar arrays, 384 W at solar encounter", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2018-065A", "quote": "The solar arrays produce 384 W power at solar encounter."},
        ],
    },
    {
        # Juno orbits Jupiter, never travels between stars: no speed. NASA's mission page calls it a "four-ton probe"
        # where NSSDC gives 1500 kg; NSSDC's figure is the one stated as a mass.
        "id": "juno",
        "name": "Juno",
        "model": "juno",
        "travel": None,
        "facts": [
            {"key": "launch", "value": "2011-08-05", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2011-040A", "quote": "Juno launched on 05 August 2011 at 16:25 UTC (12:25 p.m. EDT)"},
            {"key": "jupiterOrbit", "value": "2016-07-04", "page": "https://science.nasa.gov/mission/juno/", "quote": "the Juno spacecraft entered orbit around Jupiter on July 4, 2016"},
            {"key": "massKg", "value": 1500, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2011-040A", "quote": "Mass: 1500 kg"},
            {"key": "busDiameterM", "value": 3.5, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2011-040A", "quote": "The spacecraft is built around a hexagonal cylinder bus measuring 3.5 m in diameter by 3.5 m high."},
            {"key": "solarArrayLengthM", "value": 9, "page": "https://science.nasa.gov/mission/juno/", "quote": "each of its three solar arrays extend about 29.5 feet (9 meters) from the main body"},
            {"key": "spanM", "value": 20, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2011-040A", "quote": "giving a total diameter of approximately 20 m"},
            {"key": "power", "value": "solar panels, 18 kW at Earth and 400 W at Jupiter", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2011-040A", "quote": "The solar panels will produce a total of 18 kW at Earth and 400 W initially at Jupiter."},
        ],
    },
    {
        # Dawn ended in orbit around Ceres and NASA states no speed relative to the Sun for it: no speed.
        "id": "dawn",
        "name": "Dawn",
        "model": "dawn",
        "travel": None,
        "facts": [
            {"key": "launch", "value": "2007-09-27", "page": "https://science.nasa.gov/mission/dawn/technology/spacecraft/", "quote": "Launch Date: Sept. 27, 2007 | 11:34 UTC"},
            {"key": "dryMassKg", "value": 747.1, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2007-043A", "quote": "with a dry mass of 747.1 kg and a fueled launch mass of 1217.7 kg"},
            {"key": "launchMassKg", "value": 1217.7, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2007-043A", "quote": "with a dry mass of 747.1 kg and a fueled launch mass of 1217.7 kg"},
            {"key": "spanM", "value": 19.7, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2007-043A", "quote": "Two solar panel wings extend 19.7 m tip-to-tip"},
            {"key": "dishDiameterM", "value": 1.52, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2007-043A", "quote": "A parabolic fixed 1.52 m high gain dish antenna"},
            {"key": "propulsion", "value": "three ion thrusters", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2007-043A", "quote": "The ion propulsion consists of three ion thrusters"},
            {"key": "vestaArrival", "value": "2011-07-16", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2007-043A", "quote": "Dawn reached Vesta on 16 July 2011"},
            {"key": "ceresOrbit", "value": "2015-03-06", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2007-043A", "quote": "entered initial orbit around Ceres on 6 March 2015 at 12:29 UT"},
            {"key": "communicationsEnded", "value": "2018-10-31", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2007-043A", "quote": "communications ended on 31 October 2018"},
        ],
    },
    {
        # MESSENGER orbited Mercury and ended on its surface: no speed between stars. The mission page gives 1,107.9 kg
        # where NSSDC's description gives 1093 kg; NSSDC's is used, with its propellant share.
        "id": "messenger",
        "name": "MESSENGER",
        "model": "messenger",
        "travel": None,
        "facts": [
            {"key": "launch", "value": "2004-08-03", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2004-030A", "quote": "MESSENGER launched on 3 August 2004 at 6:15:56 UT (2:15:56 a.m. EDT)"},
            {"key": "massKg", "value": 1093, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2004-030A", "quote": "The total mass of the spacecraft is 1093 kg, 607.8 kg of this is propellant and helium."},
            {"key": "sunshadeHeightM", "value": 2.5, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2004-030A", "quote": "a semi-cylindrical thermal shade (roughly 2.5 meters tall and 2 meters wide)"},
            {"key": "sunshadeWidthM", "value": 2, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2004-030A", "quote": "a semi-cylindrical thermal shade (roughly 2.5 meters tall and 2 meters wide)"},
            {"key": "wingSpanM", "value": 6, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2004-030A", "quote": "two solar panel wings extending radially about 6 meters from tip to tip"},
            {"key": "mercuryOrbit", "value": "2011-03-18", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2004-030A", "quote": "Mercury orbit insertion took place with a 15 minute burn starting at 00:45 UT on 18 March 2011"},
            {"key": "impact", "value": "2015-04-30", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2004-030A", "quote": "The MESSENGER spacecraft impacted the surface of Mercury as planned on 30 April 2015 at 19:26 UT (3:26 p.m. EDT)."},
            {"key": "power", "value": "450 W in Mercury orbit", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2004-030A", "quote": "provides a nominal 450 W in Mercury orbit"},
        ],
    },
    {
        # Spitzer trailed Earth around the Sun and NASA states no speed for it: no speed. NSSDC gives its mass as 865 kg
        # where the mission page gives 950 kg; the mission page's figure is used, in its own words.
        "id": "spitzer",
        "name": "Spitzer Space Telescope",
        "model": "spitzer",
        "travel": None,
        "facts": [
            {"key": "launch", "value": "2003-08-25", "page": "https://science.nasa.gov/mission/spitzer/", "quote": "Aug. 25, 2003 / 05:35:39 UT"},
            {"key": "massKg", "value": 950, "page": "https://science.nasa.gov/mission/spitzer/", "quote": "2,094 pounds (950 kilograms)"},
            {"key": "apertureCm", "value": 85, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2003-038A", "quote": "The telescope has an 85 cm diameter aperture."},
            {"key": "orbit", "value": "Earth-trailing, around the Sun", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2003-038A", "quote": "Spitzer has an Earth-trailing Heliocentric orbit."},
            {"key": "decommissioned", "value": "2020-01-30", "page": "https://science.nasa.gov/mission/spitzer/", "quote": "was decommissioned on Jan. 30, 2020"},
        ],
    },
    {
        # OSIRIS-REx went to the asteroid Bennu and back, and flies on as OSIRIS-APEX; NASA states no speed relative to
        # the Sun for it: no speed. NSSDC gives 1529 kg at launch where the in-depth page gives 2,110 kg; NSSDC's,
        # stated as the launch mass with propellant, is used.
        "id": "osiris-rex",
        "name": "OSIRIS-REx",
        "model": "osirisrex",
        "travel": None,
        "facts": [
            {"key": "launch", "value": "2016-09-08", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2016-055A", "quote": "OSIRIS-REx launched on 08 September 2016 at 23:05 UT"},
            {"key": "launchMassKg", "value": 1529, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2016-055A", "quote": "Launch mass including propellant is 1529 kg."},
            {"key": "dishDiameterM", "value": 2, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2016-055A", "quote": "Primary communication is via a 2 meter high gain directional dish antenna."},
            {"key": "heightM", "value": 2.72, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2016-055A", "quote": "The spacecraft is a roughly cubic shape, 2.72 meters high and 3.1 meters wide"},
            {"key": "widthM", "value": 3.1, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2016-055A", "quote": "The spacecraft is a roughly cubic shape, 2.72 meters high and 3.1 meters wide"},
            {"key": "bennuArrival", "value": "2018-12-03", "page": "https://science.nasa.gov/mission/osiris-rex/in-depth/", "quote": "After arriving at Bennu on Dec. 3, 2018,"},
            {"key": "renamed", "value": "OSIRIS-APEX", "page": "https://science.nasa.gov/mission/osiris-rex/in-depth/", "quote": "After the delivery, OSIRIS-REx was renamed OSIRIS-APEX (OSIRIS-Apophis Explorer)"},
        ],
    },
    {
        # MRO orbits Mars: no speed between stars.
        "id": "mro",
        "name": "Mars Reconnaissance Orbiter",
        "model": "mro",
        "travel": None,
        "facts": [
            {"key": "launch", "value": "2005-08-12", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2005-029A", "quote": "Launch on an Atlas V-401 took place at 11:43 UT (7:43 a.m. EDT) on 12 August 2005 from Kennedy Space Center."},
            {"key": "launchMassKg", "value": 2180, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2005-029A", "quote": "The maximum spacecraft mass is 2180 kg, which includes 1149 kg of propellants."},
            {"key": "dishDiameterM", "value": 3, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2005-029A", "quote": "primarily through a 3 m diameter steerable high-gain dish antenna"},
            {"key": "solarArrayAreaM2", "value": 10, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2005-029A", "quote": "Each array has an area of 10 square meters and contains 3744 solar cells."},
            {"key": "marsOrbit", "value": "2006-03-10", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2005-029A", "quote": "Mars Reconnaissance Orbiter reached Mars and went into orbit on 10 March 2006."},
            {"key": "power", "value": "1000 W at Mars", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2005-029A", "quote": "The panels produce 1000 Watts at Mars"},
        ],
    },
    {
        # Stardust flew through a comet's dust and returned it; NASA states no speed relative to the Sun for it: no
        # speed. NSSDC's text gives 385 kg with capsule and propellant where its Facts in Brief box says 300 kg.
        "id": "stardust",
        "name": "Stardust",
        "model": "stardust",
        "travel": None,
        "facts": [
            {"key": "launch", "value": "1999-02-07", "page": "https://science.nasa.gov/mission/stardust/", "quote": "Feb. 7, 1999 / 21:04:15 UT"},
            {"key": "massKg", "value": 385, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1999-003A", "quote": "The total mass of the spacecraft including the return capsule and 85 kg of propellant is 385 kg."},
            {"key": "spanM", "value": 4.8, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1999-003A", "quote": "Two long (4.8 m tip-to-tip) rectangular solar arrays"},
            {"key": "busLengthM", "value": 1.6, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1999-003A", "quote": "a box-shaped main bus 1.6 m long, 0.66 m wide and 0.66 m deep"},
            {"key": "capsuleDiameterM", "value": 0.8, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1999-003A", "quote": "The short cone-shaped 0.8 m diameter, 0.5 m high, 46 kg sample reentry capsule"},
            {"key": "dishDiameterM", "value": 0.6, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1999-003A", "quote": "0.6 m diameter high gain dish"},
            {"key": "cometComa", "value": "2003-12-31", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1999-003A", "quote": "The spacecraft entered the coma of comet P/Wild 2 on 31 December 2003."},
        ],
    },
    {
        # Deep Space 1 tested an ion engine on its way to an asteroid and a comet; NASA states no speed relative to the
        # Sun for it: no speed. NSSDC's text gives 486.3 kg at launch where its Facts in Brief box says 373.7 kg.
        "id": "deep-space-1",
        "name": "Deep Space 1",
        "model": "ds1",
        "travel": None,
        "facts": [
            {"key": "launch", "value": "1998-10-24", "page": "https://science.nasa.gov/mission/deep-space-1/", "quote": "Oct. 24, 1998 / 12:08:00 UT"},
            {"key": "launchMassKg", "value": 486.3, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1998-061A", "quote": "The launch mass of the spacecraft is about 486.3 kg"},
            {"key": "spanM", "value": 11.75, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1998-061A", "quote": "span roughly 11.75 m when deployed"},
            {"key": "heightM", "value": 2.5, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1998-061A", "quote": "the spacecraft measures 2.5 m high, 2.1 m deep, and 1.7 m wide"},
            {"key": "ionEngineDiameterCm", "value": 30, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1998-061A", "quote": "The 30 cm diameter engine"},
            {"key": "cometComa", "value": "2001-09-22", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1998-061A", "quote": "On 22 September 2001, Deep Space 1 entered the coma of comet Borrelly"},
            {"key": "missionEnd", "value": "2001-12-18", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1998-061A", "quote": "The ion engines on DS1 were commanded off on 18 December 2001 at about 20:00 UT (3:00 p.m. EST) to end the mission."},
        ],
    },
    {
        # Mars Global Surveyor orbited Mars: no speed between stars. NSSDC dates the lost contact 2 November 2006 where
        # the mission page ends the mission on 14 November 2006; NSSDC's line is kept, in its own words.
        "id": "mars-global-surveyor",
        "name": "Mars Global Surveyor",
        "model": "mgs",
        "travel": None,
        "facts": [
            {"key": "launch", "value": "1996-11-07", "page": "https://science.nasa.gov/mission/mars-global-surveyor/", "quote": "Launch Date: Nov. 7, 1996 UTC"},
            {"key": "massKg", "value": 1030.5, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1996-062A", "quote": "Mass: 1030.5 kg"},
            {"key": "dishDiameterM", "value": 1.5, "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1996-062A", "quote": "A 1.5 meter diameter parabolic high gain dish antenna"},
            {"key": "solarPanels", "value": "two, each 3.5 by 1.9 m", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1996-062A", "quote": "Two solar panels, each 3.5 x 1.9 m in size"},
            {"key": "body", "value": "about 1.17 by 1.17 by 1.7 m", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1996-062A", "quote": "a rectangular box approximately 1.17 x 1.17 x 1.7 meters in size"},
            {"key": "marsOrbit", "value": "1997-09-12", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1996-062A", "quote": "was inserted into an elliptical capture orbit at 01:17 UT 12 September 1997"},
            {"key": "contactLost", "value": "2006-11-02", "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=1996-062A", "quote": "contact with the spacecraft was lost on 2 November 2006"},
        ],
    },
]
