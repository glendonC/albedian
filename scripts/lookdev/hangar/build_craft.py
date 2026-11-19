"""Build one hangar craft for the web from NASA's own public-domain model, inside Blender 5.2, headless.

From the repo root:
    blender -b --factory-startup -P scripts/lookdev/hangar/build_craft.py -- --craft voyager \
        --out apps/web/public/models/hangar/voyager

Reads the recipe in scripts/lookdev/hangar/crafts.py and the pinned source in .cache/hangar/nasa3d/ (it refuses a
source whose size or SHA-256 differ), then:
1. drops the source's helper objects, joins the craft into one mesh and turns it so its direction of travel is glTF +Z
   and its up glTF +Y, at true scale (one unit is one metre, from one dimension NASA states), the origin at the
   centre of the part the scale is taken from;
2. rebuilds the materials as physically based ones: NASA's own photo texture stays the base colour (any NASA insignia
   or logotype on it painted over, since NASA's guidelines keep those out of the public domain), and its
   metalness, roughness and relief are added from the texture's own colours (gold foil and bare metal shine, black
   blankets and white paint do not, the blankets' wrinkles come from the photo's own shading); painted parts get a
   flat paint colour with NASA's baked shading moved to the occlusion slot, where a renderer that computes its own
   shadows ignores it;
3. writes three levels of detail as glb with WebP textures (lod0, lod1, lod2), and the picker's picture, NASA's own
   render cut down to PICKER_EDGE pixels. Geometry is not Draco-compressed, so three.js's GLTFLoader reads every file
   with no decoder to serve; at these sizes the textures dominate.

The provenance record and the facts file are written afterwards by write_hangar.py, which reads the files this wrote.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.lookdev.hangar.crafts import (  # noqa: E402
    CACHE,
    CRAFTS,
    LOD_RATIO,
    PICKER_EDGE,
    TEXTURE_EDGE,
)

# How each file differs from NASA's model, in plain words; write_hangar.py copies these into the provenance record.
CHANGES = {
    "lod0": (
        "Rebuilt from NASA's model, not copied: helper objects removed, the parts joined into one mesh, turned so the "
        "craft travels along +Z with +Y up and scaled to metres from a dimension NASA states; NASA's photo texture "
        "kept as the base colour, with any NASA insignia or logotype it shows painted over in the colour around it, "
        "metalness, roughness and surface relief added from the texture's own colours and shading, flat paint "
        "colours for the painted parts with NASA's baked shading moved to the occlusion slot, and the textures "
        "re-encoded as WebP."
    ),
    "lod1": (
        "The same rebuild as lod0 with about half its triangles, removed by Blender's collapse decimation, and "
        "textures at most 1024 pixels on a side."
    ),
    "lod2": (
        "The same rebuild as lod0 with under a fifth of its triangles, removed by Blender's collapse decimation, and "
        "textures at most 512 pixels on a side."
    ),
    "picker": "NASA's own render of its model, scaled down to 176 pixels on its longer side and re-encoded as WebP.",
}

# Photo atlas material rules, on the texture's sRGB values: hue in degrees, saturation and value in 0..1.
GOLD_HUE = (22.0, 62.0)  # gold and amber foil, gold-coloured anodising
GOLD_MIN_SAT = 0.30
DARK_MAX_VALUE = 0.22  # black blankets and black paint
WHITE_MAX_SAT = 0.15
WHITE_MIN_VALUE = 0.72  # white paint and white blankets
ROUGHNESS = {"gold": 0.28, "dark": 0.52, "white": 0.62, "grey": 0.36}
METALLIC = {"gold": 1.0, "dark": 0.0, "white": 0.0, "grey": 0.7}
RELIEF_STRENGTH = {"gold": 2.2, "dark": 1.6, "white": 0.4, "grey": 0.5}
RELIEF_BLUR_PX = 3  # the high-pass radius, in source pixels, that separates wrinkles from the photo's broad shading
PAINT_OUT_BAND_PX = 3  # the band around a painted-out box whose median colour fills it


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_source(path: Path, size: int, digest: str) -> None:
    if not path.is_file():
        raise SystemExit(f"missing source {path}; fetch it as data/downloads.json says")
    if path.stat().st_size != size or sha256_file(path) != digest:
        raise SystemExit(f"source {path} differs from the pinned size or SHA-256")


def smoothstep(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def box_blur(a: np.ndarray, r: int) -> np.ndarray:
    """Three passes of a separable box blur of radius r, about a Gaussian, edges clamped."""
    out = a
    for _ in range(3):
        for axis in (0, 1):
            pad = [(0, 0), (0, 0)]
            pad[axis] = (r + 1, r)
            c = np.cumsum(np.pad(out, pad, mode="edge"), axis=axis)
            n = out.shape[axis]
            hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
            lo = np.take(c, np.arange(0, n), axis=axis)
            out = (hi - lo) / (2 * r + 1)
    return out


def image_array(img: bpy.types.Image) -> np.ndarray:
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    return px.reshape(h, w, 4)


def new_image(name: str, arr: np.ndarray, non_color: bool) -> bpy.types.Image:
    h, w, _ = arr.shape
    img = bpy.data.images.new(name, width=w, height=h, alpha=False)
    if non_color:
        img.colorspace_settings.name = "Non-Color"
    img.pixels.foreach_set(np.ascontiguousarray(arr, dtype=np.float32).ravel())
    img.pack()
    return img


def atlas_masks(rgb: np.ndarray) -> dict[str, np.ndarray]:
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    v = rgb.max(axis=-1)
    mn = rgb.min(axis=-1)
    s = np.where(v > 1e-6, (v - mn) / np.maximum(v, 1e-6), 0.0)
    d = np.maximum(v - mn, 1e-6)
    hue = np.where(v == r, (g - b) / d % 6, np.where(v == g, (b - r) / d + 2, (r - g) / d + 4)) * 60.0
    gold = smoothstep(GOLD_HUE[0] - 6, GOLD_HUE[0], hue) * (1 - smoothstep(GOLD_HUE[1], GOLD_HUE[1] + 6, hue))
    gold *= smoothstep(GOLD_MIN_SAT - 0.08, GOLD_MIN_SAT + 0.04, s) * smoothstep(0.12, 0.2, v)
    dark = (1 - smoothstep(DARK_MAX_VALUE - 0.06, DARK_MAX_VALUE + 0.04, v)) * (1 - gold)
    white = (1 - smoothstep(WHITE_MAX_SAT - 0.05, WHITE_MAX_SAT + 0.05, s)) * smoothstep(WHITE_MIN_VALUE - 0.06, WHITE_MIN_VALUE + 0.04, v)
    white *= 1 - gold - dark
    grey = np.clip(1 - gold - dark - white, 0.0, 1.0)
    return {"gold": gold, "dark": dark, "white": white, "grey": grey}


def atlas_maps(base: bpy.types.Image) -> tuple[np.ndarray, np.ndarray]:
    """The metallic-roughness map (glTF: G roughness, B metalness) and the tangent-space normal map of a photo atlas."""
    px = image_array(base)
    rgb = px[..., :3]
    masks = atlas_masks(rgb)
    rough = sum(masks[k] * ROUGHNESS[k] for k in masks)
    metal = sum(masks[k] * METALLIC[k] for k in masks)
    orm = np.ones(px.shape, dtype=np.float32)
    orm[..., 1] = rough
    orm[..., 2] = metal
    lum = rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    height = lum - box_blur(lum, RELIEF_BLUR_PX)
    strength = sum(masks[k] * RELIEF_STRENGTH[k] for k in masks)
    gy, gx = np.gradient(height * strength)
    n = np.dstack([-gx, -gy, np.full_like(gx, 1.0 / 4.0)])
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    nrm = np.ones(px.shape, dtype=np.float32)
    nrm[..., :3] = n * 0.5 + 0.5
    return orm, nrm


def painted_out(img: bpy.types.Image, boxes: list[dict]) -> bpy.types.Image:
    """A copy of the photo texture with each box (source pixels, x0 y0 x1 y1 from the top left) filled with the median
    colour of a PAINT_OUT_BAND_PX band around it: NASA's insignia and logotype are not public domain, so a mark NASA
    photographed on the hardware is covered with the surface it is printed on."""
    px = image_array(img).copy()
    h, w, _ = px.shape
    for box in boxes:
        x0, y0, x1, y1 = box["box"]
        r0, r1 = h - y1, h - y0  # Blender's rows run from the bottom
        b = PAINT_OUT_BAND_PX
        ring = np.concatenate(
            [
                px[max(r0 - b, 0) : r0, x0:x1].reshape(-1, 4),
                px[r1 : r1 + b, x0:x1].reshape(-1, 4),
                px[r0:r1, max(x0 - b, 0) : x0].reshape(-1, 4),
                px[r0:r1, x1 : x1 + b].reshape(-1, 4),
            ]
        )
        px[r0:r1, x0:x1] = np.median(ring, axis=0)
    out = bpy.data.images.new(f"{img.name}_painted", width=w, height=h, alpha=False)
    out.pixels.foreach_set(px.ravel())
    out.pack()
    return out


def material_output_group() -> bpy.types.NodeTree:
    """The node group Blender's glTF exporter reads the occlusion texture from."""
    group = bpy.data.node_groups.get("glTF Material Output")
    if group is None:
        group = bpy.data.node_groups.new("glTF Material Output", "ShaderNodeTree")
        group.interface.new_socket("Occlusion", in_out="INPUT", socket_type="NodeSocketFloat")
    return group


def build_material(name: str, spec: dict, source_mat: bpy.types.Material) -> bpy.types.Material:
    src_img = next((n.image for n in source_mat.node_tree.nodes if n.type == "TEX_IMAGE" and n.image), None)
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    links = nt.links
    if spec["role"] == "photo_atlas":
        if spec.get("paint_out"):
            src_img = painted_out(src_img, spec["paint_out"])
        base = nt.nodes.new("ShaderNodeTexImage")
        base.image = src_img
        links.new(base.outputs["Color"], bsdf.inputs["Base Color"])
        orm_arr, nrm_arr = atlas_maps(src_img)
        orm = nt.nodes.new("ShaderNodeTexImage")
        orm.image = new_image(f"{name}_metal_rough", orm_arr, non_color=True)
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        links.new(orm.outputs["Color"], sep.inputs["Color"])
        links.new(sep.outputs[1], bsdf.inputs["Roughness"])
        links.new(sep.outputs[2], bsdf.inputs["Metallic"])
        nrm = nt.nodes.new("ShaderNodeTexImage")
        nrm.image = new_image(f"{name}_normal", nrm_arr, non_color=True)
        nmap = nt.nodes.new("ShaderNodeNormalMap")
        links.new(nrm.outputs["Color"], nmap.inputs["Color"])
        links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    elif spec["role"] == "painted":
        bsdf.inputs["Base Color"].default_value = (*spec["base"], 1.0)
        bsdf.inputs["Roughness"].default_value = spec["roughness"]
        bsdf.inputs["Metallic"].default_value = 0.0
        if spec.get("occlusion_from_source") and src_img is not None:
            ao = nt.nodes.new("ShaderNodeTexImage")
            ao.image = src_img
            src_img.colorspace_settings.name = "Non-Color"
            sep = nt.nodes.new("ShaderNodeSeparateColor")
            links.new(ao.outputs["Color"], sep.inputs["Color"])
            out = nt.nodes.new("ShaderNodeGroup")
            out.node_tree = material_output_group()
            links.new(sep.outputs[0], out.inputs["Occlusion"])
    else:
        raise SystemExit(f"unknown material role {spec['role']}")
    return mat


def basis(forward: Vector, up: Vector) -> Matrix:
    f = forward.normalized()
    u = (up - f * up.dot(f)).normalized()
    r = f.cross(u)
    return Matrix((f, u, r)).transposed()


def orient_matrix(forward, up) -> Matrix:
    """Rotation taking the recipe's forward to Blender -Y (glTF +Z) and its up to Blender +Z (glTF +Y)."""
    src = basis(Vector(forward), Vector(up))
    dst = basis(Vector((0.0, -1.0, 0.0)), Vector((0.0, 0.0, 1.0)))
    return (dst @ src.transposed()).to_4x4()


def resized(img: bpy.types.Image, edge: int, suffix: str) -> bpy.types.Image:
    w, h = img.size
    if max(w, h) <= edge:
        return img
    k = edge / max(w, h)
    copy = img.copy()
    copy.name = f"{img.name}_{suffix}"
    copy.scale(max(1, round(w * k)), max(1, round(h * k)))
    copy.pack()
    return copy


def export_level(obj: bpy.types.Object, level: str, path: Path) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.export_scene.gltf(
        filepath=str(path),
        export_format="GLB",
        use_selection=True,
        export_apply=True,
        export_yup=True,
        export_normals=True,
        export_tangents=False,
        export_texcoords=True,
        export_materials="EXPORT",
        export_image_format="WEBP",
        export_image_quality=88,
        export_draco_mesh_compression_enable=False,
        export_cameras=False,
        export_lights=False,
        export_extras=False,
    )


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--craft", required=True, choices=sorted(CRAFTS))
    ap.add_argument("--out", required=True)
    ap.add_argument("--blend", help="also save the built scene as this .blend, for renders")
    ap.add_argument("--maps", help="also save each rebuilt texture as PNG in this folder, to look at")
    args = ap.parse_args(argv)
    recipe = CRAFTS[args.craft]
    src = recipe["source"]
    folder = REPO_ROOT / CACHE / src["local"]
    model = folder / src["file"]
    check_source(model, src["bytes"], src["sha256"])
    picture = folder / src["picture"]
    check_source(picture, src["picture_bytes"], src["picture_sha256"])
    out = (REPO_ROOT / args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(model))
    for name in recipe.get("drop_objects", []):
        ob = bpy.data.objects.get(name)
        if ob is not None:
            for child in list(ob.children):
                mw = child.matrix_world.copy()
                child.parent = None
                child.matrix_world = mw
            bpy.data.objects.remove(ob, do_unlink=True)
    meshes = [ob for ob in bpy.context.scene.objects if ob.type == "MESH"]
    for ob in meshes:
        mw = ob.matrix_world.copy()
        ob.parent = None
        ob.matrix_world = mw
    bpy.ops.object.select_all(action="DESELECT")
    for ob in meshes:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    craft = bpy.context.view_layer.objects.active
    craft.name = args.craft
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    # Blender's own repair of invalid geometry (degenerate or repeated faces, bad indices), keeping custom normals.
    craft.data.validate(verbose=False, clean_customdata=False)
    for ob in [o for o in bpy.context.scene.objects if o is not craft]:
        bpy.data.objects.remove(ob, do_unlink=True)

    # Scale and origin from the part NASA states a dimension of.
    me = craft.data
    sf = recipe["scale_from"]
    if sf["metres"] is None:
        raise SystemExit("the recipe has no sourced dimension to scale from")
    mat_index = [i for i, m in enumerate(me.materials) if m and m.name == sf["material"]]
    vidx = sorted({v for p in me.polygons if p.material_index in mat_index for v in p.vertices})
    co = np.array([me.vertices[i].co for i in vidx])
    axis = "xyz".index(sf["axis"])
    extent = co[:, axis].max() - co[:, axis].min()
    centre = Vector(((co.max(0) + co.min(0)) / 2).tolist())
    scale = sf["metres"] / extent
    m = orient_matrix(recipe["forward"], recipe["up"]) @ Matrix.Diagonal((scale, scale, scale, 1.0)) @ Matrix.Translation(-centre)
    me.transform(m)
    me.update()

    # Materials.
    for i, slot in enumerate(craft.material_slots):
        spec = recipe["materials"].get(slot.material.name)
        if spec is None:
            raise SystemExit(f"the recipe has no role for material {slot.material.name}")
        slot.material = build_material(f"{args.craft}_{slot.material.name}", spec, slot.material)

    # Levels of detail.
    tris0 = sum(len(p.vertices) - 2 for p in me.polygons)
    for level, ratio in LOD_RATIO.items():
        obj = craft.copy()
        obj.data = craft.data.copy()
        obj.name = f"{args.craft}_{level}"
        bpy.context.scene.collection.objects.link(obj)
        for slot in obj.material_slots:
            mat = slot.material.copy()
            for node in mat.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image is not None:
                    node.image = resized(node.image, TEXTURE_EDGE[level], level)
            slot.material = mat
        if ratio < 1.0:
            dec = obj.modifiers.new("lod", "DECIMATE")
            dec.decimate_type = "COLLAPSE"
            dec.ratio = ratio
            dec.use_collapse_triangulate = True
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.modifier_apply(modifier=dec.name)
            obj.data.validate(verbose=False, clean_customdata=False)
        export_level(obj, level, out / f"{args.craft}_{level}.glb")
        tris = sum(len(p.vertices) - 2 for p in obj.data.polygons)
        print(f"HANGAR {args.craft} {level} triangles {tris} of {tris0}")
        bpy.data.objects.remove(obj, do_unlink=True)

    # The picker's picture: NASA's own render, scaled down.
    if args.maps:
        maps = Path(args.maps).resolve()
        maps.mkdir(parents=True, exist_ok=True)
        for img in bpy.data.images:
            if img.size[0] and ("_painted" in img.name or img.name.endswith(("_metal_rough", "_normal"))):
                img.filepath_raw = str(maps / f"{bpy.path.clean_name(img.name)}.png")
                img.file_format = "PNG"
                img.save()
    pic = bpy.data.images.load(str(picture))
    w, h = pic.size
    k = PICKER_EDGE / max(w, h)
    pic.scale(max(1, round(w * k)), max(1, round(h * k)))
    pic.filepath_raw = str(out / f"{args.craft}_picker.webp")
    pic.file_format = "WEBP"
    bpy.context.scene.render.image_settings.quality = 88
    pic.save(quality=88)
    print(f"HANGAR {args.craft} scale {scale:.6f} metres per source unit, dish-axis extent {extent:.4f}")
    if args.blend:
        craft.hide_render = False
        bpy.ops.wm.save_as_mainfile(filepath=str(Path(args.blend).resolve()))


main()
