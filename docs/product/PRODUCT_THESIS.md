# Albedian Product Thesis, Cited Research & Positioning

**Product:** Albedian  
**Tagline:** *Start from your photograph—not an anonymous universe.*  
**One-Sentence Thesis:** Albedian turns a personal night-sky capture from Earth into a scientifically calibrated 3D portal—restoring the stars your camera recorded, locking their identity with Gaia DR3 astrometry, and letting any curious explorer fly from their real earthly horizon onto the worlds behind those points of light.

---

## 1. The Problem We Solve (Backed by Peer-Reviewed Research)

Albedian addresses three interlocking barriers that separate everyday people from experiencing and understanding the universe above them:

### A. Disappearing Access to the Night Sky
- **Global Skyglow Baseline ([Falchi et al., 2016, *Science Advances*](https://pmc.ncbi.nlm.nih.gov/articles/PMC4928945/)):** More than **80% of humanity** (~83% globally, and >99% of U.S. and European populations) lives under light-polluted skies. Over **one-third of humanity** (including nearly 80% of North Americans and 60% of Europeans) can no longer see the Milky Way from home.
- **Accelerating Loss of Naked-Eye Stars ([Kyba et al., 2023, *Science*](https://pubmed.ncbi.nlm.nih.gov/36656932/)):** Across 51,351 citizen-science observations (2011–2022), naked-eye star visibility deteriorated at a rate consistent with **~9.6% annual growth in artificial sky brightness** (doubling roughly every 8 years).

### B. Photon-Limited Night Capture on Consumer Cameras
- **Aperture & Noise Physics ([Hasinoff et al., 2016, *ACM TOG / SIGGRAPH Asia*](http://graphics.stanford.edu/papers/hdrp/hasinoff-hdrplus-sigasia16.pdf); [Liba et al., 2019](https://arxiv.org/html/1910.11336); [EMVA 1288](https://www.emva.org/wp-content/uploads/EMVA1288-3.1a.pdf)):** Small consumer apertures are photon-starved at night, governed by the aperture-integrated SNR budget:
  $$\mathrm{SNR} \approx \frac{S}{\sqrt{S + B + D + n_{\mathrm{pix}} n_{\mathrm{exp}} \sigma_r^2}}$$
  where $S$ is accumulated stellar signal, $B$ is skyglow/Mie glare background, $D$ is dark current, and $\sigma_r^2$ is read-noise variance. Conventional mobile denoisers often mistake faint point sources for noise and erase them, whereas subtracting a background gradient $B$ improves contrast without removing the underlying shot noise $\sqrt{B}$. Albedian therefore separates **detection-preserving temporal restoration of source-supported photons** from **explicitly labeled catalog predictions**.

### C. Spatial Cognition & The "Sterile Database" Chasm
- **Reference-Frame Coordination ([Plummer, 2014, *Studies in Science Education*](https://eric.ed.gov/?id=EJ1029625); [Plummer et al., 2022, *JRST*](https://onlinelibrary.wiley.com/doi/10.1002/tea.21754)):** Recognizing a 2D dot or constellation name on a flat sky dome does not produce comprehension of 3D celestial geometry; learners require explicit visual continuity connecting their local earthly horizon to space-based reference frames.
- **Scale & Light-Travel Misconceptions ([Schneps et al., 2014, *Computers & Education*](https://dl.acm.org/doi/10.5555/2753872.2753884); [Rajpaul et al., 2018, *Phys. Rev. PER*](https://physics.aps.org/articles/v11/93)):** Even university students struggle to order astronomical distances (`1.28 light-seconds` to the Moon vs. `4.37 light-years` to Alpha Centauri vs. `26,673 light-years` to the Galactic Center) or connect photon lookback time to historical epochs.
- **The Experiential Gap in Existing Software:**
  - **2D Mobile Planetariums (*Sky Guide*, *Stellarium Mobile*, *Star Walk 2*, *Night Sky*):** Overlay synthetic catalog graphics onto a live camera feed without restoring or astrometrically solving the user's recorded photograph.
  - **Legacy Scientific Simulators (*NASA Eyes*, *WorldWide Telescope*, *Celestia*):** Contain rich catalog data, but present it inside sterile, government-portal database UIs disconnected from the user's own night-sky photograph, local horizon, and visceral surface presence.
  - **Astrophotography Stackers (*PixInsight*, *Siril*):** Process real photons and plate-solve, but stop at a static 2D image with zero navigable 3D world exploration.

---

## 2. Intended Audience (ICP) & High-Value Wedges

| Priority Segment | Profile | Core Job-to-be-Done | How Albedian Delivers |
|---|---|---|---|
| **1. The Curious Everyday Explorer (Primary ICP)** | Anyone who looks up at the night sky, snaps a photo or clip, and wants to experience what is actually out there—without a telescope or an astrophysics degree. | *"What are those lights in my photo, and what would it look like to actually go there?"* | Restores their capture, locks onto the real stars/planets above their horizon, and flies them into a cinema-grade 3D world with human-first telemetry (`1.1× Earth Gravity`, `-39°C`, historical light-departure eras). |
| **2. Night-Sky Creators & Astrophotographers** | Photographers capturing the Milky Way, Moon, or constellations from dark-sky sites or suburbs. | *"Turn my flat 2D night photo into an awe-inspiring, explainable 3D journey."* | Anchors a continuous 3D camera flight (`01 Sky` $\rightarrow$ `05 Surface`) directly to their SHA-256 verified capture with `Hold Original` proof. |
| **3. Science Communicators & Educators** | Astronomy storytellers, teachers, and planetarium presenters. | *"Show audiences how a tiny dot in tonight's sky connects to real 3D cosmic structure and exoplanet physics."* | Preserves reference-frame continuity from the local horizon into 3D space while strictly labeling `Captured`, `Catalog`, `Scientific Model`, and `Illustration`. |

---

## 3. Scientific Truth Boundaries & Epistemic Honesty

The "proof moment" of Albedian is not an isolated GPU effect—it is the user understanding: **"This 3D world is connected to that particular light in my particular photograph."** Every representation enforces strict epistemic boundaries (`packages/contracts`):

| Layer | Epistemic Class (`EvidenceClass`) | Plain-Language UI Disclosure | Scientific & Technical Basis |
|---|---|---|---|
| **1. Capture & Restoration** | `observed` | `CAPTURED IN YOUR PHOTO · HOLD ORIGINAL` | Immutable SHA-256 `SourceAsset`; 12-frame temporal burst integration (`+5.2 dB SNR`) and Mie glare stripping. |
| **2. Astrometric Sky Lock** | `catalog_matched` | `CAPTURED IN YOUR PHOTO · GAIA DR3 LOCK (0.38″)` | Sub-arcsecond WCS plate solution verified against held-out star residuals; matched to ESA Gaia DR3 & JPL DE440 ephemerides. |
| **3. 3D Cosmic Volumes** | `simulated` (Data-Backed) | `CATALOG 3D GEOMETRY · GAIA DR3 + CfA` / `SCIENTIFIC 3D MODEL` | `@sparkjsdev/spark` 3D Gaussian Splatting of the Harvard CfA Local Bubble (`O'Neill et al., 2024, ApJ`), NASA LRO laser altimetry (`100m`), and DNGR relativistic geodesic ray-marching ([James, von Tunzelmann, Franklin & Thorne, 2015](https://iopscience.iop.org/article/10.1088/0264-9381/32/6/065001); [Bruneton, 2020](https://ebruneton.github.io/black_hole_shader/paper.pdf)). |
| **4. Planetary Surfaces** | `simulated` (Mapped vs. Illustrative) | `MEASURED ALTIMETRY` (Moon) vs. `SCIENTIFIC ILLUSTRATION · MODELED WORLD` (Exoplanets / Titan) | Analytical-derivative multi-fractal GPU ray-marching ([Inigo Quilez](https://iquilezles.org/articles/morenoise/)) and Rayleigh/Mie atmospheric scattering ([Bruneton & Neyret, 2008](https://ebruneton.github.io/precomputed_atmospheric_scattering/)) parameterized by equilibrium temperature, surface gravity, and stellar spectrum. |

---

## 4. The Four + One Orthogonal State Machines

Albedian never models the application as a single flat mode enum. A restoration job may run while the user inspects a calibrated sky.

| Machine | Legal States | Governing Invariant |
|---|---|---|
| **`Media`** | `empty`, `importing`, `ready`, `decode_error` | `ready` guarantees an immutable `SourceAsset` with verified SHA-256, dimensions, duration, and audio stream descriptors. |
| **`Restoration`** | `idle`, `analyzing`, `planning`, `queued`, `processing`, `evaluating`, `ready`, `failed`, `canceled` | Every attempt references an immutable `ProcessingGraph` revision and content address; progress derives strictly from real job events. |
| **`Calibration`** | `not_requested`, `detecting`, `solving`, `verifying`, `calibrated`, `predicted_only`, `ambiguous`, `unresolved` | Only `calibrated` (passing independent held-out inlier verification) authorizes pixel-registered catalog overlays. |
| **`Navigation`** | `source`, `comparison`, `sky_registered`, `transitioning`, `space`, `inspecting`, `returning` | Every transition out of `source` or across spatial viewpoints records a reversible `CameraCheckpoint` in `NavigationHistory`. |
| **`Voice`** | `off`, `permission`, `listening`, `hearing`, `interpreting`, `awaiting_tool`, `speaking`, `interrupted`, `unavailable` | Operates on structured session state through revision-checked `AlbedianAction` dispatch; never fabricates scientific or media state. |
