"""Copies one clip through `services.media.clip_io` in a process of its own and prints its peak resident size.

Run by `test_clip_io.py` (P-RST-17), never collected as a test: a fresh process, so the peak is this copy's alone.
`--hold` decodes every frame into a list before encoding them, the non-streamed copy the property must be able to
see. Prints one JSON line: the frames copied and the process's own peak resident size in bytes.
Usage: <python> clip_copy_child.py <source> <variant> [--hold]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.media import clip_io  # noqa: E402


def main() -> None:
    source, variant = Path(sys.argv[1]), Path(sys.argv[2])
    hold = "--hold" in sys.argv[3:]
    probe = clip_io.probe_clip(source)
    with clip_io.ClipDecoder(source, probe) as decoder:
        with clip_io.ClipEncoder(variant, probe.video, time_base=probe.time_base, frame_times=probe.frame_times,
                                 container="mov", preset="ultrafast") as encoder:
            if hold:
                frames = list(decoder)
                for frame in frames:
                    encoder.write(frame.planes)
            else:
                for frame in decoder:
                    encoder.write(frame.planes)
            encoded = encoder.finish()
    print(json.dumps({"frames": encoded.frames, "peakSelfBytes": clip_io.peak_memory().self_bytes}))


if __name__ == "__main__":
    main()
