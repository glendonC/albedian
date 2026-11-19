import SwiftUI

/// The capture companion. Two plain screens in the system's default style, while the approved screens wait their
/// units: the pipeline run by hand with sample values (mobile-0), and the sensors and camera check (mobile-2).
@main
struct AlbedianCaptureApp: App {
    #if DEBUG
    @State private var samplePath: [String] = {
        let args = ProcessInfo.processInfo.arguments
        if args.contains("-AlbedianOpenControls") { return ["controls"] }
        if args.contains("-AlbedianOpenCheck") { return ["check"] }
        if args.contains("-AlbedianOpenPipeline") { return ["pipeline"] }
        return []
    }()
    #endif

    var body: some Scene {
        WindowGroup {
            #if DEBUG
            NavigationStack(path: $samplePath) {
                rootList.navigationDestination(for: String.self) { target in
                    if target == "controls" {
                        ControlsGallery()
                    } else if target == "pipeline" {
                        PipelineScreen()
                    } else {
                        CaptureCheckScreen()
                    }
                }
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
            #if DEBUG
            NavigationLink("Controls (debug only)") { ControlsGallery() }
            #endif
        }
        .navigationTitle("Albedian Capture")
    }
}
