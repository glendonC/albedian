import Foundation

/// kept-send-solves-one and kept-solve-retry-one (DECIDED-BY-LEAD 24 Nov 18:28 and 18:54, from MOBILE's kept-walk-m and
/// AUDIT-C's F1): the counted send of the photos kept and never sent (D13). Oldest first, each photo goes as a single Send
/// sends it, unless the laptop already holds it; then its solve is asked for once, without waiting (`SolveClient.start`).
/// A photo counts as done only when the laptop has taken that ask: until then it stays among the photos home counts, and
/// the next counted send asks again, without sending the photo again. The first photo that does not go ends the run: it
/// stays kept, and those after it are not tried. Nothing new on the phone's screens.
public enum KeptSend {
    public enum End: Equatable, Sendable {
        /// Every photo was tried: each one sent or already held, and its solve asked for (taken or not).
        case allTried
        /// This photo did not go, with its upload's last state; it stays kept, and those after it were not tried.
        case stopped(sha256: String, last: UploadState)
    }

    /// - Parameters:
    ///   - queue: the kept photos not yet done, oldest first, by SHA-256.
    ///   - held: whether the laptop already holds a photo (sent before, its solve not yet taken), so it is not sent again.
    ///   - send: sends one photo as a single Send would and gives its upload's last state, or nil when the photo can no
    ///     longer be read from the phone (it is passed over).
    ///   - startSolve: asks the laptop to solve a photo it holds, once, without waiting; true when the laptop took it.
    ///   - progress: after each photo whose solve the laptop took, how many have been done.
    public static func run(_ queue: [String], held: (String) -> Bool, send: (String) async -> UploadState?,
                           startSolve: (String) async -> Bool, progress: (Int) -> Void,
                           isolation: isolated (any Actor)? = #isolation) async -> End {
        var done = 0
        for sha256 in queue {
            if !held(sha256) {
                guard let last = await send(sha256) else { continue }
                guard case .solve? = SendEnd.after(last) else { return .stopped(sha256: sha256, last: last) }
            }
            if await startSolve(sha256) {
                done += 1
                progress(done)
            }
        }
        return .allTried
    }
}
