# Design: Voice-Native Actions & Context Resolution

## Architecture
- **Packages:** `@albedian/contracts` (`AlbedianActionSchema`, `ActionContextSchema`, `ActionOutcomeSchema`, `VoiceCommandRequestSchema`, `VoiceCommandResponseSchema`), `@albedian/observation` (`resolveVoiceUtterance`, `validateAndDispatchAction`), `services/voice/intent.py`, `services/voice/claude.py`, `apps/web/src/voice/voiceBackend.ts`

## Correctness Properties
- **Property PBT-VOICE-1 (Revision & Ambiguity Safety):** Any `AlbedianAction` with `expectedViewRevision !== session.viewRevision` or referencing a non-existent/uncalibrated `findingId` is rejected with `status: 'rejected'` and leaves `ObservationSession` state bitwise unchanged.
- **Property PBT-VNA-2 (Partial History-Back Chaining):** Applying `history.back` with `mode: 'partial'` and fractions `f₁, f₂ ∈ (0,1)` in sequence SHALL NOT overshoot the baseline checkpoint; the camera position after two partial backs MUST remain between the two surrounding checkpoints.
- **Property PBT-VNA-3 (Stale Pointer Clock-Skew Guard):** A Unix-epoch pointer timestamp is fresh IFF `0 ≤ Date.now() − atTimestampMs ≤ 10_000`. Pointers with `atTimestampMs > Date.now() + 60_000` (future-dated, clock skew) SHALL also be rejected as stale.
- **Property PBT-VNA-4 (Input Bound, Year-Zero Refusal, Unconnected Overlay & Capture Epoch):** `VoiceCommandRequest.utterance` is capped at 2,000 UTF-16 code units without echoing the raw utterance on validation error; astronomical year `0` is rejected in both parsers and `SpaceEpoch.yearCE`; `ui.overlay` is refused with `reason: 'not_connected'` until a consumer is wired; and predicted Solar System bodies are evaluated at the recording's `observedAtUTC`.
