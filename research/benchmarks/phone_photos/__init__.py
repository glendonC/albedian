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
"""

EVIDENCE_CLASS = "simulated"
