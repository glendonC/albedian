# Albedian

**See (Beyond) the Night Sky.**  
*Start from your photograph—not an anonymous universe.*

Albedian is an observation-centered media restoration and 3D spatial astronomy instrument built for **Kiroween 2026** (*Frankenstein* category). It aims to turn a noisy night-sky photograph or video from Earth into a scientifically calibrated 3D portal: restoring only the stars your camera recorded, identifying them against star catalogs once a plate solution passes verification, and letting any curious explorer fly from their real earthly horizon to the worlds behind those points of light.

---

## Why Albedian Exists (Problem & Cited Research)

1. **Disappearing Access to the Night Sky:** Over **80% of humanity** (~83% globally, and >99% of the U.S. and Europe) lives under light-polluted skies, and **more than one-third cannot see the Milky Way** ([Falchi et al., 2016, *Science Advances*](https://pmc.ncbi.nlm.nih.gov/articles/PMC4928945/)). Global citizen-science observations from 2011–2022 showed naked-eye star visibility declining at a rate equivalent to **~9.6% annual sky-brightness growth** ([Kyba et al., 2023, *Science*](https://pubmed.ncbi.nlm.nih.gov/36656932/)).
2. **Photon-Limited Consumer Cameras:** Small apertures on phones and wearables suffer from Poisson photon shot noise, read noise, and Mie atmospheric glare at night ([Hasinoff et al., 2016, *ACM TOG / SIGGRAPH Asia*](http://graphics.stanford.edu/papers/hdrp/hasinoff-hdrplus-sigasia16.pdf); [Liba et al., 2019](https://arxiv.org/html/1910.11336)). Conventional denoisers often erase faint stars, whereas Albedian uses detection-preserving temporal burst restoration, reporting the SNR gain measured for each capture, paired with instant `Hold Original` verification.
3. **The Comprehension & Experience Chasm:** Recognizing a flat 2D dot on a sky map does not produce comprehension of 3D cosmic scale, reference-frame transitions, or light-travel time ([Plummer, 2014](https://eric.ed.gov/?id=EJ1029625); [Plummer et al., 2022, *JRST*](https://onlinelibrary.wiley.com/doi/10.1002/tea.21754); [Schneps et al., 2014](https://dl.acm.org/doi/10.5555/2753872.2753884); [Rajpaul et al., 2018](https://physics.aps.org/articles/v11/93)). Existing tools either paste synthetic 2D labels onto a screen (*Sky Guide*, *Stellarium*) or trap catalog data inside sterile, government-portal database UIs (*NASA Eyes*, *WWT*) disconnected from your own photograph and horizon.

## Intended Audience (ICP)

- **Primary ICP — The Curious Everyday Explorer:** Anyone who looks up at the night sky, snaps a photo, and wants to experience and understand what is actually out there—without needing a telescope or an astrophysics degree. Albedian pairs measured and catalog data with human-understandable readouts such as light-travel time and the historical era when that light set out.
- **Secondary ICPs — Night-Sky Creators & Science Communicators:** Photographers and educators who turn real night-sky captures into provenance-backed, shareable 3D journeys from recorded camera pixels to planetary surfaces.

## The Five-Regime Spatial Journey (`@sparkjsdev/spark` + WebGL2 Ray-Marching)

1. **`01 · Night Sky` (`earth_capture`):** Restores the user's real recording, locks WCS coordinates only after held-out star verification passes, and grounds the sky above a 3D ray-marched local horizon, with a northern or southern landscape picked by the observer's hemisphere.
2. **`02 · Cislunar Orbit` (`cislunar` — `1.28 light-sec`):** Transitions into Earth–Moon orbital space with Rayleigh/Mie atmospheric limb scattering and NASA LRO laser-altimetry lunar relief.
3. **`03 · Nearby Stars` (`solar_neighborhood` — `4.37–548 ly`):** Navigates 3D stellar photospheres and the `@sparkjsdev/spark` 3D Gaussian Splat Local Bubble dust cavity (`O'Neill et al., 2024, ApJ`).
4. **`04 · Deep Space` (`deep_sky` — `1,344–26,673 ly`):** Renders multi-wavelength 3D Gaussian Splat nebulae and relativistic Kerr/Schwarzschild geodesic gravitational lensing ([James, von Tunzelmann, Franklin & Thorne, 2015](https://iopscience.iop.org/article/10.1088/0264-9381/32/6/065001); [Bruneton, 2020](https://ebruneton.github.io/black_hole_shader/paper.pdf)).
5. **`05 · World Surface` (`planetary_surface`):** Lands on procedurally generated, illustrative worlds (analytical-derivative GPU ray-marching after [Inigo Quilez](https://iquilezles.org/articles/morenoise/)): Shackleton Crater on the Moon, an imagined canyon on Proxima Centauri b, Titan's hydrocarbon seas beneath Saturn's rings, and a fictional relativistic tidal ocean world. `Return to My Photo [Esc]` reversibly restores the exact source recording. See [`docs/product/PRODUCT_THESIS.md`](docs/product/PRODUCT_THESIS.md) for full citations and epistemic boundaries.

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
      public/catalog/stars/ # Gaia DR3 + Hipparcos 2007 catalog star tiles (docs/science/CATALOG_STAR_TILES.md)
  services/
    media/                  # Python Media Experiment Runtime (FFmpeg DAG, probe, audio remux)
    science/                # Python astrometry, WCS solver/verifier, pinned bright-star catalog & analytical ephemerides
    voice/                  # Context-bound intent parser & authoritative typed action validator
  packages/
    contracts/              # Authoritative TypeScript/Zod & JSON schemas for all 14 domain entities
    evidence/               # Epistemic state transitions, uncertainty formatting & guardrails
    observation/            # 4+1 orthogonal state machines, session reducer & NavigationHistory
    camera-rig/             # WCS pixel<->world math, quaternion SLERP, log-tan-FOV & floating origin
    design-system/          # Albedian observational instrument tokens, marker grammar & motion specs
  research/
    originals/              # Intact foundational research reports (2026-10-31 & 2026-11-02)
    experiments/            # Reproducible technical experiment registry & benchmark harnesses
  docs/
    product/                # Product thesis, experience states & UI/UX reference system
    architecture/           # Modular monolith topology, 14 entities, ADRs & licensing
    science/                # Astrometric math, WCS validation & evidence rules
  tests/
    fixtures/               # Deterministic astronomical starfields, media clips & golden WCS fixtures
    integration/            # Cross-package and service-to-UI integration tests
  .kiro/
    steering/               # Global and file-matched Kiro steering documents
    specs/                  # 8 Kiro domain specifications (requirements.md, design.md, tasks.md)
    hooks/                  # Validated Kiro 1.4.62 workspace hooks (.kiro.hook)
    agents/                 # Specialized agent role definitions
```

What works, what is still missing, the decisions waiting and the long-running jobs to start early are tracked in [`docs/ROADMAP.md`](docs/ROADMAP.md).

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

The API answers browser pages only from the web app's own origins, by default http://localhost:5173 and http://127.0.0.1:5173. A lane (web ports 5180 to 5188) or a preview (5174, 5186) that serves the web app on another port sets `ALBEDIAN_CORS_ORIGINS` to its origins, comma-separated, when it starts the API, for example `ALBEDIAN_CORS_ORIGINS=http://localhost:5180 uv run uvicorn services.api:app --port 8100`. Any other origin's preflight is refused, and its POST, PUT, PATCH or DELETE gets 403 before a route runs.

The API also answers only a `Host` that names this machine: localhost, 127.0.0.1 or [::1], on any port. A server reached under another name sets `ALBEDIAN_TRUSTED_HOSTS` to its host names, comma-separated, when it starts the API. Discover scans only `data/uploads`, `apps/web/public` and `data/artifacts` in this repository, whatever a request asks, and returns paths relative to those folders.

---

## Scientific Methodology & Limitations

- **Photons cannot be invented:** Low-light restoration operates on recorded signal (`Observed-data processing`) and constrained denoising. Albedian never pastes catalog stars onto dark images and claims they were recovered from camera pixels.
- **Independent WCS Verification:** A plate-solve candidate is accepted as `calibrated` only when at least 6 spatially distributed inliers pass held-out residual verification ($\text{median residual} \le 3.0\text{ px}$). Otherwise, the observation enters `unresolved`, `ambiguous`, or `predicted_only`.
- **3D Stellar Distances:** A single photograph encodes angular direction ($\alpha, \delta$), not depth. 3D positions in `Explore` mode derive from a pinned list of 71 bright stars with Hipparcos parallax distances (Gaia DR3 for 6 of them) and their uncertainty intervals (`median`, `lower`, `upper` pc), with a `Catalog Geometry · Distance Estimates` disclosure. A full Gaia DR3 layer with Bailer-Jones distances is planned.

---

## License

Released under the [MIT License](LICENSE). Third-party astronomical catalog attributions (`ESA Gaia DR3`, `CDS Hipparcos/SIMBAD`, `NASA JPL Ephemerides`) and library licenses are documented in [`docs/architecture/ARCHITECTURE_OVERVIEW.md`](docs/architecture/ARCHITECTURE_OVERVIEW.md).
