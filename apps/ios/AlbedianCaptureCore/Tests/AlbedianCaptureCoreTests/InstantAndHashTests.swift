import Foundation
import XCTest
@testable import AlbedianCaptureCore

final class InstantAndHashTests: XCTestCase {
    /// The synthetic moment of the other fixtures, 2030-01-01T02:00:00Z, as seconds since 1970.
    static let syntheticMoment = Date(timeIntervalSince1970: 1_893_463_200)

    func testWritesTheDeviceClockAsAContractInstantRoundedDownToTheMillisecond() {
        XCTAssertEqual(UTCInstant.string(from: Self.syntheticMoment), "2030-01-01T02:00:00.000Z")
        XCTAssertEqual(UTCInstant.string(from: Self.syntheticMoment.addingTimeInterval(0.1239)), "2030-01-01T02:00:00.123Z")
        XCTAssertEqual(UTCInstant.string(from: Self.syntheticMoment.addingTimeInterval(-0.0001)), "2030-01-01T01:59:59.999Z")
        XCTAssertEqual(UTCInstant.string(from: Date(timeIntervalSince1970: 0)), "1970-01-01T00:00:00.000Z")
        // A leap day, and the first and last instants of the years the contract takes
        XCTAssertEqual(UTCInstant.string(from: Date(timeIntervalSince1970: 1_835_395_200)), "2028-02-29T00:00:00.000Z")
        XCTAssertEqual(UTCInstant.string(from: Date(timeIntervalSince1970: -62_135_596_800)), "0001-01-01T00:00:00.000Z")
        XCTAssertNil(UTCInstant.string(from: Date(timeIntervalSince1970: -62_135_596_800.001)))
        // A half second is exact in a double at this size; a thousandth is not, and rounds down to the stored value
        XCTAssertEqual(UTCInstant.string(from: Date(timeIntervalSince1970: 253_402_300_799.5)), "9999-12-31T23:59:59.500Z")
        XCTAssertNil(UTCInstant.string(from: Date(timeIntervalSince1970: 253_402_300_800)))
        XCTAssertNil(UTCInstant.string(from: Date(timeIntervalSince1970: .infinity)))
    }

    func testEveryInstantItWritesIsOneTheContractTakes() {
        var generator = SeededGenerator(seed: propertySeed())
        for _ in 0..<2000 {
            let seconds = generator.double(in: -62_135_596_800...253_402_300_799)
            guard let text = UTCInstant.string(from: Date(timeIntervalSince1970: seconds)) else { return XCTFail("\(seconds)") }
            XCTAssertTrue(UTCInstant.isValid(text), text)
        }
    }

    func testHashesBytesAsSHA256InLowerCaseHex() {
        // FIPS 180-2 examples (NIST): "abc", the empty message and the 448-bit message
        XCTAssertEqual(PhotoHash.sha256Hex(Data("abc".utf8)), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        XCTAssertEqual(PhotoHash.sha256Hex(Data()), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
        XCTAssertEqual(PhotoHash.sha256Hex(Data("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq".utf8)),
                       "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1")
        XCTAssertTrue(PhotoHash.isContractHash(PhotoHash.sha256Hex(Data("abc".utf8))))
        XCTAssertFalse(PhotoHash.isContractHash(String(repeating: "0", count: 64)))
        XCTAssertFalse(PhotoHash.isContractHash(String(repeating: "A", count: 64)))
        XCTAssertFalse(PhotoHash.isContractHash(String(repeating: "a", count: 63)))
    }
}
