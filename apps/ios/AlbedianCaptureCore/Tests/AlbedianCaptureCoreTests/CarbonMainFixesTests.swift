import Foundation
import Network
import XCTest
@testable import AlbedianCaptureCore

/// Follow-up fixes on `main` from Carbon's review (`mobile-fix-1`):
/// 1. Cellular and non-LAN tunnel prohibition on `NWParameters.tcp`, connected-path local interface check, single-label
///    `.local` and canonical pairing syntax, and keeping `NWConnection` alive in `.waiting` during the Local Network
///    permission prompt (`Exchange` strongly retained until `finish`).
/// 2. `CaptureUploader.init(timeoutSec:)` validation, task cancellation on `NetworkLaptopTransport.exchange` and
///    `UploadCoordinator.reset()`, `check-built.zsh` `.app`-only symbol checks and path-scoped transport exemptions, and
///    `JSONNumber` ECMA-262 `JSON.stringify` formatting and numeric equality.
/// 3. Fallback from `.xTrueNorthZVertical` to `.xArbitraryZVertical` when Location Services are off or denied, live
///    heading-orientation updates without clearing `MotionRing`, `@MainActor` source isolation, and fix/heading age bounds.
final class CarbonMainFixesTests: XCTestCase {
    final class StandInDriver: ConnectionDriver, @unchecked Sendable {
        let lock = NSLock()
        var remoteEndpoint: NWEndpoint?
        var usesCellular = false
        var usesOther = false
        var stateHandler: (@Sendable (NWConnection.State) -> Void)?
        var started = false
        var cancelled = false
        var sentChunks: [Data] = []
        var replyBytes = Data()
        var holdReceive = false

        init(remoteEndpoint: NWEndpoint?, usesCellular: Bool = false, usesOther: Bool = false, replyBytes: Data = Data()) {
            self.remoteEndpoint = remoteEndpoint
            self.usesCellular = usesCellular
            self.usesOther = usesOther
            self.replyBytes = replyBytes
        }

        func setStateHandler(_ handler: (@Sendable (NWConnection.State) -> Void)?) {
            lock.withLock { stateHandler = handler }
        }

        func start(queue: DispatchQueue) {
            lock.withLock { started = true }
        }

        func emit(_ state: NWConnection.State) {
            let h = lock.withLock { stateHandler }
            h?(state)
        }

        func writeChunk(_ chunk: Data, completion: @escaping @Sendable (NWError?) -> Void) {
            lock.withLock { sentChunks.append(chunk) }
            completion(nil)
        }

        func readChunk(maxBytes: Int, completion: @escaping @Sendable (Data?, Bool, NWError?) -> Void) {
            let (hold, bytes) = lock.withLock {
                let b = replyBytes
                replyBytes = Data()
                return (holdReceive, b)
            }
            if hold { return }
            if !bytes.isEmpty {
                completion(bytes, true, nil)
            }
        }

        func cancel() {
            lock.withLock { cancelled = true }
        }

        var totalSentBytes: Int { lock.withLock { sentChunks.reduce(0) { $0 + $1.count } } }
    }

    @MainActor
    final class FallibleMotion: DeviceMotionSource {
        var availableFrames: Set<AttitudeFrame>
        private(set) var started: [(AttitudeFrame, Double)] = []
        private(set) var stops = 0
        var deliver: (@Sendable (DeviceMotionEvent) -> Void)?

        init(_ frames: Set<AttitudeFrame>) { availableFrames = frames }

        func start(frame: AttitudeFrame, intervalSec: Double, deliver: @escaping @Sendable (DeviceMotionEvent) -> Void) {
            started.append((frame, intervalSec))
            self.deliver = deliver
        }

        func stop() { stops += 1 }
    }

    @MainActor
    final class TrackingPlace: PlaceAndHeadingSource {
        private(set) var startedOrientations: [HeadingOrientation] = []
        private(set) var updatedOrientations: [HeadingOrientation] = []
        private(set) var stops = 0
        var deliver: (@Sendable (PlaceEvent) -> Void)?

        func start(headingOrientation: HeadingOrientation, deliver: @escaping @Sendable (PlaceEvent) -> Void) {
            startedOrientations.append(headingOrientation)
            self.deliver = deliver
        }

        func updateHeadingOrientation(_ headingOrientation: HeadingOrientation) {
            updatedOrientations.append(headingOrientation)
        }

        func stop() { stops += 1 }
    }

    private static func okReply(_ body: String = "{}") -> Data {
        Data("HTTP/1.1 201 Created\r\nContent-Length: \(body.utf8.count)\r\n\r\n\(body)".utf8)
    }

    func testProhibitsCellularAndTunnelInterfacesAndChecksTheConnectedPathBeforeSending() async throws {
        let params = NetworkLaptopTransport.tcpParameters()
        XCTAssertTrue(params.preferNoProxies)
        XCTAssertEqual(Set(params.prohibitedInterfaceTypes ?? []), [.cellular, .other])

        XCTAssertTrue(NetworkLaptopTransport.allowsLocalInterface(usesCellular: false, usesOther: false))
        XCTAssertFalse(NetworkLaptopTransport.allowsLocalInterface(usesCellular: true, usesOther: false))
        XCTAssertFalse(NetworkLaptopTransport.allowsLocalInterface(usesCellular: false, usesOther: true))
        XCTAssertFalse(NetworkLaptopTransport.allowsLocalInterface(usesCellular: true, usesOther: true))

        let paired = LaptopEndpoint(host: "10.0.0.2", port: 5173)
        let remote = NWEndpoint.hostPort(host: "10.0.0.2", port: 5173)
        let req = Data("POST /api/ingest HTTP/1.1\r\nContent-Length: 4\r\n\r\ntest".utf8)

        // Even when remoteEndpoint matches 10.0.0.2:5173 byte for byte, cellular and .other tunnels are refused before a byte is sent
        for (cellular, other) in [(true, false), (false, true)] {
            let driver = StandInDriver(remoteEndpoint: remote, usesCellular: cellular, usesOther: other, replyBytes: Self.okReply())
            let exchange = Exchange(driver: driver, request: req, endpoint: paired,
                                    peerCheck: { NetworkLaptopTransport.isPairedPeer($0, paired: $1) }, onSent: { _ in })
            let outcome = await within(5, otherwise: Result<HTTPReply, ExchangeFailure>.failure(.timedOut)) {
                await withCheckedContinuation { continuation in
                    exchange.run(timeoutSec: 5) { continuation.resume(returning: $0) }
                    driver.emit(.ready)
                }
            }
            XCTAssertEqual(outcome, .failure(.notThePairedPeer))
            XCTAssertEqual(driver.totalSentBytes, 0)
            XCTAssertTrue(driver.lock.withLock { driver.cancelled })
        }

        // Local interface (.wifi / .wiredEthernet / .loopback) with matching peer transmits and succeeds
        let cleanDriver = StandInDriver(remoteEndpoint: remote, usesCellular: false, usesOther: false, replyBytes: Self.okReply())
        let cleanExchange = Exchange(driver: cleanDriver, request: req, endpoint: paired,
                                     peerCheck: { NetworkLaptopTransport.isPairedPeer($0, paired: $1) }, onSent: { _ in })
        let cleanOutcome = await within(5, otherwise: Result<HTTPReply, ExchangeFailure>.failure(.timedOut)) {
            await withCheckedContinuation { continuation in
                cleanExchange.run(timeoutSec: 5) { continuation.resume(returning: $0) }
                cleanDriver.emit(.ready)
            }
        }
        XCTAssertEqual(cleanOutcome, .success(HTTPReply(status: 201, headers: [(name: "content-length", value: "2")], body: Data("{}".utf8))))
        XCTAssertEqual(cleanDriver.totalSentBytes, req.count)
    }

    func testTakesOnlyASingleLabelLocalNameWithLettersDigitsAndInteriorHyphensAndCanonicalSyntax() {
        let maxLabel = String(repeating: "a", count: 63) + ".local"
        let tooLongLabel = String(repeating: "a", count: 64) + ".local"
        for good in ["a.local", "z.local", "0.local", "9.local", "a-b.local", "a--b.local", "z9-0a.local", "laptop.local", maxLabel] {
            XCTAssertTrue(LaptopAddress.isLocal(good), good)
            XCTAssertNotNil(LaptopAddress("http://\(good):5173"), good)
        }
        for bad in ["a.b.local", "x.y.z.local", "-.local", "--.local", "-a.local", "a-.local", "-a-.local", tooLongLabel] {
            XCTAssertFalse(LaptopAddress.isLocal(bad), bad)
            XCTAssertNil(LaptopAddress("http://\(bad):5173"), bad)
        }
        // Canonical address form: no percent-encoded host characters other than %25 in IPv6 zone, no empty port colon, and no leading-zero port
        for nonCanonical in ["http://%6cocalhost:8000", "http://127.0.0.%31:8000", "http://laptop%2elocal:5173",
                             "http://127.0.0.1:", "http://[::1]:", "http://localhost:", "http://192.168.1.20:/",
                             "http://127.0.0.1:080", "http://[::1]:08000"] {
            XCTAssertNil(LaptopAddress(nonCanonical), nonCanonical)
        }
    }

    func testKeepsTheConnectionWaitingThroughLocalNetworkPermissionAndRetainsExchangeUntilFinished() async {
        // Local Network prompt / Wi-Fi bring-up / Bonjour states stay waiting; active TCP peer rejection fails at once
        for transient: NWError in [.posix(.EHOSTDOWN), .posix(.ENETDOWN), .posix(.EHOSTUNREACH), .posix(.ENETUNREACH), .dns(-65570)] {
            XCTAssertTrue(NetworkLaptopTransport.shouldKeepWaiting(transient), "\(transient)")
        }
        for terminal: NWError in [.posix(.ECONNREFUSED), .posix(.ECONNRESET), .posix(.ECONNABORTED)] {
            XCTAssertFalse(NetworkLaptopTransport.shouldKeepWaiting(terminal), "\(terminal)")
        }

        let paired = LaptopEndpoint(host: "192.168.1.20", port: 5173)
        let remote = NWEndpoint.hostPort(host: "192.168.1.20", port: 5173)
        let req = Data("POST /api/ingest HTTP/1.1\r\nContent-Length: 2\r\n\r\nhi".utf8)
        let driver = StandInDriver(remoteEndpoint: remote, replyBytes: Self.okReply())

        final class WeakExchangeBox: @unchecked Sendable {
            weak var value: Exchange?
        }
        let weakBox = WeakExchangeBox()
        let outcome = await within(5, otherwise: Result<HTTPReply, ExchangeFailure>.failure(.timedOut)) {
            await withCheckedContinuation { continuation in
                do {
                    let exchange = Exchange(driver: driver, request: req, endpoint: paired,
                                            peerCheck: { NetworkLaptopTransport.isPairedPeer($0, paired: $1) }, onSent: { _ in })
                    weakBox.value = exchange
                    exchange.run(timeoutSec: 5) { continuation.resume(returning: $0) }
                }
                // Local scope exited: Exchange must still retain itself while waiting for permission and transition to .ready
                XCTAssertNotNil(weakBox.value, "Exchange deallocated before finish while waiting")
                driver.emit(.waiting(.posix(.EHOSTDOWN)))
                driver.emit(.waiting(.dns(-65570)))
                XCTAssertFalse(driver.lock.withLock { driver.cancelled }, "Exchange cancelled NWConnection during .waiting")
                driver.emit(.ready)
            }
        }
        XCTAssertEqual(outcome, .success(HTTPReply(status: 201, headers: [(name: "content-length", value: "2")], body: Data("{}".utf8))))
        XCTAssertNil(weakBox.value, "Exchange leaked self after finish")

        // Active TCP rejection in .waiting (.ECONNREFUSED, .ECONNRESET, .ECONNABORTED) and .failed(.dns) finish immediately
        for code: POSIXErrorCode in [.ECONNREFUSED, .ECONNRESET, .ECONNABORTED] {
            let refusedDriver = StandInDriver(remoteEndpoint: remote)
            let refusedExchange = Exchange(driver: refusedDriver, request: req, endpoint: paired,
                                           peerCheck: { NetworkLaptopTransport.isPairedPeer($0, paired: $1) }, onSent: { _ in })
            let refusedOutcome = await within(5, otherwise: Result<HTTPReply, ExchangeFailure>.failure(.timedOut)) {
                await withCheckedContinuation { continuation in
                    refusedExchange.run(timeoutSec: 5) { continuation.resume(returning: $0) }
                    refusedDriver.emit(.waiting(.posix(code)))
                }
            }
            XCTAssertEqual(refusedOutcome, .failure(.network(posixCode: code.rawValue)))
            XCTAssertTrue(refusedDriver.lock.withLock { refusedDriver.cancelled })
        }
        let dnsFailedDriver = StandInDriver(remoteEndpoint: remote)
        let dnsFailedExchange = Exchange(driver: dnsFailedDriver, request: req, endpoint: paired,
                                         peerCheck: { NetworkLaptopTransport.isPairedPeer($0, paired: $1) }, onSent: { _ in })
        let dnsFailedOutcome = await within(5, otherwise: Result<HTTPReply, ExchangeFailure>.failure(.timedOut)) {
            await withCheckedContinuation { continuation in
                dnsFailedExchange.run(timeoutSec: 5) { continuation.resume(returning: $0) }
                dnsFailedDriver.emit(.failed(.dns(-65554)))
            }
        }
        XCTAssertEqual(dnsFailedOutcome, .failure(.network(posixCode: nil)))

        let posixFailedDriver = StandInDriver(remoteEndpoint: remote)
        let posixFailedExchange = Exchange(driver: posixFailedDriver, request: req, endpoint: paired,
                                           peerCheck: { NetworkLaptopTransport.isPairedPeer($0, paired: $1) }, onSent: { _ in })
        let posixFailedOutcome = await within(5, otherwise: Result<HTTPReply, ExchangeFailure>.failure(.timedOut)) {
            await withCheckedContinuation { continuation in
                posixFailedExchange.run(timeoutSec: 5) { continuation.resume(returning: $0) }
                posixFailedDriver.emit(.failed(.posix(.ETIMEDOUT)))
            }
        }
        XCTAssertEqual(posixFailedOutcome, .failure(.network(posixCode: POSIXErrorCode.ETIMEDOUT.rawValue)))
    }

    @MainActor
    func testValidatesUploaderTimeoutAndCancelsInFlightExchangeAndStalePipelineUploads() async throws {
        XCTAssertEqual(CaptureUploader.maxTimeoutSec, 3600)
        for valid in [Double.leastNonzeroMagnitude, 0.5, 60, 3600] {
            XCTAssertTrue(CaptureUploader.isValidTimeout(valid), "\(valid)")
        }
        for invalid in [0.0, -0.0, -Double.leastNonzeroMagnitude, -1, 3600.000001, 3601, 1e20, .infinity, -.infinity, .nan] {
            XCTAssertFalse(CaptureUploader.isValidTimeout(invalid), "\(invalid)")
        }

        let holdServer = try RequestGateTests.RawServer { _ in
            RequestGateTests.RawServer.Script(dropAfterBodyBytes: nil, reply: { _, _ in nil }, holdOpen: true)
        }
        defer { holdServer.stop() }
        let address = try XCTUnwrap(LaptopAddress("http://127.0.0.1:\(holdServer.port)"))
        let photo = Data([0x01, 0x02, 0x03])

        for badTimeout in [Double.nan, .infinity, -.infinity, 0, -5, 3601, 1e20] {
            let uploader = CaptureUploader(address: address, timeoutSec: badTimeout)
            XCTAssertEqual(uploader.timeoutSec, 0, "\(badTimeout)")
            let state = await bounded(3) { await uploader.send(photo: photo, contentType: .jpeg, record: nil) { _ in } }
            XCTAssertEqual(state, .failed(.timedOut(phase: .photo)), "\(badTimeout)")
            let customTransportReply = HTTPReply(status: 201, headers: [], body: Data("{\"sourceAsset\":{\"sha256\":\"\(PhotoHash.sha256Hex(photo))\"},\"alreadyStored\":false}".utf8))
            let customUploader = CaptureUploader(address: address, timeoutSec: badTimeout,
                                                 transport: UploadClientTests.CountingTransport(counts: [-1], reply: customTransportReply))
            let customState = await bounded(3) { await customUploader.send(photo: photo, contentType: .jpeg, record: nil) { _ in } }
            XCTAssertEqual(customState, .failed(.timedOut(phase: .photo)), "custom transport \(badTimeout)")
        }
        XCTAssertEqual(holdServer.connections, 0)

        // Direct Exchange.run timeout guard
        for badTimeout in [Double.nan, .infinity, 0, -1, 3601] {
            guard !CaptureUploader.isValidTimeout(badTimeout) else { continue }
            let driver = StandInDriver(remoteEndpoint: nil)
            let ex = Exchange(driver: driver, request: photo, endpoint: address.endpoint, peerCheck: { _, _ in true }, onSent: { _ in })
            let res = await within(2, otherwise: Result<HTTPReply, ExchangeFailure>.failure(.closed)) {
                await withCheckedContinuation { c in ex.run(timeoutSec: badTimeout) { c.resume(returning: $0) } }
            }
            XCTAssertEqual(res, .failure(.timedOut))
            XCTAssertFalse(driver.lock.withLock { driver.started })
        }

        // Swift Task cancellation cancels NetworkLaptopTransport.exchange promptly without waiting 60 s (both mid-flight and pre-cancelled)
        let req = HTTPMessage.request(method: "POST", path: "/api/ingest", endpoint: address.endpoint, contentType: "image/jpeg", body: photo)
        let cancelTask = Task.detached {
            await NetworkLaptopTransport().exchange(req, to: address.endpoint, timeoutSec: 60) { _ in }
        }
        try await Task.sleep(nanoseconds: 60_000_000)
        cancelTask.cancel()
        let cancelledResult = await within(3, otherwise: Result<HTTPReply, ExchangeFailure>.failure(.timedOut)) {
            await cancelTask.value
        }
        XCTAssertEqual(cancelledResult, .failure(.closed))

        let preCancelledTask = Task.detached { () -> Result<HTTPReply, ExchangeFailure> in
            withUnsafeCurrentTask { $0?.cancel() }
            return await NetworkLaptopTransport().exchange(req, to: address.endpoint, timeoutSec: 60) { _ in }
        }
        let preCancelledResult = await within(2, otherwise: Result<HTTPReply, ExchangeFailure>.failure(.timedOut)) {
            await preCancelledTask.value
        }
        XCTAssertEqual(preCancelledResult, .failure(.closed))

        // Direct Exchange.run inside an already-cancelled Task finishes immediately without starting driver
        let preCancelledDriver = StandInDriver(remoteEndpoint: nil)
        let preCancelledDirect = Task.detached { () -> Result<HTTPReply, ExchangeFailure> in
            withUnsafeCurrentTask { $0?.cancel() }
            let ex = Exchange(driver: preCancelledDriver, request: photo, endpoint: address.endpoint, peerCheck: { _, _ in true }, onSent: { _ in })
            return await withCheckedContinuation { c in ex.run(timeoutSec: 60) { c.resume(returning: $0) } }
        }
        let preCancelledDirectRes = await within(2, otherwise: Result<HTTPReply, ExchangeFailure>.failure(.timedOut)) {
            await preCancelledDirect.value
        }
        XCTAssertEqual(preCancelledDirectRes, .failure(.closed))
        XCTAssertFalse(preCancelledDriver.lock.withLock { preCancelledDriver.started })

        // UploadCoordinator (used by PipelineModel): reset() and second start() cancel in-flight task and block stale callbacks
        final class GatedTransport: LaptopTransport, @unchecked Sendable {
            let lock = NSLock()
            var enteredCount = 0
            var cancelledCount = 0
            let release1 = DispatchSemaphore(value: 0)
            let release2 = DispatchSemaphore(value: 0)
            func exchange(_ request: Data, to endpoint: LaptopEndpoint, timeoutSec: Double,
                          onSent: @escaping @Sendable (Int64) -> Void) async -> Result<HTTPReply, ExchangeFailure> {
                onSent(1)
                let index = lock.withLock { () -> Int in
                    enteredCount += 1
                    return enteredCount
                }
                let sem = index == 1 ? release1 : release2
                return await withTaskCancellationHandler {
                    await withCheckedContinuation { c in
                        DispatchQueue.global().async {
                            _ = sem.wait(timeout: .now() + 5)
                            c.resume(returning: .failure(.timedOut))
                        }
                    }
                } onCancel: {
                    self.lock.withLock { self.cancelledCount += 1 }
                }
            }
        }
        let gated = GatedTransport()
        let coordinator = UploadCoordinator()
        XCTAssertNil(coordinator.state)
        XCTAssertFalse(coordinator.isSending)

        coordinator.start(photo: photo, contentType: .png, record: nil,
                          uploader: CaptureUploader(address: address, timeoutSec: 5, transport: gated))
        XCTAssertTrue(coordinator.isSending)
        let deadline = Date().addingTimeInterval(3)
        while Date() < deadline, gated.lock.withLock({ gated.enteredCount }) < 1 {
            try await Task.sleep(nanoseconds: 5_000_000)
        }
        XCTAssertEqual(gated.lock.withLock { gated.enteredCount }, 1)

        coordinator.reset()
        XCTAssertNil(coordinator.state)
        XCTAssertFalse(coordinator.isSending)
        let cancelDeadline1 = Date().addingTimeInterval(2)
        while Date() < cancelDeadline1, gated.lock.withLock({ gated.cancelledCount }) < 1 {
            try await Task.sleep(nanoseconds: 5_000_000)
        }
        XCTAssertEqual(gated.lock.withLock { gated.cancelledCount }, 1)
        gated.release1.signal()
        try await Task.sleep(nanoseconds: 60_000_000)
        XCTAssertNil(coordinator.state, "stale upload callback overwrote reset state")
        XCTAssertFalse(coordinator.isSending)

        // Second start() while an upload is running cancels the previous task and keeps the newer upload's state
        coordinator.start(photo: photo, contentType: .png, record: nil,
                          uploader: CaptureUploader(address: address, timeoutSec: 5, transport: gated))
        let deadline2 = Date().addingTimeInterval(3)
        while Date() < deadline2, gated.lock.withLock({ gated.enteredCount }) < 2 {
            try await Task.sleep(nanoseconds: 5_000_000)
        }
        let receipt = HTTPReply(status: 201, headers: [], body: Data("{\"sourceAsset\":{\"sha256\":\"\(PhotoHash.sha256Hex(photo))\"},\"alreadyStored\":false}".utf8))
        coordinator.start(photo: photo, contentType: .png, record: nil,
                          uploader: CaptureUploader(address: address, timeoutSec: 5,
                                                    transport: UploadClientTests.CountingTransport(counts: [-1], reply: receipt)))
        let expectedDone = UploadState.done(sha256: PhotoHash.sha256Hex(photo), photoAlreadyStored: false, recordAccepted: false)
        let doneDeadline = Date().addingTimeInterval(2)
        while Date() < doneDeadline, coordinator.state != expectedDone || gated.lock.withLock({ gated.cancelledCount }) < 2 {
            try await Task.sleep(nanoseconds: 5_000_000)
        }
        XCTAssertEqual(gated.lock.withLock { gated.cancelledCount }, 2)
        XCTAssertEqual(coordinator.state, expectedDone)
        gated.release2.signal()
        try await Task.sleep(nanoseconds: 60_000_000)
        XCTAssertEqual(coordinator.state, expectedDone)
        XCTAssertFalse(coordinator.isSending)
    }

    func testFormatsJSONNumbersExactlyAsJSONStringifyAndComparesNumericEquality() throws {
        let cases: [(Double, String)] = [
            (0.0, "0"),
            (-0.0, "0"),
            (1.0, "1"),
            (-1.0, "-1"),
            (1.5, "1.5"),
            (-1.5, "-1.5"),
            (0.5, "0.5"),
            (-0.5, "-0.5"),
            (0.25, "0.25"),
            (0.00005, "0.00005"),
            (-0.00005, "-0.00005"),
            (1e-6, "0.000001"),
            (-1e-6, "-0.000001"),
            (1.25e-6, "0.00000125"),
            (9.999999999999997e-7, "9.999999999999997e-7"),
            (1e-7, "1e-7"),
            (-1e-7, "-1e-7"),
            (1.25e-7, "1.25e-7"),
            (1e-9, "1e-9"),
            (1e-10, "1e-10"),
            (Double.leastNonzeroMagnitude, "5e-324"),
            (9_007_199_254_740_991, "9007199254740991"),
            (-9_007_199_254_740_991, "-9007199254740991"),
            (9_007_199_254_740_992, "9007199254740992"),
            (1e16, "10000000000000000"),
            (-1e16, "-10000000000000000"),
            (1e20, "100000000000000000000"),
            (-1e20, "-100000000000000000000"),
            (1.5e20, "150000000000000000000"),
            (1e21, "1e+21"),
            (-1e21, "-1e+21"),
            (1.5e21, "1.5e+21"),
            (1e300, "1e+300"),
        ]
        for (value, expected) in cases {
            let num = try XCTUnwrap(JSONNumber(value), "\(value)")
            XCTAssertEqual(num.literal, expected, "\(value)")
            XCTAssertEqual(String(decoding: JSONText.units(.number(num)), as: UTF16.self), expected, "\(value)")
            let parsed = try JSONText.read(expected)
            XCTAssertEqual(parsed, .number(num), "\(value)")
        }

        // Numeric equality on JSONNumber / JSONValue across equivalent lexical tokens, and -0.0 canonicalized to +0.0
        XCTAssertEqual(1.0 / (try XCTUnwrap(JSONNumber(-0.0))).value, .infinity)
        XCTAssertEqual(try JSONText.read("1.0"), .int(1))
        XCTAssertEqual(try JSONText.read("1e0"), .int(1))
        XCTAssertEqual(try JSONText.read("-0"), .int(0))
        XCTAssertEqual(try JSONText.read("-0.0"), .int(0))
        XCTAssertEqual(try JSONText.read("1e-6"), .number(try XCTUnwrap(JSONNumber(1e-6))))
        XCTAssertEqual(try JSONText.read("0.000001"), .number(try XCTUnwrap(JSONNumber(1e-6))))
        XCTAssertNotEqual(try JSONText.read("1"), .int(2))
    }

    @MainActor
    func testFallsBackToArbitraryFrameUpdatesHeadingOrientationAndDropsStaleFixes() async throws {
        // 1. SensorMapping.fallbackFrame exact rule (CMErrorDomain 104, CMErrorTrueNorthNotAvailable)
        XCTAssertEqual(SensorMapping.trueNorthUnavailableErrorDomain, "CMErrorDomain")
        XCTAssertEqual(SensorMapping.trueNorthUnavailableErrorCode, 104)
        let both: Set<AttitudeFrame> = [.xTrueNorthZVertical, .xArbitraryZVertical]
        XCTAssertEqual(SensorMapping.fallbackFrame(afterErrorDomain: "CMErrorDomain", code: 104,
                                                   requested: .xTrueNorthZVertical, available: both), .xArbitraryZVertical)
        XCTAssertNil(SensorMapping.fallbackFrame(afterErrorDomain: "NSCocoaErrorDomain", code: 104,
                                                 requested: .xTrueNorthZVertical, available: both))
        XCTAssertNil(SensorMapping.fallbackFrame(afterErrorDomain: "CMErrorDomain", code: 103,
                                                 requested: .xTrueNorthZVertical, available: both))
        XCTAssertNil(SensorMapping.fallbackFrame(afterErrorDomain: "CMErrorDomain", code: 105,
                                                 requested: .xTrueNorthZVertical, available: both))
        XCTAssertNil(SensorMapping.fallbackFrame(afterErrorDomain: "CMErrorDomain", code: 104,
                                                 requested: .xArbitraryZVertical, available: both))
        XCTAssertNil(SensorMapping.fallbackFrame(afterErrorDomain: "CMErrorDomain", code: 104,
                                                 requested: .xTrueNorthZVertical, available: [.xTrueNorthZVertical]))

        let photoHash = PhotoHash.sha256Hex(Data("fallback test photo".utf8))
        let baseSample = SampleCapture.capture(photoSha256: photoHash)
        let boot = SampleCapture.photoBootClockSec
        let moment = SampleCapture.moment

        // 2. Fallback on handleTrueNorthUnavailable() (system Location Services off -> CMErrorTrueNorthNotAvailable)
        let motion1 = FallibleMotion(both)
        let place1 = TrackingPlace()
        let recorder1 = try XCTUnwrap(SensorRecorder(motion: motion1, place: place1))
        // Start and stop once first so generation == 2 during fallback, proving fallback forwards the active generation
        _ = recorder1.start(interface: .portrait)
        recorder1.stop()
        recorder1.handleTrueNorthUnavailable() // no-op when stopped
        XCTAssertEqual(motion1.stops, 1)
        XCTAssertEqual(motion1.started.count, 1)
        XCTAssertEqual(recorder1.start(interface: .portrait), .started(.xTrueNorthZVertical))
        XCTAssertEqual(motion1.started.map(\.0), [.xTrueNorthZVertical, .xTrueNorthZVertical])
        recorder1.handleTrueNorthUnavailable()
        recorder1.handleTrueNorthUnavailable() // no-op when already in .xArbitraryZVertical
        XCTAssertEqual(motion1.stops, 2)
        XCTAssertEqual(motion1.started.map(\.0), [.xTrueNorthZVertical, .xTrueNorthZVertical, .xArbitraryZVertical])
        XCTAssertEqual(motion1.started.map(\.1), [0.01, 0.01, 0.01])
        XCTAssertEqual(recorder1.status.frame, .xArbitraryZVertical)
        for offset in [-0.02, 0.0, 0.02] {
            motion1.deliver?(DeviceMotionEvent(
                timestampSec: boot + offset,
                frame: .xArbitraryZVertical,
                quaternion: .init(x: 0.2, y: -0.4, z: 0.4, w: 0.8),
                gravityG: .init(x: 0, y: -0.6, z: -0.8),
                rotationRateRadPerSec: .init(x: 0.002, y: -0.001, z: 0.0005)
            ))
        }
        let built1 = try PointingRecordBuilder.build(recorder1.capture(
            photoSha256: photoHash, photoBootClockSec: boot, photoDeviceClock: moment,
            device: baseSample.device, camera: baseSample.camera, windowStartOffsetSec: -0.05, windowEndOffsetSec: 0.05
        ))
        XCTAssertEqual(built1.heading, .absent(.noMagnetometer))
        recorder1.stop()

        // Guard when .xArbitraryZVertical is not in availableFrames, and status.frame tracking when motion delivers .xArbitraryZVertical directly
        let motionNoArb = FallibleMotion([.xTrueNorthZVertical])
        let recorderNoArb = try XCTUnwrap(SensorRecorder(motion: motionNoArb, place: TrackingPlace()))
        _ = recorderNoArb.start(interface: .portrait)
        recorderNoArb.handleTrueNorthUnavailable()
        XCTAssertEqual(motionNoArb.stops, 0)
        XCTAssertEqual(recorderNoArb.status.frame, .xTrueNorthZVertical)
        motionNoArb.deliver?(DeviceMotionEvent(
            timestampSec: boot,
            frame: .xArbitraryZVertical,
            quaternion: .init(x: 0.2, y: -0.4, z: 0.4, w: 0.8),
            gravityG: .init(x: 0, y: -0.6, z: -0.8),
            rotationRateRadPerSec: .init(x: 0.002, y: -0.001, z: 0.0005)
        ))
        XCTAssertEqual(recorderNoArb.status.frame, .xArbitraryZVertical)
        recorderNoArb.stop()

        // Background-thread delivery of .denied also hops to MainActor and triggers fallback
        let bgMotion = FallibleMotion(both)
        let bgPlace = TrackingPlace()
        let bgRecorder = try XCTUnwrap(SensorRecorder(motion: bgMotion, place: bgPlace))
        _ = bgRecorder.start(interface: .portrait)
        let bgDeliver = bgPlace.deliver
        await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in
            DispatchQueue.global().async {
                bgDeliver?(.authorization(.denied, .full))
                c.resume()
            }
        }
        let bgDeadline = Date().addingTimeInterval(2)
        while Date() < bgDeadline, bgRecorder.status.frame != .xArbitraryZVertical {
            try await Task.sleep(nanoseconds: 5_000_000)
        }
        XCTAssertEqual(bgRecorder.status.frame, .xArbitraryZVertical)
        bgRecorder.stop()

        // 3. Fallback when authorization reports .denied or .restricted (REQ-IOS-6.2)
        for deniedStatus: LocationAuthorization in [.denied, .restricted] {
            let motion2 = FallibleMotion(both)
            let place2 = TrackingPlace()
            let recorder2 = try XCTUnwrap(SensorRecorder(motion: motion2, place: place2))
            _ = recorder2.start(interface: .portrait)
            place2.deliver?(.authorization(deniedStatus, .full))
            XCTAssertEqual(motion2.started.map(\.0), [.xTrueNorthZVertical, .xArbitraryZVertical], "\(deniedStatus)")
            XCTAssertEqual(recorder2.status.frame, .xArbitraryZVertical, "\(deniedStatus)")
            for offset in [-0.02, 0.0, 0.02] {
                motion2.deliver?(DeviceMotionEvent(
                    timestampSec: boot + offset,
                    frame: .xArbitraryZVertical,
                    quaternion: .init(x: 0.2, y: -0.4, z: 0.4, w: 0.8),
                    gravityG: .init(x: 0, y: -0.6, z: -0.8),
                    rotationRateRadPerSec: .init(x: 0.002, y: -0.001, z: 0.0005)
                ))
            }
            let built2 = try PointingRecordBuilder.build(recorder2.capture(
                photoSha256: photoHash, photoBootClockSec: boot, photoDeviceClock: moment,
                device: baseSample.device, camera: baseSample.camera, windowStartOffsetSec: -0.05, windowEndOffsetSec: 0.05
            ))
            XCTAssertEqual(built2.heading, .absent(.noMagnetometer))
            XCTAssertEqual(built2.location, .absent(.notAllowed))
            recorder2.stop()
        }

        // 4. Live rotation via updateInterface(_:) updates headingOrientation without wiping MotionRing or fixes
        let motion3 = FallibleMotion(both)
        let place3 = TrackingPlace()
        let recorder3 = try XCTUnwrap(SensorRecorder(motion: motion3, place: place3))
        _ = recorder3.start(interface: .unknown)
        XCTAssertEqual(place3.startedOrientations, [.portrait])
        place3.deliver?(.authorization(.authorizedWhenInUse, .full))
        place3.deliver?(.location(LocationEvent(latDeg: 30, lonDeg: -40, horizontalAccuracyM: 8, altitudeM: 12,
                                                ellipsoidalAltitudeM: 45.5, verticalAccuracyM: 3,
                                                timestamp: moment.addingTimeInterval(-2), isSimulatedBySoftware: false)))
        for index in 0..<20 {
            motion3.deliver?(DeviceMotionEvent(
                timestampSec: boot - 0.10 + Double(index) * 0.01,
                frame: .xTrueNorthZVertical,
                quaternion: .init(x: 0.2, y: -0.4, z: 0.4, w: 0.8),
                gravityG: .init(x: 0, y: -0.6, z: -0.8),
                rotationRateRadPerSec: .init(x: 0.002, y: -0.001, z: 0.0005)
            ))
        }
        recorder3.updateInterface(.landscapeLeft)
        recorder3.updateInterface(.unknown)
        XCTAssertEqual(place3.updatedOrientations, [.landscapeRight])
        XCTAssertEqual(motion3.stops, 0)
        XCTAssertEqual(place3.stops, 0)
        XCTAssertEqual(recorder3.status.samplesKept, 20)
        XCTAssertNotNil(recorder3.status.lastFix)
        recorder3.stop()
        recorder3.updateInterface(.portraitUpsideDown)
        XCTAssertEqual(place3.updatedOrientations, [.landscapeRight])

        // 5. Staleness bounds: fixes up to 300 s and headings up to 60 s before the photo are kept; older cached ones are dropped
        XCTAssertEqual(SensorMapping.maxFixAgeSec, 300)
        XCTAssertEqual(SensorMapping.maxHeadingAgeSec, 60)
        let motion4 = FallibleMotion(both)
        let place4 = TrackingPlace()
        let recorder4 = try XCTUnwrap(SensorRecorder(motion: motion4, place: place4))
        _ = recorder4.start(interface: .portrait)
        place4.deliver?(.authorization(.authorizedWhenInUse, .full))
        place4.deliver?(.location(LocationEvent(latDeg: 10, lonDeg: 20, horizontalAccuracyM: 8, altitudeM: 0,
                                                ellipsoidalAltitudeM: 0, verticalAccuracyM: -1,
                                                timestamp: moment.addingTimeInterval(-86_400), isSimulatedBySoftware: false)))
        place4.deliver?(.location(LocationEvent(latDeg: 11, lonDeg: 21, horizontalAccuracyM: 8, altitudeM: 0,
                                                ellipsoidalAltitudeM: 0, verticalAccuracyM: -1,
                                                timestamp: moment.addingTimeInterval(-300.001), isSimulatedBySoftware: false)))
        place4.deliver?(.heading(HeadingEvent(trueHeadingDeg: 100, headingAccuracyDeg: 5,
                                              timestamp: moment.addingTimeInterval(-60.001), orientation: .portrait)))
        let staleCapture = recorder4.capture(
            photoSha256: photoHash, photoBootClockSec: boot, photoDeviceClock: moment,
            device: baseSample.device, camera: baseSample.camera, windowStartOffsetSec: -0.05, windowEndOffsetSec: 0.05
        )
        XCTAssertEqual(staleCapture.location, .allowed(.full, fix: nil))
        XCTAssertNil(staleCapture.heading)

        // Boundary inside: exactly -300.0 s and -60.0 s are accepted
        place4.deliver?(.location(LocationEvent(latDeg: 12, lonDeg: 22, horizontalAccuracyM: 8, altitudeM: 0,
                                                ellipsoidalAltitudeM: 0, verticalAccuracyM: -1,
                                                timestamp: moment.addingTimeInterval(-300.0), isSimulatedBySoftware: false)))
        place4.deliver?(.heading(HeadingEvent(trueHeadingDeg: 120, headingAccuracyDeg: 5,
                                              timestamp: moment.addingTimeInterval(-60.0), orientation: .portrait)))
        let boundaryCapture = recorder4.capture(
            photoSha256: photoHash, photoBootClockSec: boot, photoDeviceClock: moment,
            device: baseSample.device, camera: baseSample.camera, windowStartOffsetSec: -0.05, windowEndOffsetSec: 0.05
        )
        guard case .allowed(.full, let freshFix?) = boundaryCapture.location else {
            return XCTFail("expected fresh fix at 300 s boundary, got \(boundaryCapture.location)")
        }
        XCTAssertEqual(freshFix.latDeg, 12)
        XCTAssertEqual(boundaryCapture.heading?.trueHeadingDeg, 120)
        XCTAssertEqual(SensorRecorder.newest([(7, moment.addingTimeInterval(-90_000))], notAfter: moment, time: \.1)?.0, 7)
        recorder4.stop()

        // 6. @MainActor source isolation on CoreMotionSource and CoreLocationSource (no @unchecked Sendable)
        for rel in ["apps/ios/AlbedianCapture/Sensors/CoreMotionSource.swift",
                    "apps/ios/AlbedianCapture/Sensors/CoreLocationSource.swift"] {
            let src = try String(contentsOf: repositoryRoot.appendingPathComponent(rel), encoding: .utf8)
            let code = try SourceReader.code(src, .swift)
            XCTAssertTrue(code.contains("@MainActor\nfinal class "), rel)
            XCTAssertFalse(code.contains("@unchecked"), rel)
        }
    }
}
