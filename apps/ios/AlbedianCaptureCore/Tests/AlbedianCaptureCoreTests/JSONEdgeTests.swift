import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The JSON reader and writer at the edges of RFC 8259 and of JavaScript's `JSON.stringify`: every escape both ways,
/// every control character, every hex digit, surrogates at both ends of their ranges, the nesting limit and the
/// indented form, each as an exact text.
final class JSONEdgeTests: XCTestCase {
    private func units(_ text: String) -> [UInt16] { Array(text.utf16) }

    func testTellsAWellFormedTextFromOneWithALoneSurrogate() {
        let cases: [([UInt16], Bool)] = [
            ([], true), ([0x41], true), ([0xD7FF], true), ([0xE000], true),
            ([0xD800, 0xDC00], true), ([0xDBFF, 0xDFFF], true), ([0xD800, 0xDFFF], true), ([0xDBFF, 0xDC00], true),
            ([0x41, 0xD800, 0xDC00, 0x41], true), ([0xD800, 0xDC00, 0xDBFF, 0xDFFF], true),
            ([0xD800], false), ([0xDBFF], false), ([0xDC00], false), ([0xDFFF], false),
            ([0xDC00, 0x41], false), ([0xDFFF, 0x41], false), ([0xD800, 0x41], false), ([0xDBFF, 0xD800], false),
            ([0xD800, 0xDC00, 0xDC00], false), ([0x41, 0xDFFF], false),
        ]
        for (text, wellFormed) in cases {
            XCTAssertEqual(JSONString(units: text).isWellFormed, wellFormed, "\(text.map { String($0, radix: 16) })")
            XCTAssertEqual(JSONString(units: text).string != nil, wellFormed)
        }
    }

    func testWritesWholeNumbersOfEitherSignAsIntegers() {
        let cases: [(Double, String)] = [
            (1, "1"), (-1, "-1"), (2, "2"), (-2, "-2"), (0.5, "0.5"), (-0.5, "-0.5"), (-0.0, "0"),
            (9_007_199_254_740_992, "9007199254740992"), (-9_007_199_254_740_992, "-9007199254740992"),
        ]
        for (value, literal) in cases { XCTAssertEqual(JSONNumber(value)?.literal, literal, "\(value)") }
    }

    func testReadsSixtyFourLevelsOfObjectsOrArraysAndNoMore() {
        // The limit is [proposed] at 64 levels (JSONText.maxDepth); the counts are written out so a change shows here
        XCTAssertEqual(JSONText.maxDepth, 64)
        func objects(_ levels: Int) -> String {
            String(repeating: "{\"a\":", count: levels - 1) + "{}" + String(repeating: "}", count: levels - 1)
        }
        func arrays(_ levels: Int) -> String { String(repeating: "[", count: levels) + String(repeating: "]", count: levels) }
        func mixed(_ levels: Int) -> String {
            String(repeating: "[{\"a\":", count: levels / 2) + "1" + String(repeating: "}]", count: levels / 2)
        }
        for text in [objects(64), arrays(64), mixed(62), objects(1), arrays(1)] { XCTAssertNoThrow(try JSONText.read(text)) }
        for text in [objects(65), arrays(65), mixed(64)] {
            XCTAssertThrowsError(try JSONText.read(text)) { XCTAssertEqual(($0 as? JSONReadError)?.reason, "nested deeper than 64") }
        }
    }

    func testWritesTheIndentedFormJSONStringifyWritesWithTwoSpaces() {
        let value: JSONValue = .object(JSONObject([
            "a": .array([.int(1), .array([])]),
            "b": .object(JSONObject()),
            "c": .object(JSONObject(["d": .array([.int(2), .object(JSONObject(["e": .null]))])])),
        ]))
        let expected = """
        {
          "a": [
            1,
            []
          ],
          "b": {},
          "c": {
            "d": [
              2,
              {
                "e": null
              }
            ]
          }
        }
        """
        XCTAssertEqual(JSONText.pretty(value), expected)
        XCTAssertEqual(JSONText.pretty(.array([])), "[]")
        XCTAssertEqual(JSONText.pretty(.object(JSONObject())), "{}")
        XCTAssertEqual(String(decoding: JSONText.units(value), as: UTF16.self), "{\"a\":[1,[]],\"b\":{},\"c\":{\"d\":[2,{\"e\":null}]}}")
    }

    func testWritesEveryControlCharacterAsJSONStringifyDoes() {
        let text = JSONString(units: Array(UInt16(0x00)...0x20) + [0x7F, 0x22, 0x5C, 0x2F])
        let expected = "\"\\u0000\\u0001\\u0002\\u0003\\u0004\\u0005\\u0006\\u0007\\b\\t\\n\\u000b\\f\\r\\u000e\\u000f"
            + "\\u0010\\u0011\\u0012\\u0013\\u0014\\u0015\\u0016\\u0017\\u0018\\u0019\\u001a\\u001b\\u001c\\u001d\\u001e\\u001f"
            + " \u{7F}\\\"\\\\/\""
        XCTAssertEqual(String(decoding: JSONText.units(.string(text)), as: UTF16.self), expected)
        XCTAssertEqual(try JSONText.read(expected), .string(text))
    }

    func testWritesAPairAsItIsAndALoneSurrogateAsItsEscape() {
        let cases: [([UInt16], String)] = [
            ([0xD800, 0xDC00], "\u{10000}"), ([0xDBFF, 0xDFFF], "\u{10FFFF}"), ([0xD800, 0xDC00, 0x41], "\u{10000}A"),
            ([0xD800], "\\ud800"), ([0xDBFF], "\\udbff"), ([0xDC00], "\\udc00"), ([0xDFFF], "\\udfff"),
            ([0xD800, 0x41], "\\ud800A"), ([0x41, 0xDFFF], "A\\udfff"), ([0xD800, 0xD800, 0xDC00], "\\ud800\u{10000}"),
            ([0xDBFF, 0xDFFF, 0xDBFF], "\u{10FFFF}\\udbff"), ([0xDC00, 0xD800], "\\udc00\\ud800"),
        ]
        for (text, written) in cases {
            let value = JSONValue.string(JSONString(units: text))
            XCTAssertEqual(JSONText.units(value), units("\"" + written + "\""), "\(text.map { String($0, radix: 16) })")
            XCTAssertEqual(try JSONText.read(units: JSONText.units(value)), value)
        }
    }

    func testReadsEveryEscapeAndRefusesEveryOther() throws {
        XCTAssertEqual(try JSONText.read("\"\\\"\\\\\\/\\b\\f\\n\\r\\t\""), .string(JSONString(units: [0x22, 0x5C, 0x2F, 0x08, 0x0C, 0x0A, 0x0D, 0x09])))
        for letter in ["a", "c", "e", "g", "m", "o", "q", "s", "B", "F", "N", "R", "T", "U", "x", "0", "'", " "] {
            XCTAssertThrowsError(try JSONText.read("\"\\\(letter)\""), letter) { XCTAssertEqual(($0 as? JSONReadError)?.reason, "an unknown escape") }
        }
        XCTAssertThrowsError(try JSONText.read("\"\\")) { XCTAssertEqual(($0 as? JSONReadError)?.reason, "the escape does not end") }
    }

    func testReadsEveryHexDigitOfAUnicodeEscapeInEitherCase() throws {
        XCTAssertEqual(try JSONText.read("\"\\u0123\\u4567\\u89AB\\uCDEF\\uabcd\\uef00\\uFFFF\\u0000\""),
                       .string(JSONString(units: [0x0123, 0x4567, 0x89AB, 0xCDEF, 0xABCD, 0xEF00, 0xFFFF, 0x0000])))
        // The units just outside each run of digits: '/' ':' '@' 'G' '`' 'g'
        for digit in ["/", ":", "@", "G", "`", "g"] {
            XCTAssertThrowsError(try JSONText.read("\"\\u000\(digit)\""), digit) {
                XCTAssertEqual(($0 as? JSONReadError)?.reason, "four hex digits were expected")
            }
        }
        XCTAssertThrowsError(try JSONText.read("\"\\u12\""))
    }

    func testRefusesARawControlCharacterInsideAStringAndTakesTheUnitsAfterThem() throws {
        for unit in [UInt16(0x00), 0x09, 0x0A, 0x1F] {
            XCTAssertThrowsError(try JSONText.read(units: [0x22, unit, 0x22]), "\(unit)") {
                XCTAssertEqual(($0 as? JSONReadError)?.reason, "a control character inside a string")
            }
        }
        for unit in [UInt16(0x20), 0x7F, 0xFFFF] {
            XCTAssertEqual(try JSONText.read(units: [0x22, unit, 0x22]), .string(JSONString(units: [unit])))
        }
    }

    func testRefusesAKeyWithoutItsQuotesOrItsColon() {
        for text in ["{xk\":1}", "{k:1}", "{\"k\"x1}", "{\"k\" 1}", "{\"k\";1}", "{1:1}"] {
            XCTAssertThrowsError(try JSONText.read(text), text)
        }
        XCTAssertEqual(try? JSONText.read("{ \"k\" : 1 }"), .object(JSONObject(["k": .int(1)])))
    }

    func testReadsAnExponentWithEitherSignAndRefusesAnyOtherMark() throws {
        let cases: [(String, Double)] = [("1e+5", 100_000), ("1E+5", 100_000), ("1e-5", 1e-5), ("1E-5", 1e-5), ("1e5", 100_000), ("2e+0", 2)]
        for (text, value) in cases {
            XCTAssertEqual(try JSONText.read(text).numberValue, value, text)
            XCTAssertEqual(try JSONText.read("[\(text),1]").arrayValue?.count, 2, text)
        }
        for text in ["1e*5", "1e,5", "1e.5", "1e", "1e+", "1e-", "1e++5", "[1e,5]"] {
            XCTAssertThrowsError(try JSONText.read(text), text)
        }
    }
}
