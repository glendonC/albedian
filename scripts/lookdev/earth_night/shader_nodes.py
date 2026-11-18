"""Reusable Cycles shader-node, equirectangular UV, exponential density, and UV-sphere builders."""

from __future__ import annotations

import math
from pathlib import Path

import bpy

from scripts.lookdev.earth_night.constants import EARTH_RADIUS_M


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
    """Object-space direction to equirectangular `(u, v)`: `u = 0.5` at `+X` (longitude 0), `v = 1` at `+Z`."""
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


def exp_density(t, height, scale_height_m: float, coefficient: float, loc):
    """`coefficient * exp(-height / H)`, with `height` and `H` in Earth radii."""
    x = math_node(t, "MULTIPLY", height, -EARTH_RADIUS_M / scale_height_m, loc)
    e = math_node(t, "EXPONENT", x, None, (loc[0] + 180, loc[1]))
    return math_node(t, "MULTIPLY", e, coefficient, (loc[0] + 360, loc[1]))


def create_smooth_uv_sphere(name: str, segments: int, ring_count: int, radius: float):
    """Create a smooth-shaded UV sphere mesh object named `name`."""
    bpy.ops.mesh.primitive_uv_sphere_add(segments=segments, ring_count=ring_count, radius=radius)
    obj = bpy.context.active_object
    obj.name = name
    bpy.ops.object.shade_smooth()
    return obj


def chain_add_shaders(tree, shader_outputs: list, locations: list[tuple[int, int]]):
    """Sum two or more shader outputs via left-folded `ShaderNodeAddShader` nodes."""
    acc = shader_outputs[0]
    for shader_out, loc in zip(shader_outputs[1:], locations, strict=True):
        add_node = node(tree, "ShaderNodeAddShader", loc)
        link(tree, acc, add_node.inputs[0])
        link(tree, shader_out, add_node.inputs[1])
        acc = add_node.outputs[0]
    return acc


__all__ = [
    "chain_add_shaders",
    "create_smooth_uv_sphere",
    "equirect_uv",
    "exp_density",
    "image_node",
    "link",
    "math_node",
    "new_material",
    "node",
]
