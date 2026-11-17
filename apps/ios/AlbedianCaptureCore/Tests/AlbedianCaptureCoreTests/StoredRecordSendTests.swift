import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// Sending a capture the phone kept: the record goes out as the bytes kept, checked as before, against a server on
/// 127.0.0.1. Synthetic bytes.
final class StoredRecordSendTests: XCTestCase {
    private let photo = Data("synthetic photo bytes for a kept capture".utf8)

    private func wire(for photo: Data) throws -> Data {
        try PointingRecordBuilder.build(SampleCapture.capture(photoSha256: PhotoHash.sha256Hex(photo))).wireData()
    }

    private func send(_ server: LoopbackHTTPServer, recordWire: Data?, file: StaticString = #filePath, line: UInt = #line) async throws -> UploadState {
        let address = try XCTUnwrap(LaptopAddress(server.baseURL))
        let photo = photo
        return await bounded(file: file, line: line) {
            await CaptureUploader(address: address, timeoutSec: 10).send(photo: photo, contentType: .heic, recordWire: recordWire) { _ in }
        }
    }

    func testSendsTheKeptRecordBytesAsTheyWereKept() async throws {
        let server = try LoopbackHTTPServer(responder: UploadClientTests.laptop)
        defer { server.stop() }
        // Indented on purpose: the bytes kept go out unchanged, not written again
        let kept = Data(JSONText.pretty(try JSONText.read(try wire(for: photo))).utf8)
        let last = try await send(server, recordWire: kept)
        XCTAssertEqual(last, .done(sha256: PhotoHash.sha256Hex(photo), photoAlreadyStored: false, recordAccepted: true))
        XCTAssertEqual(server.requests.map(\.method), ["POST", "PUT"])
        XCTAssertEqual(server.requests.first?.body, photo)
        XCTAssertEqual(server.requests.last?.body, kept)
        XCTAssertEqual(server.requests.last?.headers["content-type"], "application/json")
    }

    func testSendsThePhotoAloneWhenNoRecordWasKept() async throws {
        let server = try LoopbackHTTPServer(responder: UploadClientTests.laptop)
        defer { server.stop() }
        let last = try await send(server, recordWire: nil)
        XCTAssertEqual(last, .done(sha256: PhotoHash.sha256Hex(photo), photoAlreadyStored: false, recordAccepted: false))
        XCTAssertEqual(server.requests.map(\.method), ["POST"])
    }

    func testChecksAKeptRecordBeforeSendingAnything() async throws {
        let server = try LoopbackHTTPServer(responder: UploadClientTests.laptop)
        defer { server.stop() }
        let other = try wire(for: Data("other bytes".utf8))
        let forOther = try await send(server, recordWire: other)
        XCTAssertEqual(forOther, .failed(.recordNamesAnotherPhoto))
        let broken = Data(String(decoding: try wire(for: photo), as: UTF8.self).replacingOccurrences(of: "\"pointing_hint\"", with: "\"observed\"").utf8)
        guard case .failed(.recordNotWritable(let issues)) = try await send(server, recordWire: broken) else { return XCTFail() }
        XCTAssertFalse(issues.isEmpty)
        guard case .failed(.recordNotWritable(let unreadable)) = try await send(server, recordWire: Data("{\"a\":".utf8)) else { return XCTFail() }
        XCTAssertEqual(unreadable.first?.message, "The kept record is not JSON")
        XCTAssertEqual(server.requests.count, 0)
    }
}
