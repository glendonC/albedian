# Albedian Capture (iPhone companion)

The part of Albedian that happens outside: aim, take the photo exactly as the camera wrote it, record where the phone
pointed beside it, and send both to the laptop's service over the local network. The phone solves nothing and claims
nothing about the sky. Spec: `.kiro/specs/ios-capture-companion/` (requirements, design, tasks).

Unit mobile-0 holds the project, the tested core and one plain screen. Unit mobile-1 adds the sensors: Core Motion
and Core Location read into the core, tested on the Mac with stand-in sources; no screen shows them yet. The
camera, a check screen and the permission texts come with mobile-2. Nothing is styled: the look waits for the
reference board and the owner's yes.

## Layout

- `AlbedianCaptureCore/`: a Swift package with no interface code.
  - `PointingRecordBuilder` builds the pointing record from sensor readings with every rule of the contract and
    nothing guessed; it refuses, with a named reason, rather than round, clamp or fill in a value.
  - `PointingValidator` is a third reading of the contract (`packages/contracts/src/pointing.ts`,
    `services/contracts/pointing.py`), judged on the same fixture, `tests/fixtures/pointing_contract.json`.
  - `JSONText` reads and writes JSON the way `JSON.parse` does (strings as UTF-16 units, numbers as the nearest
    double), so a wire fault reads the same in all three languages.
  - `CaptureUploader` sends the photo's bytes as given to `POST /api/ingest` (unchanged) and the record to
    `PUT /api/ingest/{sha256}/pointing` [proposed in the design; unit pointing-2 builds the route]. It writes each
    request itself on a connection of its own to the paired address (`LaptopConnection.swift`); its states come from
    what that connection reports; it takes only a local network address.
  - `SampleCapture` holds synthetic readings (open ocean, the fixtures' synthetic moment) for tests and the screen.
  - `SensorRecorder` starts the two sensor sources, keeps their reports (device motion in a `MotionRing`, the
    newest headings and fixes) and at a photo hands the builder a `SensorCapture`: the window's samples, and the
    newest heading and fix not after the photo. `SensorMapping` carries each report onto the core's types as the
    device gave it. The sources are protocols (`SensorReadings.swift`), so the tests feed synthetic reports.
- `AlbedianCapture/`: the app, one screen in the system's default style, to run the pipeline by hand, and
  `Sensors/`: `CoreMotionSource` (`CMMotionManager`, 100 Hz, true north when the device has it) and
  `CoreLocationSource` (`CLLocationManager`, when in use only, best accuracy). Both run only on an iPhone: the
  simulator has no motion sensors.
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
  core's sources with comments and literals taken out by reading the syntax; a file of a kind it cannot read, and
  anything there that is neither a regular file nor a folder (a link), is a finding.
  Its first rule is a list of the forms that make an address, each refused outside the transport's own files
  (`LaptopConnection.swift`, `UploadClient.swift`): `URL(...)` with any first label but the two that take a file's path,
  URL components and the Objective-C URL, the initialisers that read an address (`.init(string:)` and the like), the
  `#URL` macro, a decodable type beside an address, and a literal that holds a scheme. Named calls are a second list
  (with the standard types' readers of an address, allowed only where the scan's list of exceptions gives the file,
  the line, the code on it and the reason). Their limit: an address made, or read, by a call the lists do not hold is
  not seen by the scan. What stands behind them is the check of what was built, below, and in the end the owner's
  check on a real phone and behind a proxy. The scan cannot see a name put together from pieces, or what comes from
  outside these files.
- The check of what was built reads the compiled app and core: the system symbols and classes they import, demangled,
  and their strings. It refuses each named family, a run-time lookup function, an address made from text or an
  address's contents read in any object but the transport's (or a listed exception), and the connection's creating
  call in any object but `LaptopConnection`'s. From the app itself it reads four closed lists, each allowing what is
  listed and refusing the rest: the system frameworks and libraries its executables link (those the companion's design
  names), the libraries it embeds and links by `@rpath/` or `@executable_path/` (none today: the core is linked into the
  executable), the entitlements it carries (the app's identifier, its team and `get-task-allow`: no iCloud container,
  shared group, keychain sharing or push), and its Info.plist's keys (no address scheme, background mode or document
  type, and no transport exception beyond local networking). It cannot see what Apple's frameworks do inside. Build
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
