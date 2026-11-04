"""Synthetic phone-photo benchmark for Albedian's plate solver (roadmap Track 1, M3).

The benchmark renders real Gaia DR3 and Hipparcos stars from the G 12 catalog tiles through
modelled phone cameras, so that every frame comes with its exact ground truth: the camera, its
attitude, the moment and the place. Everything it makes is simulated. A solve here measures how
a solver behaves on modelled phone photos; it never shows that a real photo solves.

Modules:

- `astrometry`: ICRS to the observer's horizon at a moment and place (precession, nutation,
  sidereal time, annual aberration, refraction), with its inverse.
- `geometry`: the ground-truth camera (pinhole intrinsics, Brown-Conrady or Kannala-Brandt
  distortion, attitude) mapping pixels to the sky and back.
- `cameras`: phone cameras with their makers' published fields of view, and a generic head-worn
  video camera.
- `stars`: cone queries over the G 12 catalog tiles, moved to the frame's date.
- `scene`: sky brightness, the Sun and Moon, the ground and its lights, clouds, aircraft, screens.
- `render`: optics, sensor and image processing to a JPEG, with the frame's exact truth.
- `suite`: the seeded frame specs of the full suite and the CI subset.
- `solvers`: today's solvers behind one interface, with a time limit.
- `score`: outcomes, false claims and summaries; the parallel runner.
- `real_clips`: today's solver on the owner's clips, locally only.

Run it with `uv run python -m research.benchmarks.phone_photos run` (see `__main__`); the method
and results are in docs/science/PHONE_PHOTO_BENCHMARK.md.
"""

EVIDENCE_CLASS = "simulated"
