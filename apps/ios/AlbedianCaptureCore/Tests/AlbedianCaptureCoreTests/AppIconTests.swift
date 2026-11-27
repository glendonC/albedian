import CoreGraphics
import Foundation
import ImageIO
import XCTest
@testable import AlbedianCaptureCore

/// app-icon-one (DECIDED-BY-LEAD 25 Nov: the app gets an icon from what the brand already has and nothing new): the
/// wordmark's own mark, the moon with Albedian's star cut out in the logo's 32 unit drawing framed as the website's
/// favicon frames it, in the home's white (the logo's ink) on the home's black (the field), as one 1024 pixel square
/// from which Xcode makes every size the iPhone asks for.
final class AppIconTests: XCTestCase {
    private let app = repositoryRoot.appendingPathComponent("apps/ios")
    private var iconSet: URL { app.appendingPathComponent("AlbedianCapture/Assets.xcassets/AppIcon.appiconset") }

    func testTheIconSetIsOneSquareOf1024ForTheIPhone() throws {
        let data = try Data(contentsOf: iconSet.appendingPathComponent("Contents.json"))
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let images = try XCTUnwrap(json["images"] as? [[String: String]])
        XCTAssertEqual(images, [["filename": "app-icon.png", "idiom": "universal", "platform": "ios", "size": "1024x1024"]])
    }

    func testEveryConfigurationNamesTheIconSet() throws {
        let project = try String(contentsOf: app.appendingPathComponent("AlbedianCapture.xcodeproj/project.pbxproj"), encoding: .utf8)
        let names = project.components(separatedBy: "\n")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { $0.hasPrefix("ASSETCATALOG_COMPILER_APPICON_NAME = ") }
        XCTAssertEqual(names, ["ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon;", "ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon;"])
    }

    private func iconImage() throws -> CGImage {
        let url = iconSet.appendingPathComponent("app-icon.png")
        let source = try XCTUnwrap(CGImageSourceCreateWithURL(url as CFURL, nil))
        XCTAssertEqual(CGImageSourceGetType(source) as String?, "public.png")
        return try XCTUnwrap(CGImageSourceCreateImageAtIndex(source, 0, nil))
    }

    func testTheIconIsAnOpaqueSRGBSquareOf1024() throws {
        let image = try iconImage()
        XCTAssertEqual(image.width, 1024)
        XCTAssertEqual(image.height, 1024)
        XCTAssertTrue([.none, .noneSkipLast, .noneSkipFirst].contains(image.alphaInfo), "no alpha channel, as the App Store asks")
        XCTAssertEqual(image.colorSpace?.name, CGColorSpace.sRGB)
    }

    /// Red, green and blue of every pixel, read as 8 bit sRGB.
    private func rgb(_ image: CGImage) throws -> [UInt8] {
        let side = 1024
        var pixels = [UInt8](repeating: 0, count: side * side * 4)
        let drawn = pixels.withUnsafeMutableBytes { buffer -> Bool in
            guard let space = CGColorSpace(name: CGColorSpace.sRGB),
                  let context = CGContext(data: buffer.baseAddress, width: side, height: side, bitsPerComponent: 8,
                                          bytesPerRow: side * 4, space: space,
                                          bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return false }
            context.draw(image, in: CGRect(x: 0, y: 0, width: side, height: side))
            return true
        }
        XCTAssertTrue(drawn)
        return pixels
    }

    /// The mark drawn here from the logo's own numbers (the star `ShutterGeometry` holds, the moon of radius 11.5 at its
    /// centre), ink on field, rows from the top.
    private func expectedMark() throws -> [UInt8] {
        let side = 1024
        let unit = Double(side) / 32
        func point(_ x: Double, _ y: Double) -> CGPoint { CGPoint(x: x * unit, y: Double(side) - y * unit) }
        let ink = ControlStyle.logoInk, field = ControlStyle.field
        var pixels = [UInt8](repeating: 0, count: side * side * 4)
        let drawn = pixels.withUnsafeMutableBytes { buffer -> Bool in
            guard let space = CGColorSpace(name: CGColorSpace.sRGB),
                  let context = CGContext(data: buffer.baseAddress, width: side, height: side, bitsPerComponent: 8,
                                          bytesPerRow: side * 4, space: space,
                                          bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return false }
            context.setFillColor(red: field.red, green: field.green, blue: field.blue, alpha: 1)
            context.fill(CGRect(x: 0, y: 0, width: side, height: side))
            let star = CGMutablePath()
            let points = ShutterGeometry.starPointsInLogo
            let centre = point(ShutterGeometry.starCentreInLogo.x, ShutterGeometry.starCentreInLogo.y)
            star.move(to: point(points[0].x, points[0].y))
            for next in points.dropFirst() + [points[0]] { star.addQuadCurve(to: point(next.x, next.y), control: centre) }
            star.closeSubpath()
            context.addRect(CGRect(x: 0, y: 0, width: side, height: side))
            context.addPath(star)
            context.clip(using: .evenOdd)
            let moon = ShutterGeometry.moonCentreInLogo
            let corner = point(moon.x - 11.5, moon.y + 11.5)
            context.setFillColor(red: ink.red, green: ink.green, blue: ink.blue, alpha: 1)
            context.fillEllipse(in: CGRect(x: corner.x, y: corner.y, width: 23 * unit, height: 23 * unit))
            return true
        }
        XCTAssertTrue(drawn)
        return pixels
    }

    func testTheIconIsTheLogosMarkInTheHomesWhiteOnTheHomesBlack() throws {
        let icon = try rgb(try iconImage())
        let mark = try expectedMark()
        var total = 0, worst = 0, ink = 0, field = 0, notGrey = 0
        for pixel in stride(from: 0, to: icon.count, by: 4) {
            let r = Int(icon[pixel]), g = Int(icon[pixel + 1]), b = Int(icon[pixel + 2])
            if r != g || g != b { notGrey += 1 }
            if r == 255 { ink += 1 }
            if r == 0 { field += 1 }
            let difference = abs(r - Int(mark[pixel]))
            total += difference
            worst = max(worst, difference)
        }
        let count = icon.count / 4
        XCTAssertEqual(notGrey, 0, "white on black only, with no tint")
        XCTAssertLessThan(Double(total) / Double(count), 0.1, "the logo's drawing, pixel for pixel on average")
        XCTAssertLessThanOrEqual(worst, 64, "an edge pixel differs by its antialiasing at most")
        // The moon less its star covers 0.317 of the square (the moon pi 11.5 squared over 32 squared, 0.406, less the star)
        XCTAssertGreaterThan(Double(ink) / Double(count), 0.30)
        XCTAssertLessThan(Double(ink) / Double(count), 0.40)
        XCTAssertGreaterThan(Double(field) / Double(count), 0.55)
        // The corners and the cut star's centre are the field; the moon's lower left is the ink
        for (x, y) in [(0, 0), (1023, 0), (0, 1023), (1023, 1023), (582, 442)] {
            XCTAssertEqual(icon[(y * 1024 + x) * 4], 0, "(\(x), \(y)) is the home's black")
        }
        XCTAssertEqual(icon[(700 * 1024 + 380) * 4], 255, "the moon is the home's white")
    }
}
