import Foundation

/// The laptop's address on the local network, as `pnpm field --lan` prints it (`http://<laptop address>:5173`), or
/// the loopback address in the simulator. Only plain http to a local host is taken (REQ-IOS-4.1: the local network
/// only, never an outside service) [proposed]: a loopback, private (10/8, 172.16/12, 192.168/16) or link-local
/// (169.254/16) IPv4 address, the IPv6 loopback, link-local (fe80::/10) or unique local (fc00::/7) address,
/// `localhost`, or a `.local` name. An IPv4 address is four decimal octets with no leading zero, since the system's
/// resolver reads a leading zero as octal (`man 3 inet`: `010.0.0.1` is 8.0.0.1). An IPv6 address may carry a zone
/// (`%25` then an interface name: a letter, then letters and digits) only when it is link-local, and a link-local one
/// must carry it, since without its interface it cannot be reached. A pairing address is written one way: the zone as
/// `%25` (never a raw `%`), and brackets only around an IPv6 address. A port from 1 to 65535, else 80. No path, query, fragment,
/// user or password. The text is read once, here, into the endpoint every connection uses.
public struct LaptopAddress: Equatable, Sendable, CustomStringConvertible {
    public let endpoint: LaptopEndpoint

    public init?(_ text: String) {
        guard let components = URLComponents(string: text), components.scheme == "http", let given = components.host,
              components.user == nil, components.password == nil, components.query == nil, components.fragment == nil,
              components.path.isEmpty || components.path == "/" else { return nil }
        let bracketed = given.hasPrefix("[") && given.hasSuffix("]")
        let host = bracketed ? String(given.dropFirst().dropLast()) : given
        guard bracketed == host.contains(":"), !host.contains("%") || text.contains("%25") else { return nil }
        guard Self.isLocal(host), Self.zoneIsSound(host) else { return nil }
        let port = components.port ?? 80
        guard (1...65535).contains(port) else { return nil }
        endpoint = LaptopEndpoint(host: host.lowercased(), port: UInt16(port))
    }

    /// The address's bytes (4 for IPv4, 16 for IPv6, any zone left out), or nil for a name.
    static func addressBytes(_ host: String) -> [UInt8]? {
        let name = host.lowercased()
        if name.contains(":") {
            var address = in6_addr()
            let bare = String(name.split(separator: "%", maxSplits: 1, omittingEmptySubsequences: false)[0])
            guard inet_pton(AF_INET6, bare, &address) == 1 else { return nil }
            return withUnsafeBytes(of: &address) { Array($0) }
        }
        let parts = name.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 4 else { return nil }
        var octets: [UInt8] = []
        for part in parts {
            guard (1...3).contains(part.count), part.utf8.allSatisfy({ (0x30...0x39).contains($0) }),
                  part.count == 1 || part.first != "0", let value = Int(part), value <= 255 else {
                return nil
            }
            octets.append(UInt8(value))
        }
        return octets
    }

    /// Whether an address's bytes are on the local network, by the ranges above.
    static func isLocal(bytes: [UInt8]) -> Bool {
        if bytes.count == 4 {
            switch (bytes[0], bytes[1]) {
            case (127, _), (10, _), (192, 168), (169, 254): return true
            case (172, 16...31): return true
            default: return false
            }
        }
        guard bytes.count == 16 else { return false }
        let loopback = bytes.prefix(15).allSatisfy { $0 == 0 } && bytes[15] == 1
        return loopback || isLinkLocal(bytes) || (bytes[0] & 0xFE) == 0xFC
    }

    static func isLinkLocal(_ bytes: [UInt8]) -> Bool { bytes.count == 16 && bytes[0] == 0xFE && (bytes[1] & 0xC0) == 0x80 }

    /// A zone only on a link-local IPv6 address, there always, and only an interface name: 1 to 15 letters and digits,
    /// the first a letter.
    static func zoneIsSound(_ host: String) -> Bool {
        let parts = host.split(separator: "%", maxSplits: 1, omittingEmptySubsequences: false)
        guard let bytes = addressBytes(host), bytes.count == 16 else { return parts.count == 1 }
        guard isLinkLocal(bytes) else { return parts.count == 1 }
        guard parts.count == 2 else { return false }
        let zone = parts[1].utf8
        guard let first = zone.first, (0x61...0x7A).contains(first) || (0x41...0x5A).contains(first) else { return false }
        return (1...15).contains(zone.count) && zone.allSatisfy { (0x61...0x7A).contains($0) || (0x41...0x5A).contains($0) || (0x30...0x39).contains($0) }
    }

    /// Whether a host is on the local network, by the ranges above.
    static func isLocal(_ host: String) -> Bool {
        let name = host.lowercased()
        if name == "localhost" { return true }
        if name.hasSuffix(".local") {
            let labels = name.dropLast(".local".count).split(separator: ".", omittingEmptySubsequences: false)
            return !labels.isEmpty && labels.allSatisfy { label in
                !label.isEmpty && label.utf8.allSatisfy { (0x61...0x7A).contains($0) || (0x30...0x39).contains($0) || $0 == 0x2D }
            }
        }
        guard let bytes = addressBytes(name) else { return false }
        return isLocal(bytes: bytes)
    }

    public var description: String {
        // A zone is written %25 in a URL (RFC 3986 percent-encoding of %)
        "http://" + (endpoint.host.contains(":") ? "[\(endpoint.host.replacingOccurrences(of: "%", with: "%25"))]" : endpoint.host)
            + ":\(endpoint.port)"
    }
}

/// The type the photo's bytes are declared as on upload; the laptop checks it against the bytes
/// (`services/ingest/store.py`, `DECLARABLE`).
public enum PhotoContentType: String, Sendable, CaseIterable {
    case heic = "image/heic"
    case jpeg = "image/jpeg"
    /// Only for the synthetic sample image the app draws in the simulator; the camera writes HEIC or JPEG.
    case png = "image/png"
}

/// Which of the two requests is under way.
public enum UploadPhase: String, Sendable {
    case photo
    case record
}

/// Why an upload stopped. Each names what happened, never a value from the record. None of them counts the photo as
/// stored or the record as accepted.
public enum UploadFailure: Error, Equatable, Sendable {
    /// The record names another photo than the bytes given; nothing was sent.
    case recordNamesAnotherPhoto
    /// The record could not be written as contract JSON; nothing was sent.
    case recordNotWritable([PointingIssue])
    /// The request was not one the uploader may send (`CaptureUploader.permits`); nothing was sent. No request the
    /// uploader builds is refused this way: it is the last guard.
    case notPermitted(phase: UploadPhase)
    /// The connection failed (the laptop refused it, or the network did), with the POSIX error's code when it was one
    /// (61 is "connection refused"); nil when the cause is not known.
    case networkError(phase: UploadPhase, posixCode: Int32?)
    /// No whole reply came within the time allowed.
    case timedOut(phase: UploadPhase)
    /// The connection ended before a whole reply came, or before the whole request was sent.
    case connectionClosed(phase: UploadPhase)
    /// The connected peer was not the paired address at the paired port (or, when the pairing holds a name, not an
    /// address on the local network); the connection was closed before anything was sent.
    case notThePairedPeer(phase: UploadPhase)
    /// The laptop answered with a status other than success or redirection, and its `detail` when it gave one.
    case refused(phase: UploadPhase, status: Int, detail: String?)
    /// The laptop's address answered with a redirect (a 3xx status). Nothing follows a redirect, so the request went
    /// to no other place.
    case redirected(phase: UploadPhase, status: Int)
    /// The laptop's answer was not a reply the reader takes, or not a receipt for these bytes.
    case unreadableReply(phase: UploadPhase)
    /// The laptop's receipt names a different SHA-256 than the bytes sent.
    case storedDifferentBytes(receiptSha256: String)
}

/// Where an upload stands. Each state is what happened, from what the connection reported: no time-based or invented
/// progress (REQ-IOS-4.2).
public enum UploadState: Equatable, Sendable {
    /// Nothing sent yet.
    case waiting
    /// A request's body is going out: the body's bytes the connection has taken so far, of the body's length. Once all
    /// are taken the state stays here, with both equal, until the laptop answers.
    case sending(phase: UploadPhase, bytesSent: Int64, bytesTotal: Int64)
    case failed(UploadFailure)
    /// The laptop's final reply on the paired connection said it stored the photo (or already had it), naming its
    /// SHA-256; and, when there was a record, said the same of the record.
    case done(sha256: String, photoAlreadyStored: Bool, recordAccepted: Bool)
}

/// A request `CaptureUploader.permits` allowed: the paired endpoint, the same value the address holds, and the
/// request line.
public struct ApprovedRequest: Equatable, Sendable {
    public let endpoint: LaptopEndpoint
    public let method: String
    public let path: String
}

/// Sends a photo exactly as given and its pointing record to the laptop, each on a connection of its own to the
/// paired endpoint: the photo as the body of `POST /api/ingest` (unchanged, `services/ingest/router.py`), its type as
/// Content-Type and no file name; then the record as the JSON body of `PUT /api/ingest/{sha256}/pointing` [proposed
/// in the design, component 4; the route is unit pointing-2, so today it answers 404 or 405 and the state says so].
public final class CaptureUploader: Sendable {
    /// The User-Agent every Albedian request carries, and nothing personal.
    public static let userAgent = "Albedian/0.1 (research prototype)"

    public let address: LaptopAddress
    public let timeoutSec: Double
    private let transport: any LaptopTransport

    /// How long one request may take, from connecting to the whole reply, unless told otherwise [proposed; whether the
    /// person sees it on a screen is the owner's].
    public static let defaultTimeoutSec: Double = 60

    /// - Parameter timeoutSec: how long one request may take, from connecting to the whole reply.
    public init(address: LaptopAddress, timeoutSec: Double = CaptureUploader.defaultTimeoutSec,
                transport: any LaptopTransport = NetworkLaptopTransport()) {
        self.address = address
        self.timeoutSec = timeoutSec
        self.transport = transport
    }

    /// Sends the photo, then the record when there is one, reporting every state change to `onState` in order,
    /// and returns the last state. The record is checked before anything is sent: it must name these bytes' SHA-256
    /// and pass the contract.
    public func send(photo: Data, contentType: PhotoContentType, record: PointingRecord?,
                     onState: @escaping @Sendable (UploadState) -> Void) async -> UploadState {
        onState(.waiting)
        let sha256 = PhotoHash.sha256Hex(photo)
        var recordBody: Data?
        if let record {
            guard record.sourceAssetSha256 == sha256 else { return Self.finish(.failed(.recordNamesAnotherPhoto), onState) }
            do throws(PointingEncodingError) {
                let wire = try record.wireValue()
                let issues = PointingValidator.issues(in: wire)
                guard issues.isEmpty else { return Self.finish(.failed(.recordNotWritable(issues)), onState) }
                recordBody = JSONText.data(wire)
            } catch {
                return Self.finish(.failed(.recordNotWritable([PointingIssue(path: error.path, message: "Number must be finite")])), onState)
            }
        }
        return await transmit(photo: photo, sha256: sha256, contentType: contentType, recordBody: recordBody, onState: onState)
    }

    /// The two requests, once the record's bytes (if any) are checked.
    func transmit(photo: Data, sha256: String, contentType: PhotoContentType, recordBody: Data?,
                  onState: @escaping @Sendable (UploadState) -> Void) async -> UploadState {
        let photoReply: HTTPReply
        switch await exchange(method: "POST", path: "/api/ingest", contentType: contentType.rawValue, body: photo, phase: .photo, onState: onState) {
        case .failure(let failure): return Self.finish(.failed(failure), onState)
        case .success(let reply): photoReply = reply
        }
        guard let receipt = try? JSONText.read(photoReply.body), let stored = receipt["sourceAsset"]?["sha256"]?.stringValue,
              let already = receipt["alreadyStored"]?.boolValue else { return Self.finish(.failed(.unreadableReply(phase: .photo)), onState) }
        guard stored == sha256 else { return Self.finish(.failed(.storedDifferentBytes(receiptSha256: stored)), onState) }
        guard let recordBody else { return Self.finish(.done(sha256: sha256, photoAlreadyStored: already, recordAccepted: false), onState) }

        let recordReply: HTTPReply
        switch await exchange(method: "PUT", path: "/api/ingest/\(sha256)/pointing", contentType: "application/json", body: recordBody,
                              phase: .record, onState: onState) {
        case .failure(let failure): return Self.finish(.failed(failure), onState)
        case .success(let reply): recordReply = reply
        }
        guard Self.recordReceiptNames(sha256, recordReply.body) else { return Self.finish(.failed(.unreadableReply(phase: .record)), onState) }
        return Self.finish(.done(sha256: sha256, photoAlreadyStored: already, recordAccepted: true), onState)
    }

    private static func finish(_ state: UploadState, _ onState: @Sendable (UploadState) -> Void) -> UploadState {
        onState(state)
        return state
    }

    /// The one decision on whether and where a request may go: only this uploader's two requests (`POST
    /// /api/ingest`, and `PUT /api/ingest/{sha256}/pointing` for a contract SHA-256), and only to the endpoint the
    /// paired address holds, the same value. The connection is opened to the endpoint it returns.
    static func permits(method: String, path: String, to address: LaptopAddress) -> ApprovedRequest? {
        let parts = path.split(separator: "/", omittingEmptySubsequences: false)
        let allowed: Bool
        switch method {
        case "POST":
            allowed = parts == ["", "api", "ingest"]
        case "PUT":
            allowed = parts.count == 5 && parts[0] == "" && parts[1] == "api" && parts[2] == "ingest"
                && PhotoHash.isContractHash(String(parts[3])) && parts[4] == "pointing"
        default:
            allowed = false
        }
        return allowed ? ApprovedRequest(endpoint: address.endpoint, method: method, path: path) : nil
    }

    /// One request on a connection of its own, and its final reply: a 2xx, or the failure that names what else came.
    private func exchange(method: String, path: String, contentType: String, body: Data, phase: UploadPhase,
                          onState: @escaping @Sendable (UploadState) -> Void) async -> Result<HTTPReply, UploadFailure> {
        guard let approved = Self.permits(method: method, path: path, to: address) else { return .failure(.notPermitted(phase: phase)) }
        let request = HTTPMessage.request(method: approved.method, path: approved.path, endpoint: approved.endpoint,
                                          contentType: contentType, body: body)
        let head = Int64(request.count - body.count)
        let total = Int64(body.count)
        onState(.sending(phase: phase, bytesSent: 0, bytesTotal: total))
        let result = await transport.exchange(request, to: approved.endpoint, timeoutSec: timeoutSec) { sent in
            // The connection counts the whole request (head and body), never more
            onState(.sending(phase: phase, bytesSent: max(0, sent - head), bytesTotal: total))
        }
        switch result {
        case .failure(.network(let code)): return .failure(.networkError(phase: phase, posixCode: code))
        case .failure(.timedOut): return .failure(.timedOut(phase: phase))
        case .failure(.closed): return .failure(.connectionClosed(phase: phase))
        case .failure(.notThePairedPeer): return .failure(.notThePairedPeer(phase: phase))
        case .failure(.unreadable): return .failure(.unreadableReply(phase: phase))
        case .success(let reply):
            if (300...399).contains(reply.status) { return .failure(.redirected(phase: phase, status: reply.status)) }
            guard (200...299).contains(reply.status) else {
                return .failure(.refused(phase: phase, status: reply.status, detail: Self.detail(reply.body)))
            }
            return .success(reply)
        }
    }

    /// The key the record's receipt names the photo by [proposed, the lead's answer of 2026-11-12: the record route's
    /// reply contract is unit pointing-2a, not written yet].
    public static let recordReceiptHashKey = "sourceAssetSha256"

    /// Whether a record reply's body is a receipt for the record of this photo: a JSON object whose
    /// `recordReceiptHashKey` holds this SHA-256 (other keys are not read) [proposed until pointing-2a].
    static func recordReceiptNames(_ sha256: String, _ body: Data) -> Bool {
        // A body that is not an object has no key, so it names nothing
        (try? JSONText.read(body))?[recordReceiptHashKey]?.stringValue == sha256
    }

    /// The laptop's own words for a refusal (FastAPI's `detail`), when it gave them as text.
    static func detail(_ body: Data) -> String? { (try? JSONText.read(body))?["detail"]?.stringValue }
}
