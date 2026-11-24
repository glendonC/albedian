import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// mobile-8 (UIUX-iphone-solve-READY, approved 2026-11-18): the wait's one row, read from the job as the laptop's
/// approved working screen reads it (`apps/web/src/reveal/arrival.ts`, `stageRows`), and the sentence of a result that
/// is not calibrated, the web's own (`apps/web/src/services/skyLayerWords.ts`), its count with a thousands comma.
final class AfterSendTests: XCTestCase {
    private let sha = String(repeating: "ab", count: 32)

    private func job(_ state: SolveJob.State, _ stages: [SolveJob.StageProgress]) -> SolveJob {
        SolveJob(id: "job_" + sha.prefix(16), sourceAssetSha256: sha, state: state, createdAtUTC: "2031-05-02T19:20:00.000Z",
                 startedAtUTC: state == .queued ? nil : "2031-05-02T19:20:01.000Z", finishedAtUTC: nil, stages: stages,
                 solutionId: nil, error: nil)
    }

    private func stage(_ name: SolveJob.Stage, _ unit: SolveJob.Unit, _ done: Int, _ total: Int?) -> SolveJob.StageProgress {
        SolveJob.StageProgress(stage: name, unit: unit, done: done, total: total)
    }

    /// The result's second row: the observed then the predicted count, a class with none left out, as the web's
    /// evidenceCounts filters its legend (revealStatus.ts); the design lane saw "0" beside Predicted on a calibrated result.
    func testTheResultsSecondRowLeavesOutAClassWithNone() {
        let row = { (counts: [EvidenceClass: Int]) in ResultCounts.secondRow(counts).map { "\($0.0.rawValue) \($0.1)" } }
        XCTAssertEqual(row([.catalogMatched: 39, .observed: 3919, .predicted: 0]), ["observed 3919"])
        XCTAssertEqual(row([.observed: 12, .predicted: 471]), ["observed 12", "predicted 471"])
        XCTAssertEqual(row([.observed: 0, .predicted: 2]), ["predicted 2"])
        XCTAssertEqual(row([:]), [])
        XCTAssertEqual(row([.simulated: 4, .catalogMatched: 9]), [], "only the observed and predicted classes stand in that row")
    }

    func testTheStepWordsAreTheLaptopsApprovedOnes() {
        XCTAssertEqual(SolveJob.Stage.allCases.map(WaitRow.word), ["Saved", "Opened", "Measured", "Matching", "Checking"])
    }

    /// D32 (UIUX-iphone-queued-READY): while the job waits its turn, "Queued" in the step name's place and "{n} ahead"
    /// in the count's, n the job's queueAhead; the name alone when none is ahead or none is said; no row once it runs.
    func testAQueuedJobShowsQueuedAndHowManyAreAhead() {
        var queued = job(.queued, [])
        queued.queueAhead = 3
        XCTAssertEqual(QueuedRow.current(queued), QueuedRow(ahead: 3))
        XCTAssertEqual(QueuedRow.current(queued)?.aheadWords, "3 ahead")
        XCTAssertEqual(QueuedRow.word, "Queued")
        queued.queueAhead = 1
        XCTAssertEqual(QueuedRow.current(queued)?.aheadWords, "1 ahead")
        queued.queueAhead = 1_204
        XCTAssertEqual(QueuedRow.current(queued)?.aheadWords, "1204 ahead", "as the web writes it, no grouping")
        queued.queueAhead = 0
        XCTAssertNotNil(QueuedRow.current(queued))
        XCTAssertNil(QueuedRow.current(queued)?.aheadWords)
        queued.queueAhead = nil
        XCTAssertNotNil(QueuedRow.current(queued))
        XCTAssertNil(QueuedRow.current(queued)?.aheadWords)
        XCTAssertNil(WaitRow.current(queued), "no step while queued")
        for state in SolveJob.State.allCases where state != .queued {
            var other = job(state, [])
            other.queueAhead = 2
            XCTAssertNil(QueuedRow.current(other), state.rawValue)
        }
    }

    func testTheRowIsTheStepBeingWorkedWithItsOwnCount() {
        let store = stage(.store, .bytes, 2_382_523, 2_382_523)
        // The last stage that reported, while its count is not full
        XCTAssertEqual(WaitRow.current(job(.running, [store, stage(.decode, .frames, 12, 36)])),
                       WaitRow(stage: .decode, count: .of(done: 12, total: 36)))
        // The stage after the last, when the last is full and has not been followed yet: its name alone
        XCTAssertEqual(WaitRow.current(job(.running, [store])), WaitRow(stage: .decode, count: .none))
        XCTAssertEqual(WaitRow.current(job(.running, [store, stage(.decode, .frames, 1, 1), stage(.detect, .frames, 1, 1),
                                                      stage(.solve, .candidates, 2080, 4160)])),
                       WaitRow(stage: .solve, count: .of(done: 2080, total: 4160)))
        // A total not known: the seconds passed in the count's place
        XCTAssertEqual(WaitRow.current(job(.running, [store, stage(.decode, .frames, 1, 1), stage(.detect, .frames, 0, nil)])),
                       WaitRow(stage: .detect, count: .secondsPassed))
        // A count of one: the name alone
        XCTAssertEqual(WaitRow.current(job(.running, [store, stage(.decode, .frames, 0, 1)])), WaitRow(stage: .decode, count: .none))
        // A count in bytes is not a step count: the name alone
        XCTAssertEqual(WaitRow.current(job(.running, [stage(.store, .bytes, 10, 20)])), WaitRow(stage: .store, count: .none))
    }

    func testNoRowBeforeTheJobRunsOrOnceItHasEnded() {
        XCTAssertNil(WaitRow.current(job(.queued, [stage(.store, .bytes, 10, 10)])))
        XCTAssertNil(WaitRow.current(job(.succeeded, [stage(.store, .bytes, 10, 10)])))
        XCTAssertNil(WaitRow.current(job(.failed, [stage(.store, .bytes, 10, 10)])))
        XCTAssertNil(WaitRow.current(job(.canceled, [])))
        // Every stage full on a running job: nothing is being worked that the app knows of
        let all = SolveJob.Stage.allCases.map { stage($0, .steps, 1, 1) }
        XCTAssertNil(WaitRow.current(job(.running, all)))
    }

    func testTheCountIsWrittenAsTheWorkingScreenWritesIt() {
        XCTAssertEqual(WaitRow.Count.of(done: 12, total: 364).words(secondsPassed: 0), "12 of 364")
        XCTAssertEqual(WaitRow.Count.of(done: 2080, total: 4160).words(secondsPassed: 0), "2,080 of 4,160")
        XCTAssertEqual(WaitRow.Count.of(done: 2080, total: 4160).fraction, 0.5)
        XCTAssertEqual(WaitRow.Count.secondsPassed.words(secondsPassed: 12.9), "12 s")
        XCTAssertNil(WaitRow.Count.none.words(secondsPassed: 5))
        XCTAssertNil(WaitRow.Count.none.fraction)
        XCTAssertNil(WaitRow.Count.secondsPassed.fraction)
    }

    // MARK: The sentence of a result that is not calibrated

    private func readout(_ status: SolutionStatus, detected: Int? = nil, pose: SolutionReadout.Pose? = nil, wcs: Bool = false,
                         frameTimeSec: Double = 0) -> SolutionReadout {
        SolutionReadout(status: status, counts: [:], marks: [], detectedCandidateCount: detected, matchedInlierCount: nil, pose: pose,
                        hasWCS: wcs, frameTimeSec: frameTimeSec)
    }

    private let photo = AssetFacts(isVideo: false, capturedAtUTC: "2031-05-02T03:42:18.120Z", hasPlace: true)

    func testAPhotoWhosePointsOfLightWereNotMatchedSaysHowManyWithAComma() {
        XCTAssertEqual(OutcomeSentence.sentence(readout(.predictedOnly, detected: 31_371), photo),
                       "Found 31,371 points of light, but could not match them to stars for sure.")
        XCTAssertEqual(OutcomeSentence.sentence(readout(.predictedOnly, detected: 5), photo), "Too few points of light to match to stars.")
        XCTAssertEqual(OutcomeSentence.sentence(readout(.predictedOnly, detected: 6), photo),
                       "Found 6 points of light, but could not match them to stars for sure.")
        XCTAssertEqual(OutcomeSentence.sentence(readout(.predictedOnly), photo), "The points of light could not be matched to stars.")
    }

    func testALinedUpSkySaysWhatItTookToBeSoAndHowFarOffItMayBe() {
        let pose = SolutionReadout.Pose(method: "moon_camera_profile_level_head", maxCenterOffsetDeg: 0.4, beaconCentroidUncertaintyDeg: 0.004)
        XCTAssertEqual(OutcomeSentence.sentence(readout(.approximate, pose: pose, wcs: true), photo),
                       "Lined up by taking a bright spot to be the Moon, with the camera's field of view and a level head, within about 1 degree; not checked against stars.")
        let wide = SolutionReadout.Pose(method: "moon_horizon", maxCenterOffsetDeg: 2.5, beaconCentroidUncertaintyDeg: 0.6)
        XCTAssertEqual(OutcomeSentence.sentence(readout(.approximate, pose: wide, wcs: true), photo),
                       "Lined up by taking a bright spot to be the Moon, with the skyline taken to be level, within about 4 degrees; not checked against stars.")
        // pointing-3: the phone's own attitude, no beacon and so no beacon uncertainty, in C17's words (decided 12:47, item 7)
        let phone = SolutionReadout.Pose(method: "device_attitude", maxCenterOffsetDeg: 5.2, beaconCentroidUncertaintyDeg: nil)
        XCTAssertEqual(OutcomeSentence.sentence(readout(.approximate, pose: phone, wcs: true), photo),
                       "Lined up by where your iPhone pointed, within about 6 degrees; not checked against stars.")
        let sun = SolutionReadout.Pose(method: "sun_horizon", maxCenterOffsetDeg: 1.2, beaconCentroidUncertaintyDeg: 0.3)
        XCTAssertEqual(OutcomeSentence.sentence(readout(.approximate, pose: sun, wcs: true), photo),
                       "Lined up by taking a bright spot to be the Sun, with the skyline taken to be level, within about 2 degrees; not checked against stars.")
        // Unknown pose methods never fabricate a Moon or Sun claim (verdicts/mobile-8.md)
        let unknown = SolutionReadout.Pose(method: "compass_heading", maxCenterOffsetDeg: 4.0, beaconCentroidUncertaintyDeg: nil)
        XCTAssertEqual(OutcomeSentence.sentence(readout(.approximate, pose: unknown, wcs: true), photo),
                       "Lined up roughly, not checked against stars. How far off it may be is not known, so the sky is not drawn over your photo.")
        XCTAssertEqual(OutcomeSentence.sentence(readout(.approximate, wcs: true), photo),
                       "Lined up roughly, not checked against stars. How far off it may be is not known, so the sky is not drawn over your photo.")
        XCTAssertEqual(OutcomeSentence.sentence(readout(.approximate, pose: pose, wcs: false), photo),
                       "You can open the sky predicted for your photo's time and place.")
    }

    func testNoSkySaysWhyInTheOrderTheServerChecks() {
        XCTAssertEqual(OutcomeSentence.sentence(readout(.unresolved), AssetFacts(isVideo: false, capturedAtUTC: nil, hasPlace: false)),
                       "Your photo does not say when or where it was taken, so its sky cannot be shown.")
        XCTAssertEqual(OutcomeSentence.sentence(readout(.ambiguous), AssetFacts(isVideo: false, capturedAtUTC: nil, hasPlace: true)),
                       "Your photo does not say when it was taken, so its sky cannot be shown.")
        XCTAssertEqual(OutcomeSentence.sentence(readout(.unresolved), AssetFacts(isVideo: false, capturedAtUTC: "2031-05-02T03:42:18Z", hasPlace: false)),
                       "Your photo does not say where it was taken, so its sky cannot be shown.")
        XCTAssertEqual(OutcomeSentence.sentence(readout(.unresolved), AssetFacts(isVideo: false, capturedAtUTC: "2051-01-01T00:00:00Z", hasPlace: true)),
                       "Your photo was taken outside the years 1800 to 2050, the only years its sky can be worked out for, so its sky cannot be shown.")
        XCTAssertEqual(OutcomeSentence.sentence(readout(.unresolved), AssetFacts(isVideo: false, capturedAtUTC: "2050-12-31T23:59:59.999Z", hasPlace: true)),
                       "No sky could be worked out for your photo.")
        XCTAssertEqual(OutcomeSentence.sentence(readout(.unresolved), AssetFacts(isVideo: false, capturedAtUTC: "1799-12-31T23:59:59Z", hasPlace: true)),
                       "Your photo was taken outside the years 1800 to 2050, the only years its sky can be worked out for, so its sky cannot be shown.")
        XCTAssertEqual(OutcomeSentence.sentence(readout(.pending), photo), "The sky for your photo has not been worked out yet.")
    }

    func testACalibratedResultHasNoSentence() {
        XCTAssertNil(OutcomeSentence.sentence(readout(.calibrated, detected: 40), photo))
    }

    /// "The counts the result has, each with its tag (observed, predicted)", and no zero count.
    func testTheResultsCountsAreTheObservedAndPredictedItHas() {
        let r = SolutionReadout(status: .predictedOnly, counts: [.observed: 31_371, .predicted: 471, .catalogMatched: 0, .simulated: 2], marks: [],
                                detectedCandidateCount: nil, matchedInlierCount: nil, pose: nil, hasWCS: false, frameTimeSec: 0)
        XCTAssertEqual(OutcomeSentence.countsShown(r).map(\.0), [.observed, .predicted])
        let none = SolutionReadout(status: .unresolved, counts: [.observed: 0, .predicted: 3], marks: [], detectedCandidateCount: nil,
                                   matchedInlierCount: nil, pose: nil, hasWCS: false, frameTimeSec: 0)
        XCTAssertEqual(OutcomeSentence.countsShown(none).map(\.0), [.predicted])
    }

    func testAnInstantIsReadBackAsTheInstantItNames() throws {
        XCTAssertEqual(UTCInstant.date("1970-01-01T00:00:00Z")?.timeIntervalSince1970, 0)
        XCTAssertEqual(UTCInstant.date("2000-03-01T00:00:00Z")?.timeIntervalSince1970, 951_868_800)
        XCTAssertEqual(UTCInstant.date("2000-02-29T23:59:59.999Z")?.timeIntervalSince1970, 951_868_799.999)
        XCTAssertEqual(UTCInstant.date("1800-01-01T00:00:00Z")?.timeIntervalSince1970, -5_364_662_400)
        XCTAssertNil(UTCInstant.date("2031-02-29T00:00:00Z"), "not a real day")
        XCTAssertNil(UTCInstant.date("2031-05-02T03:42:18+00:00"))
        var generator = SeededGenerator(seed: propertySeed())
        for _ in 0..<2000 {
            let ms = Double(generator.int(in: -62_135_596_800_000...253_402_300_799_999))
            let date = Date(timeIntervalSince1970: ms / 1000)
            let text = try XCTUnwrap(UTCInstant.string(from: date))
            // The writer rounds down after dividing in floating point, so it may write the millisecond before
            let back = (try XCTUnwrap(UTCInstant.date(text)).timeIntervalSince1970 * 1000).rounded()
            XCTAssertLessThanOrEqual(abs(back - ms), 1, text)
        }
    }

    // MARK: The readers carry what the sentence needs

    func testTheSolutionReaderKeepsTheCountsAndPoseTheSentenceReads() throws {
        let valid = try XCTUnwrap(try ContractFixture.load("observation_solution_contract")["valid"]?.objectValue)
        let posed = try XCTUnwrap(valid["approximate_with_pose"])
        let readout = try SolutionReader.read(posed, sha256: try XCTUnwrap(posed["sourceSha256"]?.stringValue)).get()
        XCTAssertEqual(readout.pose, SolutionReadout.Pose(method: "moon_camera_profile_level_head", maxCenterOffsetDeg: 0.4,
                                                         beaconCentroidUncertaintyDeg: 0.004))
        XCTAssertTrue(readout.hasWCS)
        let predicted = try XCTUnwrap(valid["predicted_only"])
        let p = try SolutionReader.read(predicted, sha256: try XCTUnwrap(predicted["sourceSha256"]?.stringValue)).get()
        XCTAssertEqual(p.detectedCandidateCount, 1)
        XCTAssertFalse(p.hasWCS)
        XCTAssertNil(p.pose)
    }

    func testTheReceiptReaderKeepsTheFactsTheSentenceReads() throws {
        let samples = try XCTUnwrap(try ContractFixture.load("ingest_contract")["valid"]?["IngestReceipt"]?.arrayValue)
        let first = try XCTUnwrap(samples.first)
        let receipt = try XCTUnwrap(StoredReceiptReader.read(first, sha256: try XCTUnwrap(first["sourceAsset"]?["sha256"]?.stringValue)))
        XCTAssertEqual(receipt.facts.capturedAtUTC, first["sourceAsset"]?["capturedAtUTC"]?.stringValue)
        XCTAssertEqual(receipt.facts.hasPlace, first["sourceAsset"]?["observerLocation"] != nil)
        XCTAssertEqual(receipt.facts.isVideo, first["sourceAsset"]?["mediaKind"]?.stringValue == "video")
    }
}
