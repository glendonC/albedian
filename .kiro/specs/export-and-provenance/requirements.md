# Requirements: Export & Provenance

## Introduction
Defines reproducible media export, original audio stream preservation, and cryptographic provenance manifests (`ExportArtifact`).

## Requirements

### REQ-EXP-1: Audio-Preserving Media Export
**User Story:** As a creator exporting a restored night video, I want my original soundtrack preserved in sync and a machine-readable provenance manifest attached.
- **Acceptance Criteria:**
  1. Video exports SHALL preserve or remux the original audio stream without passing audio through visual restoration filters, maintaining A/V sync offset within $\le 33.3\text{ ms}$.
  2. Every export SHALL produce an `ExportArtifact` manifest recording `sourceSha256`, `outputSha256`, `processingGraph`, `observationSolution`, catalog citations, and epistemic disclosures.
