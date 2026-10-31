---
inclusion: always
---

# Scientific Integrity & Epistemic Rules

1. **Four Mandatory Evidence Classes (`EvidenceClass`):**
   - `observed`: Measured directly from original pixels (`(xPx, yPx)`, `snr`, `flux`).
   - `catalog_matched`: Linked to an external catalog (`Gaia DR3`, `HIP`, `JPL`) only after an independently verified WCS calibration (`heldOutInliers >= 6`, `medianResidualPx <= 3.0`).
   - `predicted`: Computed from ephemeris/catalog + time/location without pixel registration proof.
   - `simulated`: 3D catalog-geometry flight or modeled historical/future epochs (`Modeled Sky`).
2. **Never Invent Photons:** Do not draw catalog stars into a restoration output and claim they were recovered from sensor observations.
3. **Explicit Failure States:** If plate solving lacks sufficient star detections or fails held-out verification, set `ObservationSolution.status` to `unresolved`, `ambiguous`, or `predicted_only`—never fabricate a `calibrated` WCS.
4. **Authoritative Scientific Computation:** Always use deterministic astrometric/ephemeris math (`services/science` / `@albedian/camera-rig`) rather than asking an LLM to calculate celestial coordinates or distances.
5. **Negative Gaia Parallax:** When a `CatalogReference` has `parallaxMas ≤ 0` (negative or zero parallax is a valid Gaia DR3 output for faint/extended sources), distance MUST NOT be estimated as `1000 / parallaxMas`. Use `bailerJonesDistancePc.median` if present, otherwise treat distance as unavailable (`distancePolicy: 'unavailable'`).
6. **Rolling-Shutter Frame Guard:** When stacking rolling-shutter video frames (`sourceAsset.rollingShutterRatio > 0`), THE science service SHALL apply column-strip de-skew before cross-correlation alignment; misregistering rolling-shutter strips as translation shifts can produce false WCS residuals that pass the held-out gate artificially.
