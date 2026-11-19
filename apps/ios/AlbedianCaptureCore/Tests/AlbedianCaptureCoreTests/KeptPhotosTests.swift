import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// D13, INTERIM (UIUX-iphone-kept-QUESTION): photos kept from earlier sessions and never sent, oldest first, and the
/// words the home's pill, the line under the photo and the sending row say.
final class KeptPhotosTests: XCTestCase {
    private var folder: URL!

    override func setUpWithError() throws {
        folder = FileManager.default.temporaryDirectory.appendingPathComponent("kept-\(UUID().uuidString)")
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: folder) }

    func testKeptPhotosComeOldestFirstByWhenTheirFilesWereWritten() throws {
        let store = CaptureStore(folder: folder)
        let photos = (0..<3).map { Data("photo \($0) bytes, not a picture".utf8) }
        var hashes: [String] = []
        for photo in photos { hashes.append(try store.keep(photo: photo, contentType: .jpeg, recordWire: nil).sha256) }
        // Give the files times out of their hash order: the second written first, then the third, then the first
        let base = Date(timeIntervalSince1970: 2_000_000_000)
        for (index, hash) in hashes.enumerated() {
            let when = base.addingTimeInterval([30, 10, 20][index])
            try FileManager.default.setAttributes([.modificationDate: when], ofItemAtPath: folder.appendingPathComponent("\(hash).jpg").path)
        }
        XCTAssertEqual(try store.keptOldestFirst().map(\.sha256), [hashes[1], hashes[2], hashes[0]])
    }

    func testWithoutTimesTheyComeInTheStoresOwnOrder() throws {
        final class NoTimes: CaptureFiles, @unchecked Sendable {
            let inner = SystemCaptureFiles()
            func write(_ data: Data, to url: URL) throws { try inner.write(data, to: url) }
            func read(_ url: URL) throws -> Data { try inner.read(url) }
            func remove(_ url: URL) throws { try inner.remove(url) }
            func names(in folder: URL) throws -> [String] { try inner.names(in: folder) }
            func makeFolder(_ url: URL) throws { try inner.makeFolder(url) }
        }
        let store = CaptureStore(folder: folder, files: NoTimes())
        for index in 0..<3 { _ = try store.keep(photo: Data("kept \(index)".utf8), contentType: .heic, recordWire: nil) }
        XCTAssertEqual(try store.keptOldestFirst().map(\.sha256), try store.kept().map(\.sha256))
    }

    func testPhotosWithATimeComeBeforeThoseWithout() throws {
        final class SomeTimes: CaptureFiles, @unchecked Sendable {
            let inner = SystemCaptureFiles()
            var timed: Set<String> = []
            func write(_ data: Data, to url: URL) throws { try inner.write(data, to: url) }
            func read(_ url: URL) throws -> Data { try inner.read(url) }
            func remove(_ url: URL) throws { try inner.remove(url) }
            func names(in folder: URL) throws -> [String] { try inner.names(in: folder) }
            func makeFolder(_ url: URL) throws { try inner.makeFolder(url) }
            func modified(_ url: URL) -> Date? { timed.contains(url.lastPathComponent) ? Date(timeIntervalSince1970: 1) : nil }
        }
        let files = SomeTimes()
        let store = CaptureStore(folder: folder, files: files)
        var hashes: [String] = []
        for index in 0..<3 { hashes.append(try store.keep(photo: Data("timed or not \(index)".utf8), contentType: .jpeg, recordWire: nil).sha256) }
        let byHash = hashes.sorted()
        // Only the last by hash has a time: it comes first, the others after it in the store's own order
        files.timed = ["\(byHash[2]).jpg"]
        XCTAssertEqual(try store.keptOldestFirst().map(\.sha256), [byHash[2], byHash[0], byHash[1]])
    }

    func testTheWordsCountWhatIsKeptAndSent() {
        XCTAssertEqual(KeptWords.pill(3), "3 not sent")
        XCTAssertEqual(KeptWords.pill(1), "1 not sent")
        XCTAssertEqual(KeptWords.line(3), "3 photos not sent")
        XCTAssertEqual(KeptWords.line(1), "1 photo not sent")
        XCTAssertEqual(KeptWords.line(0), "0 photos not sent")
        XCTAssertEqual(KeptWords.line(1200), "1,200 photos not sent")
        XCTAssertEqual(KeptWords.sending, "Sending")
        XCTAssertEqual(KeptWords.count(sent: 1, of: 3), "1 of 3")
        XCTAssertEqual(KeptWords.fraction(sent: 1, of: 3), 1.0 / 3, accuracy: 1e-12)
        XCTAssertEqual(KeptWords.fraction(sent: 0, of: 0), 0)
        XCTAssertEqual(KeptWords.fraction(sent: 5, of: 3), 1)
    }
}
