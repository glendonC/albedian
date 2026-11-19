import Foundation

/// The lock-on's early marks from the sky the running solve predicted (platform-10a and 10b: an approximate solution
/// with its pose estimate, served at `GET /api/ingest/jobs/{id}/predicted-sky`), read as the web reads it
/// (apps/web/src/reveal/earlyPose.ts `earlyPoseOf`, alignPose.ts `alignTargetOf`): stars to magnitude 3.2 and planets,
/// each placed in the stored frame through that pose's own WCS (`PoseWCS`), never through a pose the phone works out.
/// Every mark is `predicted` until the solve matches it (lockon-m5b).
public struct PredictedEarlySky: Equatable, Sendable {
    /// Where the early pose came from, as the lock-on record words them.
    public enum Source: String, Sendable { case pointing, moon, sun }

    public struct Target: Equatable, Sendable {
        public let id: String
        /// Its own name, or "" for a star titled by its catalogue number (D20: the ring stays, unnamed).
        public let name: String
        public let magnitude: Double?
        /// The catalog's own id ("HIP 27989"), so the lock-on finds the same object in the solve.
        public let catalogSourceId: String?
        /// Its place in the stored frame, pixel centres at whole numbers.
        public let xPx: Double
        public let yPx: Double
        /// A planet, which the ring drawing names by the body rule (D20).
        public var isPlanet = false
        public var evidence: EvidenceClass { .predicted }
    }

    public let source: Source
    /// Brightest first, a planet the record gives no magnitude ahead of the stars.
    public let targets: [Target]

    /// The web's ALIGN_MAGNITUDE_LIMIT.
    public static let magnitudeLimit = 3.2
    /// The source each pose method names (earlyPose.ts POSE_SOURCES).
    static let sources: [String: Source] = [
        "device_attitude": .pointing, "moon_camera_profile_level_head": .moon, "moon_horizon": .moon, "sun_horizon": .sun,
    ]

    /// The decided sentence for the source (UIUX-lockon-READY point 1; C13 for the iPhone's record).
    public var sentence: String {
        switch source {
        case .pointing: EarlyPose.sourceSentence
        case .moon: "Placed from the Moon: predicted until matched."
        case .sun: "Placed from the Sun: predicted until matched."
        }
    }

    /// The early sky for this upload, or nil: another upload's, not an approximate solution with a pose and a WCS, or a
    /// pose no record words.
    public static func read(_ value: JSONValue, sha256: String) -> PredictedEarlySky? {
        guard value["status"]?.stringValue == "approximate",
              let method = value["poseEstimate"]?["method"]?.stringValue, let source = sources[method],
              let wcsValue = value["wcs"], let wcs = PoseWCS.read(wcsValue),
              let findings = value["findings"]?.arrayValue else { return nil }
        if let named = value["sourceSha256"], named.stringValue != sha256 { return nil }
        let targets = findings.compactMap { finding -> Target? in
            guard finding["evidenceClass"]?.stringValue == "predicted", let id = finding["id"]?.stringValue,
                  let ra = finding["skyPosition"]?["raDeg"]?.numberValue, let dec = finding["skyPosition"]?["decDeg"]?.numberValue else { return nil }
            let kind = finding["catalogRef"]?["objectType"]?.stringValue
            let magnitude = finding["catalogRef"]?["apparentMagnitude"]?.numberValue
            guard kind == "planet" || (kind == "star" && (magnitude.map { $0 <= magnitudeLimit } ?? false)),
                  let at = wcs.pixel(raDeg: ra, decDeg: dec) else { return nil }
            let title = (finding["title"]?.stringValue ?? "").replacingOccurrences(of: " (Predicted Ephemeris)", with: "")
            return Target(id: id, name: properName(title) ?? "", magnitude: magnitude,
                          catalogSourceId: finding["catalogRef"]?["catalogSourceId"]?.stringValue, xPx: at.x, yPx: at.y,
                          isPlanet: kind == "planet")
        }
        let sorted = targets.enumerated().sorted { a, b in
            let (ma, mb) = (a.element.magnitude ?? -1, b.element.magnitude ?? -1)
            return ma == mb ? a.offset < b.offset : ma < mb
        }.map(\.element)
        return PredictedEarlySky(source: source, targets: sorted)
    }

    /// Each target as a mark with its place on the `widthPt` by `heightPt` frame the oriented photo covers (the wait's
    /// photo), for D20's ring drawing; none for a target the photo's cut leaves off. A star with no name of its own
    /// carries its catalogue id as its name, which D20 never draws. The marks' altitude and azimuth are unused here: the
    /// laptop's pose placed them.
    public func placed(frame: StoredFrame, widthPt: Double, heightPt: Double) -> [(mark: SkyMark, x: Double, y: Double)] {
        targets.compactMap { target in
            guard frame.isOnCard(xPx: target.xPx, yPx: target.yPx, width: widthPt, height: heightPt) else { return nil }
            let at = frame.placeCovering(xPx: target.xPx, yPx: target.yPx, onWidth: widthPt, height: heightPt)
            let name = target.name.isEmpty ? (target.catalogSourceId ?? target.id) : target.name
            var mark = SkyMark(id: target.id, name: name, altDeg: 0, azDeg: 0,
                               source: target.isPlanet ? .ephemeris : .catalog(release: ""))
            mark.mag = target.magnitude
            mark.catalogSourceId = target.isPlanet ? nil : (target.catalogSourceId ?? name)
            return (mark, at.x, at.y)
        }
    }

    /// A name, or nil for a catalogue number (predictedSkyMarks.ts `properName`): Gaia, HIP, HD, HR or TYC and a number,
    /// or digits alone.
    static func properName(_ name: String) -> String? {
        let trimmed = name.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return nil }
        if trimmed.range(of: #"^(Gaia DR\d+|HIP|HD|HR|TYC)\s*\d"#, options: [.regularExpression, .caseInsensitive]) != nil { return nil }
        if trimmed.range(of: #"^[\d\s+-]+$"#, options: .regularExpression) != nil { return nil }
        return trimmed
    }
}
