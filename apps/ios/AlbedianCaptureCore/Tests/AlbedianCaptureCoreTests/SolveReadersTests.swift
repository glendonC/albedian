import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// A contract fixture of the repository, read as it is (the TypeScript and Python suites write and judge them).
enum ContractFixture {
    static func load(_ name: String) throws -> JSONObject {
        let data = try Data(contentsOf: repositoryRoot.appendingPathComponent("tests/fixtures/\(name).json"))
        guard let object = try JSONText.read(data).objectValue else { throw FixtureError("\(name) is not a JSON object") }
        return object
    }
}

/// The phone's readers of what the laptop answers about a solve (REQ-IOS-4.2, REQ-IOS-5.1), judged on the existing
/// contract fixtures as a further reading: the solve job (`SolveJobSchema`, `tests/fixtures/ingest_contract.json`),
/// the receipt's fields the result needs (`IngestReceiptSchema`, same fixture) and the solution's status, its counts by
/// evidence class and its catalog matched marks (`ObservationSolutionSchema`,
/// `tests/fixtures/observation_solution_contract.json`).
final class SolveReadersTests: XCTestCase {
    private func ingest() throws -> JSONObject { try ContractFixture.load("ingest_contract") }
    private func solutions() throws -> JSONObject { try ContractFixture.load("observation_solution_contract") }

    // MARK: The solve job

    func testEveryValidSolveJobOfTheFixtureReads() throws {
        let samples = try XCTUnwrap(try ingest()["valid"]?["SolveJob"]?.arrayValue)
        XCTAssertEqual(samples.count, 6)
        for sample in samples {
            switch SolveJobReader.read(sample) {
            case .success(let job):
                XCTAssertEqual(job.id, sample["id"]?.stringValue)
                XCTAssertEqual(job.state.rawValue, sample["state"]?.stringValue)
                XCTAssertEqual(job.stages.count, sample["stages"]?.arrayValue?.count)
                XCTAssertEqual(job.solutionId, sample["solutionId"]?.stringValue)
                XCTAssertEqual(job.error, sample["error"]?.stringValue)
            case .failure(let refusal):
                XCTFail("a valid job was refused: \(refusal)")
            }
        }
    }

    func testEveryInvalidSolveJobOfTheFixtureIsRefused() throws {
        let invalid = try XCTUnwrap(try ingest()["invalid"]?.arrayValue).filter { $0["schema"]?.stringValue == "SolveJob" }
        XCTAssertEqual(invalid.count, 20)
        for item in invalid {
            let sample = try XCTUnwrap(item["sample"])
            if case .success = SolveJobReader.read(sample) {
                XCTFail("an invalid job was read: \(item["reason"]?.stringValue ?? "")")
            }
        }
    }

    func testTheRunningSampleKeepsEachStageAsTheServerReportsIt() throws {
        let running = try XCTUnwrap(try ingest()["valid"]?["SolveJob"]?.arrayValue?[1])
        let job = try SolveJobReader.read(running).get()
        XCTAssertEqual(job.stages, [
            SolveJob.StageProgress(stage: .store, unit: .bytes, done: 52_811_776, total: 52_811_776),
            SolveJob.StageProgress(stage: .decode, unit: .frames, done: 12, total: 36),
            SolveJob.StageProgress(stage: .solve, unit: .steps, done: 0, total: nil),
        ])
    }

    func testAJobRefusesWhatTheContractRefusesAtEachRiskyLine() throws {
        let base = try XCTUnwrap(try ingest()["valid"]?["SolveJob"]?.arrayValue?[1])
        func refused(_ change: (inout JSONObject) -> Void, _ why: String, line: UInt = #line) {
            var object = base.objectValue!
            change(&object)
            if case .success = SolveJobReader.read(.object(object)) { XCTFail("read: \(why)", line: line) }
        }
        func stages(_ items: [(String, String, Int, Int?)]) -> JSONValue {
            .array(items.map { item in
                .object(JSONObject(["stage": .string(item.0), "unit": .string(item.1), "done": .int(item.2),
                                    "total": item.3.map(JSONValue.int) ?? .null]))
            })
        }
        refused({ $0.set("extra", .bool(true)) }, "an unknown key")
        refused({ $0.remove("stages") }, "no stages")
        refused({ $0.set("state", .string("paused")) }, "a state the contract does not name")
        refused({ $0.set("stages", stages([("store", "bytes", 3, 2)])) }, "more done than the total")
        refused({ $0.set("stages", stages([("store", "bytes", -1, 2)])) }, "a negative count")
        refused({ $0.set("stages", stages([("store", "bytes", 0, 0)])) }, "a zero total")
        refused({ $0.set("stages", stages([("decode", "frames", 1, 2), ("store", "bytes", 1, 1)])) }, "stages out of order")
        refused({ $0.set("stages", stages([("store", "bytes", 1, 1), ("store", "bytes", 1, 1)])) }, "a stage twice")
        refused({ $0.set("stages", stages([("store", "pixels", 1, 1)])) }, "a unit the contract does not name")
        refused({ $0.set("stages", .array([.object(JSONObject(["stage": .string("store"), "unit": .string("bytes"),
                                                                "done": .number(JSONNumber(1.5)!), "total": .int(2)]))])) },
                "a count that is not whole")
        refused({ $0.set("stages", .array([.object(JSONObject(["stage": .string("store"), "unit": .string("bytes"),
                                                                "done": .int(1)]))])) },
                "a total left out, not null")
        refused({ $0.set("id", .string("job_0A1B2C3D4E5F6071")) }, "a job id in capitals")
        refused({ $0.set("startedAtUTC", .string("2031-05-02T19:19:59Z")) }, "started before it was created")
        refused({ $0.set("error", .string("")) }, "an empty error")
        refused({ $0.set("error", .string("x")) }, "a running job with an error")
        refused({ $0.set("solutionId", .string("sol_x")) }, "a running job with a solution")
    }

    func testAnEmptySentenceOrLabelIsRefusedWhereTheStateAsksForIt() throws {
        let jobs = try XCTUnwrap(try ingest()["valid"]?["SolveJob"]?.arrayValue)
        var failed = try XCTUnwrap(jobs[3].objectValue)
        failed.set("error", .string(""))
        if case .success = SolveJobReader.read(.object(failed)) { XCTFail("a failed job with an empty sentence") }
        var succeeded = try XCTUnwrap(jobs[2].objectValue)
        succeeded.set("solutionId", .string(""))
        if case .success = SolveJobReader.read(.object(succeeded)) { XCTFail("a succeeded job with an empty solution id") }
        succeeded.set("solutionId", .string(String(repeating: "s", count: 129)))
        if case .success = SolveJobReader.read(.object(succeeded)) { XCTFail("a solution id past 128 characters") }
    }

    func testTimesCompareAsInstantsWhetherOrNotTheyCarryMilliseconds() throws {
        let base = try XCTUnwrap(try ingest()["valid"]?["SolveJob"]?.arrayValue?[2]).objectValue!
        var same = base
        same.set("startedAtUTC", .string("2031-05-02T19:20:00.000Z"))
        same.set("finishedAtUTC", .string("2031-05-02T19:20:00Z"))
        XCTAssertNoThrow(try SolveJobReader.read(.object(same)).get())
        var earlier = base
        earlier.set("finishedAtUTC", .string("2031-05-02T19:20:01.249Z"))
        if case .success = SolveJobReader.read(.object(earlier)) { XCTFail("finished a millisecond before it started") }
    }

    // MARK: The receipt's frame

    func testEveryValidReceiptOfTheFixtureGivesItsFrameAndJob() throws {
        let samples = try XCTUnwrap(try ingest()["valid"]?["IngestReceipt"]?.arrayValue)
        XCTAssertFalse(samples.isEmpty)
        for sample in samples {
            let sha = try XCTUnwrap(sample["sourceAsset"]?["sha256"]?.stringValue)
            let receipt = try XCTUnwrap(StoredReceiptReader.read(sample, sha256: sha), "a valid receipt was refused")
            XCTAssertEqual(receipt.frame.widthPx, sample["sourceAsset"]?["width"]?.numberValue.map(Int.init))
            XCTAssertEqual(receipt.frame.heightPx, sample["sourceAsset"]?["height"]?.numberValue.map(Int.init))
            XCTAssertEqual(receipt.solveJobId, sample["solveJobId"]?.stringValue)
            let turns = sample["sourceAsset"]?["displayOrientation"]?["quarterTurnsClockwise"]?.numberValue.map(Int.init) ?? 0
            XCTAssertEqual(receipt.frame.orientation.quarterTurnsClockwise, turns)
        }
    }

    func testAReceiptForAnotherPhotoOrWithABrokenFieldIsRefused() throws {
        let sample = try XCTUnwrap(try ingest()["valid"]?["IngestReceipt"]?.arrayValue?.first)
        let sha = try XCTUnwrap(sample["sourceAsset"]?["sha256"]?.stringValue)
        XCTAssertNil(StoredReceiptReader.read(sample, sha256: String(repeating: "0", count: 64)), "another photo's receipt")
        var path = sample
        path.set(["sourceAsset", "width"], .int(0))
        XCTAssertNil(StoredReceiptReader.read(path, sha256: sha), "a zero width")
        path = sample
        path.set(["solveJobId"], .string("job_short"))
        XCTAssertNil(StoredReceiptReader.read(path, sha256: sha), "a malformed job id")
        path = sample
        path.set(["sourceAsset", "displayOrientation"], .object(JSONObject(["quarterTurnsClockwise": .int(4), "mirrored": .bool(false)])))
        XCTAssertNil(StoredReceiptReader.read(path, sha256: sha), "four quarter turns")
    }

    // MARK: The solution

    /// Each valid solution of the fixture that names its photo reads with its status and with every finding counted
    /// once under its own class; the pending sample that names no recording cannot be tied to the photo sent.
    func testEveryValidSolutionReadsWithItsStatusAndCountsByClass() throws {
        let valid = try XCTUnwrap(try solutions()["valid"]?.objectValue)
        var read = 0
        for member in valid.members {
            let sample = member.value
            guard let sha = sample["sourceSha256"]?.stringValue else {
                XCTAssertEqual(sample["status"]?.stringValue, "pending")
                if case .success = SolutionReader.read(sample, sha256: String(repeating: "a", count: 64)) {
                    XCTFail("\(member.key): a solution that names no photo was tied to one")
                }
                continue
            }
            let readout = try SolutionReader.read(sample, sha256: sha).get()
            read += 1
            XCTAssertEqual(readout.status.rawValue, sample["status"]?.stringValue, "\(member.key)")
            let findings = sample["findings"]?.arrayValue ?? []
            for evidence in EvidenceClass.allCases {
                let expected = findings.filter { $0["evidenceClass"]?.stringValue == evidence.rawValue }.count
                XCTAssertEqual(readout.counts[evidence], expected, "\(member.key) \(evidence)")
            }
            XCTAssertEqual(readout.marks.count, readout.counts[.catalogMatched])
        }
        XCTAssertEqual(read, 12)
    }

    func testTheCalibratedSampleGivesItsThreeMarksWithTheirPixelsAndMagnitudes() throws {
        let calibrated = try XCTUnwrap(try solutions()["valid"]?["calibrated"])
        let sha = try XCTUnwrap(calibrated["sourceSha256"]?.stringValue)
        let readout = try SolutionReader.read(calibrated, sha256: sha).get()
        XCTAssertEqual(readout.counts, [.observed: 3, .catalogMatched: 3, .predicted: 1, .simulated: 1])
        XCTAssertEqual(readout.marks.map(\.title), ["Betelgeuse", "Rigel", "Aldebaran"])
        XCTAssertEqual(readout.marks.map(\.apparentMagnitude), [0.42, 0.13, 0.86])
        XCTAssertEqual(readout.marks[0].xPx, 401.63469853678794, accuracy: 1e-9)
        XCTAssertEqual(readout.marks[0].yPx, 252.99501055304617, accuracy: 1e-9)
    }

    func testTheFixturesNamedFaultsInTheFieldsShownAreRefused() throws {
        let invalid = try XCTUnwrap(try solutions()["invalid"]?.arrayValue)
        let shown = ["a status the contract does not name", "no status", "64 zeros in place of a hash", "a hash in capitals",
                     "no findings list", "an evidence class the contract does not name",
                     "a catalog match in a solution with no WCS", "a catalog match under a WCS that did not pass verification"]
        var judged = 0
        for item in invalid {
            guard let reason = item["reason"]?.stringValue, shown.contains(reason) else { continue }
            judged += 1
            let sample = try XCTUnwrap(Self.patched(item, in: try solutions()), reason)
            // A sample is judged against the photo it names, so only its fault can refuse it
            let sha = sample["sourceSha256"]?.stringValue.flatMap { PhotoHash.isContractHash($0) ? $0 : nil }
                ?? String(repeating: "a", count: 64)
            if case .success = SolutionReader.read(sample, sha256: sha) { XCTFail("read: \(reason)") }
        }
        XCTAssertEqual(judged, shown.count)
    }

    func testAMatchOnASolutionThatIsNotCalibratedAndVerifiedIsRefused() throws {
        let calibrated = try XCTUnwrap(try solutions()["valid"]?["calibrated"])
        let sha = try XCTUnwrap(calibrated["sourceSha256"]?.stringValue)
        for (path, value, why) in [
            (["status"], JSONValue.string("approximate"), "approximate"),
            (["quality", "passedIndependentVerification"], .bool(false), "not verified"),
            (["wcs"], .null, "no WCS"),
        ] {
            var changed = calibrated
            changed.set(path, value)
            if case .success = SolutionReader.read(changed, sha256: sha) { XCTFail("read with a match: \(why)") }
        }
        var noDetection = calibrated
        noDetection.set(["findings"], .array((calibrated["findings"]?.arrayValue ?? []).map { finding in
            guard finding["evidenceClass"]?.stringValue == "catalog_matched", var object = finding.objectValue else { return finding }
            object.remove("sourceDetection")
            return .object(object)
        }))
        if case .success = SolutionReader.read(noDetection, sha256: sha) { XCTFail("a match with no pixels to mark") }
    }

    func testAMatchAtANegativePixelIsRefused() throws {
        let calibrated = try XCTUnwrap(try solutions()["valid"]?["calibrated"])
        let sha = try XCTUnwrap(calibrated["sourceSha256"]?.stringValue)
        for key in ["xPx", "yPx"] {
            var changed = calibrated
            changed.set(["findings"], .array((calibrated["findings"]?.arrayValue ?? []).map { finding in
                guard finding["evidenceClass"]?.stringValue == "catalog_matched" else { return finding }
                var moved = finding
                moved.set(["sourceDetection", key], .number(JSONNumber(-0.5)!))
                return moved
            }))
            if case .success = SolutionReader.read(changed, sha256: sha) { XCTFail("a match at a negative \(key)") }
        }
    }

    func testASolutionForAnotherPhotoIsRefused() throws {
        let calibrated = try XCTUnwrap(try solutions()["valid"]?["calibrated"])
        if case .success = SolutionReader.read(calibrated, sha256: String(repeating: "b", count: 64)) {
            XCTFail("another photo's solution was read")
        }
    }

    /// The fixture's invalid item as the sample it describes: its `base` (a valid sample's name) with its `patch`.
    static func patched(_ item: JSONValue, in fixture: JSONObject) -> JSONValue? {
        if let sample = item["sample"] { return sample }
        guard let baseName = item["base"]?.stringValue, var value = fixture["valid"]?[baseName] else { return nil }
        for op in item["patch"]?.arrayValue ?? [] {
            guard let kind = op["op"]?.stringValue, let path = op["path"]?.arrayValue else { return nil }
            guard let next = apply(kind, path[...], op["value"], to: value) else { return nil }
            value = next
        }
        return value
    }

    /// One `set` or `remove` at a path of object keys and array indices.
    private static func apply(_ kind: String, _ path: ArraySlice<JSONValue>, _ newValue: JSONValue?, to value: JSONValue) -> JSONValue? {
        guard let step = path.first else { return kind == "set" ? newValue : nil }
        let rest = path.dropFirst()
        if let key = step.stringValue, var object = value.objectValue {
            if rest.isEmpty, kind == "remove" { object.remove(JSONString(key)); return .object(object) }
            guard let child = rest.isEmpty ? JSONValue.null : object[key], let next = apply(kind, rest, newValue, to: child) else { return nil }
            object.set(JSONString(key), next)
            return .object(object)
        }
        if let index = step.numberValue.map(Int.init), var items = value.arrayValue, items.indices.contains(index) {
            if rest.isEmpty, kind == "remove" { items.remove(at: index); return .array(items) }
            guard let next = apply(kind, rest, newValue, to: items[index]) else { return nil }
            items[index] = next
            return .array(items)
        }
        return nil
    }

    // MARK: Where a mark lands on the card

    func testAStoredPointTurnsAsTheContractsOrientStoredPointDoes() {
        // packages/contracts/src/media.ts orientStoredPoint: a clockwise quarter turn sends (x, y) in w by h to (h - y, x)
        let frame = StoredFrame(widthPx: 400, heightPx: 300, orientation: DisplayOrientation(quarterTurnsClockwise: 1, mirrored: false))
        XCTAssertEqual(frame.shownSize.widthPx, 300)
        XCTAssertEqual(frame.shownSize.heightPx, 400)
        let p = frame.orient(xPx: 10, yPx: 20)
        XCTAssertEqual(p.xPx, 280)
        XCTAssertEqual(p.yPx, 10)
        let mirrored = StoredFrame(widthPx: 400, heightPx: 300, orientation: DisplayOrientation(quarterTurnsClockwise: 0, mirrored: true))
        XCTAssertEqual(mirrored.orient(xPx: 10, yPx: 20).xPx, 390)
        let half = StoredFrame(widthPx: 400, heightPx: 300, orientation: DisplayOrientation(quarterTurnsClockwise: 2, mirrored: false))
        XCTAssertEqual(half.orient(xPx: 10, yPx: 20).xPx, 390)
        XCTAssertEqual(half.orient(xPx: 10, yPx: 20).yPx, 280)
        let three = StoredFrame(widthPx: 400, heightPx: 300, orientation: DisplayOrientation(quarterTurnsClockwise: 3, mirrored: false))
        XCTAssertEqual(three.orient(xPx: 10, yPx: 20).xPx, 20)
        XCTAssertEqual(three.orient(xPx: 10, yPx: 20).yPx, 390)
    }

    /// The web's sourcePixelToViewport: the pixel's centre (half a pixel on), turned, then placed in the largest
    /// rectangle of the photo's shape that fits the card, centred.
    func testAMarkLandsWhereTheWebPlacesItOnARectangleOfTheCardsSize() {
        let frame = StoredFrame(widthPx: 3024, heightPx: 4032, orientation: DisplayOrientation(quarterTurnsClockwise: 0, mirrored: false))
        let fitted = frame.place(xPx: 0, yPx: 0, onWidth: 342, height: 456)
        XCTAssertEqual(fitted.x, 0.5 * 342 / 3024, accuracy: 1e-9)
        XCTAssertEqual(fitted.y, 0.5 * 456 / 4032, accuracy: 1e-9)
        let wide = StoredFrame(widthPx: 400, heightPx: 300, orientation: DisplayOrientation(quarterTurnsClockwise: 0, mirrored: false))
        let letterboxed = wide.place(xPx: 199.5, yPx: 149.5, onWidth: 342, height: 456)
        XCTAssertEqual(letterboxed.x, 171, accuracy: 1e-9)
        XCTAssertEqual(letterboxed.y, 228, accuracy: 1e-9)
        let top = wide.place(xPx: -0.5, yPx: -0.5, onWidth: 342, height: 456)
        XCTAssertEqual(top.y, (456 - 342 * 300 / 400) / 2, accuracy: 1e-9)
    }

    /// The marks named on the iPhone are those brighter than magnitude 3 (UIUX-reveal-READY, iPhone 1).
    func testOnlyMarksBrighterThanMagnitudeThreeAreNamed() {
        func mark(_ magnitude: Double?) -> SolutionReadout.Mark {
            SolutionReadout.Mark(id: "m", title: "t", xPx: 0, yPx: 0, apparentMagnitude: magnitude)
        }
        XCTAssertTrue(mark(2.99).isNamedOnThePhone)
        XCTAssertFalse(mark(3).isNamedOnThePhone)
        XCTAssertFalse(mark(nil).isNamedOnThePhone)
        XCTAssertEqual(SolutionReadout.Mark.namedBrighterThanMagnitude, 3)
    }
}
