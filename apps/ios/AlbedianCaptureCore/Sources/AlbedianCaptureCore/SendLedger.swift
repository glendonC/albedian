import Foundation

/// Each kept capture's send state, kept on the phone with its reason (task 9, unit mobile-3p), so it survives the app
/// closing: one small JSON file per photo, named by the photo's SHA-256, in a folder of its own beside the store's.
/// It holds the hash, "sent" or "not_sent", and for not sent the failure's name; never a time, a place, the address,
/// the laptop's words or any value from the photo or its record.
public final class SendLedger: Sendable {
    public enum State: Equatable, Sendable {
        /// The laptop's receipt named this photo: it holds it.
        case sent
        /// The last send did not reach that, and the failure's name (`reasonName`).
        case notSent(reason: String)
    }

    private let folder: URL
    private let files: any CaptureFiles

    public init(folder: URL, files: any CaptureFiles = SystemCaptureFiles()) {
        self.folder = folder
        self.files = files
    }

    /// Keeps a send's last state for this photo; a state under way, a hash that is not a contract SHA-256, or a done
    /// send whose receipt names another photo is not kept. A write that fails leaves the earlier state.
    public func record(_ state: UploadState, for sha256: String) {
        guard PhotoHash.isContractHash(sha256) else { return }
        let entry: JSONObject
        switch state {
        case .waiting, .sending: return
        case .done(let stored, _, _):
            guard stored == sha256 else { return }
            entry = JSONObject(["sha256": .string(sha256), "state": .string("sent")])
        case .failed(let failure):
            entry = JSONObject(["sha256": .string(sha256), "state": .string("not_sent"), "reason": .string(Self.reasonName(failure))])
        }
        try? files.makeFolder(folder)
        try? files.write(JSONText.data(.object(entry)), to: url(sha256))
    }

    /// The kept state of this photo, or nil when none is kept or the file is not an entry for it.
    public func state(of sha256: String) -> State? {
        guard PhotoHash.isContractHash(sha256), let data = try? files.read(url(sha256)), let value = try? JSONText.read(data),
              value["sha256"]?.stringValue == sha256 else { return nil }
        switch value["state"]?.stringValue {
        case "sent": return .sent
        case "not_sent": return value["reason"]?.stringValue.map { .notSent(reason: $0) }
        default: return nil
        }
    }

    /// Of these photos, the ones the laptop is not known to hold, in the order given.
    public func notSent(among hashes: [String]) -> [String] { hashes.filter { state(of: $0) != .sent } }

    /// A failure's name, as kept: the case, and the status for a refusal or a redirect.
    public static func reasonName(_ failure: UploadFailure) -> String {
        switch failure {
        case .recordNamesAnotherPhoto: "record_names_another_photo"
        case .recordNotWritable: "record_not_writable"
        case .notPermitted: "not_permitted"
        case .networkError: "network_error"
        case .timedOut: "timed_out"
        case .connectionClosed: "connection_closed"
        case .notThePairedPeer: "not_the_paired_peer"
        case .refused(_, let status, _): "refused_\(status)"
        case .redirected(_, let status): "redirected_\(status)"
        case .unreadableReply: "unreadable_reply"
        case .storedDifferentBytes: "stored_different_bytes"
        }
    }

    private func url(_ sha256: String) -> URL { folder.appendingPathComponent("\(sha256).json") }
}
