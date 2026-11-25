import CoreGraphics
import Foundation
import ImageIO
import XCTest

/// home-edge-and-name-one (DECIDED-BY-LEAD on first-use-walk-m's findings five and six): the home's Milky Way draws its
/// stars as points at every height, where the panorama's rows near the poles stretched them sideways; and the app is
/// named Albedian, one word, wherever the system names it.
final class HomeEdgeAndNameTests: XCTestCase {
    private let app = repositoryRoot.appendingPathComponent("apps/ios")

    func testTheSystemNamesTheAppAlbedianInEveryConfiguration() throws {
        let project = try String(contentsOf: app.appendingPathComponent("AlbedianCapture.xcodeproj/project.pbxproj"), encoding: .utf8)
        let names = project.components(separatedBy: "\n")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { $0.hasPrefix("INFOPLIST_KEY_CFBundleDisplayName = ") }
            .map { String($0.dropFirst("INFOPLIST_KEY_CFBundleDisplayName = ".count).dropLast()) }
        XCTAssertEqual(names, ["Albedian", "Albedian"], "the Debug and the Release name, as the app's own home says it")
    }

    /// Luminance of the home's picture, row by row (8 bit, as the device draws it in grey).
    private func homeLuminance() throws -> (width: Int, height: Int, pixels: [UInt8]) {
        let url = app.appendingPathComponent("AlbedianCapture/Assets.xcassets/milky-way-home.imageset/milky-way-home.jpg")
        let source = try XCTUnwrap(CGImageSourceCreateWithURL(url as CFURL, nil))
        let image = try XCTUnwrap(CGImageSourceCreateImageAtIndex(source, 0, nil))
        let width = image.width, height = image.height
        var pixels = [UInt8](repeating: 0, count: width * height)
        let drawn = pixels.withUnsafeMutableBytes { buffer -> Bool in
            guard let context = CGContext(data: buffer.baseAddress, width: width, height: height, bitsPerComponent: 8,
                                          bytesPerRow: width, space: CGColorSpaceCreateDeviceGray(),
                                          bitmapInfo: CGImageAlphaInfo.none.rawValue) else { return false }
            context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        XCTAssertTrue(drawn)
        return (width, height, pixels)
    }

    /// How much more each pixel's light runs on sideways than downward over a band of rows: the correlation with the pixel
    /// two to its right less the correlation with the pixel two below. About zero where stars are points.
    private func sidewaysRun(_ luma: (width: Int, height: Int, pixels: [UInt8]), rows: Range<Int>) -> Double {
        func correlation(dx: Int, dy: Int) -> Double {
            var a: [Double] = [], b: [Double] = []
            for y in rows where y + 2 < luma.height {
                for x in 0..<(luma.width - 2) {
                    a.append(Double(luma.pixels[y * luma.width + x]))
                    b.append(Double(luma.pixels[(y + dy) * luma.width + x + dx]))
                }
            }
            let ma = a.reduce(0, +) / Double(a.count), mb = b.reduce(0, +) / Double(b.count)
            var ab = 0.0, aa = 0.0, bb = 0.0
            for i in a.indices {
                ab += (a[i] - ma) * (b[i] - mb); aa += (a[i] - ma) * (a[i] - ma); bb += (b[i] - mb) * (b[i] - mb)
            }
            return ab / (aa * bb).squareRoot()
        }
        return correlation(dx: 2, dy: 0) - correlation(dx: 0, dy: 2)
    }

    func testTheHomeSkyDrawsItsStarsAsPointsAtItsTopAndFootEdges() throws {
        let luma = try homeLuminance()
        XCTAssertEqual(luma.width, 1386)
        XCTAssertEqual(luma.height, 3000)
        for top in [0, 150, 300, luma.height - 450, luma.height - 300, luma.height - 150] {
            XCTAssertLessThan(sidewaysRun(luma, rows: top..<(top + 150)), 0.05, "rows \(top) to \(top + 149) draw their stars as points")
        }
    }

    func testTheHomeSkyRunsNoBandSideways() throws {
        let luma = try homeLuminance()
        for top in stride(from: 0, to: luma.height, by: 150) {
            XCTAssertLessThan(sidewaysRun(luma, rows: top..<min(top + 150, luma.height)), 0.08, "rows \(top) to \(top + 149)")
        }
    }
}
