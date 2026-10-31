# Requirements: Media Experiment Runtime

## Introduction
Defines the content-addressed `MediaExperimentRuntime` that executes, evaluates, compares, and exports real low-light photo and video restoration attempts without inventing stars or corrupting original audio.

## Requirements

### REQ-MER-1: Typed Acyclic Processing Graph
**User Story:** As a user or researcher, I want every restoration attempt to execute a real, inspectable DAG of media transformations with deterministic caching.
- **Acceptance Criteria:**
  1. THE `ProcessingGraph` SHALL validate topological ordering, reject cycles, and compute deterministic cache keys from `sourceSha256`, frame span, operation version, parameters, and parent artifact hashes.
  2. Forking a parameter or region mask SHALL create a new immutable `ProcessingAttempt` while reusing unaffected upstream cached nodes.

### REQ-MER-2: Star-Protected & Foreground-Aware Restoration
**User Story:** As an observer, when I say "Make the sky clearer; don't change the people; don't invent stars," I want background noise reduced while preserving source-detected stars and human subjects.
- **Acceptance Criteria:**
  1. THE runtime SHALL support `tone_transfer`, `spatial_denoise`, `temporal_stack`, `region_mask`, and `star_protect_merge` operations.
  2. Under `allowSyntheticSky: false`, THE runtime SHALL protect source-detected point sources (`observed`) from over-smoothing and evaluate false-point artifact counts before marking an attempt `ready`.
