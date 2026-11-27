// The app's icon (app-icon-one, DECIDED-BY-LEAD 25 Nov: from what the brand already has and nothing new): the
// website's favicon drawing (apps/web/index.html, the logo's 32 unit mark: the moon of radius 11.5 at (15.2, 16) with
// Albedian's star cut out) in the home's white (#FFFFFF, the logo's ink) on the home's black (#000000, the field), as
// one opaque 1024 pixel sRGB square. Xcode makes every size the iPhone asks for from it.
//
//   swift apps/ios/scripts/app-icon.swift apps/ios/AlbedianCapture/Assets.xcassets/AppIcon.appiconset/app-icon.png

import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

let arguments = CommandLine.arguments
guard arguments.count == 2 else {
    FileHandle.standardError.write(Data("usage: swift app-icon.swift <out.png>\n".utf8))
    exit(2)
}

let side = 1024
let unit = Double(side) / 32
// The favicon's path, y down in its 32 unit drawing; Core Graphics draws y up.
func point(_ x: Double, _ y: Double) -> CGPoint { CGPoint(x: x * unit, y: Double(side) - y * unit) }

guard let space = CGColorSpace(name: CGColorSpace.sRGB),
      let context = CGContext(data: nil, width: side, height: side, bitsPerComponent: 8, bytesPerRow: side * 4,
                              space: space, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { exit(1) }

context.setFillColor(red: 0, green: 0, blue: 0, alpha: 1)
context.fill(CGRect(x: 0, y: 0, width: side, height: side))

// M18.2 2.2 Q18.2 13.8 29.8 13.8 Q18.2 13.8 18.2 25.4 Q18.2 13.8 6.6 13.8 Q18.2 13.8 18.2 2.2 Z, as the mask's black
let centre = point(18.2, 13.8)
let star = CGMutablePath()
star.move(to: point(18.2, 2.2))
star.addQuadCurve(to: point(29.8, 13.8), control: centre)
star.addQuadCurve(to: point(18.2, 25.4), control: centre)
star.addQuadCurve(to: point(6.6, 13.8), control: centre)
star.addQuadCurve(to: point(18.2, 2.2), control: centre)
star.closeSubpath()
context.addRect(CGRect(x: 0, y: 0, width: side, height: side))
context.addPath(star)
context.clip(using: .evenOdd)

// circle cx 15.2 cy 16.0 r 11.5, filled with the ink
context.setFillColor(red: 1, green: 1, blue: 1, alpha: 1)
let corner = point(15.2 - 11.5, 16.0 + 11.5)
context.fillEllipse(in: CGRect(x: corner.x, y: corner.y, width: 23 * unit, height: 23 * unit))

guard let image = context.makeImage(),
      let out = CGImageDestinationCreateWithURL(URL(fileURLWithPath: arguments[1]) as CFURL, UTType.png.identifier as CFString, 1, nil)
else { exit(1) }
CGImageDestinationAddImage(out, image, nil)
guard CGImageDestinationFinalize(out) else { exit(1) }
