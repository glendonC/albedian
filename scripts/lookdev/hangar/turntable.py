"""Render a hangar craft's turntable and its chase view for the design record, in Blender Cycles, headless.

From the repo root:
    blender -b --factory-startup -P scripts/lookdev/hangar/turntable.py -- \
        --glb apps/web/public/models/hangar/voyager/voyager_lod0.glb --out <folder> [--frames 36] [--samples 48]

It imports the web file itself (so the record shows what the web gets), lights it with one distant key light from the
upper left and a faint blue-grey rim from behind, on black: a studio light for the design record, not the light where
the craft really is (between the stars the Sun is a point among the others). Writes:
- chase.png: the D26 framing, 1440 by 900: behind the craft and 14 degrees above it, the craft's middle at 62 percent
  of the height, about 320 px across;
- turn_000.png ... : the craft turning once about its up axis, seen from 15 degrees above its side.
Then `ffmpeg -framerate 24 -i turn_%03d.png -pix_fmt yuv420p -c:v libx264 -crf 18 turntable.mp4` in that folder.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import bpy
from mathutils import Euler, Vector

CHASE_ELEVATION_DEG = 14.0  # D26: behind the craft and a little above it
CHASE_CENTRE_FROM_TOP = 0.62  # D26: the craft's middle at 62 percent of the screen height
CHASE_WIDTH_PX = 320  # D26: about 320 px across on the laptop
TURN_ELEVATION_DEG = 15.0
SKY_STRENGTH = 0.6  # the panorama's brightness as the world light, when one is given
# Light directions, as the way the light travels, in Blender's frame (the craft travels along -Y). In the chase view the
# key comes from behind the craft, the camera's side: for a craft leaving the Sun that is where the Sun is.
CHASE_KEY_TRAVEL = (0.3, -1.0, -0.45)
CHASE_RIM_TRAVEL = (-0.4, 1.0, -0.3)
TURN_KEY_TRAVEL = (1.0, -0.8, -0.8)
TURN_RIM_TRAVEL = (-1.0, 0.4, 0.3)
HERO_KEY_TRAVEL = (0.35, 1.0, -0.6)
HERO_RIM_TRAVEL = (-0.6, -1.0, -0.3)


def setup_render(samples: int, w: int, h: int, sky: str | None = None, sky_strength: float = SKY_STRENGTH) -> None:
    scene = bpy.context.scene
    try:
        scene.render.engine = "CYCLES"
    except TypeError as exc:
        raise SystemExit(f"Cycles not available: {exc}")
    prefs = bpy.context.preferences.addons["cycles"].preferences
    try:
        prefs.compute_device_type = "METAL"
        prefs.get_devices()
        for d in prefs.devices:
            d.use = True
        scene.cycles.device = "GPU"
    except (TypeError, ValueError):
        scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    scene.render.resolution_x = w
    scene.render.resolution_y = h
    scene.render.film_transparent = False
    world = bpy.data.worlds.new("space")
    scene.world = world
    world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    bg.inputs[0].default_value = (0.0, 0.0, 0.0, 1.0)
    if sky:
        # A real sky for the metal to reflect: the app's own Milky Way panorama (ESO/S. Brunier, CC BY 4.0), faint.
        env = world.node_tree.nodes.new("ShaderNodeTexEnvironment")
        env.image = bpy.data.images.load(str(Path(sky).resolve()))
        world.node_tree.links.new(env.outputs["Color"], bg.inputs["Color"])
        bg.inputs["Strength"].default_value = sky_strength
    key = bpy.data.objects.new("key", bpy.data.lights.new("key", "SUN"))
    key.data.energy = 5.0
    key.data.angle = math.radians(0.53)
    scene.collection.objects.link(key)
    rim = bpy.data.objects.new("rim", bpy.data.lights.new("rim", "SUN"))
    rim.data.energy = 0.6
    rim.data.color = (0.75, 0.82, 1.0)
    scene.collection.objects.link(rim)


def aim_lights(key_travel: tuple, rim_travel: tuple) -> None:
    """Point the key and rim lights along the directions their light travels."""
    for name, travel in (("key", key_travel), ("rim", rim_travel)):
        bpy.data.objects[name].rotation_euler = Vector(travel).normalized().to_track_quat("-Z", "Y").to_euler()


def craft_bounds(objs) -> tuple[Vector, Vector]:
    lo = Vector((1e9,) * 3)
    hi = Vector((-1e9,) * 3)
    for ob in objs:
        for c in ob.bound_box:
            w = ob.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
    return lo, hi


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1 :]
    ap = argparse.ArgumentParser()
    ap.add_argument("--glb", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--frames", type=int, default=36)
    ap.add_argument("--samples", type=int, default=48)
    ap.add_argument("--skip-turn", action="store_true")
    ap.add_argument("--body-radius", type=float, help="metres around the origin the hero shot frames")
    ap.add_argument("--sky", help="an equirectangular sky image for the craft to reflect, e.g. apps/web/public/textures/eso_milkyway_panorama.jpg")
    args = ap.parse_args(argv)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(Path(args.glb).resolve()))
    objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    pivot = bpy.data.objects.new("pivot", None)
    bpy.context.scene.collection.objects.link(pivot)
    for o in objs:
        if o.parent is None:
            o.parent = pivot
    lo, hi = craft_bounds(objs)
    size = hi - lo
    radius = size.length / 2
    scene = bpy.context.scene
    cam_data = bpy.data.cameras.new("cam")
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam

    # Chase view. Forward is Blender -Y (glTF +Z), so behind is +Y.
    setup_render(args.samples, 1440, 900, args.sky)
    aim_lights(CHASE_KEY_TRAVEL, CHASE_RIM_TRAVEL)
    cam_data.lens = 35
    across = size.x
    fov = 2 * math.atan(cam_data.sensor_width / (2 * cam_data.lens))
    dist = (across / 2) / math.tan(fov / 2) * (1440 / CHASE_WIDTH_PX)
    el = math.radians(CHASE_ELEVATION_DEG)
    centre = Vector((0.0, 0.0, 0.0))
    cam.location = centre + Vector((0.0, math.cos(el), math.sin(el))) * dist
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam_data.shift_y = (CHASE_CENTRE_FROM_TOP - 0.5) * (900 / 1440)
    scene.render.filepath = str(out / "chase.png")
    bpy.ops.render.render(write_still=True)

    # Hero: a close look at the body from the front quarter, framed on --body-radius metres around the origin.
    scene.render.resolution_x = 1440
    scene.render.resolution_y = 900
    cam_data.shift_y = 0.0
    cam_data.lens = 50
    fov = 2 * math.atan(cam_data.sensor_width / (2 * cam_data.lens))
    body = args.body_radius if args.body_radius else radius / 3
    aim_lights(HERO_KEY_TRAVEL, HERO_RIM_TRAVEL)
    hero_dir = Vector((-0.75, -0.55, 0.38)).normalized()
    cam.location = hero_dir * (body / math.tan(fov / 2) * 1.6)
    cam.rotation_euler = (-cam.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = str(out / "hero.png")
    bpy.ops.render.render(write_still=True)
    if args.skip_turn:
        return

    # Turntable about the origin, the whole craft in frame at every angle.
    scene.render.resolution_x = 960
    scene.render.resolution_y = 540
    aim_lights(TURN_KEY_TRAVEL, TURN_RIM_TRAVEL)
    # The farthest the craft reaches from its turning axis, so no angle leaves the frame.
    reach = max(math.hypot(x, y) for x in (lo.x, hi.x) for y in (lo.y, hi.y))
    reach = max(reach, abs(lo.z), abs(hi.z))
    dist = reach / math.tan(fov / 2) * 1.08
    el = math.radians(TURN_ELEVATION_DEG)
    cam.location = Vector((-math.cos(el), 0.0, math.sin(el))) * dist
    cam.rotation_euler = (-cam.location).to_track_quat("-Z", "Y").to_euler()
    for i in range(args.frames):
        pivot.rotation_euler = Euler((0.0, 0.0, 2 * math.pi * i / args.frames))
        scene.render.filepath = str(out / f"turn_{i:03d}.png")
        bpy.ops.render.render(write_still=True)

main()
