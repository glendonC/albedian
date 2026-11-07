# Requirements: Voice-Native Actions & Context Resolution

## Introduction
Defines the voice and typed command pipeline (`Speech/Text -> Intent -> Context Resolution -> Typed Action -> Validation -> Domain Execution -> Minimal Feedback`).

## Requirements

### REQ-VNA-1: Deictic Pronoun & Context Resolution
**User Story:** As a user pointing at or selecting a star and asking "What's that one?", "How far away is it?", or "Take me there," I want the system to resolve my reference accurately or ask for clarification when ambiguous.
- **Acceptance Criteria:**
  1. THE context resolver SHALL resolve references in strict priority: explicit named object -> active `selectedFindingId` -> contemporaneous pointer (`<= 10s`, matching `viewRevision`) -> unique visible candidate -> `needs_input` disambiguation.
  2. THE system SHALL reject any action whose `expectedViewRevision` does not match the session's current `viewRevision`, returning `status: 'rejected'` with `reason: 'stale_revision'` and leaving `ObservationSession` state bitwise unchanged.
  3. WHEN a pointer's `atTimestampMs` encodes a Unix epoch timestamp (`>= 1_000_000_000_000`), THE system SHALL treat the pointer as stale IF `Date.now() - atTimestampMs > 10_000` OR `atTimestampMs - Date.now() > 60_000` (clock skew guard).
  4. WHEN the session provides two or more visible candidate findings within the pointer cone, THE context resolver SHALL return `status: 'needs_input'` rather than selecting arbitrarily.

### REQ-VNA-2: Interruption, Barge-In & Non-Fabrication
**User Story:** As a user saying "Stop", "Go back", or "Go back a little" mid-flight, I want the transition to halt or reverse immediately without losing my place.
- **Acceptance Criteria:**
  1. `navigation.cancel` SHALL immediately halt active camera interpolation and commit a valid intermediate pose.
  2. `history.back` with `mode: 'partial'` and `fraction f ∈ (0, 1)` SHALL interpolate the camera checkpoint along the fractional path; chaining two `history.back` calls with fractions `f₁` and `f₂` SHALL NOT overshoot beyond the previous checkpoint (i.e., effective combined fraction ≤ max(f₁, f₂)).
  3. THE voice service SHALL NEVER invent celestial coordinates, distances, or completed restoration jobs, SHALL evaluate predicted bodies at `observedAtUTC` without echoing private coordinates or timestamps, and SHALL refuse `ui.overlay` with `reason: 'not_connected'` until an overlay consumer exists.
  4. WHEN an `AlbedianAction` is dispatched with a `findingId` that does not exist in `session.observationSolution.findings`, THE system SHALL return `status: 'rejected'` with `reason: 'unknown_finding'`.
  5. `VoiceCommandRequest.utterance` SHALL be capped at 2,000 UTF-16 code units on both client and server without echoing the rejected utterance, and `space.epoch` SHALL reject year `0`.
