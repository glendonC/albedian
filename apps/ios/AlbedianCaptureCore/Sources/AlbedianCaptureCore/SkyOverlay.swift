import Foundation

/// The aim overlay (mobile-5, arrive iPhone 2): the stars and bodies the paired laptop predicts for the phone's time and
/// place (`POST /api/science/sky-context`, services/contracts/sky_context.py), each drawn where the phone's attitude
/// and the camera's field of view put it, and every one `predicted`: computed from catalog rows and an ephemeris,
/// never found in the camera's pixels. The phone does no astronomy of its own (AGENTS.md, the mobile row): it asks
/// the laptop and only turns an altitude and azimuth into a point on the preview.
public enum SkyContextRequest {
    public static let method = "POST"
    public static let path = "/api/science/sky-context"
    /// [proposed] Asked again this often while aiming: the sky turns 0.125 degrees in 30 s, well under any heading
    /// accuracy the phone keeps a heading with.
    public static let refreshSec: Double = 30

    /// The body: the device clock's moment as a UTC instant with Z, the place, and only what is above the horizon.
    /// Nil for a moment the contract cannot carry (`UTCInstant`) or a place that is not one.
    public static func body(at moment: Date, latDeg: Double, lonDeg: Double) -> Data? {
        guard let instant = UTCInstant.string(from: moment), latDeg.isFinite, lonDeg.isFinite,
              (-90...90).contains(latDeg), (-180...180).contains(lonDeg),
              let lat = JSONNumber(latDeg), let lon = JSONNumber(lonDeg) else { return nil }
        let object = JSONObject(["observedAtUTC": .string(JSONString(instant)), "latDeg": .number(lat), "lonDeg": .number(lon),
                                 "aboveHorizonOnly": .bool(true)])
        return JSONText.data(.object(object))
    }
}

/// One mark of the overlay: where the laptop predicts a star or a body, and where that prediction comes from.
public struct SkyMark: Equatable, Sendable {
    public enum Source: Equatable, Sendable {
        /// A catalog row, by its release (Hipparcos 2007, Gaia DR3).
        case catalog(release: String)
        /// The laptop's ephemeris.
        case ephemeris
    }

    public let id: String
    /// The name to draw: a star's common name, a body's name.
    public let name: String
    public let altDeg: Double
    public let azDeg: Double
    public let source: Source
    /// A star's catalog magnitude; nil for a body, which has none in this reply.
    public var mag: Double? = nil
    /// Always predicted: the reader takes no other.
    public var evidence: EvidenceClass { .predicted }
}

/// Reads the laptop's sky-context reply into marks: only a reply in the contract's format, every item of which is
/// `predicted`, or none at all, so a reply that claims anything else draws nothing.
public enum SkyContextReader {
    public static let format = "albedian.sky-context"
    static let convention = ["frame": "topocentric_sea_level", "azimuth": "true_north_through_east", "altitude": "apparent_airless"]

    public static func marks(_ value: JSONValue) -> [SkyMark]? {
        guard value["format"]?.stringValue == format, value["formatVersion"]?.numberValue == 1,
              let convention = value["convention"]?.objectValue,
              Set(convention.keys.compactMap(\.string)) == Set(Self.convention.keys),
              Self.convention.allSatisfy({ convention[$0.key]?.stringValue == $0.value }),
              let stars = value["stars"]?.arrayValue, let bodies = value["ephemerides"]?.arrayValue else { return nil }
        var out: [SkyMark] = []
        for star in stars {
            guard star["evidenceClass"]?.stringValue == "predicted", let release = star["releaseVersion"]?.stringValue,
                  var mark = mark(star, name: star["commonName"]?.stringValue, source: .catalog(release: release)),
                  let mag = star["mag"]?.numberValue, mag.isFinite else { return nil }
            mark.mag = mag
            out.append(mark)
        }
        for body in bodies {
            guard body["evidenceClass"]?.stringValue == "predicted", body["evidence"]?["evidenceClass"]?.stringValue == "predicted",
                  let mark = mark(body, name: body["name"]?.stringValue, source: .ephemeris) else { return nil }
            out.append(mark)
        }
        return out
    }

    public static func marks(_ data: Data) -> [SkyMark]? {
        guard let value = try? JSONText.read(data) else { return nil }
        return marks(value)
    }

    private static func mark(_ item: JSONValue, name: String?, source: SkyMark.Source) -> SkyMark? {
        guard let id = item["id"]?.stringValue, let name, !name.isEmpty,
              let alt = item["altDeg"]?.numberValue, let az = item["azDeg"]?.numberValue,
              (-90...90).contains(alt), (0...360).contains(az) else { return nil }
        return SkyMark(id: id, name: name, altDeg: alt, azDeg: az, source: source)
    }
}

/// Where a direction in the sky falls on the aiming preview.
public enum SkyProjection {
    /// The unit vector toward an altitude and azimuth in Core Motion's `xTrueNorthZVertical` frame: X true north, Z up,
    /// Y west (Z cross X), the azimuth from north through east.
    public static func direction(altDeg: Double, azDeg: Double) -> (x: Double, y: Double, z: Double) {
        let alt = altDeg * .pi / 180
        let az = azDeg * .pi / 180
        return (cos(alt) * cos(az), -cos(alt) * sin(az), sin(alt))
    }

    /// The point on a portrait preview `widthPt` by `heightPt` that shows `acrossFovDeg` from its left edge to its right,
    /// where a direction falls, or nil when it is behind the lens or off the preview by more than `marginPt`.
    ///
    /// The attitude is Core Motion's: it turns a vector held in the device's axes into the reference frame, so its
    /// conjugate turns the sky's direction into the device's. The back camera looks along the device's minus Z; the
    /// device's X is the preview's right and its Y the preview's top. Both rest on mobile-1's device check of the
    /// quaternion's direction and the camera's axis [open: iPhone only].
    public static func point(altDeg: Double, azDeg: Double, attitude: PointingRecord.Quaternion, widthPt: Double, heightPt: Double,
                             acrossFovDeg: Double, marginPt: Double = 0) -> (x: Double, y: Double)? {
        guard widthPt > 0, heightPt > 0, acrossFovDeg > 0, acrossFovDeg < 180, altDeg.isFinite, azDeg.isFinite else { return nil }
        let norm = (attitude.x * attitude.x + attitude.y * attitude.y + attitude.z * attitude.z + attitude.w * attitude.w).squareRoot()
        guard norm.isFinite, norm > 0 else { return nil }
        let q = PointingRecord.Quaternion(x: attitude.x / norm, y: attitude.y / norm, z: attitude.z / norm, w: attitude.w / norm)
        let sky = direction(altDeg: altDeg, azDeg: azDeg)
        let device = q.conjugate.multiplied(PointingRecord.Quaternion(x: sky.x, y: sky.y, z: sky.z, w: 0)).multiplied(q)
        let depth = -device.z
        guard depth > 1e-9 else { return nil }
        let focal = widthPt / 2 / tan(acrossFovDeg * .pi / 360)
        let x = widthPt / 2 + focal * device.x / depth
        let y = heightPt / 2 - focal * device.y / depth
        guard x >= -marginPt, x <= widthPt + marginPt, y >= -marginPt, y <= heightPt + marginPt else { return nil }
        return (x, y)
    }

    /// The field of view across a portrait 3:4 preview's width from the camera format's field of view across its long
    /// side (Apple's `videoFieldOfView` is the landscape width): the same focal length over the short side. Which side
    /// the format's number belongs to is iPhone check 8 [open].
    public static func acrossPortraitWidthDeg(longSideFovDeg: Double, shortOverLong: Double = 3.0 / 4.0) -> Double? {
        guard longSideFovDeg > 0, longSideFovDeg < 180, shortOverLong > 0, shortOverLong <= 1 else { return nil }
        return 2 * atan(tan(longSideFovDeg * .pi / 360) * shortOverLong) * 180 / .pi
    }
}

/// The words of the overlay (arrive iPhone 2): the tag and the one small line under the preview.
public enum SkyOverlayWords {
    public static let tag = "Predicted"
    static let ephemeris = "ERFA ephemeris"

    /// "Hipparcos 2007 · heading ±12°": each source of the marks drawn, the catalog releases in the order first drawn,
    /// then the ephemeris, then the heading accuracy in whole degrees, rounded up. The record's example names one
    /// catalog; more than one, and the ephemeris's name (review question C7's proposed source word), are
    /// [proposed, INTERIM].
    public static func sourceLine(_ drawn: [SkyMark], headingAccuracyDeg: Double) -> String? {
        guard !drawn.isEmpty, headingAccuracyDeg.isFinite, headingAccuracyDeg >= 0 else { return nil }
        var sources: [String] = []
        for mark in drawn {
            if case .catalog(let release) = mark.source, !sources.contains(release) { sources.append(release) }
        }
        if drawn.contains(where: { $0.source == .ephemeris }) { sources.append(ephemeris) }
        return (sources + ["heading ±\(Int(headingAccuracyDeg.rounded(.up)))°"]).joined(separator: " · ")
    }

}

/// The record's sizes, in points (arrive iPhone 2's picture: `.cache/uiux/arrive/screen/p-aim.html`).
public enum SkyOverlayStyle {
    public static let ringRadiusPt = 6.5
    public static let ringLineWidthPt = 1.1
    public static let ringDash: [Double] = [2.6, 2.4]
    /// From the ring's centre to the name's near edge, and the name's baseline below the centre.
    public static let nameGapPt = 11.0
    public static let nameBaselinePt = 4.0
    public static let nameSizePt = 12.0
    public static let nameTracking = -0.01
    /// The name's dark edge: a 3 px stroke at 75 percent black under the fill.
    public static let haloWidthPt = 3.0
    public static let haloOpacity = 0.75
}

/// Asks the paired laptop for the sky now, through the one gate (`CaptureUploader.permits`), on a connection of its
/// own. Any failure (no reply, a refusal, a reply not in the contract's form) gives no marks, and the aiming screen
/// then draws no overlay: no mark is better than a stale or a made-up one.
public final class SkyContextClient: Sendable {
    /// [proposed] The time one request may take: the route answers in about 0.2 s on the Mac.
    public static let timeoutSec: Double = 10
    /// [proposed] The bound on the reply: the whole sky today is about 89 kB (71 stars, 6 bodies, 72 edges), above the
    /// receipts' 64 kB, so 1 MiB.
    public static let replyMaxBytes = 1_048_576

    public let address: LaptopAddress
    private let transport: any LaptopTransport

    public init(address: LaptopAddress, transport: any LaptopTransport = NetworkLaptopTransport()) {
        self.address = address
        self.transport = transport
    }

    public func marks(at moment: Date, latDeg: Double, lonDeg: Double) async -> [SkyMark]? {
        guard let body = SkyContextRequest.body(at: moment, latDeg: latDeg, lonDeg: lonDeg),
              let approved = CaptureUploader.permits(method: SkyContextRequest.method, path: SkyContextRequest.path, to: address)
        else { return nil }
        let request = HTTPMessage.request(method: approved.method, path: approved.path, endpoint: approved.endpoint,
                                          contentType: "application/json", body: body)
        guard case .success(let reply) = await transport.exchange(request, to: approved.endpoint, timeoutSec: Self.timeoutSec,
                                                                  bodyMaxBytes: Self.replyMaxBytes, onSent: { _ in }),
              (200...299).contains(reply.status) else { return nil }
        return SkyContextReader.marks(reply.body)
    }
}

/// The place and heading accuracy the overlay is asked and labelled with, while a photo's record would hold both.
public struct AimingSky: Equatable, Sendable {
    public let latDeg: Double
    public let lonDeg: Double
    public let headingAccuracyDeg: Double

    public init(latDeg: Double, lonDeg: Double, headingAccuracyDeg: Double) {
        self.latDeg = latDeg
        self.lonDeg = lonDeg
        self.headingAccuracyDeg = headingAccuracyDeg
    }
}

extension SkyProjection {
    /// The attitude of a phone held upright in portrait, its back camera toward an azimuth and tilted up by an angle,
    /// its top up: the device's axes written in the reference frame, in the attitude's convention (`point`) [open: the
    /// same device check]. For the tests and the simulator's stand-in, never a reading.
    public static func uprightAttitude(towardAzDeg azDeg: Double, pitchUpDeg: Double = 0) -> PointingRecord.Quaternion {
        // Device X (right) is the horizon direction 90 degrees clockwise of the view; Z is toward the viewer (the view
        // reversed); Y = Z cross X is up
        let view = direction(altDeg: pitchUpDeg, azDeg: azDeg)
        let right = direction(altDeg: 0, azDeg: azDeg + 90)
        let back = (x: -view.x, y: -view.y, z: -view.z)
        let up = (x: back.y * right.z - back.z * right.y, y: back.z * right.x - back.x * right.z, z: back.x * right.y - back.y * right.x)
        // The rotation's columns are the device axes in the reference frame; to a quaternion by the largest of its four
        // squared components (Shepperd), so no turn divides by a small number
        let m = [[right.x, up.x, back.x], [right.y, up.y, back.y], [right.z, up.z, back.z]]
        let trace = m[0][0] + m[1][1] + m[2][2]
        let squares = [1 + trace, 1 + m[0][0] - m[1][1] - m[2][2], 1 - m[0][0] + m[1][1] - m[2][2], 1 - m[0][0] - m[1][1] + m[2][2]]
        let largest = squares.indices.max { squares[$0] < squares[$1] }!
        let s = squares[largest].squareRoot() * 2
        switch largest {
        case 0: return PointingRecord.Quaternion(x: (m[2][1] - m[1][2]) / s, y: (m[0][2] - m[2][0]) / s, z: (m[1][0] - m[0][1]) / s, w: s / 4)
        case 1: return PointingRecord.Quaternion(x: s / 4, y: (m[0][1] + m[1][0]) / s, z: (m[0][2] + m[2][0]) / s, w: (m[2][1] - m[1][2]) / s)
        case 2: return PointingRecord.Quaternion(x: (m[0][1] + m[1][0]) / s, y: s / 4, z: (m[1][2] + m[2][1]) / s, w: (m[0][2] - m[2][0]) / s)
        default: return PointingRecord.Quaternion(x: (m[0][2] + m[2][0]) / s, y: (m[1][2] + m[2][1]) / s, z: s / 4, w: (m[1][0] - m[0][1]) / s)
        }
    }
}

/// Which marks get a name, and on which side: the web's interim rule for a crowded sky (`apps/web/src/reveal/
/// revealMarks.ts`, the lead's answer of 2026-11-19 03:31; INTERIM, the design lane records it). A star gets its name
/// only when it is brighter than magnitude 3.2 and its name runs into no other mark's room; the name goes right of its
/// ring unless that crosses the preview's right edge, and tries the other side once when it runs into a mark. Every
/// ring stays. A body has no catalog magnitude in this reply: it keeps its name by the same room rule alone
/// [inference: the web names its predicted bodies through a budget of its own, not by magnitude].
public enum SkyOverlayNames {
    /// The web's numbers, kept as it states them: the faintest star named, a name's width per character in ems, the room a
    /// mark keeps around its centre, and a name's margin at each end.
    public static let magnitudeLimit = 3.2
    static let emPerChar = 0.56
    static let markRoomPt = 14.0
    static let marginPt = 3.0

    public struct Placed: Equatable, Sendable {
        public let mark: SkyMark
        public let x: Double
        public let y: Double
        public init(mark: SkyMark, x: Double, y: Double) {
            self.mark = mark
            self.x = x
            self.y = y
        }
    }

    struct Box {
        let x0, x1, y0, y1: Double
        func overlaps(_ other: Box) -> Bool { x0 < other.x1 && x1 > other.x0 && y0 < other.y1 && y1 > other.y0 }
    }

    /// The box a name takes beside its ring at the overlay's size: from 0.8 of its size above the centre to 0.5 below.
    static func box(_ placed: Placed, left: Bool) -> Box {
        let size = SkyOverlayStyle.nameSizePt
        let width = Double(placed.mark.name.utf16.count) * size * emPerChar  // as the web counts a name's length
        let start = left ? placed.x - SkyOverlayStyle.nameGapPt : placed.x + SkyOverlayStyle.nameGapPt
        return Box(x0: (left ? start - width : start) - marginPt, x1: (left ? start : start + width) + marginPt,
                   y0: placed.y - size * 0.8, y1: placed.y + size * 0.5)
    }

    /// For each mark that gets a name, by its id: whether the name sits on the left of its ring.
    public static func names(_ placed: [Placed], widthPt: Double) -> [String: Bool] {
        let rooms = placed.map { p in
            (p.mark.id, Box(x0: p.x - markRoomPt, x1: p.x + markRoomPt, y0: p.y - markRoomPt, y1: p.y + markRoomPt))
        }
        var out: [String: Bool] = [:]
        for p in placed {
            if case .catalog = p.mark.source, !(p.mark.mag.map { $0 < magnitudeLimit } ?? false) { continue }
            func runsInto(_ box: Box) -> Bool { rooms.contains { $0.0 != p.mark.id && box.overlaps($0.1) } }
            var onLeft = box(p, left: false).x1 - marginPt > widthPt
            if runsInto(box(p, left: onLeft)) {
                let other = box(p, left: !onLeft)
                if runsInto(other) || other.x1 > widthPt || other.x0 < 0 { continue }
                onLeft.toggle()
            }
            out[p.mark.id] = onLeft
        }
        return out
    }
}
