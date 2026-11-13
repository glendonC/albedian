import Foundation

/// JSON text read and written the way the two other readings of the contract read it: JavaScript's `JSON.parse`
/// (the Zod side) and Python's `json` (the Pydantic side).
///
/// - A string is kept as UTF-16 code units, as JavaScript keeps it, so a lone surrogate written as `\ud800` survives
///   the reading and is judged by the contract (no Swift `String` can hold one; it would turn into U+FFFD).
/// - A number keeps the literal it was written with, and its value is the nearest double (`Double(literal)`, which
///   rounds as `JSON.parse` does: `1e400` reads as infinity and `9007199254740993` as 2^53) [tested].
/// - An object keeps its keys in the order first written; a key written twice keeps its first place and its last
///   value, as `JSON.parse` and Python's `json` both do [inference from their documented behaviour].
/// - Only the JSON grammar of RFC 8259 is read: no `NaN`, no `Infinity`, no comments, no trailing commas, no byte
///   order mark, and no unescaped control character inside a string.
public struct JSONString: Hashable, Sendable, ExpressibleByStringLiteral, CustomStringConvertible {
    public var units: [UInt16]

    public init(units: [UInt16]) { self.units = units }
    public init(_ string: String) { self.units = Array(string.utf16) }
    public init(stringLiteral value: String) { self.init(value) }

    /// Whether every surrogate is one half of a pair, so the text is valid Unicode.
    public var isWellFormed: Bool {
        var index = 0
        while index < units.count {
            let unit = units[index]
            if (0xD800...0xDBFF).contains(unit) {
                guard index + 1 < units.count, (0xDC00...0xDFFF).contains(units[index + 1]) else { return false }
                index += 2
                continue
            }
            if (0xDC00...0xDFFF).contains(unit) { return false }
            index += 1
        }
        return true
    }

    /// The text, or nil when it holds a lone surrogate.
    public var string: String? { isWellFormed ? String(decoding: units, as: UTF16.self) : nil }

    public var description: String { string ?? "<\(units.count) UTF-16 units, not valid Unicode>" }
}

/// A JSON number: the literal it was written with, and its value as the nearest double.
public struct JSONNumber: Equatable, Sendable, CustomStringConvertible {
    public let literal: String
    public let value: Double

    /// A number read from JSON text; `literal` must already follow the JSON number grammar.
    init(literal: String) {
        self.literal = literal
        // The grammar was checked by the reader, so Double(_:) only overflows to infinity or underflows to zero
        self.value = Double(literal) ?? .nan
    }

    /// A number written from a finite double: a whole number up to 2^53 as an integer, anything else in the
    /// shortest form that reads back as the same double; minus zero is written as 0. Nil for an infinite or
    /// not-a-number value, which JSON cannot hold.
    public init?(_ value: Double) {
        guard value.isFinite else { return nil }
        self.value = value == 0 ? 0 : value
        if value == 0 {
            self.literal = "0"
        } else if value == value.rounded(), abs(value) <= 9_007_199_254_740_992 {
            self.literal = String(Int64(value))
        } else {
            self.literal = value.description
        }
    }

    public init(_ value: Int) {
        self.value = Double(value)
        self.literal = String(value)
    }

    public static func == (lhs: JSONNumber, rhs: JSONNumber) -> Bool { lhs.literal == rhs.literal }

    public var description: String { literal }
}

/// A JSON object with its members in the order their keys were first written.
public struct JSONObject: Equatable, Sendable {
    public struct Member: Equatable, Sendable {
        public var key: JSONString
        public var value: JSONValue
    }

    public private(set) var members: [Member]

    public init(_ members: [Member] = []) { self.members = [] ; for member in members { set(member.key, member.value) } }

    public init(_ pairs: KeyValuePairs<String, JSONValue>) {
        self.members = []
        for (key, value) in pairs { set(JSONString(key), value) }
    }

    public var keys: [JSONString] { members.map(\.key) }

    public func value(for key: JSONString) -> JSONValue? { members.first { $0.key == key }?.value }

    /// Sets a member: a key already present keeps its place and takes the new value, a new key goes last.
    public mutating func set(_ key: JSONString, _ value: JSONValue) {
        if let index = members.firstIndex(where: { $0.key == key }) {
            members[index].value = value
        } else {
            members.append(Member(key: key, value: value))
        }
    }

    public mutating func remove(_ key: JSONString) { members.removeAll { $0.key == key } }

    public subscript(key: String) -> JSONValue? {
        get { value(for: JSONString(key)) }
        set {
            if let newValue { set(JSONString(key), newValue) } else { remove(JSONString(key)) }
        }
    }
}

public indirect enum JSONValue: Equatable, Sendable {
    case null
    case bool(Bool)
    case number(JSONNumber)
    case string(JSONString)
    case array([JSONValue])
    case object(JSONObject)

    public static func string(_ text: String) -> JSONValue { .string(JSONString(text)) }
    public static func int(_ value: Int) -> JSONValue { .number(JSONNumber(value)) }

    public var objectValue: JSONObject? { if case .object(let object) = self { object } else { nil } }
    public var stringValue: String? { if case .string(let text) = self { text.string } else { nil } }
    public var arrayValue: [JSONValue]? { if case .array(let items) = self { items } else { nil } }
    public var numberValue: Double? { if case .number(let number) = self { number.value } else { nil } }
    public var boolValue: Bool? { if case .bool(let flag) = self { flag } else { nil } }

    /// The member at `key` of an object, or nil.
    public subscript(key: String) -> JSONValue? {
        get { objectValue?[key] }
        set {
            guard case .object(var object) = self else { return }
            object[key] = newValue
            self = .object(object)
        }
    }
}

public struct JSONReadError: Error, Equatable, Sendable, CustomStringConvertible {
    /// Where the reading stopped, in UTF-16 units from the start (or in bytes, for text that is not UTF-8).
    public let offset: Int
    public let reason: String
    public var description: String { "not JSON at \(offset): \(reason)" }
}

public enum JSONText {
    /// The deepest nesting read [proposed]: the pointing record nests four levels; 64 leaves room and keeps a
    /// hostile text from exhausting the stack.
    public static let maxDepth = 64

    /// Reads JSON from bytes, which must be UTF-8 (a byte sequence that is not, such as an encoded surrogate, is
    /// refused rather than replaced).
    public static func read(_ data: Data) throws(JSONReadError) -> JSONValue {
        guard let text = String(validating: data, as: UTF8.self) else {
            throw JSONReadError(offset: firstInvalidUTF8Byte(data), reason: "the bytes are not UTF-8")
        }
        return try read(units: Array(text.utf16))
    }

    /// Where the first byte sequence that is not UTF-8 starts.
    static func firstInvalidUTF8Byte(_ data: Data) -> Int {
        var iterator = data.makeIterator()
        var decoder = UTF8()
        var offset = 0
        while case .scalarValue(let scalar) = decoder.decode(&iterator) { offset += UTF8.width(scalar) }
        return offset
    }

    /// Reads JSON from UTF-16 code units, as JavaScript holds a text.
    public static func read(units: [UInt16]) throws(JSONReadError) -> JSONValue {
        var reader = Reader(units: units)
        reader.skipWhitespace()
        let value = try reader.value(depth: 0)
        reader.skipWhitespace()
        guard reader.atEnd else { throw reader.error("text after the value") }
        return value
    }

    public static func read(_ text: String) throws(JSONReadError) -> JSONValue { try read(units: Array(text.utf16)) }

    /// Compact JSON text, as `JSON.stringify` writes it, in UTF-16 units. A lone surrogate is written as its
    /// `\u` escape, so the text is always valid Unicode.
    public static func units(_ value: JSONValue) -> [UInt16] {
        var out: [UInt16] = []
        write(value, into: &out, indent: nil, level: 0)
        return out
    }

    /// Compact JSON text as UTF-8 bytes.
    public static func data(_ value: JSONValue) -> Data {
        Data(String(decoding: units(value), as: UTF16.self).utf8)
    }

    /// The same text indented by two spaces, for reading on a screen.
    public static func pretty(_ value: JSONValue) -> String {
        var out: [UInt16] = []
        write(value, into: &out, indent: 2, level: 0)
        return String(decoding: out, as: UTF16.self)
    }

    private static func write(_ value: JSONValue, into out: inout [UInt16], indent: Int?, level: Int) {
        func ascii(_ text: String) { out.append(contentsOf: text.utf16) }
        func newline(_ depth: Int) {
            guard let indent else { return }
            out.append(0x0A)
            out.append(contentsOf: Array(repeating: 0x20, count: indent * depth))
        }
        switch value {
        case .null: ascii("null")
        case .bool(let flag): ascii(flag ? "true" : "false")
        case .number(let number): ascii(number.literal)
        case .string(let text): writeString(text, into: &out)
        case .array(let items):
            out.append(0x5B)
            for (index, item) in items.enumerated() {
                if index > 0 { out.append(0x2C) }
                newline(level + 1)
                write(item, into: &out, indent: indent, level: level + 1)
            }
            if !items.isEmpty { newline(level) }
            out.append(0x5D)
        case .object(let object):
            out.append(0x7B)
            for (index, member) in object.members.enumerated() {
                if index > 0 { out.append(0x2C) }
                newline(level + 1)
                writeString(member.key, into: &out)
                out.append(0x3A)
                if indent != nil { out.append(0x20) }
                write(member.value, into: &out, indent: indent, level: level + 1)
            }
            if !object.members.isEmpty { newline(level) }
            out.append(0x7D)
        }
    }

    private static func writeString(_ text: JSONString, into out: inout [UInt16]) {
        func escape(_ unit: UInt16) {
            out.append(contentsOf: "\\u".utf16)
            let hex = String(unit, radix: 16)
            out.append(contentsOf: (String(repeating: "0", count: 4 - hex.count) + hex).utf16)
        }
        out.append(0x22)
        let units = text.units
        var index = 0
        while index < units.count {
            let unit = units[index]
            switch unit {
            case 0x22: out.append(contentsOf: "\\\"".utf16)
            case 0x5C: out.append(contentsOf: "\\\\".utf16)
            case 0x08: out.append(contentsOf: "\\b".utf16)
            case 0x0C: out.append(contentsOf: "\\f".utf16)
            case 0x0A: out.append(contentsOf: "\\n".utf16)
            case 0x0D: out.append(contentsOf: "\\r".utf16)
            case 0x09: out.append(contentsOf: "\\t".utf16)
            case 0x00..<0x20: escape(unit)
            case 0xD800...0xDBFF:
                if index + 1 < units.count, (0xDC00...0xDFFF).contains(units[index + 1]) {
                    out.append(unit)
                    out.append(units[index + 1])
                    index += 1
                } else {
                    escape(unit)
                }
            case 0xDC00...0xDFFF: escape(unit)
            default: out.append(unit)
            }
            index += 1
        }
        out.append(0x22)
    }

    /// A recursive-descent reader over UTF-16 units, following RFC 8259 section by section.
    private struct Reader {
        let units: [UInt16]
        var index = 0

        var atEnd: Bool { index >= units.count }
        var current: UInt16? { atEnd ? nil : units[index] }

        func error(_ reason: String) -> JSONReadError { JSONReadError(offset: index, reason: reason) }

        mutating func skipWhitespace() {
            // Section 2: space, horizontal tab, line feed, carriage return; nothing else
            while let unit = current, unit == 0x20 || unit == 0x09 || unit == 0x0A || unit == 0x0D { index += 1 }
        }

        mutating func value(depth: Int) throws(JSONReadError) -> JSONValue {
            guard depth < JSONText.maxDepth else { throw error("nested deeper than \(JSONText.maxDepth)") }
            guard let unit = current else { throw error("a value was expected") }
            switch unit {
            case 0x7B: return try object(depth: depth)
            case 0x5B: return try array(depth: depth)
            case 0x22: return .string(try string())
            case 0x74: try word("true"); return .bool(true)
            case 0x66: try word("false"); return .bool(false)
            case 0x6E: try word("null"); return .null
            case 0x2D, 0x30...0x39: return .number(try number())
            default: throw error("a value was expected")
            }
        }

        mutating func word(_ text: String) throws(JSONReadError) {
            for expected in text.utf16 {
                guard current == expected else { throw error("\(text) was expected") }
                index += 1
            }
        }

        mutating func object(depth: Int) throws(JSONReadError) -> JSONValue {
            index += 1
            var object = JSONObject()
            skipWhitespace()
            if current == 0x7D { index += 1; return .object(object) }
            while true {
                skipWhitespace()
                guard current == 0x22 else { throw error("a key was expected") }
                let key = try string()
                skipWhitespace()
                guard current == 0x3A else { throw error("a colon was expected") }
                index += 1
                skipWhitespace()
                object.set(key, try value(depth: depth + 1))
                skipWhitespace()
                if current == 0x2C { index += 1; continue }
                if current == 0x7D { index += 1; return .object(object) }
                throw error("a comma or the end of the object was expected")
            }
        }

        mutating func array(depth: Int) throws(JSONReadError) -> JSONValue {
            index += 1
            var items: [JSONValue] = []
            skipWhitespace()
            if current == 0x5D { index += 1; return .array(items) }
            while true {
                skipWhitespace()
                items.append(try value(depth: depth + 1))
                skipWhitespace()
                if current == 0x2C { index += 1; continue }
                if current == 0x5D { index += 1; return .array(items) }
                throw error("a comma or the end of the array was expected")
            }
        }

        /// Section 7: any unit but the quote, the backslash and the control characters, or an escape.
        mutating func string() throws(JSONReadError) -> JSONString {
            index += 1
            var out: [UInt16] = []
            while true {
                guard let unit = current else { throw error("the string does not end") }
                index += 1
                switch unit {
                case 0x22:
                    return JSONString(units: out)
                case 0x5C:
                    guard let escaped = current else { throw error("the escape does not end") }
                    index += 1
                    switch escaped {
                    case 0x22, 0x5C, 0x2F: out.append(escaped)
                    case 0x62: out.append(0x08)
                    case 0x66: out.append(0x0C)
                    case 0x6E: out.append(0x0A)
                    case 0x72: out.append(0x0D)
                    case 0x74: out.append(0x09)
                    case 0x75:
                        var code: UInt16 = 0
                        for _ in 0..<4 {
                            guard let digit = current, let value = Self.hexValue(digit) else { throw error("four hex digits were expected") }
                            code = code * 16 + value
                            index += 1
                        }
                        out.append(code)
                    default:
                        throw error("an unknown escape")
                    }
                case 0x00..<0x20:
                    throw error("a control character inside a string")
                default:
                    out.append(unit)
                }
            }
        }

        static func hexValue(_ unit: UInt16) -> UInt16? {
            switch unit {
            case 0x30...0x39: unit - 0x30
            case 0x41...0x46: unit - 0x41 + 10
            case 0x61...0x66: unit - 0x61 + 10
            default: nil
            }
        }

        /// Section 6: `-? (0 | [1-9][0-9]*) (. [0-9]+)? ([eE] [+-]? [0-9]+)?`.
        mutating func number() throws(JSONReadError) -> JSONNumber {
            let start = index
            func digit(_ unit: UInt16?) -> Bool { unit.map { (0x30...0x39).contains($0) } ?? false }
            if current == 0x2D { index += 1 }
            if current == 0x30 {
                index += 1
            } else if digit(current) {
                while digit(current) { index += 1 }
            } else {
                throw error("a digit was expected")
            }
            if current == 0x2E {
                index += 1
                guard digit(current) else { throw error("a digit was expected after the point") }
                while digit(current) { index += 1 }
            }
            if current == 0x65 || current == 0x45 {
                index += 1
                if current == 0x2B || current == 0x2D { index += 1 }
                guard digit(current) else { throw error("a digit was expected in the exponent") }
                while digit(current) { index += 1 }
            }
            return JSONNumber(literal: String(decoding: units[start..<index], as: UTF16.self))
        }
    }
}
