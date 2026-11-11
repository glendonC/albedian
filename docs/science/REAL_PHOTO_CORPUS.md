# Real photo corpus

60 photos by third parties, fetched from Wikimedia Commons under open licences, for testing the plate solver on real pixels. The simulated benchmark ([PHONE_PHOTO_BENCHMARK.md](PHONE_PHOTO_BENCHMARK.md)) knows the exact truth of every frame it renders, and a solve there never shows that a real photo solves. This corpus is the other half: real optics, real sensors, real phone processing and real skies, taken by other people.

Labels: [tested] by `tests/science/test_real_photo_corpus.py`; [verified] read at the source named; [inference] reasoned from what was read or measured, not tested; [proposed] a choice made here that a later unit may change; [open] not settled.

## What it is, and what it is not

- **Real photographs, kept as served.** Each file is the original that Commons serves, byte for byte: nothing is stripped, rewritten or re-encoded, so its own metadata is intact. [tested: each present file has the size and SHA-256 that `data/downloads.json` lists]
- **It holds no ground truth yet.** No photo here has a verified plate solution, and nothing in the manifest was measured from the sky. `statedSubject` records what the uploader's own description or categories say is in the picture, as tags from a closed vocabulary. It is the uploader's statement, not ground truth: a photo titled after a constellation may show another part of the sky, or clouds. Any claim a solver makes on these photos needs its own independent check.
- **The photos are not in the repository.** They live in the shared `.cache/real-photos/`, which git ignores. `data/downloads.json` lists each file's URL, size, SHA-256, licence and author; the manifest `research/benchmarks/real_photos/corpus.json` lists each photo's tier, kind and file facts.
- **No coordinate and no capture time in git.** A photo's position and time stay in the file's own metadata. The manifest says only whether the file carries a capture time, a UTC offset and a place, and on which side of the equator it was taken. [tested: the manifest has no field a coordinate, a time or place text fits in]
- **Other people's work.** CC BY and CC BY-SA require the credit below wherever a photo is shown or passed on, and CC BY-SA requires the same licence for anything derived from it.

## Tiers

| Tier | Minimum | Photos | What it holds |
|---|---|---|---|
| A, clean wide field | 8 | 13 | Single untracked exposures from cameras with a lens of at most 35 mm, with the focal length in the file, full of stars. |
| B, phone night sky | 16 | 22 | Phone photos of the night sky with their camera metadata: long exposures and night modes, star-rich and star-poor. |
| C, a beacon and a skyline | 10 | 14 | The Moon over a visible horizon at night or twilight, and the Sun over a skyline in daylight. |
| D, hard negatives | 8 | 11 | Real night or dark photos with no solvable sky. A solver must claim nothing on these. |

In all 60 photos and 397 MB (limits: about 60 photos, 500 MB in all, 40 MB a file). [tested]

What each tier's files carry, read from the files by the ingest readers (`services/ingest`):

| Tier | Makers | North | South | Side unknown | Capture time | UTC offset | Place |
|---|---|---|---|---|---|---|---|
| A | Nikon 7, Canon 4, Sony 2 | 6 | 6 | 1 | 13 | 5 | 4 |
| B | Apple 8, Samsung 4, Google 3, vivo 2, Xiaomi 2, Huawei 1, Nokia 1, OPPO 1 | 10 | 9 | 3 | 22 | 12 | 14 |
| C | Apple 6, Samsung 5, Sony 2, Nikon 1 | 11 | 3 | 0 | 14 | 10 | 12 |
| D | Nikon 4, Apple 3, Fujifilm 1, Pentax 1, vivo 1, Xiaomi 1 | 6 | 1 | 4 | 11 | 7 | 6 |

North and south count the side of the equator: from the file's own position where it has one, and from the place the uploader's page names for the 16 photos whose file has none. Capture time, UTC offset and place count the files that carry each in their own metadata.

Kinds: camera wide field 13; phone night sky 22; Moon over a horizon 8; Sun over a skyline 6; overcast or cloudy night 2; city street at night 1; fireworks 2; aircraft trails 2; dark interior 2; a screen 2. Skyline is meant widely, as the line where buildings, land or water meet the sky: one of the Sun photos has a water horizon.

## How the photos were chosen

- **Source.** Wikimedia Commons originals, never thumbnails, found through its public search API with words for the sky (constellations, the Milky Way, the Moon), for the scene (skyline, fireworks, overcast) and for phones, in several languages, and never with a place name. Requests carried the User-Agent `Albedian/0.1 (research prototype)`, no account, no key and nothing personal, one request every 6 s.
- **Licence.** CC0, public domain, CC BY or CC BY-SA in any version, recorded as the page states it. Anything else is refused, NonCommercial and NoDerivatives included. [tested] In this corpus: CC BY 2.0 5, CC BY 4.0 5, CC BY-SA 2.0 1, CC BY-SA 3.0 us 1, CC BY-SA 4.0 30, CC0 14, Public domain 4. The licence link is the one the page gives, written with https; a public-domain page gives none, so its link is the Commons page of the public-domain tag the file page carries.
- **Metadata.** Photos whose file carries a capture time, a UTC offset and a place were preferred, because the solver's hint path needs them. Tier A files must give their focal length and tier A and B files their camera. [tested]
- **Looked at first.** Each candidate was viewed before it was kept, as a reduced copy and, for the star fields, as a full-size crop. Left out: photos titled after stars that show none, pictures whose page says they are star trails, stacked, stitched or tracked, and photos where a person can be recognised. That a tier A photo is one untracked exposure rests on its page not saying otherwise and on its exposure time; it was not verified. [inference]
- **Spread.** Tier A and tier B hold photos from both sides of the equator; tier B holds iPhone, Pixel and Samsung photos and at least two other makers. [tested]
- **No dated names.** A file whose name carries a date or a time of day was left out, since its URL would put a capture time into the registry. [tested]
- **Wide lens.** A tier A lens is at most 35 mm long. [proposed: the usual upper edge of a wide lens on a 36 mm wide sensor; on a smaller sensor the same length sees less]

## The manifest

One entry per photo in `research/benchmarks/real_photos/corpus.json`:

- `id`, `downloadPath`: a neutral id and the registry path `.cache/real-photos/<id>/<id>.<extension>`.
- `tier`, `kind`, `mediaKind`: the tier above, the kind of scene within it, and image or video.
- `widthPx`, `heightPx`, `cameraMake`, `cameraModel`, `lensFocalLengthMm`, `focalLength35mmEquivMm`, `exposureTimeSec`: what the file's own metadata gives; a field the file does not give is absent, never guessed.
- `hasCaptureTime`, `hasUtcOffset`, `hasPlace`: whether the file carries each. The values stay in the file.
- `hemisphere`, `hemisphereSource`: north or south, from the file's position (`file_metadata`) or from the uploader's page (`uploader_statement`); absent when neither says.
- `statedSubject`, `statedSubjectSource`: the uploader's statement as tags (the 88 IAU constellation names, `milky_way`, `moon`, `sun`, `planet`, `horizon`, `clouds`, `city_lights`, `aircraft`, `no_sky`), and the page it was read from, as a page id link. `horizon` is used when the page names a horizon or a skyline, or the land, water or buildings the Moon or Sun stands over; `no_sky` when the page describes an indoor scene. A photo whose page names nothing from the vocabulary has no tag.

Where the photos are present, the tests read each file with the upload path's own readers and compare the result with the manifest: size, camera fields and the three booleans. [tested]

## Fetching

```
uv run python -m research.benchmarks.real_photos fetch
uv run python -m research.benchmarks.real_photos status
```

`fetch` gets every photo that is not yet in the shared `.cache` with its listed size and SHA-256, through `scripts/fetch_download.py`; a file is kept only once it matches its entry, and one that is already there and matches makes no request. `status` makes no request and prints how many photos are present and match their checksum. A single photo can also be fetched by its registry path with `scripts/fetch_download.py`.

`fetch` makes a request only for an entry whose URL is a Commons original on `https://upload.wikimedia.org`, the rule the tests hold `data/downloads.json` to; any other entry is refused before a request. It reads a URL the way the request itself will be read, by Python's `urllib.request` and `http.client`, and the URL must be plainly spelled: a user or password part, a port, another spelling of the host (a name, an escaped dot, upper case, an address in another notation), a space, a backslash, a control character or a fragment is refused. The one exception is plain http on `127.0.0.1` with a port, where the tests' own server listens. [tested: a table of such URLs and generated ones; a connection is opened only for a plainly spelled URL, and only to its own host and port]

Commons limits how fast originals may be fetched. Measured when this corpus was first fetched, with the project's User-Agent and no account: thirteen originals 6 s apart came through in two runs three minutes apart (five, then eight) and the fourteenth was answered 429 Too Many Requests with Retry-After 600; ten originals 30 s apart were followed by another 429; the 38 originals fetched 70 s apart after that came through without one. So `fetch` asks for the first eight photos of a run 6 s apart and for the rest 70 s apart. [tested: against a server on 127.0.0.1 inside the test] At this pace the whole corpus takes about an hour. [inference: the command fetched five photos from Commons, not yet all of them in one run; the limit is Commons' own and may change]

When the server still answers 429, `fetch` reads the whole seconds the answer asks for. Only a plain run of ASCII digits counts; no value, a date or anything else is taken as 600 s, the wait Commons asked for. It waits that long plus 30 s, never longer than 900 s, tries the photo again and stays at the slow pace, at most 6 times in one run. When the answer asks for more than 900 s, or when the server still answers 429 after those waits, `fetch` stops and says how many photos are still missing; a later run goes on from there. [tested: these rules and odd values of the header, with the same test server] [proposed: the 30 s, the 6 waits and the 900 s]

Of what a server answers, `fetch` prints only the HTTP status code; the wait it names is its own number of whole seconds. No header value, no reason phrase and no text of an error reaches its output: a failure without a status code is named in the command's own words or by the class of its Python error. [tested: the test server puts words of its own in a header, in the reason phrase, in a redirect target and in an answer that is not HTTP]

## Credits

Every photo was retrieved on 2026-11-11 from Wikimedia Commons. The author is given as the photo's page states it; the last column links that page, where the title, the full licence text and any further credit are. The file URL and the titled page URL of each photo are in `data/downloads.json`.

| Photo | Author | Licence | Commons page |
|---|---|---|---|
| `rp-a-01` | Mustang Joe | [CC0](https://creativecommons.org/publicdomain/zero/1.0/deed.en) | [195729529](https://commons.wikimedia.org/w/index.php?curid=195729529) |
| `rp-a-02` | Valerian Guillot | [CC BY 2.0](https://creativecommons.org/licenses/by/2.0) | [201508043](https://commons.wikimedia.org/w/index.php?curid=201508043) |
| `rp-a-03` | Jacob W. Frank, National Park Service | [Public domain](https://commons.wikimedia.org/wiki/Template:PD-USGov-NPS) | [110735599](https://commons.wikimedia.org/w/index.php?curid=110735599) |
| `rp-a-04` | ForestWander | [CC BY-SA 3.0 us](https://creativecommons.org/licenses/by-sa/3.0/us/deed.en) | [21353199](https://commons.wikimedia.org/w/index.php?curid=21353199) |
| `rp-a-05` | Вікторія Сергійчук | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [179396239](https://commons.wikimedia.org/w/index.php?curid=179396239) |
| `rp-a-06` | DavidGjorgiev | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0) | [140052320](https://commons.wikimedia.org/w/index.php?curid=140052320) |
| `rp-a-07` | Eric Kilby | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [175134222](https://commons.wikimedia.org/w/index.php?curid=175134222) |
| `rp-a-08` | Dave Young from New Zealand | [CC BY 2.0](https://creativecommons.org/licenses/by/2.0) | [97499525](https://commons.wikimedia.org/w/index.php?curid=97499525) |
| `rp-a-09` | cafuego | [CC BY-SA 2.0](https://creativecommons.org/licenses/by-sa/2.0) | [189727532](https://commons.wikimedia.org/w/index.php?curid=189727532) |
| `rp-a-10` | Danilo Curado | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [70399463](https://commons.wikimedia.org/w/index.php?curid=70399463) |
| `rp-a-11` | ELVIO GUSMÃO SANTOS | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [195747072](https://commons.wikimedia.org/w/index.php?curid=195747072) |
| `rp-a-12` | Sky_xe | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [64995715](https://commons.wikimedia.org/w/index.php?curid=64995715) |
| `rp-a-13` | Talesofaviewfinder | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [177431917](https://commons.wikimedia.org/w/index.php?curid=177431917) |
| `rp-b-01` | Chris Olszewski | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [181464149](https://commons.wikimedia.org/w/index.php?curid=181464149) |
| `rp-b-02` | Chris Olszewski | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [181464148](https://commons.wikimedia.org/w/index.php?curid=181464148) |
| `rp-b-03` | Kwozyn | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [128748523](https://commons.wikimedia.org/w/index.php?curid=128748523) |
| `rp-b-04` | Kgbo | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [147434840](https://commons.wikimedia.org/w/index.php?curid=147434840) |
| `rp-b-05` | Dicklyon | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [116993160](https://commons.wikimedia.org/w/index.php?curid=116993160) |
| `rp-b-06` | Wrenalicious | [CC0](https://creativecommons.org/publicdomain/zero/1.0/deed.en) | [179958408](https://commons.wikimedia.org/w/index.php?curid=179958408) |
| `rp-b-07` | Paocharbo | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [150313000](https://commons.wikimedia.org/w/index.php?curid=150313000) |
| `rp-b-08` | Butterflyyyyyyyy y y y | [CC0](https://creativecommons.org/publicdomain/zero/1.0/deed.en) | [164538127](https://commons.wikimedia.org/w/index.php?curid=164538127) |
| `rp-b-09` | Lchfabbri | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [196397091](https://commons.wikimedia.org/w/index.php?curid=196397091) |
| `rp-b-10` | radicalespresso from St Clair Shores, MI | [Public domain](https://commons.wikimedia.org/wiki/Template:PD-author-FlickrPDM) | [183046253](https://commons.wikimedia.org/w/index.php?curid=183046253) |
| `rp-b-11` | StarMatics | [CC0](https://creativecommons.org/publicdomain/zero/1.0/deed.en) | [201033986](https://commons.wikimedia.org/w/index.php?curid=201033986) |
| `rp-b-12` | YagnKambariya | [CC0](https://creativecommons.org/publicdomain/zero/1.0/deed.en) | [179998887](https://commons.wikimedia.org/w/index.php?curid=179998887) |
| `rp-b-13` | makelessnoise | [CC BY 2.0](https://creativecommons.org/licenses/by/2.0) | [159111271](https://commons.wikimedia.org/w/index.php?curid=159111271) |
| `rp-b-14` | Oleh325 | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [141094304](https://commons.wikimedia.org/w/index.php?curid=141094304) |
| `rp-b-15` | Aelson Apolinário | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [195814478](https://commons.wikimedia.org/w/index.php?curid=195814478) |
| `rp-b-16` | TBOE PC Ohm | [CC0](https://creativecommons.org/publicdomain/zero/1.0/deed.en) | [178297645](https://commons.wikimedia.org/w/index.php?curid=178297645) |
| `rp-b-17` | Aruncmohan1987 | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0) | [143159342](https://commons.wikimedia.org/w/index.php?curid=143159342) |
| `rp-b-18` | Kheon05 | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [89901663](https://commons.wikimedia.org/w/index.php?curid=89901663) |
| `rp-b-19` | Irnabela | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0) | [195963199](https://commons.wikimedia.org/w/index.php?curid=195963199) |
| `rp-b-20` | Kenrick95 | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [49948838](https://commons.wikimedia.org/w/index.php?curid=49948838) |
| `rp-b-21` | I Gede Krisna Dharmayudha | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [187080104](https://commons.wikimedia.org/w/index.php?curid=187080104) |
| `rp-b-22` | Gari González | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [93205228](https://commons.wikimedia.org/w/index.php?curid=93205228) |
| `rp-c-01` | Nbendersky | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [196586596](https://commons.wikimedia.org/w/index.php?curid=196586596) |
| `rp-c-02` | Tombersax | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [196466303](https://commons.wikimedia.org/w/index.php?curid=196466303) |
| `rp-c-03` | Forest Service Alaska Region, USDA | [Public domain](https://commons.wikimedia.org/wiki/Template:PD-USGov-USDA-FS) | [184549387](https://commons.wikimedia.org/w/index.php?curid=184549387) |
| `rp-c-04` | Drkirstyross | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [193977623](https://commons.wikimedia.org/w/index.php?curid=193977623) |
| `rp-c-05` | K.torri | [CC0](https://creativecommons.org/publicdomain/zero/1.0/deed.en) | [179722526](https://commons.wikimedia.org/w/index.php?curid=179722526) |
| `rp-c-06` | MimiAhid | [CC0](https://creativecommons.org/publicdomain/zero/1.0/deed.en) | [197355428](https://commons.wikimedia.org/w/index.php?curid=197355428) |
| `rp-c-07` | Lianguanlun | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0) | [178737051](https://commons.wikimedia.org/w/index.php?curid=178737051) |
| `rp-c-08` | AdamKelliher | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [202516487](https://commons.wikimedia.org/w/index.php?curid=202516487) |
| `rp-c-09` | PattayaPatrol | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [197295562](https://commons.wikimedia.org/w/index.php?curid=197295562) |
| `rp-c-10` | Paul Colin Hennig firstdorsal.eu | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [194757850](https://commons.wikimedia.org/w/index.php?curid=194757850) |
| `rp-c-11` | NPS Photo | [Public domain](https://commons.wikimedia.org/wiki/Template:PD-USGov-NPS) | [119009447](https://commons.wikimedia.org/w/index.php?curid=119009447) |
| `rp-c-12` | Eleneru | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [199180529](https://commons.wikimedia.org/w/index.php?curid=199180529) |
| `rp-c-13` | Muhammad Wajih Ud Din | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [72850904](https://commons.wikimedia.org/w/index.php?curid=72850904) |
| `rp-c-14` | Luciajimena1990 | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [76433005](https://commons.wikimedia.org/w/index.php?curid=76433005) |
| `rp-d-01` | Karlheinz Klingbeil | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0) | [176144900](https://commons.wikimedia.org/w/index.php?curid=176144900) |
| `rp-d-02` | PattayaPatrol | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [200537644](https://commons.wikimedia.org/w/index.php?curid=200537644) |
| `rp-d-03` | Z3lvs | [CC0](https://creativecommons.org/publicdomain/zero/1.0/deed.en) | [203049704](https://commons.wikimedia.org/w/index.php?curid=203049704) |
| `rp-d-04` | CTracy0803 | [CC0](https://creativecommons.org/publicdomain/zero/1.0/deed.en) | [163746066](https://commons.wikimedia.org/w/index.php?curid=163746066) |
| `rp-d-05` | PattayaPatrol | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | [202813951](https://commons.wikimedia.org/w/index.php?curid=202813951) |
| `rp-d-06` | tsuna72 from Fukuoka, Japan | [CC BY 2.0](https://creativecommons.org/licenses/by/2.0) | [66449856](https://commons.wikimedia.org/w/index.php?curid=66449856) |
| `rp-d-07` | Ine10 | [CC0](https://creativecommons.org/publicdomain/zero/1.0/deed.en) | [198026666](https://commons.wikimedia.org/w/index.php?curid=198026666) |
| `rp-d-08` | Mojmir Churavy | [CC0](https://creativecommons.org/publicdomain/zero/1.0/deed.en) | [197807086](https://commons.wikimedia.org/w/index.php?curid=197807086) |
| `rp-d-09` | Jay Wennington jaywennington | [CC0](https://creativecommons.org/publicdomain/zero/1.0/deed.en) | [61739035](https://commons.wikimedia.org/w/index.php?curid=61739035) |
| `rp-d-10` | Andrewthewikimedian | [CC0](https://creativecommons.org/publicdomain/zero/1.0/deed.en) | [175903823](https://commons.wikimedia.org/w/index.php?curid=175903823) |
| `rp-d-11` | Koshy Koshy from Faridabad, Haryana, India | [CC BY 2.0](https://creativecommons.org/licenses/by/2.0) | [26318614](https://commons.wikimedia.org/w/index.php?curid=26318614) |

## Limits

- No ground truth: which stars a photo shows, its scale and its pointing are unknown until a solution is verified. [open]
- The tags and the stated hemispheres are other people's statements, read once from each page; they were not checked. [open]
- The photos were edited by their authors to unknown degrees; many passed through an editor or an export that dropped the UTC offset or the position.
- A Commons file can be replaced or deleted by its uploader or by the community. `fetch` then fails for that photo, by checksum, and the entry must be retired; the corpus is a snapshot, not a mirror. [open]
- Tier D photos were chosen for showing no solvable sky; none was run through a solver to confirm it finds nothing there. [open]
- `fetch` checks the URL it asks for, not where the answer comes from. The shared `scripts/fetch_download.py` opens the URL with Python's `urllib.request`, whose redirect handler follows a redirect to any http, https or ftp address, on any host [verified: `HTTPRedirectHandler` in `urllib/request.py` of Python 3.14], and keeps the file only if it has the registry's size and SHA-256. Refusing such a redirect is a change to the shared script. [open]
