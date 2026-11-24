import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// mobile-pointing-fuzz-1: seeded fuzzing at the edges of the pointing path, each case judged by an oracle written apart
/// from the code under test. The pose's own WCS (`PoseWCS`) at the poles, across right ascension 0 and 360, behind the
/// tangent plane and in its reader's ranges; the attitude quaternion at its unit tolerance and under its sign (q and -q
/// are one attitude); the uploader's states and requests under every answer a laptop can give, never sending a request
/// twice; and the upload coordinator under starts, resets and answers in any order. The generator is seeded
/// (`propertySeed()`, ALBEDIAN_SWIFT_SEED). Every place `PoseWCS` gives is `predicted`; none of this is a sky claim.
///
/// Width (the lead's rule of 24 November, 12:41: at most two CPU-bound threads): every case runs after the one before
/// it, on the test's own thread, with no task group or concurrent loop. The uploader's cases add one bounded task at a
/// time (`bounded`), and the coordinator's cases are waited on from the test itself, so no poller is left behind when a
/// wait runs out: a poller whose sleep throws once it is cancelled would spin on a core for the rest of the run.
final class PointingFuzzTests: XCTestCase {
    private static let radian = Double.pi / 180

    // MARK: The pose's own WCS

    /// A unit vector toward a right ascension and declination in degrees.
    private static func direction(_ raDeg: Double, _ decDeg: Double) -> SIMD3<Double> {
        let ra = raDeg * radian
        let dec = decDeg * radian
        return SIMD3(cos(dec) * cos(ra), cos(dec) * sin(ra), sin(dec))
    }

    /// The gnomonic place by vectors (the direction's parts along the centre and along the centre's east and north),
    /// then the roll and the radial distortion in polar form: the model `PoseWCS.pixel` holds, written apart from its
    /// spherical trigonometry. Gives the cosine of the distance from the centre too, for the cases at the cut.
    private static func oracle(_ m: PoseWCS, raDeg: Double, decDeg: Double) -> (cosC: Double, at: (x: Double, y: Double)?) {
        let point = direction(raDeg, decDeg)
        let ra0 = m.crvalRaDeg * radian
        let dec0 = m.crvalDecDeg * radian
        let east = SIMD3(-sin(ra0), cos(ra0), 0)
        let north = SIMD3(-sin(dec0) * cos(ra0), -sin(dec0) * sin(ra0), cos(dec0))
        let cosC = (point * direction(m.crvalRaDeg, m.crvalDecDeg)).sum()
        guard cosC > 1e-6 else { return (cosC, nil) }
        let xi = Double(m.parity) * (point * east).sum() / cosC
        let eta = (point * north).sum() / cosC
        let r = (xi * xi + eta * eta).squareRoot()
        let angle = atan2(eta, xi) + m.rotationDeg * radian
        let bent = r * (1 + m.k1 * r * r + m.k2 * r * r * r * r) / radian / m.scaleDegPerPx
        return (cosC, (m.crpixX + bent * cos(angle), m.crpixY - bent * sin(angle)))
    }

    private static func drawModel<G: RandomNumberGenerator>(_ g: inout G) -> PoseWCS {
        let ras: [Double] = [0, 360, Double(360).nextDown, .leastNonzeroMagnitude, 1e-9, 359.999_999, 180,
                             g.double(in: 0...360), g.double(in: 0...360)]
        let decs: [Double] = [90, -90, Double(90).nextDown, -89.999_999, 89.99, 0, g.double(in: -90...90), g.double(in: -90...90)]
        let k1 = g.bool() ? 0 : g.double(in: -0.25...0.25)
        let k2 = g.bool() ? 0 : g.double(in: -0.25...0.25)
        return PoseWCS(crvalRaDeg: g.pick(ras), crvalDecDeg: g.pick(decs), crpixX: g.double(in: -10...4100),
                       crpixY: g.double(in: -10...4100), scaleDegPerPx: pow(10, g.double(in: -4 ... -1)),
                       rotationDeg: g.double(in: -720...720), k1: k1, k2: k2, parity: g.bool() ? 1 : -1)
    }

    /// A sky point: anywhere on the sphere, near the centre (its right ascension often past 0 or 360), or on an edge.
    private static func drawPoint<G: RandomNumberGenerator>(_ g: inout G, near m: PoseWCS) -> (ra: Double, dec: Double) {
        switch g.int(in: 0...9) {
        case 0...3:
            return (g.double(in: 0...360), asin(g.double(in: -1...1)) / radian)
        case 4...7:
            let dec = min(90, max(-90, m.crvalDecDeg + g.double(in: -30...30)))
            return (m.crvalRaDeg + g.double(in: -30...30) / max(cos(m.crvalDecDeg * radian), 0.05), dec)
        default:
            let ras: [Double] = [0, 360, -0.0, Double(360).nextDown, 1e-12, -1e-12, m.crvalRaDeg + 180, m.crvalRaDeg - 360,
                                 m.crvalRaDeg + 720, g.double(in: -1080...1080)]
            let decs: [Double] = [90, -90, 0, 89.999_999, -89.999_999, m.crvalDecDeg, -m.crvalDecDeg]
            return (g.pick(ras), g.pick(decs))
        }
    }

    private static func with(_ m: PoseWCS, crvalRaDeg: Double? = nil, parity: Int? = nil) -> PoseWCS {
        PoseWCS(crvalRaDeg: crvalRaDeg ?? m.crvalRaDeg, crvalDecDeg: m.crvalDecDeg, crpixX: m.crpixX, crpixY: m.crpixY,
                scaleDegPerPx: m.scaleDegPerPx, rotationDeg: m.rotationDeg, k1: m.k1, k2: m.k2, parity: parity ?? m.parity)
    }

    /// Two places within a millionth of a pixel and a billionth of their distance from the reference pixel, or both none.
    private func assertSamePlace(_ a: (x: Double, y: Double)?, _ b: (x: Double, y: Double)?, from m: PoseWCS, relative: Double = 1e-9,
                                 _ context: @autoclosure () -> String, line: UInt = #line) {
        XCTAssertEqual(a == nil, b == nil, context(), line: line)
        guard let a, let b else { return }
        let tolerance = 1e-6 + relative * (abs(b.x - m.crpixX) + abs(b.y - m.crpixY))
        XCTAssertEqual(a.x, b.x, accuracy: tolerance, context(), line: line)
        XCTAssertEqual(a.y, b.y, accuracy: tolerance, context(), line: line)
    }

    /// Every sky point lands where the vector form puts it, or has no place in both. The centre lands on the reference
    /// pixel; a direction written 360 or 720 degrees on or back lands where it does; the whole sky and the centre turned
    /// together by any angle (across 0 and 360) land where they did; and the other parity is the mirror image across
    /// the centre's meridian.
    func testThePoseWCSAgreesWithAVectorOracleAtThePolesAcrossZeroAndBehindThePlane() throws {
        let seed = propertySeed()
        var g = SeededGenerator(seed: seed)
        var reached: [String: Int] = [:]
        for index in 0..<4000 {
            let m = Self.drawModel(&g)
            let (ra, dec) = Self.drawPoint(&g, near: m)
            let context = "seed \(seed) case \(index): \(m) at \(ra) \(dec)"
            let centre = try XCTUnwrap(m.pixel(raDeg: m.crvalRaDeg, decDeg: m.crvalDecDeg), context)
            XCTAssertEqual(centre.x, m.crpixX, accuracy: 1e-9, context)
            XCTAssertEqual(centre.y, m.crpixY, accuracy: 1e-9, context)
            let (cosC, want) = Self.oracle(m, raDeg: ra, decDeg: dec)
            // Within a trillionth of the cut either form may round to either side
            guard abs(cosC - 1e-6) > 1e-12 else { reached["at the cut", default: 0] += 1; continue }
            let got = m.pixel(raDeg: ra, decDeg: dec)
            XCTAssertEqual(got == nil, want == nil, context)
            if got == nil { reached["behind", default: 0] += 1 }
            if abs(m.crvalDecDeg) == 90 { reached["centre on a pole", default: 0] += 1 }
            if abs(dec) == 90, got != nil { reached["point on a pole", default: 0] += 1 }
            if ra < 0 || ra > 360 { reached["written past 0 or 360", default: 0] += 1 }
            guard cosC > 1e-3 else { continue }
            reached["placed", default: 0] += 1
            assertSamePlace(got, want, from: m, context)
            for turns in [-2.0, -1, 1, 2] {
                assertSamePlace(m.pixel(raDeg: ra + 360 * turns, decDeg: dec), got, from: m, relative: 1e-8, "\(context), \(turns) turns")
            }
            let shift = g.pick([360, -360, g.double(in: -400...400)])
            assertSamePlace(Self.with(m, crvalRaDeg: m.crvalRaDeg + shift).pixel(raDeg: ra + shift, decDeg: dec), got, from: m,
                            relative: 1e-8, "\(context), all turned by \(shift)")
            assertSamePlace(Self.with(m, parity: -m.parity).pixel(raDeg: 2 * m.crvalRaDeg - ra, decDeg: dec), got, from: m,
                            relative: 1e-8, "\(context), mirrored")
        }
        for outcome in ["placed", "behind", "centre on a pole", "point on a pole", "written past 0 or 360"] {
            XCTAssertGreaterThan(reached[outcome, default: 0], 0, "seed \(seed): no \(outcome) in \(reached)")
        }
    }

    /// With the centre on a pole, the pole itself lands on the reference pixel at any right ascension, a ring of equal
    /// declination lands at one distance from it (the gnomonic radius with its distortion), and the other pole has no
    /// place. No number is nil or not finite for a direction in front of the plane.
    func testAtAPoleTheRingsAreCirclesAndThePoleIsTheReferencePixel() {
        let seed = propertySeed()
        var g = SeededGenerator(seed: seed)
        for index in 0..<1500 {
            var m = Self.drawModel(&g)
            let pole: Double = g.bool() ? 90 : -90
            m = PoseWCS(crvalRaDeg: m.crvalRaDeg, crvalDecDeg: pole, crpixX: m.crpixX, crpixY: m.crpixY, scaleDegPerPx: m.scaleDegPerPx,
                        rotationDeg: m.rotationDeg, k1: m.k1, k2: m.k2, parity: m.parity)
            let ra = g.double(in: -720...720)
            let context = "seed \(seed) case \(index): \(m) at \(ra)"
            if let at = m.pixel(raDeg: ra, decDeg: pole) {
                XCTAssertEqual(at.x, m.crpixX, accuracy: 1e-6, context)
                XCTAssertEqual(at.y, m.crpixY, accuracy: 1e-6, context)
            } else { XCTFail("the pole has no place: \(context)") }
            XCTAssertNil(m.pixel(raDeg: ra, decDeg: -pole), context)
            let off = g.pick([g.double(in: 1e-9...1e-3), g.double(in: 0...60), g.double(in: 60...89)])
            let t = tan(off * Self.radian)
            let radius = t * (1 + m.k1 * t * t + m.k2 * t * t * t * t) / Self.radian / m.scaleDegPerPx
            let ring = (pole > 0 ? 90 - off : -90 + off)
            guard let at = m.pixel(raDeg: ra, decDeg: ring) else { XCTFail("no place \(off) from the pole: \(context)"); continue }
            XCTAssertTrue(at.x.isFinite && at.y.isFinite, context)
            XCTAssertEqual(hypot(at.x - m.crpixX, at.y - m.crpixY), abs(radius), accuracy: 1e-6 + 1e-9 * abs(radius), "\(context) off \(off)")
        }
    }

    /// No number at all is no place.
    func testANonFiniteDirectionHasNoPlace() {
        let m = PoseWCS(crvalRaDeg: 0, crvalDecDeg: 90, crpixX: 1000, crpixY: 750, scaleDegPerPx: 0.02, rotationDeg: 10, k1: 0.1,
                        k2: -0.1, parity: -1)
        for (ra, dec) in [(Double.nan, 89.0), (.infinity, 89), (-.infinity, 89), (0, .nan), (0, .infinity), (0, -.infinity)] {
            XCTAssertNil(m.pixel(raDeg: ra, decDeg: dec), "\(ra) \(dec)")
        }
    }

    /// The reader takes a WCS exactly when every number is in the contract's range (`WCSCalibrationModelSchema`,
    /// packages/contracts/src/astronomy.ts: crval in [0, 360] and [-90, 90], crpix and roll finite, a positive finite
    /// scale, each distortion in [-0.25, 0.25], parity 1 or -1, each field of view in (0, 175]), every edge included
    /// or left out as the schema writes it, and keeps each number as given.
    func testTheWCSReaderTakesExactlyTheContractsRanges() throws {
        let solutions = try XCTUnwrap(ContractFixture.load("observation_solution_contract")["valid"]?.objectValue)
        let base = try XCTUnwrap(solutions.members.lazy.compactMap { $0.value["wcs"] }.first)
        let seed = propertySeed()
        var g = SeededGenerator(seed: seed)
        let notANumber: [JSONValue?] = [nil, .null, .string("1"), .bool(true), literal("1e400"), literal("-1e400"), .array([]),
                                        .object(JSONObject())]
        func n(_ x: Double) -> JSONValue? { .double(x) }
        func anyFinite() -> [JSONValue?] { [n(g.double(in: -1e6...1e6)), literal("-0"), n(1e300), n(-1e300), .int(0)] }
        let range: (Double, Double) -> [JSONValue?] = { low, high in [n(low), n(high), n(g.double(in: low...high))] }
        var counts = (taken: 0, refused: 0)
        for index in 0..<1500 {
            let keys: [(String, valid: [JSONValue?], invalid: [JSONValue?])] = [
                ("projection", [.string("TAN")], [nil, .null, .string("SIN"), .string("tan"), .string("TAN "), .int(1)]),
                ("crvalRaDeg", [n(0), n(360), literal("-0"), n(.leastNonzeroMagnitude), n(Double(360).nextDown)] + range(0, 360),
                 [n(-.leastNonzeroMagnitude), n(Double(360).nextUp), n(-1), n(720)] + notANumber),
                ("crvalDecDeg", range(-90, 90), [n(Double(-90).nextDown), n(Double(90).nextUp), n(180)] + notANumber),
                ("crpixX", anyFinite(), notANumber),
                ("crpixY", anyFinite(), notANumber),
                ("scaleDegPerPx", [n(.leastNonzeroMagnitude), n(g.double(in: 1e-6...1)), n(1e300)],
                 [.int(0), literal("-0"), n(-1e-9)] + notANumber),
                ("rotationDeg", anyFinite(), notANumber),
                ("radialDistortionK1", range(-0.25, 0.25) + [.int(0)], [n(Double(0.25).nextUp), n(-Double(0.25).nextUp), .int(1)] + notANumber),
                ("radialDistortionK2", range(-0.25, 0.25) + [.int(0)], [n(Double(0.25).nextUp), n(-Double(0.25).nextUp), .int(-1)] + notANumber),
                ("parity", [.int(1), .int(-1), literal("1.0"), literal("-1.0")], [.int(0), .int(2), .int(-2), n(0.5), n(Double(1).nextUp)] + notANumber),
                ("fovHorizontalDeg", [n(.leastNonzeroMagnitude), n(175), n(Double(175).nextDown), n(g.double(in: 0.1...175))],
                 [.int(0), literal("-0"), n(Double(175).nextUp), n(-1)] + notANumber),
                ("fovVerticalDeg", [n(.leastNonzeroMagnitude), n(175), n(Double(175).nextDown), n(g.double(in: 0.1...175))],
                 [.int(0), literal("-0"), n(Double(175).nextUp), n(-1)] + notANumber),
            ]
            var wcs = base
            var allValid = true
            var chosen: [String: JSONValue?] = [:]
            // Usually all in range, so the taken cases are many; otherwise one to three keys out of it
            let outOfRange = Set(g.int(in: 0...2) == 0 ? [] : (0..<g.int(in: 1...3)).map { _ in g.int(in: 0...(keys.count - 1)) })
            for (position, key) in keys.enumerated() {
                let valid = !outOfRange.contains(position)
                let value = g.pick(valid ? key.valid : key.invalid)
                allValid = allValid && valid
                chosen[key.0] = value
                wcs[key.0] = value
            }
            let context = "seed \(seed) case \(index): \(chosen)"
            let read = PoseWCS.read(wcs)
            XCTAssertEqual(read != nil, allValid, context)
            guard let read else { counts.refused += 1; continue }
            counts.taken += 1
            func number(_ key: String) -> Double? { chosen[key]??.numberValue }
            XCTAssertEqual(read.crvalRaDeg, number("crvalRaDeg"), context)
            XCTAssertEqual(read.crvalDecDeg, number("crvalDecDeg"), context)
            XCTAssertEqual(read.crpixX, number("crpixX"), context)
            XCTAssertEqual(read.crpixY, number("crpixY"), context)
            XCTAssertEqual(read.scaleDegPerPx, number("scaleDegPerPx"), context)
            XCTAssertEqual(read.rotationDeg, number("rotationDeg"), context)
            XCTAssertEqual(read.k1, number("radialDistortionK1"), context)
            XCTAssertEqual(read.k2, number("radialDistortionK2"), context)
            XCTAssertEqual(Double(read.parity), number("parity"), context)
        }
        XCTAssertGreaterThan(counts.taken, 100, "seed \(seed)")
        XCTAssertGreaterThan(counts.refused, 100, "seed \(seed)")
        for whole in [JSONValue.null, .array([base]), .string("TAN"), .int(1)] { XCTAssertNil(PoseWCS.read(whole)) }
    }

    // MARK: The attitude quaternion

    private static func unitQuaternion<G: RandomNumberGenerator>(_ g: inout G) -> PointingRecord.Quaternion {
        while true {
            let raw = (0..<4).map { _ in g.double(in: -1...1) }
            let norm = raw.map { $0 * $0 }.reduce(0, +).squareRoot()
            if norm > 0.1 { return .init(x: raw[0] / norm, y: raw[1] / norm, z: raw[2] / norm, w: raw[3] / norm) }
        }
    }

    private static func negated(_ q: PointingRecord.Quaternion) -> PointingRecord.Quaternion { .init(x: -q.x, y: -q.y, z: -q.z, w: -q.w) }

    /// The builder and the validator draw the unit line at the same double, for either sign: an attitude scaled to a
    /// norm at the tolerance's edge, give or take a few doubles, is built into a measured heading exactly when the
    /// validator takes the same four numbers in an otherwise valid record, and the record carries them as given (no sign
    /// turned, no number rounded), through its wire bytes too. The TypeScript and Python contracts compute the norm with
    /// the same arithmetic in the same order (pointing.ts `AttitudeQuaternionSchema`, pointing.py `AttitudeQuaternion`).
    func testTheBuilderAndTheValidatorDrawTheUnitLineAtTheSameDoubleEitherSign() throws {
        let seed = propertySeed()
        var g = SeededGenerator(seed: seed)
        let tolerance = PointingContract.unitQuaternionTolerance
        let hash = PhotoHash.sha256Hex(Data("pointing-fuzz".utf8))
        let base = try SharedFixture.base()
        var counts = (built: 0, refused: 0, negative: 0)
        for index in 0..<500 {
            let unit = Self.unitQuaternion(&g)
            var norm = g.pick([1 + tolerance, 1 - tolerance, 1, 1 + tolerance * g.double(in: -2...2)])
            for _ in 0..<g.int(in: 0...6) { norm = g.bool() ? norm.nextUp : norm.nextDown }
            var q = PointingRecord.Quaternion(x: unit.x * norm, y: unit.y * norm, z: unit.z * norm, w: unit.w * norm)
            if g.bool() { q = Self.negated(q) }
            let context = "seed \(seed) case \(index): \(q) norm \(q.norm)"
            var capture = SampleCapture.capture(photoSha256: hash)
            capture.motion.samples = capture.motion.samples.map { var sample = $0; sample.quaternion = q; return sample }
            var record = base
            record.set(["heading", "attitude", "quaternion"],
                       .object(JSONObject(["x": .double(q.x), "y": .double(q.y), "z": .double(q.z), "w": .double(q.w)])))
            let validatorTakes = judge(record) == nil
            if validatorTakes { XCTAssertEqual(abs(q.norm - 1) <= tolerance, true, context) }
            switch Result(catching: { () throws(PointingBuildError) -> PointingRecord in try PointingRecordBuilder.build(capture) }) {
            case .success(let built):
                XCTAssertTrue(validatorTakes, context)
                guard case .measured(let measured) = built.heading else { XCTFail("not measured: \(context)"); continue }
                XCTAssertEqual(measured.quaternion, q, context)
                let wire = try built.wireData()
                XCTAssertEqual(PointingValidator.issues(inWire: wire), [], context)
                let back = try JSONText.read(wire).at(["heading", "attitude", "quaternion"])
                XCTAssertEqual([back?["x"], back?["y"], back?["z"], back?["w"]].map { $0?.numberValue }, [q.x, q.y, q.z, q.w], context)
                counts.built += 1
                if q.w < 0 { counts.negative += 1 }
            case .failure(.attitudeNotUnitQuaternion(let reported)):
                XCTAssertFalse(validatorTakes, context)
                XCTAssertEqual(reported, q.norm, context)
                counts.refused += 1
            case .failure(let other):
                XCTFail("\(other): \(context)")
            }
        }
        XCTAssertGreaterThan(counts.built, 0, "seed \(seed)")
        XCTAssertGreaterThan(counts.refused, 0, "seed \(seed)")
        XCTAssertGreaterThan(counts.negative, 0, "seed \(seed)")
    }

    /// The second crosshair moves the same for an attitude and its negation at either end (q and -q are one attitude);
    /// a turn about the lens's axis does not move it; a turn about the device's Y or X axis alone moves it across or up
    /// and down by the preview's focal length times the turn's tangent; and it has a place for a turn below a quarter
    /// and none at or past one, the turn found from the two attitudes' dot product.
    func testTheCrosshairIsTheSameForAnAttitudeAndItsNegationAndHoldsTheQuarterTurn() throws {
        let seed = propertySeed()
        var g = SeededGenerator(seed: seed)
        var counts = (placed: 0, none: 0)
        for index in 0..<3000 {
            let start = Self.unitQuaternion(&g)
            let axis = Self.unitQuaternion(&g)
            let length = (axis.x * axis.x + axis.y * axis.y + axis.z * axis.z).squareRoot()
            let angle = g.pick([g.double(in: 0...Double.pi), Double.pi / 2 + g.double(in: -1e-6...1e-6), g.double(in: 0...0.3)])
            let half = sin(angle / 2) / max(length, 1e-12)
            let turn = PointingRecord.Quaternion(x: axis.x * half, y: axis.y * half, z: axis.z * half, w: cos(angle / 2))
            let now = start.multiplied(turn)
            let width = g.double(in: 100...1200)
            let fov = g.double(in: 5...150)
            let context = "seed \(seed) case \(index): \(start) \(now) \(width) \(fov)"
            let offset = NightExposure.crosshairOffset(start: start, now: now, previewWidthPt: width, horizontalFovDeg: fov)
            for (a, b) in [(Self.negated(start), now), (start, Self.negated(now)), (Self.negated(start), Self.negated(now))] {
                let other = NightExposure.crosshairOffset(start: a, now: b, previewWidthPt: width, horizontalFovDeg: fov)
                XCTAssertEqual(other?.dx, offset?.dx, context)
                XCTAssertEqual(other?.dy, offset?.dy, context)
            }
            let dot = abs(start.x * now.x + start.y * now.y + start.z * now.z + start.w * now.w)
            let between = 2 * acos(min(1, dot))
            if abs(between - Double.pi / 2) > 1e-7 { XCTAssertEqual(offset == nil, between > Double.pi / 2, "\(context) turn \(between)") }
            if offset == nil { counts.none += 1 } else { counts.placed += 1 }

            let focal = width / 2 / tan(fov * Double.pi / 360)
            let small = g.double(in: -1.5...1.5)
            let aboutLens = start.multiplied(.init(x: 0, y: 0, z: sin(small / 2), w: cos(small / 2)))
            let still = try XCTUnwrap(NightExposure.crosshairOffset(start: start, now: aboutLens, previewWidthPt: width, horizontalFovDeg: fov), context)
            XCTAssertEqual(still.dx, 0, accuracy: 1e-7, context)
            XCTAssertEqual(still.dy, 0, accuracy: 1e-7, context)
            let aboutY = start.multiplied(.init(x: 0, y: sin(small / 2), z: 0, w: cos(small / 2)))
            let across = try XCTUnwrap(NightExposure.crosshairOffset(start: start, now: aboutY, previewWidthPt: width, horizontalFovDeg: fov), context)
            XCTAssertEqual(across.dx, focal * tan(small), accuracy: 1e-6 * max(1, abs(focal * tan(small))), context)
            XCTAssertEqual(across.dy, 0, accuracy: 1e-7, context)
            let aboutX = start.multiplied(.init(x: sin(small / 2), y: 0, z: 0, w: cos(small / 2)))
            let upDown = try XCTUnwrap(NightExposure.crosshairOffset(start: start, now: aboutX, previewWidthPt: width, horizontalFovDeg: fov), context)
            XCTAssertEqual(upDown.dx, 0, accuracy: 1e-7, context)
            XCTAssertEqual(upDown.dy, focal * tan(small), accuracy: 1e-6 * max(1, abs(focal * tan(small))), context)
        }
        XCTAssertGreaterThan(counts.placed, 0, "seed \(seed)")
        XCTAssertGreaterThan(counts.none, 0, "seed \(seed)")
    }

    // MARK: The uploader

    /// What the scripted laptop answers one request with: the counts the connection reports (fractions of the whole
    /// request, never going back, never past it, as `LaptopTransport` promises) and the result.
    struct Answer {
        let fractions: [Double]
        let result: Result<HTTPReply, ExchangeFailure>
    }

    /// A laptop that answers each request with the next scripted answer and keeps every request it was sent.
    final class ScriptedLaptop: LaptopTransport, @unchecked Sendable {
        private let lock = NSLock()
        private var answers: [Answer]
        private var taken: [Data] = []
        init(_ answers: [Answer]) { self.answers = answers }
        var requests: [Data] { lock.withLock { taken } }

        func exchange(_ request: Data, to endpoint: LaptopEndpoint, timeoutSec: Double,
                      onSent: @escaping @Sendable (Int64) -> Void) async -> Result<HTTPReply, ExchangeFailure> {
            let answer = lock.withLock { () -> Answer? in
                taken.append(request)
                return answers.isEmpty ? nil : answers.removeFirst()
            }
            // A request the script did not foresee: the test sees it among the requests
            guard let answer else { return .failure(.closed) }
            for fraction in answer.fractions { onSent(Int64(Double(request.count) * fraction)) }
            return answer.result
        }
    }

    final class StateLog: @unchecked Sendable {
        private let lock = NSLock()
        private var items: [UploadState] = []
        func append(_ state: UploadState) { lock.withLock { items.append(state) } }
        var states: [UploadState] { lock.withLock { items } }
    }

    /// One drawn answer and the failure, if any, the uploader's documented rules (`UploadFailure`) give for it.
    private struct Drawn {
        let answer: Answer
        /// Nil: a 2xx whose body is a receipt the step takes. `alreadyStored` is the photo receipt's flag.
        let failure: UploadFailure?
        let alreadyStored: Bool
    }

    private static let statuses = [200, 201, 204, 299, 100, 199, 300, 301, 302, 307, 308, 399, 400, 401, 404, 405, 409, 413, 415,
                                   422, 499, 500, 503, 599]

    private static func drawFractions<G: RandomNumberGenerator>(_ g: inout G) -> [Double] {
        ((0..<g.int(in: 0...5)).map { _ in g.pick([0, 1, g.double(in: 0...1)]) }).sorted()
    }

    private static func drawExchangeFailure<G: RandomNumberGenerator>(_ g: inout G, _ phase: UploadPhase) -> (ExchangeFailure, UploadFailure) {
        switch g.int(in: 0...5) {
        case 0: return (.network(posixCode: 61), .networkError(phase: phase, posixCode: 61))
        case 1: return (.network(posixCode: nil), .networkError(phase: phase, posixCode: nil))
        case 2: return (.timedOut, .timedOut(phase: phase))
        case 3: return (.closed, .connectionClosed(phase: phase))
        case 4: return (.notThePairedPeer, .notThePairedPeer(phase: phase))
        default: return (.unreadable("a reply the reader does not take"), .unreadableReply(phase: phase))
        }
    }

    /// A body the laptop might send: (bytes, its `detail` text if it holds one, whether it is the step's own receipt).
    private static func drawPhotoAnswer<G: RandomNumberGenerator>(_ g: inout G, sha: String, other: String) -> Drawn {
        if g.int(in: 0...4) == 0 {
            let (exchange, failure) = drawExchangeFailure(&g, .photo)
            return Drawn(answer: Answer(fractions: drawFractions(&g), result: .failure(exchange)), failure: failure, alreadyStored: false)
        }
        let status = g.bool() ? g.pick([200, 201]) : g.pick(statuses)
        let already = g.bool()
        let flag = already ? "true" : "false"
        let bodies: [(String, String?, UploadFailure?)] = [
            ("{\"sourceAsset\":{\"sha256\":\"\(sha)\",\"title\":\"Upload\"},\"hints\":{},\"alreadyStored\":\(flag)}", nil, nil),
            ("{\"alreadyStored\":\(flag),\"sourceAsset\":{\"sha256\":\"\(sha)\"}}", nil, nil),
            ("{\"sourceAsset\":{\"sha256\":\"\(other)\"},\"alreadyStored\":\(flag)}", nil, .storedDifferentBytes(receiptSha256: other)),
            ("{\"sourceAsset\":{\"sha256\":\"\(sha.uppercased())\"},\"alreadyStored\":\(flag)}", nil,
             .storedDifferentBytes(receiptSha256: sha.uppercased())),
            ("{\"sourceAsset\":{\"sha256\":\"\(sha)\"}}", nil, .unreadableReply(phase: .photo)),
            ("{\"sourceAsset\":{\"sha256\":\"\(sha)\"},\"alreadyStored\":\"yes\"}", nil, .unreadableReply(phase: .photo)),
            ("{\"sourceAsset\":{\"sha256\":7},\"alreadyStored\":true}", nil, .unreadableReply(phase: .photo)),
            ("{\"sourceAsset\":\"\(sha)\",\"alreadyStored\":true}", nil, .unreadableReply(phase: .photo)),
            ("{\"detail\":\"declared as image/heic, but the bytes are image/png\"}", "declared as image/heic, but the bytes are image/png",
             .unreadableReply(phase: .photo)),
            ("{\"detail\":[{\"msg\":\"field required\"}]}", nil, .unreadableReply(phase: .photo)),
            ("<html>Not Found</html>", nil, .unreadableReply(phase: .photo)),
            ("[]", nil, .unreadableReply(phase: .photo)),
            ("", nil, .unreadableReply(phase: .photo)),
        ]
        // Half the time one of the two receipts for these bytes, so both requests succeed in every seed
        let (text, detail, onSuccess) = g.bool() ? g.pick(Array(bodies.prefix(2))) : g.pick(bodies)
        let reply = HTTPReply(status: status, headers: [], body: Data(text.utf8))
        let failure: UploadFailure? = switch status {
        case 200...299: onSuccess
        case 300...399: .redirected(phase: .photo, status: status)
        default: .refused(phase: .photo, status: status, detail: detail)
        }
        return Drawn(answer: Answer(fractions: drawFractions(&g), result: .success(reply)), failure: failure, alreadyStored: already)
    }

    private static func drawRecordAnswer<G: RandomNumberGenerator>(_ g: inout G, sha: String, other: String) -> Drawn {
        if g.int(in: 0...4) == 0 {
            let (exchange, failure) = drawExchangeFailure(&g, .record)
            return Drawn(answer: Answer(fractions: drawFractions(&g), result: .failure(exchange)), failure: failure, alreadyStored: false)
        }
        let status = g.bool() ? g.pick([200, 201]) : g.pick(statuses)
        let bodies: [(String, String?, UploadFailure?)] = [
            ("{\"sourceAssetSha256\":\"\(sha)\"}", nil, nil),
            ("{\"sourceAssetSha256\":\"\(sha)\",\"receivedAtUTC\":\"2000-01-01T00:00:00Z\"}", nil, nil),
            ("{\"sourceAssetSha256\":\"\(other)\"}", nil, .unreadableReply(phase: .record)),
            ("{\"sourceAssetSha256\":\"\(sha.uppercased())\"}", nil, .unreadableReply(phase: .record)),
            ("{\"sourceAsset\":{\"sha256\":\"\(sha)\"}}", nil, .unreadableReply(phase: .record)),
            ("[\"\(sha)\"]", nil, .unreadableReply(phase: .record)),
            ("\"\(sha)\"", nil, .unreadableReply(phase: .record)),
            ("{\"detail\":\"Not Found\"}", "Not Found", .unreadableReply(phase: .record)),
            ("{\"detail\":\"Method Not Allowed\"}", "Method Not Allowed", .unreadableReply(phase: .record)),
            ("not json", nil, .unreadableReply(phase: .record)),
            ("", nil, .unreadableReply(phase: .record)),
        ]
        let (text, detail, onSuccess) = g.bool() ? g.pick(Array(bodies.prefix(2))) : g.pick(bodies)
        let reply = HTTPReply(status: status, headers: [], body: Data(text.utf8))
        let failure: UploadFailure? = switch status {
        case 200...299: onSuccess
        case 300...399: .redirected(phase: .record, status: status)
        default: .refused(phase: .record, status: status, detail: detail)
        }
        return Drawn(answer: Answer(fractions: drawFractions(&g), result: .success(reply)), failure: failure, alreadyStored: false)
    }

    private enum RecordGiven {
        case none
        case object(PointingRecord)
        case wire(Data)
    }

    /// Every answer a laptop can give, to either request, for a photo with or without its record (as the record or as the
    /// very bytes kept), or with a record for another photo or one the contract refuses: the states begin with `waiting`,
    /// end with the one state returned, the only `failed` or `done` among them; each body's counts start at zero, never
    /// go back and never pass the body, the photo's before the record's; the requests are exactly the photo's
    /// `POST /api/ingest` and then the record's `PUT /api/ingest/{sha256}/pointing`, byte for byte, and no request is
    /// ever sent again after a failure (the uploader never retries); and the end is the one `UploadFailure` names for
    /// what came, or `done` with the photo receipt's flag and the record counted accepted only when its receipt named
    /// these bytes.
    func testTheUploadersStatesAndRequestsFollowEveryAnswerAndNothingIsSentTwice() async throws {
        let seed = propertySeed()
        var g = SeededGenerator(seed: seed)
        let address = try XCTUnwrap(LaptopAddress("http://127.0.0.1:8187"))
        var reached: [String: Int] = [:]
        for index in 0..<600 {
            var drawnPhoto = Data(count: g.int(in: 0...9) == 0 ? 0 : g.int(in: 1...400))
            for position in drawnPhoto.indices { drawnPhoto[position] = UInt8(truncatingIfNeeded: g.next()) }
            let photo = drawnPhoto
            let sha = PhotoHash.sha256Hex(photo)
            let other = PhotoHash.sha256Hex(photo + Data([0x2A]))
            let contentType = g.pick(PhotoContentType.allCases)
            var own = try PointingRecordBuilder.build(SampleCapture.capture(photoSha256: sha))
            var given = RecordGiven.none
            var preFailure: UploadFailure?
            var refusedForContract = false
            switch g.int(in: 0...9) {
            case 0...2: given = .none
            case 3...5: given = .object(own)
            case 6, 7: given = .wire(try own.wireData())
            case 8:
                own.sourceAssetSha256 = other
                given = g.bool() ? .object(own) : .wire(try own.wireData())
                preFailure = .recordNamesAnotherPhoto
            default:
                own.motion.gravityDeviceG = .init(x: 0, y: 0, z: 9.81)
                given = g.bool() ? .object(own) : .wire(try own.wireData())
                refusedForContract = true
            }
            var recordBody: Data?
            switch given {
            case .none: recordBody = nil
            case .object(let record): recordBody = JSONText.data(try record.wireValue())
            case .wire(let bytes): recordBody = bytes
            }
            let photoDrawn = Self.drawPhotoAnswer(&g, sha: sha, other: other)
            let recordDrawn = Self.drawRecordAnswer(&g, sha: sha, other: other)
            // Two spare answers, so a request sent again would be answered and seen
            let spare = Answer(fractions: [1], result: .success(HTTPReply(status: 201, headers: [], body: Data("{}".utf8))))
            let laptop = ScriptedLaptop([photoDrawn.answer, recordDrawn.answer, spare, spare])
            let uploader = CaptureUploader(address: address, timeoutSec: 5, transport: laptop)
            let log = StateLog()
            let last: UploadState
            switch given {
            case .none, .object:
                var chosen: PointingRecord?
                if case .object(let r) = given { chosen = r }
                let record = chosen
                last = await bounded { await uploader.send(photo: photo, contentType: contentType, record: record) { log.append($0) } }
            case .wire(let bytes):
                last = await bounded { await uploader.send(photo: photo, contentType: contentType, recordWire: bytes) { log.append($0) } }
            }
            let states = log.states
            let context = "seed \(seed) case \(index): photo \(photo.count) B, record \(recordBody?.count ?? -1) B, ended \(last)"

            // The end the rules call for, and the requests it took
            let expected: UploadState
            var requestsWanted = 0
            if let preFailure {
                expected = .failed(preFailure)
            } else if refusedForContract {
                guard case .failed(.recordNotWritable(let issues)) = last, !issues.isEmpty else {
                    XCTFail("a record the contract refuses was not refused: \(context)"); continue
                }
                expected = last
            } else if let failure = photoDrawn.failure {
                expected = .failed(failure)
                requestsWanted = 1
            } else if recordBody == nil {
                expected = .done(sha256: sha, photoAlreadyStored: photoDrawn.alreadyStored, recordAccepted: false)
                requestsWanted = 1
            } else if let failure = recordDrawn.failure {
                expected = .failed(failure)
                requestsWanted = 2
            } else {
                expected = .done(sha256: sha, photoAlreadyStored: photoDrawn.alreadyStored, recordAccepted: true)
                requestsWanted = 2
            }
            XCTAssertEqual(last, expected, context)
            reached[String(describing: expected).prefix(while: { $0 != "(" }).description + "/\(requestsWanted)", default: 0] += 1

            // The states
            XCTAssertEqual(states.first, .waiting, context)
            XCTAssertEqual(states.last, last, context)
            let ends = states.filter { if case .failed = $0 { true } else if case .done = $0 { true } else { false } }
            XCTAssertEqual(ends, [last], context)
            if requestsWanted == 0 { XCTAssertEqual(states, [.waiting, last], context) }
            var lastCount: [UploadPhase: Int64] = [:]
            for state in states {
                guard case .sending(let phase, let sent, let total) = state else { continue }
                if lastCount[phase] == nil { XCTAssertEqual(sent, 0, "\(phase) starts at zero: \(context)") }
                if phase == .photo { XCTAssertNil(lastCount[.record], "a photo count after the record's: \(context)") }
                XCTAssertEqual(total, Int64(phase == .photo ? photo.count : (recordBody?.count ?? -1)), context)
                XCTAssertTrue(0 <= sent && sent <= total, "\(phase) \(sent) of \(total): \(context)")
                XCTAssertGreaterThanOrEqual(sent, lastCount[phase] ?? 0, "\(phase) went back: \(context)")
                lastCount[phase] = sent
            }
            XCTAssertEqual(lastCount[.photo] != nil, requestsWanted >= 1, context)
            XCTAssertEqual(lastCount[.record] != nil, requestsWanted == 2, context)

            // The requests, byte for byte, and none sent again
            let requests = laptop.requests
            XCTAssertEqual(requests.count, requestsWanted, context)
            if requests.count >= 1 {
                XCTAssertEqual(requests[0], HTTPMessage.request(method: "POST", path: "/api/ingest", endpoint: address.endpoint,
                                                                contentType: contentType.rawValue, body: photo), context)
            }
            if requests.count >= 2, let recordBody {
                XCTAssertEqual(requests[1], HTTPMessage.request(method: "PUT", path: "/api/ingest/\(sha)/pointing", endpoint: address.endpoint,
                                                                contentType: "application/json", body: recordBody), context)
            }
        }
        for outcome in ["done/1", "done/2", "failed/0", "failed/1", "failed/2"] {
            XCTAssertGreaterThan(reached[outcome, default: 0], 0, "seed \(seed): no \(outcome) in \(reached)")
        }
    }

    // MARK: The upload coordinator

    /// A laptop that holds its one photo request until the test answers it, and ends it as closed when its task is
    /// cancelled, as the app's connection does (`NetworkLaptopTransport`).
    final class HeldLaptop: LaptopTransport, @unchecked Sendable {
        private let lock = NSLock()
        private var waiting: CheckedContinuation<Result<HTTPReply, ExchangeFailure>, Never>?
        private var answer: Result<HTTPReply, ExchangeFailure>?
        private var counts = (entered: 0, ended: 0)
        var entered: Int { lock.withLock { counts.entered } }
        var ended: Int { lock.withLock { counts.ended } }

        /// The first answer given is the one the request gets; a later one does nothing.
        func answer(_ result: Result<HTTPReply, ExchangeFailure>) {
            let resume = lock.withLock { () -> CheckedContinuation<Result<HTTPReply, ExchangeFailure>, Never>? in
                guard answer == nil else { return nil }
                answer = result
                defer { waiting = nil }
                return waiting
            }
            resume?.resume(returning: result)
        }

        func exchange(_ request: Data, to endpoint: LaptopEndpoint, timeoutSec: Double,
                      onSent: @escaping @Sendable (Int64) -> Void) async -> Result<HTTPReply, ExchangeFailure> {
            lock.withLock { counts.entered += 1 }
            onSent(Int64(request.count))
            let result = await withTaskCancellationHandler {
                await withCheckedContinuation { continuation in
                    let now = lock.withLock { () -> Result<HTTPReply, ExchangeFailure>? in
                        if let answer { return answer }
                        waiting = continuation
                        return nil
                    }
                    if let now { continuation.resume(returning: now) }
                }
            } onCancel: {
                self.answer(.failure(.closed))
            }
            lock.withLock { counts.ended += 1 }
            return result
        }
    }

    @MainActor
    final class Ends {
        var runs: [Int] = []
    }

    /// Starts, resets and the laptop's answers in any order: right after a start the state is `waiting`, right after a
    /// reset there is none; once everything has ended, the state is the last run's own end when a start came last and
    /// none when a reset did, and no late state from an earlier run overwrote it. `then` is called at most once a run,
    /// in the order the runs started, once for the last run when a start came last, and never for a run a start or a
    /// reset came after before the laptop answered it (a run answered before that may have ended first, and then its
    /// `then` came while it was still the current run). A settled run (answered, then waited on until its `then` came)
    /// keeps that `then` when a later start or reset supersedes it.
    @MainActor
    func testTheCoordinatorKeepsOnlyTheLastRunsStatesUnderStartsAndResetsInAnyOrder() async throws {
        let seed = propertySeed()
        var g = SeededGenerator(seed: seed)
        let address = try XCTUnwrap(LaptopAddress("http://127.0.0.1:8187"))
        var reached: [String: Int] = [:]
        for index in 0..<120 {
            let coordinator = UploadCoordinator()
            let ends = Ends()
            var laptops: [HeldLaptop] = []
            var finals: [UploadState] = []
            var answered: Set<Int> = []
            var lastWasReset = false
            var current: Int?
            var supersededUnanswered: Set<Int> = []
            var settled: Set<Int> = []
            var script: [String] = []
            func answer(_ run: Int) {
                let photo = Data([UInt8(run), UInt8(index & 0xFF)])
                let sha = PhotoHash.sha256Hex(photo)
                let already = (run + index) % 2 == 0
                let result: Result<HTTPReply, ExchangeFailure> = run % 3 == 2
                    ? .failure(.timedOut)
                    : .success(HTTPReply(status: 201, headers: [],
                                         body: Data("{\"sourceAsset\":{\"sha256\":\"\(sha)\"},\"alreadyStored\":\(already)}".utf8)))
                laptops[run].answer(result)
                answered.insert(run)
                script.append("answer \(run)")
            }
            for _ in 0..<g.int(in: 1...8) {
                let open = laptops.indices.filter { !answered.contains($0) }
                switch g.int(in: 0...9) {
                case 0...3:
                    if let current, !answered.contains(current) { supersededUnanswered.insert(current) }
                    let run = laptops.count
                    current = run
                    let photo = Data([UInt8(run), UInt8(index & 0xFF)])
                    let laptop = HeldLaptop()
                    laptops.append(laptop)
                    let sha = PhotoHash.sha256Hex(photo)
                    finals.append(run % 3 == 2 ? .failed(.timedOut(phase: .photo))
                                  : .done(sha256: sha, photoAlreadyStored: (run + index) % 2 == 0, recordAccepted: false))
                    coordinator.start(photo: photo, contentType: .png, record: nil,
                                      uploader: CaptureUploader(address: address, timeoutSec: 5, transport: laptop)) { _ in ends.runs.append(run) }
                    XCTAssertEqual(coordinator.state, .waiting, "seed \(seed) case \(index)")
                    XCTAssertTrue(coordinator.isSending, "seed \(seed) case \(index)")
                    lastWasReset = false
                    script.append("start \(run)")
                case 4, 5:
                    if let current, !answered.contains(current) { supersededUnanswered.insert(current) }
                    current = nil
                    coordinator.reset()
                    XCTAssertNil(coordinator.state, "seed \(seed) case \(index)")
                    XCTAssertFalse(coordinator.isSending, "seed \(seed) case \(index)")
                    lastWasReset = true
                    script.append("reset")
                case 6:
                    // Settle: the current run answered and waited on until its `then` came
                    guard let run = current else { break }
                    if !answered.contains(run) { answer(run) }
                    let deadline = Date().addingTimeInterval(3)
                    while Date() < deadline, ends.runs.last != run { try await Task.sleep(nanoseconds: 1_000_000) }
                    XCTAssertEqual(ends.runs.last, run, "seed \(seed) case \(index): run \(run) did not settle")
                    settled.insert(run)
                    script.append("settle \(run)")
                default:
                    if let run = open.isEmpty ? nil : g.pick(open) { answer(run) }
                }
                if g.bool() { await Task.yield() }
            }
            for run in laptops.indices where !answered.contains(run) { answer(run) }
            let context = "seed \(seed) case \(index): \(script.joined(separator: ", "))"

            // Every run's request ends (an earlier run's when its task is cancelled), waited on here and bounded, and
            // each run sent exactly one: none is sent again
            let endBy = Date().addingTimeInterval(3)
            while Date() < endBy, laptops.contains(where: { $0.entered == 0 || $0.ended != $0.entered }) {
                try await Task.sleep(nanoseconds: 1_000_000)
            }
            XCTAssertEqual(laptops.map(\.entered), laptops.map { _ in 1 }, "requests sent: \(context)")
            XCTAssertEqual(laptops.map(\.ended), laptops.map(\.entered), "requests ended: \(context)")
            let wantState: UploadState? = current.map { finals[$0] }
            let deadline = Date().addingTimeInterval(3)
            while Date() < deadline, coordinator.state != wantState || (current != nil && ends.runs.last != current) {
                try await Task.sleep(nanoseconds: 1_000_000)
            }
            // Then a little longer, for any late state of an earlier run
            try await Task.sleep(nanoseconds: 20_000_000)
            XCTAssertEqual(coordinator.state, wantState, context)
            XCTAssertEqual(ends.runs, ends.runs.sorted(), "in start order: \(ends.runs) \(context)")
            XCTAssertEqual(Set(ends.runs).count, ends.runs.count, "once a run: \(ends.runs) \(context)")
            if let current { XCTAssertEqual(ends.runs.last, current, "\(ends.runs) \(context)") }
            XCTAssertTrue(supersededUnanswered.isDisjoint(with: ends.runs), "\(ends.runs) superseded \(supersededUnanswered) \(context)")
            XCTAssertTrue(settled.isSubset(of: ends.runs), "\(ends.runs) settled \(settled) \(context)")
            XCTAssertFalse(coordinator.isSending, context)
            if laptops.isEmpty { reached["nothing started", default: 0] += 1 }
            if lastWasReset, !laptops.isEmpty { reached["reset last", default: 0] += 1 }
            if !supersededUnanswered.isEmpty { reached["superseded before its answer", default: 0] += 1 }
            if ends.runs.contains(where: { $0 != current }) { reached["ended before it was superseded", default: 0] += 1 }
            if current != nil { reached["a start last", default: 0] += 1 }
        }
        for outcome in ["reset last", "superseded before its answer", "ended before it was superseded", "a start last"] {
            XCTAssertGreaterThan(reached[outcome, default: 0], 0, "seed \(seed): no \(outcome) in \(reached)")
        }
    }
}
