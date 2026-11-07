# Requirements: Design System & Accessibility

## Introduction
Defines Albedian's observational instrument design tokens, marker grammar across the 4 evidence classes, progressive disclosure hierarchy, Web Audio bus, and accessibility guarantees.

## Requirements

### REQ-DSA-1: Non-Color-Only Epistemic Marker Grammar
**User Story:** As a user (including users with color-vision deficiency), I want to distinguish `observed`, `catalog_matched`, `predicted`, and `simulated` objects by shape, stroke, and text badge, not color alone.
- **Acceptance Criteria:**
  1. `observed` markers SHALL use a solid centroid dot + tick; `catalog_matched` SHALL add a concentric precision ring + elbow label; `predicted` SHALL use a dashed ring + `Predicted` badge; `simulated` SHALL use bracketed reticles + `Simulated` badge.
  2. All primary text and control tokens SHALL satisfy WCAG AA/AAA contrast on `#06090D` and `#10161D`.

### REQ-DSA-2: Reduced Motion & Keyboard Parity
**User Story:** As a keyboard or motion-sensitive user, I want complete control over restoration, sky inspection, and 3D exploration without forced camera swoops.
- **Acceptance Criteria:**
  1. Enabling Reduced Motion SHALL replace continuous camera flight with instant/crossfade checkpoint transitions while preserving identical scientific state.
  2. Every voice command and pointer interaction SHALL have a keyboard/text equivalent.
