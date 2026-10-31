# Requirements: Observation Session & Source Lifecycle

## Introduction
Defines the root `ObservationSession` aggregate, immutable `SourceAsset` lifecycle, the 4+1 orthogonal state machines (`Media`, `Restoration`, `Calibration`, `Navigation`, `Voice`), and reversible `NavigationHistory`.

## Requirements

### REQ-OS-1: Immutable Source Asset Ingestion
**User Story:** As an observer importing a night photo or video, I want my original recording and audio preserved unmodified so every restoration or exploration remains anchored to what I actually captured.
- **Acceptance Criteria:**
  1. WHEN a media file is imported, THE system SHALL compute its SHA-256 content digest and create an immutable `SourceAsset` record with dimensions, duration, frame rate, and audio stream descriptors.
  2. THE system SHALL NEVER overwrite or mutate the bytes or audio streams of a `SourceAsset`.

### REQ-OS-2: Orthogonal State Machines
**User Story:** As a user, I want restoration jobs, sky calibration, spatial navigation, and voice commands to operate without blocking or corrupting each other's state.
- **Acceptance Criteria:**
  1. THE `ObservationSession` SHALL maintain independent states for `Media`, `Restoration`, `Calibration`, `Navigation`, and `Voice`.
  2. WHEN a background restoration attempt is processing, THE user SHALL still be able to inspect a calibrated sky or navigate to a matched star without cancelling the restoration job.

### REQ-OS-3: Reversible Checkpoint Stack
**User Story:** As an explorer, when I say "Take me back" or click `Back to Moment`, I want to return to the exact frame, timestamp, crop, selection, and audio state I left.
- **Acceptance Criteria:**
  1. WHEN transitioning from `source` or `comparison` to `sky_registered` or `space`, THE session reducer SHALL push an immutable `CameraCheckpoint` onto `NavigationHistory`.
  2. WHEN `source.return` or `history.back` is dispatched, THE system SHALL restore `sourceAssetId`, `sourceTimeSec`, `playbackWasPlaying`, `audioGain`, `mediaVariantId`, `cropTransform`, `selectedFindingId`, and `cameraRig` identically.
