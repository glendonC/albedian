# Requirements: restoration and export for every upload

## Introduction

Albedian promises to restore dark night footage without ever painting in a star. Today an uploaded photo or clip
cannot be restored or exported at all. A read-only survey of `main` `dcdbd65d`
(`.lead/kiro/reports/survey-media.md`) found the gap, the owner asked on 2026-11-11 for the backend to be finished
properly (nothing hardcoded where it should be measured), and the lead took twelve decisions
(`.lead/briefs/restore.md`). This spec records them as requirements. Unit ids are `restore-<n>`.

Labels: [tested] measured or run for this spec, with what it ran on; [verified] read at the named source;
[inference]; [proposed]; [open]; [decided 2026-11-11] a ruling by the lead on this spec, with its reason (see
"Rulings by the lead on this spec").

Facts this spec rests on [verified by reading the code at `main` `dcdbd65d`]:
- The only restore route, `POST /api/media/restore`, takes luma arrays in the request, returns numbers and stores
  nothing (`services/media/router.py:81-111`). Export is a method that only tests call
  (`services/media/runtime.py:432`; no caller outside `tests/`).
- The runtime works on one grey plane in every branch (`services/media/runtime.py:247`, `260`, `266`, `322`), with
  its curve, blur and residual weights fixed in code (`runtime.py:251`, `261`, `276`).
- Every attempt is written with `"status": "ready"` (`services/media/models.py:108`). The false point count
  (`runtime.py:363-375`) gates nothing.
- The `temporal` branch writes one stacked sky into every frame at 90 percent (`runtime.py:321-331`).
- `faceRegionStabilityScore` is a mean difference divided by 64 with no face detection (`runtime.py:380-383`);
  `shadowSnrGainDb` has no signal term (`runtime.py:337-344`); `strictFidelityEnforced: True` and
  `allowSyntheticSky=False` are literals (`models.py:162`, `runtime.py:429`).
- Forced photometry on original pixels exists and is tested (`services/science/detector.py:359`
  `verify_candidates_on_original_pixels`, `tests/science/test_detection_integrity.py`). Nothing in
  `services/media/` calls it.
- The ingest store keeps each upload write-once at `<root>/<aa>/<sha256>/original.<ext>`, mode 0400, folders 0700
  (`services/ingest/store.py:236-265`), with `record.json`; the solve job writes `job.json` and `solution.json`
  beside it (`services/ingest/jobs.py:82-96`, `625`). The asset record carries each audio stream's packet hash
  (`services/ingest/probe.py:178-208`) and the display orientation.
- No code reads a colour tag: a search of `services/ingest/` and `services/media/` for an ICC profile, EXIF
  ColorSpace, a `colr` box or a stream colour field finds none.
- The solve job decodes a still through Pillow to 8 bit RGB and a clip through ffmpeg to 8 bit grey, at most 8
  frames (`jobs.py:44`, `159-204`, `311-343`). A tiled HEIC needs ffprobe 7.1 or later (`jobs.py:241-266`).
- Zod has `ProcessingGraph`, `ProcessingAttempt`, `MediaVariant` and `ExportArtifact`
  (`packages/contracts/src/media.ts:209-332`) and a DAG validator with cache keys (`media.ts:340-453`). Python has
  no mirror of any of them, and nothing executes a graph or keeps a cache.
- The solve job runs its searches in a child process stopped at a time bound
  (`services/science/bounded_worker.py`, `jobs.py:46-52`, `576-606`) and stores generic failure reasons
  (`jobs.py:60-72`).

Facts measured for this spec [tested 2026-11-11; scripts and pictures stay outside the repository, in the lane's
ignored tool folder; details and numbers in `design.md`, section "Measured facts"]:
- On five real phone night photos (CC BY-SA, three cameras), the noise is signal dependent in linear light, does not
  fit a two parameter line, and neighbouring pixels share a fifth to three quarters of it.
- On crops of those photos the detector, called as the solve job calls it, reports about a thousand point sources
  per megapixel and forced photometry confirms nearly all of them. With the noise taken from the spread of the
  detector's own matched-filter response, tile by tile, it reports a few dozen per crop.
- A filter that keeps whatever exceeds a threshold leaves 60 to 1210 points per crop that the original does not
  confirm. Plain linear smoothing leaves none. The filter chosen here leaves 0 to 8 per crop before its last pass
  and none after it.
- A variant stored at 8 bits showed 50 unconfirmed points on one crop where the same variant at 16 bits showed 1.
- The owner's three distinct clips are 8 bit HEVC tagged BT.2020 and HLG, with uneven frame gaps and one AAC stream.
  In two of them the luma sits on one code for about half or more of the frame, and 76 to 96 percent of pixels
  repeat exactly from one frame to the next.
- With the local ffmpeg, a clip decoded in its own pixel format gives the decoder's own samples, and frames piped
  back in can be stamped with the source's exact frame times while the audio is copied with an equal packet hash.

Not in scope: any learned or generative step; people or face detection and region masks; super-resolution,
deblurring or stabilisation; RAW files; the web interface (owned by `experience`; this spec gives it routes and
records); graph forking and node caches; a display copy for clips ([open], see the decisions).

## Glossary

- **Original**: the stored upload, `original.<ext>` in the ingest store, named by its SHA-256. Never changed.
- **Variant**: a file made from one original and stored beside it, with its records. Two kinds: a **display
  copy** (the original's decoded pixels, re-encoded without loss) and a **restoration**.
- **Attempt**: one run of the job on one original and one request. It ends `ready` (a variant exists), `rejected`
  (the job ran to the end and a check refused the result) or `failed` (the job could not finish).
- **Graph**: the ordered list of steps that ran, each with its parameters and where each parameter came from.
- **Parameter origin**: `measured` (on this file), `requested` (in the request) or `default` (named, with its source
  or reason).
- **Linear light**: values proportional to light, obtained by undoing the file's transfer function.
- **Noise table**: the measured noise of each colour channel as a function of intensity in linear light.
- **Confirmed star**: a point source found on the original's own pixels by the repository's detector and confirmed
  by its forced photometry. Only these are protected from smoothing. For a clip frame made with neighbours, the
  original pixels of those frames taken together count as well (REQ-RST-6.10, 6.11).
- **Footprint**: the pixels around a confirmed star where the original's light is kept, sized from that star's own
  measured profile.
- **Lift**: the exposure and tone step, one increasing curve applied to every pixel alike.
- **Lift undone**: the stored variant decoded and passed through the inverse of its recorded lift, so it can be
  compared with the original on the original's own scale.
- **Gate**: the check, on the stored variant file, that every point source of the variant is confirmed on the
  original (for a clip frame made with neighbours, on its original frames alone or together) and that star flux
  is preserved.
- **Dissolve**: replacing an unconfirmed point of the working image, inside a small footprint, by the plain
  smoothing of the original.
- **Manifest**: the JSON sidecar of an export.

## Requirements

### REQ-RST-1: A restoration job on a stored upload
**User Story:** As someone who uploaded a dark photo or clip, I want to ask for a restored version of that stored
file and get back a stored file I can check, with my original untouched.
- **Acceptance Criteria:**
  1. `POST /api/media/{sha256}/restore` SHALL take the content hash of a stored upload (64 lowercase hex) and a
     `RestoreRequest` (the kind, and for a restoration an optional `exposureEv`). It SHALL answer 404 for a hash
     with no stored original and 422 for any other body. No route SHALL take pixel arrays.
  2. THE job SHALL read the original only through the store, SHALL never open it for writing, and SHALL hash its
     bytes at the start and at the end of the job. WHEN either hash differs from the store's, THE job SHALL fail.
  3. A variant SHALL be written at `<root>/<aa>/<sha256>/variants/<variantId>/`, linked into place without
     overwriting, the file and each of its records with mode 0400 and every folder with mode 0700.
  4. THE variant record SHALL carry the source's SHA-256, the variant file's own SHA-256 hashed from the written
     bytes, its byte size, its stored width and height (equal to the original's) and the ids of its graph and
     attempt.
  5. THE graph SHALL list every step in order and, for every parameter of every step, its name, its value and its
     origin: `measured` with what on this file it was measured from, `requested` with the request field, or
     `default` with its source or its reason.
  6. THE same request on the same original with the same pipeline version SHALL give the same graph and variant
     ids. WHEN that variant is already stored, THE route SHALL return it and run nothing.
  7. THE attempt record SHALL be stored whether the attempt ends `ready`, `rejected` or `failed`, with the
     measured results it reached. Only a `ready` attempt has a variant file.

### REQ-RST-2: A display copy of an original the browser cannot draw
**User Story:** As someone who uploads an iPhone photo, I want to see my photo on the laptop, although the browser
cannot draw HEIC.
- **Acceptance Criteria:**
  1. WHEN the request's kind is `display_copy` and the original is a still, THE job SHALL decode it at stored size
     by its own colour tags (REQ-RST-3) and write the decoded pixels, with no filter, no tone change and no resize,
     as a PNG with 8 bits per channel for an 8 bit original and 16 bits for a deeper one.
  2. THE job SHALL verify the copy by decoding the written file and comparing every sample with the decoded
     original. Any difference SHALL end the attempt `rejected` with the reason `copy_differs`, and no file is kept.
  3. THE graph SHALL be exactly the two steps `decode` and `encode`, and the disclosure SHALL be the fixed sentence:
     "The original's own pixels, re-encoded without loss so a browser can show them. Nothing was restored."
  4. A display copy SHALL be stored and served exactly like any other variant (REQ-RST-1.3, REQ-RST-8.6), SHALL
     carry the original's display orientation and its ICC profile byte for byte, and SHALL need neither the noise
     table nor the gate.
  5. WHEN the original is a clip, THE job SHALL refuse the kind `display_copy` with a plain reason (see the
     decisions: no lossless form that every browser plays was found).
  6. THE unit SHALL land before the noise, gate and clip units: it needs only the contracts, the variant store, the
     serving routes and the still decode.
  7. THE client SHALL ask for a display copy through the same route as any other variant (REQ-RST-1.1). Ingest
     SHALL make no variant on its own [decided 2026-11-11: one route for every variant, and an upload stays exactly
     what was uploaded until someone asks for more].

### REQ-RST-3: Colour, at stored size, in linear light
**User Story:** As someone whose photo has colour, I want the restored version to keep my colours and my file's own
brightness scale, not a grey guess.
- **Acceptance Criteria:**
  1. THE decode SHALL return the stored pixels at stored size: no scale, no crop and no turn. Display orientation
     stays metadata, as for the solve job.
  2. THE decode SHALL read the file's own colour description: for a JPEG, PNG or TIFF its ICC profile's tone
     curves, else its EXIF ColorSpace; for a HEIC or a clip the stream's range, matrix, transfer, primaries and
     chroma siting.
  3. Supported input SHALL be: RGB stills at 8 and 16 bits; Y'CbCr at 8 and 10 bits in 4:2:0, 4:2:2 and 4:4:4 with
     the BT.601, BT.709 and BT.2020 non-constant luminance matrices, full or limited range; the sRGB, BT.709, HLG
     and PQ transfer functions and ICC parametric and table curves. Anything else SHALL fail the job with a plain
     reason that names what is not supported.
  4. WHEN the file does not state a field, THE decode SHALL use a named assumption, and THE attempt record SHALL
     list each assumed field with the assumed value and the reason. Nothing is assumed silently.
  5. Every step that averages light (spatial and temporal noise reduction, the resampling of a registered frame)
     and the lift SHALL work in linear light obtained with the transfer function's defining formula.
  6. THE variant SHALL be encoded with the original's own transfer, matrix, range, primaries and bit depth class,
     and tagged so. A still's ICC profile SHALL be carried byte for byte.
  7. FOR a clip, the samples handed to the pipeline SHALL be the decoder's own: frames are asked for in the
     stream's own pixel format, and a test SHALL show that ffmpeg inserted no conversion.

### REQ-RST-4: Parameters are measured
**User Story:** As the owner, I want nothing hardcoded where it should be measured, so the result follows my file
and not a constant someone typed.
- **Acceptance Criteria:**
  1. THE noise SHALL be measured on the file itself, per colour channel, as a function of intensity in linear
     light, from the flattest blocks of each intensity bin; for a clip from frames spread over the whole clip.
  2. THE attempt record SHALL carry the noise table: the intensities, the sigma per channel and the number of
     blocks behind each value.
  3. WHEN the noise cannot be measured (too few blocks, or more than half of a channel's fine detail is exactly
     zero, as in a frame recorded at black), THE job SHALL record the reason, SHALL skip the noise reduction steps
     and SHALL bound the lift by the quantisation bound alone. It SHALL say so in plain words in the record's notes.
  4. THE lift SHALL be bounded by measured quantities, and the record SHALL say which bound decided:
     the noise bound (the noise left after noise reduction, lifted and encoded, is no larger in output code values
     than the original's noise was in its own code values at the same dark level); the quantisation bound (one code
     step of the original at the dark level becomes at most a stated number of output code steps); and a stated
     maximum.
  5. A request's `exposureEv` SHALL be honoured only inside those bounds. Without it THE lift SHALL aim at a stated
     default level for the frame's median.
  6. No curve constant SHALL be fixed in code: the lift's one parameter is solved per file.
  7. Every numeric constant in the restoration code SHALL name its source in a comment (a standard's clause, a
     paper, a library's documentation, a measurement with what it was measured on) or carry `[proposed]` with its
     reason. A test SHALL list the module-level numeric constants of the new modules and fail on one with neither.
  8. THE restoration path SHALL draw no random number: no dither, no seeded noise.

### REQ-RST-5: Deterministic filters only
**User Story:** As someone who trusts the result, I want to know that no model imagined any part of it.
- **Acceptance Criteria:**
  1. A graph SHALL hold only the step kinds the contract lists. None loads a learned model, and none generates
     content. A learned denoiser may be evaluated later behind the same gate ([open]); it is out of scope here.
  2. Outside the footprints of confirmed stars, every output sample of the spatial and temporal steps SHALL be a
     weighted average of the original's own samples with weights that are not negative and sum to one, so no
     output value lies outside the smallest and largest of the samples it was made from.
  3. THE lift SHALL be one strictly increasing function applied to every sample of a channel alike.
  4. Two runs of the same request on the same original, on the same machine and ffmpeg build, SHALL write variant
     files with the same SHA-256.

### REQ-RST-6: Star protection and the integrity gate
**User Story:** As someone looking at my restored sky, I want every point of light in it to be one my camera
recorded, at the brightness it recorded.
- **Acceptance Criteria:**
  1. THE stars of an original frame SHALL be the point sources the repository's detector finds on that frame's own
     luma and its forced photometry confirms, tile by tile, each tile's noise taken from the spread of the
     detector's matched-filter response on that tile. For a clip frame made with neighbours, the points confirmed
     by criterion 10 are stars of that frame too.
  2. Inside a confirmed star's footprint THE pipeline SHALL keep the original's light (for a clip frame made with
     neighbours, the light of its merged original frames): the spatial filter neither smooths those pixels nor
     spreads them into their surroundings. The footprint's radius SHALL be measured from that star's own profile.
  3. Before encoding, THE pipeline SHALL look for point sources in its working image with the gate's own rule and
     SHALL dissolve each one the original does not confirm (criterion 10 says what confirms a clip's point). It
     SHALL do so at most three times and record how many points each pass dissolved.
  4. THE gate SHALL run on the stored variant file decoded back, with its recorded lift undone: for a still on the
     frame, for a clip on every frame against the original frame with the same index and, where the frame was made
     with neighbours, against those original frames together (criterion 10).
  5. Every point source of the variant at or above the variant threshold SHALL be confirmed at the same place on
     the original's pixels by forced photometry (`verify_candidates_on_original_pixels`). An attempt with one
     unconfirmed point or more SHALL end `rejected`, SHALL record how many points in how many frames, and its file
     SHALL be removed: never served, never listed as a variant.
  6. FOR every confirmed star, the aperture flux measured on the variant with the lift undone SHALL lie within the
     tolerance of the flux measured on the original (for a star confirmed only on the frames together, on those
     frames together). The tolerance SHALL be stated in the record and measured against the noise of empty
     apertures of the same size on the same original pixels. A star outside it SHALL end the attempt `rejected`
     with the reason `star_flux_changed`. The record SHALL carry the number of stars measured, the worst change and
     the median ratio.
  7. THE gate SHALL refuse a painted star [decided 2026-11-11: the detector's own statistic refused none of 40
     painted stars on the most lifted crop, so it cannot be the gate as it stands]. On every real photo of the
     corpus, lifted as the job lifts it, a star-shaped patch painted into the stored variant at the stated
     sensitivity SHALL be refused in every trial. The stated sensitivity is a peak, in the stored variant's own
     code values, of at least five times the variant's pixel noise around the patch and at least 4 codes of 255
     [proposed first value, in what a person sees and not in the statistic's own units, so no statistic can meet
     it by definition; restore-4 states the value it reaches]. Unit restore-4 SHALL choose the gate's statistic by
     measurement; the detector's statistic with the local background at the star scale removed is the first
     candidate. The same statistic SHALL find the stars of criterion 1, the points of criterion 3 and the points
     of criterion 5.
  8. Points inside regions where the original is clipped at its largest code SHALL not be counted, and the record
     SHALL say how many were left out.
  9. THE statistic, the thresholds, the tile size, the match radius and the flux tolerance SHALL be recorded in
     the attempt.
  10. FOR a clip frame made with help from registered neighbours, a point of the variant SHALL count as confirmed
      when forced photometry confirms it on the original pixels of the frames it was measured in, taken together
      after registration and with no filtering, at the confirmation threshold [decided 2026-11-11: a real star too
      faint for one frame is in the original pixels of many frames, so showing it paints nothing]. A point those
      original frames together do not confirm SHALL be dissolved, and the gate SHALL refuse it if it remains. For
      a still nothing changes: its one original frame decides.
  11. Two guards SHALL hold for a clip, each with its own test [decided 2026-11-11: taking frames together must
      not let in what the frame itself did not record]: a point present only in the neighbours never appears in
      the frame (REQ-RST-7.4); and a point that stays at the same sensor pixel while the registration says the
      scene moved is not a confirmed star, because it is a hot pixel or a codec block.
  12. Points of an honest variant that the chosen statistic flags SHALL be dissolved by the pipeline, so an honest
      run still ends `ready`. Unit restore-4 SHALL report, per real photo, the painted stars refused, the honest
      points flagged and dissolved, and what the dissolving costs in visible detail. WHEN no statistic meets both
      criterion 7 and an honest `ready` on the real photos, THE unit SHALL say "unsolved on real data" with the
      numbers, and the job SHALL serve no restoration [decided 2026-11-11: a gate that lets a visible painted
      star through does not keep the promise, so no restoration is served until a statistic refuses them].
  13. A still's variant SHALL be the 16 bit PNG, and the gate SHALL run on that stored file [decided 2026-11-11:
      what is served is the file the gate ran on]. Any other rendition, if one is ever served, SHALL be gated as
      its own file.

### REQ-RST-7: Clips keep their own frames
**User Story:** As someone who filmed the night, I want each restored frame to show what that frame recorded, at
the time it was recorded, with my sound exactly as it was.
- **Acceptance Criteria:**
  1. Each output frame SHALL be made from the original frame with the same index. Its neighbours may only help it
     (criteria 2 to 4).
  2. Each neighbour used for a frame SHALL be registered to that frame, with a measured quality. A neighbour below
     the stated quality SHALL not be used for that frame. The record SHALL say for how many frames registration
     held.
  3. Temporal noise reduction SHALL weight each registered neighbour sample by how well it agrees with the frame's
     own sample, given the measured noise. Where they disagree the frame's own sample stands. A neighbour sample
     that is an exact repeat of the previous original frame at the same place, while the registration says the
     scene moved, SHALL get no weight.
  4. A stack or an average SHALL never be written into a frame as its content: the frame's own sample always has
     the largest weight, and a point present only in the neighbours SHALL not appear in the frame.
  5. THE variant SHALL have the same number of frames as the original, and each frame's presentation time SHALL
     equal the original's exactly, as an integer in the original's time base, with the same offset between the
     video and each audio stream.
  6. Every audio stream SHALL be copied bit for bit: the packet hash of each of the variant's audio streams SHALL
     equal the hash stored at ingest, and every packet's time SHALL equal the original's. A clip whose audio cannot
     be copied unchanged into the output container SHALL fail the job. Audio is never re-encoded.
  7. THE decode, the processing and the encode SHALL be streamed: at most a stated number of frames is held in
     memory at once by each worker process, whatever the clip's length, and the attempt SHALL record the workers'
     peak memory.
  8. THE noise table, the star footprint rule and the lift SHALL be measured once per clip and used for every
     frame, so the lift cannot flicker.
  9. THE variant SHALL carry the original's display orientation.
  10. An 8 bit clip SHALL be encoded as 8 bit H.264; a 10 bit clip as 10 bit HEVC where the installed ffmpeg has
      the encoder, and otherwise the job SHALL fail with a plain reason.

### REQ-RST-8: Honest jobs
**User Story:** As someone waiting, I want progress that counts real work, a job that cannot hang, and a plain
reason when it fails.
- **Acceptance Criteria:**
  1. THE job record SHALL report these stages with real counts: `decode` in frames out of the frames counted
     before decoding; `measure` in frames; `restore` in frames; `encode` in frames; `write` in bytes, its total
     unknown (null) until the file is closed; `verify` in frames. A still counts as one frame.
  2. No stage SHALL report work it did not do, and a stage that did not run SHALL be absent.
  3. THE work SHALL run in a child process stopped at a time bound computed from the file's megapixels and frame
     count at a stated rate. At the bound the job SHALL end `failed` with a generic reason, and every partial file
     SHALL be removed.
  4. THE job record SHALL be stored at each stage's completion and read by
     `GET /api/media/restore-jobs/{jobId}`.
  5. A failure reason SHALL be one of a fixed list of sentences. No stored record and no response SHALL hold a
     path, a file name, a title, a capture time or a place.
  6. `GET /api/media/{sha256}/variants` SHALL list the stored variants and attempts of an original;
     `GET /api/media/{sha256}/variants/{variantId}` SHALL return a variant's records; and
     `GET /api/media/{sha256}/variants/{variantId}/file` SHALL return its bytes, private, never sniffed, with byte
     ranges.
  7. One restoration SHALL run at a time. A second request SHALL wait in the state `queued`.
  8. THE job SHALL reuse what the solve job already has: the store and its asset record (size, frame rate, audio
     stream hashes, display orientation), the HEIC tile grid reader, the bounded worker, and the solution id.
  9. Speed [decided 2026-11-11: the product may use the laptop's cores at run time; the limit of two processes in
     the lane contract is for agents' test runs, not for the app]. Unit restore-3 SHALL report the still path's
     seconds per megapixel on one core and bring it to a stated target [proposed: at most 0.5 s per megapixel on
     one core]. Unit restore-7 SHALL run a clip's frames in a bounded pool of worker processes with a stated
     default size [proposed: half the cores] and SHALL report the time for each of the owner's clips against a
     target [proposed: a 15 second clip of 2.4 megapixel frames in under 10 minutes on this laptop]. A unit that
     misses its target SHALL say what dominates the time.

### REQ-RST-9: Export
**User Story:** As someone who wants to keep or share the result, I want the file and a plain account of how it was
made.
- **Acceptance Criteria:**
  1. An export of a `ready` variant SHALL be its stored file, byte for byte, as an attachment named by its variant
     id, and its manifest as a sidecar.
  2. THE manifest SHALL hold: its format name and version; the source's SHA-256; the variant record (its own
     SHA-256, kind, size, colour description, audio stream hashes, frame count and the hash of its frame times);
     the graph; the attempt with its measured results and its gate result; the observation solution id when a
     solution is stored for the source; and the disclosure.
  3. THE disclosure of a restoration SHALL be the fixed sentence: "Restored from the original's own pixels with
     deterministic filters. Nothing was generated."
  4. THE manifest SHALL hold no capture time, no place, no file name, no title and no path, and the exported file
     SHALL carry no container tag copied from the original [decided 2026-11-11: an export is made to be shared;
     the owner can ask for their own metadata later].
  5. A still SHALL be exported as the PNG that is the variant.
  6. A `rejected` or `failed` attempt SHALL have nothing to export (404).
  7. THE manifest SHALL parse under `ExportManifestSchema` in both languages, and its variant hash SHALL equal the
     SHA-256 of the exported bytes.

### REQ-RST-10: Contracts first
**User Story:** As the web app and every later unit, we need one shape for a request, a job, a graph, an attempt, a
variant and a manifest before anyone builds on them.
- **Acceptance Criteria:**
  1. THE shapes SHALL be Zod schemas in `packages/contracts/src/media.ts` and Pydantic models in
     `services/contracts/media.py`, with a shared fixture `tests/fixtures/media_contract.json` written by the Python
     side and parsed by the TypeScript tests. Both sides SHALL accept and refuse the same samples.
  2. THE contract unit SHALL land before any implementation unit.
  3. THE contract SHALL lose every field that describes something that does not exist (listed in `design.md`,
     section "The contract"), and SHALL gain nothing that no unit of this spec produces. The DAG validator and the
     cache keys SHALL be removed [decided 2026-11-11: nothing branches or caches, and the contract must not
     describe what does not exist].
  4. THE session schema, the reducer, the synthetic sessions and `scripts/verify-contracts.mjs` SHALL be adapted in
     the same unit, so that `pnpm typecheck` and `pnpm verify:contracts` hold. THE session's `media.restore`
     SHALL stay honest: it still refuses plainly when no variant exists. The web app is wired to the new routes
     later, by its own lane [decided 2026-11-11: the session must not claim a variant it does not have, and the
     web app is another lane's files].

### REQ-RST-11: Cut what is invented
**User Story:** As a reader of any number this app shows, I want it to be measured.
- **Acceptance Criteria:**
  1. Each item of the survey's section 4 SHALL be cut or made real; none stays as it is. `design.md`, section
     "Corrections of old code", lists each with its outcome.
  2. `POST /api/media/restore` with arrays in the request, `execute_restoration_branch` and
     `export_video_with_audio_remux` SHALL be removed once the job covers them, with their tests replaced by tests
     of the job.
  3. `scripts/run_real_media_experiment.py` and `scripts/render_clip_variants.py` SHALL be removed or rewritten to
     call the job: no fixed curve, no default camera name or duration, no figure printed beside a picture it was
     not measured on, no detection on a stretched picture reported as observed.

### REQ-RST-12: Real data
**User Story:** As the lead, I want every claim about pixels shown on real photos and real clips, and pictures I
can look at.
- **Acceptance Criteria:**
  1. Each unit that changes pixels SHALL report real photos: from `.cache/real-photos/` (registry
     `data/downloads.json`, manifest `research/benchmarks/real_photos/corpus.json`) once that corpus lands, and
     until then the five photos under `.cache/commons-southern/`. Its tests on them SHALL carry the
     `local_dataset` marker.
  2. Each unit that touches the clip path SHALL report the owner's clips as counts only: frames, statuses, points,
     noise ratios and times. Never a name, a folder, a capture time or a place, in a test's output either.
  3. Before and after pictures SHALL be written to a cache folder outside the repository for the lead to look at,
     and never committed.
  4. A unit that cannot show an improvement on real data SHALL say "unsolved on real data" with the reason.

## Which older requirements this spec replaces

- `media-experiment-runtime` REQ-MER-1 (a typed acyclic graph with caches and forks): replaced by the recorded,
  ordered graph of REQ-RST-1.5. Forks and node caches are not built and not claimed ([open]).
- `media-experiment-runtime` REQ-MER-2 (star protection, false points evaluated before `ready`): replaced by
  REQ-RST-4, REQ-RST-5 and REQ-RST-6. Its region masks and its promise about people are not covered: nothing
  detects a person, so nothing is promised about one.
- `export-and-provenance` REQ-EXP-1.1 (audio kept in sync): replaced by REQ-RST-7.5 and 7.6, which ask for equal
  packet hashes and equal packet times instead of a 33 ms bound.
- `export-and-provenance` REQ-EXP-1.2 (a manifest for every export): replaced by REQ-RST-9.

## Properties to test (derived from the requirements)

Python properties use hypothesis, TypeScript properties fast-check; each runs under 20 seeds before a unit is ready,
with rare required cases seeded by `@example` or `examples`. No property compares float output byte for byte:
arrays are compared within a stated tolerance, integers and hashes of files exactly.

- **P-RST-1** (REQ-RST-1.2, 1.3): for any stored original and any request, after the job the original's bytes
  hash to the same SHA-256, its mode is unchanged, and nothing under the upload's folder changed outside
  `variants/`, `attempts/` and `restore-jobs/`.
- **P-RST-2** (REQ-RST-1.5, 4.7): every parameter of every step of a generated graph has an origin and a basis that
  is not empty; a parameter marked `measured` changes when the frame's noise is doubled, and none marked `default`
  does.
- **P-RST-3** (REQ-RST-1.6): the graph id is a function of the source hash, the request and the pipeline version
  only, and changes when any one of them changes.
- **P-RST-4** (REQ-RST-2.1, 2.2): for any generated 8 or 16 bit RGB image stored as PNG or TIFF, the display copy
  decodes to exactly the decoded original; changing one sample of the written copy fails its verification.
- **P-RST-5** (REQ-RST-3.5, 3.6): for each of the four transfer functions and every code value at 8 and 10 bits,
  encode after decode returns the same code, decode is strictly increasing, and both agree with the standard's
  formula at seeded points within 1e-9.
- **P-RST-6** (REQ-RST-3.3, 3.7): for any generated Y'CbCr frame (8 or 10 bits, each subsampling, range and
  matrix), converting to R'G'B' and back returns luma within 1 code and chroma within 2 codes.
- **P-RST-7** (REQ-RST-4.1): for a synthetic frame with a known sigma at each intensity, white or shared between
  neighbours, the measured table is within 20 percent of the truth in every bin with enough blocks; covering half
  of the blocks with texture moves it by less than 20 percent.
- **P-RST-8** (REQ-RST-4.4, 4.5): for any noise table, frame and request, the applied lift never exceeds the noise
  bound, the quantisation bound or the maximum, and the record names the bound that decided.
- **P-RST-9** (REQ-RST-5.2, 5.3): for any frame and any footprints, every output sample of the spatial step and of
  the temporal step lies between the smallest and largest input sample in its support; the lift preserves order.
- **P-RST-10** (REQ-RST-4.8, 5.4): the same input and request give equal output arrays, and two job runs give
  variant files with the same SHA-256.
- **P-RST-11** (REQ-RST-6.5, 6.7, 6.12): for a seeded synthetic scene and, under the `local_dataset` marker, for
  every real photo of the corpus lifted as the job lifts it: the honest run ends `ready`; adding one star-shaped
  patch at the stated sensitivity, anywhere at least 12 px from a confirmed star and from a clipped region, makes
  the gate reject with an unconfirmed count of at least 1, in every trial.
- **P-RST-12** (REQ-RST-6.2, 6.6): for any synthetic star field with noise, every confirmed star's flux after the
  photo path, lift undone, is within the tolerance; with footprints switched off (a mutant) it is not.
- **P-RST-13** (REQ-RST-6.3): after the dissolve passes the working image holds no unconfirmed point, or the pass
  limit was reached and the gate rejects.
- **P-RST-14** (REQ-RST-7.1, 7.4, 6.11): for a generated clip, replacing every neighbour of frame k by frames of
  another scene leaves output frame k equal to its single-frame result within the encode tolerance; a bright point
  drawn only into the neighbours, into all of them or into one alone, is not a point source of output frame k; and
  painting that point into the stored frame k makes the gate reject, although the frames together would show it.
- **P-RST-15** (REQ-RST-7.5): for any strictly increasing list of 1 to 300 integer frame times and each time base
  of a stated set, the variant's video packet times equal the list exactly.
- **P-RST-16** (REQ-RST-7.6): for a generated clip with 0, 1 or 2 audio streams, each audio stream of the variant
  has the original's packet hash and packet times; an audio codec the container refuses fails the job with the
  generic reason and leaves no file.
- **P-RST-17** (REQ-RST-7.7): the worker's peak memory for a 300 frame clip is within a stated margin of its peak
  for a 30 frame clip of the same size.
- **P-RST-18** (REQ-RST-8.1, 8.2): in every job record a run emits, each stage's `done` never decreases and never
  exceeds its `total`; at the end `decode`, `restore`, `encode` and `verify` all equal the frame count and `write`
  equals the variant's byte size.
- **P-RST-19** (REQ-RST-8.5, 9.4): for any failure injected at any stage of a job on a synthetic upload planted
  with a title, a capture time and a place, no stored record and no response holds the store's path, the title,
  the time or the place.
- **P-RST-20** (REQ-RST-10.1): TypeScript and Python accept and refuse the same generated records: an attempt
  whose fields do not fit its status, a gate result whose counts do not add up, a variant with the other kind's
  disclosure, a graph whose first step is not `decode`, a parameter with no origin.
- **P-RST-21** (REQ-RST-9.2, 9.7): for any `ready` variant, the manifest parses in both languages and its variant
  hash equals the SHA-256 of the exported bytes.
- **P-RST-22** (REQ-RST-7.3): where a registered neighbour sample differs from the frame's own by more than the
  stated number of noise sigmas, its weight is below one tenth; where it is equal, its weight equals the frame's.
- **P-RST-23** (REQ-RST-6.10): for a generated clip whose scene shifts by known amounts, a star drawn at the same
  place of the scene in every frame, too faint for forced photometry to confirm on any one frame, is confirmed on
  the frames together, appears in the output frame and passes the gate; a patch painted into the stored frame at a
  place where the frames together show nothing makes the gate reject. A generated case in which one frame's own
  noise at the star's place breaks the agreement of the first guard (`design.md` section 7) is set aside and
  counted: there the guard is meant to refuse.
- **P-RST-24** (REQ-RST-6.11): for the same clip, a patch drawn at one sensor pixel in every frame while the scene
  shifts by more than the guard's stated distance across the window is not a confirmed star: it is not a point
  source of the output frame, and painted into the stored frame it makes the gate reject. A star drawn at one
  place of the scene, bright enough for one frame to confirm, stays a confirmed star in every frame. When the
  scene does not move, the guard does not apply and the frame's own pixels decide.

## Decisions taken by the lead (2026-11-11), recorded here [proposed, open to the owner]

Each is the lead's decision from `.lead/briefs/restore.md`, with the reason, and where this spec had to choose how
to meet it, the choice and its reason.

1. A job on a stored upload, named by its content hash, the variant beside the original. Reason: an upload is the
   only thing a person has; arrays in a request restore nothing they own.
2. Colour at stored size in linear light, by the file's own tags, a missing tag stated as an assumption. Reason:
   averaging gamma-encoded values darkens and shifts colour.
3. Parameters are measured. Reason: the owner's instruction.
4. Deterministic filters only. Reason: a learned or generative step can paint what was not recorded.
5. The integrity gate decides `ready` or `rejected`. Reason: "never paint in stars" needs a check that can say no.
   Chosen here [proposed]: the gate's rule is also the pipeline's rule (the dissolve pass), so an honest run meets
   it by construction and the gate then checks the stored file. Rulings 1 and 2 below settle what confirms a
   clip's point and which statistic the gate uses.
6. Clips keep their own frames. Reason: the old temporal branch pasted one stacked sky into every frame.
7. Honest jobs, as the solve job does.
8. Export is the stored file plus a manifest sidecar.
9. Contracts first, as their own unit.
10. Cut what is invented.
11. Real data.
12. The old specs are corrected (done in this unit, `restore-0`).

Choices this spec adds, each with its reason:
- A still variant is a PNG at 16 bits per channel [decided 2026-11-11, ruling 7]. Reason: at 8 bits one crop
  showed 50 unconfirmed points from contouring where 16 bits showed 1 [tested].
- The gate looks at the variant with its lift undone and uses a variant threshold of 6 against a confirmation
  threshold of 4.75 (the detector's own floor) [proposed]. Reason: near a shared threshold the two images disagree
  by chance. At equal thresholds three of five crops showed 4 to 10 unconfirmed points; at 6, stored at 16 bits,
  the five crops showed 0 to 8 before the dissolve pass and none after it [tested, with the detector's own
  statistic]. These are the starting values for the statistic restore-4 chooses (ruling 2).
- The detector's noise is taken per tile from its matched-filter response [proposed]. Reason: without it the
  detector reports about a thousand points per megapixel on real photos [tested].
- The restoration request runs in the background and is polled [proposed]. Reason: a clip takes minutes.
- An export carries no capture time, place or file name, in the manifest or in container tags [decided
  2026-11-11, ruling 6]. Reason: an export is made to be shared.
- The default lift aims the frame's median at code 43 of 255, the median of the medians of the five real night
  photos measured here (15, 36, 43, 53 and 56) [proposed; tested on five photos; set from pictures, ruling 10].

## Rulings by the lead on this spec (2026-11-11)

The spec was accepted as the plan with ten rulings (`.lead/kiro/requests/restore-0-rulings.md`). Each is written
in where it belongs and marked [decided 2026-11-11] there.

1. **A clip's faint stars.** A point of a clip frame made with neighbours is confirmed on the original pixels of
   the frames it was measured in, taken together after registration and with no filtering. Reason: a real star
   too faint for one frame is in the original pixels of many frames, so showing it paints nothing. Two guards
   stay, each with a test: nothing present only in the neighbours, and nothing fixed to a sensor pixel while the
   scene moves. A still is unchanged. REQ-RST-6.1, 6.4, 6.6, 6.10, 6.11; P-RST-14, 23, 24.
2. **The gate's statistic.** The detector's own statistic refused none of 40 painted stars on the most lifted
   crop, so it cannot be the gate as it stands. restore-4 chooses the statistic by measurement; a painted star at
   the stated sensitivity is refused in every trial on every real photo; honest points the stricter statistic
   flags are dissolved; if nothing meets both, "unsolved on real data" and no restoration is served. REQ-RST-6.7,
   6.12; P-RST-11.
3. **The contract's unused parts.** The DAG validator and the cache keys are removed. Reason: nothing branches or
   caches, and the contract must not describe what does not exist. `media.restore` stays honest, and the web is
   wired later by its own lane. REQ-RST-10.3, 10.4.
4. **Speed.** The product may use the laptop's cores at run time. Targets for the still path and for a clip, a
   pool of worker processes, and what dominates the time when a target is missed. REQ-RST-8.9.
5. **Who asks for a display copy.** The client, through the same route; ingest makes nothing on its own. A clip
   gets none in this spec. REQ-RST-2.7.
6. **What an export carries.** No capture time, place, file name or container tag from the original. Reason: an
   export is made to be shared; the owner can ask for their own metadata later. REQ-RST-9.4.
7. **What is served.** A still's variant is the 16 bit PNG and the gate runs on that stored file; any other
   rendition would be gated as its own file. REQ-RST-6.13.
8. **File lists.** The files `tasks.md` names for a unit are approved when the spec lands, the shared ones
   included; a unit asks only before touching a file `tasks.md` does not name for it. `tasks.md`, top.
9. **The detector's matched-filter noise.** restore-3 imports the two private helpers for now; the lane that owns
   the detector will publish one shared function, which the solver needs too, and restore switches to it when it
   lands. `tasks.md`, restore-3; `design.md` section 3.
10. **First values.** The default lift target, the quantisation bound, the flux tolerance and the encoder quality
    stay [proposed]; restore-4 and restore-7 write before and after pictures for the lead and the owner to judge,
    and the values are set from those. `tasks.md`, restore-4 and restore-7; `design.md` sections 6, 7 and 10.

## What is open

- [open] What the detector confirms on the owner's clips. One frame gave 97 to 655 confirmed points per megapixel
  crop where the luma is within a code or two of black [tested]; whether they are stars or codec blocks is not
  known. The guard of REQ-RST-6.11 refuses those that stay on a sensor pixel while the scene moves.
- [open] A display copy for clips. The owner's three clips are HEVC, which Safari plays and Chrome and Firefox
  play in part [verified: caniuse.com/hevc, read through a fetch on 2026-11-11]. No lossless video form that every
  browser plays was found [inference].
- [open] A smaller 8 bit rendition of a still for a phone on a slow link. It is not part of this spec; if one is
  ever served, it is gated as its own file (REQ-RST-6.13).
- [open] A 10 bit clip has not been seen: the 10 bit path is specified and synthetic-tested only until one exists.
- [open] People and faces: nothing detects them; the same filter runs on the whole frame.
- [open] A learned denoiser behind the same gate.
