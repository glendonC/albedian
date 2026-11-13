# Design: Scientific Observation Solution

## Architecture
- **Packages:** `@albedian/evidence` (`packages/evidence/src/index.ts`), `services/science/solver.py`, `services/science/catalog.py`, `services/science/ephemeris.py`, `services/ingest/store.py`, `services/ingest/metadata.py`, `services/ingest/probe.py`, `services/ingest/router.py`
- **Ingest & Solve Routes:** `POST /api/ingest`, `GET /api/ingest/{sha256}`, and `GET /api/ingest/{sha256}/original` store uploads and read facts and hints (`services/ingest/router.py`), and `/api/ingest/{sha256}/solve`, `/api/ingest/jobs/{job_id}`, and `/api/ingest/{sha256}/solution` execute `SolveJob` with real byte, frame, and candidate-triplet progress denominators; `ObservationSolution` omits `observedAtUTC` and `observerLocation` when unrecorded in the source.
- **Catalog & Ephemeris:** Pinned Gaia DR3 + Hipparcos bright navigational & neighborhood catalog snapshot with Bailer-Jones photogeometric distance intervals (`lower`, `median`, `upper` pc) and proper motion $(\mu_{\alpha^*}, \mu_\delta)$; `services/science/ephemeris.py` computes the Sun, the Moon and the planets on the IAU's ERFA routines (eraMoon98, eraPlan94, eraEpv00), seen from the given place with light time and parallax, without visual magnitudes.

## Correctness Properties
- **Property PBT-EVID-1:** No transformation can upgrade `predicted` or `simulated` findings to `observed` or `catalog_matched` without a verified `SourceDetection` and `WCSVerification` proof.
- **Property PBT-COORD-1:** Celestial coordinate conversions preserve unit vector norms ($\|\hat{r}\| = 1$), valid RA/Dec bounds, and ordered positive distance intervals ($0 < d_{\text{lower}} \le d_{\text{median}} \le d_{\text{upper}}$).
