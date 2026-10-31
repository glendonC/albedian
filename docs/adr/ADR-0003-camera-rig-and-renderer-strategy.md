# ADR-0003: Camera Rig Architecture & Renderer Adapter Strategy

- **Status:** Accepted
- **Date:** 2026-10-31
- **Deciders:** Principal Orchestrator (`spatial`, `architecture`)

## Context

Albedian's signature interaction transitions from a user's 2D night recording (`Original Image`) to an angularly calibrated celestial sphere (`Registered Sky`), across an explicit epistemic boundary into a 3D stellar neighborhood (`Spatial Exploration`), and back (`Original Image`) with zero perceptual jump on the selected anchor star.

We evaluated two primary rendering architectures (`EXP-07`):
1. **WorldWide Telescope (WWT) WebGL Engine** as the primary transition canvas.
2. **Custom WCS-locked Three.js / WebGL Camera Rig** (`packages/camera-rig`) with an adapter interface for external survey layers (WWT / Aladin Lite).

## Decision

1. **Primary Transition & 3D Engine:** Use a **custom WCS-locked Camera Rig (`packages/camera-rig`) + Three.js/WebGL viewport** for the `Original Image → Registered Sky → Spatial Exploration → Original Image` continuum.
   - **Why:** Maintaining $< 1\text{ px}$ screen-space lock on an arbitrary off-center star during FOV and orientation convergence requires an off-axis frustum projection offset (`AnchorConstraint`) coupled with per-frame WCS/SIP distortion evaluation, $\log(\tan(\text{FOV}/2))$ zoom interpolation, and double-precision (`Float64Array`) floating-origin rebasing. Off-the-shelf planetarium embeds force center-of-viewport camera targeting or lack synchronous frame-locked alpha compositing with HTML5 `<video>`.
2. **Renderer Adapter Contract:** Define a clean `SpatialRendererAdapter` interface in `packages/contracts` so survey overlays (Aladin Lite HiPS / WWT deep links) can be mounted in `Inspect` mode without coupling scientific findings to a single rendering library.
3. **Two-Phase Boundary Enforcement:**
   - **Phase A (`source_to_sky`):** Purely angular transformation ($d = 0$ observer offset). Zero parallax is applied to foreground terrain or stars.
   - **Phase B (`sky_to_space`):** Displays `Catalog Geometry · Distance Estimates` disclosure and enables 3D translation in parsecs relative to a floating origin.

## Consequences

- Guarantees mathematical reversibility, sub-pixel anchor stability, deterministic property-based testing in headless Node/Vitest, and 60fps browser performance.
