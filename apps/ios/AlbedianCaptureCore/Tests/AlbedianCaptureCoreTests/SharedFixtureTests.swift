import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The third reading of the shared fixture (REQ-IOS-7.1, P4): the Swift validator must accept and refuse exactly the
/// records the Pydantic mirror wrote its verdicts for, and the Zod schema gives, in the same words where the rule is
/// the contract's own. Every value in the fixture is synthetic (the synthetic moment and open-ocean place of the
/// other fixtures). A record Swift judges differently is a finding: the tests list every one by name.
final class SharedFixtureTests: XCTestCase {
    func testSharesFormatLimitsListsRuleWordsAndStatement() throws {
        let fixture = try SharedFixture.load()
        XCTAssertEqual(fixture["format"]?.stringValue, PointingContract.format)
        XCTAssertEqual(fixture["formatVersion"]?.numberValue, Double(PointingContract.formatVersion))
        let limits = try XCTUnwrap(fixture["limits"]?.objectValue)
        let expected: [(String, Double)] = [
            ("headingAccuracyLimitDeg", PointingContract.headingAccuracyLimitDeg),
            ("motionSampleOffsetLimitSec", PointingContract.motionSampleOffsetLimitSec),
            ("unitQuaternionTolerance", PointingContract.unitQuaternionTolerance),
            ("gravityLengthMinG", PointingContract.gravityLengthMinG),
            ("gravityLengthMaxG", PointingContract.gravityLengthMaxG),
            ("dimensionMaxPx", Double(PointingContract.dimensionMaxPx)),
            ("sampleCountMax", Double(PointingContract.sampleCountMax)),
            ("labelMaxUtf16", Double(PointingContract.labelMaxUTF16)),
            ("systemVersionMaxUtf16", Double(PointingContract.systemVersionMaxUTF16)),
        ]
        XCTAssertEqual(limits.keys.compactMap(\.string), expected.map(\.0))
        for (key, value) in expected { XCTAssertEqual(limits[key]?.numberValue, value, key) }
        XCTAssertEqual(fixture["patterns"]?["hardwareModel"]?.stringValue, PointingContract.hardwareModelPattern)
        XCTAssertEqual(fixture["patterns"]?["systemVersion"]?.stringValue, PointingContract.systemVersionPattern)
        let lists: [(String, [String])] = [
            ("cameraPositions", CameraPosition.allCases.map(\.rawValue)),
            ("cameraDeviceTypes", CameraDeviceType.allCases.map(\.rawValue)),
            ("headingOrientations", HeadingOrientation.allCases.map(\.rawValue)),
            ("noHeadingReasons", NoHeadingReasonName.allCases.map(\.rawValue)),
            ("noPlaceReasons", NoPlaceReason.allCases.map(\.rawValue)),
            ("accuracyAuthorizations", AccuracyAuthorization.allCases.map(\.rawValue)),
        ]
        let enums = try XCTUnwrap(fixture["enums"]?.objectValue)
        XCTAssertEqual(enums.keys.compactMap(\.string), lists.map(\.0))
        for (key, names) in lists { XCTAssertEqual(enums[key]?.arrayValue?.compactMap(\.stringValue), names, key) }
        let rules = try XCTUnwrap(fixture["rules"]?.objectValue)
        XCTAssertEqual(rules.keys.compactMap(\.string), PointingContract.Rule.allCases.map(\.rawValue))
        for rule in PointingContract.Rule.allCases { XCTAssertEqual(rules[rule.rawValue]?.stringValue, rule.words, rule.rawValue) }
        XCTAssertEqual(fixture["statement"]?.stringValue, PointingContract.statement)
    }

    func testAcceptsEveryRecordThePythonSideAccepts() throws {
        let valid = try SharedFixture.samples("valid")
        XCTAssertGreaterThanOrEqual(valid.count, 10)
        var refused: [String] = []
        for sample in valid {
            let name = sample["name"]?.stringValue ?? "?"
            XCTAssertEqual(sample["verdict"]?.stringValue, "accepted", name)
            let record = try XCTUnwrap(sample["record"], name)
            let issues = PointingValidator.issues(in: record)
            if !issues.isEmpty { refused.append("\(name): \(issues)") }
            // Written and read again, the record is the same record
            XCTAssertEqual(try JSONText.read(units: JSONText.units(record)), record, name)
        }
        XCTAssertEqual(refused, [], "valid samples Swift refuses")
    }

    func testRefusesEveryRecordThePythonSideRefusesInTheContractWords() throws {
        let invalid = try SharedFixture.samples("invalid")
        XCTAssertGreaterThanOrEqual(invalid.count, 40)
        var accepted: [String] = []
        var otherWords: [String] = []
        for sample in invalid {
            let name = sample["name"]?.stringValue ?? "?"
            XCTAssertEqual(sample["verdict"]?.stringValue, "refused", name)
            let record = try XCTUnwrap(sample["record"], name)
            guard let messages = judge(record) else { accepted.append(name); continue }
            if let message = sample["message"]?.stringValue, !messages.contains(message) {
                otherWords.append("\(name): \(messages)")
            }
        }
        XCTAssertEqual(accepted, [], "invalid samples Swift accepts")
        XCTAssertEqual(otherWords, [], "invalid samples Swift refuses in other words")
        // Every rule of the contract's own has a fault in the fixture
        let faulted = Set(invalid.compactMap { $0["message"]?.stringValue })
        for rule in PointingContract.Rule.allCases { XCTAssertTrue(faulted.contains(rule.words), rule.rawValue) }
    }

    func testGivesEveryGeneratedWireFaultTheVerdictThePythonMirrorGaveIt() throws {
        let faults = try SharedFixture.samples("wireFaults")
        XCTAssertGreaterThanOrEqual(faults.count, 300)
        XCTAssertGreaterThanOrEqual(faults.filter { $0["kind"]?.stringValue == "rename" }.count, 30)
        var disagreements: [String] = []
        for fault in faults {
            let name = fault["name"]?.stringValue ?? "?"
            let text = try wireText(fault)
            let verdict: String
            do {
                verdict = PointingValidator.accepts(try JSONText.read(units: text)) ? "accepted" : "refused"
            } catch {
                verdict = "refused"
            }
            if verdict != fault["verdict"]?.stringValue {
                disagreements.append("\(name): \(fault["verdict"]?.stringValue ?? "?") by the mirror, \(verdict) by Swift")
            }
        }
        XCTAssertEqual(disagreements, [], "wire faults Swift judges differently")
    }

    func testGivesEveryFieldVariantOnTheBaseRecordItsOwnVerdict() throws {
        let variants = try SharedFixture.samples("fieldVariants")
        XCTAssertGreaterThanOrEqual(variants.count, 100)
        let base = try SharedFixture.base()
        var applied = 0
        var disagreements: [String] = []
        for variant in variants {
            let path = try XCTUnwrap(variant["path"]?.arrayValue).compactMap(\.stringValue)
            guard base.at(Array(path.dropLast()))?.objectValue?[path.last!] != nil else { continue }
            var record = base
            record.set(path, variant["value"])
            applied += 1
            let ok = variant["ok"]?.boolValue
            if PointingValidator.accepts(record) != ok {
                disagreements.append("\(path.joined(separator: ".")) = \(JSONText.pretty(variant["value"] ?? .null)): ok \(String(describing: ok)) in the fixture")
            }
        }
        XCTAssertGreaterThanOrEqual(applied, 50)
        XCTAssertEqual(disagreements, [], "field variants Swift judges differently")
    }

    /// The fixture's own table of edges (written by the Python generator): every bounded number at and a step past
    /// each end, every pattern and length at its shortest and longest and one beyond, each on a named valid sample.
    func testGivesEveryEdgeOfTheFixtureTablesTheVerdictItsTableGives() throws {
        let edges = try SharedFixture.samples("edges")
        XCTAssertGreaterThanOrEqual(edges.count, 150)
        var disagreements: [String] = []
        for edge in edges {
            let name = edge["name"]?.stringValue ?? "?"
            var record = try SharedFixture.validRecord(named: try XCTUnwrap(edge["sample"]?.stringValue, name))
            let changes = try XCTUnwrap(edge["changes"]?.arrayValue, name)
            XCTAssertFalse(changes.isEmpty, name)
            for change in changes {
                let path = try XCTUnwrap(change["path"]?.arrayValue, name).compactMap(\.stringValue)
                let value = try XCTUnwrap(change["value"], name)
                // The parent must exist, as the other two readings index into it
                XCTAssertNotNil(record.at(Array(path.dropLast()))?.objectValue, "\(name): no parent at \(path)")
                record.set(path, value)
                XCTAssertEqual(record.at(path), value, "\(name): not set at \(path)")
            }
            let verdict = PointingValidator.accepts(record) ? "accepted" : "refused"
            if verdict != edge["verdict"]?.stringValue {
                disagreements.append("\(name): \(edge["verdict"]?.stringValue ?? "?") by the table, \(verdict) by Swift")
            }
        }
        XCTAssertEqual(disagreements, [], "edges Swift judges differently")
    }

    /// The JSON text a wire fault describes, built from its valid sample as the other two readings build it, in UTF-16
    /// units so that a lone surrogate in the fault survives.
    private func wireText(_ fault: JSONObject) throws -> [UInt16] {
        let baseName = try XCTUnwrap(fault["base"]?.stringValue)
        var record = try SharedFixture.validRecord(named: baseName)
        let path = try XCTUnwrap(fault["path"]?.arrayValue).compactMap(\.stringValue)
        let parents = Array(path.dropLast())
        switch fault["kind"]?.stringValue {
        case "add":
            var target = try XCTUnwrap(record.at(path))
            target["unknownKey"] = .int(1)
            record.set(path, target)
            return JSONText.units(record)
        case "rename":
            let last = try XCTUnwrap(path.last)
            let to = try XCTUnwrap(fault["to"]?.stringValue)
            var parent = try XCTUnwrap(record.at(parents))
            let moved = try XCTUnwrap(parent[last])
            parent[last] = nil
            parent[to] = moved
            record.set(parents, parent)
            return JSONText.units(record)
        case "raw":
            guard case .string(let raw)? = fault["raw"] else { throw FixtureError("a raw fault with no raw text") }
            let mark = "@@wire fault@@"
            record.set(path, .string(mark))
            let text = JSONText.units(record)
            let marked = Array("\"\(mark)\"".utf16)
            let places = (0...(text.count - marked.count)).filter { Array(text[$0..<($0 + marked.count)]) == marked }
            XCTAssertEqual(places.count, 1)
            let at = try XCTUnwrap(places.first)
            return Array(text[..<at]) + raw.units + Array(text[(at + marked.count)...])
        default:
            throw FixtureError("an unknown wire fault kind")
        }
    }
}
