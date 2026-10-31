# Design: Scientific Observation Solution

## Architecture
- **Packages:** `@albedian/evidence` (`packages/evidence/src/index.ts`), `services/science/solver.py`, `services/science/catalog.py`, `services/science/ephemeris.py`
- **Catalog:** Pinned Gaia DR3 + Hipparcos bright navigational & neighborhood catalog snapshot with Bailer-Jones photogeometric distance intervals (`lower`, `median`, `upper` pc) and proper motion $(\mu_{\alpha^*}, \mu_\delta)$.

## Correctness Properties
- **Property PBT-EVID-1:** No transformation can upgrade `predicted` or `simulated` findings to `observed` or `catalog_matched` without a verified `SourceDetection` and `WCSVerification` proof.
- **Property PBT-COORD-1:** Celestial coordinate conversions preserve unit vector norms ($\|\hat{r}\| = 1$), valid RA/Dec bounds, and ordered positive distance intervals ($0 < d_{\text{lower}} \le d_{\text{median}} \le d_{\text{upper}}$).
