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
        XCTAssertGreaterThanOrEqual(replies.count, 6)
        XCTAssertTrue(replies.contains { $0.0 == "a reply with a star that has no Bayer designation" })
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
            ("a star with no catalog id", changed { star(&$0, "catalogSourceId", nil) }),
            ("a star with an empty catalog id", changed { star(&$0, "catalogSourceId", .string("")) }),
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
        // A negative margin keeps a mark only where a ring of that radius is wholly inside (the overlay passes the ring's)
        XCTAssertNotNil(SkyProjection.point(altDeg: 0, azDeg: 24, attitude: q, widthPt: 390, heightPt: 520, acrossFovDeg: 50, marginPt: -6.5))
        XCTAssertNil(SkyProjection.point(altDeg: 0, azDeg: 24.8, attitude: q, widthPt: 390, heightPt: 520, acrossFovDeg: 50, marginPt: -6.5))
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

    // MARK: Which marks get a name (D20, UIUX-aimnames-DECIDED-BY-LEAD.md)

    private func star(_ name: String, mag: Double, catalogSourceId: String = "HIP 1") -> SkyMark {
        var mark = SkyMark(id: "star-\(name.lowercased())", name: name, altDeg: 30, azDeg: 100, source: .catalog(release: "Hipparcos 2007"))
        mark.mag = mag
        mark.catalogSourceId = catalogSourceId
        return mark
    }

    private func at(_ mark: SkyMark, _ x: Double, _ y: Double) -> SkyOverlayNames.Placed { .init(mark: mark, x: x, y: y) }

    private func names(_ placed: [SkyOverlayNames.Placed], width: Double = 390, height: Double = 500) -> [String: Bool] {
        SkyOverlayNames.names(placed, widthPt: width, heightPt: height)
    }

    func testOnlyAStarWithANameOfItsOwnBrighterThan3Point2IsNamedAndABodyKeepsItsName() {
        let mars = SkyMark(id: "finding-predicted-mars", name: "Mars", altDeg: 30, azDeg: 100, source: .ephemeris)
        let placed = [at(star("Vega", mag: 0.03), 60, 60), at(star("Sheliak", mag: 3.52), 60, 120),
                      at(star("Edge", mag: 3.2), 60, 180), at(star("Under", mag: 3.19), 60, 240),
                      at(star("HIP 27989", mag: 0.5, catalogSourceId: "HIP 27989"), 60, 300), at(mars, 60, 360)]
        XCTAssertEqual(names(placed), ["star-vega": false, "star-under": false, "finding-predicted-mars": false])
        var noMag = star("Nomag", mag: 0)
        noMag.mag = nil
        XCTAssertEqual(names([at(noMag, 60, 60)]), [:])
    }

    func testANameGoesLeftNearTheRightEdgeAndOnlyWhereItFits() {
        // Geist 11, 0.56 em a letter, 12 pt from the ring, 3 pt of margin, 2 pt inside the sides: Alkaid's name with its
        // margin ends 51.96 pt right of its centre
        XCTAssertEqual(names([at(star("Alkaid", mag: 1.86), 336, 100)]), ["star-alkaid": false])
        XCTAssertEqual(names([at(star("Alkaid", mag: 1.86), 336.1, 100)]), ["star-alkaid": true])
        // Flipped and still off the preview: no name
        XCTAssertEqual(names([at(star("Alkaid", mag: 1.86), 30, 100)], width: 60), [:])
        // Flipped, its name fits, but the named ring (r 6.5) would cross the right edge
        XCTAssertEqual(names([at(star("Alkaid", mag: 1.86), 383.5, 100)]), ["star-alkaid": true])
        XCTAssertEqual(names([at(star("Alkaid", mag: 1.86), 383.6, 100)]), [:])
        // The named ring wholly inside, and the name's box (0.8 of its size above the centre, 0.5 below)
        XCTAssertEqual(names([at(star("Sirius", mag: -1.46), 6, 100)]), [:])
        XCTAssertEqual(names([at(star("Sirius", mag: -1.46), 6.5, 100)]), ["star-sirius": false])
        XCTAssertEqual(names([at(star("Sirius", mag: -1.46), 100, 8.7)]), [:])
        XCTAssertEqual(names([at(star("Sirius", mag: -1.46), 100, 9)]), ["star-sirius": false])
        XCTAssertEqual(names([at(star("Sirius", mag: -1.46), 100, 493.5)]), ["star-sirius": false])
        XCTAssertEqual(names([at(star("Sirius", mag: -1.46), 100, 493.6)]), [:])
    }

    func testANameRunsIntoNoRingsRoomAndNoNameGivenBeforeItTryingTheOtherSideOnce() {
        // Rigel's name at (150, 200) spans x 159 to 195.8 on the right, 104.2 to 141 on the left, y 191.2 to 205.5
        let rigel = at(star("Rigel", mag: 0.13), 150, 200)
        // Every ring keeps 9 pt about its centre: a ring just right of the name, above it and below it sends it left
        XCTAssertEqual(names([rigel, at(star("Faint", mag: 4), 204.7, 200)]), ["star-rigel": true])
        XCTAssertEqual(names([rigel, at(star("Faint", mag: 4), 204.9, 200)]), ["star-rigel": false])
        XCTAssertEqual(names([rigel, at(star("Faint", mag: 4), 180, 182.3)]), ["star-rigel": true])
        XCTAssertEqual(names([rigel, at(star("Faint", mag: 4), 180, 182.1)]), ["star-rigel": false])
        XCTAssertEqual(names([rigel, at(star("Faint", mag: 4), 180, 214.4)]), ["star-rigel": true])
        XCTAssertEqual(names([rigel, at(star("Faint", mag: 4), 180, 214.6)]), ["star-rigel": false])
        // Rings on both sides: no name, and the ring stays (the view draws every placed ring)
        let right = at(star("Faint", mag: 4), 204.7, 200)
        XCTAssertEqual(names([rigel, right, at(star("Dim", mag: 4), 95.3, 200)]), [:])
        XCTAssertEqual(names([rigel, right, at(star("Dim", mag: 4), 95.1, 200)]), ["star-rigel": true])
        // The other side off the preview: no name, whichever side it starts on
        XCTAssertEqual(names([at(star("Sirius", mag: -1.46), 20, 300), at(star("Dim", mag: 4), 50, 300)]), [:])
        XCTAssertEqual(names([at(star("Alkaid", mag: 1.86), 350, 300), at(star("Dim", mag: 4), 320, 300)]), [:])
        // Brightest first, whatever the order given: a fainter star's name that would run into Rigel's goes left, and a
        // body (no magnitude) comes after every star
        XCTAssertEqual(names([at(star("Saiph", mag: 2.09), 150, 210), rigel]), ["star-rigel": false, "star-saiph": true])
        let mars = SkyMark(id: "finding-predicted-mars", name: "Mars", altDeg: 30, azDeg: 100, source: .ephemeris)
        XCTAssertEqual(names([at(mars, 150, 210), rigel]), ["star-rigel": false, "finding-predicted-mars": true])
        // Far apart, both are named on the right
        XCTAssertEqual(names([rigel, at(star("Saiph", mag: 2.09), 150, 260)]), ["star-rigel": false, "star-saiph": false])
    }

    func testACentredMarkFrameHoldsLongSouthernBayerNamesWithoutClippingAndStaysCompactWhenUnnamed() {
        // Gamma Trianguli Australis (25 chars) at x = 335 on a 393 pt preview goes left and is kept by D20; the old 260 pt
        // EarlyMark frame extended only 130 pt from the ring centre and clipped the leading "G"
        let gammaTrA = star("Gamma Trianguli Australis", mag: 2.88, catalogSourceId: "HIP 74946")
        let kept = names([at(gammaTrA, 335, 220)], width: 393, height: 524)
        XCTAssertEqual(kept, ["star-gamma trianguli australis": true])

        let namedWidth = SkyOverlayNames.markFrameWidthPt(name: gammaTrA.name, named: true, previewWidthPt: 393)
        // Half-width from ring centre covers the whole preview width (393 pt) and 1 em per UTF-16 unit + gap + halo + margin
        XCTAssertEqual(namedWidth, 786.0, accuracy: 1e-9)
        XCTAssertGreaterThanOrEqual(namedWidth / 2, 12.0 + 25.0 * 11.0 + 3.0 + 3.0)

        // When a long title's generous 1 em span exceeds the preview width, the character span governs
        let veryLong = String(repeating: "W", count: 32)
        let expectedSide = 12.0 + 32.0 * 11.0 + 3.0 + 3.0
        XCTAssertEqual(SkyOverlayNames.markFrameWidthPt(name: veryLong, named: true, previewWidthPt: 320), 2 * expectedSide, accuracy: 1e-9)
        XCTAssertEqual(SkyOverlayNames.markFrameWidthPt(name: "Vega", named: true, previewWidthPt: -10), 2 * (12.0 + 4.0 * 11.0 + 3.0 + 3.0), accuracy: 1e-9)

        // Unnamed rings take the compact 40 pt square frame
        XCTAssertEqual(SkyOverlayNames.markFrameWidthPt(name: gammaTrA.name, named: false, previewWidthPt: 393), 40.0, accuracy: 1e-9)
        XCTAssertEqual(SkyOverlayNames.markFrameHeightPt, 40.0, accuracy: 1e-9)
    }

    func testTheReaderKeepsEachStarsMagnitudeAndCatalogIdAndNoneForABody() throws {
        let marks = try XCTUnwrap(SkyContextReader.marks(base()))
        let vegaRow = try XCTUnwrap(base()["stars"]?.arrayValue?.first { $0["id"]?.stringValue == "star-vega" })
        XCTAssertEqual(marks.first { $0.id == "star-vega" }?.mag, vegaRow["mag"]?.numberValue)
        XCTAssertEqual(marks.first { $0.id == "star-vega" }?.catalogSourceId, vegaRow["catalogSourceId"]?.stringValue)
        XCTAssertNil(marks.first { $0.source == .ephemeris }?.catalogSourceId)
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
