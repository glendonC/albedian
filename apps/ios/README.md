# Albedian Capture (iPhone companion)

The part of Albedian that happens outside: aim, take the photo exactly as the camera wrote it, record where the phone
pointed beside it, and send both to the laptop's service over the local network. The phone solves nothing and claims
nothing about the sky. Spec: `.kiro/specs/ios-capture-companion/` (requirements, design, tasks).

Unit mobile-0 holds the project, the tested core and one plain screen. Unit mobile-1 adds the sensors: Core Motion
and Core Location read into the core, tested on the Mac with stand-in sources. Unit mobile-2 adds the camera, keeps
each photo and its record on the phone until the laptop has them, and a check screen that shows what the sensors
report and takes a photo; the permission texts for the camera, motion and location are in `AlbedianCapture-Info.plist`.
Unit mobile-3 adds the approved iPhone controls and logo (`ControlStyle` in the core; `MainButton`, `IconButton`,
`EvidenceTag`, `Logo` and a debug-only `ControlsGallery` in `UI/`), embeds the pinned Geist v1.9.2 static TrueType
weights (`Geist-Regular.ttf`, `Geist-SemiBold.ttf`) and the four Phosphor 2.1.1 regular icons the approved screens use,
and keeps their licence texts and `PROVENANCE.json` records under `apps/ios/notices/` (with both licence texts also
bundled inside `AlbedianCapture.app`). Each full screen's layout still waits for its own unit.

## Layout

- `AlbedianCaptureCore/`: a Swift package with no interface code.
  - `PointingRecordBuilder` builds the pointing record from sensor readings with every rule of the contract and
    nothing guessed; it refuses, with a named reason, rather than round, clamp or fill in a value.
  - `PointingValidator` is a third reading of the contract (`packages/contracts/src/pointing.ts`,
    `services/contracts/pointing.py`), judged on the same fixture, `tests/fixtures/pointing_contract.json`.
  - `JSONText` reads and writes JSON the way `JSON.parse` and `JSON.stringify` do (strings as UTF-16 units, numbers as
    the nearest double formatted per ECMA-262 `Number::toString` and compared numerically), so a wire fault reads the
    same in all three languages.
  - `CaptureUploader` sends the photo's bytes as given to `POST /api/ingest` (unchanged) and the record to
    `PUT /api/ingest/{sha256}/pointing` [proposed in the design; unit pointing-2 builds the route]. It writes each
    request itself on a connection of its own to the paired address (`LaptopConnection.swift`), prohibiting `.cellular`
    and `.other` tunnel interfaces and keeping the connection alive in `.waiting` during iOS's Local Network permission
    alert; its states come from what that connection reports; it takes only a canonical local network address (including
    single-label `.local` mDNS names only).
  - `SolveClient` asks the laptop to solve a stored photo (`POST /api/ingest/{sha256}/solve`, which the laptop holds until
    the solve ends) and, beside it, reads the receipt for the job's id and the job itself, so each stage shows with the
    counts the job wrote; then the solution. `SolveJobReader` is a further reading of `SolveJobSchema` on
    `tests/fixtures/ingest_contract.json`; `SolutionReader` reads only the status, the findings counted by evidence
    class and the catalog matched marks (drawn only on a calibrated solution whose WCS passed held-out verification),
    on `tests/fixtures/observation_solution_contract.json`; `StoredFrame` places a detection on the photo as the web
    app does. The gate (`CaptureUploader.permits`) lets through the solve's four requests and no other.
    Receipts and jobs are read within 64 kB (`HTTPReplyLimits`); the solution alone within 64 MiB
    (`SolveClient.solutionMaxBytes`), since a photo with a time and place solves to about 1.3 MB and a 12 MP night photo
    to 35 MB; the connection reads a reply's bytes once, when its stated length has arrived (`HTTPMessage.wholeLength`).
  - `CaptureStore` keeps each photo as the bytes given, named by its SHA-256 and its type's extension, and its record
    beside it as the record's JSON bytes; a file counts as kept only once it has been read back and equals the bytes
    given, and a name holds no time, place or count. `CaptureUploader.send(photo:contentType:recordWire:)` sends a kept
    capture: the record goes out as the very bytes kept, after the same checks.
  - `PhotoClock` gives the device clock at the photo from one pair of readings of the boot clock and the device clock
    taken together after the shot.
  - `CheckReadout` writes the check screen's words for each reading (tested here): the heading accuracy named for the
    magnetic heading Apple states it for, Apple's words for a true heading that could not be determined, no place,
    and every value that is not a number written as such. `CheckCaptureState` runs the check screen's photo hashing,
    keeps and counts on the store's queue off the main actor.
  - `SampleCapture` holds synthetic readings (open ocean, the fixtures' synthetic moment) for tests and the screen.
  - `SensorRecorder` starts the two sensor sources, keeps their reports (device motion in a `MotionRing`, the
    newest headings within 60 s and fixes within 300 s of the photo on the device clock; since Core Location stamps
    fixes and headings with `Date`, a backward wall-clock step before the shutter leaves a report after the photo and
    records `no_fix` or `no_heading_event` rather than clamping) and at a photo hands the builder a `SensorCapture`.
    `SensorMapping` carries each report onto the core's types as the device gave it. The sources are `@MainActor`
    protocols (`SensorReadings.swift`), so the tests feed synthetic reports.
  - `ControlStyle` and `ShutterGeometry` hold the numbers of the approved iPhone controls (colours, 44 pt split main
    action and icon button geometry, 1.2 s spring and 0.15 s / 0.2 s standard curves, the website logo's proportions,
    and all four evidence classes' words, colours and tints), tested in `ControlStyleTests` and `ShutterGeometryTests`.
- The approved screens (unit mobile-6, `AlbedianCapture/Screens/`): the app opens on the home (UIUX-arrive-READY
  iPhone 1 with UIUX-credit), then Credits, aiming (no overlay until mobile-5), after the shot, "Connect to your
  laptop" (UIUX-connect: typed once, kept by `PairingKeeper` after a send goes through), "Can't reach the laptop"
  (UIUX-unreachable, chosen by `SendEnd`) and the result (status word, card with the catalog matched marks placed by
  `StoredFrame.placeCovering`, counts by evidence class). `CompanionModel` runs them on the camera, the store, the
  uploader and the solve. The home's Milky Way is a crop of the web's eso0932a panorama (`notices/eso`), the light
  weight is the web's own Geist variable font, and the three new icons come from the pinned Phosphor package.
- Night exposure (unit mobile-7): the capture button takes one photo at the format's longest exposure with the meter's
  light carried to it (`NightExposure.plan`, `CameraSession.shootLong`), shown on the approved hold-still screen
  (`Screens/HoldStillScreen.swift`): the length, the ring filling over the real exposure, the gyroscope's crosshair.
- `AlbedianCapture/`: the app: two plain screens in the system's default style (the pipeline run by hand with sample
  values, cancelling any in-flight upload when rebuilt, and `CaptureCheckScreen`), plus in `DEBUG` builds
  `ControlsGallery` showing the approved controls together. `UI/` holds `MainButton`, `IconButton`, `EvidenceTag`,
  `Logo`, `CaptureButton` and `Style`. `Fonts/` embeds `Geist-Regular.ttf` and `Geist-SemiBold.ttf` (with `OFL.txt`),
  `Assets.xcassets/` holds the four Phosphor 2.1.1 regular icons (`arrow-right`, `x`, `paper-plane-tilt`,
  `arrow-counter-clockwise`), and `Notices/` bundles `Phosphor-LICENSE.txt`. `Sensors/`: `@MainActor` `CoreMotionSource`
  (`CMMotionManager`, 100 Hz, true north when available and falling back to `.xArbitraryZVertical` on
  `CMErrorTrueNorthNotAvailable` when Location Services are off or denied), `@MainActor` `CoreLocationSource`
  (`CLLocationManager`, when in use only, best accuracy) and `CameraSession` (the back wide-angle camera, HEIC else JPEG,
  the bytes of `fileDataRepresentation()` as they are, content-aware distortion correction off). All three run only on an
  iPhone: the simulator has no motion sensors and no camera, and the check screen says so.
- `notices/`: `geist/` (`OFL.txt`, `PROVENANCE.json`) and `phosphor/` (`LICENSE`, `PROVENANCE.json`, and the four
  icon `.svg` files byte for byte from `@phosphor-icons/core` 2.1.1), verified in `ApprovedAssetsTests`.
- `AlbedianCapture.xcodeproj`: the iPhone app target, using the package.

## Run the tests

From the repository root, on this Mac (Xcode 26.6, its Swift 6.3 toolchain); nothing is downloaded and the only
network use is a server the tests start and stop on 127.0.0.1:

```bash
swift test --package-path apps/ios/AlbedianCaptureCore -j 2
```

The tests run on macOS, not in the simulator: the core uses only Foundation, CryptoKit and Network (in the tests),
which behave the same there. The property tests draw from a seeded generator (SplitMix64). Set the seed with
`ALBEDIAN_SWIFT_SEED`; unset, a seed is drawn and printed, so a failure can be run again:

```bash
ALBEDIAN_SWIFT_SEED=7 swift test --package-path apps/ios/AlbedianCaptureCore -j 2
```

To look for a test that needs another to run first, or leaves state behind, set `ALBEDIAN_SWIFT_ORDER_SEED`: the
suite `AAShuffledOrder` then runs every test once, in an order shuffled by that seed, before the classes run in
XCTest's own order (`xctest` runs a given list of tests sorted, so the order is set by that suite):

```bash
ALBEDIAN_SWIFT_ORDER_SEED=3 swift test --package-path apps/ios/AlbedianCaptureCore -j 2
```

## Build and run the app in the simulator

```bash
xcodebuild -project apps/ios/AlbedianCapture.xcodeproj -scheme AlbedianCapture -destination 'generic/platform=iOS Simulator' -jobs 2 build
```

Then open the built `AlbedianCapture.app` in an iPhone simulator. "Build record" builds a record from the sample
values; "Send" takes a laptop address such as `http://127.0.0.1:8000` (the API in the simulator) or the `--lan`
address `pnpm field --lan` prints. The sample photo is a black image the app draws, not a photo.

## Choices

- iOS 26.0: the newest interface the design names is `AVCaptureDeviceFormat.videoFieldOfView(for:geometricDistortionCorrected:)`,
  the field of view per aspect ratio, which the iOS 26.5 SDK header marks `API_AVAILABLE(ios(26.0))` [verified in
  `AVCaptureDevice.h`]. The others the design names are older, `synchronizationClock` from iOS 15.4 among them
  [verified in `AVCaptureSession.h`; the rest as the design read them at Apple's documentation].
- The field of view: mobile-2 reads the active format's `videoFieldOfView` and
  `geometricDistortionCorrectedVideoFieldOfView` with the session's photo preset; the iOS 26 call per aspect ratio is
  not called yet [open]. That the preset's format has the photo's aspect ratio, so the two agree, is [inference],
  on the owner's checklist.
- Bundle identifier `com.example.albedian.capture` [proposed]: a placeholder under the domain reserved for examples
  (RFC 2606), so it names no one. The owner picks the real one before a device build.
- No signing team and no Apple account: builds are for the simulator only. Running on an iPhone needs the owner to
  choose a team in Xcode and trust the device; that is listed in each later unit that needs a device.
- The app sends no file name with the photo (a name can hold a capture time): the request's head is written line by
  line in `HTTPMessage.request` (request line, Host, User-Agent, Content-Type, Content-Length, Connection), and
  `UploadClientTests` holds that the laptop receives no file name header. It reads no name a person gave the device,
  keeps no cookie, cache or credential, and writes nothing to a log: `PrivacyScanTests` checks the code for these by
  its list of names (`privacy`), with that list's limit (a way not listed is not seen).
- The code makes one connection, to the paired address: there is no general HTTP stack. One TCP connection per request
  goes to the endpoint `CaptureUploader.permits` approved; once it is made, the peer it actually reached
  (`currentPath.remoteEndpoint`, never the endpoint asked for) is checked before a byte is sent; the client's own reader
  takes one strict reply (interim replies read past and never acted on, a 3xx a failure the person is told), within
  60 s from connecting to the whole reply (`CaptureUploader.defaultTimeoutSec` [proposed]; whether the person sees
  that limit on a screen is the owner's). Nothing in it follows a redirect, resumes an upload elsewhere, uses an
  alternative service, a cookie or a credential. `RequestGateTests` runs a hostile laptop on 127.0.0.1 through a table
  of steering replies while a second server counts what reaches it: nothing. The checks below keep that from
  drifting, each with its stated limit; on a real phone, and behind a proxy, it is not yet verified.
- What the peer check promises depends on what was paired. Paired by an address (what `pnpm field --lan` prints: a
  private IPv4 address, `scripts/field.mjs`), the peer must be that very address at the paired port. Paired by a name
  (`localhost`, a `.local` name, taken only when typed), it must be some address on the local network at that port:
  a name promises no more, since it can resolve to any machine there, or to another listener on the laptop itself.
  An IPv6 link-local address must carry its zone (`[fe80::1%25en0]`); the zone is the phone's own interface and is
  never sent (the Host line leaves it out, RFC 9844 section 6).
- Proxies: no proxy is preferred (`preferNoProxies`, a preference only), and what happens when the system routes the
  connection through one is not yet verified [open: the owner's check on a phone]. The peer check is not claimed to
  stop a proxy: if the system reported the laptop as the peer of a proxied connection, the check would pass.
- What a person does on purpose is not a way out: the record's text on the pipeline screen can be selected and
  copied by hand, as any text on a screen can.

## Checks on what else could send

Two checks keep the one connection from drifting, each with its stated limit.

- The source scan (`PrivacyScanTests`, run with the tests above) reads every regular file in the app's folder and the
  core's sources with comments and literals taken out by reading the syntax; a file of a kind it cannot read passes
  only when `PrivacyScanTests.assetFiles` lists both its path under a built folder and its SHA-256, and anything there
  that is neither a regular file nor a folder (a link) is a finding.
  Its first rule is a list of the forms that make an address, each refused outside the transport's own core paths
  (`AlbedianCaptureCore/Sources/AlbedianCaptureCore/LaptopConnection.swift` and `UploadClient.swift`, never by basename
  alone): `URL(...)` with any first label but the two that take a file's path,
  URL components and the Objective-C URL, the initialisers that read an address (`.init(string:)` and the like), the
  `#URL` macro, a decodable type beside an address, and a literal that holds a scheme. Named calls are a second list
  (with the standard types' readers of an address, allowed only where the scan's list of exceptions gives the file,
  the line, the code on it and the reason). Their limit: an address made, or read, by a call the lists do not hold is
  not seen by the scan. What stands behind them is the check of what was built, below, and in the end the owner's
  check on a real phone and behind a proxy. The scan cannot see a name put together from pieces, or what comes from
  outside these files.
- The check of what was built reads the compiled app and core: the system symbols and classes they import, demangled,
  and their strings. It refuses each named family, a run-time lookup function, an address made from text or an
  address's contents read in any object but the core transport's (`AlbedianCaptureCore.build/{LaptopConnection,UploadClient}.o`,
  or a listed exception, and refused in any linked `.app` when no single-file objects were given to account for them),
  and the connection's creating call in any object but `AlbedianCaptureCore.build/LaptopConnection.o`. From the app itself it reads four closed lists, each allowing what is
  listed and refusing the rest: the system frameworks and libraries its executables link (those the companion's design
  names), the libraries it embeds and links by `@rpath/` or `@executable_path/` (none today: the core is linked into the
  executable), the entitlements it carries (the app's identifier, its team and `get-task-allow`: no iCloud container,
  shared group, keychain sharing or push), and its Info.plist's keys (no address scheme, background mode or document
  type, `UIAppFonts` only as the two approved Geist files, and no transport exception beyond local networking). It cannot see what Apple's frameworks do inside. Build
  without Xcode's debug launcher, then check:

```bash
xcodebuild -project apps/ios/AlbedianCapture.xcodeproj -scheme AlbedianCapture -destination 'generic/platform=iOS Simulator' -derivedDataPath /tmp/albedian-dd -jobs 2 ENABLE_DEBUG_DYLIB=NO build
```

```bash
zsh apps/ios/scripts/check-built.zsh /tmp/albedian-dd/Build/Products/Debug-iphonesimulator/AlbedianCapture.app /tmp/albedian-dd/Build/Intermediates.noindex
```

  Its own test compiles planted cases for the simulator and sees each refused: one call from each of nineteen of the
  named families, the last reader's eight address reads, a class named in text and one in Objective-C, the connection
  created outside its file and from two files, and an app off each closed list (two for the embedded one); and sees
  what the app does use pass, among it a listed embedded library:

```bash
zsh apps/ios/scripts/check-built-test.zsh
```

## CI (not added)

Running these tests in CI needs a macOS runner with Xcode 26.x: the core uses CryptoKit, and the tests Network,
which are Apple's frameworks [inference: neither ships with Swift on Linux]. It would be one job: check out,
`swift test --package-path apps/ios/AlbedianCaptureCore`, and optionally the `xcodebuild` line above. A macOS runner
costs more per minute than a Linux one [inference from GitHub's published rates, not read for this unit]; whether
that is worth it is the lead's decision [open].
