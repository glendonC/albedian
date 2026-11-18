"""Solar illumination, Deep Star Map background, camera rig, AgX bloom compositor, Cycles GPU, and CLI flags."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector

from scripts.lookdev.earth_night.constants import SRC
from scripts.lookdev.earth_night.shader_nodes import link, math_node, node

_SUN_ANGULAR_DIAMETER_DEG = 0.533
_FULL_FRAME_SENSOR_WIDTH_MM = 36.0
_GLARE_THRESHOLD = 0.004
_GLARE_MAXIMUM = 0.06


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


def sun(direction: Vector, irradiance: float):
    light = bpy.data.lights.new("Sun", "SUN")
    light.energy = irradiance
    light.angle = math.radians(_SUN_ANGULAR_DIAMETER_DEG)
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
    cam_data.sensor_width = _FULL_FRAME_SENSOR_WIDTH_MM
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
    glare.inputs["Threshold"].default_value = _GLARE_THRESHOLD
    glare.inputs["Strength"].default_value = bloom_strength
    # The sunlit limb is thousands of times brighter than any city; cap what a pixel can feed the
    # bloom so the limb glows like a bright light instead of flooding the frame
    glare.inputs["Clamp"].default_value = True
    glare.inputs["Maximum"].default_value = _GLARE_MAXIMUM
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


__all__ = [
    "camera",
    "grade",
    "parse_args",
    "render_settings",
    "stars",
    "sun",
]
