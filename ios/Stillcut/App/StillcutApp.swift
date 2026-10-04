import SwiftUI

@main
struct StillcutApp: App {
    var body: some Scene {
        WindowGroup {
            LibraryView()
                .tint(Color.amber)
                .preferredColorScheme(.dark)
                .task { FrameExporter.purgeOldFiles() }
        }
    }
}

enum AppInfo {
    /// Display name used in the UI and written into exported images' TIFF Software tag.
    static let name = "Stillcut"

    static var version: String {
        let info = Bundle.main.infoDictionary
        let short = info?["CFBundleShortVersionString"] as? String ?? "1.0"
        let build = info?["CFBundleVersion"] as? String ?? "1"
        return "\(short) (\(build))"
    }
}

extension ShapeStyle where Self == Color {
    /// Grease-pencil amber, the app's single accent.
    static var amber: Color { Color(red: 1.0, green: 0.714, blue: 0.153) }
    static var panel: Color { Color(red: 0.07, green: 0.078, blue: 0.09) }
}
