# Renderer WebGPU Spike

Date: 2026-11-03. Spike code: [`apps/webgpu-spike/`](../../apps/webgpu-spike/) (isolated workspace package, its own `three`).
Machine: Apple M5 Max (40-core GPU, Metal 4), macOS 26.7, Node 24.15.0, pnpm 10.7.1.
Sections 9, 10 and 12 were brought up to date with `main` after the spike; the measurements are as taken (see the data note in section 4). Section 10 records milestone 1 of the migration (2026-11-04), with its own measurements.

Every claim carries one label:

- **[verified]** checked against the npm registry, release notes, project docs, or installed source in this worktree.
- **[measured]** timed or observed on this machine during this spike.
- **[inference]** reasoned from verified or measured facts, not tested directly.
- **[proposed]** a recommendation or plan.

## 1. Recommendation

**Hybrid now, full migration in stages.** [proposed]

Move the cosmos renderer to three r187 `WebGPURenderer` with TSL now, and choose the primitive per content type instead of defaulting to Gaussian splats: analytic point-spread-function sprites for stars, ray-marched emission-absorption volumes for interstellar dust and nebulae, and splats only for places reconstructed from photos or video. The same TSL code ran on both WebGPU and the WebGL2 fallback on this machine, and the full Local Bubble scene with 100k HDR stars, bloom, AgX and FXAA took 0.85 ms per frame at 3840x2160 with splats and 2.08 ms with a half-resolution dust volume, against an 8.33 ms budget at 120 Hz [measured]. For capture-site splats, three's native `GaussianSplat` handled about 1M splats inside the same renderer [measured]; keep the existing Spark 2.4 (WebGL2) path only for larger captures until a WebGPU splat renderer selects level of detail. `@sparkjsdev/radix` is the likely successor but is 5 days old and already broke on three r187 [verified, measured].

Why not "wait": the WebGPU path is faster than the current stack needs, the TSL post-processing set covers the film look (HDR, AgX, bloom, temporal and morphological AA, depth of field, motion blur), and the fallback is the same code [verified, measured]. Why not "migrate everything now": the photoreal splat options for WebGPU lack streaming level of detail, and three's WebGPU API is still changing release to release [verified].

## 2. Versions and platform status

| Item | Finding | Label |
|---|---|---|
| three | Latest `0.187.0` (npm, 2026-10-30). `0.186.0` published 2026-09-08, `0.186.1` 2026-09-24. | [verified] |
| three native splats | r186 added "Gaussian Splat renderer / loader using TSL for WebGPU/WebGL + glTF import" (PR #33950, merged 2026-08-08), later renamed `GaussianSplatMesh` to `GaussianSplat` (#34306). Addon at `three/addons/objects/GaussianSplat.js`: GPU counting sort (4096 depth bins, re-sort after about 1.8 degrees of rotation, planar depth), CPU sort on the WebGL2 backend, SH up to degree 3, RGBA8 color, loaders for PLY, SPZ, SPLAT, KSPLAT and glTF `KHR_gaussian_splatting`, raycasting. No level of detail or streaming. | [verified] (release notes, installed source) |
| @types/three | `0.187.0` covers `GaussianSplat`, `GaussianSplatUtils` and every TSL display node used here. | [verified] |
| @sparkjsdev/spark | Latest `2.4.0` (2026-10-20), peer `three >=0.180.0`. WebGL2 only: `RawShaderMaterial`/`ShaderMaterial` and `renderer.getContext()` WebGL extensions; no WebGPU in any 2.x release note. Level of detail via RAD paging since 2.0. Opacity stored as 8-bit unorm. | [verified] (npm, releases, installed dist) |
| @sparkjsdev/radix | `0.0.1` (2026-10-29), "An advanced WebGPU 3D Gaussian Splatting renderer for THREE.js" (World Labs). Peer `three >=0.186.0`, developed on 0.186.0. WebGPU compute projection and radix sort every frame, WebGL2 fallback with worker sort, TSL shaders, HDR output, PLY/SPZ/RAD/RDX. Its own docs list level-of-detail selection, skinning and raycasting as not built in (edits only through example ports) and call it new. Its `dependencies` field lists 114 packages (http-server, eslint and similar tooling). | [verified] (npm, installed dist, project docs) |
| radix on three 0.187 | Throws `A dispatch size cannot be passed to "renderer.compute()" for nodes created with "compute( count )"` (three #34813). A 10-line shim ([`radixCompat.ts`](../../apps/webgpu-spike/src/render/radixCompat.ts)) restores the r186 behavior. | [measured] |
| Safari 26 | WebKit: WebGPU "is now shipping in Safari 26.0 for macOS, iOS, iPadOS, and visionOS". Safari 26.6.2 is installed here but was not benchmarked. | [verified], not measured |
| Chrome | Chrome 156.0.8078.80 headless ran WebGPU on the Apple M5 Max adapter (`metal-3`). The Claude desktop Browser pane is Chromium 152 and also exposed WebGPU. | [measured] |
| TSL post-processing in r187 | `RenderPipeline` (renamed from `PostProcessing` in r183) and `DirectRenderPipeline` (r186). HDR: `pass()` renders to HalfFloat by default. Tone mapping: `AgXToneMapping` and TSL `agxToneMapping`. Bloom: `BloomNode`. AA: `FXAANode`, `SMAANode`, `TRAANode`, `TAAUNode`, `SSAAPassNode`, MSAA via `pass(..., { samples })`. Depth of field: `DepthOfFieldNode`. Motion blur: `MotionBlur.js` (needs a velocity MRT). Also `GTAONode`, `SSRNode`, `SSGINode`, `GodraysNode`, `LensflareNode`, `ChromaticAberrationNode`, `FilmNode`, `Lut3DNode`, `lensDirt` (r187), `FSR1Node`, `OITPassNode`. | [verified] (installed source) |
| Used and timed here | AgX, bloom, FXAA, SMAA, TRAA, 4x MSAA. Depth of field and motion blur were not built: splats, sprites and volumes write neither depth nor correct velocity, so those nodes need a depth or velocity proxy first. | [measured] / [inference] |

The brief's "current state" was right about `main` but not about this worktree: the worktree started at `bd97a9e`, which predates the spatial pivot (three `^0.174`, no Spark, no `SparkPortalRenderer.ts`). It was fast-forwarded to `main` (`7405315`), where `apps/web` pins three `^0.180.0` (installs 0.180.0) and Spark `^2.4.0` (installs 2.4.0). [verified]

## 3. Rendering primitive per content type

Measured numbers are pipelined medians at 3840x2160 in headless Chrome 156 on WebGPU, each including the post chain (bloom, AgX, FXAA), whose own cost was 0.60 ms. See section 4 for the method and all resolutions.

| Content | Chosen primitive | Physical or mathematical reason | Measured (4K) | Not verified |
|---|---|---|---|---|
| Stars (point sources) | Analytic PSF sprites, additive linear HDR, no sort. Chromatic Airy core (ring radius scales with wavelength per channel), Moffat glare halo, four diffraction spikes, energy normalized; flux 10^(-0.4 m); color from Teff through Planck x CIE 1931 to linear sRGB at unit luminance. Faint stars keep only the rings above the display threshold, rescaled by encircled energy so total flux is conserved. ([`stars.ts`](../../apps/webgpu-spike/src/render/stars.ts), [`psfMath.ts`](../../apps/webgpu-spike/src/render/psfMath.ts), [`blackbody.ts`](../../apps/webgpu-spike/src/color/blackbody.ts)) | A star is unresolved, so its image is the instrument PSF scaled by its flux; magnitude is photometry and Teff fixes the spectrum. Addition is commutative, so accumulation is order independent and needs no sorting. WebGPU has only 1 px points, so `gl_PointSize` starfields must become instanced sprites anyway. | 100k stars: 0.74 ms total, about 0.14 ms over the post chain alone. PSF integrates to 0.997 within 120 px; J1 and CCM values match references (unit tests). | Real catalog rows (the 100k stars are a seeded synthetic perf fixture); 1M+ stars, which need GPU culling and LOD; Safari. |
| Interstellar dust and nebula volumes (Local Bubble) | Ray-marched emission-absorption volume over a 3D density texture: Beer-Lambert transmittance exp(-tau), exact per-step emission integral, CCM89 per-channel extinction, 8^3-brick empty-space skipping, march at half resolution and composite at full resolution. ([`dustVolume.ts`](../../apps/webgpu-spike/src/render/dustVolume.ts), [`voxelize.ts`](../../apps/webgpu-spike/src/data/voxelize.ts)) | 3D dust maps are extinction densities per unit length, which is exactly what this integrates; splats only approximate the integral by sorted alpha blending. Optical depth per channel dims and reddens every background source along the ray, which scalar splat alpha cannot. No sorting, so no pops; the field is view consistent by construction. | Same 2,811-splat data: volume 4.33 ms full res, 1.85 ms half res, 11.33 ms without skipping; splats 0.76 ms (three) and 0.86 ms (radix). With 100k stars: volume half res 2.08 ms, splats 0.85 to 0.95 ms. Reddening and sort stability measured (section 5). | Calibrated densities (the stand-in comes from presentation opacities); finer grids (memory grows with the cube); temporal stability of the start jitter under motion; Safari. |
| Photoreal places from photos or video (the user's capture site) | Gaussian splats. | Reconstruction output (3DGS) is splats with view-dependent color; no other real-time primitive reproduces captured appearance. | 982k-splat fixture: three 1.56 ms, radix 0.96 ms. 500k fixture: three 1.04 ms median but 15.3 ms p95, radix 0.88 ms median, 4.89 ms p95. | The user's own capture (none in the repo), streaming level of detail on WebGPU, mobile GPUs. |
| Planets and moons (proposed) | Meshes with TSL node materials (existing LRO, Earth day/night, Bruneton LUT, Lommel-Seeliger). | Opaque surfaces with measured textures. | Not measured. | All. |
| All-sky background (proposed) | Equirectangular photograph as a background node. | Measured imagery at infinity, not geometry. | Not measured. | All. |
| Relativistic and procedural set pieces (proposed) | Screen-space analytic ray marching in TSL at adaptive resolution. | Simulated content; cost is per pixel, not per object. | Not measured. | All. |

## 4. Measurements

### Method [measured]

- **Primary run:** headless Chrome 156 (`--headless=new --enable-webgpu-developer-features`, separate profile in the session scratchpad) loading the spike with `?bench=suite`. The page reported `visibilityState=visible`, `hasFocus=true`, rAF at 60 Hz (not display synced), adapter `apple / metal-3 / Apple M5 Max`. Results are POSTed to the dev server and written as JSON.
- **Cross-check:** the Claude desktop Browser pane (Chromium 152), in its own tab on port 5191. The pane was hidden, so rAF was paused; the harness does not depend on rAF. That run predates the analytic PSF and the volume, and for the configurations both runs share (post only, stars + splats, scaling fixtures) it agreed with the headless run within about 15%.
- **Frame loop:** frames are rendered from an async loop, not rAF. three r187 only advances its node frame inside its own rAF loop, so the harness advances it per frame; without that, every frame after the first redrew only the final full-screen quad (this invalidated a first round of numbers and is a real migration trap, see section 8).
- **Pipelined** (the headline number): 30 frames rendered back to back, then `queue.onSubmittedWorkDone()`, divided by 30, 8 batches. **Serialized:** `render()` through queue idle per frame, 240 frames. WebGL2 backend: a `fenceSync` polled across tasks (Chrome's `gl.finish()` does not block).
- **Camera:** one full orbit of the Local Bubble per 240 frames (radius 22, elevation wave), 60 warm-up frames per configuration. The scaling fixtures orbit their own bounds center at 0.15 of their bounding radius.
- **GPU timestamps** were recorded but are not used: with Chrome's default quantization they come in 65.5 microsecond steps, and even unquantized their per-pass sums disagree with wall clock by up to 10x on this Metal adapter.
- **Noise:** an earlier run of the same configuration measured the full-resolution volume at 6.67 ms (4K) against 4.33 ms in the final run; fill-bound configurations varied by up to about 50% between runs, light ones by up to about 25%.
- **Local Bubble data:** the 2,811 splats the app's loader kept at the time, with the bake's earlier colours and opacities. Both have since changed (section 12): a re-run on `main` draws all 14,281 splats, so the Local Bubble rows below would need re-measuring.

### Local Bubble scene, WebGPU, pipelined median ms [measured]

| Configuration | 1920x1080 | 2560x1440 | 3840x2160 |
|---|---|---|---|
| Post only (bloom, AgX, FXAA) | 0.47 | 0.46 | 0.60 |
| 100k analytic-PSF stars | 0.51 | 0.48 | 0.74 |
| Local Bubble splats, three `GaussianSplat` | 0.51 | 0.53 | 0.76 |
| Local Bubble splats, radix | 0.63 | 0.61 | 0.86 |
| Local Bubble volume, skipping, full res | 1.45 | 1.98 | 4.33 |
| Local Bubble volume, no skipping | 3.98 | 5.84 | 11.33 |
| Local Bubble volume, skipping, half res | 0.94 | 1.24 | 1.85 |
| Stars + three splats | 0.58 | 0.66 | 0.85 |
| Stars + radix splats | 0.66 | 0.80 | 0.95 |
| Stars + volume, full res | 1.33 | 2.30 | 4.89 |
| Stars + volume, half res | 0.86 | 1.28 | 2.08 |

Serialized medians at 4K were 1.4 ms (stars + three splats) and 2.2 ms (stars + half-res volume). JavaScript submit time was 0.2 to 0.4 ms per frame throughout.

### Anti-aliasing at 4K (stars + three splats), pipelined median ms [measured]

| No bloom, no AA | Bloom + FXAA | Bloom + SMAA | Bloom + TRAA | Bloom + 4x MSAA |
|---|---|---|---|---|
| 0.34 | 0.85 | 1.08 | 1.43 | 1.07 |

TRAA ran but was not judged for correctness: none of these layers writes velocity, so ghosting under motion is expected [inference].

### Splat-count scaling fixtures, pipelined median (p95) ms [measured]

Two unreferenced `.spz` scenes in `apps/web/public/splats/` (provenance not recorded, not astronomical), SH disabled on both paths, post chain on, no stars.

| Fixture | Path | 1920x1080 | 3840x2160 |
|---|---|---|---|
| 500k splats | three | 0.88 (20.39) | 1.04 (15.27) |
| 500k splats | radix | 0.74 (2.39) | 0.88 (4.89) |
| 982k splats | three | 1.25 (1.27) | 1.56 (1.66) |
| 982k splats | radix | 0.61 (0.66) | 0.96 (1.03) |

The three p95 spikes on the 500k scene appeared on both backends; the cause was not isolated (likely very large near-camera footprints, which three caps at 1024 px) [inference].

### Same TSL code on the WebGL2 backend (`forceWebGL`), 4K pipelined median ms [measured]

| Configuration | WebGPU | WebGL2 |
|---|---|---|
| Post only | 0.60 | 0.75 |
| Stars + three splats | 0.85 | 1.04 |
| Stars + radix splats | 0.95 | 1.47 |
| Volume, skipping, full res | 4.33 | 4.96 |
| Volume, no skipping | 11.33 | 11.27 |
| Stars + volume, half res | 2.08 | 2.16 |
| 982k splats, three (CPU sort on WebGL2, 1.5 ms of main-thread JS per frame) | 1.56 | 2.05 |
| 982k splats, radix (worker sort on WebGL2) | 0.96 | 1.27 |

Fill-bound work (the volume) runs at nearly the same speed on both backends; WebGPU is about 20 to 55% faster on draw- and sort-heavy frames.

### Other measurements [measured]

- **Voxelization:** 2,811 splats into 161x240x207 voxels (0.1 units, 8.0M voxels, 64 MB RGBA16F) in 76 ms on the main thread, upload 140 ms; 13.1% of 8^3 bricks are non-empty, which is why skipping gives 2.6x.
- **Visual parity:** at the same pose the volume and the splats render the same structure, brightness and color ([splats](img/webgpu-spike/local-bubble-splats-three.jpg), [volume](img/webgpu-spike/local-bubble-volume.jpg)); the volume is slightly softer because 0.1-unit voxels blur sheets whose thinnest sigma is 0.042.
- **Sorting pops:** three's `GaussianSplat` rendered with the order it computed 1.7 degrees earlier (the staleness it shows between re-sorts) against a fresh sort, 24 poses at 1280x720, AA off: largest difference 1 display code value, 0% of pixels at 2 or more. radix and the volume re-rendered at the same pose: 0 difference.
- **Reddening on the GPU path:** absorption only, extinction x16, bloom and AA off: 665 star pixels dimmed (median display luminance x0.715), their red/blue ratio rose by a median 14.7%, and 86.8% got redder. At x4 (measured in the pane): 128 pixels, +7.4%. At x1 (the splats' own opacity) the stand-in dust is optically thin (tau up to about 0.13) and only 3 star pixels dimmed by more than 15% ([no dust](img/webgpu-spike/reddening-no-dust.jpg), [extinction x16](img/webgpu-spike/reddening-extinction-x16.jpg)).
- **Bumping `apps/web` to three 0.187 and @types/three 0.187** (on the current WebGLRenderer plus Spark 2.4) typechecked, passed the web tests, and rendered the landing showcase (photosphere, plasma ring, stars and Local Bubble splats visible); the console messages still buffered showed only `THREE.Clock` deprecation warnings. Other regimes were not exercised. The bump was reverted in the spike and landed as migration step 1 (section 10).

## 5. Dust and nebulae: splats against a ray-marched volume

| Criterion | Splats (three or radix) | Ray-marched volume |
|---|---|---|
| Frame time, same data, 4K | 0.76 to 0.86 ms [measured] | 4.33 ms full res, 1.85 ms half res [measured]; step size and temporal reprojection would cut it further [inference] |
| Sorting | Required every view change; three re-sorts after about 1.8 degrees with 4096 bins, radix every frame [verified] | None [verified] |
| Sort pops | Invisible for this data (max 1 code value) because every splat has alpha 0.003 to 0.045, and a pairwise order error is bounded by alpha_i x alpha_j x color difference [measured, inference]. Dense photoreal splats will pop more [inference]. | None [measured] |
| View-dependent artifacts | Affine (EWA) projection distorts footprints near the camera and at wide FOV; three clips centers outside 1.4x the clip box and caps footprints at 1024 px [verified]. | Resolution limited (voxel size) and stepping noise; the start jitter is a fixed per-pixel pattern that slides with the camera until temporal accumulation is added [inference]. |
| Physical model | Sorted alpha compositing of projected Gaussians, an approximation of the volume integral; projected opacity is view independent although a real column is not [verified] | Emission-absorption radiative transfer, exact per step; densities can come straight from a 3D dust map in extinction per unit length [proposed] |
| Chromatic extinction of stars behind dust | No: alpha is one scalar, so attenuation is grey. It would need a per-channel transmittance target or dual-source blending, which none of the three splat renderers provides [verified for three and radix source; inference for Spark]. | Yes, measured above. Because one extinction law applies everywhere, tau at one channel reconstructs all three exactly, so the half-res pass stores only `vec4(L_rgb, tau)` [measured]. |
| Precision | 8-bit opacity: three 1/255, Spark 1/255, radix 1/128; the Local Bubble's 0.003 to 0.045 landed on about 11 code values in the app at the time, and 0.003 rounds to 0 in radix [verified]. The bake now writes one opacity, 0.0039, which also rounds to 0 at radix's 1/128 unless stored scaled [inference]. The spike stores opacity x16 or x32 and divides in the shader [measured]. | Half-float density, no quantization issue at this range [verified]. |
| Memory | 32 bytes per splat in radix (`extSplats`), about 52 bytes plus sort buffers in three's addon; well under 1 MB for 2,811 splats [verified] | 64 MB for this grid; 0.05 units would be 512 MB, so finer data needs sparse bricks [inference] |

**Answer to the reddening question:** yes. Background stars are dimmed and reddened per ray by the integrated column with CCM89 (R_V = 3.1, A/A_V = 0.887, 1.001, 1.234 at 612, 549 and 465 nm), in the same full-screen pass that integrates the dust emission, for the cost already in the volume numbers. [measured]

**Choice:** volume for dust and nebulae [proposed]. Splats win on frame time for this tiny stand-in, but only the volume is physically correct for extinction, is free of sorting, and matches how the science data is stored. At half resolution it fits the budget with 6 ms to spare at 4K.

## 6. Splats for photoreal capture sites

| Option | Status | Backend | Level of detail | Measured here |
|---|---|---|---|---|
| Spark 2.4.0 | Released, documented, many formats (PLY, SPZ, SPLAT, KSPLAT, SOG, RAD), WebXR, edits, skinning [verified] | WebGL2 only [verified] | Yes, RAD paging [verified] | The current app; not benchmarked in the spike |
| three r186+ `GaussianSplat` | In three's addons, maintained with the renderer [verified] | WebGPU (GPU sort) and WebGL2 (CPU sort) [verified] | No [verified] | 982k at 4K: 1.56 ms; 500k: 15 to 20 ms p95 spikes |
| @sparkjsdev/radix 0.0.1 | New; self-reported 1.37x Spark over five scenes and 1.8x at 10M splats (not checked) [verified that it claims this] | WebGPU compute, WebGL2 fallback [verified] | Loads LoD trees, does not select yet [verified] | 982k at 4K: 0.96 ms; needed an r187 shim |

Plan: keep Spark for large captures until radix (or three) selects level of detail; use three's `GaussianSplat` inside the WebGPU renderer for captures up to about 1M splats [proposed]. 3DGS captures are trained with blending in sRGB space. In a linear HDR pipeline both radix (which decodes colors to linear for linear targets) and three's addon (which treats colors as linear) blend in linear space, so a trained capture will not match its training views exactly unless it is drawn in a separate sRGB pass, as radix's default `directCanvas` mode does [verified in source]. The capture-site layer must pick a convention deliberately.

## 7. Does WebGPU compute change the platform decision, splats aside?

Yes, for some workloads [inference]:

- **Culling and level of detail for catalogs.** Gaia-scale star sets (10^7 and more) need GPU frustum and magnitude culling with indirect draws; WebGL2 has neither compute nor storage buffers (transform feedback is its only GPU-side option), so this mostly falls back to CPU work.
- **Sorting.** Only splats need it. On WebGL2 three's sort cost 1.5 ms of main-thread JavaScript per frame at 982k splats [measured].
- **Accumulation.** Additive PSF accumulation already works with blending on both backends [measured]; compute adds tile-binned rasterization of millions of tiny PSFs and luminance histograms for physically based auto-exposure.
- **Ray marching.** Works in fragment shaders on both backends at the same speed [measured]. Compute helps with GPU-built occupancy pyramids, tiled marching and temporal reprojection, not with feasibility.

Because TSL compiles to both backends, compute-only features can degrade on WebGL2 without forking the renderer. That favors `WebGPURenderer` as the platform even for users without WebGPU [inference].

## 8. Risks

- **API churn in three's WebGPU path:** `PostProcessing` was renamed to `RenderPipeline` in r183, and r187's compute dispatch check broke radix within a day of its release. Pin exact versions and upgrade deliberately. [verified]
- **radix maturity:** 0.0.1, a 114-package `dependencies` list, an r187 incompatibility, and missing level of detail. Treat it as a candidate, not a dependency. [verified]
- **Frame-loop semantics:** in r187 frame-scoped nodes (the scene `PassNode`, bloom) re-render only when the node frame advances, which happens inside `setAnimationLoop`. Offscreen rendering, video export and tests must advance it themselves. Reproduced in the app in milestone 1 (1 draw call per frame instead of 26); `WebGpuHost.advanceNodeFrame()` does it. [measured]
- **No large points, no `preserveDrawingBuffer`:** WebGPU points are 1 px, so all five starfield materials and both particle materials need sprites; screenshots need `readRenderTargetPixelsAsync` or a same-task canvas read. [verified]
- **Color pipeline change:** ACES to AgX, and three code paths tone-map inline (Earth `x/(1+0.56x)`, black hole `x/(1+0.42x)`, nebula plane `x/(1+0.82x)`), which would double-compress under AgX. Needs an art pass. The Earth's inline curve is gone from the WebGPU port; the atmosphere LUT itself is stored tone-mapped (section 12), so a mild second compression remains there. [verified]
- **GLSL to WGSL semantics:** the GLSL relied on behaviour WGSL leaves undefined or computes differently: `pow(x, 2.0)` on negative values (NaN in WGSL) and `smoothstep` with reversed edges. Ports must square explicitly and order the edges; a straight transliteration renders NaNs or nothing. [verified]
- **Depth and velocity:** splats, sprites and volumes write neither, so TRAA, motion blur and depth of field need proxies before they can be trusted on these layers. [inference]
- **Browser coverage:** measured only in Chromium on macOS. Safari 26 ships WebGPU but was not tested; Firefox, Windows, Android and Linux are unverified. The WebGL2 fallback ran the full spike. [verified, measured]
- **Measurement fidelity:** GPU timestamps were unusable on this adapter; all numbers are wall clock on one machine. [measured]
- **Volume memory:** cubic growth with resolution; science-grade maps need sparse bricks or streaming. [inference]
- **WebXR on WebGPU:** three added it in r185, but device and browser support was not checked; the app currently uses `SparkXr`. [verified, not tested]
- **TSL typing:** `@types/three` generics needed explicit type arguments in places, and radix's declarations bind to whichever `@types/three` pnpm hoists unless `paths` pins them (done in the spike's `tsconfig.json`). [measured]

## 9. Custom shader audit: `SparkPortalRenderer.ts`

Brought up to date with `main` after the spike, where the Gaia DR3 and Hipparcos star tiles (`catalogStars/`) replaced the procedural starfield helper and the Earth shader lost its procedural detail. `SparkPortalRenderer.ts` defines 10 `ShaderMaterial`s and 1 `ShaderPass`, and `catalogStars/CatalogStarField.ts` 1 more `ShaderMaterial` (12 GLSL programs). At runtime that is 12 `ShaderMaterial` instances (the particle helper runs twice; one catalog star material serves every tile and all 5 sky mounts) plus the internal materials of `UnrealBloomPass` and `OutputPass`. Line numbers are `SparkPortalRenderer.ts` unless stated. [verified]

| # | Program (line) | Instances | What it does | Verdict | Days |
|---|---|---|---|---|---|
| 1 | `skyMat`, all-sky dome (578) | 1 | ESO Milky Way and NASA star map on a back-side sphere, spectral band remaps, monocle mask with a hard-coded 1600x1000 viewport | Direct port to a background node; replace the hard-coded size with the viewport | 0.5 |
| 2 | Catalog star points (`CatalogStarField.ts`, 59) | 1 | Gaia DR3 (to G 12) and Hipparcos 2007 tiles streamed per HEALPix cell for the view and limiting magnitude; `gl_PointSize` core and halo sized by catalog flux, spikes brighter than magnitude 1, tint from the catalog colour index | Rewrite as the spike's analytic-PSF sprites fed by the same tiles (WebGPU points are 1 px) | 1.5 |
| 3 | Curl "plasma" particles (706) | 2 | Vertex-displaced `gl_PointSize` points with sin/cos noise, labeled GPGPU and MHD although it is neither | Drop (or rewrite as sprites with a Modeled label) | 0 to 1 |
| 4 | Moon (785) | 1 | LRO albedo, LOLA-derived bump, micro-relief noise, Lommel-Seeliger, opposition surge, earthshine | Direct port (TSL node material with a custom lighting function) | 1 |
| 5 | Earth (963) | 1 | Day (Blue Marble 2004), night lights (Black Marble 2016), clouds and their shadows, specular, Bruneton LUT limb, inline tone mapping | Direct port; remove the inline tone mapping | 1.5 |
| 6 | Earth atmosphere shell (1068) | 1 | Additive back-side limb glow from the Bruneton LUT | Direct port | 0.5 |
| 7 | Betelgeuse photosphere (1149) | 1 | 6-octave fbm and ridged convection, limb darkening | Direct port (pure math) | 0.75 |
| 8 | Corona plane (1272) | 1 | Additive fbm rays on a fixed plane | Direct port (billboard it) | 0.25 |
| 9 | Plasma ring (1347) | 1 | Additive noise filaments on a ring | Direct port | 0.25 |
| 10 | Nebula plane / black hole (1458) | 1 | Orion and Lagoon image plane, or a 120-step Schwarzschild null-geodesic integrator with disk crossing, Doppler and redshift, inline tone mapping | Split: image plane is a direct port; the geodesic integrator is a rewrite as a TSL full-screen pass at adaptive resolution | 2 |
| 11 | Planetary world dome (1700) | 1 | 96-step ray-marched terrain with 8-octave analytic-derivative noise, water with Fresnel, four palettes, Saturn rings, Earth disc, Bruneton sky | Rewrite as a TSL full-screen pass at reduced resolution with temporal upsampling | 3.5 |
| 12 | Cinema optics `ShaderPass` (418) | post | Chromatic aberration, 8-tap anamorphic streak, vignette | Direct port to a TSL post node (r187 has chromatic aberration; the anamorphic streak stays custom) | 0.5 |
| | Shared `BRUNETON_ATMO_GLSL_CHUNK` | | LUT lookup with analytic fallback (used by 5, 6, 11) | Port once as a TSL `Fn` | 0.5 |
| | `EffectComposer` + `UnrealBloomPass` + `OutputPass` | | ACES and Unreal bloom | Replace with the spike's `RenderPipeline` chain | 0.5 |
| | Spark `SparkRenderer`, 3 `SplatMesh` builders, `SparkControls`, `SparkXr` | | Local Bubble, nebula and terrain-mist splats; controls; WebXR | Local Bubble and nebula become volumes; the 1,800 hash-generated terrain-mist splats are dropped or become volume fog; controls and XR move to three | 2.5 |

Milestone 1 ported rows 1, 2, 4, 5, 6 and 12, the Bruneton chunk and the composer for the cislunar scene (section 10).

Totals: 8 direct ports, 2 rewrites (starfield, planetary world), 1 split (nebula image plane ported directly, black-hole integrator rewritten), 1 drop (particles), plus infrastructure. About 16 to 17 engineer-days for the port, plus about 30% for integration and visual QA, plus 5 to 8 days for the new physically based layers (catalog stars with GPU culling, dust map volume): roughly 4 to 6 weeks for one engineer. [inference]

## 10. Migration plan

Milestone 1 (steps 1 to 5, with the step 6 ports the cislunar scene needs) landed on 2026-11-04 on branch `claude/xenodochial-mclean-6796f5`. The WebGL2 and Spark renderer stays the default; `?renderer=webgpu` selects the new host, which draws the cislunar landing scene only. Everything after milestone 1 is still [proposed].

1. **Bump three in place. Landed.** `apps/web` pins three 0.187.0 and @types/three 0.187.0 exactly, on the WebGLRenderer and Spark 2.4; `THREE.Clock` became `THREE.Timer`, connected to the Page Visibility API. Typecheck, tests and the landing render pass, and the console no longer shows the `Clock` deprecation warning. [verified, measured]
2. **Contracts first. Landed.** `RenderLayerSchema` in [`packages/contracts/src/render-layer.ts`](../../packages/contracts/src/render-layer.ts), mirrored by [`services/contracts/render_layer.py`](../../services/contracts/render_layer.py) (Pydantic). A layer carries `kind`, `evidenceClass`, a one-line `disclosure`, `sources`, and `sourceAssetId` / `observationSolutionId`. The rules hold on both sides: an `observed` or `catalog_matched` layer must name its SourceAsset, a `catalog_matched` layer must name the observation solution with the verified WCS, and `procedural` and `camera_optics` layers must be `simulated`. Unknown fields and explicit nulls are rejected on both sides. The Python side writes `tests/fixtures/render_layer_contract.json`; the Zod tests parse it and compare enums and limits. PBT-LAYER-1 runs on both sides, and removing the always-simulated rule fails both halves (mutation check). `pnpm verify:contracts` now requires the schema. [verified]
3. **Renderer host. Landed.** [`webgpu/WebGpuHost.ts`](../../apps/web/src/spatial/webgpu/WebGpuHost.ts): `forceWebGL` via `?backend=webgl` (three also falls back by itself when WebGPU cannot start), `setAnimationLoop`, canvas CSS size with the device pixel ratio capped at 2, fixed drawing buffers for benchmarks, readback captures through an RGBA8 render target, `advanceNodeFrame()`, and GPU idle waits (`onSubmittedWorkDone`, or a fence on WebGL2). The host and the cislunar scene load on demand: the build puts them in their own chunk (726 KB, 227 KB gzip), and the default chunk contains no WebGPU backend code. [measured]
   - The frame-loop trap from section 8 reproduces in the app: rendered outside the loop without advancing the node frame, every frame after the first issues 1 draw call (the final quad) instead of 26. [measured]
   - At device pixel ratio 2, a 1280x720 CSS canvas gets a 2560x1440 drawing buffer; narrowing it to 900 CSS px gives 1800x1440 with the camera aspect following (1.25). [measured]
4. **Post chain. Landed.** [`webgpu/postChain.ts`](../../apps/web/src/spatial/webgpu/postChain.ts): `pass()` into HalfFloat, bloom, the cinema optics as a TSL node ([`cinemaOptics.ts`](../../apps/web/src/spatial/webgpu/cinemaOptics.ts): chromatic aberration, the 8-tap anamorphic streak, vignette), AgX and sRGB via `renderOutput`, then FXAA. The GLSL vignette called `smoothstep` with reversed edges, which WGSL leaves undefined; the node uses the identical `1 - smoothstep(0.18, 0.92, r2)`. Bloom uses the spike's settings (strength 0.35, radius 0.5, threshold 2.0), not the composer's 0.64 / 0.72 / 0.6: catalog stars now carry linear flux and their own Airy, halo and spike terms, and a star's core peaks near 318 x 10^(-0.4 m), so a 0.6 threshold put a second glare halo on every star brighter than magnitude 6.8 ([comparison](img/webgpu-migration/stars-before-and-bloom-choice.jpg)). [verified, measured]
5. **Stars. Landed for the cislunar scene.** [`stars/CatalogStarSpriteField.ts`](../../apps/web/src/spatial/webgpu/stars/CatalogStarSpriteField.ts) streams the same Gaia DR3 and Hipparcos 2007 tiles through `CatalogStarTileLoader` and draws the spike's analytic PSF as instanced sprites, one mesh per tile with its own culling sphere; 7,153 stars are resident in the landing view. Colour comes from the catalog colour index: Gaia BP-RP to B-V by inverting the Gaia DR3 documentation's relation (section 5.5.1, Table 5.9, valid for -0.5 < B-V < 3.5, scatter 0.066 mag), then the blackbody with that B-V (Ballesteros 2012, EPL 97, 34008, eq. 14), then Planck x CIE 1931 to linear sRGB at unit luminance. Colours are observed, not dereddened; a star without a colour index is drawn white. Exposure 650 gives a magnitude 6 star a core peak of 1.27 in linear radiance, against 1.3 at the centre of the WebGL point sprite. The WebGL `CatalogStarField` is unchanged. Compute culling for deeper tiles is still [proposed]. [verified, measured]
6. **Direct ports. Landed for the cislunar scene; the rest [proposed].** Sky dome as a background node that computes the dome's `SphereGeometry` UVs per pixel and wraps the u derivative at both seams (since replaced in both renderers by sampling by view direction, registered to the catalog stars: [SKY_PANORAMA_REGISTRATION.md](../science/SKY_PANORAMA_REGISTRATION.md)); Earth, atmosphere shell and Moon as node materials; the shared Bruneton lookup as a TSL `Fn` ([`brunetonAtmosphere.ts`](../../apps/web/src/spatial/webgpu/brunetonAtmosphere.ts)) whose loading fallback keys off an explicit ready uniform. The Earth's inline `x/(1+0.56x)` is gone, so AgX is the only tone map. Each layer is declared under the render-layer contract ([`cislunarLayers.ts`](../../apps/web/src/spatial/webgpu/cislunar/cislunarLayers.ts)) and the UI shows "Modeled scene · 6 layers", which expands to each layer's evidence class and disclosure ([screenshot](img/webgpu-migration/cislunar-webgpu-page-disclosure.jpg)). [verified, measured]
   - Changed on the way: the GLSL squared negative values with `pow(x, 2.0)`, which GLSL leaves undefined and WGSL evaluates as NaN, so the port squares explicitly. Spin and camera easing were per frame; they are now per second at the 60 Hz they were tuned at, so the look no longer depends on refresh rate. `?t=<seconds>` holds the scene clock (showcase drift evaluated at t, no spin) on both renderers, for matched snapshots. [verified, measured]
   - Dropped: the Moon's four octaves of hashed value noise added as "micro-crater" relief, terrain the LOLA map does not have. The Earth shader's procedural cloud, land and city-light detail went the same way on `main` in `a035536`, replaced by NASA imagery. [verified]
   - Not ported, because the landing never shows them: the `halpha` and `jwst_ir` band remaps, the spectral monocle, and the unrestored sensor veil of the sky dome; they warn once on the new host. The remaining scenes (photosphere, corona, ring, nebula plane) are [proposed], one landing scene at a time with matched `?t=` snapshots.
7. **Dust and nebulae.** Local Bubble and nebula as density volumes (voxelized splats for parity first, then the published dust map), half-res march, CCM89 reddening of everything behind them. [proposed]
8. **Ray-marched set pieces.** Planetary world and black-hole integrator as TSL full-screen passes with adaptive resolution. [proposed]
9. **Capture-site splats.** three `GaussianSplat` for up to about 1M splats; Spark behind a flag for larger captures until radix or three selects level of detail; move controls off `SparkControls`, and XR off `SparkXr` after a device check. [proposed]
10. **Remove WebGL-only code.** Drop Spark and the GLSL materials once parity is signed off; keep the WebGL2 fallback through `forceWebGL`. [proposed]
11. **Verification.** Per-regime readback snapshots (the host's `capture()`), headless frame-time budgets in CI with [`frameTimeBench.ts`](../../apps/web/src/spatial/webgpu/frameTimeBench.ts), and Safari and Windows runs before switching the default. [proposed]
12. **Art pass on the AgX look.** Decide contrast and black level against the measurements below (an AgX look with more contrast, or a lower floor in the sky dome), and whether the Moon's bright limb should keep the composer's bloom and streak. [proposed]

### Milestone 1 frame times, cislunar scene [measured]

Method as in section 4: headless Chrome 156 on the Apple M5 Max, frames from an async loop with the node frame advanced by hand, 60 warm-up frames, 240 serialized frames, 8 pipelined batches of 30. The camera follows the landing's showcase drift over one 14.96 s cycle per 240 frames (the clock is set per frame). Every catalog tile the path needs is loaded before timing. Three runs per backend; each cell lists the three medians.

| Configuration | 1920x1080 | 3840x2160 |
|---|---|---|
| WebGPU, pipelined median ms | 0.59, 0.60, 0.59 | 1.04, 1.04, 1.03 |
| WebGPU, pipelined p95 ms (worst run) | 0.68 | 1.09 |
| WebGPU, serialized median ms | 0.9, 0.9, 0.9 | 1.4, 1.4, 1.3 |
| WebGL2 fallback (`forceWebGL`), pipelined median ms | 0.51, 0.50, 0.52 | 1.45, 1.40, 1.41 |
| WebGL2 fallback, pipelined p95 ms (worst run) | 0.83 | 1.54 |
| WebGL2 fallback, serialized median ms | 1.4, 1.4, 1.4 | 2.0, 2.0, 2.0 |

Each frame is 26 draw calls and 134,193 triangles; JavaScript submit time was 0.2 ms on WebGPU and 0.1 ms on WebGL2. At 4K the scene uses about 1 ms of the 8.33 ms budget at 120 Hz. An earlier single WebGPU run measured 0.42 ms (1080p) and 1.02 ms (4K) pipelined, within the run-to-run noise noted in section 4. `performance.now()` is coarsened to 0.1 ms in this Chrome, so serialized values come in 0.1 ms steps. The WebGL renderer (`SparkPortalRenderer`) was not timed this way: it has no hook to render frames outside its own requestAnimationFrame loop.

### Milestone 1 before and after, cislunar scene [measured]

`?scene=0&freeze=1&t=4` at 1920x1080, canvas only (page overlays hidden): [WebGL renderer, ACES](img/webgpu-migration/cislunar-webgl-aces-before.jpg), [WebGPU host, AgX](img/webgpu-migration/cislunar-webgpu-agx-after.jpg), [Moon, both](img/webgpu-migration/moon-before-after.jpg).

- The same TSL on the WebGL2 backend matches WebGPU: mean difference 0.009 display code values, 0.01% of pixels differ by 8 or more.
- The new host against the WebGL renderer: 99.9% of pixels differ by 2 or more (the tone curve changes every pixel), 49.8% by 8 or more. Mean display luminance and its standard deviation per region: Moon sunlit side 67 to 75 (31 to 25), Earth day side 61 to 72 (50 to 42), Milky Way band 16 to 21; the dark sky's median goes from (1, 2, 7) to (1, 4, 14). AgX is brighter and flatter in the midtones and lifts the sky dome's constant blue floor that ACES crushed.
- Catalog stars are now coloured by temperature with white cores; the WebGL point sprites were pale and small. The Moon loses the bloom and streak on its bright limb (threshold 2.0) and the invented micro-relief.

## 11. What could not be verified

- Safari 26, Firefox, Windows, Android and Linux behavior and performance; only Chromium on this Mac was measured.
- Display-synced frame pacing at 120 Hz: the headless run is not display synced and the pane was hidden.
- Real star catalogs (the 100k stars are a synthetic perf fixture) and catalog sizes beyond 100k.
- Calibrated dust: the volume's densities come from the baked presentation opacities in `lb_splats.json`, not extinction per parsec; which dust-map product underlies the O'Neill et al. 2024 geometry was not checked.
- The user's capture-site reconstruction (none exists in the repo) and splat level of detail on WebGPU.
- Correctness of TRAA, depth of field and motion blur on splat, sprite and volume layers.
- radix's self-reported speedups over Spark, and the root cause of three's p95 spikes on the 500k fixture.
- Provenance and license of `apps/web/public/splats/*.spz`.
- WebXR with the WebGPU backend on headsets.
- Milestone 1: the WebGL renderer's frame times by the same method (it renders only from its own requestAnimationFrame loop), and the new host in any browser but headless Chrome 156 on this Mac.

## 12. Findings outside the spike's scope

- Fixed on `main` after the spike: the app's Local Bubble loader drew 2,811 of the 14,281 rows and added a deterministic jitter of `(sin(...) - 0.5) * 0.28`, which ranges from -0.42 to +0.14 and shifted the whole cloud by about -0.14 units per axis on average. Stride 2 dropped every other row, and a cylinder of radius 4.55 around the splat group's z axis, which runs through the star, cut out everything inside it: 63% of the shell's rows. The loader now draws every row at its own position, and the nebula loader no longer drops every other row either. [verified]
- Also fixed: the bake gave the Local Bubble colours named after emission lines (H-alpha, [O III]) and an opacity ramp, both shaped around that cylinder, although the model carries no density or emission per point. It now keeps the k-NN PCA orientation and footprints and writes one presentation colour and one opacity, 0.0039, which Spark's 8-bit encoding stores as its smallest step, 1/255. The spike's port (`apps/webgpu-spike/src/data/localBubble.ts`) follows the fixed loader; check out `b257439` to reproduce the measured setup. [verified]
- The landing showcase logs `THREE.Clock` deprecation warnings on three 0.187. Fixed in milestone 1 (`THREE.Timer`). [measured]
- Found while porting the cislunar scene in milestone 1:
  - The atmosphere LUT (`bruneton_atmo_lut.png`) is not a Bruneton and Neyret (2008) precomputed model. `scripts/bake_bruneton_lut_and_splats.py` integrates single scattering (Rayleigh, Cornette-Shanks Mie, ozone) over 32 steps, adds hand-set "sapphire zenith" and "amber crimson limb" colour terms, and stores 1 - exp(-1.18 x) in 8 bits, so the table is tone-mapped appearance, not radiance. The Earth and its shell are disclosed as modeled. [verified]
  - The sky dome drew the ESO panorama and the NASA star map at an arbitrary rotation (0.18, 0, 0.32), so neither lined up with the catalog stars. Its `hAlphaKnots` term is a colour boost of the panorama's red excess, not H-alpha data, and the `halpha` and `jwst_ir` band modes synthesize band images from RGB. Fixed after milestone 1: both renderers now sample the dome by view direction, registered to the catalog stars within about a texel. The panorama needed a fitted solution, since it is not in exact galactic coordinates. The star map turned out to be a NASA SVS plot of catalog stars, so removing it is proposed. Details, and the open `hAlphaKnots` and band-mode questions, are in [SKY_PANORAMA_REGISTRATION.md](../science/SKY_PANORAMA_REGISTRATION.md). [verified, measured, proposed]
  - Texture sizes do not match their names or the shader: `nasa_starmap_4k.jpg` and `lro_moon_2k.jpg` are 1024x512, and the Moon's bump step assumes a 2048x1024 relief map, so it spans half a texel (kept in the port for parity). Provenance is recorded for the Blue Marble and Black Marble textures (`earth_texture_sources.json`) and, since, for the panorama and star map (`sky_texture_sources.json`); the cloud and ocean masks, Moon maps and Moon mesh have none. [verified]
  - The WebGL loop spins the Earth and Moon and eases the camera by fixed amounts per frame, so a 120 Hz display plays the landing twice as fast as a 60 Hz one; the WebGPU port uses per-second rates. [verified]
  - The Earth and the Moon are lit from different view-space directions, so their lighting is a composition, not one Sun; both layers say so in their disclosures. [verified]

## 13. Reproducing

```sh
pnpm install
pnpm --filter @albedian/webgpu-spike dev              # http://127.0.0.1:5191
# interactive: ?splats=three|radix|none&volume=off|skip|noskip|skip-half&ext=1|4|8|16&res=3840x2160
# benchmark:   ?bench=suite[&benchres=1920x1080,3840x2160][&backend=webgl][&quick=1]
SPIKE_BENCH_OUT=/some/dir pnpm --filter @albedian/webgpu-spike dev   # where results are written

pnpm dev                                              # apps/web, milestone 1 host
# WebGPU host:    ?scene=0&freeze=1&renderer=webgpu[&backend=webgl][&t=4]
# WebGL renderer: ?scene=0&freeze=1[&t=4]            (t holds the scene clock on both)
# In the page:    await window.__albedianCosmos.runBench()          frame times (section 10)
#                 await window.__albedianCosmos.capturePngDataUrl() readback still
```
