import MapKit
import SwiftUI

struct EditorView: View {
    @State private var model: EditorModel
    @AppStorage(Prefs.timeFormat) private var timeFormat: TimeFormat = .frames
    @State private var flash = 0.0
    @State private var showInfo = false
    @State private var showSettings = false
    @State private var previewShot: Shot?

    init(source: VideoSource) {
        _model = State(initialValue: EditorModel(source: source))
    }

    var body: some View {
        VStack(spacing: 0) {
            stage
            controls
        }
        .background(Color.black)
        .navigationTitle(model.name)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                Button { showInfo = true } label: { Image(systemName: "info.circle") }
                    .accessibilityLabel("Video details")
                    .disabled(model.state != .ready)
                Button { showSettings = true } label: { Image(systemName: "slider.horizontal.3") }
                    .accessibilityLabel("Export settings")
            }
        }
        .task { await model.load() }
        .onDisappear { model.teardown() }
        .onChange(of: model.shots.count) { old, new in
            guard new > old else { return }
            flash = 0.55
            withAnimation(.easeOut(duration: 0.35)) { flash = 0 }
        }
        .sensoryFeedback(.impact(weight: .medium), trigger: model.shots.count)
        .sheet(isPresented: $showInfo) { VideoInfoView(model: model) }
        .sheet(isPresented: $showSettings) { SettingsView() }
        .sheet(item: $previewShot) { shot in ShotPreview(shot: shot, model: model) }
        .alert("Something went wrong", isPresented: Binding(
            get: { model.errorMessage != nil },
            set: { if !$0 { model.errorMessage = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(model.errorMessage ?? "")
        }
        .overlay(alignment: .top) { noticeBanner }
    }

    // MARK: Stage

    private var stage: some View {
        ZStack {
            Color.black
            PlayerLayerView(player: model.player)
            Color.white.opacity(flash).allowsHitTesting(false)
            switch model.state {
            case .loading:
                ProgressView("Loading video…").tint(.white).foregroundStyle(.white)
            case .failed(let message):
                ContentUnavailableView("Can't open this video", systemImage: "exclamationmark.triangle", description: Text(message))
            case .ready:
                EmptyView()
            }
        }
        .contentShape(Rectangle())
        .onTapGesture { model.togglePlay() }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: Controls

    private var controls: some View {
        VStack(spacing: 14) {
            readout
            ScrubberView(model: model)
            transport
            grabButton
            if !model.shots.isEmpty {
                ShotsTray(model: model, preview: { previewShot = $0 })
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
        .padding(16)
        .background(.panel)
        .animation(.snappy, value: model.shots.isEmpty)
        .disabled(model.state != .ready)
    }

    private var readout: some View {
        let time = Timecode.parts(seconds: model.currentTime, fps: model.fps, format: timeFormat)
        return HStack(alignment: .firstTextBaseline) {
            HStack(spacing: 0) {
                Text(time.main)
                Text(time.tail).foregroundStyle(.amber)
            }
            .font(.system(size: 28, weight: .semibold, design: .monospaced))
            .accessibilityElement(children: .combine)
            Spacer(minLength: 8)
            VStack(alignment: .trailing, spacing: 2) {
                Text("Frame \(model.currentFrame + 1) of \(model.frameCount)")
                Text("\(Timecode.rateLabel(model.fps)) fps · \(Int(model.pixelSize.width))×\(Int(model.pixelSize.height))")
            }
            .font(.caption.monospacedDigit())
            .foregroundStyle(.secondary)
        }
    }

    private var transport: some View {
        HStack(spacing: 8) {
            stepButton("chevron.left.2", by: -10, label: "Back 10 frames", key: .leftArrow, modifiers: .shift)
            stepButton("chevron.left", by: -1, label: "Previous frame", key: .leftArrow, modifiers: [])
            Button { model.togglePlay() } label: {
                Image(systemName: model.isPlaying ? "pause.fill" : "play.fill")
                    .frame(width: 44, height: 40)
            }
            .buttonStyle(.bordered)
            .keyboardShortcut(.space, modifiers: [])
            .accessibilityLabel(model.isPlaying ? "Pause" : "Play")
            stepButton("chevron.right", by: 1, label: "Next frame", key: .rightArrow, modifiers: [])
            stepButton("chevron.right.2", by: 10, label: "Forward 10 frames", key: .rightArrow, modifiers: .shift)
            Spacer(minLength: 0)
            Menu {
                Picker("Playback speed", selection: Binding(get: { model.playbackRate }, set: { model.setRate($0) })) {
                    ForEach([Float(0.25), 0.5, 1, 2], id: \.self) { rate in
                        Text(rateLabel(rate)).tag(rate)
                    }
                }
            } label: {
                Text(rateLabel(model.playbackRate))
                    .font(.callout.monospacedDigit().weight(.medium))
                    .frame(minWidth: 52, minHeight: 40)
            }
            .buttonStyle(.bordered)
            .accessibilityLabel("Playback speed")
        }
        .tint(.white)
    }

    private func stepButton(_ symbol: String, by frames: Int, label: String, key: KeyEquivalent, modifiers: EventModifiers) -> some View {
        Button { model.step(frames) } label: {
            Image(systemName: symbol).frame(width: 30, height: 40)
        }
        .buttonStyle(.bordered)
        .buttonRepeatBehavior(.enabled)
        .keyboardShortcut(key, modifiers: modifiers)
        .accessibilityLabel(label)
    }

    private var grabButton: some View {
        Button {
            Task { await model.grab() }
        } label: {
            HStack {
                if model.isGrabbing { ProgressView().tint(.black) } else { Image(systemName: "camera.viewfinder") }
                Text("Grab Frame")
            }
            .font(.headline)
            .frame(maxWidth: .infinity, minHeight: 34)
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
        .tint(Color.amber)
        .foregroundStyle(.black)
        .keyboardShortcut("g", modifiers: [])
        .disabled(model.isGrabbing)
    }

    private func rateLabel(_ rate: Float) -> String {
        switch rate {
        case 0.25: "¼×"
        case 0.5: "½×"
        default: "\(Int(rate))×"
        }
    }

    @ViewBuilder private var noticeBanner: some View {
        if let notice = model.notice {
            Label(notice, systemImage: "checkmark.circle.fill")
                .font(.subheadline.weight(.medium))
                .padding(.horizontal, 14)
                .padding(.vertical, 9)
                .background(.thinMaterial, in: Capsule())
                .padding(.top, 8)
                .transition(.move(edge: .top).combined(with: .opacity))
                .task(id: notice) {
                    try? await Task.sleep(for: .seconds(2))
                    withAnimation { model.notice = nil }
                }
        }
    }
}

// MARK: - Grabbed frames

struct ShotsTray: View {
    let model: EditorModel
    let preview: (Shot) -> Void
    @State private var isSaving = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 12) {
                Text("Grabbed · \(model.shots.count)")
                    .font(.caption.weight(.semibold))
                    .textCase(.uppercase)
                    .foregroundStyle(.secondary)
                Spacer()
                Menu {
                    Button("Remove All", systemImage: "trash", role: .destructive) { model.removeAllShots() }
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
                .accessibilityLabel("More")
                ShareLink(items: model.shots.map(\.url)) {
                    Image(systemName: "square.and.arrow.up")
                }
                .accessibilityLabel("Share all")
                Button {
                    isSaving = true
                    Task {
                        await model.saveToPhotos(model.shots)
                        isSaving = false
                    }
                } label: {
                    Label("Save All", systemImage: "square.and.arrow.down")
                        .font(.subheadline.weight(.semibold))
                }
                .disabled(isSaving)
            }
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(spacing: 8) {
                    ForEach(model.shots.reversed()) { shot in
                        thumbnail(shot)
                    }
                }
            }
            .frame(height: 64)
        }
    }

    private func thumbnail(_ shot: Shot) -> some View {
        Image(uiImage: shot.thumbnail)
            .resizable()
            .aspectRatio(contentMode: .fill)
            .frame(width: 96, height: 64)
            .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
            .overlay(alignment: .bottomLeading) {
                Text("#\(shot.frame + 1)")
                    .font(.caption2.monospacedDigit().weight(.semibold))
                    .padding(.horizontal, 4)
                    .padding(.vertical, 2)
                    .background(.black.opacity(0.65), in: RoundedRectangle(cornerRadius: 4))
                    .padding(4)
            }
            .contentShape(Rectangle())
            .onTapGesture { preview(shot) }
            .contextMenu {
                Button("Save to Photos", systemImage: "square.and.arrow.down") {
                    Task { await model.saveToPhotos([shot]) }
                }
                ShareLink(item: shot.url) { Label("Share", systemImage: "square.and.arrow.up") }
                Button("Go to Frame", systemImage: "arrow.uturn.backward") { model.seek(toFrame: shot.frame) }
                Button("Remove", systemImage: "trash", role: .destructive) { model.remove(shot) }
            }
            .accessibilityElement()
            .accessibilityLabel("Frame \(shot.frame + 1)")
            .accessibilityAddTraits(.isButton)
    }
}

struct ShotPreview: View {
    let shot: Shot
    let model: EditorModel
    @Environment(\.dismiss) private var dismiss
    @State private var image: UIImage?

    var body: some View {
        NavigationStack {
            ZStack {
                Color.black.ignoresSafeArea()
                Image(uiImage: image ?? shot.thumbnail)
                    .resizable()
                    .aspectRatio(contentMode: .fit)
            }
            .navigationTitle("Frame \(shot.frame + 1)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } }
                ToolbarItemGroup(placement: .bottomBar) {
                    Text("\(Int(shot.pixelSize.width))×\(Int(shot.pixelSize.height)) · \(shot.format.label) · \(fileSize)")
                        .font(.footnote.monospacedDigit())
                        .foregroundStyle(.secondary)
                    Spacer()
                    ShareLink(item: shot.url)
                    Button("Save", systemImage: "square.and.arrow.down") {
                        Task { await model.saveToPhotos([shot]); dismiss() }
                    }
                }
            }
            .task { image = UIImage(contentsOfFile: shot.url.path) }
        }
    }

    private var fileSize: String {
        let bytes = (try? shot.url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
        return ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)
    }
}

// MARK: - Video details

struct VideoInfoView: View {
    let model: EditorModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section("Video") {
                    LabeledContent("Resolution", value: "\(Int(model.pixelSize.width)) × \(Int(model.pixelSize.height))")
                    LabeledContent("Frame rate", value: "\(Timecode.rateLabel(model.fps)) fps")
                    LabeledContent("Length", value: Timecode.duration(model.duration))
                    LabeledContent("Frames", value: model.frameCount.formatted())
                }
                Section {
                    LabeledContent("Recorded", value: recordedText)
                    if let location = model.metadata.location {
                        LabeledContent("Location", value: String(format: "%.5f, %.5f", location.coordinate.latitude, location.coordinate.longitude))
                        Map(initialPosition: .region(MKCoordinateRegion(
                            center: location.coordinate,
                            latitudinalMeters: 800,
                            longitudinalMeters: 800
                        ))) {
                            Marker("", coordinate: location.coordinate).tint(Color.amber)
                        }
                        .frame(height: 180)
                        .listRowInsets(EdgeInsets())
                    } else {
                        LabeledContent("Location", value: "None")
                    }
                } header: {
                    Text("Kept in grabbed frames")
                } footer: {
                    Text("Each frame's capture time is the recording time plus the frame's position in the video.")
                }
            }
            .navigationTitle(model.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
        .presentationDetents([.medium, .large])
    }

    private var recordedText: String {
        guard let date = model.metadata.creationDate else { return "Unknown" }
        var style = Date.FormatStyle(date: .abbreviated, time: .standard)
        style.timeZone = model.metadata.timeZone ?? .current
        return date.formatted(style)
    }
}
