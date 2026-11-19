import AlbedianCaptureCore
import SwiftUI

/// The laptop's solve on the plain pipeline screen, in the system's default style and the contract's own words: the
/// job's state and each stage's count as the laptop wrote them, elapsed seconds only while a stage's total is not
/// known, then the solution's status and its findings counted by evidence class, as the server reports them
/// (REQ-IOS-4.2, 5.1). The approved result screen is unit mobile-6.
struct SolveSection: View {
    let state: SolveState
    let startedAt: ContinuousClock.Instant?

    var body: some View {
        Section("Solve") {
            switch state {
            case .starting:
                LabeledContent("Job", value: "Not read yet")
                elapsed
            case .running(let job):
                LabeledContent("Job", value: job.state.rawValue)
                ForEach(job.stages, id: \.stage) { stage in
                    LabeledContent(stage.stage.rawValue, value: count(stage))
                    if let total = stage.total { ProgressView(value: Double(stage.done), total: Double(total)) }
                }
                if job.stages.contains(where: { $0.total == nil }) { elapsed }
            case .solved(let job, let readout, _):
                LabeledContent("Job", value: job.state.rawValue)
                LabeledContent("Status", value: readout.status.rawValue)
                ForEach(EvidenceClass.allCases, id: \.self) { evidence in
                    LabeledContent(evidence.rawValue, value: (readout.counts[evidence] ?? 0).formatted())
                }
            case .failed(.jobDidNotSucceed(let job)):
                LabeledContent("Job", value: job.state.rawValue)
                if let error = job.error { LabeledContent("Reason", value: error) }
            case .failed(let failure):
                LabeledContent("Not solved", value: describe(failure))
            }
        }
    }

    private var elapsed: some View {
        TimelineView(.periodic(from: .now, by: 1)) { _ in
            if let startedAt {
                let seconds = Int((ContinuousClock.now - startedAt).components.seconds)
                LabeledContent("Elapsed", value: "\(seconds) s")
            }
        }
    }

    private func count(_ stage: SolveJob.StageProgress) -> String {
        let unit = stage.unit.rawValue
        guard let total = stage.total else { return "\(stage.done.formatted()) \(unit), total not known" }
        return "\(stage.done.formatted()) of \(total.formatted()) \(unit)"
    }

    private func describe(_ failure: SolveFailure) -> String {
        switch failure {
        case .notPermitted: "Not sent: not a request this app sends"
        case .networkError(let code): "Network error" + (code.map { " (error \($0))" } ?? ", cause not known")
        case .timedOut: "The laptop did not answer in time"
        case .connectionClosed: "The connection closed before the laptop answered"
        case .notThePairedPeer: "Not sent: the connection reached another address than the laptop's"
        case .refused(let status, let detail): "The laptop refused: \(status)" + (detail.map { ", \($0)" } ?? "")
        case .redirected(let status): "The laptop's address answered with a redirect (\(status))"
        case .unreadableReply: "The laptop's answer could not be read"
        case .jobForAnotherPhoto: "The laptop answered with another photo's job"
        case .jobDidNotSucceed(let job): job.state.rawValue
        }
    }
}
