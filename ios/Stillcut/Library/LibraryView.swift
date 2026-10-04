import Photos
import SwiftUI
import UniformTypeIdentifiers

struct LibraryView: View {
    @State private var library = LibraryModel()
    @State private var path: [VideoSource] = []
    @State private var isImporting = false
    @State private var showSettings = false
    @State private var importError: String?
    @AppStorage(Prefs.squareGrid) private var squareGrid = true
    @Environment(\.openURL) private var openURL

    var body: some View {
        NavigationStack(path: $path) {
            content
                .navigationTitle(AppInfo.name)
                .toolbar { toolbar }
                .navigationDestination(for: VideoSource.self) { EditorView(source: $0) }
        }
        .task { await library.start() }
        .fileImporter(isPresented: $isImporting, allowedContentTypes: [.movie], allowsMultipleSelection: false) { result in
            switch result {
            case .success(let urls):
                guard let url = urls.first else { return }
                Task {
                    do {
                        let copy = try await Task.detached { try FileImport.copyToSandbox(url) }.value
                        path.append(.file(copy))
                    } catch {
                        importError = error.localizedDescription
                    }
                }
            case .failure(let error):
                importError = error.localizedDescription
            }
        }
        .sheet(isPresented: $showSettings) { SettingsView() }
        .alert("Couldn't open that file", isPresented: Binding(
            get: { importError != nil },
            set: { if !$0 { importError = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(importError ?? "")
        }
    }

    @ViewBuilder private var content: some View {
        switch library.status {
        case .authorized, .limited:
            if library.assets.isEmpty {
                ContentUnavailableView {
                    Label("No \(library.filter == .livePhotos ? "Live Photos" : "videos") yet", systemImage: library.filter.symbol)
                } description: {
                    Text("Videos you record or save to Photos show up here. You can also open a video from Files.")
                } actions: {
                    Button("Open from Files") { isImporting = true }
                }
            } else {
                grid
            }
        case .notDetermined:
            ProgressView()
        default:
            ContentUnavailableView {
                Label("Allow access to your videos", systemImage: "photo.on.rectangle.angled")
            } description: {
                Text("\(AppInfo.name) shows the videos and Live Photos in your library so you can pull stills from them. Nothing leaves your device.")
            } actions: {
                Button("Open Settings") {
                    if let url = URL(string: UIApplication.openSettingsURLString) { openURL(url) }
                }
                .buttonStyle(.borderedProminent)
                .foregroundStyle(.black)
                Button("Open from Files") { isImporting = true }
            }
        }
    }

    private var grid: some View {
        ScrollView {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 104, maximum: 180), spacing: 2)], spacing: 2) {
                ForEach(library.assets, id: \.localIdentifier) { asset in
                    NavigationLink(value: VideoSource.photos(asset)) {
                        AssetThumbnail(asset: asset, library: library, square: squareGrid)
                    }
                    .buttonStyle(.plain)
                }
            }
            if library.status == .limited {
                Text("\(AppInfo.name) can see only the items you've selected. Change this in Settings › Privacy & Security › Photos.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(24)
            }
        }
    }

    @ToolbarContentBuilder private var toolbar: some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) {
            Menu {
                Picker("Show", selection: $library.filter) {
                    ForEach(LibraryModel.Filter.allCases) { filter in
                        Label(filter.label, systemImage: filter.symbol).tag(filter)
                    }
                }
                Toggle("Square Thumbnails", systemImage: "square", isOn: $squareGrid)
            } label: {
                Image(systemName: "line.3.horizontal.decrease.circle")
            }
            .accessibilityLabel("Filter")
        }
        ToolbarItemGroup(placement: .topBarTrailing) {
            Button { isImporting = true } label: { Image(systemName: "folder") }
                .accessibilityLabel("Open from Files")
            Button { showSettings = true } label: { Image(systemName: "gearshape") }
                .accessibilityLabel("Settings")
        }
    }
}

struct AssetThumbnail: View {
    let asset: PHAsset
    let library: LibraryModel
    let square: Bool
    @State private var image: UIImage?

    var body: some View {
        Color(white: 0.09)
            .aspectRatio(1, contentMode: .fit)
            .overlay {
                if let image {
                    Image(uiImage: image)
                        .resizable()
                        .aspectRatio(contentMode: square ? .fill : .fit)
                }
            }
            .clipped()
            .overlay(alignment: .bottomTrailing) {
                if asset.mediaType == .video {
                    Text(Timecode.duration(asset.duration))
                        .font(.caption2.monospacedDigit().weight(.semibold))
                        .foregroundStyle(.white)
                        .shadow(color: .black.opacity(0.8), radius: 2)
                        .padding(5)
                }
            }
            .overlay(alignment: .topLeading) {
                if asset.mediaSubtypes.contains(.photoLive) {
                    Image(systemName: "livephoto")
                        .font(.caption)
                        .foregroundStyle(.white)
                        .shadow(color: .black.opacity(0.8), radius: 2)
                        .padding(5)
                } else if asset.mediaSubtypes.contains(.videoHighFrameRate) {
                    Image(systemName: "slowmo")
                        .font(.caption)
                        .foregroundStyle(.white)
                        .shadow(color: .black.opacity(0.8), radius: 2)
                        .padding(5)
                }
            }
            .task(id: asset.localIdentifier) {
                image = await library.thumbnail(for: asset, size: CGSize(width: 360, height: 360))
            }
            .accessibilityElement()
            .accessibilityLabel(accessibilityText)
    }

    private var accessibilityText: String {
        let kind = asset.mediaType == .video ? "Video, \(Timecode.duration(asset.duration))" : "Live Photo"
        guard let date = asset.creationDate else { return kind }
        return "\(kind), \(date.formatted(date: .abbreviated, time: .shortened))"
    }
}
