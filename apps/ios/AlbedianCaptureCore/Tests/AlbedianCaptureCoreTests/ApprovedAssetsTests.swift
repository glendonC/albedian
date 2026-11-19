import CoreGraphics
import CryptoKit
import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The approved font and icons in the app (owner's yes, 2026-11-12 and in the MOBILE session 2026-11-13): each the
/// bytes of its checked download, the icons the exact shapes the approved screens draw, the fonts the names the app
/// asks for, and the licence notices and provenance records beside them and bundled with the app.
final class ApprovedAssetsTests: XCTestCase {
    private let app = repositoryRoot.appendingPathComponent("apps/ios/AlbedianCapture")
    private let icons = ["arrow-right", "x", "paper-plane-tilt", "arrow-counter-clockwise", "arrow-left", "arrow-square-out", "wifi-slash", "warning-circle"]

    private func sha256(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    private func svg(_ name: String) throws -> Data {
        try Data(contentsOf: app.appendingPathComponent("Assets.xcassets/\(name).imageset/\(name).svg"))
    }

    /// The one path of a Phosphor icon file.
    private func path(_ name: String) throws -> String {
        let text = try XCTUnwrap(String(data: try svg(name), encoding: .utf8))
        let start = try XCTUnwrap(text.range(of: "d=\""))
        let end = try XCTUnwrap(text[start.upperBound...].firstIndex(of: "\""))
        XCTAssertEqual(text.components(separatedBy: "<path").count, 2, "\(name) holds one path")
        return String(text[start.upperBound..<end])
    }

    private func downloadsByPath() throws -> [String: [String: Any]] {
        let data = try Data(contentsOf: repositoryRoot.appendingPathComponent("data/downloads.json"))
        let root = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let items = try XCTUnwrap(root["downloads"] as? [[String: Any]])
        var byPath: [String: [String: Any]] = [:]
        for entry in items {
            if let path = entry["path"] as? String { byPath[path] = entry }
        }
        return byPath
    }

    func testTheIconsAreThePackagesFilesAndTheExactShapesTheApprovedScreensDraw() throws {
        let notices = repositoryRoot.appendingPathComponent("apps/ios/notices/phosphor")
        for name in icons {
            XCTAssertEqual(try svg(name), try Data(contentsOf: notices.appendingPathComponent("\(name).svg")), name)
        }
        // The two the web draws too are byte for byte the web's notice copies and the very path apps/web/src/look/Icon.tsx draws
        let webNotices = repositoryRoot.appendingPathComponent("apps/web/public/notices/phosphor")
        let webIcon = try String(contentsOf: repositoryRoot.appendingPathComponent("apps/web/src/look/Icon.tsx"), encoding: .utf8)
        for name in ["arrow-right", "x"] {
            XCTAssertEqual(try svg(name), try Data(contentsOf: webNotices.appendingPathComponent("\(name).svg")), name)
            XCTAssertTrue(webIcon.contains("'\(try path(name))'"), name)
        }
        // The back and open-link icons the Credits and Connect screens draw are the web's notice copies too
        for name in ["arrow-left", "arrow-square-out", "warning-circle"] {
            XCTAssertEqual(try svg(name), try Data(contentsOf: webNotices.appendingPathComponent("\(name).svg")), name)
        }
        XCTAssertEqual(
            try path("wifi-slash"),
            "M213.92,210.62a8,8,0,1,1-11.84,10.76l-52-57.15a60,60,0,0,0-57.41,7.24,8,8,0,1,1-9.42-12.93A75.43,75.43,0,0,1,128,144c1.28,0,2.55,0,3.82.1L104.9,114.49A108,108,0,0,0,61,135.31,8,8,0,0,1,49.73,134,8,8,0,0,1,51,122.77a124.27,124.27,0,0,1,41.71-21.66L69.37,75.4a155.43,155.43,0,0,0-40.29,24A8,8,0,0,1,18.92,87,171.87,171.87,0,0,1,58,62.86L42.08,45.38A8,8,0,1,1,53.92,34.62ZM128,192a12,12,0,1,0,12,12A12,12,0,0,0,128,192ZM237.08,87A172.3,172.3,0,0,0,106,49.4a8,8,0,1,0,2,15.87A158.33,158.33,0,0,1,128,64a156.25,156.25,0,0,1,98.92,35.37A8,8,0,0,0,237.08,87ZM195,135.31a8,8,0,0,0,11.24-1.3,8,8,0,0,0-1.3-11.24,124.25,124.25,0,0,0-51.73-24.2A8,8,0,1,0,150,114.24,108.12,108.12,0,0,1,195,135.31Z"
        )
        // The other two match the full Phosphor 2.1.1 regular paths the approved iPhone board draws
        XCTAssertEqual(
            try path("paper-plane-tilt"),
            "M227.32,28.68a16,16,0,0,0-15.66-4.08l-.15,0L19.57,82.84a16,16,0,0,0-2.49,29.8L102,154l41.3,84.87A15.86,15.86,0,0,0,157.74,248q.69,0,1.38-.06a15.88,15.88,0,0,0,14-11.51l58.2-191.94c0-.05,0-.1,0-.15A16,16,0,0,0,227.32,28.68ZM157.83,231.85l-.05.14,0-.07-40.06-82.3,48-48a8,8,0,0,0-11.31-11.31l-48,48L24.08,98.25l-.07,0,.14,0L216,40Z"
        )
        XCTAssertEqual(
            try path("arrow-counter-clockwise"),
            "M224,128a96,96,0,0,1-94.71,96H128A95.38,95.38,0,0,1,62.1,197.8a8,8,0,0,1,11-11.63A80,80,0,1,0,71.43,71.39a3.07,3.07,0,0,1-.26.25L44.59,96H72a8,8,0,0,1,0,16H24a8,8,0,0,1-8-8V56a8,8,0,0,1,16,0V85.8L60.25,60A96,96,0,0,1,224,128Z"
        )
    }

    func testTheFontsArePinnedInDownloadsAndRegisteredWithTheNamesTheAppAsksFor() throws {
        let expected = ["Geist-Regular.ttf": "Geist-Regular", "Geist-SemiBold.ttf": "Geist-SemiBold"]
        let downloads = try downloadsByPath()
        for (file, postScript) in expected {
            let data = try Data(contentsOf: app.appendingPathComponent("Fonts/\(file)"))
            let font = try XCTUnwrap(CGDataProvider(data: data as CFData).flatMap(CGFont.init), file)
            XCTAssertEqual(font.postScriptName as String?, postScript, file)
            let pin = try XCTUnwrap(downloads[".cache/geist-font/v1.9.2/ttf/\(file)"], file)
            XCTAssertEqual(pin["sha256"] as? String, sha256(data), file)
            XCTAssertEqual(pin["bytes"] as? Int, data.count, file)
            XCTAssertEqual(pin["licence"] as? String, "OFL-1.1", file)
        }
        let plist = try XCTUnwrap(PropertyListSerialization.propertyList(
            from: try Data(contentsOf: repositoryRoot.appendingPathComponent("apps/ios/AlbedianCapture-Info.plist")), format: nil) as? [String: Any])
        XCTAssertEqual(plist["UIAppFonts"] as? [String], expected.keys.sorted() + ["Geist-wght.woff2"])
        let style = try String(contentsOf: app.appendingPathComponent("UI/Style.swift"), encoding: .utf8)
        for postScript in expected.values { XCTAssertTrue(style.contains("\"\(postScript)\""), postScript) }
        // The light weight is the web's own variable font, byte for byte, and the app asks for its Geist-Light instance
        let variable = try Data(contentsOf: app.appendingPathComponent("Fonts/Geist-wght.woff2"))
        XCTAssertEqual(variable, try Data(contentsOf: repositoryRoot.appendingPathComponent("apps/web/public/fonts/geist/Geist-wght.woff2")))
        XCTAssertTrue(style.contains("\"Geist-Light\""))
    }

    func testCarriesTheLicenceNoticesAndProvenanceRecordsBesideTheAssetsAndInTheBundle() throws {
        let downloads = try downloadsByPath()
        let geistNotice = try Data(contentsOf: repositoryRoot.appendingPathComponent("apps/ios/notices/geist/OFL.txt"))
        let webGeistNotice = try Data(contentsOf: repositoryRoot.appendingPathComponent("apps/web/public/fonts/geist/OFL.txt"))
        let bundledGeistNotice = try Data(contentsOf: app.appendingPathComponent("Fonts/OFL.txt"))
        XCTAssertEqual(geistNotice, webGeistNotice)
        XCTAssertEqual(bundledGeistNotice, geistNotice)
        XCTAssertEqual(downloads[".cache/geist-font/v1.9.2/OFL.txt"]?["sha256"] as? String, sha256(geistNotice))

        let phosphorNotice = try Data(contentsOf: repositoryRoot.appendingPathComponent("apps/ios/notices/phosphor/LICENSE"))
        let webPhosphorNotice = try Data(contentsOf: repositoryRoot.appendingPathComponent("apps/web/public/notices/phosphor/LICENSE"))
        let bundledPhosphorNotice = try Data(contentsOf: app.appendingPathComponent("Notices/Phosphor-LICENSE.txt"))
        XCTAssertEqual(phosphorNotice, webPhosphorNotice)
        XCTAssertEqual(bundledPhosphorNotice, phosphorNotice)
        XCTAssertEqual(downloads[".cache/phosphor-icons/core-2.1.1.tgz"]?["licence"] as? String, "MIT")

        for folder in ["apps/ios/notices/geist", "apps/ios/notices/phosphor", "apps/ios/notices/eso"] {
            let dir = repositoryRoot.appendingPathComponent(folder)
            let recordData = try Data(contentsOf: dir.appendingPathComponent("PROVENANCE.json"))
            let record = try XCTUnwrap(JSONSerialization.jsonObject(with: recordData) as? [String: Any])
            XCTAssertEqual(record["format"] as? String, "albedian.asset-provenance", folder)
            XCTAssertEqual(record["formatVersion"] as? Int, 1, folder)
            let files = try XCTUnwrap(record["files"] as? [[String: Any]], folder)
            XCTAssertFalse(files.isEmpty, folder)
            for entry in files {
                let relPath = try XCTUnwrap(entry["path"] as? String, folder)
                let recordedSha = try XCTUnwrap(entry["sha256"] as? String, folder)
                let fileBytes = try Data(contentsOf: dir.appendingPathComponent(relPath))
                XCTAssertEqual(sha256(fileBytes), recordedSha, "\(folder)/\(relPath)")
            }
        }
    }
}
