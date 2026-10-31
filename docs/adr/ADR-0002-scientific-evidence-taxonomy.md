# ADR-0002: Scientific Evidence Taxonomy & Anti-Circularity Guardrails

- **Status:** Accepted
- **Date:** 2026-10-31
- **Deciders:** Principal Orchestrator (`architecture`, `science`)

## Context

Low-light restoration models can hallucinate point sources, and planetarium overlays can tempt developers to draw catalog stars directly onto an unsolved or noisy photograph and claim those stars were "recovered." Doing so violates scientific integrity.

## Decision

1. **Four Immutable Evidence Classes (`EvidenceClass`):**
   - `observed`: A feature detected directly in source pixels with recorded `(xPx, yPx)`, `snr`, `flux`, and frame timestamp.
   - `catalog_matched`: An `observed` detection linked to an external catalog record (`Gaia DR3`, `HIP`, `JPL`) via an independently verified WCS solution.
   - `predicted`: An object position computed from observer time, location, and ephemerides/catalog without pixel-level detection proof.
   - `simulated`: A 3D spatial rendering, alternate viewpoint projection, or modeled historical/future epoch calculation.
2. **Provenance Promotion Invariant (Tested via PBT):**
   - No `predicted` or `simulated` finding may ever transition to `observed` or `catalog_matched` without an authorized pixel-detection and WCS-verification proof object.
3. **Anti-Circularity in Restoration:**
   - Source star detection for baseline WCS calibration runs on the immutable original (or a strictly linear deterministic composite), never on a catalog-conditioned generative output.
   - Any point source appearing in an enhanced `MediaVariant` that lacks corresponding support in the source pixel neighborhood or paired reference is flagged as `unverified_artifact` rather than promoted to a star observation.
4. **Distance Estimation:**
   - 3D stellar positions use Bailer-Jones photogeometric distance priors (`median`, `lower`, `upper` parsecs) or parallax bounds with explicit uncertainty intervals—never naive $1/\varpi$ on negative or low-SNR parallaxes.

## Consequences

- Users and judges can visually and programmatically distinguish camera measurements from astronomical predictions and 3D simulations at every moment.
