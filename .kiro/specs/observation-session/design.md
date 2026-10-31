# Design: Observation Session & Source Lifecycle

## Architecture
- **Package:** `@albedian/observation` (`packages/observation/src/index.ts`)
- **Contracts:** `@albedian/contracts` (`ObservationSessionSchema`, `SourceAssetSchema`, `CameraCheckpointSchema`, `NavigationHistorySchema`)

## State Transitions
- `MediaState`: `'empty' | 'importing' | 'ready' | 'decode_error'`
- `RestorationState`: `'idle' | 'analyzing' | 'planning' | 'queued' | 'processing' | 'evaluating' | 'ready' | 'failed' | 'canceled'`
- `CalibrationState`: `'not_requested' | 'detecting' | 'solving' | 'verifying' | 'calibrated' | 'predicted_only' | 'ambiguous' | 'unresolved'`
- `NavigationState`: `'source' | 'comparison' | 'sky_registered' | 'transitioning' | 'space' | 'inspecting' | 'returning'`
- `VoiceState`: `'off' | 'permission' | 'listening' | 'hearing' | 'interpreting' | 'awaiting_tool' | 'speaking' | 'interrupted' | 'unavailable'`

## Correctness Properties
- **Property PBT-NAV-1 (Checkpoint Round-Trip):** Applying any valid sequence of spatial navigation actions followed by `source.return` restores the initial `CameraCheckpoint` fields identically.
