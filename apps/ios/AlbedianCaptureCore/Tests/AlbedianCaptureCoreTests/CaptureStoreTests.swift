import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The photo and its record kept on the phone until the laptop has them (REQ-IOS-4.3), and P3 (REQ-IOS-1.1): the
/// bytes kept, and their SHA-256, are the bytes the camera wrote. Each test works in a folder of its own under the
/// system's temporary folder and removes it. Synthetic bytes.
final class CaptureStoreTests: XCTestCase {
    private var folder: URL!

    override func setUpWithError() throws {
        folder = FileManager.default.temporaryDirectory.appendingPathComponent("albedian-store-\(UUID().uuidString)")
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: folder)
    }

    private func bytes(_ count: Int, seed: UInt64) -> Data {
        var generator = SeededGenerator(seed: seed)
        return Data((0..<count).map { _ in UInt8(truncatingIfNeeded: generator.next()) })
    }

    private func recordWire(for photo: Data) throws -> Data {
        try PointingRecordBuilder.build(SampleCapture.capture(photoSha256: PhotoHash.sha256Hex(photo))).wireData()
    }

    private func names() throws -> [String] {
        try FileManager.default.contentsOfDirectory(atPath: folder.path).sorted()
    }

    func testKeepsThePhotoAndTheRecordByteForByteUnderTheirHashOnly() throws {
        let store = CaptureStore(folder: folder)
        let photo = bytes(3 * 1024 * 1024 + 5, seed: 31)
        let sha = PhotoHash.sha256Hex(photo)
        let record = try recordWire(for: photo)
        let kept = try store.keep(photo: photo, contentType: .heic, recordWire: record)
        XCTAssertEqual(kept, StoredCapture(sha256: sha, contentType: .heic, hasRecord: true))
        // The names hold the hash and nothing else (no time, no place, no count)
        XCTAssertEqual(try names(), ["\(sha).heic", "\(sha).pointing.json"])
        XCTAssertEqual(try Data(contentsOf: folder.appendingPathComponent("\(sha).heic")), photo)
        XCTAssertEqual(try Data(contentsOf: folder.appendingPathComponent("\(sha).pointing.json")), record)
        let loaded = try store.load(sha)
        XCTAssertEqual(loaded.photo, photo)
        XCTAssertEqual(PhotoHash.sha256Hex(loaded.photo), sha)
        XCTAssertEqual(loaded.contentType, .heic)
        XCTAssertEqual(loaded.recordWire, record)
    }

    func testKeepsAPhotoWithNoRecordAndEachTypeUnderItsOwnExtension() throws {
        let store = CaptureStore(folder: folder)
        var expected: [StoredCapture] = []
        for (index, type) in PhotoContentType.allCases.enumerated() {
            let photo = bytes(100 + index, seed: UInt64(40 + index))
            expected.append(try store.keep(photo: photo, contentType: type, recordWire: nil))
            XCTAssertEqual(try store.load(PhotoHash.sha256Hex(photo)).recordWire, nil)
        }
        XCTAssertEqual(Set(try names().map { $0.split(separator: ".").last.map(String.init) ?? "" }), ["heic", "jpg", "png"])
        XCTAssertEqual(try store.kept(), expected.sorted { $0.sha256 < $1.sha256 })
        XCTAssertTrue(expected.allSatisfy { !$0.hasRecord })
    }

    func testRefusesARecordForAnotherPhotoOrOneTheContractRefusesBeforeKeepingAnything() throws {
        let store = CaptureStore(folder: folder)
        let photo = bytes(500, seed: 50)
        let other = try recordWire(for: bytes(500, seed: 51))
        XCTAssertThrowsError(try store.keep(photo: photo, contentType: .jpeg, recordWire: other)) {
            XCTAssertEqual(($0 as? CaptureStoreFailure)?.reason, .recordNamesAnotherPhoto)
            XCTAssertEqual(($0 as? CaptureStoreFailure)?.afterwards, .holding(.nothing))
        }
        let broken = Data(String(decoding: try recordWire(for: photo), as: UTF8.self).replacingOccurrences(of: "\"pointing_hint\"", with: "\"observed\"").utf8)
        XCTAssertThrowsError(try store.keep(photo: photo, contentType: .jpeg, recordWire: broken)) {
            guard case .recordNotContract(let issues)? = ($0 as? CaptureStoreFailure)?.reason else { return XCTFail("\($0)") }
            XCTAssertFalse(issues.isEmpty)
        }
        XCTAssertThrowsError(try store.keep(photo: photo, contentType: .jpeg, recordWire: Data("{".utf8))) {
            guard case .recordNotContract? = ($0 as? CaptureStoreFailure)?.reason else { return XCTFail("\($0)") }
        }
        XCTAssertEqual((try? names()) ?? [], [])
    }

    func testKeepsTheSameBytesOnceAndListsOnlyItsOwnFiles() throws {
        let store = CaptureStore(folder: folder)
        let photo = bytes(800, seed: 60)
        let record = try recordWire(for: photo)
        let first = try store.keep(photo: photo, contentType: .heic, recordWire: record)
        let second = try store.keep(photo: photo, contentType: .heic, recordWire: record)
        XCTAssertEqual(first, second)
        XCTAssertEqual(try names().count, 2)
        // Files that are not a kept photo are not listed (and not touched)
        try Data("x".utf8).write(to: folder.appendingPathComponent("notes.txt"))
        try Data("x".utf8).write(to: folder.appendingPathComponent(String(repeating: "A", count: 64) + ".heic"))
        try Data("x".utf8).write(to: folder.appendingPathComponent(String(repeating: "a", count: 63) + ".heic"))
        try Data("x".utf8).write(to: folder.appendingPathComponent(PhotoHash.sha256Hex(Data("y".utf8)) + ".gif"))
        // A kept photo's name with an empty part beside a dot is another file's name
        let hash = PhotoHash.sha256Hex(Data("z".utf8))
        for name in ["\(hash)..heic", ".\(hash).heic", "\(hash).heic.", "\(hash).", ".heic"] {
            try Data("x".utf8).write(to: folder.appendingPathComponent(name))
            XCTAssertNil(CaptureStore.photo(named: name), name)
        }
        XCTAssertEqual(try store.kept(), [first])
        XCTAssertEqual(try names().count, 11)
    }

    func testListsNothingBeforeTheFirstCaptureAndSaysWhenTheFolderCannotBeRead() throws {
        XCTAssertFalse(FileManager.default.fileExists(atPath: folder.path))
        XCTAssertEqual(try CaptureStore(folder: folder).kept(), [])
        XCTAssertFalse(FileManager.default.fileExists(atPath: folder.path), "listing makes no folder")
        // The two codes Foundation gives for a folder that is not there read as none kept; any other is a failure
        for (code, kept) in [(NSFileReadNoSuchFileError, true), (NSFileNoSuchFileError, true), (NSFileReadNoPermissionError, false)] {
            let store = CaptureStore(folder: folder, files: ListingFails(code: code))
            do {
                let found = try store.kept()
                XCTAssertTrue(kept, "code \(code)")
                XCTAssertEqual(found, [])
            } catch {
                XCTAssertFalse(kept, "code \(code)")
                XCTAssertEqual(error, .fileSystem(code: code))
            }
        }
    }

    func testRefusesToCountAPhotoKeptWhenTheBytesReadBackAreNotTheBytesWritten() throws {
        // A file system that changes one byte on the way, as a failing disk would
        final class Corrupting: CaptureFiles, @unchecked Sendable {
            let real = SystemCaptureFiles()
            func write(_ data: Data, to url: URL) throws {
                var changed = data
                if !changed.isEmpty { changed[changed.startIndex] ^= 0x01 }
                try real.write(changed, to: url)
            }
            func read(_ url: URL) throws -> Data { try real.read(url) }
            func remove(_ url: URL) throws { try real.remove(url) }
            func names(in folder: URL) throws -> [String] { try real.names(in: folder) }
            func makeFolder(_ url: URL) throws { try real.makeFolder(url) }
        }
        let store = CaptureStore(folder: folder, files: Corrupting())
        let photo = bytes(700, seed: 70)
        XCTAssertThrowsError(try store.keep(photo: photo, contentType: .heic, recordWire: nil)) {
            XCTAssertEqual(($0 as? CaptureStoreFailure)?.reason, .readBackDiffers(file: "\(PhotoHash.sha256Hex(photo)).heic"))
            XCTAssertEqual(($0 as? CaptureStoreFailure)?.afterwards, .holding(.nothing))
        }
        // What failed its read-back is not left behind as if kept
        XCTAssertEqual(try CaptureStore(folder: folder).kept(), [])
    }

    func testSaysAPhotoChangedOnDiskWhenItIsLoaded() throws {
        let store = CaptureStore(folder: folder)
        let photo = bytes(900, seed: 80)
        let sha = PhotoHash.sha256Hex(photo)
        _ = try store.keep(photo: photo, contentType: .jpeg, recordWire: nil)
        var changed = photo
        changed[10] ^= 0xFF
        try changed.write(to: folder.appendingPathComponent("\(sha).jpg"))
        XCTAssertThrowsError(try store.load(sha)) { XCTAssertEqual($0 as? CaptureStoreError, .storedBytesChanged(sha256: sha)) }
        XCTAssertThrowsError(try store.load(String(repeating: "b", count: 64))) { XCTAssertEqual($0 as? CaptureStoreError, .notKept) }
        XCTAssertThrowsError(try store.load("../x")) { XCTAssertEqual($0 as? CaptureStoreError, .notKept) }
    }

    func testRemovesAPhotoAndItsRecordOnceTheLaptopHasThem() throws {
        let store = CaptureStore(folder: folder)
        let photo = bytes(600, seed: 90)
        let sha = PhotoHash.sha256Hex(photo)
        _ = try store.keep(photo: photo, contentType: .heic, recordWire: try recordWire(for: photo))
        let alone = bytes(601, seed: 91)
        _ = try store.keep(photo: alone, contentType: .heic, recordWire: nil)
        try store.remove(sha)
        XCTAssertEqual(try names(), ["\(PhotoHash.sha256Hex(alone)).heic"])
        try store.remove(PhotoHash.sha256Hex(alone))
        XCTAssertEqual(try names(), [])
        XCTAssertThrowsError(try store.remove(sha)) { XCTAssertEqual(($0 as? CaptureStoreFailure)?.reason, .notKept) }
        XCTAssertThrowsError(try store.remove("not a hash")) { XCTAssertEqual(($0 as? CaptureStoreFailure)?.reason, .notKept) }
    }
}

/// Files whose folder cannot be listed, with the given Cocoa error code.
private struct ListingFails: CaptureFiles {
    let code: Int
    func write(_ data: Data, to url: URL) throws {}
    func read(_ url: URL) throws -> Data { Data() }
    func remove(_ url: URL) throws {}
    func names(in folder: URL) throws -> [String] { throw NSError(domain: NSCocoaErrorDomain, code: code) }
    func makeFolder(_ url: URL) throws {}
}
