# CI's Python job (.github/workflows/gates.yml), step by step, inside the container scripts/ci_linux.py starts.
# Usage: bash /opt/ci-linux/job.bash <distro|pinned> [pytest arguments ...]
# The script has copied the checkout's files into the workspace before this starts. The steps after the checkout
# are the workflow's own commands in its order, under bash -e as the runner runs each step; the one addition is the
# watcher plugin on the pytest line (scripts/ci_linux/ci_linux_watch.py), which only writes files under /tmp.
set -e
kind=$1
shift
step() { printf '\nci_linux step: %s\n' "$*"; }

step "checkout (the copied files as one commit, as actions/checkout leaves a one-commit clone)"
git init --quiet
git add --force --all
git -c user.name=ci-linux -c user.email=ci-linux@localhost commit --quiet --no-verify --message checkout

step "ffmpeg -hide_banner -version && ffprobe -hide_banner -version"
ffmpeg -hide_banner -version && ffprobe -hide_banner -version

help=/home/runner/work/_temp/ffprobe-help.txt
if [[ $kind == pinned ]]; then
  step "the pinned ffprobe reads tile grids"
  ffprobe -hide_banner -h > "$help"
  grep -q -e -show_stream_groups "$help"
elif [[ $kind == distro ]]; then
  step "the distro ffprobe does not read tile grids"
  ffprobe -hide_banner -h > "$help"
  if grep -q -e -show_stream_groups "$help"; then
    echo "This ffprobe reads HEIC tile grids, so the refusal path would go untested"
    exit 1
  fi
else
  echo "job.bash: the ffmpeg kind is distro or pinned, not '$kind'"
  exit 2
fi

step "uv sync --frozen"
uv sync --frozen

# Not a workflow step: the interpreter to its patch, as CI's own pytest header prints it. The 3.12 job's is Ubuntu's
# python3.12 package (CI: /usr/bin/python3.12 of the runner image); the 3.14 job's is the build uv downloads.
step "the interpreter (an addition: printed only)"
uv run --frozen python -VV
dpkg-query --show --showformat '${Package} ${Version}\n' python3.12

step "uv run --frozen python scripts/verify_provenance.py"
uv run --frozen python scripts/verify_provenance.py

step "PYTHONPATH=/opt/ci-linux uv run --frozen pytest -rs --no-unexpected-skips -p ci_linux_watch $*"
PYTHONPATH=/opt/ci-linux uv run --frozen pytest -rs --no-unexpected-skips -p ci_linux_watch "$@"
