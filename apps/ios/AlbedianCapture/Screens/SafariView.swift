import AlbedianCaptureCore
import SafariServices
import SwiftUI

/// The system's Safari view for a page `SafariViewGate` allowed (mobile-4b): the reveal on the paired laptop, or a
/// credit page. Safari's process makes the request; the person returns with the system's own Done.
struct SafariView: UIViewControllerRepresentable {
    let url: URL
    /// Told once, when the page's first load has ended, whether it loaded or not (open-flash-m1).
    var onFirstLoad: () -> Void = {}

    func makeCoordinator() -> Coordinator { Coordinator(onFirstLoad: onFirstLoad) }

    func makeUIViewController(context: Context) -> SFSafariViewController {
        let controller = SFSafariViewController(url: url)
        controller.delegate = context.coordinator
        return controller
    }

    func updateUIViewController(_ controller: SFSafariViewController, context: Context) {}

    final class Coordinator: NSObject, SFSafariViewControllerDelegate {
        let onFirstLoad: () -> Void
        init(onFirstLoad: @escaping () -> Void) { self.onFirstLoad = onFirstLoad }

        func safariViewController(_ controller: SFSafariViewController, didCompleteInitialLoad didLoadSuccessfully: Bool) { onFirstLoad() }
    }
}

/// open-flash-m1 (DECIDED-BY-LEAD 24 Nov 18:32): the Safari view under the app's black until its first load has ended,
/// so the page's first paint, white before its styles arrive, never shows at night; touches pass through the cover to the
/// sheet's close, and the cover leaves at once (no motion: MOTION's record states none).
struct CoveredSafariView: View {
    let url: URL
    @State private var loaded = false

    var body: some View {
        ZStack {
            SafariView(url: url, onFirstLoad: { loaded = true })
            if !loaded { Color.black.ignoresSafeArea().allowsHitTesting(false) }
        }
    }
}

/// A page to show in the Safari view, as a sheet's item.
struct OutsidePageItem: Identifiable {
    let url: URL
    var id: String { url.absoluteString }
}
