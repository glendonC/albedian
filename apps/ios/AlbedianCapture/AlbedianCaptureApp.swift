import SwiftUI

/// The capture companion. It opens on the approved home (UIUX-arrive-READY, iPhone 1). In a debug build a launch
/// argument opens one of the plain screens instead, in the system's default style: `-AlbedianOpenPipeline` (the
/// pipeline run by hand with sample values, mobile-0 and mobile-4), `-AlbedianOpenCheck` (the sensors and camera check,
/// mobile-2) or `-AlbedianOpenControls` (the approved controls together).
@main
struct AlbedianCaptureApp: App {
    var body: some Scene {
        WindowGroup {
            #if DEBUG
            let args = ProcessInfo.processInfo.arguments
            if args.contains("-AlbedianOpenControls") {
                NavigationStack { ControlsGallery() }
            } else if args.contains("-AlbedianOpenCheck") {
                NavigationStack { CaptureCheckScreen() }
            } else if args.contains("-AlbedianOpenPipeline") {
                NavigationStack { PipelineScreen() }
            } else {
                CompanionRoot()
            }
            #else
            CompanionRoot()
            #endif
        }
    }
}
