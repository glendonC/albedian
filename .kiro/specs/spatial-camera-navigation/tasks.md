# Tasks: Spatial Camera & Scene Navigation

- [x] 1. Implement `SourceProjection` (TAN + radial $k_1, k_2$ distortion pixel-to-world and world-to-pixel transforms) in `packages/camera-rig`.
  - Traced to: `packages/camera-rig/src/__tests__/camera-rig.test.ts`: `PBT-CAM-1`
- [x] 2. Implement `ObservationCamera`, quaternion SLERP, log-tan-half-FOV interpolation, and off-axis `AnchorConstraint`.
  - Traced to: `packages/camera-rig/src/__tests__/camera-rig.test.ts`: `PBT-CAM-2`; `packages/camera-rig/src/__tests__/experiments.test.ts`: `EXP-05`
- [x] 3. Implement double-precision floating-origin 3D starfield projection, sightline cone query (`What's behind it?`), and proper-motion epoch propagation (`1026 CE`).
  - Traced to: `packages/camera-rig/src/__tests__/camera-rig.test.ts`: `PBT-CAM-3`, `propagates catalog proper motion...`; `packages/camera-rig/src/__tests__/experiments.test.ts`: `EXP-06`, `EXP-07`
- [x] 4. Benchmark WWT adapter vs. custom Three.js/WebGL camera rig (`EXP-05`, `EXP-06`, `EXP-07`, `EXP-11`) and verify `PBT-CAM-1..3`.
  - Traced to: `packages/camera-rig/src/__tests__/experiments.test.ts`: `EXP-07 & EXP-11`
- [x] 5. Add southern-hemisphere circumpolar coverage (`decDeg ∈ [-90, -75]`) and multi-step `history.back` partial-fraction chaining regression tests.
  - Traced to: `packages/camera-rig/src/__tests__/camera-rig.test.ts`: `PBT-COORD-1-SOUTH`, `PBT-CAM-2-SOUTH`; `packages/observation/src/__tests__/observation.test.ts`: `PBT-NAV-HISTBACK-CHAIN`
