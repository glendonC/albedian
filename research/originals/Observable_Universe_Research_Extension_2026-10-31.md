# From Night Media to the Observable Universe
## Research Extension to *Frontier AI Media Restoration: Research and Product Foundation*

**Research cutoff:** 31 October 2026  
**Purpose:** Independent scientific, competitive, product and engineering assessment. Not an implementation order.  
**Decision:** **CONDITIONAL GO** for a narrowly scoped *Observation Explorer* prototype; **DEFER** fully unified night-video restoration + live satellite identification + arbitrary 3D flight; **NO-GO** for building a generic photo plate solver or planetarium as the central innovation.  
**Prior research:** `Frontier_Media_Restoration_Research_2026-10-31.md` remains intact. Its Conditional GO and untested low-light-video enhancement performance are unchanged.  
**Evidence labels:** **Confirmed externally** = official source/paper/repository supports a capability; **Engineering inference** = plausible integration unproven here; **Proposed** = design or acceptance criterion; **Not verified** = missing product/API/performance evidence. **No source Sahara media, iPhone recording or Meta glasses file was tested. No experiments described below have been run.**

---

# D1. Executive Verdict

## The most important discovery: existing products already do much of this

The core pattern of importing a photograph, plate-solving it, identifying its stars, and placing it in a sky map is **not new**. [SkySafari 8](https://support.simulationcurriculum.com/hc/en-us/community/posts/37033110168215-SkySafari-8-Is-Now-Available-Read-On) officially advertises image importing and plate solving onto a sky chart. [Sideris](https://apps.apple.com/us/app/sideris-sky-map-plate-solver/id6809145518) supports offline imported-photo plate solving with an interactive sky map. [Plate Solver](https://alokm.com/solve/) already takes phone/RAW images in a browser, overlays named stars, constellations, planets when metadata permits, distances and a 3D globe. [Astrometry.net](https://doi.org/10.1088/0004-6256/139/5/1782) has provided blind calibration for years. It is therefore wrong to claim innovation from photo-to-sky-map alignment itself.

Similarly, [WorldWide Telescope](https://worldwidetelescope.org/Learn/Exploring), [Gaia Sky](https://gaiasky.space/) and other astronomical engines already enable real-catalog 3D exploration. Generic flight through the universe is not new either. The product's **possible differentiation** lies in a continuous evidence-aware interaction from **the user's original lived and recorded moment** into a calibrated scientific view; context-aware questions grounded in that exact observation; and, if tests succeed, restoration-assisted solveability without falsely creating stars.

## Decision matrix

| Question | Verdict | Reason |
|---|---|---|
| Can sufficient consumer night photos be plate-solved? | **Yes, conditional on visible patterns** | Established algorithms and contemporary consumer products. Phone Night mode does not guarantee a solve. |
| Can dark compressed videos be solved? | **Sometimes; unproven on target clips** | Frames and registered temporal composites may yield adequate sources; compression, motion and scarce stars can prevent a solution. |
| Can time/location predict a plausible sky when the photograph does not solve? | **Yes, but no pixel registration** | Ephemerides yield predicted objects, not proof of what the camera recorded. |
| Can measured catalog star positions support a 3D journey? | **Yes, for a cataloged subset** | Gaia positions and distances; WWT reusable engine; proper uncertainty and artistic-transition labels needed. |
| Can astronomy reliably add stars missing from pixels? | **No** | A catalog position is evidence of a model prediction, not recorded photons. |
| Can catalog knowledge materially improve restoration? | **Possible, not established** | It may reject false detections, protect source-supported points, tune alignment. Must be benchmarked against unguided processing without leakage. |
| Can a generic astronomy import/overlay tool stand out? | **No** | SkySafari, Sideris and Plate Solver already do it. |
| Could a grounded experience beat a separate planetarium workflow? | **Potentially; usability test required** | Cohesive return to audio/video, timestamps, evidence labels and targeted refinement could be valuable. |

**The recommendation:** do not replace the Media Experiment Runtime. Introduce a **versioned Observation Solution** beside its media transformations: source-frame detections, WCS solution, ephemeris context, catalog matches and provenance. In the interface, make astronomy a contextual **Explore** affordance that appears when an observation has passed explicit calibration checks. Defer universal live 3D exploration until a short scientific and interaction spike proves it works.

**Three independent gates:** (1) reliable calibration or explicit unresolved fallback; (2) actual additional usefulness over SkySafari/Sideris/Plate Solver, measured with people; (3) reproducible working demo without invented observations. Failure on (1) or (2) removes the astronomy feature from the core hackathon scope.

---

# D2. Technical Research: From Pixels to a Solved Sky

## 2.1 Established astrometry is the right baseline

The foundational [Lang et al., *Astrometry.net: Blind Astrometric Calibration of Arbitrary Astronomical Images*, Astronomical Journal (2010)](https://doi.org/10.1088/0004-6256/139/5/1782) method extracts candidate stars, forms relative geometric patterns (commonly quads), compares them to indexed catalog patterns, creates candidate sky mappings and rejects unsupported hypotheses. It returns a **World Coordinate System** (WCS), which maps image pixel positions to celestial coordinates, with pointing, orientation and scale. The paper's very high success figure concerns its tested astronomical survey images, **not arbitrary phone or smart-glasses videos**. Distortion and low source counts remain relevant failure cases.

An implementable baseline is `source photo → sky-region mask → local background/noise estimate → point-source detection → blind solver → independently checked WCS → catalog associations → overlay`. Source extraction can use [`photutils.DAOStarFinder`](https://docs.astropy.org/projects/photutils/en/stable/api/photutils.detection.DAOStarFinder.html) (PSF-shaped detections) or [`SEP`](https://sep.readthedocs.io/en/stable/) (Source Extractor-inspired extraction). A meaningful WCS verification checks inlier count, held-out catalog stars, residual distributions, spatial coverage across the frame, mirror parity, expected magnitudes and alternate hypotheses. **A solver returning success is not itself absolute proof of a correct match.**

A calibrated image supports pixel-to-direction correspondence, not a depth map. It does not reveal distances to dunes, people or the foreground. A set of stars alone cannot prove whether a faint point was present when a network reconstructs it.

## 2.2 Inputs and failure modes

**Still images.** Ordinary iPhone Night mode, conventional JPEG/HEIC, smartphone RAW and Meta glasses stills are candidates **if multiple reliable stars survive the camera pipeline**. Large landscapes require excluding trees/dunes/buildings, dividing sky into suitable regions and correcting wide-angle distortion. Meta stills must be tested rather than assumed to expose RAW or calibrated optical parameters. Aggressive sharpening, synthetic night-mode processing, cloud cover, point-light artifacts and foreground reflections can produce false detections.

**Compressed video.** Select stable intervals, identify the sky mask, estimate frame-to-frame motion, align to a shared celestial coordinate image plane, produce a motion-compensated average/robust stack, detect sources and attempt a solve. Do not average moving stars into trails; correct camera motion first. Handle camera pan, rolling shutter, autofocus changes and digital stabilization. Prefer a *solved anchor frame* and tracked mappings for neighboring frames over independently solving every frame. If the video consists of featureless darkness or short motion-blurred glimpses, **Unresolved** is a correct result.

**Metadata fallback.** Date, time, approximate location, camera model and IMU/compass can predict the visible sky. This makes a **Predicted Sky** view possible. It does **not** prove which pixels correspond to which objects. Compass headings can be imprecise, EXIF may be missing or stripped, time zones may be misinterpreted, and videos might contain changing camera pose. Require the distinction `Calibrated` versus `Predicted` versus `Unresolved`.

**Optics and environment.** Use pinhole/gnomonic projection only when justified. Wide/fisheye lenses often need polynomial, SIP or other empirically verified distortion models. Refraction near the horizon, haze, atmospheric seeing, local light pollution, clouds, shot noise, compression artifacts, light streaks and sensor saturation affect both detection and mapping. [`Astropy` coordinate transforms](https://docs.astropy.org/en/stable/coordinates/) include proper handling of time, observer location and frames; its [`AltAz` documentation](https://docs.astropy.org/en/stable/api/astropy.coordinates.AltAz.html) explicitly warns that its refraction approximation becomes unreliable below about 5° altitude.

## 2.3 Literature and implementations: fit-for-purpose comparison

| Work | Publication/status | Actual contribution and evidence | Reuse/readiness | Relevance/caveat |
|---|---|---|---|---|
| [Lang, Hogg, Mierle, Blanton, Roweis, *Astrometry.net*](https://doi.org/10.1088/0004-6256/139/5/1782) | *Astronomical Journal*, 2010, peer-reviewed | Geometric hashing, blind camera WCS, Bayesian solution verification | [Code](https://github.com/dstndstn/astrometry.net); actual distribution has GPL dependencies | **P0 baseline**; survey success rates cannot be copied to phones. |
| [`photutils` star finders](https://docs.astropy.org/projects/photutils/en/stable/api/photutils.detection.DAOStarFinder.html) | Mature astronomy software, not novel 2026 AI | Point-like source detection, centroid/shape/brightness extraction | Package; verify version lock; BSD-style Astropy ecosystem | **P0 detection baseline**; local noise estimate and distorted phone PSFs matter. |
| [SEP, Barbary et al.](https://sep.readthedocs.io/en/stable/) | Research software / classical source extraction | Background estimation, detection, measurements | Public C/Python library; LGPL | **P0 alternative**; must test stars vs. noise and codec artifacts. |
| [Megeri, *Towards Learned Star Detection and Identification for Plate-Solving under Urban Light Pollution*](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7518006) | **SSRN preprint posted Sep 24, 2026**; peer review not verified | On 365 London Bortle 7–9 photos from four fields, reports Astrometry.net 195/365 solves and ASTAP 0/365 under reported setup; DNA-Net F1 .671 vs U-Net .629 for learned detection | Code, weights, licensing and independent replication **not verified** | Extremely relevant caution: detection bottleneck, and simplistic preprocessing may *reduce* solves. Dataset small/narrow, not conclusive cross-device performance. |
| [Luo, Ding, Han, Chen, *Robust star identification using topology-invariant features and a SOM neural network*](https://pubmed.ncbi.nlm.nih.gov/41842109/) | *Applied Optics*, Jan 2026, peer-reviewed | Topology-invariant star identification for reconfigurable satellite star sensors, simulated noise/camera variants | Code/weights/license **not verified** | Related algorithm, but **spacecraft star sensors ≠ compressed consumer night scenes**. Research only. |
| [Luo, Guo, Feng, Li, Zhang, *Centroid algorithm for high-dynamic star sensor...*](https://pubmed.ncbi.nlm.nih.gov/40797963/) | *Optics Express*, Apr 2025, peer-reviewed | Deep-keypoint localization under trailing/high-dynamic star sensor conditions | Code/weights/license **not verified** | Useful idea for blur and star trails, but domain shift makes it **non-MVP**. |
| [Gaia DR3 stellar astrometry](https://gea.esac.esa.int/archive/) | ESA mission archive, DR3 released 2022 | Measured positions, proper motion, photometry, parallax for huge catalog, with errors | TAP/ADQL/ESA Archive; data use/attribution reviewed per dataset | **P0 catalog** for robust star identity; many distances uncertain. |
| [Skyfield satellite/planet positions](https://rhodesmill.org/skyfield/earth-satellites.html) | Maintained software | Ephemerides and SGP4 satellite positions, explicit TLE degradation warning | MIT Python package | **P1 planetary context**, **P2 satellite ID** only with suitable historical data. |

**Interpretation:** the strongest integrated system uses **classical astrometry for calibration** and optional learned detection only where tests show fewer missed real sources and no increase in false solves. No recent AI paper here establishes that a frontier language model should predict star identities directly from a dark image. LLMs belong in intent handling and explanations, not unverified pixel/WCS generation.

## 2.4 Astronomical coordinate and uncertainty pipeline

1. Preserve source timestamp and timezone; normalize to UTC, recording assumptions and device clock precision. A wrong minute can materially change satellite predictions; it matters less for fixed-star identities on a wide field.
2. Extract high-SNR candidate points on the **original or low-artifact deterministic branch**; keep centroids and flux/noise values for all detected sources.
3. Solve WCS, applying validated lens distortion/projection. Store WCS header, solver version/index catalog and coordinate epoch.
4. Verify on **held-out detections** not used to create candidate calibration; check spatial distribution and systematic residuals across image corners.
5. Query nearby catalog sources. Proper-motion-correct positions when precision warrants, particularly for long historical intervals; use apparent planetary positions from a planetary ephemeris rather than fixed-star catalog coordinates.
6. Convert between celestial and horizon coordinates using time, observer location and optional refraction model. Distinguish catalog coordinates from apparent observer directions.
7. Output `Matched` only when a real image detection is associated with a catalog source; otherwise label visible/expected sky objects `Predicted`.
8. Preserve full uncertainty: centroid error, lens fit, WCS residual, timestamp/GPS error, matching ambiguity and catalog astrometric uncertainties. Do not multiply unverifiable confidence scores into an apparent scientific probability.

**Proposed acceptance test, not measured performance:** for clean benchmark photos, require at least six spatially distributed independently verified stellar correspondences and a held-out median residual below a specified per-image pixel tolerance (initial design target: ≤3 px in 2K images), *then* separately check orientation, FOV, false matches and distortion. This is a provisional engineering gate, not a universal scientific threshold. A narrow-FOV/large-distortion image may require different tolerances and many more stars. Avoid claiming *0.99 confidence* absent calibrated probabilities.

---

# D3. Capability Matrix: What Each Input Actually Authorizes

Legend: **Yes** = established under adequate quality; **Conditional** = test-dependent or metadata-dependent; **No** = unsupported by that input alone. A catalog prediction and detection are not equivalent.

| Capability | Consumer still with stars | Near-black/poor still | Short compressed video | Time+GPS only | WCS+catalog + metadata |
|---|---|---|---|---|---|
| Detect actual star-like points | Yes/conditional quality | Usually weak or none | Conditional | No | Only if pixels support them |
| Blind solve image direction/FOV | Conditional | Often unresolved | Conditional anchor/stack | No | Yes, if WCS verified |
| Label *observed* stars | Conditional after solve | No without detections | Conditional | No | Yes for source-matched detections |
| Predict planets/Moon positions | Conditional time/place | Yes with time/place | Conditional time/place | Yes | Yes; pixel registration needs WCS |
| Place predicted sky over actual photo pixels | Only with WCS or calibrated pose | No | Only in solved spans | No | Yes with residual/pose uncertainty |
| Estimate constellations seen | If solved | Predicted candidates only | If solved | Predicted sky region only if orientation known | Yes |
| Recover stars absent from the sensor | No authentic guarantee | No | No | No | No; predictions are separate |
| Identify a faint streak as a particular satellite | Rare/conditional | No | Conditional with track, date, location and valid archived orbit | Only candidate overpasses | Conditional; still ambiguous |
| Determine physical star distance | Catalog-backed after identity | No | After identity | No | For stars with usable parallax or distance inference |
| Accurate deep-space 3D fly-through | Not from photo pixels alone | No | Not from pixels alone | No | Conditional on reliable catalog 3D coordinates |
| Understand audio/voices during observation | Not from still | Not from still | Yes if audio stream exists | No | No astronomy data needed |
| Recreate actual original camera experience | Preserve source only | Preserve source only | Preserve original video/audio | No | Registered sky context is additional data |

**Example modes and fallback decisions:**

- A visible Milky Way iPhone Night-mode still with dozens of reliable point sources can be a serious plate-solving candidate.
- A Meta Ray-Ban still with clipped black sky and two bright points is **not** a reliable blind-solve case; show the photograph and a separate predicted sky if time/place are known.
- A sweeping five-second compressed video with motion-blurred points may fail even if the observer saw many stars; motion compensation and multi-frame techniques are hypotheses to test.
- An image with a single bright dot may plausibly be a star, planet, aircraft or sensor artifact; don't label as Venus from appearance alone. Time/location and ephemeris can narrow candidates but not eliminate source ambiguity.

---

# D4. Astronomical Data and Tool Infrastructure

An API's existence does not establish permission to redistribute a complete local catalog or survey imagery. The table distinguishes software licenses from data/site terms; **verify the exact data release and asset terms before bundling, caching or commercially deploying**.

| Provider / link | Observation vs prediction vs render | Useful outputs / historical support | Integration and key caveat | Rights / status |
|---|---|---|---|---|
| [Astrometry.net API](https://astrometry.net/doc/net/api.html) | **Inference from observed image** | WCS calibration, match products; date usually not needed to solve fixed stars | Upload API or local `solve-field`; server may be queued; local index sizes; exposure/privacy concerns | [Repo license](https://github.com/dstndstn/astrometry.net/blob/main/LICENSE): GPL obligations from dependencies despite BSD-licensed parts; [index files](https://data.astrometry.net/) separate terms. |
| [ESA Gaia Archive DR3](https://gea.esac.esa.int/archive/) | **Measured** stellar positions, parallax, proper motion, magnitudes | ADQL/TAP, stellar RA/Dec and uncertainty; coordinate epoch, adjust when needed | Query limited region / magnitude range; do not pull billion-star catalog to browser | Scientific use and attribution; **specific bulk redistribution terms must be checked**. **DR4 is scheduled Dec 2, 2026**, not available at this cutoff. [ESA status](https://www.cosmos.esa.int/web/gaia/data-release-4). |
| [NASA JPL Horizons API](https://ssd-api.jpl.nasa.gov/doc/horizons.html) | **Computed ephemerides** | Sun/Moon/planets/comets observer directions with historical dates and valid kernels | Text-returning API or backend requests; use proper observer location and times; use caching | Official NASA/JPL service; inspect service rules, acknowledgments and imagery separately. |
| [CelesTrak](https://celestrak.org/) | **Orbit element observations/fits → propagated predictions** | Recent GP/OMM satellite state; historical archive requests | [2026 usage policy](https://celestrak.org/usage-policy.php); archive [request form](https://celestrak.org/norad/archives/request.php?FORMAT=json); old orbital elements required | Not an unlimited public historical API; respect distribution/rate terms. |
| [Space-Track](https://www.space-track.org/documentation) | **Historical/current orbital element sets** | `GP_HISTORY`, GP/OMM, satellite identity | Account/service restrictions, download-once and caching guidance; API terms | Need compliant account and redistribution review. |
| [SIMBAD](https://simbad.cds.unistra.fr/simbad/) | **Aggregated observations and bibliographic identities** | Star aliases, object types, reference coordinates, links | CDS queries; use when star identified and additional astrophysical data wanted | Check CDS usage/attribution, table-specific terms before caching. |
| [VizieR](https://vizier.cds.unistra.fr/) | **Published survey catalogs** | TAP/ADQL, cone search, survey-specific photometry/distances | Data quality and coordinate frames differ by catalog; source citations needed | Terms can differ by source catalog. |
| [Aladin Lite v3](https://aladin.cds.unistra.fr/AladinLite/doc/) | **Scientific survey visualization** | HiPS sky images, catalog layers, WCS projection; web embed/JS API | Rust/WASM/WebGL sky atlas, excellent 2D fallback | [LGPL-3.0](https://github.com/cds-astro/aladin-lite/blob/master/LICENSE); surveys have their own rights. |
| [WorldWide Telescope WebGL Engine](https://docs.worldwidetelescope.org/webgl-reference/latest/) | **Visualization of catalog/survey information and computed positions** | Browser sky/3D universe, layered surveys and 3D navigation | Reusable TypeScript renderer; verify desired photo-to-3D camera transition through spike | [MIT engine](https://github.com/WorldWideTelescope/wwt-webgl-engine); embedded survey imagery separate terms. |
| [Stellarium Web Engine](https://github.com/Stellarium/stellarium-web-engine) | **Predicted/visualized sky** | Gaia star rendering, atmosphere, planets, constellations | Browser embed; compelling realistic sky, more copyleft integration considerations | **AGPL-3.0**, not assume permissive. |
| [Astropy](https://docs.astropy.org/en/stable/coordinates/) | **Coordinate math** | Frame transformations, proper motion, Earth location, refraction | Python on Mac/backend, IERS time data availability | [BSD 3-clause](https://docs.astropy.org/en/stable/license.html). |
| [Skyfield](https://rhodesmill.org/skyfield/) | **Celestial/orbital computation** | Ephemerides, satellite SGP4, historical predictions using proper input files | Python, MIT; requires appropriate kernel and epoch-matched GP | [MIT](https://pypi.org/project/skyfield/); orbital data use terms independent. |
| [NASA Exoplanet Archive](https://exoplanetarchive.ipac.caltech.edu/docs/program_interfaces.html) | **Published observations/inferred planet properties** | TAP `ps` and `pscomppars`, orbital/distance parameters, sources | Historical discovery content, not pixel-level stellar calibration; no scientifically accurate planet-surface fly-through from tabular values | Check specific dataset/citation policy; not MVP. |
| [NASA SkyView](https://skyview.gsfc.nasa.gov/current/help/faq.html) | **Reprojected survey observations** | Multiwavelength reference cutouts | Useful **separate survey layer**, not what phone captured | Most images reusable with **exceptions** such as Digitized Sky Survey. |
| [ESASky](https://sky.esa.int/esasky/) | **Observed archive datasets + visual explorer** | Multiwavelength missions/survey browser; URL/bridge integration | Possible deep-field handoff; not a universal 3D engine | Survey/mission-specific terms; check before embedding/rehosting. |
| [NAIF SPICE](https://naif.jpl.nasa.gov/naif/rules.html) | **Ephemeris/orbital geometry** | High-precision kernel-driven transformations, historical mission trajectories | Higher complexity than Skyfield/JPL Horizons for MVP | Commercial use allowed with conditions; toolkit redistribution restrictions. |
| [NASA Eyes](https://science.nasa.gov/eyes/) | **Scientific visualization and simulation** | Historical/future Solar System and missions in 3D; browser links/embeds | Great competing experience or outbound link; not assumed to be a freely reusable engine | No general open-source SDK license verified. |
| [Gaia Sky](https://gaiasky.space/) | **3D catalog visualization** | Gaia stellar datasets, motion, scripted space exploration on desktop/VR | Superb comparison, not a tiny browser SDK. Released v3.9.2 Oct 27, 2026 | Open source; check chosen component license/data use before repackaging. |

**Two late-2026 practical findings:**

- [ESA confirms Gaia DR4 is planned for **December 2, 2026**](https://www.cosmos.esa.int/web/gaia/data-release-4). That is two days before Kiroween's deadline. **Use DR3/fixed catalog snapshots now.** Do not make the release a launch dependency.
- [CelesTrak warns its catalog exceeded five-digit satellite numbers in July 2026](https://celestrak.org/norad/archives/request.php?FORMAT=json). Old fixed-width TLE cannot represent newly assigned IDs above 99,999. **Use OMM/GP modern formats** and inspect provider data formats rather than assuming all objects are available as TLE.

**Short architecture choice:** local or server Astrometry.net for calibration; Astropy + a small Gaia bright-star selection + JPL Horizons for scientific context; WWT for a renderer spike; use Aladin Lite as a robust 2D fallback. Satellite tracking stays separate and off by default until archived ephemeris access is verified.

---

# D5. Competition: Precise Substitute Analysis

| Existing product | Confirmed relevant ability | Difference still to prove | Verdict |
|---|---|---|---|
| [SkySafari 8](https://support.simulationcurriculum.com/hc/en-us/community/posts/37033110168215-SkySafari-8-Is-Now-Available-Read-On) | Imports images; plate-solves and places them in sky chart; established extensive planetarium features | Not verified as offering source-preserving audio/video restoration experiments, versioned evidence graph and integrated 3D transition from the personal media | **Direct substitute for photo registration**, high overlap. |
| [Sideris](https://apps.apple.com/us/app/sideris-sky-map-plate-solver/id6809145518) | Offline photo plate solving, aligned map, object discovery and historical sky map | Need prove video/voice/evidence/3D experience, not just a faster solver | **Direct substitute**, especially mobile privacy. |
| [Plate Solver](https://alokm.com/solve/) | Browser photo/RAW solving and interactive annotations; distances, planets with EXIF and globe | Not verified to restore video/audio and run versioned experiments or 3D measured stellar viewpoint change | **Very close first-use experience**. |
| [Astrometry.net](https://astrometry.net/) | Robust blind calibration and public integration | Not a polished personal memory-to-universe product | **Foundation/substitute for algorithm**, not feature differentiation. |
| [Stellarium](https://stellarium.org/) / [Stellarium Web Engine](https://github.com/Stellarium/stellarium-web-engine) | Accurate sky simulation by location/date; many objects and atmosphere | Not established as media-first provenance-centric experience | **Sky-map substitute**; cannot claim uniqueness for projected sky. |
| [Sky Guide](https://fifthstarlabs.com/) / [SkySafari](https://skysafariastronomy.com/) / Star Walk / Night Sky | Location-aware sky identification, time travel, AR, guides; detailed product variations | In this research, precise *personal-photo import/solve* capabilities for each except SkySafari **not verified**; do not declare absence | **General substitute** for 'what's overhead?' |
| [WorldWide Telescope](https://worldwidetelescope.org/Learn/Exploring) | Web sky imagery and **3D universe** flight | Personal video restoration and source-linked return journey not established here | **Strong 3D substitute**; prefer reuse to rebuild. |
| [Gaia Sky](https://gaiasky.space/) | Scientific multi-scale real-data space exploration, macOS + desktop/VR | Continuity from source video/audio and calibrated restoration not verified | **Strong science/3D substitute**. |
| [NASA Eyes](https://science.nasa.gov/eyes/) | Rich 3D Solar System, mission history and travel | Not a general personal-star-photo WCS solving tool | **Strong Solar System exploration substitute**. |
| [Aladin Lite](https://aladin.cds.unistra.fr/AladinLite/doc/) | Survey/celestial-layer exploration embedded in browser | No intrinsic user video restoration runtime | **Excellent integration candidate**. |
| [Cosmoscope](https://cosmoscope.space/) / [Stellata](https://github.com/alexmensch/stellata) | Web 3D catalog-inspired space travel / catalog star rendering | Personal calibrated photo-to-flight feature not independently confirmed | **Further reason 3D alone is not novel**; verify scientific claims separately. |

**Strongest current user workaround:** use the iPhone's image-processing tools or Topaz for exposure/noise, import the photo into SkySafari/Sideris/Plate Solver for identification, then open WWT or Gaia Sky for 3D exploration. The new product must save a meaningful handoff, reduce misinterpretation or enrich the actual memory enough to justify integration complexity. A generic LLM chat box does not achieve this.

**Differentiation must be demonstrated as a workflow, not a checklist:** accurate source-aligned labels on the photo, one click to inspect a catalog-backed star, an honest view-change animation, and one action to return to the exact source frame and audio. Stronger differentiator if restoration measurably improves reliable WCS solve rates without false-star contamination; otherwise this is an optional experience product, not an imaging research breakthrough.

---
# D6. The Restoration Connection: Where Astronomy Can Actually Help

## Hypothesis to test, not an established imaging advance

The only technically defensible claim today is that calibrated astronomical knowledge might improve **selection, alignment and quality control** of a restoration workflow. There is no verified evidence here that injecting predicted catalog stars into a neural restoration model recovers authentic sensor measurements that were never observed.

**Potential closed loop:**

```text
IMMUTABLE ORIGINAL
       │
       ├── Conventional image/video restoration ──┐
       │                                          │
       └── Source-only star detection ────────────┤
                                                  v
                     Independent WCS solution / calibration
                                                  │
                                 Catalog consistency evaluation
                                                  │
                      Reject artifacts / protect source-backed
                         detections / adjust next restoration
                                                  │
                                     Compare against baselines
                                                  │
                                    No synthetic-star export
```

Three useful mechanisms merit experimentation:

1. **Preserve point-source evidence.** Denoising may destroy weak stars; sharpening may manufacture points. A verified star signal from the **original source** can identify regions where the restoration should be conservative. This is primarily a fidelity constraint and may reduce artistic smoothness.
2. **Use astronomy to evaluate candidate results.** Compare the location of output point detections with a WCS independently established from high-confidence source stars. A model that introduces many catalog-inconsistent points can be penalized. But *catalog-consistent* synthetic points are also possible; consistency does **not** prove authenticity. Measure source support and held-out image residuals separately.
3. **Improve geometric alignment of the sky.** When true stars are tracked across frames, their geometry can help stabilize or register a camera motion sequence. This can benefit temporal denoising, especially for a static sky, but depends on enough tracked points and careful treatment of foreground parallax, camera rotation and rolling shutter. A global star homography may misalign dunes and people.

**Important anti-circularity design:** freeze a source-only validation set before any catalog-guided restoration. Solve WCS with originals or separately confirmed reliable points; withhold a subset of detections from optimization. Measure whether enhancements retain genuine signals, not merely whether their output resembles the catalog used to guide them. If the source never yields a statistically detectable star point and the model draws one at a catalog coordinate, label it **Predicted/Synthetic**, not recovered.

## What not to do

- Do not paste Gaia stars over a dark photograph and call it recovered.
- Do not select a WCS hypothesis, draw stars from that WCS, then use those drawn stars to validate the same WCS.
- Do not use an LLM vision explanation or generative model score as proof of physical object identity.
- Do not interpret 'new points after denoising' as added information without original-source evidence.
- Do not compare PSNR when no real clean reference is available. Do not claim star precision/recall against a catalog as physical truth when the sensor might never have observed predicted stars.
- Do not prioritize this research loop ahead of ordinary temporal denoising unless E6 produces meaningful controlled results.

## A defensible benchmark design

Collect (a) actual handheld consumer photographs, (b) short real consumer-night videos, (c) controlled paired exposures of the same sky with independent longer-exposure or RAW reference, and (d) deliberate negatives (clouds, blank walls, streetlights, compressed dark noise). Retain location/time/optics where known. Split by scene, device and night so identical star patterns do not trivially leak between train/test. Do not feed the reference into restoration.

For each treatment: `Original`, `Tone-only`, `Spatial denoise`, `Motion-aligned temporal stack`, `Neural restoration`, and (experimental) `Astronomy-aware constraints`:

| Metric | Valid evidence and interpretation |
|---|---|
| Plate-solve rate | Fraction of source cases passing **independent** WCS verification. Count false solves, not only successes. |
| Source-point precision/recall | Relative to controlled reference annotated star detections, where reference imaging is available. Otherwise use cautious proxy/quality assessment. |
| False-point artifacts | Detector reports output points unsupported by original or paired reference; sample blinded manual reviews. |
| Angular calibration error | WCS-to-reference-catalog angular residual, with held-out matched stars; report median and 95th percentile. |
| Centroid motion stability | Temporal drift after compensating for known camera motion and sky rotation. |
| SNR at real star positions | Measured source-background differences and noise, with reference where possible; distinguish amplification from independent signal gain. |
| Image usefulness | Blind paired human preference and recognition of *real* landmarks, not just brightness. |
| Runtime/compute | End-to-end time, median and p95, memory, provider cost and failure rate on target Mac/server. |

**Proposed decision threshold (not sourced as a literature standard):** require the experimental astronomy-aware approach to improve independently verified solve rate on a held-out representative dataset by at least **10 percentage points**, with **no increase in verified false solves** and no significant loss of source-supported star detections. If the baseline already solves almost all photos, test harder video cases or deprioritize the capability; a saturated benchmark cannot demonstrate value. Threshold should be revised after inspecting actual base rates and confidence intervals. With small samples, report uncertainty and don't declare success from a handful of cherry-picked clips.

**Research conclusion:** astronomy is immediately valuable as a **scientific interpretation and verification layer**, not yet as a proven new restoration algorithm. The latter is a differentiated research experiment, not a core MVP promise.

---

# D7. Scientific Integrity: Four Classes of Truth

Every selectable object, animation and export must carry explicit epistemic state:

| State | Definition | Allowed visual behavior | Forbidden representation |
|---|---|---|---|
| **Observed** | A source-image measurement exists: original-pixel point, streak or resolved feature with time/frame coordinates and measurement uncertainty | Neutral detection marker anchored to source pixels; inspect crop and raw values | Naming it as a specific star solely from detection shape |
| **Catalog Matched** | Observed feature is associated with an external object after verified WCS/geometry and ambiguity checks | Named marker and catalog record with match details and angular residual | Presenting the catalog identity as beyond error or implying measured distance from the phone |
| **Predicted** | Object's position/visibility is calculated from ephemeris, time, location, catalog and assumed camera pose; may be absent in recording | Distinct outlines/dashed projections; only overlay photo pixels if pose/calibration supports it | Drawing predicted stars as though they were camera-captured points |
| **Simulated** | Rendered view, surveyed-image layer, scaled model, artistic flight or extrapolation beyond the actual source | A clearly marked `Explore` environment with provenance and geometry limits | Calling a stylized star surface/galaxy interior an actual photograph |

**Separate additional states** such as `Unresolved`, `Ambiguous`, `Insufficient Metadata`, `Time Uncertain` and `Orbit Unavailable` should not be merged into a generic percentage 'confidence'. Show a specific explanation: e.g., `4 possible detections · minimum independent pattern not verified`; `Historical orbit unavailable`; `Sky predicted from approximate location`; `Distance estimate uses a prior`.

### The scientific geometry of 'Take me beyond that star'

When a fixed star is catalog-matched and has a usable 3D location, the renderer can select a **catalog epoch** and place a virtual object at measured/inferred celestial coordinates and estimated distance. Gaia gives parallax and astrometry but many parallax distances are noisy. For a statistically informed distance, consider the [Bailer-Jones distance catalog guidance](https://bailer-jones.www3.mpia.de/gedr3_distances/FAQ.html) rather than naively using `distance = 1/parallax` for all stars. Include an uncertainty interval where available. High-confidence nearby star geometry is much better suited to a 'change the constellation viewpoint' demonstration than distant or poorly constrained sources.

**There is no complete 3D reconstruction of the star's actual visible surroundings.** Point rendering, radius/color estimates, surrounding cataloged objects and survey imagery are legitimate with labels. A star's detailed surface, planets and nebulae around it are generally **not** derivable from a phone image; avoid synthetic close-ups unless marked illustrative. Galaxy-scale smooth lighting, dust clouds and the speed of a fly-through are visualization choices, not directly measured geometry. Because distant light takes time to arrive, a naïve scene putting all stars at their present extrapolated positions is not literally a simultaneous observation of what a distant traveler sees. For MVP, render a catalog-epoch Euclidean snapshot and label viewpoint changes as approximate geometric reconstructions, not a fully relativistic light-cone simulation.

### Historical time and satellites

**Planet, Moon and Sun:** use an observer ephemeris with explicit date/time/location and appropriate kernels (e.g., JPL Horizons), accounting for apparent sky coordinates rather than naive heliocentric positions. Predicted visibility additionally depends on horizon, twilight, weather, brightness and occlusion. A planet crossing the expected camera field does not prove it was recorded.

**Satellite:** [Skyfield warns that TLE/GP orbit predictions quickly degrade away from the element epoch](https://rhodesmill.org/skyfield/earth-satellites.html), often over days to weeks; a 2026 orbit element is not an accurate historical replacement for a 2025 observation. CelesTrak offers [historical archived GP data requests](https://celestrak.org/norad/archives/request.php?FORMAT=json), and Space-Track documents [GP_HISTORY](https://www.space-track.org/documentation), but access, freshness, coverage, orbital maneuvers, time synchronization and brightness/illumination geometry must be checked. Need projected path, timing and independent streak evidence; even then rank candidates and label ambiguity. A single bright streak could be a satellite, aircraft, meteor or internal artifact. Never claim named satellite identity solely because it passed overhead.

**Meteors and aircraft:** a brief trail may have suggestive shape and duration, but confident attribution often requires multi-station detection, timing/trajectory information, color/motion and independent event records. Do not promise identification of a specific meteor from one consumer frame. Airplanes can flash or leave trails; satellite brightness changes are complex; clouds and codec artifacts complicate detection.

**Constellation from alternate viewpoint:** available mathematically for reliable 3D positions. It is a *change in apparent angular pattern*, not travel into a physically connected 'constellation object'. Select a nearby constellation group with good distance estimates and show star depth; for distant clusters or missing distances, display uncertainty or disable 3D distortion.

---

# D8. Experience Design: Original Moment to Grounded Exploration

## Interface principle

The product is first a **memory artifact and a useful result**, not a telescope simulator. Media occupies most of the window. There is no permanent node DAG, catalog table or chat sidebar. Underlying processing remains inspectable via a drawer.

**Recommended primary states** are not four permanent navigation tabs. Make **`Original` ↔ `Enhanced`** a comparison toggle; show **`Sky`** only when there is a solved or clearly predicted sky; show **`Explore`** only when a real catalog-backed destination can be represented. Underlying states: `Import`, `Analyzing`, `Unresolved`, `Calibrated`, `Exploring`, `Exported`. The user can always return to the original recording and timestamp.

```
┌─────────────────────────────────────────────────────────────────┐
│  NIGHT OBSERVATION           Sahara · Oct 2026          Export  │
│─────────────────────────────────────────────────────────────────│
│                                                                 │
│                                                                 │
│             [ ORIGINAL PHOTO / VIDEO DOMINATES ]                │
│                                                                 │
│                     • Vega         • Deneb                      │
│                 subtle anchored catalog markers                │
│                                                                 │
│                        ▶ 00:08 / 00:19                         │
│                                                                 │
│─────────────────────────────────────────────────────────────────│
│ Original  Enhanced   ·  Sky Calibrated   ·  18 Matches           │
│ ━━━━━━━━━━━━━━━━━━━━━●━━━━━━━━━━━━━━━━━━━━━━━━━━━━   Timeline  │
│                          Ask anything…                         │
└─────────────────────────────────────────────────────────────────┘

[on selecting a matched source]
┌─────────────────────────────────────────────────────────────────┐
│  < Back to Moment                              Scientific View  │
│                        [ MATCHED STAR ]                        │
│               Distance · Catalog · Uncertainty                 │
│                       Explore  →                               │
└─────────────────────────────────────────────────────────────────┘
```

This is an **information hierarchy**, not a final visual comp. Use generous space, readable typography, quiet point-source markers, no neon science-fiction decoration. The precision should be visible in a carefully anchored overlay and a graceful camera transition.

## Concrete first-use walkthrough

**Scene A: import a Sahara sky photograph.** The original image appears immediately. Original recording and audio are preserved with immutable checksum and metadata. An understated `Analyzing` activity region says which measurable work is occurring: source point detection, solving, brightness correction. The experience never hides the source behind a spinner. If enough stars exist, `Calibrated` appears with count of independently matched sources and a small `Details` affordance. A `Sky` toggle reveals labels positioned via the WCS. If solve fails, use `Unresolved` plus a reason and offer `Predicted Sky` separately if timestamp/location suffice. No fake star overlay on an unsolved photo.

**Scene B: restore but don't invent stars.** On voice or text request `Recover genuine details; don't create stars`, create a constrained transformation plan using conventional enhancement and (if tested) neural restoration. Track precise graph/input changes, compare against the untouched recording and original-aligned point sources; label methods. The original sky solution remains an independent measurement; rerun restoration only on affected job branches. New bright points that cannot be supported by original pixels get flagged instead of silently named.

**Scene C: explore a real star in the actual photograph.** Tap a source marker known to be `Catalog Matched`. Show `Star Name`, `Distance Estimate`, `Source: Gaia DR3`, `Match Error`, `Data Quality`. The catalog ID and WCS fit remain available from the technical inspector. Ask `How far away is that?`: answer with distance, an uncertainty/limitations note and a visual scale if reliable. Choose `Explore`.

**Scene D: defining transition, no fake spatial continuity.** Pause the source at the selected frame. The camera stays locked on that exact star pixel while a subtle coordinate reticle expands. The source image gently dims or recedes as the renderer aligns a *celestial sphere* to the solved image's WCS; catalog matched stars retain angular relationships to the original. Only after an explicit change to `Scientific View` does the system move into a **3D catalog-backed scene**. A minimal disclosure `Catalog Geometry · Approximate Distances` appears. The first transition is angular (can be calibrated); the subsequent depth journey is an intentional illustration using Gaia distances, nonlinear travel speed and incomplete catalog coverage. The image foreground is not turned into fake dune geometry. Back returns to the precise paused source image/time and original audio.

**Scene E: viewpoint change.** For a supported local-star grouping choose `Different View`. Render 3D stars from catalog epoch with distance uncertainty. Label that constellation line drawings are Earth-view visual conventions. If too many target star distances are missing, disable the claim of scientifically accurate 3D deformation and offer a simple 2D sky map.

**Scene F: video.** Keep original audio/timestamps. When playback advances, only display overlays inside frames with a validated transformed WCS. If lost tracking occurs, hide markers and show `Tracking Lost`; do not pin names to the wrong pixels. During navigation away from the video, either pause sound or show an explicit `Original Audio` toggle; do not imply sounds are from deep space.

## State transitions and motion specifications

| Transition | Visual change | Underlying truth and performance requirement |
|---|---|---|
| `Imported → Analyzing` | Original remains center; subtle localized progress | Real detector/solver job events only; no invented agent cursor |
| `Analyzing → Calibrated` | Sparse anchored markers fade in over ~200 ms | WCS verified independently, object IDs matched |
| `Analyzing → Unresolved` | Markers absent; actionable reason | Fail-safe, no simulated match |
| `Original ↔ Enhanced` | Synchronized wipe or toggle, same crop/frame | Actual two processed assets; original immutable |
| `Photo → Sky` | Smooth camera orientation match, sky catalog behind | WCS-to-sky projection; use zoom/FOV interpolation, do not infer foreground depth |
| `Sky → Explore` | Deliberate transition label then 3D flight | Switches from image evidence to catalog-backed simulation |
| `Explore → Original` | Return to exact pixel/frame/time | Persist source ID, timeline timestamp and selected star |

**Timing numbers are proposed motion design targets, not tested implementation values.** For example, a 200–300 ms label reveal, 500–900 ms photo-to-sky dissolve, and short >1 s 3D camera move. Allow reduced-motion mode that uses fades instead of cinematic motion. Test actual GPU frame rate and accessibility, not the impression of speed.

## Voice should be structured state manipulation

| Utterance | Structured action | Evidence guard |
|---|---|---|
| “What am I looking at?” | `describe_observation({asset_id, timecode})` | Must state calibrated vs predicted vs unresolved |
| “Which of these are planets?” | `list_matches({types:['planet'], view:'source'})` | Need ephemeris time/place and WCS/pose for pixel overlay |
| “How far away is this star?” | `show_object({source_id, attributes:['distance','error']})` | Only matched source + a distance estimate with source |
| “Show the sky that night.” | `open_sky({mode:'predicted', observed_at, location})` | Explicit prediction label if photo unsolved |
| “Take me beyond that star.” | `enter_3d({source_id, catalog_epoch, distance_policy})` | Must be catalog matched + 3D coordinates acceptable |
| “Did a satellite pass?” | `search_orbit_candidates({timespan, observer, field_of_view})` | Returns possible passes; named ID only after orbital+pixel checks |
| “Don't invent more stars.” | `set_restoration_constraints({allow_synthetic_stars:false,source_support_required:true})` | Does not imply proof of every output pixel |
| “Show what it looks like from another side.” | `change_viewpoint({object_group, offset_pc, uncertainty_display:true})` | Reject/qualify if missing distances; do not fabricate scenes |
| “Go back to when we were talking.” | `restore_media_state({asset_id,timecode,audio_mode:'original'})` | Exact recorded timeline, not inferred celestial dialogue |

The LLM may translate user intent and explain domain terms. A **typed command validator** handles prerequisites, and scientific libraries compute actual coordinates. Never let an LLM invent a star record or directly set WCS without an external solver. A “helpful” assistant must also be able to say, `That point is unresolved. I can show what the sky was predicted to contain.`

## UI copy system

Use `Original`, `Enhanced`, `Sky`, `Explore`, `Details`, `Export`, `Unresolved`, `Predicted Sky`, `Catalog Matched`, `Source Evidence`, `Match Error`, `Viewpoint`, `Back to Moment`. Typical metadata: `18 Matches · Median Error 1.4 px` (only if real data), `Gaia DR3 · Distance Estimated`, `Time Approximate · Sky Predicted`, `Original Audio · Preserved`. Avoid generic “AI is working hard” prose, neon graph theater, fake certainty percentages and permanently exposed advanced controls.

**Visual references:** photo-to-sky registration in SkySafari 8 and Sideris; high-quality astronomical survey transitions in [WorldWide Telescope](https://docs.worldwidetelescope.org/webgl-reference/latest/); layered survey data in [Aladin Lite](https://aladin.cds.unistra.fr/AladinLite/doc/); scientific multi-scale 3D in [Gaia Sky](https://gaiasky.space/). Borrow behavior without copying screenshots/assets or assuming licensed visual code can be redistributed.

---

# D9. Architecture: Extend, Do Not Replace, the Media Experiment Runtime

## The new primitive

The original research defines a **Media Experiment Runtime**: immutable input; graph of inspectable transformations; versioned attempts; evaluators; result artifacts; provenance. Keep that unchanged. Add a separate, first-class **Observation Solution** (a verified, versioned mapping between a particular source observation and scientific coordinate/data models).

```
                        IMMUTABLE MEDIA ASSET
                                │
              ┌─────────────────┴────────────────┐
              │                                  │
       Media Experiment                     Observation Job
       Runtime                              │
       │                                    ├─ Source Detection
       ├─ Tone / denoise                    ├─ Calibration / WCS
       ├─ Temporal alignment                ├─ Independent Verification
       ├─ Quality evaluation                ├─ Catalog Association
       └─ Processed results                 └─ Predicted Ephemerides
              │                                  │
              └──────────────────┬───────────────┘
                                 │
                    Evidence-Linked View Model
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                  │
         Media Viewer       Sky Overlay       3D Renderer
             ↕                  ↕                  ↕
                      Shared Source/Time/Selection
                                 │
                      Export + Provenance Manifest
```

**No separate astronomy multi-agent supervisor.** A single constrained intent planner may call typed deterministic workers: `analyze_media`, `detect_sources`, `plate_solve`, `verify_wcs`, `match_catalog`, `calculate_ephemeris`, `render_spatial_view`, `evaluate_restoration`. Queue/rerun only jobs invalidated by changes to actual inputs. Sources, catalogs and ephemeris versions must be pinned; otherwise a previously generated scene may change as upstream providers update their data.

## Proposed explicit data contracts (illustrative, not implementation code)

```ts
type EvidenceClass = 'observed' | 'catalog_matched' | 'predicted' | 'simulated';
type SolutionStatus = 'pending' | 'calibrated' | 'predicted_only' | 'unresolved' | 'ambiguous';

type ObservationSolution = {
  id: string;
  originalAssetId: string;
  frameTimeSec?: number;
  sourceSha256: string;
  status: SolutionStatus;
  timestampUTC?: string; timestampUncertaintyMs?: number;
  location?: { lat: number; lon: number; errorMeters?: number };
  camera?: { model?: string; lensModel?: string; distortionModel?: string };
  solver: { name: string; version: string; catalogIndexVersion?: string };
  wcs?: { headerRef: string; projection: string; verificationRef: string };
  quality: { heldOutInliers?: number; medianResidualPx?: number; p95ResidualPx?: number };
  detections: string[]; matches: string[];
  ephemerisRefs: string[];
  warnings: string[];
};

type CelestialFinding = {
  id: string; evidenceClass: EvidenceClass;
  sourceDetection?: { assetId: string; frameTimeSec?: number; xPx: number; yPx: number; snr?: number };
  catalog?: { provider: string; release: string; sourceId: string; coordinateEpoch: string };
  skyPosition?: { raDeg: number; decDeg: number; uncertaintyArcsec?: number };
  inferredDistancePc?: { median: number; lower?: number; upper?: number; method: string };
  predicted?: { observerRef: string; ephemerisRef: string; time: string };
  verification: { method: string; source: string; limitations: string[] };
};
```

Notably, `evidenceClass` labels **the finding's grounding**, not whether every rendered pixel is genuine. A `Catalog Matched` source may have an uncertain distance, and a `Simulated` flight can contain measured catalog coordinate data.

## Invariants and error handling

1. The source asset/audio is immutable. All image processing produces named derivative artifacts.
2. Every science result records source/frame/time, WCS and/or prediction assumptions, scientific data version, algorithm and audit record.
3. A direct sky-object overlay on source pixels requires a verified WCS or separately characterized calibrated pose; EXIF GPS alone is insufficient.
4. `Unresolved` and `Ambiguous` are first-class normal states; don't hide them behind synthetic labels.
5. Catalog predictions are stored separately from detected image features and cannot silently enter the source-restoration branch.
6. A renderer can be changed without changing measured scientific findings; visual style/flight path are not part of object identity evidence.
7. Every astronomy edit has an explicit invalidation footprint. For example, modifying a local tone curve need not invalidate the source-only WCS, but replacing a source frame/time or applying a crop may require remapping coordinates.
8. Users can return to their media exactly, with timecode, volume/audio playback state, frame crop and selected object preserved.
9. Missing network/API or missing metadata produces graceful `Sky Unavailable`/`Unresolved`, not fabricated coordinates.
10. Logging avoids precise location and friends' images/audio unless required, disclosed and consented to; preferred astrometry submission uses local solving or nonpublic mode with residual metadata leakage explained.

## Smallest viable technical stack

| Layer | Recommendation | Why / known risks |
|---|---|---|
| App/media | React + TypeScript + native HTML video + canvas overlays | Works in judges' browsers; preserve existing app architecture. |
| Media processing | Existing FFmpeg/OpenCV worker and media graph | Avoid rebuilding runtime; normalize media timestamps/audio. |
| Source extraction | Python `photutils` or SEP | Established measurement baseline; fast controlled experiment. |
| Plate solving | Astrometry.net local CLI *or* its remote API for initial tests | Reliable baseline; **watch GPL obligations, index size, remote-upload privacy**. |
| Coordinates | Astropy and Gaia DR3 bright-star subset | Explicit frame transformations, epoch and uncertainties. |
| Planet/Moon | JPL Horizons server requests (or Skyfield with official kernels) | Reliable computed ephemerides; cache safely and disclose source. |
| Survey view | Aladin Lite as fallthrough | Proven browser embed + WCS support; LGPL compliance required. |
| 3D renderer | WWT WebGL Engine **first spike**; fallback Three.js with strictly limited catalog subset | WWT already has sky/3D modes and MIT engine; exact camera/photo bridge **unverified**. |
| AI actions | One intent-to-typed-action adapter + deterministic validator | Voice later using browser speech or suitable supported API; avoid core dependency. |
| Storage | SQLite/Postgres metadata + object store; versioned manifests | Source-to-finding-to-output traceability, reproducibility. |
| Jobs/telemetry | Existing runtime queue + state events | Show real solves/processing states, cancel and retry. |

**Licensing blocker:** local Astrometry.net binaries must be packaged in accordance with the actual GPL-linked distribution, not assumed BSD solely because parts of its code are BSD. An open-source hackathon repo does not automatically solve every combined-distribution issue; choose a compatible license/deployment arrangement and record dependencies. Aladin Lite is LGPL-3.0; Stellarium Web Engine is AGPL-3.0; WWT WebGL Engine is MIT. Catalog licenses and survey imagery rights require separate review. The open-source GitHub entry for Kiroween needs a top-level license as well.

**Approximate resource strategy:** begin with sub-10 MP JPEG or short 1080p clips and a small stellar catalog cone query rather than a billion-star local database. One local WCS solving worker plus basic video frame extraction is practical to prototype on an Apple-silicon Mac; CPU/RAM/runtime estimates are **not verified** here. Avoid GPU cloud dependency for the first scientific demo. Do not promise phone-local near-realtime astrometry from repo synthetic benchmark timings (e.g. [Skyshot](https://skyshot.app/technology) timing starts **after** star extraction, uses a warm process, and includes synthetic phone-like test frames). The pipeline must measure end-to-end acquisition, detection, solving, catalog matching and rendering on target hardware.

---
# D10. Eight Falsifiable Technical Experiments

**All experiments below are proposed. None has been run.** Effort estimates are approximate developer time on a prepared Mac, **not** verified timings. Costs exclude model subscriptions, potential GPU rentals, provider fees, and production hardening; first experiments can normally use local open tools and public sample images with correct rights. A successful isolated proof of concept still needs repeated tests on real held-out footage.

## E1. Consumer Photo Plate Solving — P0 / 0.5–2 developer-days

**Hypothesis:** an ordinary non-telescope consumer nighttime photograph with several detected stars can be plate-solved and assigned verifiable WCS accurately enough for anchored overlays.

**Required assets:** at least 15 independent original iPhone Night mode or standard-camera night photos, preferably different skies, horizons and lens FOV; metadata, if present; a handful of independently solved reference images. Do not use a single beautiful astrophoto to infer general feasibility.

**Dependencies:** local `photutils`/SEP star detector, Astrometry.net remote/local solving, WCS validation script, manually reviewed reference source associations.

**Exact procedure:** (1) preserve bytes and metadata; (2) create sky-only mask/crop; (3) detect candidate stars on source; (4) solve with no location hint, then optional metadata hints in a separate track; (5) count matches and held-out errors; (6) examine every false positive and unresolved case; (7) re-run with distortion correction if needed. Compare results on untouched image, minimal contrast adjustment and simple detector threshold variants.

**Baseline:** raw input with default Astrometry.net or an existing consumer photo-solving app as a controlled reference. **Provisional success:** ≥80% independently validated WCS on a preselected **star-rich** test subset and no verified false solves, with acceptable held-out pixel residuals. This is a proposed demo engineering goal, **not a scientifically established expected rate**. **Failure:** low solve rate on star-rich inputs, frequent false sky locations, unusable wide-lens distortion. **Decision:** fail => don't make import-and-identify the core; restrict input conditions or postpone.

## E2. Low-Light Compressed Video Calibration — P0 / 1–3 developer-days

**Hypothesis:** motion-aware temporal integration from consumer video can increase useful point-source evidence and solve more sky intervals than individual frames.

**Assets:** 8–12 representative 5–15 s clips: a truly dark Meta glasses clip if available, iPhone handheld night video, static tripod, pans, people under sky, and a known-negative sky. Include camera/time info when available, but do not rely on it for blind verification.

**Procedure:** (1) sample source frames at fixed times; (2) detect independently; (3) use nonstar image features/optical flow and candidate stars to estimate sky plane motion; (4) align frames only when credible; (5) median/robust-stack static sky regions with motion masks; (6) solve per-frame and from composite; (7) validate composites against held-out raw-frame detections and metadata where possible; (8) track WCS forward while camera pose is stable.

**Baseline:** highest-SNR single frame, tone-adjusted frame, off-the-shelf plate solver. **Provisional success:** stacking solves at least 2 otherwise-unsolved clips **without introducing any false verified solves** and produces stable overlays through at least a short solved interval. **Failure:** artifacts masquerade as stars, registration drifts, time savings are swamped by processing time, or all representative clips remain unsolved. **Decision:** restrict MVP to stills if video fails; retain video restoration as original project without astronomy overlays.

## E3. Catalog Alignment and False-Match Rejection — P0 / 1–2 developer-days

**Hypothesis:** verified WCS and catalog information can map a subset of source detections to Gaia stars with auditable errors, including difficult edge regions.

**Assets:** verified WCS and star detections from E1 plus preselected catalog cone queries and manually checked references.

**Procedure:** transform image detections to celestial coordinates; retrieve catalog nearby with magnitude filter; apply source matching with uncertainty and plausible color/brightness ordering; hold out detections; measure residuals and spatial bias across frame; inject synthetic false bright points into **test copies only** and check rejection; rerun with multiple confusing candidate regions.

**Baseline:** nearest-neighbor catalog assignment without WCS verification or false-positive rejection. **Provisional success:** high precision for confirmed matched points, no false positive *catalog label* on controlled injected artifacts, residual reporting is stable under cropping/rotation. Use detailed count/uncertainty rather than a universal threshold. **Failure:** labels jump, corners misregister, multiple equally plausible solutions are silently collapsed. **Decision:** no direct pixel overlay until reliable; use an unregistered sky map with explicit prediction labels.

## E4. Historical Sky Reconstruction — P0/P1 / 0.5–1.5 developer-days

**Hypothesis:** known observation time and approximate place suffice to compute a credible historical sky independently of image solving.

**Assets:** at least three observations with known UTC time/location/pointing; one southern-hemisphere test case for New Zealand conditions; ephemeris and stellar catalog subset; validated comparison using Stellarium or independent planetarium.

**Procedure:** choose one clock/coordinate convention; query planet/Moon positions via Horizons or Skyfield; propagate bright-star positions as appropriate; transform RA/Dec to local Alt/Az using Astropy; independently compare object direction and Moon phase at documented times; vary timestamp by ±5 minutes and location by ±10 km to visualize sensitivity; keep expected objects separate from actual image detections.

**Baseline:** Stellarium or another established planetarium on same observer/time. **Success:** consistency within a predeclared angular error budget appropriate to object, reference model and test setup; predicted/observed distinction remains clear. **Failure:** systematic time-zone sign errors, horizon inversion, missing ephemeris dates, highly misleading image-space overlays without WCS. **Decision:** make predicted-only sky fallback available even when E1/E2 fail, but not as a fraudulent calibrated result.

## E5. Source Photo → Spatial Universe Navigation — P0/P1 / 2–4 developer-days

**Hypothesis:** a catalog-matched star in a real photo can anchor a convincing, understandable transition to a scientific 3D viewpoint while preserving exact media return state.

**Assets:** one verified WCS photo; small Gaia star set with measured/inferred distances and error intervals; WWT WebGL Engine or tightly bounded custom Three.js point cloud; UX state machine.

**Procedure:** map selected star source pixel via WCS to celestial coordinates; align the 2D celestial sphere's camera projection to the photo; crossfade **only where sky is supported**; switch to 3D scene with coordinate epoch and distance assumptions; fly toward the selected star using intentional nonlinear scale; show catalog identity and confidence/uncertainty; return to exact image/frame; compare WWT API feasibility with a custom small star-field fallback.

**Baseline:** open same object manually in WWT or Gaia Sky and compare cognitive effort and visual orientation. **Provisional success:** user understands which part is actual photo, sky prediction and 3D simulation; selected star remains visually anchored during initial transition; return is frame-perfect; runs smoothly in the target judge browser. **Failure:** renderer cannot reproduce correct initial angular geometry, moving to 3D breaks selected-object identity, UI implies reconstructed dune depth, browser performance unstable. **Decision:** fall back to 2D Aladin Lite + outbound WWT deep link; do not sink the hackathon into custom universe graphics.

## E6. Restoration-Assisted Plate Solving — P0 research gate / 2–4 developer-days

**Hypothesis:** source-constrained low-light enhancement improves *independently verified* solveability without fabricating or suppressing star observations.

**Assets:** at least 20 diverse difficult consumer stills and short video clips, plus held-out reference sky solutions or controlled long/short exposure paired data and negatives. Lock the treatment groups before evaluating outcomes.

**Procedure:** run `original`, `tone-only`, `classical spatial denoise`, `temporal alignment/stack`, `vetted neural model` and optionally `astronomy-aware point preservation`. Extract detections and solve all variants; keep catalog-hint evaluation separate from any method using the catalog; verify holds/false solves on source and controlled references; count newly produced points without evidence; compare compute cost and subjective appearance.

**Baseline:** tone-only + standard solver and temporal-stack + standard solver. **Provisional strong success:** held-out validated solve rate gains ≥10 percentage points over strongest fair baseline, no increase in independently confirmed false solutions, acceptable source-supported point preservation and compute costs. Report binomial confidence intervals and per-condition outcomes, not a leaderboard without denominators. **Failure:** only makes images brighter, produces catalog-looking artificial points, improves accuracy only on a handpicked small subset, or is slower without useful extra solves. **Decision:** success can elevate astronomy-aware imaging to the core USP; failure keeps astronomy as interpretation, not restoration science.

## E7. Historical Satellite Identification — P2 / 1–3 developer-days for proof, unknown for reliable production

**Hypothesis:** archived epoch-matched GP/OMM records, accurate media time/location and calibrated camera field can narrow down a recorded moving streak to credible satellite candidates.

**Assets:** an intentionally recorded ISS or well-documented satellite pass with accurate clock and observer location, visible multiple-frame track, known WCS, historical orbit elements nearest observation epoch. Obtain access under provider policies **before** promising this feature.

**Procedure:** source-only moving streak detection and track timestamps; query **historical** archive GP/OMM; propagate with Skyfield/SGP4 for each candidate from the observer, test above-horizon and likely sunlit geometry, project into the WCS at precise frames and compare angular path, speed, direction and timing; check multiple hypotheses including aircraft/meteor/unknown. Record orbit-source timestamp and prediction uncertainty.

**Baseline:** independent known ISS prediction/log and measured streak trajectory. **Success:** right object ranked under controlled case, no forced identification on obvious aircraft/blank negatives, clear uncertainty state. **Failure:** historical orbital elements unavailable, media clock too inaccurate, multiple candidate tracks overlap, predicted brightness too uncertain. **Decision:** keep as `Possible Satellite` research extension only; never show confidently named satellite for anonymous single streak without independent evidence. [Skyfield limitation](https://rhodesmill.org/skyfield/earth-satellites.html), [CelesTrak archive](https://celestrak.org/norad/archives/request.php?FORMAT=json).

## E8. Product Usability + Competitive Differentiation — P0 / 1–2 developer-days

**Hypothesis:** the photo-grounded workflow materially improves understanding and context recovery compared with separate plate-solving and 3D tools.

**Assets:** working E1+E5 prototype, 3 realistic photos (one resolvable, one difficult, one unresolved), 5–8 test participants without astronomy training and a comparable SkySafari/Sideris/Plate Solver + WWT workflow. Participants should be unfamiliar with the prototype; pre-register task success criteria.

**Tasks:** (a) determine whether a particular dot was truly observed and identified; (b) find distance and uncertainty of a star; (c) distinguish `Predicted` Moon/planet from source-observed point; (d) visit a measured star region and return to exact original photo; (e) handle an unsolved picture without assuming invented stars; (f) obtain an export or source reference.

**Baseline:** direct use of available competitor apps for same task (where feature parity exists). **Provisional success:** at least 4/5 newcomers correctly distinguish observed from predicted/simulated; participants complete the photo-to-object-to-source loop independently; median task time or task error is meaningfully better than workaround, with feedback explaining why. These are prototype heuristics, **not statistical proof**. **Failure:** confusion about invented observations, little task-time advantage or unclear return to original. **Decision:** de-scope exploration even if technically successful when users gain no real value.

## Experiment order and dependencies

```text
E1 (solve a real photo) ────┬── E3 (verified catalog registration) ── E5 (3D transition)
                           │                                             │
E4 (predicted sky) ────────┴───────────────────────────────────────────── E8 (user test)

E2 (video frames/stacks) ─────── E6 (restore → better solve?)

E7 (satellite) = standalone optional investigation after E1/E3/E4
```

**Fastest stop conditions:** two days of E1 without any reliable consumer photo match => do not build generic photo-to-space product. E5 failure to maintain visual source continuity within four developer-days => use a 2D science map and let the astronomy extension remain optional. E8 showing no advantage over competitors => cut it from the hackathon demo regardless of animations.

---

# D11. Product Choice, Kiroween Strategy, and Demo

## Compare five possible products

| Direction | User and immediate value | Differentiation after competitor review | Science/engineering difficulty | Hackathon decision |
|---|---|---|---|---|
| **A. Night Restoration Studio** | Travelers with unreadably dark authentic clips; better video with source audio | Moderate; Topaz/Resolve formidable; adaptive evidence loop still unproven | Moderate-high; actual neural and codec tradeoffs | **Retain as baseline**. Prior conditional GO unchanged. |
| **B. Astronomy-Aware Restoration** | Astrophotographers/recorders needing star-preserving low-light improvements | Could be strong **if E6 measured gains**, otherwise speculative | **Very high validation risk** with false star bias | **Research branch only**, not MVP promise. |
| **C. Observation-to-Universe Explorer** | Curious travelers/educators; turn personal sky memory into catalog-backed 3D journey | Weak if merely photo→map→3D; SkySafari/Plate Solver/WWT cover pieces | Moderate if reuse WWT; high if custom engine | **Conditional targeted demo**, not standalone large universe engine. |
| **D. Unified Night Observation System** | End-to-end capture, restore, identify, orbital replay, fly-through | Broad 'everything' story but weak clarity and large substitute overlap | Extremely high; hardware API, scientific rigor, editing, satellite orbits | **NO-GO for Dec 4**, long-term platform only. |
| **E. Observation Lens** *(proposed narrow alternative)* | Anyone with one meaningful night recording; see what the camera captured and what science predicts, choose an object, explore and return | Stronger coherent memory/evidence proposition; still must beat existing photo solving in E8 | Moderate if still-first and reuse proven science renderers | **Recommended astronomy prototype** that attaches to, not replaces, Media Experiment Runtime. |

**One-sentence provisional product definition:** *An evidence-aware night-media workspace that restores real recordings and, when the sky can be calibrated, lets you explore the astronomical objects connected to that exact moment without confusing observation with prediction.*

**Better narrow first use:** a 5–10 MP star-rich photograph with source-preserving optional tone processing, verified WCS, click-to-inspect a few matched sources, and one catalog-backed `Explore` transition. A follow-up restoration-assisted video demonstration is acceptable **only after E2/E6 work**. Do not promise deep galaxies, arbitrary solar system flight, face restoration, atmospheric science, live wearable streaming and archived satellites in one first release.

## Rules and category

[Kiroween 2026 official rules](https://kiroween.devpost.com/rules) set the submission period **October 30–December 4, 2026**, deadline **December 4 at 2 PM Pacific**. Projects must be new work during that window (preexisting standard frameworks are permitted subject to disclosure), built meaningfully with Kiro, working and installable/runnable as presented, hosted for judges, with public GitHub repo, root `/.kiro`, open-source license, Kiro writeup and public YouTube demo **under three minutes**. You must disclose preexisting third-party code and comply with licenses; team size max three. Judging weights: potential impact 20%, Kiro/technical implementation 20%, design 20%, innovation 20%, presentation 20%. The rules allow one project category.

**Recommended category: Frankenstein**, if and only if the finished integration meaningfully combines the source-media evidence system, astronomy calibration and scientific navigation. The meaningful result is the unbroken photo-to-scientific-discovery experience, **not** a dashboard listing many APIs. If the astronomy direction proves only a 2D overlay and its 3D transition fails, the original restoration app may be stronger in **Costume Contest** on polished design, but that category expects Halloween-themed design serving function; don't force spooky decoration. Resurrection would require a real obsolete-tech revival thesis, not manufactured retro branding. Skeleton Crew requires two distinct apps from one template and is not suitable here. [Official rule definitions](https://kiroween.devpost.com/rules).

**Kiro use should be genuine and reviewable:** `/.kiro/specs/` for scientific contracts, failure modes and API bindings; steering on evidence taxonomy, strict provenance and UI copy; hooks running deterministic unit and property tests on WCS coordinate inversion, time-zone handling, catalog/provenance; custom agent if it demonstrably accelerates specialized tasks; MCP only when data retrieval adds material value. Require traceable design decisions and experiment results in README, not decorative Kiro mentions. Check contest eligibility and current Kiro credits separately; don't rely on platform features not actually tested.

## Minimum complete judge-tested product

1. Import one real, rights-cleared night-sky photo (plus optional sample photo) and preserve original bytes.
2. Run a real source point detector and real WCS solve; show explicit valid/invalid calibration result.
3. Show a few source-aligned stars, with external catalog identity and distance data with uncertainty.
4. Provide a reliable `Explore` transition to a bounded scientific scene based on those coordinates and a return to the identical photo.
5. Show actual `Original ↔ Enhanced` toggle **only if enhancement is real, non-fabricating and useful**; optional existing Media Experiment Runtime panel can expose parameters.
6. Export a standard processed still (optional) and observation manifest with WCS and source data. A video feature can exist separately but should not be faked.
7. Demonstrate failure-state photo (`Unresolved`) and a separate predicted sky map if time/place are known.

## Three-minute demo storyboard (proposed)

| Time | Onscreen action | What the judge learns |
|---|---|---|
| **0:00–0:18** | Real dark Sahara or other rights-cleared night photo/video with original ambient sound. “I saw more than the camera recorded.” | Immediate human problem, real media. |
| **0:18–0:40** | Drop source into app. Show actual source detection and calibrated result or transparent fallback. | Actual software does work, not a concept animation. |
| **0:40–1:02** | Toggle `Original` / genuinely enhanced treatment; inspect source-aligned evidence and label which detections are real matches. | Restoration constraints and honesty about observations. If enhancement unproven, show honest minimal adjustment, not invented stars. |
| **1:02–1:27** | Select a matched star, retrieve Gaia identity/distance with uncertainty; optionally ask “How far away?” | Scientific data meaningfully tied to this photo. |
| **1:27–1:53** | Tap `Explore`; source angular geometry anchors, then transition to marked catalog-geometry 3D view; show different viewpoint if supported. | Memorable defining interaction and a real scene engine. |
| **1:53–2:13** | `Back to Moment`; photo or video returns to exact frame/audio; inspector shows WCS residual and catalog/release. | Cohesive experience with provenance, not a disconnected planetarium. |
| **2:13–2:30** | Import an intentionally unsolvable frame; `Unresolved`, show separate `Predicted Sky` without fake photo labels. | Science integrity and useful failure handling. |
| **2:30–2:47** | Show executable graph in secondary inspector, rerun one actual step, export manifest/source-preserved artifact. | Real technical substance and Kiro-build relevance. |
| **2:47–2:58** | One-line summary, app/GitHub link. Leave slack for production editing. | Understandable product and working proof. |

**Important:** this is a contingent storyboard, not footage or proven app behavior. Do not record a polished animation in place of the actual result. If E5 fails, replace the 3D segment with honest source→sky-map interaction and reconsider whether the extension deserves the hackathon spotlight.

## Feasible work plan (dates are planning targets, not completed work)

| Window | One deliverable | Cutoff rule |
|---|---|---|
| Oct 31–Nov 3 | E1 consumer photo solving + E4 historical sky prediction | Stop full feature if star-rich photo cannot be validated. |
| Nov 4–7 | E3 WCS matching; minimal source viewer; first WWT/Three.js experiment | No simulated match markers in source view. |
| Nov 8–12 | E5 full photo→space→same photo interaction; E8 first user test | If no spatial continuity and usability advantage, cut `Explore`. |
| Nov 13–18 | E2/E6 low-light stack and limited restoration branch, if target clips available | Do not claim new imaging capability without held-out results. |
| Nov 19–28 | Polish core UX, real job telemetry, error states, tests and export; remote judge accessibility | Defer satellites and ambitious simulation. Plan around travel/compute constraints. |
| Nov 29–Dec 3 | GitHub, root `.kiro`, open-source license and obligations, demo recording, judging smoke test | Freeze features. Do **not** wait for Gaia DR4 scheduled Dec 2. |
| Dec 4 before 2 PM PT | Submit verified build, public YouTube video under 3 min and all form fields | Deadline is absolute, no late submissions. |

If this schedule collides with development commitments or travel, **cut scope before sacrificing a functioning end-to-end app.** Video calibration, AI optimization, historical satellites, entire universe catalog streaming and phone capture are first to cut. A precise photo-first experience is more competitive than a broad but broken video-to-space platform.

---

# D12. Final Decision and Implementation Readiness

## What has changed versus the October 31 media-restoration research

| Earlier finding | New evidence | Updated decision |
|---|---|---|
| Recoverable night detail depends on sensor data and noise; models may invent details | Plate-solving also depends on *detectable original star patterns*; neural detection research warns preprocessing may even reduce solving | **Unchanged physical limit.** Add source-point evidence and calibration verification. |
| Best core architecture was versioned Media Experiment Runtime | Astrometry.net, Gaia/Horizons, WWT/Aladin can be composed as an independent `Observation Solution` | **Extend runtime**, do not pivot to standalone planetarium architecture. |
| Interface should be media-first and graph inspectable | Competitors already provide photo-to-map but don't independently establish the integrated original-audio/experiment-return workflow | **Keep media-first.** Add contextual `Sky`/`Explore` only when supported. |
| Novelty hypothesis was adaptive media experimentation | SkySafari 8, Sideris, Plate Solver already do photo solving; WWT/Gaia Sky already do space flight | **Do not pitch plate solving or 3D flight as new.** Demonstrate actual unified evidence-aware interaction and stronger real-world value. |
| Main risk was low-light enhancement utility | Additional risks: calibration fail, distortion/pose errors, WWT photo-to-3D camera API limitations, missing 3D distance info | **Run E1 and E5 immediately**, before extensive UX work. |
| Moon/planets/satellites were potential future sky extras | Official ephemeris solutions available, but archived satellite GP and timing limit certainty | **Planets P1; satellites P2**, never core hackathon promise. |
| Future trip footage would provide validation | Current photo-first experiment can use controlled real images before any trip; Gaia DR4 planned two days before deadline | **Start now on existing/permissioned media and Gaia DR3**. Future real footage validates but cannot block progress. |

## Explicit GO / DEFER / NO-GO

**GO — research prototype now:** E1 consumer-photo plate solving, E3 validated catalog match, E4 historic predicted sky, E5 photo-to-catalog-3D transition, then E8 usability comparison. This is a short spike attached to the existing concept. It is a good fit for the *Frankenstein* category **if it yields an unusually useful integrated result**.

**CONDITIONAL GO — restoration-aware astronomy as a core product capability:** only after E2/E6 show measured improvements in source-supported detection, valid solve rate and real user value over ordinary image processing and separate plate-solving tools. Until then, describe it as an experiment, not a proven imaging breakthrough.

**DEFER:** named historical satellite ID, robust noisy moving video calibration across arbitrary glasses footage, real-time wearable streaming, physically complete 3D universe, simultaneous full-media restoration and astronomy agents, exoplanet worlds and arbitrary constellation light-travel simulation.

**NO-GO:** 'AI reveals the actual stars that were invisible in the recording'; painting catalog-predicted stars into authentic media; all-in-one planetarium clone; image-to-space cinematics without valid WCS/science state; multicolored animated agent cursors pretending to execute science.

## Readiness criteria before issuing a final coding prompt

- [ ] At least one representative non-astrophotography **consumer photo** has verified WCS with measured residuals.
- [ ] A dark/unsolved example correctly remains `Unresolved` and never receives invented catalog matches.
- [ ] At least one catalog-backed star returns a sourced distance **and its limitations**, not a fabricated exact number.
- [ ] A real 2D anchored overlay and photo-to-3D renderer spike exists; selected source star preserves angular alignment across the transition, or a bounded alternative is chosen.
- [ ] E8 participants can identify what is **Observed / Catalog Matched / Predicted / Simulated** and navigate back to the exact source.
- [ ] Third-party licenses (Astrometry.net, Aladin, WWT, image surveys) and actual distribution methods are checked.
- [ ] The primary user experience still provides **a genuinely useful result**, not a novel technology collage.
- [ ] If claiming restoration helps astronomy, E6 includes independent negative tests and reports validated gains **and false solves**.

**One decisive recommendation:** **Do not change the main build plan yet.** Keep the narrow night-media restoration direction as baseline. Authorize a **time-boxed Observation Lens feasibility spike** that proves *one real photo → one verified celestial match → one honest scientific 3D transition → exact return to the original moment*. If those four transitions work and user tests show a clear advantage over existing combinations, add this to the Kiroween core. If they do not, retain the media restoration MVP and drop the universe experience from the competition entry.

---

# Primary Source Register and Verification Notes

The following links are not all equal kinds of evidence. Peer-reviewed papers establish methods in their test domain; vendor pages establish described features, not independent quality; official archives document their datasets; GitHub demonstrates available source and licenses but not necessarily deployment performance. No independent hands-on app comparisons were conducted in this research.

**Core science and software**

1. Lang et al. (2010), *Astrometry.net: Blind Astrometric Calibration of Arbitrary Astronomical Images*, *Astronomical Journal* — https://doi.org/10.1088/0004-6256/139/5/1782
2. Astrometry.net API — https://astrometry.net/doc/net/api.html
3. Astrometry.net distribution license — https://github.com/dstndstn/astrometry.net/blob/main/LICENSE
4. Astrometry.net index files — https://data.astrometry.net/
5. Astropy coordinate transforms — https://docs.astropy.org/en/stable/coordinates/
6. Astropy AltAz/refraction limits — https://docs.astropy.org/en/stable/api/astropy.coordinates.AltAz.html
7. Photutils DAOFIND — https://docs.astropy.org/projects/photutils/en/stable/api/photutils.detection.DAOStarFinder.html
8. SEP source extraction — https://sep.readthedocs.io/en/stable/
9. Megeri (2026), unpublished SSRN preprint on learned urban-light-polluted star detection — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7518006
10. Luo et al. (2026), star identification, *Applied Optics* — https://pubmed.ncbi.nlm.nih.gov/41842109/
11. Luo et al. (2025), star-sensor centroid detection, *Optics Express* — https://pubmed.ncbi.nlm.nih.gov/40797963/
12. Gaia DR3 archive — https://gea.esac.esa.int/archive/
13. Gaia DR4 scheduled-release page — https://www.cosmos.esa.int/web/gaia/data-release-4
14. Bailer-Jones EDR3 distance inference FAQ — https://bailer-jones.www3.mpia.de/gedr3_distances/FAQ.html

**Catalogs, ephemerides and simulations**

15. JPL Horizons API — https://ssd-api.jpl.nasa.gov/doc/horizons.html
16. Skyfield satellite prediction limitations — https://rhodesmill.org/skyfield/earth-satellites.html
17. CelesTrak usage policy, updated May 2026 — https://celestrak.org/usage-policy.php
18. CelesTrak historical GP request and six-digit ID warning — https://celestrak.org/norad/archives/request.php?FORMAT=json
19. Space-Track GP_HISTORY documentation — https://www.space-track.org/documentation
20. VizieR/TAP — https://vizier.cds.unistra.fr/
21. SIMBAD — https://simbad.cds.unistra.fr/simbad/
22. NASA Exoplanet Archive TAP — https://exoplanetarchive.ipac.caltech.edu/docs/program_interfaces.html
23. NASA SkyView permissions — https://skyview.gsfc.nasa.gov/current/help/faq.html
24. ESASky — https://sky.esa.int/esasky/
25. NAIF SPICE terms — https://naif.jpl.nasa.gov/naif/rules.html

**Visualization and existing products**

26. WWT renderer docs — https://docs.worldwidetelescope.org/webgl-reference/latest/
27. WWT 3D mode — https://worldwidetelescope.org/Learn/Exploring
28. WWT WebGL Engine (MIT) — https://github.com/WorldWideTelescope/wwt-webgl-engine
29. Aladin Lite v3 documentation — https://aladin.cds.unistra.fr/AladinLite/doc/
30. Aladin Lite LGPL license — https://github.com/cds-astro/aladin-lite/blob/master/LICENSE
31. Stellarium Web Engine (AGPL) — https://github.com/Stellarium/stellarium-web-engine
32. Gaia Sky v3.9.2 Oct 2026 — https://gaiasky.space/
33. NASA Eyes — https://science.nasa.gov/eyes/
34. SkySafari 8 image gallery / plate solve — https://support.simulationcurriculum.com/hc/en-us/community/posts/37033110168215-SkySafari-8-Is-Now-Available-Read-On
35. Sideris mobile photo solver — https://apps.apple.com/us/app/sideris-sky-map-plate-solver/id6809145518
36. Plate Solver online — https://alokm.com/solve/
37. Skyshot (device-specific benchmark methodological note) — https://skyshot.app/technology
38. Cosmoscope 3D universe (third-party claim) — https://cosmoscope.space/
39. Stellata open-source 3D model (third-party claim) — https://github.com/alexmensch/stellata
40. Kiroween 2026 official rules — https://kiroween.devpost.com/rules
41. Kiroween FAQ — https://kiroween.devpost.com/details/faqs

**Unverified items that must remain marked unverified:** accurate solve rate on user's actual recordings; availability of sufficient historical orbit elements for a given satellite; WWT seamless photo-to-3D API choreography; scene-quality/performance on the exact intended browsers and Mac; a direct commercial competitor offering the complete media/audio/restoration/provenance workflow; individual licensed redistributability of every survey asset; viability of astronomy-guided restoration as an actual quality improvement.

**Final sentence:** The scientifically defensible idea is not to *recover the universe from a camera*. It is to **anchor an exploration of the known universe to what the camera actually observed, while preserving the difference between evidence and inference**.
