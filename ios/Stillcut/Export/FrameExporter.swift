import CoreLocation
import ImageIO
import Photos
import UIKit
import UniformTypeIdentifiers

/// A grabbed frame, already encoded to a file in the app's temporary folder.
struct Shot: Identifiable {
    let id = UUID()
    let url: URL
    let thumbnail: UIImage
    let frame: Int
    let time: Double
    let pixelSize: CGSize
    let format: ExportFormat
    let captureDate: Date?
    let location: CLLocation?
}

enum ExportError: LocalizedError {
    case encoderUnavailable
    case writeFailed
    case photosDenied

    var errorDescription: String? {
        switch self {
        case .encoderUnavailable: "This device can't encode that image format. Pick another format in Settings."
        case .writeFailed: "The image couldn't be written. Check that your device has free space."
        case .photosDenied: "Allow \(AppInfo.name) to add photos in Settings › Privacy & Security › Photos."
        }
    }
}

enum FrameExporter {
    static let folder = FileManager.default.temporaryDirectory.appendingPathComponent("Frames", isDirectory: true)

    /// Encodes `image` and writes it with the video's date and location.
    /// Falls back to JPEG on hardware without a HEIF encoder.
    static func write(
        _ image: CGImage, name: String, options: ExportOptions, metadata: VideoMetadata, frameTime: Double
    ) throws -> (url: URL, format: ExportFormat) {
        let directory = folder.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)

        var format = options.format
        var url = directory.appendingPathComponent(name).appendingPathExtension(format.fileExtension)
        var destination = CGImageDestinationCreateWithURL(url as CFURL, format.utType.identifier as CFString, 1, nil)
        if destination == nil, format == .heif {
            format = .jpeg
            url = directory.appendingPathComponent(name).appendingPathExtension(format.fileExtension)
            destination = CGImageDestinationCreateWithURL(url as CFURL, format.utType.identifier as CFString, 1, nil)
        }
        guard let destination else { throw ExportError.encoderUnavailable }

        var properties: [CFString: Any] = [:]
        if format.supportsQuality {
            properties[kCGImageDestinationLossyCompressionQuality] = options.quality
        }
        if options.keepMetadata {
            properties.merge(imageProperties(metadata: metadata, frameTime: frameTime)) { $1 }
        }
        CGImageDestinationAddImage(destination, image, properties as CFDictionary)
        guard CGImageDestinationFinalize(destination) else { throw ExportError.writeFailed }
        return (url, format)
    }

    /// EXIF, TIFF and GPS dictionaries. The capture time is the recording time plus the
    /// frame's offset into the video, in the time zone the video was recorded in.
    static func imageProperties(metadata: VideoMetadata, frameTime: Double) -> [CFString: Any] {
        var tiff: [CFString: Any] = [kCGImagePropertyTIFFSoftware: AppInfo.name]
        var exif: [CFString: Any] = [:]
        var result: [CFString: Any] = [:]

        if let recorded = metadata.creationDate {
            let date = recorded.addingTimeInterval(frameTime)
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "en_US_POSIX")
            formatter.calendar = Calendar(identifier: .gregorian)
            formatter.timeZone = metadata.timeZone ?? .current
            formatter.dateFormat = "yyyy:MM:dd HH:mm:ss"
            let stamp = formatter.string(from: date)
            formatter.dateFormat = "xxx"
            let offset = formatter.string(from: date)
            let fraction = abs(date.timeIntervalSince1970.truncatingRemainder(dividingBy: 1))
            let subsec = String(format: "%03d", min(999, Int(fraction * 1000)))

            exif[kCGImagePropertyExifDateTimeOriginal] = stamp
            exif[kCGImagePropertyExifDateTimeDigitized] = stamp
            exif["OffsetTimeOriginal" as CFString] = offset
            exif["OffsetTimeDigitized" as CFString] = offset
            exif["SubsecTimeOriginal" as CFString] = subsec
            exif["SubsecTimeDigitized" as CFString] = subsec
            tiff[kCGImagePropertyTIFFDateTime] = stamp
        }

        if let location = metadata.location {
            let c = location.coordinate
            var gps: [CFString: Any] = [
                kCGImagePropertyGPSLatitude: abs(c.latitude),
                kCGImagePropertyGPSLatitudeRef: c.latitude >= 0 ? "N" : "S",
                kCGImagePropertyGPSLongitude: abs(c.longitude),
                kCGImagePropertyGPSLongitudeRef: c.longitude >= 0 ? "E" : "W",
            ]
            if location.verticalAccuracy >= 0 {
                gps[kCGImagePropertyGPSAltitude] = abs(location.altitude)
                gps[kCGImagePropertyGPSAltitudeRef] = location.altitude < 0 ? 1 : 0
            }
            result[kCGImagePropertyGPSDictionary] = gps
        }

        result[kCGImagePropertyTIFFDictionary] = tiff
        if !exif.isEmpty { result[kCGImagePropertyExifDictionary] = exif }
        return result
    }

    static func remove(_ shot: Shot) {
        try? FileManager.default.removeItem(at: shot.url.deletingLastPathComponent())
    }

    /// Clears frames left over from earlier launches.
    static func purgeOldFiles() {
        try? FileManager.default.removeItem(at: folder)
    }
}

enum PhotoSaver {
    static func save(_ shots: [Shot]) async throws {
        let status = await PHPhotoLibrary.requestAuthorization(for: .addOnly)
        guard status == .authorized || status == .limited else { throw ExportError.photosDenied }
        let items = shots.map { ($0.url, $0.captureDate, $0.location) }
        try await PHPhotoLibrary.shared().performChanges {
            for (url, date, location) in items {
                let request = PHAssetCreationRequest.forAsset()
                request.addResource(with: .photo, fileURL: url, options: nil)
                request.creationDate = date
                request.location = location
            }
        }
    }
}
