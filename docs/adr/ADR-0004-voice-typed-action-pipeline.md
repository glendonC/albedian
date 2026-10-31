# ADR-0004: Voice-Native Direct Manipulation Pipeline

- **Status:** Accepted
- **Date:** 2026-10-31
- **Deciders:** Principal Orchestrator (`voice`, `experience`)

## Context

Voice in Albedian must operate as direct manipulation of structured application state (`"Make the sky clearer"`, `"Don't change the people"`, `"What's that one?"`, `"How far away is it?"`, `"Take me there"`, `"Where is Earth?"`, `"Show me this sky a thousand years ago"`, `"Go back"`), not as an ungrounded conversational chatbot.

## Decision

1. **Five-Stage Deterministic Pipeline:**
   `Utterance/Text → Context-Bound Intent Parser → Typed AlbedianAction Proposal → Authoritative State & Evidence Validator → Domain Execution & Minimal Feedback`
2. **Deictic Context Resolution (`ActionContext`):**
   - Resolves pronouns (`"that one"`, `"it"`, `"there"`) using strict priority:
     1. Explicit object name in utterance (`"Betelgeuse"`, `"Rigel"`, `"Vega"`).
     2. Active `selectedFindingId` if `expectedViewRevision === currentViewRevision`.
     3. Recent pointer/focus target (`recentPointer` within 10 seconds and matching `viewRevision`).
     4. Unique visible matched candidate in the active ROI.
     5. Fail closed with `needs_input` disambiguation choices when multiple candidates overlap or state is stale.
3. **Revision-Gated Idempotency:**
   - Every `AlbedianAction` carries `requestId` and `expectedViewRevision`. Stale async completions or interrupted turns cannot mutate the active selection or camera pose.
4. **Transport Abstraction:**
   - Support Web Speech API / push-to-talk microphone capture, optional OpenAI Realtime WebRTC session adapter, and full keyboard/typed command parity with zero permanent listening by default.

## Consequences

- Voice commands are 100% deterministic, property-testable, interruptible (`navigation.cancel`), and incapable of inventing astronomical coordinates or bypassing evidence checks.
