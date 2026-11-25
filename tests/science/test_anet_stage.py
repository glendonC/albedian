"""The opt-in astrometry.net candidate stage (anet-stage-one): its settings, its WCS reader, and a run of a stand-in
program with the argument list, the copy of the frame and the bound the stage gives it. The stand-in is a small Python
program this test writes, so CI needs no install; one test reads a real solution file through astrometry.net's own
`wcs-xy2rd` and runs only where that program is on this machine.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
from types import SimpleNamespace

import numpy as np
import pytest

from services.science import anet_stage

REPO = Path(__file__).resolve().parents[2]
# rp-b-16 and the index files astrometry.net solves it with, registered downloads (data/downloads.json); where they
# are, astrometry.net's own programs are installed beside them, as the benchmark's oracle needs
REAL_PHOTO_REL = ".cache/real-photos/rp-b-16/rp-b-16.jpg"
REAL_INDEX_REL = [f".cache/astrometry-net/4100/index-{n}.fits" for n in range(4115, 4120)]
# rp-b-16's frame centre as the benchmark's astrometry.net record states it (a public photo's sky coordinates)
RP_B_16_CENTRE = (273.259183765, -9.7877686714)


def _card(key: str, value: str) -> bytes:
    return f"{key:<8}= {value:>20}".ljust(80).encode("ascii")


def tan_header(crval: tuple[float, float], crpix: tuple[float, float], cd: tuple[float, float, float, float],
               sip_a: dict | None = None, sip_b: dict | None = None) -> bytes:
    cards = [_card("SIMPLE", "T"), _card("CTYPE1", "'RA---TAN-SIP'"), _card("CTYPE2", "'DEC--TAN-SIP'"),
             _card("CRVAL1", repr(crval[0])), _card("CRVAL2", repr(crval[1])), _card("CRPIX1", repr(crpix[0])),
             _card("CRPIX2", repr(crpix[1])), _card("CD1_1", repr(cd[0])), _card("CD1_2", repr(cd[1])),
             _card("CD2_1", repr(cd[2])), _card("CD2_2", repr(cd[3]))]
    for axis, terms in (("A", sip_a or {}), ("B", sip_b or {})):
        cards.append(_card(f"{axis}_ORDER", "3"))
        cards += [_card(f"{axis}_{p}_{q}", repr(v)) for (p, q), v in terms.items()]
    cards.append(b"END".ljust(80))
    data = b"".join(cards)
    return data + b" " * (-len(data) % 2880)


def _gnomonic(ra0: float, dec0: float, ra: float, dec: float) -> tuple[float, float]:
    """World to standard coordinates (degrees), the forward gnomonic projection."""
    a0, d0, a, d = map(math.radians, (ra0, dec0, ra, dec))
    cos_c = math.sin(d0) * math.sin(d) + math.cos(d0) * math.cos(d) * math.cos(a - a0)
    xi = math.cos(d) * math.sin(a - a0) / cos_c
    eta = (math.cos(d0) * math.sin(d) - math.sin(d0) * math.cos(d) * math.cos(a - a0)) / cos_c
    return math.degrees(xi), math.degrees(eta)


def test_a_tan_header_reads_back_to_its_reference_point_and_inverts_the_forward_projection() -> None:
    wcs = anet_stage.read_wcs(tan_header((273.26, -9.79), (1530.5, 2040.5), (-0.0065, -0.0165, 0.0165, -0.0065)))
    ra, dec = wcs.pixel_to_world(1529.5, 2039.5)
    assert (ra, dec) == pytest.approx((273.26, -9.79), abs=1e-12)
    for x, y in ((0.0, 0.0), (3059.0, 4079.0), (100.0, 3900.0), (2900.0, 50.0)):
        ra, dec = wcs.pixel_to_world(x, y)
        xi, eta = _gnomonic(273.26, -9.79, ra, dec)
        u, v = x + 1.0 - 1530.5, y + 1.0 - 2040.5
        assert xi == pytest.approx(-0.0065 * u - 0.0165 * v, abs=1e-9)
        assert eta == pytest.approx(0.0165 * u - 0.0065 * v, abs=1e-9)


def test_the_sip_terms_move_a_pixel_as_the_polynomial_says() -> None:
    plain = anet_stage.read_wcs(tan_header((10.0, 20.0), (500.5, 400.5), (-0.01, 0.0, 0.0, 0.01)))
    bent = anet_stage.read_wcs(tan_header((10.0, 20.0), (500.5, 400.5), (-0.01, 0.0, 0.0, 0.01), sip_a={(2, 0): 1e-4}))
    # u = 100 at x = 599.5: SIP adds 1e-4 * 100**2 = 1 px along u, so the bent pixel lands where the plain one at u = 101 does
    assert bent.pixel_to_world(599.5, 399.5) == pytest.approx(plain.pixel_to_world(600.5, 399.5), abs=1e-12)


@pytest.mark.local_dataset(REAL_PHOTO_REL, *REAL_INDEX_REL)
def test_the_reader_agrees_with_astrometry_nets_own_wcs_xy2rd_on_a_real_solution(tmp_path: Path) -> None:
    # astrometry.net's own solution of rp-b-16, made here by its program, read by ours and by its wcs-xy2rd
    import subprocess

    from research.benchmarks.real_photos import oracle

    config = tmp_path / "astrometry.cfg"
    config.write_text(f"cpulimit 120\nadd_path {REPO / '.cache/astrometry-net/4100'}\nautoindex\n", encoding="utf-8")
    subprocess.run(
        ["solve-field", "--config", str(config), "--dir", str(tmp_path), "--out", "photo", "--no-plots", "--new-fits", "none",
         "--overwrite", "--scale-units", "degwidth", "--scale-low", "25", "--scale-high", "120", "--downsample", "4",
         "--crpix-center", "--tweak-order", "3", str(REPO / REAL_PHOTO_REL)],
        capture_output=True, timeout=180, check=True,
    )
    solution = tmp_path / "photo.wcs"
    wcs = anet_stage.read_wcs(solution.read_bytes())
    points = [(-0.5, -0.5), (3059.5, 4079.5), (1529.5, 2039.5), (200.0, 3800.0), (2800.0, 300.0), (1000.0, 1000.0)]
    theirs = oracle.pixel_to_sky(solution, [p[0] for p in points], [p[1] for p in points], tmp_path)
    for (x, y), (ra, dec) in zip(points, theirs):
        ours = wcs.pixel_to_world(x, y)
        assert anet_stage._separation_deg(ours, (ra, dec)) < 1e-6, (x, y)


def test_the_stage_is_off_unless_turned_on_with_its_program_and_an_index_file(tmp_path: Path) -> None:
    program = _stand_in(tmp_path / "bin")
    index = tmp_path / "index"
    index.mkdir()
    on = {anet_stage.ENV_ON: "1", anet_stage.ENV_BIN: str(program.parent), anet_stage.ENV_INDEX: str(index)}
    assert anet_stage.settings_from_env({}) is None
    assert anet_stage.settings_from_env({**on, anet_stage.ENV_ON: "0"}) is None
    # No index file yet, then one
    assert anet_stage.settings_from_env(on) is None
    (index / "index-4117.fits").write_bytes(b"")
    settings = anet_stage.settings_from_env(on)
    assert settings is not None and settings.solve_field == str(program) and settings.index_dir == str(index)
    # With the program and an index file both there, only the setting turns it on
    assert anet_stage.settings_from_env({**on, anet_stage.ENV_ON: "0"}) is None
    assert anet_stage.settings_from_env({k: v for k, v in on.items() if k != anet_stage.ENV_ON}) is None
    # No program in the named folder
    assert anet_stage.settings_from_env({**on, anet_stage.ENV_BIN: str(tmp_path / "empty")}) is None


# ---- The stand-in program ----

STAND_IN = '''#!{python}
"""A stand-in for astrometry.net's solve-field: it records its arguments, follows a plan, and writes a solution."""
import json, os, sys, time
from pathlib import Path

args = sys.argv[1:]
if args == ["--version"]:
    time.sleep(float(os.environ.get("ANET_STAND_IN_VERSION_SLEEP", "0")))
    print(os.environ.get("ANET_STAND_IN_VERSION", "0.97-stand-in"))
    sys.exit(0)
plan = json.loads(Path(os.environ["ANET_STAND_IN_PLAN"]).read_text())
with open(os.environ["ANET_STAND_IN_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
if plan.get("engine"):
    # As solve-field starts astrometry-engine: a child that would run on, its pid written for the test
    import subprocess
    time.sleep(plan.get("engine_after", 0.0))
    engine = subprocess.Popen([sys.executable, str(Path(__file__).with_name("sleeper.py"))])
    Path(plan["engine"]).write_text(str(engine.pid))
time.sleep(plan.get("sleep", 0.0))
folder, out = args[args.index("--dir") + 1], args[args.index("--out") + 1]
if plan.get("header") is not None and plan.get("solve_on", 1) <= sum(1 for _ in open(os.environ["ANET_STAND_IN_LOG"])):
    Path(folder, out + ".wcs").write_bytes(bytes.fromhex(plan["header"]))
    Path(folder, out + ".solved").write_bytes(b"\\x01")
    print("Field 1: solved with index " + plan.get("index", "index-4117.fits") + ".")
'''


def _stand_in(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    program = folder / "solve-field"
    program.write_text(STAND_IN.replace("{python}", sys.executable), encoding="utf-8")
    program.chmod(program.stat().st_mode | stat.S_IXUSR)
    # The child the stand-in starts as solve-field starts astrometry-engine: it only sleeps
    (folder / "sleeper.py").write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    return program


@pytest.fixture
def stand_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The stand-in, its settings, and a way to set its plan and read the arguments it was given."""
    program = _stand_in(tmp_path / "bin")
    index = tmp_path / "index"
    index.mkdir()
    (index / "index-4117.fits").write_bytes(b"")
    log, plan = tmp_path / "args.jsonl", tmp_path / "plan.json"
    monkeypatch.setenv("ANET_STAND_IN_LOG", str(log))
    monkeypatch.setenv("ANET_STAND_IN_PLAN", str(plan))
    settings = anet_stage.AnetSettings(solve_field=str(program), index_dir=str(index))

    class StandIn:
        def __init__(self) -> None:
            self.settings = settings

        def plan(self, **fields) -> None:
            if "header" in fields and isinstance(fields["header"], bytes):
                fields["header"] = fields["header"].hex()
            plan.write_text(json.dumps(fields), encoding="utf-8")

        def calls(self) -> list[list[str]]:
            return [json.loads(line) for line in log.read_text().splitlines()] if log.is_file() else []

    return StandIn()


# North up and east left with rows counted down the frame, as a camera sees it: a determinant above zero (a mirrored
# frame, which no camera takes, gives no candidate)
HEADER = tan_header((83.8, -5.4), (320.5, 240.5), (-0.05, 0.0, 0.0, -0.05))
MIRRORED = tan_header((83.8, -5.4), (320.5, 240.5), (-0.05, 0.0, 0.0, 0.05))


def test_the_program_gets_an_argument_list_with_a_copy_of_the_frame_and_its_candidate_comes_back(stand_in) -> None:
    stand_in.plan(header=HEADER)
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    found = anet_stage.find_candidate(image, 640, 480, stand_in.settings)
    assert found is not None and found.version == "0.97-stand-in" and found.index_file == "index-4117.fits"
    ra, dec = found.wcs.pixel_to_world(319.5, 239.5)
    assert anet_stage._separation_deg((ra, dec), (83.8, -5.4)) < 1e-6
    (args,) = stand_in.calls()
    # The lead attempt first, on the copy the app wrote in its own private folder, with the oracle's options
    assert args[-1].endswith("frame.png") and "albedian-anet-" in args[-1]
    assert args[args.index("--downsample") + 1] == "4"
    assert (args[args.index("--scale-low") + 1], args[args.index("--scale-high") + 1]) == ("25", "120")
    assert "--crpix-center" in args and args[args.index("--tweak-order") + 1] == "3"


def test_attempts_follow_the_oracles_order_and_stop_at_the_first_that_solves(stand_in) -> None:
    stand_in.plan(header=HEADER, solve_on=3)
    found = anet_stage.find_candidate(np.zeros((480, 640)), 640, 480, stand_in.settings, fov_hint_deg=80.0)
    assert found is not None
    calls = stand_in.calls()
    assert [c[c.index("--downsample") + 1] for c in calls] == ["4", "4", "2"]
    # The tag's width from its diagonal field: 0.8 to 1.25 times it
    low, high = float(calls[1][calls[1].index("--scale-low") + 1]), float(calls[1][calls[1].index("--scale-high") + 1])
    assert high / low == pytest.approx(1.25 / 0.8, rel=1e-3)


def test_no_solution_gives_no_candidate(stand_in) -> None:
    stand_in.plan(header=None)
    assert anet_stage.find_candidate(np.zeros((480, 640)), 640, 480, stand_in.settings) is None
    assert len(stand_in.calls()) == 2  # the lead attempt and the fine one: no tag, so no hinted attempt


def test_a_solution_that_names_no_index_gives_no_candidate(stand_in) -> None:
    stand_in.plan(header=HEADER, index="somewhere/else")
    assert anet_stage.find_candidate(np.zeros((480, 640)), 640, 480, stand_in.settings) is None


def test_the_program_is_stopped_at_its_bound(stand_in) -> None:
    stand_in.plan(header=HEADER, sleep=30.0)
    began = time.monotonic()
    assert anet_stage.find_candidate(np.zeros((480, 640)), 640, 480, stand_in.settings, budget_s=2.0) is None
    assert time.monotonic() - began < 8.0


def test_a_mirrored_solution_gives_no_candidate(stand_in) -> None:
    stand_in.plan(header=MIRRORED)
    assert anet_stage.find_candidate(np.zeros((480, 640)), 640, 480, stand_in.settings) is None



@pytest.mark.local_dataset(REAL_PHOTO_REL, *REAL_INDEX_REL)
def test_the_real_program_gives_a_candidate_near_its_own_solution_on_a_public_photo() -> None:
    # One local run on the real program (the lead's rule for the stage): rp-b-16, whose own solve the benchmark's
    # astrometry.net record holds; the candidate's centre lands within a tenth of a degree of it
    from PIL import Image

    with Image.open(REPO / REAL_PHOTO_REL) as im:
        rgb = np.asarray(im.convert("RGB"), dtype=np.uint8)
    height, width = rgb.shape[:2]
    settings = anet_stage.AnetSettings(solve_field=shutil.which("solve-field"), index_dir=str(REPO / ".cache/astrometry-net/4100"))
    found = anet_stage.find_candidate(rgb, width, height, settings)
    assert found is not None and found.version and found.index_file.startswith("index-41")
    ra, dec = found.wcs.pixel_to_world((width - 1) / 2.0, (height - 1) / 2.0)
    assert anet_stage._separation_deg((ra, dec), RP_B_16_CENTRE) < 0.1


@pytest.mark.parametrize("line", ["/opt/homebrew/bin/solve-field 0.97", "solve-field version 0.97", ""])
def test_a_version_line_that_is_not_one_word_gives_no_candidate(stand_in, monkeypatch: pytest.MonkeyPatch, line: str) -> None:
    # The solution names the program's version as the contract's one word, never a path: a line it cannot name so
    # gives no candidate, and the program is not run on the frame at all
    monkeypatch.setenv("ANET_STAND_IN_VERSION", line)
    stand_in.plan(header=HEADER)
    assert anet_stage.find_candidate(np.zeros((480, 640)), 640, 480, stand_in.settings) is None
    assert stand_in.calls() == []


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class _HeldUntilEngine:
    """The stand-in's process, its bounded wait begun only once the engine it starts has written its pid, so the bound
    can end only what has started, however slow a loaded machine is to start it (batch 168: under the gate's shards the
    stand-in had not written the pid when a 3 s bound ended it)."""

    def __init__(self, process: subprocess.Popen, marker: Path) -> None:
        self._process, self._marker = process, marker
        self.engine_seen: float | None = None

    def __getattr__(self, name: str):
        return getattr(self._process, name)

    def communicate(self, *args, **kwargs):
        if self.engine_seen is None and kwargs.get("timeout") is not None:
            deadline = time.monotonic() + 120.0
            while not (self._marker.is_file() and self._marker.read_text().strip().isdigit()):
                assert time.monotonic() < deadline, "the stand-in never started its engine"
                time.sleep(0.02)
            self.engine_seen = time.monotonic()
        return self._process.communicate(*args, **kwargs)


@pytest.mark.parametrize("engine_after_s", [0.0, 4.0])
def test_the_programs_own_children_end_with_it_at_the_stages_bound(
    stand_in, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine_after_s: float
) -> None:
    # AUDIT's verdict on version one: solve-field's astrometry-engine outlived the stage's timeout, reparented, running
    # on to its CPU limit. The stage stops the program's whole tree, and its pid is gone at once. The bound's wait
    # begins once the engine runs (4.0 s: a start slower than the bound itself), and the version call, tested on its
    # own above and below, answers at once, so no load can end the stand-in before its engine is there
    marker = tmp_path / "engine.pid"
    stand_in.plan(header=HEADER, sleep=30.0, engine=str(marker), engine_after=engine_after_s)
    held: list[_HeldUntilEngine] = []
    real_popen = subprocess.Popen

    def popen(arguments, *args, **kwargs):
        process = real_popen(arguments, *args, **kwargs)
        if arguments[0] == stand_in.settings.solve_field and "--version" not in arguments:
            held.append(_HeldUntilEngine(process, marker))
            return held[-1]
        return process

    shim = SimpleNamespace(**{name: getattr(subprocess, name) for name in ("PIPE", "run", "CompletedProcess", "TimeoutExpired")})
    shim.Popen = popen
    monkeypatch.setattr(anet_stage, "subprocess", shim)
    monkeypatch.setattr(anet_stage, "program_version", lambda settings, timeout_s=30.0: "0.97-stand-in")
    assert anet_stage.find_candidate(np.zeros((480, 640)), 640, 480, stand_in.settings, budget_s=3.0) is None
    assert len(held) == 1 and held[0].engine_seen is not None
    assert time.monotonic() - held[0].engine_seen < 10.0
    engine = int(marker.read_text())
    deadline = time.monotonic() + 10.0
    while _alive(engine) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(engine)


def test_a_version_call_that_hangs_ends_at_the_stages_bound(stand_in, monkeypatch: pytest.MonkeyPatch) -> None:
    # The version call counts inside the program's own time (AUDIT's note on version one)
    monkeypatch.setenv("ANET_STAND_IN_VERSION_SLEEP", "30")
    stand_in.plan(header=HEADER)
    began = time.monotonic()
    assert anet_stage.find_candidate(np.zeros((480, 640)), 640, 480, stand_in.settings, budget_s=2.0) is None
    assert time.monotonic() - began < 8.0
