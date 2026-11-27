import Foundation

/// firstuse-connect-one (UIUX-iphone-firstuse-READY item 1; the check DECIDED-BY-LEAD 27 Nov 04:17): an address typed
/// from Aim is kept only once the laptop answers its health route as Albedian. The request carries nothing personal (no
/// body, no place, no time), so nothing about the person reaches a host that is not Albedian; the sky request, which
/// carries the place, goes only to a kept address.
public enum LaptopHealth {
    public static let method = "GET"
    public static let path = "/api/health"
    /// The service's own name for itself in the health route's reply (`services/api.py`, `get_health`).
    public static let serviceName = "albedian-observation-media-runtime"

    /// Whether a reply is Albedian's: an object whose `service` is the service's name, exactly.
    public static func isAlbedian(_ reply: JSONValue) -> Bool {
        reply.objectValue?["service"]?.stringValue == serviceName
    }
}

/// Asks the typed address's health route once, through the one gate (`CaptureUploader.permits`), on a connection of its own.
public final class LaptopHealthClient: Sendable {
    /// [proposed] The time the one request may take: the route answers at once on the laptop.
    public static let timeoutSec: Double = 5
    /// [proposed] The bound on the reply: the route's reply is under 1 kB.
    public static let replyMaxBytes = 65_536

    public let address: LaptopAddress
    private let transport: any LaptopTransport

    public init(address: LaptopAddress, transport: any LaptopTransport = NetworkLaptopTransport()) {
        self.address = address
        self.transport = transport
    }

    /// True only when the laptop answered 2xx with Albedian's service name; false on any other answer or none.
    public func isAlbedian() async -> Bool {
        guard let approved = CaptureUploader.permits(method: LaptopHealth.method, path: LaptopHealth.path, to: address) else { return false }
        let request = HTTPMessage.request(method: approved.method, path: approved.path, endpoint: approved.endpoint,
                                          contentType: nil, body: Data())
        guard case .success(let reply) = await transport.exchange(request, to: approved.endpoint, timeoutSec: Self.timeoutSec,
                                                                  bodyMaxBytes: Self.replyMaxBytes, onSent: { _ in }),
              (200...299).contains(reply.status), let value = try? JSONText.read(reply.body) else { return false }
        return LaptopHealth.isAlbedian(value)
    }
}

/// Connect at default text sizes (UIUX-iphone-firstuse-READY item 1, its mock's own numbers): the line at 238, the
/// command's box 12 pt under it (Geist Mono 16 at 1.2 a line with 12 pt in from the left, 40 pt high, radius 8, the
/// ink wash), "Address" 16 pt under the box, the field 24 pt under "Address", the refused line's room 14 pt under the
/// field (two lines of 15 pt at 1.2), and Connect 12 pt under that room: 469 to 513 on the 844 point frame.
public enum ConnectLayout {
    public static let startTop = 238.0
    public static let startLineHeight = 14 * 1.2
    public static let boxHeight = 40.0
    public static let boxRadius = 8.0
    public static let boxInset = 12.0
    /// A hundredth of a point of tracking on the command: Core Text then sets no contextual alternate, which in Geist
    /// Mono joins the space before "--" (15 glyphs for the command's 16 characters as is) [tested, FirstUseConnectScreenTests].
    public static let commandTracking = 0.01
    public static let fieldHeight = 60.0
    public static let roomHeight = 15 * 1.2 * 2
    public static var boxTop: Double { startTop + startLineHeight + 12 }
    public static var addressTop: Double { boxTop + boxHeight + 16 }
    public static var fieldTop: Double { addressTop + 24 }
    public static var messageTop: Double { fieldTop + fieldHeight + 14 }
    public static var connectTop: Double { messageTop + roomHeight + 12 }
}

/// The words the record gives Send's first use (UIUX-iphone-firstuse-READY item 2): where the send goes, on the button
/// and in the location ask, in the term Connect uses.
public enum SendWords {
    /// The main button after the shot.
    public static let mainButton = "Send to laptop"
    /// The system's location ask (`NSLocationWhenInUseUsageDescription`).
    public static let locationReason = "Albedian uses your location while you aim to find true north and the sky above you, and sends it only to Albedian on your laptop."
}

/// The words the record gives Connect's first use (UIUX-iphone-firstuse-READY item 1).
public enum ConnectWords {
    /// The pill on Aim while no laptop is kept.
    public static let pill = "Connect laptop"
    /// The line over the command on Connect, in place of "Albedian shows this address on the laptop when it starts."
    public static let start = "Start Albedian on your laptop with:"
    /// The one command that starts the laptop side for a phone on the same network.
    public static let command = "pnpm field --lan"
}
