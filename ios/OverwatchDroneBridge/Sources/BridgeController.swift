import CoreLocation
import DJISDK
import Foundation

struct AuditEntry: Identifiable, Equatable {
    let id: Int
    let uptimeS: Double
    let text: String

    var line: String { String(format: "%8.1f  ", uptimeS) + text }
}

/// Everything the bridge UI shows, published to the main queue at 5 Hz.
struct BridgeUISnapshot {
    var registration = "Registering with DJI…"
    var registered = false
    var productConnected = false
    var productModel: String?
    var modelWarning: String?
    var sdkVersion = ""
    var socket = ReconnectingWebSocket.Snapshot()
    var vstick = VirtualStickController.Status()
    var telemetry = TelemetryCollector.Cache()
    var telemetrySent = 0
    var audit: [AuditEntry] = []
    var realFlightArmed = false
    var translationArmed = false
    var macError: String?
    var lastCommand: String?
    var video = VideoForwarder.Stats()
}

/// Core of the DJI bridge (iPhone A on the RC): DJI product ↔ `/ws/bridge`.
/// Up: telemetry (10 Hz), video (0x02 H.264 or 0x01 JPEG), hello/status/audit, sync replies.
/// Down: BridgeCommand (vstick, gimbal, mode, hover, rtl, estop, sim_start, sim_stop, ping) and sync.
/// All state lives on `core` (serial); DJI delegate/completion callbacks and socket callbacks hop onto it.
final class BridgeController: NSObject, DJIRemoteControllerDelegate {
    static let path = "/ws/bridge"
    /// config/default.yaml world.geo_reference — DJI simulator start point when sim_start carries no lat/lon.
    static let defaultSimLocation = CLLocationCoordinate2D(latitude: 30.26690, longitude: -97.77290)

    let core: DispatchQueue
    let socket = ReconnectingWebSocket(label: "bridge", maxInFlightBytes: 2 * 1024 * 1024,
                                       stateHighWaterBytes: 512 * 1024, stateMaxAgeMs: 400)
    let product = ProductLink()
    let telemetry = TelemetryCollector()
    let video = VideoForwarder()
    let preview = FPVPreviewController()
    let vstick: VirtualStickController

    /// Delivered on the main queue.
    var onUI: ((BridgeUISnapshot) -> Void)?

    private let realFlightArmed = Locked(false)
    private let translationArmed = Locked(false)
    private var telemetryTimer: DispatchSourceTimer?
    private var uiTimer: DispatchSourceTimer?
    private var telemetrySeq = 0
    private var lastTelemetryFcTime: Double?
    private var telemetrySent = 0
    private var audit: [AuditEntry] = []
    private var auditSeq = 0
    private var outbox: [String] = []           // audit lines produced while the Mac link was down
    private var registration = "Registering with DJI…"
    private var registered = false
    private var productConnected = false
    private var productModel: String?
    private var macError: String?
    private var lastCommand: String?
    private var lastGimbalErrorLogS = -1.0e9
    private var started = false
    private var serverClockOffsetMs: Double?
    private var externalSDK = false
    private weak var externalAircraft: DJIAircraft?
    private weak var externalFlightController: DJIFlightController?
    private weak var externalRemoteController: DJIRemoteController?

    override init() {
        let queue = DispatchQueue(label: "com.overwatch.bridge.core", qos: .userInteractive)
        core = queue
        vstick = VirtualStickController(queue: queue)
        super.init()
    }

    // MARK: - Start-up (main thread)

    func start(externalSDK: Bool = false) {
        guard !started else { return }
        started = true
        self.externalSDK = externalSDK

        vstick.audit = { [weak self] text in self?.log(text) }                                  // core
        vstick.onPhaseChange = { [weak self] _ in self?.sendStatus() }                          // core
        vstick.onPilotOverride = { [weak self] reason in
            guard let self = self else { return }
            if let text = WireJSON.encode(PilotOverrideMessage(text: reason)) {
                self.socket.sendControl(text)
            }
            self.sendStatus()
        }
        vstick.isSimulatorActive = { [weak self] in self?.telemetry.snapshot.simActive ?? false }
        vstick.isRealFlightArmed = { [weak self] in self?.realFlightArmed.current ?? false }
        vstick.isTranslationArmed = { [weak self] in self?.translationArmed.current ?? false }
        vstick.isFlying = { [weak self] in self?.telemetry.snapshot.flying ?? false }

        socket.onOpen = { [weak self] in self?.core.async { self?.socketOpened() } }
        socket.onClose = { [weak self] reason in self?.core.async { self?.log("Mac link down: \(reason ?? "closed")", toMac: false) } }
        socket.onText = { [weak self] text, rxMs in self?.handleText(text, rxMs: rxMs) }      // socket queue
        socket.onBinary = { [weak self] _, _ in self?.core.async { self?.vstick.noteMacMessage() } }

        product.onRegistration = { [weak self] ok, message in
            self?.core.async {
                guard let self = self else { return }
                self.registered = ok
                self.registration = message
                self.log(message)
            }
        }
        product.onProductChange = { [weak self] in self?.productChanged() }       // main
        product.onComponentChange = { [weak self] in self?.productChanged() }     // main

        let socket = self.socket
        video.sendFrame = { data, onDrop in socket.sendDroppable(data, onDrop: onDrop) }
        video.canSend = { socket.canAcceptDroppable }

        core.async {
            self.vstick.start()
            self.startTimers()
            self.log("bridge started (DJI SDK \(ProductLink.sdkVersion))", toMac: false)
        }
        if externalSDK {
            registration = "Using the existing DJI SDK sample registration and FPV preview"
            refreshExternalSDK()
        } else {
            product.register()
        }
    }

    /// Embedded into the existing SDK FPV screen: never re-register DJI or take over its preview/video feed.
    func refreshExternalSDK() {
        guard externalSDK else { return }
        let aircraft = ProductLink.aircraft
        if aircraft !== externalAircraft || aircraft?.flightController !== externalFlightController
            || aircraft?.remoteController !== externalRemoteController {
            externalAircraft = aircraft
            externalFlightController = aircraft?.flightController
            externalRemoteController = aircraft?.remoteController
            productChanged()
        }
    }

    func sendExternalJPEG(_ jpeg: Data) {
        guard externalSDK, socket.canAcceptDroppable else { return }
        socket.sendDroppable(Wire.binaryFrame(kind: .jpeg, captureTsMs: DeviceClock.nowMs(), payload: jpeg), onDrop: {})
    }

    private func startTimers() {
        // 20 Hz poll, but each 10 Hz DJIFlightControllerState is sent exactly once (<= 50 ms after it arrived).
        let tel = DispatchSource.makeTimerSource(queue: core)
        tel.schedule(deadline: .now() + 0.05, repeating: 0.05, leeway: .milliseconds(5))
        tel.setEventHandler { [weak self] in self?.telemetryTick() }
        tel.resume()
        telemetryTimer = tel

        let ui = DispatchSource.makeTimerSource(queue: core)
        ui.schedule(deadline: .now() + 0.2, repeating: 0.2, leeway: .milliseconds(20))
        ui.setEventHandler { [weak self] in
            self?.publishUI()
            self?.sendStatus()
        }
        ui.resume()
        uiTimer = ui
    }

    // MARK: - Mac link

    func connect(host: String, token: String) -> Bool {
        guard let url = Wire.socketURL(host: host, path: Self.path, token: token) else { return false }
        socket.connect(to: url)
        return true
    }

    func disconnect() {
        socket.disconnect()
    }

    private func socketOpened() {
        serverClockOffsetMs = nil
        macError = nil
        vstick.noteMacMessage()
        vstick.macLinkOpened()
        video.markNeedResync()  // the Mac's decoder must start at SPS/IDR
        let model = productModel ?? "no product"
        var hello = HelloMessage(device: "dji-bridge", source_id: BridgeIdentity.sourceId, app: BridgeIdentity.app,
                                 text: "OverwatchDroneBridge (DJI SDK \(ProductLink.sdkVersion)), product: \(model)")
        hello.pilot_override_protocol = 1
        if let text = WireJSON.encode(hello) {
            socket.sendControl(text)
        }
        sendStatus()
        for line in outbox {
            socket.sendControl(line)
        }
        outbox.removeAll()
        log("Mac link up", toMac: false)
    }

    /// Socket queue. Sync replies go out immediately; everything else is executed on the core queue.
    private func handleText(_ text: String, rxMs: Double) {
        let probe = WireJSON.decode(WireProbe.self, from: text)
        if probe?.type == "sync", let t0 = probe?.t0 {
            socket.sendControl(build: {
                WireJSON.encode(SyncReply(t0: t0, t1: rxMs, t2: DeviceClock.nowMs()))
            })
        }
        core.async { [weak self] in
            guard let self = self else { return }
            self.vstick.noteMacMessage()  // ANY message keeps the 2 s link watchdog fed
            guard let type = probe?.type else { return }
            if type == "sync", let t0 = probe?.t0 {
                let sample = rxMs - t0
                self.serverClockOffsetMs = min(self.serverClockOffsetMs ?? sample, sample)
                return
            }
            if type == "video_resync" {
                self.video.markNeedResync()
                return
            }
            if type == "error" {
                self.macError = probe?.detail ?? text
                self.log("Mac: \(self.macError ?? "error")", toMac: false)
                return
            }
            guard let command = WireJSON.decode(BridgeCommand.self, from: text) else {
                self.log("ignored undecodable message: \(text.prefix(120))", toMac: false)
                return
            }
            self.execute(command)
        }
    }

    // MARK: - BridgeCommand (core queue)

    private func execute(_ cmd: BridgeCommand) {
        if externalSDK && ["operator_rearm", "sim_start", "sim_stop"].contains(cmd.type) {
            guard cmd.string("control_token") == vstick.controlToken,
                  let offset = serverClockOffsetMs, let sent = cmd.ts_ms, sent.isFinite else { return }
            let age = DeviceClock.nowMs() - offset - sent
            guard age >= -100 && age < 500 else { return }
            if cmd.type == "operator_rearm" {
                // Explicit website request; physical RC freshness/neutral checks remain authoritative here.
                guard vstick.controllerReady else { log("web re-arm refused: RC monitor stale or sticks not neutral"); return }
                realFlightArmed.set(cmd.flag("allow_real_flight") == true)
                translationArmed.set(cmd.flag("allow_translation") == true && realFlightArmed.current)
                vstick.operatorRearm()
                sendStatus()
                return
            }
        }
        if vstick.inhibited && ["vstick", "gimbal", "rtl"].contains(cmd.type) { return }
        let needsToken = ["vstick", "gimbal", "rtl"].contains(cmd.type)
            || (cmd.type == "mode" && cmd.flag("vstick") == true)
        if needsToken && (vstick.inhibited || !vstick.controllerReady
                          || cmd.string("control_token") != vstick.controlToken) { return }
        if cmd.type != "ping" && cmd.type != "vstick" {
            lastCommand = "\(cmd.type) #\(Int(cmd.seq ?? 0))"
        }
        switch cmd.type {
        case "vstick":
            let now = DeviceClock.nowS()
            guard let offset = serverClockOffsetMs, let sent = cmd.number("ts_ms") ?? cmd.ts_ms,
                  sent.isFinite else { return }
            let ageMs = DeviceClock.nowMs() - offset - sent
            let ttlMs = min(300.0, cmd.number("ttl_ms") ?? 300.0)
            guard ageMs.isFinite, ageMs >= -100, ageMs < ttlMs else { return }
            let sp = VirtualStickController.Setpoint(
                forward: cmd.number("forward_mps") ?? 0,
                right: cmd.number("right_mps") ?? 0,
                up: cmd.number("up_mps") ?? 0,
                yawRate: cmd.number("yaw_rate_dps") ?? 0,
                ttlS: max(0.0, ttlMs - max(0, ageMs)) / 1000.0,
                seq: Int(cmd.number("seq") ?? cmd.seq ?? 0),
                rxS: now)
            vstick.latch(sp)
            lastCommand = String(format: "vstick #%ld  fwd %.1f  right %.1f  up %.1f  yaw %.0f", sp.seq, sp.forward,
                                 sp.right, sp.up, sp.yawRate)
        case "gimbal":
            rotateGimbal(pitchDeg: cmd.number("pitch_deg"))
        case "mode":
            if let enable = cmd.flag("vstick") {
                if enable {
                    vstick.configureProfile(cmd.string("motion_profile"))
                    vstick.requestEnable(reason: "Mac mode command")
                } else {
                    vstick.requestDisable(reason: "virtual stick disabled by the Mac: PILOT IN CONTROL")
                }
            } else {
                log("mode command without a vstick flag ignored")
            }
        case "hover":
            vstick.hover()
        case "rtl":
            if vstick.cameraOnly {
                vstick.requestDisable(reason: "camera-only profile: automatic translating RTL refused; pilot in control")
            } else {
                vstick.rtl()
            }
        case "estop":
            vstick.estop()
        case "sim_start":
            startSimulator(lat: cmd.number("lat"), lon: cmd.number("lon"))
        case "sim_stop":
            stopSimulator()
        case "ping":
            break
        default:
            log("unknown command type '\(cmd.type)' ignored")
        }
    }

    private func rotateGimbal(pitchDeg: Double?) {
        guard let pitch = pitchDeg, pitch.isFinite else {
            log("gimbal command without a valid pitch_deg ignored")
            return
        }
        guard let gimbal = ProductLink.aircraft?.gimbal else {
            log("gimbal command ignored: no gimbal connected")
            return
        }
        let clamped = min(0.0, max(-90.0, pitch))
        // VERIFY: `ignore:` semantics in MSDK 4.16 (DJI docs: "true if the app can control the gimbal").
        let rotation = DJIGimbalRotation(pitchValue: NSNumber(value: clamped), rollValue: nil, yawValue: nil,
                                         time: 0.5, mode: .absoluteAngle, ignore: true)
        gimbal.rotate(with: rotation, completion: { [weak self] error in
            guard let error = error, let self = self else { return }
            self.core.async {
                let now = DeviceClock.nowS()
                if now - self.lastGimbalErrorLogS > 5.0 {
                    self.lastGimbalErrorLogS = now
                    self.log("gimbal rotate failed: \(error.localizedDescription)")
                }
            }
        })
    }

    /// `sim_start {lat, lon}`: DJI simulator at 20 Hz with 12 satellites (aircraft must be on the ground, motors off).
    private func startSimulator(lat: Double?, lon: Double?) {
        guard let fc = ProductLink.flightController, let simulator = fc.simulator else {
            log("sim_start refused: this product exposes no DJI simulator")
            return
        }
        if telemetry.snapshot.flying {
            log("sim_start refused: aircraft is flying")
            return
        }
        var location = Self.defaultSimLocation
        if let lat = lat, let lon = lon {
            location = CLLocationCoordinate2D(latitude: lat, longitude: lon)
        } else if let lat = telemetry.snapshot.lat, let lon = telemetry.snapshot.lon {
            location = CLLocationCoordinate2D(latitude: lat, longitude: lon)
        }
        guard CLLocationCoordinate2DIsValid(location) else {
            log("sim_start refused: invalid location")
            return
        }
        let where_ = String(format: "%.5f, %.5f", location.latitude, location.longitude)
        // VERIFY: Swift name of -startWithLocation:updateFrequency:GPSSatellitesNumber:withCompletion: (MSDK 4.16),
        // and that the Mini 2 exposes `fc.simulator` at all (if nil we refuse with an audit line).
        simulator.start(withLocation: location, updateFrequency: 20, gpsSatellitesNumber: 12, withCompletion: { [weak self] error in
            self?.core.async {
                if let error = error {
                    self?.log("DJI simulator start FAILED: \(error.localizedDescription)")
                } else {
                    self?.log("DJI simulator started at \(where_) (20 Hz, 12 satellites): props OFF test mode")
                    self?.sendStatus()
                }
            }
        })
    }

    private func stopSimulator() {
        guard let simulator = ProductLink.flightController?.simulator else {
            log("sim_stop ignored: no DJI simulator")
            return
        }
        if vstick.phase != .disabled && !realFlightArmed.current {
            vstick.requestDisable(reason: "simulator stopping: virtual stick disabled")
        }
        simulator.stop { [weak self] error in
            self?.core.async {
                if let error = error {
                    self?.log("DJI simulator stop FAILED: \(error.localizedDescription)")
                } else {
                    self?.log("DJI simulator stopped")
                    self?.sendStatus()
                }
            }
        }
    }

    // MARK: - Product (main thread → core)

    private func productChanged() {
        let aircraft = ProductLink.aircraft
        if let aircraft = aircraft {
            aircraft.remoteController?.delegate = self
            telemetry.attach(to: aircraft)        // main thread, like DJI's samples
            if !externalSDK {
                video.attachToFeed()
                preview.updateEncoderType()
            }
        } else {
            video.detachFromFeed()
        }
        let stripAUD = FPVPreviewController.needsAUDStrip
        video.setStripAUD(stripAUD)
        let model = ProductLink.modelName
        let connected = aircraft != nil
        core.async {
            let wasConnected = self.productConnected
            self.productConnected = connected
            self.productModel = model
            if connected && !wasConnected {
                self.log("aircraft connected: \(model ?? "unknown model")")
                if !ProductLink.isExpectedModel(model) {
                    self.log("warning: \(model ?? "unknown model") is not a DJI Mini 2; the bridge is untested on it")
                }
                self.vstick.productConnected()
            } else if !connected && wasConnected {
                self.log("aircraft disconnected")
                self.telemetry.reset()
                self.vstick.productDisconnected()
            } else if connected {
                self.vstick.componentsChanged()  // a component (re)connected: re-apply control modes only
            }
            self.sendStatus()
        }
    }

    // MARK: - Telemetry (core, 10 Hz)

    private func telemetryTick() {
        let c = telemetry.snapshot
        guard let fcTime = c.fcUptimeMs, fcTime != lastTelemetryFcTime, DeviceClock.nowMs() - fcTime < 1000 else {
            return
        }
        lastTelemetryFcTime = fcTime
        telemetrySeq += 1
        let enabled = vstick.isEnabled
        let message = DroneTelemetry(
            seq: telemetrySeq,
            capture_ts_ms: fcTime,
            lat: c.lat,
            lon: c.lon,
            alt_rel_m: c.altRelM.map { Wire.rounded($0, 2) },
            yaw_deg: c.yawDeg.map { Wire.rounded($0, 2) },
            pitch_deg: Wire.rounded(c.pitchDeg, 2),
            roll_deg: Wire.rounded(c.rollDeg, 2),
            vel_ned: c.velNED.map { $0.map { Wire.rounded($0, 3) } },
            gimbal_pitch_deg: c.gimbalPitchDeg.map { Wire.rounded($0, 1) },
            battery_pct: c.batteryPct,
            gps_level: c.gpsLevel,
            sats: c.sats,
            flying: c.flying,
            vstick_enabled: enabled,
            sim: c.simActive,
            flight_mode: enabled ? "VIRTUAL_STICK" : c.flightMode)
        if let text = WireJSON.encode(message) {
            socket.sendLatest(text)
            telemetrySent += 1
        }
    }

    private func sendStatus() {
        let c = telemetry.snapshot
        let text = "product=\(productModel ?? "none") vstick=\(vstick.phase.rawValue) sim=\(c.simActive) "
            + "real_flight_armed=\(realFlightArmed.current)"
        let status = StatusMessage(text: text, product: productModel, product_connected: productConnected,
                                   vstick_enabled: vstick.isEnabled, sim: c.simActive,
                                   real_flight_armed: realFlightArmed.current,
                                   control_inhibited: vstick.inhibited || !vstick.controllerReady,
                                   pilot_reason: vstick.status().pilotReason,
                                   control_token: vstick.controlToken,
                                   motion_profile: vstick.cameraOnly ? "camera_only" : "full",
                                   translation_supported: true,
                                   translation_armed: realFlightArmed.current && translationArmed.current,
                                   web_control_supported: externalSDK)
        if let json = WireJSON.encode(status) {
            socket.sendControl(json)
        }
    }

    // MARK: - Audit log (core)

    /// Local log + `{"type":"audit"}` to the Mac (buffered while the link is down so the dashboard still learns
    /// about e.g. the 2 s watchdog after reconnecting).
    private func log(_ text: String, toMac: Bool = true) {
        auditSeq += 1
        audit.append(AuditEntry(id: auditSeq, uptimeS: DeviceClock.nowS(), text: text))
        if audit.count > 300 {
            audit.removeFirst(audit.count - 300)
        }
        guard toMac, let json = WireJSON.encode(AuditMessage(text: text)) else { return }
        if socket.snapshot.state == .connected {
            socket.sendControl(json)
        } else {
            outbox.append(json)
            if outbox.count > 50 {
                outbox.removeFirst(outbox.count - 50)
            }
        }
    }

    private func publishUI() {
        var s = BridgeUISnapshot()
        s.registration = registration
        s.registered = registered
        s.productConnected = productConnected
        s.productModel = productModel
        if productConnected && !ProductLink.isExpectedModel(productModel) {
            s.modelWarning = "Untested product: \(productModel ?? "unknown") (built for DJI Mini 2)"
        }
        s.sdkVersion = ProductLink.sdkVersion
        s.socket = socket.snapshot
        s.vstick = vstick.status()
        s.telemetry = telemetry.snapshot
        s.telemetrySent = telemetrySent
        s.audit = Array(audit.suffix(200))
        s.realFlightArmed = realFlightArmed.current
        s.translationArmed = translationArmed.current
        s.macError = macError
        s.lastCommand = lastCommand
        s.video = video.stats
        DispatchQueue.main.async { [weak self] in
            self?.onUI?(s)
        }
    }

    // MARK: - Operator actions (main thread → core)

    func operatorDisable() {
        core.async { self.vstick.operatorDisable() }
    }

    func operatorRearm() {
        core.async {
            self.vstick.operatorRearm()
            self.sendStatus()
        }
    }

    func remoteController(_ rc: DJIRemoteController, didUpdate state: DJIRCHardwareState) {
        let sticks = [Int(state.leftStick.horizontalPosition), Int(state.leftStick.verticalPosition),
                      Int(state.rightStick.horizontalPosition), Int(state.rightStick.verticalPosition)]
        core.async {
            self.vstick.observeController(sticks: sticks, wheel: Int(state.leftWheel),
                                          pause: state.pauseButton.isClicked.boolValue, home: state.goHomeButton.isClicked.boolValue,
                                          mode: Int(state.flightModeSwitch.rawValue))
        }
    }

    func setRealFlightArmed(_ armed: Bool) {
        realFlightArmed.set(armed)
        if !armed { translationArmed.set(false) }
        core.async {
            self.log(armed ? "REAL FLIGHT ARMED on the bridge: virtual stick may fly the real aircraft"
                           : "real flight disarmed: virtual stick only while the DJI simulator runs")
            if !armed && !self.telemetry.snapshot.simActive && self.vstick.phase != .disabled {
                self.vstick.requestDisable(reason: "real flight disarmed: virtual stick disabled")
            }
            self.sendStatus()
        }
    }

    func setTranslationArmed(_ armed: Bool) {
        translationArmed.set(armed && realFlightArmed.current)
        core.async {
            self.vstick.operatorDisable() // a profile permission change always requires neutral RC re-arm
            self.log(armed ? "TRANSLATION opted in locally; re-arm and explicitly start full follow"
                           : "translation disarmed; pilot control")
            self.sendStatus()
        }
    }

    func startSimulatorFromUI() {
        core.async { self.startSimulator(lat: nil, lon: nil) }
    }

    func stopSimulatorFromUI() {
        core.async { self.stopSimulator() }
    }

    func rtlFromUI() {
        core.async { self.vstick.rtl() }
    }

    /// Simulator-only convenience for the props-off test (never offered for a real flight).
    func simTakeoff() {
        core.async {
            guard self.telemetry.snapshot.simActive, let fc = ProductLink.flightController else {
                self.log("takeoff refused: only available while the DJI simulator is running")
                return
            }
            fc.startTakeoff { [weak self] error in
                self?.core.async {
                    self?.log(error.map { "simulator takeoff failed: \($0.localizedDescription)" } ?? "simulator takeoff started")
                }
            }
        }
    }

    func simLand() {
        core.async {
            guard self.telemetry.snapshot.simActive, let fc = ProductLink.flightController else {
                self.log("landing refused: only available while the DJI simulator is running")
                return
            }
            fc.startLanding { [weak self] error in
                self?.core.async {
                    self?.log(error.map { "simulator landing failed: \($0.localizedDescription)" } ?? "simulator landing started")
                }
            }
        }
    }

    func appWillBackground() {
        core.async { self.vstick.appWillBackground() }
    }

    func setVideoMode(_ mode: VideoForwarder.UplinkMode) {
        video.mode = mode
        if mode == .jpeg {
            video.startJPEGTimer()
        } else {
            video.stopJPEGTimer()
        }
        core.async { self.log("video uplink: \(mode.label)", toMac: false) }
    }
}
