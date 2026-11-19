import AlbedianCaptureCore
import Foundation
import XCTest

/// The capture coach's data from the sky-context reply (`predictedSkyBrightness`), read against the shared contract
/// fixture (tests/fixtures/sky_context_contract.json): every value the laptop's, `predicted`.
final class SkyConditionsTests: XCTestCase {
    private func fixture() throws -> JSONObject { try ContractFixture.load("sky_context_contract") }
    private func base(_ name: String = "reply") throws -> JSONValue { try XCTUnwrap(fixture()["bases"]?[name]) }

    private func changed(_ reply: JSONValue, _ key: String, _ new: JSONValue?) -> JSONValue {
        var copy = reply
        var block = copy["predictedSkyBrightness"]!.objectValue!
        if let new { block.set(JSONString(key), new) } else { block.remove(JSONString(key)) }
        copy["predictedSkyBrightness"] = .object(block)
        return copy
    }

    func testReadsTheBlockAsTheReplyStatesIt() throws {
        let reply = try base()
        let block = try XCTUnwrap(reply["predictedSkyBrightness"])
        let read = try XCTUnwrap(SkyConditions.read(reply))
        XCTAssertEqual(read.skyBrightnessMagArcsec2, block["skyBrightnessMagArcsec2"]?.numberValue)
        XCTAssertEqual(read.darkSkyMagArcsec2, block["darkSkyMagArcsec2"]?.numberValue)
        XCTAssertEqual(read.lunarVeilDeltaMag, block["lunarVeilDeltaMag"]?.numberValue)
        XCTAssertEqual(read.moonIlluminationFraction, block["illuminationFraction"]?.numberValue)
        XCTAssertEqual(read.moonAltDeg, block["moonAltDeg"]?.numberValue)
        XCTAssertEqual(read.moonPhaseName, block["lunarPhaseName"]?.stringValue)
        XCTAssertNotNil(read.moonPhaseName)
        XCTAssertEqual(read.evidence, .predicted)
        XCTAssertEqual(SkyConditions.read(JSONText.data(reply)), read)
    }

    func testANullPhaseNameAtASectorsEdgeIsReadAsNone() throws {
        let edge = try base("reply-edge")
        XCTAssertEqual(edge["predictedSkyBrightness"]?["lunarPhaseName"], .null)
        let read = try XCTUnwrap(SkyConditions.read(edge))
        XCTAssertNil(read.moonPhaseName)
    }

    func testEveryPhaseNameOfTheContractAndNoOther() throws {
        let reply = try base()
        for name in SkyConditions.phaseNames {
            XCTAssertEqual(SkyConditions.read(changed(reply, "lunarPhaseName", .string(JSONString(name))))?.moonPhaseName, name)
        }
        XCTAssertNil(SkyConditions.read(changed(reply, "lunarPhaseName", .string("Blue Moon"))))
        XCTAssertNil(SkyConditions.read(changed(reply, "lunarPhaseName", nil)))
    }

    func testABlockNotPredictedOrOutOfRangeOrAReplyTheOverlayRefusesGivesNothing() throws {
        let reply = try base()
        let refused: [(String, JSONValue)] = [
            ("said observed", changed(reply, "evidenceClass", .string("observed"))),
            ("no sky brightness", changed(reply, "skyBrightnessMagArcsec2", nil)),
            ("no dark-sky value", changed(reply, "darkSkyMagArcsec2", nil)),
            ("a negative veil", changed(reply, "lunarVeilDeltaMag", .number(JSONNumber(-0.1)!))),
            ("illumination past 1", changed(reply, "illuminationFraction", .number(JSONNumber(1.01)!))),
            ("illumination below 0", changed(reply, "illuminationFraction", .number(JSONNumber(-0.01)!))),
            ("the Moon past 90", changed(reply, "moonAltDeg", .number(JSONNumber(90.5)!))),
            ("the Moon as text", changed(reply, "moonAltDeg", .string("10"))),
            ("another format", { var r = reply; r["format"] = .string("albedian.sky"); return r }()),
            ("no block", { var o = reply.objectValue!; o.remove("predictedSkyBrightness"); return .object(o) }()),
        ]
        for (name, value) in refused { XCTAssertNil(SkyConditions.read(value), name) }
        XCTAssertNotNil(SkyConditions.read(changed(reply, "illuminationFraction", .number(JSONNumber(1)!))))
        XCTAssertNotNil(SkyConditions.read(changed(reply, "moonAltDeg", .number(JSONNumber(-90)!))))
        XCTAssertNotNil(SkyConditions.read(changed(reply, "lunarVeilDeltaMag", .number(JSONNumber(0)!))))
    }

    // MARK: The Sun (skycontext-3)

    /// The base reply with the fixture's changes made in turn, as its Python and TypeScript readings make them.
    private func applied(_ document: JSONValue, _ changes: [JSONValue]) -> JSONValue {
        func put(_ node: JSONValue, _ path: ArraySlice<JSONValue>, _ change: JSONValue) -> JSONValue {
            guard let key = path.first else { return change["value"] ?? .null }
            let rest = path.dropFirst()
            if case .array(var items) = node, let index = key.numberValue.map({ Int($0) }) {
                if rest.isEmpty, change["drop"] == .bool(true) { items.remove(at: index); return .array(items) }
                if index == items.count { items.append(put(.null, rest, change)) } else { items[index] = put(items[index], rest, change) }
                return .array(items)
            }
            guard case .object(var object) = node, let name = key.stringValue else { return node }
            if rest.isEmpty, change["drop"] == .bool(true) { object.remove(JSONString(name)); return .object(object) }
            object[name] = put(object[name] ?? .null, rest, change)
            return .object(object)
        }
        return changes.reduce(document) { node, change in put(node, ArraySlice(change["path"]?.arrayValue ?? []), change) }
    }

    func testReadsTheSunAsTheReplyStatesIt() throws {
        let reply = try base()
        let sun = try XCTUnwrap(reply["sun"])
        let read = try XCTUnwrap(SkyConditions.read(reply)?.sun)
        XCTAssertEqual(read.altDeg, sun["altDeg"]?.numberValue)
        XCTAssertEqual(read.band.rawValue, sun["band"]?.stringValue)
        XCTAssertEqual(read.band, .night)
        XCTAssertEqual(read.evidence, .predicted)
        XCTAssertEqual(SkySun.Band.allCases.map(\.rawValue), try fixture()["sunBands"]?.arrayValue?.compactMap(\.stringValue))
    }

    /// A laptop that predates the Sun answers none: the rest is read, and there is simply no Sun to tell of.
    func testAReplyWithoutTheSunIsReadWithNoSun() throws {
        let older = try XCTUnwrap(fixture()["valid"]?.arrayValue?.first { $0["name"]?.stringValue?.hasPrefix("a reply without the Sun") == true }?["body"])
        XCTAssertNil(older["sun"])
        let read = try XCTUnwrap(SkyConditions.read(older))
        XCTAssertNil(read.sun)
        XCTAssertNotNil(SkyConditions.read(try base("reply-edge")), "the edge base predates the Sun too")
    }

    /// Every Sun sample of the fixture holds here: each refused one gives nothing, each bound holds at its edges.
    func testHoldsEverySunSampleOfTheFixture() throws {
        let reply = try base()
        let invalid = try XCTUnwrap(fixture()["invalid"]?.arrayValue).filter { $0["name"]?.stringValue?.contains("Sun") == true }
        XCTAssertEqual(invalid.count, 12)
        for sample in invalid {
            XCTAssertNil(SkyConditions.read(applied(reply, sample["changes"]?.arrayValue ?? [])), sample["name"]?.stringValue ?? "?")
        }
        let edges = try XCTUnwrap(fixture()["edges"]?.arrayValue).filter { $0["name"]?.stringValue?.hasPrefix("the Sun") == true }
        XCTAssertEqual(edges.count, 18)
        for sample in edges {
            let read = SkyConditions.read(applied(reply, sample["changes"]?.arrayValue ?? []))
            XCTAssertEqual(read != nil, sample["verdict"]?.stringValue == "accepted", sample["name"]?.stringValue ?? "?")
        }
    }

    /// The band edges are the fixture's, and an altitude's band is the contract's rule at each edge and either side.
    func testTheBandOfAnAltitudeIsTheContractsRule() throws {
        let edges = try XCTUnwrap(fixture()["sunBandEdges"]?.objectValue)
        XCTAssertEqual(edges["dayAboveDeg"]?.numberValue, SkySun.dayAboveDeg)
        XCTAssertEqual(edges["civilTwilightFromDeg"]?.numberValue, SkySun.civilTwilightFromDeg)
        XCTAssertEqual(edges["nauticalTwilightFromDeg"]?.numberValue, SkySun.nauticalTwilightFromDeg)
        XCTAssertEqual(edges["astronomicalTwilightFromDeg"]?.numberValue, SkySun.astronomicalTwilightFromDeg)
        let cases: [(Double, SkySun.Band)] = [
            (90, .day), (0, .day), (-0.833333, .day), (-50.0 / 60.0, .civilTwilight), (-6, .civilTwilight),
            (-6.000001, .nauticalTwilight), (-12, .nauticalTwilight), (-12.000001, .astronomicalTwilight),
            (-18, .astronomicalTwilight), (-18.000001, .night), (-90, .night),
        ]
        for (alt, band) in cases { XCTAssertEqual(SkySun.band(of: alt), band, "\(alt)") }
    }
}
