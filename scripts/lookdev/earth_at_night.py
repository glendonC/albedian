"""Render a real Earth-at-night look-dev frame in Blender Cycles from NASA data.

Plan the moment first with `scripts/lookdev/plan_moment.py`, then from the repo root:
    blender -b --factory-startup -P scripts/lookdev/earth_at_night.py -- \
        --plan data/lookdev/plans/<plan>.json --out data/lookdev/renders/earth_at_night.png

World axes are ICRS (+X toward RA 0h, +Z toward the north celestial pole) and one unit is one
mean Earth radius (6371 km). Sunlight, the Moon's position and Earth's rotation come from the
plan. The atmosphere is a physically based volume (Rayleigh, Mie and ozone with exponential
density at real scale heights, coefficients after Bruneton 2017). City lights are NASA Black
Marble radiance mapped to emission, so their relative brightness is real; the exposure is a
camera choice, set for the night side the way orbital night photography is.
"""

import argparse
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Vector

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "data" / "lookdev" / "sources"
EARTH_RADIUS_M = 6_371_000.0
MOON_RADIUS = 1737.4 / 6371.0

# Sea-level coefficients in 1/m (Bruneton 2017), converted to 1/(Earth radius) below
RAYLEIGH_PER_M = (5.802e-6, 13.558e-6, 33.1e-6)
RAYLEIGH_SCALE_HEIGHT_M = 8_000.0
MIE_SCATTER_PER_M = 3.996e-6
MIE_ABSORB_PER_M = 4.40e-6
MIE_SCALE_HEIGHT_M = 1_200.0
OZONE_ABSORB_PER_M = (0.650e-6, 1.881e-6, 0.085e-6)
OZONE_CENTER_M, OZONE_HALF_WIDTH_M = 25_000.0, 15_000.0
ATMOSPHERE_TOP_M = 110_000.0
AIRGLOW_ALTITUDE_M, AIRGLOW_SIGMA_M = 95_000.0, 5_000.0
AIRGLOW_COLOR = (0.30, 1.0, 0.25, 1.0)  # oxygen 557.7 nm, in linear sRGB


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--res", default="2560x1440")
    p.add_argument("--samples", type=int, default=512)
    p.add_argument("--lens-mm", type=float, default=30.0)
    p.add_argument("--exposure", type=float, default=5.5, help="stops; the default exposes for city lights")
    p.add_argument("--bloom", type=float, default=0.25, help="strength of the compositor bloom on bright light")
    p.add_argument("--sun-irradiance", type=float, default=1361.0, help="W/m^2 at 1 AU")
    p.add_argument("--city-radiance", type=float, default=0.03, help="emission strength for the brightest Black Marble pixel")
    p.add_argument("--star-strength", type=float, default=0.05, help="multiplier on the linear Deep Star Map values")
    p.add_argument("--airglow-radiance", type=float, default=0.004, help="peak emission of the modeled 557.7 nm airglow layer")
    return p.parse_args(argv)


def node(tree, kind: str, loc=(0, 0), **props):
    n = tree.nodes.new(kind)
    n.location = loc
    for key, value in props.items():
        setattr(n, key, value)
    return n


def link(tree, out_socket, in_socket) -> None:
    tree.links.new(out_socket, in_socket)


def math_node(tree, op: str, a, b=None, loc=(0, 0)):
    """Math node fed by sockets or constants."""
    n = node(tree, "ShaderNodeMath", loc, operation=op)
    for socket, value in ((n.inputs[0], a), (n.inputs[1], b)):
        if value is None:
            continue
        if isinstance(value, (int, float)):
            socket.default_value = value
        else:
            link(tree, value, socket)
    return n.outputs[0]


def image_node(tree, path: Path, colorspace: str, loc=(0, 0)):
    n = node(tree, "ShaderNodeTexImage", loc, interpolation="Cubic", extension="REPEAT")
    n.image = bpy.data.images.load(str(path), check_existing=True)
    n.image.colorspace_settings.name = colorspace
    return n


def equirect_uv(tree, loc=(-1400, 0)):
    """Object-space direction to equirectangular (u, v): u = 0.5 at +X (longitude 0), v = 1 at +Z."""
    x, y = loc
    tc = node(tree, "ShaderNodeTexCoord", (x, y))
    nrm = node(tree, "ShaderNodeVectorMath", (x + 180, y), operation="NORMALIZE")
    link(tree, tc.outputs["Object"], nrm.inputs[0])
    sep = node(tree, "ShaderNodeSeparateXYZ", (x + 360, y))
    link(tree, nrm.outputs["Vector"], sep.inputs[0])
    lon = math_node(tree, "ARCTAN2", sep.outputs["Y"], sep.outputs["X"], (x + 540, y + 80))
    u = math_node(tree, "MULTIPLY_ADD", lon, 1.0 / (2.0 * math.pi), (x + 720, y + 80))
    u.node.inputs[2].default_value = 0.5
    lat = math_node(tree, "ARCSINE", sep.outputs["Z"], None, (x + 540, y - 80))
    v = math_node(tree, "MULTIPLY_ADD", lat, 1.0 / math.pi, (x + 720, y - 80))
    v.node.inputs[2].default_value = 0.5
    comb = node(tree, "ShaderNodeCombineXYZ", (x + 900, y))
    link(tree, u, comb.inputs["X"])
    link(tree, v, comb.inputs["Y"])
    return comb.outputs["Vector"]


def new_material(name: str):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.node_tree.nodes.clear()
    return mat, mat.node_tree


def earth(gmst_deg: float, city_radiance: float):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=512, ring_count=256, radius=1.0)
    obj = bpy.context.active_object
    obj.name = "Earth"
    bpy.ops.object.shade_smooth()
    obj.rotation_euler = (0.0, 0.0, math.radians(gmst_deg))  # Greenwich (object +X) at the GMST angle

    mat, t = new_material("Earth")
    uv = equirect_uv(t)
    day = image_node(t, SRC / "earth_bluemarble_2004-11_16384.jpg", "sRGB", (-300, 300))
    night = image_node(t, SRC / "earth_blackmarble_2016_3km.jpg", "sRGB", (-300, -100))
    link(t, uv, day.inputs["Vector"])
    link(t, uv, night.inputs["Vector"])

    # Keep the lights and drop the dim base layer NASA blended into the Black Marble composite
    lum = node(t, "ShaderNodeRGBToBW", (0, -260))
    link(t, night.outputs["Color"], lum.inputs[0])
    mask = node(t, "ShaderNodeMapRange", (180, -260))
    mask.inputs["From Min"].default_value = 0.10
    mask.inputs["From Max"].default_value = 0.55
    link(t, lum.outputs[0], mask.inputs["Value"])
    lights = node(t, "ShaderNodeVectorMath", (380, -120), operation="SCALE")
    link(t, night.outputs["Color"], lights.inputs[0])
    link(t, mask.outputs["Result"], lights.inputs["Scale"])

    bsdf = node(t, "ShaderNodeBsdfPrincipled", (600, 200))
    link(t, day.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.6
    link(t, lights.outputs["Vector"], bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = city_radiance
    out = node(t, "ShaderNodeOutputMaterial", (900, 200))
    link(t, bsdf.outputs["BSDF"], out.inputs["Surface"])
    obj.data.materials.append(mat)
    return obj


def exp_density(t, height, scale_height_m: float, coefficient: float, loc):
    """coefficient * exp(-height / H), with height and H in Earth radii."""
    x = math_node(t, "MULTIPLY", height, -EARTH_RADIUS_M / scale_height_m, loc)
    e = math_node(t, "EXPONENT", x, None, (loc[0] + 180, loc[1]))
    return math_node(t, "MULTIPLY", e, coefficient, (loc[0] + 360, loc[1]))


def atmosphere(airglow_radiance: float):
    top = 1.0 + ATMOSPHERE_TOP_M / EARTH_RADIUS_M
    bpy.ops.mesh.primitive_uv_sphere_add(segments=256, ring_count=128, radius=top)
    obj = bpy.context.active_object
    obj.name = "Atmosphere"
    bpy.ops.object.shade_smooth()

    mat, t = new_material("Atmosphere")
    mat.cycles.volume_step_rate = 0.02
    tc = node(t, "ShaderNodeTexCoord", (-1400, 0))
    length = node(t, "ShaderNodeVectorMath", (-1220, 0), operation="LENGTH")
    link(t, tc.outputs["Object"], length.inputs[0])
    h = math_node(t, "SUBTRACT", length.outputs["Value"], 1.0, (-1040, 0))
    h = math_node(t, "MAXIMUM", h, 0.0, (-860, 0))

    r = [c * EARTH_RADIUS_M for c in RAYLEIGH_PER_M]
    rayleigh = node(t, "ShaderNodeVolumeScatter", (0, 300), phase="RAYLEIGH")
    rayleigh.inputs["Color"].default_value = (r[0] / r[2], r[1] / r[2], 1.0, 1.0)
    link(t, exp_density(t, h, RAYLEIGH_SCALE_HEIGHT_M, r[2], (-600, 300)), rayleigh.inputs["Density"])

    mie = node(t, "ShaderNodeVolumeScatter", (0, 60), phase="HENYEY_GREENSTEIN")
    mie.inputs["Anisotropy"].default_value = 0.8
    link(t, exp_density(t, h, MIE_SCALE_HEIGHT_M, MIE_SCATTER_PER_M * EARTH_RADIUS_M, (-600, 60)), mie.inputs["Density"])
    mie_abs = node(t, "ShaderNodeVolumeAbsorption", (0, -160))
    mie_abs.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    link(t, exp_density(t, h, MIE_SCALE_HEIGHT_M, MIE_ABSORB_PER_M * EARTH_RADIUS_M, (-600, -160)), mie_abs.inputs["Density"])

    # Ozone: a tent profile centred at 25 km; absorption color chosen so sigma_a = (1 - color) * density
    oz = [c * EARTH_RADIUS_M for c in OZONE_ABSORB_PER_M]
    off = math_node(t, "SUBTRACT", h, OZONE_CENTER_M / EARTH_RADIUS_M, (-860, -400))
    off = math_node(t, "ABSOLUTE", off, None, (-680, -400))
    tent = math_node(t, "MULTIPLY", off, -EARTH_RADIUS_M / OZONE_HALF_WIDTH_M, (-500, -400))
    tent = math_node(t, "ADD", tent, 1.0, (-320, -400))
    tent = math_node(t, "MAXIMUM", tent, 0.0, (-140, -400))
    ozone = node(t, "ShaderNodeVolumeAbsorption", (0, -400))
    ozone.inputs["Color"].default_value = (1 - oz[0] / oz[1], 0.0, 1 - oz[2] / oz[1], 1.0)
    link(t, math_node(t, "MULTIPLY", tent, oz[1], (-140, -520)), ozone.inputs["Density"])

    add1 = node(t, "ShaderNodeAddShader", (250, 200))
    link(t, rayleigh.outputs[0], add1.inputs[0])
    link(t, mie.outputs[0], add1.inputs[1])
    add2 = node(t, "ShaderNodeAddShader", (420, 0))
    link(t, add1.outputs[0], add2.inputs[0])
    link(t, mie_abs.outputs[0], add2.inputs[1])
    add3 = node(t, "ShaderNodeAddShader", (590, -150))
    link(t, add2.outputs[0], add3.inputs[0])
    link(t, ozone.outputs[0], add3.inputs[1])

    # Modeled airglow: the oxygen 557.7 nm green layer near 95 km (Gaussian, 5 km sigma). Real but
    # variable; the brightness here is a typical value, not a measurement for this night
    glow_off = math_node(t, "SUBTRACT", h, AIRGLOW_ALTITUDE_M / EARTH_RADIUS_M, (-860, -700))
    glow_off = math_node(t, "MULTIPLY", glow_off, EARTH_RADIUS_M / AIRGLOW_SIGMA_M, (-680, -700))
    glow_sq = math_node(t, "MULTIPLY", glow_off, glow_off, (-500, -700))
    glow = math_node(t, "MULTIPLY", glow_sq, -0.5, (-320, -700))
    glow = math_node(t, "EXPONENT", glow, None, (-140, -700))
    airglow = node(t, "ShaderNodeEmission", (0, -700))
    airglow.inputs["Color"].default_value = AIRGLOW_COLOR
    link(t, math_node(t, "MULTIPLY", glow, airglow_radiance, (-140, -820)), airglow.inputs["Strength"])
    add4 = node(t, "ShaderNodeAddShader", (760, -300))
    link(t, add3.outputs[0], add4.inputs[0])
    link(t, airglow.outputs[0], add4.inputs[1])

    out = node(t, "ShaderNodeOutputMaterial", (950, 0))
    link(t, add4.outputs[0], out.inputs["Volume"])
    obj.data.materials.append(mat)
    return obj


def moon(position: Vector):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=256, ring_count=128, radius=MOON_RADIUS)
    obj = bpy.context.active_object
    obj.name = "Moon"
    bpy.ops.object.shade_smooth()
    # Tidally locked: longitude 0 (object +X) faces Earth; north roughly along the ecliptic pole
    x_axis = (-position).normalized()
    eps = math.radians(23.43929111)
    north = Vector((0.0, -math.sin(eps), math.cos(eps)))
    z_axis = (north - north.dot(x_axis) * x_axis).normalized()
    y_axis = z_axis.cross(x_axis)
    rot = Matrix((x_axis, y_axis, z_axis)).transposed().to_4x4()
    obj.matrix_world = Matrix.Translation(position) @ rot

    mat, t = new_material("Moon")
    uv = equirect_uv(t)
    color = image_node(t, SRC / "moon_lroc_color_4k.tif", "sRGB", (-300, 200))
    elevation = image_node(t, SRC / "moon_lola_ldem_16.tif", "Non-Color", (-300, -150))
    link(t, uv, color.inputs["Vector"])
    link(t, uv, elevation.inputs["Vector"])
    bump = node(t, "ShaderNodeBump", (100, -150))
    bump.inputs["Distance"].default_value = 1.0 / 6371.0  # elevation is in km; one unit is 6371 km
    link(t, elevation.outputs["Color"], bump.inputs["Height"])
    bsdf = node(t, "ShaderNodeBsdfPrincipled", (350, 150))
    link(t, color.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 1.0
    link(t, bump.outputs["Normal"], bsdf.inputs["Normal"])
    out = node(t, "ShaderNodeOutputMaterial", (650, 150))
    link(t, bsdf.outputs["BSDF"], out.inputs["Surface"])
    obj.data.materials.append(mat)
    return obj


def sun(direction: Vector, irradiance: float):
    light = bpy.data.lights.new("Sun", "SUN")
    light.energy = irradiance
    light.angle = math.radians(0.533)
    obj = bpy.data.objects.new("Sun", light)
    bpy.context.scene.collection.objects.link(obj)
    obj.rotation_euler = direction.to_track_quat("Z", "Y").to_euler()  # light travels along local -Z
    return obj


def stars(strength: float):
    world = bpy.data.worlds.new("DeepStarMap")
    world.use_nodes = True
    t = world.node_tree
    t.nodes.clear()
    tc = node(t, "ShaderNodeTexCoord", (-900, 0))
    # The map puts RA 0h at its centre with RA increasing leftward; Blender's lookup needs a half turn
    mapping = node(t, "ShaderNodeMapping", (-700, 0), vector_type="POINT")
    mapping.inputs["Rotation"].default_value = (0.0, 0.0, math.pi)
    link(t, tc.outputs["Generated"], mapping.inputs["Vector"])
    env = node(t, "ShaderNodeTexEnvironment", (-450, 0), interpolation="Cubic")
    env.image = bpy.data.images.load(str(SRC / "starmap_2020_8k.exr"), check_existing=True)
    env.image.colorspace_settings.name = "Linear Rec.709"
    link(t, mapping.outputs["Vector"], env.inputs["Vector"])
    # Starlight is about a billionth of sunlight, so the map is seen by the camera but lights nothing
    camera_ray = node(t, "ShaderNodeLightPath", (-450, 300))
    seen = math_node(t, "MULTIPLY", camera_ray.outputs["Is Camera Ray"], strength, (-250, 250))
    bg = node(t, "ShaderNodeBackground", (-150, 0))
    link(t, seen, bg.inputs["Strength"])
    link(t, env.outputs["Color"], bg.inputs["Color"])
    out = node(t, "ShaderNodeOutputWorld", (100, 0))
    link(t, bg.outputs["Background"], out.inputs["Surface"])
    bpy.context.scene.world = world


def camera(position: Vector, lens_mm: float):
    cam_data = bpy.data.cameras.new("Camera")
    cam_data.lens = lens_mm
    cam_data.sensor_width = 36.0
    cam_data.clip_start = 0.001
    cam_data.clip_end = 1000.0
    obj = bpy.data.objects.new("Camera", cam_data)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = position
    obj.rotation_euler = (-position).to_track_quat("-Z", "Y").to_euler()  # celestial north up
    bpy.context.scene.camera = obj
    return obj


def grade(bloom_strength: float) -> None:
    """Filmic bloom on scene-linear light before the AgX view transform, as a lens and eye produce."""
    scene = bpy.context.scene
    tree = bpy.data.node_groups.new("Grade", "CompositorNodeTree")
    tree.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    scene.compositing_node_group = tree
    layers = node(tree, "CompositorNodeRLayers", (0, 0))
    glare = node(tree, "CompositorNodeGlare", (300, 0))
    for value in ("Bloom", "BLOOM"):
        try:
            glare.inputs["Type"].default_value = value
            break
        except TypeError:
            continue
    glare.inputs["Threshold"].default_value = 0.004
    glare.inputs["Strength"].default_value = bloom_strength
    # The sunlit limb is thousands of times brighter than any city; cap what a pixel can feed the
    # bloom so the limb glows like a bright light instead of flooding the frame
    glare.inputs["Clamp"].default_value = True
    glare.inputs["Maximum"].default_value = 0.06
    out = node(tree, "NodeGroupOutput", (600, 0))
    link(tree, layers.outputs["Image"], glare.inputs["Image"])
    link(tree, glare.outputs["Image"], out.inputs["Image"])


def render_settings(args: argparse.Namespace) -> None:
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.compute_device_type = "METAL"
    prefs.get_devices()
    for device in prefs.devices:
        device.use = device.type == "METAL"
    scene.cycles.device = "GPU"
    w, h = (int(v) for v in args.res.lower().split("x"))
    scene.render.resolution_x, scene.render.resolution_y = w, h
    scene.render.resolution_percentage = 100
    scene.cycles.samples = args.samples
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = 0.005
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = 6
    scene.cycles.volume_bounces = 1
    scene.cycles.volume_step_rate = 0.25
    scene.cycles.volume_max_steps = 4096
    scene.cycles.sample_clamp_indirect = 10.0
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.exposure = args.exposure
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_depth = "16"
    scene.render.filepath = str(args.out)


def main() -> None:
    args = parse_args()
    plan = json.loads(args.plan.read_text())
    bpy.ops.wm.read_factory_settings(use_empty=True)

    earth(plan["gmstDeg"], args.city_radiance)
    atmosphere(args.airglow_radiance)
    moon(Vector(plan["moonPosition"]))
    sun(Vector(plan["sunDirection"]), args.sun_irradiance)
    stars(args.star_strength)
    camera(Vector(plan["cameraPosition"]), args.lens_mm)
    render_settings(args)
    grade(args.bloom)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.render.render(write_still=True)
    print(f"LOOKDEV_RENDERED {args.out}")


main()
