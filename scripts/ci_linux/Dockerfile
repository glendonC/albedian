# CI's two Python jobs (.github/workflows/gates.yml) as one image each, for scripts/ci_linux.py, which builds it for
# linux/amd64 and passes the workflow's own uv version and pinned ffmpeg address and sha256 as build arguments.
# Target "distro" is the Python 3.12 job with Ubuntu's ffmpeg; target "pinned" is the Python 3.14 job with the
# FFmpeg-Builds static build. Neither holds the checkout: the script copies it in at run time.

# Ubuntu 24.04.5, Docker Hub's ubuntu image release noble-20261024.1, by its index digest: the last release before
# the GitHub runner image CI ran on (ubuntu24/20261101.360, Ubuntu 24.04.5).
FROM ubuntu:noble-20261024.1@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852 AS base

ARG UV_VERSION
ARG UV_SHA256
# The runner's locale, set for the tests as on the runner
ENV LANG=C.UTF-8
# Keeps apt from asking for a time zone while the image is built; an ARG, so it is not left set for the tests
ARG DEBIAN_FRONTEND=noninteractive
# What the runner image has and the tests use: git (tests list tracked files), Python 3.12 at /usr/bin/python3.12
# (the 3.12 job's interpreter), the time zone database, and the certificates, curl and xz that the downloads need
RUN apt-get update \
 && apt-get install --yes --no-install-recommends ca-certificates curl git python3 tzdata xz-utils
# uv as setup-uv installs it: the release's x86_64 Linux archive, checked against its sha256 before it is unpacked
RUN test -n "$UV_VERSION" && test -n "$UV_SHA256" \
 && curl -fsSL --retry 3 -o /tmp/uv.tar.gz \
      "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-unknown-linux-gnu.tar.gz" \
 && echo "$UV_SHA256  /tmp/uv.tar.gz" | sha256sum --check --strict \
 && tar -xzf /tmp/uv.tar.gz -C /usr/local/bin --strip-components=1 \
 && rm /tmp/uv.tar.gz \
 && uv --version
# The runner's account and its workspace path; the tests run as this user, never as root. The uv cache folder is
# where scripts/ci_linux.py mounts its volume (a new volume takes the folder's owner)
RUN useradd --create-home --uid 1001 runner \
 && mkdir -p /home/runner/work/albedian/albedian /home/runner/work/_temp/setup-uv-cache \
 && chown -R runner:runner /home/runner/work
# The variables setup-uv sets for the steps after it (UV_PYTHON is set per job when the container starts)
ENV UV_PYTHON_INSTALL_DIR=/home/runner/work/_temp/uv-python-dir UV_CACHE_DIR=/home/runner/work/_temp/setup-uv-cache

FROM base AS distro
# A build argument holds in its own stage only, so this stage's apt names it again
ARG DEBIAN_FRONTEND=noninteractive
# The workflow's own step for this job
RUN apt-get update && apt-get install --yes --no-install-recommends ffmpeg
COPY job.bash ci_linux_watch.py /opt/ci-linux/
USER runner
WORKDIR /home/runner/work/albedian/albedian

FROM base AS pinned
ARG FFMPEG_URL
ARG FFMPEG_SHA256
# The workflow's own step for this job, with the folder in /opt instead of $RUNNER_TEMP
RUN test -n "$FFMPEG_URL" && test -n "$FFMPEG_SHA256" \
 && curl -fsSL --retry 3 -o /tmp/ffmpeg.tar.xz "$FFMPEG_URL" \
 && echo "$FFMPEG_SHA256  /tmp/ffmpeg.tar.xz" | sha256sum --check --strict \
 && mkdir /opt/ffmpeg \
 && tar -xJf /tmp/ffmpeg.tar.xz -C /opt/ffmpeg --strip-components=1 \
 && rm /tmp/ffmpeg.tar.xz \
 && test -x /opt/ffmpeg/bin/ffmpeg \
 && test -x /opt/ffmpeg/bin/ffprobe
# $GITHUB_PATH puts the folder in front of PATH
ENV PATH=/opt/ffmpeg/bin:$PATH
COPY job.bash ci_linux_watch.py /opt/ci-linux/
USER runner
WORKDIR /home/runner/work/albedian/albedian
