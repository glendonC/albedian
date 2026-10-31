# Requirements: Scientific Observation Solution

## Introduction
Defines the `ObservationSolution` engine connecting recorded source pixels to verified World Coordinate Systems (WCS), pinned `Gaia DR3` / Hipparcos star catalogs, Bailer-Jones distance intervals, and Solar System ephemerides.

## Requirements

### REQ-OBS-1: Source Detection & Independent WCS Verification
**User Story:** As a user asking "What were we looking at?", I want the sky calibrated against astronomical data only when the pixel geometry is genuinely verified.
- **Acceptance Criteria:**
  1. THE detector SHALL extract candidate point sources above local background noise from the sky region (`xPx`, `yPx`, `flux`, `snr`).
  2. THE plate solver SHALL fit a gnomonic (TAN) + radial/SIP distortion WCS against geometric star quads and verify the solution on held-out detections not used to form the minimal quad.
  3. IF `heldOutInliers >= 6` and `medianResidualPx <= 3.0`, THE status SHALL be `calibrated`; OTHERWISE it SHALL be `unresolved`, `ambiguous`, or `predicted_only`.

### REQ-OBS-2: Epistemic Classification & Bailer-Jones Distances
**User Story:** As a user selecting a star and asking "How far away is it?", I want a catalog-sourced distance interval with explicit uncertainty and evidence class.
- **Acceptance Criteria:**
  1. Matched detections SHALL be labeled `catalog_matched` with `Gaia DR3` / `HIP` identifiers, angular residual in arcseconds, and Bailer-Jones distance bounds (`lower`, `median`, `upper` pc).
  2. Non-detected catalog stars or planets computed from time/location SHALL be labeled `predicted`, never `observed` or `catalog_matched`.
