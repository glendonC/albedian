import Foundation

/// firstuse-home-result-one (UIUX-iphone-firstuse-READY item 3): how far the home's Milky Way answers the phone's
/// tilt, each way on both axes, with the picture drawn that far past every edge. [proposed] 12 pt: the record lets it
/// move within 8 to 16 pt once seen beside the system's own Perspective Zoom wallpaper on a device.
public enum HomeTilt {
    public static let maxPt = 12.0
}

/// The result's names (UIUX-iphone-firstuse-READY item 3, the phone reveal's rule UIUX-reveal-READY iPhone 1, and D20's
/// rule as corrected 19 Nov: a name never runs into a brighter star's name or ring; the record's mock,
/// `.lanetools/firstuse_m/mock.py` in the UIUX worktree, is the exact rule): a matched star brighter than magnitude 3
/// with a name of its own (not its catalog number) keeps its name, brightest first, in Geist 11, 10 pt right of its
/// mark's centre, else 10 pt left; the name's box (2 pt more at each end, from 8 pt above the centre to 6 below) stays
/// 4 pt inside the card's sides and on the card, and runs into no brighter star's mark and no name given before it.
public enum ResultNames {
    public static let sizePt = 11.0
    public static let gapPt = 10.0
    /// As the aim overlay's names count a name's length (`SkyOverlayNames`).
    static let emPerChar = 0.56
    /// The room a mark keeps about its centre: its ticks reach 7.5 pt (`CatalogMarks`).
    static let roomPt = 7.5
    static let marginPt = 2.0
    static let sideInsetPt = 4.0
    static let abovePt = 8.0
    static let belowPt = 6.0

    public struct Placed: Equatable, Sendable {
        public let mark: SolutionReadout.Mark
        public let x: Double
        public let y: Double
        public init(mark: SolutionReadout.Mark, x: Double, y: Double) {
            self.mark = mark
            self.x = x
            self.y = y
        }
    }

    public struct Box: Equatable, Sendable {
        public let x0, x1, y0, y1: Double
        public func overlaps(_ other: Box) -> Bool { x0 < other.x1 && x1 > other.x0 && y0 < other.y1 && y1 > other.y0 }
    }

    /// The box a name takes beside its mark: from 8 pt above the centre to 6 below, with its margin at each end.
    public static func box(_ placed: Placed, left: Bool) -> Box {
        let width = Double(placed.mark.title.utf16.count) * sizePt * emPerChar
        let start = left ? placed.x - gapPt : placed.x + gapPt
        return Box(x0: (left ? start - width : start) - marginPt, x1: (left ? start : start + width) + marginPt,
                   y0: placed.y - abovePt, y1: placed.y + belowPt)
    }

    /// The room a mark keeps about its centre.
    public static func room(_ placed: Placed) -> Box {
        Box(x0: placed.x - roomPt, x1: placed.x + roomPt, y0: placed.y - roomPt, y1: placed.y + roomPt)
    }

    /// Whether a mark may carry a name: brighter than magnitude 3 and named by more than its catalog number.
    static func mayBeNamed(_ mark: SolutionReadout.Mark) -> Bool {
        mark.isNamedOnThePhone && mark.title != mark.catalogSourceId
    }

    /// For each mark that gets a name, by its id: whether the name sits on the left of its mark.
    public static func names(_ placed: [Placed], widthPt: Double, heightPt: Double) -> [String: Bool] {
        let order = placed.indices.filter { mayBeNamed(placed[$0].mark) }.sorted { a, b in
            let (ma, mb) = (placed[a].mark.apparentMagnitude ?? .infinity, placed[b].mark.apparentMagnitude ?? .infinity)
            return ma == mb ? a < b : ma < mb
        }
        let rooms = placed.map(room)
        let mags = placed.map { $0.mark.apparentMagnitude ?? .infinity }
        var named: [Int: Bool] = [:]
        var given: [Box] = []
        for i in order {
            // Only a brighter star's mark, and the names given before (all brighter), stand in a name's way
            func clashes(_ name: Box) -> Bool {
                rooms.indices.contains { mags[$0] < mags[i] && name.overlaps(rooms[$0]) } || given.contains(where: name.overlaps)
            }
            func fits(_ name: Box) -> Bool {
                name.x0 >= sideInsetPt && name.x1 <= widthPt - sideInsetPt && name.y0 >= 0 && name.y1 <= heightPt
            }
            // Right, else left
            guard let onLeft = [false, true].first(where: { fits(box(placed[i], left: $0)) && !clashes(box(placed[i], left: $0)) }) else { continue }
            named[i] = onLeft
            given.append(box(placed[i], left: onLeft))
        }
        var out: [String: Bool] = [:]
        for (i, onLeft) in named { out[placed[i].mark.id] = onLeft }
        return out
    }
}
