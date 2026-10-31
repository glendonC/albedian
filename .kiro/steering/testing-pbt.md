---
inclusion: fileMatch
fileMatchPattern: "**/*test*"
---

# Property-Based Testing & Verification Steering

Every core mathematical or state invariant must be verified with property-based tests (`fast-check` in TypeScript, `hypothesis` in Python):

1. **Camera Projection:** `pixelToWorld` and `worldToPixel` round-trip within tolerance over valid FOV and distortion bounds; anchor NDC remains stable during Phase A transition.
2. **Navigation:** `source.return` restores the exact saved `CameraCheckpoint` after arbitrary 3D navigation sequences.
3. **Evidence:** `predicted` and `simulated` findings can never be promoted to `observed` or `catalog_matched` without verified pixel + WCS proof.
4. **Processing DAG:** Acyclic invariant, topological execution order, immutable `SourceAsset` hash, and deterministic cache key invalidation.
5. **Voice Actions:** Stale `expectedViewRevision` actions are rejected without mutating state.
6. **Media Sync:** Variant frame timestamps and audio stream offsets remain within declared tolerance.
7. **Scientific Coordinates:** Unit vectors stay normalized and Bailer-Jones distance bounds satisfy `0 < lower <= median <= upper`.
