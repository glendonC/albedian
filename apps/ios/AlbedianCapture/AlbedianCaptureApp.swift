import SwiftUI

/// The capture companion. Two plain screens in the system's default style, while the approved screens wait their
/// units: the pipeline run by hand with sample values (mobile-0), and the sensors and camera check (mobile-2).
@main
struct AlbedianCaptureApp: App {
    #if DEBUG
    @State private var samplePath: [Int] = ProcessInfo.processInfo.arguments.contains("-AlbedianOpenCheck") ? [0] : []
    #endif

    var body: some Scene {
        WindowGroup {
            #if DEBUG
            NavigationStack(path: $samplePath) {
                rootList.navigationDestination(for: Int.self) { _ in CaptureCheckScreen() }
            }
            #else
            NavigationStack { rootList }
            #endif
        }
    }

    private var rootList: some View {
        List {
            NavigationLink("Pipeline with sample values") { PipelineScreen() }
            NavigationLink("Sensors and camera") { CaptureCheckScreen() }
        }
        .navigationTitle("Albedian Capture")
    }
}
