import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The local network check at the edge of every range it names, and the laptop's success statuses at both ends of
/// 2xx, against a server on 127.0.0.1 that each test starts and stops.
final class LaptopAddressEdgeTests: XCTestCase {
    func testTakesALocalNameOnlyOfLettersDigitsAndHyphens() {
        for name in ["laptop.local", "a.local", "z.local", "0.local", "9.local", "a-b.local", "a.b.local", "z9-0a.local",
                     "LAPTOP.LOCAL", "localhost", "LocalHost"] {
            XCTAssertTrue(LaptopAddress.isLocal(name), name)
        }
        for name in [".local", "a..local", ".a.local", "a.local.", "a_b.local", "a,b.local", "a`.local", "a{.local", "a/.local",
                     "a@.local", "a.b..local", "local", "a.localx", "localhost.", "xlocalhost"] {
            XCTAssertFalse(LaptopAddress.isLocal(name), name)
        }
    }

    func testTakesAnIPv6AddressOnlyInTheLoopbackLinkLocalAndUniqueLocalRanges() {
        for name in ["::1", "0:0:0:0:0:0:0:1", "fe80::1", "fe81::1", "febf::1", "fe80::", "FE80::1", "fc00::1", "fd12:3456::1", "fdff::"] {
            XCTAssertTrue(LaptopAddress.isLocal(name), name)
        }
        for name in ["::", "::2", "::101", "1::1", "0:0:0:0:0:0:1:1", "fe00::1", "fe7f::1", "fec0::1", "feff::1", "8080::1", "fb00::1",
                     "fe::1", "2001:db8::1", "::ffff:10.0.0.1", "fe80::zz", ":::", "fe80:1", "[::1]"] {
            XCTAssertFalse(LaptopAddress.isLocal(name), name)
        }
    }

    func testTakesAnIPv4AddressOnlyOfFourDecimalOctetsInALocalRange() {
        for name in ["127.0.0.1", "127.255.255.255", "10.0.0.0", "10.255.255.255", "192.168.0.0", "192.168.255.255", "169.254.0.1",
                     "169.254.255.255", "172.16.0.0", "172.31.255.255", "10.0.0.255", "10.0.0.0"] {
            XCTAssertTrue(LaptopAddress.isLocal(name), name)
        }
        for name in ["10.0.0.1.", ".10.0.0.1", "010.0.0.1", "10.00.0.1", "10.0.0.01", "10.000.0.1", "10..0.1", "10.0.0", "10.0.0.0.0", "10.0.0.256", "10.0.0.1000", "10.0.0.0255", "10.0.0.-1",
                     "10.0.0.+1", "10.0.0.a", "10.0.0. 1", "172.15.255.255", "172.32.0.0", "192.167.0.1", "192.169.0.1", "169.253.0.1",
                     "169.255.0.1", "126.0.0.1", "128.0.0.1", "9.0.0.1", "11.0.0.1", "0.0.0.0", "255.255.255.255"] {
            XCTAssertFalse(LaptopAddress.isLocal(name), name)
        }
    }

    func testTakesNoAddressWithAUserOrAPasswordPart() {
        for text in ["http://user@127.0.0.1:8000", "http://user:@127.0.0.1:8000", "http://:pw@127.0.0.1:8000", "http://user:pw@127.0.0.1:8000",
                     "http://@127.0.0.1:8000", "http://a@laptop.local:5173", "http://a@[::1]:8000"] {
            XCTAssertNil(LaptopAddress(text), text)
        }
        XCTAssertNotNil(LaptopAddress("http://127.0.0.1:8000"))
    }

    func testTakesAPortFromOneTo65535AndReadsTheAddressOnceIntoItsEndpoint() throws {
        XCTAssertEqual(LaptopAddress("http://127.0.0.1")?.endpoint, LaptopEndpoint(host: "127.0.0.1", port: 80))
        XCTAssertEqual(LaptopAddress("http://127.0.0.1:1")?.endpoint.port, 1)
        XCTAssertEqual(LaptopAddress("http://127.0.0.1:65535")?.endpoint.port, 65535)
        for text in ["http://127.0.0.1:0", "http://127.0.0.1:65536", "http://127.0.0.1:99999", "http://127.0.0.1:-1"] { XCTAssertNil(LaptopAddress(text), text) }
        XCTAssertEqual(LaptopAddress("http://[FE80::1%25lo0]:8000")?.endpoint, LaptopEndpoint(host: "fe80::1%lo0", port: 8000))
        XCTAssertEqual(LaptopAddress("http://Laptop.LOCAL:5173")?.endpoint, LaptopEndpoint(host: "laptop.local", port: 5173))
        XCTAssertEqual(LaptopAddress("http://[::1]:8000")?.description, "http://[::1]:8000")
        XCTAssertEqual(LaptopAddress("http://192.168.1.20/")?.description, "http://192.168.1.20:80")
    }

    func testTakesAZoneOnlyOnALinkLocalAddressAsAnInterfaceNameAndSendsItNowhere() throws {
        // A link-local address needs its interface; a zone is an interface name of letters and digits, 1 to 15
        for (text, host) in [("http://[fe80::1%25en0]:8000", "fe80::1%en0"), ("http://[fe80::1%25lo0]:8000", "fe80::1%lo0"),
                             ("http://[FE80::AB%25utun12]:8000", "fe80::ab%utun12"), ("http://[fe80::1%25a]:8000", "fe80::1%a"),
                             ("http://[fe80::1%25abcdefghijklmn5]:8000", "fe80::1%abcdefghijklmn5")] {
            XCTAssertEqual(LaptopAddress(text)?.endpoint, LaptopEndpoint(host: host, port: 8000), text)
        }
        for text in ["http://[fe80::1]:8000", "http://[fe80::1%25]:8000", "http://[fe80::1%25en0.x]:8000", "http://[fe80::1%25en%2F0]:8000",
                     "http://[fe80::1%25abcdefghijklmno6]:8000", "http://[fe80::1%25en-0]:8000", "http://[fe80::1%25%25en0]:8000",
                     "http://[::1%25example.com]:8000", "http://[::1%25lo0]:8000", "http://[::1%25]:8000", "http://[fd00::1%25anything]:8000",
                     "http://[fd00::1%25en0]:8000"] {
            XCTAssertNil(LaptopAddress(text), text)
        }
        XCTAssertNotNil(LaptopAddress("http://[::1]:8000"))
        XCTAssertNotNil(LaptopAddress("http://[fd00::1]:8000"))
        // A pairing address is written one way (AUDIT L's lenient forms): the zone as %25, its name starting with a
        // letter, brackets only around IPv6
        for text in ["http://[fe80::1%en0]:8000", "http://[fe80::1%2525en0]:8000", "http://[fe80::1%25%30]:8000", "http://[fe80::1%250en]:8000",
                     "http://[192.168.1.5]:8000", "http://[127.0.0.1]:8000", "http://[laptop.local]:5173", "http://[localhost]:5173"] {
            XCTAssertNil(LaptopAddress(text), text)
        }
        XCTAssertNotNil(LaptopAddress("http://192.168.1.5:8000"))
        XCTAssertFalse(LaptopAddress.zoneIsSound("fe80::1%0en"))
        // Each end of each character class a zone may hold
        XCTAssertEqual(LaptopAddress("http://[fe80::1%25Zz9]:8000")?.endpoint, LaptopEndpoint(host: "fe80::1%zz9", port: 8000))
        XCTAssertEqual(LaptopAddress("http://[fe80::1%25Aa0]:8000")?.endpoint, LaptopEndpoint(host: "fe80::1%aa0", port: 8000))
        XCTAssertEqual(LaptopAddress("http://[fe80::1%25zz0]:8000")?.endpoint, LaptopEndpoint(host: "fe80::1%zz0", port: 8000))
        for zone in ["@", "[", "`", "{", "/", ":"] { XCTAssertFalse(LaptopAddress.zoneIsSound("fe80::1%a\(zone)"), zone) }
        // The zone stays on the phone: the URL writes it %25, the Host line leaves it out (RFC 9844, section 6)
        let address = try XCTUnwrap(LaptopAddress("http://[fe80::1%25en0]:8000"))
        XCTAssertEqual(address.description, "http://[fe80::1%25en0]:8000")
        XCTAssertEqual(LaptopAddress(address.description)?.endpoint, address.endpoint)
        let request = String(decoding: HTTPMessage.request(method: "POST", path: "/api/ingest", endpoint: address.endpoint,
                                                           contentType: "image/heic", body: Data()), as: UTF8.self)
        XCTAssertTrue(request.contains("\r\nHost: [fe80::1]:8000\r\n"), request)
        XCTAssertFalse(request.contains("en0"))
    }

    func testReadsAnAddressIntoItsBytesOnlyWhenItIsWhole() {
        XCTAssertEqual(LaptopAddress.addressBytes("192.168.1.20"), [192, 168, 1, 20])
        XCTAssertEqual(LaptopAddress.addressBytes("fe80::1%en0")?.count, 16)
        for text in ["10.0.0", "10.0.0.1.2", "laptop.local", "localhost", "%::1", "", "%"] { XCTAssertNil(LaptopAddress.addressBytes(text), text) }
        XCTAssertFalse(LaptopAddress.isLocal("%::1"))
        // Only 4 or 16 bytes are an address: no other length is local, whatever its first bytes
        for bytes: [UInt8] in [[], [10], [10, 0, 0], [252, 0, 0], [254, 128, 0], [127, 0, 0, 1, 0]] {
            XCTAssertFalse(LaptopAddress.isLocal(bytes: bytes), "\(bytes)")
        }
        XCTAssertFalse(LaptopAddress.isLinkLocal([254, 128, 0, 1]), "an IPv4 address is never IPv6 link-local")
        XCTAssertTrue(LaptopAddress.isLinkLocal([0xFE, 0x80] + [UInt8](repeating: 0, count: 13) + [1]))
    }

    func testWaitsSixtySecondsForTheLaptopUnlessToldOtherwise() async throws {
        let address = try XCTUnwrap(LaptopAddress("http://127.0.0.1:8000"))
        XCTAssertEqual(CaptureUploader.defaultTimeoutSec, 60)
        XCTAssertEqual(CaptureUploader(address: address).timeoutSec, 60)
        XCTAssertEqual(CaptureUploader(address: address, timeoutSec: 5).timeoutSec, 5)
        // The limit the transport is given is that one: 60 unless told otherwise
        final class Recording: LaptopTransport, @unchecked Sendable {
            let lock = NSLock()
            var limits: [Double] = []
            func exchange(_ request: Data, to endpoint: LaptopEndpoint, timeoutSec: Double,
                          onSent: @escaping @Sendable (Int64) -> Void) async -> Result<HTTPReply, ExchangeFailure> {
                lock.withLock { limits.append(timeoutSec) }
                return .failure(.closed)
            }
        }
        let photo = Data("synthetic photo bytes for the time limit".utf8)
        for (given, expected) in [(nil, 60.0), (Optional(5.0), 5.0)] {
            let recording = Recording()
            let uploader = given.map { CaptureUploader(address: address, timeoutSec: $0, transport: recording) }
                ?? CaptureUploader(address: address, transport: recording)
            let last = await bounded { await uploader.send(photo: photo, contentType: .heic, record: nil) { _ in } }
            XCTAssertEqual(last, .failed(.connectionClosed(phase: .photo)))
            XCTAssertEqual(recording.lock.withLock { recording.limits }, [expected])
        }
    }

    /// The laptop of UploadClientTests, but answering the photo or the record with the given status.
    private func send(photoStatus: Int?, recordStatus: Int?) async throws -> UploadState {
        let server = try LoopbackHTTPServer { request in
            let answer = UploadClientTests.laptop(request)
            let status = request.method == "POST" ? photoStatus : recordStatus
            return status.map { LoopbackHTTPServer.Response(status: $0, body: answer.body) } ?? answer
        }
        defer { server.stop() }
        let photo = Data("synthetic photo bytes for the status edges".utf8)
        let record = try PointingRecordBuilder.build(SampleCapture.capture(photoSha256: PhotoHash.sha256Hex(photo)))
        let address = try XCTUnwrap(LaptopAddress(server.baseURL))
        return await bounded { await CaptureUploader(address: address, timeoutSec: 10).send(photo: photo, contentType: .heic, record: record) { _ in } }
    }

    func testTakesEveryStatusFrom200To299AsSuccessForThePhotoAndTheRecord() async throws {
        let sha = PhotoHash.sha256Hex(Data("synthetic photo bytes for the status edges".utf8))
        for status in [200, 201, 299] {
            let photoLast = try await send(photoStatus: status, recordStatus: nil)
            XCTAssertEqual(photoLast, .done(sha256: sha, photoAlreadyStored: false, recordAccepted: true), "photo \(status)")
            let recordLast = try await send(photoStatus: nil, recordStatus: status)
            XCTAssertEqual(recordLast, .done(sha256: sha, photoAlreadyStored: false, recordAccepted: true), "record \(status)")
        }
        // A 3xx is a redirect, never followed and never a success (RequestGateTests has each kind with a Location)
        for status in [300, 301, 399] {
            let photoLast = try await send(photoStatus: status, recordStatus: nil)
            XCTAssertEqual(photoLast, .failed(.redirected(phase: .photo, status: status)), "photo \(status)")
            let recordLast = try await send(photoStatus: nil, recordStatus: status)
            XCTAssertEqual(recordLast, .failed(.redirected(phase: .record, status: status)), "record \(status)")
        }
        for status in [400, 404, 422, 500] {
            let photoLast = try await send(photoStatus: status, recordStatus: nil)
            XCTAssertEqual(photoLast, .failed(.refused(phase: .photo, status: status, detail: nil)), "photo \(status)")
            let recordLast = try await send(photoStatus: nil, recordStatus: status)
            XCTAssertEqual(recordLast, .failed(.refused(phase: .record, status: status, detail: nil)), "record \(status)")
        }
    }
}
