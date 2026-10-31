# ALBEDIAN
## Spatial Experience, Motion, Voice and Scientific Visualization

**Tagline:** See (Beyond) the Night Sky.  
**Research date:** 31 October 2026  
**Status:** Research and design specification, **not a built or usability-tested product**.  
**Parent documents:** *Frontier AI Media Restoration: Research and Product Foundation* (31 October 2026) and *From Night Media to the Observable Universe* (31 October 2026). This report preserves their established **Media Experiment Runtime**, independent astronomical observation solutions, and four evidence classes.

### Claim/evidence key

- **[V] Verified documentation / observed public behavior:** supported by cited source; not proof that we integrated or tested it.
- **[I] Engineering inference:** technically reasoned from known methods; unverified in Albedian.
- **[D] Design proposal:** a deliberate interaction, visual or architecture decision; not a user-test finding.
- **[U] Unresolved:** depends on hardware, integration, rights, performance, or user testing.

Source codes `[S01]` etc. refer to the linked **Source Register** at the end. Existing research's scientific feasibility and restoration benchmarking are not repeated in full; this document only recalls results needed to engineer the experience.

---

# D1. Experience Thesis and Critical Verdict

**[D] Albedian is an observation-centered environment, not a gallery, AI chat app, node editor, or disconnected planetarium.** A person's recording is the anchor. Their media can be restored through actual executable processes, measured against its source, aligned to the sky when supported, and traversed into scientific visualizations. Every expedition maintains a traceable route home.

The distinctive experience is not the word *seamless* or a spectacular flight. It is the continuity of **identity, orientation, time, measurement, and memory** during transitions between representations.

**One observation, four representations:**

1. **Recorded:** original immutable media, sound, frame and camera geometry.
2. **Restored:** derivative image/video attempts, same frame/time with inspectable transformations.
3. **Calibrated sky:** sky-direction map tied to validated camera/WCS measurements, when solved.
4. **Scientific space:** positions, distances, motions, trajectories, surveys and artistic visual aids, each explicitly sourced or marked as simulation.

**Three testable propositions:**

- **[I] Perceptual continuity** can be achieved by selecting an invariant celestial direction and retaining it at a stable screen point through image-to-sky calibration, before enabling three-dimensional viewpoint movement.
- **[I] Scientific continuity** can be achieved by retaining distinct references to a source detection, catalog association, coordinate frame, epoch, distance estimate and renderer object. A seamless render is **not** a measurement.
- **[D] Interaction continuity** comes from a versioned `ObservationSession` with a single selection and navigation history, not from separate navigation tabs. Neither restoration nor exploration is allowed to discard the other one's state.

## Executive decision

**GO: design the complete shared experience system and run targeted prototypes.**  
**DO NOT COMMIT YET:** a single physically continuous renderer, full 3D rendering of all stars, unrestricted historical navigation, authenticated moving-object identification, AI-guided enhancement superiority, or a persistent listening microphone.

**[V] Substantial existing engines are reusable:** WorldWide Telescope has a browser-embeddable rendering engine [S01-S03]; Aladin Lite v3 offers WebGL survey/coordinate interaction [S04-S05]; Astropy WCS handles distortion-aware pixel/world transforms [S06-S07]; Gaia Sky documents real astronomical star rendering and scripting [S08-S09]. **[I] None of those sources confirms plug-and-play continuity from arbitrary consumer media into Albedian's proposed flight, so the integration is itself the main technical design challenge.**

**Positioning:** The promise *See (Beyond) the Night Sky* works because the parenthetical distinguishes two valuable experiences: improve what the camera genuinely captured; explore scientifically supported context beyond the capture. Never treat the second as proof of the first.

## Non-negotiable experience invariants

`Original media is immutable` / `selection is stable` / `timestamp is recoverable` / `source audio is preserved` / `calibration is validated` / `scientific provenance travels with objects` / `model output cannot assert measured identity` / `navigation is reversible` / `user can interrupt` / `reduced-motion is first class`.

The first user success is **an improved usable video or an honestly identified sky**, not seeing a graph. The deeper user's success is a reproducible explanation of *how we know* and *what we don't know*.

---

# D2. Experience Architecture and State Map

## 2.1 The four orthogonal state machines

**[D] Do not model the whole application as a single mutually exclusive state enum.** A restoration job may be running while the user inspects an already-solved sky. Use four coordinated state machines instead.

| Machine | Legal states | Invariant / transitions |
|---|---|---|
| `Media` | `empty`, `importing`, `ready`, `decode_error` | `ready` identifies immutable media, frame index, duration, actual audio track(s) |
| `Restoration` | `idle`, `analyzing`, `planning`, `queued`, `processing`, `evaluating`, `ready`, `failed`, `canceled` | Every variant points to a real graph version and content address; UI progress derives from job events, never countdown guesses |
| `Calibration` | `not_requested`, `detecting`, `solving`, `verifying`, `calibrated`, `predicted_only`, `ambiguous`, `unresolved` | Only `calibrated` authorizes a validated source-pixel catalog overlay; predicted sky remains distinct |
| `Navigation` | `source`, `comparison`, `sky_registered`, `transitioning`, `space`, `inspecting`, `returning` | Each transition preserves or checkpoints source ID, frame, audio, camera and selection |

A fifth **Voice** controller independently handles `off`, `permission`, `listening`, `hearing`, `interpreting`, `awaiting_tool`, `speaking`, `interrupted`, `unavailable`. Its voice state is never used as the actual restoration or astronomy status.

### Derived surface policy

- `Media.ready + Navigation.source`: full media viewport, bottom controls, no stellar HUD until the user requests it.
- `Restoration.processing`: place actual job lifecycle and attempt thumbnails in a low-height tray. Keep the source playable where possible.
- `Calibration.calibrated`: sky registration is available, but not automatically expanded.
- `Calibration.predicted_only`: show an independent predicted sky view with a visible `Predicted Sky` label, **not** source-anchored matches.
- `Calibration.ambiguous | unresolved`: refrain from named star markers on the source; display limited capabilities and retry suggestions.
- `Navigation.space`: scientific scene dominates; a persistent quiet `Back to Moment` anchor and a compact source thumbnail remain available.
- `Navigation.returning`: restoring the checkpoint takes precedence over crossfade aesthetics.

## 2.2 End-to-end journey

| Phase | User intention | Primary visual | Actual computation | Failure behavior |
|---|---|---|---|---|
| Import | “Here is what I saw” | Original media appears immediately | Validate codec, hashes, audio, duration, orientation, EXIF | Unsupported file is still retained for download if safe; actionable import error |
| Diagnose | “Why is it dark?” | Contextual defect annotations, optional | Noise/clipping/blur/metadata analysis | `No Reliable Detail` without fabricated discovery |
| Reveal | “Make it clearer” | Same frame before/after, attempt strip | Execute reproducible restoration graph | Show meaningful partial/failed branch, original unaffected |
| Register | “What am I seeing?” | Source image + sparse measured markers | Detect stars, solve WCS, independently verify, match catalog | `Unresolved` or separately `Predicted Sky` |
| Inspect | “That one” | Small docked source card and anchored selected reticle | Resolve stable finding ID, fetch trusted measurements | Disambiguate multiple stars or missing distance |
| Depart | “Take me there” | Point locked while image transitions into sky | WCS registration, match projection and render catalog geometry | Offer sky-only context if 3D position lacks valid distance |
| Explore | “Show me from elsewhere” | Real catalog geometry, source checkpoint present | Recentered positions, distance-aware navigation | Visibly mark incomplete star positions and nonphysical scales |
| Investigate | “What was moving?” | Frame trace vs historical prediction, clearly distinguished | Track motion and compare modeled candidates | Show candidates, not confident identity without evidence |
| Return | “Go back” | Original frame/crop/audio state | Restore checkpoint and media clock | If source unavailable, explicit recovery error |
| Share | “Show someone this journey” | Share preview with evidence legend | Serialize deterministic steps and licensed references | Degrade gracefully if catalogs/assets unavailable or permissions revoked |

## 2.3 Navigation history is an actual data structure

**[D]** History entries are snapshots, not browser URL strings:

`{sourceAssetId, sourceTimeSec, playbackWasPlaying, audioGain, mediaVariantId, cropTransform, orientation, selectedFindingId, evidenceMode, skyEpoch, observerOrigin, cameraRig, sceneScalePolicy, activeOverlays, dataRevision, changedAt}`.

A navigation action pushes an entry only if it changes meaningful state. `Go back` pops a navigation entry; `Go back a little` preferentially reverses the *current continuous dimension* (camera travel, time scrub, or source playback), using an explicit ambiguity policy described in D7. Browser Back is an additional supported route, not the only way home. All history checkpoints keep source time in **media seconds** independent of astronomical epoch.

**[D] When leaving a video**, freeze source playback, remember the audio playback cursor and level, and keep original audio paused rather than accidentally playing it over an interstellar visualization. Returning restores the precise cursor and previous playback intention, with a user-configurable `Stay Paused on Return` option. A normal media export retains the original sound.

## 2.4 Distinguish partial from impossible

- **No star solution:** plain restoration remains useful. No overlay of invented source matches.
- **No reliable distance:** 2D sky identification can work. `Explore in 3D` must be disabled or clearly `Illustrative`.
- **No audio track:** `Original Audio · None`, never fabricate one.
- **No metadata:** source-relative edits work, calibrated sky may still blind-solve, historical time/location predictions require explicit user input and uncertainty.
- **Lost tracking during video:** hide registration markers immediately and show `Tracking Lost` rather than letting a label drift.
- **Offline:** cached source and existing derived media still work; catalog or API-dependent actions explicitly report cache coverage.

---

# D3. Screen and Canvas Architecture

## 3.1 The observation-centered shell

**[D] Desktop, reference 1440 × 900 CSS pixels.** Content hierarchy, not a fixed mockup. The shell owns global navigation, playback, overlays, voice affordance and session snapshots. The rendering surface underneath can change without remounting global interaction state.

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ ALBEDIAN      Sahara Night · 2026-10-19         [Original] [Compare]    ↗   │
│                                                                             │
│                                                                             │
│                    [ SOURCE VIDEO / PHOTO ]                                 │
│                                                                             │
│                  · measured star marker (optional)                          │
│                                ◎ selected source                            │
│                                                        ┌────────────────┐   │
│                                                        │ Selected Star  │   │
│                                                        │ Match · Gaia   │   │
│                                                        │ Distance       │   │
│                                                        │ [Explore]      │   │
│                                                        └────────────────┘   │
│                                                                             │
│  Media Audio ●             00:08.400 / 00:19.000        Back to Moment      │
├─────────────────────────────────────────────────────────────────────────────┤
│ ORIGINAL ──────●─────────── TIMELINE ────────────────────────── ⏯           │
│ Original     Natural        Temporal        [Attempts...]                   │
│                                                                             │
│  [Mic]  Ask or type…                                  [Details] [Export]     │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Layers (back to front)

1. **Renderer stage:** `<video>`/image, a registered sky renderer, or the 3D universe; minimum layout movement.
2. **Source-aligned layer:** detection glyphs and region outlines computed from WCS and current projection, not manually positioned annotations.
3. **Selection layer:** persistent focused object, one leading label, optional measurement line.
4. **Activity layer:** job status, voice listening state, nonmodal messages.
5. **Context layer:** one docked inspector/command tray; additional panels only by explicit intent.
6. **Navigation layer:** `Back to Moment`, current source identity/time, restore/sky/space mode metadata.

**[D] Most of the time there should be no floating cards.** Labels appear only on hover/focus, tap, voice selection, deliberate `Show Labels`, or `Show Matches`. A starfield with 400 labels is not a scientific visualization; it is a failed layout.

## 3.2 Two coherent workspaces, one observation

**Reveal workspace:** original video/image gets ~75% of available visual attention. Restore, compare, scrub, export. Advanced processing graph only via `Methods`. Astronomical detection is optional and never creates a permanent science sidebar.

**Explore workspace:** registered sky or catalog-backed scientific 3D dominates. Overlay minimal fields `Object`, `Distance`, `Viewpoint`, `Epoch`, and `Evidence`. Source thumbnail and `Back to Moment` remain present; original restoration attempts do not clutter flight mode.

**[D] The morph happens at the *content plane*, not in the browser information architecture.** Avoid `Photo` / `Sky Map` / `Universe` top-level navigation tabs as the defining experience. They can exist as accessibility alternatives or developer inspection states.

### Direct manipulation contracts

| Input | Source | Sky registered | Scientific 3D |
|---|---|---|---|
| Tap/click point | Select finding at actual pixel or region | Select catalog object if pixel registration valid | Ray-pick catalog object using current camera projection |
| Drag | Pan/compare wipe depending tool; never implicit both | Pan angular field, preserve coordinate reference | Orbit around selected target or rotate viewpoint, explicit control mode |
| Wheel/pinch | Zoom source around pointer | Change angular FOV with clamp | Dolly/logarithmic navigation depending `Navigation Mode` |
| Space | Play/pause media | Play/pause source media if retained | Pause/resume cinematic travel, not audio playback |
| Shift+drag | Region selection | Angular box selection | Multi-select/lasso only when enabled |
| Escape | Dismiss inspector/region tool | Close overlay | Stop flight then return to last stable camera checkpoint |
| Enter | Inspect focused element | Inspect selected matched object | Inspect object, not instant travel |
| Dedicated Back | Return from subpanel | Source checkpoint | Exact `Back to Moment` checkpoint |

**[D] Distinguish single click selection from double-action navigation.** Selection must not automatically launch the camera. After selecting a star, the user chooses `Explore` or says `Take me there`.

## 3.3 Contextual inspectors

Source inspector: `Source`, `Time`, `Detected`, `Matched`, `Unresolved`, `Solve Quality`, `Method`. Processing inspector: graph inputs, masks, attempts, actual times/costs/metrics. Object inspector: display name, alternate/catalog identifier, celestial coordinates, distance *with uncertainty and method*, epoch, apparent vs intrinsic brightness, proper motion, `Evidence`. Flight inspector: `Observer`, `Distance from Earth`, `Scale`, `Epoch`, `Data Coverage`. Never show a dubious two-decimal light-year value without data quality.

**[D] Difference between hover, focus, and selection** follows Apple's guidance: temporary hover highlight; visible keyboard focus; persistent selected object. These states have different visual treatments, not simply the same glow at different intensities [S18].

## 3.4 Mobile, desktop and spatial future

- **Desktop:** single large content plane, optional right inspector 280–340px; fixed bottom transport/voice tray; resizable captions; keyboard shortcuts.
- **Mobile portrait:** immersive full-width source, touch-sized bottom control rail, bottom-sheet inspector. Star markers are larger hit regions than visible glyphs; avoid masking the sky with keyboards.
- **Landscape tablet:** desktop-like immersive content with collapsible inspector. No implicit 3D movement on device tilt unless opted in.
- **Spatial computing / XR (long-term):** the source video is a stable, readable window, with nearby controls treated like ornaments; opening scientific space can explicitly opt into an immersive volume. Apple warns against too many windows and overly effortful gestures [S19-S20]. **[I] Browser-to-native visionOS/Quest portability is not established by this report.**
- **Accessibility:** meaningful source description, keyboard navigation, visible focus, named findings list alternative, on/off voice text transcripts, captioned voice responses, contrast-safe selection, color-independent evidence badges and reduced-motion.

---

# D4. Original Visual Language: Albedian

## 4.1 Design concept: Observational Instrument, not Sci-fi HUD

**[D]** Albedian's identity should derive from *albedo*: reflected light measured in darkness. It should look like a precision instrument that leaves the night intact, not like a spaceship command console. The interface is absent until there is a reason for it; when invoked, it is exact and exquisitely legible.

**Imagery is the color. Geometry is the interface.** A dim Sahara photograph, the color of astronomical survey data, and the luminous star points must be the visual hero. Avoid a branded purple nebula wallpaper, persistent grids, swarm cursors, animated random stars or noisy glass UI.

### Proposed tokens (not externally validated usability measurements)

| Token | Proposed value | Use |
|---|---|---|
| `space-0` | `#06090D` | Immersive background, not flat absolute black |
| `space-1` | `#10161D` | Inspector/transport surfaces |
| `surface-raised` | `#1B232B` | Active local controls |
| `text-primary` | `#F1F4F6` | Essential controls |
| `text-secondary` | `#A4ADB5` | Labels, readable provenance |
| `text-muted` | `#77838E` | Nonessential metadata; verify contrast |
| `accent-observation` | `#D1E9EF` | Measured selection, neutral cool-white |
| `accent-catalog` | `#81C5CC` | Catalog-matched identity when categorical distinction needed |
| `accent-predicted` | `#D9AE75` | Predicted-only overlays, never same mark as observed |
| `accent-warning` | `#E6AA8C` | Tracking loss/uncertain result, with icon and text |
| `accent-simulation` | `#ABB0D6` | Illustrative/camera-flight labeling |

The colors are **design proposals**. All components must be contrast-tested in actual video and phone displays. For categorical scientific truth, use shape and text in addition to color.

### Type and spacing

- **[D] Primary type:** Apple-like system sans stack (`-apple-system`, `SF Pro` when platform-provided, `Inter` as distributable fallback) rather than bundling Apple font files.
- **[D] Technical data:** `SF Mono` only when available locally, else a suitable open-source mono. Use tabular figures for timecode, coordinates and distances.
- Labels 11–12px only when nonessential; controls 13–16px, selected object 18–22px, title 22–28px in view. 8px base spacing, 4px micro-offsets; edge gutters 20–32px desktop and 16px mobile.
- Touch hit areas target at least ~44 CSS px; visible point glyphs can be 8–12px because hit areas are separate.
- No all-uppercase body metadata; no long explanatory chatter; no em dashes in product copy. `Calibrated · 14 Matches · Residual 0.9 px` is **illustrative copy only** and must never appear with invented values in the actual app.

### Line and marker grammar

- **Observed:** tiny solid center dot plus optional short hairline tick; only when actually detected in original media.
- **Catalog Matched:** thin ring around observed centroid, label anchored through a short elbow connector when selected.
- **Predicted:** broken/dashed circle and `Predicted` badge; not composited as fake source-visible light.
- **Simulated:** faint bracketed point rendered in scientific scene with `Catalog Geometry` or `Illustrative Scale` disclosure.
- **Unresolved:** optional uncolored question marker in inspection mode only; no guessed star name.
- **Uncertainty:** projected error ellipse/footprint only where a calibrated covariance exists; do not draw arbitrary confidence rings.

### Materials and depth

- Opaque/dim neutral work surfaces win over heavy translucent glass. Optional light background blur behind a tiny chip only if contrast is stable; never blur the source image to simulate magic.
- Depth hierarchy: content at z0; markers at overlay z1; contextual inspector at z2; modal confirmation at z3. In 3D mode, HTML controls remain non-world-space to preserve readability.
- Light should originate from the content (selected point, waveform, actual job activity). No decorative nebulas as scene chrome.

## 4.2 Graphic and brand motion

A symbol based on a *single radial observation ray crossing a sparse, open aperture* is more appropriate than a planet, moon, infinity glyph, astronaut or random starburst. **[D]** The logo can appear as a small 2D steady mark. A subtle transient alignment of two rings could represent moving from recorded to measured sky, but the animated logo must not monopolize import time.

**Voice presence** is an expandable 2–3-stroke waveform/oscillating arc around the microphone control. Its animation must come from real microphone voice activity and state, not an always-pulsing AI orb. No audio capture until explicitly activated.

---

# D5. Motion Specification: Every Significant Interaction

## 5.1 Motion principles

**[D] 1. Physical continuity beats spectacle. 2. Data events control animation. 3. Motion is interruptible. 4. A selected object is the anchor. 5. No animation may impersonate computation.** Apple's motion and accessibility guidance supports purposeful, optional animation [S21-S22].

The following numbers are **initial design hypotheses**, not measured performance or empirical animation recommendations. They must be tuned against actual footage, devices and motion-sensitivity tests.

| Interaction | Stage / motion contract | Proposed timing | Rendering mechanism | Cancel / reduced motion |
|---|---|---|---|---|
| Import | Source thumbnail fills media plane; other controls arrive only after first decodable frame | 200–400ms UI placement | CSS transform/opacity or Motion | Instant placement, no zoom |
| Analyze | Small source-edge status; badges update on real detector events | 100–180ms text transition; job has no fabricated duration | CSS/Motion state UI | Static text |
| Restoration preview | New output appears at exact same crop/time, crossfade only after pixels ready | 150–300ms | Composited two video/image planes | Direct switch |
| Before/after | Handle tracks pointer 1:1 with no spring overshoot; optional snap to full source | During drag, 120ms release | CSS clip-path, WebGL mask if GPU pipeline | Same slider, no animation |
| Region selection | Outline follows pointer, corners resolve on release; no particles | 0ms during drag, 120ms settle | SVG/Canvas overlay | Static rectangle |
| Detection | Dots/rings appear on actual measured coordinates; no scan pretending to find stars | 100–220ms after validated results | HTML/SVG or WebGL instancing | Immediate |
| Source-to-catalog | An observed dot acquires a thin second ring; label materializes | 180–300ms | Overlay / Motion | Instant label |
| Inspect | Inspector attaches to screen-safe side of selected point without moving source | 160–240ms | Motion shared layout | Static popover |
| Photo→Sky | Keep selected star at chosen screen coordinate; match sky projection; source opacity yields to catalog/survey scene | 450–950ms | WebGL camera + alpha composition | Direct registered sky view |
| Sky→3D | Commit explicit `Catalog Geometry` mode, then start depth movement | 300–500ms disclosure; optional 1.8–3.5s travel | Custom shader/camera rig | Jump to destination with short fade |
| Interstellar flight | Selected target remains projected inside safe central zone; speed varies with distance, no instantaneous incoherent parallax | Variable, user controllable | 3D camera easing + origin rebasing | Stop, skip, reverse, direct target |
| Change viewpoint | Pivot around stable target and update all catalog-relative positions, not image-texture fake 3D | 400–900ms small move | Quaternion slerp + position path | Immediate reposition |
| Time scrub | Update exact epoch values and recalc data; show pending vs available results | Pointer-locked, no artificial easing | Deterministic science state + WebGL | Same |
| Return | Reverse route when feasible; always exact source checkpoint | 450–900ms sky→image; 1–2s long flight if preferred | Camera checkpoints + alpha | Immediate snap to source |
| Processing failure | Stop any activity, retain valid preview, show concise actionable error | 100–180ms | Text/icon | Static |
| Tracking lost | Immediately remove anchored labels, retain selected object in inspector as `Tracking Lost` | Immediate marker removal | State-driven | Same |

### What *must not* animate

Unsolved guesses, confidence percentages not measured, fake processing edges, agent avatars with no real job identity, a star brightening simply because a catalog lists it, a parallax effect on foreground dunes inferred from a single photo, real-video audio drifting during image crossfades, and celestial positions interpolating across missing data as if verified.

## 5.2 Easing and temporal structure

- **[D] Interface:** fast ease-out or critically damped spring, short displacement (<~16px for inspector), low/no bounce.
- **[D] Angular registration:** slerp camera orientation; interpolate field of view in **log tan-half-FOV space** to avoid erratic perceived zoom; constrain selected star's projected point via off-axis adjustment where needed.
- **[D] Interstellar travel:** use smooth monotonic progress along the path with explicit physical distance readout; reduce speed near destination; distant star counts and field of view can change by level-of-detail, not random spawning.
- **[D] Large scale:** do **not** imply a literal constant-speed flight. Distance curves, time compression and change of representational scale must be labeled. Option `Scale View` should reveal when nonlinear mapping is used.
- **[D] Interruptions:** gestures/voice supersede active camera tweens; current orientation/position is committed to a resumable checkpoint; tool results arriving after cancellation cannot restart flight.

## 5.3 Animation ownership and tool choice

| Technology | Owns | Should not own |
|---|---|---|
| CSS / Web Animations | Hover, focus, opacity, simple tool feedback | 3D astronomy state or scientific computation |
| Motion | Inspector docking, thumbnail shared-element layout, reversible panels [S23] | Projected star positions or shader-based celestial flight |
| Rive | Optional small voice-status glyph, branded interaction state machine; runtimes MIT [S24-S25] | Real job scheduling or spatial navigation |
| WebGL shaders | Image-to-sky blend, stars, faint line/marker rendering, orbital/spatial geometry | Fake evidence or meaningless particles |
| Camera controller | FOV, quaternion, target-tracking, floating origin and save/restore | LLM-driven arbitrary coordinates |
| Job state stream | Only execution badges and real progress based on actual workers | Decorative agent theater |

**[V] Motion's shared-layout API offers interruptible component transitions [S23].** **[V] Rive exposes runtime-controlled state machines [S24].** **[I] Both are useful for shell animation, but scientific anchors should be redrawn from camera matrices each frame so DOM interpolation does not introduce pixel registration drift.**

## 5.4 Frame performance and rendering budgets

**[D] Targets, not benchmarks:** 60fps preferred for navigation on a modern desktop; 30fps minimum sustained fallback during data-heavy 3D; event-to-highlight feedback ideally below 100ms; avoid forcing full source re-encode for small UI changes. Profile p50/p95 CPU/GPU frame times, long tasks, texture memory, dropped video frames, audio-video timestamp offset, number of rendered sources, cache misses, and camera matrix recomputation.

A 60Hz frame is **16.7ms total**, 30Hz **33.3ms total**. Leave substantial headroom for decoding and OS. Dynamic resolution and star-level-of-detail are preferable to visually freezing while loading billions of catalog objects. **[V] R3F supports on-demand rendering for static scenes [S26]; [V] Three.js documents both logarithmic and reversed-depth buffering and the associated constraints [S27].** None of these alone solves astronomical coordinate precision.

**Device tiers:** (1) source/2D overlay only, (2) near-star 3D with bounded catalog subset, (3) high-density 3D/scientific surveys. A device falling to tier 1 remains fully usable; no silent degradation of scientific claims.

---

# D6. Photo-to-Space Engineering: The Defining Transition

## 6.1 Four distinct coordinate systems

**[I] There is no valid 3D environment recoverable from a single photograph of the night sky.** The photograph encodes angle (subject to camera/lens calibration), not stellar distance. The 3D scene must come from external physical measurements and estimates.

Keep the following mathematical frames explicit:

1. **Image coordinates:** distorted pixel `p = (u,v)` and its corresponding original media frame/time. Pixels are from the file, not a generated starfield.
2. **Camera ray:** an undistorted unit vector `r_cam(u,v)` produced by lens/camera projection, possibly a fitted distortion polynomial or fisheye model.
3. **Celestial direction:** `r_sky` with frame, observer and epoch, usually from WCS plus relevant transforms; represent in ICRS-like direction vectors only after converting correctly.
4. **Cartesian scientific position:** `X_i(t)` of an object in a declared physical origin and units, derived from catalog position, estimated distance and (where available) velocity; a time-evolved position is a **model** not a new measurement.

**[V]** Astropy WCS supports SIP and other distortion corrections via its full pixel-to-world and world-to-pixel methods, which matter when the original camera is wide-angle [S06-S07]. **[V]** Astropy supports observer/time-dependent AltAz transforms and linear space-motion propagation [S28-S29]. The latter cannot guarantee accurate ancient/future positions for systems with poorly measured radial velocities, binaries or nonlinear motion.

### Equations and units (illustrative implementation math)

Given calibrated right ascension `α` and declination `δ` in a common frame:

```
r(α,δ) = [cosδ cosα, cosδ sinα, sinδ]
X_i = d_i * r(α_i,δ_i)  // only once distance d_i is justified
X_local_i = X_i - C_origin
```

Angles must be radians in trigonometric functions; catalog epoch, parallax method, reference frame and distance units are essential. A literal `d=1/parallax` can be badly behaved for noisy/zero/negative parallaxes; represent estimated distance distribution and exclusions explicitly. For nearby Solar System bodies, use a separate observer/time ephemeris solution, not the stellar static-position calculation.

**[I] One robust alternative is to derive the direction mapping by querying the authoritative WCS for selected image points** (or densely sampling it for a shader LUT), rather than reducing a wide-angle image to just an approximate camera FOV and orientation. A simple perspective camera can match a central TAN projection; it cannot automatically match fisheye or rolling-shutter distortions. This is a critical fidelity gate.

## 6.2 Two distinct transitions, with an explicit boundary

### Phase A: Recorded photons → angularly registered sky (measurable)

**[D]** Show the original photograph full-frame with a selected, independently verified star at screen position `s=(sx,sy)`. The selected centroid is the stable anchor. Prepare the sky renderer in the background, fitted to the same sky directions and angular field, with catalog objects in corresponding image positions.

- Fit/validate WCS and distortion map on **source** image; record held-out residuals, match counts and uncertainty.
- Map source pixel/anchor through WCS to celestial coordinates. Use observer epoch and projection conventions consistently.
- Render the calibrated sky in an offscreen plane or full canvas aligned to the original **pixel direction field**; compare matched control points before showing the effect.
- Crossfade opacity between recorded image and angular sky *without changing any celestial angle*. A radial vignette or anchored edge reveal is possible but only as a cosmetic transition.
- `Observed` and `Catalog Matched` markers retain their evidence semantics; any additional catalog stars not observed must be rendered as **catalog projections**, not newly recovered stars.

**[U] A rigid 3D perspective camera may not reproduce every original pixel under distortion.** If max/median reprojection residual exceeds the product's accepted tolerance, fall back to image-plane mapping and no claim of perfect alignment. Test with synthetic calibration grids and consumer wide-angle/ultrawide photographs.

### Phase B: Angular sky → 3D catalog geometry (model-based)

**[D]** Once the source has passed into a WCS-registered *sky*, briefly display `Catalog Geometry · Distance Estimates`. From this moment, the scene can move beyond the Earth viewpoint because stellar distances have been imported from catalogs. The direction-to-depth change is deliberate, not an invisible conversion of source pixels into an imaginary volume.

- Keep target identity and object ID stable; retain a ghost of its selected angular bearing for the first part of the move.
- Introduce parallax **only** after 3D mode is activated. In the strict Earth-view phase, points match sky angles; after departure, nearer and farther stars shift according to their 3D catalog positions.
- Initially show a manageable volume of stars with accepted distance estimates; reveal nearby neighbors as view changes. Avoid arbitrary procedural stars, unless visibly marked as illustrative background and excluded from claims.
- On arrival, show target's measured/estimated position, distance interval, source catalog, and neighboring measured stars. **Do not display a detailed textured star surface from Gaia astrometry alone.** Most stars should be point sources or carefully labeled visual symbols.
- Preserve a fixed `Earth Direction` reference arrow or `Return Ray` for spatial orientation; tapping it shows the original observation direction.

A rendered flight may be smooth, but the world has changed evidentially from *captured image* to *catalog-backed simulation*. This short disclosure is more rigorous than a faux-holographic transition.

## 6.3 Camera rig and projective anchor

**[D] Camera rig composition**

```
ObservationCamera
 ├─ SourceProjection        // original media crop, distortion, WCS
 ├─ AngularSkyCamera        // ICRS/topocentric direction projection
 ├─ SpatialObserver         // physical X, current observer origin, epoch
 ├─ SpatialViewCamera       // quaternion, FOV, target and render scale
 ├─ AnchorConstraint        // preferred screen location for selected object
 ├─ TransitionController    // phase + eased progress + cancellation token
 └─ SavedCameraCheckpoints  // exact return/undo state
```

For a selected point `P`, define its projected normalized device coordinates `NDC(P,C(t))`. During the source-to-sky transition, constrain `NDC(P,C(t)) ≈ NDC_target`, allowing off-axis frustum shift and interpolating sky orientation/FOV separately. In 3D travel, lock the selected target to a generous **safe focus region** rather than continuously dead-centering it; rigid central tracking can feel artificial and induce motion discomfort.

**[I]** Quaternion SLERP avoids orientation flips, but position alone does not guarantee visual continuity. The route must also manage FOV, camera roll, focus, near/far planes, level of detail, and star brightness response. Use smooth, reversible camera curves with target-relative coordinates rather than arbitrary spline flight across the whole galaxy.

## 6.4 Position, precision and scale

**[V]** The three.js renderer supports logarithmic depth (potential performance costs) and reversed depth when supported [S27]. **[I]** Both primarily address depth-buffer behavior, not float32 precision across parsecs and planetary radii. A production renderer needs a **double-precision scientific scene state** plus floating-origin or camera-relative coordinates for GPU rendering.

Proposed precision architecture:

- Store authoritative star positions in double precision on CPU, units explicitly as parsecs (or another declared astronomical unit), with origin/epoch recorded.
- Subtract the current camera/origin anchor **before float32 GPU upload**; rebase on travel thresholds to keep local coordinates numerically well-conditioned.
- For extreme ranges, use hierarchical origins (Earth/Solar System → stellar neighborhood → galactic) and optional high/low vector encoding in shaders.
- Spatial chunks use a visibility cone, magnitude threshold, distance threshold and LOD; never load an entire multibillion-object catalog into a browser by default.
- Keep near-field and far-field rendering passes separate when clipping-range requirements demand it. FOV and point sizes should be tested across both.

**Scale design:** default navigation should preserve physical relative positions of the subset being rendered. A nonlinear distance transform may be offered in `Scale View`, but must show `Nonlinear Scale`, give the mapping and avoid claiming the resulting constellation deformation is literal. For exploration travel, nonlinear **camera speed as a function of path distance** is compatible with an otherwise physical scene and preferable to permanently warping the positions.

## 6.5 Celestial event and temporal treatment

Separate three clocks:

- `mediaTime`: seconds/frame number in immutable video.
- `observationUTC`: original capture instant or time interval, including precision/uncertainty.
- `simulationEpoch`: adjustable scientific time within the selected ephemeris/catalog validity regime.

**[D]** A time slider in 3D mode changes `simulationEpoch`. It does **not** alter original media or imply that a 2026 recording contains light from an alternative year. `Observation · 2026` stays pinned as a reference; `Modeled Sky · 1026 CE` appears when switching to a past epoch.

**[V]** Astropy's space-motion method assumes linear motion with available velocity data [S29]. For a thousand-year query, apply proper motion only where scientifically supportable and propagate or visibly caveat uncertainties. Earth perspective precession/nutation and time/location affect *observed sky*, while moving the simulated observer through space changes *viewpoint*. These are different controls and must not be conflated.

**Light travel time is another limitation:** a 3D catalog often represents reconstructed source positions at a reference epoch. It is not automatically a physically consistent light-cone simulation for a traveler at an arbitrary position and epoch. `What would it really look like there and then?` must disclose this distinction; a future research branch may implement retarded light-time and velocity propagation.

## 6.6 Picking, selection and hit testing

Map pointer `s` to the active view's ray. In source mode, select only actual detections and regions (pixel proximity plus estimated centroid covariance). In registered sky, match angular tolerance to zoom and solution residual. In 3D mode, render selection IDs into an offscreen picking buffer or use bounded scene raycasting; account for rendered point size versus astronomical point geometry. When overlapping candidates project to the same few pixels, present a small disambiguation list rather than arbitrarily selecting a star that is physically nearest.

The selected entity's stable key is a `FindingRef` independent of screen coordinate or sprite ID. WCS re-evaluation, viewport resize, playback frame changes and LOD swaps must never silently change what `that star` refers to.

## 6.7 Renderer choice: adapter benchmark, not premature commitment

| Rendering path | Verified capability | Integration advantage | Limitation / required test | Decision |
|---|---|---|---|---|
| WorldWide Telescope WebGL Engine | Reusable JS/TS renderer and sky/3D universe; MIT [S01-S03] | Scientific imagery, broad astronomy coverage, existing universe-navigation modes | External control of exact camera rigs, compositing with source video, transition hooks, data-provenance instrumentation unclear without prototype | **P0 adapter candidate** |
| Custom Three.js + React Three Fiber | 3D/WebGL, scene/camera control, on-demand rendering [S26-S27] | Exact selected-star pixel lock, shaders, custom scientific data structures | Must implement catalog render, precision, ephemeris display, LOD and camera math carefully | **P0 transition candidate** |
| Aladin Lite v3 | WebGL2 HiPS layers, catalog/source events, pixel↔world, embed docs [S04-S05] | Excellent 2D sky+survey inspect layer | Not a general interstellar flight renderer; branding/terms and layers need checking | **Use for sky/survey inspection** |
| Gaia Sky | Mature native astronomical 3D visualization, star rendering and script controls [S08-S09] | Reference for physical rendering, LOD and viewpoint behavior | Native Java/desktop, not a drop-in React library | **Benchmark/reference; remote adapter possible later** |
| NASA Eyes | Sophisticated scientific 3D time navigation [S10] | Excellent time/observer/object framing reference | Not a reusable drop-in engine established in sources | **Reference, not dependency** |

**Decisive P0 experiment:** Render **the same selected star in the same source photo** via (A) WWT-controlled view and (B) a custom WCS-backed Three.js view. Score exact star-screen reprojection, ability to interrupt/return, frame pacing, data fidelity, API ergonomics and licensing. Choose the best measured combination; avoid mounting two full WebGL renderers solely for eye candy.

---

# D7. Voice-Native Interaction: Grammar, Typed Actions and Safety

## 7.1 Voice architecture: listen → resolve → validate → execute → acknowledge

**[D]** Voice is a universal **input modality** to session state, not a parallel chatbot that narrates the interface. User speech may change media processing, selected objects, camera state, overlays, observer position, epoch or navigation history, but only through a typed action registry.

```
Mic permission → streaming speech/ASR or realtime audio model
  → intent + references (“this”, “back”, “there”)
  → grounded context resolver (point, selected ID, recent gesture, active mode)
  → typed action proposal (schema + evidence requirements)
  → authoritative validator (permissions, state, data availability, bounds)
  → deterministic domain service / runtime command
  → structured event + UI state + brief spoken acknowledgment if useful
```

**[V]** The OpenAI Realtime API supports browser speech-to-speech sessions with ephemeral client credentials and WebRTC, tool calls, interruption and server/semantic VAD [S30-S33]. **[V]** LiveKit's agents include turn-detection and interruption configuration [S34-S35]. **[I]** A server-controlled session with authorized app actions is a better trust boundary than exposing unrestricted scientific tool operations to browser-generated or model-generated parameters [S33].

### Transport options

1. **Direct Realtime over WebRTC (leading prototype candidate):** low-latency input/output and interruptions; model proposes typed calls, authoritative backend validates, short spoken feedback. Simplifies conversational naturalness but requires careful event synchronization, privacy, permissions and tool-state grounding.
2. **LiveKit Agents orchestration:** useful when comparing speech providers, managing audio turn detection, noise cancellation or multiple transports; more components to operate.
3. **STT → intent model → structured command → optional TTS:** often more deterministic and testable for a small vocabulary, but longer turn delays and less natural barge-in unless carefully engineered.
4. **Push-to-talk / typed command:** required fallback with explicit turn boundaries, less privacy-sensitive and viable in loud environments. **[V]** The Realtime docs explicitly discuss push-to-talk as a legitimate strategy [S31].

**Recommendation:** abstract the transport behind `SpeechSession`, place `IntentResolver`/`ActionRegistry` on trusted app backend, measure 1 and 3 against actual interruption/accuracy requirements. Do not bind Albedian's scene state directly to one model provider.

## 7.2 Context binding and resolving “that one”

Define `ActionContext = {sessionId, sourceId, sourceTime, view, selectedFindingId, focusedFindingId, recentPointer?:{x,y,at,viewRevision}, gestureSelection?:{findingId,at}, observerEpoch, cameraCheckpointId, voiceTurnId}`.

**[D] Resolution priority:** explicit named object → active selection → contemporaneous pointer/focus within a short defined window → unique visible candidate → ask clarification. Context must include view revision so an outdated click never selects a different star after camera movement. Repeated `that one` references persist until selection changes or becomes invalid.

A dangerous misunderstanding is a confident but wrong object selection. The system should ask, `Which point?` and offer numbered visual targets if multiple plausible hits overlap. The spoken response should be short; most confirmations can be visual.

## 7.3 Proposed typed command contracts (illustrative TypeScript)

```ts
type FindingId = string & { readonly kind: 'FindingId' };
type SourceId = string & { readonly kind: 'SourceId' };
type CheckpointId = string & { readonly kind: 'CheckpointId' };
type AstroEpoch = { isoUTC: string; precisionMs?: number };

type AlbedianAction =
  | { type: 'media.restore'; sourceId: SourceId; profile: 'natural'|'temporal'|'custom';
      constraints: { preservePeople: boolean; allowSyntheticSky: false }; requestId: string }
  | { type: 'media.compare'; variantA: string; variantB: string; preserveTime: true }
  | { type: 'media.seek'; sourceTimeSec: number; preserveAudioSync: true }
  | { type: 'sky.show'; mode: 'measured'|'predicted'; observationSolutionId?: string }
  | { type: 'finding.select'; findingId: FindingId; expectedViewRevision: number }
  | { type: 'finding.describe'; findingId: FindingId; fields: ('distance'|'type'|'motion'|'evidence')[] }
  | { type: 'space.enter'; findingId: FindingId; distancePolicy: 'measured_or_estimated';
      checkpointId: CheckpointId }
  | { type: 'space.viewpoint'; originRef: string; targetRef: FindingId; units: 'pc';
      offset: [number,number,number]; trajectory: 'orbit'|'translate' }
  | { type: 'space.epoch'; epoch: AstroEpoch; scienceModelRef: string }
  | { type: 'events.motionCandidates'; sourceId: SourceId; startTime: number; endTime: number }
  | { type: 'history.back'; mode: 'checkpoint'|'partial'; fraction?: number }
  | { type: 'navigation.cancel'; transitionId: string }
  | { type: 'ui.overlay'; overlay: 'labels'|'evidence'|'survey'|'constellations'; enabled: boolean }
  | { type: 'source.return'; checkpointId: CheckpointId; restoreAudio: true };

type ActionOutcome =
  | {status:'accepted'; actionId:string; optimisticView?:string}
  | {status:'running'; actionId:string; jobId:string; lifecycle:string}
  | {status:'completed'; actionId:string; stateVersion:number; resultRefs:string[]}
  | {status:'needs_input'; question:string; choices?:{id:string;label:string}[]}
  | {status:'rejected'; reason:'unresolved'|'ambiguous'|'no_distance'|
      'unauthorized'|'invalid_epoch'|'missing_source'|'offline'|'unsafe_edit'; nextActions:string[]}
  | {status:'canceled'; actionId:string; settledStateVersion:number};
```

**Validation invariants:** `media.restore` never overwrites original pixels or audio; `space.enter` requires a resolvable matched object and accepted depth; `space.viewpoint` enforces numeric bounds and does not let the LLM invent Cartesian catalog records; `space.epoch` checks data coverage and discloses extrapolation; `source.return` restores the selected source checkpoint exactly; all mutations carry idempotent `requestId`/`actionId` and expected state version.

## 7.4 Representative spoken commands and exact state changes

| Spoken utterance | Resolving context | Typed action(s) | Visible response | Spoken response / fallback |
|---|---|---|---|---|
| “Show me what we saw.” | Active source | `source.return` or `media.compare` original if already source | Exact original frame and audio state; no invented stars | Usually none; `This is the original` if ambiguity |
| “Make the sky clearer.” | Sky region/source | `media.restore` with constrained natural profile | Actual job appears; source remains | `Trying a natural restoration.` If no recoverable signal, report it |
| “Don't change the people.” | Active job/version | Update/fork restoration constraints, segmentation with user-verifiable mask | Foreground mask preview; only affected graph reruns | `People protected in this attempt.` **Not** a proof no pixels changed |
| “What's that one?” | Selected/pointed star | `finding.select`, `finding.describe` | Anchor and object card | Name + evidence class; ask `Which point?` if unclear |
| “How far away is it?” | Stable finding ID | `finding.describe(fields:['distance'])` | Source-backed distance and interval | Say uncertainty where material; no generated precision |
| “Take me there.” | Selected catalog-matched object with depth | `space.enter` | Angular lock, disclosure, 3D travel | `Opening catalog view.` Missing distance: offer sky view |
| “What's behind it?” | Current target, observer ray | Cone search / qualified `finding.describe` or `space.viewpoint` | Sightline cone and depth-ordered catalog hits | Clarify `Along Earth's line of sight, or from this viewpoint?` when needed |
| “Where is Earth?” | Current 3D scene | `ui.overlay` Earth direction; optional viewpoint line | Direction arrow and distance reference | `Earth is in that direction.` Only if coordinate transform resolved |
| “Go back.” | Navigation history | `history.back(checkpoint)` | Previous meaningful state, not an arbitrary reset | No speech |
| “Go back a little.” | Active travel or epoch scrub | `history.back(partial,fraction)` or ask | Reversible travel/time adjustment | Clarify if last action not continuous |
| “What was moving across the sky?” | Source video interval | `events.motionCandidates` | Measured track + separately predicted candidates | `I found a moving point; its identity is unconfirmed.` if not established |
| “Show me that night a thousand years ago.” | Recording date/location | `space.epoch(1026-equivalent)` with policy | **Modeled Sky** overlay with epoch and limitations | `This is a model, not what your camera recorded.` |

**Important:** the timestamp, object name, distances, counts, and result sentences above are *response patterns*, not actual results from the user's uploaded footage. No media was analyzed during this research.

## 7.5 Interruption, concurrency and failure

- **Barge-in while speech plays:** stop playback/truncate unused generated audio using transport-native semantics. **[V]** OpenAI Realtime documents automatic WebRTC interruption/truncation versus explicit client handling for WebSockets [S31].
- **Barge-in during camera flight:** `navigation.cancel` supersedes active transition, freeze current valid camera pose and show `Stopped`. Return remains possible. A new navigation command can begin from this pose.
- **Barge-in during processing:** user may cancel queued/active compute by explicit command. Stopping spoken commentary should **not** implicitly terminate an expensive job. Separate voice interruption from job cancellation.
- **Tool result after new request:** compare action revision tokens; stale completion can be stored in history but must not hijack current view.
- **Tool failure:** concise visual status, precise remedy, no false spoken success. `I can't place that point accurately in the recording. I can show a predicted sky separately.`
- **Muted/no mic:** fully functional pointer, keyboard and typed input. No repeated permissions nags.
- **Audio privacy:** get explicit mic permission, clear status when live, accessible mute/off, short-lived credentials, retention/deletion controls, no unauthorized upload of people/voices or precise GPS in telemetry.

## 7.6 Spoken output policy

**[D]** Silence is often the best response. For a reversible visible action, use no speech or a very short phrase. Longer answers only for scientific questions, uncertainty or error. No artificially theatrical assistant personality, sci-fi mission-control narration or unnecessary narration of button actions. Display critical measurements visually **and** provide accessible speech/text equivalents. Apple recommends concise, context-aware voice responses [S36].

---

# D8. Sound and Spatial Audio

## 8.1 The audio hierarchy

**[D]** Audio should communicate **the boundary between remembered reality and current computation**.

1. **Original Media:** primary authentic audio; untouched in stored source/export, retained timecode and sample rate.
2. **Voice Interface:** short spoken scientific explanation and control feedback, not background chatter.
3. **Navigation Feedback:** optional near-silent click/soft tonal cue for selection and commit, no exaggerated space-whooshes.
4. **Scientific Sonification:** opt-in mapping of data to sound, always marked as artificial mapping (`Sonification · Magnitude to Pitch`, with mapping documented).
5. **Ambient Music:** absent by default. Optional user-supplied soundtrack, never implied to be recorded extraterrestrial sound.

## 8.2 Exact source audio and transitions

- In `source` / `comparison`, only one media transport clock is authoritative. If multiple variants play, synchronize their frame PTS to the **original source timeline** and use original audio exactly once.
- On `sky_registered`, source audio may continue if source video is visibly playing and the user has not switched into a simulation. On `space`, pause source playback and store the exact time and prior playing state.
- Entering 3D can briefly fade the **monitor** level of source audio under an optional cue. This is not a destructive audio edit; normal source/export audio stays identical.
- Return seeks to the stored source PTS. Do not auto-play duplicate audio channels after restoring video element state.
- For comparison export and cinematic journey recordings, treat sound design as additional editable tracks; never replace the archival original asset audio.

## 8.3 Web implementation

**[V]** Web Audio supports `AudioContext`, `MediaElementAudioSourceNode`, `GainNode`, `PannerNode` and audio parameter automation; browser autoplay may require user gestures [S37-S39]. **[I]** Use a small audio bus topology:

```
OriginalVideoElement ── SourceGain ──────────────┐
VoiceWebRTC/Audio ───── VoiceGain ───────────────┼── MasterGain ── Output
UIFeedback ──────────── UIGain ─────────────────┤
OptInSonification ──── SonificationGain/Panner ──┘
```

`VoiceActive` may gently duck *monitor* source audio using gain automation; never silence friends' speech in the exported video. Do not spatialize a mono recording as if the original microphone recorded real positions. Use actual `PannerNode` only for interface sounds with declared virtual positions or for labeled sonification.

### Original restrained identity: proposed palette

- Selection: a quiet, short, dry two-part click, optional with haptic substitute.
- Calibrated match: one tiny harmonic arrival (not one note per star).
- Enter scientific view: a short smooth spectral crossfade that **stops before** travel, avoiding a perpetual drone.
- Return to source: restore actual ambient recording sound as the emotional anchor.
- Failure: no scary buzz; visual text and a low-key warning tone only if enabled.
- Sonification mapping can encode *measured brightness, time series, or orbit periodicity*, but must disclose any arbitrary mapping and must never be described as sound propagating through vacuum.

**[U]** Loudness, sound palette and perceived authenticity are not tested. Compare silent-only, minimal cues and immersive-audio variants with sighted and screen-reader users; default to minimal cues.

---

# D9. Exploration Beyond a Planetarium: Which Journeys Are Worth Building?

**Criterion:** each exploration must begin from a real user's *specific observation, selected source region, or recording time*, and carry that connection forward. Otherwise a dedicated astronomy application is likely the better tool. **[V]** WWT already supports multispectral surveys and 3D exploration [S02, S40]; NASA Eyes supports modeled, time-dependent Solar System explorations [S10]; Gaia Sky supports 3D catalog-driven views [S08-S09]. Albedian should not claim these underlying modes as novel.

| Experience | Specific Albedian interaction | Real data / service | Science and UX limitation | Existing substitute | Decision |
|---|---|---|---|---|---|
| **Constellations From Elsewhere** | Select constellation stars in user's photo → `Take me to another viewpoint` → watch apparent outline deform while Earth sightline stays visible | Gaia positions/distances/proper motions; constellation convention polygons | Only stars with reliable distances; lines are Earth-perspective constructs; missing members need clear labels | Gaia Sky/WWT 3D | **High-value prototype** because observation supplies emotional and geometric anchor |
| **Stellar Neighborhood** | Select a matched star → see nearby catalog sources in pc, physical distances, radial/velocity context where known | Gaia DR3 and validated distance estimates | Star discs/artistic textures are not observed; complete neighborhood not guaranteed | Gaia Sky, WWT | **Core Explore** |
| **Then vs Now** | `Show me that night 1000 years ago` from actual GPS/time → paired historical model, exact recorded source stays accessible | Astropy reference frames, proper motion, ephemerides, precession | Extrapolation/data gaps/atmospheric refraction and long-term gravitational dynamics | Stellarium, SkySafari | **Include with scoped epoch coverage, visible uncertainty** |
| **Planetary Motion** | Select apparent bright point → identify plausible planet given solved image/time, open orbit and phase | JPL Horizons/Skyfield, observer location/time | Requires reliable metadata and matching; visible planet may be saturated/unresolved in source | NASA Eyes, planetarium apps | **Strong scientific investigation, after star link** |
| **Satellite/ISS Passage** | Draw motion trail on source video → overlay predicted candidates from historical ephemerides | Video tracks, accurate timestamps/location, archived elements/state vectors | Orbital elements stale; ambiguous aircraft/satellite/meteor; proper identity often unverifiable | Specialized orbit trackers | **Defer confident ID; keep candidate investigation** |
| **Survey Comparison** | Pick apparent fuzzy object/region → show real telescope imagery registered to source direction, select visible/IR/X-ray (with clear label) | Aladin Lite/HiPS, WWT surveys, rights/credits | Survey wavelengths and exposures differ radically; not source recovery | WWT, Aladin Lite | **High-value science mode**, but not the defining MVP flight |
| **What's Behind It?** | Draw a line-of-sight cone from the current observer; sort objects by catalog distance with distance uncertainty | Catalog RA/Dec/distance, selection angular cone | Objects are not literally occluded by point stars; incompleteness means `No catalog source` ≠ empty space | 3D catalog visualization | **Strong voice+pointing affordance** |
| **Transient/Event Trace** | Select a streak → detect time series; compare against orbit/meteor/aircraft models | Motion extraction, archived ephemerides, optional ADS-B if available | Camera movement, compression and missing records frustrate identification | Astronomy and tracking tools | **Research** |
| **Personal Memory Journey** | Export video of original 4s moment → reveal → select → registered sky → spatial view → return, with provenance cards | Deterministic source/audio, graph, astronomy manifest, licensed render captures | Rights, privacy of friends' faces/voices; scientific flight is illustrative | Could be assembled manually across multiple apps | **Promising unique product experience** |

## The most compelling prospective differentiator

**[D] “Experience Continuity”:** an observation-derived journey with independently inspectable layers: `Recorded Moment → Recoverable Detail → Measured Sky → Catalog Context → Explorable Geometry → Recorded Moment`. The user is never forced to forget where the question came from.

**[U] Differentiation is a usability claim, not yet demonstrated.** Prior product research identified sky-photo solvers and 3D planetariums. The app is not justified if users prefer a standard photo editor plus a planetarium after experiencing the prototype. A blinded 5–10 minute task comparison is a required product gate.

---

# D10. Technology and Systems Evaluation Matrix

| Layer / technology | Verified role | What Albedian should use it for | Integration risk | Recommendation / evidence |
|---|---|---|---|---|
| **TypeScript + React** | Mature client UI ecosystem | Shared shell, stores, inspectors, accessibility | Bad state ownership and render churn | [D] Core shell |
| **Three.js / R3F** | 3D cameras, shaders, point geometries, managed render scheduling | Exact source-to-space transition and custom camera rigs | Floating origin, huge catalog scale, WebGPU compatibility | [D] Main P0 renderer proof, [S26-S27] |
| **WWT WebGL Engine** | Reusable astronomy renderer, MIT-licensed [S01-S03] | 3D catalog/sky adapter; possible replacement for custom astronomy rendering | Camera API and compositing constraints, memory duplication | [D] Parallel P0 alternative, benchmark objectively |
| **Aladin Lite v3** | Embeddable WebGL2 sky survey/catalog renderer; pixel↔sky functionality [S04-S05] | Inspect survey and multiwavelength sky, catalog layers | Layout/canvas integration; logo terms; release pinning | [D] Secondary science view |
| **Gaia Sky** | Native 3D astronomy visualizer with scripting and star rendering [S08-S09] | Reference for navigation/data rendering, external comparison | Native app not React renderer | [D] Benchmark, not direct component |
| **Astropy WCS / coordinates** | Pixel/world with distortion; observer/time transforms [S06-S07, S28-S29] | Authoritative science computations and reprojection verification | Python service deployment and astronomical convention errors | [D] Science backend |
| **Gaia catalog + distances** | Astrometric positions, parallaxes, motions and partial radial velocities [S41] | Catalog-backed neighborhood geometry | Missing or poor distances, incompleteness, data sizes | [D] Bounded subset with quality filters |
| **JPL Horizons / Skyfield** | Solar System/observer ephemeris models (verified in parent report) | Planets/orbits/historical positions | Time scale, spatial frame and archived orbital records | [D] Science service, not LLM |
| **FFmpeg + OpenCV + neural adapters** | Real video decode, processing, scoring (parent research) | Immutable source + reproducible restoration | Heavy GPU jobs, model/license, false details | [D] Keep Media Experiment Runtime intact |
| **Motion** | Interruptible/shared UI layout animation [S23] | Inspectors and shell transitions | May drift if used to place real sky labels | [D] UI shell only |
| **Rive** | State-machine animation with MIT web runtimes [S24-S25] | Optional microphone/status and logo microinteraction | Editor/assets licensing, unnecessary dependency | [D] Optional, not critical path |
| **OpenAI Realtime + WebRTC** | Browser audio, turn detection, interruption, tools [S30-S33] | Natural voice input/output with typed action adapter | Latency, costs, consent, backend trust | [D] Leading voice candidate, test end-to-end |
| **LiveKit Agents** | Turn management and interruption controls [S34-S35] | Provider-independent voice orchestration | Operating complexity and additional services | [D] Compare if turn-taking needs justify |
| **Web Audio API** | Gain, sources, panning, automation [S37-S39] | Authentic source/audio bus + restrained cues | Browser permissions/autoplay; audio sync | [D] Browser default |
| **Persistent job queue + events** | Established backend pattern (parent research) | Actual stage progress and cancellation, graph evaluation | Partial and stale result races | [D] Required for reliable restoration UI |
| **Local cache + catalog snapshot** | Architectural proposal | Deterministic export/replay and offline fallback | Storage size, license/redistribution | [D] Pin every version; rights review |
| **WebGPU** | Browser GPU API with changing support (not benchmarked in this report) | Long-term compute and richer rendering where viable | Device/browser fragmentation; library changes | [U] Benchmark, keep WebGL2 fallback |
| **WebXR / native spatial** | Platform frameworks exist (not integrated) | Long-term immersive travel | Comfort, gestures, labels, cross-platform parity | [U] Separate exploratory adapter |

## Proposed component/service boundaries

```text
                   ALBEDIAN SESSION SERVICE
                          |  state+events
     ┌────────────────────┼───────────────────────────┐
     |                    |                           |
  Media Runtime      Science Service             Voice Gateway
  (source, jobs,     (WCS, catalog,             (speech transport,
   variants, audio)   ephemerides, evidence)       typed intent)
     |                    |                           |
     └───────────────┬────┴───────────────────────────┘
                     |
               Observation Model
        {source, variants, calibration,
         findings, selected ID, observer,
         epoch, camera, history, permissions}
                     |
       ┌─────────────┼────────────────────────┐
       |             |                        |
   Media Plane    Sky/Survey Plane        Space Plane
   HTML video     Aladin/custom          WWT/custom
   + comparison   WCS projection         catalog rig
       └─────────────┼────────────────────────┘
                     |
        Global accessible overlay and controls
```

**[D] Runtime objects are not UI objects.** The restoration DAG contains real transformations. The scene graph contains rendered objects and cameras. The science object model contains stable source and catalog references. They must be related by IDs, not collapsed into one generic `node` entity.

### Event guarantees

- Every processing status has actual `jobId`, `state`, timestamps, emitted event source and provenance.
- Every science update has `solutionRevision` and `catalogRevision`.
- Every camera transition has `transitionId`, `startCheckpoint`, target/goal and `cancelToken`.
- Every voice action has `turnId`, resolved finding ID, permission scope and `expectedStateVersion`.
- UI commits actions through a reducer with version checks; old network responses cannot undo user navigation.
- Shareable sessions serialize an allowed replay script plus actual output files and references, not private access tokens.

### Recommended codebase separation (design proposal)

```
/apps/web                 UI shell, presentation, input/accessibility
/services/media           source handling, processing graphs, audio/export
/services/science         WCS, catalog, ephemerides, provenance
/services/voice           realtime session, intent validation, tool routing
/packages/observation     typed cross-layer state and history
/packages/camera-rig      projections, anchor, travel and rebasing
/packages/evidence        observed/matched/predicted/simulated semantics
/packages/design-system   type, spacing, markers, inspectors, motion
/packages/contracts       command schemas and lifecycle events
/experiments              reproduction reports and falsifiable benchmarks
/.kiro                    actual specs/steering/hooks and dev notes
```

This folder arrangement is **not** a requirement to implement a multi-package monorepo immediately. Its purpose is to identify module ownership and stop the visual prototype from hardcoding scientific fake data into UI components.

---

# D11. Reference Studies: Borrow Behavior, Not Appearance

A study is only useful if it changes a specific interaction. The following findings are **[V] source-backed product behavior** and **[D] authored interpretation/recommendation**. Actual source screenshots were inspected through web image results for WWT and game UI, but no hands-on live rendering or source-code instrumentation was performed here. Visual descriptions are not licenses to copy their artwork.

| Reference | Verified behavior / source | Borrow for Albedian | Do not copy |
|---|---|---|---|
| **Apple Photos (Mac)** | [V] Original is retained; press-and-hold compare temporarily reveals original, including M shortcut [S17] | [D] Source and enhanced should always be instantly comparable at the same crop/time; `Original` held shortcut | Hideable irrecoverable origin or unsupported auto-enhance claims |
| **Apple visionOS ornaments and focus** | [V] Related controls can remain attached to windows; focus/selection have distinguishable semantics [S18-S20] | [D] Keep a quiet attached command rail; separate hover/keyboard focus/selected object; spatial controls near, not in the starfield | Excess glass, untested gaze interactions, tiring direct gestures |
| **Apple motion/accessibility** | [V] Motion should be purposeful/optional; reduced motion should avoid z-depth and excessive animation [S21-S22] | [D] Short, interruptible, reassuring state transitions; full no-flight mode | Camera swoops without accessibility alternatives |
| **NASA Eyes** | [V] Time navigation and scientifically sourced exploration of modeled Solar System objects [S10] | [D] `Epoch`, observer and scale should be explicit, not magic; inspect object before traveling | Claim that all art is direct photographic evidence |
| **WorldWide Telescope** | [V] Sky and 3D universe modes; astronomical surveys and wavelength exploration [S01-S03, S40] | [D] Reuse proven science infrastructure; separate sky imaging, space and survey datasets | Old heavy toolbar/menu density or fixed full-screen planetarium chrome |
| **Aladin Lite** | [V] Embedded HiPS imagery, catalogs, coordinate transformations and source events [S04-S05] | [D] Scientific layer switching, inspectable catalog picks, pixel-to-sky correctness | Treat different wavelengths as enhanced visible-light source images |
| **Gaia Sky** | [V] Native 3D catalog exploration and documented star-shader approach [S08-S09] | [D] Measured star geometry, magnitude visualization and large-scale spatial constraints | Unexplained nonlinear scales or assumption every bright dot is physically resolved |
| **Destiny's Director** | [V] Game's map is a destination navigation tool; Bungie recognized longstanding complexity and redesigned it [S42-S44] | [D] Sense of navigable destination and clear wayfinding breadcrumbs | Fictional astronomical distances, game-map clutter, mission icons on real sky |
| **Halo Infinite TacMap** | [V] Map lets players set/clear waypoints and inspect game areas [S45] | [D] Quiet context map and one active destination; explicit backtracking | Combat HUD style, game-world cartographic decoration |
| **Detroit: Become Human investigation** | [D] Public images show reconstructed event silhouettes and an investigative branch flow [S46] | [D] A selected scene point reveals relevant investigative evidence at that location; a frozen source can show an abstract explanatory overlay | Treat narrative/fictional reconstruction as real astronomical measurements |
| **Motion** | [V] Shared element layout animation and interruptible transitions [S23] | [D] Inspector motion and continuity from source label to side card | Layout transforms on scientific point data |
| **Rive** | [V] Programmatic state machine inputs and open-source runtime [S24-S25] | [D] Small mic/listening/response state interface | Giant constantly-moving assistant avatar |
| **affectUI Agent Graph / Cursors** | [V] Agent Graph represents live tool calls; vendor lists when appropriate and labels Pro [S47-S48] | [D] Adapt the *principle* of event-linked status for actual restoration execution | Swarm of fake agent cursors, ornamental graph pulses, assuming Pro source free |

**Particular subtlety:** The best *game* reference is not a color palette; it is destination continuity. The best *Apple* reference is not translucent glass; it is reversible, faithful editing. The best *research visualization* reference is not visual density; it is preserving coordinate and provenance meaning while the view changes.

### Suggested viewing/inspection exercises for the development team

1. Hold compare in Apple Photos while viewing a high-contrast edit and inspect same-pixel behavior [S17].
2. Navigate from a targeted sky region to a 3D neighborhood in WWT and document what camera controls are programmatically exposed [S01-S03].
3. Use an Aladin Lite v3 example with a catalog overlay to verify `pix2world`/`world2pix` and object selection [S04-S05].
4. Use Gaia Sky's actual star-shader documentation and scripting to benchmark distance cues/point brightness [S08-S09].
5. Compare the old and redesigned Destiny Director, focusing on navigation complexity rather than graphic style [S42-S44].
6. Inspect affectUI Agent Graph's live-call API and prohibit it if there are no genuine calls to represent [S47-S48].

**Licenses:** WWT's renderer is MIT [S03]; Rive runtimes are MIT but editor/assets have separate requirements [S25]; Three.js MIT [S27]; affectUI Agent Graph is Pro [S47]; Aladin Lite docs request retaining logo/link and dataset rights are independent [S04]. Verify all pinned dependency and visual-asset licenses again at implementation time. Game UI is inspiration only, not reusable product artwork.

---

# D12. Three Prototype Specifications

## Prototype A — Reveal

**Purpose:** prove that the source-first restoration and voice experience genuinely helps users, even when astronomy is unavailable. **Status: proposed only.**

### Exact screen and components

- Hero: source video with frame-accurate timecode and original audio button; import overlay only before source exists.
- Bottom rail: `Original`, `Natural`, `Temporal`, optional `Neural`, compare wipe and playback; one mic/text action field.
- Activity tray: job stage/elapsed time, completed/failed attempts; `Methods` inspector with actual graph, model versions and preservation constraints.
- Export drawer: original audio flag, output resolution/codec, reproducibility manifest, privacy warning if sharing human subjects.

### State machine

`Empty → Imported → SourceAnalyzed → Planned → AttemptQueued → AttemptRunning → AttemptReady/Failed → Comparing → Refining → Exported`. The machine permits multiple parallel attempts and partial ready states. Every attempt has immutable run ID. Failure is not global failure if one branch is playable.

### Interaction script

1. Import genuine extremely dark consumer MP4 with original audio. Player shows source, not a simulated darkness-to-light effect.
2. Say `Make the sky clearer. Don't change the people.` Voice resolves constraints and actual restoration plan; UI shows real methods and starts jobs.
3. First valid preview appears on the **same frame**; display classed attempt and `Processing` state if other branches are still running.
4. Swipe compare, zoom to same region, toggle a blind A/B result. Explain whether objective reference exists before displaying any PSNR/SSIM.
5. Say `Try more background denoising.` Alter validated mask/parameters; reprocess only affected dependent graph stages; preserve original.
6. Export synchronized video with original audio, manifest, and a source/processed comparison if requested.

### Animation/camera

The camera is an image crop/zoom transform. No 3D camera. Use 150–300ms real-result reveal and pointer-locked comparison. Show job activity only when backend events arrive. During voice listening, mic glyph is sound-level responsive but not an agent avatar.

### Outputs and tests

**Actual output:** playable enhanced video + original audio + variant and processing graph manifest. Source pixels and original audio unchanged. **Success:** A/B same PTS and crop, processing materially changes video, exports correct audio duration/sync, no fabricated metrics or job times, user can finish without science features. **Failure:** preview is simulated; audio lost; asynchronous branches display wrong frame; hidden hallucination; export cannot play; “preserve people” has no inspectable masking/evaluation.

**Implementation:** parent Media Experiment Runtime, FFmpeg/OpenCV adapters, media transform store, job events, compare canvas, voice actions. **[U]** Actual dark glasses video quality, model licensing/performance, face/mask preservation and cost are still untested on target footage. Reference: Apple Photos original-compare [S17]; affectUI activity status as conceptual inspiration [S47].

## Prototype B — From Earth to Space

**Purpose:** test Albedian's defining continuity from a user-originated astronomical direction into catalog-backed 3D geometry. **Status: proposed only.**

### Exact screen and components

- Full-viewport **actual** photograph (calibrated external asset, not an artist-rendered sky).
- Sparse matched-star overlay, source status `Calibrated`, selected-star reticle; minimal scientific object card with `Catalog Matched`, data source, distance interval and `Explore`.
- Transition disclosure `Catalog Geometry · Distance Estimates`.
- 3D near-star renderer, source thumbnail, `Earth Direction`, `Distance`, `Observer`, `Scale`, `Epoch`, `Back to Moment`.
- Optional developer drawer with reprojection residual, WCS header snapshot, catalog release, star IDs and camera transform.

### State machine

`PhotoReady → CalibrationVerified → StarSelected → AnchorLocked → SkyRegistered → ScientificBoundary → Traveling → Arrived → AlternateViewpoint → Returning → PhotoRestored`. `Unresolved` and `NoReliableDistance` branch to source/sky-only views. `Traveling` supports `Canceled`, `Skipped`, `Reversed`.

### Exact transition sequence

1. Pick a bright matched source centroid `p0` on actual image. Save source crop, media time (if extracted from video), current client viewport, image+WCS versions and selected ID.
2. Verify all held-out star match residuals; compute WCS-derived `r_sky(p0)`.
3. Render matching sky layer with identical selected point projection and **calibrated angular relationships**. Diagnostic mode overlays a source-to-catalog residual vector plot.
4. Fade source to sky while selected point stays within tolerance. Do not animate foreground mountains in 3D.
5. Mark the moment the scene becomes catalog geometry; include an actual Gaia subset with finite accepted distances and uncertainty.
6. Travel to a reproducible neighborhood waypoint; show real catalog stars and parallax when the origin changes, with clear approximate distance labels.
7. User drags viewpoint. Recompute positions relative to observer origin; selected object does not become an artistic star mesh.
8. `Back to Moment` restores exact source, crop, selected point, frame and audio checkpoint.

### Successful interaction and acceptance targets

- Same star ID from source marker to sky overlay to 3D object to source return.
- **Provisional engineering goal:** median held-out reprojection error ≤2 source-image pixels at canonical resolution *for the chosen dataset*, with a separately measured p95 and failure reporting. This is a proposed target, not a universal scientific standard.
- Selected-star screen-space discontinuity at phase handoff ≤~2 CSS pixels under calibrated supported projections, or disclose alignment mismatch rather than hiding it.
- Source checkpoint restored exactly; no unannounced camera roll flips or dead-end navigation.
- Parallax positions calculable from the pinned catalog subset; missing or unreliable distances are excluded/marked.
- Acceptable performance on supported test devices and graceful fallback when WebGL unavailable.

**Implementation:** Astropy-produced WCS/quality model, server-side source→catalog linkage, Three/R3F **and** WWT adapter experiments, bounded Gaia subset, camera-relative coordinate renderer, math-driven camera controller. **[U]** 100% continuity under ultrawide lens distortion, mixed renderer composition, and perceptual comfort remain untested. References: WWT engine [S01-S03], Aladin [S04], Gaia Sky [S08-S09], Astropy [S06-S07].

## Prototype C — Investigate the Night

**Purpose:** prove that voice + pointing + scientific state performs a real contextual investigation beyond simple star labels. **Status: proposed only.**

### Exact screen and components

- Original recorded video with source PTS and optionally detected time-series trail.
- Selected region/track overlay with `Observed` and `Motion · Unresolved` until sufficient identification evidence.
- Contextual investigation panel: observations on left, prediction candidates on right (not a single falsely confident ID).
- Optional separate ephemeris/sky reconstruction scene, source/time slider, `Model Epoch` badge, source/candidate evidence panel.
- Mic/text affordance and visible `Uncertainty` button.

### State machine

`SourceReady → RegionSelected → TrackMeasured → ContextEvaluated → CandidateSearch → CandidatesReady/Ambiguous/Unresolved → HypothesisInspected → SourceReturned`. Source can remain useful in `Unresolved`. Historical reconstruction branches to `Predicted Sky` without becoming a measured source ID.

### Interaction script

1. Import short video with people and a streak (real assets). User points at moving trail and asks `What was moving across the sky?`.
2. Source track pipeline detects time-displaced feature; quantify pixel motion and camera-motion residual, record source frames and confidence limitations.
3. If time/location/WCS support it, query archived satellite/planet event candidates. Keep observation measured positions in one column, predictions in another. No unverified satellite naming.
4. Say `Show me that night a thousand years ago.` UI pins `Original · 2026` and changes separate modeled sky epoch; a brief caveat appears and the source image remains one action away.
5. Say `What's behind that star?` Resolve selection and observer origin. Show a catalog cone/depth distribution with distance uncertainties, not a photograph of nonexistent foreground layers.
6. Return to exact video frame and original sound.

### Success and failure

**Success:** every claimed measured track points to actual source frames; time/location uncertainty visible; prediction candidates separately sourced; ambiguous orbit never presented as confirmed; changed epoch visibly labeled `Modeled`; typed action histories reproducible; voice interruptions never alter stale selections.

**Failure:** UI overlays satellite name based solely on similar trajectory; assumes present-day TLE valid in old footage; treats meteor as an individually cataloged object; same display style for measured and predicted stars; arbitrary “1000 years ago” model without epoch definition or velocity limits.

**Implementation:** frame track and timestamp integration, optional verified WCS per frame, Astropy/Skyfield/Horizons/archived orbit data, typed action validation and scientific query adapters. **[U]** Historical satellite reliability remains a research problem; a meaningful demo may use bright planet identification or robust historical sky reconstruction instead if archival orbital evidence is inadequate.

---

# D13. Three-Minute Walkthrough: Cinematic, Real, Reproducible

**[D] Demo design, not filmed footage.** The entire demonstration must use real input assets, real application behavior, truthful screen recordings, and clearly credited science datasets. A camera move should never hide a failure in processing or calibration. No concept-trailer-only shortcuts.

| Clock | What judge sees | Real software proof | Visual / sound direction |
|---|---|---|---|
| **0:00–0:15** | Dark recorded Sahara clip with people's voices or night ambience. Text: `What the camera kept` | Actual source hash and source player; no simulated darkness | Original sound establishes emotional context, no music |
| **0:15–0:34** | Say `Recover what you can. Don't invent stars.` Real processing starts | Typed constraint + actual graph/job lifecycle event | One understated job activity lane, not a swarm |
| **0:34–0:55** | One completed preview, same timestamp A/B reveal; optional quick second attempt | Two real media derivatives and synchronized compare | Quiet source sound, crisp wipe, no theatrical flash |
| **0:55–1:12** | Pause on solved sky image, `Calibrated` and actual matched-star dots | Real WCS result + held-out residual; calibrated on real uploaded image | Dots align one by one only after result arrives |
| **1:12–1:30** | Tap one **actually matched** star; ask `How far away is that?` | Catalog object ID and a defensible distance estimate | Object card, brief spoken response |
| **1:30–1:55** | Say `Take me there.` Image→angular sky→catalog geometry→star neighborhood | Actual WCS handoff; one star retains identity and screen anchor; real catalog subset | Data boundary disclosure, restrained camera travel |
| **1:55–2:14** | Rotate viewpoint; Earth direction and measured neighboring stars visibly change | Positions recalculated using actual distances, not 2D particle parallax | No fake sky sound; subtle optional cue |
| **2:14–2:29** | Say `Show the sky a thousand years ago.` | Epoch-labeled modeled view, source epoch pinned | `Modeled Sky` and caveat clearly visible |
| **2:29–2:44** | Say `Go back.` Exact original frame/time/audio reappears | Saved checkpoint restoration | Recorded voices/ambience return as emotional ending |
| **2:44–3:00** | Export restored playable video and inspect `Evidence`/`Methods` | Real output MP4 with original sound, source/graph/catalog manifest | Final card: `ALBEDIAN · See (Beyond) the Night Sky.` |

### Must be legible within three minutes

- Source footage is *real* and visibly dark.
- At least one restoration result is *real* and useful, not just brighter.
- Registration is *actual* and robust enough for the chosen image.
- Object is a catalog-backed ID, not a name generated by an LLM.
- 3D movement is based on measured/estimated 3D positions and clearly marked as simulation.
- Voice actions actually change scene state.
- Return restores the recorded moment and sound.
- Export is playable and testable by a judge.

**Fallback narrative if technical gates fail:** run an honest restoration + source-aligned 2D sky science demo, demonstrate the science limitations, and explicitly omit the 3D claim. A truthful useful app is better than an untestable flight.

---

# D14. Recommendations for the Master Development Prompt

**Do not convert this research document into a generic request to “build a stunning sci-fi AI app.”** The master prompt should bind agents to technical invariants, measurable experiments, design references and review gates. The ambition is full-platform, but implementation order should falsify the riskiest ideas early, not permanently cap the vision.

## Mandatory construction order (based on dependency, not a ceiling)

**Stage 0: Source and acceptance fixtures.** Curate real, licensed/consented consumer images and video with original audio; known astrometry ground truth where available; fixed catalog subset. Record their metadata, hashes and expected states. No random synthetic star photos as exclusive test data.

**Stage 1: Science and camera proof.** Implement and compare two runnable camera transition approaches. Establish pixel/WCS/frustum matching, selected-star anchor stability, interruption, return, bounded catalog positions and error reporting. Preserve records showing failed tests as well as successful ones.

**Stage 2: Reveal/Media product.** Maintain original Media Experiment Runtime, real processing workers and synchronized comparison, background job events, versioned attempts, sound-preserving export.

**Stage 3: Shared observation shell.** Implement one cross-representation session store and navigation history. Build clean source/sky/space shell with evidence classes, source checkpointer, inspector, keyboard/touch controls and reduced-motion.

**Stage 4: Voice as typed input.** Compare transport candidates, implement intent/context resolution, authorized typed action execution, idempotency, barge-in and cancellation; test ambiguous pronouns with multi-object scenes.

**Stage 5: Science exploration and investigations.** Add sightline queries, temporal modeling, surveys, reference provenance; expand planetary/satellite capabilities only as source evidence allows.

**Stage 6: Product hardening and visual finish.** Browser/device matrix, performance profiling, privacy, accessibility, audio mix correctness, user task tests, cache/versioning, reliable replay/export, independent demo reproducibility.

## Long-term frontier horizon, independent of the hackathon

The MVP is a **verification wedge**, not the definition of the final product. **[D]** If Albedian earns the observation-to-space interaction, there are five major research programs worth pursuing without assuming that all can be implemented immediately:

1. **Observation-Aware Worldline:** navigate from a historical source frame into physical scene coordinates with uncertainty-aware time propagation, changing observer frame and source light-travel semantics. **[U]** Requires consistency across inertial frames, incomplete radial velocities, changing measurement confidence, and physically meaningful rather than arbitrary interpolation.
2. **Scientific Multi-Spectral Lens:** transition from recorded visible-band sky to separate genuine surveys (infrared, ultraviolet, radio, X-ray), preserving angular registration and provenance, with sensor wavelength and observation epoch visible. **[U]** Overlay images often have very different resolution, point-spread functions, epochs, calibration and coverage, so visually aligned crossfades should not be interpreted as a source-camera restoration.
3. **Capture-to-Knowledge Feedback:** suggest future source captures and exposure strategies using actual prior signal/plate-solving failures, then compare captured evidence with astronomical predictions while preventing catalog priors from being falsely treated as observed photons. **[U]** Depends on device-level camera capabilities, experimental recovery validation and privacy.
4. **Interactive Scientific Inference:** the user's traced streak, star selection or region becomes a reproducible hypothesis query across ephemerides, catalogs, survey data and recorded footage, with alternative explanations and confidence limitations shown spatially. **[U]** Requires evidence pipelines richer than natural-language descriptions and historical data coverage.
5. **Persistent and Collaborative Observation Spaces:** replayable, shareable journeys whose original source, derivatives, viewpoints, camera paths, scientific solutions and credited surveys remain separable. **[U]** Needs long-lived object/data versioning, privacy controls, rights management, chunked rendering and resilient replay as catalogs change.

**Physical limit versus engineering limit:** No future budget, GPU or model can make a particular star's photons observable if none were captured; external catalogs can create an accurate-enough *modeled* explanation, not retroactively convert it into a photo observation. Other barriers here, such as 3D rendering scale and voice responsiveness, are primarily systems-engineering and evaluation problems, not equivalent physical impossibilities.

## Agent responsibilities (if using autonomous development)

A single lead orchestrator may delegate: **science validation** (astrometry/ephemerides), **spatial graphics** (camera/projection), **media runtime** (processing/export), **voice and trust** (typed actions/session), **experience design** (tokens/states/accessibility), **evaluation** (fixtures/benchmarks) and **integration** (end-to-end states). These are responsibility boundaries, **not** simulated visible AI workers. Each specialist must submit concrete artifacts, tests and evidence before the integration lead can mark a milestone complete.

## Prompt acceptance clauses

Include these as explicit non-negotiables:

1. **Source truth:** source immutability, audio preservation, all derivatives versioned.
2. **Science truth:** every celestial point has an evidence class, a stable identifier and traceable catalog/solver version; WCS claims require validation.
3. **Camera truth:** no arbitrary procedural starfields passed off as celestial scene; angle→depth handoff disclosed; distances are not invented.
4. **Voice truth:** model proposes only schema-conformant commands; trusted validators enforce scientific prerequisites and permissions.
5. **Motion truth:** animation uses real result states; no fake progress, no ornamental agents; all flights interruptible, reversible and reduced-motion compatible.
6. **Performance truth:** benchmark on target hardware and representative media; record actual frame pacing/latency and failure cases.
7. **Product truth:** source-first user succeeds without technical UI; researcher can inspect methods and scientific data.
8. **Reproducibility truth:** pinned dependency/data/model versions, runnable test fixtures, timestamped science and processing manifests.
9. **Licensing/privacy truth:** do not assume any web image, survey tile, game asset, voice or third-party library is unrestricted; obtain rights/consent.
10. **No unwarranted implementation claims:** do not label a prototype or render “validated” until a recorded reproducible test exists.

## Suggested implementation documents to create

`PRODUCT_THESIS.md`, `EXPERIENCE_STATES.md`, `DESIGN_TOKENS.md`, `MOTION_SPEC.md`, `CAMERA_MATH.md`, `SCIENCE_EVIDENCE.md`, `VOICE_ACTIONS.md`, `AUDIO_POLICY.md`, `PROTOTYPE_A_REVEAL.md`, `PROTOTYPE_B_SPACE.md`, `PROTOTYPE_C_INVESTIGATE.md`, `EXPERIMENTS.md`, `LICENSES.md`, `TEST_FIXTURES.md`, `RISK_REGISTER.md`, `DEMO_RUNBOOK.md`.

**Boundary:** This report provides the research and interfaces needed for a master prompt; it is **not** an implementation prompt itself, and the three prototype specifications do not imply their implementations exist.

---

# D15. Open Questions and Falsification Program

## 15.1 Prioritized hands-on experiments

| ID / priority | Unresolved question | Reproducible test | Proposed decision condition / stop signal |
|---|---|---|---|
| **X1 / P0** | Can WCS-matched photographic pixels remain aligned during FOV/camera transition? | 10+ varied real wide-angle/night photos, held-out matches, camera path per source, residual trace | GO if reproducible high accuracy for supported subset, otherwise label unsupported/projection fallback |
| **X2 / P0** | WWT custom control or Three/R3F camera rig? | Same source, star target, 3D subset, single hardware/software matrix | Choose path with measured continuity, interruptibility, profiling and API/license feasibility |
| **X3 / P0** | Can precise source checkpoint survive 3D flight? | Seek source to fractional timestamp, change crop and audio gain, leave/return, decode original frame | NO-GO for continuous experience if source/audio state cannot be restored reliably |
| **X4 / P0** | Does restoration meaningfully help real night footage? | Parent benchmark comparing source, classical, neural with temporal metrics and actual source sound | Keep Reveal only if real footage shows meaningful utility; no fabricated proof |
| **X5 / P0** | Do users perceive the scientific boundary correctly? | Show transition to 8–12 non-team testers; ask what was recorded vs predicted vs simulated | Revise motion/labels if users falsely believe catalog stars were recovered from image |
| **X6 / P1** | Is target tracking comfortable and controllable? | Test stationary/short/long camera moves, cancel and reduced motion on desktop/mobile | Reduce visual travel and add instant jump if motion discomfort or disorientation appears |
| **X7 / P1** | Can voice reliably bind “that”, “back”, “there”? | 40+ utterance-context pairs with two or more visible findings, speech overlap, mid-transition events | Fail closed on ambiguity; no wrong star or unauthorized job executed |
| **X8 / P1** | Are star distances/scale represented honestly? | Compare camera positions to Astropy-derived expected ray projections at multiple viewpoints | Reject silent distance invention and unmarked nonlinear spatial warp |
| **X9 / P1** | Does browser meet actual render/audio budgets? | Chrome/Safari desktop + supported mobiles, 1k/10k/100k stars, source video streaming, camera flight | Profile p95 dropped frames, memory and audio offset; implement tiered fallback |
| **X10 / P1** | Will live voice audio overlap/duck recording appropriately? | Recorded speech source + simultaneous voice replies, interruption, return, export | Fail if export original audio modified or two playback clocks desynchronize |
| **X11 / P1** | Is the combined product better than existing separate tools? | 5–10 minute tasks, crossover groups: Albedian vs photo editor + plate solver/WWT, satisfaction + errors | If no substantial convenience/understanding advantage, narrow to best single capability |
| **X12 / P2** | Is historical satellite identity supportable? | Recordings with known trajectory + archived orbital state; report time/location uncertainties | Display candidates only if independent pixel/ephemeris agreement; defer otherwise |
| **X13 / P2** | Does VR/spatial interaction materially improve observation? | Port same controlled camera/selection state to spatial device with comfort/accessibility study | Defer if novelty is only stereoscopic spectacle |
| **X14 / P2** | Can a shareable journey replay with licenses preserved? | Serialize source-derived script/data refs; replay on new client and offline partial cache | Reject missing provenance/assets silently replaced by generated visuals |

### How to measure success without inventing numbers

The values below are **provisional product engineering gates**, not state-of-the-art claims:

- **Geometry:** log median/p95 pixel reprojection for held-out matched stars (original resolution) per lens class. Establish source-specific acceptance before presenting `Calibrated`.
- **Transition:** log the selected star's screen-space location every frame, phase boundary jump, WCS residual, camera quaternion, FOV and world origin; inspect worst-case rather than averages only.
- **Rendering:** log p50/p95 frame times, dropped video frames, GPU memory, shader compilation delay, render source count and dynamic LOD changes.
- **Voice:** measure intent accuracy, entity resolution, false trigger rate, cancellation success, action acknowledgment latency and whether any unsupported scientific claim was voiced.
- **Restoration:** use parent document's objective reference/non-reference distinction; preserve PTS/audio and report real compute cost.
- **Scientific honesty:** test if a user can tell measured source stars from predicted sky markers and what the distance numbers actually mean.
- **Usability:** ask whether participants can restore, select, explore, return and export without training; track misclicks, time-to-completion and restoration-vs-astronomy UI interference.

## 15.2 Risk register

| Risk | Severity | Mitigation / fallback |
|---|---|---|
| Consumer video truly contains insufficient star signal | High | Keep source/restoration workflow, `Unresolved` astronomy branch; use separate valid photo for 3D proof |
| Camera model/WCS cannot represent ultrawide source accurately | High | Calibrated source-plane map, no false 3D continuity claim; constrained supported lens modes |
| Renderer API doesn't support selected-anchor film projection | High | Custom Three camera for transition, WWT/Aladin as secondary exploration surface |
| Gaia distance poorly constrained / no radial motion | High | Quality filter, uncertainty display, 2D-only fallback, epoch limits |
| Multi-canvas WebGL memory/performance collapse | High | One active high-cost renderer at a time, pre-rendered handoff texture if needed, adaptive LOD |
| Source audio resumes incorrectly | High | One PTS clock, audio state snapshot and automated seek/sync regression tests |
| Voice hallucinates a specific object or result | High | Typed validator, stable IDs, provenance-only science facts, user disambiguation |
| Temporal renderer implies thousand-year real observation | Medium/High | Persistent `Modeled Sky`, explicit source epoch, no original photo repaint |
| UI labels obscure stars and faces | Medium | Progressive disclosure, list alternative, deconfliction and safe zones |
| Accessibility or motion sickness | High | Reduced-motion modes, skip/instant navigation, keyboard/touch parity |
| Dataset and game/image rights | Medium/High | Dependency license/asset rights ledger, attribution, separate original media permissions |
| Hackathon demo is mostly precomputed montage | High | Live end-to-end runbook, artifact verifiability, judges can retry source and actions |

## 15.3 Final architecture and experience recommendation

**[D] Commit to the concept of a single `ObservationSession` that unifies four representations, with a common evidence-aware state and two intentional UX modes: `Reveal` and `Explore`.** The core underlying components remain the parent Media Experiment Runtime and a distinct verified Observation Solution. `NavigationHistory`, `CameraRig`, `FindingRef` and a typed `ActionRegistry` are the new critical primitives.

**[I] Start the most difficult research with Prototype B's reversible camera and geometry continuity** while concurrently keeping Prototype A's useful media restoration intact. The ability to *return* to the real moment is as central as the flight outward. If the custom flight cannot preserve astronomical validity and usable performance, a WCS-aligned sky overlay plus selected-object science view is the honest fallback, not a particle-space animation.

**[U] Open claims:** seamless exact-pixel registration for broad camera types, strong enhancement of extreme-night video, voice deictic resolution during concurrent scene motion, scientific quality at large nonlinear distances, historical satellite verification, and actual competitor-relative user value. Those are not solved by this design specification.

**Albedian's product test:** The user says “Take me there” and understands what traveled: **their point of view, not their photograph's evidence.** When they say “Go back,” the sound and frame of their recorded night are exactly where they left them.

---

# Source Register: Primary Documentation and Observed References

Sources inspected through 31 October 2026. A source may document an API without establishing whether Albedian has tested or integrated it. Some commercial documentation changes frequently; pin and re-verify implementation versions.

- **[S01]** WorldWide Telescope, [WebGL Engine Reference Manual](https://docs.worldwidetelescope.org/webgl-reference/latest/): reusable JS/TS engine and embedding models.
- **[S02]** WorldWide Telescope, [Getting Started and Rendering Modes](https://docs.worldwidetelescope.org/webgl-reference/latest/getting-started/); [Explore 3D Universe and Surveys](https://worldwidetelescope.org/Learn/Exploring): embed options, sky vs universe use.
- **[S03]** WorldWide Telescope, [Engine GitHub and MIT License](https://github.com/WorldWideTelescope/wwt-webgl-engine/blob/master/LICENSE): software license.
- **[S04]** CDS, [Aladin Lite v3 Documentation](https://aladin.cds.unistra.fr/AladinLite/doc/): WebGL, surveys, Rust/WASM, embed terms, layers and catalog capabilities.
- **[S05]** CDS, [Aladin Lite v3 API Reference](https://aladin.cds.unistra.fr/AladinLite/doc/API/): WCS/methods and events. Also [current API](https://cds-astro.github.io/aladin-lite/).
- **[S06]** Astropy, [WCS Full Pixel/World Methods](https://docs.astropy.org/en/stable/api/astropy.wcs.WCS.html): distortion-aware transformations.
- **[S07]** Astropy, [SIP/WCS Convention and Caveats](https://docs.astropy.org/en/stable/wcs/note_sip.html): correct distortion handling.
- **[S08]** Gaia Sky, [Current Official Documentation](https://gaia.ari.uni-heidelberg.de/gaiasky/docs/master/index.html): 3D astronomy software and scripting.
- **[S09]** Gaia Sky, [Star Rendering Documentation](https://gaia.ari.uni-heidelberg.de/gaiasky/docs/3.9.0/Star-rendering.html): actual star-size/brightness pipeline design.
- **[S10]** NASA, [Eyes and Other Interactives](https://www.nasa.gov/interactives/): modeled Solar System/asteroids/exoplanets, time navigation.
- **[S11]** Three.js, [Camera API](https://threejs.org/docs/pages/Camera.html): world, inverse and projection camera matrices.
- **[S12]** Three.js, [WebGLRenderer](https://threejs.org/docs/pages/WebGLRenderer): render parameters and depth-buffer options.
- **[S13]** React Three Fiber, [Scaling Performance](https://github.com/pmndrs/react-three-fiber/blob/master/docs/advanced/scaling-performance.mdx): on-demand rendering and invalidation.
- **[S14]** React Three Fiber, [Frame Loop Scheduler](https://r3f.docs.pmnd.rs/next/frame-loop): manual/demand rendering and phases.
- **[S15]** Astropy, [SkyCoord and Space Motion](https://docs.astropy.org/en/stable/api/astropy.coordinates.SkyCoord.html).
- **[S16]** Astropy, [AltAz Observer/Refraction](https://docs.astropy.org/en/stable/api/astropy.coordinates.AltAz.html).
- **[S17]** Apple Support, [Photos Mac: Compare Original and Edits](https://support.apple.com/en-euro/guide/photos/-pht304c2ace6/mac): hold Compare/M, immutable original and reversibility.
- **[S18]** Apple, [Focus and Selection HIG](https://developer.apple.com/design/human-interface-guidelines/focus-and-selection/): selection/focus semantics.
- **[S19]** Apple, [Spatial Layout HIG](https://developer.apple.com/design/human-interface-guidelines/spatial-layout/): spatial comfort/gestures.
- **[S20]** Apple, [Ornaments HIG](https://developer.apple.com/design/human-interface-guidelines/ornaments/): related controls adjacent to content.
- **[S21]** Apple, [Motion HIG](https://developer.apple.com/design/human-interface-guidelines/motion): purposeful, optional motion.
- **[S22]** Apple, [Accessibility HIG](https://developer.apple.com/design/human-interface-guidelines/accessibility): reduce z-movement/flash/motion.
- **[S23]** Motion, [React Layout Animations](https://motion.dev/docs/react-layout-animations): interruptible shared-layout motion.
- **[S24]** Rive, [Runtime State Machines](https://github.com/rive-app/help-center/blob/master/runtimes/state-machines.md): state inputs and transitions.
- **[S25]** Rive, [Runtime Licensing](https://github.com/rive-app/help-center/blob/master/runtimes/overview.md): MIT-licensed runtimes, separate design assets/editor policy.
- **[S26]** React Three Fiber, [Performance and Render Scheduling](https://github.com/pmndrs/react-three-fiber/blob/master/docs/advanced/scaling-performance.mdx): static scenes and invalidation.
- **[S27]** Three.js, [Renderer Depth Buffers and MIT package](https://threejs.org/docs/pages/WebGLRenderer); [npm license](https://www.npmjs.com/package/three).
- **[S28]** Astropy, [AltAz Frames](https://docs.astropy.org/en/stable/api/astropy.coordinates.AltAz.html): time/location and refraction cautions.
- **[S29]** Astropy, [apply_space_motion](https://docs.astropy.org/en/stable/api/astropy.coordinates.SkyCoord.html): linear motion assumptions.
- **[S30]** OpenAI API, [Realtime Getting Started](https://developers.openai.com/api/docs/guides/realtime): browser speech-to-speech and tool calling.
- **[S31]** OpenAI API, [Realtime Conversations](https://developers.openai.com/api/docs/guides/realtime-conversations): interruption/truncation and push-to-talk.
- **[S32]** OpenAI API, [Realtime Server Events](https://developers.openai.com/api/reference/resources/realtime/server-events): VAD and semantic VAD options.
- **[S33]** OpenAI Agents SDK, [Realtime Transport Layer](https://openai.github.io/openai-agents-js/guides/voice-agents/transport/): WebRTC and backend-side authorization trust boundaries.
- **[S34]** LiveKit, [Turn-taking and Interruptions](https://docs.livekit.io/agents/logic/turns/): speech and interruption handling.
- **[S35]** LiveKit, [Turn Tuning](https://docs.livekit.io/agents/logic/turns/tuning/): endpointing and false interruption handling.
- **[S36]** Apple, [Siri HIG](https://developer.apple.com/design/human-interface-guidelines/siri/): concise contextual speech UX.
- **[S37]** MDN, [Web Audio API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API): mixing graph primitives.
- **[S38]** MDN, [Using Web Audio API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API/Using_Web_Audio_API): gain automation.
- **[S39]** MDN, [Autoplay Policies](https://developer.mozilla.org/en-US/docs/Web/Media/Guides/Autoplay): playback and user gesture constraints.
- **[S40]** WWT, [Explore from Any Angle](https://worldwidetelescope.org/Learn/Exploring): 3D and multiwavelength survey modes.
- **[S41]** ESA, [Gaia Mission Factsheet](https://www.esa.int/Science_Exploration/Space_Science/Gaia/Gaia_factsheet) and STScI [Gaia Overview](https://archive.stsci.edu/missions-and-data/gaia): catalog dimensionality, measurements, limitations.
- **[S42]** Bungie, [Director Navigation](https://help.bungie.net/hc/en-us/articles/45946334996756--7-Understanding-The-Director): destinations and UI functions.
- **[S43]** Bungie, [Problems with Director Complexity](https://www.bungie.net/7/en/News/Article/theportal): explicit prior interaction challenges.
- **[S44]** Bungie, [Return of the Director](https://www.bungie.net/7/en/News/Article/dev_insights_return_of_the_director): 2026 revisions and wayfinding.
- **[S45]** Halo Support, [TacMap Navigation and Waypoints](https://support.halowaypoint.com/hc/en-us/articles/4408175405716-Halo-Infinite-Support-Glossary).
- **[S46]** Visual image references used for authored interpretation: [Detroit investigation screenshot](https://blog.retinadesgastada.com.br/2023/02/jogando-detroit-become-human.html), [public game flow screenshots](https://steamcommunity.com/sharedfiles/filedetails/?id=2146507135). These are third-party visual references, not endorsed or licensed for reuse.
- **[S47]** affectUI, [Agent Graph Documentation](https://www.affectui.com/components/agent-graph): real call-based animations, interaction semantics and Pro license status.
- **[S48]** affectUI, [Documentation and Licensing Overview](https://www.affectui.com/docs): free vs Pro components.

## Research method and honest omissions

Research was conducted using published product docs, primary technical/API docs, actual public source repositories and selected publicly retrievable interface screenshots. **No Albedian code was executed, no private Meta-glasses night videos were analyzed, no WWT/Three/Aladin integration benchmark was performed, no voice session was opened, and no usability study was conducted in preparing this document.** This report therefore deliberately separates source-backed capabilities from the proposed design system and high-risk engineering hypotheses. Frame rates, durations, target residuals and visual tokens are targets to validate, not measurements.

**End of Albedian experience research.**
