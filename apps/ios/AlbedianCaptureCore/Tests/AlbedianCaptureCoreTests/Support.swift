import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The repository root, found from this file's own path: apps/ios/AlbedianCaptureCore/Tests/AlbedianCaptureCoreTests.
let repositoryRoot: URL = URL(fileURLWithPath: #filePath)
    .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
    .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()

/// The shared fixture, written by tests/integration/test_pointing_contract.py and parsed by
/// packages/contracts/src/__tests__/pointing.test.ts; read here from the repository as it is.
enum SharedFixture {
    static let url = repositoryRoot.appendingPathComponent("tests/fixtures/pointing_contract.json")

    nonisolated(unsafe) private static var cached: JSONObject?

    static func load() throws -> JSONObject {
        if let cached { return cached }
        let data = try Data(contentsOf: url)
        let value = try JSONText.read(data)
        guard let object = value.objectValue else { throw FixtureError("the fixture is not a JSON object") }
        cached = object
        return object
    }

    static func samples(_ key: String) throws -> [JSONObject] {
        guard let items = try load()[key]?.arrayValue else { throw FixtureError("no \(key) list in the fixture") }
        return items.compactMap(\.objectValue)
    }

    static func validRecord(named name: String) throws -> JSONValue {
        guard let sample = try samples("valid").first(where: { $0["name"]?.stringValue == name }),
              let record = sample["record"] else { throw FixtureError("no valid sample \(name)") }
        return record
    }

    /// The measured, placed sample the other two readings build their property records from.
    static func base() throws -> JSONValue { try validRecord(named: "measured_heading_and_place") }
}

struct FixtureError: Error, CustomStringConvertible {
    let description: String
    init(_ description: String) { self.description = description }
}

extension JSONValue {
    /// The value at a path of object keys, or nil.
    func at(_ path: [String]) -> JSONValue? {
        var current: JSONValue? = self
        for key in path { current = current?[key] }
        return current
    }

    /// Sets the value at a path of object keys; does nothing when a parent is missing or not an object.
    mutating func set(_ path: [String], _ value: JSONValue?) {
        guard let first = path.first else { if let value { self = value }; return }
        guard case .object(var object) = self else { return }
        if path.count == 1 {
            object[first] = value
        } else {
            guard var child = object[first], child.objectValue != nil else { return }
            child.set(Array(path.dropFirst()), value)
            object[first] = child
        }
        self = .object(object)
    }

    static func double(_ value: Double) -> JSONValue {
        // Infinite and not-a-number values cannot be written as JSON; the tests that need one read "1e400"
        .number(JSONNumber(value)!)
    }
}

/// Parses a JSON literal, for test values that need an exact literal (1e400, -0, 4032.0).
func literal(_ text: String) -> JSONValue {
    try! JSONText.read(text)
}

func judge(_ record: JSONValue) -> [String]? {
    let issues = PointingValidator.issues(in: record)
    return issues.isEmpty ? nil : issues.map(\.message)
}

/// The smallest double above a positive finite x, and the largest below it.
func nextUp(_ x: Double) -> Double { x.nextUp }
func nextDown(_ x: Double) -> Double { x.nextDown }

/// SplitMix64 (Steele, Lea and Flood, 2014): a small seeded generator, so a property run can be repeated exactly.
struct SeededGenerator: RandomNumberGenerator {
    private var state: UInt64
    init(seed: UInt64) { state = seed }
    mutating func next() -> UInt64 {
        state &+= 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }
}

/// The seed of a property run: ALBEDIAN_SWIFT_SEED when set (the lane's 20-seed runs set 1 to 20), else one drawn
/// at random and printed, so a failing run can be repeated.
func propertySeed(file: StaticString = #filePath, line: UInt = #line) -> UInt64 {
    if let text = ProcessInfo.processInfo.environment["ALBEDIAN_SWIFT_SEED"], let seed = UInt64(text) { return seed }
    let seed = UInt64.random(in: 1...UInt64(Int64.max))
    print("property seed \(seed) (repeat with ALBEDIAN_SWIFT_SEED=\(seed))")
    return seed
}

extension RandomNumberGenerator {
    mutating func double(in range: ClosedRange<Double>) -> Double { Double.random(in: range, using: &self) }
    mutating func bool() -> Bool { Bool.random(using: &self) }
    mutating func pick<T>(_ items: [T]) -> T { items[Int.random(in: 0..<items.count, using: &self)] }
    mutating func int(in range: ClosedRange<Int>) -> Int { Int.random(in: range, using: &self) }
}

/// An upload run with a bound of its own (the lead's rule of 2026-11-12: whatever waits on a connection fails by an
/// assertion when its bound passes, and never hangs the run). Past `seconds` the test fails here, and the state given
/// back is `.waiting`, which no test expects as an upload's end.
func bounded(_ seconds: Double = 20, file: StaticString = #filePath, line: UInt = #line,
             _ operation: @escaping @Sendable () async -> UploadState) async -> UploadState {
    await within(seconds, otherwise: .waiting, file: file, line: line, operation)
}

/// Any async work with a bound of its own: past `seconds` the test fails here and `fallback` is given back.
func within<T: Sendable>(_ seconds: Double, otherwise fallback: T, file: StaticString = #filePath, line: UInt = #line,
                         _ operation: @escaping @Sendable () async -> T) async -> T {
    let box = BoundBox<T>()
    let task = Task.detached { let value = await operation(); box.lock.withLock { box.value = value } }
    let deadline = Date().addingTimeInterval(seconds)
    while Date() < deadline {
        if let value = box.lock.withLock({ box.value }) { return value }
        try? await Task.sleep(nanoseconds: 5_000_000)
    }
    task.cancel()
    XCTFail("the work did not end within \(seconds) s", file: file, line: line)
    return fallback
}

/// Synchronous work (the reply reader) with a bound of its own, run on another thread: past `seconds` the test fails
/// here and `fallback` is given back. Once one call has not ended, every later one fails at once, so a reader that
/// loops leaves one thread spinning, not one per call, until the run ends.
func finishes<T: Sendable>(_ seconds: Double = 5, otherwise fallback: T, file: StaticString = #filePath, line: UInt = #line,
                           _ operation: @escaping @Sendable () -> T) -> T {
    guard !Stuck.flag.lock.withLock({ Stuck.flag.value }) else {
        XCTFail("an earlier call did not end", file: file, line: line)
        return fallback
    }
    let box = BoundBox<T>()
    let ended = DispatchSemaphore(value: 0)
    DispatchQueue.global().async {
        let value = operation()
        box.lock.withLock { box.value = value }
        ended.signal()
    }
    guard ended.wait(timeout: .now() + seconds) == .success, let value = box.lock.withLock({ box.value }) else {
        Stuck.flag.lock.withLock { Stuck.flag.value = true }
        XCTFail("the work did not end within \(seconds) s", file: file, line: line)
        return fallback
    }
    return value
}

/// What a bounded call ends with, behind a lock.
final class BoundBox<T>: @unchecked Sendable {
    let lock = NSLock()
    var value: T?
}

private final class Stuck: @unchecked Sendable {
    static let flag = Stuck()
    let lock = NSLock()
    var value = false
}
