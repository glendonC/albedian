# Albedian

**See (Beyond) the Night Sky.**  
*Start from your photograph—not an anonymous universe.*

Albedian is an observation-centered media restoration and 3D spatial astronomy instrument built for **Kiroween 2026** (*Frankenstein* category). It turns a noisy night-sky photograph or video from Earth into a scientifically calibrated 3D portal—restoring the stars your camera recorded, locking their identity with Gaia DR3 astrometry, and letting any curious explorer fly from their real earthly horizon onto the surfaces of the worlds behind those points of light.

---

## Why Albedian Exists (Problem & Cited Research)

1. **Disappearing Access to the Night Sky:** Over **80% of humanity** (~83% globally, and >99% of the U.S. and Europe) lives under light-polluted skies, and **more than one-third cannot see the Milky Way** ([Falchi et al., 2016, *Science Advances*](https://pmc.ncbi.nlm.nih.gov/articles/PMC4928945/)). Global citizen-science observations from 2011–2022 showed naked-eye star visibility declining at a rate equivalent to **~9.6% annual sky-brightness growth** ([Kyba et al., 2023, *Science*](https://pubmed.ncbi.nlm.nih.gov/36656932/)).
2. **Photon-Limited Consumer Cameras:** Small apertures on phones and wearables suffer from Poisson photon shot noise, read noise, and Mie atmospheric glare at night ([Hasinoff et al., 2016, *ACM TOG / SIGGRAPH Asia*](http://graphics.stanford.edu/papers/hdrp/hasinoff-hdrplus-sigasia16.pdf); [Liba et al., 2019](https://arxiv.org/html/1910.11336)). Conventional denoisers often erase faint stars, whereas Albedian uses detection-preserving temporal burst restoration (`+5.2 dB SNR`) paired with instant `Hold Original` verification.
3. **The Comprehension & Experience Chasm:** Recognizing a flat 2D dot on a sky map does not produce comprehension of 3D cosmic scale, reference-frame transitions, or light-travel time ([Plummer, 2014](https://eric.ed.gov/?id=EJ1029625); [Plummer et al., 2022, *JRST*](https://onlinelibrary.wiley.com/doi/10.1002/tea.21754); [Schneps et al., 2014](https://dl.acm.org/doi/10.5555/2753872.2753884); [Rajpaul et al., 2018](https://physics.aps.org/articles/v11/93)). Existing tools either paste synthetic 2D labels onto a screen (*Sky Guide*, *Stellarium*) or trap catalog data inside sterile, government-portal database UIs (*NASA Eyes*, *WWT*) disconnected from your own photograph and horizon.

## Intended Audience (ICP)

- **Primary ICP — The Curious Everyday Explorer:** Anyone who looks up at the night sky, snaps a photo, and wants to experience and understand what is actually out there—without needing a telescope or an astrophysics degree. Albedian pairs exact scientific telemetry with human-understandable readouts (`1.1× Earth` gravity, `-39°C` surface temperature, and historical photon departure eras).
- **Secondary ICPs — Night-Sky Creators & Science Communicators:** Photographers and educators who turn real night-sky captures into provenance-backed, shareable 3D journeys from recorded camera pixels to planetary surfaces.

## The Five-Regime Spatial Journey (`@sparkjsdev/spark` + WebGL2 Ray-Marching)

1. **`01 · Night Sky` (`earth_capture`):** Restores the user's real recording (`+5.2 dB`), locks sub-arcsecond WCS coordinates (`0.38″`), and grounds the sky above a 3D ray-marched local horizon (Sahara Erg Chebbi dunes `31.2°N` or Aoraki / Lake Tekapo `44.0°S`).
2. **`02 · Cislunar Orbit` (`cislunar` — `1.28 light-sec`):** Transitions into Earth–Moon orbital space with Rayleigh/Mie atmospheric limb scattering and NASA LRO laser-altimetry lunar relief.
3. **`03 · Nearby Stars` (`solar_neighborhood` — `4.37–548 ly`):** Navigates 3D stellar photospheres and the `@sparkjsdev/spark` 3D Gaussian Splat Local Bubble dust cavity (`O'Neill et al., 2024, ApJ`).
4. **`04 · Deep Space` (`deep_sky` — `1,344–26,673 ly`):** Renders multi-wavelength 3D Gaussian Splat nebulae and relativistic Kerr/Schwarzschild geodesic gravitational lensing ([James, von Tunzelmann, Franklin & Thorne, 2015](https://iopscience.iop.org/article/10.1088/0264-9381/32/6/065001); [Bruneton, 2020](https://ebruneton.github.io/black_hole_shader/paper.pdf)).
5. **`05 · World Surface` (`planetary_surface`):** Lands on analytical-derivative GPU ray-marched worlds ([Inigo Quilez](https://iquilezles.org/articles/morenoise/))—including Shackleton Crater on the Moon, the glacial terminator canyons of Proxima Centauri b, Titan's hydrocarbon seas beneath Saturn's rings, and a relativistic tidal ocean world—while `Return to My Photo [Esc]` reversibly restores the exact source recording. See [`docs/product/PRODUCT_THESIS.md`](docs/product/PRODUCT_THESIS.md) for full citations and epistemic boundaries.

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
