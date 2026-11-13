import AlbedianCaptureCore
import SwiftUI

/// One plain screen in the system's default style: sample sensor values in, the pointing record out, and an upload
/// to a laptop address the person types. No colour, font, layout or motion of its own; the words are the fewest
/// that keep each value honest (a missing heading or place says why, an unknown field of view says unknown).
/// It shows nothing about the sky: anything later drawn from a record is predicted (REQ-IOS-3).
struct PipelineScreen: View {
    @State private var model = PipelineModel()

    var body: some View {
        NavigationStack {
            Form {
                Section("Sample sensors") {
                    Toggle("True-north frame", isOn: $model.options.trueNorthFrame)
                    Toggle("Location allowed", isOn: $model.options.locationAllowed)
                    Toggle("Place simulated", isOn: $model.options.placeSimulatedBySoftware)
                    Toggle("Heading event", isOn: $model.options.headingArrived)
                    Stepper(value: $model.options.headingAccuracyDeg, in: -1...40, step: 1) {
                        LabeledContent("Heading accuracy", value: degrees(model.options.headingAccuracyDeg))
                    }
                    Toggle("Field of view reported", isOn: $model.options.fieldOfViewReported)
                    Toggle("Motion running", isOn: $model.options.motionRunning)
                    Button("Build record") { model.build() }
                }
                if let record = model.record {
                    Section("Pointing record") {
                        LabeledContent("Heading", value: heading(record.heading))
                        LabeledContent("Place", value: place(record.location))
                        LabeledContent("Field of view", value: fieldOfView(record.camera.fieldOfView))
                        LabeledContent("Evidence", value: "Pointing hint")
                        LabeledContent("Contract", value: model.issues.isEmpty ? "Accepted" : "Refused (\(model.issues.count))")
                        LabeledContent("SHA-256", value: String(record.sourceAssetSha256.prefix(12)) + "\u{2026}")
                        DisclosureGroup("Wire JSON") {
                            Text(model.json).textSelection(.enabled)
                        }
                    }
                } else if let refusal = model.refusal {
                    Section("Pointing record") {
                        LabeledContent("Not built", value: describe(refusal))
                    }
                }
                if model.photo != nil {
                    Section("Send") {
                        TextField("Laptop address", text: $model.address)
                            .keyboardType(.URL)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                        Button("Send") { model.send() }
                            .disabled(model.laptop == nil || model.isSending)
                        if let upload = model.upload {
                            LabeledContent("Status", value: describe(upload))
                            if case .sending(_, let sent, let total) = upload, total > 0 {
                                ProgressView(value: Double(sent), total: Double(total))
                            }
                        }
                    }
                }
            }
            .navigationTitle("Albedian Capture")
        }
    }

    private func degrees(_ value: Double) -> String { value.formatted(.number.precision(.fractionLength(0...2))) + "\u{B0}" }

    private func heading(_ heading: PointingRecord.Heading) -> String {
        switch heading {
        case .measured(let measured):
            "\(degrees(measured.trueHeadingDeg)) \u{B1}\(degrees(measured.headingAccuracyDeg))"
        case .absent(.accuracyOutsideLimit(let reported)):
            "None: \u{B1}\(degrees(reported)) is past \(degrees(PointingContract.headingAccuracyLimitDeg))"
        case .absent(let reason):
            "None: " + reason.name.rawValue.replacingOccurrences(of: "_", with: " ")
        }
    }

    private func place(_ place: PointingRecord.Place) -> String {
        switch place {
        case .allowed(let allowed):
            "Allowed, \u{B1}\(allowed.horizontalAccuracyM.formatted(.number.precision(.fractionLength(0...1)))) m"
        case .absent(let reason):
            "None: " + reason.rawValue.replacingOccurrences(of: "_", with: " ")
        }
    }

    private func fieldOfView(_ fieldOfView: PointingRecord.FieldOfView) -> String {
        switch fieldOfView {
        case .reported(let horizontalDeg, _): degrees(horizontalDeg)
        case .unknown: "Unknown"
        }
    }

    private func describe(_ refusal: PointingBuildError) -> String {
        switch refusal {
        case .noMotionSampleAtThePhoto: "No motion sample at the photo"
        case .gravityOutsideBand: "Gravity is not about 1 g"
        case .attitudeNotUnitQuaternion: "Attitude is not a unit quaternion"
        case .motionWindowEmpty: "No motion sample in the window"
        case .deviceLabelOutsidePattern(let field): "\(field) does not fit its pattern"
        default: String(describing: refusal)
        }
    }

    private func describe(_ state: UploadState) -> String {
        switch state {
        case .waiting:
            "Waiting"
        case .sending(let phase, let sent, let total):
            "Sending the \(phase.rawValue): \(sent.formatted()) of \(total.formatted()) bytes"
        case .failed(.networkError(let phase, let code)):
            "Network error during the \(phase.rawValue)" + (code.map { " (error \($0))" } ?? ", cause not known")
        case .failed(.timedOut(let phase)):
            "The laptop did not answer the \(phase.rawValue) in time"
        case .failed(.connectionClosed(let phase)):
            "The connection closed before the laptop answered the \(phase.rawValue)"
        case .failed(.notThePairedPeer(let phase)):
            "Not sent: the \(phase.rawValue) connection reached another address than the laptop's"
        case .failed(.refused(let phase, let status, let detail)):
            "The laptop refused the \(phase.rawValue): \(status)" + (detail.map { ", \($0)" } ?? "")
        case .failed(.redirected(let phase, let status)):
            "The laptop's address answered the \(phase.rawValue) with a redirect (\(status)); nothing was sent elsewhere"
        case .failed(.notPermitted(let phase)):
            "Not sent: the \(phase.rawValue) request was not one this app sends"
        case .failed(.unreadableReply(let phase)):
            "The laptop's answer to the \(phase.rawValue) could not be read"
        case .failed(.storedDifferentBytes):
            "The laptop stored different bytes"
        case .failed(.recordNamesAnotherPhoto):
            "Not sent: the record names another photo"
        case .failed(.recordNotWritable):
            "Not sent: the record fails the contract"
        case .done(_, let alreadyStored, let recordAccepted):
            "Sent" + (alreadyStored ? ", photo already stored" : "") + (recordAccepted ? "" : ", photo only")
        }
    }
}
