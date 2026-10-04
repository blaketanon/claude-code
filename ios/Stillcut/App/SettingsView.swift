import SwiftUI

struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @AppStorage(Prefs.format) private var format: ExportFormat = .heif
    @AppStorage(Prefs.quality) private var quality: Double = 0.9
    @AppStorage(Prefs.keepMetadata) private var keepMetadata = true
    @AppStorage(Prefs.timeFormat) private var timeFormat: TimeFormat = .frames

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Picker("Format", selection: $format) {
                        ForEach(ExportFormat.allCases) { Text($0.label).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    if format.supportsQuality {
                        VStack(alignment: .leading, spacing: 4) {
                            HStack {
                                Text("Quality")
                                Spacer()
                                Text(quality, format: .percent.precision(.fractionLength(0)))
                                    .monospacedDigit()
                                    .foregroundStyle(.secondary)
                            }
                            Slider(value: $quality, in: 0.5...1, step: 0.01)
                        }
                    }
                } header: {
                    Text("Image format")
                } footer: {
                    Text(format.footnote)
                }

                Section {
                    Toggle("Keep date and location", isOn: $keepMetadata)
                } footer: {
                    Text("Frames are saved with the video's recording time plus the frame's position, and the place it was filmed.")
                }

                Section("Time display") {
                    Picker("Show time as", selection: $timeFormat) {
                        ForEach(TimeFormat.allCases) { Text($0.label).tag($0) }
                    }
                }

                Section {
                    LabeledContent("Version", value: AppInfo.version)
                } footer: {
                    Text("\(AppInfo.name) never uploads your videos. Everything happens on this device.")
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
    }
}
