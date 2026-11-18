"""Earth day/night surface, Bruneton volume atmosphere with airglow, and tidally locked Moon builders."""

from __future__ import annotations

import math

from mathutils import Matrix, Vector

from scripts.lookdev.earth_night.constants import (
    AIRGLOW_ALTITUDE_M,
    AIRGLOW_COLOR,
    AIRGLOW_SIGMA_M,
    ATMOSPHERE_TOP_M,
    EARTH_RADIUS_KM,
    EARTH_RADIUS_M,
    MIE_ABSORB_PER_M,
    MIE_SCALE_HEIGHT_M,
    MIE_SCATTER_PER_M,
    MOON_RADIUS,
    OZONE_ABSORB_PER_M,
    OZONE_CENTER_M,
    OZONE_HALF_WIDTH_M,
    RAYLEIGH_PER_M,
    RAYLEIGH_SCALE_HEIGHT_M,
    SRC,
)
from scripts.lookdev.earth_night.shader_nodes import (
    chain_add_shaders,
    create_smooth_uv_sphere,
    equirect_uv,
    exp_density,
    image_node,
    link,
    math_node,
    new_material,
    node,
)

_BLACK_MARBLE_MASK_MIN = 0.10
_BLACK_MARBLE_MASK_MAX = 0.55
_EARTH_ROUGHNESS = 0.6
_MIE_ANISOTROPY = 0.8
_ATMOSPHERE_VOLUME_STEP_RATE = 0.02
_J2000_MEAN_OBLIQUITY_DEG = 23.43929111


def earth(gmst_deg: float, city_radiance: float):
    obj = create_smooth_uv_sphere("Earth", segments=512, ring_count=256, radius=1.0)
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
    mask.inputs["From Min"].default_value = _BLACK_MARBLE_MASK_MIN
    mask.inputs["From Max"].default_value = _BLACK_MARBLE_MASK_MAX
    link(t, lum.outputs[0], mask.inputs["Value"])
    lights = node(t, "ShaderNodeVectorMath", (380, -120), operation="SCALE")
    link(t, night.outputs["Color"], lights.inputs[0])
    link(t, mask.outputs["Result"], lights.inputs["Scale"])

    bsdf = node(t, "ShaderNodeBsdfPrincipled", (600, 200))
    link(t, day.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = _EARTH_ROUGHNESS
    link(t, lights.outputs["Vector"], bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = city_radiance
    out = node(t, "ShaderNodeOutputMaterial", (900, 200))
    link(t, bsdf.outputs["BSDF"], out.inputs["Surface"])
    obj.data.materials.append(mat)
    return obj


def atmosphere(airglow_radiance: float):
    top = 1.0 + ATMOSPHERE_TOP_M / EARTH_RADIUS_M
    obj = create_smooth_uv_sphere("Atmosphere", segments=256, ring_count=128, radius=top)

    mat, t = new_material("Atmosphere")
    mat.cycles.volume_step_rate = _ATMOSPHERE_VOLUME_STEP_RATE
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
    mie.inputs["Anisotropy"].default_value = _MIE_ANISOTROPY
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

    combined = chain_add_shaders(
        t,
        [rayleigh.outputs[0], mie.outputs[0], mie_abs.outputs[0], ozone.outputs[0], airglow.outputs[0]],
        [(250, 200), (420, 0), (590, -150), (760, -300)],
    )
    out = node(t, "ShaderNodeOutputMaterial", (950, 0))
    link(t, combined, out.inputs["Volume"])
    obj.data.materials.append(mat)
    return obj


def moon(position: Vector):
    obj = create_smooth_uv_sphere("Moon", segments=256, ring_count=128, radius=MOON_RADIUS)
    # Tidally locked: longitude 0 (object +X) faces Earth; north roughly along the ecliptic pole
    x_axis = (-position).normalized()
    eps = math.radians(_J2000_MEAN_OBLIQUITY_DEG)
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
    bump.inputs["Distance"].default_value = 1.0 / EARTH_RADIUS_KM  # elevation is in km; one unit is 6371 km
    link(t, elevation.outputs["Color"], bump.inputs["Height"])
    bsdf = node(t, "ShaderNodeBsdfPrincipled", (350, 150))
    link(t, color.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 1.0
    link(t, bump.outputs["Normal"], bsdf.inputs["Normal"])
    out = node(t, "ShaderNodeOutputMaterial", (650, 150))
    link(t, bsdf.outputs["BSDF"], out.inputs["Surface"])
    obj.data.materials.append(mat)
    return obj


__all__ = [
    "atmosphere",
    "earth",
    "moon",
]
