# Frontier AI Media Restoration: Research and Product Foundation

**Research cutoff:** October 31, 2026  
**Competition:** Kiroween 2026  
**Decision:** Conditional GO for a narrowly scoped *evidence-aware night video restoration workspace*; NO-GO for a broad, multi-model autonomous editor as the first build.  
**Status:** External research and proposed design, not an implementation or firsthand performance test. No supplied original Sahara footage was tested.

## Reading Guide

- **Verified:** Supported by linked primary research, official technical documentation, or observed repositories.
- **Inferred:** A technically plausible product or architectural conclusion, not experimentally validated.
- **Open:** A question requiring local tests or further verification.
- **Proposed:** A concrete design choice recommended for prototyping, not a claim of existing functionality.

The essential claim is not that AI can recover invisible stars or scene geometry. It is that a system can make the *best defensible improvement from available evidence* easier to find, compare, revise, and export. Low-light restoration has hard physical limits. On-device RAW methods, commercial diffusion models, and compressed-media enhancement are not interchangeable.

---

# D1. Executive Verdict

## Strengths

1. **Real source problem:** user-recorded night video from ordinary cameras frequently captures less than the observer experienced. A side-by-side restoration visibly explains the problem.
2. **Technically grounded opportunity:** temporal redundancy, sensor-aware denoising, exposure/tone processing, and restoration networks are established research areas. Careful *selection and measurement* of methods can be useful without inventing a new foundational model.
3. **Interaction opportunity:** high-quality photo applications often present many model and slider decisions; a media-first system that automatically tests sensible alternatives and exposes exact differences can reduce trial-and-error.
4. **Compelling demo:** authentic footage, real execution, synchronized A/B, audio preservation, and a reproducible MP4 can be tested by judges.

## Weaknesses

1. **Photons cannot be invented:** if useful signal never reached a sensor or was destroyed by processing/compression, restoration must infer rather than recover.
2. **Technological distinctiveness is not yet proven:** Topaz, Resolve, and ComfyUI individually cover most of the proposed feature list. The hypothesized differentiator is *the integrated adaptive experiment loop and truth-oriented evidence*, not merely neural enhancement or editable graphs.
3. **Benchmarks are not camera-transfer guarantees:** RAW datasets and controlled low-light pairs differ greatly from an auto-exposed, compressed, handheld Meta video of dunes, stars, and moving people.
4. **Models are expensive and fragile:** pre-trained weights can require CUDA, fail on Apple MPS, require unknown licenses, create flicker, or synthesize detail.
5. **The permanent graph can ruin the interface:** nodes should be inspectable, not the first thing a newcomer sees.

## The verdict and its falsifiers

**GO** if a real 5–15-second low-light consumer-video test shows a defensible improvement over a strong ordinary baseline, exported with synchronized audio, and demonstrates a repeatable multi-attempt selection process. **Pivot** to an evidence-based *media experiment workspace* if actual night restoration is only marginally better than straightforward tone/denoise processing. **Stop** if results are regularly hallucinated or weak compared with a simple commercial tool, while the extra workflow complexity does not produce appreciable user benefit.

**Recommendation:** product Option A's immediate clarity, Option C's experimental credibility, and Option D's hybrid interaction: a **night-first adaptive restoration workspace with an inspectable experiment runtime**. Do not build a generalized studio in the first month.

---

# D2. Frontier Technical Research

## 2.1 Physical imaging limits

A useful sensor approximation is `observed electrons = scene photons + shot noise + read noise + other artifacts`. Shot noise generally follows Poisson statistics; for a photon count N, ideal shot-noise-limited SNR grows approximately as sqrt(N). Combining *N independent aligned observations of the same static scene* can yield an SNR improvement up to sqrt(N), subject to read noise, alignment, motion, saturation, compression, changing lighting, and correlations. Thus 16 valid independent observations might theoretically deliver roughly 4x SNR in a shot-noise-dominated static region; this is **an idealized calculation, not a guaranteed 4x improvement in a compressed handheld recording**.

A source value at digital black may be clipped, quantized, heavily smoothed by the camera ISP, or so near the noise floor that spatial structure is statistically indistinguishable. Increasing gain in post raises noise and codec artifacts along with signal. Generative models can introduce believable detail but cannot prove it occurred at the location and time recorded. Stars absent from sensor observations cannot be authenticated by predicting where stars *ought* to be. A star catalog could predict celestial positions only with reliable camera location/orientation/time/calibration; that is an external reconstruction, not recovery of recorded light.

**Operational definitions:**

- **Observed-data processing:** traditional ISP/tone mapping, alignment, filtering, aggregation and other computations from source pixels. This does *not* mean each resulting edge is a physically verified fact.
- **Prior-assisted restoration:** learned methods conditioned on a source; output may be visually convincing while some detail is uncertain.
- **Synthetic reconstruction:** adding or substantively replacing semantically unobserved content, possibly with an external generative model. Keep separate, default disabled.

No practical automatic classifier can prove per-pixel authenticity from a single source clip. Instead record *algorithm family, inputs and uncertainty flags*, show source-aligned crops, run consistency tests, and disclose what the metrics do and do not mean.

Core foundations: [Google HDR+ burst photography, SIGGRAPH Asia 2016](https://research.google/pubs/burst-photography-for-high-dynamic-range-and-low-light-imaging-on-mobile-cameras/); [Learning to See in the Dark, CVPR 2018](https://cchen156.github.io/SID.html).

## 2.2 Research matrix: highest-relevance candidates

| Work, date, authors | Input / task | Evidence and reported setup | Code / integration | Limitations / relevance |
|---|---|---|---|---|
| **AIM 2025 Low-light RAW Video Denoising Challenge**, ICCV Workshops 2025, Yakovenko et al. | Ten-frame smartphone linear RAW sequence to denoised tenth Bayer frame | **756 sequences, 14 sensors, 1/5/10 lx, 1/24–1/120 s**; high-SNR burst references; PSNR/SSIM ranking | [Paper](https://openaccess.thecvf.com/content/ICCV2025W/AIM/html/Yakovenko_AIM_2025_Low-light_RAW_Video_Denoising_Challenge_Dataset_Methods_and_ICCVW_2025_paper.html); challenge dataset access requires checking | Superb sensor-level research reference, **not directly compatible** with post-ISP Meta MP4. Challenge paper does not establish transferable weights, licensing, or consumer-video performance. |
| **Low-Light Video Enhancement via Spatial-Temporal Consistent Decomposition**, IJCAI 2025, Xiaogang Xu, Kun Zhou, Tao Hu, Jiafei Wu, Ruixing Wang, Hao Peng, Bei Yu | RGB video with temporal supervision | Authors report on SDSD indoor/outdoor, SMID, DID and DAVIS; SDSD outdoor **26.32 dB PSNR**, versus **25.17 dB** for best listed baseline in their protocol; methods retrained on common splits | [Paper](https://ijcai.org/proceedings/2025/238), [repo](https://github.com/xiaogang00/LLVE_STCD) | Good research candidate for compressed video; repository availability does not itself establish pretrained weights, license, Mac speed, or cross-sensor reliability. |
| **RetinexMCNet**, ICCV 2025, Meiao Wang, Xuejing Kang, Yaxi Lu, Jie Xu | sRGB low-light video | Two-stage illumination/reflectance processing and memory controller, reports temporal-quality improvements on common sRGB datasets | [Paper](https://openaccess.thecvf.com/content/ICCV2025/html/Wang_RetinexMCNet_A_Memory_Controller_Dominated_Network_for_Low-Light_Video_Enhancement_ICCV_2025_paper.html) | Published paper's code URL is a placeholder (`xxx/...`); reproducible integration and weights **not verified**. Do not schedule as core dependency. |
| **DarkIR**, CVPR 2025, Daniel Feijoo, Juan C. Benito, Alvaro Garcia, Marcos V. Conde | Single low-light image, joint light/noise/blur restoration | Repository reports **27.30 dB PSNR on LOLBlur for DarkIR-l**; 12.96M parameters and 27.19 GMACs for that variant; datasets include LOL-Blur, LOLv2, LSRW | [Repo, checkpoints](https://github.com/cidautai/DarkIR) | Excellent photo and sampled-frame baseline; independent per-frame operation risks video flicker. Public setup references PyTorch 2.5.1 and CUDA 12.4. Check checkpoint and repository licenses. |
| **BVI-Mamba**, April 26, 2026 arXiv preprint, Guoxi Huang, Ruirui Lin, Yini Li, David R. Bull, Nantheera Anantrasirichai | Video low-light / underwater enhancement | Visual state-space blocks with feature alignment and enhancement; authors report efficiency/perceptual gains versus CNN/transformer baselines | [Preprint](https://arxiv.org/abs/2604.23655), [code](https://github.com/edhuang1/BVI-Mamba) | **Preprint**, not confirmed peer-reviewed here. Runtime, weights, hardware feasibility and license require repository-level verification. Promising candidate, not guaranteed winner. |
| **VSRELL**, CVPR 2026, Yanming Hui et al. | Joint low-light video enhancement and super-resolution | INCO illumination/noise co-optimization and ISFP feature propagation; reports improved consistency and detail | [Paper](https://openaccess.thecvf.com/content/CVPR2026/html/Hui_VSRELL_A_Simple_Baseline_for_Video_Super-Resolution_and_Enhancement_in_CVPR_2026_paper.html) | Joint super-resolution can complicate fidelity; weights, operating conditions and practical code availability not confirmed. Not essential for MVP. |
| **Efficient Real-Time RAW-to-RAW Denoising for Extreme Low-Light UHD Video**, CVPR 2026, Charantej Pochimireddy et al. | Sensor RAW UHD video, mobile ISP processing | Targets <33 ms / frame and <250 mA design constraints for real-time 30 fps; optimized model architecture and quantization | [Paper](https://openaccess.thecvf.com/content/CVPR2026/html/Pochimireddy_Efficient_Real-Time_Raw-to-Raw_Denoising_for_Extreme_Low-Light_Ultra_HD_Video_CVPR_2026_paper.html) | Valuable future capture/hardware reference; downstream app cannot assume access to sensor RAW or proprietary ISP insertion. |
| **L²DGS**, CVPR 2026, Ashish Kumar, Rajagopalan N Ambasamduram | sRGB low-light dynamic video for 4D Gaussian-splat reconstruction | Geometry/time-aware self-supervised reconstruction; introduces L²DyV | [Paper/code](https://github.com/akumar005/L2DGS) | Interesting future 3D direction, but high complexity and possible ambiguity; **reject for hackathon scope**. |
| **Learning to See in the Dark**, CVPR 2018, Chen Chen et al. | Camera-specific RAW short exposure to long-exposure target | Demonstrates significant RAW-domain gains in photon-limited conditions | [Code, MIT](https://github.com/cchen156/Learning-to-See-in-the-Dark) | Its own authors explicitly warn pretrained weights **do not work on JPG/PNG or arbitrary sensor data**. Include as control/reference, not as Meta-video engine. |

### Interpretation of benchmark numbers

PSNR values shown above use **different datasets, reference formats, sensor distributions and evaluation procedures**. Do not rank these papers by comparing their isolated headline numbers. The IJCAI paper explicitly retrained comparison methods on a unified split. Any local leaderboard must rerun all candidates on identical frames, alignment, color space, dynamic range, masks, and paired references.

## 2.3 Candidate algorithm families for real footage

**Reliable, low-complexity initial baselines:** controlled transfer functions (gamma/log/filmic tone), exposure compensation with clipping monitoring, white-balance correction, spatial denoise, classical temporal denoise, scene-stabilized accumulation for static regions, edge-preserving guided filtering, and modest deblocking. Implemented with FFmpeg, OpenCV and/or targeted native code. Their strength is predictable execution, not automatic truth verification.

**Neural candidates:** use one vetted low-light **video** model for the first benchmark; evaluate a photo model (DarkIR) separately or with temporal mitigation. Model applicability is conditional on runtime compatibility, weights availability, licensing, memory, expected color space and measured gains. Do **not** combine every paper into one multi-model workflow.

**Generative candidates:** diffusion and latent restoration should be an optional explicitly labeled creative mode, disabled under a user's instruction to avoid inventing stars or changing people. Even a vendor's “precise” setting is a product claim, not independent proof of pixel-level truth.

## 2.4 Dataset strategy

| Dataset / source | What it supports | What it does NOT establish |
|---|---|---|
| **BVI-RLV**, 2026, IEEE TMM accepted October 3: [paper](https://research-information.bris.ac.uk/en/publications/bvi-rlv-a-fully-registered-dataset-for-low-light-video-enhancemen/), [project/data](https://lrr-rachel.github.io/BVI-RLV/) | 31,800 paired 1920×1080 frames, 40 controlled scenes, 25 fps, carefully registered moving scenes. Suitable for full-reference evaluation. | The project page describes studio lighting around **3–5 and 10–14 lux** in reduced-light captures, not moonless Sahara star fields. Also check dataset rights before redistributing samples. |
| **AIM 2025 RAW challenge** | Capture-aware Bayer denoising; controlled sensor and illumination variables | Cannot be substituted for real RGB/HEVC consumer video benchmark. |
| **SDSD / SMID / DID** (discussed in [IJCAI 2025 study](https://ijcai.org/proceedings/2025/238)) | Established low-light video comparisons, motion and paired references | Inconsistent captures/splits/alignment among studies; retrieve the exact versions and licenses first. |
| **LOLBlur, LOLv2, RealLOLBlur, LSRW** ([DarkIR](https://github.com/cidautai/DarkIR)) | Still-image enhancement, noise/blur baselines | Not sufficient for temporal video consistency. |
| **Private controlled capture** | Night landscape/stars, moving people, bright lamp and dark subject; capture repeated video plus long-exposure RAW/still references where feasible | Requires careful geometric and temporal registration; cannot claim perfect ground truth for a dynamic scene. |

### Serious experiment ordering

First test **compressed real video**, rather than a neat sRGB benchmark alone: 1) original; 2) modest tone+denoise baseline; 3) motion-aware temporal strategy; 4) one neural model; 5) strong commercial reference. Score paired clips where available; for unpaired consumer footage use source-consistency, temporal artifacts, user preferences, and honest ambiguity statements. Do not optimize only NIQE/BRISQUE: such no-reference metrics measure learned image naturalness/distortions, not whether alleged recovered stars and faces were really present. See [MathWorks IQA documentation](https://www.mathworks.com/help/images/image-quality-metrics.html).

---

# D3. Competitive Landscape

**A rigorous competitor thesis must acknowledge what already exists.**

| System | Verified overlap | Remaining testable gap | Strategic implication |
|---|---|---|---|
| **Topaz Video / Astra** | Nyx targeted denoise; multiple enhancement filters; Starlight Mini diffusion; Astra distinguishes *creative* from *precise* video enhancement. Starlight local processing can be resource-intensive. [Topaz Nyx](https://docs.topazlabs.com/topaz-video/filters/denoise), [Astra](https://www.topazlabs.com/astra), [Starlight](https://docs.topazlabs.com/topaz-video/project-starlight-series/starlight-mini) | An automatically constructed **reproducible experimental plan** with synchronized transparent evidence and graph-state edits was **not established** by the reviewed product documentation; do not treat absence of docs as proof of absence. | **Primary quality benchmark**; if our output is worse than a simpler Topaz run and our experiment interface adds little, the business case weakens severely. |
| **DaVinci Resolve 21 / Fusion** | Professional color, spatial/temporal noise reduction, AI UltraNR, node-based Fusion; Resolve 21 adds AI features and a Photo page. [Official announcement](https://www.blackmagicdesign.com/media/release/20260414-01) | A stripped-down importer that autonomously evaluates night-specific attempts with no editing expertise; test against Resolve's actual newest workflow. | Do not sell “nodes” or “AI noise reduction” as invention. |
| **ComfyUI** | Open-source node workflow engine for image/video/audio/3D; reproducible graphs, model composition. [About Comfy](https://support.comfy.org/articles/9806306061-about-comfy) | Media-first consumer flow with objective/user preference evaluation, safe default fidelity rules, automatic experiments, and polished audio-preserving export. | Most direct **architectural** competitor. Strongest product differentiator must be usability plus evaluative logic, not an arbitrary DAG. |
| **Adobe Premiere** | Timeline editing and Firefly-driven generative extension of clips/audio. [Official 2026 guide](https://helpx.adobe.com/premiere/desktop/edit-projects/edit-with-generative-ai/generative-extend-overview.html) | Automated and inspectable low-light experiment selection is not established by reviewed docs. | Different primary workflow, but still a strong substitute for experienced editors. |
| **CapCut and general AI enhancers** | Consumer-friendly enhancement and auto-editing exist broadly; specific October 2026 capability matrix not independently verified in this review. | Can a reproducible, cautious, multi-attempt workflow offer durable value over one-click filters? | Treat as functional substitutes; run hands-on comparison before market claims. |
| **Camera Night modes / ProRAW** | Google HDR+ uses aligned RAW bursts; Apple ProRAW can capture with image-fusion advantages. [Google HDR+](https://research.google/pubs/burst-photography-for-high-dynamic-range-and-low-light-imaging-on-mobile-cameras/), [Apple](https://developer.apple.com/documentation/avfoundation/capturing-photos-in-raw-and-apple-proraw-formats) | Imported video from smaller, more restricted cameras and recovery of existing recordings. | In good conditions, **better capture will beat post-processing**. Future companion capture path, not current core. |

### What cannot be claimed yet

- “No competitor has automated experimentation”: **not established**. A hands-on comparison is still required.
- “Higher quality than Topaz”: **unproven**.
- “Scientifically verified recovery of true details”: **overclaim**, even with source consistency tests.
- “LLM orchestration is novel”: **false as a general claim**. Novelty must be in constraints, user experience and feedback coupling.

---

# D4. Core Computing Primitive

## Proposed name: **Media Experiment Runtime**

**Definition:** a content-addressed execution system that turns an imported media asset and an enhancement goal into versioned restoration attempts, makes results comparable against a shared source, and preserves all transformations through export.

This is a better core than “Verifiable Media Transformation Runtime”: **provenance is verifiable; restored scene truth often is not**.

### Structural invariants

1. Every attempt references an immutable `source_asset_id`, source hash and exact source time span.
2. The typed DAG is the **execution source of truth**, never an illustration unrelated to real processing.
3. Each node records operation ID/version, parameter schema, input hashes, execution environment, dependency versions and output hashes.
4. A branch fork is immutable; tweaking a slider creates a new attempted output with a parent reference; affected downstream nodes are invalidated.
5. Shared upstream steps reuse a cache keyed by source content, operation, canonical parameters and model artifact hash.
6. Metrics always store evaluation method, crop/mask, reference availability and validity conditions.
7. The source audio is not passed through visual enhancement stages.
8. Every output export records video codec, audio codec, offsets, frame rate, color-space handling and mux validation.
9. An unsafe transformation class (e.g. synthetic detail) cannot enter a strict-fidelity graph without explicit user approval.
10. A user command changes actual project/graph parameters or is rejected with an explanation; never animate a nonexistent execution.

### State transition

`Imported → Diagnosed → Attempt Plan → Queued → Running → Evaluating → Compared → Accepted → Exported`

Each attempt may independently enter `Failed`, `Cancelled`, or `Superseded`. These are data states, not eye-candy.

### Why an experiment runtime instead of a generalized agent system?

It provides a concrete abstraction for **multiple defensible processing hypotheses applied to the exact same underlying source**. A prompt-based LLM can suggest a *goal* or constrained graph changes. The DAG validator, worker, cache and metrics are deterministic software. A neural model improves pixels, but it does not get to invent execution metadata or pronounce its own visual truth.

### Improvement flywheel

Immediate: per-clip method selection, cached reruns, paired human preferences and reusable processing recipes. Potential later: learn a ranking policy from consented source/attempt/preference triples with stratified device/scene datasets. **Not yet justified:** self-training on generated results without independent references; automatic claims of “truth learned from feedback.”

---

# D5. End-to-End User Journey

## Scenario A: Import

**0–1 s:** the uploaded original stays visually dominant; a poster frame is displayed immediately. File name, duration, camera/codec where available, and the original audio track are identified. Do not fabricate a processing preview.

**1–5 s, conditional on actual compute:** behind the same player, ingest/probe checks dimensions, transfer function, noise, exposure histogram, codec, motion and scene boundaries. UI changes `Analyzing` to `Ready to Restore` only when real analysis completes. If metadata inspection fails, provide a usable manual path.

**First visible choices:** `Restore`, `Compare`, `Original`, a small `Advanced`/`Processing Graph` entry. On a heavy dark Sahara clip the user can issue “Recover genuine detail. Keep people unchanged. Don't invent stars.”

## Scenario B: Autonomous attempts

A constrained policy selects a **cheap baseline** first, then one motion-aware strategy and one vetted neural candidate if hardware and source permit. It creates attempt cards with `Queued / Processing / Evaluating / Ready / Failed`, actual timestamps, and a relevant source-aligned thumbnail. The selected branch's preview appears in the same player, not in a separate dashboard.

Possible strategy (illustrative, not fixed):

`Original 00:05–00:15` → `[Decode + color normalize]` → branch A `[Exposure + mild denoise]`, branch B `[Motion estimate + temporal denoise + tone]`, branch C `[Vetted RGB-video model]` → `[Evaluate]`.

The planner must avoid a star-hallucination model for the above fidelity constraint. It can display `No reliable sky detail found` rather than claiming success.

## Scenario C: Voice refinement

“Stronger denoise in the background, but keep faces natural.”

1. Speech yields transcript plus structured intent: `modify(region=background, operation=denoise, intensity=up, constraint=preserve_face_texture)`.
2. Vision segmentation/motion mask locates faces and background; model uncertainty is explicitly reported when mask is unreliable.
3. Graph validator selects an allowed denoiser and adds a feathered matte/merge step with temporal smoothing.
4. A new branch and graph diff are committed; only invalidated nodes run; prior result remains available and undo reselects its immutable branch.
5. Preview uses identical start time; compare immediately shows differences in people/sky/background.
6. If the system cannot confidently separate faces it asks one focused clarification or offers a visible manual mask.

## Scenario D: Compare

Every comparison shares one canonical source timecode and identical zoom/ROI geometry. The user can toggle synchronized playback, vertical wipe, 2-up, 100% detail crop, and blind A/B (labels hidden). A result summary says `Cleaner shadows; mild face smoothing; 1.8× processing time` **only when actual evaluations support those statements**. Example figures must not appear as measured values in a real demo until collected.

## Scenario E: Export

A normal user sees `Export Video`; expert actions disclose `Video Codec`, `Original Audio`, `Processing Manifest`, and optionally `Comparison Reel`.

For video: render the chosen branch at stable source timestamps, encode video, remux or otherwise preserve original soundtrack where compatible, probe both streams, confirm nonempty decodable video, correct duration and audio/video PTS alignment. Store source hash and output manifest. When original audio cannot be stream-copied into the selected container, preserve a separate original audio copy and disclose any required transcoding rather than falsely advertising bit-identical audio.

---

# D6. UI/UX Research and Design Specification

## Choose the **media-first hybrid adaptive workspace**

Comparison of four hypotheses, design assessment (1 weak to 5 strong; **subjective proposed ratings, not empirical UX results**):

| Model | First-use comprehension | Research inspection | Video attention | Build complexity | Verdict |
|---|---:|---:|---:|---:|---|
| Permanent processing graph | 2 | 5 | 2 | 4 | Reject default |
| Experiment-space-first canvas | 3 | 4 | 4 | 4 | Useful expanded mode |
| Media-first single surface | 5 | 2 | 5 | 2 | Too little researcher depth alone |
| **Hybrid: media-first + inspectable experiments** | **5** | **5** | **5** | **4** | **Recommend** |

### Spatial hierarchy (desktop)

```text
┌────────────────────────────────────────────────────────────────────┐
│  PROJECT / Sahara Night        Original | Restore | Compare  Export │
├─────────────────────────────────────────────────────────┬──────────┤
│                                                         │          │
│           LARGE VIDEO PLAYER                            │ INSPECT  │
│           Current source-aligned result                 │          │
│           [Before/After wipe, contextually]             │ Analysis │
│                                                         │ Denoise  │
│                                                         │ Metrics  │
│                                                         │          │
├─────────────────────────────────────────────────────────┴──────────┤
│ TIMELINE: [00:00────── people ───── landscape ── sky ─────────]      │
│ ATTEMPTS:  [Original] [Natural] [Temporal] [Neural] [ + Experiment ]│
├────────────────────────────────────────────────────────────────────┤
│  [Mic]  Ask to refine…                                        [↵] │
└────────────────────────────────────────────────────────────────────┘
```

**Default state:** 70–80% of visual emphasis on media, not nodes. Right inspector closed until a result or diagnostic is selected. A narrow bottom experiment strip replaces a permanent graph. `Processing Graph` opens as an overlay/alternate perspective with thumbnails and dependency edges.

### Five intentional canvas states

1. **Import**: original fills player; ingestion metadata enters subtly.
2. **Diagnose**: defects appear as small inspectable annotations associated with footage and time ranges. The first frame stays visible.
3. **Restore**: 2–3 attempted branches enter a compact bottom strip; actual processing jobs display determinate progress only when a known denominator exists; otherwise show `Processing` and elapsed time.
4. **Compare**: player becomes synchronized split/wipe. Branch cards are ordered by meaningful criteria (quality and fidelity, speed), not fake composite scores.
5. **Inspect**: a `Processing Graph` overlay shows actual node inputs/outputs and event trace, with an obvious back to media.

### Interaction decisions

- Clicking any attempt changes the current player to the *same timestamp*.
- Dragging the before/after handle reveals pixels at the exact same coordinates; canonical crop and zoom are shared.
- Clicking a metric highlights its measured region, dataset or time span.
- Graph modifications use validated parameter controls and fork; drag-reorder only permitted where dependencies remain valid.
- Undo changes the selected branch/version, not necessarily deletion of persistent work.
- Voice is optional; text editing, accessibility shortcuts and direct manipulation must cover the same supported commands.
- Single subtle execution indicator rather than multiple decorative agent cursors. Animated edges are allowed only while real jobs emit lifecycle events.

### Visual treatment and tokens

- Surfaces: neutral near-black editing room; content obtains all visual emphasis. Light mode optional; no neon/gradient wallpaper.
- Type: system sans; 12–13 px metadata, 14–16 px controls, ~22–28 px headings only when necessary; consistent tabular numbers for time/metrics.
- Layout: 8-point grid, 16–24 px panel gutters, at least 44 px touch targets on small screens; rounded elements used sparingly.
- Motion: 120–220 ms for UI transitions; no animated fake progress; reduced-motion preference supported.
- Accent: single restrained selection accent; distinct accessible status cues with icon/text (not color only).
- Accessibility: keyboardable timeline/compare slider, captionable voice transcripts, ARIA labels, visible focus, no reliance on audio instructions, voice confirmation on risky synthetic actions.
- Example exact copy: `Original`, `Natural`, `Temporal`, `Compare`, `Processing Graph`, `Preserve Audio`, `Export`, `No Reliable Detail`, `Reference Unavailable`.

### UI precedents and licenses

- **React Flow / xyflow:** reliable typed node editor substrate; confirmed MIT for core. [Official](https://reactflow.dev/). Recommended only for advanced graph overlay.
- **tldraw:** broad unconstrained infinite canvas; **commercially licensed in production**; excellent idea prototyping but less appropriate for authoritative typed media-execution DAG. [FAQ](https://tldraw.dev/faq).
- **Affect UI:** `Agent Graph` and `Agent Cursors` are attractive interaction references; `Agent Graph` is listed under Pro; free elements are separately MIT. Do not scrape/copy paywalled code. [Component](https://www.affectui.com/components/agent-graph), [license/pricing](https://www.affectui.com/docs).
- **Realtime Whiteboard:** constrained voice commands→editable tldraw; useful demonstration of validated tool schema, but canvas output is not real media processing. [Repo](https://github.com/theCampel/realtime-whiteboard).
- **Speech-to-Whiteboard:** voice-to-semantic layout with structured graph state; concepts can inspire command mapping, not restoration algorithms. [Repo](https://github.com/halvorot/speech-to-whiteboard).
- **Voiceboard:** speech-to-Mermaid diagram; demonstrates speech-driven editing but not pixel execution. [Repo](https://github.com/DVidal1205/voiceboard).
- **ComfyUI:** useful for workflow representation but its dense graph should not define the first screen. [Official](https://support.comfy.org/articles/9806306061-about-comfy).
- **Resolve/Fusion/Nuke/TouchDesigner:** professional node inspection paradigms; borrow typed connections, selected-node inspector, timeline provenance. Resist borrowing default complexity.

**Design principle:** the extraordinary part is watching your *actual footage* become clearer while its alternative attempts remain inspectable. The node animation is secondary.

# D7. Processing and Orchestration Architecture

## The proposed stack

**Frontend:** React, TypeScript, one custom media player, synchronized timeline and A/B controls, lightweight state layer, and React Flow only for `Processing Graph` inspection. Prefer a browser-delivered app for accessible judging over a Mac-only native shell.

**Service API:** Python/FastAPI or an equivalent thin service for import, probe, plan validation, run submission, snapshots, events, metrics, and export. Exact language is less important than one authoritative data model.

**Processing:** FFmpeg for decode/encode/stream probing, OpenCV/scikit-image for deterministic filters and tests, optional PyTorch backend for *one* vetted RGB video model. Mac-local MPS is acceptable for development only after a smoke test; CUDA-targeted research repositories may need adaptation or an optional CUDA GPU worker.

**Persistence:** SQLite for a single-judge demo is adequate; project manifests and media in a local/content-addressed directory or object storage; no mandatory Redis/Postgres until measured need. For a remotely accessible judge app, use a durable worker plus persistent object storage and job records, with limits on file size and concurrent jobs.

**Compute:** local CPU/MPS processing for deterministic baseline and inexpensive previews; optional cloud CUDA GPU only if the neural model passes real-footage evaluation. No unsupported statement of precise GPU cost is made. Keep a reproducible CPU-only path for judges.

**Voice:** speech→intent→validated graph transaction; begin with text command parity, add real-time voice after actual restoration works. A hosted realtime voice service can use browser WebRTC with server-initiated credentials; a Mac-native later app could evaluate Apple's Foundation Models for language planning, never automatically for raw pixel restoration. Relevant docs: [Realtime API](https://developers.openai.com/api/docs/guides/realtime), [Apple Foundation Models](https://developer.apple.com/documentation/FoundationModels), [Apple MPS](https://developer.apple.com/metal/pytorch/).

### Logical topology

```text
Browser Import + Player ─────┐
                             ▼
                    API / Project Store
                             │
             ┌───────────────┴─────────────────┐
             ▼                                 ▼
        Media Probe                     Intent Parser
             │                                 │
             ▼                                 ▼
      Scene/Defect Signals ─────────► Policy Planner
                                              │
                                       Graph Validator
                                              │
                                  Content-Addressed Job DAG
                                              │
                         ┌────────────────────┼──────────────────┐
                         ▼                    ▼                  ▼
                    Basic CPU/MPS       Temporal Worker      Optional NN
                         └────────────────────┼──────────────────┘
                                              ▼
                                     Evaluation / Artifacts
                                              │
                                      Event Stream / UI
                                              │
                                     Choose / Refine Branch
                                              │
                                     Export + Audio Remux
                                              │
                                    Probe + Manifest + MP4
```

### Two service modes

- **Local development:** browser frontend + Python service + FFmpeg + MPS/CPU. Best for scientific iteration and user privacy; cannot be the only judge path if judges require a remotely accessible app.
- **Hosted demo:** same frontend and API deployed publicly; sample media always works; short user-uploaded assets may be processed subject to a public file-size/time quota and optional GPU capacity. Run the representative demo with reliable deterministic methods even when neural acceleration is unavailable. The remote application must actually execute the advertised operations, not present pre-rendered fake processing.

## Media and graph records (proposed schemas, shortened)

```json
{
  "project_id": "uuid",
  "source_asset": {
    "sha256": "hex",
    "uri": "opaque-storage-id",
    "duration_ms": 12410,
    "video_codec": "h264",
    "width": 1440,
    "height": 1920,
    "fps_numerator": 30,
    "fps_denominator": 1,
    "audio_streams": [{"index": 1, "codec": "aac", "start_pts": 0}],
    "color_metadata": {},
    "privacy_class": "private"
  },
  "fidelity_policy": {
    "no_synthetic_scene_content": true,
    "preserve_original_audio": true,
    "preserve_faces": true
  },
  "attempts": [
    {"id": "attempt_2", "parent": "attempt_1", "graph_revision": "sha256", "status": "ready"}
  ]
}
```

Example node: `node_id`, `kind`, `input_artifact_ids`, `parameters`, `implementation`, `model_sha256` (if applicable), `source_range`, `mask_id`, `output_artifact_id`, `cpu_ms`, `gpu_ms`, `max_memory_bytes`, `status`, `failure_reason`.

## Events and telemetry (design contract)

```json
{
  "event_id": "uuid",
  "project_id": "uuid",
  "attempt_id": "attempt_2",
  "node_id": "denoise_02",
  "type": "node.completed",
  "occurred_at": "2026-10-31T14:00:00Z",
  "sequence": 104,
  "payload": {
    "elapsed_ms": 1832,
    "output_hash": "sha256",
    "preview_uri": "opaque-storage-id",
    "measured_cpu_ms": 310,
    "measured_gpu_ms": 1000,
    "peak_memory_bytes": 721420288
  }
}
```

Values above are **schema examples**, not measured results from an implemented product. Event types might include `asset.imported`, `probe.completed`, `plan.proposed`, `plan.validated`, `attempt.queued`, `node.started`, `node.progress`, `node.completed`, `metric.ready`, `attempt.failed`, `attempt.ready`, `branch.selected`, and `export.verified`.

**No fake progress:** emit percentage only for operations with observable denominators (frames actually processed / total scheduled frames, bytes uploaded / bytes expected, completed subtasks / validated subtasks). GPU kernels without a measurable progress mechanism show elapsed time and an indeterminate state.

## Constrained automated experiment selection

A rules-first planner should segment each clip into compatible scene types and identify likely artifacts. Example rules:

| Signals | Candidate action | Guardrails |
|---|---|---|
| dark static terrain, low estimated motion, stationary camera | multi-frame aligned denoise + tone | motion/reprojection confidence; avoid treating disocclusion as noise |
| moving people | shorter temporal window or motion-compensated filtering | face texture and identity checks; lower denoise strength near faces |
| stars / isolated point lights | conservative tone, calibrated point-source retention | reject hallucinated isolated bright points; inspect before/after point maps |
| bright headlights + dark background | restrained tone mapping/highlights | never claim blown highlights recovered from pure clipping |
| aggressive video compression | deblock + conservative denoise | compare against direct denoise; avoid waxy textures and ringing |
| quick cuts or moving shadows | per-shot strategies with transition guards | avoid abrupt exposure shifts; use overlapping-window transitions |

**LLM role:** parse a user goal and rank *allowed* recipes, summarize uncertainties and translate edits to typed operations. **Not its role:** determine whether a generated patch is visually true; directly issue arbitrary shell commands; invent benchmarks; generate a DAG with unsupported node types.

**Optimizer:** deterministic staged search is the MVP: baseline → small candidate set → validate → compare. If the search space becomes large, a constrained Bayesian optimizer could target a *measurable loss function* that includes visual quality, flicker, time, user preference, and penalties for unsafe changes. Without a trustworthy loss signal, sophisticated optimization is just repeated guesswork.

**Scene and region composition:** avoid independently postprocessing arbitrary frame spans and hard-cut merging. Use shot detection, consistent color transforms and overlapping windows with blending where appropriate. For moving people, prefer spatially and temporally stabilized masks; verify identity and seams on frame-by-frame playback. A segment-specific model may not combine well with a second model's output because transfer functions, noise statistics and learned priors differ.

## Cache, resilience and provenance

- Cache key includes `source hash + exact frame span + operation version + canonical parameters + model hash + environment version + mask hash + input artifact hashes`.
- A changed mask invalidates affected denoise/merge/evaluation/export descendants, not decoding of unrelated frames.
- Resume after failure from completed immutable artifacts; distinguish retry from replacement branch.
- Ensure single attempt/run ID cannot receive conflicting final statuses; sequence events, deduplicate delivery and persist terminal results.
- Manifest includes input/output hashes, graph JSON, model/checkpoint hashes, environment, parameters, notes about external/cloud processing and licenses.
- Store no user faces/voices for training without specific informed consent; support deletion and sample opt-out by default.

---

# D8. Benchmark and Validation Protocol

## Defect-class test suite

Eight mandatory cases:

1. Fixed camera pointed at star field; compare visible point sources against independently measured long-exposure reference where possible.
2. Dim static terrain/mountains; assess texture and shadow SNR, reference registration and invented contours.
3. Moving person in low light; assess face naturalness, motion trails and identity consistency.
4. Handheld pan/walk; assess alignment, ghosting and stabilization motion artifacts.
5. Bright lamp plus dark background; assess clipping and contrast artifacts.
6. Extremely dark almost-black scene; establish honest `No Reliable Detail` outcome.
7. Heavily compressed glasses/smartphone MP4; assess blocking, low-bit-depth banding and false sharpening.
8. Controlled paired capture with synchronized higher-quality camera reference; assess PSNR/SSIM/LPIPS only after alignment and color normalization.

All clips are partitioned by scene and device; avoid test data leakage from model training datasets. Keep source clips unchanged and logged. Define a common evaluation playback framerate, resolution, aspect ratio, color management, ROI and timestamps. Run every attempt through the same export path.

## Metric validity matrix

| Metric | Valid when | Invalid / caution |
|---|---|---|
| PSNR, SSIM | Valid clean reference and aligned, compatible output | Do not calculate source-as-reference and claim “accuracy” for denoised output; reference exposure/color mismatches can dominate |
| LPIPS | Comparable reference and consistent preprocessing | Learned perceptual distance is not a truth detector or identity guarantee |
| NIQE / BRISQUE / no-reference video IQA | Secondary aesthetic/quality signal with known domain limits | Can reward plausible synthesis or inappropriate brightness; never stand-alone fidelity proof |
| Estimated noise reduction | Robust noise estimation on comparable stable regions | Texture removal can masquerade as denoising |
| Temporal flicker / temporal warping errors | Stable registration and motion masking | Dynamic lighting and flow failure confound results |
| Point-source star retention | Repeatable detections with stable geometry and valid references | False positives from hot pixels/noise; user footage alone may be insufficient to prove a star's existence |
| Face similarity | Consent, valid face detections, controlled comparisons | Embedding similarity alone cannot certify faithful facial restoration |
| Clipping / histogram range | Consistent dynamic range and gamma/color metadata | Brightness spread is not synonymous with retained information |
| Latency / RAM / power | Measured on exact test hardware | Not transferable between cloud, Apple Silicon and CUDA without benchmarking |
| A/V sync and original audio | Source and export stream inspection, playback checks and waveform alignment | A small file or audible soundtrack alone does not prove synchronization/bit identity |

**Human evaluation:** collect blind, randomized A/B judgments and targeted prompts (`Which retains real facial features?`, `Which sky contains fewer false points?`, `Which is more pleasant?`). Separate subjective aesthetics from evidence-driven fidelity claims. Include `No material improvement` as an allowed outcome.

## Baseline ladder

`Unmodified source` → `manual tone only` → `tone + classical denoise` → `motion-aware temporal strategy` → `single vetted neural candidate` → `current commercial solution` → `proposed adaptive selector`.

The proposed selector is not assumed to beat every baseline per clip. Its value must show up as **better selection across scene types**, lower user effort, or clearer disclosure of failure and fidelity tradeoffs.

## Provisional gates (not scientific constants)

- **P0 functional:** every accepted upload yields a correct playable output with source-consistent duration and synchronized, unaltered audio when stream-copy is possible; no hallucinated metrics or jobs.
- **P1 usefulness:** on at least a majority of a small held-out dark-video test suite, blinded reviewers prefer the proposed selected result over manual tone-only and do not flag meaningful new false structure.
- **P2 added intelligence:** branch selection produces a user-visible benefit over the strongest fixed classical recipe on at least some distinct scene types, with no systematic regressions on people or point lights.
- **P3 scientific integrity:** full-reference scoring only where paired reference exists; all unpaired results clearly marked; exact attempt runs reproducible from manifest.
- **P4 experience:** a newcomer can import, enhance, compare, and export a short clip without manipulating a node graph; technical reviewer can inspect a specific processing node and provenance.

Before implementation, set numerical thresholds from a pilot dataset and user studies. Do not manufacture arbitrary PSNR targets for night shots lacking paired references.

---

# D9. Feasibility, Dependencies and Cost Risks

## Hardware and capture reality

- **Meta Ray-Ban Display:** official product documentation states a 12 MP camera; product pages list high-resolution still capture and video modes. [Ray-Ban specs](https://www.ray-ban.com/usa/l/discover-ray-ban-meta-ai-glasses).
- **Meta Device Access Toolkit 1.0:** the official FAQ says the supported release includes camera/microphone/audio/motion capabilities, with iOS/Android integration; SDK repository includes photo/streaming features, but some experimental capabilities and publishing restrictions need inspection. [Meta FAQ](https://developers.meta.com/wearables/faq/), [SDK](https://github.com/meta/meta-wearables-dat-ios). **No verified public promise of unrestricted RAW sensor frames or manual multi-second exposure settings**.
- **Apple AVFoundation:** RAW/ProRAW still capture, bracketed photos and processed image formats are supported on eligible devices. The actual capabilities must be queried at runtime for the connected camera. [Apple RAW docs](https://developer.apple.com/documentation/avfoundation/capturing-photos-in-raw-and-apple-proraw-formats).
- **Apple Silicon:** PyTorch offers MPS acceleration, but no assumption that a CUDA-focused repository runs unchanged; check each model's unsupported operations, memory, performance and output parity. [Apple MPS](https://developer.apple.com/metal/pytorch/).
- **Apple Foundation Models:** useful for local structured language tasks or planning where capabilities permit; not an advertised pixel-restoration neural network. [Apple docs](https://developer.apple.com/documentation/FoundationModels).

### Compute / build-budget estimates

These are **planning envelopes, not verified prices or benchmark measurements**:

| Experiment/build item | Working estimate | Cost risk |
|---|---|---|
| Probe + FFmpeg deterministic baselines | 0.5–2 days engineering | Low CPU cost; careful color/audio handling costs time |
| Media-first player with exact A/B sync | 1–3 days engineering | Moderate; VFR/timebases, transformations and mobile preview handling |
| One learned model reproduction on real RGB clips | 1–4 days **if** weights and compatible dependencies exist; may be impossible under license/hardware constraints | Highest technical uncertainty |
| Graph execution records + cache + fork | 2–4 days engineering | Moderate; content hashing and invalidation correctness |
| Evaluation and live telemetry | 2–4 days engineering | Metric validity is harder than plotting numbers |
| Hosted judging demo | 1–3 days engineering | GPU availability, uploads, queue caps and privacy |
| Voice commands after text editing | 1–2 days for constrained MVP | API usage, latency, transcript ambiguity |

For a small batch of clips, trial GPU bills may be modest, but there is **no defensible exact dollar figure** without selected model/frame sizes, workers, provider pricing and render benchmark. Set a real spending cap and sample workloads before enabling open-ended automation. Treat Topaz Starlight's stated potentially hours-long renders as a warning, not the default experience. [Topaz Starlight requirements](https://docs.topazlabs.com/topaz-video/system-requirements).

### Licensing and privacy checklist

1. Inspect exact code license at the pinned commit and separately check pretrained weights license and dependency licenses.
2. Inspect dataset capture rights, subject consent, redistribution rules and training-only limitations.
3. Keep Meta SDK integration optional and compliant with Meta developer terms; do not treat a published GitHub repository as blanket unrestricted permission.
4. React Flow core is MIT; tldraw production is commercially licensed; Affect UI Pro elements require appropriate rights. Refer to [xyflow](https://xyflow.com/open-source), [tldraw](https://tldraw.dev/faq), [Affect UI](https://www.affectui.com/pricing).
5. Keep upload consent explicit for friends' faces/voices; use private assets, short retention, deletion and cloud opt-in.
6. Preserve originals, store hashes, remove geolocation metadata from shareable demos when unneeded, and never expose private footage in a public judge project.
7. Use only legally redistributable example/test content or personally authorized clips; include a small public demo sample and deterministic fallback route.

**Open dependencies:** no local repository was provided; no original video was supplied for measurement; no neural model was executed or reproduced here; weights/license/real consumer-footage performance remain unresolved.

---

# D10. Kiroween 2026 Strategy

## Verified requirements

Official [rules](https://kiroween.devpost.com/rules), [FAQ](https://kiroween.devpost.com/details/faqs), and [competition overview](https://kiroween.devpost.com/):

- Submission/build window **Oct 30, 2026 9 AM PT–Dec 4, 2026 2 PM PT**. Only projects newly built in the period are eligible; previously started projects cannot simply be repackaged.
- Entrants must meaningfully use **Kiro IDE, CLI, Web and/or Crew**. A functioning software app is required.
- Public **GitHub** repository, with an open-source license visible as required; `/.kiro` directory in repo root and not gitignored.
- Public YouTube demo **under 3 minutes**, working app URL, test credentials/instructions if needed, and explanation of Kiro use. Judges need the product to run as depicted.
- Team maximum three individuals; one selected category per project. Eligible bonus categories have additional documentation; do not assume eligibility.
- Equal judging weights: **Potential Impact, Technological Implementation, Design, Innovation/Idea, Presentation**, each 20%.
- Category choices: Resurrection, Frankenstein, Skeleton Crew, Costume Contest. **Recommended: Frankenstein**, because the outcome joins computer vision, media processing, optimization, structured AI commands and auditable visual workflow into one product. *Costume Contest* is weaker because its theme explicitly mentions haunting UI, not simply high polish.

### Recommended scope cutoff

**Must ship:** one sample clip and user import, diagnosis, two or three genuine methods, actual execution, synchronized A/B comparison, branch choice, original audio preservation, valid export, basic manifest, intelligent text refinement, and a technically honest demo. **Stretch:** voice, fine region masks, graph node editor, extra video model, hosted GPU. **Cut:** glasses live preview, full capture companion, custom model training, generalized video editing, automatic cloud scale, persistent multi-agent cursors, diffusion image synthesis under strict-fidelity demo.

### Meaningful Kiro workflow

- `.kiro/specs/`: product requirements, fidelity safety constraints, typed graph schema, benchmark plan, export rules.
- `.kiro/steering/`: editorial copy system, project conventions, truthfulness limits, review checklist, design tokens.
- Kiro **spec-driven development**: task plans track functional slices with executable acceptance tests, rather than prompts that merely ask to “make it beautiful.”
- **Hooks**: automatically lint/typecheck graph schema, check asset privacy rules, validate provenance presence, and run FFmpeg smoke tests on relevant file changes.
- **Property-based tests**: DAG must remain acyclic; source timestamps monotonic after trim/retime; valid graph edits preserve dependencies; revisions cannot mutate ancestor attempts; exported duration and timestamps stay within specified tolerance; cache keys change on parameter/model/hash changes.
- **MCP/tool integrations**: approved docs/test execution/report lookups where genuinely useful. Do not allow agent access to secrets and private footage beyond needs.
- Kiro Crew or custom agents only for **documented development roles** (research verification, backend tests, UI accessibility), not to claim autonomous end-user multi-agent novelty. [Kiro docs](https://kiro.dev/docs/), [Kiro feature overview](https://aws.amazon.com/documentation-overview/kiro/).

### Suggested timeline (not a promise)

| Window | Gate |
|---|---|
| Oct 31–Nov 3 | Collect representative dark clips; run baseline/restoration experiments, select a real model or reject neural dependency |
| Nov 4–9 | Build real import→decode→process→export vertical slice and deterministic end-to-end tests |
| Nov 10–16 | Source-aligned synchronized compare, immutable attempts, real event stream, minimal visual polish |
| Nov 17–23 | Constrained planner/text edit, basic metrics, audio sync, artifact provenance, scene-class demo |
| Nov 24–28 | Voice only if stable, graph overlay, user experience/accessibility testing, New Zealand footage as optional validation |
| Nov 29–Dec 3 | Freeze dependencies, deploy judge path, record <3 min truthful demo, public repo and Kiro writeup, submit early |

No milestone requires New Zealand footage; Sahara or controlled clips are sufficient if lawful and representative.

### Three-minute demo narrative

| Clock | What the judge actually sees | Claim justified |
|---|---|---|
| 0:00–0:22 | Original nearly-dark travel clip with authentic ambient audio and visible inability to see terrain | Problem immediately obvious |
| 0:22–0:40 | Import, original viewer, brief defect diagnosis and constrained natural-language instruction | No special technical knowledge needed |
| 0:40–1:18 | Two or three **real** attempts execute; a true preview appears; original stays accessible | Architecture does genuine work |
| 1:18–1:54 | A/B synchronized wipe at same timestamp; zoomed crop of stars, faces and terrain; show measurable *valid* evidence and caveats | Improvement and limits are inspectable |
| 1:54–2:25 | User says/types “More denoise behind people”; UI shows graph change and targeted rerun | Interaction is consequential, not theatrical |
| 2:25–2:49 | Final MP4 export with original soundtrack, normal playback and provenance manifest | Delivered artifact usable outside app |
| 2:49–2:58 | One-line innovation and Kiro development evidence | Explain product and hackathon substance |

**Do not** splice a pre-rendered enhanced example into a fake live job. If render time is longer than demo time, use an accelerated cut clearly indicated and show real state/provenance; host a reproducible short clip for judges.

---

# D11. Falsification-First Experiments

The tests are ordered to disprove the largest risky assumptions before polishing UI. All costs/effort below are provisional engineering estimates, not observed results.

| Priority | Hypothesis / assets | Procedure and strong baseline | Go / failure signal | Effort / implications |
|---|---|---|---|---|
| **E1: Very dark MP4 actually contains recoverable signal** | 3–5 representative glasses/phone clips, including hardest Sahara footage, no special capture | Probe bit depth, codec, low-percentile histogram; compare tone-only, classical denoise, multi-frame. Blind crop inspection. | **Go** if genuine structures become more discernible without severe new artifacts. **Fail** if dominant area is clipped or pure codec noise; pivot to capture assistance or footage-quality triage. | 0.5–1 day, local CPU |
| **E2: Temporal method beats good classical baseline** | Static terrain, pan, moving person clips | Aligned temporal vs tuned spatial processing on same spans; motion and flicker review | **Go** if temporal improves static details without harming faces/motion. **Fail** if ghosting dominates; make temporal method conditional. | 0.5–2 days |
| **E3: Available neural candidate runs and helps** | 1 vetted repo, checkpoint, licensed code, representative compressed clips | Reproduce pinned environment; test 50–150 frames against classical method; measure speed/memory and artifacts | **Go** if accessible and materially useful. **Fail** if CUDA-only, weights absent, weak generalization, or unacceptable flicker; remove from MVP. | 1–4 days, cloud GPU optional |
| **E4: Star and face integrity can be guarded** | Source with point light/true stars, face footage with consent; secondary reference if possible | Source-aligned point map, user A/B, frame sequence, motion-aware face ROIs | **Go** if obvious invented points and face distortions are rejected reliably enough for demo. **Fail** if output changes identity/creates stars; strictly restrict restoration class. | 1–2 days |
| **E5: Automated branching matters** | 4 scene types, 2–3 fixed recipes, 1 adaptive selector | Compare best fixed baseline against selected branch and user time/effort | **Go** if choice changes appropriately across scene types and reduces effort. **Fail** if one simple filter always wins; remove agentic framing. | 1–2 days |
| **E6: True export and original audio** | MP4 with people speaking, silent clips, unusual timebases | Render, remux/probe, compare stream hash where stream-copied, waveform/PTS and listening test | **Go** if audio duration and sync pass. **Fail** if metadata/timebases break; repair before any UI polish. | 0.5–1 day |
| **E7: Interface comprehensible and inspectable** | Clickable no-backend prototype then real vertical slice | Ask three novice users to import/compare/export; ask two engineers to find model parameters and source linkage | **Go** if both tasks can be completed without explanation. **Fail** if graph consumes attention or experts cannot reproduce. | 1–2 days |
| **E8: Hosted judge path stable** | 5–15 s open demo clip, CPU-safe workflow | Repeated cold starts, error injection, upload/timeout tests, actual MP4 export | **Go** if sample route consistently works independently. **Fail** if a GPU wait makes judges unable to test; move CPU route to primary. | 1–2 days |

**Minimum experiment report format:** asset/hash, device/source class, exact commands and model commit, hardware, step time, output, metrics + validity flags, synchronized crops, failure analysis, decision (`Adopt`, `Conditional`, `Reject`).

---

# D12. Product Specification and Implementation Readiness

## One-sentence definition

**An intelligent night-media workspace that tests real restoration methods on your footage, shows their differences, lets you refine the process in plain language, and exports a faithful, traceable result.**

## The product contract

| Dimension | Final recommended choice |
|---|---|
| Problem | Ordinary cameras record night scenes less clearly than the observer experienced; existing enhancement workflows require choosing methods and checking artifacts manually |
| Target user | Traveler, creator, hobby photographer; secondary technical/research user who wants reproducibility |
| Primary input | Existing ordinary MP4/MOV with audio, optionally single JPEG/HEIF; no RAW/glasses API dependency |
| Primary output | Viewable/usable enhanced MP4 with source soundtrack, optional source-aligned comparison and machine-readable manifest |
| Core primitive | **Media Experiment Runtime** with immutable source, typed transforms, versioned attempts and evidence-aware evaluation |
| Defining interaction | `Import → Restore → Compare → Refine → Export`; advanced `Processing Graph` when requested |
| Distinction to test | Constrained automatic multiple-attempt evaluation + source-aligned visual comparison + true graph edits + honest limitations |
| MVP | One dark video use case; 2–3 methods; real processing; strict-fidelity mode; export/audio; no dummy telemetry |
| Defer | Live Meta capture, model training, autonomous cloud fleet, semantic video editing, broad image/video types, star synthesis |
| Architecture | Web UI + thin Python API + FFmpeg/OpenCV + one optional learned backend + minimal persistent job engine + immutable manifests |
| Technical gating | E1/E2 determine recoverability; E3 validates model; E6 establishes end-to-end reliability; E5 determines whether “adaptive” is defensible |
| Competition | Frankenstein; new Oct 30+ repo; Kiro used meaningfully; public playable app and <3-minute truthful demo |
| Readiness | **Research-informed but NOT yet implementation-ready for model selection or quality claims.** Await earliest controlled processing experiments and license/compute checks. |

## Exact problem–solution narrative

**Problem:** a recording of a night scene can be noisy, compressed and much darker than the memory. Most people don't know whether to brighten, denoise, align frames, run a model, or give up. Worse, a realistic-looking AI output may contain invented detail.

**Solution:** import the source; the system diagnoses measurable conditions; tries a few justified restoration paths; keeps every result aligned to the same source; shows what changed and what remains uncertain; accepts simple instructions that produce actual graph revisions; and exports a normal playable file with provenance.

**Structural fault line:** between *automatic visual improvement without auditable choice* and *professional manual node processing without easy automated selection and communication*. Whether today's products already bridge this gap is a **hands-on competitive experiment**, not a proven absence.

**Flywheel:** on-device/user-local attempts and consented A/B preferences improve per-device, per-scene recipe selection. Research-grade training from user data is deferred until high-quality labels, consent and provenance exist.

**Technology/demo duality:** newcomer sees footage and a `Restore` action. Researcher sees source IDs, exact temporal operations, weights, masks, runtime and metric validity. Both are accessing the same execution facts.

## Hard non-goals

- No promise to recreate stars or faces unseen by the recording.
- No automatic claim that a neural output is physically true.
- No always-visible six-agent orchestra, invented progress animations or decorative workflow lines.
- No reliance on unsupported Meta RAW control or a new capture API.
- No claim that one paper's PSNR is superior to another's without common benchmarks.
- No need for Kiro to run inside the end-user application merely to meet development-use requirements.

## Implementation-readiness checklist

- [ ] At least five representative real-world clips obtained with proper permissions.
- [ ] Actual exposure/noise/codec conditions inspected and recoverability classified.
- [ ] Single reliable baseline shipped locally on a video with preserved sound.
- [ ] One neural method reproduced *or explicitly removed* after failed feasibility test.
- [ ] Real A/B source-aligned output verified on multiple timestamps.
- [ ] A clearly valid paired benchmark set and a distinct unpaired footage evaluation method.
- [ ] Chosen open-source libraries, data, checkpoints and UI assets have reviewed licenses.
- [ ] Public judge app path viable without manual GPU setup.
- [ ] Kiro development specs/tests/steering and `/.kiro` committed from inception.
- [ ] Product copy correctly distinguishes observation, restoration, inference and synthesis.

---

# Research Source Ledger

## Peer-reviewed / accepted conference and journal work

1. Hasinoff et al. (2016), **Burst Photography for High Dynamic Range and Low-Light Imaging on Mobile Cameras**, ACM SIGGRAPH Asia: https://research.google/pubs/burst-photography-for-high-dynamic-range-and-low-light-imaging-on-mobile-cameras/
2. Chen et al. (2018), **Learning to See in the Dark**, CVPR: https://openaccess.thecvf.com/content_cvpr_2018/html/Chen_Learning_to_See_CVPR_2018_paper.html ; MIT code: https://github.com/cchen156/Learning-to-See-in-the-Dark
3. Yakovenko et al. (2025), **AIM 2025 Low-light RAW Video Denoising Challenge**, ICCV Workshop: https://openaccess.thecvf.com/content/ICCV2025W/AIM/html/Yakovenko_AIM_2025_Low-light_RAW_Video_Denoising_Challenge_Dataset_Methods_and_ICCVW_2025_paper.html
4. Xu et al. (2025), **Low-Light Video Enhancement via Spatial-Temporal Consistent Decomposition**, IJCAI: https://ijcai.org/proceedings/2025/238 ; https://github.com/xiaogang00/LLVE_STCD
5. Wang et al. (2025), **RetinexMCNet**, ICCV: https://openaccess.thecvf.com/content/ICCV2025/html/Wang_RetinexMCNet_A_Memory_Controller_Dominated_Network_for_Low-Light_Video_Enhancement_ICCV_2025_paper.html
6. Feijoo et al. (2025), **DarkIR**, CVPR: https://github.com/cidautai/DarkIR
7. Pochimireddy et al. (2026), **Efficient Real-Time Raw-to-Raw Denoising for Extreme Low-Light Ultra HD Video on Mobile Devices**, CVPR: https://openaccess.thecvf.com/content/CVPR2026/html/Pochimireddy_Efficient_Real-Time_Raw-to-Raw_Denoising_for_Extreme_Low-Light_Ultra_HD_Video_CVPR_2026_paper.html
8. Hui et al. (2026), **VSRELL**, CVPR: https://openaccess.thecvf.com/content/CVPR2026/html/Hui_VSRELL_A_Simple_Baseline_for_Video_Super-Resolution_and_Enhancement_in_CVPR_2026_paper.html
9. Kumar and Ambasamduram (2026), **L²DGS**, CVPR: https://openaccess.thecvf.com/content/CVPR2026/html/Kumar_L2DGS_Low-Light_Dynamic_Gaussian_Splatting_CVPR_2026_paper.html
10. Lin et al. (accepted October 3, 2026), **BVI-RLV: A Fully Registered Dataset for Low-Light Video Enhancement**, IEEE TMM accepted/in press: https://research-information.bris.ac.uk/en/publications/bvi-rlv-a-fully-registered-dataset-for-low-light-video-enhancemen/ ; dataset https://lrr-rachel.github.io/BVI-RLV/

## Preprint, code and practice

11. Huang et al. (April 2026), **BVI-Mamba**, **arXiv preprint**: https://arxiv.org/abs/2604.23655 ; https://github.com/edhuang1/BVI-Mamba
12. FFmpeg documentation: https://ffmpeg.org/ffmpeg.html and https://ffmpeg.org/ffmpeg-filters.html
13. MathWorks IQA metrics and limitations: https://www.mathworks.com/help/images/image-quality-metrics.html
14. React Flow: https://reactflow.dev/ ; tldraw licensing: https://tldraw.dev/faq
15. Affect UI Agent Graph and licensing: https://www.affectui.com/components/agent-graph ; https://www.affectui.com/docs
16. Voice whiteboard implementations: https://github.com/theCampel/realtime-whiteboard ; https://github.com/halvorot/speech-to-whiteboard ; https://github.com/DVidal1205/voiceboard
17. ComfyUI: https://support.comfy.org/articles/9806306061-about-comfy

## Hardware, vendors and competition primary sources

18. Meta Wearables FAQ, 1.0: https://developers.meta.com/wearables/faq/ ; iOS repo: https://github.com/meta/meta-wearables-dat-ios ; Meta product: https://www.ray-ban.com/usa/l/discover-ray-ban-meta-ai-glasses
19. Apple ProRAW: https://developer.apple.com/documentation/avfoundation/capturing-photos-in-raw-and-apple-proraw-formats ; MPS: https://developer.apple.com/metal/pytorch/ ; Foundation Models: https://developer.apple.com/documentation/FoundationModels
20. Topaz Nyx / Starlight / Astra: https://docs.topazlabs.com/topaz-video/filters/denoise ; https://docs.topazlabs.com/topaz-video/project-starlight-series/starlight-mini ; https://www.topazlabs.com/astra
21. Blackmagic DaVinci Resolve 21: https://www.blackmagicdesign.com/media/release/20260414-01
22. Adobe Premiere generative extension (not a low-light benchmark): https://helpx.adobe.com/premiere/desktop/edit-projects/edit-with-generative-ai/generative-extend-overview.html
23. OpenAI Realtime API integration: https://developers.openai.com/api/docs/guides/realtime
24. Kiroween official: https://kiroween.devpost.com/ ; https://kiroween.devpost.com/rules ; https://kiroween.devpost.com/details/faqs
25. Kiro documentation: https://kiro.dev/docs/ ; https://aws.amazon.com/documentation-overview/kiro/

## Research gaps and honesty statement

This is a **source-backed architecture research document**, not a full systematic literature meta-analysis or an independently replicated benchmark survey. I did not have original Sahara clips, test cameras, model artifacts, running jobs, exact cloud pricing or a live competitive usability study. Claims about *best algorithm on the user's footage, model performance on their Mac, reliable star retention, all competitor capabilities, licenses of every research checkpoint, and commercial uniqueness* therefore remain open. The next step is a test/evidence package, **not** a final agent implementation prompt. If E1–E3 fail, revise the product definition before designing a large autonomous system.
