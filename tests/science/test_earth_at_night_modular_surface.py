"""Characterization and property-based surface tests for `scripts/lookdev/earth_at_night.py`.

Exercises the full Blender Cycles scene, shader node graphs, atmosphere volume profiles,
tidally locked Moon frame geometry, compositor glare setup, Cycles GPU render configuration,
CLI argument parsing, and `__main__` execution using deterministic pure-Python `bpy` and
`mathutils` stand-ins (no external Blender binary required, zero test skips). When the
`scripts/lookdev/earth_night/` subpackage is present, also verifies explicit `__all__`
exports, pure module acyclicity, and import-safe loading without premature `main()` exit.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import json
import math
from pathlib import Path
import runpy
import sys
import types
from typing import Any

import numpy as np
import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "lookdev" / "earth_at_night.py"
SUBPKG_DIR = REPO_ROOT / "scripts" / "lookdev" / "earth_night"

EXPECTED_CONSTANTS = {
    "REPO_ROOT",
    "SRC",
    "EARTH_RADIUS_M",
    "MOON_RADIUS",
    "RAYLEIGH_PER_M",
    "RAYLEIGH_SCALE_HEIGHT_M",
    "MIE_SCATTER_PER_M",
    "MIE_ABSORB_PER_M",
    "MIE_SCALE_HEIGHT_M",
    "OZONE_ABSORB_PER_M",
    "OZONE_CENTER_M",
    "OZONE_HALF_WIDTH_M",
    "ATMOSPHERE_TOP_M",
    "AIRGLOW_ALTITUDE_M",
    "AIRGLOW_SIGMA_M",
    "AIRGLOW_COLOR",
}

EXPECTED_FUNCTIONS = {
    "parse_args",
    "node",
    "link",
    "math_node",
    "image_node",
    "equirect_uv",
    "new_material",
    "earth",
    "exp_density",
    "atmosphere",
    "moon",
    "sun",
    "stars",
    "camera",
    "grade",
    "render_settings",
    "main",
}

EXPECTED_PUBLIC_SYMBOLS = EXPECTED_CONSTANTS | EXPECTED_FUNCTIONS

_PK = inspect.Parameter.POSITIONAL_OR_KEYWORD
_VK = inspect.Parameter.VAR_KEYWORD
_EMPTY = inspect.Parameter.empty

EXPECTED_SIGNATURES: dict[str, list[tuple[str, object, object]]] = {
    "parse_args": [],
    "node": [("tree", _PK, _EMPTY), ("kind", _PK, _EMPTY), ("loc", _PK, (0, 0)), ("props", _VK, _EMPTY)],
    "link": [("tree", _PK, _EMPTY), ("out_socket", _PK, _EMPTY), ("in_socket", _PK, _EMPTY)],
    "math_node": [
        ("tree", _PK, _EMPTY),
        ("op", _PK, _EMPTY),
        ("a", _PK, _EMPTY),
        ("b", _PK, None),
        ("loc", _PK, (0, 0)),
    ],
    "image_node": [
        ("tree", _PK, _EMPTY),
        ("path", _PK, _EMPTY),
        ("colorspace", _PK, _EMPTY),
        ("loc", _PK, (0, 0)),
    ],
    "equirect_uv": [("tree", _PK, _EMPTY), ("loc", _PK, (-1400, 0))],
    "new_material": [("name", _PK, _EMPTY)],
    "earth": [("gmst_deg", _PK, _EMPTY), ("city_radiance", _PK, _EMPTY)],
    "exp_density": [
        ("t", _PK, _EMPTY),
        ("height", _PK, _EMPTY),
        ("scale_height_m", _PK, _EMPTY),
        ("coefficient", _PK, _EMPTY),
        ("loc", _PK, _EMPTY),
    ],
    "atmosphere": [("airglow_radiance", _PK, _EMPTY)],
    "moon": [("position", _PK, _EMPTY)],
    "sun": [("direction", _PK, _EMPTY), ("irradiance", _PK, _EMPTY)],
    "stars": [("strength", _PK, _EMPTY)],
    "camera": [("position", _PK, _EMPTY), ("lens_mm", _PK, _EMPTY)],
    "grade": [("bloom_strength", _PK, _EMPTY)],
    "render_settings": [("args", _PK, _EMPTY)],
    "main": [],
}


# =============================================================================================
# Deterministic pure-Python `mathutils` and `bpy` stand-ins with node-graph evaluation
# =============================================================================================


class _FakeQuat:
    def __init__(self, direction: tuple[float, float, float], track: str, up: str) -> None:
        self.direction = direction
        self.track = track
        self.up = up

    def to_euler(self) -> tuple[str, str, tuple[float, float, float]]:
        return (self.track, self.up, self.direction)


class FakeVector:
    def __init__(self, coords: Any) -> None:
        arr = np.asarray(list(coords), dtype=np.float64)
        assert arr.shape == (3,)
        self._v = arr

    def __neg__(self) -> FakeVector:
        return FakeVector(-self._v)

    def __sub__(self, other: FakeVector) -> FakeVector:
        return FakeVector(self._v - other._v)

    def __rmul__(self, scalar: float) -> FakeVector:
        return FakeVector(float(scalar) * self._v)

    def __mul__(self, scalar: float) -> FakeVector:
        return FakeVector(self._v * float(scalar))

    def __iter__(self):
        return iter(float(x) for x in self._v)

    def normalized(self) -> FakeVector:
        norm = float(np.linalg.norm(self._v))
        return FakeVector(self._v / norm)

    def dot(self, other: FakeVector) -> float:
        return float(np.dot(self._v, other._v))

    def cross(self, other: FakeVector) -> FakeVector:
        return FakeVector(np.cross(self._v, other._v))

    def to_track_quat(self, track: str, up: str) -> _FakeQuat:
        u = self.normalized()
        return _FakeQuat(tuple(float(c) for c in u._v), track, up)


class FakeMatrix:
    def __init__(self, rows: Any) -> None:
        self._m = np.asarray([list(r) for r in rows], dtype=np.float64)

    def transposed(self) -> FakeMatrix:
        return FakeMatrix(self._m.T)

    def to_4x4(self) -> FakeMatrix:
        m4 = np.eye(4, dtype=np.float64)
        m4[:3, :3] = self._m
        return FakeMatrix(m4)

    @classmethod
    def Translation(cls, vec: FakeVector) -> FakeMatrix:
        m4 = np.eye(4, dtype=np.float64)
        m4[:3, 3] = vec._v
        return cls(m4)

    def __matmul__(self, other: FakeMatrix) -> FakeMatrix:
        return FakeMatrix(self._m @ other._m)

    @property
    def array(self) -> np.ndarray:
        return self._m.copy()


class _FakeSocket:
    def __init__(self, node: _FakeNode, name: str, default_value: Any = 0.0, reject_values: set[Any] | None = None) -> None:
        self.node = node
        self.name = name
        self._default_value = default_value
        self._reject_values = reject_values or set()
        self.incoming: _FakeSocket | None = None

    @property
    def default_value(self) -> Any:
        return self._default_value

    @default_value.setter
    def default_value(self, value: Any) -> None:
        if value in self._reject_values:
            raise TypeError(f"Socket {self.name} rejected {value!r}")
        self._default_value = value


class _SocketDict:
    """Supports both integer index (`inputs[0]`) and socket name (`inputs['Color']`) lookup."""

    def __init__(self, node: _FakeNode, names: list[tuple[str, Any]], reject_map: dict[str, set[Any]] | None = None) -> None:
        self._node = node
        self._reject_map = reject_map or {}
        self._list: list[_FakeSocket] = []
        self._by_name: dict[str, _FakeSocket] = {}
        for name, default in names:
            sock = _FakeSocket(node, name, default, self._reject_map.get(name))
            self._list.append(sock)
            self._by_name[name] = sock

    def __getitem__(self, key: int | str) -> _FakeSocket:
        if isinstance(key, int):
            while len(self._list) <= key:
                sock = _FakeSocket(self._node, f"idx_{len(self._list)}", 0.0)
                self._list.append(sock)
            return self._list[key]
        if key not in self._by_name:
            sock = _FakeSocket(self._node, key, 0.0, self._reject_map.get(key))
            self._list.append(sock)
            self._by_name[key] = sock
        return self._by_name[key]


_NODE_SPECS: dict[str, tuple[list[tuple[str, Any]], list[tuple[str, Any]]]] = {
    "ShaderNodeMath": ([("Value", 0.0), ("Value_001", 0.0), ("Value_002", 0.0)], [("Value", 0.0)]),
    "ShaderNodeVectorMath": ([("Vector", (0.0, 0.0, 0.0)), ("Vector_001", (0.0, 0.0, 0.0)), ("Scale", 1.0)], [("Vector", (0.0, 0.0, 0.0)), ("Value", 0.0)]),
    "ShaderNodeTexCoord": ([], [("Object", (1.0, 0.0, 0.0)), ("Generated", (0.0, 0.0, 1.0))]),
    "ShaderNodeSeparateXYZ": ([("Vector", (0.0, 0.0, 0.0))], [("X", 0.0), ("Y", 0.0), ("Z", 0.0)]),
    "ShaderNodeCombineXYZ": ([("X", 0.0), ("Y", 0.0), ("Z", 0.0)], [("Vector", (0.0, 0.0, 0.0))]),
    "ShaderNodeMapRange": (
        [("Value", 0.0), ("From Min", 0.0), ("From Max", 1.0), ("To Min", 0.0), ("To Max", 1.0)],
        [("Result", 0.0)],
    ),
    "ShaderNodeRGBToBW": ([("Color", (0.0, 0.0, 0.0))], [("Val", 0.0)]),
    "ShaderNodeTexImage": ([("Vector", (0.0, 0.0, 0.0))], [("Color", (0.5, 0.5, 0.5))]),
    "ShaderNodeTexEnvironment": ([("Vector", (0.0, 0.0, 0.0))], [("Color", (0.2, 0.3, 0.4))]),
    "ShaderNodeBsdfPrincipled": (
        [("Base Color", (0.8, 0.8, 0.8, 1.0)), ("Roughness", 0.5), ("Emission Color", (0.0, 0.0, 0.0, 1.0)), ("Emission Strength", 0.0), ("Normal", (0.0, 0.0, 1.0))],
        [("BSDF", "bsdf")],
    ),
    "ShaderNodeOutputMaterial": ([("Surface", None), ("Volume", None)], []),
    "ShaderNodeVolumeScatter": ([("Color", (1.0, 1.0, 1.0, 1.0)), ("Density", 1.0), ("Anisotropy", 0.0)], [("Volume", "scatter")]),
    "ShaderNodeVolumeAbsorption": ([("Color", (1.0, 1.0, 1.0, 1.0)), ("Density", 1.0)], [("Volume", "absorb")]),
    "ShaderNodeEmission": ([("Color", (1.0, 1.0, 1.0, 1.0)), ("Strength", 1.0)], [("Emission", "emission")]),
    "ShaderNodeAddShader": ([("Shader", None), ("Shader_001", None)], [("Shader", "add")]),
    "ShaderNodeBump": ([("Strength", 1.0), ("Distance", 1.0), ("Height", 0.0)], [("Normal", (0.0, 0.0, 1.0))]),
    "ShaderNodeMapping": ([("Vector", (0.0, 0.0, 0.0)), ("Location", (0.0, 0.0, 0.0)), ("Rotation", (0.0, 0.0, 0.0)), ("Scale", (1.0, 1.0, 1.0))], [("Vector", (0.0, 0.0, 0.0))]),
    "ShaderNodeLightPath": ([], [("Is Camera Ray", 1.0)]),
    "ShaderNodeBackground": ([("Color", (0.0, 0.0, 0.0)), ("Strength", 1.0)], [("Background", "bg")]),
    "ShaderNodeOutputWorld": ([("Surface", None)], []),
    "CompositorNodeRLayers": ([], [("Image", "render_image")]),
    "CompositorNodeGlare": ([("Image", None), ("Type", "Streaks"), ("Threshold", 1.0), ("Strength", 0.5), ("Clamp", False), ("Maximum", 10.0)], [("Image", "glared_image")]),
    "NodeGroupOutput": ([("Image", None)], []),
}


class _FakeNode:
    def __init__(self, kind: str, glare_reject_bloom_titlecase: bool = False) -> None:
        self.kind = kind
        self.location = (0, 0)
        in_spec, out_spec = _NODE_SPECS.get(kind, ([], [("Out", 0.0)]))
        reject = {"Type": {"Bloom"}} if (kind == "CompositorNodeGlare" and glare_reject_bloom_titlecase) else None
        self.inputs = _SocketDict(self, in_spec, reject)
        self.outputs = _SocketDict(self, out_spec)


class _FakeLinks:
    def __init__(self) -> None:
        self.items: list[tuple[_FakeSocket, _FakeSocket]] = []

    def new(self, out_socket: _FakeSocket, in_socket: _FakeSocket) -> None:
        in_socket.incoming = out_socket
        self.items.append((out_socket, in_socket))


class _FakeNodeCollection:
    def __init__(self, glare_reject_bloom_titlecase: bool = False) -> None:
        self.items: list[_FakeNode] = []
        self._reject = glare_reject_bloom_titlecase

    def new(self, kind: str) -> _FakeNode:
        n = _FakeNode(kind, self._reject)
        self.items.append(n)
        return n

    def clear(self) -> None:
        self.items.clear()


class _FakeInterface:
    def __init__(self) -> None:
        self.sockets: list[dict[str, str]] = []

    def new_socket(self, *, name: str, in_out: str, socket_type: str) -> None:
        self.sockets.append({"name": name, "in_out": in_out, "socket_type": socket_type})


class FakeNodeTree:
    def __init__(self, name: str = "", tree_type: str = "", glare_reject_bloom_titlecase: bool = False) -> None:
        self.name = name
        self.tree_type = tree_type
        self.nodes = _FakeNodeCollection(glare_reject_bloom_titlecase)
        self.links = _FakeLinks()
        self.interface = _FakeInterface()


def _eval_socket(
    sock: _FakeSocket,
    obj_coord: tuple[float, float, float] = (1.0, 0.0, 0.0),
    image_rgb: dict[str, tuple[float, float, float]] | None = None,
) -> Any:
    if sock.incoming is not None:
        return _eval_node_output(sock.incoming, obj_coord, image_rgb)
    return sock.default_value


def _eval_node_output(
    out_sock: _FakeSocket,
    obj_coord: tuple[float, float, float] = (1.0, 0.0, 0.0),
    image_rgb: dict[str, tuple[float, float, float]] | None = None,
) -> Any:
    n = out_sock.node
    if n.kind == "ShaderNodeTexCoord":
        return obj_coord
    if n.kind == "ShaderNodeVectorMath":
        op = getattr(n, "operation")
        v0 = np.asarray(_eval_socket(n.inputs[0], obj_coord, image_rgb), dtype=np.float64)
        if op == "NORMALIZE":
            return tuple(float(c) for c in (v0 / np.linalg.norm(v0)))
        if op == "LENGTH":
            return float(np.linalg.norm(v0))
        if op == "SCALE":
            sc = float(_eval_socket(n.inputs["Scale"], obj_coord, image_rgb))
            return tuple(float(c) for c in (v0 * sc))
    if n.kind == "ShaderNodeSeparateXYZ":
        vec = _eval_socket(n.inputs[0], obj_coord, image_rgb)
        return {"X": float(vec[0]), "Y": float(vec[1]), "Z": float(vec[2])}[out_sock.name]
    if n.kind == "ShaderNodeCombineXYZ":
        return (
            float(_eval_socket(n.inputs["X"], obj_coord, image_rgb)),
            float(_eval_socket(n.inputs["Y"], obj_coord, image_rgb)),
            float(_eval_socket(n.inputs["Z"], obj_coord, image_rgb)),
        )
    if n.kind == "ShaderNodeMath":
        op = getattr(n, "operation")
        a = float(_eval_socket(n.inputs[0], obj_coord, image_rgb))
        b = float(_eval_socket(n.inputs[1], obj_coord, image_rgb))
        c = float(_eval_socket(n.inputs[2], obj_coord, image_rgb))
        if op == "ARCTAN2":
            return math.atan2(a, b)
        if op == "ARCSINE":
            return math.asin(max(-1.0, min(1.0, a)))
        if op == "MULTIPLY_ADD":
            return a * b + c
        if op == "SUBTRACT":
            return a - b
        if op == "ADD":
            return a + b
        if op == "MULTIPLY":
            return a * b
        if op == "MAXIMUM":
            return max(a, b)
        if op == "ABSOLUTE":
            return abs(a)
        if op == "EXPONENT":
            return math.exp(a)
    if n.kind == "ShaderNodeTexImage":
        img_path = Path(n.image.filepath).name
        return (image_rgb or {}).get(img_path, (0.5, 0.5, 0.5))
    if n.kind == "ShaderNodeRGBToBW":
        r, g, b = _eval_socket(n.inputs[0], obj_coord, image_rgb)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    if n.kind == "ShaderNodeMapRange":
        val = float(_eval_socket(n.inputs["Value"], obj_coord, image_rgb))
        fmin = float(_eval_socket(n.inputs["From Min"], obj_coord, image_rgb))
        fmax = float(_eval_socket(n.inputs["From Max"], obj_coord, image_rgb))
        tmin = float(_eval_socket(n.inputs["To Min"], obj_coord, image_rgb))
        tmax = float(_eval_socket(n.inputs["To Max"], obj_coord, image_rgb))
        t = max(0.0, min(1.0, (val - fmin) / (fmax - fmin)))
        return tmin + t * (tmax - tmin)
    if n.kind == "ShaderNodeAddShader":
        left = _eval_socket(n.inputs[0], obj_coord, image_rgb)
        right = _eval_socket(n.inputs[1], obj_coord, image_rgb)
        items: list[Any] = []
        for part in (left, right):
            if isinstance(part, list):
                items.extend(part)
            else:
                items.append(part)
        return items
    if n.kind == "ShaderNodeLightPath":
        return float(out_sock.default_value)
    if n.kind in ("ShaderNodeVolumeScatter", "ShaderNodeVolumeAbsorption", "ShaderNodeEmission"):
        return [n]
    raise AssertionError(f"Unhandled node kind in test evaluator: {n.kind}")


def _build_fake_blender(glare_reject_bloom_titlecase: bool = False) -> tuple[types.ModuleType, types.ModuleType]:
    bpy = types.ModuleType("bpy")

    class _CyclesMatSettings:
        def __init__(self) -> None:
            self.volume_step_rate = 1.0

    class _Material:
        def __init__(self, name: str) -> None:
            self.name = name
            self.use_nodes = False
            self.node_tree = FakeNodeTree(name, "ShaderNodeTree")
            # Real Blender pre-populates default nodes until nodes.clear() is called
            self.node_tree.nodes.new("DefaultPreExistingNode")
            self.cycles = _CyclesMatSettings()

    class _Image:
        def __init__(self, filepath: str, check_existing: bool) -> None:
            self.filepath = filepath
            self.check_existing = check_existing
            self.colorspace_settings = types.SimpleNamespace(name="sRGB")

    class _ObjectData:
        def __init__(self) -> None:
            self.materials: list[_Material] = []

    class _Object:
        def __init__(self, name: str, data: Any = None) -> None:
            self.name = name
            self.data = data if data is not None else _ObjectData()
            self.rotation_euler = (0.0, 0.0, 0.0)
            self.location: Any = (0.0, 0.0, 0.0)
            self.matrix_world: Any = None
            self.shaded_smooth = False
            self.sphere_params: dict[str, Any] | None = None

    materials: list[_Material] = []
    images: list[_Image] = []
    lights: list[Any] = []
    worlds: list[Any] = []
    cameras: list[Any] = []
    objects: list[_Object] = []
    node_groups: list[FakeNodeTree] = []
    linked_objects: list[_Object] = []
    renders_triggered: list[bool] = []

    devices = [
        types.SimpleNamespace(type="CPU", use=True),
        types.SimpleNamespace(type="METAL", use=False),
    ]
    cycles_prefs = types.SimpleNamespace(
        compute_device_type="NONE",
        devices=devices,
        get_devices_called=0,
    )

    def _get_devices() -> None:
        cycles_prefs.get_devices_called += 1

    cycles_prefs.get_devices = _get_devices

    scene = types.SimpleNamespace(
        collection=types.SimpleNamespace(objects=types.SimpleNamespace(link=linked_objects.append)),
        world=None,
        camera=None,
        compositing_node_group=None,
        render=types.SimpleNamespace(
            engine="BLENDER_EEVEE",
            resolution_x=0,
            resolution_y=0,
            resolution_percentage=0,
            filepath="",
            image_settings=types.SimpleNamespace(file_format="", color_depth=""),
        ),
        cycles=types.SimpleNamespace(
            device="CPU",
            samples=0,
            use_adaptive_sampling=False,
            adaptive_threshold=0.0,
            use_denoising=False,
            max_bounces=0,
            volume_bounces=0,
            volume_step_rate=0.0,
            volume_max_steps=0,
            sample_clamp_indirect=0.0,
        ),
        view_settings=types.SimpleNamespace(view_transform="Standard", exposure=0.0),
    )

    context = types.SimpleNamespace(
        active_object=None,
        scene=scene,
        preferences=types.SimpleNamespace(addons={"cycles": types.SimpleNamespace(preferences=cycles_prefs)}),
    )

    def _uv_sphere_add(*, segments: int, ring_count: int, radius: float) -> None:
        obj = _Object("Sphere")
        obj.sphere_params = {"segments": segments, "ring_count": ring_count, "radius": radius}
        objects.append(obj)
        context.active_object = obj

    def _shade_smooth() -> None:
        assert context.active_object is not None
        context.active_object.shaded_smooth = True

    def _read_factory_settings(*, use_empty: bool) -> None:
        context.factory_reset_empty = use_empty

    def _render(*, write_still: bool) -> None:
        renders_triggered.append(write_still)

    bpy.context = context
    bpy.ops = types.SimpleNamespace(
        mesh=types.SimpleNamespace(primitive_uv_sphere_add=_uv_sphere_add),
        object=types.SimpleNamespace(shade_smooth=_shade_smooth),
        wm=types.SimpleNamespace(read_factory_settings=_read_factory_settings),
        render=types.SimpleNamespace(render=_render),
    )
    def _new_world(name: str) -> Any:
        wt = FakeNodeTree(name, "ShaderNodeTree")
        wt.nodes.new("DefaultPreExistingWorldNode")
        w = types.SimpleNamespace(name=name, use_nodes=False, node_tree=wt)
        worlds.append(w)
        return w

    bpy.data = types.SimpleNamespace(
        materials=types.SimpleNamespace(
            items=materials,
            new=lambda name: (materials.append(_Material(name)) or materials[-1]),
        ),
        images=types.SimpleNamespace(
            items=images,
            load=lambda path, check_existing=False: (images.append(_Image(path, check_existing)) or images[-1]),
        ),
        lights=types.SimpleNamespace(
            items=lights,
            new=lambda name, kind: (lights.append(types.SimpleNamespace(name=name, kind=kind, energy=0.0, angle=0.0)) or lights[-1]),
        ),
        worlds=types.SimpleNamespace(
            items=worlds,
            new=_new_world,
        ),
        cameras=types.SimpleNamespace(
            items=cameras,
            new=lambda name: (
                cameras.append(types.SimpleNamespace(name=name, lens=0.0, sensor_width=0.0, clip_start=0.0, clip_end=0.0))
                or cameras[-1]
            ),
        ),
        objects=types.SimpleNamespace(
            items=objects,
            new=lambda name, data=None: (objects.append(_Object(name, data)) or objects[-1]),
        ),
        node_groups=types.SimpleNamespace(
            items=node_groups,
            new=lambda name, tree_type: (
                node_groups.append(FakeNodeTree(name, tree_type, glare_reject_bloom_titlecase)) or node_groups[-1]
            ),
        ),
    )
    bpy._renders_triggered = renders_triggered
    bpy._linked_objects = linked_objects

    mathutils = types.ModuleType("mathutils")
    mathutils.Vector = FakeVector
    mathutils.Matrix = FakeMatrix
    return bpy, mathutils


def _load_earth_module(
    monkeypatch: pytest.MonkeyPatch,
    glare_reject_bloom_titlecase: bool = False,
) -> tuple[types.ModuleType, types.ModuleType, types.ModuleType]:
    fake_bpy, fake_mathutils = _build_fake_blender(glare_reject_bloom_titlecase)
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    monkeypatch.setitem(sys.modules, "mathutils", fake_mathutils)
    monkeypatch.setattr(sys, "argv", ["blender"])

    for key in list(sys.modules):
        if key == "earth_at_night_under_test" or key.startswith("scripts.lookdev"):
            monkeypatch.delitem(sys.modules, key, raising=False)

    spec = importlib.util.spec_from_file_location("earth_at_night_under_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        # Untouched pre-split script calls main() unconditionally at EOF; split script guards with __name__ == "__main__"
        assert not (SUBPKG_DIR / "__init__.py").is_file(), "split earth_at_night.py must not invoke main() on import"
    return mod, fake_bpy, fake_mathutils


# =============================================================================================
# 1. Public surface, physical constants, callable signatures, and subpackage DAG checks
# =============================================================================================


def test_public_surface_constants_and_signatures(monkeypatch: pytest.MonkeyPatch) -> None:
    mod, _, _ = _load_earth_module(monkeypatch)
    assert SCRIPT_PATH.is_file()
    assert len(EXPECTED_PUBLIC_SYMBOLS) == 33

    for sym in EXPECTED_PUBLIC_SYMBOLS:
        assert hasattr(mod, sym), f"missing symbol on earth_at_night: {sym}"

    assert mod.REPO_ROOT == REPO_ROOT
    assert mod.SRC == REPO_ROOT / "data" / "lookdev" / "sources"
    assert mod.EARTH_RADIUS_M == pytest.approx(6_371_000.0, abs=0.0)
    assert mod.MOON_RADIUS == pytest.approx(1737.4 / 6371.0, rel=1e-12)
    assert mod.RAYLEIGH_PER_M == (5.802e-6, 13.558e-6, 33.1e-6)
    assert mod.RAYLEIGH_SCALE_HEIGHT_M == pytest.approx(8_000.0, abs=0.0)
    assert mod.MIE_SCATTER_PER_M == pytest.approx(3.996e-6, abs=0.0)
    assert mod.MIE_ABSORB_PER_M == pytest.approx(4.40e-6, abs=0.0)
    assert mod.MIE_SCALE_HEIGHT_M == pytest.approx(1_200.0, abs=0.0)
    assert mod.OZONE_ABSORB_PER_M == (0.650e-6, 1.881e-6, 0.085e-6)
    assert mod.OZONE_CENTER_M == pytest.approx(25_000.0, abs=0.0)
    assert mod.OZONE_HALF_WIDTH_M == pytest.approx(15_000.0, abs=0.0)
    assert mod.ATMOSPHERE_TOP_M == pytest.approx(110_000.0, abs=0.0)
    assert mod.AIRGLOW_ALTITUDE_M == pytest.approx(95_000.0, abs=0.0)
    assert mod.AIRGLOW_SIGMA_M == pytest.approx(5_000.0, abs=0.0)
    assert mod.AIRGLOW_COLOR == (0.30, 1.0, 0.25, 1.0)


@pytest.mark.parametrize("fn_name", sorted(EXPECTED_SIGNATURES))
def test_all_seventeen_callable_signatures(monkeypatch: pytest.MonkeyPatch, fn_name: str) -> None:
    mod, _, _ = _load_earth_module(monkeypatch)
    fn = getattr(mod, fn_name)
    sig = inspect.signature(fn)
    actual = [(p.name, p.kind, p.default) for p in sig.parameters.values()]
    assert actual == EXPECTED_SIGNATURES[fn_name]


def test_modular_subpackage_exports_and_acyclic_graph_when_split(monkeypatch: pytest.MonkeyPatch) -> None:
    mod, _, _ = _load_earth_module(monkeypatch)
    subpkg_present = (SUBPKG_DIR / "__init__.py").is_file()
    if hasattr(mod, "__all__") or subpkg_present:
        assert hasattr(mod, "__all__")
        assert len(mod.__all__) == len(set(mod.__all__))
        assert set(mod.__all__) == EXPECTED_PUBLIC_SYMBOLS

    if not subpkg_present:
        return

    pkg = importlib.import_module("scripts.lookdev.earth_night")
    assert hasattr(pkg, "__all__")
    submodules = sorted(p for p in SUBPKG_DIR.glob("*.py") if p.name != "__init__.py")
    assert len(submodules) >= 3
    stems = {p.stem for p in submodules}
    edges: dict[str, set[str]] = {s: set() for s in stems}

    for path in submodules:
        stem = path.stem
        submod = importlib.import_module(f"scripts.lookdev.earth_night.{stem}")
        assert hasattr(submod, "__all__"), f"{stem}.py must declare explicit __all__"
        assert len(submod.__all__) == len(set(submod.__all__))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                mname = n.module or ""
                assert mname != "scripts.lookdev.earth_at_night"
                target = None
                if n.level == 1 and mname in stems:
                    target = mname
                elif mname.startswith("scripts.lookdev.earth_night."):
                    target = mname.removeprefix("scripts.lookdev.earth_night.")
                if target is not None:
                    assert target != stem
                    edges[stem].add(target)

    state = {s: 0 for s in stems}

    def visit(s: str) -> None:
        state[s] = 1
        for dep in edges[s]:
            assert state[dep] != 1, f"cycle in earth_night: {s} -> {dep}"
            if state[dep] == 0:
                visit(dep)
        state[s] = 2

    for s in sorted(stems):
        if state[s] == 0:
            visit(s)

    shader_nodes = importlib.import_module("scripts.lookdev.earth_night.shader_nodes")
    dummy_tree = FakeNodeTree("strict_check")
    s0 = dummy_tree.nodes.new("ShaderNodeEmission").outputs[0]
    s1 = dummy_tree.nodes.new("ShaderNodeEmission").outputs[0]
    with pytest.raises(ValueError):
        shader_nodes.chain_add_shaders(dummy_tree, [s0, s1], [])


# =============================================================================================
# 2. CLI argument parsing, equirectangular UV mapping, Earth mask, and Bruneton atmosphere
# =============================================================================================


def test_parse_args_defaults_overrides_and_missing_separator(monkeypatch: pytest.MonkeyPatch) -> None:
    mod, _, _ = _load_earth_module(monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        ["blender", "-b", "--", "--plan", "p.json", "--out", "o.png"],
    )
    defaults = mod.parse_args()
    assert (defaults.plan, defaults.out) == (Path("p.json"), Path("o.png"))
    assert defaults.res == "2560x1440"
    assert defaults.samples == 512
    assert defaults.lens_mm == pytest.approx(30.0, abs=0.0)
    assert defaults.exposure == pytest.approx(5.5, abs=0.0)
    assert defaults.bloom == pytest.approx(0.25, abs=0.0)
    assert defaults.sun_irradiance == pytest.approx(1361.0, abs=0.0)
    assert defaults.city_radiance == pytest.approx(0.03, abs=0.0)
    assert defaults.star_strength == pytest.approx(0.05, abs=0.0)
    assert defaults.airglow_radiance == pytest.approx(0.004, abs=0.0)

    # Missing --plan alone, missing --out alone, and missing "--" separator each trigger SystemExit(2)
    for bad_argv in (
        ["blender", "--", "--out", "o.png"],
        ["blender", "--", "--plan", "p.json"],
        ["blender", "--plan", "ignored.json", "--out", "ignored.png"],
    ):
        monkeypatch.setattr(sys, "argv", bad_argv)
        with pytest.raises(SystemExit):
            mod.parse_args()


def test_earth_shader_gmst_rotation_and_black_marble_luminance_mask(monkeypatch: pytest.MonkeyPatch) -> None:
    mod, _, _ = _load_earth_module(monkeypatch)
    earth_obj = mod.earth(gmst_deg=90.0, city_radiance=0.045)
    assert earth_obj.name == "Earth"
    assert earth_obj.shaded_smooth is True
    assert earth_obj.sphere_params == {"segments": 512, "ring_count": 256, "radius": 1.0}
    assert earth_obj.rotation_euler[2] == pytest.approx(math.pi / 2.0, abs=1e-12)

    mat = earth_obj.data.materials[0]
    out_node = next(n for n in mat.node_tree.nodes.items if n.kind == "ShaderNodeOutputMaterial")
    bsdf = out_node.inputs["Surface"].incoming.node
    assert bsdf.kind == "ShaderNodeBsdfPrincipled"
    assert bsdf.inputs["Roughness"].default_value == pytest.approx(0.6, abs=1e-12)
    assert bsdf.inputs["Emission Strength"].default_value == pytest.approx(0.045, abs=1e-12)

    # Below 0.10 luminance -> masked to 0; above 0.55 -> full emission; midpoint 0.325 -> 0.5 scale
    em_sock = bsdf.inputs["Emission Color"]
    low = _eval_socket(em_sock, (1.0, 0.0, 0.0), {"earth_blackmarble_2016_3km.jpg": (0.05, 0.05, 0.05)})
    mid = _eval_socket(em_sock, (1.0, 0.0, 0.0), {"earth_blackmarble_2016_3km.jpg": (0.325, 0.325, 0.325)})
    high = _eval_socket(em_sock, (1.0, 0.0, 0.0), {"earth_blackmarble_2016_3km.jpg": (0.80, 0.80, 0.80)})
    assert np.allclose(low, (0.0, 0.0, 0.0), atol=1e-12)
    assert np.allclose(mid, (0.1625, 0.1625, 0.1625), atol=1e-6)
    assert np.allclose(high, (0.80, 0.80, 0.80), atol=1e-12)


def test_atmosphere_volume_profiles_rayleigh_mie_ozone_and_airglow(monkeypatch: pytest.MonkeyPatch) -> None:
    mod, _, _ = _load_earth_module(monkeypatch)
    atm_obj = mod.atmosphere(airglow_radiance=0.008)
    assert atm_obj.name == "Atmosphere"
    assert atm_obj.shaded_smooth is True
    assert atm_obj.sphere_params["segments"] == 256
    assert atm_obj.sphere_params["ring_count"] == 128
    assert atm_obj.sphere_params["radius"] == pytest.approx(1.0 + 110_000.0 / 6_371_000.0, rel=1e-12)

    mat = atm_obj.data.materials[0]
    assert mat.cycles.volume_step_rate == pytest.approx(0.02, abs=1e-12)
    out_node = next(n for n in mat.node_tree.nodes.items if n.kind == "ShaderNodeOutputMaterial")
    components = _eval_socket(out_node.inputs["Volume"])
    assert len(components) == 5
    rayleigh, mie, mie_abs, ozone, airglow = components

    assert getattr(rayleigh, "phase") == "RAYLEIGH"
    assert rayleigh.inputs["Color"].default_value == pytest.approx(
        (5.802 / 33.1, 13.558 / 33.1, 1.0, 1.0), rel=1e-12
    )
    assert getattr(mie, "phase") == "HENYEY_GREENSTEIN"
    assert mie.inputs["Anisotropy"].default_value == pytest.approx(0.8, abs=1e-12)
    assert mie_abs.inputs["Color"].default_value == (0.0, 0.0, 0.0, 1.0)
    assert ozone.inputs["Color"].default_value == pytest.approx(
        (1.0 - 0.650 / 1.881, 0.0, 1.0 - 0.085 / 1.881, 1.0), rel=1e-12
    )
    assert airglow.inputs["Color"].default_value == (0.30, 1.0, 0.25, 1.0)

    r_earth = 6_371_000.0
    # Subsurface point (r = 0.95 < 1.0) clamps height to 0.0 -> exact sea-level Rayleigh density
    sea_rayleigh = 33.1e-6 * r_earth
    assert _eval_socket(rayleigh.inputs["Density"], (0.95, 0.0, 0.0)) == pytest.approx(sea_rayleigh, rel=1e-12)
    assert _eval_socket(rayleigh.inputs["Density"], (1.0 + 8_000.0 / r_earth, 0.0, 0.0)) == pytest.approx(
        sea_rayleigh / math.e, rel=1e-12
    )

    # Mie scale height 1200 m
    assert _eval_socket(mie.inputs["Density"], (1.0 + 1_200.0 / r_earth, 0.0, 0.0)) == pytest.approx(
        (3.996e-6 * r_earth) / math.e, rel=1e-12
    )
    assert _eval_socket(mie_abs.inputs["Density"], (1.0 + 1_200.0 / r_earth, 0.0, 0.0)) == pytest.approx(
        (4.40e-6 * r_earth) / math.e, rel=1e-12
    )

    # Ozone tent profile: peak at 25 km, half at 17.5 km and 32.5 km, zero outside [10 km, 40 km]
    oz_peak = 1.881e-6 * r_earth
    assert _eval_socket(ozone.inputs["Density"], (1.0 + 25_000.0 / r_earth, 0.0, 0.0)) == pytest.approx(oz_peak, rel=1e-12)
    assert _eval_socket(ozone.inputs["Density"], (1.0 + 17_500.0 / r_earth, 0.0, 0.0)) == pytest.approx(0.5 * oz_peak, rel=1e-9)
    assert _eval_socket(ozone.inputs["Density"], (1.0 + 32_500.0 / r_earth, 0.0, 0.0)) == pytest.approx(0.5 * oz_peak, rel=1e-9)
    assert _eval_socket(ozone.inputs["Density"], (1.0 + 9_000.0 / r_earth, 0.0, 0.0)) == pytest.approx(0.0, abs=1e-12)
    assert _eval_socket(ozone.inputs["Density"], (1.0 + 41_000.0 / r_earth, 0.0, 0.0)) == pytest.approx(0.0, abs=1e-12)

    # Airglow Gaussian: peak at 95 km, exp(-0.5) at +-5 km (90 km and 100 km)
    assert _eval_socket(airglow.inputs["Strength"], (1.0 + 95_000.0 / r_earth, 0.0, 0.0)) == pytest.approx(0.008, rel=1e-12)
    assert _eval_socket(airglow.inputs["Strength"], (1.0 + 90_000.0 / r_earth, 0.0, 0.0)) == pytest.approx(
        0.008 * math.exp(-0.5), rel=1e-9
    )
    assert _eval_socket(airglow.inputs["Strength"], (1.0 + 100_000.0 / r_earth, 0.0, 0.0)) == pytest.approx(
        0.008 * math.exp(-0.5), rel=1e-9
    )


# =============================================================================================
# 3. Moon tidal lock, Sun/Stars/Camera/Grade/Cycles settings, Hypothesis properties, and __main__
# =============================================================================================


def test_sun_stars_camera_grade_bloom_fallback_and_cycles_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Test glare fallback when "Bloom" raises TypeError -> falls back to "BLOOM"
    mod, fake_bpy, fake_mathutils = _load_earth_module(monkeypatch, glare_reject_bloom_titlecase=True)
    sun_obj = mod.sun(fake_mathutils.Vector((1.0, 0.0, 0.0)), 1365.0)
    assert sun_obj in fake_bpy._linked_objects
    assert sun_obj.data.energy == pytest.approx(1365.0, abs=1e-12)
    assert sun_obj.data.angle == pytest.approx(math.radians(0.533), rel=1e-12)
    assert sun_obj.rotation_euler[:2] == ("Z", "Y")

    mod.stars(0.07)
    world = fake_bpy.context.scene.world
    assert world is not None and world.name == "DeepStarMap" and world.use_nodes is True
    assert all(n.kind != "DefaultPreExistingWorldNode" for n in world.node_tree.nodes.items)
    mapping = next(n for n in world.node_tree.nodes.items if n.kind == "ShaderNodeMapping")
    assert mapping.inputs["Rotation"].default_value[2] == pytest.approx(math.pi, rel=1e-12)
    env = next(n for n in world.node_tree.nodes.items if n.kind == "ShaderNodeTexEnvironment")
    assert env.image.check_existing is True
    assert env.image.colorspace_settings.name == "Linear Rec.709"
    bg = next(n for n in world.node_tree.nodes.items if n.kind == "ShaderNodeBackground")
    light_path = next(n for n in world.node_tree.nodes.items if n.kind == "ShaderNodeLightPath")
    assert _eval_socket(bg.inputs["Strength"]) == pytest.approx(0.07, abs=1e-12)
    light_path.outputs["Is Camera Ray"].default_value = 0.0
    assert _eval_socket(bg.inputs["Strength"]) == pytest.approx(0.0, abs=1e-12)

    cam_pos = fake_mathutils.Vector((0.0, 3.0, 0.0))
    cam_obj = mod.camera(cam_pos, 35.0)
    assert cam_obj in fake_bpy._linked_objects
    assert tuple(cam_obj.location) == pytest.approx((0.0, 3.0, 0.0), abs=1e-12)
    assert fake_bpy.context.scene.camera is cam_obj
    assert cam_obj.data.lens == pytest.approx(35.0, abs=1e-12)
    assert cam_obj.data.sensor_width == pytest.approx(36.0, abs=1e-12)
    assert cam_obj.data.clip_start == pytest.approx(0.001, abs=1e-12)
    assert cam_obj.data.clip_end == pytest.approx(1000.0, abs=1e-12)
    assert cam_obj.rotation_euler == ("-Z", "Y", (0.0, -1.0, 0.0))

    mod.grade(0.33)
    comp = fake_bpy.context.scene.compositing_node_group
    assert comp is not None
    assert comp.interface.sockets == [{"name": "Image", "in_out": "OUTPUT", "socket_type": "NodeSocketColor"}]
    glare = next(n for n in comp.nodes.items if n.kind == "CompositorNodeGlare")
    assert glare.inputs["Type"].default_value == "BLOOM"
    assert glare.inputs["Threshold"].default_value == pytest.approx(0.004, abs=1e-12)
    assert glare.inputs["Strength"].default_value == pytest.approx(0.33, abs=1e-12)
    assert glare.inputs["Clamp"].default_value is True
    assert glare.inputs["Maximum"].default_value == pytest.approx(0.06, abs=1e-12)

    monkeypatch.setattr(
        sys,
        "argv",
        ["blender", "--", "--plan", str(tmp_path / "p.json"), "--out", str(tmp_path / "out.png"), "--res", "1920X1080", "--samples", "64", "--exposure", "4.5"],
    )
    args = mod.parse_args()
    mod.render_settings(args)
    sc = fake_bpy.context.scene
    assert sc.render.engine == "CYCLES"
    assert sc.render.filepath == str(tmp_path / "out.png")
    assert (sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage) == (1920, 1080, 100)
    assert sc.cycles.device == "GPU"
    assert sc.cycles.samples == 64
    assert sc.cycles.use_adaptive_sampling is True
    assert sc.cycles.adaptive_threshold == pytest.approx(0.005, abs=1e-12)
    assert sc.cycles.use_denoising is True
    assert (sc.cycles.max_bounces, sc.cycles.volume_bounces) == (6, 1)
    assert sc.cycles.volume_step_rate == pytest.approx(0.25, abs=1e-12)
    assert sc.cycles.volume_max_steps == 4096
    assert sc.cycles.sample_clamp_indirect == pytest.approx(10.0, abs=1e-12)
    assert sc.view_settings.view_transform == "AgX"
    assert sc.view_settings.exposure == pytest.approx(4.5, abs=1e-12)
    assert (sc.render.image_settings.file_format, sc.render.image_settings.color_depth) == ("PNG", "16")
    prefs = fake_bpy.context.preferences.addons["cycles"].preferences
    assert prefs.compute_device_type == "METAL"
    assert prefs.get_devices_called == 1
    assert [d.use for d in prefs.devices] == [False, True]


def _tree_signature(tree: FakeNodeTree) -> tuple[set[Any], set[Any]]:
    nodes = {
        (
            n.kind,
            tuple(n.location),
            Path(n.image.filepath).name if hasattr(n, "image") else None,
            n.image.check_existing if hasattr(n, "image") else None,
            n.image.colorspace_settings.name if hasattr(n, "image") else None,
        )
        for n in tree.nodes.items
    }
    assert len(nodes) == len(tree.nodes.items), "every node in the shader/compositor tree has a unique (kind, location)"
    links = {
        ((o.node.kind, tuple(o.node.location), o.name), (i.node.kind, tuple(i.node.location), i.name))
        for o, i in tree.links.items
    }
    assert len(links) == len(tree.links.items)
    return nodes, links


EXPECTED_EARTH_NODES = {
    ("ShaderNodeTexCoord", (-1400, 0), None, None, None),
    ("ShaderNodeVectorMath", (-1220, 0), None, None, None),
    ("ShaderNodeSeparateXYZ", (-1040, 0), None, None, None),
    ("ShaderNodeMath", (-860, 80), None, None, None),
    ("ShaderNodeMath", (-680, 80), None, None, None),
    ("ShaderNodeMath", (-860, -80), None, None, None),
    ("ShaderNodeMath", (-680, -80), None, None, None),
    ("ShaderNodeCombineXYZ", (-500, 0), None, None, None),
    ("ShaderNodeTexImage", (-300, 300), "earth_bluemarble_2004-11_16384.jpg", True, "sRGB"),
    ("ShaderNodeTexImage", (-300, -100), "earth_blackmarble_2016_3km.jpg", True, "sRGB"),
    ("ShaderNodeRGBToBW", (0, -260), None, None, None),
    ("ShaderNodeMapRange", (180, -260), None, None, None),
    ("ShaderNodeVectorMath", (380, -120), None, None, None),
    ("ShaderNodeBsdfPrincipled", (600, 200), None, None, None),
    ("ShaderNodeOutputMaterial", (900, 200), None, None, None),
}

EXPECTED_EARTH_LINKS = {
    (("ShaderNodeTexCoord", (-1400, 0), "Object"), ("ShaderNodeVectorMath", (-1220, 0), "Vector")),
    (("ShaderNodeVectorMath", (-1220, 0), "Vector"), ("ShaderNodeSeparateXYZ", (-1040, 0), "Vector")),
    (("ShaderNodeSeparateXYZ", (-1040, 0), "Y"), ("ShaderNodeMath", (-860, 80), "Value")),
    (("ShaderNodeSeparateXYZ", (-1040, 0), "X"), ("ShaderNodeMath", (-860, 80), "Value_001")),
    (("ShaderNodeMath", (-860, 80), "Value"), ("ShaderNodeMath", (-680, 80), "Value")),
    (("ShaderNodeSeparateXYZ", (-1040, 0), "Z"), ("ShaderNodeMath", (-860, -80), "Value")),
    (("ShaderNodeMath", (-860, -80), "Value"), ("ShaderNodeMath", (-680, -80), "Value")),
    (("ShaderNodeMath", (-680, 80), "Value"), ("ShaderNodeCombineXYZ", (-500, 0), "X")),
    (("ShaderNodeMath", (-680, -80), "Value"), ("ShaderNodeCombineXYZ", (-500, 0), "Y")),
    (("ShaderNodeCombineXYZ", (-500, 0), "Vector"), ("ShaderNodeTexImage", (-300, 300), "Vector")),
    (("ShaderNodeCombineXYZ", (-500, 0), "Vector"), ("ShaderNodeTexImage", (-300, -100), "Vector")),
    (("ShaderNodeTexImage", (-300, -100), "Color"), ("ShaderNodeRGBToBW", (0, -260), "Color")),
    (("ShaderNodeRGBToBW", (0, -260), "Val"), ("ShaderNodeMapRange", (180, -260), "Value")),
    (("ShaderNodeTexImage", (-300, -100), "Color"), ("ShaderNodeVectorMath", (380, -120), "Vector")),
    (("ShaderNodeMapRange", (180, -260), "Result"), ("ShaderNodeVectorMath", (380, -120), "Scale")),
    (("ShaderNodeTexImage", (-300, 300), "Color"), ("ShaderNodeBsdfPrincipled", (600, 200), "Base Color")),
    (("ShaderNodeVectorMath", (380, -120), "Vector"), ("ShaderNodeBsdfPrincipled", (600, 200), "Emission Color")),
    (("ShaderNodeBsdfPrincipled", (600, 200), "BSDF"), ("ShaderNodeOutputMaterial", (900, 200), "Surface")),
}

EXPECTED_ATMOSPHERE_NODES = {
    ("ShaderNodeTexCoord", (-1400, 0), None, None, None),
    ("ShaderNodeVectorMath", (-1220, 0), None, None, None),
    ("ShaderNodeMath", (-1040, 0), None, None, None),
    ("ShaderNodeMath", (-860, 0), None, None, None),
    ("ShaderNodeVolumeScatter", (0, 300), None, None, None),
    ("ShaderNodeMath", (-600, 300), None, None, None),
    ("ShaderNodeMath", (-420, 300), None, None, None),
    ("ShaderNodeMath", (-240, 300), None, None, None),
    ("ShaderNodeVolumeScatter", (0, 60), None, None, None),
    ("ShaderNodeMath", (-600, 60), None, None, None),
    ("ShaderNodeMath", (-420, 60), None, None, None),
    ("ShaderNodeMath", (-240, 60), None, None, None),
    ("ShaderNodeVolumeAbsorption", (0, -160), None, None, None),
    ("ShaderNodeMath", (-600, -160), None, None, None),
    ("ShaderNodeMath", (-420, -160), None, None, None),
    ("ShaderNodeMath", (-240, -160), None, None, None),
    ("ShaderNodeMath", (-860, -400), None, None, None),
    ("ShaderNodeMath", (-680, -400), None, None, None),
    ("ShaderNodeMath", (-500, -400), None, None, None),
    ("ShaderNodeMath", (-320, -400), None, None, None),
    ("ShaderNodeMath", (-140, -400), None, None, None),
    ("ShaderNodeVolumeAbsorption", (0, -400), None, None, None),
    ("ShaderNodeMath", (-140, -520), None, None, None),
    ("ShaderNodeAddShader", (250, 200), None, None, None),
    ("ShaderNodeAddShader", (420, 0), None, None, None),
    ("ShaderNodeAddShader", (590, -150), None, None, None),
    ("ShaderNodeMath", (-860, -700), None, None, None),
    ("ShaderNodeMath", (-680, -700), None, None, None),
    ("ShaderNodeMath", (-500, -700), None, None, None),
    ("ShaderNodeMath", (-320, -700), None, None, None),
    ("ShaderNodeMath", (-140, -700), None, None, None),
    ("ShaderNodeEmission", (0, -700), None, None, None),
    ("ShaderNodeMath", (-140, -820), None, None, None),
    ("ShaderNodeAddShader", (760, -300), None, None, None),
    ("ShaderNodeOutputMaterial", (950, 0), None, None, None),
}

EXPECTED_MOON_NODES = {
    ("ShaderNodeTexCoord", (-1400, 0), None, None, None),
    ("ShaderNodeVectorMath", (-1220, 0), None, None, None),
    ("ShaderNodeSeparateXYZ", (-1040, 0), None, None, None),
    ("ShaderNodeMath", (-860, 80), None, None, None),
    ("ShaderNodeMath", (-680, 80), None, None, None),
    ("ShaderNodeMath", (-860, -80), None, None, None),
    ("ShaderNodeMath", (-680, -80), None, None, None),
    ("ShaderNodeCombineXYZ", (-500, 0), None, None, None),
    ("ShaderNodeTexImage", (-300, 200), "moon_lroc_color_4k.tif", True, "sRGB"),
    ("ShaderNodeTexImage", (-300, -150), "moon_lola_ldem_16.tif", True, "Non-Color"),
    ("ShaderNodeBump", (100, -150), None, None, None),
    ("ShaderNodeBsdfPrincipled", (350, 150), None, None, None),
    ("ShaderNodeOutputMaterial", (650, 150), None, None, None),
}

EXPECTED_MOON_LINKS = {
    (("ShaderNodeTexCoord", (-1400, 0), "Object"), ("ShaderNodeVectorMath", (-1220, 0), "Vector")),
    (("ShaderNodeVectorMath", (-1220, 0), "Vector"), ("ShaderNodeSeparateXYZ", (-1040, 0), "Vector")),
    (("ShaderNodeSeparateXYZ", (-1040, 0), "Y"), ("ShaderNodeMath", (-860, 80), "Value")),
    (("ShaderNodeSeparateXYZ", (-1040, 0), "X"), ("ShaderNodeMath", (-860, 80), "Value_001")),
    (("ShaderNodeMath", (-860, 80), "Value"), ("ShaderNodeMath", (-680, 80), "Value")),
    (("ShaderNodeSeparateXYZ", (-1040, 0), "Z"), ("ShaderNodeMath", (-860, -80), "Value")),
    (("ShaderNodeMath", (-860, -80), "Value"), ("ShaderNodeMath", (-680, -80), "Value")),
    (("ShaderNodeMath", (-680, 80), "Value"), ("ShaderNodeCombineXYZ", (-500, 0), "X")),
    (("ShaderNodeMath", (-680, -80), "Value"), ("ShaderNodeCombineXYZ", (-500, 0), "Y")),
    (("ShaderNodeCombineXYZ", (-500, 0), "Vector"), ("ShaderNodeTexImage", (-300, 200), "Vector")),
    (("ShaderNodeCombineXYZ", (-500, 0), "Vector"), ("ShaderNodeTexImage", (-300, -150), "Vector")),
    (("ShaderNodeTexImage", (-300, -150), "Color"), ("ShaderNodeBump", (100, -150), "Height")),
    (("ShaderNodeTexImage", (-300, 200), "Color"), ("ShaderNodeBsdfPrincipled", (350, 150), "Base Color")),
    (("ShaderNodeBump", (100, -150), "Normal"), ("ShaderNodeBsdfPrincipled", (350, 150), "Normal")),
    (("ShaderNodeBsdfPrincipled", (350, 150), "BSDF"), ("ShaderNodeOutputMaterial", (650, 150), "Surface")),
}

EXPECTED_WORLD_NODES = {
    ("ShaderNodeTexCoord", (-900, 0), None, None, None),
    ("ShaderNodeMapping", (-700, 0), None, None, None),
    ("ShaderNodeTexEnvironment", (-450, 0), "starmap_2020_8k.exr", True, "Linear Rec.709"),
    ("ShaderNodeLightPath", (-450, 300), None, None, None),
    ("ShaderNodeMath", (-250, 250), None, None, None),
    ("ShaderNodeBackground", (-150, 0), None, None, None),
    ("ShaderNodeOutputWorld", (100, 0), None, None, None),
}

EXPECTED_WORLD_LINKS = {
    (("ShaderNodeTexCoord", (-900, 0), "Generated"), ("ShaderNodeMapping", (-700, 0), "Vector")),
    (("ShaderNodeMapping", (-700, 0), "Vector"), ("ShaderNodeTexEnvironment", (-450, 0), "Vector")),
    (("ShaderNodeLightPath", (-450, 300), "Is Camera Ray"), ("ShaderNodeMath", (-250, 250), "Value")),
    (("ShaderNodeMath", (-250, 250), "Value"), ("ShaderNodeBackground", (-150, 0), "Strength")),
    (("ShaderNodeTexEnvironment", (-450, 0), "Color"), ("ShaderNodeBackground", (-150, 0), "Color")),
    (("ShaderNodeBackground", (-150, 0), "Background"), ("ShaderNodeOutputWorld", (100, 0), "Surface")),
}

EXPECTED_COMP_NODES = {
    ("CompositorNodeRLayers", (0, 0), None, None, None),
    ("CompositorNodeGlare", (300, 0), None, None, None),
    ("NodeGroupOutput", (600, 0), None, None, None),
}

EXPECTED_COMP_LINKS = {
    (("CompositorNodeRLayers", (0, 0), "Image"), ("CompositorNodeGlare", (300, 0), "Image")),
    (("CompositorNodeGlare", (300, 0), "Image"), ("NodeGroupOutput", (600, 0), "Image")),
}


def test_main_cli_entrypoint_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_bpy, fake_mathutils = _build_fake_blender()
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    monkeypatch.setitem(sys.modules, "mathutils", fake_mathutils)
    for key in list(sys.modules):
        if key.startswith("scripts.lookdev"):
            monkeypatch.delitem(sys.modules, key, raising=False)

    plan_path = tmp_path / "plan.json"
    out_path = tmp_path / "deep" / "nested" / "render.png"
    plan_path.write_text(
        json.dumps(
            {
                "gmstDeg": 120.0,
                "moonPosition": [50.0, 10.0, 5.0],
                "sunDirection": [-1.0, 0.0, 0.0],
                "cameraPosition": [2.5, 0.0, 0.5],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["blender", "-b", "--", "--plan", str(plan_path), "--out", str(out_path), "--samples", "16"],
    )
    # First run creates 2-level parent dirs (parents=True); second run succeeds when dir exists (exist_ok=True)
    runpy.run_path(str(SCRIPT_PATH), run_name="__main__")
    assert fake_bpy.context.factory_reset_empty is True
    assert out_path.parent.is_dir()
    assert fake_bpy._renders_triggered == [True]
    assert fake_bpy.context.scene.render.filepath == str(out_path)
    assert [o.name for o in fake_bpy.data.objects.items] == ["Earth", "Atmosphere", "Moon", "Sun", "Camera"]

    mats = {m.name: m for m in fake_bpy.data.materials.items}
    assert set(mats) == {"Earth", "Atmosphere", "Moon"}
    for m in mats.values():
        assert m.use_nodes is True
    objs = {o.name: o for o in fake_bpy.data.objects.items}
    assert objs["Earth"].data.materials == [mats["Earth"]]
    assert objs["Atmosphere"].data.materials == [mats["Atmosphere"]]
    assert objs["Moon"].data.materials == [mats["Moon"]]
    moon_bsdf = next(n for n in mats["Moon"].node_tree.nodes.items if n.kind == "ShaderNodeBsdfPrincipled")
    assert moon_bsdf.inputs["Roughness"].default_value == pytest.approx(1.0, abs=1e-12)

    earth_nodes, earth_links = _tree_signature(mats["Earth"].node_tree)
    assert earth_nodes == EXPECTED_EARTH_NODES
    assert earth_links == EXPECTED_EARTH_LINKS

    atm_nodes, _ = _tree_signature(mats["Atmosphere"].node_tree)
    assert atm_nodes == EXPECTED_ATMOSPHERE_NODES

    moon_nodes, moon_links = _tree_signature(mats["Moon"].node_tree)
    assert moon_nodes == EXPECTED_MOON_NODES
    assert moon_links == EXPECTED_MOON_LINKS

    world = fake_bpy.context.scene.world
    assert world is not None and world.use_nodes is True
    world_nodes, world_links = _tree_signature(world.node_tree)
    assert world_nodes == EXPECTED_WORLD_NODES
    assert world_links == EXPECTED_WORLD_LINKS

    comp = fake_bpy.context.scene.compositing_node_group
    assert comp is not None
    glare = next(n for n in comp.nodes.items if n.kind == "CompositorNodeGlare")
    assert glare.inputs["Type"].default_value == "Bloom"
    comp_nodes, comp_links = _tree_signature(comp)
    assert comp_nodes == EXPECTED_COMP_NODES
    assert comp_links == EXPECTED_COMP_LINKS

    # Second run exercises exist_ok=True when out_path.parent already exists
    runpy.run_path(str(SCRIPT_PATH), run_name="__main__")
    captured = capsys.readouterr()
    assert captured.out.count(f"LOOKDEV_RENDERED {out_path}") == 2
    assert fake_bpy._renders_triggered == [True, True]


@settings(max_examples=40, deadline=None)
@given(
    lon=st.floats(min_value=-math.pi + 1e-4, max_value=math.pi - 1e-4, allow_nan=False, allow_infinity=False),
    lat=st.floats(min_value=-0.5 * math.pi + 1e-4, max_value=0.5 * math.pi - 1e-4, allow_nan=False, allow_infinity=False),
    radius=st.floats(min_value=0.2, max_value=80.0, allow_nan=False, allow_infinity=False),
)
@example(lon=0.0, lat=0.0, radius=1.0)
@example(lon=0.5 * math.pi, lat=0.0, radius=60.0)
def test_pbt_equirect_uv_inverts_spherical_angles_and_moon_frame_is_proper_orthonormal(
    lon: float, lat: float, radius: float
) -> None:
    fake_bpy, fake_mathutils = _build_fake_blender()
    saved_bpy = sys.modules.get("bpy")
    saved_mu = sys.modules.get("mathutils")
    saved_lookdev = {k: v for k, v in sys.modules.items() if k.startswith("scripts.lookdev")}
    for k in list(saved_lookdev):
        sys.modules.pop(k, None)
    saved_argv = list(sys.argv)
    sys.modules["bpy"] = fake_bpy
    sys.modules["mathutils"] = fake_mathutils
    sys.argv = ["blender"]
    try:
        spec = importlib.util.spec_from_file_location("earth_pbt_mod", SCRIPT_PATH)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except SystemExit:
            pass

        tree = FakeNodeTree("uv")
        uv_sock = mod.equirect_uv(tree)
        pt = (
            radius * math.cos(lat) * math.cos(lon),
            radius * math.cos(lat) * math.sin(lon),
            radius * math.sin(lat),
        )
        u, v, z = _eval_node_output(uv_sock, pt)
        assert u == pytest.approx(0.5 + lon / (2.0 * math.pi), abs=1e-9)
        assert v == pytest.approx(0.5 + lat / math.pi, abs=1e-9)
        assert z == pytest.approx(0.0, abs=1e-12)

        # Avoid exact collinearity with ecliptic north pole (0, -sin(eps), cos(eps))
        eps = math.radians(23.43929111)
        north = np.array([0.0, -math.sin(eps), math.cos(eps)], dtype=np.float64)
        unit_pt = np.asarray(pt, dtype=np.float64) / radius
        if abs(float(np.dot(unit_pt, north))) < 0.99:
            moon_obj = mod.moon(fake_mathutils.Vector(pt))
            m4 = moon_obj.matrix_world.array
            rot = m4[:3, :3]
            assert np.allclose(m4[:3, 3], pt, atol=1e-9)
            assert np.allclose(rot.T @ rot, np.eye(3), atol=1e-9)
            assert float(np.linalg.det(rot)) == pytest.approx(1.0, abs=1e-9)
            # Moon +X column points strictly from Moon toward Earth (-pt / ||pt||)
            assert np.allclose(rot[:, 0], -unit_pt, atol=1e-9)
            bump = next(n for n in moon_obj.data.materials[0].node_tree.nodes.items if n.kind == "ShaderNodeBump")
            assert bump.inputs["Distance"].default_value == pytest.approx(1.0 / 6371.0, rel=1e-12)
    finally:
        sys.argv = saved_argv
        for k in [k for k in sys.modules if k.startswith("scripts.lookdev")]:
            sys.modules.pop(k, None)
        sys.modules.update(saved_lookdev)
        if saved_bpy is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = saved_bpy
        if saved_mu is None:
            sys.modules.pop("mathutils", None)
        else:
            sys.modules["mathutils"] = saved_mu
