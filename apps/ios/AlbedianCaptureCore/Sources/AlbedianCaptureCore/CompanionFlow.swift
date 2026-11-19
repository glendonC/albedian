import Foundation

/// Where the phone keeps small values of its own (the paired address), so the tests keep them in memory.
public protocol KeptValues: Sendable {
    func string(for key: String) -> String?
    func set(_ value: String?, for key: String)
}

/// The app's own defaults on the phone. `UserDefaults` is thread safe (Apple: "The UserDefaults class is
/// thread-safe"), so the wrapper may be shared.
public struct PhoneKeptValues: KeptValues, @unchecked Sendable {
    private let defaults: UserDefaults
    public init(defaults: UserDefaults = .standard) { self.defaults = defaults }
    public func string(for key: String) -> String? { defaults.string(forKey: key) }
    public func set(_ value: String?, for key: String) {
        if let value { defaults.set(value, forKey: key) } else { defaults.removeObject(forKey: key) }
    }
}

/// The laptop's address, typed once and kept on the phone (UIUX-connect-APPROVED): the person types what follows the
/// fixed "http://", and the address is kept only after a send to it went through, so later sends go straight there.
public final class PairingKeeper: Sendable {
    public static let key = "albedian.laptop-address"
    /// Shown in front of the field, never typed.
    public static let fixedScheme = "http://"

    private let values: any KeptValues

    public init(values: any KeptValues = PhoneKeptValues()) { self.values = values }

    /// The pairing address the typed text makes, or nil when it is not one (`LaptopAddress`: a local network host only).
    public static func address(typed: String) -> LaptopAddress? {
        let text = typed.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        return LaptopAddress(fixedScheme + text)
    }

    /// The kept address, read again as a pairing address each time, so a kept text that no longer is one is not used.
    public var kept: LaptopAddress? { values.string(for: Self.key).flatMap(LaptopAddress.init) }

    public func keep(_ address: LaptopAddress) { values.set(address.description, for: Self.key) }

    public func forget() { values.set(nil, for: Self.key) }
}

/// Where a send's end leads on the approved screens.
public enum SendEnd: Equatable, Sendable {
    /// The laptop holds the photo (and the record, when one was sent): its solve starts and the result follows.
    case solve(sha256: String)
    /// The laptop could not be reached: "Can't reach the laptop", the photo kept on the phone (UIUX-unreachable).
    case unreachable
    /// The laptop was reached and did not take the send, or nothing was sent; no screen is designed for it [open].
    case notSent

    /// The end an upload's last state leads to, or nil while it is under way.
    public static func after(_ state: UploadState) -> SendEnd? {
        switch state {
        case .waiting, .sending: return nil
        case .done(let sha256, _, _): return .solve(sha256: sha256)
        case .failed(let failure):
            switch failure {
            case .networkError, .timedOut, .connectionClosed, .notThePairedPeer: return .unreachable
            case .refused, .redirected, .unreadableReply, .storedDifferentBytes, .recordNamesAnotherPhoto, .recordNotWritable, .notPermitted:
                return .notSent
            }
        }
    }
}

extension SolutionStatus {
    /// The server's status word as the result's title shows it (arrive 6, "Calibrated"): the word itself, its
    /// underscore read as a space and its first letter a capital.
    public var word: String {
        let spaced = rawValue.replacingOccurrences(of: "_", with: " ")
        return spaced.prefix(1).uppercased() + spaced.dropFirst()
    }
}

extension StoredFrame {
    /// Where a detection lands on a `width` by `height` card that the oriented photo covers, centred (the result's
    /// card, `background: center / cover`): at its pixel's centre, half a pixel on.
    public func placeCovering(xPx: Double, yPx: Double, onWidth width: Double, height: Double) -> (x: Double, y: Double) {
        let shown = shownSize
        let shownW = Double(max(1, shown.widthPx))
        let shownH = Double(max(1, shown.heightPx))
        let scale = max(width / shownW, height / shownH)
        let point = orient(xPx: xPx + 0.5, yPx: yPx + 0.5)
        return ((width - shownW * scale) / 2 + point.xPx * scale, (height - shownH * scale) / 2 + point.yPx * scale)
    }

    /// Whether a detection lies on that card once the photo is cut to cover it.
    public func isOnCard(xPx: Double, yPx: Double, width: Double, height: Double) -> Bool {
        let p = placeCovering(xPx: xPx, yPx: yPx, onWidth: width, height: height)
        return p.x >= 0 && p.x <= width && p.y >= 0 && p.y <= height
    }
}
