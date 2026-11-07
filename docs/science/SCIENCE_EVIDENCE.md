# Scientific Evidence Rules & Camera Projection Mathematics

## 1. Epistemic Taxonomy (`EvidenceClass`)

Every `CelestialFinding` in Albedian carries one of four mutually exclusive evidence classes:

| Class | Requirement | Visual Marker Grammar | Forbidden Claim |
|---|---|---|---|
| `observed` | Point or streak extracted from immutable `SourceAsset` pixels with `snr >= 3.5` | Solid center dot (`#D1E9EF`) with hairline tick at measured `(xPx, yPx)` | Naming the star from pixel brightness alone, or giving it a sky position, without verified WCS |
| `catalog_matched` | `observed` detection linked to a pinned catalog entry via verified WCS (`heldOutInliers >= 6`, `medianResidualPx <= 3.0`) | Concentric precision ring (`#81C5CC`) around observed centroid + elbow-connected label | Claiming zero distance uncertainty or phone-measured depth |
| `predicted` | Computed from observer UTC timestamp, GPS coordinates, and ephemeris/catalog | Dashed open circle (`#D9AE75`) + `Predicted` badge | Rendering as synthetic camera pixels or claiming sensor detection |
| `simulated` | 3D catalog-geometry rendering, modeled bodies and scenes, alternate viewpoint, or non-observation epoch (`simulationEpoch != observationUTC`) | Bracketed reticle (`#ABB0D6`) + `Simulated` badge and `"Drawn or computed by the app; not measured in the source recording."` disclosure, with each layer naming its own source and limits | Presenting artistic star surfaces or unmarked non-Euclidean warps |

---

## 2. Four Explicit Coordinate Systems

1. **Image Pixel Coordinates:** $p = (u, v) \in [0, W) \times [0, H)$ at canonical frame timestamp $t_{\text{media}}$.
2. **Undistorted Camera Ray:** Unit vector $\hat{r}_{\text{cam}}(u, v) \in S^2$ after inverting focal scale and radial polynomial lens distortion ($k_1$, $k_2$; the camera model has no SIP terms):
   $$x_n = \frac{u - c_x}{f}, \quad y_n = \frac{v - c_y}{f}, \quad r^2 = x_n^2 + y_n^2$$
   $$x_d = x_n(1 + k_1 r^2 + k_2 r^4), \quad y_d = y_n(1 + k_1 r^2 + k_2 r^4)$$
3. **Celestial Direction (ICRS / J2000 / Epoch $T$):** Right ascension $\alpha$ and declination $\delta$ mapped via gnomonic (TAN) projection and tangent-plane CD matrix around reference point $(\alpha_0, \delta_0)$:
   $$\hat{r}_{\text{sky}}(\alpha, \delta) = \begin{bmatrix} \cos\delta \cos\alpha \\ \cos\delta \sin\alpha \\ \sin\delta \end{bmatrix}$$
4. **Cartesian Scientific Space (Parsecs, Floating Origin):**
   $$\mathbf{X}_i(T) = d_i \cdot \hat{r}_{\text{sky}}(\alpha_i(T), \delta_i(T)), \quad \mathbf{X}_{\text{local}, i} = \mathbf{X}_i(T) - \mathbf{C}_{\text{origin}}$$
   where $d_i$ is the Bailer-Jones median distance estimate (in parsecs) with confidence interval $[d_{\text{lower}}, d_{\text{upper}}]$, and $\mathbf{C}_{\text{origin}} \in \mathbb{R}^3$ is stored in IEEE-754 double precision (`Float64Array`) before subtracting to form camera-relative `Float32Array` GPU coordinates.

---

## 3. Anchor-Locked Signature Transition Math

During **Phase A (`Photo to Registered Sky`)**, for a selected star $P$ at initial normalized device coordinates $\text{NDC}_0 = (s_x, s_y) \in [-1, 1]^2$:
- Orientation interpolates via unit quaternion SLERP: $q(t) = \text{slerp}(q_0, q_1, \sigma(t))$.
- Field of view interpolates in logarithmic tangent half-angle space so apparent zoom rate is constant:
  $$\theta(t) = 2 \arctan\left( \exp\left( (1 - \sigma(t)) \ln\tan\frac{\theta_0}{2} + \sigma(t) \ln\tan\frac{\theta_1}{2} \right) \right)$$
- Off-axis frustum shift $(\Delta_x(t), \Delta_y(t))$ is solved at each frame $t \in [0, 1]$ so that:
  $$\Pi(q(t), \theta(t), \Delta(t), \hat{r}_{\text{sky}}(P)) \equiv \text{NDC}_0$$
  preserving the anchor star's screen coordinate to within $< 10^{-6}\text{ NDC}$ ($< 0.01\text{ px}$; `anchorDriftPx` in `packages/camera-rig/src/projection3d.ts` measures the IEEE-754 floating-point round-trip residual of `rawNdc + (anchorTargetNdc - rawNdc)`).
