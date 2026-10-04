import AVFoundation
import Observation
import UIKit

@MainActor
@Observable
final class EditorModel {
    enum LoadState: Equatable {
        case loading
        case ready
        case failed(String)
    }

    let source: VideoSource
    let player = AVPlayer()

    private(set) var state: LoadState = .loading
    private(set) var name = ""
    private(set) var metadata = VideoMetadata()
    private(set) var duration: Double = 0
    private(set) var fps: Double = 30
    private(set) var pixelSize: CGSize = .zero
    private(set) var currentTime: Double = 0
    private(set) var isPlaying = false
    private(set) var playbackRate: Float = 1
    private(set) var shots: [Shot] = []
    private(set) var isGrabbing = false
    var errorMessage: String?
    var notice: String?

    @ObservationIgnored private var asset: AVAsset?
    @ObservationIgnored private var timeObserver: Any?
    @ObservationIgnored private var statusObservation: NSKeyValueObservation?
    @ObservationIgnored private var seekTarget: CMTime = .zero
    @ObservationIgnored private var isSeeking = false
    @ObservationIgnored private var didStartLoading = false

    init(source: VideoSource) {
        self.source = source
    }

    var frameCount: Int { max(1, Int((duration * fps).rounded())) }
    var currentFrame: Int { min(frameCount - 1, Timecode.frameIndex(at: currentTime, fps: fps)) }
    var aspectRatio: CGFloat { pixelSize.height > 0 ? pixelSize.width / pixelSize.height : 16 / 9 }

    // MARK: Loading

    func load() async {
        guard !didStartLoading else { return }
        didStartLoading = true
        do {
            let video = try await VideoLoader.load(source)
            guard let track = try await video.asset.loadTracks(withMediaType: .video).first else {
                throw VideoLoadError.noVideoTrack
            }
            let (rate, size, transform) = try await track.load(.nominalFrameRate, .naturalSize, .preferredTransform)
            let rotated = CGRect(origin: .zero, size: size).applying(transform)

            asset = video.asset
            name = video.name
            metadata = video.metadata
            fps = rate > 0 ? Double(rate) : 30
            pixelSize = CGSize(width: abs(rotated.width), height: abs(rotated.height))
            duration = try await video.asset.load(.duration).seconds

            player.replaceCurrentItem(with: AVPlayerItem(asset: video.asset))
            player.actionAtItemEnd = .pause
            observePlayer()
            state = .ready
        } catch {
            state = .failed(error.localizedDescription)
        }
    }

    private func observePlayer() {
        let interval = CMTime(seconds: 1 / max(fps, 1), preferredTimescale: 600)
        timeObserver = player.addPeriodicTimeObserver(forInterval: interval, queue: .main) { [weak self] time in
            MainActor.assumeIsolated {
                guard let self, !self.isSeeking else { return }
                self.currentTime = time.seconds
            }
        }
        statusObservation = player.observe(\.timeControlStatus, options: [.initial, .new]) { [weak self] player, _ in
            let playing = player.timeControlStatus != .paused
            Task { @MainActor in self?.isPlaying = playing }
        }
    }

    func teardown() {
        player.pause()
        if let timeObserver { player.removeTimeObserver(timeObserver) }
        timeObserver = nil
        statusObservation = nil
    }

    // MARK: Playback

    func togglePlay() {
        guard state == .ready else { return }
        if player.timeControlStatus == .paused {
            if currentTime >= duration - 1.5 / fps {
                player.seek(to: .zero, toleranceBefore: .zero, toleranceAfter: .zero)
            }
            player.playImmediately(atRate: playbackRate)
        } else {
            player.pause()
        }
    }

    func setRate(_ rate: Float) {
        playbackRate = rate
        if isPlaying { player.rate = rate }
    }

    /// Moves by whole frames. `AVPlayerItem.step` follows the real frame timing,
    /// so it stays exact on variable-frame-rate iPhone footage.
    func step(_ frames: Int) {
        guard state == .ready, let item = player.currentItem else { return }
        player.pause()
        let canStep = frames > 0 ? item.canStepForward : item.canStepBackward
        if canStep, !isSeeking {
            item.step(byCount: frames)
            Task {
                try? await Task.sleep(for: .milliseconds(40))
                currentTime = player.currentTime().seconds
            }
        } else {
            seek(toFrame: currentFrame + frames)
        }
    }

    func seek(toFrame frame: Int) {
        let clamped = min(max(0, frame), frameCount - 1)
        scrub(to: (Double(clamped) + 0.25) / fps)
    }

    /// Frame-accurate seeking that never queues up: while one seek runs, only the
    /// latest requested time is kept and sought next.
    func scrub(to seconds: Double) {
        guard state == .ready else { return }
        if isPlaying { player.pause() }
        let clamped = min(max(0, seconds), duration)
        currentTime = clamped
        seekTarget = CMTime(seconds: clamped, preferredTimescale: 6000)
        if !isSeeking { seekToTarget() }
    }

    private func seekToTarget() {
        isSeeking = true
        let target = seekTarget
        player.seek(to: target, toleranceBefore: .zero, toleranceAfter: .zero) { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                if CMTimeCompare(target, self.seekTarget) == 0 {
                    self.isSeeking = false
                } else {
                    self.seekToTarget()
                }
            }
        }
    }

    // MARK: Grabbing

    func grab() async {
        guard let asset, state == .ready, !isGrabbing else { return }
        isGrabbing = true
        defer { isGrabbing = false }
        player.pause()

        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.requestedTimeToleranceBefore = .zero
        generator.requestedTimeToleranceAfter = .zero

        do {
            let (image, actualTime) = try await generator.image(at: player.currentTime())
            let seconds = actualTime.seconds
            let options = ExportOptions.current
            let metadata = self.metadata
            let fileName = "\(safeFileName)_\(Timecode.fileStamp(seconds: seconds, fps: fps))"

            let written = try await Task.detached(priority: .userInitiated) {
                try FrameExporter.write(image, name: fileName, options: options, metadata: metadata, frameTime: seconds)
            }.value

            let full = UIImage(cgImage: image)
            let thumbSize = CGSize(width: 240, height: 240 * CGFloat(image.height) / CGFloat(max(image.width, 1)))
            let thumbnail = await full.byPreparingThumbnail(ofSize: thumbSize) ?? full
            shots.append(Shot(
                url: written.url,
                thumbnail: thumbnail,
                frame: Timecode.frameIndex(at: seconds, fps: fps),
                time: seconds,
                pixelSize: CGSize(width: image.width, height: image.height),
                format: written.format,
                captureDate: options.keepMetadata ? metadata.creationDate?.addingTimeInterval(seconds) : nil,
                location: options.keepMetadata ? metadata.location : nil
            ))
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func remove(_ shot: Shot) {
        shots.removeAll { $0.id == shot.id }
        FrameExporter.remove(shot)
    }

    func removeAllShots() {
        shots.forEach(FrameExporter.remove)
        shots.removeAll()
    }

    func saveToPhotos(_ toSave: [Shot]) async {
        guard !toSave.isEmpty else { return }
        do {
            try await PhotoSaver.save(toSave)
            notice = toSave.count == 1 ? "Saved to Photos" : "Saved \(toSave.count) frames to Photos"
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    // MARK: Thumbnails for the scrub strip

    func stripImages(count: Int, maxDimension: CGFloat) async -> [CGImage?] {
        guard let asset, count > 0, duration > 0 else { return [] }
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.maximumSize = CGSize(width: maxDimension, height: maxDimension)
        let tolerance = CMTime(seconds: duration / Double(count) / 2, preferredTimescale: 600)
        generator.requestedTimeToleranceBefore = tolerance
        generator.requestedTimeToleranceAfter = tolerance

        let times = (0..<count).map { CMTime(seconds: (Double($0) + 0.5) / Double(count) * duration, preferredTimescale: 600) }
        var images = [CGImage?](repeating: nil, count: count)
        for await result in generator.images(for: times) {
            if let index = times.firstIndex(of: result.requestedTime), let image = try? result.image {
                images[index] = image
            }
        }
        return images
    }

    private var safeFileName: String {
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_"))
        let cleaned = String(name.unicodeScalars.map { allowed.contains($0) ? Character($0) : "-" })
        return cleaned.isEmpty ? "Frame" : String(cleaned.prefix(60))
    }
}
