import Foundation

/// The lock-on's pass on the iPhone's wait (D31 item 3, UIUX-iphone-lockon-READY; the move is MOTION-READY-iphone
/// section 5): when the solve passes, each early mark the solve matched goes to its matched detection on the wait's photo
/// and every other early mark leaves. The same star is known by its catalog id: the early mark's sky-context id is the
/// matched finding's `catalogReferenceId`. Nothing moves for a solution that is not calibrated, and a detection the
/// photo's cut leaves off the frame is no place to go.
public enum LockOnTravel {
    /// Where each early mark goes, by its id, in the `widthPt` by `heightPt` frame the oriented photo covers; a mark with
    /// no entry leaves.
    public static func targets(early: [SkyMark], readout: SolutionReadout, frame: StoredFrame, widthPt: Double,
                               heightPt: Double) -> [String: (x: Double, y: Double)] {
        guard readout.status == .calibrated, readout.hasWCS else { return [:] }
        let ids = Set(early.map(\.id))
        var out: [String: (x: Double, y: Double)] = [:]
        for mark in readout.marks {
            guard let star = mark.catalogReferenceId, ids.contains(star),
                  frame.isOnCard(xPx: mark.xPx, yPx: mark.yPx, width: widthPt, height: heightPt) else { continue }
            out[star] = frame.placeCovering(xPx: mark.xPx, yPx: mark.yPx, onWidth: widthPt, height: heightPt)
        }
        return out
    }
}
