import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var model: ResponderModel
    @Environment(\.scenePhase) private var scenePhase
    @State private var showSettings = false

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            ScrollView {
                VStack(spacing: 14) {
                    header
                    AlertBannerView(alert: model.currentAlert, acked: model.currentAlertAcked,
                                    localProximityText: model.localProximityText)
                    CompassRoseView(kin: model.kin, subjectState: model.subjectState, verified: model.subjectVerified,
                                    stale: model.connectionState != .connected || model.macViewStale)
                        .frame(height: 300)
                    SectorBarsView(sectors: model.displaySectors, lidarSupported: model.lidarSupported)
                        .frame(height: 130)
                    ackButton
                    toggles
                    footer
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
            }
        }
        .preferredColorScheme(.dark)
        .sheet(isPresented: $showSettings) {
            SettingsView()
                .environmentObject(model)
        }
        .onAppear {
            model.start()
            if model.host.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                showSettings = true
            }
        }
        .onChange(of: scenePhase) { _, phase in
            model.scenePhaseChanged(phase)
        }
    }

    private var statusColor: Color {
        switch model.connectionState {
        case .connected: return .green
        case .connecting, .waiting: return .yellow
        case .idle: return .gray
        }
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 10) {
            Circle()
                .fill(statusColor)
                .frame(width: 12, height: 12)
            VStack(alignment: .leading, spacing: 2) {
                Text("OVERWATCH")
                    .font(.headline.weight(.heavy))
                    .tracking(1.5)
                    .foregroundStyle(Color.white)
                Text(model.serverError ?? model.connectionText)
                    .font(.caption)
                    .foregroundStyle(model.serverError == nil ? Color.white.opacity(0.65) : Color.orange)
                    .lineLimit(2)
            }
            Spacer()
            if model.aiPaused {
                Text("AI PAUSED")
                    .font(.caption2.weight(.bold))
                    .padding(.horizontal, 6)
                    .padding(.vertical, 3)
                    .background(Capsule().fill(Color.purple.opacity(0.7)))
            }
            Button {
                showSettings = true
            } label: {
                Image(systemName: "gearshape.fill")
                    .font(.title2)
                    .foregroundStyle(Color.white.opacity(0.8))
            }
            .accessibilityLabel("Settings")
        }
    }

    private var ackButton: some View {
        Button {
            model.acknowledge()
        } label: {
            Text(model.currentAlertAcked ? "ACKNOWLEDGED" : "ACK")
                .font(.system(size: 36, weight: .heavy, design: .rounded))
                .frame(maxWidth: .infinity, minHeight: 84)
        }
        .buttonStyle(.borderedProminent)
        .tint(model.ackUrgent ? Color.red : Color(white: 0.35))
        .disabled(!model.canAcknowledge)
        .accessibilityHint("Acknowledges the current alert and stops its repeating vibration")
    }

    private var toggles: some View {
        HStack(spacing: 10) {
            Toggle(isOn: $model.speechEnabled) {
                Label("Voice", systemImage: model.speechEnabled ? "speaker.wave.2.fill" : "speaker.slash.fill")
                    .frame(maxWidth: .infinity)
            }
            Toggle(isOn: $model.hapticsEnabled) {
                Label("Haptics", systemImage: "iphone.radiowaves.left.and.right")
                    .frame(maxWidth: .infinity)
            }
            Toggle(isOn: $model.spatialAudioEnabled) {
                Label("3D", systemImage: "headphones")
                    .frame(maxWidth: .infinity)
            }
        }
        .toggleStyle(.button)
        .controlSize(.large)
    }

    private var footer: some View {
        let p = model.pipelineState
        let s = model.sensorsState
        var parts: [String] = []
        parts.append("ARKit \(p.trackingState)\(p.trackingReason.map { " (\($0))" } ?? "")")
        if model.lidarSupported, let valid = p.depthValidFraction {
            parts.append(String(format: "depth %.0f%%", valid * 100))
        }
        if let heading = s.headingDeg {
            parts.append(String(format: "hdg %.0f° ±%.0f", heading, s.headingAccuracyDeg ?? 0))
        }
        if let gps = s.gps, let acc = gps.accuracy_m {
            parts.append(String(format: "GPS ±%.0f m", acc))
        }
        if let battery = s.batteryPct {
            parts.append(String(format: "batt %.0f%%", battery))
        }
        parts.append(String(format: "%.0f obs/s", p.observationRateHz))
        if p.personCount > 0 {
            parts.append("\(p.personCount) person\(p.personCount == 1 ? "" : "s")")
        }
        return VStack(alignment: .leading, spacing: 4) {
            Text(parts.joined(separator: " · "))
                .font(.caption2.monospacedDigit())
                .foregroundStyle(Color.white.opacity(0.5))
            if let event = model.arEvent {
                Text(event)
                    .font(.caption2)
                    .foregroundStyle(Color.orange.opacity(0.8))
            }
            if !model.arSupported {
                Text("This device does not support ARKit world tracking.")
                    .font(.caption)
                    .foregroundStyle(Color.red)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
