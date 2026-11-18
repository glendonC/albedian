import AlbedianCaptureCore
import SwiftUI

/// An evidence class, never a button: a tinted tag 28 pt high with both its glyph and its word (`ControlStyle` and
/// `packages/design-system/src/look.ts`), so colour is never the only cue.
struct EvidenceTag: View {
    let evidence: ControlStyle.Evidence

    var body: some View {
        HStack(spacing: CGFloat(ControlStyle.tagGap)) {
            EvidenceGlyph(evidence: evidence)
                .frame(width: CGFloat(ControlStyle.tagGlyphSize), height: CGFloat(ControlStyle.tagGlyphSize))
            Text(evidence.word)
                .font(AlbedianFont.regular(ControlStyle.tagFontSize))
                .tracking(CGFloat(ControlStyle.tagLetterSpacingEm * ControlStyle.tagFontSize))
                .lineLimit(1)
        }
        .foregroundStyle(Color(evidence.colour))
        .padding(.leading, CGFloat(ControlStyle.tagLeadingPadding))
        .padding(.trailing, CGFloat(ControlStyle.tagTrailingPadding))
        .frame(height: CGFloat(ControlStyle.tagHeight))
        .background(Capsule().fill(Color(evidence.tint)))
        .accessibilityElement(children: .combine)
    }
}

struct EvidenceGlyph: View {
    let evidence: ControlStyle.Evidence

    var body: some View {
        Canvas { context, size in
            let side = min(size.width, size.height)
            let centre = CGPoint(x: size.width / 2, y: size.height / 2)
            let unit22 = side / 22
            func ring(_ radius: Double) -> Path {
                Path(ellipseIn: CGRect(
                    x: centre.x - radius * unit22,
                    y: centre.y - radius * unit22,
                    width: 2 * radius * unit22,
                    height: 2 * radius * unit22
                ))
            }
            let line22 = 1.3 * unit22
            switch evidence {
            case .observed:
                context.stroke(ring(5.5), with: .foreground, lineWidth: line22)
                context.fill(ring(1.4), with: .foreground)
            case .catalogMatched:
                context.stroke(ring(5.5), with: .foreground, lineWidth: line22)
                var ticks = Path()
                for (from, to) in [((0.0, -10.0), (0.0, -6.0)), ((0, 6), (0, 10)), ((-10, 0), (-6, 0)), ((6, 0), (10, 0))] {
                    ticks.move(to: CGPoint(x: centre.x + from.0 * unit22, y: centre.y + from.1 * unit22))
                    ticks.addLine(to: CGPoint(x: centre.x + to.0 * unit22, y: centre.y + to.1 * unit22))
                }
                context.stroke(ticks, with: .foreground, lineWidth: line22)
            case .predicted:
                context.stroke(ring(6), with: .foreground, style: StrokeStyle(lineWidth: line22, dash: [2.4 * unit22, 2.4 * unit22]))
            case .simulated:
                // Corner brackets in a 12 by 12 box at stroke 1.1 (LOOK_GLYPHS.simulated / G.sim)
                let unit12 = side / 12
                let origin = CGPoint(x: centre.x - side / 2, y: centre.y - side / 2)
                func pt(_ x: Double, _ y: Double) -> CGPoint {
                    CGPoint(x: origin.x + x * unit12, y: origin.y + y * unit12)
                }
                var brackets = Path()
                for (a, b, c) in [
                    ((1.0, 3.8), (1.0, 1.0), (3.8, 1.0)),
                    ((8.2, 1.0), (11.0, 1.0), (11.0, 3.8)),
                    ((11.0, 8.2), (11.0, 11.0), (8.2, 11.0)),
                    ((3.8, 11.0), (1.0, 11.0), (1.0, 8.2)),
                ] {
                    brackets.move(to: pt(a.0, a.1))
                    brackets.addLine(to: pt(b.0, b.1))
                    brackets.addLine(to: pt(c.0, c.1))
                }
                context.stroke(brackets, with: .foreground, lineWidth: 1.1 * unit12)
            }
        }
        .accessibilityHidden(true)
    }
}
