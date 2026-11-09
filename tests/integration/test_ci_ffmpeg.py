"""CI tests the HEIC tile path with a pinned ffmpeg and keeps the refusal path on the distro ffmpeg.

The solver's HEIC tests take the branch ffprobe allows: with -show_stream_groups (ffmpeg 7.1 or later) they check the
stored pixels, without it they check the refusal. Ubuntu 24.04's ffmpeg 6.1.1 has no -show_stream_groups, so one
pytest job runs a pinned static build and the other keeps the distro build. This reads the workflow as text, as
test_skip_policy.py does.

Covers:
- The pytest matrix pairs Python 3.12 with the distro ffmpeg and Python 3.14 with the pinned build
- The pinned build is a dated FFmpeg-Builds release download with a 64-hex sha256, checked before it is unpacked
- No download in the workflow is piped into a shell
- Each job fails unless its ffprobe is the one it means to test: show_stream_groups present, or absent
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (REPO_ROOT / ".github" / "workflows" / "gates.yml").read_text(encoding="utf-8")


def python_job() -> str:
    start = WORKFLOW.index("\n  python:\n")
    return WORKFLOW[start:]


def steps(job: str) -> list[str]:
    """The job's steps, each from its '- ' line to the next one."""
    body = job[job.index("    steps:\n") :]
    return [chunk for chunk in re.split(r"\n(?=      - )", body)[1:]]


def condition(step: str) -> str | None:
    found = re.search(r"if:\s*matrix\.ffmpeg\s*==\s*'(\w+)'", step)
    return found.group(1) if found else None


def test_the_pytest_matrix_pairs_each_python_with_one_ffmpeg() -> None:
    job = python_job()
    assert re.search(r'-\s*python:\s*"3\.12"\s*\n\s*ffmpeg:\s*distro\b', job)
    assert re.search(r'-\s*python:\s*"3\.14"\s*\n\s*ffmpeg:\s*pinned\b', job)
    assert "${{ matrix.ffmpeg }}" in job.split("\n")[2], "the job name says which ffmpeg it runs"


def test_the_pinned_build_is_a_dated_release_checked_by_sha256_before_it_is_unpacked() -> None:
    install = [step for step in steps(python_job()) if "FFMPEG_URL" in step]
    assert len(install) == 1
    step = install[0]
    assert condition(step) == "pinned"
    url = re.search(r"FFMPEG_URL:\s*(\S+)", step).group(1)
    assert re.fullmatch(
        r"https://github\.com/FFmpeg-Builds/FFmpeg-Builds/releases/download/autobuild-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}/"
        r"ffmpeg-n\d+\.\d+[^/\s]*-linux64-gpl-\d+\.\d+\.tar\.xz",
        url,
    ), url
    assert re.search(r"FFMPEG_SHA256:\s*[0-9a-f]{64}\s*$", step, re.MULTILINE)
    download, check, unpack = step.index("curl -fsSL"), step.index("sha256sum --check --strict"), step.index("tar -xJf")
    assert download < check < unpack
    # One binary per test line: in a && list bash -e would not stop on the first one failing
    assert re.search(r"^\s*test -x \S*/bin/ffmpeg\"?\s*$", step, re.MULTILINE)
    assert re.search(r"^\s*test -x \S*/bin/ffprobe\"?\s*$", step, re.MULTILINE)
    assert '"$FFMPEG_SHA256' in step[check - 80 : check]


def test_the_distro_job_keeps_ubuntus_ffmpeg() -> None:
    apt = [step for step in steps(python_job()) if "apt-get install" in step and "ffmpeg" in step]
    assert len(apt) == 1
    assert condition(apt[0]) == "distro"


def test_no_download_is_piped_into_a_shell() -> None:
    assert not re.search(r"(curl|wget)[^\n]*\|\s*(sudo\s+)?(ba|z)?sh\b", WORKFLOW)


def test_each_job_fails_unless_its_ffprobe_is_the_one_it_tests() -> None:
    checks = {condition(step): step for step in steps(python_job()) if "show_stream_groups" in step}
    assert set(checks) == {"pinned", "distro"}
    # The pinned job's last command is the grep itself, so no match fails the step
    last = [line.strip() for line in checks["pinned"].strip().splitlines()][-1]
    assert last.startswith("grep -q -e -show_stream_groups "), last
    # bash -e does not stop on a negated command, so the distro job fails the step explicitly on a match
    assert re.search(r"if grep -q -e -show_stream_groups [^\n]*; then\n(?:[^\n]*\n)*?\s*exit 1\n\s*fi", checks["distro"])
    for step in checks.values():
        # ffprobe -h goes to a file first: a grep -q reading a pipe can end ffprobe early under pipefail
        assert "ffprobe -hide_banner -h >" in step


def test_both_jobs_print_the_ffmpeg_they_ran() -> None:
    version = [step for step in steps(python_job()) if "ffprobe -hide_banner -version" in step]
    assert len(version) == 1
    assert condition(version[0]) is None
