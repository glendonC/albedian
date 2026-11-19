import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// Task 9 (mobile-3p): each kept capture's send state is kept on the phone with its reason, so it survives the app
/// closing: sent once the laptop holds it, otherwise not sent with the failure's name; nothing else (no time, no
/// place, no address, no value from the photo or the record).
final class SendLedgerTests: XCTestCase {
    private var folder: URL!

    override func setUpWithError() throws {
        folder = FileManager.default.temporaryDirectory.appendingPathComponent("send-ledger-\(UUID().uuidString)")
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: folder)
    }

    private let sha = String(repeating: "ab", count: 32)

    func testASentCaptureIsReadBackAsSentAfterAFreshStart() throws {
        let ledger = SendLedger(folder: folder)
        XCTAssertNil(ledger.state(of: sha))
        ledger.record(.done(sha256: sha, photoAlreadyStored: false, recordAccepted: true), for: sha)
        XCTAssertEqual(SendLedger(folder: folder).state(of: sha), .sent)
    }

    func testANotSentCaptureKeepsTheFailuresNameAndOnlyThat() throws {
        let ledger = SendLedger(folder: folder)
        ledger.record(.failed(.networkError(phase: .photo, posixCode: 61)), for: sha)
        XCTAssertEqual(SendLedger(folder: folder).state(of: sha), .notSent(reason: "network_error"))
        ledger.record(.failed(.refused(phase: .record, status: 413, detail: "a value the laptop wrote")), for: sha)
        XCTAssertEqual(SendLedger(folder: folder).state(of: sha), .notSent(reason: "refused_413"))
        let names = try FileManager.default.contentsOfDirectory(atPath: folder.path)
        XCTAssertEqual(names, ["\(sha).json"], "one file per photo, named by its hash")
        let text = try String(contentsOf: folder.appendingPathComponent("\(sha).json"), encoding: .utf8)
        XCTAssertFalse(text.contains("a value the laptop wrote"), "the laptop's words are not kept")
        XCTAssertEqual(text, "{\"sha256\":\"\(sha)\",\"state\":\"not_sent\",\"reason\":\"refused_413\"}")
    }

    func testEachFailureHasItsOwnName() {
        let cases: [(UploadFailure, String)] = [
            (.networkError(phase: .photo, posixCode: nil), "network_error"), (.timedOut(phase: .photo), "timed_out"),
            (.connectionClosed(phase: .record), "connection_closed"), (.notThePairedPeer(phase: .photo), "not_the_paired_peer"),
            (.refused(phase: .photo, status: 404, detail: nil), "refused_404"), (.redirected(phase: .photo, status: 302), "redirected_302"),
            (.unreadableReply(phase: .record), "unreadable_reply"), (.storedDifferentBytes(receiptSha256: sha), "stored_different_bytes"),
            (.recordNamesAnotherPhoto, "record_names_another_photo"), (.recordNotWritable([]), "record_not_writable"),
            (.notPermitted(phase: .photo), "not_permitted"),
        ]
        for (failure, name) in cases { XCTAssertEqual(SendLedger.reasonName(failure), name) }
    }

    func testAStateUnderWayIsNotKeptAndAHashThatIsNotOneIsRefused() throws {
        let ledger = SendLedger(folder: folder)
        ledger.record(.waiting, for: sha)
        ledger.record(.sending(phase: .photo, bytesSent: 1, bytesTotal: 2), for: sha)
        XCTAssertNil(ledger.state(of: sha))
        ledger.record(.failed(.timedOut(phase: .photo)), for: "../escape")
        XCTAssertNil(ledger.state(of: "../escape"))
        XCTAssertFalse(FileManager.default.fileExists(atPath: folder.deletingLastPathComponent().appendingPathComponent("escape.json").path),
                       "nothing written outside the folder")
        let written = (try? FileManager.default.contentsOfDirectory(atPath: folder.path)) ?? []
        XCTAssertFalse(written.contains { $0.contains("escape") })
        // A done send names the photo it stored: one for another photo is not recorded as this one sent
        ledger.record(.done(sha256: String(repeating: "cd", count: 32), photoAlreadyStored: false, recordAccepted: true), for: sha)
        XCTAssertNil(ledger.state(of: sha))
    }

    func testAKeptFileThatIsNotALedgerEntryForItsPhotoReadsAsNoState() throws {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try Data("{\"sha256\":\"\(String(repeating: "cd", count: 32))\",\"state\":\"sent\"}".utf8).write(to: folder.appendingPathComponent("\(sha).json"))
        XCTAssertNil(SendLedger(folder: folder).state(of: sha))
        try Data("{\"sha256\":\"\(sha)\",\"state\":\"maybe\"}".utf8).write(to: folder.appendingPathComponent("\(sha).json"))
        XCTAssertNil(SendLedger(folder: folder).state(of: sha))
        try Data("not json".utf8).write(to: folder.appendingPathComponent("\(sha).json"))
        XCTAssertNil(SendLedger(folder: folder).state(of: sha))
    }

    func testTheCapturesNotYetSentAreThoseWithNoSentState() {
        let ledger = SendLedger(folder: folder)
        let a = String(repeating: "a", count: 63) + "1", b = String(repeating: "b", count: 64), c = String(repeating: "c", count: 64)
        ledger.record(.done(sha256: a, photoAlreadyStored: false, recordAccepted: true), for: a)
        ledger.record(.failed(.timedOut(phase: .photo)), for: b)
        XCTAssertEqual(ledger.notSent(among: [a, b, c]), [b, c])
    }
}
