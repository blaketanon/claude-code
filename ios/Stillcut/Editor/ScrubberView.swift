import AVFoundation
import SwiftUI

/// Renders an AVPlayer without system controls.
struct PlayerLayerView: UIViewRepresentable {
    let player: AVPlayer

    func makeUIView(context: Context) -> PlayerUIView {
        let view = PlayerUIView()
        view.backgroundColor = .black
        view.playerLayer.videoGravity = .resizeAspect
        view.playerLayer.player = player
        return view
    }

    func updateUIView(_ view: PlayerUIView, context: Context) {
        view.playerLayer.player = player
    }

    final class PlayerUIView: UIView {
        override static var layerClass: AnyClass { AVPlayerLayer.self }
        var playerLayer: AVPlayerLayer { layer as! AVPlayerLayer }
    }
}

/// Thumbnail strip with a playhead. Dragging sideways scrubs; sliding the finger up,
/// away from the strip, slows scrubbing down for fine positioning.
struct ScrubberView: View {
    let model: EditorModel

    @State private var images: [CGImage?] = []
    @State private var dragTime: Double?
    @State private var lastX: CGFloat = 0
    @State private var speed: ScrubSpeed = .normal

    private let height: CGFloat = 52

    var body: some View {
        VStack(spacing: 6) {
            GeometryReader { geo in
                let width = geo.size.width
                ZStack(alignment: .leading) {
                    strip(width: width)
                    Capsule()
                        .fill(.amber)
                        .frame(width: 4, height: height + 6)
                        .shadow(color: .black.opacity(0.6), radius: 1)
                        .offset(x: playheadX(width: width) - 2)
                    ForEach(model.shots) { shot in
                        Capsule()
                            .fill(.white.opacity(0.9))
                            .frame(width: 2, height: 9)
                            .offset(x: x(for: shot.time, width: width) - 1, y: -(height / 2) + 4.5)
                    }
                }
                .frame(width: width, height: height)
                .contentShape(Rectangle())
                .gesture(dragGesture(width: width))
                .task(id: "\(Int(width))-\(model.state == .ready)") { await loadImages(width: width) }
            }
            .frame(height: height)
            .accessibilityElement()
            .accessibilityLabel("Video position")
            .accessibilityValue("Frame \(model.currentFrame + 1) of \(model.frameCount)")
            .accessibilityAdjustableAction { direction in
                model.step(direction == .increment ? 1 : -1)
            }

            Text(speed.label)
                .font(.caption)
                .foregroundStyle(speed == .normal ? Color.secondary : Color.amber)
                .animation(.default, value: speed)
        }
    }

    private func strip(width: CGFloat) -> some View {
        let count = max(images.count, 1)
        let cell = width / CGFloat(count)
        return HStack(spacing: 0) {
            ForEach(images.indices, id: \.self) { index in
                Group {
                    if let image = images[index] {
                        Image(decorative: image, scale: 1)
                            .resizable()
                            .aspectRatio(contentMode: .fill)
                    } else {
                        Color.white.opacity(0.06)
                    }
                }
                .frame(width: cell, height: height)
                .clipped()
            }
        }
        .frame(width: width, height: height, alignment: .leading)
        .background(Color.white.opacity(0.06))
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }

    private func x(for time: Double, width: CGFloat) -> CGFloat {
        guard model.duration > 0 else { return 0 }
        return CGFloat(min(max(time / model.duration, 0), 1)) * width
    }

    private func playheadX(width: CGFloat) -> CGFloat {
        x(for: dragTime ?? model.currentTime, width: width)
    }

    private func dragGesture(width: CGFloat) -> some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { value in
                guard model.duration > 0, width > 0 else { return }
                if dragTime == nil {
                    let start = Double(min(max(value.startLocation.x / width, 0), 1)) * model.duration
                    dragTime = start
                    lastX = value.startLocation.x
                    model.scrub(to: start)
                }
                let newSpeed = ScrubSpeed(liftedBy: -value.translation.height)
                if newSpeed != speed { speed = newSpeed }
                let dx = value.location.x - lastX
                lastX = value.location.x
                let time = min(max((dragTime ?? 0) + Double(dx / width) * model.duration * speed.factor, 0), model.duration)
                dragTime = time
                model.scrub(to: time)
            }
            .onEnded { _ in
                dragTime = nil
                speed = .normal
            }
    }

    private func loadImages(width: CGFloat) async {
        guard width > 0, model.state == .ready else { return }
        let cellWidth = height * max(model.aspectRatio, 0.5)
        let count = min(max(Int((width / cellWidth).rounded(.up)), 4), 30)
        images = Array(repeating: nil, count: count)
        images = await model.stripImages(count: count, maxDimension: height * 3)
    }
}

enum ScrubSpeed: Equatable {
    case normal, half, quarter, fine

    init(liftedBy distance: CGFloat) {
        switch distance {
        case ..<50: self = .normal
        case ..<110: self = .half
        case ..<170: self = .quarter
        default: self = .fine
        }
    }

    var factor: Double {
        switch self {
        case .normal: 1
        case .half: 0.5
        case .quarter: 0.25
        case .fine: 0.1
        }
    }

    var label: String {
        switch self {
        case .normal: "Drag to scrub. Slide up while dragging for finer control."
        case .half: "Half-speed scrubbing"
        case .quarter: "Quarter-speed scrubbing"
        case .fine: "Fine scrubbing"
        }
    }
}
