# Tasks: Voice-Native Actions & Context Resolution

- [x] 1. Define `AlbedianAction`, `ActionContext`, and `ActionOutcome` schemas in `packages/contracts`.
  - Traced to: `packages/contracts/src/__tests__/contracts.test.ts` → schema round-trip & Zod parse tests
- [x] 2. Implement context-bound natural-language intent parser and deictic resolver in `packages/observation` and `services/voice`.
  - Traced to: `packages/observation/src/__tests__/voice-experiments.test.ts` → EXP-09 (40+ utterance-context pairs)
- [x] 3. Implement revision-checked action validator and barge-in cancellation handler.
  - Traced to: `packages/observation/src/__tests__/observation.test.ts` → `PBT-VOICE-1` (stale revision rejection), `history.back(partial)` multi-step chaining
- [x] 4. Verify `EXP-09` (40+ utterance-context pairs) and property test `PBT-VOICE-1`.
  - Traced to: `packages/observation/src/__tests__/voice-experiments.test.ts` (50 tests); `packages/observation/src/__tests__/observation.test.ts` → `PBT-VOICE-1`
