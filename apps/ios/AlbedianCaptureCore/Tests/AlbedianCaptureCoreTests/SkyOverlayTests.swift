import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The aim overlay (mobile-5): the request the phone sends, its reading of the laptop's reply against the shared
/// sky-context fixture (tests/fixtures/sky_context_contract.json), the projection onto the preview, and the words.
final class SkyOverlayTests: XCTestCase {
    private func fixture() throws -> JSONObject { try ContractFixture.load("sky_context_contract") }

    /// The fixture's valid replies, and its base reply with each of the fixture's own changes applied.
    private func validReplies() throws -> [(String, JSONValue)] {
        try fixture()["valid"]?.arrayValue?.compactMap { sample in
            sample["kind"]?.stringValue == "reply" ? (sample["name"]?.stringValue ?? "", sample["body"]!) : nil
        } ?? []
    }

    private func base() throws -> JSONValue { try XCTUnwrap(fixture()["bases"]?["reply"]) }

    // MARK: The request

    func testTheRequestIsTheContractsBaseRequestWithAboveHorizonOnly() throws {
        let moment = try XCTUnwrap(UTCInstant.date("2031-03-20T12:00:00Z"))
        let body = try XCTUnwrap(SkyContextRequest.body(at: moment, latDeg: 20, lonDeg: -140))
        let read = try JSONText.read(body)
        // The phone writes every instant with its milliseconds (`UTCInstant`), a form the contract's pattern takes
        let expected = try XCTUnwrap(fixture()["bases"]?["request"])
        XCTAssertEqual(read["observedAtUTC"]?.stringValue, "2031-03-20T12:00:00.000Z")
        XCTAssertEqual(read["latDeg"]?.numberValue, expected["latDeg"]?.numberValue)
        XCTAssertEqual(read["lonDeg"]?.numberValue, expected["lonDeg"]?.numberValue)
        XCTAssertEqual(read["aboveHorizonOnly"], .bool(true))
        // The request the fixture lists with both filters keeps the same keys
        let keys = Set(read.objectValue!.keys.compactMap(\.string))
        XCTAssertEqual(keys, ["observedAtUTC", "latDeg", "lonDeg", "aboveHorizonOnly"])
    }

    func testNoRequestForAPlaceThatIsNotOne() {
        let now = Date(timeIntervalSince1970: 1_900_000_000)
        for (lat, lon) in [(91.0, 0.0), (-90.5, 0.0), (0.0, 180.5), (Double.nan, 0.0), (0.0, .infinity)] {
            XCTAssertNil(SkyContextRequest.body(at: now, latDeg: lat, lonDeg: lon), "\(lat) \(lon)")
        }
        XCTAssertNotNil(SkyContextRequest.body(at: now, latDeg: -90, lonDeg: 180))
    }

    func testTheUploaderLetsTheRequestThroughOnlyAsAPostToThePairedLaptop() throws {
        let address = try XCTUnwrap(LaptopAddress("http://192.168.1.20:5173"))
        XCTAssertEqual(CaptureUploader.permits(method: "POST", path: "/api/science/sky-context", to: address),
                       ApprovedRequest(endpoint: address.endpoint, method: "POST", path: "/api/science/sky-context"))
        for (method, path) in [("GET", "/api/science/sky-context"), ("PUT", "/api/science/sky-context"), ("POST", "/api/science/sky-context/"),
                               ("POST", "/api/science/sky-context?latDeg=1"), ("POST", "/api/science/ephemeris"), ("POST", "/api/science/solve"),
                               ("POST", "api/science/sky-context"), ("POST", "/api/science/sky-context#x"), ("post", "/api/science/sky-context")] {
            XCTAssertNil(CaptureUploader.permits(method: method, path: path, to: address), "\(method) \(path)")
        }
    }

    // MARK: The reply

    func testReadsEveryValidReplyOfTheFixtureIntoPredictedMarks() throws {
        let replies = try validReplies()
        XCTAssertEqual(replies.count, 4)
        for (name, reply) in replies {
            let marks = try XCTUnwrap(SkyContextReader.marks(reply), name)
            let stars = reply["stars"]?.arrayValue ?? []
            let bodies = reply["ephemerides"]?.arrayValue ?? []
            XCTAssertEqual(marks.map(\.id), (stars + bodies).compactMap { $0["id"]?.stringValue }, name)
            XCTAssertTrue(marks.allSatisfy { $0.evidence == .predicted }, name)
        }
    }

    func testAStarIsDrawnByItsCommonNameFromItsReleaseAndABodyFromTheEphemeris() throws {
        let marks = try XCTUnwrap(SkyContextReader.marks(base()))
        let vega = try XCTUnwrap(marks.first { $0.id == "star-vega" })
        XCTAssertEqual(vega.name, "Vega")
        XCTAssertEqual(vega.source, .catalog(release: "Hipparcos 2007"))
        let vegaRow = try XCTUnwrap(base()["stars"]?.arrayValue?.first { $0["id"]?.stringValue == "star-vega" })
        XCTAssertEqual(vega.altDeg, vegaRow["altDeg"]?.numberValue)
        XCTAssertEqual(vega.azDeg, vegaRow["azDeg"]?.numberValue)
        XCTAssertEqual(marks.first { $0.id == "star-megrez" }?.source, .catalog(release: "Gaia DR3"))
        let mars = try XCTUnwrap(marks.first { $0.id == "finding-predicted-mars" })
        XCTAssertEqual(mars.name, "Mars")
        XCTAssertEqual(mars.source, .ephemeris)
    }

    func testAReplyThatClaimsAnythingButPredictedOrAnotherFormDrawsNothing() throws {
        let reply = try base()
        func changed(_ change: (inout JSONValue) -> Void) -> JSONValue {
            var copy = reply
            change(&copy)
            return copy
        }
        func star(_ value: inout JSONValue, _ key: String, _ new: JSONValue?) {
            var stars = value["stars"]!.arrayValue!
            var first = stars[0].objectValue!
            if let new { first.set(JSONString(key), new) } else { first.remove(JSONString(key)) }
            stars[0] = .object(first)
            value["stars"] = .array(stars)
        }
        func body(_ value: inout JSONValue, _ path: [String], _ new: JSONValue) {
            var bodies = value["ephemerides"]!.arrayValue!
            var first = bodies[0]
            if path.count == 1 { first[path[0]] = new } else { var inner = first[path[0]]!; inner[path[1]] = new; first[path[0]] = inner }
            bodies[0] = first
            value["ephemerides"] = .array(bodies)
        }
        let refused: [(String, JSONValue)] = [
            ("a star said observed", changed { star(&$0, "evidenceClass", .string("observed")) }),
            ("a star with no evidence class", changed { star(&$0, "evidenceClass", nil) }),
            ("a star with no release", changed { star(&$0, "releaseVersion", nil) }),
            ("a star with no name", changed { star(&$0, "commonName", nil) }),
            ("a star with an empty name", changed { star(&$0, "commonName", .string("")) }),
            ("a star's altitude past 90", changed { star(&$0, "altDeg", .number(JSONNumber(91))) }),
            ("a star's azimuth as text", changed { star(&$0, "azDeg", .string("10")) }),
            ("a star with no magnitude", changed { star(&$0, "mag", nil) }),
            ("a body said catalog matched", changed { body(&$0, ["evidenceClass"], .string("catalog_matched")) }),
            ("a body's evidence said observed", changed { body(&$0, ["evidence", "evidenceClass"], .string("observed")) }),
            ("another format", changed { $0["format"] = .string("albedian.sky") }),
            ("format version 2", changed { $0["formatVersion"] = .number(JSONNumber(2)) }),
            ("refraction applied", changed { var c = $0["convention"]!; c["altitude"] = .string("apparent_refracted"); $0["convention"] = c }),
            ("an azimuth from south", changed { var c = $0["convention"]!; c["azimuth"] = .string("south_through_west"); $0["convention"] = c }),
            ("no convention", changed { var o = $0.objectValue!; o.remove("convention"); $0 = .object(o) }),
            ("no stars", changed { var o = $0.objectValue!; o.remove("stars"); $0 = .object(o) }),
        ]
        for (name, value) in refused { XCTAssertNil(SkyContextReader.marks(value), name) }
        XCTAssertNil(SkyContextReader.marks(Data("{not json".utf8)))
        XCTAssertNotNil(SkyContextReader.marks(JSONText.data(reply)))
    }

    // MARK: The projection

    /// The attitude of a phone held upright in portrait with its back camera toward `azDeg` and its top up: the device's
    /// minus Z along that azimuth on the horizon, its Y up. Built from the frame's axes, so it states the convention the
    /// projection takes (Core Motion's attitude turns device vectors into the reference frame) [open: iPhone check 2].
    private func upright(towardAzDeg azDeg: Double, pitchUpDeg: Double = 0) -> PointingRecord.Quaternion {
        SkyProjection.uprightAttitude(towardAzDeg: azDeg, pitchUpDeg: pitchUpDeg)
    }

    func testTheDirectionTheCameraLooksFallsInThePreviewsCentre() throws {
        for az in [0.0, 45, 90, 180, 270, 359] {
            let at = try XCTUnwrap(SkyProjection.point(altDeg: 0, azDeg: az, attitude: upright(towardAzDeg: az), widthPt: 390, heightPt: 520, acrossFovDeg: 50))
            XCTAssertEqual(at.x, 195, accuracy: 1e-6, "\(az)")
            XCTAssertEqual(at.y, 260, accuracy: 1e-6, "\(az)")
        }
        let tilted = try XCTUnwrap(SkyProjection.point(altDeg: 30, azDeg: 120, attitude: upright(towardAzDeg: 120, pitchUpDeg: 30),
                                                       widthPt: 390, heightPt: 520, acrossFovDeg: 50))
        XCTAssertEqual(tilted.x, 195, accuracy: 1e-6)
        XCTAssertEqual(tilted.y, 260, accuracy: 1e-6)
    }

    func testEastOfTheViewIsRightUpIsUpAndTheEdgeIsHalfTheFieldOfView() throws {
        let q = upright(towardAzDeg: 0)
        let right = try XCTUnwrap(SkyProjection.point(altDeg: 0, azDeg: 25, attitude: q, widthPt: 390, heightPt: 520, acrossFovDeg: 50))
        XCTAssertEqual(right.x, 390, accuracy: 1e-6)
        XCTAssertEqual(right.y, 260, accuracy: 1e-6)
        let left = try XCTUnwrap(SkyProjection.point(altDeg: 0, azDeg: 345, attitude: q, widthPt: 390, heightPt: 520, acrossFovDeg: 50))
        XCTAssertEqual(left.x, 195 - 195 * tan(15 * Double.pi / 180) / tan(25 * Double.pi / 180), accuracy: 1e-6)
        let up = try XCTUnwrap(SkyProjection.point(altDeg: 10, azDeg: 0, attitude: q, widthPt: 390, heightPt: 520, acrossFovDeg: 50))
        XCTAssertEqual(up.x, 195, accuracy: 1e-6)
        XCTAssertEqual(up.y, 260 - 195 * tan(10 * Double.pi / 180) / tan(25 * Double.pi / 180), accuracy: 1e-6)
    }

    func testNothingBehindTheLensOrOffThePreviewIsDrawn() {
        let q = upright(towardAzDeg: 0)
        XCTAssertNil(SkyProjection.point(altDeg: 0, azDeg: 180, attitude: q, widthPt: 390, heightPt: 520, acrossFovDeg: 50))
        // Just off the axis behind the lens, where dividing by a negative depth would put it back on the preview
        XCTAssertNil(SkyProjection.point(altDeg: 3, azDeg: 178, attitude: q, widthPt: 390, heightPt: 520, acrossFovDeg: 50))
        XCTAssertNil(SkyProjection.point(altDeg: -2, azDeg: 181, attitude: q, widthPt: 390, heightPt: 520, acrossFovDeg: 50))
        XCTAssertNil(SkyProjection.point(altDeg: 0, azDeg: 90, attitude: q, widthPt: 390, heightPt: 520, acrossFovDeg: 50))
        XCTAssertNil(SkyProjection.point(altDeg: 0, azDeg: 26, attitude: q, widthPt: 390, heightPt: 520, acrossFovDeg: 50))
        XCTAssertNotNil(SkyProjection.point(altDeg: 0, azDeg: 26, attitude: q, widthPt: 390, heightPt: 520, acrossFovDeg: 50, marginPt: 12))
        XCTAssertNil(SkyProjection.point(altDeg: 0, azDeg: 0, attitude: PointingRecord.Quaternion(x: 0, y: 0, z: 0, w: 0), widthPt: 390,
                                         heightPt: 520, acrossFovDeg: 50))
        XCTAssertNil(SkyProjection.point(altDeg: 0, azDeg: 0, attitude: q, widthPt: 390, heightPt: 520, acrossFovDeg: 0))
        XCTAssertNil(SkyProjection.point(altDeg: .nan, azDeg: 0, attitude: q, widthPt: 390, heightPt: 520, acrossFovDeg: 50))
    }

    func testAnAttitudeNotOfUnitLengthIsReadAsItsDirection() throws {
        let q = upright(towardAzDeg: 70, pitchUpDeg: 20)
        let long = PointingRecord.Quaternion(x: q.x * 3, y: q.y * 3, z: q.z * 3, w: q.w * 3)
        let a = try XCTUnwrap(SkyProjection.point(altDeg: 25, azDeg: 75, attitude: q, widthPt: 390, heightPt: 520, acrossFovDeg: 50))
        let b = try XCTUnwrap(SkyProjection.point(altDeg: 25, azDeg: 75, attitude: long, widthPt: 390, heightPt: 520, acrossFovDeg: 50))
        XCTAssertEqual(a.x, b.x, accuracy: 1e-9)
        XCTAssertEqual(a.y, b.y, accuracy: 1e-9)
    }

    func testTheStarsTurnWithThePhoneByTheirAngularDistance() throws {
        // Two stars a known angle apart on the horizon sit that angle apart through the lens, wherever the phone points
        for az in stride(from: 0.0, to: 360, by: 37) {
            let q = upright(towardAzDeg: az)
            let a = try XCTUnwrap(SkyProjection.point(altDeg: 0, azDeg: az - 10, attitude: q, widthPt: 390, heightPt: 520, acrossFovDeg: 50))
            let b = try XCTUnwrap(SkyProjection.point(altDeg: 0, azDeg: az + 10, attitude: q, widthPt: 390, heightPt: 520, acrossFovDeg: 50))
            let focal = 195 / tan(25 * Double.pi / 180)
            XCTAssertEqual(b.x - a.x, 2 * focal * tan(10 * Double.pi / 180), accuracy: 1e-6, "\(az)")
        }
    }

    func testThePortraitWidthsFieldOfViewHasTheLongSidesFocalLength() throws {
        let across = try XCTUnwrap(SkyProjection.acrossPortraitWidthDeg(longSideFovDeg: 69.4))
        XCTAssertEqual(tan(across * .pi / 360), tan(69.4 * .pi / 360) * 0.75, accuracy: 1e-12)
        XCTAssertNil(SkyProjection.acrossPortraitWidthDeg(longSideFovDeg: 0))
        XCTAssertNil(SkyProjection.acrossPortraitWidthDeg(longSideFovDeg: 180))
    }

    // MARK: The words

    func testTheLineNamesEachSourceDrawnAndTheHeadingAccuracy() {
        let vega = SkyMark(id: "star-vega", name: "Vega", altDeg: 34, azDeg: 60, source: .catalog(release: "Hipparcos 2007"))
        let megrez = SkyMark(id: "star-megrez", name: "Megrez", altDeg: 45, azDeg: 10, source: .catalog(release: "Gaia DR3"))
        let mars = SkyMark(id: "finding-predicted-mars", name: "Mars", altDeg: 51, azDeg: 120, source: .ephemeris)
        XCTAssertEqual(SkyOverlayWords.sourceLine([vega], headingAccuracyDeg: 11.2), "Hipparcos 2007 · heading ±12°")
        XCTAssertEqual(SkyOverlayWords.sourceLine([vega, vega], headingAccuracyDeg: 12), "Hipparcos 2007 · heading ±12°")
        XCTAssertEqual(SkyOverlayWords.sourceLine([mars, megrez, vega], headingAccuracyDeg: 3.01),
                       "Gaia DR3 · Hipparcos 2007 · ERFA ephemeris · heading ±4°")
        XCTAssertNil(SkyOverlayWords.sourceLine([], headingAccuracyDeg: 5))
        XCTAssertNil(SkyOverlayWords.sourceLine([vega], headingAccuracyDeg: .nan))
        XCTAssertEqual(SkyOverlayWords.tag, "Predicted")
    }

    // MARK: Which marks get a name (INTERIM, the web's rule)

    private func star(_ name: String, mag: Double) -> SkyMark {
        var mark = SkyMark(id: "star-\(name.lowercased())", name: name, altDeg: 30, azDeg: 100, source: .catalog(release: "Hipparcos 2007"))
        mark.mag = mag
        return mark
    }

    func testOnlyAStarBrighterThan3Point2IsNamedAndEveryBodyKeepsItsName() {
        let mars = SkyMark(id: "finding-predicted-mars", name: "Mars", altDeg: 30, azDeg: 100, source: .ephemeris)
        let placed = [SkyOverlayNames.Placed(mark: star("Vega", mag: 0.03), x: 60, y: 60),
                      SkyOverlayNames.Placed(mark: star("Sheliak", mag: 3.52), x: 60, y: 160),
                      SkyOverlayNames.Placed(mark: star("Edge", mag: 3.2), x: 60, y: 260),
                      SkyOverlayNames.Placed(mark: star("Under", mag: 3.19), x: 60, y: 360),
                      SkyOverlayNames.Placed(mark: mars, x: 60, y: 460)]
        XCTAssertEqual(SkyOverlayNames.names(placed, widthPt: 390), ["star-vega": false, "star-under": false, "finding-predicted-mars": false])
        var noMag = star("Nomag", mag: 0)
        noMag.mag = nil
        XCTAssertEqual(SkyOverlayNames.names([SkyOverlayNames.Placed(mark: noMag, x: 60, y: 60)], widthPt: 390), [:])
    }

    func testANameGoesLeftNearTheRightEdgeAndTriesTheOtherSideOnceWhenItRunsIntoAMark() {
        // Near the right edge: 12 pt type, 0.56 em a letter, 11 pt from the ring, 3 pt of margin
        let edge = SkyOverlayNames.Placed(mark: star("Alkaid", mag: 1.86), x: 346.9, y: 100)
        XCTAssertEqual(SkyOverlayNames.names([edge], widthPt: 390), ["star-alkaid": true])
        // A mark just right of a name pushes it to the left of its ring
        let named = SkyOverlayNames.Placed(mark: star("Rigel", mag: 0.13), x: 150, y: 200)
        let crowd = SkyOverlayNames.Placed(mark: star("Faint", mag: 4), x: 185, y: 200)
        XCTAssertEqual(SkyOverlayNames.names([named, crowd], widthPt: 390), ["star-rigel": true])
        // Marks on both sides: no name, and the ring stays (the view draws every placed ring)
        let other = SkyOverlayNames.Placed(mark: star("Dim", mag: 4), x: 115, y: 200)
        XCTAssertEqual(SkyOverlayNames.names([named, crowd, other], widthPt: 390), [:])
        // The left side past the preview's left edge: no name
        let atLeft = SkyOverlayNames.Placed(mark: star("Sirius", mag: -1.46), x: 20, y: 300)
        let right = SkyOverlayNames.Placed(mark: star("Dim2", mag: 4), x: 50, y: 300)
        XCTAssertEqual(SkyOverlayNames.names([atLeft, right], widthPt: 390), [:])
        // The room a mark keeps (14 pt about its centre) and a name's height (0.8 of its size above, 0.5 below): a mark
        // 16 pt above the name's line still pushes it aside, and one 16 pt below too
        let above = SkyOverlayNames.Placed(mark: star("Above", mag: 4), x: 180, y: 184)
        XCTAssertEqual(SkyOverlayNames.names([named, above], widthPt: 390), ["star-rigel": true])
        let below = SkyOverlayNames.Placed(mark: star("Below", mag: 4), x: 180, y: 216)
        XCTAssertEqual(SkyOverlayNames.names([named, below], widthPt: 390), ["star-rigel": true])
        let clear = SkyOverlayNames.Placed(mark: star("Clear", mag: 4), x: 180, y: 222)
        XCTAssertEqual(SkyOverlayNames.names([named, clear], widthPt: 390), ["star-rigel": false])
        // A mark far away runs into nothing
        let far = SkyOverlayNames.Placed(mark: star("Far", mag: 4), x: 150, y: 260)
        XCTAssertEqual(SkyOverlayNames.names([named, far], widthPt: 390), ["star-rigel": false])
    }

    func testTheReaderKeepsEachStarsMagnitudeAndNoneForABody() throws {
        let marks = try XCTUnwrap(SkyContextReader.marks(base()))
        let vegaRow = try XCTUnwrap(base()["stars"]?.arrayValue?.first { $0["id"]?.stringValue == "star-vega" })
        XCTAssertEqual(marks.first { $0.id == "star-vega" }?.mag, vegaRow["mag"]?.numberValue)
        XCTAssertNil(marks.first { $0.source == .ephemeris }?.mag)
        XCTAssertTrue(marks.filter { if case .catalog = $0.source { true } else { false } }.allSatisfy { $0.mag != nil })
    }

    // MARK: The client

    final class Stub: LaptopTransport, @unchecked Sendable {
        let answer: Result<HTTPReply, ExchangeFailure>
        private(set) var sent: [(Data, LaptopEndpoint, Int)] = []
        init(_ answer: Result<HTTPReply, ExchangeFailure>) { self.answer = answer }
        func exchange(_ request: Data, to endpoint: LaptopEndpoint, timeoutSec: Double, bodyMaxBytes: Int,
                      onSent: @escaping @Sendable (Int64) -> Void) async -> Result<HTTPReply, ExchangeFailure> {
            sent.append((request, endpoint, bodyMaxBytes))
            return answer
        }
    }

    func testTheClientPostsTheBodyToThePairedLaptopAndReadsItsMarks() async throws {
        let address = try XCTUnwrap(LaptopAddress("http://192.168.1.20:5173"))
        let reply = JSONText.data(try base())
        let stub = Stub(.success(HTTPReply(status: 200, headers: [], body: reply)))
        let moment = try XCTUnwrap(UTCInstant.date("2031-03-20T12:00:00Z"))
        let asked = await SkyContextClient(address: address, transport: stub).marks(at: moment, latDeg: 20, lonDeg: -140)
        let marks = try XCTUnwrap(asked)
        XCTAssertEqual(marks, SkyContextReader.marks(reply))
        XCTAssertEqual(stub.sent.count, 1)
        let (request, endpoint, bound) = stub.sent[0]
        XCTAssertEqual(endpoint, address.endpoint)
        XCTAssertEqual(bound, SkyContextClient.replyMaxBytes)
        let text = String(decoding: request, as: UTF8.self)
        XCTAssertTrue(text.hasPrefix("POST /api/science/sky-context HTTP/1.1\r\n"), text)
        XCTAssertTrue(text.contains("\r\nContent-Type: application/json\r\n"), text)
        XCTAssertTrue(text.hasSuffix("\r\n\r\n" + String(decoding: SkyContextRequest.body(at: moment, latDeg: 20, lonDeg: -140)!, as: UTF8.self)))
    }

    func testTheClientGivesNoMarksForARefusalAFailureOrAReplyNotInTheContractsForm() async throws {
        let address = try XCTUnwrap(LaptopAddress("http://192.168.1.20:5173"))
        let now = Date(timeIntervalSince1970: 1_900_000_000)
        let answers: [Result<HTTPReply, ExchangeFailure>] = [
            .success(HTTPReply(status: 422, headers: [], body: Data(#"{"detail":"latDeg must be a number from -90 to 90."}"#.utf8))),
            .success(HTTPReply(status: 302, headers: [], body: JSONText.data(try base()))),
            .success(HTTPReply(status: 200, headers: [], body: Data(#"{"stars":[]}"#.utf8))),
            .failure(.timedOut), .failure(.closed), .failure(.notThePairedPeer), .failure(.network(posixCode: 61)),
        ]
        for answer in answers {
            let marks = await SkyContextClient(address: address, transport: Stub(answer)).marks(at: now, latDeg: 20, lonDeg: -140)
            XCTAssertNil(marks, "\(answer)")
        }
        let unsent = Stub(.failure(.timedOut))
        let none = await SkyContextClient(address: address, transport: unsent).marks(at: now, latDeg: 95, lonDeg: 0)
        XCTAssertNil(none)
        XCTAssertTrue(unsent.sent.isEmpty)
    }
}
