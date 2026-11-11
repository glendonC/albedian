# Albedian and Kiro

This page records what Kiro did on Albedian, and what was done before Kiro led, so neither is presented as the
other. Every claim names its file, commit or verdict. Paths under `.lead/` and `.claude/` are the lead's local
working files: git ignores them, so they are named here in code spans and are not in the repository.

## Before Kiro led (to 2026-11-09)

- The `.kiro/` folder (8 specs with requirements, design and tasks; 5 steering files; 5 hooks in the
  `.kiro.hook` format; `.kiro/agents/organization.json`) was added in commit `69319669` on 2026-10-31 and edited
  by lane commits `36decdc8`, `42388821` and `b622d83e` on 2026-11-06. This page does not claim which tool
  wrote those files.
- From 2026-11-04 to 2026-11-09 the code was written by Claude Code sessions: one lead and lanes (solver, render,
  audio, language, integrity, platform, data, experience, hud, and an auditor lane), each in its own git
  worktree, landing one audited unit per commit on `main`. Main was `a3f27a38` when Kiro took over.

## What Kiro did (2026-11-10)

Kiro ran as the lead in the Kiro IDE on a Claude model, from the handoff prompt `.lead/prompts/kiro-lead.md`. It
finished the seven units the Claude Code lanes had left committed but not landed.

### Custom agent: the independent auditor
- `.kiro/agents/albedian-auditor.md`, made with Kiro's custom agent creator. It audits one lane commit with no
  stake in it: patch checksum and patch-id, tests first, mutants, property seeds, evidence labels, fabrication,
  trip privacy, dashes and conventions, contract parity and security, then writes a PASS or FAIL verdict.
- It wrote 21 verdicts, several at once, each in its own detached audit worktree (`.lead/kiro/verdicts/`).
  Twice it refused to start because the lead's request named a commit hash that did not exist; the lead had
  typed the hash instead of copying it from git output.

| Unit | Verdicts | What the auditor caught |
|---|---|---|
| integrity 27 | PASS | Ran 34 of its own samples through TS and Python; 33 agree, and the one difference predates the unit |
| experience 9m | FAIL, FAIL, PASS | Leaving from the compare wipe restored the session start (v1); after the lead's first fix, a wipe opened over the sky or 3D space counted as the moment left (v2) |
| render 14b | FAIL, PASS | Two surviving mutants: the stay guard at 1.5 times and the one light year tolerance at 0.05 |
| hud 5b | FAIL, PASS | Sideways on a notched phone the star landed under the sheet: the layout ignored the right safe-area inset |
| language 6b | FAIL, FAIL, PASS | Python and JavaScript whitespace differ inside the new star name pattern (448 of 88,320 phrases); then four look-behind mutants |
| solver 10l | FAIL x4, PASS | The noise floor fell on stacked and colour images (real clip detections 129 to 1,214); a zero MAD fallback below the old one; injected test stars scaled to one noise and found against another (an SNR 18 star read 5,102 sigma) |
| audio 15 | FAIL x4, PASS | A star note played with Music set to Off; then while the music was unavailable; then a note in a silence was cut at full level; then the doc misstated the output route's level |

### Fixes
- Kiro general task agents, one per lane worktree and run in parallel, wrote the first fixes from the shared
  brief `.lead/kiro/FIX_BRIEF.md` and the failing verdict: render 14b v2, hud 5b v4, language 6b v2 and v3,
  solver 10l v2 and v3, audio 15 v2 and v3.
- The lead wrote experience 9m v2 and v3, solver 10l v4 and v5, and audio 15 v4 and v5, each tests first.
- A fresh auditor checked every fix; the lead never audited its own.

### Landed on main by Kiro, all pushed
| Commit | Unit | Written by | Kiro's part |
|---|---|---|---|
| `6549f476` | data 11 (226 Hipparcos stars) | data lane | landed (the Claude Code auditor lane had passed it) |
| `e64b0f67` | integrity 27 | integrity lane | audit, gates, landed |
| `b79ec740` | experience 9m | experience lane, then the Kiro lead | two fixes, audits, gates, landed |
| `075feab0` | hud 5b | hud lane, then a Kiro fix agent | audits, fix, gates, landed |
| `053b6eed` | render 14b | render lane, then a Kiro fix agent | audits, fix, gates, landed; rebased onto 9m by the lead |
| `51bcc3df` | language 6b | language lane, then Kiro fix agents | audits, two fixes, gates, landed |
| `000c4b3b` | solver 10l | solver lane, then Kiro fix agents and the lead | audits, four fixes, gates, landed |
| `2d49b447` | audio 15 | audio lane, then Kiro fix agents and the lead | audits, four fixes, gates, landed |

Every landing ran all six gates on the lane commit through `.lead/locked_gates.zsh`, then the combined commits on
a scratch worktree (`.lead/kiro/land_batch.zsh`), and landed only when main's tree equalled the scratch tree.

### Spec
- `.kiro/specs/ios-capture-companion/requirements.md` (a draft, committed 2026-11-11, still waiting for the owner's
  yes): a native iOS app for aiming and capture that records where the phone pointed as a solver hint, never as
  evidence, with property tests derived from the requirements.

## What Kiro did (2026-11-10 evening to 2026-11-11)

After the owner decided that every photo or clip must get a sky, visible stars or not, Kiro wrote a spec and built
it with sub-agents, each unit in its own worktree, tests first, audited by a fresh `albedian-auditor`.

### Spec: `.kiro/specs/predicted-sky-for-every-upload/`
- `requirements.md` (REQ-SKY-1 to 7, with properties P-SKY-1 to 9 derived from them), `design.md` and `tasks.md`,
  written by the Kiro lead from three Kiro `context-gatherer` analyses of the upload path, the horizon solver and the
  solution contract, and the web display.
- The three layers: the sky predicted for the file's time and place (`predicted`); the predicted sky lined up over
  the photo from an approximate pose with its uncertainty (`predicted`, solution `approximate`); exact
  `catalog_matched` only through the unchanged held-out verification.

### Units
| Unit | Written by | Audits | State |
|---|---|---|---|
| sky-2, predicted sky for every upload with a recorded time and place | Kiro builder agent | PASS | landed `eeab4d15` |
| sky-1, the pose estimate contract (TS and Python) | Kiro builder, then a Kiro fix agent | FAIL, PASS | landed `cd10d044` |
| sky-3, the head-worn Moon pose | Kiro builder, amended three times on the lead's decisions | not yet | branch `kiro/sky` |
| sky-5, the web display of the layers | Kiro builder, then Kiro fix agents | FAIL | branch `kiro/sky-web`, fixed, re-audit next |
| solver-10k, tile catalog in the triangle search | Kiro builder, then Kiro fix agents | FAIL, FAIL | split: the tile pass kept for 10k-b |
| solver-10k-a, the pinned search made fast | Kiro fix agent | FAIL (non-finite input crash, a timing flake) | fixed on branch `claude/lane-solver-bf963f`, re-audit next |

- What the auditors caught: a calibrated tile-pass solution naming stars 62 to 80 deg outside the field, through
  radial terms that folded over (solver-10k); then wrong stars inside the field from fits pushed to the distortion
  bound (solver-10k v3); a catalog-backed predicted finding with a pixel detection that no test would have refused
  (sky-1); a clip gate keyed on a clock the stage never followed, and "too few stars" said of photos with many
  (sky-5); a NaN detection that crashed the fast search where main returned a solution (solver-10k-a).
- The real case, the owner's newest head-worn clip, counts only: before, `unresolved` with 146 pixel-only
  detections; with sky-2 on main, `predicted_only` with 65 predicted findings; with sky-3 (not yet landed),
  `approximate`, lined up from the Moon 3.30 s into the clip, within about 9 degrees near the Moon.

### Not done yet
- Steering that carries the owner's rules (no trip data, no dashes, commit conventions), hooks for the checks
  run by hand (dash, trip, contract parity, a unit ready report), MCP for CI runs, and the Kiro CLI.
