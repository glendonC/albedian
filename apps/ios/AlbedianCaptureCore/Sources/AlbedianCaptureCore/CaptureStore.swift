import Foundation

/// A photo the phone keeps until the laptop has it, with its record when one was written.
public struct StoredCapture: Equatable, Sendable {
    public let sha256: String
    public let contentType: PhotoContentType
    public let hasRecord: Bool
    public init(sha256: String, contentType: PhotoContentType, hasRecord: Bool) {
        self.sha256 = sha256
        self.contentType = contentType
        self.hasRecord = hasRecord
    }
}

/// What the store holds of one capture, and the only states it can be in: nothing; the photo with its record; or the
/// photo alone, only when no record was made for it. A capture is its photo's file: a record's file whose photo is
/// not there is no capture, only a stray file (`CaptureStoreFailure.stray`).
public enum CaptureHolding: Equatable, Sendable {
    case nothing
    case photoWithRecord
    case photoAlone
}

/// Why a capture could not be kept, read or removed. None holds a value from the photo or the record.
public enum CaptureStoreError: Error, Equatable, Sendable {
    /// The record names another photo than the bytes given.
    case recordNamesAnotherPhoto
    /// The record's bytes are not a record the contract takes.
    case recordNotContract([PointingIssue])
    /// A file just written read back as other bytes (P3).
    case readBackDiffers(file: String)
    /// A file just written could not be read back, with the error's code: it counts no more than one that differs.
    case readBackFailed(file: String, code: Int)
    /// A kept photo's bytes no longer have the SHA-256 they are kept under.
    case storedBytesChanged(sha256: String)
    /// A kept record is no longer a record the contract takes for its photo.
    case storedRecordChanged(sha256: String)
    /// No capture is kept under that name.
    case notKept
    /// The file system refused, with the error's code.
    case fileSystem(code: Int)
}

/// What is known of a capture after a keep or a removal failed: what the folder holds of it, read once the call had
/// undone what it could; or that the folder could not be read, and whether the call had changed anything before that.
public enum CaptureAfterFailure: Equatable, Sendable {
    /// Read from the folder after the call.
    case holding(CaptureHolding)
    /// The folder could not be read before the call wrote or removed anything (the error's code): the call changed
    /// nothing, and what the folder held already is not known.
    case notReadNothingChanged(code: Int)
    /// The call may have written or removed some of the capture, and the folder could not be read afterwards (the
    /// error's code): what is left of it is not known.
    case notReadAfterChanges(code: Int)
    /// A file of the capture is in the folder by name but does not read back whole, by the check `load` makes (the
    /// photo's bytes are not its SHA-256, or its record is not the contract's record for it): nothing of it is kept,
    /// and `load` refuses it, so it is never sent.
    case notWhole
}

/// A keep or a removal that did not end as asked: why, and what is known of that capture afterwards.
public struct CaptureStoreFailure: Error, Equatable, Sendable {
    public let reason: CaptureStoreError
    /// What is known of the capture now.
    public let afterwards: CaptureAfterFailure
    /// Files of this capture that are left over and belong to no capture (a record's file whose photo is not there);
    /// none when the folder could not be read.
    public let stray: [String]

    public init(reason: CaptureStoreError, afterwards: CaptureAfterFailure, stray: [String]) {
        self.reason = reason
        self.afterwards = afterwards
        self.stray = stray
    }
}

/// The file operations the store uses, so a test can stand in a failing disk.
public protocol CaptureFiles: Sendable {
    func write(_ data: Data, to url: URL) throws
    func read(_ url: URL) throws -> Data
    func remove(_ url: URL) throws
    func names(in folder: URL) throws -> [String]
    func makeFolder(_ url: URL) throws
    /// When a file was last written, or nil when the files cannot say (D13, oldest kept photo first).
    func modified(_ url: URL) -> Date?
}

extension CaptureFiles {
    public func modified(_ url: URL) -> Date? { nil }
}

/// The system's files: each write atomic (to a temporary file, then moved into place), and on an iPhone readable
/// only once the person has unlocked the phone after it started [proposed: `completeUntilFirstUserAuthentication`,
/// so a capture can still be sent while the phone is locked].
public struct SystemCaptureFiles: CaptureFiles {
    public init() {}
    public func write(_ data: Data, to url: URL) throws {
        #if os(iOS)
        try data.write(to: url, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        #else
        try data.write(to: url, options: .atomic)
        #endif
    }
    public func read(_ url: URL) throws -> Data { try Data(contentsOf: url) }
    public func remove(_ url: URL) throws { try FileManager.default.removeItem(at: url) }
    public func names(in folder: URL) throws -> [String] { try FileManager.default.contentsOfDirectory(atPath: folder.path) }
    public func modified(_ url: URL) -> Date? {
        (try? FileManager.default.attributesOfItem(atPath: url.path))?[.modificationDate] as? Date
    }
    public func makeFolder(_ url: URL) throws { try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true) }
}

/// The photos and records kept on the phone until the laptop has them (REQ-IOS-4.3). Each photo is kept as the bytes
/// given, named by its SHA-256 and the extension of its type, its record beside it as the record's JSON bytes; a
/// name holds nothing else (no time, no place, no count). A file counts as kept only once it has been read back and
/// equals the bytes given (P3, REQ-IOS-1.1); a read-back that cannot be done counts as one that differs.
///
/// Every operation ends in one of `CaptureHolding`'s states. A keep writes the record first and the photo last, so a
/// photo's file is there only once its record (when one was made) is kept whole; a removal takes the photo first, so
/// a photo is never left without the record it had. A keep that fails undoes what it wrote as far as the disk lets it,
/// and keeps a record whose photo it cannot tell is gone (a stray at worst, named as such); a failure reports what is
/// known of the capture afterwards (`CaptureAfterFailure`): what the folder holds, read from it, or that it could not
/// be read, and whether the call had changed anything by then.
public final class CaptureStore: Sendable {
    private let folder: URL
    private let files: any CaptureFiles
    private let workQueue = DispatchQueue(label: "Albedian capture store", qos: .userInitiated)

    /// `folder` is the app's own (Application Support), created when the first capture is kept.
    public init(folder: URL, files: any CaptureFiles = SystemCaptureFiles()) {
        self.folder = folder
        self.files = files
    }

    static func fileExtension(_ type: PhotoContentType) -> String {
        switch type {
        case .heic: "heic"
        case .jpeg: "jpg"
        case .png: "png"
        }
    }

    private func photoName(_ sha256: String, _ type: PhotoContentType) -> String { "\(sha256).\(Self.fileExtension(type))" }
    private func recordName(_ sha256: String) -> String { "\(sha256).pointing.json" }
    private func url(_ name: String) -> URL { folder.appendingPathComponent(name) }

    /// What the folder holds of one capture: its state, the photo's type when its file is there, and its stray files.
    private func read(_ sha256: String) throws(CaptureStoreError) -> (holding: CaptureHolding, type: PhotoContentType?, stray: [String]) {
        let names: Set<String>
        do { names = Set(try listing()) } catch { throw error }
        let type = PhotoContentType.allCases.first { names.contains(photoName(sha256, $0)) }
        let record = names.contains(recordName(sha256))
        switch (type, record) {
        case (nil, false): return (.nothing, nil, [])
        case (nil, true): return (.nothing, nil, [recordName(sha256)])
        case (let type?, true): return (.photoWithRecord, type, [])
        case (let type?, false): return (.photoAlone, type, [])
        }
    }

    /// The folder's file names; none when the folder is not there yet.
    private func listing() throws(CaptureStoreError) -> [String] {
        do { return try files.names(in: folder) } catch {
            let code = (error as NSError).code
            if code == NSFileReadNoSuchFileError || code == NSFileNoSuchFileError { return [] }
            throw .fileSystem(code: code)
        }
    }

    /// The error's code, for a folder that could not be read.
    private static func code(_ error: CaptureStoreError) -> Int {
        if case .fileSystem(let code) = error { return code }
        return 0
    }

    /// The failure for `reason`, with what the folder holds of the capture now; `changed` says whether the call may
    /// have written or removed anything before it failed.
    private func failure(_ reason: CaptureStoreError, _ sha256: String, changed: Bool) -> CaptureStoreFailure {
        let now = known(sha256, changed: changed)
        return CaptureStoreFailure(reason: reason, afterwards: now.afterwards, stray: now.stray)
    }

    /// What is known of a capture now: the folder's names, then each file there read back as `load` reads it, so a
    /// photo or record that is there by name and not whole is said as that (`notWhole`), never as held.
    private func known(_ sha256: String, changed: Bool) -> (afterwards: CaptureAfterFailure, stray: [String]) {
        func notRead(_ error: CaptureStoreError) -> CaptureAfterFailure {
            changed ? .notReadAfterChanges(code: Self.code(error)) : .notReadNothingChanged(code: Self.code(error))
        }
        let now: (holding: CaptureHolding, type: PhotoContentType?, stray: [String])
        do { now = try read(sha256) } catch { return (notRead(error), []) }
        guard now.holding != .nothing else { return (.holding(.nothing), now.stray) }
        do { _ = try load(sha256) } catch {
            switch error {
            case .storedBytesChanged, .storedRecordChanged: return (.notWhole, [])
            default: return (notRead(error), [])
            }
        }
        return (.holding(now.holding), [])
    }

    /// Keeps a photo and its record. The record is checked before anything is written: it must be contract JSON for
    /// these bytes. Keeping a photo the store already holds answers from what it holds: a record is added to a photo
    /// kept alone, and nothing else is written.
    public func keep(photo: Data, contentType: PhotoContentType, recordWire: Data?) throws(CaptureStoreFailure) -> StoredCapture {
        let sha256 = PhotoHash.sha256Hex(photo)
        if let recordWire {
            do { try Self.check(recordWire, for: sha256) } catch { throw failure(error, sha256, changed: false) }
        }
        let before: (holding: CaptureHolding, type: PhotoContentType?, stray: [String])
        do { before = try read(sha256) } catch {
            throw CaptureStoreFailure(reason: error, afterwards: .notReadNothingChanged(code: Self.code(error)), stray: [])
        }
        switch before.holding {
        case .photoWithRecord:
            // Answered from what is held only once it reads back whole, as load reads it
            do { _ = try load(sha256) } catch { throw failure(error, sha256, changed: false) }
            return StoredCapture(sha256: sha256, contentType: before.type ?? contentType, hasRecord: true)
        case .photoAlone:
            let type = before.type ?? contentType
            // The photo answered from, or the one a record is added to, reads back whole first
            do { _ = try load(sha256) } catch { throw failure(error, sha256, changed: false) }
            guard let recordWire else { return StoredCapture(sha256: sha256, contentType: type, hasRecord: false) }
            do { try writeChecked(recordWire, name: recordName(sha256)) } catch { throw failure(error, sha256, changed: true) }
            return StoredCapture(sha256: sha256, contentType: type, hasRecord: true)
        case .nothing:
            do throws(CaptureStoreError) {
                // A stray record of an earlier failed keep is not this keep's: it goes, or is replaced by this one
                if recordWire == nil, !before.stray.isEmpty {
                    do { try files.remove(url(recordName(sha256))) } catch { throw CaptureStoreError.fileSystem(code: (error as NSError).code) }
                }
                do { try files.makeFolder(folder) } catch { throw CaptureStoreError.fileSystem(code: (error as NSError).code) }
                if let recordWire { try writeChecked(recordWire, name: recordName(sha256)) }
                do { try writeChecked(photo, name: photoName(sha256, contentType)) } catch {
                    // The record goes only when the photo's file is gone: a photo is never left without its record
                    let photoLeft = (try? listing())?.contains(photoName(sha256, contentType)) ?? true
                    if recordWire != nil, !photoLeft { try? files.remove(url(recordName(sha256))) }
                    throw error
                }
            } catch { throw failure(error, sha256, changed: true) }
            return StoredCapture(sha256: sha256, contentType: contentType, hasRecord: recordWire != nil)
        }
    }

    /// Runs `work` on the store's own serial queue, never on the main thread, so photo-sized reads, writes and SHA-256
    /// checks do not hold the main actor and two tasks on the same store run one after another.
    public func offMain<T: Sendable>(_ work: @escaping @Sendable (CaptureStore) -> T) async -> T {
        await withCheckedContinuation { continuation in
            workQueue.async { continuation.resume(returning: work(self)) }
        }
    }

    public func countWhole() async -> CheckCount {
        await offMain { $0.countedSync() }
    }

    func keepSync(photo: Data, contentType: PhotoContentType, recordWire: Data?) -> Result<StoredCapture, CaptureStoreFailure> {
        do throws(CaptureStoreFailure) {
            return .success(try keep(photo: photo, contentType: contentType, recordWire: recordWire))
        } catch {
            return .failure(error)
        }
    }

    func countedSync() -> CheckCount {
        do {
            let c = try wholeCount()
            return .counted(whole: c.whole, notWhole: c.notWhole)
        } catch {
            return .couldNotBeRead
        }
    }

    /// How many captures in the folder read back whole by the check `load` makes, and how many are there by name but
    /// do not.
    public func wholeCount() throws(CaptureStoreError) -> (whole: Int, notWhole: Int) {
        var whole = 0
        var notWhole = 0
        for capture in try kept() {
            do { _ = try load(capture.sha256); whole += 1 } catch {
                switch error {
                case .storedBytesChanged, .storedRecordChanged: notWhole += 1
                default: throw error
                }
            }
        }
        return (whole, notWhole)
    }

    /// Every capture kept, ordered by SHA-256 (the names hold no time to order them by). A record's file whose photo is
    /// not there is not a capture and is not listed.
    public func kept() throws(CaptureStoreError) -> [StoredCapture] {
        let names = try listing()
        let present = Set(names)
        var found: [StoredCapture] = []
        for name in names {
            guard let (sha256, type) = Self.photo(named: name) else { continue }
            found.append(StoredCapture(sha256: sha256, contentType: type, hasRecord: present.contains(recordName(sha256))))
        }
        return found.sorted { $0.sha256 < $1.sha256 }
    }

    /// A kept capture's bytes, checked again: the photo against the SHA-256 it is kept under, the record against the
    /// contract and that photo, so nothing is handed on that the store does not hold whole.
    public func load(_ sha256: String) throws(CaptureStoreError) -> (photo: Data, contentType: PhotoContentType, recordWire: Data?) {
        let now = try read(sha256)
        guard let type = now.type else { throw .notKept }
        let photo: Data
        do { photo = try files.read(url(photoName(sha256, type))) } catch { throw .fileSystem(code: (error as NSError).code) }
        guard PhotoHash.sha256Hex(photo) == sha256 else { throw .storedBytesChanged(sha256: sha256) }
        guard now.holding == .photoWithRecord else { return (photo, type, nil) }
        let record: Data
        do { record = try files.read(url(recordName(sha256))) } catch { throw .fileSystem(code: (error as NSError).code) }
        do { try Self.check(record, for: sha256) } catch { throw .storedRecordChanged(sha256: sha256) }
        return (photo, type, record)
    }

    /// Removes a kept photo and its record, once the laptop has them: the photo first, then the record, so a
    /// failure leaves the capture whole or leaves no capture (and then names the record's file left over).
    public func remove(_ sha256: String) throws(CaptureStoreFailure) {
        let now: (holding: CaptureHolding, type: PhotoContentType?, stray: [String])
        do { now = try read(sha256) } catch {
            throw CaptureStoreFailure(reason: error, afterwards: .notReadNothingChanged(code: Self.code(error)), stray: [])
        }
        guard let type = now.type else { throw CaptureStoreFailure(reason: .notKept, afterwards: .holding(.nothing), stray: now.stray) }
        do throws(CaptureStoreError) {
            do { try files.remove(url(photoName(sha256, type))) } catch { throw CaptureStoreError.fileSystem(code: (error as NSError).code) }
            if now.holding == .photoWithRecord {
                do { try files.remove(url(recordName(sha256))) } catch { throw CaptureStoreError.fileSystem(code: (error as NSError).code) }
            }
        } catch { throw failure(error, sha256, changed: true) }
    }

    /// The SHA-256 and type of a kept photo's file name, or nil for any other file.
    static func photo(named name: String) -> (String, PhotoContentType)? {
        let parts = name.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 2, PhotoHash.isContractHash(String(parts[0])),
              let type = PhotoContentType.allCases.first(where: { fileExtension($0) == parts[1] }) else { return nil }
        return (String(parts[0]), type)
    }

    /// A record's bytes must be contract JSON naming this SHA-256.
    static func check(_ recordWire: Data, for sha256: String) throws(CaptureStoreError) {
        let wire: JSONValue
        do { wire = try JSONText.read(recordWire) } catch {
            throw .recordNotContract([PointingIssue(path: [], message: "The kept record is not JSON")])
        }
        let issues = PointingValidator.issues(in: wire)
        guard issues.isEmpty else { throw .recordNotContract(issues) }
        guard wire["sourceAssetSha256"]?.stringValue == sha256 else { throw .recordNamesAnotherPhoto }
    }

    /// Writes a file and reads it back; it counts only when the bytes read back are the bytes given. On any failure
    /// the file is removed (as far as the disk lets it) and the failure thrown.
    private func writeChecked(_ data: Data, name: String) throws(CaptureStoreError) {
        let target = url(name)
        do { try files.write(data, to: target) } catch {
            try? files.remove(target)
            throw .fileSystem(code: (error as NSError).code)
        }
        let back: Data
        do { back = try files.read(target) } catch {
            try? files.remove(target)
            throw .readBackFailed(file: name, code: (error as NSError).code)
        }
        guard back == data else {
            try? files.remove(target)
            throw .readBackDiffers(file: name)
        }
    }
}

extension CaptureStore {
    /// D13, INTERIM: every capture kept, oldest first by when its photo's file was written; captures whose time the
    /// files cannot give keep the store's own order (by SHA-256) after those that have one.
    public func keptOldestFirst() throws(CaptureStoreError) -> [StoredCapture] {
        let all = try kept()
        let timed = all.map { capture in (capture, files.modified(url(photoName(capture.sha256, capture.contentType)))) }
        return timed.enumerated().sorted { a, b in
            switch (a.element.1, b.element.1) {
            case let (x?, y?) where x != y: return x < y
            case (_?, nil): return true
            case (nil, _?): return false
            default: return a.offset < b.offset
            }
        }.map(\.element.0)
    }
}
