import Foundation

/// The lock-on's pass on the iPhone's wait (D31 item 3, UIUX-iphone-lockon-READY; the move is MOTION-READY-iphone
/// section 5): when the solve passes, each early mark the solve matched goes to its matched detection on the wait's photo
/// and every other early mark leaves. The same star is known by its catalog's own id (`catalogSourceId`, "HIP 71860",
/// "Gaia DR3 <n>"), which the sky context and the solution both carry. Nothing moves for a solution that is not calibrated, and a detection the
/// photo's cut leaves off the frame is no place to go.
public enum LockOnTravel {
    /// Where each early mark goes, by its id, in the `widthPt` by `heightPt` frame the oriented photo covers; a mark with
    /// no entry leaves.
    public static func targets(early: [SkyMark], readout: SolutionReadout, frame: StoredFrame, widthPt: Double,
                               heightPt: Double) -> [String: (x: Double, y: Double)] {
        guard readout.status == .calibrated, readout.hasWCS else { return [:] }
        var byCatalog: [String: String] = [:]
        for mark in early { if let source = mark.catalogSourceId, byCatalog[source] == nil { byCatalog[source] = mark.id } }
        var out: [String: (x: Double, y: Double)] = [:]
        for mark in readout.marks {
            guard let source = mark.catalogSourceId, let earlyId = byCatalog[source],
                  frame.isOnCard(xPx: mark.xPx, yPx: mark.yPx, width: widthPt, height: heightPt) else { continue }
            out[earlyId] = frame.placeCovering(xPx: mark.xPx, yPx: mark.yPx, onWidth: widthPt, height: heightPt)
        }
        return out
    }
}

/// The lock-on's fades for one early mark (D31 item 3 with MOTION-READY-iphone section 5) and its name
/// (UIUX-iphone-firstuse-READY item 3: the amber names ride their rings and take the matched colour with MOTION's class
/// change). `moved` is the pass begun (the travel, or the dissolve under Reduce Motion), `settled` the travel over,
/// `matched` the solve placing the mark's star on the photo.
public enum LockOnFade {
    /// The early ring: whole until the pass; then a ring the solve does not place leaves, a matched ring travels whole
    /// and goes at rest in the class change, and under Reduce Motion every ring goes where it stood in the dissolve.
    public static func ring(matched: Bool, moved: Bool, settled: Bool, reduceMotion: Bool) -> Double {
        moved && (!matched || reduceMotion || settled) ? 0 : 1
    }

    /// The matched mark (and under Reduce Motion the name in the matched colour beside it): it comes at rest in the
    /// class change, or at its matched place in the dissolve under Reduce Motion.
    public static func matchedMark(moved: Bool, settled: Bool, reduceMotion: Bool) -> Double {
        reduceMotion ? (moved ? 1 : 0) : (settled ? 1 : 0)
    }

    /// An early ring's name: it rides its ring and stays with a matched one; it leaves with a ring the solve does not
    /// place; under Reduce Motion the amber name goes where it stood, with its ring.
    public static func name(matched: Bool, moved: Bool, reduceMotion: Bool) -> Double {
        moved && (!matched || reduceMotion) ? 0 : 1
    }

    /// How far a riding name has taken the matched colour: all of the way once its matched ring is at rest, none before,
    /// and none for a name that only leaves (an unmatched ring's, or the amber name under Reduce Motion).
    public static func nameShare(matched: Bool, settled: Bool, reduceMotion: Bool) -> Double {
        matched && settled && !reduceMotion ? 1 : 0
    }

    /// The name's colour `share` of the way from the predicted amber to the catalog matched colour, in sRGB, held to
    /// the two ends.
    public static func nameColour(_ share: Double) -> ControlStyle.Colour {
        let amber = ControlStyle.Evidence.predicted.colour
        let matched = ControlStyle.Evidence.catalogMatched.colour
        let t = min(max(share, 0), 1)
        func mix(_ a: Double, _ b: Double) -> Double { a * (1 - t) + b * t }
        return ControlStyle.Colour(red: mix(amber.red, matched.red), green: mix(amber.green, matched.green),
                                   blue: mix(amber.blue, matched.blue), opacity: mix(amber.opacity, matched.opacity))
    }
}

private extension ControlStyle.Colour {
    init(red: Double, green: Double, blue: Double, opacity: Double) {
        self.red = red
        self.green = green
        self.blue = blue
        self.opacity = opacity
    }
}
