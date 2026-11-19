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
}
