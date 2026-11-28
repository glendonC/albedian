# Albedian

**See (Beyond) the Night Sky.**  
*Start from your photograph, not an anonymous universe.*

Albedian is an observation-centered media restoration and 3D astronomy app built for **Kiroween 2026** (*Frankenstein* category). You upload a night photo or clip from Earth. It finds the points of light your camera recorded, names them from Gaia DR3 and Hipparcos only once a plate solution passes a check on stars held back from the fit, restores the picture without painting in a star, and flies you out to the stars it matched. How Kiro was used to build it is in [`docs/KIRO.md`](docs/KIRO.md).

---

## Why Albedian Exists (Problem & Cited Research)

1. **Disappearing Access to the Night Sky:** Over **80% of humanity** (~83% globally, and >99% of the U.S. and Europe) lives under light-polluted skies, and **more than one-third cannot see the Milky Way** ([Falchi et al., 2016, *Science Advances*](https://pmc.ncbi.nlm.nih.gov/articles/PMC4928945/)). Global citizen-science observations from 2011 to 2022 showed naked-eye star visibility declining at a rate equivalent to **~9.6% annual sky-brightness growth** ([Kyba et al., 2023, *Science*](https://pubmed.ncbi.nlm.nih.gov/36656932/)).
2. **Photon-Limited Consumer Cameras:** Small apertures on phones and wearables suffer from Poisson photon shot noise, read noise, and Mie atmospheric glare at night ([Hasinoff et al., 2016, *ACM TOG / SIGGRAPH Asia*](http://graphics.stanford.edu/papers/hdrp/hasinoff-hdrplus-sigasia16.pdf); [Liba et al., 2019](https://arxiv.org/html/1910.11336)). Conventional denoisers often erase faint stars. Albedian restores a clip with a temporal step in which each frame is helped only by registered neighbouring frames, and what they carry that the frame contradicts is kept out (`services/media/temporal.py`), and a photo one frame at a time; a wipe compares Original and Restored. It does not yet report the SNR gain for each capture [open].
3. **The Comprehension & Experience Chasm:** Recognizing a flat 2D dot on a sky map does not produce comprehension of 3D cosmic scale, reference-frame transitions, or light-travel time ([Plummer, 2014](https://eric.ed.gov/?id=EJ1029625); [Plummer et al., 2022, *JRST*](https://onlinelibrary.wiley.com/doi/10.1002/tea.21754); [Schneps et al., 2014](https://dl.acm.org/doi/10.5555/2753872.2753884); [Rajpaul et al., 2018](https://physics.aps.org/articles/v11/93)). Existing tools either paste synthetic 2D labels onto a screen (*Sky Guide*, *Stellarium*) or trap catalog data inside sterile, government-portal database UIs (*NASA Eyes*, *WWT*) disconnected from your own photograph and horizon.

## Intended Audience (ICP)

- **Primary ICP: The Curious Everyday Explorer:** Anyone who looks up at the night sky, snaps a photo, and wants to experience and understand what is actually out there, without needing a telescope or an astrophysics degree. Albedian pairs measured and catalog data with human-understandable readouts such as how many light years a star is from Earth.
- **Secondary ICPs: Night-Sky Creators & Science Communicators:** Photographers and educators who restore real night-sky captures and download each restored photo or clip with its provenance record.

## What It Does Today

1. **Your photo.** Upload a night photo or clip; the upload is stored by its SHA-256 and never changed. The reveal marks what it found with four tags, each a word and a shape: *Observed*, *Catalog matched*, *Predicted* and *Simulated* (`packages/design-system/src/look.ts`). Tap a star for its card: its name, its distance from Earth in light years, how far above the noise it was measured and the catalog row it was matched to (`apps/web/src/reveal/StarCard.tsx`). Deep-sky objects from OpenNGC are marked too (`apps/web/src/reveal/deepSky.ts`).
2. **Predicted sky.** When the stars do not solve and the file carries its time and place, Show predicted sky draws the sky that was above it then, labelled *Predicted* (`apps/web/src/reveal/PredictedSkyScreen.tsx`, [`docs/science/PREDICTED_SKY.md`](docs/science/PREDICTED_SKY.md)).
3. **Restore.** Restore makes a restored copy of a photo or a clip, a wipe compares Original and Restored, and Download gives a zip of the restored file with its provenance record (`apps/web/src/reveal/RestoreParts.tsx`, `services/media/router.py`).
4. **Real sky.** For a photo whose solution passed the check, Real sky shows it inside ESO's Milky Way panorama (`apps/web/src/reveal/RealSkyLayer.tsx`).
5. **3D.** Go there on a star's card flies to a *Catalog matched* star that has a catalog distance, or, from the predicted sky, to a *Predicted* star a catalog names, which stays *Predicted* (`flightRefusal` in `packages/contracts/src/astronomy.ts`); the flight is among the other stars at their Bailer-Jones (2021) or Hipparcos 2007 distances, all tagged *Simulated*. Your view puts the camera behind models of 41 spacecraft and suits from NASA's 3D Resources, each credited as its own page credits it (`apps/web/public/models/hangar/`, `apps/web/public/models/PROVENANCE.json`, `apps/web/src/spatial/flight/crafts.ts`); Your photo's view and Fly on your own are beside it. Back to Moment, or Esc, returns to the view you left (`apps/web/src/components/SpatialObservatoryViewport.tsx`).
6. **Sound.** The Sound control plays an adaptive soundtrack of three pieces that changes with the moment (arriving, travel, a star's card, the reveal); the stars on screen play its data notes and everything else is composed (`apps/web/src/audio/music/soundtrack.ts`, `apps/web/src/audio/music/soundtrackMoment.ts`).
7. **iPhone companion.** `apps/ios` aims with a predicted overlay, takes the photo exactly as the camera wrote it, records where the phone pointed as a hint, never as evidence, and sends both to the laptop over the local network ([`apps/ios/README.md`](apps/ios/README.md); `pnpm field --lan`).

---

## Core Principles

- **Real Observation First:** The source photograph or video (`SourceAsset`) is immutable and SHA-256 content-addressed.
- **Four Classes of Scientific Evidence:** Every celestial finding and visual marker carries an explicit epistemic classification:
  - `observed`: Measured directly in source pixels (centroid, SNR, flux).
  - `catalog_matched`: Linked to a catalog entry via an independently verified WCS solution with held-out pixel residual metrics.
  - `predicted`: Computed from time, observer location, and ephemerides without claiming pixel-level sensor recovery.
  - `simulated`: the 3D catalog-geometry flight and the models drawn in it, disclosed explicitly in the UI.
- **Seamless Continuity (`ObservationSession`):** Five state machines (`Media`, `Restoration`, `Calibration`, `Navigation` and `Voice`) are fields of one observation session, changed by one reducer (`packages/observation/src/reducer.ts`), with a reversible `CameraCheckpoint` stack.
- **Voice as Direct Manipulation:** Spoken and typed utterances (`"What's that one?"`, `"How far away is it?"`, `"Take me there"`, `"Go back"`) resolve against active pointer/selection context into schema-validated `AlbedianAction` commands (`packages/contracts/src/data/voice-command-grammar.json`). The optional Claude reading of a sentence is off unless `ALBEDIAN_ENABLE_LIVE_CLAUDE=1` and `ANTHROPIC_API_KEY` are set ([`docs/FIELD_USE.md`](docs/FIELD_USE.md)).

---

## Repository Structure

```text
/
  apps/
    web/                    # React + TypeScript + WebGL/Three.js + Web Audio observational shell
      public/catalog/stars/ # Gaia DR3 + Hipparcos 2007 catalog star tiles (docs/science/CATALOG_STAR_TILES.md)
      public/models/hangar/ # NASA's models of 41 spacecraft and suits, rebuilt for the flight (PROVENANCE.json)
    ios/                    # Albedian Capture, the iPhone companion (SwiftUI)
    webgpu-spike/           # The WebGPU renderer spike (docs/architecture/RENDERER_WEBGPU_SPIKE.md)
  services/
    api.py                  # The FastAPI app the web app talks to
    ingest/                 # Content-addressed uploads, metadata and probe readers, solve jobs
    contracts/              # The Pydantic side of the shared contracts
    media/                  # Python Media Experiment Runtime (FFmpeg DAG, probe, audio remux)
    science/                # Python astrometry, WCS solver/verifier, pinned bright-star catalog & analytical ephemerides
    voice/                  # Context-bound intent parser & authoritative typed action validator
  packages/
    contracts/              # Authoritative TypeScript/Zod & JSON schemas for all 14 domain entities
    evidence/               # Epistemic state transitions, uncertainty formatting & guardrails
    observation/            # Five state machines in one session reducer & NavigationHistory
    camera-rig/             # WCS pixel<->world math, quaternion SLERP, log-tan-FOV & floating origin
    design-system/          # Albedian observational instrument tokens, marker grammar & motion specs
  research/
    originals/              # Preserved foundational research reports
    experiments/            # Reproducible technical experiment registry & benchmark harnesses
    benchmarks/             # The simulated phone photo and real photo solver benchmarks
  docs/
    product/                # Product thesis, experience states & UI/UX reference system
    architecture/           # Modular monolith topology, 14 entities, ADRs & licensing
    science/                # Astrometric math, WCS validation & evidence rules
  tests/
    fixtures/               # Deterministic astronomical starfields, media clips & golden WCS fixtures
    integration/            # Cross-package and service-to-UI integration tests
  .kiro/
    steering/               # Global and file-matched Kiro steering documents
    specs/                  # 12 Kiro specifications (requirements.md, design.md, tasks.md)
    hooks/                  # 5 Kiro agent hooks (.kiro.hook)
    agents/                 # The albedian-auditor custom agent and the agent role definitions
```

The roadmap as it stood on 9 November is [`docs/ROADMAP.md`](docs/ROADMAP.md); how Kiro was used is [`docs/KIRO.md`](docs/KIRO.md).

---

## Quick Start

### 1. What You Need
Node 22 or later, pnpm 10.7.1 (the `packageManager` in `package.json`), [uv](https://docs.astral.sh/uv/), and ffmpeg with ffprobe on the `PATH` (an ffmpeg older than 7.1 refuses iPhone HEIC photos; JPEG works with any). A browser with WebGL 2. Built and walked on macOS 26 on an Apple silicon Mac; Linux and Windows have not been tried (CI runs the tests and the build on Ubuntu 24.04, never the app). The iPhone companion was built with Xcode 26.6 and needs iOS 26 ([`apps/ios/README.md`](apps/ios/README.md)).

### 2. Install and Start
```bash
pnpm install
uv sync
uv run python scripts/fetch_music_sources.py
pnpm field --check
pnpm field
```

`pnpm field --check` starts nothing and prints one line per need; fix any `MISSING` line first. `pnpm field` starts the API on 127.0.0.1:8000, the web app on http://localhost:5173 and the music sample server; open the web app and press Try a sample night, which stands beside Upload once the first start has fetched and solved the app's sample night in the background: the finished reveal of TBOE PC Ohm's public-domain (CC0) photo [Starry sky 2](https://commons.wikimedia.org/w/index.php?curid=178297645) from Wikimedia Commons (corpus photo `rp-b-16`, [`docs/science/REAL_PHOTO_CORPUS.md`](docs/science/REAL_PHOTO_CORPUS.md)), named as the sample photo with its credit. Or press Upload and give it a night photo of your own; to upload this one, download its original file from that page; on main at `f783fdc9a` on 27 November 2026 it calibrated in 13 s, and astrometry.net confirmed all 43 of its catalog matched stars ([`docs/science/REAL_PHOTO_BENCHMARK.md`](docs/science/REAL_PHOTO_BENCHMARK.md)). The music's samples are not in the repository: `uv run python scripts/fetch_music_sources.py` fetches the 144 files the app plays (CC0 and CC BY 4.0, about 262 MB, listed in [`data/music_sources.json`](data/music_sources.json)) into `.cache/music/sources`, and is safe to run again; without them the app runs and the music stays silent, and `pnpm field --check` counts them ([`docs/FIELD_USE.md`](docs/FIELD_USE.md)).

### 3. Run Verification & Property-Based Test Suites
```bash
# TypeScript unit + fast-check property-based tests (camera projection, DAG, evidence, navigation, voice, sync)
pnpm test

# Python scientific & media runtime tests + Hypothesis property-based tests
pnpm test:python

# Typecheck all packages and web application
pnpm typecheck
```

### 4. Or Start the API and the Web App Yourself
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
- **Independent WCS Verification:** A plate-solve candidate is accepted as `calibrated` only when at least 6 held-out detections are inliers and the residuals of every held-out pair, inlier or not, have a median of at most 3.0 px and a 95th percentile of at most 6.0 px. An upload's photo goes to the phone photo solver, which holds the 95th percentile of the held-out inliers to 5.5 px and also refuses a solution whose two solving paths disagree by more than 2.5 px or whose held-out matches could be chance (`services/science/phone_photo_solver.py`). Otherwise, the observation enters `unresolved`, `ambiguous`, or `predicted_only`.
- **The predicted sky:** An upload with a recorded capture time and place also gets the bright stars, the Sun, the Moon and the planets that were above its horizon at that moment, labelled `predicted` and placed on the picture only when a pose estimate lines it up; when its stars do not match, it ends `predicted_only` ([docs/science/PREDICTED_SKY.md](docs/science/PREDICTED_SKY.md)).
- **3D Stellar Distances:** A single photograph encodes angular direction ($\alpha, \delta$), not depth. 3D positions in `Explore` mode derive from a pinned list of 71 bright stars: 59 Hipparcos 2007 rows with geometric parallax distances and 12 Gaia DR3 rows with Bailer-Jones photogeometric distances, each with its uncertainty interval (`median`, `lower`, `upper` pc), under a `Catalog Geometry · Distance Estimates` disclosure. The web sky also draws the Gaia DR3 sources brighter than G 12 (3,087,666 in the tiles; 155 that repeat a bright Hipparcos star are left out), with Bailer-Jones (2021) distances ([docs/science/CATALOG_STAR_TILES.md](docs/science/CATALOG_STAR_TILES.md)).

---

## Continuous integration

`.github/workflows/gates.yml` runs the six gates on every pull request and every push to `main`, on Node 22 and 24
and on Python 3.12 and 3.14. The two pytest jobs each run one ffmpeg, so both HEIC paths stay tested:

- Python 3.12 runs Ubuntu 24.04's ffmpeg 6.1.1. Its ffprobe has no `-show_stream_groups`, so a tiled iPhone HEIC
  photo is refused, and that refusal is what this job tests.
- Python 3.14 runs a static ffmpeg 8.1.3 from FFmpeg-Builds (`github.com/FFmpeg-Builds/FFmpeg-Builds`), pinned by
  URL and SHA-256 to the last build of October 2026, which reads the tile grid; this job tests the stored pixels.
  That build is GPL version 3 or later, because the video export encodes with libx264, which the LGPL build lacks.
  CI downloads and runs it only; nothing in this repository ships it.
- Each job fails if its ffprobe is not the one it means to test (`tests/integration/test_ci_ffmpeg.py` checks the
  workflow says so).

---

## License

The [MIT License](LICENSE) covers this repository's own code and text; the third-party data, pictures, models, fonts and sounds it ships keep their own licences, some non-commercial (Gaia DR3, Hipparcos) and some share-alike (OpenNGC, the HYG database), as the `PROVENANCE.json` beside each asset folder under `apps/web/public/` and `apps/ios/notices/` records ([`docs/architecture/ASSET_PROVENANCE.md`](docs/architecture/ASSET_PROVENANCE.md)), with the sounds' notices in `apps/web/src/audio/assets/`. The catalogs and models used are ESA Gaia DR3, ESA Hipparcos 2007, the IAU's ERFA routines (through `pyerfa`, BSD 3-Clause; [`docs/architecture/THIRD_PARTY.md`](docs/architecture/THIRD_PARTY.md)) for the Sun, the Moon and the planets and, for the landing scene's Sun and Moon positions and check values (sub-observer and sub-solar points, pole and phase angles) only, the NASA/JPL Horizons API; their credits and the library licenses are in [`docs/architecture/ARCHITECTURE_OVERVIEW.md`](docs/architecture/ARCHITECTURE_OVERVIEW.md).
