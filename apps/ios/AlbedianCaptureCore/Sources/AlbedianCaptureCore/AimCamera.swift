import Foundation

/// The aiming view's camera as the person meets it (UIUX-iphone-cameraunusable-READY, DECIDED-BY-LEAD 24 Nov 15:34, beside
/// D8): live; refused (D8's camera state with Open Settings); or allowed but not usable, D8's camera state with "Try
/// again" where the capture button sits, and the grey line only when another app or a call may hold the camera.
public enum AimCamera: Equatable, Sendable {
    case live
    case refused
    /// Allowed but the session could not be set up (`CameraSession.Failure`); `inUse` for `cameraNotUsable`, when another
    /// app or a call may hold the camera, and not for `noCamera`. "Try again" sets it up again.
    case unusable(inUse: Bool)
    /// The running session was interrupted (Apple's `AVCaptureSession.InterruptionReason`); `inUse` when another app or a
    /// call holds the camera. It comes back by itself when the system ends the interruption.
    case interrupted(inUse: Bool)

    /// D8's words for a camera Albedian cannot use.
    public static let title = "Albedian can't use the camera"
    /// The record's new grey line, for a camera another app may be using.
    public static let inUseLine = "Another app may be using it."

    /// The grey line under the title, or none.
    public var detail: String? {
        switch self {
        case .unusable(inUse: true), .interrupted(inUse: true): Self.inUseLine
        default: nil
        }
    }

    /// Whether D8's camera state with "Try again" stands in place of the preview.
    public var showsTryAgain: Bool {
        switch self {
        case .unusable, .interrupted: true
        case .live, .refused: false
        }
    }

    /// The capture button shows only while the camera can shoot, so nothing looks live that is not.
    public var showsCaptureButton: Bool { self == .live }

    /// The preview, its Predicted tag and its rings show only over a live camera (no sky drawn over nothing).
    public var showsSky: Bool { self == .live }
}

/// What the camera reported, as the aim view's camera reads it.
public enum CameraEvent: Equatable, Sendable {
    /// The person refused the camera, or it is restricted.
    case refused
    /// The session took the back camera.
    case configured
    /// This device has no back camera (`CameraSession.Failure.noCamera`).
    case noCamera
    /// The session could not take the camera (`CameraSession.Failure.cameraNotUsable`).
    case notUsable
    /// The running session was interrupted because another app or a call holds the camera, or several apps share the
    /// screen (`videoDeviceInUseByAnotherClient`, `videoDeviceNotAvailableWithMultipleForegroundApps`).
    case interruptedInUse
    /// The running session was interrupted because the system needs the camera to rest
    /// (`videoDeviceNotAvailableDueToSystemPressure`).
    case interruptedBySystemPressure
    /// The system ended the interruption: the camera comes back by itself.
    case interruptionEnded
}

extension AimCamera {
    /// The state after an event. A refusal stands until the app opens again (changing the permission in Settings ends
    /// the app). An interruption only takes a live camera; its end gives back a camera only an interruption took.
    public func after(_ event: CameraEvent) -> AimCamera {
        if self == .refused { return .refused }
        switch event {
        case .refused: return .refused
        case .configured: return .live
        case .noCamera: return .unusable(inUse: false)
        case .notUsable: return .unusable(inUse: true)
        case .interruptedInUse: return self == .live ? .interrupted(inUse: true) : self
        case .interruptedBySystemPressure: return self == .live ? .interrupted(inUse: false) : self
        case .interruptionEnded:
            if case .interrupted = self { return .live }
            return self
        }
    }
}

/// A refused photo's sentence on the phone, by the owner's approved rule for the same refusal on the laptop
/// (`UIUX-refused-APPROVED.md` line 2, `apps/web/src/components/homeRefusal.ts` `refusalSentence`): the laptop's own
/// reason, its first letter capital and a full stop added unless it already ends a sentence; none when it gave none.
public enum RefusalSentence {
    public static func of(_ detail: String?) -> String? {
        let words = (detail ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard let first = words.first else { return nil }
        let capital = first.uppercased() + words.dropFirst()
        return capital.hasSuffix(".") || capital.hasSuffix("!") || capital.hasSuffix("?") || capital.hasSuffix("\u{2026}") ? capital : capital + "."
    }
}
