import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var model: ResponderModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Mac address, e.g. 192.168.1.20", text: $model.host)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                        .submitLabel(.go)
                        .onSubmit { model.connect() }
                    SecureField("Access token (OVERWATCH_TOKEN, optional)", text: $model.token)
                    Button(model.wantsConnection ? "Reconnect" : "Connect") {
                        model.connect()
                    }
                    if model.wantsConnection {
                        Button("Disconnect", role: .destructive) {
                            model.disconnect()
                        }
                    }
                    LabeledContent("Status", value: model.serverError ?? model.connectionText)
                } header: {
                    Text("Mac server")
                } footer: {
                    Text("The Mac must run the backend with OVERWATCH_MODE=LIVE. Port 8000 is used when none is given. "
                         + "Local LiDAR proximity alerts work even when this is not connected.")
                }

                Section("Feedback") {
                    Toggle("Spoken alerts", isOn: $model.speechEnabled)
                    Toggle("Haptics", isOn: $model.hapticsEnabled)
                    Toggle("Spatial earcon before speech", isOn: $model.spatialAudioEnabled)
                    Toggle("Send camera video to the Mac", isOn: $model.sendVideo)
                }

                Section {
                    Toggle("AirPods head tracking", isOn: $model.headTrackingEnabled)
                        .disabled(!model.sensorsState.headTrackingAvailable)
                    Button("Zero: I'm facing where the phone points") {
                        model.zeroHeadYaw()
                    }
                    .disabled(!model.headTrackingEnabled)
                    if let yaw = model.pipelineState.headYawDeg {
                        LabeledContent("Head vs phone", value: String(format: "%+.0f°", yaw))
                    }
                } header: {
                    Text("Head tracking")
                } footer: {
                    Text("Optional. With supported AirPods the Mac turns alert bearings into where your head points.")
                }

                Section("Sensors") {
                    LabeledContent("ARKit", value: trackingText)
                    LabeledContent("LiDAR", value: model.lidarSupported ? "sceneDepth" : "not available")
                    if let valid = model.pipelineState.depthValidFraction {
                        LabeledContent("Valid depth", value: String(format: "%.0f %%", valid * 100))
                    }
                    if let floor = model.pipelineState.floorYM {
                        LabeledContent("Floor (ARKit y)", value: String(format: "%.2f m", floor))
                    }
                    LabeledContent("Held", value: model.pipelineState.orientation)
                    LabeledContent("Compass", value: headingText)
                    LabeledContent("GPS", value: gpsText)
                    LabeledContent("Uplink", value: String(format: "%.1f obs/s · %ld JPEG · %.0f ms/frame",
                                                           model.pipelineState.observationRateHz,
                                                           model.pipelineState.jpegsSent,
                                                           model.pipelineState.processingMs))
                    if !model.sensorsState.locationAuthorized {
                        Text("Location permission is off: no GPS and only magnetic heading.")
                            .font(.footnote)
                            .foregroundStyle(Color.orange)
                    }
                }

                if !model.transcript.isEmpty {
                    Section("Recent announcements") {
                        ForEach(Array(model.transcript.suffix(8).enumerated()), id: \.offset) { _, line in
                            Text(line).font(.footnote)
                        }
                    }
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    private var trackingText: String {
        let p = model.pipelineState
        if let reason = p.trackingReason { return "\(p.trackingState) (\(reason))" }
        return p.trackingState
    }

    private var headingText: String {
        guard let heading = model.sensorsState.headingDeg else { return "no heading" }
        return String(format: "%.0f° true, ±%.0f°", heading, model.sensorsState.headingAccuracyDeg ?? 0)
    }

    private var gpsText: String {
        guard let gps = model.sensorsState.gps else { return "no fix" }
        return String(format: "%.5f, %.5f ±%.0f m", gps.lat, gps.lon, gps.accuracy_m ?? 0)
    }
}
