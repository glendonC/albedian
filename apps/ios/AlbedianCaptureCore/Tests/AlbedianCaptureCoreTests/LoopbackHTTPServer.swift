import Foundation
import Network

/// A small HTTP/1.1 server on 127.0.0.1 that a test starts and stops itself: it reads each request whole (headers,
/// then a Content-Length body), keeps it, answers with what the test's responder says, and closes the connection.
/// Nothing leaves the loopback address.
final class LoopbackHTTPServer: @unchecked Sendable {
    struct Request: Sendable {
        let method: String
        let path: String
        /// Header names in lower case.
        let headers: [String: String]
        let body: Data
    }

    struct Response: Sendable {
        let status: Int
        let body: Data
        /// Header lines beyond the fixed ones (a redirect's Location, an authentication challenge).
        var headers: [(name: String, value: String)] = []
        static func json(_ status: Int, _ text: String) -> Response { Response(status: status, body: Data(text.utf8)) }
    }

    typealias Responder = @Sendable (Request) -> Response

    private let listener: NWListener
    private let queue = DispatchQueue(label: "albedian.loopback-http")
    private let lock = NSLock()
    private var received: [Request] = []
    private let responder: Responder
    private let answerDelaySec: Double
    private(set) var port: UInt16 = 0

    /// - Parameter answerDelaySec: how long to hold each answer after the request is whole, so the client has
    ///   reported every byte it sent before the answer arrives.
    init(answerDelaySec: Double = 0, responder: @escaping Responder) throws {
        self.responder = responder
        self.answerDelaySec = answerDelaySec
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

    var baseURL: String { "http://127.0.0.1:\(port)" }

    var requests: [Request] { lock.withLock { received } }

    func stop() { listener.cancel() }

    deinit { listener.cancel() }

    private func serve(_ connection: NWConnection) {
        connection.start(queue: queue)
        read(connection, buffer: Data())
    }

    private func read(_ connection: NWConnection, buffer: Data) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 1 << 20) { [weak self] data, _, isComplete, error in
            guard let self else { return }
            var buffer = buffer
            if let data { buffer.append(data) }
            if let request = Self.parse(buffer) {
                self.lock.withLock { self.received.append(request) }
                let response = self.responder(request)
                self.queue.asyncAfter(deadline: .now() + self.answerDelaySec) { self.answer(connection, response) }
            } else if isComplete || error != nil {
                connection.cancel()
            } else {
                self.read(connection, buffer: buffer)
            }
        }
    }

    private func answer(_ connection: NWConnection, _ response: Response) {
        var head = "HTTP/1.1 \(response.status) Status\r\nContent-Type: application/json\r\n"
        for header in response.headers { head += "\(header.name): \(header.value)\r\n" }
        head += "Content-Length: \(response.body.count)\r\nConnection: close\r\n\r\n"
        connection.send(content: Data(head.utf8) + response.body, completion: .contentProcessed { _ in connection.cancel() })
    }

    /// The request in the buffer once it is whole, else nil.
    private static func parse(_ buffer: Data) -> Request? {
        guard let end = buffer.range(of: Data("\r\n\r\n".utf8)) else { return nil }
        let head = String(decoding: buffer[..<end.lowerBound], as: UTF8.self).components(separatedBy: "\r\n")
        let line = head.first?.split(separator: " ") ?? []
        guard line.count >= 2 else { return nil }
        var headers: [String: String] = [:]
        for field in head.dropFirst() {
            guard let colon = field.firstIndex(of: ":") else { continue }
            headers[field[..<colon].lowercased()] = field[field.index(after: colon)...].trimmingCharacters(in: .whitespaces)
        }
        let length = Int(headers["content-length"] ?? "0") ?? 0
        let body = buffer[end.upperBound...]
        guard body.count >= length else { return nil }
        return Request(method: String(line[0]), path: String(line[1]), headers: headers, body: Data(body.prefix(length)))
    }
}
