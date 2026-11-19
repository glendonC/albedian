import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The approved screens' rules that are not looks (UIUX-connect-APPROVED, UIUX-unreachable-APPROVED, UIUX-arrive-READY
/// iPhone 5 and 6): the laptop's address typed once and kept only after a good send, where a send's end leads, the
/// server's status word, and a mark on the result's card, which the photo covers.
final class CompanionFlowTests: XCTestCase {
    final class MemoryValues: KeptValues, @unchecked Sendable {
        private let lock = NSLock()
        private var values: [String: String] = [:]
        func string(for key: String) -> String? { lock.withLock { values[key] } }
        func set(_ value: String?, for key: String) { lock.withLock { values[key] = value } }
    }

    func testTheAddressIsTypedAfterTheFixedSchemeAndReadAsAPairingAddress() {
        XCTAssertEqual(PairingKeeper.address(typed: "172.20.10.3:5173")?.description, "http://172.20.10.3:5173")
        XCTAssertEqual(PairingKeeper.address(typed: "  192.168.1.20:5173 ")?.description, "http://192.168.1.20:5173")
        XCTAssertNil(PairingKeeper.address(typed: ""))
        XCTAssertNil(PairingKeeper.address(typed: "http://172.20.10.3:5173"), "the scheme is shown, never typed twice")
        XCTAssertNil(PairingKeeper.address(typed: "example.com"), "only an address on the local network")
        XCTAssertNil(PairingKeeper.address(typed: "172.20.10.3:5173/path"))
        XCTAssertEqual(PairingKeeper.fixedScheme, "http://")
    }

    func testNothingIsKeptUntilAGoodSendAndThenTheSameAddressComesBack() throws {
        let values = MemoryValues()
        let keeper = PairingKeeper(values: values)
        XCTAssertNil(keeper.kept)
        let address = try XCTUnwrap(PairingKeeper.address(typed: "172.20.10.3:5173"))
        keeper.keep(address)
        XCTAssertEqual(PairingKeeper(values: values).kept, address)
        XCTAssertEqual(values.string(for: PairingKeeper.key), "http://172.20.10.3:5173")
    }

    func testAKeptTextThatIsNoLongerALocalAddressIsNotUsed() {
        let values = MemoryValues()
        values.set("http://example.com", for: PairingKeeper.key)
        XCTAssertNil(PairingKeeper(values: values).kept)
        values.set("http://10.0.0.2:5173", for: PairingKeeper.key)
        XCTAssertEqual(PairingKeeper(values: values).kept?.description, "http://10.0.0.2:5173")
    }

    func testForgettingRemovesTheKeptAddress() throws {
        let values = MemoryValues()
        let keeper = PairingKeeper(values: values)
        keeper.keep(try XCTUnwrap(PairingKeeper.address(typed: "10.0.0.2:5173")))
        keeper.forget()
        XCTAssertNil(keeper.kept)
        XCTAssertNil(values.string(for: PairingKeeper.key))
    }

    /// "Can't reach the laptop" only when the laptop was not reached; a laptop that answered and refused is not that.
    func testOnlyAFailureToReachTheLaptopLeadsToTheUnreachableScreen() {
        let sha = String(repeating: "ab", count: 32)
        XCTAssertEqual(SendEnd.after(.done(sha256: sha, photoAlreadyStored: false, recordAccepted: true)), .solve(sha256: sha))
        XCTAssertEqual(SendEnd.after(.done(sha256: sha, photoAlreadyStored: true, recordAccepted: false)), .solve(sha256: sha))
        for failure: UploadFailure in [.networkError(phase: .photo, posixCode: 61), .networkError(phase: .record, posixCode: nil),
                                        .timedOut(phase: .photo), .connectionClosed(phase: .record), .notThePairedPeer(phase: .photo)] {
            XCTAssertEqual(SendEnd.after(.failed(failure)), .unreachable, "\(failure)")
        }
        for failure: UploadFailure in [.refused(phase: .record, status: 413, detail: nil), .redirected(phase: .photo, status: 302),
                                        .unreadableReply(phase: .photo), .storedDifferentBytes(receiptSha256: sha),
                                        .recordNamesAnotherPhoto, .recordNotWritable([]), .notPermitted(phase: .photo)] {
            XCTAssertEqual(SendEnd.after(.failed(failure)), .notSent, "\(failure)")
        }
        XCTAssertNil(SendEnd.after(.waiting))
        XCTAssertNil(SendEnd.after(.sending(phase: .photo, bytesSent: 1, bytesTotal: 2)))
    }

    /// The result's title is the server's status word (arrive 6: "Calibrated"), its underscore read as a space.
    func testTheStatusWordIsTheServersWord() {
        XCTAssertEqual(SolutionStatus.calibrated.word, "Calibrated")
        XCTAssertEqual(SolutionStatus.predictedOnly.word, "Predicted only")
        XCTAssertEqual(SolutionStatus.unresolved.word, "Unresolved")
        XCTAssertEqual(SolutionStatus.allCases.map(\.word), ["Pending", "Calibrated", "Approximate", "Predicted only", "Unresolved", "Ambiguous"])
    }

    /// The card shows the photo covering it, centred (`background: center / cover`), so a detection lands where that
    /// cropped photo shows it.
    func testAMarkLandsWhereTheCoveringPhotoShowsIt() {
        let portrait = StoredFrame(widthPx: 3024, heightPx: 4032, orientation: .upright)
        let corner = portrait.placeCovering(xPx: -0.5, yPx: -0.5, onWidth: 342, height: 456)
        XCTAssertEqual(corner.x, 0, accuracy: 1e-9)
        XCTAssertEqual(corner.y, 0, accuracy: 1e-9)
        // A 4:3 photo on the 3:4 card: the height fills, the sides are cut equally
        let wide = StoredFrame(widthPx: 400, heightPx: 300, orientation: .upright)
        let centre = wide.placeCovering(xPx: 199.5, yPx: 149.5, onWidth: 342, height: 456)
        XCTAssertEqual(centre.x, 171, accuracy: 1e-9)
        XCTAssertEqual(centre.y, 228, accuracy: 1e-9)
        let left = wide.placeCovering(xPx: -0.5, yPx: -0.5, onWidth: 342, height: 456)
        XCTAssertEqual(left.x, (342 - 456 * 400.0 / 300) / 2, accuracy: 1e-9)
        XCTAssertEqual(left.y, 0, accuracy: 1e-9)
        // Turned a quarter: a 4032 by 3024 frame shows as 3024 by 4032 and fills the card exactly
        let turned = StoredFrame(widthPx: 4032, heightPx: 3024, orientation: DisplayOrientation(quarterTurnsClockwise: 1, mirrored: false))
        let p = turned.placeCovering(xPx: -0.5, yPx: 3023.5, onWidth: 342, height: 456)
        XCTAssertEqual(p.x, 0, accuracy: 1e-9)
        XCTAssertEqual(p.y, 0, accuracy: 1e-9)
    }

    /// Whether a mark lies on the card at all once the photo is cut to cover it.
    func testAMarkOutsideTheCoveredCardIsNotDrawn() {
        let wide = StoredFrame(widthPx: 400, heightPx: 300, orientation: .upright)
        XCTAssertFalse(wide.isOnCard(xPx: 0, yPx: 150, width: 342, height: 456))
        XCTAssertTrue(wide.isOnCard(xPx: 200, yPx: 150, width: 342, height: 456))
        // Just off each side: the 400 by 300 frame covers the card at 1.52 per pixel from x = -133
        XCTAssertFalse(wide.isOnCard(xPx: 83.7, yPx: 150, width: 342, height: 456), "about 5 pt left of the card")
        XCTAssertFalse(wide.isOnCard(xPx: 312.3, yPx: 150, width: 342, height: 456), "about 5 pt right of the card")
        XCTAssertTrue(wide.isOnCard(xPx: 88, yPx: 150, width: 342, height: 456), "just inside the left edge")
    }
}

/// The coordinators hand the screens a send's and a solve's last state once, and never after a reset.
@MainActor
final class CoordinatorEndTests: XCTestCase {
    final class Ends: @unchecked Sendable {
        var upload: [UploadState] = []
        var solve: [SolveState] = []
    }

    func testAKeptCaptureSendReportsItsLastStateOnceAndAResetSilencesIt() async throws {
        let server = try LoopbackHTTPServer(responder: UploadClientTests.laptop)
        defer { server.stop() }
        let address = try XCTUnwrap(LaptopAddress(server.baseURL))
        let photo = Data("not a photo, a body".utf8)
        let ends = Ends()
        let coordinator = UploadCoordinator()
        coordinator.start(photo: photo, contentType: .jpeg, recordWire: nil, uploader: CaptureUploader(address: address, timeoutSec: 5)) {
            ends.upload.append($0)
        }
        for _ in 0..<400 where ends.upload.isEmpty { try await Task.sleep(nanoseconds: 10_000_000) }
        XCTAssertEqual(ends.upload, [.done(sha256: PhotoHash.sha256Hex(photo), photoAlreadyStored: false, recordAccepted: false)])

        let silenced = Ends()
        let held = try LoopbackHTTPServer(answerDelaySec: 0.3, responder: UploadClientTests.laptop)
        defer { held.stop() }
        coordinator.start(photo: photo, contentType: .jpeg, recordWire: nil,
                          uploader: CaptureUploader(address: try XCTUnwrap(LaptopAddress(held.baseURL)), timeoutSec: 5)) { silenced.upload.append($0) }
        coordinator.reset()
        try await Task.sleep(nanoseconds: 800_000_000)
        XCTAssertTrue(silenced.upload.isEmpty)
    }

    func testASolveReportsItsLastStateOnce() async throws {
        let sha = String(repeating: "ab", count: 32)
        let server = try LoopbackHTTPServer { _ in .json(404, "{\"detail\":\"no file with that hash is stored\"}") }
        defer { server.stop() }
        let ends = Ends()
        let coordinator = SolveCoordinator()
        coordinator.start(sha256: sha, client: SolveClient(address: try XCTUnwrap(LaptopAddress(server.baseURL)), solveTimeoutSec: 5,
                                                          readTimeoutSec: 2, pollIntervalSec: 0.05)) { ends.solve.append($0) }
        for _ in 0..<400 where ends.solve.isEmpty { try await Task.sleep(nanoseconds: 10_000_000) }
        XCTAssertEqual(ends.solve, [.failed(.refused(status: 404, detail: "no file with that hash is stored"))])
        XCTAssertNotNil(coordinator.startedAt)
    }
}
