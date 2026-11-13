import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The upload client against a server on 127.0.0.1 that each test starts and stops (REQ-IOS-1.1, P3 through the
/// client; REQ-IOS-4.1, 4.2 for the upload, 4.3 for a laptop that cannot be reached). The photo's bytes go out exactly as
/// given, the SHA-256 in the record and the path is of those bytes, and every state comes from what the network
/// reported.
final class UploadClientTests: XCTestCase {
    final class StateLog: @unchecked Sendable {
        private let lock = NSLock()
        private var items: [UploadState] = []
        func append(_ state: UploadState) { lock.withLock { items.append(state) } }
        var states: [UploadState] { lock.withLock { items } }
    }

    /// The laptop as pointing-2 proposes it: the photo stored and its receipt returned, the record checked by the
    /// contract and its receipt naming the photo [proposed: pointing-2's contract does not exist yet].
    static func laptop(_ request: LoopbackHTTPServer.Request) -> LoopbackHTTPServer.Response {
        if request.method == "POST", request.path == "/api/ingest" {
            let sha = PhotoHash.sha256Hex(request.body)
            return .json(201, "{\"sourceAsset\":{\"sha256\":\"\(sha)\",\"title\":\"Upload\"},\"hints\":{},\"alreadyStored\":false}")
        }
        if request.method == "PUT", request.path.hasPrefix("/api/ingest/"), request.path.hasSuffix("/pointing") {
            let issues = PointingValidator.issues(inWire: request.body)
            let sha = request.path.split(separator: "/").dropLast().last.map(String.init) ?? ""
            return issues.isEmpty ? .json(201, "{\"sourceAssetSha256\":\"\(sha)\"}") : .json(422, "{\"detail\":\"the record fails the contract\"}")
        }
        return .json(404, "{\"detail\":\"Not Found\"}")
    }

    private func photoBytes(count: Int, seed: UInt64) -> Data {
        var generator = SeededGenerator(seed: seed)
        var bytes = Data(count: count)
        for index in 0..<count { bytes[index] = UInt8(truncatingIfNeeded: generator.next()) }
        return bytes
    }

    private func record(for photo: Data) throws -> PointingRecord {
        try PointingRecordBuilder.build(SampleCapture.capture(photoSha256: PhotoHash.sha256Hex(photo)))
    }

    private func send(_ server: LoopbackHTTPServer, photo: Data, record: PointingRecord?, timeoutSec: Double = 10)
        async throws -> (UploadState, [UploadState]) {
        let address = try XCTUnwrap(LaptopAddress(server.baseURL))
        let log = StateLog()
        let last = await bounded {
            await CaptureUploader(address: address, timeoutSec: timeoutSec).send(photo: photo, contentType: .heic, record: record) { log.append($0) }
        }
        return (last, log.states)
    }

    /// Every sending state holds real counts: zero before the body starts, then never more than the body and never
    /// going back, the photo's before the record's. URLSession may report a body's last bytes only after the laptop
    /// has answered, when the relay is already closed (it was seen for the 1.6 kB record in a shuffled run), so the
    /// last count reaching the body's length is asserted only where the server holds its answer back (`complete`).
    private func assertHonestProgress(_ states: [UploadState], photoBytes: Int, recordBytes: Int?, complete: Bool = false,
                                      file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(states.first, .waiting, file: file, line: line)
        var last: [UploadPhase: Int64] = [:]
        for state in states {
            guard case .sending(let phase, let sent, let total) = state else { continue }
            if last[phase] == nil { XCTAssertEqual(sent, 0, "\(phase) starts at zero", file: file, line: line) }
            if phase == .photo { XCTAssertNil(last[.record], "a photo count after the record's", file: file, line: line) }
            let expected = Int64(phase == .photo ? photoBytes : (recordBytes ?? -1))
            XCTAssertEqual(total, expected, "\(phase) total", file: file, line: line)
            XCTAssertLessThanOrEqual(sent, total, file: file, line: line)
            XCTAssertGreaterThanOrEqual(sent, last[phase] ?? 0, "\(phase) went back", file: file, line: line)
            last[phase] = sent
        }
        XCTAssertNotNil(last[.photo], "the photo's counts", file: file, line: line)
        XCTAssertEqual(last[.record] != nil, recordBytes != nil, "the record's counts", file: file, line: line)
        guard complete else { return }
        XCTAssertEqual(last[.photo], Int64(photoBytes), "the photo's last count", file: file, line: line)
        if let recordBytes { XCTAssertEqual(last[.record], Int64(recordBytes), "the record's last count", file: file, line: line) }
    }

    func testSendsThePhotoBytesExactlyAsGivenThenTheRecordForTheirHash() async throws {
        // The answers are held 0.3 s, so the connection has reported every byte it took before they arrive
        let server = try LoopbackHTTPServer(answerDelaySec: 0.3, responder: Self.laptop)
        defer { server.stop() }
        let photo = photoBytes(count: 3 * 1024 * 1024 + 17, seed: 7)
        let record = try record(for: photo)
        let (last, states) = try await send(server, photo: photo, record: record)
        let sha = PhotoHash.sha256Hex(photo)
        XCTAssertEqual(last, .done(sha256: sha, photoAlreadyStored: false, recordAccepted: true))
        XCTAssertEqual(states.last, last)
        let requests = server.requests
        XCTAssertEqual(requests.map(\.method), ["POST", "PUT"])
        XCTAssertEqual(requests[0].path, "/api/ingest")
        XCTAssertEqual(requests[0].body, photo)
        XCTAssertEqual(requests[0].headers["content-type"], "image/heic")
        XCTAssertEqual(requests[0].headers["content-length"], String(photo.count))
        XCTAssertEqual(requests[0].headers["user-agent"], CaptureUploader.userAgent)
        XCTAssertNil(requests[0].headers["x-albedian-filename"], "no file name is sent: a name can hold a time")
        XCTAssertEqual(requests[1].path, "/api/ingest/\(sha)/pointing")
        XCTAssertEqual(requests[1].headers["content-type"], "application/json")
        XCTAssertEqual(requests[1].body, try record.wireData())
        XCTAssertEqual(PointingValidator.issues(inWire: requests[1].body), [])
        // The zero before each body, then the connection's own counts up to the body's length (64 KiB at a time)
        assertHonestProgress(states, photoBytes: photo.count, recordBytes: try record.wireData().count, complete: true)
    }

    /// A transport that reports the counts it is told to and answers with a fixed reply, so the uploader's handling of
    /// counts is seen apart from any network.
    struct CountingTransport: LaptopTransport {
        let counts: [Int64]
        let reply: HTTPReply
        func exchange(_ request: Data, to endpoint: LaptopEndpoint, timeoutSec: Double,
                      onSent: @escaping @Sendable (Int64) -> Void) async -> Result<HTTPReply, ExchangeFailure> {
            for count in counts { onSent(count == -1 ? Int64(request.count) : count) }
            return .success(reply)
        }
    }

    func testPassesOnTheBodyBytesTheConnectionReportsTakenAndNothingMore() async throws {
        // The connection counts the whole request; the states count the body: the request head is taken off, and no
        // count is ever above the body's length or below zero
        let photo = photoBytes(count: 1000, seed: 19)
        let sha = PhotoHash.sha256Hex(photo)
        let receipt = HTTPReply(status: 201, headers: [], body: Data("{\"sourceAsset\":{\"sha256\":\"\(sha)\"},\"alreadyStored\":true}".utf8))
        let address = try XCTUnwrap(LaptopAddress("http://127.0.0.1:8000"))
        let head = Int64(HTTPMessage.request(method: "POST", path: "/api/ingest", endpoint: address.endpoint, contentType: "image/heic",
                                             body: photo).count - photo.count)
        let log = StateLog()
        let uploader = CaptureUploader(address: address, timeoutSec: 5,
                                       transport: CountingTransport(counts: [10, head, head + 400, -1], reply: receipt))
        let last = await bounded { await uploader.send(photo: photo, contentType: .heic, record: nil) { log.append($0) } }
        XCTAssertEqual(last, .done(sha256: sha, photoAlreadyStored: true, recordAccepted: false))
        XCTAssertEqual(log.states, [.waiting, .sending(phase: .photo, bytesSent: 0, bytesTotal: 1000),
                                    .sending(phase: .photo, bytesSent: 0, bytesTotal: 1000), .sending(phase: .photo, bytesSent: 0, bytesTotal: 1000),
                                    .sending(phase: .photo, bytesSent: 400, bytesTotal: 1000), .sending(phase: .photo, bytesSent: 1000, bytesTotal: 1000),
                                    last])
    }

    func testRefusesARecordWithANumberJSONCannotHold() async throws {
        let server = try LoopbackHTTPServer(responder: Self.laptop)
        defer { server.stop() }
        let photo = photoBytes(count: 500, seed: 18)
        var broken = try record(for: photo)
        broken.motion.rotationRateDeviceRadPerSec.x = .nan
        let (last, _) = try await send(server, photo: photo, record: broken)
        XCTAssertEqual(last, .failed(.recordNotWritable([PointingIssue(path: ["motion", "rotationRateDeviceRadPerSec", "x"],
                                                                       message: "Number must be finite")])))
        XCTAssertEqual(server.requests.count, 0)
    }

    func testSendsThePhotoAloneWhenNoRecordCouldBeWritten() async throws {
        let server = try LoopbackHTTPServer(responder: Self.laptop)
        defer { server.stop() }
        let photo = photoBytes(count: 1000, seed: 8)
        let (last, states) = try await send(server, photo: photo, record: nil)
        XCTAssertEqual(last, .done(sha256: PhotoHash.sha256Hex(photo), photoAlreadyStored: false, recordAccepted: false))
        XCTAssertEqual(server.requests.map(\.method), ["POST"])
        assertHonestProgress(states, photoBytes: photo.count, recordBytes: nil)
    }

    func testSaysThePhotoWasAlreadyStored() async throws {
        let server = try LoopbackHTTPServer { request in
            if request.method == "POST" {
                return .json(200, "{\"sourceAsset\":{\"sha256\":\"\(PhotoHash.sha256Hex(request.body))\"},\"alreadyStored\":true}")
            }
            return Self.laptop(request)
        }
        defer { server.stop() }
        let photo = photoBytes(count: 500, seed: 9)
        let (last, _) = try await send(server, photo: photo, record: try record(for: photo))
        XCTAssertEqual(last, .done(sha256: PhotoHash.sha256Hex(photo), photoAlreadyStored: true, recordAccepted: true))
    }

    func testRefusesARecordForAnotherPhotoBeforeSendingAnything() async throws {
        let server = try LoopbackHTTPServer(responder: Self.laptop)
        defer { server.stop() }
        let photo = photoBytes(count: 500, seed: 10)
        let other = try record(for: photoBytes(count: 500, seed: 11))
        let (last, states) = try await send(server, photo: photo, record: other)
        XCTAssertEqual(last, .failed(.recordNamesAnotherPhoto))
        XCTAssertEqual(states, [.waiting, .failed(.recordNamesAnotherPhoto)])
        XCTAssertEqual(server.requests.count, 0)
    }

    func testRefusesARecordTheContractRefusesBeforeSendingAnything() async throws {
        let server = try LoopbackHTTPServer(responder: Self.laptop)
        defer { server.stop() }
        let photo = photoBytes(count: 500, seed: 12)
        var broken = try record(for: photo)
        broken.motion.gravityDeviceG = .init(x: 0, y: 0, z: 9.81)
        let (last, _) = try await send(server, photo: photo, record: broken)
        guard case .failed(.recordNotWritable(let issues)) = last else { return XCTFail("\(last)") }
        XCTAssertEqual(issues.map(\.message), [PointingContract.Rule.gravityNearOneG.words])
        XCTAssertEqual(server.requests.count, 0)
    }

    func testSaysWhatTheLaptopRefusedWithItsStatusAndDetail() async throws {
        let server = try LoopbackHTTPServer { request in
            request.method == "POST" ? .json(415, "{\"detail\":\"declared as image/heic, but the bytes are image/png\"}") : Self.laptop(request)
        }
        defer { server.stop() }
        let photo = photoBytes(count: 500, seed: 13)
        let (last, states) = try await send(server, photo: photo, record: try record(for: photo))
        XCTAssertEqual(last, .failed(.refused(phase: .photo, status: 415, detail: "declared as image/heic, but the bytes are image/png")))
        XCTAssertEqual(states.last, last)
        XCTAssertEqual(server.requests.map(\.method), ["POST"])
    }

    func testSaysTheRecordRouteIsNotOnTheLaptopYet() async throws {
        // Today's laptop: the photo is stored, the pointing route of unit pointing-2 does not exist
        let server = try LoopbackHTTPServer { request in
            request.method == "POST" ? Self.laptop(request) : .json(405, "{\"detail\":\"Method Not Allowed\"}")
        }
        defer { server.stop() }
        let photo = photoBytes(count: 500, seed: 14)
        let (last, _) = try await send(server, photo: photo, record: try record(for: photo))
        XCTAssertEqual(last, .failed(.refused(phase: .record, status: 405, detail: "Method Not Allowed")))
    }

    func testStopsWhenTheReceiptNamesOtherBytes() async throws {
        let wrong = String(repeating: "ab", count: 32)
        let server = try LoopbackHTTPServer { request in
            request.method == "POST" ? .json(201, "{\"sourceAsset\":{\"sha256\":\"\(wrong)\"},\"alreadyStored\":false}") : Self.laptop(request)
        }
        defer { server.stop() }
        let photo = photoBytes(count: 500, seed: 15)
        let (last, _) = try await send(server, photo: photo, record: try record(for: photo))
        XCTAssertEqual(last, .failed(.storedDifferentBytes(receiptSha256: wrong)))
        XCTAssertEqual(server.requests.map(\.method), ["POST"])
    }

    func testSaysAnUnreadableReplyIsUnreadable() async throws {
        let server = try LoopbackHTTPServer { _ in LoopbackHTTPServer.Response(status: 201, body: Data("<html>".utf8)) }
        defer { server.stop() }
        let photo = photoBytes(count: 500, seed: 16)
        let (last, _) = try await send(server, photo: photo, record: nil)
        XCTAssertEqual(last, .failed(.unreadableReply(phase: .photo)))
    }

    func testSaysTheLaptopCouldNotBeReached() async throws {
        let server = try LoopbackHTTPServer(responder: Self.laptop)
        let address = server.baseURL
        server.stop()
        let closed = try XCTUnwrap(LaptopAddress(address))
        let log = StateLog()
        let photo = photoBytes(count: 500, seed: 17)
        let last = await bounded { await CaptureUploader(address: closed, timeoutSec: 5).send(photo: photo, contentType: .jpeg, record: nil) { log.append($0) } }
        // ECONNREFUSED is 61 on Darwin (sys/errno.h)
        guard case .failed(.networkError(phase: .photo, posixCode: 61)) = last else { return XCTFail("\(last)") }
        XCTAssertFalse(log.states.contains { if case .done = $0 { true } else { false } })
    }

    func testTakesOnlyALocalNetworkAddress() {
        for text in ["http://127.0.0.1:8000", "http://localhost:5173", "http://192.168.1.20:5173", "http://10.0.0.2:5173",
                     "http://172.20.10.2:5173", "http://172.16.0.1", "http://169.254.3.4:5173", "http://laptop.local:5173",
                     "http://[::1]:8000", "http://[fe80::1%25en0]:5173", "http://[fd00::2]:5173"] {
            XCTAssertNotNil(LaptopAddress(text), text)
        }
        for text in ["https://192.168.1.20:5173", "http://8.8.8.8:5173", "http://172.32.0.1:5173", "http://172.15.255.1",
                     "http://example.com", "http://laptop.local.example.com", "http://192.168.1.20:5173/api", "http://192.168.1.20:5173?x=1",
                     "http://user:pass@192.168.1.20:5173", "ftp://192.168.1.20", "192.168.1.20:5173", "", "http://[2001:db8::1]:5173",
                     "http://192.168.1:5173", "http://192.168.1.256:5173", "http://192.169.1.1:5173", "http://10.0.0.1.5:80", "http://[::ffff:8.8.8.8]:80", "http://1.1.1.1#x", "http://[fe80::1]:5173"] {
            XCTAssertNil(LaptopAddress(text), text)
        }
    }
}
