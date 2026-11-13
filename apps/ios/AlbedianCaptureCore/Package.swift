// swift-tools-version: 6.0
// The capture companion's core: the pointing record, its wire JSON, its validator and the upload client. No
// interface code and no dependency outside Apple's frameworks and the Swift standard library. The app in
// ../AlbedianCapture.xcodeproj uses it; its tests run on the Mac with `swift test` (see ../README.md).
import PackageDescription

let package = Package(
    name: "AlbedianCaptureCore",
    platforms: [
        // The app targets iOS 26.0 (see ../README.md); the Mac is only where the tests run.
        .iOS("26.0"),
        .macOS("15.0"),
    ],
    products: [
        .library(name: "AlbedianCaptureCore", targets: ["AlbedianCaptureCore"]),
    ],
    targets: [
        .target(name: "AlbedianCaptureCore"),
        .testTarget(name: "AlbedianCaptureCoreTests", dependencies: ["AlbedianCaptureCore"]),
    ]
)
