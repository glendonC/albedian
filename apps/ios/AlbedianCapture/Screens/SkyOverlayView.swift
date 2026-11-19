import AlbedianCaptureCore
import SwiftUI

/// The aim overlay (mobile-5, arrive iPhone 2): the marks the kept laptop predicts, each an amber dashed ring with its
/// name where the phone's newest attitude and the preview's field of view put it, redrawn with every frame. Every mark
/// is `predicted`; none is drawn without an attitude, a field of view, or marks.
struct SkyOverlayView: View {
    let marks: [SkyMark]
    let attitude: PointingRecord.Quaternion?
    let acrossFovDeg: Double?
    let width: Double
    let height: Double

    /// The marks on the preview now, with where each falls: only where the whole ring is inside the preview, as the
    /// record draws every ring (a mark at the edge was drawn half outside it, cut with its name: the drift pass of 19 Nov);
    /// at the smaller ring, a named one being kept wholly inside by the naming rule.
    static func placed(_ marks: [SkyMark], attitude: PointingRecord.Quaternion?, acrossFovDeg: Double?, width: Double,
                       height: Double) -> [(SkyMark, CGPoint)] {
        guard let attitude, let acrossFovDeg else { return [] }
        return marks.compactMap { mark in
            SkyProjection.point(altDeg: mark.altDeg, azDeg: mark.azDeg, attitude: attitude, widthPt: width, heightPt: height,
                                acrossFovDeg: acrossFovDeg, marginPt: -SkyOverlayStyle.unnamedRingRadiusPt).map { (mark, CGPoint(x: $0.x, y: $0.y)) }
        }
    }

    var body: some View {
        PlacedSkyOverlay(placed: Self.placed(marks, attitude: attitude, acrossFovDeg: acrossFovDeg, width: width, height: height),
                         width: width, height: height)
    }
}

/// D20's rings and names for marks already placed on a `width` by `height` frame: from the phone's projection
/// (`SkyOverlayView`) or from the laptop's early sky (lockon-m5). Every mark is `predicted`.
struct PlacedSkyOverlay: View {
    let placed: [(SkyMark, CGPoint)]
    let width: Double
    let height: Double

    var body: some View {
        Canvas { context, _ in
            let amber = Color(ControlStyle.Evidence.predicted.colour)
            let ring = StrokeStyle(lineWidth: CGFloat(SkyOverlayStyle.ringLineWidthPt), dash: SkyOverlayStyle.ringDash.map { CGFloat($0) })
            // D20 (`SkyOverlayNames`): every ring stays; names only for named stars brighter than 3.2 that fit
            let names = SkyOverlayNames.names(placed.map { SkyOverlayNames.Placed(mark: $0.0, x: $0.1.x, y: $0.1.y) }, widthPt: width, heightPt: height)
            for (mark, at) in placed {
                // D20: a named ring at r 6.5, one without a name at r 4.5
                let r = names[mark.id] == nil ? SkyOverlayStyle.unnamedRingRadiusPt : SkyOverlayStyle.ringRadiusPt
                context.stroke(Path(ellipseIn: CGRect(x: at.x - r, y: at.y - r, width: 2 * r, height: 2 * r)), with: .color(amber), style: ring)
                guard let onLeft = names[mark.id] else { continue }
                let size = SkyOverlayStyle.nameSizePt
                func name(_ colour: Color) -> GraphicsContext.ResolvedText {
                    context.resolve(Text(mark.name).font(AlbedianFont.regular(size)).tracking(CGFloat(SkyOverlayStyle.nameTracking * size))
                        .foregroundStyle(colour))
                }
                let fill = name(amber)
                let gap = SkyOverlayStyle.nameGapPt
                let anchorAt = CGPoint(x: onLeft ? at.x - gap : at.x + gap, y: at.y)
                let anchor: UnitPoint = onLeft ? .trailing : .leading
                // The record's dark edge (a 3 px stroke under the fill): the name in black at 75 percent, drawn a half
                // stroke out on eight sides, then the amber name on top
                let halo = name(Color.black.opacity(SkyOverlayStyle.haloOpacity))
                let out = SkyOverlayStyle.haloWidthPt / 2
                for (dx, dy) in [(-1.0, 0.0), (1, 0), (0, -1), (0, 1), (-0.7, -0.7), (0.7, -0.7), (-0.7, 0.7), (0.7, 0.7)] {
                    context.draw(halo, at: CGPoint(x: anchorAt.x + dx * out, y: anchorAt.y + dy * out), anchor: anchor)
                }
                context.draw(fill, at: anchorAt, anchor: anchor)
            }
        }
        .frame(width: CGFloat(width), height: CGFloat(height))
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}
