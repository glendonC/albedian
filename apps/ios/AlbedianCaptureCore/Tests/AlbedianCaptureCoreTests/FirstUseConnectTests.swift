import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// firstuse-connect-one (UIUX-iphone-firstuse-READY item 1, DECIDED-BY-LEAD 26 Nov 23:58; the check ruled 27 Nov 04:17):
/// a newcomer connects from Aim, Connect names the one command that starts the laptop, and an address typed from Aim is
/// kept only once the laptop answers its health route as Albedian, a request that carries nothing personal.
final class FirstUseConnectTests: XCTestCase {
    final class Stub: LaptopTransport, @unchecked Sendable {
        let answer: Result<HTTPReply, ExchangeFailure>
        private(set) var sent: [(request: Data, endpoint: LaptopEndpoint, timeoutSec: Double, bound: Int)] = []
        init(_ answer: Result<HTTPReply, ExchangeFailure>) { self.answer = answer }
        func exchange(_ request: Data, to endpoint: LaptopEndpoint, timeoutSec: Double, bodyMaxBytes: Int,
                      onSent: @escaping @Sendable (Int64) -> Void) async -> Result<HTTPReply, ExchangeFailure> {
            sent.append((request, endpoint, timeoutSec, bodyMaxBytes))
            return answer
        }
    }

    private func reply(_ status: Int, _ text: String) -> Result<HTTPReply, ExchangeFailure> {
        .success(HTTPReply(status: status, headers: [], body: Data(text.utf8)))
    }

    /// The service's own name for itself, as services/api.py writes it in the health route's reply.
    func testTheServiceNameIsTheOneTheLaptopsHealthRouteWrites() throws {
        let api = try String(contentsOf: repositoryRoot.appendingPathComponent("services/api.py"), encoding: .utf8)
        XCTAssertTrue(api.contains("@health_router.get(\"\(LaptopHealth.path)\")"))
        XCTAssertTrue(api.contains("\"service\": \"\(LaptopHealth.serviceName)\","))
        XCTAssertEqual(LaptopHealth.serviceName, "albedian-observation-media-runtime")
    }

    func testTheGateLetsTheHealthRouteThroughAndNothingNearIt() throws {
        let address = try XCTUnwrap(LaptopAddress("http://192.168.1.20:5173"))
        XCTAssertEqual(CaptureUploader.permits(method: "GET", path: "/api/health", to: address)?.endpoint, address.endpoint)
        for (method, path) in [("POST", "/api/health"), ("PUT", "/api/health"), ("GET", "/api/health/"), ("GET", "/api/healthz"),
                               ("GET", "/api/health?x=1"), ("GET", "api/health"), ("GET", "//api/health"), ("GET", "/api/media/discover")] {
            XCTAssertNil(CaptureUploader.permits(method: method, path: path, to: address), "\(method) \(path)")
        }
    }

    func testTheReplyIsAlbedianOnlyWhenItNamesTheService() throws {
        XCTAssertTrue(LaptopHealth.isAlbedian(try JSONText.read(Data("{\"status\":\"ok\",\"service\":\"albedian-observation-media-runtime\",\"version\":\"0.2.0\"}".utf8))))
        for text in ["{\"status\":\"ok\"}", "{\"service\":\"another-runtime\"}", "{\"service\":1}", "[\"albedian-observation-media-runtime\"]",
                     "\"albedian-observation-media-runtime\"", "{\"service\":\"albedian-observation-media-runtime \"}"] {
            XCTAssertFalse(LaptopHealth.isAlbedian(try JSONText.read(Data(text.utf8))), text)
        }
    }

    func testTheClientAsksOnePlainGetAndTakesOnlyAlbediansAnswer() async throws {
        let address = try XCTUnwrap(LaptopAddress("http://192.168.1.20:5173"))
        let good = Stub(reply(200, "{\"status\":\"ok\",\"service\":\"albedian-observation-media-runtime\"}"))
        let answered = await LaptopHealthClient(address: address, transport: good).isAlbedian()
        XCTAssertTrue(answered)
        XCTAssertEqual(good.sent.count, 1)
        let sent = try XCTUnwrap(good.sent.first)
        XCTAssertEqual(sent.endpoint, address.endpoint)
        XCTAssertEqual(sent.timeoutSec, LaptopHealthClient.timeoutSec)
        XCTAssertEqual(sent.bound, LaptopHealthClient.replyMaxBytes)
        let text = String(decoding: sent.request, as: UTF8.self)
        XCTAssertEqual(text, "GET /api/health HTTP/1.1\r\nHost: 192.168.1.20:5173\r\nUser-Agent: \(CaptureUploader.userAgent)\r\n"
                           + "Content-Length: 0\r\nConnection: close\r\n\r\n", "nothing personal: no body, no place, no time")
        for answer in [reply(200, "{\"service\":\"another-runtime\"}"), reply(404, "{\"detail\":\"Not Found\"}"), reply(200, "not json"),
                       reply(500, "{\"service\":\"albedian-observation-media-runtime\"}"),
                       .failure(.timedOut), .failure(.closed), .failure(.notThePairedPeer), .failure(.network(posixCode: 61))] {
            let isAlbedian = await LaptopHealthClient(address: address, transport: Stub(answer)).isAlbedian()
            XCTAssertFalse(isAlbedian, "\(answer)")
        }
        XCTAssertGreaterThan(LaptopHealthClient.timeoutSec, 0)
        XCTAssertLessThanOrEqual(LaptopHealthClient.timeoutSec, 10)
    }

    /// The words the record gives, and the command is the repository's own: `pnpm field` runs scripts/field.mjs,
    /// which takes `--lan`.
    func testTheWordsAreTheRecordsAndTheCommandIsTheRepositorysOwn() throws {
        XCTAssertEqual(ConnectWords.pill, "Connect laptop")
        XCTAssertEqual(ConnectWords.start, "Start Albedian on your laptop with:")
        XCTAssertEqual(ConnectWords.command, "pnpm field --lan")
        let package = try JSONSerialization.jsonObject(with: Data(contentsOf: repositoryRoot.appendingPathComponent("package.json"))) as? [String: Any]
        XCTAssertEqual((package?["scripts"] as? [String: Any])?["field"] as? String, "node scripts/field.mjs")
        let field = try String(contentsOf: repositoryRoot.appendingPathComponent("scripts/field.mjs"), encoding: .utf8)
        XCTAssertTrue(field.contains("if (arg === '--lan') options.lan = true;"))
    }
}
