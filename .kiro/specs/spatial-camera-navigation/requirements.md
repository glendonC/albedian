# Requirements: Spatial Camera & Scene Navigation

## Introduction
Defines the `ObservationCamera` rig, WCS pixel↔world projection math, off-axis anchor lock, quaternion SLERP, $\log(\tan(\text{FOV}/2))$ zoom interpolation, double-precision floating-origin 3D rendering, and the two-phase signature transition.

## Requirements

### REQ-SCN-1: Anchor-Locked Phase A Transition (`Photo -> Registered Sky`)
**User Story:** As a user selecting a star and initiating exploration, I want that exact star to remain perceptually anchored on screen while the photograph aligns with the calibrated celestial sphere.
- **Acceptance Criteria:**
  1. During Phase A ($t \in [0, 1]$), THE camera rig SHALL solve an off-axis frustum shift $(\Delta_x(t), \Delta_y(t))$ so the selected anchor star remains within $\le 0.5\text{ CSS px}$ of its initial screen position at every sampled progress value. The implementation MUST satisfy `anchorDriftPx < 1e-5` as verified by PBT-CAM-2 (`packages/camera-rig/src/__tests__/camera-rig.test.ts`).
  2. Zero 3D parallax SHALL be applied to foreground terrain during Phase A.
  3. WHEN the anchor finding's `decDeg` is in the range $[-90, -75]$ (southern circumpolar), THE off-axis frustum computation SHALL still converge within the same drift tolerance; high-latitude gimbal singularities in the tangent basis SHALL be handled by clamping `|decDeg| ≤ 89.9°` before basis construction.

### REQ-SCN-2: Disclosed Phase B 3D Flight & Floating Origin (`Registered Sky -> 3D Space`)
**User Story:** As a user saying "Take me there," I want to fly through catalog-backed 3D stellar space, inspect alternate viewpoints and sightlines, and see clearly what is measured vs. simulated.
- **Acceptance Criteria:**
  1. Entering Phase B SHALL display the `Catalog Geometry · Distance Estimates` disclosure and enable 3D parsec translation using a double-precision (`Float64Array`) floating origin.
  2. Rotating the viewpoint around a target star SHALL recompute 3D catalog star projections in real time while preserving an explicit `Earth Direction` reference vector.
  3. WHEN a `CatalogReference` carries a negative `parallaxMas` (Gaia quality flag for objects too faint/extended for a reliable geometric solution), THE system SHALL NOT derive a distance via `1000 / parallaxMas`; it SHALL fall back to `bailerJonesDistancePc.median` if present, or mark the finding as having `distancePolicy: 'unavailable'`.
