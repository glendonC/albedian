import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// D31 item 3 (UIUX-iphone-lockon-READY) with MOTION-READY-iphone section 5: when the solve passes, each early mark the
/// solve matched goes to its matched detection on the wait's photo, every other early mark leaves; nothing moves for a
/// solution that is not calibrated. The same star is known by its catalog's own id (`catalogSourceId`, "HIP 71860" or
/// "Gaia DR3 <n>"), which both the sky context and the solution carry; a real solve's reference ids are hip-<n> or
/// gaia-dr3-<n> (the local solve of rp-b-01, 19 Nov), so the sky context's star-<name> ids never pair with them.
final class LockOnTravelTests: XCTestCase {
    private func calibrated() throws -> (SolutionReadout, JSONValue) {
        let valid = try XCTUnwrap(ContractFixture.load("observation_solution_contract")["valid"]?.objectValue).members.map(\.value)
        let sample = try XCTUnwrap(valid.first { $0["status"]?.stringValue == "calibrated" && $0["sourceSha256"] != nil
            && ($0["findings"]?.arrayValue ?? []).contains { $0["evidenceClass"]?.stringValue == "catalog_matched" } })
        return (try SolutionReader.read(sample, sha256: sample["sourceSha256"]!.stringValue!).get(), sample)
    }

    private func early(_ source: String, id: String = "star-early") -> SkyMark {
        var mark = SkyMark(id: id, name: id, altDeg: 40, azDeg: 120, source: .catalog(release: "Hipparcos 2007"))
        mark.catalogSourceId = source
        return mark
    }

    func testEachMatchedFindingNamesTheStarItMatched() throws {
        let (readout, sample) = try calibrated()
        let matched = (sample["findings"]?.arrayValue ?? []).filter { $0["evidenceClass"]?.stringValue == "catalog_matched" }
        XCTAssertEqual(readout.marks.map(\.catalogSourceId), matched.map { $0["catalogRef"]?["catalogSourceId"]?.stringValue })
        XCTAssertTrue(readout.marks.allSatisfy { $0.catalogSourceId != nil })
        XCTAssertFalse(readout.marks.isEmpty)
    }

    func testAMatchedStarGoesToItsDetectionOnThePhotoAndEveryOtherLeaves() throws {
        let (readout, _) = try calibrated()
        let first = try XCTUnwrap(readout.marks.first)
        let star = try XCTUnwrap(first.catalogSourceId)
        let frame = StoredFrame(widthPx: 3024, heightPx: 4032, orientation: .upright)
        // The early mark's own id is the sky context's (star-<name>); it pairs by the catalog's source id alone
        let targets = LockOnTravel.targets(early: [early(star, id: "star-sky-context-name"), early("HIP 1", id: "star-not-in-this-photo")],
                                           readout: readout, frame: frame, widthPt: 390, heightPt: 520)
        let at = frame.placeCovering(xPx: first.xPx, yPx: first.yPx, onWidth: 390, height: 520)
        XCTAssertEqual(targets.count, frame.isOnCard(xPx: first.xPx, yPx: first.yPx, width: 390, height: 520) ? 1 : 0)
        XCTAssertTrue(frame.isOnCard(xPx: first.xPx, yPx: first.yPx, width: 390, height: 520))
        let target = try XCTUnwrap(targets["star-sky-context-name"], "keyed by the early mark's own id")
        XCTAssertEqual(target.x, at.x, accuracy: 1e-9)
        XCTAssertEqual(target.y, at.y, accuracy: 1e-9)
        XCTAssertNil(targets["star-not-in-this-photo"])
    }

    func testNothingMovesUnlessTheSolvePassed() throws {
        let (readout, _) = try calibrated()
        let star = try XCTUnwrap(readout.marks.first?.catalogSourceId)
        let frame = StoredFrame(widthPx: 3024, heightPx: 4032, orientation: .upright)
        for status in [SolutionStatus.approximate, .predictedOnly, .unresolved, .pending] {
            let other = SolutionReadout(status: status, counts: readout.counts, marks: readout.marks, detectedCandidateCount: nil,
                                        matchedInlierCount: nil, pose: nil, hasWCS: readout.hasWCS, frameTimeSec: 0)
            XCTAssertEqual(LockOnTravel.targets(early: [early(star)], readout: other, frame: frame, widthPt: 390, heightPt: 520).count, 0,
                           status.rawValue)
        }
    }

    /// A calibrated word without a WCS behind it moves nothing either.
    func testNothingMovesWithoutAWCS() throws {
        let (readout, _) = try calibrated()
        let star = try XCTUnwrap(readout.marks.first?.catalogSourceId)
        let frame = StoredFrame(widthPx: 3024, heightPx: 4032, orientation: .upright)
        let bare = SolutionReadout(status: .calibrated, counts: readout.counts, marks: readout.marks, detectedCandidateCount: nil,
                                   matchedInlierCount: nil, pose: nil, hasWCS: false, frameTimeSec: 0)
        XCTAssertTrue(LockOnTravel.targets(early: [early(star)], readout: bare, frame: frame, widthPt: 390, heightPt: 520).isEmpty)
        XCTAssertFalse(LockOnTravel.targets(early: [early(star)], readout: readout, frame: frame, widthPt: 390, heightPt: 520).isEmpty)
    }

    /// A detection the photo's cut leaves off the wait's frame is not a place to travel to: that mark leaves too.
    func testADetectionOffTheFrameIsNoTarget() throws {
        let (readout, _) = try calibrated()
        let mark = try XCTUnwrap(readout.marks.first)
        let star = try XCTUnwrap(mark.catalogSourceId)
        // A wide frame whose sides the 3:4 cut removes, the detection placed at its far left
        let wide = StoredFrame(widthPx: 8000, heightPx: 3000, orientation: .upright)
        let moved = SolutionReadout.Mark(id: mark.id, title: mark.title, xPx: 10, yPx: 1500, apparentMagnitude: nil, catalogSourceId: star)
        let off = SolutionReadout(status: .calibrated, counts: readout.counts, marks: [moved], detectedCandidateCount: nil,
                                  matchedInlierCount: nil, pose: nil, hasWCS: true, frameTimeSec: 0)
        XCTAssertFalse(wide.isOnCard(xPx: 10, yPx: 1500, width: 390, height: 520))
        XCTAssertTrue(LockOnTravel.targets(early: [early(star)], readout: off, frame: wide, widthPt: 390, heightPt: 520).isEmpty)
    }
}
