import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The JSON reader and writer: the RFC 8259 grammar and nothing else, numbers as JSON.parse reads them, strings as
/// UTF-16 units so a lone surrogate survives, and compact text out.
final class JSONTextTests: XCTestCase {
    func testReadsTheGrammarAndRefusesEverythingElse() {
        for text in ["{}", "[]", "0", "-0", "1.5", "1e400", "-1E-2", "\"a\"", "true", "false", "null", " {\"a\" : [1, 2] } \n\t\r",
                     "{\"a\":{\"b\":[{}]}}", "\"\\u00e9\\n\\\\\\/\\\"\""] {
            XCTAssertNoThrow(try JSONText.read(text), text)
        }
        for text in ["", " ", "{", "}", "[1,]", "{\"a\":1,}", "01", "1.", ".5", "+1", "1e", "1e+", "-", "--1", "0x10", "NaN",
                     "Infinity", "-Infinity", "nul", "True", "'a'", "\"a", "\"\\x\"", "\"\\u12\"", "\"\\u12g4\"", "{a:1}",
                     "{\"a\" 1}", "[1 2]", "1 2", "{} {}", "/* c */ 1", "\u{FEFF}{}", "\"\u{0007}\"", "\"\n\"", "\"\t\""] {
            XCTAssertThrowsError(try JSONText.read(text), text.debugDescription)
        }
    }

    func testRefusesBytesThatAreNotUTF8() {
        // The error names where the bad bytes start
        for (bytes, offset) in [([0x22, 0xC3, 0x28, 0x22], 1), ([0x22, 0xED, 0xA0, 0x80, 0x22], 1), ([0x7B, 0x7D, 0xFF], 2),
                                ([0xEF, 0xBB, 0xBF, 0x7B, 0x7D], -1)] as [([UInt8], Int)] {
            XCTAssertThrowsError(try JSONText.read(Data(bytes)), "\(bytes)") { error in
                // A byte order mark is UTF-8, so its refusal is the reader's, at the start
                XCTAssertEqual((error as? JSONReadError)?.offset, max(offset, 0), "\(bytes)")
                XCTAssertEqual((error as? JSONReadError)?.reason == "the bytes are not UTF-8", offset >= 0, "\(bytes)")
            }
        }
        XCTAssertEqual(try JSONText.read(Data("\"\u{E9}\"".utf8)), .string("\u{E9}"))
    }

    func testReadsNumbersAsJavaScriptDoes() throws {
        let cases: [(String, Double)] = [("1e400", .infinity), ("-1e400", -.infinity), ("1e-400", 0), ("9007199254740993", 9_007_199_254_740_992),
                                         ("0.1", 0.1), ("4.032e3", 4032), ("-0", 0)]
        for (text, value) in cases { XCTAssertEqual(try JSONText.read(text).numberValue, value, text) }
        XCTAssertEqual(try JSONText.read("-0").numberValue?.sign, .minus)
        let long = String(repeating: "9", count: 401)
        XCTAssertEqual(try JSONText.read(long).numberValue, .infinity)
    }

    func testKeepsALoneSurrogateAndAPairAsUTF16Units() throws {
        guard case .string(let lone) = try JSONText.read("\"a\\ud800\"") else { return XCTFail("not a string") }
        XCTAssertEqual(lone.units, [0x61, 0xD800])
        XCTAssertFalse(lone.isWellFormed)
        XCTAssertNil(lone.string)
        guard case .string(let pair) = try JSONText.read("\"\\ud83d\\ude00\"") else { return XCTFail("not a string") }
        XCTAssertEqual(pair.string, "\u{1F600}")
        // Raw units read from a UTF-16 text, as JSON.parse reads them
        guard case .string(let raw) = try JSONText.read(units: [0x22, 0xDC00, 0x22]) else { return XCTFail("not a string") }
        XCTAssertEqual(raw.units, [0xDC00])
    }

    func testKeepsTheFirstPlaceAndTheLastValueOfARepeatedKey() throws {
        let value = try JSONText.read("{\"a\":1,\"b\":2,\"a\":3}")
        XCTAssertEqual(value.objectValue?.keys, ["a", "b"])
        XCTAssertEqual(value["a"]?.numberValue, 3)
    }

    func testRefusesNestingDeeperThanItsLimit() {
        let deep = String(repeating: "[", count: JSONText.maxDepth + 1) + String(repeating: "]", count: JSONText.maxDepth + 1)
        XCTAssertThrowsError(try JSONText.read(deep))
        let fits = String(repeating: "[", count: JSONText.maxDepth) + String(repeating: "]", count: JSONText.maxDepth)
        XCTAssertNoThrow(try JSONText.read(fits))
    }

    func testWritesCompactTextThatReadsBackTheSame() throws {
        let value: JSONValue = .object(JSONObject(["b": .int(1), "a": .array([.bool(true), .null, .string("q\"\\\n\u{1}\u{E9}")]),
                                                   "c": .double(0.05), "d": .double(-0.0), "e": .double(4032), "f": .double(1e-6)]))
        let text = String(decoding: JSONText.units(value), as: UTF16.self)
        XCTAssertEqual(text, "{\"b\":1,\"a\":[true,null,\"q\\\"\\\\\\n\\u0001\u{E9}\"],\"c\":0.05,\"d\":0,\"e\":4032,\"f\":1e-06}")
        XCTAssertEqual(try JSONText.read(units: JSONText.units(value)), value)
        XCTAssertEqual(try JSONText.read(JSONText.data(value)), value)
        let lone: JSONValue = .string(JSONString(units: [0xD800]))
        XCTAssertEqual(String(decoding: JSONText.units(lone), as: UTF16.self), "\"\\ud800\"")
        XCTAssertEqual(try JSONText.read(JSONText.data(lone)), lone)
        XCTAssertTrue(JSONText.pretty(value).contains("\n  \"b\": 1"))
    }

    func testWritesNoNumberJSONCannotHold() {
        XCTAssertNil(JSONNumber(.infinity))
        XCTAssertNil(JSONNumber(-.infinity))
        XCTAssertNil(JSONNumber(.nan))
        XCTAssertEqual(JSONNumber(-0.0)?.literal, "0")
        XCTAssertEqual(JSONNumber(9_007_199_254_740_992)?.literal, "9007199254740992")
        // Past 2^53 a whole number is written in the shortest form that reads back the same
        XCTAssertEqual(JSONNumber(1e16)?.literal, "1e+16")
        XCTAssertEqual(JSONNumber(1e300)?.literal, "1e+300")
    }
}
