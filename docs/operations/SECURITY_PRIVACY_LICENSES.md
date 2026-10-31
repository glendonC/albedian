# Security, Privacy, Licensing, Risk Register & Demo Runbook

## 1. Security & Privacy Controls

1. **Local-First Processing:** Media probing, restoration DAG execution, star extraction, and WCS plate solving run locally by default without uploading personal videos, faces, voices, or GPS coordinates to third-party servers.
2. **Immutable Sandboxed Storage:** Uploaded files are validated for MIME/container safety, hashed (`SHA-256`), and stored under `.runtime/storage/` (excluded from Git via `.gitignore`).
3. **Explicit Microphone Permission:** Voice capture requires an explicit user gesture (`Push-to-Talk`). No background listening occurs.
4. **Metadata Sanitization on Share/Export:** Export manifests allow stripping EXIF GPS coordinates while retaining celestial calibration (`RA/Dec`) when sharing publicly.

---

## 2. Third-Party Licensing & Data Attribution Ledger

| Component / Dataset | License / Terms | Usage in Albedian | Attribution / Compliance |
|---|---|---|---|
| **Albedian Source Code** | MIT License | Core repository | Root `LICENSE` file |
| **ESA Gaia DR3 Subset** | CC BY-SA 3.0 IGO / ESA Archive Terms | Bright-star astrometry, parallaxes, proper motions | Credited in UI inspector & export manifest: *"Credit: ESA/Gaia/DPAC"* |
| **CDS Hipparcos / Bright Star Cross-Match** | CDS Academic & Open Scientific Attribution | Bayer/common star names & cross-IDs | Credited in UI inspector & export manifest |
| **Bailer-Jones et al. (2021) Distance Priors** | Open Scientific Publication | Photogeometric distance intervals (`lower`, `median`, `upper` pc) | Cited in `ScientificEvidence` records |
| **Astropy / Photutils / NumPy / SciPy** | BSD 3-Clause | Coordinate transformations, WCS math, star extraction | Compatible with MIT distribution |
| **FFmpeg / OpenCV (`opencv-python-headless`)** | LGPL / Apache 2.0 | Subprocess video decode/encode & deterministic image filters | Invoked via clean subprocess / Apache-2.0 Python bindings |
| **Three.js / React / Vitest / fast-check / Zod** | MIT License | Web shell, 3D viewport, contracts, property testing | Compatible with MIT distribution |

---

## 3. Risk Register

| Risk ID | Description | Severity | MitigationImplemented |
|---|---|---|---|
| **R-01** | Consumer clip has zero detectable stars (heavy cloud/clipping) | High | Deterministic `unresolved` state + `No Reliable Sky Detail` disclosure; media restoration (`Reveal`) remains 100% functional; optional `Predicted Sky` from time/location. |
| **R-02** | Restoration filter attenuates faint stars or invents bright noise | High | `star_protect_merge` DAG node masks source-detected star centroids (`observed`) before background denoising and audits post-process false point count. |
| **R-03** | Anchor star jumps on screen during `Photo → Registered Sky` transition | High | Off-axis frustum projection offset solved per-frame (`EXP-05`), keeping anchor residual $< 0.05\text{ px}$. |
| **R-04** | Float32 jitter during multi-parsec 3D navigation | Medium | Double-precision `Float64Array` catalog state with camera-relative floating-origin subtraction before GPU upload (`EXP-06`). |
| **R-05** | Stale async voice/tool completion mutates scene after user cancels | Medium | Strict `expectedViewRevision` and `transitionId` cancellation tokens enforced in session reducer (`EXP-09`). |

---

## 4. Reproducible 3-Minute Demo Runbook

1. **Launch:** Run `pnpm build && pnpm preview` (or `pnpm dev` + `uv run uvicorn services.api:app --port 8000`).
2. **0:00–0:35 (`Reveal`):** Load the default **Sahara Night — Orion & Taurus Over synthetic site** observation session (or upload any MP4/JPEG). Inspect the dark original frame and ambient audio. Issue command: `"Make the sky clearer. Don't change the people."` Compare `Original`, `Natural Tone + Star-Protected Denoise`, and `Motion-Compensated Temporal Stack` with the synchronized 1:1 wipe slider and inspect the `Processing Graph` DAG.
3. **0:35–1:15 (`Register`):** Click `Calibrate Sky` or ask `"What were we looking at?"` Inspect the verified WCS solution (`14 Matches · Held-out Median Residual 0.42 px · Gaia DR3`), toggling between `Observed` pixel detections, `Catalog Matched` stars, and `Predicted` planetary ephemerides (Jupiter / Uranus).
4. **1:15–2:15 (`Explore`):** Select **Betelgeuse ($\alpha$ Ori)** or **Rigel ($\beta$ Ori)**. Ask `"How far away is it?"` to inspect the Bailer-Jones distance interval. Say `"Take me there."` Experience the two-phase signature transition:
   - Phase A: Angular sky lock with zero anchor drift.
   - Phase B: `Catalog Geometry · Distance Estimates` disclosure and 3D parsec flight into the Orion neighborhood.
   - Orbit viewpoint to watch the Orion constellation deform from 3D perspective while the `Earth Direction` vector remains pinned.
   - Ask `"Show me this sky a thousand years ago"` (`1026 CE`) or `"What's behind it?"` to inspect sightline cone depth ordering.
5. **2:15–3:00 (`Return & Export`):** Say `"Take me back."` Verify exact restoration of `sourceTimeSec`, crop, selected star, and original audio state. Click `Export` to generate the reproducible MP4/still artifact and JSON provenance manifest.
