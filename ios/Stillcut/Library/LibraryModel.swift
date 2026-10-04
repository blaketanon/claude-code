import Observation
import Photos
import UIKit

@MainActor
@Observable
final class LibraryModel {
    enum Filter: String, CaseIterable, Identifiable {
        case all, videos, livePhotos

        var id: String { rawValue }

        var label: String {
            switch self {
            case .all: "Videos & Live Photos"
            case .videos: "Videos"
            case .livePhotos: "Live Photos"
            }
        }

        var symbol: String {
            switch self {
            case .all: "square.grid.2x2"
            case .videos: "video"
            case .livePhotos: "livephoto"
            }
        }
    }

    private(set) var status = PHPhotoLibrary.authorizationStatus(for: .readWrite)
    private(set) var assets: [PHAsset] = []
    var filter: Filter = .all {
        didSet { reload() }
    }

    @ObservationIgnored let imageManager = PHCachingImageManager()
    @ObservationIgnored private var observer: LibraryChangeObserver?

    var hasAccess: Bool { status == .authorized || status == .limited }

    func start() async {
        if status == .notDetermined {
            status = await PHPhotoLibrary.requestAuthorization(for: .readWrite)
        }
        guard hasAccess else { return }
        if observer == nil {
            observer = LibraryChangeObserver { [weak self] in
                Task { @MainActor in self?.reload() }
            }
        }
        reload()
    }

    func reload() {
        guard hasAccess else {
            assets = []
            return
        }
        let options = PHFetchOptions()
        options.sortDescriptors = [NSSortDescriptor(key: "creationDate", ascending: false)]
        let video = PHAssetMediaType.video.rawValue
        let live = Int(PHAssetMediaSubtype.photoLive.rawValue)
        switch filter {
        case .all:
            options.predicate = NSPredicate(format: "mediaType == %ld OR (mediaSubtypes & %ld) != 0", video, live)
        case .videos:
            options.predicate = NSPredicate(format: "mediaType == %ld", video)
        case .livePhotos:
            options.predicate = NSPredicate(format: "(mediaSubtypes & %ld) != 0", live)
        }
        let result = PHAsset.fetchAssets(with: options)
        var list: [PHAsset] = []
        list.reserveCapacity(result.count)
        result.enumerateObjects { asset, _, _ in list.append(asset) }
        assets = list
    }

    func thumbnail(for asset: PHAsset, size: CGSize) async -> UIImage? {
        let options = PHImageRequestOptions()
        options.deliveryMode = .highQualityFormat
        options.resizeMode = .fast
        options.isNetworkAccessAllowed = true
        return await withCheckedContinuation { continuation in
            imageManager.requestImage(for: asset, targetSize: size, contentMode: .aspectFill, options: options) { image, _ in
                continuation.resume(returning: image)
            }
        }
    }
}

/// Bridges PhotoKit's change callbacks, which arrive on a background queue.
private final class LibraryChangeObserver: NSObject, PHPhotoLibraryChangeObserver {
    private let onChange: @Sendable () -> Void

    init(onChange: @escaping @Sendable () -> Void) {
        self.onChange = onChange
        super.init()
        PHPhotoLibrary.shared().register(self)
    }

    deinit {
        PHPhotoLibrary.shared().unregisterChangeObserver(self)
    }

    func photoLibraryDidChange(_ changeInstance: PHChange) {
        onChange()
    }
}
