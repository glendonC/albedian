# Contributing to Albedian

Thank you for contributing to **Albedian** (*See (Beyond) the Night Sky.*).

## Engineering & Scientific Standards

Albedian connects personal night-sky photographs and videos to reproducible media restoration and calibrated astronomical exploration. Every contribution must uphold our core invariants:

1. **Source Immutability:** Never alter original uploaded assets or audio streams in place. All transformations produce content-addressed derivatives (`MediaVariant`).
2. **Epistemic Honesty (`EvidenceClass`):**
   - `observed`: Directly detected in source pixels.
   - `catalog_matched`: Matched to a pinned catalog (`Gaia DR3`, `HIP`) via an independently verified WCS solution (held-out inlier check).
   - `predicted`: Computed from observer time, location, and ephemerides without pixel registration proof.
   - `simulated`: 3D spatial exploration or modeled historical sky epochs.
3. **No Synthetic Stars in Strict-Fidelity Mode:** Do not inject catalog-predicted stars into a restoration output and claim they were recovered from sensor photons.
4. **Property-Based Testing (PBT):** Changes to coordinate math, camera projection, navigation history, evidence state transitions, processing DAGs, voice state revisions, or media timeline synchronization must include or update property-based tests (`fast-check` in TypeScript, `hypothesis` in Python).

## Development Setup

### Prerequisites
- **Node.js** `>= 22.0.0` (tested on `v24.15.0`) and **pnpm** `10.x`
- **Python** `>= 3.12` (tested on `3.14.7`) and **uv** `>= 0.12`
- **FFmpeg** `>= 7.0` (tested on `8.1.1`)

### Commands
```bash
# Install TypeScript workspace dependencies
pnpm install

# Build all shared packages and web application
pnpm build

# Run TypeScript unit + property-based tests (Vitest + fast-check)
pnpm test

# Run Python scientific & media runtime tests (pytest + Hypothesis)
pnpm test:python

# Run the TypeScript and Python test suites together (package.json test:all)
pnpm test:all

# The contract and docs checks run on their own
pnpm verify:contracts
pnpm verify:docs
```

## Kiro Spec-Driven Workflow

Specifications live in `.kiro/specs/<domain>/` (`requirements.md`, `design.md`, `tasks.md`). Steering rules live in `.kiro/steering/` and workspace hooks in `.kiro/hooks/`. Keep specifications and implementation synchronized when modifying domain contracts.
