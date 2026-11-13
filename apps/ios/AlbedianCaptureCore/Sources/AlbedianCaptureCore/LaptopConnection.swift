import Foundation
import Network

/// The app's connection (the lead's ruling for mobile-0's third version): a TCP connection to the paired laptop's host
/// and port, one request written by this file, one reply read by this file, then closed. The source scan refuses the
/// connection's type and its send and receive calls in any other file, and the check of what was built refuses its
/// creation in any other object. It uses no general HTTP stack, and this file holds no code for a redirect, an upload
/// resumption, an alternative service, a cookie, a cache, a credential or a retry. No proxy is preferred; what
/// happens when the system routes the connection through one is not yet verified [open: the owner's check on a phone].

/// Where a request may go: the paired laptop's host (no brackets for IPv6) and port, taken from `LaptopAddress` once.
public struct LaptopEndpoint: Equatable, Hashable, Sendable {
    public let host: String
    public let port: UInt16
}

/// The bounds a reply is read within, in one table [proposed: far above what the laptop's receipts need, which are
/// under 1 kB, and small enough that a hostile reply cannot grow without end].
public enum HTTPReplyLimits {
    /// The status line, without its line end.
    public static let statusLineMaxBytes = 256
    /// One header line, without its line end.
    public static let headerLineMaxBytes = 8192
    /// All header lines of one reply, line ends included.
    public static let headerBlockMaxBytes = 16384
    public static let headerCountMax = 64
    /// A final reply's body.
    public static let bodyMaxBytes = 65536
    /// Interim (1xx) replies read past before the final one.
    public static let interimRepliesMax = 8
}

/// A final reply: its status, its headers (names in lower case, in order), and its body of the stated length.
public struct HTTPReply: Equatable, Sendable {
    public let status: Int
    public let headers: [(name: String, value: String)]
    public let body: Data

    public static func == (lhs: HTTPReply, rhs: HTTPReply) -> Bool {
        lhs.status == rhs.status && lhs.body == rhs.body
            && lhs.headers.map(\.name) == rhs.headers.map(\.name) && lhs.headers.map(\.value) == rhs.headers.map(\.value)
    }
}

/// Writes the two requests and reads replies, exactly and nothing more (RFC 9112's message syntax, narrowed).
public enum HTTPMessage {
    /// The request's bytes: request line, Host, User-Agent, Content-Type, Content-Length, Connection: close, the body.
    public static func request(method: String, path: String, endpoint: LaptopEndpoint, contentType: String, body: Data) -> Data {
        // An IPv6 zone names one of the phone's own interfaces and is not sent (RFC 9844, section 6)
        let bare = String(endpoint.host.split(separator: "%", maxSplits: 1, omittingEmptySubsequences: false)[0])
        let host = bare.contains(":") ? "[\(bare)]" : bare
        var head = "\(method) \(path) HTTP/1.1\r\n"
        head += "Host: \(host):\(endpoint.port)\r\n"
        head += "User-Agent: \(CaptureUploader.userAgent)\r\n"
        head += "Content-Type: \(contentType)\r\n"
        head += "Content-Length: \(body.count)\r\n"
        head += "Connection: close\r\n\r\n"
        return Data(head.utf8) + body
    }

    public enum ReadOutcome: Equatable, Sendable {
        /// The bytes so far are a correct start; more are needed.
        case needMore
        /// A final reply, read whole; any bytes after its body are not read.
        case reply(HTTPReply)
        /// The bytes are not a reply this reader takes, and why.
        case unreadable(String)
    }

    /// Reads the bytes received so far: any interim (1xx) replies are read past, their headers never acted on, then
    /// one final reply with a Content-Length body. A 3xx's body is not needed: its status is the answer.
    public static func read(_ bytes: Data) -> ReadOutcome {
        let units = [UInt8](bytes)
        var index = 0
        var interim = 0
        while true {
            switch head(units, from: index) {
            case .needMore: return .needMore
            case .unreadable(let why): return .unreadable(why)
            case .head(let status, let headers, let end):
                if (100...199).contains(status) {
                    guard status != 101 else { return .unreadable("a switch of protocol was not asked for") }
                    interim += 1
                    guard interim <= HTTPReplyLimits.interimRepliesMax else { return .unreadable("too many interim replies") }
                    index = end
                    continue
                }
                if headers.contains(where: { $0.name == "transfer-encoding" }) {
                    return .unreadable("a transfer coding is not read")
                }
                if (300...399).contains(status) { return .reply(HTTPReply(status: status, headers: headers, body: Data())) }
                let lengths = headers.filter { $0.name == "content-length" }.map(\.value)
                guard lengths.count == 1, let text = lengths.first, !text.isEmpty, text.count <= 9,
                      text.utf8.allSatisfy({ (0x30...0x39).contains($0) }), let length = Int(text) else {
                    return .unreadable("the reply states no single length")
                }
                guard length <= HTTPReplyLimits.bodyMaxBytes else { return .unreadable("the body is longer than read") }
                guard units.count - end >= length else { return .needMore }
                return .reply(HTTPReply(status: status, headers: headers, body: Data(units[end..<(end + length)])))
            }
        }
    }

    private enum Head {
        case needMore
        case unreadable(String)
        case head(status: Int, headers: [(name: String, value: String)], end: Int)
    }

    /// One status line and its header lines, from `start`.
    private static func head(_ units: [UInt8], from start: Int) -> Head {
        var lines: [ArraySlice<UInt8>] = []
        var index = start
        var lineStart = start
        // Where the header lines start, once the status line has ended
        var headerStart: Int?
        while true {
            // The status line and the header block each have a bound, checked before more is awaited
            let current = index - lineStart
            if lines.isEmpty, current > HTTPReplyLimits.statusLineMaxBytes { return .unreadable("the status line is too long") }
            if !lines.isEmpty, current > HTTPReplyLimits.headerLineMaxBytes { return .unreadable("a header line is too long") }
            if let headerStart, index - headerStart > HTTPReplyLimits.headerBlockMaxBytes { return .unreadable("the header block is too long") }
            guard index < units.count else { return .needMore }
            let unit = units[index]
            if unit == 0x0A { return .unreadable("a line ends without a carriage return") }
            if unit == 0x0D {
                guard index + 1 < units.count else { return .needMore }
                guard units[index + 1] == 0x0A else { return .unreadable("a carriage return without a line feed") }
                let line = units[lineStart..<index]
                index += 2
                if line.isEmpty {
                    guard !lines.isEmpty else { return .unreadable("no status line") }
                    break
                }
                lines.append(line)
                guard lines.count <= HTTPReplyLimits.headerCountMax + 1 else { return .unreadable("too many headers") }
                if headerStart == nil { headerStart = index }
                lineStart = index
                continue
            }
            index += 1
        }
        guard let status = statusCode(lines[0]) else { return .unreadable("the status line is not HTTP/1.1 or HTTP/1.0") }
        var headers: [(name: String, value: String)] = []
        for line in lines.dropFirst() {
            guard let header = header(line) else { return .unreadable("a header line is not a name and a value") }
            headers.append(header)
        }
        return .head(status: status, headers: headers, end: index)
    }

    /// `HTTP/1.1 200 reason` or `HTTP/1.0 200 reason`: a version, a space, three digits from 100 to 599, a space and
    /// a reason of visible characters and spaces (possibly empty).
    static func statusCode(_ line: ArraySlice<UInt8>) -> Int? {
        let units = Array(line)
        // RFC 9112: HTTP-version SP status-code SP [ reason-phrase ]; the second space is required
        guard units.count >= 13 else { return nil }
        guard Array(units[0..<7]) == Array("HTTP/1.".utf8), units[7] == 0x31 || units[7] == 0x30, units[8] == 0x20, units[12] == 0x20 else {
            return nil
        }
        let digits = units[9..<12]
        guard digits.allSatisfy({ (0x30...0x39).contains($0) }) else { return nil }
        let status = digits.reduce(0) { $0 * 10 + Int($1 - 0x30) }
        guard (100...599).contains(status) else { return nil }
        guard units.dropFirst(13).allSatisfy({ $0 == 0x09 || (0x20...0x7E).contains($0) }) else { return nil }
        return status
    }

    /// `name: value`, the name a token (RFC 9110 tchar) in any case, kept in lower case; a line that starts with a
    /// space or tab (an obsolete folded line) is refused.
    static func header(_ line: ArraySlice<UInt8>) -> (name: String, value: String)? {
        guard let colon = line.firstIndex(of: 0x3A), colon > line.startIndex else { return nil }
        let name = line[line.startIndex..<colon]
        let tchar = Set("!#$%&'*+-.^_`|~".utf8)
        guard name.allSatisfy({ (0x30...0x39).contains($0) || (0x41...0x5A).contains($0) || (0x61...0x7A).contains($0) || tchar.contains($0) }) else {
            return nil
        }
        let rest = line[(colon + 1)...]
        guard rest.allSatisfy({ $0 == 0x09 || (0x20...0x7E).contains($0) || $0 >= 0x80 }) else { return nil }
        let value = String(decoding: rest, as: UTF8.self).trimmingCharacters(in: CharacterSet(charactersIn: " \t"))
        return (String(decoding: name, as: UTF8.self).lowercased(), value)
    }
}

/// What one exchange ended in.
public enum ExchangeFailure: Error, Equatable, Sendable {
    /// The connection failed, with the POSIX error's code when it was one (61 is "connection refused").
    case network(posixCode: Int32?)
    /// No whole reply within the time allowed.
    case timedOut
    /// The connection ended before a whole reply came, or before the whole request was sent.
    case closed
    /// The connected peer was not the paired address at the paired port (or, when the pairing holds a name, not an
    /// address on the local network); the connection was closed before a byte was sent.
    case notThePairedPeer
    /// The reply was not one the reader takes, and why.
    case unreadable(String)
}

/// One request to the paired laptop and its reply. The app's transport is `NetworkLaptopTransport`.
public protocol LaptopTransport: Sendable {
    /// Connects to `endpoint` only, writes `request`, reports the bytes the connection has taken, and reads one reply.
    /// A final 2xx counts only once the whole request was taken.
    func exchange(_ request: Data, to endpoint: LaptopEndpoint, timeoutSec: Double,
                  onSent: @escaping @Sendable (Int64) -> Void) async -> Result<HTTPReply, ExchangeFailure>
}

/// The transport on the platform's own connection layer: `NWConnection` over TCP to the paired endpoint, proxies not
/// preferred (`preferNoProxies`: per Network's parameters.h a preference only), and before anything is sent the
/// connection's actual remote endpoint (`currentPath.remoteEndpoint`, never the endpoint asked for) must pass
/// `isPairedPeer`. Whether, behind a proxy, the system reports the proxy or the laptop as that endpoint is not known
/// [open], so the check is not claimed to stop a proxy.
public struct NetworkLaptopTransport: LaptopTransport {
    /// Bytes handed to the connection at a time, so the counts reported move as the body goes out [proposed].
    public static let chunkBytes = 65536

    /// The check of the connected peer, given the connection's remote endpoint and the paired endpoint; always
    /// `isPairedPeer` in the app (a test may stand in another).
    let peerCheck: @Sendable (NWEndpoint?, LaptopEndpoint) -> Bool

    public init() { peerCheck = { Self.isPairedPeer($0, paired: $1) } }

    init(peerCheck: @escaping @Sendable (NWEndpoint?, LaptopEndpoint) -> Bool) { self.peerCheck = peerCheck }

    public func exchange(_ request: Data, to endpoint: LaptopEndpoint, timeoutSec: Double,
                         onSent: @escaping @Sendable (Int64) -> Void) async -> Result<HTTPReply, ExchangeFailure> {
        guard let port = NWEndpoint.Port(rawValue: endpoint.port) else { return .failure(.notThePairedPeer) }
        let parameters = NWParameters.tcp
        parameters.preferNoProxies = true
        let connection = NWConnection(host: NWEndpoint.Host(endpoint.host), port: port, using: parameters)
        let exchange = Exchange(connection: connection, request: request, endpoint: endpoint, peerCheck: peerCheck, onSent: onSent)
        return await withCheckedContinuation { continuation in
            exchange.run(timeoutSec: timeoutSec) { continuation.resume(returning: $0) }
        }
    }

    /// Whether a connected remote endpoint is the paired one, at the paired port: when the pairing holds an address,
    /// that very address (an IPv4 address the system writes as IPv6, `::ffff:a.b.c.d`, counts as itself; an interface
    /// or zone the peer carries is not compared); when it holds a name (`localhost`, a `.local` name), any address on
    /// the local network, which is all a name promises.
    static func isPairedPeer(_ remote: NWEndpoint?, paired: LaptopEndpoint) -> Bool {
        guard case .hostPort(let host, let remotePort)? = remote, remotePort.rawValue == paired.port else { return false }
        var peer: [UInt8]
        switch host {
        case .ipv4(let address): peer = Array(address.rawValue)
        case .ipv6(let address): peer = Array(address.rawValue)
        default: return false
        }
        if peer.count == 16, peer.prefix(10).allSatisfy({ $0 == 0 }), peer[10] == 0xFF, peer[11] == 0xFF { peer = Array(peer.suffix(4)) }
        if let wanted = LaptopAddress.addressBytes(paired.host) { return peer == wanted }
        return LaptopAddress.isLocal(bytes: peer)
    }
}

/// One exchange's state, behind one lock: it ends exactly once.
private final class Exchange: @unchecked Sendable {
    private let connection: NWConnection
    private let request: Data
    private let endpoint: LaptopEndpoint
    private let onSent: @Sendable (Int64) -> Void
    private let peerCheck: @Sendable (NWEndpoint?, LaptopEndpoint) -> Bool
    private let queue = DispatchQueue(label: "Albedian laptop connection")
    private let lock = NSLock()
    private var done: ((Result<HTTPReply, ExchangeFailure>) -> Void)?
    private var received = Data()
    private var sentAll = false
    private var finalReply: HTTPReply?
    private var started = false

    init(connection: NWConnection, request: Data, endpoint: LaptopEndpoint, peerCheck: @escaping @Sendable (NWEndpoint?, LaptopEndpoint) -> Bool,
         onSent: @escaping @Sendable (Int64) -> Void) {
        self.connection = connection
        self.peerCheck = peerCheck
        self.request = request
        self.endpoint = endpoint
        self.onSent = onSent
    }

    func run(timeoutSec: Double, done: @escaping (Result<HTTPReply, ExchangeFailure>) -> Void) {
        lock.withLock { self.done = done }
        connection.stateUpdateHandler = { [weak self] state in self?.changed(state) }
        connection.start(queue: queue)
        queue.asyncAfter(deadline: .now() + timeoutSec) { [weak self] in self?.finish(.failure(.timedOut)) }
    }

    private func changed(_ state: NWConnection.State) {
        switch state {
        case .ready:
            let first = lock.withLock { () -> Bool in
                defer { started = true }
                return !started
            }
            guard first else { return }
            guard peerCheck(connection.currentPath?.remoteEndpoint, endpoint) else {
                return finish(.failure(.notThePairedPeer))
            }
            receive()
            send(from: 0)
        case .failed(let error), .waiting(let error):
            if case .posix(let code) = error { finish(.failure(.network(posixCode: code.rawValue))) } else { finish(.failure(.network(posixCode: nil))) }
        case .cancelled:
            finish(.failure(.closed))
        default:
            break
        }
    }

    private func send(from offset: Int) {
        let end = min(offset + NetworkLaptopTransport.chunkBytes, request.count)
        let chunk = request.subdata(in: offset..<end)
        connection.send(content: chunk, completion: .contentProcessed { [weak self] error in
            guard let self else { return }
            if error != nil { return self.finish(.failure(.closed)) }
            self.onSent(Int64(end))
            if end < self.request.count { return self.send(from: end) }
            let reply = self.lock.withLock { () -> HTTPReply? in
                self.sentAll = true
                return self.finalReply
            }
            if let reply { self.finish(.success(reply)) }
        })
    }

    private func receive() {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, isComplete, error in
            guard let self else { return }
            let buffer: Data = self.lock.withLock {
                if let data { self.received.append(data) }
                return self.received
            }
            switch HTTPMessage.read(buffer) {
            case .reply(let reply):
                // A final 2xx counts only for the whole request; any other answer ends the exchange at once
                if (200...299).contains(reply.status) {
                    let ready = self.lock.withLock { () -> Bool in
                        self.finalReply = reply
                        return self.sentAll
                    }
                    if ready { self.finish(.success(reply)) }
                    return
                }
                return self.finish(.success(reply))
            case .unreadable(let why):
                return self.finish(.failure(.unreadable(why)))
            case .needMore:
                if isComplete || error != nil { return self.finish(.failure(.closed)) }
                self.receive()
            }
        }
    }

    private func finish(_ result: Result<HTTPReply, ExchangeFailure>) {
        let callback = lock.withLock { () -> ((Result<HTTPReply, ExchangeFailure>) -> Void)? in
            defer { done = nil }
            return done
        }
        guard let callback else { return }
        connection.stateUpdateHandler = nil
        connection.cancel()
        callback(result)
    }
}
