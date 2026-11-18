#if DEBUG
import AlbedianCaptureCore
import SwiftUI

/// The approved controls together, for the owner's look in a debug build only (unit mobile-3): the logo, the split
/// main buttons with the words the approved screens use, the icon buttons, all four evidence tags and the capture
/// button, on the black field in the dark appearance. The buttons do nothing.
struct ControlsGallery: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                Logo(size: 52)
                HStack(spacing: 12) {
                    MainButton(title: "Start", icon: .arrow) {}
                    MainButton(title: "Send", icon: .send) {}
                }
                HStack(spacing: 12) {
                    IconButton(icon: .close, label: "Close") {}
                    IconButton(icon: .again, label: "Take again") {}
                }
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(ControlStyle.Evidence.allCases, id: \.word) { EvidenceTag(evidence: $0) }
                }
                CaptureButton(action: {})
                    .frame(maxWidth: .infinity)
            }
            .padding(24)
        }
        .background(Color(ControlStyle.field).ignoresSafeArea())
        .environment(\.colorScheme, .dark)
        .navigationTitle("Controls (debug only)")
        .navigationBarTitleDisplayMode(.inline)
    }
}
#endif
