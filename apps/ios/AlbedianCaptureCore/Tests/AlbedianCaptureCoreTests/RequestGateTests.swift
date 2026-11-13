import Foundation
import Network
import XCTest
@testable import AlbedianCaptureCore

/// Nothing leaves for anywhere but the paired address, by construction (the lead's ruling for mobile-0's third
/// version). A hostile laptop on 127.0.0.1 answers each request with a steering reply from one table; a second server
/// stands for every other place and counts the connections and bytes it receives. For every row it receives none,
/// and no state says stored or accepted unless the row's reply is a true receipt. Synthetic photo bytes.
final class RequestGateTests: XCTestCase {
    /// A raw TCP server: per connection it reads one request (head, then a Content-Length body), and follows a script.
    final class RawServer: @unchecked Sendable {
        struct Script: Sendable {
            /// Sent as soon as the request's head is whole, before its body is read (an interim reply).
            var early: Data?
            /// Closes the connection once this many body bytes have arrived, without a reply.
            var dropAfterBodyBytes: Int?
            /// The reply to the whole request, as bytes; nil closes without one.
            var reply: @Sendable (_ head: String, _ body: Data) -> Data?
            /// With no reply, or after the last of `pieces`, keeps the connection open instead of closing it.
            var holdOpen = false
            /// The reply sent in these pieces, 50 ms apart, instead of `reply`.
            var pieces: [Data]?
        }

        private let listener: NWListener
        private let queue = DispatchQueue(label: "albedian.raw-server")
        private let lock = NSLock()
        private var connectionCount = 0
        private var byteCount = 0
        private var heads: [String] = []
        /// Connections a script keeps open, held here so they are not closed when released.
        private var held: [NWConnection] = []
        private let script: @Sendable (_ connectionIndex: Int) -> Script
        private(set) var port: UInt16 = 0

        init(script: @escaping @Sendable (_ connectionIndex: Int) -> Script) throws {
            self.script = script
            let parameters = NWParameters.tcp
            parameters.requiredLocalEndpoint = NWEndpoint.hostPort(host: .ipv4(.loopback), port: .any)
            listener = try NWListener(using: parameters)
            let ready = DispatchSemaphore(value: 0)
            listener.stateUpdateHandler = { state in
                switch state {
                case .ready, .failed, .cancelled: ready.signal()
                default: break
                }
            }
            listener.newConnectionHandler = { [weak self] connection in self?.serve(connection) }
            listener.start(queue: queue)
            guard ready.wait(timeout: .now() + 10) == .success else { throw URLError(.timedOut) }
            guard let port = listener.port?.rawValue, port != 0 else { throw URLError(.cannotConnectToHost) }
            self.port = port
        }

        var connections: Int { lock.withLock { connectionCount } }
        var bytes: Int { lock.withLock { byteCount } }
        var requestHeads: [String] { lock.withLock { heads } }
        func stop() {
            listener.cancel()
            for connection in lock.withLock({ held }) { connection.cancel() }
        }

        private func serve(_ connection: NWConnection) {
            let index: Int = lock.withLock { connectionCount += 1; return connectionCount - 1 }
            let plan = script(index)
            connection.start(queue: queue)
            read(connection, plan: plan, buffer: Data(), earlySent: false)
        }

        private func read(_ connection: NWConnection, plan: Script, buffer: Data, earlySent: Bool) {
            connection.receive(minimumIncompleteLength: 1, maximumLength: 1 << 20) { [weak self] data, _, isComplete, error in
                guard let self else { return }
                var buffer = buffer
                if let data {
                    buffer.append(data)
                    self.lock.withLock { self.byteCount += data.count }
                }
                guard let end = buffer.range(of: Data("\r\n\r\n".utf8)) else {
                    if isComplete || error != nil { connection.cancel() } else { self.read(connection, plan: plan, buffer: buffer, earlySent: earlySent) }
                    return
                }
                let head = String(decoding: buffer[..<end.lowerBound], as: UTF8.self)
                var sent = earlySent
                if !sent, let early = plan.early {
                    connection.send(content: early, completion: .contentProcessed { _ in })
                    sent = true
                }
                let length = head.components(separatedBy: "\r\n")
                    .first { $0.lowercased().hasPrefix("content-length:") }
                    .flatMap { Int($0.split(separator: ":")[1].trimmingCharacters(in: .whitespaces)) } ?? 0
                let body = buffer[end.upperBound...]
                if let drop = plan.dropAfterBodyBytes, body.count >= drop {
                    // Reading stops here, so a long body stalls; an interim reply is given time to arrive before the drop
                    self.lock.withLock { self.heads.append(head) }
                    if plan.early == nil { return connection.cancel() }
                    return self.queue.asyncAfter(deadline: .now() + 0.3) { connection.cancel() }
                }
                guard body.count >= length else {
                    if isComplete || error != nil { connection.cancel() } else { self.read(connection, plan: plan, buffer: buffer, earlySent: sent) }
                    return
                }
                self.lock.withLock { self.heads.append(head) }
                if let pieces = plan.pieces {
                    for (index, piece) in pieces.enumerated() {
                        self.queue.asyncAfter(deadline: .now() + 0.05 * Double(index)) {
                            connection.send(content: piece, completion: .contentProcessed { _ in
                                guard index == pieces.count - 1 else { return }
                                if plan.holdOpen { self.lock.withLock { self.held.append(connection) } } else { connection.cancel() }
                            })
                        }
                    }
                    return
                }
                guard let reply = plan.reply(head, Data(body.prefix(length))) else {
                    if plan.holdOpen { return self.lock.withLock { self.held.append(connection) } }
                    return connection.cancel()
                }
                connection.send(content: reply, completion: .contentProcessed { _ in connection.cancel() })
            }
        }
    }

    private let photo = Data("synthetic photo bytes for the hostile laptop rows".utf8)
    private var sha: String { PhotoHash.sha256Hex(photo) }

    private static func raw(_ status: String, _ headers: [String] = [], body: String = "") -> Data {
        var text = "HTTP/1.1 \(status)\r\n"
        for header in headers { text += header + "\r\n" }
        text += "Content-Length: \(Data(body.utf8).count)\r\n\r\n" + body
        return Data(text.utf8)
    }

    private static func receipt(_ sha: String) -> Data {
        raw("201 Created", body: "{\"sourceAsset\":{\"sha256\":\"\(sha)\"},\"alreadyStored\":false}")
    }

    private static func recordReceipt(_ sha: String) -> Data { raw("201 Created", body: "{\"sourceAssetSha256\":\"\(sha)\"}") }

    /// One row: what the laptop does on the photo's connection and on the record's, and the state that must end it.
    struct Row {
        let name: String
        let photo: RawServer.Script
        let record: RawServer.Script?
        let expected: UploadState
    }

    private func normalPhoto() -> RawServer.Script {
        let sha = self.sha
        return RawServer.Script(reply: { _, _ in Self.receipt(sha) })
    }

    private func rows(otherPort: UInt16) -> [Row] {
        let sha = self.sha
        func hostile(_ phase: UploadPhase, _ script: RawServer.Script, _ expected: UploadState, _ name: String) -> Row {
            phase == .photo
                ? Row(name: "photo: " + name, photo: script, record: nil, expected: expected)
                : Row(name: "record: " + name, photo: normalPhoto(), record: script, expected: expected)
        }
        func fixed(_ bytes: Data) -> RawServer.Script { RawServer.Script(reply: { _, _ in bytes }) }
        // "paired" is the paired address itself, read from the request's own Host line when the reply is written
        let elsewhere = ["http://127.1:\(otherPort)/api/ingest", "http://127.0.0.1:\(otherPort)/api/ingest", "/api/ingest", "paired"]
        var rows: [Row] = []
        for phase in [UploadPhase.photo, .record] {
            // Every 3xx, with a Location to a refused host, an allowed one, a relative one and the paired address
            for status in 300...308 {
                for location in elsewhere {
                    let script = RawServer.Script(reply: { head, _ in
                        let host = head.components(separatedBy: "\r\n").first { $0.hasPrefix("Host: ") }.map { String($0.dropFirst(6)) } ?? ""
                        return Self.raw("\(status) Moved", ["Location: " + (location == "paired" ? "http://\(host)/api/ingest" : location)])
                    })
                    rows.append(hostile(phase, script, .failed(.redirected(phase: phase, status: status)), "\(status) to \(location)"))
                }
            }
            rows.append(hostile(phase, fixed(Self.raw("399 Odd")), .failed(.redirected(phase: phase, status: 399)), "399 with no Location"))
            // 104 Upload Resumption Supported with a Location elsewhere, the connection dropped at several offsets
            let resume = Data("HTTP/1.1 104 Upload Resumption Supported\r\nLocation: http://127.1:\(otherPort)/resume/1\r\nUpload-Draft-Interop-Version: 6\r\n\r\n".utf8)
            for offset in [0, 1, 10] {
                rows.append(hostile(phase, RawServer.Script(early: resume, dropAfterBodyBytes: offset, reply: { _, _ in nil }),
                                    .failed(.connectionClosed(phase: phase)), "104 then dropped after \(offset) body bytes"))
            }
            let final = phase == .photo ? Self.receipt(sha) : Self.recordReceipt(sha)
            let done = UploadState.done(sha256: sha, photoAlreadyStored: false, recordAccepted: phase == .record)
            rows.append(hostile(phase, RawServer.Script(early: resume, reply: { _, _ in final }), done, "104 read past, then the receipt"))
            rows.append(hostile(phase, fixed(resume + final), done, "104 in the reply, then the receipt"))
            // Other interim replies, read past and never acted on
            rows.append(hostile(phase, fixed(Data("HTTP/1.1 100 Continue\r\n\r\n".utf8) + final), done, "100 then the receipt"))
            rows.append(hostile(phase, fixed(Data("HTTP/1.1 103 Early Hints\r\nLink: <http://127.1:\(otherPort)/x>; rel=preload\r\nLink: </y>; rel=preconnect\r\n\r\n".utf8) + final),
                                done, "103 with Link headers, then the receipt"))
            rows.append(hostile(phase, fixed(Data(String(repeating: "HTTP/1.1 100 Continue\r\n\r\n", count: 9).utf8) + final),
                                .failed(.unreadableReply(phase: phase)), "nine interim replies"))
            rows.append(hostile(phase, fixed(Data("HTTP/1.1 101 Switching Protocols\r\nUpgrade: h2c\r\n\r\n".utf8) + final),
                                .failed(.unreadableReply(phase: phase)), "101"))
            // Challenges: answered by no credential and no second request
            for challenge in ["Basic realm=\"laptop\"", "Digest realm=\"laptop\", nonce=\"1\"", "Bearer", "Negotiate", "NTLM"] {
                rows.append(hostile(phase, fixed(Self.raw("401 Unauthorized", ["WWW-Authenticate: \(challenge)"], body: "{\"detail\":\"Not authenticated\"}")),
                                    .failed(.refused(phase: phase, status: 401, detail: "Not authenticated")), "401 \(challenge)"))
                rows.append(hostile(phase, fixed(Self.raw("407 Proxy Authentication Required", ["Proxy-Authenticate: \(challenge)"])),
                                    .failed(.refused(phase: phase, status: 407, detail: nil)), "407 \(challenge)"))
            }
            rows.append(hostile(phase, fixed(Self.raw("421 Misdirected Request")), .failed(.refused(phase: phase, status: 421, detail: nil)), "421"))
            // Steering headers on a true receipt: none is acted on
            for header in ["Alt-Svc: h2=\"127.0.0.1:\(otherPort)\", h3=\":\(otherPort)\"", "Refresh: 0; url=http://127.1:\(otherPort)/",
                           "Location: http://127.1:\(otherPort)/stored", "Content-Location: http://127.1:\(otherPort)/stored",
                           "Set-Cookie: session=1; Path=/", "Upgrade: h2c"] {
                let body = phase == .photo ? "{\"sourceAsset\":{\"sha256\":\"\(sha)\"},\"alreadyStored\":false}" : "{\"sourceAssetSha256\":\"\(sha)\"}"
                rows.append(hostile(phase, fixed(Self.raw("201 Created", [header], body: body)), done, "receipt with \(header.prefix(16))"))
            }
            // Replies the reader cannot take exactly
            let bodyText = phase == .photo ? "{\"sourceAsset\":{\"sha256\":\"\(sha)\"},\"alreadyStored\":false}" : "{\"sourceAssetSha256\":\"\(sha)\"}"
            let unreadable = UploadState.failed(.unreadableReply(phase: phase))
            let closed = UploadState.failed(.connectionClosed(phase: phase))
            let malformed: [(String, Data, UploadState)] = [
                ("no length", Data("HTTP/1.1 201 Created\r\n\r\n\(bodyText)".utf8), unreadable),
                ("a length past the body", Data("HTTP/1.1 201 Created\r\nContent-Length: 500\r\n\r\n\(bodyText)".utf8), closed),
                ("a length short of the body", Data("HTTP/1.1 201 Created\r\nContent-Length: 10\r\n\r\n\(bodyText)".utf8), unreadable),
                ("two lengths", Data("HTTP/1.1 201 Created\r\nContent-Length: \(bodyText.utf8.count)\r\nContent-Length: 1\r\n\r\n\(bodyText)".utf8), unreadable),
                ("a signed length", Data("HTTP/1.1 201 Created\r\nContent-Length: +\(bodyText.utf8.count)\r\n\r\n\(bodyText)".utf8), unreadable),
                ("chunked", Data("HTTP/1.1 201 Created\r\nTransfer-Encoding: chunked\r\n\r\n10\r\n0123456789abcdef\r\n0\r\n\r\n".utf8), unreadable),
                ("an oversized header block", Self.raw("201 Created", ["X-Long: " + String(repeating: "a", count: 9000)], body: bodyText), unreadable),
                ("too many headers", Self.raw("201 Created", (0...64).map { "X-\($0): 1" }, body: bodyText), unreadable),
                ("a folded header", Data("HTTP/1.1 201 Created\r\nX-A: 1\r\n  2\r\nContent-Length: \(bodyText.utf8.count)\r\n\r\n\(bodyText)".utf8), unreadable),
                ("line feeds only", Data("HTTP/1.1 201 Created\nContent-Length: \(bodyText.utf8.count)\n\n\(bodyText)".utf8), unreadable),
                ("a status line that is not HTTP", Data("HTTX/1.1 201 Created\r\nContent-Length: 0\r\n\r\n".utf8), unreadable),
                ("HTTP/2 in the status line", Data("HTTP/2 201 Created\r\nContent-Length: 0\r\n\r\n".utf8), unreadable),
                ("status 600", Self.raw("600 Odd"), unreadable),
                ("status 099", Self.raw("099 Odd"), unreadable),
                ("no reason space", Data("HTTP/1.1 201\r\nContent-Length: \(bodyText.utf8.count)\r\n\r\n\(bodyText)".utf8), unreadable),
                ("closed before any reply", Data(), closed),
                ("closed after the headers", Data("HTTP/1.1 201 Created\r\nContent-Length: \(bodyText.utf8.count)\r\n\r\n".utf8), closed),
                ("closed mid body", Data("HTTP/1.1 201 Created\r\nContent-Length: \(bodyText.utf8.count)\r\n\r\n\(bodyText.prefix(5))".utf8), closed),
                ("closed mid status line", Data("HTTP/1.1 20".utf8), closed),
                ("a receipt for other bytes", phase == .photo ? Self.receipt(String(repeating: "ab", count: 32)) : Self.recordReceipt(String(repeating: "ab", count: 32)),
                 phase == .photo ? .failed(.storedDifferentBytes(receiptSha256: String(repeating: "ab", count: 32))) : unreadable),
                ("an empty receipt", Self.raw("201 Created", body: "{}"), unreadable),
                ("an empty body", Self.raw("201 Created"), unreadable),
                ("a receipt that is not an object", Self.raw("201 Created", body: "[\"\(sha)\"]"), unreadable),
                ("a receipt with extra keys", phase == .photo
                    ? Self.raw("201 Created", body: "{\"sourceAsset\":{\"sha256\":\"\(sha)\",\"x\":1},\"alreadyStored\":false,\"y\":[1]}")
                    : Self.raw("201 Created", body: "{\"sourceAssetSha256\":\"\(sha)\",\"storedAt\":\"x\",\"y\":[1]}"), done),
                ("a receipt naming the hash in upper case", phase == .photo ? Self.receipt(sha.uppercased()) : Self.recordReceipt(sha.uppercased()),
                 phase == .photo ? .failed(.storedDifferentBytes(receiptSha256: sha.uppercased())) : unreadable),
                // A receipt for one request never counts for the other, though it names the right hash
                ("the other request's receipt", phase == .photo ? Self.recordReceipt(sha) : Self.receipt(sha), unreadable),
            ]
            for (name, bytes, expected) in malformed {
                rows.append(hostile(phase, RawServer.Script(reply: { _, _ in bytes.isEmpty ? nil : bytes }), expected, name))
            }
        }
        return rows
    }

    func testSendsNothingAnywhereButThePairedAddressForEveryHostileReply() async throws {
        let other = try RawServer { _ in RawServer.Script(reply: { _, _ in Self.raw("200 OK", body: "{}") }) }
        defer { other.stop() }
        XCTAssertNil(LaptopAddress("http://127.1:\(other.port)"))
        let record = try PointingRecordBuilder.build(SampleCapture.capture(photoSha256: sha))
        let table = rows(otherPort: other.port)
        XCTAssertGreaterThanOrEqual(table.count, 160)
        var wrong: [String] = []
        for row in table {
            let laptop = try RawServer { index in index == 0 ? row.photo : (row.record ?? row.photo) }
            defer { laptop.stop() }
            let address = try XCTUnwrap(LaptopAddress("http://127.0.0.1:\(laptop.port)"))
            final class Log: @unchecked Sendable {
                let lock = NSLock(); var states: [UploadState] = []
                func add(_ state: UploadState) { lock.withLock { states.append(state) } }
            }
            let log = Log()
            let sendRecord = row.record == nil ? nil : record
            let photo = self.photo
            let last = await bounded { await CaptureUploader(address: address, timeoutSec: 5).send(photo: photo, contentType: .heic, record: sendRecord) { log.add($0) } }
            if last == .waiting {
                wrong.append("\(row.name): did not end")
                break
            }
            if last != row.expected { wrong.append("\(row.name): \(last)") }
            let doneStates = log.states.filter { if case .done = $0 { true } else { false } }
            if case .done = row.expected {} else if !doneStates.isEmpty { wrong.append("\(row.name): a done state") }
            if laptop.connections > (row.record == nil ? 1 : 2) { wrong.append("\(row.name): \(laptop.connections) connections to the laptop") }
        }
        XCTAssertEqual(wrong, [])
        XCTAssertEqual(other.connections, 0, "connections to another place")
        XCTAssertEqual(other.bytes, 0, "bytes to another place")
    }

    func testSendsNoCookieAndUsesNoAlternativeServiceOnTheNextSend() async throws {
        let photo = self.photo
        let other = try RawServer { _ in RawServer.Script(reply: { _, _ in Self.raw("200 OK", body: "{}") }) }
        defer { other.stop() }
        let sha = self.sha
        let laptop = try RawServer { _ in
            RawServer.Script(reply: { _, _ in
                Self.raw("201 Created", ["Set-Cookie: session=1; Path=/", "Alt-Svc: h2=\"127.0.0.1:\(other.port)\"; ma=3600"],
                         body: "{\"sourceAsset\":{\"sha256\":\"\(sha)\"},\"alreadyStored\":false}")
            })
        }
        defer { laptop.stop() }
        let uploader = CaptureUploader(address: try XCTUnwrap(LaptopAddress("http://127.0.0.1:\(laptop.port)")), timeoutSec: 5)
        for _ in 0..<2 {
            let last = await bounded { await uploader.send(photo: photo, contentType: .heic, record: nil) { _ in } }
            XCTAssertEqual(last, .done(sha256: sha, photoAlreadyStored: false, recordAccepted: false))
        }
        XCTAssertEqual(laptop.connections, 2)
        XCTAssertFalse(laptop.requestHeads.joined().lowercased().contains("cookie"))
        XCTAssertEqual(other.connections, 0)
        // Each request is exactly the one this uploader writes: its line and five headers, nothing the stack adds
        let lines = try XCTUnwrap(laptop.requestHeads.first).components(separatedBy: "\r\n")
        XCTAssertEqual(lines[0], "POST /api/ingest HTTP/1.1")
        XCTAssertEqual(lines.dropFirst().map { $0.split(separator: ":")[0] }, ["Host", "User-Agent", "Content-Type", "Content-Length", "Connection"])
    }

    func testLetsThroughOnlyTheTwoRequestsAndOnlyToThePairedEndpoint() throws {
        let address = try XCTUnwrap(LaptopAddress("http://192.168.1.20:5173"))
        XCTAssertEqual(address.endpoint, LaptopEndpoint(host: "192.168.1.20", port: 5173))
        let sha = self.sha
        XCTAssertEqual(CaptureUploader.permits(method: "POST", path: "/api/ingest", to: address),
                       ApprovedRequest(endpoint: address.endpoint, method: "POST", path: "/api/ingest"))
        XCTAssertEqual(CaptureUploader.permits(method: "PUT", path: "/api/ingest/\(sha)/pointing", to: address)?.endpoint, address.endpoint)
        let refused: [(String, String)] = [
            ("POST", "/api/ingest/"), ("POST", "/api/other"), ("POST", "api/ingest"), ("POST", "//api/ingest"), ("GET", "/api/ingest"),
            ("HEAD", "/api/ingest"), ("PATCH", "/api/ingest"), ("PUT", "/api/ingest"), ("post", "/api/ingest"),
            ("POST", "/api/ingest/\(sha)/pointing"), ("DELETE", "/api/ingest/\(sha)/pointing"), ("PUT", "/api/ingest/\(sha)/other"),
            ("PUT", "/api/ingest/\(sha.uppercased())/pointing"), ("PUT", "/api/ingest/\(sha)/pointing/x"), ("PUT", "/x/ingest/\(sha)/pointing"),
            ("PUT", "/api/x/\(sha)/pointing"), ("PUT", "x/api/ingest/\(sha)/pointing"), ("PUT", "/api/ingest/\(String(repeating: "0", count: 64))/pointing"),
            ("POST", "/api/ingest?x=1"), ("POST", "/api/ingest#x"), ("POST", "http://127.1/api/ingest"),
        ]
        for (method, path) in refused { XCTAssertNil(CaptureUploader.permits(method: method, path: path, to: address), "\(method) \(path)") }
    }

    func testSendsNotOneByteWhenTheConnectedPeerIsNotThePairedOne() async throws {
        let photo = self.photo
        // The loopback peer is the paired one, so a stand-in check that refuses stands for any other peer. It takes
        // 0.3 s before it answers, so a byte handed to the connection before the check would reach the laptop meanwhile
        let laptop = try RawServer { _ in RawServer.Script(reply: { _, _ in Self.receipt("x") }) }
        defer { laptop.stop() }
        let address = try XCTUnwrap(LaptopAddress("http://127.0.0.1:\(laptop.port)"))
        final class Seen: @unchecked Sendable { let lock = NSLock(); var bytesWhenAsked: [Int] = []; var sentWhenAnswered: [Int64] = [] }
        let seen = Seen()
        let refusing = NetworkLaptopTransport(peerCheck: { _, _ in
            seen.lock.withLock { seen.bytesWhenAsked.append(laptop.bytes) }
            Thread.sleep(forTimeInterval: 0.3)
            seen.lock.withLock { seen.bytesWhenAsked.append(laptop.bytes) }
            return false
        })
        let last = await bounded {
            await CaptureUploader(address: address, timeoutSec: 5, transport: refusing).send(photo: photo, contentType: .heic, record: nil) { state in
                if case .sending(_, let sent, _) = state { seen.lock.withLock { seen.sentWhenAnswered.append(sent) } }
            }
        }
        XCTAssertEqual(last, .failed(.notThePairedPeer(phase: .photo)))
        // Given time for any byte in flight to arrive: none was written, before the check, during it or after it
        try await Task.sleep(nanoseconds: 500_000_000)
        XCTAssertEqual(laptop.connections, 1)
        XCTAssertEqual(laptop.bytes, 0)
        XCTAssertEqual(seen.lock.withLock { seen.bytesWhenAsked }, [0, 0])
        XCTAssertEqual(seen.lock.withLock { seen.sentWhenAnswered }, [0], "only the state before any byte")
        // The app's own check is the one that takes the paired peer
        let real = NetworkLaptopTransport()
        XCTAssertTrue(real.peerCheck(.hostPort(host: "127.0.0.1", port: 8000), LaptopEndpoint(host: "127.0.0.1", port: 8000)))
        XCTAssertFalse(real.peerCheck(.hostPort(host: "127.0.0.1", port: 8000), LaptopEndpoint(host: "127.0.0.1", port: 8001)))
        XCTAssertFalse(real.peerCheck(.hostPort(host: "127.0.0.2", port: 8000), LaptopEndpoint(host: "127.0.0.1", port: 8000)))
    }

    func testChecksThePeerTheConnectionReachedNotTheNameItWasGiven() async throws {
        // Sent by name to this machine: the check is given the address the connection reached, never the name
        let laptop = try RawServer { _ in RawServer.Script(reply: { _, _ in Self.raw("201 Created", body: "{}") }) }
        defer { laptop.stop() }
        final class Seen: @unchecked Sendable { let lock = NSLock(); var remotes: [NWEndpoint?] = []; var paired: [LaptopEndpoint] = [] }
        let seen = Seen()
        let recording = NetworkLaptopTransport(peerCheck: { remote, paired in
            seen.lock.withLock { seen.remotes.append(remote); seen.paired.append(paired) }
            return true
        })
        let endpoint = LaptopEndpoint(host: "localhost", port: laptop.port)
        let request = HTTPMessage.request(method: "POST", path: "/api/ingest", endpoint: endpoint, contentType: "image/heic", body: Data("x".utf8))
        let result = await within(20, otherwise: Result<HTTPReply, ExchangeFailure>.failure(.closed)) {
            await recording.exchange(request, to: endpoint, timeoutSec: 5) { _ in }
        }
        XCTAssertEqual(result, .success(HTTPReply(status: 201, headers: [(name: "content-length", value: "2")], body: Data("{}".utf8))))
        let remotes = seen.lock.withLock { seen.remotes }
        XCTAssertEqual(remotes.count, 1)
        XCTAssertEqual(seen.lock.withLock { seen.paired }, [endpoint])
        guard case .hostPort(.ipv4(let address), let port)?? = remotes.first else { return XCTFail("not an IPv4 address: \(remotes)") }
        XCTAssertEqual(Array(address.rawValue), [127, 0, 0, 1])
        XCTAssertEqual(port.rawValue, laptop.port)
        // The app's own check takes that peer for a pairing by name, and not for a pairing by another address
        XCTAssertTrue(NetworkLaptopTransport.isPairedPeer(remotes[0], paired: endpoint))
        XCTAssertFalse(NetworkLaptopTransport.isPairedPeer(remotes[0], paired: LaptopEndpoint(host: "127.0.0.2", port: laptop.port)))
    }

    private static func bigPhoto() -> Data {
        var bytes = Data(count: 3 * 1024 * 1024)
        for index in stride(from: 0, to: bytes.count, by: 4096) { bytes[index] = UInt8(index / 4096 % 251) }
        return bytes
    }

    func testCountsNoReceiptThatCameBeforeTheWholeBodyWasTaken() async throws {
        // A 2xx receipt sent as soon as the head arrives, then the connection closed while the 3 MB body still goes out
        let bytes = Self.bigPhoto()
        for status in ["201 Created", "200 OK", "299 Odd"] {
            let early = Self.raw(status, body: "{\"sourceAsset\":{\"sha256\":\"\(PhotoHash.sha256Hex(bytes))\"},\"alreadyStored\":false}")
            let laptop = try RawServer { _ in RawServer.Script(early: early, dropAfterBodyBytes: 0, reply: { _, _ in nil }) }
            defer { laptop.stop() }
            let address = try XCTUnwrap(LaptopAddress("http://127.0.0.1:\(laptop.port)"))
            let last = await bounded { await CaptureUploader(address: address, timeoutSec: 10).send(photo: bytes, contentType: .heic, record: nil) { _ in } }
            XCTAssertEqual(last, .failed(.connectionClosed(phase: .photo)), status)
        }
    }

    func testCountsAnEarlyReceiptOnceTheWholeBodyIsTaken() async throws {
        // The same early receipt, but the laptop reads the whole body and keeps the connection open: once the last byte
        // is taken the receipt counts
        let bytes = Self.bigPhoto()
        let sha = PhotoHash.sha256Hex(bytes)
        let laptop = try RawServer { _ in RawServer.Script(early: Self.receipt(sha), reply: { _, _ in nil }, holdOpen: true) }
        defer { laptop.stop() }
        let address = try XCTUnwrap(LaptopAddress("http://127.0.0.1:\(laptop.port)"))
        let last = await bounded { await CaptureUploader(address: address, timeoutSec: 10).send(photo: bytes, contentType: .heic, record: nil) { _ in } }
        XCTAssertEqual(last, .done(sha256: sha, photoAlreadyStored: false, recordAccepted: false))
    }

    func testReadsAReplyThatArrivesInPieces() async throws {
        let sha = self.sha
        let photo = self.photo
        let whole = Self.receipt(sha)
        let cut = [0, 7, 20, whole.count - 3, whole.count]
        let pieces = zip(cut, cut.dropFirst()).map { whole.subdata(in: $0.0..<$0.1) }
        let laptop = try RawServer { _ in RawServer.Script(reply: { _, _ in nil }, pieces: pieces) }
        defer { laptop.stop() }
        let address = try XCTUnwrap(LaptopAddress("http://127.0.0.1:\(laptop.port)"))
        let last = await bounded { await CaptureUploader(address: address, timeoutSec: 5).send(photo: photo, contentType: .heic, record: nil) { _ in } }
        XCTAssertEqual(last, .done(sha256: sha, photoAlreadyStored: false, recordAccepted: false))
    }

    func testReadsAReplyWhoseLastByteArrivesAloneOnAConnectionHeldOpen() async throws {
        // A reply is read as soon as its last byte is in, one byte or more, with no close to mark its end
        let sha = self.sha
        let photo = self.photo
        let whole = Self.receipt(sha)
        let pieces = [whole.prefix(whole.count - 1), whole.suffix(1)].map { Data($0) }
        let laptop = try RawServer { _ in RawServer.Script(reply: { _, _ in nil }, holdOpen: true, pieces: pieces) }
        defer { laptop.stop() }
        let address = try XCTUnwrap(LaptopAddress("http://127.0.0.1:\(laptop.port)"))
        let last = await bounded { await CaptureUploader(address: address, timeoutSec: 5).send(photo: photo, contentType: .heic, record: nil) { _ in } }
        XCTAssertEqual(last, .done(sha256: sha, photoAlreadyStored: false, recordAccepted: false))
    }

    func testHandsTheConnectionTheRequestIn64KiBStepsAndCountsEach() async throws {
        let request = Data(count: 200_000)
        let laptop = try RawServer { _ in RawServer.Script(reply: { _, _ in Self.raw("201 Created", body: "{}") }) }
        defer { laptop.stop() }
        XCTAssertEqual(NetworkLaptopTransport.chunkBytes, 65536)
        final class Counts: @unchecked Sendable { let lock = NSLock(); var values: [Int64] = [] }
        let counts = Counts()
        // A request of zeros has no head the server can read; it only takes the bytes, so the reply never comes and
        // the exchange ends by its own short time limit after every byte was handed over
        let port = laptop.port
        let result = await within(20, otherwise: Result<HTTPReply, ExchangeFailure>.failure(.closed)) {
            await NetworkLaptopTransport().exchange(request, to: LaptopEndpoint(host: "127.0.0.1", port: port), timeoutSec: 2) { sent in
                counts.lock.withLock { counts.values.append(sent) }
            }
        }
        XCTAssertEqual(result, .failure(.timedOut))
        XCTAssertEqual(counts.lock.withLock { counts.values }, [65536, 131072, 196608, 200000])
    }

    func testEndsAnExchangeOnceEvenWhenItsTimeLimitPassesAfterIt() async throws {
        let sha = self.sha
        let photo = self.photo
        let laptop = try RawServer { _ in RawServer.Script(reply: { _, _ in Self.receipt(sha) }) }
        defer { laptop.stop() }
        let address = try XCTUnwrap(LaptopAddress("http://127.0.0.1:\(laptop.port)"))
        let last = await bounded { await CaptureUploader(address: address, timeoutSec: 0.5).send(photo: photo, contentType: .heic, record: nil) { _ in } }
        XCTAssertEqual(last, .done(sha256: sha, photoAlreadyStored: false, recordAccepted: false))
        // The limit's own callback comes after the end and must end nothing (a second resume would trap): by then the
        // exchange is released (its timer, handlers and receive hold it weakly), and its one-end guard stands for a
        // callback that runs while it is still held
        try await Task.sleep(nanoseconds: 1_000_000_000)
    }

    func testStopsWaitingForALaptopThatNeverAnswers() async throws {
        let photo = self.photo
        let laptop = try RawServer { _ in RawServer.Script(dropAfterBodyBytes: nil, reply: { _, _ in nil }, holdOpen: true) }
        defer { laptop.stop() }
        let address = try XCTUnwrap(LaptopAddress("http://127.0.0.1:\(laptop.port)"))
        let started = Date()
        let last = await bounded { await CaptureUploader(address: address, timeoutSec: 1).send(photo: photo, contentType: .heic, record: nil) { _ in } }
        let waited = Date().timeIntervalSince(started)
        XCTAssertEqual(last, .failed(.timedOut(phase: .photo)))
        // It ends at its limit: not before, and not at some multiple of it
        XCTAssertGreaterThanOrEqual(waited, 1.0)
        XCTAssertLessThan(waited, 2.5)
    }

    func testTakesAsThePeerOnlyThePairedAddressAtThePairedPortOrForANameAnyLocalAddress() throws {
        func peer(_ host: String, _ port: UInt16 = 8000) -> NWEndpoint { .hostPort(host: NWEndpoint.Host(host), port: NWEndpoint.Port(rawValue: port)!) }
        func paired(_ host: String, _ port: UInt16 = 8000) -> LaptopEndpoint { LaptopEndpoint(host: host, port: port) }
        let check = NetworkLaptopTransport.isPairedPeer
        // Paired by an address: that address, at that port, and nothing else on the network
        XCTAssertTrue(check(peer("192.168.1.20", 5173), paired("192.168.1.20", 5173)))
        XCTAssertFalse(check(peer("192.168.1.99", 5173), paired("192.168.1.20", 5173)))
        XCTAssertFalse(check(peer("192.168.1.20", 5174), paired("192.168.1.20", 5173)))
        XCTAssertTrue(check(peer("127.0.0.1"), paired("127.0.0.1")))
        XCTAssertFalse(check(peer("127.0.0.2"), paired("127.0.0.1")))
        XCTAssertFalse(check(peer("::1"), paired("127.0.0.1")))
        XCTAssertFalse(check(peer("127.0.0.1"), paired("::1")))
        XCTAssertTrue(check(peer("::1"), paired("::1")))
        XCTAssertTrue(check(peer("fd00::1"), paired("FD00:0:0::1")))
        XCTAssertFalse(check(peer("fd00::2"), paired("fd00::1")))
        // The same IPv4 address written as IPv6 by the system is itself; an interface or zone on the peer is not compared
        XCTAssertTrue(check(peer("::ffff:192.168.1.20"), paired("192.168.1.20")))
        XCTAssertFalse(check(peer("::ffff:192.168.1.21"), paired("192.168.1.20")))
        let mapped = try XCTUnwrap(IPv6Address("::ffff:192.168.1.20"))
        XCTAssertEqual(Array(mapped.rawValue).count, 16)
        XCTAssertTrue(check(.hostPort(host: .ipv6(mapped), port: 8000), paired("192.168.1.20")))
        XCTAssertFalse(check(.hostPort(host: .ipv6(mapped), port: 8000), paired("192.168.1.21")))
        // Only the ten zero bytes and then ff ff make an IPv4 address written as IPv6
        for (index, value) in [(9, UInt8(1)), (0, 1), (10, 0), (11, 0)] {
            var bytes = [UInt8](repeating: 0, count: 10) + [0xFF, 0xFF, 192, 168, 1, 20]
            bytes[index] = value
            let near = try XCTUnwrap(IPv6Address(Data(bytes)))
            XCTAssertFalse(check(.hostPort(host: .ipv6(near), port: 8000), paired("192.168.1.20")), "\(bytes)")
        }
        let carried = try XCTUnwrap(IPv4Address("127.0.0.1%lo0"))
        XCTAssertTrue(check(.hostPort(host: .ipv4(carried), port: 8000), paired("127.0.0.1")))
        XCTAssertTrue(check(peer("fe80::1%lo0"), paired("fe80::1%lo0")))
        XCTAssertTrue(check(peer("fe80::1"), paired("fe80::1%en0")))
        XCTAssertFalse(check(peer("fe80::2%lo0"), paired("fe80::1%lo0")))
        // Paired by a name: any address on the local network at the port, which is all a name promises
        for host in ["127.0.0.1", "::1", "192.168.1.5", "10.0.0.9", "fe80::1%lo0", "fd12::3"] {
            XCTAssertTrue(check(peer(host), paired("localhost")), host)
            XCTAssertTrue(check(peer(host), paired("laptop.local")), host)
        }
        for host in ["8.8.8.8", "2001:db8::1", "::ffff:8.8.8.8"] { XCTAssertFalse(check(peer(host), paired("localhost")), host) }
        XCTAssertFalse(check(peer("127.0.0.1", 8001), paired("localhost")))
        // A peer that is not an address at all, or none
        XCTAssertFalse(check(.hostPort(host: .name("laptop.local", nil), port: 8000), paired("laptop.local")))
        XCTAssertFalse(check(.hostPort(host: .name("localhost", nil), port: 8000), paired("localhost")))
        XCTAssertFalse(check(.service(name: "a", type: "_http._tcp", domain: "local", interface: nil), paired("laptop.local")))
        XCTAssertFalse(check(nil, paired("127.0.0.1")))
    }
}
