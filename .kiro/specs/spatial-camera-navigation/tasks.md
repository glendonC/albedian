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
- [x] 6. Fly through the catalog star field after Go there (REQ-SCN-2): each star at its catalog distance or kept on the sky sphere when it has no usable one, the photo's own WCS as the camera at any place in space, brightness by the inverse square law, positions rebased in float64 on the star flown to (flight-1).
  - Traced to: `packages/camera-rig/src/__tests__/flight3d.test.ts`; `apps/web/src/spatial/flight/__tests__/flightFrame.test.ts`, `flightStarsLayer.test.tsx`
- [x] 7. Draw a real NASA craft (NASA 3D Resources: Voyager, Webb, an astronaut) in the photo's own view during the flight, turned toward the star flown to and lit from the Sun's real direction, and compute a craft's real travel time from its NASA-sourced speed (pilot-1 mechanics; the picker, framing, light and words wait on the design lane's record, the choice is by page address `?craft=` until then).
  - Traced to: `packages/camera-rig/src/__tests__/chase.test.ts`; `apps/web/src/spatial/flight/__tests__/craftScene.test.ts`, `flightCraftLayer.test.tsx`
- [x] 8. Show the photo's view cone in the third person after Go there (cone-1 mechanics): its apex at Earth, its edges along the photo's corner rays through the verified solve, the photo laid on its image plane at the base, the catalog stars around it at their distances through an orbit camera that follows a drag and the wheel; on by page address `?view=cone` until the design lane's record names its control and look.
  - Traced to: `packages/camera-rig/src/__tests__/cone.test.ts`; `apps/web/src/spatial/flight/__tests__/coneView.test.ts`
