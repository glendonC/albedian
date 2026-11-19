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
    # lod0 for a model textured with NASA's photographs (recipe "changes": "photo")
    "lod0_photo": (
        "Rebuilt from NASA's model, not copied: helper objects removed, the parts joined into one mesh, turned so the "
        "craft travels along +Z with +Y up and scaled to metres from a dimension NASA states; NASA's photo texture "
        "kept as the base colour, with any NASA insignia or logotype it shows painted over in the colour around it, "
        "metalness, roughness and surface relief added from the texture's own colours and shading, flat paint "
        "colours for the painted parts with NASA's baked shading moved to the occlusion slot, and the textures "
        "re-encoded as WebP."
    ),
    # lod0 for a model of flat colours whose materials NASA names by what they are (recipe "changes": "named")
    "lod0_named": (
        "Rebuilt from NASA's model, not copied: the parts joined into one mesh, turned so the craft faces along +Z "
        "with +Y up and scaled to metres from a dimension NASA states; the parts whose material NASA's model names "
        "(gold mirror, silver and pink blankets, black paint, aluminium, solar cells) given physically based "
        "materials of that kind, the others keeping NASA's own colour, faces that lie flat within a small angle "
        "merged, and the normals made again, smooth within an angle and sharp beyond it."
    ),
    # lod0 for a model of flat colours with a few photo panels (recipe "changes": "flat")
    "lod0_flat": (
        "Rebuilt from NASA's model, not copied: the parts joined into one mesh, turned so the craft faces along +Z "
        "with +Y up and scaled to metres from a dimension NASA states; NASA's flat colours kept, the parts NASA colours "
        "as gold foil made metal foil, its photo panels given metalness, roughness and relief from their own colours, "
        "faces NASA's file keeps apart at shared corners welded, the normals made again, smooth within an angle and "
        "sharp beyond it, and the textures re-encoded as WebP."
    ),
    # lod0 for a model of flat colours whose blankets NASA's model names as silver foil (recipe "changes": "foil")
    "lod0_foil": (
        "Rebuilt from NASA's model, not copied: the parts joined into one mesh, turned so the craft faces along +Z "
        "with +Y up and scaled to metres from a dimension NASA states; NASA's flat colours kept, the parts NASA's "
        "model names as silver foil made metal foil, its texture of solar cells, panels and the heat shield's white "
        "face kept as the base colour with one roughness and no metalness, faces NASA's file keeps apart at shared "
        "corners welded, the normals made again, smooth within an angle and sharp beyond it, and the texture "
        "re-encoded as WebP."
    ),
    # lod0 for a model of foil, panel and solar cell textures (recipe "changes": "atlas")
    "lod0_atlas": (
        "Rebuilt from NASA's model, not copied: a second copy of the dish's top surface removed, the parts joined into "
        "one mesh, turned so the craft faces along +Z with +Y up and scaled to metres from a dimension NASA states; "
        "NASA's foil and panel textures kept as the base colour, with metalness, roughness and surface relief added "
        "from each texture's own colours and shading, the solar cells' textures kept with one roughness and no "
        "metalness, a perforated panel given the mean of NASA's map with NASA's alpha kept as its mask, faces NASA's "
        "file keeps apart at shared corners welded, the normals made again, smooth within an angle and sharp beyond "
        "it, and the textures re-encoded as WebP."
    ),
    # lod0 for a model whose blankets NASA's model names, with a gold foil and one photo texture (recipe "changes": "dawn")
    "lod0_dawn": (
        "Rebuilt from NASA's model, not copied: helper objects removed, the parts joined into one mesh, turned so the "
        "craft faces along +Z with +Y up and scaled to metres from a dimension NASA states; the parts NASA's model "
        "names as black blanket or silver foil given physically based materials of that kind, the gold foil and the "
        "photo texture given metalness, roughness and surface relief from their own colours, a small printed label "
        "in the photo painted over in the colour around it, NASA's flat colours kept for the rest, faces NASA's file "
        "keeps apart at shared corners welded, the normals made again, smooth within an angle and sharp beyond it, "
        "and the textures re-encoded as WebP."
    ),
    # lod0 for a model of flat colours whose materials NASA names as metals, foils, a mirror and cells ("metals")
    "lod0_metals": (
        "Rebuilt from NASA's model, not copied: the parts joined into one mesh, turned so the craft faces along +Z "
        "with +Y up and scaled to metres from a dimension NASA states; the parts whose material NASA's model names as "
        "gold, brass, copper, silver foil, chrome, brushed metal, the mirror or solar cells given physically based "
        "materials of that kind, the others keeping NASA's own colour, and the normals made again, smooth within an "
        "angle and sharp beyond it."
    ),
    # lod0 for a model of flat colours whose materials NASA names as foils, silver and cells (recipe "changes": "foils")
    "lod0_foils": (
        "Rebuilt from NASA's model, not copied: the parts joined into one mesh, turned so the craft faces along +Z "
        "with +Y up and scaled to metres from a dimension NASA states; the parts whose material NASA's model names as "
        "gold foil, bronze, silver or solar cells given physically based materials of that kind, the bronze in NASA's "
        "own colour, the others keeping NASA's own colour, faces NASA's file keeps apart at shared corners welded, "
        "the normals made again, smooth within an angle and sharp beyond it, and fewer triangles kept at every level."
    ),
    # lod0 for a model whose parts all sample one texture of foil, dish and cells (recipe "changes": "mro")
    "lod0_mro": (
        "Rebuilt from NASA's model, not copied: the parts joined into one mesh, turned so the craft faces along +Z "
        "with +Y up and scaled to metres from a dimension NASA states; NASA's one texture of foil, dish and panels "
        "kept as the base colour, with metalness, roughness and surface relief added from its own colours and "
        "shading, the solar cells keeping it with one roughness and no metalness, the normals made again, smooth "
        "within an angle and sharp beyond it, fewer triangles kept at every level, and the texture re-encoded as WebP."
    ),
    # lod0 for a model of photo textures and gold foil with solar wings (recipe "changes": "photo_foil")
    "lod0_photo_foil": (
        "Rebuilt from NASA's model, not copied: the parts joined into one mesh, turned so the craft faces along +Z "
        "with +Y up and scaled to metres from a dimension NASA states; NASA's photo textures and gold foil kept as "
        "the base colour, with metalness, roughness and surface relief added from their own colours and shading, the "
        "silver foil made metal foil, the solar wings keeping their texture with one roughness and no metalness, the "
        "normals made again, smooth within an angle and sharp beyond it, and the textures re-encoded as WebP."
    ),
    # lod0 for photo textures, gold foil and solar wings, a printed label painted out ("changes": "photo_foil_label")
    "lod0_photo_foil_label": (
        "Rebuilt from NASA's model, not copied: the parts joined into one mesh, turned so the craft faces along +Z "
        "with +Y up and scaled to metres from a dimension NASA states; NASA's photo texture and gold foil kept as the "
        "base colour, with metalness, roughness and surface relief added from their own colours and shading, a small "
        "printed label in the photo painted over in the colour around it, the silver foil made metal foil and the "
        "black blanket black, the solar wings keeping their texture with one roughness and no metalness, the normals "
        "made again, smooth within an angle and sharp beyond it, and the textures re-encoded as WebP."
    ),
    # lod0 for photo texture, gold foil and solar panels, a printed label painted out (recipe "changes": "photo_label")
    "lod0_photo_label": (
        "Rebuilt from NASA's model, not copied: the parts joined into one mesh, turned so the craft faces along +Z "
        "with +Y up and scaled to metres from a dimension NASA states; NASA's photo texture and gold foil kept as the "
        "base colour, with metalness, roughness and surface relief added from their own colours and shading, a small "
        "printed label in the photo painted over in the colour around it, the silver foil made metal foil, the solar "
        "panels keeping their texture with one roughness and no metalness, the normals made again, smooth within an "
        "angle and sharp beyond it, and the textures re-encoded as WebP."
    ),
    # lod0 for named metals and blankets, gold foil and a white dish with baked shading (recipe "changes": "cassini")
    "lod0_cassini": (
        "Rebuilt from NASA's model, not copied: helper objects removed, the parts joined into one mesh, turned so the "
        "craft faces along +Z with +Y up and scaled to metres from a dimension NASA states; the parts NASA's model "
        "names as aluminium and black blanket given physically based materials of that kind, the gold foil and the "
        "photo texture given metalness, roughness and surface relief from their own colours, the dish painted white "
        "with NASA's baked shading moved to the occlusion slot, NASA's flat colours kept for the rest, the normals "
        "made again, smooth within an angle and sharp beyond it, and the textures re-encoded as WebP."
    ),
    # lod0 for photo texture and gold foil with a solar deck and black blanket (recipe "changes": "photo_deck")
    "lod0_photo_deck": (
        "Rebuilt from NASA's model, not copied: the parts joined into one mesh, turned so the craft faces along +Z "
        "with +Y up and scaled to metres from a dimension NASA states; NASA's photo texture and gold foil kept as the "
        "base colour, with metalness, roughness and surface relief added from their own colours and shading, the "
        "silver foil made metal foil and the black blanket black, the solar deck keeping its texture with one "
        "roughness and no metalness, the normals made again, smooth within an angle and sharp beyond it, and the "
        "textures re-encoded as WebP."
    ),
    # lod0 for flat colours named bronze, silver and cells, split triangles welded (recipe "changes": "named_welded")
    "lod0_named_welded": (
        "Rebuilt from NASA's model, not copied: the parts joined into one mesh, turned so the craft faces along +Z "
        "with +Y up and scaled to metres from a dimension NASA states; the parts whose material NASA's model names as "
        "bronze, silver or solar cells given physically based materials of that kind, the bronze in NASA's own "
        "colour, the others keeping NASA's own colour, faces NASA's file keeps apart at shared corners welded, the "
        "normals made again, smooth within an angle and sharp beyond it, and fewer triangles kept at every level."
    ),
    # lod0 for a model whose maps are averaged per part (recipe "changes": "mean")
    "lod0_mean": (
        "Rebuilt from NASA's model, not copied: the parts joined into one mesh, turned so the craft faces along +Z "
        "with +Y up and scaled to metres from a dimension NASA states; each part given one colour, metalness and "
        "roughness, the mean of NASA's own maps over its opaque pixels, so no mark printed in a map survives, with "
        "NASA's alpha kept as a mask where it cuts a lattice, faces NASA's file keeps apart at shared corners welded, "
        "the normals made again, smooth within an angle and sharp beyond it, and the masks encoded as WebP."
    ),
    # lod0 for a suit of NASA's flat colours (recipe "changes": "suit_flat")
    "lod0_suit_flat": (
        "Rebuilt from NASA's model, not copied: the parts joined into one mesh and turned so the suit faces along +Z "
        "with +Y up, at the metre scale NASA's model is built in; every material made opaque in NASA's colours (NASA's "
        "file marks most of them as fully transmissive, like glass), any NASA insignia decal removed with its faces, "
        "faces NASA's file keeps apart at shared corners welded, and the normals made again, smooth within an angle "
        "and sharp beyond it."
    ),
    # lod0 for a suit whose file holds parts twice and marks every material as glass (recipe "changes": "suit")
    # lod0 for a model NASA textured with its own colour, bump and specular maps (recipe "changes": "baked")
    "lod0_baked": (
        "Rebuilt from NASA's model, not copied: turned so the suit faces along +Z with +Y up, at the metre scale "
        "NASA's model is built in; NASA's colour map kept as the base colour, NASA's bump map turned into a normal map and the "
        "roughness taken from NASA's specular map; the NASA insignia and an emblem patch, decals of their own, removed "
        "with their faces and painted over in all three maps, as are a logo and its credit line in unused texture "
        "space, and the textures re-encoded as WebP."
    ),
    "lod0_suit": (
        "Rebuilt from NASA's model, not copied: the second copies of parts NASA's file holds twice removed, the parts "
        "joined into one mesh and turned so the suit faces along +Z with +Y up, at the metre scale NASA's model is "
        "built in; every material made opaque (NASA's file marks them all as fully transmissive, like glass), keeping "
        "NASA's colours, with the visor given gold and the metal fittings metalness, the astronaut office's emblem "
        "covered with the suit's white fabric, faces NASA's file keeps apart at shared corners welded, and the normals "
        "made again, smooth within an angle and sharp beyond it."
    ),
    "lod1": (
        "The same rebuild as lod0 with fewer triangles, removed by Blender's collapse decimation (the share kept is listed "
        "with the craft's facts), and textures at most 1024 pixels on a side."
    ),
    "lod2": (
        "The same rebuild as lod0 with fewer triangles still, removed by Blender's collapse decimation (the share kept "
        "is listed with the craft's facts), and textures at most 512 pixels on a side."
    ),
    "picker": "NASA's own render of its model, scaled down to 176 pixels on its longer side and re-encoded as WebP.",
    "picker_render": (
        "A picture of the rebuilt model rendered by Blender's Cycles with one light on a clear background, 176 pixels "
        "square, since NASA's own preview of this model is a screenshot of its modelling program."
    ),
    "picker_render_insignia": (
        "A picture of the rebuilt model rendered by Blender's Cycles with one light on a clear background, 176 pixels "
        "square, since NASA's own render of this model shows the NASA insignia the rebuild covers."
    ),
    "picker_render_white": (
        "A picture of the rebuilt model rendered by Blender's Cycles with one light on a clear background, 176 pixels "
        "square, since NASA's own render of this model is a small picture on white, which the picker shows as a white "
        "square."
    ),
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
SPEC_ROUGH_AT_BLACK = 0.9  # roughness where a specular map is black
SPEC_ROUGH_AT_WHITE = 0.3  # roughness where it is white
MEAN_EDGE = 128  # maps are averaged at this size
MEAN_ROUGHNESS_FACTOR = 0.5  # NASA's ISS materials multiply their green channel by 0.5 for roughness
MASK_EDGE = 512  # a kept alpha mask's largest edge
PICKER_SAMPLES = 32  # Cycles samples for a rendered picker picture
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
    colour of a PAINT_OUT_BAND_PX band around it, or of the box its "fill_from" names: NASA's insignia and logotype
    are not public domain, so a mark NASA photographed on the hardware is covered with the surface it is printed on."""
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
        if box.get("fill_from"):
            # a decal island filled with the median of a patch of the surface it sits on, named by its own box
            fx0, fy0, fx1, fy1 = box["fill_from"]
            ring = px[h - fy1 : h - fy0, fx0:fx1].reshape(-1, 4)
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
    if spec["role"] == "keep":
        # NASA's own physically based material, kept as NASA made it (gold foil with its own normal map).
        return source_mat
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
    elif spec["role"] == "baked_maps":
        # NASA's own maps: its colour map stays the base colour (marks painted out), its bump map becomes the normal
        # map, and its specular map gives the roughness (brighter specular, smoother); no part is metal.
        imgs = {n.image.name: n.image for n in source_mat.node_tree.nodes if n.type == "TEX_IMAGE" and n.image}
        color = next(i for k, i in imgs.items() if spec["color"] in k)
        bump = next(i for k, i in imgs.items() if spec["bump"] in k)
        specular = next(i for k, i in imgs.items() if spec["specular"] in k)
        if spec.get("paint_out"):
            # the same marks are in the bump and specular maps as relief and shine: covered there too
            color = painted_out(color, spec["paint_out"])
            bump = painted_out(bump, spec["paint_out"])
            specular = painted_out(specular, spec["paint_out"])
        base = nt.nodes.new("ShaderNodeTexImage")
        base.image = color
        links.new(base.outputs["Color"], bsdf.inputs["Base Color"])
        lum = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
        height = image_array(bump)[..., :3] @ lum
        gy, gx = np.gradient(height * spec["bump_strength"])
        n = np.dstack([-gx, -gy, np.ones_like(gx)])
        n /= np.linalg.norm(n, axis=-1, keepdims=True)
        nrm_arr = np.ones((*height.shape, 4), dtype=np.float32)
        nrm_arr[..., :3] = n * 0.5 + 0.5
        del n, gx, gy, height
        s = image_array(specular)[..., :3] @ lum
        orm_arr = np.ones((*s.shape, 4), dtype=np.float32)
        orm_arr[..., 1] = np.clip(SPEC_ROUGH_AT_BLACK - (SPEC_ROUGH_AT_BLACK - SPEC_ROUGH_AT_WHITE) * s, 0.0, 1.0)
        orm_arr[..., 2] = 0.0
        del s
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
    elif spec["role"] == "maps_mean":
        # One colour, metalness and roughness per part, the mean of NASA's own maps over the part's opaque pixels, so
        # no mark printed in a map survives; where NASA cuts the part with its colour map's alpha (a lattice, a
        # mesh), that alpha is kept as a mask on a texture of the one colour.
        nodes = source_mat.node_tree.nodes
        src_bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")

        def upstream_image(socket):
            stack = [socket]
            while stack:
                s = stack.pop()
                for link in s.links:
                    n = link.from_node
                    if n.type == "TEX_IMAGE" and n.image is not None:
                        return n.image, link.from_socket.name
                    stack.extend(i for i in n.inputs if i.is_linked)
            return None, None

        def small(img):
            copy = img.copy()
            copy.scale(min(img.size[0], MEAN_EDGE), min(img.size[1], MEAN_EDGE))
            arr = image_array(copy)
            bpy.data.images.remove(copy)
            return arr

        base_img, _ = upstream_image(src_bsdf.inputs["Base Color"])
        alpha_linked = src_bsdf.inputs["Alpha"].is_linked
        if base_img is not None:
            px = small(base_img)
            weight = px[..., 3] if alpha_linked else np.ones(px.shape[:2], dtype=np.float32)
            weight = np.where(weight > 0.5, 1.0, 0.0)
            if weight.sum() == 0:
                weight = np.ones_like(weight)
            srgb = px[..., :3]
            lin = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
            colour = (lin * weight[..., None]).sum((0, 1)) / weight.sum()
        else:
            colour = np.array(src_bsdf.inputs["Base Color"].default_value[:3], dtype=np.float32)
        metal_img, metal_socket = upstream_image(src_bsdf.inputs["Metallic"])
        rough_img, _ = upstream_image(src_bsdf.inputs["Roughness"])
        metallic = float(src_bsdf.inputs["Metallic"].default_value)
        roughness = float(src_bsdf.inputs["Roughness"].default_value)
        if metal_img is not None:
            m = small(metal_img)
            metallic = float(m[..., 2].mean())  # glTF's metal-roughness packing: blue is metalness
            if rough_img is metal_img:
                factor = MEAN_ROUGHNESS_FACTOR
                rough_math = next((n for n in nodes if n.type == "MATH" and n.operation == "MULTIPLY"), None)
                if rough_math is not None:
                    factor = next((float(i.default_value) for i in rough_math.inputs if not i.is_linked), factor)
                roughness = float(m[..., 1].mean()) * factor
        bsdf.inputs["Metallic"].default_value = min(max(metallic, 0.0), 1.0)
        bsdf.inputs["Roughness"].default_value = min(max(roughness, 0.0), 1.0)
        if alpha_linked and base_img is not None:
            w, h = base_img.size
            k = min(1.0, MASK_EDGE / max(w, h))
            mask_src = base_img.copy()
            mask_src.scale(max(1, round(w * k)), max(1, round(h * k)))
            arr = image_array(mask_src)
            bpy.data.images.remove(mask_src)
            enc = np.where(colour <= 0.0031308, colour * 12.92, 1.055 * np.power(np.maximum(colour, 0), 1 / 2.4) - 0.055)
            arr[..., :3] = enc
            mask = new_image(f"{name}_colour_mask", arr, non_color=False)
            mask.alpha_mode = "STRAIGHT"
            tex = nt.nodes.new("ShaderNodeTexImage")
            tex.image = mask
            links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
            cut = nt.nodes.new("ShaderNodeMath")
            cut.operation = "ROUND"
            links.new(tex.outputs["Alpha"], cut.inputs[0])
            links.new(cut.outputs[0], bsdf.inputs["Alpha"])
        else:
            bsdf.inputs["Base Color"].default_value = (*[float(c) for c in colour], 1.0)
    elif spec["role"] == "textured":
        # NASA's own texture as the base colour (a printed label, a flag), with a flat metalness and roughness.
        base = nt.nodes.new("ShaderNodeTexImage")
        base.image = src_img
        links.new(base.outputs["Color"], bsdf.inputs["Base Color"])
        bsdf.inputs["Metallic"].default_value = spec["metallic"]
        bsdf.inputs["Roughness"].default_value = spec["roughness"]
    elif spec["role"] == "pbr":
        # A flat physically based material for a part NASA's model names (gold mirror, silver blanket, black paint).
        bsdf.inputs["Base Color"].default_value = (*spec["base"], 1.0)
        bsdf.inputs["Metallic"].default_value = spec["metallic"]
        bsdf.inputs["Roughness"].default_value = spec["roughness"]
    elif spec["role"] == "nasa_flat":
        # NASA's own flat colour and roughness, kept as the model gives them, for a part no rule names.
        src = next(n for n in source_mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        bsdf.inputs["Base Color"].default_value = tuple(src.inputs["Base Color"].default_value)
        bsdf.inputs["Roughness"].default_value = src.inputs["Roughness"].default_value
        bsdf.inputs["Metallic"].default_value = 0.0
    else:
        raise SystemExit(f"unknown material role {spec['role']}")
    return mat


def material_for(recipe: dict, name: str) -> tuple[str, dict]:
    """The recipe's spec for a source material: by exact name, or by the first rule a part of whose name it holds."""
    if name in recipe.get("materials", {}):
        return name, recipe["materials"][name]
    low = name.lower()
    for i, rule in enumerate(recipe.get("material_rules", [])):
        if any(word in low for word in rule["match"]) and not any(word in low for word in rule.get("not", [])):
            return f"rule{i:02d}_{rule['match'][0] or 'other'}", rule
    raise SystemExit(f"the recipe has no role for material {name}")


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


def drop_small_parts(obj: bpy.types.Object, metres: float) -> int:
    """Remove each loose part whose bounding box is under `metres` across: at a far level it covers under a pixel."""
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    seen: set[int] = set()
    doomed = []
    removed = 0
    for start in bm.verts:
        if start.index in seen:
            continue
        part = [start]
        seen.add(start.index)
        i = 0
        while i < len(part):
            for e in part[i].link_edges:
                o = e.other_vert(part[i])
                if o.index not in seen:
                    seen.add(o.index)
                    part.append(o)
            i += 1
        co = np.array([v.co for v in part])
        if np.linalg.norm(co.max(0) - co.min(0)) < metres:
            doomed.extend(part)
            removed += 1
    bmesh.ops.delete(bm, geom=doomed, context="VERTS")
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return removed


def render_picker(obj: bpy.types.Object, path: Path) -> None:
    """The picker's picture where NASA's own preview cannot serve: the rebuilt model from its front quarter, Cycles,
    one key light, transparent background, PICKER_EDGE pixels on its longer side, WebP."""
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = PICKER_SAMPLES
    scene.cycles.use_denoising = True
    scene.render.film_transparent = True
    scene.render.resolution_x = PICKER_EDGE
    scene.render.resolution_y = PICKER_EDGE
    scene.render.image_settings.file_format = "WEBP"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.quality = 88
    world = bpy.data.worlds.new("picker") if scene.world is None else scene.world
    scene.world = world
    world.use_nodes = True
    next(n for n in world.node_tree.nodes if n.type == "BACKGROUND").inputs["Strength"].default_value = 0.15
    key = bpy.data.objects.new("picker_key", bpy.data.lights.new("picker_key", "SUN"))
    key.data.energy = 4.0
    key.rotation_euler = Vector((0.6, 1.0, -0.7)).normalized().to_track_quat("-Z", "Y").to_euler()
    scene.collection.objects.link(key)
    cam = bpy.data.objects.new("picker_cam", bpy.data.cameras.new("picker_cam"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    centre = sum(corners, Vector()) / 8
    radius = max((c - centre).length for c in corners)
    fov = cam.data.angle
    view = Vector((-0.55, -1.0, 0.3)).normalized()  # the front quarter (forward is Blender -Y), a little above
    cam.location = centre + view * (radius / math.sin(fov / 2))
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


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
    ap.add_argument("--full", action="store_true", help="a check, not for the web: every level keeps every triangle")
    args = ap.parse_args(argv)
    recipe = CRAFTS[args.craft]
    src = recipe["source"]
    folder = REPO_ROOT / CACHE / src["local"]
    model = folder / src["file"]
    check_source(model, src["bytes"], src["sha256"])
    picture = folder / src["picture"] if src.get("picture") else None
    if picture is not None:
        check_source(picture, src["picture_bytes"], src["picture_sha256"])
    if src.get("picker_render_reason"):
        # NASA's own render cannot serve (the recipe names why): the picker is rendered from the build instead
        picture = None
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
    if recipe.get("strip_unused_layers"):
        # Vertex colour layers and UV sets past the first, which no material here reads, would ride into every glb.
        for layer in list(craft.data.color_attributes):
            craft.data.color_attributes.remove(layer)
        while len(craft.data.uv_layers) > 1:
            craft.data.uv_layers.remove(craft.data.uv_layers[-1])

    # Faces NASA's file holds twice (a second copy of a part under another material) come out first.
    me = craft.data
    if recipe.get("drop_materials"):
        drop = {i for i, m in enumerate(me.materials) if m and m.name in recipe["drop_materials"]}
        if len(drop) != len(recipe["drop_materials"]):
            raise SystemExit("a material the recipe drops is not in the model")
        import bmesh

        bm = bmesh.new()
        bm.from_mesh(me)
        bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.material_index in drop], context="FACES")
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
        bm.to_mesh(me)
        bm.free()
        me.update()

    # Decals carrying a NASA mark: their own faces come out, so the surface under them shows, not their outline.
    if recipe.get("drop_decals"):
        import bmesh

        bm = bmesh.new()
        bm.from_mesh(me)
        uv = bm.loops.layers.uv.active
        doomed = []
        for f in bm.faces:
            for d in recipe["drop_decals"]:
                x0, y0, x1, y1 = d["box"]
                edge = d["texture_edge"]
                if all(x0 <= l[uv].uv.x * edge <= x1 and y0 <= (1 - l[uv].uv.y) * edge <= y1 for l in f.loops):
                    doomed.append(f)
                    break
        if not doomed:
            raise SystemExit("no decal face found in the recipe's boxes")
        bmesh.ops.delete(bm, geom=doomed, context="FACES")
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
        bm.to_mesh(me)
        bm.free()
        me.update()
        print(f"HANGAR {args.craft} decal faces removed {len(doomed)}")

    # Scale and origin from the part NASA states a dimension of, or NASA's model as built in metres.
    sf = recipe["scale_from"]
    if sf.get("native"):
        co = np.empty(len(me.vertices) * 3, dtype=np.float64)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        extent = float(co[:, 2].max() - co[:, 2].min())
        centre = Vector(((co.max(0) + co.min(0)) / 2).tolist())
        scale = 1.0
    else:
        if sf["metres"] is None:
            raise SystemExit("the recipe has no sourced dimension to scale from")
        if sf["material"] is None:
            # the whole model's extent along the axis (for a span NASA states end to end, such as landing legs)
            co = np.array([v.co for v in me.vertices])
        else:
            mat_index = [i for i, m in enumerate(me.materials) if m and m.name == sf["material"]]
            vidx = sorted({v for p in me.polygons if p.material_index in mat_index for v in p.vertices})
            co = np.array([me.vertices[i].co for i in vidx])
            band = sf.get("band")
            if band:
                # only the part of that material lying in a slab of the import frame (one face of a shield)
                b = "xyz".index(band["axis"])
                co = co[(co[:, b] >= band["min"]) & (co[:, b] <= band["max"])]
                if len(co) < 3:
                    raise SystemExit("the recipe's band holds no part of its material")
        axis = "xyz".index(sf["axis"])
        extent = co[:, axis].max() - co[:, axis].min()
        centre = Vector(((co.max(0) + co.min(0)) / 2).tolist())
        scale = sf["metres"] / extent
    m = orient_matrix(recipe["forward"], recipe["up"]) @ Matrix.Diagonal((scale, scale, scale, 1.0)) @ Matrix.Translation(-centre)
    me.transform(m)
    me.update()

    # Materials: one per recipe entry, so parts sharing a rule share one material (one draw each in three.js).
    built: dict[str, bpy.types.Material] = {}
    unassigned = None
    for slot in craft.material_slots:
        if slot.material is None:
            # a part NASA's file gives no material: Blender's own default surface, through the recipe's rules
            if unassigned is None:
                unassigned = bpy.data.materials.new("unassigned")
                unassigned.use_nodes = True
            slot.material = unassigned
    for slot in craft.material_slots:
        key, spec = material_for(recipe, slot.material.name)
        if spec["role"] in ("nasa_flat", "maps_mean"):
            key = f"{key}_{slot.material.name}"
        if key not in built:
            built[key] = build_material(f"{args.craft}_{key}" if key.startswith("rule") else f"{args.craft}_{slot.material.name}", spec, slot.material)
        slot.material = built[key]
    # Slots sharing a material become one slot, so each material is one glTF primitive.
    first: dict[str, int] = {}
    remap = np.array([first.setdefault(s.material.name, i) for i, s in enumerate(craft.material_slots)], dtype=np.int32)
    if len(first) < len(craft.material_slots):
        idx = np.empty(len(craft.data.polygons), dtype=np.int32)
        craft.data.polygons.foreach_get("material_index", idx)
        craft.data.polygons.foreach_set("material_index", remap[idx])
        craft.data.update()
        bpy.context.view_layer.objects.active = craft
        bpy.ops.object.material_slot_remove_unused()

    # Optional clean-up for dense flat sheets: dissolve faces that lie flat within an angle, keeping material borders,
    # then Blender's own normals, smooth within an angle and sharp beyond it.
    if recipe.get("planar_deg"):
        bpy.context.view_layer.objects.active = craft
        dis = craft.modifiers.new("planar", "DECIMATE")
        dis.decimate_type = "DISSOLVE"
        dis.angle_limit = math.radians(recipe["planar_deg"])
        dis.delimit = {"MATERIAL"}
        bpy.ops.object.modifier_apply(modifier=dis.name)
        tri = craft.modifiers.new("tri", "TRIANGULATE")
        bpy.ops.object.modifier_apply(modifier=tri.name)
        craft.data.validate(verbose=False, clean_customdata=False)
    if recipe.get("weld_m"):
        # Faces NASA's file keeps apart at shared corners are welded (vertices closer than weld_m metres become
        # one), so normals can be smoothed across them and decimation sees one surface.
        import bmesh

        bm = bmesh.new()
        bm.from_mesh(craft.data)
        before = len(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=recipe["weld_m"])
        print(f"HANGAR {args.craft} welded {before - len(bm.verts)} of {before} vertices")
        bm.to_mesh(craft.data)
        bm.free()
        craft.data.update()
    if recipe.get("smooth_deg"):
        bpy.context.view_layer.objects.active = craft
        craft.select_set(True)
        if craft.data.has_custom_normals:
            bpy.ops.mesh.customdata_custom_splitnormals_clear()
        bpy.ops.object.shade_smooth_by_angle(angle=math.radians(recipe["smooth_deg"]))

    # Levels of detail.
    tris0 = sum(len(p.vertices) - 2 for p in me.polygons)
    for level, ratio in recipe.get("lod_ratio", LOD_RATIO).items():
        ratio = 1.0 if args.full else ratio
        obj = craft.copy()
        obj.data = craft.data.copy()
        obj.name = f"{args.craft}_{level}"
        bpy.context.scene.collection.objects.link(obj)
        for slot in obj.material_slots:
            mat = slot.material.copy()
            for node in mat.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image is not None:
                    node.image = resized(node.image, {**TEXTURE_EDGE, **recipe.get("texture_edge", {})}[level], level)
            slot.material = mat
        if ratio < 1.0:
            dec = obj.modifiers.new("lod", "DECIMATE")
            dec.decimate_type = "COLLAPSE"
            dec.ratio = ratio
            dec.use_collapse_triangulate = True
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.modifier_apply(modifier=dec.name)
            obj.data.validate(verbose=False, clean_customdata=False)
        small = recipe.get("drop_parts_under_m", {}).get(level)
        if small:
            removed = drop_small_parts(obj, small)
            print(f"HANGAR {args.craft} {level} parts under {small} m removed {removed}")
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
    if picture is None:
        render_picker(craft, out / f"{args.craft}_picker.webp")
        print(f"HANGAR {args.craft} scale {scale:.6f} metres per source unit, scale part extent {extent:.4f}")
        return
    pic = bpy.data.images.load(str(picture))
    w, h = pic.size
    k = PICKER_EDGE / max(w, h)
    pic.scale(max(1, round(w * k)), max(1, round(h * k)))
    pic.filepath_raw = str(out / f"{args.craft}_picker.webp")
    pic.file_format = "WEBP"
    bpy.context.scene.render.image_settings.quality = 88
    pic.save(quality=88)
    print(f"HANGAR {args.craft} scale {scale:.6f} metres per source unit, scale part extent {extent:.4f}")
    if args.blend:
        craft.hide_render = False
        bpy.ops.wm.save_as_mainfile(filepath=str(Path(args.blend).resolve()))


main()
