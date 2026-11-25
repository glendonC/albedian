"""The opt-in astrometry.net candidate stage (anet-stage-one; the lead's decision of 2026-11-24 21:32).

Off unless the setting turns it on and the program and its index files are found on the machine. When the app's own
searches have not calibrated a photo, astrometry.net's `solve-field` is run on a copy of the frame the app's own
decoder wrote (never the uploaded file), as a separate program with an argument list and no shell, under a time bound,
inside the job's child, so the job's stop and cancel end it with the child's process group. Its WCS is read back here
(TAN with SIP, from its header) and turned into a candidate PhoneCameraWCS: the frame's centre and a step toward its
right and bottom edges as the pose, and its corner to corner field. That candidate is only ever a start: the app's own
association, refinement and held-out check decide, unchanged (`services/ingest/jobs.py`, `_solve_chain`).

Measured before it was built [tested, .lead/kiro/reports/SOLVER/anet-candidate-probe.md]: on the 27 public photos
astrometry.net solves, the candidate calibrated 12 through the unchanged check, 0 wrong; of 48 random fields it solved
1 by itself and the check refused it. From the app's own detection list it solved 21 of the 27 against 27 from the
image, so the stage gives it the decoded frame, not the list (the lead's 21:32 rule).

Settings, read from the environment once per job: `ALBEDIAN_ASTROMETRY_NET` = "1" turns the stage on;
`ALBEDIAN_ASTROMETRY_NET_BIN`, a folder holding `solve-field` (else the PATH); `ALBEDIAN_ASTROMETRY_NET_INDEX`, the
folder of its index files (at least one `index-*.fits`). Whether it is on by default, and which index files may be
shipped or fetched, is the owner's decision.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Mapping, Sequence

import numpy as np

from services.contracts.astronomy import INDEX_FILE_PATTERN, VERSION_LINE_PATTERN

PROGRAM = "astrometry.net"
SOLVE_FIELD = "solve-field"
ENV_ON = "ALBEDIAN_ASTROMETRY_NET"
ENV_BIN = "ALBEDIAN_ASTROMETRY_NET_BIN"
ENV_INDEX = "ALBEDIAN_ASTROMETRY_NET_INDEX"
# The time the stage may take, its own program and the app's check of its candidate together, and the most of it
# the program may use [proposed: astrometry.net's median solve on the 27 was 15 s and its slowest 174 s on a shared
# laptop; the lead attempt alone solved 20 of them]. The job's search bound grows by STAGE_LIMIT_S when it is on, so a
# photo that still fails waits at most that much longer
STAGE_LIMIT_S = 120.0
PROGRAM_LIMIT_S = 90.0
# The oracle's attempts, in its order (research/benchmarks/real_photos/oracle.py): the lead's, then the 35 mm tag's
# width from 0.8 to 1.25 times it when the frame has one, then the fine one; each with crpix at the centre and a
# third-order tweak
LEAD_ATTEMPT = (4, 25.0, 120.0)
FINE_ATTEMPT = (2, 10.0, 120.0)
HINT_LOW, HINT_HIGH = 0.8, 1.25
SOLVED_LINE = re.compile(r"solved with index (?P<index>\S+?)\.?$", re.MULTILINE)


@dataclass(frozen=True)
class AnetSettings:
    """Where the program and its index files are, and the stage's bounds; plain values, so the job's child takes them."""

    solve_field: str
    index_dir: str
    limit_s: float = STAGE_LIMIT_S
    program_limit_s: float = PROGRAM_LIMIT_S


def settings_from_env(env: Mapping[str, str] | None = None) -> AnetSettings | None:
    """The stage's settings when it is on and its program and index files are found; None otherwise (off)."""
    env = os.environ if env is None else env
    if env.get(ENV_ON) != "1":
        return None
    folder = env.get(ENV_BIN)
    program = shutil.which(SOLVE_FIELD, path=folder) if folder else shutil.which(SOLVE_FIELD)
    index_dir = env.get(ENV_INDEX)
    if program is None or not index_dir or not any(Path(index_dir).glob("index-*.fits")):
        return None
    return AnetSettings(solve_field=program, index_dir=str(Path(index_dir)))


# ---- Its WCS, read back (TAN with SIP, the FITS header solve-field writes) ----


@dataclass(frozen=True)
class AnetWcs:
    """The WCS a `.wcs` file states: TAN about CRVAL at CRPIX through the CD matrix, with SIP's forward terms."""

    crval: tuple[float, float]
    crpix: tuple[float, float]
    cd: tuple[tuple[float, float], tuple[float, float]]
    sip_a: Mapping[tuple[int, int], float]
    sip_b: Mapping[tuple[int, int], float]

    def pixel_to_world(self, x_px: float, y_px: float) -> tuple[float, float]:
        """An array-coordinate pixel (0 at the first pixel's centre) as (RA, Dec) in degrees."""
        u = x_px + 1.0 - self.crpix[0]
        v = y_px + 1.0 - self.crpix[1]
        du = sum(c * u**p * v**q for (p, q), c in self.sip_a.items())
        dv = sum(c * u**p * v**q for (p, q), c in self.sip_b.items())
        uu, vv = u + du, v + dv
        xi = math.radians(self.cd[0][0] * uu + self.cd[0][1] * vv)
        eta = math.radians(self.cd[1][0] * uu + self.cd[1][1] * vv)
        ra0, dec0 = math.radians(self.crval[0]), math.radians(self.crval[1])
        rho = math.hypot(xi, eta)
        if rho == 0.0:
            return self.crval[0] % 360.0, self.crval[1]
        c = math.atan(rho)
        dec = math.asin(math.cos(c) * math.sin(dec0) + eta * math.sin(c) * math.cos(dec0) / rho)
        ra = ra0 + math.atan2(xi * math.sin(c), rho * math.cos(dec0) * math.cos(c) - eta * math.sin(dec0) * math.sin(c))
        return math.degrees(ra) % 360.0, math.degrees(dec)


def read_header(data: bytes) -> dict[str, str]:
    """A FITS primary header's cards (80 characters each, to END), keyword to its value's text."""
    cards: dict[str, str] = {}
    for start in range(0, len(data), 80):
        card = data[start : start + 80].decode("ascii", errors="replace")
        key = card[:8].strip()
        if key == "END":
            break
        if card[8:10] != "= ":
            continue
        value = card[10:].split("/", 1)[0].strip() if not card[10:].strip().startswith("'") else card[10:].split("'")[1]
        cards[key] = value.strip()
    return cards


def read_wcs(data: bytes) -> AnetWcs:
    cards = read_header(data)
    if not cards.get("CTYPE1", "").startswith("RA---TAN") or not cards.get("CTYPE2", "").startswith("DEC--TAN"):
        raise ValueError("The WCS is not the TAN projection the stage reads.")
    num = lambda key: float(cards[key])  # noqa: E731

    def sip(axis: str) -> dict[tuple[int, int], float]:
        order = int(cards.get(f"{axis}_ORDER", "0"))
        return {
            (p, q): num(f"{axis}_{p}_{q}")
            for p in range(order + 1) for q in range(order + 1 - p) if f"{axis}_{p}_{q}" in cards
        }

    return AnetWcs(
        crval=(num("CRVAL1"), num("CRVAL2")), crpix=(num("CRPIX1"), num("CRPIX2")),
        cd=((num("CD1_1"), num("CD1_2")), (num("CD2_1"), num("CD2_2"))), sip_a=sip("A"), sip_b=sip("B"),
    )


def _separation_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    ra1, dec1, ra2, dec2 = map(math.radians, (*a, *b))
    h = math.sin((dec2 - dec1) / 2) ** 2 + math.cos(dec1) * math.cos(dec2) * math.sin((ra2 - ra1) / 2) ** 2
    return math.degrees(2.0 * math.asin(min(1.0, math.sqrt(h))))


def diagonal_fov_deg(wcs: AnetWcs, width: int, height: int) -> float:
    """The angle between opposite corners of the frame, averaged over its two diagonals (as the oracle measures it)."""
    c = [wcs.pixel_to_world(x, y) for x, y in ((-0.5, -0.5), (width - 0.5, height - 0.5), (width - 0.5, -0.5), (-0.5, height - 0.5))]
    return 0.5 * (_separation_deg(c[0], c[1]) + _separation_deg(c[2], c[3]))


class _IcrsFrame:
    """camera_rotation's frame argument for ICRS itself, the frame the blind search's candidates are in."""

    @staticmethod
    def icrs_to_sky_vec(ra: np.ndarray, dec: np.ndarray) -> np.ndarray:
        ra, dec = np.radians(np.asarray(ra, dtype=np.float64)), np.radians(np.asarray(dec, dtype=np.float64))
        return np.stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)], axis=-1)


def candidate_wcs(wcs: AnetWcs, width: int, height: int):
    """The pinhole PhoneCameraWCS of astrometry.net's pose and corner to corner field; None for a mirrored one."""
    from services.science.person_hint import camera_rotation
    from services.science.phone_photo_solver import PhoneCameraWCS

    cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
    axes = [wcs.pixel_to_world(cx, cy), wcs.pixel_to_world(cx + 20.0, cy), wcs.pixel_to_world(cx, cy + 20.0)]
    rotation = camera_rotation(axes, _IcrsFrame())
    if rotation is None:
        return None
    return PhoneCameraWCS.from_rotation_and_intrinsics(
        rot_matrix=rotation, fov_diagonal_deg=diagonal_fov_deg(wcs, width, height), width=width, height=height,
        lens_model="brown-conrady", sky_frame=None,
    )


# ---- Running it ----


@dataclass(frozen=True)
class AnetCandidate:
    """A candidate pose from astrometry.net, and what the solution states of where it came from."""

    wcs: object
    version: str
    index_file: str
    seconds: float


def _frame_png(image: np.ndarray) -> bytes:
    """The app's own decoded frame as an 8-bit grey PNG: the copy the program reads in place of the uploaded file."""
    from PIL import Image

    pixels = np.asarray(image, dtype=np.float64)
    if pixels.ndim == 3:
        pixels = 0.299 * pixels[..., 0] + 0.587 * pixels[..., 1] + 0.114 * pixels[..., 2]
    top = float(np.max(pixels)) if pixels.size else 0.0
    scale = 255.0 if top <= 1.5 else 1.0
    buf = io.BytesIO()
    Image.fromarray(np.clip(np.round(pixels * scale), 0, 255).astype(np.uint8), mode="L").save(buf, format="PNG")
    return buf.getvalue()


def _attempts(width: int, height: int, fov_hint_deg: float | None) -> list[tuple[int, float, float]]:
    attempts = [LEAD_ATTEMPT]
    if fov_hint_deg is not None:
        # The tag's width in degrees, from its diagonal field over the stored frame
        half = math.radians(min(170.0, max(1.0, fov_hint_deg)) / 2.0)
        width_deg = 2.0 * math.degrees(math.atan(math.tan(half) * width / math.hypot(width, height)))
        attempts.append((4, round(HINT_LOW * width_deg, 2), round(HINT_HIGH * width_deg, 2)))
    attempts.append(FINE_ATTEMPT)
    return attempts


def _descendants(pid: int) -> list[int]:
    """Every process under `pid`, deepest first, read from the process table (`ps -A -o pid= -o ppid=`)."""
    table = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "ppid="], capture_output=True, text=True, timeout=10.0, check=False)
    children: dict[int, list[int]] = {}
    for line in table.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, stack = [], [pid]
    while stack:
        for child in children.get(stack.pop(), []):
            found.append(child)
            stack.append(child)
    return found[::-1]


def _run_bounded(arguments: Sequence[str], timeout_s: float) -> subprocess.CompletedProcess | None:
    """The program run to its end, or None at `timeout_s`, when it and every process it started are stopped: it is held
    first (SIGSTOP), so it starts nothing more, then each process under it and it itself are killed by pid. It stays
    in the job child's own process group, so the job's stop and cancel still end the whole tree with the child."""
    import signal

    process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = process.communicate(timeout=max(0.1, timeout_s))
        return subprocess.CompletedProcess(arguments, process.returncode, out, err)
    except subprocess.TimeoutExpired:
        try:
            os.kill(process.pid, signal.SIGSTOP)
        except ProcessLookupError:
            pass
        for pid in _descendants(process.pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.kill()
        process.communicate()
        return None


def program_version(settings: AnetSettings, timeout_s: float = 30.0) -> str | None:
    """The program's own version line as the contract's one word (`VERSION_LINE_PATTERN`), or None when its first
    line is not one, or it does not answer within `timeout_s`: then the solution could not name its candidate's
    source, so the stage gives no candidate."""
    done = _run_bounded([settings.solve_field, "--version"], timeout_s)
    if done is None:
        return None
    lines = (done.stdout or done.stderr).strip().splitlines()
    word = lines[0].strip() if lines else ""
    return word if re.fullmatch(VERSION_LINE_PATTERN, word) else None


def find_candidate(
    image: np.ndarray, width: int, height: int, settings: AnetSettings, *, fov_hint_deg: float | None = None,
    budget_s: float | None = None,
) -> AnetCandidate | None:
    """astrometry.net's candidate for the frame, or None when it does not solve within its bound or its pose is
    mirrored. The program's own time is at most `settings.program_limit_s`, and never past `budget_s`."""
    began = time.monotonic()
    limit = settings.program_limit_s if budget_s is None else min(settings.program_limit_s, budget_s)
    if limit <= 1.0:
        return None
    # The version call counts inside the program's own time (AUDIT's note on version one)
    version = program_version(settings, timeout_s=min(30.0, limit))
    if version is None:
        return None
    with tempfile.TemporaryDirectory(prefix="albedian-anet-") as tmp:
        folder = Path(tmp)
        frame = folder / "frame.png"
        frame.write_bytes(_frame_png(image))
        config = folder / "astrometry.cfg"
        config.write_text(f"cpulimit {limit:g}\nadd_path {settings.index_dir}\nautoindex\n", encoding="utf-8")
        for downsample, low, high in _attempts(width, height, fov_hint_deg):
            left = limit - (time.monotonic() - began)
            if left <= 1.0:
                return None
            arguments: Sequence[str] = [
                settings.solve_field, "--config", str(config), "--dir", str(folder), "--temp-dir", str(folder),
                "--out", "solved", "--no-plots", "--new-fits", "none", "--overwrite",
                "--scale-units", "degwidth", "--scale-low", f"{low:g}", "--scale-high", f"{high:g}",
                "--downsample", str(downsample), "--crpix-center", "--tweak-order", "3", str(frame),
            ]  # fmt: skip
            done = _run_bounded(arguments, left)
            if done is None:
                return None
            wcs_file, solved_mark = folder / "solved.wcs", folder / "solved.solved"
            if not (solved_mark.is_file() and wcs_file.is_file()):
                continue
            # The index file that matched, by name: the solution names its candidate's source, so a solve that does not
            # say which index matched gives no candidate
            found = SOLVED_LINE.search(done.stdout or "")
            index_file = Path(found.group("index")).name if found else ""
            if re.fullmatch(INDEX_FILE_PATTERN, index_file) is None:
                return None
            wcs = candidate_wcs(read_wcs(wcs_file.read_bytes()), width, height)
            if wcs is None:
                return None
            return AnetCandidate(wcs=wcs, version=version, index_file=index_file, seconds=round(time.monotonic() - began, 2))
    return None
