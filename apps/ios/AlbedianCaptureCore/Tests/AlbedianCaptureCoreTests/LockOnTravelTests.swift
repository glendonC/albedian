import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// D31 item 3 (UIUX-iphone-lockon-READY) with MOTION-READY-iphone section 5: when the solve passes, each early mark the
/// solve matched goes to its matched detection on the wait's photo, every other early mark leaves; nothing moves for a
/// solution that is not calibrated. The same star is known by its catalog id: the early mark's sky-context id is the
/// matched finding's `catalogReferenceId` (both `star-<name>`, services/science/catalog.py).
final class LockOnTravelTests: XCTestCase {
    private func calibrated() throws -> (SolutionReadout, JSONValue) {
        let valid = try XCTUnwrap(ContractFixture.load("observation_solution_contract")["valid"]?.objectValue).members.map(\.value)
        let sample = try XCTUnwrap(valid.first { $0["status"]?.stringValue == "calibrated" && $0["sourceSha256"] != nil
            && ($0["findings"]?.arrayValue ?? []).contains { $0["evidenceClass"]?.stringValue == "catalog_matched" } })
        return (try SolutionReader.read(sample, sha256: sample["sourceSha256"]!.stringValue!).get(), sample)
    }

    private func early(_ id: String) -> SkyMark { SkyMark(id: id, name: id, altDeg: 40, azDeg: 120, source: .catalog(release: "Hipparcos 2007")) }

    func testEachMatchedFindingNamesTheStarItMatched() throws {
        let (readout, sample) = try calibrated()
        let matched = (sample["findings"]?.arrayValue ?? []).filter { $0["evidenceClass"]?.stringValue == "catalog_matched" }
        XCTAssertEqual(readout.marks.map(\.catalogReferenceId), matched.map { $0["evidence"]?["catalogReferenceId"]?.stringValue })
        XCTAssertFalse(readout.marks.isEmpty)
    }

    func testAMatchedStarGoesToItsDetectionOnThePhotoAndEveryOtherLeaves() throws {
        let (readout, _) = try calibrated()
        let first = try XCTUnwrap(readout.marks.first)
        let star = try XCTUnwrap(first.catalogReferenceId)
        let frame = StoredFrame(widthPx: 3024, heightPx: 4032, orientation: .upright)
        let targets = LockOnTravel.targets(early: [early(star), early("star-not-in-this-photo")], readout: readout, frame: frame,
                                           widthPt: 390, heightPt: 520)
        let at = frame.placeCovering(xPx: first.xPx, yPx: first.yPx, onWidth: 390, height: 520)
        XCTAssertEqual(targets.count, frame.isOnCard(xPx: first.xPx, yPx: first.yPx, width: 390, height: 520) ? 1 : 0)
        if let target = targets[star] {
            XCTAssertEqual(target.x, at.x, accuracy: 1e-9)
            XCTAssertEqual(target.y, at.y, accuracy: 1e-9)
        }
        XCTAssertNil(targets["star-not-in-this-photo"])
    }

    func testNothingMovesUnlessTheSolvePassed() throws {
        let (readout, _) = try calibrated()
        let star = try XCTUnwrap(readout.marks.first?.catalogReferenceId)
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
        let star = try XCTUnwrap(readout.marks.first?.catalogReferenceId)
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
        let star = try XCTUnwrap(mark.catalogReferenceId)
        // A wide frame whose sides the 3:4 cut removes, the detection placed at its far left
        let wide = StoredFrame(widthPx: 8000, heightPx: 3000, orientation: .upright)
        let moved = SolutionReadout.Mark(id: mark.id, title: mark.title, xPx: 10, yPx: 1500, apparentMagnitude: nil, catalogReferenceId: star)
        let off = SolutionReadout(status: .calibrated, counts: readout.counts, marks: [moved], detectedCandidateCount: nil,
                                  matchedInlierCount: nil, pose: nil, hasWCS: true, frameTimeSec: 0)
        XCTAssertFalse(wide.isOnCard(xPx: 10, yPx: 1500, width: 390, height: 520))
        XCTAssertTrue(LockOnTravel.targets(early: [early(star)], readout: off, frame: wide, widthPt: 390, heightPt: 520).isEmpty)
    }
}
