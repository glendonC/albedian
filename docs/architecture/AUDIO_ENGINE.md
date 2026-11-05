# Albedian Spatial Audio Engine

Status: **Locally implemented and tested capability** (November 2026). Pure mappings are covered by
property tests; the Web Audio graph was exercised in Chrome 152 on macOS. Since 2026-11-04 the
catalog stars on screen drive the voices, with Gaia DR3 temperatures and luminosities, and the
camera turns the listener (section 8). Nothing here has been judged by a human ear yet; section 10
lists what needs one.

Code: [`apps/web/src/audio/`](../../apps/web/src/audio/). Listening harness:
[`apps/web/spike/audio/`](../../apps/web/spike/audio/) (run `pnpm dev`, open `/spike/audio/`).

## 1. Rules the engine follows

1. **One perceptual channel per physical quantity.** Each quantity drives exactly one audible
   attribute, listed in section 4. Pitch carries no quantity (section 4.8).
2. **A missing input silences what it drives.** Every mapping returns `null` for a missing or
   invalid input and the engine renders `null` as silence. A star without `teffK` is not played
   with a "neutral" timbre; a voice without `residualArcsec` gets no uncertainty layer at all
   (not a clean interval, which would claim a lock); without `extinctionAvMag` there is no
   muffling filter. `getSnapshot().silent` names the missing inputs.
3. **No narration and no theater.** The procedural per-regime drones (the "MHD convection hum",
   the "relativistic sub-bass") were removed because they encoded no data. With no data, enabling
   audio plays only the UI detent.
4. **No AudioContext before a user gesture.** Importing the module and every setter are
   context-free; only `toggle()`/`enable()` (or `SpatialAudioEngine.start()`) create or resume
   one, and those are called from click handlers. A test stubs `window.AudioContext` to check it.

## 2. Architecture

```mermaid
flowchart LR
    subgraph Pure["Pure (Node-testable)"]
        Sky["skySources.ts<br/>catalog stars on screen,<br/>camera orientation in ICRS"]
        Inputs["AudioSourceInput[]<br/>sky brightness, unveil,<br/>regime, orientation"]
        Sky --> Inputs
        Plan["planVoice()<br/>mappings.ts"]
        Select["selectVoices()<br/>8 voices, targets first,<br/>3 dB hysteresis"]
        Pitch["assignIdentityPitches()"]
        Inputs --> Plan --> Select --> Pitch
    end

    subgraph Graph["Web Audio graph (engine/)"]
        Pool["VoicePool: 8 slots<br/>generators -> dust LP -> distance LP<br/>-> level -> direct -> HRTF PannerNode"]
        Reverb["ConvolutionReverb<br/>(Aachen chapel IR, calibrated return)"]
        Bed["SkyglowBed<br/>Omnitone FOA (default)<br/>or plain stereo"]
        UI["UI bus: detent, lock tone"]
        Master["MasterBus: compressor -> limiter<br/>(makeup removed) -> soft clip<br/>-> volume -> fade -> destination"]
        Pool -->|dry| Master
        Pool -->|send| Reverb --> Master
        Bed --> Master
        UI --> Master
    end

    Pitch --> Pool
```

| File | Role |
|---|---|
| `mappings.ts` | Every data-to-parameter mapping (pure). |
| `voicePlanning.ts` | Input types, `planVoice`, `selectVoices`, identity pitches, listener geometry (pure). |
| `skySources.ts` | The catalog stars on screen as `AudioSourceInput`s from core and astro tiles, and the camera's orientation in ICRS (pure). |
| `SkyAudioFeed.ts` | Follows the camera pose each frame while audio is on, with its own tile loader; the `skyAudio` singleton (section 8). |
| `audioCredits.ts` | The credit lines for Omnitone, its SADIE filters and the chapel response, and the URL of `assets/THIRD_PARTY_NOTICES.txt` (section 7). |
| `dsp.ts` | Seeded noise, diffuse-field encodings, soft-clip curve, Schroeder phases, PeriodicWave coefficients, pulse-train series, IR energy normalization (pure). |
| `engine/SpatialAudioEngine.ts` | Composition root and lifecycle; `getSnapshot()` reports what sounds and why. |
| `engine/VoicePool.ts` | Bounded pool of HRTF voices realizing plans. |
| `engine/SkyglowBed.ts` | Bed renderers and the Omnitone loader with fallback. |
| `engine/ConvolutionReverb.ts` | Shared convolution reverb and its calibration. |
| `engine/MasterBus.ts` | Dynamics, clipping, volume, per-ear meter. |
| `ObservatoryAudioEngine.ts` | Public facade keeping the original API; `observatoryAudio` singleton. |
| `omnitone.d.ts` | Types for the parts of Omnitone the bed uses (the package ships none). |

**Lifecycle.** `start()` builds the graph, then loads two things asynchronously: the impulse
response (800 KB WAV, fetched on first enable) and Omnitone (dynamic `import()`, 52 KB minified
ESM). Neither is fetched for users who never enable audio. `stop()` fades the master out and
suspends the context 1.2 s later, so a disabled engine costs no CPU; the next `start()` resumes it.
Data set before `start()` is kept and plays at once when audio is enabled.

## 3. Spatial bed: library evaluation and choice

| | Omnitone 2.0.0 | Resonance Audio Web SDK 1.0.0 | Plain Web Audio |
|---|---|---|---|
| Maintenance (checked November 2026) | Active: 2.0.0 published September 2026, repository pushed October 2026 (CSP, bundler, promise-rejection and rotation fixes). | Repository archived (last push March 2022); last npm publish November 2017. Core `resonance-audio` repo also archived. | Browser API. |
| License | Apache-2.0; bundled Google/SADIE HRIRs Apache-2.0, (c) 2017 University of York. | Apache-2.0. | n/a |
| Offers | First- and higher-order ambisonic decoding to binaural through measured HRIRs; sound-field rotation; no runtime dependencies. | Shoebox room model (early reflections and late reverb from room size and materials), ambisonic panning, an old Omnitone 1.x inside. | `PannerNode` (HRTF) per point source; no representation of a whole field. |
| Types | None shipped (local shim). | None. | Built in. |

**Choice: Omnitone FOA for the bed; plain Web Audio HRTF panners for the point voices.**

1. The bed is a field around the listener, not a point. Ambisonics represents a whole field in a
   fixed four channels however many directions contribute, and rotates it with one 3x3 matrix.
   Plain Web Audio would need one HRTF `PannerNode` per direction.
2. An isotropic diffuse field encodes exactly as four uncorrelated noise signals with SN3D weights
   (1, 1/sqrt(3), 1/sqrt(3), 1/sqrt(3)) and decodes through measured HRIRs, so the interaural
   coherence comes from the decoder instead of a hand-set crossover (Engineering inference: FOA
   still over-estimates coherence at high frequencies compared with a real diffuse field).
3. Directional skyglow, such as a light dome on one horizon taken from the photo's background
   map, would cost four gains and the rotation matrix, not more panners (**Proposed design**, not
   built: today the only input is a single sky-brightness value, so the bed is isotropic).
4. It is maintained, permissively licensed, dependency-free, and lazy-loaded after the gesture.

Resonance Audio is rejected: archived, and its shoebox room model has no physical meaning in space
while duplicating what the convolution reverb already does.

**Fallback and A/B.** A plain Web Audio bed (`stereo-diffuse`: shared content below 700 Hz,
independent above) is used when Omnitone fails to load or initialize, and the reason appears in
`getSnapshot().bed.fallbackReason`. It is also selectable in the harness to compare by ear. The two
renderers are matched in per-ear power (Omnitone +2.8 dB, measured; section 6).

**Omnitone behaviours to know.** It logs `[Omnitone] ...` lines to the console on import and on
each initialization. When it detects Safari and both `webkitAudioContext` and
`webkitOfflineAudioContext` exist, it overwrites `window.AudioContext` with the prefixed one; the
bed declines Omnitone when the prefixed constructor is a different class, so that global is never
changed under the rest of the app.

## 4. Mappings

Defaults live in `mappings.ts` as named constants. "Basis" separates **Verified external
capability** (a cited fact), **Engineering inference** and **Proposed design** (a choice that needs
listening).

### 4.1 Distance: direct-to-reverberant ratio and high-frequency rolloff

- Input `distancePc`. `p` is its position, in log distance, inside a window. A star takes
  `STAR_DISTANCE_WINDOW`, 1 to 2,000 pc, in every regime; other kinds take the window of the
  current regime (`REGIME_DISTANCE_WINDOWS`, e.g. 1,000 to 10 million km for `cislunar`), set by
  `setScaleRegime` (`distanceWindowFor`). A window is a display choice like a map's scale bar.
  Before 2026-11-04 stars took the regime's window too, so at cislunar scale (landing scene 01)
  every star clamped to the far edge (DRR -15 dB, 3 kHz low-pass) and at deep-sky scale (scene 02)
  to the near edge, and distance said nothing there.
- DRR = lerp(+15 dB, -15 dB, p). The dry and reverb sends split unit energy:
  direct = sqrt(r/(1+r)), reverb = sqrt(1/(1+r)), r = 10^(DRR/10), so distance moves energy
  between direct and reverberant without changing the voice's loudness.
- Low-pass cutoff = 18 kHz x (3 kHz / 18 kHz)^p, Q 0.5.
- Basis: level and DRR are the main absolute distance cues, with spectral loss adding at long
  range, and perceived distance is a compressive function of physical distance (Verified external:
  Zahorik, Brungart and Bronkhorst 2005, Acta Acustica united with Acustica 91(3), 409-420). The
  +/-15 dB range, the 3 kHz far cutoff and the windows, the stellar one included, are Proposed
  design.
- Delivered DRR matches the plan within 0.8 dB in Chrome (section 6).

### 4.2 Luminosity: loudness at the source, attenuated with distance

- Inputs `luminositySolar`, `distancePc`. m_bol = 4.74 - 2.5 log10(L) + 5 log10(d / 10 pc)
  (apparent bolometric magnitude, no extinction term).
- Level = -10 dBFS - 4 x kappa x (m_bol + 1.5), kappa = 1, capped at -6 dBFS. Below -60 dBFS the
  voice is silent as too faint, which `getSnapshot()` reports separately from missing data.
- Basis (Verified external): Pogson's scale makes one magnitude exactly 4 dB of intensity; IAU 2015
  Resolution B2 fixes M_bol(Sun) = 4.74; source power proportional to L with inverse-square
  attenuation is the same thing as 4 dB per magnitude of apparent brightness, so the loudest voice
  is the brightest-looking object. Engineering inference: in Stevens' tables loudness grows with
  sound pressure to the 0.67 power (about 0.33 of intensity) and brightness of a 5 degree target in
  the dark with luminance to the 0.33 power, so intensity-for-flux keeps the two percepts growing at
  similar rates (Poulton's caveat applies: these exponents depend on procedure).

### 4.3 Effective temperature: timbre brightness

- Input `teffK`, clamped to 2,000 to 40,000 K. The voice is a harmonic tone at its identity pitch
  whose harmonic powers sample the Planck curve scaled into hearing:
  P_n proportional to x^3 / (e^x - 1), x = n f0 / f_T, f_T = (1000 Hz / 3.8322) x (T / 5772 K),
  normalized to unit power, with Schroeder (1970) phases to keep the crest factor low.
- 3.8322 = 4 zeta(5) / zeta(4) is the power-weighted mean of B_nu in units of kT/h, so the
  spectral centroid is proportional to Teff (Wien displacement): the Sun's voice sits at 1 kHz.
  Measured in the browser: 3000 K 521 Hz, 5772 K 1002 Hz, 13000 K 2252 Hz, 25000 K 4326 Hz.
- Harmonics stop at 16 kHz (below 0.45 of the sample rate). The engine's `harmonicCeilingHz`
  option lowers that ceiling only for the listening session's diagnostic clip (section 10,
  item 0); the app keeps the default.
- Basis: the Planck curve is Verified external physics; the scale factor (Sun at 1 kHz) is
  Proposed design. That the centroid rises strictly with Teff for discrete harmonics follows from
  the curve's monotone likelihood ratio and is property-tested.

### 4.4 Skyglow: broadband noise bed

- Inputs `skyBrightnessMagArcsec2` (V mag/arcsec^2) and the user's `unveil` control (0 to 1).
- ratio = max(0, 10^(-0.4 (SB - 22.0)) - 1): the ratio of artificial to natural sky brightness.
  Level = -34 dBFS + 10 log10(ratio x (1 - unveil)), capped at -12 dBFS, silent below -72 dBFS.
  A natural sky (SB 22.0 or darker) is silent: the hiss is the light pollution only.
  The bed is heard only in the `earth_capture` regime (skyglow exists inside the atmosphere).
- Spectrum: pink noise, 180 Hz high-pass. Both are design choices: no measurement sets the bed's
  spectrum.
- Basis (Verified external): Falchi et al. 2016, Science Advances 2(6) e1600377, use a natural
  background of 174 ucd/m^2, about 22.0 mag/arcsec^2 in V, and map exactly this artificial-to-
  natural ratio. Intensity proportional to artificial flux is the Pogson choice again. Design
  intent (Proposed design): as the user unveils the sky, the hiss fades and faint voices emerge
  from it, an analogue of skyglow hiding faint stars. The masking threshold is not calibrated to
  the visual limiting magnitude.

### 4.5 Uncertainty: beating that resolves at lock

- Input `residualArcsec` (for example `ScientificEvidence.angularResidualArcsec`).
- Beat = 4 Hz x min(1, residual / 120 arcsec), proportional below full scale.
- Two quiet three-harmonic tones form a just fifth on twice the voice pitch. The upper tone is
  raised by beat / 2, so the lower tone's third harmonic and the upper tone's second harmonic beat
  at exactly `beat` Hz; at zero residual the fifth is clean (3:2). Without a residual the layer is
  absent.
- Basis: Proposed design (rates and full scale need listening).

### 4.6 Pulsars: real spin rates, no scaling

- Input `spinFrequencyHz` (ATNF `F0`). The pulse rate is that number, set on an oscillator's
  frequency parameter with no glide. Verified external values used in tests and harness presets
  (ATNF Pulsar Catalogue v2.6.1): Crab, J0534+2200, F0 = 29.9469230 Hz (PEPOCH MJD 48442.5);
  Vela, J0835-4510, F0 = 11.1946499395 Hz (PEPOCH MJD 51559.319).
- Sound: broadband noise gated by a Gaussian pulse train, FWHM 3 % of the period. The pulse shape
  is generic; only the rate comes from data. For pulsars the `luminositySolar` input is whatever
  power the caller supplies (the harness uses ATNF `EDOT` / 3.828e33 erg/s).

### 4.7 Dust column: muffling

- Input `extinctionAvMag`. Low-pass cutoff = 20 kHz x 2^(-A_V / 1 mag), floor 120 Hz, Butterworth.
  Without A_V the filter sits at Nyquist (an identity), so there is no muffling layer.
- Basis: dust removes short wavelengths first (reddening), and the audio analogue removes highs;
  the one-octave-per-magnitude rate is Proposed design.

### 4.8 What is not data

- **Identity pitch.** Voices take pitches from a just pentatonic set on 110 Hz (110 to 440 Hz),
  chosen by an FNV-1a hash of the object id, kept while the object stays in the pool and never
  shared by two active voices. Pitch keeps simultaneous voices separable and lets a listener follow
  one object; it encodes nothing.
- **UI cues.** `playDetentClick` is unchanged interface feedback. `playTargetLockTone(bv, panX)`
  is a clean just fifth (220 and 330 Hz) with the Planck timbre of Ballesteros (2012, EPL 97,
  34008, Eq. 14) applied to B-V, stereo-panned by `panX`. It is silent if either value is missing,
  and B-V outside -0.5 to 3.0 counts as missing because the paper gives no validity range and the
  formula has a pole near -0.67.

## 5. Master bus

Compressor at -10 dBFS, 4:1, hard knee, 5 ms attack, 200 ms release; limiter at -2 dBFS, 20:1,
1 ms attack, 100 ms release; soft clipper (identity to 0.75, tanh to a 0.966 ceiling, 4x
oversampling); volume (default 0.85, as before); enable fade; per-ear meter.

Web Audio's `DynamicsCompressorNode` applies automatic makeup gain, (1 / curve(0 dBFS))^0.6. With
the first settings tried (-20 dB 2.5:1 soft knee, -3 dB 20:1) Chrome added +7.2 dB to quiet signals
and compressed the 4 dB-per-magnitude scale above about -30 dBFS. Both stages now use a hard knee,
whose makeup gain has a closed form (`compressorMakeupGainDb`), and a gain node after each stage
divides it back out, so the bus is unity-gain below threshold.

## 6. Calibration measurements

Chrome 152 on macOS, 48 kHz. OfflineAudioContext renders used the same node settings as the engine;
live readings came from the master bus meter (mean per-ear power).

| Measurement | Result |
|---|---|
| HRTF `PannerNode` per-ear gain, pink noise, front / azimuth -40 elevation 10 | -2.1 dB / -1.9 dB |
| Same for the Sun-like voice tone (5772 K at 330 Hz) | -1.8 dB / -1.0 dB |
| Unit-energy IR per-ear gain: pink noise, 3000 K, 5772 K, 25000 K tones | +4.6, +5.6, +5.0, +3.9 dB |
| Resulting reverb return calibration (pink noise, frontal) | -6.7 dB (`REVERB_RETURN_CALIBRATION_DB`) |
| Delivered vs planned DRR, live, Sun-like voice at 1.5 / 10 / 300 pc | 14.0 vs 13.4, 6.6 vs 5.9, -6.7 vs -7.5 dB |
| Voice level across those distances with L scaled by d^2 | constant within 0.8 dB |
| Makeup gain of the first compressor settings (Chrome) | +5.5 dB and +1.8 dB (formula gives +1.71 dB for the second) |
| Bed per-ear power at the same requested -20.2 dB: Omnitone FOA vs stereo | -40.5 vs -37.7 dBFS (trim +2.8 dB) |
| Output with every source silent | -104 dBFS |

The DRR calibration was taken on pink noise; for cool stars (most energy below 1 kHz) the wet path
runs about 1.8 dB hotter than for the Sun-like tone. A per-voice correction is possible later.

Until 2026-11-04 every parameter glide that followed a value set at the same instant started from
the parameter's default instead (section 10, item 0): onsets were wrong (the bed opened at full gain,
voice attacks and the master fade-in were skipped). The steady-state figures above are not onsets.

## 7. Assets and licenses

**Impulse response** `apps/web/src/audio/assets/aachen-nikolauskapelle-s1-r12-stereo.wav`
(48 kHz stereo, 4.17 s, 800,660 bytes). Derived from Martin Zerwas and Selin Kayku, "Room acoustic
measurement and decay analysis data of the St. Nicholas Chapel, Aachen Cathedral", version 1.2,
Zenodo, https://doi.org/10.5281/zenodo.23312556, licensed **CC BY 4.0**. Measured response
`S1-Do_R12.wav` (FuMa B-format; source and receiver in the main hall, 9.24 m apart; broadband T30
about 4.0 s from our analysis, mid-frequency T about 4.6 s per the dataset). Changes: direct sound
removed (trim 3 ms after onset), decoded to two coincident virtual cardioids at +/-90 degrees,
40 Hz high-pass, fades into the noise floor at 4.2 s, gain to -1 dBFS, 16-bit with triangular
dither. Full record, checksums and the exact operations:
[`ATTRIBUTION.txt`](../../apps/web/src/audio/assets/ATTRIBUTION.txt). The late-tail interchannel
correlation is 0.52 to 0.55, as expected (0.5) for that cardioid pair in a diffuse field.

Why not OpenAIR: in November 2026 both `openair.hosted.york.ac.uk` and `openairlib.net` served an
expired TLS certificate, and a binary asset was not downloaded over an unverified connection.
The Zenodo record was fetched over verified HTTPS; only the one member was transferred, using HTTP
range requests, and its CRC-32 was checked against the zip directory. The chapel is an earthly room:
its tail is a device for the distance cue, not a claim about the acoustics of space.

Reproduce the conversion (ffmpeg 8.1, from the extracted member):

```sh
ffmpeg -i S1-Do_R12.wav -af "atrim=start=0.03012:end=4.2,asetpts=PTS-STARTPTS,\
pan=stereo|c0=0.70710678*c0+0.5*c2|c1=0.70710678*c0-0.5*c2,highpass=f=40:p=2,\
afade=t=in:st=0:d=0.003:curve=hsin,afade=t=out:st=3.37:d=0.8:curve=hsin" -c:a pcm_f32le stereo.wav
ffmpeg -i stereo.wav -af "volume=3.699dB,aresample=dither_method=triangular" -c:a pcm_s16le \
  -map_metadata -1 -fflags +bitexact -flags:a +bitexact aachen-nikolauskapelle-s1-r12-stereo.wav
```

**Omnitone 2.0.0** (npm `omnitone`, Apache-2.0, copyright Google). Its bundled HRIRs are derived
from the Google/SADIE binaural filters, copyright 2017 University of York, also Apache-2.0; the
notice is `src/resources/NOTICE` in the Omnitone repository. Shipping the built app redistributes
both, so the app must carry the Apache-2.0 text and that NOTICE. The npm package ships no NOTICE
file (only `LICENSE`, `README.md` and `build/`), and the bundled chunk keeps Google's licence
headers but no word of SADIE or York (tested on a local build, 2026-11-04).

**Where the notices ship.** `apps/web/src/audio/assets/THIRD_PARTY_NOTICES.txt` holds the
Apache License 2.0 exactly as the package's `LICENSE` carries it, Omnitone's NOTICE quoted
exactly (fetched once from the v2.0.0 tag on 2026-11-04, 747 bytes, SHA-256
`2a35afd9c4c6d2fc9271a25ccc3c1442e0cf61c3284265722f158b78f33808a8`), and the impulse
response's `ATTRIBUTION.txt` with its CC BY 4.0 licence. `audioCredits.ts` gives one credit line
per work, worded as each source asks, and the notices file's URL, which the build emits as an
asset once the app imports the module, as it does the impulse response. Showing the lines beside
the canvas while audio is on, with a Notices link, is the viewport's part (render lane). A test
checks that each embedded text matches its source byte for byte.

## 8. Integration with the viewport and renderer

**The catalog stars on screen drive the voices** (since 2026-11-04). While audio is on, the viewport
calls `skyAudio.follow(pose)` each animation frame with the renderer's `getCameraSkyPose()`, and
`skyAudio.setEvidenceClass()` with the evidence class of the drawn `catalog_stars` layer. Those
hookups belong to the render lane (`SpatialObservatoryViewport.tsx`, `cosmosRenderer.ts` and both
renderers). A renderer that reports no pose leaves the sky silent; enabling audio then plays only
the detent.

- **One frame, ICRS.** The pose carries two world quaternions: the camera's, and the catalog star
  sphere's (ICRS axes into world axes). The listener orientation is the camera's turn in ICRS,
  `sphere^-1 x camera` (`listenerOrientationIcrs`), and each direction is its tile's ICRS unit
  vector, so the two share a frame by construction. A test builds the renderers' scene graph (a
  turned parent, the ICRS sphere centred on the camera, a camera turned inside a moved rig) and
  checks that every star sounds from the direction the camera sees it drawn. The stars sit at
  infinity, as drawn, so there is no parallax.
- **Which stars.** Those on screen (inside the frustum) and brighter than the star field's limiting
  magnitude, from the release the star field draws. Each becomes a `star` source with its core
  tile's ICRS direction and Bailer-Jones (2021) median distance and its astro tile's GSP-Phot
  temperature and FLAME luminosity medians. A value Gaia DR3 lacks stays null, and that star is
  silent. The feed passes the 24 loudest stars with all three values (by received level) and the 8
  brightest missing one, so `getSnapshot().silent` names them; the engine picks 8 voices from those.
  The two bounds limit cost; they are not data. A star of a tile whose astro tile is still loading,
  or failed to load, is left out rather than passed as missing. A voiced star keeps its voice until
  about 2 degrees past the screen edge, so jitter there does not cut it.
- **Loading.** The feed keeps its own `CatalogStarTileLoader` on the star field's release, given the
  same view cone and limit, so it reads the core files the star field reads (and decodes them a
  second time) and fetches the astro tiles of the tiles in view. It fetches nothing before the first
  pose, that is, before the user enables audio. At the default limit, 6.5, only the naked-eye layer
  is read: on the landing, 7 of its 12 core tiles and their 7 astro tiles.
- **Evidence class.** The voices carry the class of the star layer they sound (`simulated` on the
  cislunar landing, which both renderers declare) and none where a renderer declares no layers.
  Sound cannot show a class, so an inspector should disclose it; `getSnapshot()` passes it through.

What the real rows give (tested, `skySources.test.ts` and `skyAudioFeed.test.ts`): in a 20 degree
view on Cassiopeia, 289 stars are on screen and 105 of them have all three values. Segin (eps Cas:
14,806 K, 3,062 Lsun, 140.4 pc) plans at -23.0 dBFS, 6.3 dB above Achird (eta Cas: 5,726 K,
1.27 Lsun, 5.92 pc), though both are G 3.3, because Segin's bolometric flux is the larger; its
centroid is 2.6 times higher. Caph, Schedar and Ruchbah there, and Sirius and Vega elsewhere, are
Hipparcos entries with distances but no Gaia DR3 temperature or luminosity, so they are silent
with `missing: ['luminositySolar', 'teffK']`.

Measured in the app (Chrome 152 on macOS, 2026-11-04, with the render lane's hookups applied
locally): the WebGL landing had 821 stars on screen, 357 of them with all three values, and 8
voices from -25.8 to -30.7 dBFS with centroids from 776 to 1,435 Hz; the WebGPU host had 775 and
332. A drag turned the voices with the camera, the listener stayed put while audio was off, and the
console showed no errors. One source update (9 tiles, 9,222 stars, 1,097 on screen) took 0.8 ms and
runs at most ten times a second; a listener turn, which replans 32 sources, took 0.02 ms per frame.
More than half of the naked-eye stars on screen are silent (464 of 821 on the WebGL landing), the
brightest among them.

**Not wired, and why.**

| Engine input | State | Why |
|---|---|---|
| `setSkyBrightness` | null, so the bed is silent | `/api/science/radiometry-audit` gives a Krisciunas and Schaefer (1991) sky brightness: natural sky (21.6 mag/arcsec^2 at the zenith, scaled by airmass) plus moonlight, with no artificial term (`services/science/radiometry.py`). The bed sounds artificial skyglow only (section 4.4), so fed this value a moonless, pristine sky would hiss at about -37.5 dBFS and moonlight would sound as light pollution. It waits for a source of artificial brightness: the photo's measured sky background less the Krisciunas and Schaefer prediction, once a solve has photometry, or the Falchi et al. (2016) atlas (CC BY-NC 4.0, available by request). |
| `setUnveil` | 0 | Nothing in the app produces a reveal fraction yet: there is no Unveil gesture, and nothing calls `setLimitingMagnitude`. |
| `isTarget` | absent | No catalog star can be selected yet. |
| `residualArcsec` | absent | Catalog stars carry no solve residual; a matched finding's `ScientificEvidence.angularResidualArcsec` would. |
| `extinctionAvMag` | absent | GSP-Phot A0 is the extinction at 547.7 nm, not A_V, and the dust channel is unheard (section 10, item 4). |
| `spinFrequencyHz` | absent | The app has no pulsar catalog; an ATNF subset would be needed. |

The header volume slider sets `setMasterVolume` (the viewport's volume effect); its 0 to 1 value
is the master gain. For stars matched in a user's photo, carrying temperature, luminosity and
extinction on findings is a contracts decision, escalated rather than made here.

## 9. Verification

- `pnpm typecheck` and `pnpm test` from the repository root; the audio suites are
  `apps/web/src/audio/__tests__/` (mappings, voice planning, DSP, engine lifecycle, sky sources,
  sky feed). Property tests (fast-check) cover monotonicity, bounds and silence-on-missing for every
  mapping, energy preservation of the DRR split, exact pass-through of spin rates, pool bounds and
  hysteresis, pitch uniqueness, quaternion invariants, the clip curve and IR normalization, and for
  the sky: the listener orientation and the drawn direction in one frame, the view test against
  three's projection, catalog values passed through with missing ones left missing, the limiting
  magnitude, and a bounded, ranked source list whatever the tile order. `ALBEDIAN_FC_SEED` pins
  the fast-check seed for repeated runs.
- The sky suites read the committed naked-eye tiles of `apps/web/public/catalog/stars/gaia-dr3/`
  (real rows: Segin, Fulu, Achird, Caph, Schedar, Ruchbah, Sirius, Vega) and run the feed through
  the real engine with no AudioContext.
- The harness typechecks with `pnpm --dir apps/web exec tsc -p spike/audio/tsconfig.json`.
- In Chrome 152: no context before the click; IR and Omnitone load after it; 16 sources reduce to
  8 voices with the target first; dust, beating, pulsar rate and centroids match their formulas; the
  app's Audio button still toggles; no console errors. Safari, Firefox and mobile are **not tested**.

## 10. What needs a human ear

The listening session (`apps/web/spike/listening/`) turns these items into short clips for the
owner. `items.ts` holds each question and what each clip plays: catalog stars named by their IAU
names, whose values are read from the tiles at render time; published values with their
citations (the pulsars); and, for the channels the app has no data for yet (skyglow, uncertainty,
dust), test values labelled "test value, not a measurement". `render.mjs` renders every clip
through the real engine on an `OfflineAudioContext` in headless Chrome (muted, with a throwaway
profile), encodes AAC copies with ffmpeg and builds the session page in `.cache/listening/page/`,
which is published as a claude.ai Artifact that keeps the owner's yes, no and note per item in its
database. With the web app running on a port:

```sh
node apps/web/spike/listening/render.mjs --url http://localhost:5188 --out .cache/listening
```

0. First listen (asked first): the owner heard the landing's voices unprompted, on a laptop, and
   asked whether a static-like sound came from the app (2026-11-04). The clips compare the landing
   as the app plays it with the same voices dry, the loudest voice alone, and the voices with
   harmonics cut above 4 kHz.
   Found while measuring them (tested, OfflineAudioContext in headless Chrome 156, 2026-11-04):
   every start of the engine opened with a burst of noise. In each of clips A to D, energy above
   12 kHz stood about 56 dB over its steady level at 0.17 s and was gone by about 2.7 s; with no
   sources at all, and the bed meant to be silent, the same burst played and faded at about
   24 dB a second. The cause was `glideParam`: its `cancelScheduledValues(now)` deleted a value set
   at that same instant, so a glide on a new node started from the parameter's default, a gain's 1.
   A 1 kHz tone set to 0 and glided to 1 that way peaked at 1.000 in its first 2 ms instead of
   0.022. So the skyglow bed opened at full gain (plus Omnitone's +2.8 dB) and faded over its
   0.35 s time constant, each new voice skipped its 80 ms attack and started with a click, the
   uncertainty layer opened at full gain, and the master fade-in was skipped. The glide now cancels
   only what comes after its own start, and the same no-source render is silent. The burst, about
   12 dB above the star chord at the app's volume (inference from the bed's noise level and trim),
   and the voice clicks are the likeliest static the owner heard. The clips were rendered again
   after the fix.
1. Balance between bed, voices and UI detents; every absolute level is an engineering default.
2. Distance: whether +/-15 dB of DRR, the stellar window (1 to 2,000 pc) and the regime windows
   read as "near" and "far" rather than "dry" and "wet"; whether the 3 kHz far cutoff fights the
   Teff timbre.
3. Teff timbre: whether 4.3 octaves of centroid travel is pleasant; hot stars put energy in many
   unresolved harmonics and may sound buzzy, cool stars are close to a sine.
4. Separability of the three spectral channels (Teff, distance rolloff, dust): a hot, distant,
   dusty star and a cool, near one may sound alike. Untested.
5. Loudness at 4 dB per magnitude: whether the range is right in practice. Perceived loudness also
   depends on spectrum (bright timbres sound louder at equal power) and is not compensated.
6. Skyglow: hiss colour, the -34 dB reference, and whether faint voices drop out under the hiss at
   roughly the sky brightness where the eye loses them.
7. Bed renderers: Omnitone FOA against the plain stereo bed, on headphones and on speakers. They
   are matched in power, not in loudness.
8. Uncertainty: the 4 Hz ceiling, the 120 arcsec full scale, the level of the fifth against the
   star tone, and whether "no layer" and "clean fifth" are distinguishable.
9. Pulsars: the generic 3 % pulse and noise carrier; envelopes are peak-normalized, so a pulsar
   sounds quieter than a steady star at the same mapped level; very slow pulsars become soft swells.
10. Identity pitch: whether listeners read pitch as data although it carries none.
11. Reverb: the chapel's warm 4 s tail as the "space" around voices; the cardioid-pair width.
12. HRTF: Chrome's generic built-in set (front/back confusion, no personalization); the voices
    (browser HRTF) and the bed (SADIE) use different HRIR sets.
13. Transitions: attack and release constants, and pitch glides when a voice is reassigned.

## 11. Limitations and open questions

- **Unresolved technical question:** the Safari path (Omnitone's global patch guard, the
  `StereoPannerNode` lock tone) and Firefox behaviour are untested.
- Absolute output levels include the HRTF panner's roughly -2 dB per ear.
- Beds use 10 s noise loops (about 7.7 MB at 48 kHz for the four-channel bed) generated on the main
  thread at first enable.
- Omnitone's console logging cannot be turned off.
- The brightest stars of the sky are silent: the 226 Hipparcos entries (Sirius, Vega, Capella,
  Deneb and the rest of the bright end) carry no Gaia DR3 temperature or luminosity, nor do many
  Gaia stars of the naked-eye layer. That is the data, not a fault; a source of temperatures and
  luminosities for the bright end would need its own catalog, citation and labels.
- The sky feed decodes the core tiles a second time, beside the star field's loader. Sharing one
  loader would need the star fields to expose theirs.

## 12. Lessons from published sonifications

Read at the sources on 2026-11-04:

- NASA Chandra X-ray Center, "A Universe of Sound", https://chandra.si.edu/sound/, with the release
  pages https://chandra.si.edu/photo/2021/sonify4/ (Westerlund 2, Tycho's supernova remnant, M87)
  and https://chandra.si.edu/photo/2024/sonify9/ (Cassiopeia A, 30 Doradus, NGC 6872), credited to
  NASA/CXC/SAO/K. Arcand and SYSTEM Sounds (M. Russo, A. Santaguida).
- K. Arcand et al. 2024, "A Universe of Sound: processing NASA data into sonifications to explore
  participant response", Frontiers in Communication 9, doi:10.3389/fcomm.2024.1288896.
- SYSTEM Sounds, "TRAPPIST Sounds", https://www.system-sounds.com/trappist-sounds/, with
  D. Tamayo, H. Rein, C. Petrovich and N. Murray 2017, "Convergent Migration Renders TRAPPIST-1
  Long-lived", ApJL 840, L19, doi:10.3847/2041-8213/aa70ea.
- STScI's Astronify, https://astronify.readthedocs.io/, with J. Tucker Brown, C. M. Harrison,
  A. Zanella and J. Trayford 2022, "Evaluating the efficacy of sonification for signal detection in
  univariate, evenly sampled light curves using astronify", arXiv:2209.04465 (MNRAS 516, 5674).

### 12.1 Practices Albedian already follows, with their sources

- **The mapping is stated beside every sound.** Each Chandra release lists, per object, what drives
  what under "About the Sound": for Westerlund 2, brightness drives volume, height in the image
  drives pitch and left to right drives stereo position; for Tycho, X-ray colour drives pitch,
  redder lower. Arcand et al. recommend making "descriptions of the goals and the creation process"
  central, after open answers showed listeners misreading the medium. Albedian states each mapping
  in section 4, `getSnapshot()` reports what sounds and why, and the listening session lists every
  clip's values with their kinds.
- **Time scaling keeps real ratios.** The TRAPPIST-1 piece uses one speed-up for each use, the same
  for every planet. For pitch, time runs about 212 million times faster: planet h's 18.76-day orbit
  becomes 130.81 Hz (C3) and the star's rotation 745 Hz, so the resonant chain's ratios survive as
  a chord, slightly out of tune where the resonance is inexact. For rhythm, time is scaled so that
  planet h completes an orbit every 2 seconds (30 bpm), about 810,000 times faster (inference:
  18.76 days over 2 s): each planet plays one note per orbit, and each conjunction of neighbours
  strikes a drum (inference: the page calls the beat "related to the pulses of TRAPPIST Melody"
  and gives it no factor of its own). Albedian's pulsars play at their catalog spin rates with no
  scaling (4.6), and no value is quantized to a musical scale; the identity pitch carries no data
  (4.8).

### 12.2 Where Albedian differs

- **Data and design choices are labelled apart.** None of these pages separates them: Chandra
  presents its instrument assignments as mappings, and the TRAPPIST-1 page marks its choices (the
  key, the drum kit) only in passing. Albedian labels each value catalog, published, test value
  or listening device, and names the identity pitch, the chapel reverb and the bed spectrum as
  design choices.
- **One mapping per quantity everywhere.** Chandra varies its mappings "based on the object and the
  data available". Albedian keeps each quantity on one attribute in every view (rule 1), so a
  listener can learn the code once (inference; Tucker Brown et al. recommend training listeners).

### 12.3 What the evaluations found

- **Arcand et al. 2024:** 3,184 responses after cleaning, from blind or low-vision and sighted
  respondents. Enjoyment was high in both groups with no significant difference. Trust that the
  sound represents the data faithfully varied little between the groups, with three caveats from
  the authors: some of it may come from NASA's association with the project; it was the only rating
  where blind or low-vision respondents chose Agree more often than Agree Strongly; and "perhaps
  trust is the essential aspect to improve". Learning was self-reported: blind or low-vision
  respondents reported more learning for each of the three pieces, significantly so only for
  Cassiopeia A. A frequent misreading: listeners asked for "actual sound from space" and did not
  see how the sounds relate to the images. The authors name their limits (their section 4.1): a
  convenience sample, a possible social desirability bias (they asked about sonifications they
  made), few blind or low-vision respondents and a United States-heavy sample; and a single
  exposure cannot show long-term learning.
- **Tucker Brown et al. 2022:** 192 valid responses counted transit-like dips in synthetic light
  curves by sound, by plot or by both, under Astronify's linear brightness-to-pitch mapping. By sound
  alone, experts and non-experts counted correctly in 85 to 100 percent of cases at signal-to-noise
  ratios of 30 and 100, and were consistent with guessing at 3 and 5; at medium ratios experts did
  about two to three times better with plots. They recommend training, richer mappings and an
  animated plot with a moving cursor.

### 12.4 Proposed for Albedian, not built

Each needs its own unit with a file list; (a) and (d) come first, after the owner answers the
listening session.

- **(a) Stars as short events, diffuse light as sustained sound.** Chandra plays compact X-ray
  sources as bells or plucks and diffuse X-ray light as sustained tones (Westerlund 2, M87).
  Albedian sustains every star voice, and the first listener heard the landing as static (section
  10, item 0).
- **(b) A "listen to this view" sweep.** Chandra turns a still image into time with a scan (left to
  right, centre out, clockwise); each star on screen would sound as the sweep reaches it, at its
  received level.
- **(c) Show what is sounding.** Mark the voiced stars on screen, as an animated plot's cursor does
  (Tucker Brown et al.), since listeners missed how the sounds relate to the image (Arcand et al.).
- **(d) Say what the sound is.** Beside the Audio control, say that no sound reaches us from space
  and what the sound is made of: data and stated design choices, none of it a recording of space.
  Not every sound is data (the detent click and the identity pitch carry none, 4.8), and the chapel
  reverb is a recording of an earthly room. Listeners asked for "actual sound from space", and the
  same authors single out trust as the rating to improve (12.3).
- **(e) Keep fine distinctions on screen.** Sound alone carried strong signals and missed weak ones
  (Tucker Brown et al.); small differences of temperature, distance or residual need the
  inspector's numbers beside the sound.
- **(f) Orbits with stated time factors.** In the cislunar scale the Moon's motion could sound
  with one disclosed speed-up for each use (pitch, rhythm), as the TRAPPIST-1 piece does, so its
  real ratios survive.
