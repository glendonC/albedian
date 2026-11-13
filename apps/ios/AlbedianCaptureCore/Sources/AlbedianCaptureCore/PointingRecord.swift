import Foundation

/// A pointing record as the phone writes it: what its sensors said at the moment it took a photo, kept beside the
/// photo and never inside it, tied to it by the photo's SHA-256. The types hold the contract's shape, so a record
/// with a heading reason that carries no accuracy, or an accuracy on any other reason, cannot be written at all;
/// the numeric rules are checked by `PointingValidator` on the wire JSON, which `PointingRecordBuilder` runs on
/// every record it builds.
public struct PointingRecord: Equatable, Sendable {
    public var sourceAssetSha256: String
    /// The device clock at the photo's timestamp, as `UTCInstant.string(from:)` writes it.
    public var capturedAtDeviceClockUTC: String
    public var device: Device
    public var camera: Camera
    public var motion: Motion
    public var heading: Heading
    public var location: Place

    public init(sourceAssetSha256: String, capturedAtDeviceClockUTC: String, device: Device, camera: Camera,
                motion: Motion, heading: Heading, location: Place) {
        self.sourceAssetSha256 = sourceAssetSha256
        self.capturedAtDeviceClockUTC = capturedAtDeviceClockUTC
        self.device = device
        self.camera = camera
        self.motion = motion
        self.heading = heading
        self.location = location
    }

    /// The device by its machine identifier and system version, never by a name a person gave it.
    public struct Device: Equatable, Sendable {
        public var hardwareModel: String
        public var systemVersion: String
        public init(hardwareModel: String, systemVersion: String) {
            self.hardwareModel = hardwareModel
            self.systemVersion = systemVersion
        }
    }

    public struct Dimensions: Equatable, Sendable {
        public var width: Int
        public var height: Int
        public init(width: Int, height: Int) {
            self.width = width
            self.height = height
        }
    }

    /// The active format's horizontal field of view as AVFoundation reported it, or unknown where it reported zero.
    public enum FieldOfView: Equatable, Sendable {
        case reported(horizontalDeg: Double, distortionCorrected: Bool)
        case unknown
    }

    public struct Camera: Equatable, Sendable {
        public var position: CameraPosition
        public var deviceType: CameraDeviceType
        public var activeFormatDimensionsPx: Dimensions
        public var photoDimensionsPx: Dimensions
        public var fieldOfView: FieldOfView
        public var videoZoomFactor: Double
        public var contentAwareDistortionCorrection: Bool
        public init(position: CameraPosition, deviceType: CameraDeviceType, activeFormatDimensionsPx: Dimensions,
                    photoDimensionsPx: Dimensions, fieldOfView: FieldOfView, videoZoomFactor: Double,
                    contentAwareDistortionCorrection: Bool) {
            self.position = position
            self.deviceType = deviceType
            self.activeFormatDimensionsPx = activeFormatDimensionsPx
            self.photoDimensionsPx = photoDimensionsPx
            self.fieldOfView = fieldOfView
            self.videoZoomFactor = videoZoomFactor
            self.contentAwareDistortionCorrection = contentAwareDistortionCorrection
        }
    }

    /// A vector in the device's own frame (Core Motion's x, y and z axes of the device).
    public struct Vector3: Equatable, Sendable {
        public var x: Double
        public var y: Double
        public var z: Double
        public init(x: Double, y: Double, z: Double) {
            self.x = x
            self.y = y
            self.z = z
        }
        /// The same arithmetic, in the same order, as the other two readings.
        public var length: Double { (x * x + y * y + z * z).squareRoot() }
    }

    /// CMAttitude.quaternion as the device reported it: x, y, z, w.
    public struct Quaternion: Equatable, Sendable {
        public var x: Double
        public var y: Double
        public var z: Double
        public var w: Double
        public init(x: Double, y: Double, z: Double, w: Double) {
            self.x = x
            self.y = y
            self.z = z
            self.w = w
        }
        /// The same arithmetic, in the same order, as the other two readings.
        public var norm: Double { (x * x + y * y + z * z + w * w).squareRoot() }
    }

    public struct MotionWindow: Equatable, Sendable {
        public var startOffsetSec: Double
        public var endOffsetSec: Double
        public var sampleCount: Int
        public var maxRotationRateRadPerSec: Double
        public init(startOffsetSec: Double, endOffsetSec: Double, sampleCount: Int, maxRotationRateRadPerSec: Double) {
            self.startOffsetSec = startOffsetSec
            self.endOffsetSec = endOffsetSec
            self.sampleCount = sampleCount
            self.maxRotationRateRadPerSec = maxRotationRateRadPerSec
        }
    }

    public struct Motion: Equatable, Sendable {
        public var sampleOffsetFromPhotoSec: Double
        public var gravityDeviceG: Vector3
        public var rotationRateDeviceRadPerSec: Vector3
        public var window: MotionWindow
        public init(sampleOffsetFromPhotoSec: Double, gravityDeviceG: Vector3, rotationRateDeviceRadPerSec: Vector3,
                    window: MotionWindow) {
            self.sampleOffsetFromPhotoSec = sampleOffsetFromPhotoSec
            self.gravityDeviceG = gravityDeviceG
            self.rotationRateDeviceRadPerSec = rotationRateDeviceRadPerSec
            self.window = window
        }
    }

    /// A heading the device measured; the attitude is in the `x_true_north_z_vertical` frame.
    public struct MeasuredHeading: Equatable, Sendable {
        public var quaternion: Quaternion
        public var trueHeadingDeg: Double
        public var headingAccuracyDeg: Double
        public var headingOrientation: HeadingOrientation
        public var eventOffsetFromPhotoSec: Double
        public init(quaternion: Quaternion, trueHeadingDeg: Double, headingAccuracyDeg: Double,
                    headingOrientation: HeadingOrientation, eventOffsetFromPhotoSec: Double) {
            self.quaternion = quaternion
            self.trueHeadingDeg = trueHeadingDeg
            self.headingAccuracyDeg = headingAccuracyDeg
            self.headingOrientation = headingOrientation
            self.eventOffsetFromPhotoSec = eventOffsetFromPhotoSec
        }
    }

    /// Why there is no heading; only a heading left out for its accuracy carries the accuracy the device stated.
    public enum NoHeadingReason: Equatable, Sendable {
        case noMagnetometer
        case noPlace
        case noHeadingEvent
        case headingInvalid
        case accuracyOutsideLimit(reportedDeg: Double)

        public var name: NoHeadingReasonName {
            switch self {
            case .noMagnetometer: .noMagnetometer
            case .noPlace: .noPlace
            case .noHeadingEvent: .noHeadingEvent
            case .headingInvalid: .headingInvalid
            case .accuracyOutsideLimit: .accuracyOutsideLimit
            }
        }
    }

    public enum Heading: Equatable, Sendable {
        case measured(MeasuredHeading)
        case absent(NoHeadingReason)
    }

    /// CLLocation's altitudes, present only when the device called them valid (a positive vertical accuracy).
    public struct Altitude: Equatable, Sendable {
        public var aboveMeanSeaLevelM: Double
        public var aboveEllipsoidM: Double
        public var verticalAccuracyM: Double
        public init(aboveMeanSeaLevelM: Double, aboveEllipsoidM: Double, verticalAccuracyM: Double) {
            self.aboveMeanSeaLevelM = aboveMeanSeaLevelM
            self.aboveEllipsoidM = aboveEllipsoidM
            self.verticalAccuracyM = verticalAccuracyM
        }
    }

    public struct AllowedPlace: Equatable, Sendable {
        public var latDeg: Double
        public var lonDeg: Double
        public var horizontalAccuracyM: Double
        public var accuracyAuthorization: AccuracyAuthorization
        public var altitude: Altitude?
        public var fixOffsetFromPhotoSec: Double
        public init(latDeg: Double, lonDeg: Double, horizontalAccuracyM: Double,
                    accuracyAuthorization: AccuracyAuthorization, altitude: Altitude?, fixOffsetFromPhotoSec: Double) {
            self.latDeg = latDeg
            self.lonDeg = lonDeg
            self.horizontalAccuracyM = horizontalAccuracyM
            self.accuracyAuthorization = accuracyAuthorization
            self.altitude = altitude
            self.fixOffsetFromPhotoSec = fixOffsetFromPhotoSec
        }
    }

    public enum Place: Equatable, Sendable {
        case allowed(AllowedPlace)
        case absent(NoPlaceReason)
    }
}

/// A record that cannot be written as JSON: a number that is infinite or not a number, at its wire path.
public struct PointingEncodingError: Error, Equatable, Sendable {
    public let path: [String]
}

extension PointingRecord {
    /// The record as wire JSON: camelCase keys in the contract's order, every number finite, whole numbers as
    /// integers, minus zero as zero.
    public func wireValue() throws(PointingEncodingError) -> JSONValue {
        func number(_ value: Double, _ path: [String]) throws(PointingEncodingError) -> JSONValue {
            guard let number = JSONNumber(value) else { throw PointingEncodingError(path: path) }
            return .number(number)
        }
        func vector(_ value: Vector3, _ path: [String]) throws(PointingEncodingError) -> JSONValue {
            .object(JSONObject(["x": try number(value.x, path + ["x"]), "y": try number(value.y, path + ["y"]),
                                "z": try number(value.z, path + ["z"])]))
        }
        func dimensions(_ value: Dimensions) -> JSONValue {
            .object(JSONObject(["width": .int(value.width), "height": .int(value.height)]))
        }
        let fieldOfView: JSONValue
        switch camera.fieldOfView {
        case .reported(let horizontalDeg, let distortionCorrected):
            fieldOfView = .object(JSONObject(["state": .string("reported"),
                                              "horizontalDeg": try number(horizontalDeg, ["camera", "fieldOfView", "horizontalDeg"]),
                                              "distortionCorrected": .bool(distortionCorrected)]))
        case .unknown:
            fieldOfView = .object(JSONObject(["state": .string("unknown")]))
        }
        let window = motion.window
        let headingValue: JSONValue
        switch heading {
        case .measured(let measured):
            let q = measured.quaternion
            let path = ["heading", "attitude", "quaternion"]
            headingValue = .object(JSONObject([
                "state": .string("measured"),
                "attitude": .object(JSONObject([
                    "referenceFrame": .string("x_true_north_z_vertical"),
                    "quaternion": .object(JSONObject(["x": try number(q.x, path + ["x"]), "y": try number(q.y, path + ["y"]),
                                                      "z": try number(q.z, path + ["z"]), "w": try number(q.w, path + ["w"])])),
                ])),
                "trueHeadingDeg": try number(measured.trueHeadingDeg, ["heading", "trueHeadingDeg"]),
                "headingAccuracyDeg": try number(measured.headingAccuracyDeg, ["heading", "headingAccuracyDeg"]),
                "headingOrientation": .string(measured.headingOrientation.rawValue),
                "eventOffsetFromPhotoSec": try number(measured.eventOffsetFromPhotoSec, ["heading", "eventOffsetFromPhotoSec"]),
            ]))
        case .absent(let reason):
            var none = JSONObject(["state": .string("none"), "reason": .string(reason.name.rawValue)])
            if case .accuracyOutsideLimit(let reported) = reason {
                none["reportedHeadingAccuracyDeg"] = try number(reported, ["heading", "reportedHeadingAccuracyDeg"])
            }
            headingValue = .object(none)
        }
        let placeValue: JSONValue
        switch location {
        case .allowed(let place):
            var allowed = JSONObject([
                "state": .string("allowed"),
                "latDeg": try number(place.latDeg, ["location", "latDeg"]),
                "lonDeg": try number(place.lonDeg, ["location", "lonDeg"]),
                "horizontalAccuracyM": try number(place.horizontalAccuracyM, ["location", "horizontalAccuracyM"]),
                "accuracyAuthorization": .string(place.accuracyAuthorization.rawValue),
            ])
            if let altitude = place.altitude {
                let path = ["location", "altitude"]
                allowed["altitude"] = .object(JSONObject([
                    "aboveMeanSeaLevelM": try number(altitude.aboveMeanSeaLevelM, path + ["aboveMeanSeaLevelM"]),
                    "aboveEllipsoidM": try number(altitude.aboveEllipsoidM, path + ["aboveEllipsoidM"]),
                    "verticalAccuracyM": try number(altitude.verticalAccuracyM, path + ["verticalAccuracyM"]),
                ]))
            }
            allowed["fixOffsetFromPhotoSec"] = try number(place.fixOffsetFromPhotoSec, ["location", "fixOffsetFromPhotoSec"])
            placeValue = .object(allowed)
        case .absent(let reason):
            placeValue = .object(JSONObject(["state": .string("none"), "reason": .string(reason.rawValue)]))
        }
        return .object(JSONObject([
            "format": .string(PointingContract.format),
            "formatVersion": .int(PointingContract.formatVersion),
            "sourceAssetSha256": .string(sourceAssetSha256),
            "capturedAtDeviceClockUTC": .string(capturedAtDeviceClockUTC),
            "device": .object(JSONObject(["hardwareModel": .string(device.hardwareModel), "systemVersion": .string(device.systemVersion)])),
            "camera": .object(JSONObject([
                "position": .string(camera.position.rawValue),
                "deviceType": .string(camera.deviceType.rawValue),
                "activeFormatDimensionsPx": dimensions(camera.activeFormatDimensionsPx),
                "photoDimensionsPx": dimensions(camera.photoDimensionsPx),
                "fieldOfView": fieldOfView,
                "videoZoomFactor": try number(camera.videoZoomFactor, ["camera", "videoZoomFactor"]),
                "contentAwareDistortionCorrection": .bool(camera.contentAwareDistortionCorrection),
            ])),
            "motion": .object(JSONObject([
                "sampleOffsetFromPhotoSec": try number(motion.sampleOffsetFromPhotoSec, ["motion", "sampleOffsetFromPhotoSec"]),
                "gravityDeviceG": try vector(motion.gravityDeviceG, ["motion", "gravityDeviceG"]),
                "rotationRateDeviceRadPerSec": try vector(motion.rotationRateDeviceRadPerSec, ["motion", "rotationRateDeviceRadPerSec"]),
                "window": .object(JSONObject([
                    "startOffsetSec": try number(window.startOffsetSec, ["motion", "window", "startOffsetSec"]),
                    "endOffsetSec": try number(window.endOffsetSec, ["motion", "window", "endOffsetSec"]),
                    "sampleCount": .int(window.sampleCount),
                    "maxRotationRateRadPerSec": try number(window.maxRotationRateRadPerSec, ["motion", "window", "maxRotationRateRadPerSec"]),
                ])),
            ])),
            "heading": headingValue,
            "location": placeValue,
            "evidence": .object(JSONObject(["role": .string("pointing_hint"), "statement": .string(PointingContract.statement)])),
        ]))
    }

    /// The record as UTF-8 JSON bytes, ready for the wire.
    public func wireData() throws(PointingEncodingError) -> Data { JSONText.data(try wireValue()) }
}
