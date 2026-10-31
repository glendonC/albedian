# Design: Spatial Camera & Scene Navigation

## Architecture
- **Package:** `@albedian/camera-rig` (`packages/camera-rig/src/index.ts`)
- **Web Renderer:** `apps/web/src/spatial/SpatialStage.tsx`
- **Sub-components:** `SourceProjection` (gnomonic TAN + radial/SIP distortion), `AngularSkyCamera` (quaternion SLERP + log-tan-half-FOV + off-axis anchor constraint), `SpatialObserver` (double-precision parsec floating origin), `TransitionController` (interruptible, reversible state machine).

## Correctness Properties
- **Property PBT-CAM-1:** `worldToPixel(pixelToWorld(u, v))` round-trips within $< 10^{-4}\text{ px}$ across supported FOV and distortion parameters. Verified in `packages/camera-rig/src/__tests__/camera-rig.test.ts`.
- **Property PBT-CAM-2:** Selected anchor star screen-space drift during Phase A ($t \in [0, 1]$) is $< 0.05\text{ px}$ (implementation achieves $< 10^{-5}\text{ px}$). Verified in `packages/camera-rig/src/__tests__/camera-rig.test.ts` and `experiments.test.ts` `EXP-05`.
- **Property PBT-CAM-3:** Floating-origin rebasing preserves relative 3D star vectors within $10^{-4}\text{ pc}$ at arbitrary parsec offsets. Verified in `packages/camera-rig/src/__tests__/camera-rig.test.ts`.
- **Property PBT-COORD-1-SOUTH:** `raDecToUnitVector`/`unitVectorToRaDec` round-trip at southern circumpolar declinations ($\text{decDeg} \in [-90, -75]$) produces separation $< 10^{-6}\text{ arcsec}$ and unit-norm error $< 10^{-14}$. Verified in `packages/camera-rig/src/__tests__/camera-rig.test.ts`.
- **Property PBT-CAM-2-SOUTH:** Off-axis Phase A anchor constraint at southern circumpolar declinations ($\text{decDeg} \approx -89.5°$) stays within the same $0.05\text{ px}$ drift bound. Verified in `packages/camera-rig/src/__tests__/camera-rig.test.ts`.
- **Negative Gaia Parallax Guard:** `propagateCatalogPositionToYear` MUST NOT derive a distance via `1000 / parallaxMas` when `parallaxMas ≤ 0`; it falls back to `bailerJonesDistancePc.median` or a fixed 100 pc sentinel.
