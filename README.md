# Albedian

**See (Beyond) the Night Sky.**  
*See the night. Understand the sky. Explore beyond it.*

Albedian is an observation-centered, scientifically grounded media restoration and spatial astronomy environment built for **Kiroween 2026** (*Frankenstein* category).

When someone witnesses an extraordinary night scene—mountains, desert dunes, stars, and atmosphere—an ordinary camera often records a noisy, nearly black image or video. Albedian begins with that real recording:

1. **See the night (`Reveal`):** Execute inspectable, content-addressed media restoration experiments (exposure/tone transfer, spatial/temporal denoising, motion-aware star/foreground protection) and compare synchronized results against the untouched original frame and audio.
2. **Understand the sky (`Register`):** Detect source-supported point sources, perform blind or hint-assisted astrometric plate solving (WCS), independently verify held-out star residuals, and match observations against pinned astronomical catalogs (`Gaia DR3` / Hipparcos bright-star subset) and Solar System ephemerides.
3. **Explore beyond it (`Explore`):** Select a catalog-matched star and transition continuously from the recorded pixel anchor into an angularly registered sky and a catalog-backed 3D stellar neighborhood—changing viewpoints, inspecting Bailer-Jones distance intervals, or modeling the sky across epochs—then return (`Back to Moment`) to the exact source frame, crop, and original audio state.

---

## Core Principles

- **Real Observation First:** The source photograph or video (`SourceAsset`) is immutable and SHA-256 content-addressed.
- **Four Classes of Scientific Evidence:** Every celestial finding and visual marker carries an explicit epistemic classification:
  - `observed` — Measured directly in source pixels (centroid, SNR, flux).
  - `catalog_matched` — Linked to a catalog entry via an independently verified WCS solution with held-out pixel residual metrics.
  - `predicted` — Computed from time, observer location, and ephemerides without claiming pixel-level sensor recovery.
  - `simulated` — 3D catalog-geometry flight or modeled historical/future epoch views, disclosed explicitly in the UI.
- **Seamless Continuity (`ObservationSession`):** Four coordinated state machines (`Media`, `Restoration`, `Calibration`, `Navigation`) plus an independent `Voice` state machine share one observation model and reversible `CameraCheckpoint` stack.
- **Voice as Direct Manipulation:** Spoken and typed utterances (`"Make the sky clearer"`, `"What's that one?"`, `"How far away is it?"`, `"Take me there"`, `"Show me this sky a thousand years ago"`, `"Go back"`) resolve against active pointer/selection context into schema-validated `AlbedianAction` commands.

---

## Repository Structure

```text
/
  apps/
    web/                    # React + TypeScript + WebGL/Three.js + Web Audio observational shell
  services/
    media/                  # Python Media Experiment Runtime (FFmpeg/OpenCV DAG, probe, audio remux)
    science/                # Python Astrometry, WCS solver/verifier, Gaia DR3 catalog & ephemerides
    voice/                  # Context-bound intent parser & authoritative typed action validator
  packages/
    contracts/              # Authoritative TypeScript/Zod & JSON schemas for all 14 domain entities
    evidence/               # Epistemic state transitions, uncertainty formatting & guardrails
    observation/            # 4+1 orthogonal state machines, session reducer & NavigationHistory
    camera-rig/             # WCS pixel<->world math, quaternion SLERP, log-tan-FOV & floating origin
    design-system/          # Albedian observational instrument tokens, marker grammar & motion specs
  research/
    originals/              # Intact foundational research reports (2026-10-31)
    findings/               # Actionable research synthesis & capability matrix
    experiments/            # Reproducible technical experiment registry & benchmark harnesses
  docs/
    product/                # Product thesis & experience states
    architecture/           # Modular monolith topology & cross-system invariants
    adr/                    # Architecture Decision Records (ADR-0001 through ADR-0004)
    science/                # Astrometric math, WCS validation & evidence rules
    design/                 # Visual token specification & functional motion contracts
    engineering/            # Engineering standards, testing strategy, voice & audio policies
    operations/             # Security, privacy, licensing ledger, risk register & demo runbook
    internal/               # Kiro contribution ledger & multi-agent integration ledger
  tests/
    fixtures/               # Deterministic astronomical starfields, media clips & golden WCS fixtures
    integration/            # Cross-package and service-to-UI integration tests
    e2e/                    # End-to-end journey verification
  .kiro/
    steering/               # Global and file-matched Kiro steering documents
    specs/                  # 8 Kiro domain specifications (requirements.md, design.md, tasks.md)
    hooks/                  # Validated Kiro 1.4.62 workspace hooks (.kiro.hook)
    agents/                 # Specialized agent role definitions
    settings/               # Workspace MCP & sandbox configuration
```

---

## Quick Start

### 1. Install Dependencies
```bash
pnpm install
```

### 2. Run Verification & Property-Based Test Suites
```bash
# TypeScript unit + fast-check property-based tests (camera projection, DAG, evidence, navigation, voice, sync)
pnpm test

# Python scientific & media runtime tests + Hypothesis property-based tests
pnpm test:python

# Typecheck all packages and web application
pnpm typecheck
```

### 3. Start Backend Service & Web Application
```bash
# Terminal 1: Start the Python Science & Media Runtime API (port 8000)
uv run uvicorn services.api:app --reload --port 8000

# Terminal 2: Start the Albedian Web Shell (port 5173)
pnpm dev
```

---

## Scientific Methodology & Limitations

- **Photons cannot be invented:** Low-light restoration operates on recorded signal (`Observed-data processing`) and constrained denoising. Albedian never pastes catalog stars onto dark images and claims they were recovered from camera pixels.
- **Independent WCS Verification:** A plate-solve candidate is accepted as `calibrated` only when at least 6 spatially distributed inliers pass held-out residual verification ($\text{median residual} \le 3.0\text{ px}$). Otherwise, the observation enters `unresolved`, `ambiguous`, or `predicted_only`.
- **3D Stellar Distances:** A single photograph encodes angular direction ($\alpha, \delta$), not depth. 3D positions in `Explore` mode derive from pinned `Gaia DR3` / Hipparcos parallaxes and Bailer-Jones photogeometric distance estimates (`median`, `lower`, `upper` pc), with explicit uncertainty intervals and a `Catalog Geometry · Distance Estimates` disclosure.

---

## License

Released under the [MIT License](LICENSE). Third-party astronomical catalog attributions (`ESA Gaia DR3`, `CDS Hipparcos/SIMBAD`, `NASA JPL Ephemerides`) and library licenses are documented in [`docs/operations/SECURITY_PRIVACY_LICENSES.md`](docs/operations/SECURITY_PRIVACY_LICENSES.md).
