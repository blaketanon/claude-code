import Foundation
import UniformTypeIdentifiers

enum ExportFormat: String, CaseIterable, Identifiable {
    case heif, jpeg, png

    var id: String { rawValue }

    var label: String {
        switch self {
        case .heif: "HEIF"
        case .jpeg: "JPEG"
        case .png: "PNG"
        }
    }

    var utType: UTType {
        switch self {
        case .heif: .heic
        case .jpeg: .jpeg
        case .png: .png
        }
    }

    var fileExtension: String {
        switch self {
        case .heif: "heic"
        case .jpeg: "jpg"
        case .png: "png"
        }
    }

    var supportsQuality: Bool { self != .png }

    var footnote: String {
        switch self {
        case .heif: "About half the size of JPEG at the same quality. Best for saving to Photos."
        case .jpeg: "Opens everywhere. A good pick for sharing with other apps and computers."
        case .png: "Lossless and large. Use it when every pixel matters."
        }
    }
}

enum TimeFormat: String, CaseIterable, Identifiable {
    case frames, milliseconds

    var id: String { rawValue }

    var label: String {
        switch self {
        case .frames: "Hours:Min:Sec:Frames"
        case .milliseconds: "Min:Sec.Milliseconds"
        }
    }
}

/// UserDefaults keys shared by `@AppStorage` properties and `ExportOptions.current`.
enum Prefs {
    static let format = "exportFormat"
    static let quality = "exportQuality"
    static let keepMetadata = "keepMetadata"
    static let timeFormat = "timeFormat"
    static let squareGrid = "squareGrid"
    static let libraryFilter = "libraryFilter"
}

struct ExportOptions: Sendable {
    var format: ExportFormat
    var quality: Double
    var keepMetadata: Bool

    static var current: ExportOptions {
        let defaults = UserDefaults.standard
        return ExportOptions(
            format: ExportFormat(rawValue: defaults.string(forKey: Prefs.format) ?? "") ?? .heif,
            quality: defaults.object(forKey: Prefs.quality) as? Double ?? 0.9,
            keepMetadata: defaults.object(forKey: Prefs.keepMetadata) as? Bool ?? true
        )
    }
}
