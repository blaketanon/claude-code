import AVFoundation
import CoreLocation
import Photos

enum VideoSource: Hashable, Identifiable {
    case photos(PHAsset)
    case file(URL)

    var id: String {
        switch self {
        case .photos(let asset): asset.localIdentifier
        case .file(let url): url.absoluteString
        }
    }
}

struct VideoMetadata {
    var creationDate: Date?
    var timeZone: TimeZone?
    var location: CLLocation?
}

struct LoadedVideo {
    let asset: AVAsset
    let name: String
    let metadata: VideoMetadata
}

enum VideoLoadError: LocalizedError {
    case unavailable
    case noVideoTrack

    var errorDescription: String? {
        switch self {
        case .unavailable:
            "This video couldn't be loaded. If it's stored in iCloud, check your connection and try again."
        case .noVideoTrack:
            "This file doesn't contain a video track."
        }
    }
}

enum VideoLoader {
    static func load(_ source: VideoSource) async throws -> LoadedVideo {
        switch source {
        case .file(let url):
            let asset = AVURLAsset(url: url)
            let metadata = await readMetadata(asset)
            return LoadedVideo(asset: asset, name: url.deletingPathExtension().lastPathComponent, metadata: metadata)

        case .photos(let item):
            let isLivePhoto = item.mediaType == .image && item.mediaSubtypes.contains(.photoLive)
            let asset = try await (isLivePhoto ? livePhotoVideo(item) : requestVideo(item))
            var metadata = await readMetadata(asset)
            // Photos' own date and place win: the user may have corrected them in the Photos app.
            // The video file still supplies the time zone, which PHAsset doesn't expose.
            metadata.creationDate = item.creationDate ?? metadata.creationDate
            metadata.location = item.location ?? metadata.location
            let resources = PHAssetResource.assetResources(for: item)
            let filename = resources.first(where: { $0.type == .video || $0.type == .photo })?.originalFilename
                ?? resources.first?.originalFilename
                ?? "Video"
            let name = (filename as NSString).deletingPathExtension
            return LoadedVideo(asset: asset, name: name, metadata: metadata)
        }
    }

    private static func requestVideo(_ item: PHAsset) async throws -> AVAsset {
        let options = PHVideoRequestOptions()
        options.isNetworkAccessAllowed = true
        options.deliveryMode = .highQualityFormat
        options.version = .current
        return try await withCheckedThrowingContinuation { continuation in
            PHImageManager.default().requestAVAsset(forVideo: item, options: options) { asset, _, info in
                if let asset {
                    continuation.resume(returning: asset)
                } else {
                    continuation.resume(throwing: (info?[PHImageErrorKey] as? Error) ?? VideoLoadError.unavailable)
                }
            }
        }
    }

    /// Writes the movie half of a Live Photo to a temporary file.
    private static func livePhotoVideo(_ item: PHAsset) async throws -> AVAsset {
        let resources = PHAssetResource.assetResources(for: item)
        guard let resource = resources.first(where: { $0.type == .fullSizePairedVideo })
            ?? resources.first(where: { $0.type == .pairedVideo })
        else { throw VideoLoadError.noVideoTrack }

        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("LivePhotos", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let url = folder.appendingPathComponent(UUID().uuidString).appendingPathExtension("mov")
        let options = PHAssetResourceRequestOptions()
        options.isNetworkAccessAllowed = true

        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            PHAssetResourceManager.default().writeData(for: resource, toFile: url, options: options) { error in
                if let error { continuation.resume(throwing: error) } else { continuation.resume() }
            }
        }
        return AVURLAsset(url: url)
    }

    static func readMetadata(_ asset: AVAsset) async -> VideoMetadata {
        var metadata = VideoMetadata()
        let items = (try? await asset.load(.metadata)) ?? []
        let dateIDs: Set<AVMetadataIdentifier> = [.quickTimeMetadataCreationDate, .commonIdentifierCreationDate]
        let locationIDs: Set<AVMetadataIdentifier> = [
            .quickTimeMetadataLocationISO6709, .quickTimeUserDataLocationISO6709, .commonIdentifierLocation,
        ]

        for item in items {
            guard let id = item.identifier else { continue }
            if dateIDs.contains(id), metadata.timeZone == nil,
               let string = try? await item.load(.stringValue),
               let parsed = QuickTimeDate.parse(string) {
                metadata.creationDate = parsed.date
                metadata.timeZone = parsed.timeZone
            } else if locationIDs.contains(id), metadata.location == nil,
                      let string = try? await item.load(.stringValue) {
                metadata.location = ISO6709.parse(string)
            }
        }

        if metadata.creationDate == nil,
           let item = try? await asset.load(.creationDate),
           let date = try? await item.load(.dateValue) {
            metadata.creationDate = date
        }
        return metadata
    }
}

enum FileImport {
    /// Copies a file picked in the Files app into the sandbox so it stays readable.
    static func copyToSandbox(_ url: URL) throws -> URL {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        let folder = FileManager.default.temporaryDirectory
            .appendingPathComponent("Imports", isDirectory: true)
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let destination = folder.appendingPathComponent(url.lastPathComponent)
        try FileManager.default.copyItem(at: url, to: destination)
        return destination
    }
}
