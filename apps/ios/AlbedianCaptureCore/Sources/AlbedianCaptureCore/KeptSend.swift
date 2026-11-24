import Foundation

/// kept-send-solves-one (DECIDED-BY-LEAD 24 Nov 18:28, from MOBILE's kept-walk-m): the counted send of the photos kept and
/// never sent (D13). Oldest first, each photo goes as a single Send sends it; once the laptop holds it, its solve is asked
/// for once, without waiting for it (`SolveClient.start`), so the results wait on the laptop where Open leads. The first
/// photo that does not go ends the run: it stays kept, and the ones after it are not tried. Nothing new on the screens.
public enum KeptSend {
    public enum End: Equatable, Sendable {
        /// Every photo went, and each one's solve was asked for.
        case allSent
        /// This photo did not go, with its upload's last state; it stays kept, and those after it were not tried.
        case stopped(sha256: String, last: UploadState)
    }

    /// - Parameters:
    ///   - queue: the kept photos never sent, oldest first, by SHA-256.
    ///   - send: sends one photo as a single Send would and gives its upload's last state, or nil when the photo can no
    ///     longer be read from the phone (it is passed over).
    ///   - startSolve: asks the laptop to solve a photo it now holds, once, without waiting for the solve.
    ///   - progress: after each photo that went, how many have gone.
    public static func run(_ queue: [String], send: (String) async -> UploadState?, startSolve: (String) async -> Void,
                           progress: (Int) -> Void, isolation: isolated (any Actor)? = #isolation) async -> End {
        var gone = 0
        for sha256 in queue {
            guard let last = await send(sha256) else { continue }
            guard case .solve(let held)? = SendEnd.after(last) else { return .stopped(sha256: sha256, last: last) }
            await startSolve(held)
            gone += 1
            progress(gone)
        }
        return .allSent
    }
}
