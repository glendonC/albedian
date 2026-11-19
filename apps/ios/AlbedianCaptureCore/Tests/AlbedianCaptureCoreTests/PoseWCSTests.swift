import AlbedianCaptureCore
import Foundation
import XCTest

/// The pose's own WCS on the iPhone (lockon-m5a): the solver's gnomonic TAN projection with parity, roll and radial
/// distortion, placing a predicted star in the stored frame exactly as services/science/wcs_models.py
/// `WCSModel.world_to_pixel` does (the numbers below are that function's, sky coordinates only) and as the web's
/// worldToPixelWCS (packages/camera-rig/src/wcs.ts) does with the same model.
final class PoseWCSTests: XCTestCase {
    private let models: [PoseWCS] = [
        PoseWCS(crvalRaDeg: 83.8, crvalDecDeg: -5.4, crpixX: 2015.5, crpixY: 1511.5, scaleDegPerPx: 0.0168, rotationDeg: 0,
                k1: 0, k2: 0, parity: -1),
        PoseWCS(crvalRaDeg: 201.3, crvalDecDeg: -59.7, crpixX: 1511.5, crpixY: 2015.5, scaleDegPerPx: 0.021, rotationDeg: 37.5,
                k1: -0.04, k2: 0.012, parity: -1),
        PoseWCS(crvalRaDeg: 359.2, crvalDecDeg: 12, crpixX: 960, crpixY: 540, scaleDegPerPx: 0.03, rotationDeg: -120,
                k1: 0.1, k2: -0.05, parity: 1),
    ]
    // (model, ra, dec, x, y) from WCSModel.world_to_pixel (.lanetools/wcs_reference.py)
    private let reference: [(Int, Double, Double, Double, Double)] = [
        (0, 83.8, -5.4, 2015.5, 1511.5),
        (0, 88.79, 7.41, 1712.6582530067246, 734.2905203570281),
        (0, 78.63, -8.2, 2321.2695060170145, 1680.2707741118325),
        (0, 81.28, 6.35, 2167.8703085436723, 801.7440651240678),
        (1, 201.3, -59.7, 1511.5, 2015.5),
        (1, 186.65, -63.1, 1881.9101334709155, 1981.0158267663771),
        (1, 219.9, -60.83, 1241.0115205795153, 2368.2059301410345),
        (1, 210.96, -60.37, 1360.9022794859143, 2192.289750402171),
        (2, 0.6, 14.0, 995.2226444911365, 612.6720259022892),
        (2, 357.0, 9.5, 924.1875805358457, 435.6605671923587),
        (2, 359.2, 12.0, 960.0, 540.0),
    ]

    func testEachSkyPointLandsWhereTheSolversOwnModelPutsIt() throws {
        for (index, ra, dec, x, y) in reference {
            let at = try XCTUnwrap(models[index].pixel(raDeg: ra, decDeg: dec), "\(index) \(ra) \(dec)")
            XCTAssertEqual(at.x, x, accuracy: 1e-6, "\(index) \(ra) \(dec)")
            XCTAssertEqual(at.y, y, accuracy: 1e-6, "\(index) \(ra) \(dec)")
        }
    }

    /// A point on the far side of the tangent plane has no place: the solver gives NaN there, the phone nothing.
    func testAPointBehindTheTangentPlaneHasNoPlace() {
        XCTAssertNil(models[0].pixel(raDeg: 263.8, decDeg: 5.4))
        XCTAssertNil(models[0].pixel(raDeg: 173.8, decDeg: 0), "90 degrees off the centre on the sphere")
        XCTAssertNotNil(models[0].pixel(raDeg: 83.8 + 80, decDeg: -5.4))
    }

    /// The WCS as an approximate solution carries it (`WCSCalibrationModelSchema`): TAN only, each number in its range.
    func testReadsTheContractsWCSAndRefusesOneOutOfRange() throws {
        let solutions = try XCTUnwrap(ContractFixture.load("observation_solution_contract")["valid"]?.objectValue)
        let wcs = try XCTUnwrap(solutions.members.lazy.compactMap { $0.value["wcs"] }.first)
        let read = try XCTUnwrap(PoseWCS.read(wcs))
        XCTAssertEqual(read.crvalRaDeg, wcs["crvalRaDeg"]?.numberValue)
        XCTAssertEqual(read.parity, wcs["parity"]?.numberValue.map { Int($0) })
        XCTAssertEqual(read.k1, wcs["radialDistortionK1"]?.numberValue)
        func with(_ key: String, _ value: JSONValue?) -> JSONValue { var w = wcs; w[key] = value; return w }
        XCTAssertNil(PoseWCS.read(with("projection", .string("SIN"))))
        XCTAssertNil(PoseWCS.read(with("scaleDegPerPx", .int(0))))
        XCTAssertNil(PoseWCS.read(with("parity", .int(0))))
        XCTAssertNil(PoseWCS.read(with("radialDistortionK1", .number(JSONNumber(0.3)!))))
        XCTAssertNil(PoseWCS.read(with("crvalDecDeg", .int(91))))
        XCTAssertNil(PoseWCS.read(with("crpixX", nil)))
    }
}
