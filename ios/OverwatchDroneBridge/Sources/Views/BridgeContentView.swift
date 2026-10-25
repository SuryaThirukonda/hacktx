import SwiftUI

/// Landscape UI for the phone mounted on the DJI RC: FPV preview + the big DISABLE VIRTUAL STICK button on the left;
/// links, virtual-stick state, telemetry, simulator controls and the scrolling audit log on the right.
struct BridgeContentView: View {
    @EnvironmentObject private var model: BridgeModel
    @Environment(\.scenePhase) private var scenePhase
    @State private var showSettings = false
    @State private var confirmArm = false
    @State private var confirmRTL = false
    @State private var confirmTranslation = false

    private var vs: VirtualStickController.Status { model.ui.vstick }
    private var tel: TelemetryCollector.Cache { model.ui.telemetry }

    var body: some View {
        HStack(spacing: 10) {
            leftColumn
            rightColumn
                .frame(width: 370)
        }
        .padding(8)
        .background(Color.black.ignoresSafeArea())
        .preferredColorScheme(.dark)
        .onChange(of: scenePhase) { _, phase in
            model.scenePhaseChanged(phase)
        }
        .sheet(isPresented: $showSettings) {
            BridgeSettingsView()
                .environmentObject(model)
        }
        .alert("Arm REAL flight?", isPresented: $confirmArm) {
            Button("Arm real flight", role: .destructive) { model.setRealFlightArmed(true) }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Virtual-stick commands from the Mac will be able to fly the real aircraft (the Mac also needs autonomy.allow_real_flight). Keep your thumbs on the sticks: any stick input or the DISABLE button returns control to you.")
        }
        .alert("Return to home?", isPresented: $confirmRTL) {
            Button("RTL", role: .destructive) { model.returnToHome() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Disables virtual stick and starts DJI go-home.")
        }
        .alert("Allow aircraft translation?", isPresented: $confirmTranslation) {
            Button("Allow translation", role: .destructive) { model.setTranslationArmed(true) }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Full follow may move forward/backward, sideways and vertically. Requires a configured outdoor geofence, GPS and valid calibration on the Mac. Phone limits: 1 m/s horizontal, 0.5 m/s vertical, 15°/s yaw. Changing this setting disables control; re-arm with neutral sticks. RC input always takes priority.")
        }
    }

    // MARK: Left: FPV + safety button

    private var leftColumn: some View {
        VStack(spacing: 8) {
            ZStack(alignment: .top) {
                if model.previewEnabled {
                    FPVPreviewView(controller: model.controller.preview)
                } else {
                    Color(white: 0.08)
                    Text("Preview off")
                        .foregroundStyle(Color.white.opacity(0.4))
                        .frame(maxHeight: .infinity)
                }
                VStack(spacing: 6) {
                    if vs.pilotInControl {
                        banner("PILOT IN CONTROL", detail: vs.pilotReason, color: .orange)
                    }
                    if vs.phase == .enabled {
                        if vs.cameraOnly {
                            banner("YAW + GIMBAL ONLY", detail: "Position and altitude remain with the pilot", color: .blue)
                        } else {
                            banner("FULL-MOTION FOLLOW", detail: "RC input cancels Mac control", color: .orange)
                        }
                        banner(vs.watchdogHover ? "MAC CONTROL · HOVER (no fresh command)" : "MAC CONTROL · VIRTUAL STICK",
                               detail: nil, color: vs.watchdogHover ? .yellow : .green)
                    }
                    if tel.simActive {
                        banner("DJI SIMULATOR · PROPS OFF", detail: nil, color: .blue)
                    }
                }
                .padding(8)
            }
            .clipShape(RoundedRectangle(cornerRadius: 10))

            Button {
                model.disableVirtualStick()
            } label: {
                VStack(spacing: 2) {
                    Text("DISABLE VIRTUAL STICK")
                        .font(.system(size: 26, weight: .black, design: .rounded))
                    Text(vs.phase == .enabled ? "The Mac is flying: tap to hand control to the pilot"
                                              : "Virtual stick \(vs.phase.rawValue.lowercased())")
                        .font(.caption)
                }
                .frame(maxWidth: .infinity, minHeight: 84)
            }
            .buttonStyle(.borderedProminent)
            .tint(.red)

            if vs.inhibited {
                Button {
                    model.rearm()
                } label: {
                    Text("RE-ARM MAC CONTROL")
                        .font(.headline)
                        .frame(maxWidth: .infinity, minHeight: 40)
                }
                .buttonStyle(.bordered)
                .tint(.orange)
            }
        }
    }

    private func banner(_ title: String, detail: String?, color: Color) -> some View {
        VStack(spacing: 2) {
            Text(title)
                .font(.headline.weight(.heavy))
            if let detail = detail {
                Text(detail)
                    .font(.caption)
                    .lineLimit(2)
                    .multilineTextAlignment(.center)
            }
        }
        .foregroundStyle(Color.black)
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(Capsule().fill(color))
    }

    // MARK: Right: status, telemetry, controls, audit

    private var rightColumn: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                linksSection
                vstickSection
                telemetrySection
                controlsSection
                videoSection
                auditSection
            }
        }
    }

    private var linksSection: some View {
        section("LINKS") {
            HStack {
                Circle().fill(model.ui.productConnected ? Color.green : Color.gray).frame(width: 9, height: 9)
                Text(model.ui.productConnected ? (model.ui.productModel ?? "Aircraft") : "No aircraft")
                    .font(.subheadline.weight(.semibold))
                Spacer()
                Text("SDK \(model.ui.sdkVersion)").font(.caption2).foregroundStyle(.secondary)
            }
            Text(model.ui.registration).font(.caption).foregroundStyle(.secondary)
            if let warning = model.ui.modelWarning {
                Text(warning).font(.caption).foregroundStyle(Color.orange)
            }
            HStack {
                Circle().fill(socketColor).frame(width: 9, height: 9)
                Text(socketText).font(.subheadline.weight(.semibold)).lineLimit(2)
                Spacer()
                Button {
                    showSettings = true
                } label: {
                    Image(systemName: "gearshape.fill")
                }
                .accessibilityLabel("Settings")
            }
            if let error = model.ui.macError ?? model.connectError {
                Text(error).font(.caption).foregroundStyle(Color.orange)
            }
        }
    }

    private var socketColor: Color {
        switch model.ui.socket.state {
        case .connected: return .green
        case .connecting, .waiting: return .yellow
        case .idle: return .gray
        }
    }

    private var socketText: String {
        let s = model.ui.socket
        switch s.state {
        case .connected:
            let rtt = s.rttMs.map { String(format: " · %.0f ms", $0) } ?? ""
            return "Mac connected\(rtt)"
        case .connecting:
            return "Mac: connecting…"
        case .waiting:
            return String(format: "Mac: retry in %.1f s", s.nextRetryInS ?? 0) + (s.lastError.map { " (\($0))" } ?? "")
        case .idle:
            return model.wantsConnection ? "Mac: offline" : "Mac: not connected"
        }
    }

    private var vstickSection: some View {
        section("VIRTUAL STICK") {
            row("State", vs.phase.rawValue + (vs.aircraftReportsEnabled.map { $0 ? " (aircraft: on)" : " (aircraft: off)" } ?? ""))
            if let sp = vs.setpoint {
                row("Setpoint", String(format: "fwd %.1f  right %.1f  up %.1f  yaw %.0f", sp.forward, sp.right, sp.up, sp.yawRate))
                row("Setpoint age", vs.setpointAgeMs.map { String(format: "%.0f ms", $0) } ?? "—")
            }
            if let sent = vs.lastSent, vs.phase != .disabled {
                row("Sent (capped)", String(format: "fwd %.1f  right %.1f  up %.1f  yaw %.0f", sent.forward, sent.right, sent.up, sent.yawRate))
            }
            row("Mac link age", vs.macLinkAgeMs.map { String(format: "%.0f ms", $0) } ?? "never")
            row("Commands", "\(vs.commandsLatched) latched · \(vs.sendErrors) send errors")
            if let last = model.ui.lastCommand {
                row("Last", last)
            }
            Toggle(isOn: Binding(get: { model.ui.realFlightArmed },
                                 set: { armed in
                                     if armed { confirmArm = true } else { model.setRealFlightArmed(false) }
                                 })) {
                Text("Real flight armed").font(.subheadline.weight(.semibold))
            }
            .tint(.red)
            Text("Off: virtual stick only while the DJI simulator runs (props off).")
                .font(.caption2)
                .foregroundStyle(.secondary)
            Toggle(isOn: Binding(get: { model.ui.translationArmed }, set: { armed in
                if armed { confirmTranslation = true } else { model.setTranslationArmed(false) }
            })) { Text("Allow aircraft translation").font(.subheadline.weight(.semibold)) }
                .disabled(!model.ui.realFlightArmed)
                .tint(.red)
        }
    }

    private var telemetrySection: some View {
        section("TELEMETRY") {
            row("Position", tel.lat.flatMap { lat in tel.lon.map { String(format: "%.6f, %.6f", lat, $0) } } ?? "no GPS fix")
            row("Alt (rel)", fmt(tel.altRelM, "%.1f m"))
            row("Yaw / pitch / roll", String(format: "%@ / %.1f / %.1f", fmt(tel.yawDeg, "%.1f"), tel.pitchDeg, tel.rollDeg))
            row("Vel NED", tel.velNED.map { String(format: "%.1f, %.1f, %.1f m/s", $0[0], $0[1], $0[2]) } ?? "—")
            row("Gimbal pitch", fmt(tel.gimbalPitchDeg, "%.0f°"))
            row("Battery", fmt(tel.batteryPct, "%.0f %%"))
            row("GPS", "level \(tel.gpsLevel.map { "\($0)" } ?? "—") · \(tel.sats.map { "\($0)" } ?? "—") sats")
            row("Flight", "\(tel.flying ? "FLYING" : "on ground") · \(tel.flightMode ?? "—")")
            row("Uplink", "\(model.ui.telemetrySent) telemetry msgs")
        }
    }

    private var controlsSection: some View {
        section("CONTROLS") {
            HStack {
                Button(tel.simActive ? "Stop simulator" : "Start simulator") {
                    if tel.simActive { model.stopSimulator() } else { model.startSimulator() }
                }
                .buttonStyle(.bordered)
                .disabled(!model.ui.productConnected || (tel.flying && !tel.simActive))
                Spacer()
                Button("RTL") { confirmRTL = true }
                    .buttonStyle(.bordered)
                    .tint(.orange)
                    .disabled(!model.ui.productConnected)
            }
            HStack {
                Button("Sim takeoff") { model.takeoffInSimulator() }
                    .buttonStyle(.bordered)
                    .disabled(!tel.simActive)
                Button("Sim land") { model.landInSimulator() }
                    .buttonStyle(.bordered)
                    .disabled(!tel.simActive)
            }
            Text("Simulator start uses the aircraft's GPS position, else the default geo reference (Austin).")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }

    private var videoSection: some View {
        section("VIDEO") {
            Picker("Uplink", selection: $model.videoMode) {
                ForEach(VideoForwarder.UplinkMode.allCases) { mode in
                    Text(mode.label).tag(mode)
                }
            }
            .pickerStyle(.segmented)
            let v = model.ui.video
            row("Feed in", "\(v.chunksIn) chunks · \(v.bytesIn / 1024) KB")
            row("Sent", "\(v.framesSent) H.264 · \(v.jpegsSent) JPEG · \(v.dropped) dropped · \(v.resyncs) resyncs")
            if v.stripAUD {
                row("Mini 2 AUD strip", "on")
            }
        }
    }

    private var auditSection: some View {
        section("AUDIT LOG") {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 2) {
                        ForEach(model.ui.audit) { entry in
                            Text(entry.line)
                                .font(.system(size: 11, design: .monospaced))
                                .foregroundStyle(auditColor(entry.text))
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .id(entry.id)
                        }
                    }
                }
                .frame(height: 230)
                .onChange(of: model.ui.audit.last?.id) { _, id in
                    if let id = id {
                        proxy.scrollTo(id, anchor: .bottom)
                    }
                }
            }
        }
    }

    private func auditColor(_ text: String) -> Color {
        let t = text.lowercased()
        if t.contains("e-stop") || t.contains("failed") || t.contains("pilot in control") { return .orange }
        if t.contains("watchdog") { return .yellow }
        return Color.white.opacity(0.75)
    }

    // MARK: Helpers

    private func section<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.caption.weight(.heavy))
                .tracking(1.2)
                .foregroundStyle(Color.white.opacity(0.55))
            content()
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color.white.opacity(0.06)))
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer(minLength: 8)
            Text(value)
                .font(.caption.monospacedDigit())
                .multilineTextAlignment(.trailing)
        }
    }

    private func fmt(_ value: Double?, _ format: String) -> String {
        guard let v = value else { return "—" }
        return String(format: format, v)
    }
}

struct BridgeSettingsView: View {
    @EnvironmentObject private var model: BridgeModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Mac address, e.g. 192.168.1.20", text: $model.host)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    SecureField("Access token (OVERWATCH_TOKEN, optional)", text: $model.token)
                    Button(model.wantsConnection ? "Reconnect" : "Connect") { model.connect() }
                    if model.wantsConnection {
                        Button("Disconnect", role: .destructive) { model.disconnect() }
                    }
                    if let error = model.connectError {
                        Text(error).font(.footnote).foregroundStyle(Color.orange)
                    }
                } header: {
                    Text("Mac server (/ws/bridge)")
                } footer: {
                    Text("Run the backend with OVERWATCH_MODE=LIVE. Port 8000 is used when none is given.")
                }
                Section("Preview") {
                    Toggle("Local FPV preview", isOn: $model.previewEnabled)
                    Text("JPEG uplink mode needs the preview on (it snapshots the decoded picture).")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Bridge settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}
