# Requirements: iOS Capture Companion (proposed, waiting for the owner's yes)

Added after the owner's direction of 2026-11-10 (every photo gets a sky, visible stars or not): a saved photo with no
visible stars SHALL get the predicted sky drawn over it from the recorded pointing, labelled predicted and
approximate with its uncertainty, through the pose estimate of spec `predicted-sky-for-every-upload`. This draft is
not yet confirmed by the owner.

## Introduction

A native iOS app for the part of Albedian that happens outside, under the sky: aiming, taking the photo and
sending it to the solver. The web app keeps the reveal, the target card, Back to Moment and the 3D flight. The
plate solver stays the Python service run by `pnpm field` on the laptop; the app talks to it over the local
network (the phone's own hotspot) and never to an outside service.

Why native for this part [inference]: the Camera app records where the phone was, but not where it pointed
(pitch and roll). The app can read the phone's attitude and heading at the moment of capture, and the triangle
solver already accepts a pointing hint (`hint_ra_deg`, `hint_dec_deg` in `services/science/solver.py`), which
narrows the search the solver has to do. A native app also avoids the plain http limits the phone has today in
the web app (no microphone, no WebGPU views; `docs/FIELD_USE.md`).

Out of scope here: solving on the phone, the 3D flight on iOS, a native reveal (a later spec if wanted).

## Requirements

### REQ-IOS-1: Capture with the phone's pointing
**User Story:** As someone under a dark sky, I want to take the photo in Albedian so it knows where I pointed the phone.
- **Acceptance Criteria:**
  1. WHEN the user takes a photo, THE app SHALL keep the image file exactly as the camera wrote it (the `SourceAsset` is content addressed by its sha256 and never changed).
  2. THE app SHALL record, in a separate pointing record and never inside the image file: the device attitude from Core Motion, the true heading and its stated accuracy, the camera's field of view from the active format, the lens, and when location is allowed, the location and its accuracy, plus the capture time from the device clock.
  3. WHEN heading accuracy is unavailable or worse than a stated limit, THE pointing record SHALL say so and carry no heading, rather than a guessed one.

### REQ-IOS-2: Pointing is a hint, never evidence
**User Story:** As someone who trusts the labels, I want the phone's pointing to help the solve without ever standing in for it.
- **Acceptance Criteria:**
  1. THE solver MAY use the pointing record only to narrow its search; a finding SHALL become `catalog_matched` only through the unchanged held-out verification (`heldOutInliers >= 6`, `medianResidualPx <= 3.0`).
  2. WHEN a solve used the pointing hint, THE `ObservationSolution` SHALL say so; WHEN the verified WCS centre lies outside the hint's stated uncertainty, THE WCS SHALL win and the disagreement SHALL be recorded as a warning.
  3. WHEN verification fails, THE status SHALL be `unresolved`, `ambiguous` or `predicted_only`, never `calibrated`, whatever the hint says.

### REQ-IOS-3: Live aim overlay, labelled predicted
**User Story:** As someone aiming the phone, I want to see which stars and planets are where I point before I shoot.
- **Acceptance Criteria:**
  1. WHILE aiming, THE app MAY draw catalog stars and planets placed from time, location and attitude; each SHALL be drawn and labelled as `predicted`, never as detected in the camera feed.
  2. THE overlay SHALL name its source (catalog and ephemeris) and its limits (pointing accuracy) on screen.

### REQ-IOS-4: Send to the laptop
**User Story:** As someone in the field with the laptop, I want the photo and its pointing to reach the solver without internet.
- **Acceptance Criteria:**
  1. THE app SHALL send the image and its pointing record to the laptop's ingest API over the local network only, after the user pairs once with the laptop's address.
  2. Upload progress SHALL come from real byte counts; solve progress SHALL come from the server's job denominators or show elapsed time.
  3. WHEN the laptop cannot be reached, THE app SHALL keep the capture on the phone and say so plainly.

### REQ-IOS-5: The result
**User Story:** As someone who just took the photo, I want to know quickly whether the sky was solved.
- **Acceptance Criteria:**
  1. THE app SHALL show the server's solve status and counts by evidence class, as the server reports them.
  2. THE app SHALL open the full reveal for that photo in the web app on the laptop or in a web view.

### REQ-IOS-6: Privacy
- **Acceptance Criteria:**
  1. Location and capture time SHALL stay on the phone and the laptop: never in the repository, test fixtures, logs, or any request to an outside service.
  2. WHEN the user turns location off, THE solve SHALL run blind and the solution SHALL omit `observerLocation`.

### REQ-IOS-7: Contracts first
- **Acceptance Criteria:**
  1. A pointing record contract (Zod in `packages/contracts`, Pydantic in `services/contracts`) with a shared fixture SHALL land before the app or the server reads it; TS and Python SHALL accept and refuse the same samples.

## Properties to test (from the requirements)
- P1 (REQ-IOS-2.1): for any pointing record, the evidence class of every finding equals the one the same frame gets with no hint, or the hinted solve is not `calibrated`.
- P2 (REQ-IOS-2.3): no pointing record makes a failed verification `calibrated`.
- P3 (REQ-IOS-1.1): the image bytes and sha256 after capture and upload equal the bytes the camera wrote.
- P4 (REQ-IOS-7.1): TS and Python parity over generated pointing records, including missing heading and missing location.

## Open questions
- [open] Whether third-party apps can use Night mode's multi-frame capture (to check against Apple's AVFoundation documentation), or must take their own exposures (RAW or a burst to stack on the laptop).
- [open] Whether the phone-photo solver, and not only the triangle solver, accepts the pointing hint.
- [open] Where the app lives (`apps/ios` in this repository is proposed) and which lane owns it (a new `mobile` lane; `AGENTS.md` ownership table to be updated by the lead).
