import AlbedianCaptureCore
import SafariServices
import SwiftUI

/// The system's Safari view for a page `SafariViewGate` allowed (mobile-4b): the reveal on the paired laptop, or a
/// credit page. Safari's process makes the request; the person returns with the system's own Done.
struct SafariView: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> SFSafariViewController { SFSafariViewController(url: url) }

    func updateUIViewController(_ controller: SFSafariViewController, context: Context) {}
}

/// A page to show in the Safari view, as a sheet's item.
struct OutsidePageItem: Identifiable {
    let url: URL
    var id: String { url.absoluteString }
}
