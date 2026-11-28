// The Mac's own QR readers, for scripts/__tests__/qr.test.mjs: each image path given is read by Vision's barcode
// request and by Core Image's QR detector, both part of macOS, and one JSON line a path is printed:
//   {"ciDetector":["..."],"file":"...","vision":["..."]}
// Compiled at test time with Xcode's swiftc; nothing is downloaded.
import CoreImage
import Foundation
import ImageIO
import Vision

func readWithVision(_ image: CGImage) -> [String] {
  let request = VNDetectBarcodesRequest()
  request.symbologies = [.qr]
  do {
    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
  } catch {
    return []
  }
  return (request.results ?? []).compactMap { $0.payloadStringValue }
}

func readWithCoreImage(_ image: CGImage) -> [String] {
  let detector = CIDetector(
    ofType: CIDetectorTypeQRCode, context: nil, options: [CIDetectorAccuracy: CIDetectorAccuracyHigh])
  return (detector?.features(in: CIImage(cgImage: image)) ?? []).compactMap { ($0 as? CIQRCodeFeature)?.messageString }
}

for path in CommandLine.arguments.dropFirst() {
  var line: [String: Any] = ["file": path]
  if let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
    let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
  {
    line["vision"] = readWithVision(image)
    line["ciDetector"] = readWithCoreImage(image)
  } else {
    line["error"] = "unreadable image"
  }
  let data = try JSONSerialization.data(withJSONObject: line, options: [.sortedKeys])
  print(String(decoding: data, as: UTF8.self))
}
