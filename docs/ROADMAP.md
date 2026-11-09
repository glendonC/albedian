# Albedian Roadmap

This document records the current architecture state, open capability gaps, product and engineering decisions, and active roadmap tracks under [AGENTS.md](../AGENTS.md). Each milestone is sized for one engineering unit.

Labels, as AGENTS.md section 3 asks:

- **[tested]** locally implemented and tested capability
- **[verified]** verified external capability, checked at its primary source
- **[inference]** engineering inference
- **[proposed]** proposed design
- **[open]** unresolved question

## 1. Current state

### Implemented capabilities

- **Evidence contracts.** `ObservationSolution`, `CelestialFinding`, `RenderLayer`, `VoiceCommandResponse`, `SolarEclipseCatalog`, `LunarEclipseCatalog` and `ExoplanetTable`, with Zod and Pydantic parity and property tests on both sides. [tested]
- **Sky and universe in the browser.** Five scale regimes and a four-scene landing carousel. 3.09 million Gaia DR3 and Hipparcos 2007 stars to G 12, streamed per HEALPix tile with names, colours, distances, temperature, luminosity, extinction and astrometric quality ([CATALOG_STAR_TILES.md](science/CATALOG_STAR_TILES.md)). [tested]
- **WebGPU renderer, milestone 1.** Behind `?renderer=webgpu`, the cislunar scene draws catalog stars as point-spread sprites, tone-maps with AgX and discloses each layer's evidence class, in about 1 ms per frame at 3840x2160 on the M5 Max ([RENDERER_WEBGPU_SPIKE.md](architecture/RENDERER_WEBGPU_SPIKE.md), section 10). [tested, measured]
- **Spatial audio engine.** HRTF voices, an ambisonic bed and convolution reverb driven by data mappings, with `SkyAudioFeed` feeding visible catalog stars from the camera pose, evidence-class disclosure and third-party notices in the viewport ([AUDIO_ENGINE.md](architecture/AUDIO_ENGINE.md)). [tested]
- **Backend.** FastAPI endpoints covering content-addressed upload ingestion (`services/ingest/store.py`, `services/ingest/router.py`), EXIF/HEIF/QuickTime metadata, orientation and probe readers (`services/ingest/metadata.py`, `services/ingest/probe.py`), a plate solver with a held-out WCS gate (at least 6 held-out inliers and a median residual of at most 3 px), a horizon and Moon pose solver, a low-precision analytical ephemeris (`services/science/ephemeris.py`: Standish and Williams 1992 approximate Keplerian elements and Meeus 1998 chapter 47 geocentric Moon), a moonlight model, and a schema-validated voice command endpoint (`services/voice/intent.py`, `services/voice/claude.py`). [tested]
- **Look-dev.** A Blender 5.2 pipeline renders real moments from NASA Blue Marble, Black Marble and LRO data (`scripts/lookdev/`). [tested]
- **Gates.** `pnpm typecheck`, `pnpm test`, `uv run pytest`, `pnpm verify:contracts`, `pnpm verify:docs` and `pnpm build` all pass on `main`, and CI runs all six on every push and pull request (`.github/workflows/gates.yml`: Node 22 and 24, Python 3.12 and 3.14; a skip outside the `owner_media` and `local_dataset` markers fails the run, and the build refuses any untracked, locally modified or symlinked file under `apps/web/public`). [tested]

### The gap that matters most

The first promise of the product, upload a night photo and learn where and when it was taken, does not solve a real consumer photo end-to-end yet:

- The backend upload store, metadata/probe readers, and ingest routes are in place (`services/ingest/store.py`, `services/ingest/metadata.py`, `services/ingest/probe.py`, `services/ingest/router.py`), while the browser upload UI and asynchronous solve-job runner are still being connected (`apps/web/src/App.tsx`).
- The baseline plate solver matches against 18 pinned Orion and Taurus stars (`services/science/catalog.py`). It failed on all three real clips in `data/artifacts/`. On the simulated phone-photo benchmark it solved 0 of 210 frames, and once it certified a wrong sky, about 70 degrees off, that passed the held-out gate ([PHONE_PHOTO_BENCHMARK.md](science/PHONE_PHOTO_BENCHMARK.md)). [tested, simulated]
- The horizon and Moon solver returns `approximate` or `predicted_only`, never `calibrated`. It looks for the horizon only between 38% and 72% of the frame height (`services/science/horizon_solver.py`).

The real sky behind the glow, the reveal, the travel and the explanations all depend on a verified solution, so Track 1 comes first.

### Other gaps

- **Science.**
  - The ephemeris (`services/science/ephemeris.py`) uses the Standish and Williams (1992) approximate Keplerian elements (Earth-Moon barycentre, without light-time correction) and Meeus (1998) chapter 47 for a geocentric Moon (topocentric parallax is up to about 1 degree [inference]), without the Sun, Mercury, Neptune, or computed phase magnitudes.
  - Radiometry assumes a fixed natural sky and has no light-pollution atlas data yet (`services/science/router.py`, `services/media/discovery.py`).
- **Language.**
  - `services/voice/claude.py` integrates the official Anthropic Python SDK (`claude-opus-5-5` from configuration, strict tools with `tool_choice` `auto`, server-side refusal fallback and prompt caching), and `VoiceCommandResponse` carries schema-validated `AlbedianAction` payloads through the frontend reducer (`packages/contracts/src/spatial-and-voice.ts`, `packages/contracts/src/voice-command.ts`, `services/contracts/voice.py`, `apps/web/src/voice/voiceBackend.ts`). Live LLM calls stay off by default until the evaluation budget and corpus are complete. [tested]
  - There is no knowledge base, retrieval or citation system yet.
- **Data.** The star tiles stop at G 12, and their positions stay at epoch J2016.0. They carry Gaia DR3 temperature, luminosity, extinction and astrometric quality where DR3 gives them, and nothing where it does not ([CATALOG_STAR_TILES.md](science/CATALOG_STAR_TILES.md)). [tested] Nine assets still have no recorded provenance (section 6).
- **Rendering.**
  - Only the cislunar scene has WebGPU parity; every active scene carries per-layer evidence-class disclosures, and the synthetic nebula and black-hole decorative particle layers have been removed (`apps/web/src/spatial/SparkPortalRenderer.ts`). [tested]
  - The atmosphere lookup is single scattering with hand-set colours, not Bruneton's model.
- **Security.** The API answers only the web app's own origins (`ALBEDIAN_CORS_ORIGINS`) and only a `Host` that names the local host (`ALBEDIAN_TRUSTED_HOSTS`), refuses a foreign write before any route runs, scans only its configured folders, and returns no absolute path. Both Vite builds refuse any untracked, locally modified or symlinked file under `apps/web/public/`. [tested] A public deployment still needs its own review (section 3, Track 7).

## 2. Product and architecture decisions

1. **Commercial or not.** Several of the best sources are non-commercial:
   - Gaia, Hipparcos and Planck data (CC BY-NC 3.0 IGO). Commercial use needs ESA authorisation.
   - The Falchi light-pollution atlas (CC BY-NC 4.0).
   - The Open-Meteo free tier.
   - DSS2 imagery and the Mellinger panorama.

   Decision: non-commercial for the current phase, targeting the 4 December 2026 public demo. These sources can be used with their credits visible in the app. Revisit before any commercial use, sponsorship, prize terms that grant commercial rights, or adoption by a company.
2. **The sky dome registration.** Implemented across the WebGL and WebGPU renderers ([SKY_PANORAMA_REGISTRATION.md](science/SKY_PANORAMA_REGISTRATION.md)):
   - The NASA star map is removed: it duplicated the catalog stars and added a second, misplaced Milky Way.
   - Venus and Jupiter stay in the panorama, disclosed as planets the 2008 to 2009 shoot photographed.
   - The `halpha` and `jwst_ir` modes and the red boost are removed: they recoloured the RGB photograph.
   - The landing turns toward the galactic centre at true scale, seen from beyond the Moon, at one real moment under one Sun (`2025-03-07T11:00:00Z`, [LANDING_MOMENT.md](science/LANDING_MOMENT.md)), while the fidelity pass in Track 4 replaces magnified background stars with catalog stars and diffuse Milky Way light.
3. **The AgX look.** Set the contrast and black level for the WebGPU renderer (spike doc section 10, step 12).
4. **Modelled set pieces.** Betelgeuse sits at the centre of the Local Bubble, the Earth and Moon are lit from different directions, and the Saturn horizon is a stylised world. Decision: each is rebuilt to its physical layout as its scene is ported, and until then its disclosure states plainly that it is a modelled composition.
5. **Model for intent parsing.** Track 3 starts every route on `claude-opus-5-5`. Whether the latency-critical intent route later moves to a smaller model is decided after measuring.
6. **Hosting.** Deep catalogs and dust volumes need object storage and a CDN. For example, Cloudflare R2 costs $0.015 per GB-month with no egress fees [verified].
7. **Audio is music first.** Decision: a composed, adaptive score is the foundation, and the real catalog data plays inside it (Track 5).
8. **Every interaction pattern comes from a proven reference.** Decision: each moment of the experience takes its pattern from a game or app that already does it well, chosen for a stated reason, never an invented style (Track 8).

## 3. Tracks

Each track lists its owners from AGENTS.md section 2, its milestones in order, and what done means. Tracks 1, 2 and 3 can run in parallel because their files do not overlap.

### Track 1: from a real photo to a verified sky

Owners: `science` and `media`, with `experience` for the upload UI.

- **M1, upload path.** Send the browser upload to the backend as an immutable, content-addressed `SourceAsset`. Extract EXIF time, GPS and lens data as hints, recording where each came from. Run the solve as a job whose progress comes from real denominators. Missing metadata stays missing. The ingest contracts (`packages/contracts/src/ingest.ts` and `services/contracts/ingest.py`), the content-addressed upload store (`services/ingest/store.py`), the EXIF/HEIF/QuickTime metadata and probe readers (`services/ingest/metadata.py`, `services/ingest/probe.py`), the ingest routes in `services/ingest/router.py` (`POST /api/ingest`, `GET /api/ingest/{sha256}`, `GET /api/ingest/{sha256}/original`), and optional capture times on `ObservationSolution` are implemented and tested. [tested] Next come the browser upload connection and the solve job. [proposed]
- **M2, a solver for phone photos.** [proposed, from the research below]
  1. **Detect.** Mask the sky and detect stars on the original pixels only, with SEP (LGPL) or cedar-detect (source-available under FSL-1.1-MIT) [verified]. For the mask, SAM 2.1 runs on Apple Silicon through Apple's Apache-2.0 Core ML conversions [verified], but it is untested on night skies.
  2. **Hint-assisted solve.** When EXIF gives the time and place, solve for attitude only, as `ascal` does for all-sky cameras. `ascal` is MIT-licensed (2026) and reaches a median residual of 0.42 px across 23 systems [verified].
  3. **Blind solve.** Without hints, use cedar-solve (Apache-2.0) with a custom 35 to 85 degree database and a distortion search range. Fall back to astrometry.net indexes 4112 to 4119 (11 MB) on a central crop. astrometry.net is GPL v3, so run it as a separate process [inference].
  4. **Lens model.** Fit a full lens model (Brown-Conrady or Kannala-Brandt) against catalog stars from the existing G 12 tiles, which replace the 18-star catalog.
  5. **Accept.** Keep a solution only if it passes the existing held-out gate and two independent paths agree.
- **M3, golden corpus and benchmark.** The benchmark's ground-truth camera, astrometry, renderer, frame suite, scorer, solver adapters and baseline run live in `research/benchmarks/phone_photos/`, tested against ERFA and Meeus: the baseline 18-star solver solved 0 of 210 simulated frames and once certified a wrong sky, so M2's acceptance test weighs each match against its chance of coincidence ([PHONE_PHOTO_BENCHMARK.md](science/PHONE_PHOTO_BENCHMARK.md)). [tested, simulated] The corpus has four parts:
  - Synthetic Gaia fields rendered through phone fields of view, lens distortion, light-pollution gradients and foregrounds.
  - Real phone frames in RAW and JPEG, with ground truth from a co-mounted narrow-field camera.
  - Hard negatives (cloud, aircraft, the Moon, screens), scored on producing zero false solves.
  - LenghuSky-8 all-sky frames (CC BY 4.0 [verified]) to stress fisheye handling.
- **M4, ephemeris.** Replace the Keplerian elements in `services/science/ephemeris.py` with JPL DE440 (114 MB [verified]). Add the Sun, Mercury and Neptune, make the Moon topocentric, and compute magnitudes from phase. Test against JPL Horizons from the backend only, since its fair-use rules forbid browser calls [verified]. [proposed]
- **M5, sky brightness.** Model artificial sky brightness from VIIRS Black Marble VNP46A4 (NASA data, CC0 [verified]) through a Garstang/Cinzano model, or use the Falchi atlas if decision 1 allows. Calibrate against Globe at Night (CC BY 4.0) and TESS-W photometers. Drop the fixed radiometry target. [proposed]
- **M6, detection integrity.** A denoised `MediaVariant` may propose candidates, but each must pass forced photometry on the original pixels. Injection-recovery runs publish completeness and false-positive rates, and a source seen only in a variant is never `observed`. [proposed]

Done when a real phone photo from the corpus solves in the app to a verified WCS with held-out residuals, and the benchmark reports its solve rate and zero false solves on the hard negatives.

### Track 2: a universe built from real catalogs

Owners: `science` for catalogs, `architecture` for contracts, `verification` for provenance checks.

- **M1, data registry.** Every file under `apps/web/public` has a `PROVENANCE.json` record (source, version, licence, credit, checksum and bake script) or a pinned gap in `scripts/provenance_gaps.json`, enforced in CI, and unused unrecorded assets have been removed. [tested] Next: close the last remaining gap in section 6. [proposed]
- **M2, small authoritative tables.** Contracts for `SolarEclipseCatalog`, `LunarEclipseCatalog` and `ExoplanetTable` live in `packages/contracts/src/eclipses.ts`, `packages/contracts/src/exoplanets.ts`, `services/contracts/eclipses.py` and `services/contracts/exoplanets.py`, and Espenak's solar and lunar eclipse catalogues are baked in `apps/web/public/catalog/eclipses/solar.json` and `apps/web/public/catalog/eclipses/lunar.json` with provenance [tested]; remaining table bakes and ingestion: [verified sizes; ingestion proposed]
  - NASA Exoplanet Archive PSCompPars (6,451 planets).
  - The ATNF Pulsar Catalogue (4,503 pulsars).
  - OpenNGC (13,970 objects, CC BY-SA 4.0).
  - The live IAU star-name list (652 names; the tiles use 451 from 2022).
  - The IAU Meteor Data Center showers.
  - MPCORB (321 MB) for asteroids.
- **M3, Gaia DR4 and deeper tiles.** Gaia DR4 is scheduled for 2 December 2026, with the archive offline from 25 November [verified]. [proposed]
  - Run the deep extraction once, on DR4. G < 17 holds 157.7 million stars in DR3 [verified count], about 4.3 GB in the current tile format [inference].
  - Add GSP-Phot temperature, FLAME luminosity, extinction, parallax error and RUWE to the tiles.
  - Propagate positions from J2016.0 to the viewing date.
- **M4, real dust.** [proposed]
  - Edenhofer et al. 2024: CC BY 4.0, either 3.25 GB as a HEALPix mean and standard deviation or 15.7 GB on a 2 pc grid [verified].
  - The O'Neill et al. 2024 Local Chimney model, published on Harvard Dataverse under CC0 (3.5 GB) [verified]. It replaces the current GLB-derived Local Bubble splats.
  - Bake both to sparse bricks for the WebGPU volume. Bayestar19 (CC0) covers the northern sky beyond 1.25 kpc.
- **M5, galaxies.** Cosmicflows-4 (55,877 galaxies with measured distances), 2MRS (44,599) and GLADE+ (23.2 million, 13 GB) [verified sizes]. [proposed]
- **M6, live feeds through the backend.** [proposed]
  - JPL Horizons and SBDB.
  - CelesTrak OMM, downloaded once per 2-hour update. Use OMM only, since 5-digit catalogue numbers ran out in July 2026 [verified].
  - NOAA OVATION aurora.
  - Cloud cover.
- **M7, hosting.** Object storage and a CDN for tiles and volumes, with versioned releases and checksums (decision 6). [proposed]

Done when every rendered layer and every tool answer names a registered source and its licence.

### Track 3: an understanding layer, not just an explorer

Owners: `voice`, with `architecture` for action contracts and `science` for tools.

No model training is needed. Claude 3.5 Sonnet already scored 85% on the AstroMLab-1 astronomy benchmark in 2024 [verified]. What a model lacks is the user's own data, so grounding comes from deterministic tools, a licensed corpus and citations, with evidence classes carried into every answer. [inference]

- **M1, a sound model path.** Implemented in `services/voice/claude.py`: the official Anthropic Python SDK, `claude-opus-5-5` read from configuration, strict tools with `tool_choice` `auto`, a server-side refusal fallback that logs the model that served each answer, and a cached system prompt. Live calls stay off until an eval budget exists. [tested]
- **M2, actions that run.** Implemented across `packages/contracts/src/spatial-and-voice.ts`, `packages/contracts/src/voice-command.ts`, `services/contracts/voice.py`, `packages/observation/src/reducer.ts`, `services/voice/intent.py` and `apps/web/src/voice/voiceBackend.ts`:
  - `AlbedianAction` covers 20 action types, including regime and scene navigation, audio and free-form questions. [tested]
  - The backend returns schema-validated `VoiceCommandResponse` payloads that the frontend validates and dispatches through the reducer. [tested]
  - Python and TypeScript enforce identical rules for `space.enter` on `predicted_only` solutions (`services/voice/intent.py` and `packages/observation/src/reducer.ts`). [tested]
  - Year parsing uses linear-time anchored patterns without a hardcoded fallback year, and predicted bodies are evaluated at the recording's capture epoch. [tested]
- **M3, tools.** Every number in an answer comes from a tool result. [proposed] The tools:
  - the ephemeris (Track 1 M4);
  - catalog lookup, through the tiles plus SIMBAD over TAP (ODbL);
  - cross-identifications through Wikidata (CC0);
  - exoplanets, events and sky conditions;
  - the user's own `ObservationSolution`, with its evidence classes.
- **M4, a licensed corpus with citations.** [proposed]
  - **Use:** Wikidata (CC0), arXiv metadata and abstracts (CC0), IAU texts (CC BY 4.0), NASA pages (generally not copyrighted; credit NASA), ESA/Hubble (CC BY 4.0) and Wikipedia (CC BY-SA 4.0: attribute, and share alike for adaptations).
  - **Do not use:** NASA ADS abstracts, which are publisher copyright; ADS forbids building a copy [verified].
  - Use the API's document citations (`citations: {enabled: true}`). They cannot be combined with structured outputs in the same request [verified].
- **M5, evals before shipping.** [proposed]
  - Explanations of golden observations, with every number checked against tool output.
  - Evidence-class discipline: never call predicted or simulated content observed.
  - Abstention on unsolved or ambiguous frames.
  - Language to `AlbedianAction`, including "that one" and "there".
  - An AstroMLab-1 slice as a regression check.
  - Prompt injection through file names and metadata.
- **M6, voice.** Text-to-speech (not yet implemented), barge-in, and a latency budget. [proposed]

Done when a user can ask "what is that bright one?" about their own photo and get a cited, evidence-labelled answer computed from their solution and the catalogs, and the eval set passes.

### Track 4: rendering

Owners: `spatial` and `experience`.

The WebGPU migration continues from [RENDERER_WEBGPU_SPIKE.md](architecture/RENDERER_WEBGPU_SPIKE.md) section 10, steps 6 to 12: the remaining scenes, dust volumes, ray-marched set pieces, capture-site splats, removing the WebGL-only code, verification and the AgX art pass. This roadmap adds:

- **Real atmosphere.** Replace the hand-tuned lookup with Bruneton 2017's precomputation (BSD-3; the bake takes about 250 ms on a GPU [verified]) or with `@takram/three-atmosphere/webgpu` (MIT, work in progress, needs three 0.182 or later [verified]). [proposed]
- **The night sky, physically.** Model airglow, zodiacal light and integrated starlight, which the GAMBONS model covers [verified], plus artificial sky brightness from Track 1 M5. [proposed]
- **Dust from data.** Render the Track 2 M4 volume with CCM89 reddening of everything behind it, which the spike already measured. [proposed]
- **Sky dome registration.** The ESO panorama is registered to the catalog stars in both renderers, the plotted star map, the synthesized `halpha` and `jwst_ir` bands and the red boost are removed ([SKY_PANORAMA_REGISTRATION.md](science/SKY_PANORAMA_REGISTRATION.md)), the credits of the drawn imagery and catalogs show beside the canvas without a click, the catalog stars are drawn at infinity so they do not shift against the dome, and the registration pins only the 60 core tiles its fit reads. [tested]
- **Textures.** `lro_moon_2k.jpg` is 1024x512; its replacement comes from the 8k colour map and the LOLA relief of NASA SVS's CGI Moon Kit (svs.gsfc.nasa.gov/4720), with recorded provenance, and the Earth day map uses NASA's March 2004 Blue Marble base map. [tested] The plotted star map is gone from the app; the Blender look-dev sky still uses the NASA SVS Deep Star Maps and moves to the registered panorama. [proposed]
- **Honest set pieces.** `derivePlanetaryWorldDescriptor`, the decorative deep-sky and black-hole particle clouds, and `nasa_pillars_3d.glb` / `nasa_nebula_splats.json` have been removed (`apps/web/src/spatial/SparkPortalRenderer.ts`), and modelled scenes carry explicit disclosures per decision 4. [tested]
- **Switching the default.** Switch after Safari and Windows runs, per-regime snapshot tests and frame-time budgets in CI. [proposed]
- **The landing's fidelity, first.** The Milky Way behind the Moon is a 6000x3000 photograph, magnified about three to five times at the landing's view, so its stars turn soft, and the camera holds still [tested]. Point stars come only from the catalog tiles, sharp at any zoom; the Milky Way becomes diffuse light only; a slow camera motion gives real parallax at the same moment; every image layer's pixels per degree are measured against 1080p and 4K. [proposed]

Done when every landing scene runs on WebGPU with layer disclosures, the AgX look is signed off and the WebGL path is gone.

### Track 5: audio

Owners: `experience`, with `science` for the data fields.

- Connect the data inputs (`setSources`, `setListenerOrientation`, `setSkyBrightness`, `setUnveil`) to the catalog tiles, the camera, radiometry and the reveal ([AUDIO_ENGINE.md](architecture/AUDIO_ENGINE.md), section 8). `SkyAudioFeed` feeds visible catalog stars from the camera pose into the audio engine with evidence-class disclosure and third-party notices in the viewport. [tested]
- Add temperature, luminosity, extinction and pulsar spin to the contracts, as AUDIO_ENGINE.md escalates, fed by Track 2 M2 and M3. [proposed]
- In the 13-item listening evaluation ([AUDIO_ENGINE.md](architecture/AUDIO_ENGINE.md), section 10), data-only sonification was rejected in favour of music with a beat and swells (decision 7). Next: three short music directions over the real landing for selection by ear, then the chosen score in the app, with its credits and a statement of what the data controls. [proposed]
- Ship the Apache-2.0 notices for Omnitone and the York HRTFs (`apps/web/src/audio/assets/THIRD_PARTY_NOTICES.txt`, `apps/web/src/audio/audioCredits.ts`). [tested]
- Learn from published, data-grounded work: the NASA Chandra sonifications, SYSTEM Sounds' TRAPPIST-1 piece and STScI's Astronify [verified]. [proposed]

### Track 6: the capture site (later)

Owner: `spatial`, with `media`. [proposed]

- **Structure from motion:** COLMAP 4.x. Splats need only the sparse model, and COLMAP builds that on macOS without CUDA.
- **Splat training on Metal:** msplat (Apache-2.0; 700 s for 30,000 iterations on an M4 Max, by its own benchmark) or Brush (Apache-2.0) [verified].
- **Cloud fallback:** gsplat takes about 20 to 36 minutes per scene on one GPU [verified], roughly $1 per scene [inference].
- **Capture:** shoot the site at twilight and register the night frames to it [inference].
- **Rendering:** draw it with three's `GaussianSplat` in the WebGPU host (spike doc section 6).

### Track 7: platform hygiene

Owner: `verification`, with each area's owner.

- **CI.** Runs on every push and pull request across Node 22 and 24 and Python 3.12 and 3.14 (`.github/workflows/gates.yml`), enforcing all six verification gates, provenance verification, Vitest `skip`/`todo` rejection (`scripts/vitest-no-unexpected-skips.mjs`), and pytest `skip`/`xfail`/deselection rejection (`tests/conftest.py`). [tested]
- **Security.** CORS is restricted to the web app's own origins with no credentials and foreign writes refused before any route runs; trusted-host checks guard against DNS rebinding; discovery scans only configured folders and returns relative paths; both Vite builds (`apps/web/vite.config.ts` and `apps/webgpu-spike/vite.config.ts`) refuse any untracked, locally modified or symlinked file under `apps/web/public/`; and private clip renders live in the gitignored `data/renders/` directory outside the web root. [tested] A hosted deployment still needs its own infrastructure review before public launch. [proposed]
- **Docs verification.**
  - The Gaia DR3 CC BY-NC 3.0 IGO licence is stated consistently across [CATALOG_STAR_TILES.md](science/CATALOG_STAR_TILES.md) and [ARCHITECTURE_OVERVIEW.md](architecture/ARCHITECTURE_OVERVIEW.md). [tested]
  - `AGENTS.md` cites only real repository directories, enforced by `pnpm verify:docs` (`scripts/verify-docs.mjs`). [tested]
  - `EXP-01` through `EXP-12` in `research/experiments/EXPERIMENT_REGISTRY.md` align their summary status labels and detailed specifications with what each test actually runs. [tested]
  - `AUDIO_ENGINE.md` documents the connected `SkyAudioFeed` and volume controls alongside the radiometry endpoint. [proposed]
- **Fabrication removal.**
  - The synthetic vision calibrator, which sent no image, is removed. [tested]
  - The backend reports no synthetic 999 px or fit residual as held-out, gives no source a default hash, lists no unreadable file at a fallback size or frame rate, and labels no star measured through an unverified pose as `observed`. [tested]
- **Data outside git.** `data/uploads` (205 MB), `data/artifacts`, `data/lookdev` (289 MB) and several gitignored splats exist only on the local development machine.

### Track 8: the experience

Owner: `experience`.

How a person explores Albedian, from arriving on the landing to going back to their photo. Every pattern comes from a proven reference (decision 8).

- **Reference board.** For each moment (the landing, uploading a photo, the solve and the reveal, exploring, picking a star and learning about it, travelling, voice, Back to Moment, the credits), a pattern from games and apps that already do it well, with official screenshots, for review, and mockups over real WebGPU captures and catalog rows in the experience cache [verified]; the viewport HUD components (`apps/web/src/components/CelestialMarkerOverlay.tsx`, `apps/web/src/components/CelestialTargetCard.tsx`, `apps/web/src/components/FlightAndRestoreHud.tsx`, `apps/web/src/components/VoiceCommandHud.tsx`, `apps/web/src/components/SpatialObservatoryViewport.tsx`) are implemented and tested [tested], with the remaining screen builds following one screen at a time. [proposed]
- **Exploring.** Beyond the landing's Launch, picking, labels, object cards, flight, arrival and return navigation are built step by step against the reference board. [proposed]
- **A companion for third-person flight.** An optional mode in which you fly as an original Shiba Inu companion character, leaving a trail whose colours follow the real colours of the stars it passes. Its art and sound are original; any existing character would need its creator's permission. It is labelled as playful, never as data, and comes after the demo-critical tracks unless it proves cheap. [proposed]

Done when the reference board and mockups are signed off and the app follows them.

## 4. Long-running jobs to start early

None of this is model training. The only GPU work is optional splat training, plus real-time rendering.

| # | Job | Unblocks | Size or time, where grounded | Compute | Start |
|---|---|---|---|---|---|
| 1 | Real-sky golden corpus | Solver choice and every regression gate | Limited by weather, the Moon and the season | Camera time | Active |
| 2 | Licence requests (ESA commercial authorisation, the Falchi atlas form) | A commercial launch; the light-pollution layer | Lead time [open] | None | Before commercial launch |
| 3 | Gaia DR4 deep extraction and tile bake | Deeper sky, the 3D universe, temperatures for audio | 157.7 M stars at G < 17 (DR3 count). DR3's bulk `gaia_source` was 753 GB; output about 4.3 GB of tiles [inference]. The G 12 bake took 10.5 minutes | CPU | After 2 December 2026 |
| 4 | Dust download and resampling pilot | Dust volume, reddening, the Local Bubble | 3.25 GB (Edenhofer HEALPix) plus 3.5 GB (O'Neill), or 15.7 GB for the 2 pc grid. Memory-heavy; no published timing | CPU | Active |
| 5 | Capture-site pilot | Track 6 | COLMAP took 478 s on a MacBook Air; msplat 700 s on an M4 Max | Metal GPU | Later |
| 6 | Night segmentation labels and an injection-recovery benchmark | Sky masks; the original-pixels rule | Human labelling time | CPU, Core ML | With Track 1 M2 |
| 7 | LLM eval sets | Track 3 | Authoring time | None | With Track 3 M1 |

Quick jobs to run when needed [verified]:

- The astrometry.net 4100 indexes: 356 MB in all, 11 MB for 4112 to 4119.
- The cedar-solve default database: under 7 minutes on a Raspberry Pi 4.
- The Bruneton bake: about 250 ms.
- DE440: 114 MB.

## 5. Data sources

Sizes are downloads checked at the primary sources named.

| Source | What it gives | Size | Licence | Where | Track |
|---|---|---|---|---|---|
| Gaia DR3 | 1.81 billion sources; 157.7 M at G < 17; temperatures for 471 M, luminosities for 281 M | Bulk `gaia_source` 753 GB, astrophysical parameters 256 GB; TAP for subsets | CC BY-NC 3.0 IGO, credit "ESA, Gaia DPAC" | gea.esac.esa.int | 2 (have G < 12) |
| Gaia DR4 | 2.0 billion sources, 66 months of data | About 400 TB in all | ESA terms expected [open] | Due 2 Dec 2026 | 2 M3 |
| Bailer-Jones et al. 2021 | Geometric and photogeometric distances with bounds | 38 GB bulk | CC BY 4.0 | ESA `external.gaiaedr3_distance` | 2 (have G < 12) |
| Hipparcos 2007 | 117,955 bright stars | 8.6 MB | ESA terms, credit ESA | VizieR I/311 | Have |
| IAU WGSN | 652 proper star names | Small | Free to use | iau.org | 2 M2 |
| SIMBAD | 20.5 M objects, 70.2 M identifiers | TAP | ODbL | simbad.cds.unistra.fr | 3 M3 |
| AAVSO VSX | 10.3 M variable stars | 941 MB CSV | [open] | VizieR B/vsx | Later |
| astrometry.net 4100 indexes | Wide-field solving from Tycho-2 | 356 MB (11 MB for 4112 to 4119) | GPL v3 (software) | data.astrometry.net | 1 M2 |
| cedar-solve | Fast blind solving | 14.7 MB default database | Apache-2.0 | github.com/smroid/cedar-solve | 1 M2 |
| JPL DE440 | Sun, Moon and planets, 1550 to 2650 | 114 MB (DE441, 3.08 GB, for deep history) | [open] | naif.jpl.nasa.gov | 1 M4 |
| JPL Horizons and SBDB | Ephemerides and small bodies | API | Fair use: one request at a time, cache results, no browser calls | ssd-api.jpl.nasa.gov | 2 M6 |
| MPCORB | 1.58 M minor-planet orbits | 321 MB | Redistributable with the source credited | minorplanetcenter.net | 2 M2 |
| CelesTrak GP (OMM) | Satellite orbits | Small | No licence stated; strict rate limits | celestrak.org | 2 M6 |
| OpenNGC | 13,970 deep-sky objects | 3.9 MB | CC BY-SA 4.0 | github.com/mattiaverga/OpenNGC | 2 M2 |
| Cosmicflows-4 | 55,877 galaxies with measured distances | [open] | [open] | EDD, VizieR | 2 M5 |
| 2MRS | 44,599 nearby galaxies | [open] | [open] | VizieR | 2 M5 |
| GLADE+ | 23.2 M galaxies and quasars | 13 GB | None stated | VizieR VII/291 | 2 M5 |
| Edenhofer et al. 2024 | 3D dust from 69 pc to 1.25 kpc | 3.25 GB HEALPix; 15.7 GB on a 2 pc grid; 122.7 GB in all | CC BY 4.0 | doi:10.5281/zenodo.8187943 | 2 M4 |
| O'Neill et al. 2024 | The Local Bubble (Local Chimney) surface | 3.5 GB | CC0 | doi:10.7910/DVN/INB1RB | 2 M4 |
| Bayestar19 | 3D dust north of declination -30 | 0.73 GB | CC0 | doi:10.7910/DVN/2EJ9TX | 2 M4 |
| ESO eso0932a | Milky Way panorama (in use) | 6000x3000 | CC BY 4.0, credit ESO/S. Brunier | eso.org | Have |
| NASA SVS Deep Star Maps 2020 | Plotted star map, removed from the app; the Blender look-dev sky still uses it | Up to 3.8 GB EXR | NASA credit; whether Gaia DR2 terms apply is [open] | svs.gsfc.nasa.gov/4851 | Removed |
| CDS HiPS (DSS2, 2MASS, AllWISE, Planck, Fermi) | Multiwavelength all-sky images | Tiled | ODbL packaging. DSS2 is not cleared for products, Planck is CC BY-NC, the others are [open] | alasky.cds.unistra.fr, skies.esac.esa.int | Only once cleared |
| NASA Exoplanet Archive | 6,451 planets around 4,847 hosts | TAP | No restrictions; cite | exoplanetarchive.ipac.caltech.edu | 2 M2 |
| ATNF Pulsar Catalogue | 4,503 pulsars | Small | CSIRO property; cite | atnf.csiro.au | 2 M2, 5 |
| Falchi et al. 2016 | Artificial sky brightness worldwide | 2.9 GB GeoTIFF | CC BY-NC 4.0, by request form | doi:10.5880/GFZ.1.4.2016.001 | 1 M5 |
| VIIRS Black Marble VNP46A4 | Yearly night lights since 2012 | About 92 MB per tile per year | CC0 | LAADS DAAC (Earthdata login) | 1 M5 |
| Globe at Night | Citizen sky-brightness readings | Yearly CSV | CC BY 4.0 | globeatnight.org | 1 M5 |
| NOAA SWPC OVATION | Aurora nowcast | 0.9 MB JSON | Public domain | swpc.noaa.gov | 2 M6 |
| Open-Meteo | Cloud cover forecasts | API | Free tier non-commercial; data CC BY 4.0 | open-meteo.com | 2 M6 |
| Espenak eclipse catalogues | 11,898 solar and 12,064 lunar eclipses | Small | Free with credit | eclipse.gsfc.nasa.gov | 2 M2 |
| IAU Meteor Data Center | 172 established showers | 1.6 MB | None stated; cite Jenniskens et al. 2020 | ta3.sk/IAUC22DB/MDC2022 | 2 M2 |
| Wikidata | Cross-identifications: 3.28 M stars, 2.09 M galaxies, 6,274 exoplanets | SPARQL | CC0 | query.wikidata.org | 3 M3, M4 |
| arXiv metadata | Abstracts to cite | API | CC0 | arxiv.org | 3 M4 |
| NASA ADS | Paper search | API | Abstracts are publisher copyright; store no copies | ui.adsabs.harvard.edu | 3, links only |

Some licences carry obligations:

- **Share-alike:** SIMBAD and the CDS HiPS packaging (ODbL), OpenNGC and Wikipedia (CC BY-SA).
- **Blocks a commercial product until licensed (decision 1):** the ESA archive data, the Falchi atlas, DSS2, the Mellinger panorama, the Open-Meteo free tier and ADS abstracts.

## 6. Assets without recorded provenance

Every other file under `apps/web/public` has a record ([ASSET_PROVENANCE.md](architecture/ASSET_PROVENANCE.md)). This one is a pinned gap in `scripts/provenance_gaps.json`, and a test fails if a gap's bytes change or a file arrives without a record. [tested]

- **Textures:** `bruneton_atmo_lut.png` (hand-set single scattering, to be replaced by a real precomputation; queued).

Closed since the last count: `nasa_lro_moon_3d.glb` was rebuilt from the registered LOLA elevation map at true scale on a 1 degree grid ([MOON_MESH.md](science/MOON_MESH.md)); `lb_splats.json` was rebaked from the recorded O'Neill et al. 2024 table, with each cell's thickness projected onto the shell normal and the cross-normal footprint named as grid geometry ([LOCAL_BUBBLE_SPLATS.md](science/LOCAL_BUBBLE_SPLATS.md)); `local_bubble_oneill2024.glb` has a record (O'Neill et al. 2024, CC0); the three Earth textures were replaced and deleted; the WebGPU spike's `snow-street.spz` and `valley.spz` left the tracked tree for the untracked `.cache/splats`, though git history still holds them. [tested]

## 7. Running this in new sessions

- **One session per milestone.** Its brief names this file, the milestone, AGENTS.md and what done looks like. The session works on its own branch and opens a pull request.
- **Gates.** Every pull request passes the six gates. `uv run pytest` needs `data/uploads` for its two discovery tests.
- **Another machine or account.** Clone from GitHub, copy the `data/` folders and the gitignored splats, then run `uv sync` and `pnpm install`. Claude Code's memory lives in `~/.claude` and belongs to one machine and account; this file is the shared record.
- **Keeping this current.** When a milestone lands, update its line here and section 1.
