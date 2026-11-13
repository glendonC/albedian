import SwiftUI

/// The capture companion. In unit mobile-0 it has one plain screen, in the system's default style, for running the
/// core's pipeline by hand in the simulator with sample sensor values; the look waits for the reference board and
/// the owner's yes.
@main
struct AlbedianCaptureApp: App {
    var body: some Scene {
        WindowGroup {
            PipelineScreen()
        }
    }
}
