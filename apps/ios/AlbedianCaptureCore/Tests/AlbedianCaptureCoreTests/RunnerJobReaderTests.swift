import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The runner's job record read by the phone (mobile-9) as the third reading of tests/fixtures/job_contract.json, which
/// tests/integration/test_job_contract.py writes and packages/contracts/src/__tests__/jobs.test.ts reads: every verdict
/// the fixture gives a solve job holds here too, and every job of another kind is refused, since the phone follows only
/// solves.
final class RunnerJobReaderTests: XCTestCase {
    private func fixture() throws -> JSONObject { try ContractFixture.load("job_contract") }

    /// The runner record's own reading, judged alone: `SolveJobReader.read` also takes a record written before the
    /// runner (no `kind`), as the web does, which the job contract does not judge.
    private func accepts(_ sample: JSONValue) -> Bool {
        guard let object = sample.objectValue, case .success = SolveJobReader.readRunnerRecord(object) else { return false }
        return true
    }

    private func refusalPath(_ sample: JSONValue) -> String? {
        guard let object = sample.objectValue, case .failure(let refusal) = SolveJobReader.readRunnerRecord(object) else { return nil }
        return refusal.path
    }

    private func isSolve(_ sample: JSONValue) -> Bool { sample["kind"]?.stringValue == "solve" }

    /// Each entry of a section that carries a verdict: a solve's verdict is the reader's; any other kind is refused.
    private func judge(_ section: String) throws -> (checked: Int, wrong: [String]) {
        let entries = try XCTUnwrap(fixture()[section]?.arrayValue, section)
        var wrong: [String] = []
        var checked = 0
        for (index, entry) in entries.enumerated() {
            let sample = try XCTUnwrap(entry["sample"])
            let accepted = try XCTUnwrap(entry["accepted"]?.boolValue)
            let expected = isSolve(sample) && accepted
            if accepts(sample) != expected { wrong.append(entry["what"]?.stringValue ?? "\(section)[\(index)]") }
            checked += isSolve(sample) ? 1 : 0
        }
        return (checked, wrong)
    }

    func testTakesEverySolveRecordTheFixtureCallsValidAndRefusesEveryOtherKind() throws {
        let valid = try XCTUnwrap(fixture()["valid"]?.arrayValue)
        var solves = 0
        for (index, record) in valid.enumerated() {
            switch SolveJobReader.read(record) {
            case .success(let job):
                XCTAssertTrue(isSolve(record), "valid[\(index)] is not a solve and was taken")
                XCTAssertEqual(job.kind, "solve")
                XCTAssertEqual(job.id, record["id"]?.stringValue)
                XCTAssertEqual(job.state.rawValue, record["state"]?.stringValue)
                XCTAssertEqual(job.stages.count, record["stages"]?.arrayValue?.count)
                XCTAssertEqual(job.queueAhead, record["queueAhead"]?.numberValue.map { Int($0) })
                XCTAssertEqual(job.producer?.revision, record["producer"]?["revision"]?.numberValue.map { Int($0) })
                XCTAssertEqual(job.inputSpan?.frameCount, record["inputSpan"]?["frameCount"]?.numberValue.map { Int($0) })
                solves += 1
            case .failure(let refusal):
                XCTAssertFalse(isSolve(record), "valid[\(index)] refused: \(refusal)")
            }
        }
        XCTAssertEqual(solves, 10)
    }

    /// platform-10a: a solve's record may say when it published the sky predicted from an early pose, once started and
    /// within its run; the phone takes such a record and refuses the time outside the run, as the contract does.
    func testTakesAPredictedSkyTimeWithinTheRunAndRefusesOneOutsideIt() throws {
        let valid = try XCTUnwrap(fixture()["valid"]?.arrayValue)
        let withSky = valid.compactMap(\.objectValue).filter { $0["predictedSkyAtUTC"] != nil }
        XCTAssertEqual(withSky.count, 2)
        for record in withSky {
            XCTAssertNoThrow(try SolveJobReader.read(.object(record)).get())
            let started = try XCTUnwrap(record["startedAtUTC"])
            var early = record
            early["predictedSkyAtUTC"] = .string("2000-01-01T00:00:00.000Z")
            XCTAssertEqual(refusalPath(.object(early)), "predictedSkyAtUTC")
            if record["finishedAtUTC"] != nil {
                var late = record
                late["predictedSkyAtUTC"] = .string("2099-01-01T00:00:00.000Z")
                XCTAssertEqual(refusalPath(.object(late)), "predictedSkyAtUTC")
            }
            var atStart = record
            atStart["predictedSkyAtUTC"] = started
            XCTAssertTrue(accepts(.object(atStart)))
        }
    }

    func testRefusesEveryInvalidRecordOfTheFixture() throws {
        let invalid = try XCTUnwrap(fixture()["invalid"]?.arrayValue)
        XCTAssertGreaterThan(invalid.count, 50)
        for entry in invalid {
            XCTAssertFalse(accepts(try XCTUnwrap(entry["sample"])), entry["reason"]?.stringValue ?? "?")
        }
    }

    func testHoldsEveryBoundOfTheFixtureForASolve() throws {
        let (checked, wrong) = try judge("edges")
        XCTAssertGreaterThan(checked, 60)
        XCTAssertEqual(wrong, [])
    }

    func testHoldsTheStateTableCellByCellForASolve() throws {
        let (checked, wrong) = try judge("grid")
        XCTAssertEqual(checked, 7 * 14 * 2 * 2, "every solve cell of the table")
        XCTAssertEqual(wrong, [])
    }

    func testHoldsEveryFieldDroppedFromARecordForASolve() throws {
        let (checked, wrong) = try judge("dropped")
        XCTAssertGreaterThan(checked, 100)
        XCTAssertEqual(wrong, [])
    }

    func testRefusesEveryUnknownOrRenamedKey() throws {
        let keyed = try XCTUnwrap(fixture()["keyed"]?.arrayValue)
        XCTAssertGreaterThan(keyed.count, 100)
        for entry in keyed { XCTAssertFalse(accepts(try XCTUnwrap(entry["sample"])), entry["what"]?.stringValue ?? "?") }
    }

    /// The phone's tables are the contract's, word for word, as the fixture writes them.
    func testTheReadersTablesAreTheFixtures() throws {
        let fixture = try fixture()
        XCTAssertEqual(fixture["stateFields"]?.arrayValue?.compactMap(\.stringValue), RunnerJob.stateFields)
        let sentences = Set(fixture["enums"]?["solveFailureSentences"]?.arrayValue?.compactMap(\.stringValue) ?? [])
        XCTAssertEqual(sentences, RunnerJob.failureSentences)
        XCTAssertEqual(Set(fixture["enums"]?["stages"]?.arrayValue?.compactMap(\.stringValue) ?? []), RunnerJob.allStages)
        XCTAssertEqual(fixture["enums"]?["passLabels"]?.arrayValue?.compactMap(\.stringValue), SolveJob.Pass.Label.allCases.map(\.rawValue))
        let units = try XCTUnwrap(fixture["stageUnits"]?["solve"]?.objectValue)
        XCTAssertEqual(units.keys.compactMap(\.string), SolveJob.Stage.allCases.map(\.rawValue))
        for stage in SolveJob.Stage.allCases {
            XCTAssertEqual(Set(units[stage.rawValue]?.arrayValue?.compactMap(\.stringValue) ?? []), Set(RunnerJob.solveUnits[stage]!.map(\.rawValue)))
        }
        XCTAssertEqual(fixture["limits"]?["passMax"]?.numberValue, Double(RunnerJob.passMax))
        XCTAssertEqual(fixture["limits"]?["spanMaxSec"]?.numberValue, RunnerJob.spanMaxSec)
        // The solve rows of the state table, rule by rule; the contract has no row for a rejected solve
        let words: [String: RunnerJob.Rule] = ["required": .r, "forbidden": .f, "either": .e, "started": .s, "with_start": .a]
        let table = try XCTUnwrap(fixture["stateTable"]?["solve"]?.objectValue)
        XCTAssertEqual(table["rejected"], .null)
        for state in SolveJob.State.allCases {
            let fields = try XCTUnwrap(table[state.rawValue]?["fields"]?.objectValue, state.rawValue)
            XCTAssertEqual(RunnerJob.stateFields.map { fields[$0]?.stringValue.flatMap { words[$0] } }, RunnerJob.solveTable[state]!.map { $0 }, state.rawValue)
        }
    }
}

extension RunnerJobReaderTests {
    /// A record with no `kind` is read as one written before the runner, by that contract's own rules (`SolveJobSchema`),
    /// as the web falls back to it; one that is neither is refused.
    func testARecordWithNoKindIsReadByTheContractBeforeTheRunner() throws {
        let valid = try XCTUnwrap(fixture()["valid"]?.arrayValue)
        let canceled = try XCTUnwrap(valid.first { $0["kind"]?.stringValue == "solve" && $0["state"]?.stringValue == "canceled" && $0["startedAtUTC"] == nil })
        var old = try XCTUnwrap(canceled.objectValue)
        old.remove("kind")
        if case .failure(let refusal) = SolveJobReader.read(.object(old)) { XCTFail("\(refusal)") }
        let queued = try XCTUnwrap(valid.first { $0["kind"]?.stringValue == "solve" && $0["state"]?.stringValue == "queued" })
        var noKind = try XCTUnwrap(queued.objectValue)
        noKind.remove("kind")
        guard case .failure(let refusal) = SolveJobReader.read(.object(noKind)) else { return XCTFail("queueAhead is not a key before the runner") }
        XCTAssertEqual(refusal.path, "queueAhead")
    }
}
