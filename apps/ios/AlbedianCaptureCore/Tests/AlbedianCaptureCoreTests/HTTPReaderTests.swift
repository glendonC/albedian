import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The client's own HTTP/1.1: the request it writes, byte for byte, and the reply reader at every bound of
/// `HTTPReplyLimits`, each at its inside end and one step past, from one table.
final class HTTPReaderTests: XCTestCase {
    private func reply(status: String = "HTTP/1.1 201 Created", headers: [String] = [], body: String = "{}") -> Data {
        Data((([status] + headers + ["Content-Length: \(Data(body.utf8).count)"]).joined(separator: "\r\n") + "\r\n\r\n" + body).utf8)
    }

    func testWritesTheRequestByteForByteWithNothingElse() {
        let body = Data("abc".utf8)
        let four = HTTPMessage.request(method: "POST", path: "/api/ingest", endpoint: LaptopEndpoint(host: "192.168.1.20", port: 5173),
                                       contentType: "image/heic", body: body)
        XCTAssertEqual(String(decoding: four, as: UTF8.self),
                       "POST /api/ingest HTTP/1.1\r\nHost: 192.168.1.20:5173\r\nUser-Agent: Albedian/0.1 (research prototype)\r\n"
                       + "Content-Type: image/heic\r\nContent-Length: 3\r\nConnection: close\r\n\r\nabc")
        let six = HTTPMessage.request(method: "PUT", path: "/p", endpoint: LaptopEndpoint(host: "fe80::1", port: 80), contentType: "application/json", body: Data())
        XCTAssertTrue(String(decoding: six, as: UTF8.self).hasPrefix("PUT /p HTTP/1.1\r\nHost: [fe80::1]:80\r\n"))
        XCTAssertTrue(String(decoding: six, as: UTF8.self).hasSuffix("Content-Length: 0\r\nConnection: close\r\n\r\n"))
    }

    func testReadsAWholeReplyAndWaitsForEveryPartOfOne() {
        let whole = reply(headers: ["X-A: 1", "x-b:2  "], body: "{\"a\":1}")
        guard case .reply(let read) = reading(whole) else { return XCTFail() }
        XCTAssertEqual(read.status, 201)
        XCTAssertEqual(read.headers.map(\.name), ["x-a", "x-b", "content-length"])
        XCTAssertEqual(read.headers.map(\.value), ["1", "2", "7"])
        XCTAssertEqual(read.body, Data("{\"a\":1}".utf8))
        // Every proper prefix needs more; bytes after the body are not read
        for cut in 0..<whole.count { XCTAssertEqual(reading(whole.prefix(cut)), .needMore, "\(cut)") }
        XCTAssertEqual(reading(whole + Data("HTTP/1.1 500 X\r\n\r\n".utf8)), .reply(read))
        // HTTP/1.0 and an empty reason are taken
        XCTAssertNotNil({ if case .reply = reading(reply(status: "HTTP/1.0 200 ")) { true } else { nil } }())
        XCTAssertNotNil({ if case .reply = reading(reply(status: "HTTP/1.1 599 x")) { true } else { nil } }())
        XCTAssertNotNil({ if case .reply = reading(reply(status: "HTTP/1.1 100 x\r\n\r\nHTTP/1.1 200 x")) { true } else { nil } }())
        // 199, the last interim status, is read past too
        XCTAssertEqual(reading(reply(status: "HTTP/1.1 199 x\r\n\r\nHTTP/1.1 201 x")),
                       .reply(HTTPReply(status: 201, headers: [(name: "content-length", value: "2")], body: Data("{}".utf8))))
    }

    /// Each bound: a reply with the bounded part at the bound (taken) and one past it (refused).
    func testTakesEveryBoundAtItsEndAndRefusesOneStepPast() {
        let limits = HTTPReplyLimits.self
        XCTAssertEqual([limits.statusLineMaxBytes, limits.headerLineMaxBytes, limits.headerBlockMaxBytes, limits.headerCountMax,
                        limits.bodyMaxBytes, limits.interimRepliesMax], [256, 8192, 16384, 64, 65536, 8])
        func statusLine(_ length: Int) -> Data { reply(status: "HTTP/1.1 201 " + String(repeating: "r", count: length - 13)) }
        func headerLine(_ length: Int) -> Data { reply(headers: ["X-A: " + String(repeating: "v", count: length - 5)]) }
        func headerBlock(_ length: Int) -> Data {
            // Lines of 1000 bytes and one to fill, each with its line end, and the length header the helper adds (20 bytes)
            var lines: [String] = []
            var left = length - "Content-Length: 2\r\n".utf8.count
            while left > 0 {
                let line = min(left, 1002) - 2
                lines.append("X-\(lines.count): " + String(repeating: "v", count: max(0, line - "X-\(lines.count): ".utf8.count)))
                left -= line + 2
            }
            return reply(headers: lines)
        }
        func headerCount(_ count: Int) -> Data { reply(headers: (0..<(count - 1)).map { "X-\($0): 1" }) }
        func body(_ length: Int) -> Data { reply(body: String(repeating: "b", count: length)) }
        func interim(_ count: Int) -> Data {
            Data(String(repeating: "HTTP/1.1 100 Continue\r\n\r\n", count: count).utf8) + reply()
        }
        let table: [(String, (Int) -> Data, Int)] = [
            ("status line", statusLine, limits.statusLineMaxBytes),
            ("header line", headerLine, limits.headerLineMaxBytes),
            ("header block", headerBlock, limits.headerBlockMaxBytes),
            ("header count", headerCount, limits.headerCountMax),
            ("body", body, limits.bodyMaxBytes),
            ("interim replies", interim, limits.interimRepliesMax),
        ]
        for (name, make, bound) in table {
            guard case .reply = reading(make(bound)) else { XCTFail("\(name) at \(bound): \(reading(make(bound)))"); continue }
            guard case .unreadable = reading(make(bound + 1)) else { XCTFail("\(name) at \(bound + 1): \(reading(make(bound + 1)))"); continue }
        }
        // The Content-Length's digits: up to nine
        XCTAssertEqual(reading(Data("HTTP/1.1 200 x\r\nContent-Length: 000000002\r\n\r\n{}".utf8)),
                       .reply(HTTPReply(status: 200, headers: [("content-length", "000000002")], body: Data("{}".utf8))))
        guard case .unreadable = reading(Data("HTTP/1.1 200 x\r\nContent-Length: 0000000002\r\n\r\n{}".utf8)) else { return XCTFail() }
    }

    func testRefusesEveryReplyItCannotReadExactly() {
        let bad: [(String, String)] = [
            ("no status line", "\r\nContent-Length: 0\r\n\r\n"),
            ("a lower-case version", "http/1.1 200 x\r\nContent-Length: 0\r\n\r\n"),
            ("HTTP/1.2", "HTTP/1.2 200 x\r\nContent-Length: 0\r\n\r\n"),
            ("HTTP/2", "HTTP/2 200 x\r\nContent-Length: 0\r\n\r\n"),
            ("two spaces", "HTTP/1.1  200 x\r\nContent-Length: 0\r\n\r\n"),
            ("a letter in the status", "HTTP/1.1 2O0 x\r\nContent-Length: 0\r\n\r\n"),
            ("status 099", "HTTP/1.1 099 x\r\nContent-Length: 0\r\n\r\n"),
            ("status 600", "HTTP/1.1 600 x\r\nContent-Length: 0\r\n\r\n"),
            ("a control character in the reason", "HTTP/1.1 200 \u{1}\r\nContent-Length: 0\r\n\r\n"),
            ("no reason space", "HTTP/1.1 200\r\nContent-Length: 0\r\n\r\n"),
            ("a bare line feed", "HTTP/1.1 200 x\nContent-Length: 0\r\n\r\n"),
            ("a carriage return alone", "HTTP/1.1 200 x\rContent-Length: 0\r\n\r\n"),
            ("a header with no colon", "HTTP/1.1 200 x\r\nContent-Length 0\r\n\r\n"),
            ("an empty header name", "HTTP/1.1 200 x\r\n: 1\r\nContent-Length: 0\r\n\r\n"),
            ("a space in a header name", "HTTP/1.1 200 x\r\nContent Length: 0\r\n\r\n"),
            ("a folded line", "HTTP/1.1 200 x\r\nX-A: 1\r\n 2\r\nContent-Length: 0\r\n\r\n"),
            ("a control character in a value", "HTTP/1.1 200 x\r\nX-A: \u{0}\r\nContent-Length: 0\r\n\r\n"),
            ("no length", "HTTP/1.1 200 x\r\n\r\n{}"),
            ("an empty length", "HTTP/1.1 200 x\r\nContent-Length: \r\n\r\n"),
            ("a negative length", "HTTP/1.1 200 x\r\nContent-Length: -1\r\n\r\n"),
            ("a length with a space inside", "HTTP/1.1 200 x\r\nContent-Length: 1 2\r\n\r\n"),
            ("two lengths", "HTTP/1.1 200 x\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}"),
            ("a transfer coding", "HTTP/1.1 200 x\r\nTransfer-Encoding: chunked\r\nContent-Length: 2\r\n\r\n{}"),
            ("a switch of protocol", "HTTP/1.1 101 x\r\n\r\nHTTP/1.1 200 x\r\nContent-Length: 0\r\n\r\n"),
        ]
        for (name, text) in bad {
            guard case .unreadable = reading(Data(text.utf8)) else { XCTFail("\(name): \(reading(Data(text.utf8)))"); continue }
        }
        // A 3xx is answered by its status alone, with or without a body
        XCTAssertEqual(reading(Data("HTTP/1.1 308 x\r\nLocation: http://127.1/\r\n\r\n".utf8)),
                       .reply(HTTPReply(status: 308, headers: [("location", "http://127.1/")], body: Data())))
    }

    /// The reader, with a bound of its own: a reader that never ends fails the test here (the lead's rule)
    private func reading(_ bytes: Data, file: StaticString = #filePath, line: UInt = #line) -> HTTPMessage.ReadOutcome {
        finishes(otherwise: .unreadable("the reader did not end"), file: file, line: line) { HTTPMessage.read(bytes) }
    }

    private func taken(_ text: [UInt8]) -> Bool { if case .reply = reading(Data(text)) { true } else { false } }

    func testTakesEachCharacterOfAStatusLineANameAndAValueOnlyFromItsOwnClass() {
        func status(_ reason: [UInt8]) -> [UInt8] { Array("HTTP/1.1 200 ".utf8) + reason + Array("\r\nContent-Length: 0\r\n\r\n".utf8) }
        func header(name: [UInt8], value: [UInt8] = Array("1".utf8)) -> [UInt8] {
            Array("HTTP/1.1 200 x\r\n".utf8) + name + [0x3A, 0x20] + value + Array("\r\nContent-Length: 0\r\n\r\n".utf8)
        }
        // The reason: tab, space and visible characters
        for unit: UInt8 in [0x09, 0x20, 0x21, 0x41, 0x7E] { XCTAssertTrue(taken(status([unit])), "reason \(unit)") }
        for unit: UInt8 in [0x00, 0x08, 0x0B, 0x1F, 0x7F, 0x80, 0xFF] { XCTAssertFalse(taken(status([unit])), "reason \(unit)") }
        XCTAssertFalse(taken(Array("HTTP/1.1 200x OK\r\nContent-Length: 0\r\n\r\n".utf8)), "no space after the status")
        // A name: letters, digits and the token characters of RFC 9110
        for name in ["X9", "XZ", "xz", "0", "a", "Z", "x!#$%&'*+-.^_`|~"] { XCTAssertTrue(taken(header(name: Array(name.utf8))), name) }
        for name in ["X(", "X)", "X/", "X@", "X[", "X]", "X{", "X}", "X\"", "X,", "X;", "X=", "X?", "X\\", "X ", "X\t", "X\u{7F}"] {
            XCTAssertFalse(taken(header(name: Array(name.utf8))), name)
        }
        // The units next to each run of letters and digits that are not token characters (0x60, the backtick, is one)
        for unit: UInt8 in [0x2F, 0x40, 0x5B, 0x7B] {
            XCTAssertFalse(taken(header(name: [0x58, unit])), "name unit \(unit)")
        }
        // A value: tab, space, visible characters and bytes from 0x80 (obs-text)
        for unit: UInt8 in [0x09, 0x20, 0x7E, 0x80, 0xFF] { XCTAssertTrue(taken(header(name: Array("X".utf8), value: [0x61, unit, 0x61])), "value \(unit)") }
        for unit: UInt8 in [0x00, 0x08, 0x0B, 0x1F, 0x7F] { XCTAssertFalse(taken(header(name: Array("X".utf8), value: [0x61, unit, 0x61])), "value \(unit)") }
        // A carriage return must end its line with a line feed, even when what follows would read as a header
        XCTAssertFalse(taken(Array("HTTP/1.1 200 x\rXContent-Length: 2\r\n\r\n{}".utf8)))
    }

    func testReadsA3xxByItsStatusAloneAcrossTheWholeRange() {
        for status in [300, 301, 308, 399] {
            XCTAssertEqual(reading(Data("HTTP/1.1 \(status) x\r\n\r\n".utf8)), .reply(HTTPReply(status: status, headers: [], body: Data())), "\(status)")
        }
        // Either side of the range needs a length
        for status in [299, 400] { XCTAssertEqual(reading(Data("HTTP/1.1 \(status) x\r\n\r\n".utf8)), .unreadable("the reply states no single length"), "\(status)") }
    }

    func testTellsRepliesApartByEachOfTheirParts() {
        let base = HTTPReply(status: 201, headers: [("a", "1")], body: Data("x".utf8))
        XCTAssertEqual(base, HTTPReply(status: 201, headers: [("a", "1")], body: Data("x".utf8)))
        XCTAssertNotEqual(base, HTTPReply(status: 200, headers: [("a", "1")], body: Data("x".utf8)))
        XCTAssertNotEqual(base, HTTPReply(status: 201, headers: [("b", "1")], body: Data("x".utf8)))
        XCTAssertNotEqual(base, HTTPReply(status: 201, headers: [("a", "2")], body: Data("x".utf8)))
        XCTAssertNotEqual(base, HTTPReply(status: 201, headers: [("a", "1")], body: Data("y".utf8)))
    }
}
