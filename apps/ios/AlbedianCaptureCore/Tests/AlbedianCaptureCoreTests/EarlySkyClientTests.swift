import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// lockon-m5c: the phone asks for the job's predicted sky (platform-10b's `GET /api/ingest/jobs/{id}/predicted-sky`)
/// once the running job's record says it published one (`predictedSkyAtUTC`, platform-10a), with the stored frame its
/// places are in; through the one request gate, against a laptop on 127.0.0.1 each test starts.
final class EarlySkyClientTests: XCTestCase {
    private let sha = String(repeating: "ab", count: 32)
    private var jobId: String { SolveClientTests.jobId(sha) }

    func testTheGateLetsThroughOnlyTheJobsPredictedSky() throws {
        let address = try XCTUnwrap(LaptopAddress("http://192.168.1.20:5173"))
        XCTAssertNotNil(CaptureUploader.permits(method: "GET", path: "/api/ingest/jobs/\(jobId)/predicted-sky", to: address))
        for (method, path) in [("POST", "/api/ingest/jobs/\(jobId)/predicted-sky"), ("GET", "/api/ingest/jobs/\(jobId)/predicted-sky/x"),
                               ("GET", "/api/ingest/jobs/not-a-job/predicted-sky"), ("GET", "/api/ingest/jobs/\(jobId)/predicted"),
                               ("GET", "/api/ingest/\(sha)/predicted-sky")] {
            XCTAssertNil(CaptureUploader.permits(method: method, path: path, to: address), "\(method) \(path)")
        }
    }

    func testTheRunningJobSaysWhenItPublishedThePredictedSky() throws {
        let record = SolveClientTests.job(sha, "running", extra: ",\"predictedSkyAtUTC\":\"2031-05-02T19:20:05Z\"")
        let job = try SolveJobReader.read(JSONText.read(Data(record.utf8))).get()
        XCTAssertEqual(job.predictedSkyAtUTC, "2031-05-02T19:20:05.000Z")
        XCTAssertNil(try SolveJobReader.read(JSONText.read(Data(SolveClientTests.job(sha, "running").utf8))).get().predictedSkyAtUTC)
    }

    /// The fixture's approximate solution with a pose from the phone's attitude and one bright star at its WCS centre.
    private func predictedSky() throws -> String {
        let valid = try XCTUnwrap(ContractFixture.load("observation_solution_contract")["valid"]?.objectValue)
        var sample = try XCTUnwrap(valid.members.first { $0.value["status"]?.stringValue == "approximate" && $0.value["poseEstimate"] != nil
            && $0.value["wcs"] != nil }?.value)
        let wcs = try XCTUnwrap(sample["wcs"])
        let star: JSONValue = .object(JSONObject([
            "id": .string("finding-hip-24436"), "evidenceClass": .string("predicted"), "title": .string("Rigel"),
            "skyPosition": .object(JSONObject(["raDeg": try XCTUnwrap(wcs["crvalRaDeg"]), "decDeg": try XCTUnwrap(wcs["crvalDecDeg"])])),
            "catalogRef": .object(JSONObject(["objectType": .string("star"), "catalogSourceId": .string("HIP 24436"),
                                              "apparentMagnitude": .number(JSONNumber(0.13)!)])),
        ]))
        sample["findings"] = .array([star])
        sample["sourceSha256"] = .string(JSONString(sha))
        sample["poseEstimate"]?["method"] = .string("device_attitude")
        sample["poseEstimate"]?["beaconCentroidUncertaintyDeg"] = nil
        return String(decoding: JSONText.data(sample), as: UTF8.self)
    }

    private func laptop(skyStatus: Int, sky: String) -> LoopbackHTTPServer.Responder {
        let receipt = "{\"sourceAsset\":{\"sha256\":\"\(sha)\",\"width\":800,\"height\":600},\"hints\":{},\"alreadyStored\":true,\"solveJobId\":\"\(jobId)\"}"
        let path = "/api/ingest/jobs/\(jobId)/predicted-sky"
        let sha = sha
        return { request in
            switch (request.method, request.path) {
            case ("GET", "/api/ingest/\(sha)"): return .json(200, receipt)
            case ("GET", path): return .json(skyStatus, sky)
            default: return .json(404, "{\"detail\":\"Not Found\"}")
            }
        }
    }

    func testItReadsTheServedSkyAndTheFrameItsPlacesAreIn() async throws {
        let server = try LoopbackHTTPServer(responder: laptop(skyStatus: 200, sky: try predictedSky()))
        defer { server.stop() }
        let client = SolveClient(address: try XCTUnwrap(LaptopAddress(server.baseURL)), readTimeoutSec: 5)
        let early = try await XCTUnwrapAsync(await client.earlySky(jobId: jobId, sha256: sha))
        XCTAssertEqual(early.frame, StoredFrame(widthPx: 800, heightPx: 600, orientation: .upright))
        XCTAssertEqual(early.sky.source, .pointing)
        XCTAssertEqual(early.sky.targets.map(\.catalogSourceId), ["HIP 24436"])
        XCTAssertTrue(server.requests.allSatisfy { $0.method == "GET" && $0.body.isEmpty })
    }

    func testNothingWhenTheSkyIsNotServedOrNotItsForm() async throws {
        for (status, body) in [(404, "{\"detail\":\"no predicted sky is served for that job\"}"), (200, "{\"status\":\"calibrated\"}"), (200, "not json")] {
            let server = try LoopbackHTTPServer(responder: laptop(skyStatus: status, sky: body))
            let client = SolveClient(address: try XCTUnwrap(LaptopAddress(server.baseURL)), readTimeoutSec: 5)
            let early = await client.earlySky(jobId: jobId, sha256: sha)
            server.stop()
            XCTAssertNil(early, "\(status) \(body)")
        }
    }

    private func XCTUnwrapAsync<T>(_ value: T?, file: StaticString = #filePath, line: UInt = #line) async throws -> T {
        try XCTUnwrap(value, file: file, line: line)
    }
}
