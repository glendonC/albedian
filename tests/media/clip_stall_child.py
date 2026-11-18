"""Stands in for an ffmpeg that gets stuck, so `test_clip_io.py` can show that every wait of `clip_io` on a child has
its bound. Run by the tests, never collected as a test. It sleeps far past any bound and is stopped by the caller.

Modes:
- `silent`: writes nothing and never reads its input.
- `frame-without-time <bytes>`: writes one frame of that many bytes to its output and never logs its time.
- `never-reads`: as an encoder that takes no frame: never reads its input.
- `never-exits`: reads its input to its end, then does not exit.

Usage: <python> clip_stall_child.py <mode> [<bytes>]
"""

from __future__ import annotations

import sys
import time

# Far past every bound the tests set, and still finite, so a child nobody stops ends on its own [proposed]
SLEEP_SEC = 600


def main() -> None:
    mode = sys.argv[1]
    if mode == "frame-without-time":
        sys.stdout.buffer.write(bytes(int(sys.argv[2])))
        sys.stdout.buffer.flush()
    elif mode == "never-exits":
        while sys.stdin.buffer.read(65536):
            pass
    elif mode not in ("silent", "never-reads"):
        raise SystemExit(f"unknown mode {mode}")
    time.sleep(SLEEP_SEC)


if __name__ == "__main__":
    main()
