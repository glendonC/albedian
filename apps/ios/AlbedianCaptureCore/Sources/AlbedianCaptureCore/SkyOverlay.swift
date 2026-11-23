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
    /// A star's id in its catalog (its `catalogSourceId`); nil for a body. A star whose name is this id has no name of
    /// its own and is never named on the overlay (D20).
    public var catalogSourceId: String? = nil
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
                  let mag = star["mag"]?.numberValue, mag.isFinite,
                  let catalogSourceId = star["catalogSourceId"]?.stringValue, !catalogSourceId.isEmpty else { return nil }
            mark.mag = mag
            mark.catalogSourceId = catalogSourceId
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

/// The record's sizes, in points (arrive iPhone 2's picture: `.cache/uiux/arrive/screen/p-aim.html`; the ring sizes
/// and the name's size from D20, UIUX-aimnames-DECIDED-BY-LEAD.md).
public enum SkyOverlayStyle {
    /// A named ring, and a ring without a name.
    public static let ringRadiusPt = 6.5
    public static let unnamedRingRadiusPt = 4.5
    public static let ringLineWidthPt = 1.1
    public static let ringDash: [Double] = [2.6, 2.4]
    /// From the ring's centre to the name's near edge (12 in D20's screen, 11 in the aiming screen before it), and the
    /// name's baseline below the centre.
    public static let nameGapPt = 12.0
    public static let nameBaselinePt = 4.0
    public static let nameSizePt = 11.0
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
        guard let reply = await context(at: moment, latDeg: latDeg, lonDeg: lonDeg) else { return nil }
        return SkyContextReader.marks(reply)
    }

    /// The whole reply, for the overlay's marks and the capture coach's conditions from one request; nil on any failure.
    public func context(at moment: Date, latDeg: Double, lonDeg: Double) async -> JSONValue? {
        guard let body = SkyContextRequest.body(at: moment, latDeg: latDeg, lonDeg: lonDeg),
              let approved = CaptureUploader.permits(method: SkyContextRequest.method, path: SkyContextRequest.path, to: address)
        else { return nil }
        let request = HTTPMessage.request(method: approved.method, path: approved.path, endpoint: approved.endpoint,
                                          contentType: "application/json", body: body)
        guard case .success(let reply) = await transport.exchange(request, to: approved.endpoint, timeoutSec: Self.timeoutSec,
                                                                  bodyMaxBytes: Self.replyMaxBytes, onSent: { _ in }),
              (200...299).contains(reply.status) else { return nil }
        guard let value = try? JSONText.read(reply.body), SkyContextReader.marks(value) != nil else { return nil }
        return value
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

/// What the aim view shows at one moment, read once a frame (AUDIT-C's sweep, items 10 and 11): D8's line saying why
/// there is no overlay, and the sky the overlay is drawn for, nil whenever a photo's record would have no place or no
/// heading. The laptop's marks are drawn only while that sky is there, so they go the moment the line says why,
/// never on the asking loop's next turn, and no ring stands without its source line and its tag.
public struct AimingNow: Equatable, Sendable {
    public let line: String?
    public let sky: AimingSky?

    public init(line: String?, sky: AimingSky?) {
        self.line = line
        self.sky = sky
    }

    /// The laptop's last marks to draw now: all of them while the sky is there, none without it.
    public func marks(_ asked: [SkyMark]) -> [SkyMark] { sky == nil ? [] : asked }

    /// The one line under the preview: D8's why while it has one, else the drawn rings' sources and the heading accuracy
    /// they were asked with, none while no ring is drawn.
    public func shownLine(drawn: [SkyMark], headingAccuracyDeg: Double?) -> AimLine? {
        if let line { return .why(line) }
        return headingAccuracyDeg.flatMap { SkyOverlayWords.sourceLine(drawn, headingAccuracyDeg: $0) }.map(AimLine.source)
    }
}

/// The line under the aiming preview and how it arrives: D8's line saying why there is no overlay words in
/// (MOTION-READY-iphone section 4); the overlay's source line names the predicted rings' source and limits, so it
/// stands from the rings' first frame with no words-in delay or fade from nothing (the 17:38 rule as that record
/// writes it for the aim view).
public enum AimLine: Equatable, Sendable {
    case why(String)
    case source(String)

    public var words: String {
        switch self {
        case .why(let words), .source(let words): words
        }
    }

    public var wordsIn: Bool {
        if case .why = self { return true }
        return false
    }
}

/// The aim view leaving at the press, or coming back, inside the capture's one place (the lead, 21 Nov 13:48, as
/// AUDIT-C's item 5 ruled, with the 17:38 rule in MOTION-READY-iphone): the stage runs `hidden` from 0 to 1 on its own
/// timing (out on frame out 0.15 s after the press, in on frame in; the 0.27 s dissolve under Reduce Motion). The rings
/// and the rest of the view fade with it; the Predicted tag and the source line stand at full from the first frame to
/// the rings' last and leave with the view at once, so nothing predicted is drawn without them.
public enum AimExit {
    public enum Part: Sendable { case rings, tag, sourceLine, rest }

    public static func opacity(_ part: Part, hidden: Double) -> Double {
        switch part {
        case .tag, .sourceLine: 1
        case .rings, .rest: 1 - min(max(hidden, 0), 1)
        }
    }

    /// The seconds from the press to the aim view's last frame, its rings' and with them the tag's and the source
    /// line's: frame out 0.15 s after the press, or the dissolve under Reduce Motion. Hold still and the length word in
    /// only from then, where those stood, so no words lie over the standing tag (the lead, 22 Nov 22:17).
    public static func leftAfterSeconds(reduceMotion: Bool) -> Double {
        MotionStyle.captureSequenceDelaySeconds
            + (reduceMotion ? MotionStyle.reduceMotionSeconds : MotionStyle.frameOut.delaySeconds + MotionStyle.frameOut.seconds)
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
/// Which marks get a name, and on which side: D20, decided by the lead under the owner's delegation
/// (UIUX-aimnames-DECIDED-BY-LEAD.md, option (a) made exact), as its screen's own code draws it
/// (`.cache/uiux/aimnames/build.mjs`: the web's rule with the phone's sizes). Every ring stays. Brightest first, a star
/// gets its name only when it has a name of its own (not titled by its catalog number, as the web tells one), is
/// brighter than magnitude 3.2, and its name runs into no ring's room and no name given before it; the name goes right
/// of its ring, left near the right edge, and tries the other side once when it runs into something. A body has no
/// catalog magnitude in this reply: it is named by the same rule after the stars [inference: the record names stars
/// only; a body keeps its name as the approved aiming screen and the web give one].
public enum SkyOverlayNames {
    /// The screen's numbers: the faintest star named, a name's width per character in ems, the room every ring keeps
    /// about its centre, a name's margin at each end, and how far inside the preview's sides a name (with its margins)
    /// stays.
    public static let magnitudeLimit = 3.2
    public static let markFrameHeightPt = 40.0
    public static let unnamedMarkFrameWidthPt = 40.0
    static let emPerChar = 0.56
    static let ringRoomPt = 9.0
    static let marginPt = 3.0
    static let sideInsetPt = 2.0

    /// Width of a centred Canvas holding one mark (`EarlyMark` on the wait): compact when unnamed, and when named at
    /// least twice the preview width and twice a 1 em-per-unit span plus gap, halo and margin, so no name kept by
    /// `names(_:widthPt:heightPt:)` can ever be clipped by its own Canvas frame.
    public static func markFrameWidthPt(name: String, named: Bool, previewWidthPt: Double) -> Double {
        guard named else { return unnamedMarkFrameWidthPt }
        let oneSide = SkyOverlayStyle.nameGapPt
            + Double(name.utf16.count) * SkyOverlayStyle.nameSizePt
            + SkyOverlayStyle.haloWidthPt + marginPt
        return max(2 * max(previewWidthPt, 0), 2 * oneSide)
    }

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

    /// The box a name takes beside its ring at the overlay's size: from 0.8 of its size above the centre to 0.5 below,
    /// with its margin at each end.
    static func box(_ placed: Placed, left: Bool) -> Box {
        let size = SkyOverlayStyle.nameSizePt
        let width = Double(placed.mark.name.utf16.count) * size * emPerChar  // as the web counts a name's length
        let start = left ? placed.x - SkyOverlayStyle.nameGapPt : placed.x + SkyOverlayStyle.nameGapPt
        return Box(x0: (left ? start - width : start) - marginPt, x1: (left ? start : start + width) + marginPt,
                   y0: placed.y - size * 0.8, y1: placed.y + size * 0.5)
    }

    /// Whether a mark may carry a name at all: a body, or a star with a name of its own brighter than the limit.
    static func mayBeNamed(_ mark: SkyMark) -> Bool {
        switch mark.source {
        case .ephemeris: true
        case .catalog: mark.name != mark.catalogSourceId && (mark.mag.map { $0 < magnitudeLimit } ?? false)
        }
    }

    /// For each mark that gets a name, by its id: whether the name sits on the left of its ring.
    public static func names(_ placed: [Placed], widthPt: Double, heightPt: Double) -> [String: Bool] {
        let order = placed.indices.filter { mayBeNamed(placed[$0].mark) }.sorted { a, b in
            let (ma, mb) = (placed[a].mark.mag ?? .infinity, placed[b].mark.mag ?? .infinity)
            return ma == mb ? a < b : ma < mb
        }
        let rooms = placed.map { Box(x0: $0.x - ringRoomPt, x1: $0.x + ringRoomPt, y0: $0.y - ringRoomPt, y1: $0.y + ringRoomPt) }
        let r = SkyOverlayStyle.ringRadiusPt
        var named: [Int: Bool] = [:]
        var given: [Box] = []
        for i in order {
            let p = placed[i]
            func clashes(_ name: Box) -> Bool {
                rooms.indices.contains { $0 != i && name.overlaps(rooms[$0]) } || given.contains(where: name.overlaps)
            }
            var onLeft = box(p, left: false).x1 > widthPt - sideInsetPt
            if clashes(box(p, left: onLeft)) { onLeft.toggle() }
            let name = box(p, left: onLeft)
            // On the preview: the name inside its sides, and (the drift pass's rule) the name and its named ring never cut
            // by an edge (the name's top is above the ring's and its foot above the ring's foot)
            let fits = name.x0 >= sideInsetPt && name.x1 <= widthPt - sideInsetPt && name.y0 >= 0
                && p.x - r >= 0 && p.x + r <= widthPt && p.y + r <= heightPt
            guard fits, !clashes(name) else { continue }
            named[i] = onLeft
            given.append(name)
        }
        var out: [String: Bool] = [:]
        for (i, onLeft) in named { out[placed[i].mark.id] = onLeft }
        return out
    }
}
