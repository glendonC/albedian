import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// camera-unusable-m1 (UIUX-iphone-cameraunusable-READY, DECIDED-BY-LEAD 24 Nov 15:34, from MOBILE's walk-m): a camera that
/// is allowed but cannot be set up, or is taken by another app, shows D8's camera state with "Try again" and no capture
/// button, and comes back by itself when the system ends the interruption; no place line while the person has not yet
/// answered the location question; a refused photo's reason as a sentence by the owner's approved rule.
final class CameraUnusableTests: XCTestCase {
    private let all: [CameraEvent] = [.refused, .configured, .noCamera, .notUsable, .interruptedInUse, .interruptedBySystemPressure,
                                      .interruptionEnded]

    func testEachCameraReportLeavesTheStateTheRecordNames() {
        // From a live camera
        XCTAssertEqual(AimCamera.live.after(.configured), .live)
        XCTAssertEqual(AimCamera.live.after(.noCamera), .unusable(inUse: false))
        XCTAssertEqual(AimCamera.live.after(.notUsable), .unusable(inUse: true))
        XCTAssertEqual(AimCamera.live.after(.interruptedInUse), .interrupted(inUse: true))
        XCTAssertEqual(AimCamera.live.after(.interruptedBySystemPressure), .interrupted(inUse: false))
        XCTAssertEqual(AimCamera.live.after(.interruptionEnded), .live)
        XCTAssertEqual(AimCamera.live.after(.refused), .refused)
        // An interruption comes back by itself; a camera that could not be set up waits for "Try again"
        XCTAssertEqual(AimCamera.interrupted(inUse: true).after(.interruptionEnded), .live)
        XCTAssertEqual(AimCamera.interrupted(inUse: false).after(.interruptionEnded), .live)
        XCTAssertEqual(AimCamera.unusable(inUse: true).after(.interruptionEnded), .unusable(inUse: true))
        XCTAssertEqual(AimCamera.unusable(inUse: false).after(.interruptionEnded), .unusable(inUse: false))
        XCTAssertEqual(AimCamera.unusable(inUse: true).after(.configured), .live, "Try again set it up")
        XCTAssertEqual(AimCamera.unusable(inUse: false).after(.notUsable), .unusable(inUse: true))
        // A second interruption keeps the first reason; one does not take a camera that is not live
        XCTAssertEqual(AimCamera.interrupted(inUse: true).after(.interruptedBySystemPressure), .interrupted(inUse: true))
        XCTAssertEqual(AimCamera.unusable(inUse: false).after(.interruptedInUse), .unusable(inUse: false))
        // A refusal stands: changing the permission in Settings ends the app
        for event in all { XCTAssertEqual(AimCamera.refused.after(event), .refused, "\(event)") }
    }

    /// Every sequence of up to five reports from a live camera, against an oracle written from the record's words: a
    /// refusal stands; the last set up says whether the camera could be taken; an interruption takes only a camera that
    /// was set up and is not already interrupted, and its end gives it back.
    func testEverySequenceOfReportsMatchesTheRecord() {
        func oracle(_ events: [CameraEvent]) -> AimCamera {
            var refused = false
            var setUp: CameraEvent = .configured
            var interruption: Bool?
            for event in events {
                switch event {
                case .refused: refused = true
                case .configured, .noCamera, .notUsable: setUp = event; interruption = nil
                case .interruptedInUse: if setUp == .configured, interruption == nil { interruption = true }
                case .interruptedBySystemPressure: if setUp == .configured, interruption == nil { interruption = false }
                case .interruptionEnded: interruption = nil
                }
            }
            if refused { return .refused }
            if setUp == .noCamera { return .unusable(inUse: false) }
            if setUp == .notUsable { return .unusable(inUse: true) }
            if let interruption { return .interrupted(inUse: interruption) }
            return .live
        }
        var sequences: [[CameraEvent]] = [[]]
        var checked = 0
        for _ in 0..<5 {
            sequences = sequences.flatMap { prefix in all.map { prefix + [$0] } }
            for events in sequences {
                XCTAssertEqual(events.reduce(AimCamera.live) { $0.after($1) }, oracle(events), "\(events)")
                checked += 1
            }
        }
        XCTAssertEqual(checked, 7 + 49 + 343 + 2401 + 16807)
    }

    func testTheWordsAndWhatShowsForEachState() {
        XCTAssertEqual(AimCamera.title, "Albedian can't use the camera")
        XCTAssertEqual(AimCamera.unusable(inUse: true).detail, "Another app may be using it.")
        XCTAssertEqual(AimCamera.interrupted(inUse: true).detail, "Another app may be using it.")
        for state in [AimCamera.live, .refused, .unusable(inUse: false), .interrupted(inUse: false)] { XCTAssertNil(state.detail, "\(state)") }
        let shows: [(AimCamera, Bool, Bool)] = [(.live, true, false), (.refused, false, false), (.unusable(inUse: true), false, true),
                                                (.unusable(inUse: false), false, true), (.interrupted(inUse: true), false, true),
                                                (.interrupted(inUse: false), false, true)]
        for (state, live, tryAgain) in shows {
            XCTAssertEqual(state.showsCaptureButton, live, "\(state)")
            XCTAssertEqual(state.showsSky, live, "\(state)")
            XCTAssertEqual(state.showsTryAgain, tryAgain, "\(state)")
        }
    }

    /// No line under the preview while the person has not answered whether Albedian may use the location (not reported
    /// yet, or not determined); after a no, D8's "No place: location not allowed".
    @MainActor
    func testNoPlaceLineWhileTheLocationQuestionIsUnanswered() throws {
        let motion = SensorRecorderTests.StandInMotion(Set(AttitudeFrame.allCases))
        let place = SensorRecorderTests.StandInPlace()
        let recorder = try XCTUnwrap(SensorRecorder(motion: motion, place: place))
        _ = recorder.start(interface: .portrait)
        let deliver = try XCTUnwrap(place.deliver)
        let moment = SampleCapture.moment
        XCTAssertNil(recorder.aimingLine(at: moment), "nothing reported yet")
        XCTAssertNil(recorder.aimingNow(at: moment).line)
        deliver(.authorization(.notDetermined, .full))
        XCTAssertNil(recorder.aimingLine(at: moment), "the question is up")
        XCTAssertNil(recorder.aimingNow(at: moment).line)
        XCTAssertNil(recorder.aimingNow(at: moment).sky)
        deliver(.authorization(.denied, .full))
        XCTAssertEqual(recorder.aimingLine(at: moment), "No place: location not allowed")
        XCTAssertEqual(recorder.aimingNow(at: moment).line, "No place: location not allowed")
        deliver(.authorization(.restricted, .full))
        XCTAssertEqual(recorder.aimingLine(at: moment), "No place: location not allowed")
        // The record still says not allowed while the question is up: a photo taken then has no place
        deliver(.authorization(.notDetermined, .full))
        XCTAssertEqual(SensorMapping.location(authorization: .notDetermined, accuracy: .full, fix: nil), .notAllowed)
    }

    /// The web's own rows for the same rule (apps/web/src/components/__tests__/homeRefusal.test.tsx).
    func testARefusedPhotosReasonIsASentenceByTheApprovedRule() {
        let rows: [(String?, String?)] = [
            ("not a JPEG, PNG, TIFF, HEIC, QuickTime or MP4 file", "Not a JPEG, PNG, TIFF, HEIC, QuickTime or MP4 file."),
            ("the upload is empty", "The upload is empty."),
            ("larger than the 1048576-byte limit for a still", "Larger than the 1048576-byte limit for a still."),
            ("declared as image/png, but the bytes are video/mp4", "Declared as image/png, but the bytes are video/mp4."),
            ("Already a sentence.", "Already a sentence."),
            ("is this a photo?", "Is this a photo?"),
            ("too big!", "Too big!"),
            ("  padded reason  ", "Padded reason."),
            ("3 files at once", "3 files at once."),
            ("x", "X."),
            ("the upload is rrr\u{2026}", "The upload is rrr\u{2026}"),
            ("", nil),
            ("   ", nil),
            (nil, nil),
        ]
        for (detail, sentence) in rows { XCTAssertEqual(RefusalSentence.of(detail), sentence, "\(String(describing: detail))") }
    }

    /// Every refusal reason the laptop's store can give becomes one sentence: its first letter capital, one full stop,
    /// nothing else changed (services/ingest/store.py's own words, read as the web's test reads them).
    func testEveryReasonTheStoreGivesBecomesOneSentence() throws {
        let store = try String(contentsOf: repositoryRoot.appendingPathComponent("services/ingest/store.py"), encoding: .utf8)
        let pattern = try NSRegularExpression(pattern: #"Refused\(\s*\d{3},\s*f?"([^"]+)""#)
        let reasons = pattern.matches(in: store, range: NSRange(store.startIndex..., in: store)).compactMap {
            Range($0.range(at: 1), in: store).map { String(store[$0]) }
        }
        XCTAssertGreaterThanOrEqual(reasons.count, 5)
        for reason in reasons {
            let sentence = try XCTUnwrap(RefusalSentence.of(reason), reason)
            XCTAssertEqual(sentence.first.map(String.init), sentence.first.map { String($0).uppercased() }, reason)
            XCTAssertTrue(sentence.hasSuffix(".") && !sentence.hasSuffix(".."), reason)
            XCTAssertEqual(String(sentence.dropFirst().dropLast()), String(reason.dropFirst()), reason)
        }
    }

    /// The app as the record lays it out, read from its source (the app's views do not run here; the simulator captures
    /// show them): the camera state in place of the preview with "Try again" where the capture button sits, the button
    /// shown only on a live camera, the session's interruptions read, the camera set up once, the refused reason as a
    /// sentence on both send paths.
    func testTheAppShowsTheCameraStateAndReadsInterruptions() throws {
        let app = repositoryRoot.appendingPathComponent("apps/ios/AlbedianCapture")
        func raw(_ file: String) throws -> String { try String(contentsOf: app.appendingPathComponent(file), encoding: .utf8) }
        func flat(_ text: String) -> String { text.filter { !$0.isWhitespace } }
        let screens = flat(try raw("Screens/CompanionScreens.swift"))
        let model = flat(try raw("Screens/CompanionModel.swift"))
        let session = flat(try raw("Sensors/CameraSession.swift"))
        let state = try XCTUnwrap(screens.range(of: "}elseifmodel.aimCamera.showsTryAgain{"), "the camera state stands in place of the preview")
        let preview = try XCTUnwrap(screens.range(of: "}else{letwidth=Double(geometry.size.width)CameraPreview("), "the preview only after it")
        XCTAssertLessThan(state.lowerBound, preview.lowerBound)
        let branch = String(screens[state.upperBound..<preview.lowerBound])
        XCTAssertTrue(branch.contains("AlbedianIcon.cameraSlash.image(18).accessibilityHidden(true)Text(AimCamera.title).font(AlbedianFont.regular(17))"))
        XCTAssertTrue(branch.contains("ifletdetail=model.aimCamera.detail{Text(detail).font(AlbedianFont.regular(14))"))
        XCTAssertTrue(branch.contains(".multilineTextAlignment(.center).padding(.horizontal,CGFloat(Arrive.side)).atTop(340)"), "D8's camera state as laid out")
        XCTAssertTrue(branch.contains("MainButton(title:\"Tryagain\",icon:.arrow,action:model.tryCameraAgain).atBottom(Arrive.mainBottomGap)"))
        XCTAssertFalse(branch.contains("CaptureButton("), "no capture button while the camera cannot shoot")
        XCTAssertFalse(branch.contains("EvidenceTag(") || branch.contains("PlacedSkyOverlay("), "no tag and no rings over nothing")
        XCTAssertTrue(model.contains("guard!shooting,cameraProblem==nil,aimCamera.showsCaptureButtonelse{return}"))
        XCTAssertTrue(model.contains("AVCaptureSession.wasInterruptedNotification,object:camera.session"))
        XCTAssertTrue(model.contains("AVCaptureSession.interruptionEndedNotification,object:camera.session"))
        XCTAssertTrue(model.contains("case.videoDeviceInUseByAnotherClient?,.videoDeviceNotAvailableWithMultipleForegroundApps?:.interruptedInUse"))
        XCTAssertTrue(model.contains("case.videoDeviceNotAvailableDueToSystemPressure?:.interruptedBySystemPressure"))
        XCTAssertTrue(model.contains("cameraWatch.forEach(NotificationCenter.default.removeObserver)"), "no watch left after aiming")
        XCTAssertTrue(session.contains("funcconfigure()->Failure?{ifdevice!=nil{returnnil}"), "the camera set up once")
        XCTAssertEqual(model.components(separatedBy: "self.stop(RefusalSentence.of(detail)??detail,").count - 1, 2, "both send paths")
    }
}
