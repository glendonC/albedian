# Design: Voice-Native Actions & Context Resolution

## Architecture
- **Packages:** `@albedian/contracts` (`AlbedianActionSchema`, `ActionContextSchema`, `ActionOutcomeSchema`), `@albedian/observation` (`resolveVoiceUtterance`, `validateAndDispatchAction`), `services/voice/intent.py`

## Correctness Properties
- **Property PBT-VOICE-1 (Revision & Ambiguity Safety):** Any `AlbedianAction` with `expectedViewRevision !== session.viewRevision` or referencing a non-existent/uncalibrated `findingId` is rejected with `status: 'rejected'` and leaves `ObservationSession` state bitwise unchanged.
- **Property PBT-VNA-2 (Partial History-Back Chaining):** Applying `history.back` with `mode: 'partial'` and fractions `f₁, f₂ ∈ (0,1)` in sequence SHALL NOT overshoot the baseline checkpoint; the camera position after two partial backs MUST remain between the two surrounding checkpoints.
- **Property PBT-VNA-3 (Stale Pointer Clock-Skew Guard):** A Unix-epoch pointer timestamp is fresh IFF `0 ≤ Date.now() − atTimestampMs ≤ 10_000`. Pointers with `atTimestampMs > Date.now() + 60_000` (future-dated, clock skew) SHALL also be rejected as stale.
