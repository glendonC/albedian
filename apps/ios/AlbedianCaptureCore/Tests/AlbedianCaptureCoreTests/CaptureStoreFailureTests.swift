import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// Kept means kept, on every path (mobile-2's second version): the last reader's six failing-disk cases, then every
/// file operation of every keep, load and removal failing in turn, from one table built by counting the steps a
/// successful run takes, so a step added later gets its failure rows without anyone writing them. After each, the
/// store is listed afresh, as the app would see it when opened again. Synthetic bytes.
final class CaptureStoreFailureTests: XCTestCase {
    private var folder: URL!

    override func setUpWithError() throws {
        folder = FileManager.default.temporaryDirectory.appendingPathComponent("albedian-store-fail-\(UUID().uuidString)")
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: folder) }

    private func photo(_ seed: UInt8) -> Data { Data((0..<900).map { UInt8(truncatingIfNeeded: Int($0) &* 31 &+ Int(seed)) }) }
    private func record(for photo: Data) throws -> Data {
        try PointingRecordBuilder.build(SampleCapture.capture(photoSha256: PhotoHash.sha256Hex(photo))).wireData()
    }

    /// The system's files, counting every call, and failing the one numbered `failAt`: by throwing, or (a write) by
    /// writing other bytes; `alsoFail` names more calls that throw, for the cases where cleaning up fails too.
    final class Scripted: CaptureFiles, @unchecked Sendable {
        enum Way { case throwing, corrupting }
        let real = SystemCaptureFiles()
        let lock = NSLock()
        var calls: [String] = []
        var failAt: Int?
        var way = Way.throwing
        var alsoFail: (String) -> Bool = { _ in false }

        private func fails(_ call: String) -> Bool {
            lock.withLock {
                calls.append(call)
                return calls.count - 1 == failAt || alsoFail(call)
            }
        }
        func write(_ data: Data, to url: URL) throws {
            if fails("write \(url.lastPathComponent)") {
                guard way == .corrupting else { throw NSError(domain: NSCocoaErrorDomain, code: NSFileWriteOutOfSpaceError) }
                var changed = data
                if !changed.isEmpty { changed[changed.startIndex] ^= 0x01 }
                return try real.write(changed, to: url)
            }
            try real.write(data, to: url)
        }
        func read(_ url: URL) throws -> Data {
            if fails("read \(url.lastPathComponent)") { throw NSError(domain: NSCocoaErrorDomain, code: NSFileReadUnknownError) }
            return try real.read(url)
        }
        func remove(_ url: URL) throws {
            if fails("remove \(url.lastPathComponent)") { throw NSError(domain: NSCocoaErrorDomain, code: NSFileWriteNoPermissionError) }
            try real.remove(url)
        }
        func names(in folder: URL) throws -> [String] {
            if fails("names") { throw NSError(domain: NSCocoaErrorDomain, code: NSFileReadNoPermissionError) }
            return try real.names(in: folder)
        }
        func makeFolder(_ url: URL) throws {
            if fails("makeFolder") { throw NSError(domain: NSCocoaErrorDomain, code: NSFileWriteNoPermissionError) }
            try real.makeFolder(url)
        }
    }

    /// What the folder holds of a capture, read by a store of its own, as the app sees it when opened again.
    private func afresh(_ sha: String) throws -> (holding: CaptureHolding, stray: [String]) {
        let names = Set((try? FileManager.default.contentsOfDirectory(atPath: folder.path)) ?? [])
        let listed = try CaptureStore(folder: folder).kept().filter { $0.sha256 == sha }
        XCTAssertLessThanOrEqual(listed.count, 1)
        let recordFile = "\(sha).pointing.json"
        switch listed.first {
        case nil: return (.nothing, names.contains(recordFile) ? [recordFile] : [])
        case let capture?:
            XCTAssertEqual(capture.hasRecord, names.contains(recordFile))
            return (capture.hasRecord ? .photoWithRecord : .photoAlone, [])
        }
    }

    // MARK: The last reader's six cases

    func testAReadBackThatFailsLeavesNothingKept() throws {
        let files = Scripted()
        let p = photo(1)
        let sha = PhotoHash.sha256Hex(p)
        files.alsoFail = { $0 == "read \(sha).heic" }
        XCTAssertThrowsError(try CaptureStore(folder: folder, files: files).keep(photo: p, contentType: .heic, recordWire: nil)) {
            let failure = $0 as? CaptureStoreFailure
            XCTAssertEqual(failure?.reason, .readBackFailed(file: "\(sha).heic", code: NSFileReadUnknownError))
            XCTAssertEqual(failure?.afterwards, .holding(.nothing))
        }
        XCTAssertEqual(try afresh(sha).holding, .nothing)
        XCTAssertEqual(try CaptureStore(folder: folder).kept(), [])
    }

    func testARecordWriteThatFailsLeavesNoPhotoKeptAlone() throws {
        let files = Scripted()
        files.alsoFail = { $0.hasPrefix("write") && $0.hasSuffix(".pointing.json") }
        let p = photo(2)
        XCTAssertThrowsError(try CaptureStore(folder: folder, files: files).keep(photo: p, contentType: .heic, recordWire: try record(for: p))) {
            XCTAssertEqual(($0 as? CaptureStoreFailure)?.reason, .fileSystem(code: NSFileWriteOutOfSpaceError))
            XCTAssertEqual(($0 as? CaptureStoreFailure)?.afterwards, .holding(.nothing))
        }
        XCTAssertEqual(try afresh(PhotoHash.sha256Hex(p)).holding, .nothing)
        XCTAssertFalse(files.calls.contains { $0.hasSuffix(".heic") }, "the photo is written only after its record")
    }

    func testARecordThatReadsBackOtherBytesLeavesNoPhotoKeptAlone() throws {
        let files = Scripted()
        let p = photo(3)
        let sha = PhotoHash.sha256Hex(p)
        files.way = .corrupting
        let r = try record(for: p)
        // The record's write is the first write; find its number from a dry run
        let dry = Scripted()
        _ = try CaptureStore(folder: folder, files: dry).keep(photo: p, contentType: .jpeg, recordWire: r)
        try FileManager.default.removeItem(at: folder)
        files.failAt = try XCTUnwrap(dry.calls.firstIndex(of: "write \(sha).pointing.json"))
        XCTAssertThrowsError(try CaptureStore(folder: folder, files: files).keep(photo: p, contentType: .jpeg, recordWire: r)) {
            XCTAssertEqual(($0 as? CaptureStoreFailure)?.reason, .readBackDiffers(file: "\(sha).pointing.json"))
        }
        XCTAssertEqual(try afresh(sha).holding, .nothing)
        XCTAssertEqual(try afresh(sha).stray, [])
    }

    func testAFullDiskForThePhotoKeepsNothing() throws {
        let files = Scripted()
        files.alsoFail = { $0.hasPrefix("write") && !$0.hasSuffix(".pointing.json") }
        let p = photo(4)
        XCTAssertThrowsError(try CaptureStore(folder: folder, files: files).keep(photo: p, contentType: .heic, recordWire: try record(for: p))) {
            XCTAssertEqual(($0 as? CaptureStoreFailure)?.reason, .fileSystem(code: NSFileWriteOutOfSpaceError))
            XCTAssertEqual(($0 as? CaptureStoreFailure)?.afterwards, .holding(.nothing))
            XCTAssertEqual(($0 as? CaptureStoreFailure)?.stray, [])
        }
        XCTAssertEqual(try afresh(PhotoHash.sha256Hex(p)).holding, .nothing)
        XCTAssertEqual(try afresh(PhotoHash.sha256Hex(p)).stray, [], "the record written first is taken away again")
    }

    func testAPhotoKeptAgainAnswersFromWhatTheStoreHolds() throws {
        let store = CaptureStore(folder: folder)
        let p = photo(5)
        let sha = PhotoHash.sha256Hex(p)
        _ = try store.keep(photo: p, contentType: .heic, recordWire: try record(for: p))
        XCTAssertEqual(try store.keep(photo: p, contentType: .heic, recordWire: nil), StoredCapture(sha256: sha, contentType: .heic, hasRecord: true))
        XCTAssertEqual(try store.kept(), [StoredCapture(sha256: sha, contentType: .heic, hasRecord: true)])
        // A record is added to a photo kept alone, and to nothing else
        let q = photo(6)
        _ = try store.keep(photo: q, contentType: .jpeg, recordWire: nil)
        XCTAssertEqual(try store.keep(photo: q, contentType: .heic, recordWire: try record(for: q)),
                       StoredCapture(sha256: PhotoHash.sha256Hex(q), contentType: .jpeg, hasRecord: true))
        XCTAssertEqual(try afresh(PhotoHash.sha256Hex(q)).holding, .photoWithRecord)
        // A photo kept alone, kept again with no record, is still alone
        let s = photo(11)
        _ = try store.keep(photo: s, contentType: .png, recordWire: nil)
        XCTAssertEqual(try store.keep(photo: s, contentType: .heic, recordWire: nil),
                       StoredCapture(sha256: PhotoHash.sha256Hex(s), contentType: .png, hasRecord: false))
        XCTAssertEqual(try afresh(PhotoHash.sha256Hex(s)).holding, .photoAlone)
    }

    func testKeepsTheRecordWhenItCannotTellWhetherTheFailedPhotoIsGone() throws {
        // The photo's write fails, and the folder cannot then be read: whether the photo's file is there is not known,
        // so its record stays (a stray at worst, named as such), never a photo left without it
        let p = photo(12)
        let sha = PhotoHash.sha256Hex(p)
        final class Count: @unchecked Sendable { var names = 0 }
        let count = Count()
        let files = Scripted()
        files.alsoFail = { call in
            if call == "names" { count.names += 1; return count.names > 1 }
            return call == "write \(sha).heic"
        }
        XCTAssertThrowsError(try CaptureStore(folder: folder, files: files).keep(photo: p, contentType: .heic, recordWire: try record(for: p))) {
            let failure = $0 as? CaptureStoreFailure
            XCTAssertEqual(failure?.reason, .fileSystem(code: NSFileWriteOutOfSpaceError))
            // The record was written before the folder could not be read: what is left is not known, never "nothing"
            XCTAssertEqual(failure?.afterwards, .notReadAfterChanges(code: NSFileReadNoPermissionError))
            let words = failure.map { CheckReadout.keepOutcome(.failure($0)) } ?? ""
            XCTAssertEqual(words, "Not kept: the phone's storage refused (error \(NSFileWriteOutOfSpaceError)); the phone's storage "
                           + "could not be read afterwards, so what is left of this photo on the phone is not known")
            XCTAssertFalse(words.contains("nothing"), words)
        }
        XCTAssertEqual(try afresh(sha).holding, .nothing)
        XCTAssertEqual(try afresh(sha).stray, ["\(sha).pointing.json"])
    }

    func testSaysTheCallChangedNothingOnlyWhenTheFolderCouldNotBeReadBeforeItChangedAnything() throws {
        let unreadable = NSFileReadNoPermissionError
        let p = photo(13)
        let sha = PhotoHash.sha256Hex(p)
        // A keep whose first look at the folder fails writes nothing, and says so
        let noFolder = Scripted()
        noFolder.alsoFail = { $0 == "names" }
        XCTAssertThrowsError(try CaptureStore(folder: folder, files: noFolder).keep(photo: p, contentType: .heic, recordWire: try record(for: p))) {
            let failure = $0 as? CaptureStoreFailure
            XCTAssertEqual(failure?.afterwards, .notReadNothingChanged(code: unreadable))
            XCTAssertEqual(failure.map { CheckReadout.keepOutcome(.failure($0)) }, "Not kept: the phone's storage refused (error \(unreadable)); "
                           + "the phone's storage could not be read, and nothing of this photo was written")
        }
        XCTAssertEqual(noFolder.calls, ["names"], "nothing was written")
        // A record refused before anything is written, then a folder that cannot be read: nothing changed either
        XCTAssertThrowsError(try CaptureStore(folder: folder, files: noFolder).keep(photo: p, contentType: .heic, recordWire: Data("{}".utf8))) {
            XCTAssertEqual(($0 as? CaptureStoreFailure)?.afterwards, .notReadNothingChanged(code: unreadable))
        }
        XCTAssertEqual(try afresh(sha).holding, .nothing)
        XCTAssertEqual(try afresh(sha).stray, [])
        // A removal whose first look fails removes nothing; one that fails after its first removal cannot say what is left
        _ = try CaptureStore(folder: folder).keep(photo: p, contentType: .heic, recordWire: try record(for: p))
        XCTAssertThrowsError(try CaptureStore(folder: folder, files: noFolder).remove(sha)) {
            XCTAssertEqual(($0 as? CaptureStoreFailure)?.afterwards, .notReadNothingChanged(code: unreadable))
        }
        XCTAssertEqual(try afresh(sha).holding, .photoWithRecord)
        final class Count: @unchecked Sendable { var names = 0 }
        let count = Count()
        let lateFolder = Scripted()
        lateFolder.alsoFail = { call in
            if call == "names" { count.names += 1; return count.names > 1 }
            return call == "remove \(sha).pointing.json"
        }
        XCTAssertThrowsError(try CaptureStore(folder: folder, files: lateFolder).remove(sha)) {
            XCTAssertEqual(($0 as? CaptureStoreFailure)?.afterwards, .notReadAfterChanges(code: unreadable))
            XCTAssertEqual(($0 as? CaptureStoreFailure)?.stray, [])
        }
        XCTAssertEqual(try afresh(sha).holding, .nothing, "the photo was removed before the record could not be")
        // Adding a record to a photo kept alone, which fails and then cannot look: not known, not "as it was"
        _ = try CaptureStore(folder: folder).keep(photo: p, contentType: .heic, recordWire: nil)
        count.names = 0
        let addFails = Scripted()
        // The folder fails from its third look: the keep looks first, then reads the photo back whole (load's look),
        // then the record's write fails, then the failure cannot look
        addFails.alsoFail = { call in
            if call == "names" { count.names += 1; return count.names > 2 }
            return call == "write \(sha).pointing.json"
        }
        XCTAssertThrowsError(try CaptureStore(folder: folder, files: addFails).keep(photo: p, contentType: .heic, recordWire: try record(for: p))) {
            XCTAssertEqual(($0 as? CaptureStoreFailure)?.afterwards, .notReadAfterChanges(code: unreadable))
        }
    }

    func testARemovalThatFailsLeavesTheCaptureWholeOrSaysWhatIsLeft() throws {
        let p = photo(7)
        let sha = PhotoHash.sha256Hex(p)
        let r = try record(for: p)
        _ = try CaptureStore(folder: folder).keep(photo: p, contentType: .heic, recordWire: r)
        // The photo cannot be removed: nothing is removed, and the capture is whole
        let photoFails = Scripted()
        photoFails.alsoFail = { $0 == "remove \(sha).heic" }
        XCTAssertThrowsError(try CaptureStore(folder: folder, files: photoFails).remove(sha)) {
            XCTAssertEqual(($0 as? CaptureStoreFailure)?.afterwards, .holding(.photoWithRecord))
        }
        XCTAssertEqual(try afresh(sha).holding, .photoWithRecord)
        XCTAssertEqual(try CaptureStore(folder: folder).load(sha).recordWire, r)
        // The record cannot be removed after the photo was: no capture is left, and the record's file is named
        let recordFails = Scripted()
        recordFails.alsoFail = { $0 == "remove \(sha).pointing.json" }
        XCTAssertThrowsError(try CaptureStore(folder: folder, files: recordFails).remove(sha)) {
            XCTAssertEqual(($0 as? CaptureStoreFailure)?.afterwards, .holding(.nothing))
            XCTAssertEqual(($0 as? CaptureStoreFailure)?.stray, ["\(sha).pointing.json"])
        }
        XCTAssertEqual(try afresh(sha).holding, .nothing)
        XCTAssertEqual(try afresh(sha).stray, ["\(sha).pointing.json"])
        // A later keep of the same photo with no record does not take that stray record as its own
        XCTAssertEqual(try CaptureStore(folder: folder).keep(photo: p, contentType: .heic, recordWire: nil).hasRecord, false)
        XCTAssertEqual(try afresh(sha).holding, .photoAlone)
    }

    func testCleaningUpThatFailsTooIsSaidAndNeverLeavesAPhotoWithoutItsRecord() throws {
        let p = photo(8)
        let sha = PhotoHash.sha256Hex(p)
        let files = Scripted()
        // The photo reads back other bytes and cannot then be removed: its record stays beside it (never a photo without
        // it), and the pair is said as what it is, there by name and not whole; the load that a send would make refuses it
        files.way = .corrupting
        let dry = Scripted()
        _ = try CaptureStore(folder: folder, files: dry).keep(photo: p, contentType: .heic, recordWire: try record(for: p))
        try FileManager.default.removeItem(at: folder)
        files.failAt = try XCTUnwrap(dry.calls.firstIndex(of: "write \(sha).heic"))
        files.alsoFail = { $0 == "remove \(sha).heic" }
        XCTAssertThrowsError(try CaptureStore(folder: folder, files: files).keep(photo: p, contentType: .heic, recordWire: try record(for: p))) {
            let failure = $0 as? CaptureStoreFailure
            XCTAssertEqual(failure?.reason, .readBackDiffers(file: "\(sha).heic"))
            XCTAssertEqual(failure?.afterwards, .notWhole)
            XCTAssertEqual(failure.map { CheckReadout.keepOutcome(.failure($0)) }, "Not kept: a file read back other bytes than were written; "
                           + "a file of this photo is on the phone but does not read back as it was written, so it is not kept and is never sent")
        }
        XCTAssertEqual(try afresh(sha).holding, .photoWithRecord, "there by name")
        XCTAssertThrowsError(try CaptureStore(folder: folder).load(sha)) { XCTAssertEqual($0 as? CaptureStoreError, .storedBytesChanged(sha256: sha)) }
        // The same photo kept again on a healthy disk: the keep reads back what it would answer from, and says it is
        // not kept, as load agrees; the screen's count holds it as not whole
        XCTAssertThrowsError(try CaptureStore(folder: folder).keep(photo: p, contentType: .heic, recordWire: try record(for: p))) {
            let failure = $0 as? CaptureStoreFailure
            XCTAssertEqual(failure?.reason, .storedBytesChanged(sha256: sha))
            XCTAssertEqual(failure?.afterwards, .notWhole)
            XCTAssertEqual(failure.map { CheckReadout.keepOutcome(.failure($0)) }, "Not kept: a photo file under this name holds other bytes; "
                           + "a file of this photo is on the phone but does not read back as it was written, so it is not kept and is never sent")
        }
        XCTAssertEqual(try CaptureStore(folder: folder).wholeCount().whole, 0)
        XCTAssertEqual(try CaptureStore(folder: folder).wholeCount().notWhole, 1)
    }

    func testARecordThatReadsBackOtherBytesAndStaysIsNeverSaidKept() throws {
        // A record added to a photo kept alone reads back other bytes and cannot be removed; then the same keep again
        let p = photo(14)
        let sha = PhotoHash.sha256Hex(p)
        _ = try CaptureStore(folder: folder).keep(photo: p, contentType: .heic, recordWire: nil)
        let files = Scripted()
        files.way = .corrupting
        // A dry run to find the record's write among the calls, then the record taken away again: the photo alone
        let dry = Scripted()
        _ = try CaptureStore(folder: folder, files: dry).keep(photo: p, contentType: .heic, recordWire: try record(for: p))
        try FileManager.default.removeItem(at: folder.appendingPathComponent("\(sha).pointing.json"))
        files.failAt = try XCTUnwrap(dry.calls.firstIndex(of: "write \(sha).pointing.json"))
        files.alsoFail = { $0 == "remove \(sha).pointing.json" }
        XCTAssertThrowsError(try CaptureStore(folder: folder, files: files).keep(photo: p, contentType: .heic, recordWire: try record(for: p))) {
            XCTAssertEqual(($0 as? CaptureStoreFailure)?.afterwards, .notWhole)
        }
        XCTAssertThrowsError(try CaptureStore(folder: folder).load(sha)) { XCTAssertEqual($0 as? CaptureStoreError, .storedRecordChanged(sha256: sha)) }
        XCTAssertThrowsError(try CaptureStore(folder: folder).keep(photo: p, contentType: .heic, recordWire: try record(for: p))) {
            XCTAssertEqual(($0 as? CaptureStoreFailure)?.reason, .storedRecordChanged(sha256: sha))
            XCTAssertEqual(($0 as? CaptureStoreFailure)?.afterwards, .notWhole)
        }
        // Kept again without a record, the photo alone would be whole, but the record beside it is not: still not kept
        XCTAssertThrowsError(try CaptureStore(folder: folder).keep(photo: p, contentType: .heic, recordWire: nil)) {
            XCTAssertEqual(($0 as? CaptureStoreFailure)?.afterwards, .notWhole)
        }
    }

    func testAPhotoKeptAloneWhoseBytesChangedIsNeverSaidKeptAndGetsNoRecord() throws {
        let p = photo(15)
        let sha = PhotoHash.sha256Hex(p)
        _ = try CaptureStore(folder: folder).keep(photo: p, contentType: .heic, recordWire: nil)
        // Its file's bytes change on the disk after it was kept
        var other = p
        other[other.startIndex] ^= 0x01
        try other.write(to: folder.appendingPathComponent("\(sha).heic"))
        for wire in [nil, try record(for: p)] {
            XCTAssertThrowsError(try CaptureStore(folder: folder).keep(photo: p, contentType: .heic, recordWire: wire)) {
                XCTAssertEqual(($0 as? CaptureStoreFailure)?.reason, .storedBytesChanged(sha256: sha))
                XCTAssertEqual(($0 as? CaptureStoreFailure)?.afterwards, .notWhole)
            }
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: folder.appendingPathComponent("\(sha).pointing.json").path),
                       "no record is added to a photo that is not whole")
        XCTAssertEqual(try CaptureStore(folder: folder).wholeCount().notWhole, 1)
    }

    func testEveryStateTheScreenCanSayAgreesWithLoadWhateverTheDiskDoes() throws {
        // A property: random keeps and removals of two photos on a disk whose calls fail at random (throwing, or a write
        // that lands other bytes); after each, a healthy store's load and the folder itself judge every state the words claimed
        let generator = SeededGenerator(seed: propertySeed())
        let photos = [photo(21), photo(22)]
        final class Flaky: CaptureFiles, @unchecked Sendable {
            let real = SystemCaptureFiles()
            var failWrite: () -> Bool = { false }
            var failRead: () -> Bool = { false }
            var failRemove: () -> Bool = { false }
            var failNames: () -> Bool = { false }
            var corrupt: () -> Bool = { false }
            func write(_ data: Data, to url: URL) throws {
                if failWrite() { throw NSError(domain: NSCocoaErrorDomain, code: NSFileWriteOutOfSpaceError) }
                if corrupt(), !data.isEmpty { var other = data; other[other.startIndex] ^= 0x01; return try real.write(other, to: url) }
                try real.write(data, to: url)
            }
            func read(_ url: URL) throws -> Data {
                if failRead() { throw NSError(domain: NSCocoaErrorDomain, code: NSFileReadUnknownError) }
                return try real.read(url)
            }
            func remove(_ url: URL) throws {
                if failRemove() { throw NSError(domain: NSCocoaErrorDomain, code: NSFileWriteNoPermissionError) }
                try real.remove(url)
            }
            func names(in folder: URL) throws -> [String] {
                if failNames() { throw NSError(domain: NSCocoaErrorDomain, code: NSFileReadNoPermissionError) }
                return try real.names(in: folder)
            }
            func makeFolder(_ url: URL) throws {
                if failWrite() { throw NSError(domain: NSCocoaErrorDomain, code: NSFileWriteNoPermissionError) }
                try real.makeFolder(url)
            }
        }
        final class Dice: @unchecked Sendable { var generator: SeededGenerator; init(_ g: SeededGenerator) { generator = g } }
        let dice = Dice(generator)
        // What the folder holds, read with no stand-in: each file's name and bytes
        func snapshot() -> [String: Data] {
            let names = (try? FileManager.default.contentsOfDirectory(atPath: folder.path)) ?? []
            return Dictionary(uniqueKeysWithValues: names.map { ($0, (try? Data(contentsOf: folder.appendingPathComponent($0))) ?? Data()) })
        }
        // The judge of every state the screen can say of a capture after a keep or a removal failed
        var said: [String: Int] = [:]
        func judge(_ afterwards: CaptureAfterFailure, stray: [String], sha: String, before: [String: Data], _ words: String) {
            let load = Result { try CaptureStore(folder: folder).load(sha) }
            switch afterwards {
            case .holding(.nothing):
                said["nothing", default: 0] += 1
                // No photo of it: load finds none; a record file left is named, and one named is there
                if case .failure(let error) = load { XCTAssertEqual(error as? CaptureStoreError, .notKept, words) } else { XCTFail("load took it: \(words)") }
                XCTAssertEqual(stray.isEmpty, !snapshot().keys.contains("\(sha).pointing.json"), words)
            case .holding(let holding):
                said["on the phone", default: 0] += 1
                guard case .success(let loaded) = load else { return XCTFail("load refused: \(words)") }
                XCTAssertEqual(loaded.recordWire != nil, holding == .photoWithRecord, words)
            case .notWhole:
                said["not whole", default: 0] += 1
                guard case .failure(let error) = load else { return XCTFail("load took it: \(words)") }
                let changed = [CaptureStoreError.storedBytesChanged(sha256: sha), .storedRecordChanged(sha256: sha)]
                XCTAssertTrue(changed.contains { $0 == error as? CaptureStoreError }, "\(error): \(words)")
            case .notReadNothingChanged:
                said["nothing changed", default: 0] += 1
                XCTAssertEqual(snapshot(), before, words)
            case .notReadAfterChanges:
                // It says what is left is not known: there is nothing in the folder to hold it to
                said["not known", default: 0] += 1
            }
        }
        func runStep(disk: Flaky, photo p: Data, removeOp: Bool, wire: Data?) throws {
            let sha = PhotoHash.sha256Hex(p)
            let store = CaptureStore(folder: folder, files: disk)
            let before = snapshot()
            if !removeOp {
                let outcome: Result<StoredCapture, CaptureStoreFailure>
                do throws(CaptureStoreFailure) { outcome = .success(try store.keep(photo: p, contentType: .heic, recordWire: wire)) } catch {
                    outcome = .failure(error)
                }
                let words = CheckReadout.keepOutcome(outcome)
                switch outcome {
                case .success(let kept):
                    said["kept", default: 0] += 1
                    let loaded = try CaptureStore(folder: folder).load(sha)
                    XCTAssertEqual(loaded.photo, p, words)
                    XCTAssertEqual(loaded.recordWire != nil, kept.hasRecord, words)
                case .failure(let failure):
                    judge(failure.afterwards, stray: failure.stray, sha: sha, before: before, words)
                }
            } else {
                do throws(CaptureStoreFailure) { try store.remove(sha) } catch {
                    judge(error.afterwards, stray: error.stray, sha: sha, before: before, "after a removal: \(error)")
                }
            }
            // The screen's count, taken on a healthy disk and on the flaky one: whole is what load takes, not whole is
            // what load refuses as changed, and a count whose reads fail says so rather than counting
            let loads = photos.map { photo in Result { try CaptureStore(folder: folder).load(PhotoHash.sha256Hex(photo)) } }
            let loadable = loads.filter { if case .success = $0 { true } else { false } }.count
            let refused = loads.filter {
                if case .failure(let error) = $0, case .storedBytesChanged = error as? CaptureStoreError { return true }
                if case .failure(let error) = $0, case .storedRecordChanged = error as? CaptureStoreError { return true }
                return false
            }.count
            let healthy = try CaptureStore(folder: folder).wholeCount()
            XCTAssertEqual(healthy.whole, loadable)
            XCTAssertEqual(healthy.notWhole, refused)
            if let flaky = try? CaptureStore(folder: folder, files: disk).wholeCount() {
                XCTAssertEqual(flaky.whole, loadable)
                XCTAssertEqual(flaky.notWhole, refused)
            }
        }

        // Seeded examples for every state class so coverage cannot flake on any seed, judged by the same invariant
        try? FileManager.default.removeItem(at: folder)
        let exClean = Flaky()
        try runStep(disk: exClean, photo: photos[0], removeOp: false, wire: try record(for: photos[0]))
        let exNoNames = Flaky(); exNoNames.failNames = { true }
        try runStep(disk: exNoNames, photo: photos[1], removeOp: false, wire: nil)
        let exHold = Flaky(); exHold.failRemove = { true }
        try runStep(disk: exHold, photo: photos[0], removeOp: true, wire: nil)
        let exWriteFail = Flaky(); exWriteFail.failWrite = { true }
        try runStep(disk: exWriteFail, photo: photos[1], removeOp: false, wire: nil)
        var exReads = 0
        let exNotKnown = Flaky()
        exNotKnown.failRemove = { true }
        exNotKnown.failRead = { exReads += 1; return exReads > 0 }
        try runStep(disk: exNotKnown, photo: photos[0], removeOp: true, wire: nil)
        try? FileManager.default.removeItem(at: folder)
        let exCorruptStay = Flaky(); exCorruptStay.corrupt = { true }; exCorruptStay.failRemove = { true }
        try runStep(disk: exCorruptStay, photo: photos[0], removeOp: false, wire: nil)

        for _ in 0..<80 {
            try? FileManager.default.removeItem(at: folder)
            for _ in 0..<8 {
                let disk = Flaky()
                let writePct = Int.random(in: 0...35, using: &dice.generator)
                let readPct = Int.random(in: 0...35, using: &dice.generator)
                let removePct = Int.random(in: 0...40, using: &dice.generator)
                let namesPct = Int.random(in: 0...25, using: &dice.generator)
                let corruptPct = Int.random(in: 0...40, using: &dice.generator)
                disk.failWrite = { Int.random(in: 0..<100, using: &dice.generator) < writePct }
                disk.failRead = { Int.random(in: 0..<100, using: &dice.generator) < readPct }
                disk.failRemove = { Int.random(in: 0..<100, using: &dice.generator) < removePct }
                disk.failNames = { Int.random(in: 0..<100, using: &dice.generator) < namesPct }
                disk.corrupt = { Int.random(in: 0..<100, using: &dice.generator) < corruptPct }
                let p = photos[Int.random(in: 0..<2, using: &dice.generator)]
                let removeOp = Int.random(in: 0..<3, using: &dice.generator) == 2
                let wire = !removeOp && Bool.random(using: &dice.generator) ? try record(for: p) : nil
                try runStep(disk: disk, photo: p, removeOp: removeOp, wire: wire)
            }
        }
        // The run reached every state it judges, so it judged each
        for state in ["kept", "nothing", "on the phone", "not whole", "nothing changed", "not known"] {
            XCTAssertGreaterThan(said[state, default: 0], 0, state)
        }
    }

    func testAReadThatFailsInTheLookAfterAFailureIsSaidNotKnownNeverNotWhole() throws {
        // AUDIT F's F2: a photo kept alone; a record added to it fails, and the look afterwards cannot read the photo
        let p = photo(16)
        let sha = PhotoHash.sha256Hex(p)
        _ = try CaptureStore(folder: folder).keep(photo: p, contentType: .heic, recordWire: nil)
        final class Count: @unchecked Sendable { var reads = 0 }
        let count = Count()
        let files = Scripted()
        files.alsoFail = { call in
            // The keep's own read back of the photo succeeds; the one in the look after the failure does not
            if call == "read \(sha).heic" { count.reads += 1; return count.reads > 1 }
            return call == "write \(sha).pointing.json"
        }
        XCTAssertThrowsError(try CaptureStore(folder: folder, files: files).keep(photo: p, contentType: .heic, recordWire: try record(for: p))) {
            let failure = $0 as? CaptureStoreFailure
            XCTAssertEqual(failure?.afterwards, .notReadAfterChanges(code: NSFileReadUnknownError))
            let words = failure.map { CheckReadout.keepOutcome(.failure($0)) } ?? ""
            XCTAssertFalse(words.contains("never sent"), words)
        }
        XCTAssertNoThrow(try CaptureStore(folder: folder).load(sha), "the photo is whole: it was only not read")
    }

    func testAReadThatFailsInTheCountIsNotCountedAsNotWhole() throws {
        // AUDIT F's F3: the count cannot read a whole photo: it says it could not count, never "not whole"
        let p = photo(17)
        let sha = PhotoHash.sha256Hex(p)
        _ = try CaptureStore(folder: folder).keep(photo: p, contentType: .heic, recordWire: nil)
        let files = Scripted()
        files.alsoFail = { $0 == "read \(sha).heic" }
        XCTAssertThrowsError(try CaptureStore(folder: folder, files: files).wholeCount()) {
            XCTAssertEqual($0 as? CaptureStoreError, .fileSystem(code: NSFileReadUnknownError))
        }
        XCTAssertEqual(try CaptureStore(folder: folder).wholeCount().whole, 1)
        XCTAssertEqual(try CaptureStore(folder: folder).wholeCount().notWhole, 0)
        // Multiple captures in one folder: whole counts both with-record and alone, notWhole counts both photo and record changes
        let q = photo(18), s = photo(19)
        let qSha = PhotoHash.sha256Hex(q), sSha = PhotoHash.sha256Hex(s)
        _ = try CaptureStore(folder: folder).keep(photo: q, contentType: .heic, recordWire: try record(for: q))
        _ = try CaptureStore(folder: folder).keep(photo: s, contentType: .jpeg, recordWire: try record(for: s))
        XCTAssertEqual(try CaptureStore(folder: folder).wholeCount().whole, 3)
        XCTAssertEqual(try CaptureStore(folder: folder).wholeCount().notWhole, 0)
        try Data("{}".utf8).write(to: folder.appendingPathComponent("\(qSha).pointing.json"))
        XCTAssertEqual(try CaptureStore(folder: folder).wholeCount().whole, 2)
        XCTAssertEqual(try CaptureStore(folder: folder).wholeCount().notWhole, 1)
        var sChanged = s
        sChanged[0] ^= 0x01
        try sChanged.write(to: folder.appendingPathComponent("\(sSha).jpg"))
        XCTAssertEqual(try CaptureStore(folder: folder).wholeCount().whole, 1)
        XCTAssertEqual(try CaptureStore(folder: folder).wholeCount().notWhole, 2)
    }

    func testAKeptCaptureWhoseReadBackFailsOnASecondKeepSaysNothingChanged() throws {
        // Both early returns in keep (with a record, and alone) pass changed: false when load cannot read the kept file
        let p = photo(23), q = photo(24)
        let pSha = PhotoHash.sha256Hex(p), qSha = PhotoHash.sha256Hex(q)
        _ = try CaptureStore(folder: folder).keep(photo: p, contentType: .heic, recordWire: try record(for: p))
        _ = try CaptureStore(folder: folder).keep(photo: q, contentType: .heic, recordWire: nil)
        let pUnreadable = Scripted()
        pUnreadable.alsoFail = { $0 == "read \(pSha).heic" }
        XCTAssertThrowsError(try CaptureStore(folder: folder, files: pUnreadable).keep(photo: p, contentType: .heic, recordWire: try record(for: p))) {
            let failure = $0 as? CaptureStoreFailure
            XCTAssertEqual(failure?.reason, .fileSystem(code: NSFileReadUnknownError))
            XCTAssertEqual(failure?.afterwards, .notReadNothingChanged(code: NSFileReadUnknownError))
        }
        let qUnreadable = Scripted()
        qUnreadable.alsoFail = { $0 == "read \(qSha).heic" }
        for wire in [nil, try record(for: q)] {
            XCTAssertThrowsError(try CaptureStore(folder: folder, files: qUnreadable).keep(photo: q, contentType: .heic, recordWire: wire)) {
                let failure = $0 as? CaptureStoreFailure
                XCTAssertEqual(failure?.reason, .fileSystem(code: NSFileReadUnknownError))
                XCTAssertEqual(failure?.afterwards, .notReadNothingChanged(code: NSFileReadUnknownError))
            }
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: folder.appendingPathComponent("\(qSha).pointing.json").path))
    }

    func testLoadsARecordOnlyWhenItIsStillTheContractsRecordForItsPhoto() throws {
        let p = photo(9)
        let sha = PhotoHash.sha256Hex(p)
        _ = try CaptureStore(folder: folder).keep(photo: p, contentType: .heic, recordWire: try record(for: p))
        try Data("{\"a\":1}".utf8).write(to: folder.appendingPathComponent("\(sha).pointing.json"))
        XCTAssertThrowsError(try CaptureStore(folder: folder).load(sha)) { XCTAssertEqual($0 as? CaptureStoreError, .storedRecordChanged(sha256: sha)) }
        try record(for: photo(10)).write(to: folder.appendingPathComponent("\(sha).pointing.json"))
        XCTAssertThrowsError(try CaptureStore(folder: folder).load(sha)) { XCTAssertEqual($0 as? CaptureStoreError, .storedRecordChanged(sha256: sha)) }
    }

    // MARK: Every step of every operation, failing in turn

    private struct Operation {
        let name: String
        /// Brings the folder to the state before the operation, with the system's files.
        let before: (_ folder: URL) throws -> Void
        /// The operation, on a store over the given files.
        let run: (_ store: CaptureStore) throws -> Void
        /// The states the capture may end in when one step fails (with what is left over).
        let allowed: [(CaptureHolding, [String])]
    }

    func testEndsInANamedStateWhicheverStepOfAKeepALoadOrARemovalFails() throws {
        let p = photo(20)
        let sha = PhotoHash.sha256Hex(p)
        let r = try record(for: p)
        let recordFile = "\(sha).pointing.json"
        let operations: [Operation] = [
            Operation(name: "keep with a record", before: { _ in },
                      run: { _ = try $0.keep(photo: p, contentType: .heic, recordWire: r) }, allowed: [(.nothing, [])]),
            Operation(name: "keep with no record", before: { _ in },
                      run: { _ = try $0.keep(photo: p, contentType: .heic, recordWire: nil) }, allowed: [(.nothing, [])]),
            Operation(name: "keep with no record over a stray record",
                      before: { folder in
                          try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                          try r.write(to: folder.appendingPathComponent(recordFile))
                      },
                      run: { _ = try $0.keep(photo: p, contentType: .heic, recordWire: nil) }, allowed: [(.nothing, [recordFile]), (.nothing, [])]),
            Operation(name: "a record added to a photo kept alone",
                      before: { _ = try CaptureStore(folder: $0).keep(photo: p, contentType: .heic, recordWire: nil) },
                      run: { _ = try $0.keep(photo: p, contentType: .heic, recordWire: r) }, allowed: [(.photoAlone, [])]),
            Operation(name: "the same photo kept again",
                      before: { _ = try CaptureStore(folder: $0).keep(photo: p, contentType: .heic, recordWire: r) },
                      run: { _ = try $0.keep(photo: p, contentType: .heic, recordWire: r) }, allowed: [(.photoWithRecord, [])]),
            Operation(name: "remove a photo with its record",
                      before: { _ = try CaptureStore(folder: $0).keep(photo: p, contentType: .heic, recordWire: r) },
                      run: { try $0.remove(sha) }, allowed: [(.photoWithRecord, []), (.nothing, [recordFile])]),
            Operation(name: "remove a photo kept alone",
                      before: { _ = try CaptureStore(folder: $0).keep(photo: p, contentType: .heic, recordWire: nil) },
                      run: { try $0.remove(sha) }, allowed: [(.photoAlone, [])]),
            Operation(name: "load a photo with its record",
                      before: { _ = try CaptureStore(folder: $0).keep(photo: p, contentType: .heic, recordWire: r) },
                      run: { _ = try $0.load(sha) }, allowed: [(.photoWithRecord, [])]),
        ]
        var rows = 0
        for operation in operations {
            // The steps a successful run takes, counted
            try? FileManager.default.removeItem(at: folder)
            try operation.before(folder)
            let dry = Scripted()
            try operation.run(CaptureStore(folder: folder, files: dry))
            XCTAssertFalse(dry.calls.isEmpty, operation.name)
            for (index, call) in dry.calls.enumerated() {
                for way in call.hasPrefix("write") ? [Scripted.Way.throwing, .corrupting] : [.throwing] {
                    rows += 1
                    let row = "\(operation.name), step \(index) (\(call)) \(way)"
                    try? FileManager.default.removeItem(at: folder)
                    try operation.before(folder)
                    let files = Scripted()
                    files.failAt = index
                    files.way = way
                    var thrown: (any Error)?
                    do { try operation.run(CaptureStore(folder: folder, files: files)) } catch { thrown = error }
                    let now = try afresh(sha)
                    XCTAssertNotNil(thrown, "\(row): a failed step was passed over")
                    XCTAssertTrue(operation.allowed.contains { $0.0 == now.holding && $0.1 == now.stray }, "\(row): ends in \(now)")
                    if let failure = thrown as? CaptureStoreFailure, case .holding(let holding) = failure.afterwards {
                        // The failure says what the folder holds
                        XCTAssertEqual(holding, now.holding, row)
                        XCTAssertEqual(failure.stray, now.stray, row)
                    }
                    // Whatever is held is held whole: a load hands on the photo's bytes and the record kept with it
                    if now.holding != .nothing {
                        let loaded = try CaptureStore(folder: folder).load(sha)
                        XCTAssertEqual(loaded.photo, p, row)
                        XCTAssertEqual(loaded.recordWire, now.holding == .photoWithRecord ? r : nil, row)
                    }
                }
            }
        }
        // Today's steps: keep with a record 6 (and 2 writes corrupted), with none 4 (1), over a stray record 5 (1), a record
        // added 3 (1), the same photo again 1, removal with a record 3, alone 2, load 3: 32 rows; more when a step is added
        XCTAssertGreaterThanOrEqual(rows, 32)
    }
}
