import Foundation
import SwiftUI
import UIKit

/// Main-thread coordinator for the responder app: owns ARKit, sensors, the observation pipeline, the /ws/phone
/// transport and alert feedback, and publishes everything the UI shows.
final class ResponderModel: ObservableObject {
    private enum Keys {
        static let host = "ow.responder.host"
        static let token = "ow.responder.token"
        static let speech = "ow.responder.speech"
        static let haptics = "ow.responder.haptics"
        static let spatial = "ow.responder.spatial"
        static let video = "ow.responder.video"
        static let headTracking = "ow.responder.headTracking"
    }

    // MARK: Settings (persisted in UserDefaults)

    @Published var host: String {
        didSet { UserDefaults.standard.set(host, forKey: Keys.host) }
    }
    @Published var token: String {
        didSet { UserDefaults.standard.set(token, forKey: Keys.token) }
    }
    @Published var speechEnabled: Bool {
        didSet {
            UserDefaults.standard.set(speechEnabled, forKey: Keys.speech)
            feedback.speechEnabled = speechEnabled
        }
    }
    @Published var hapticsEnabled: Bool {
        didSet {
            UserDefaults.standard.set(hapticsEnabled, forKey: Keys.haptics)
            feedback.hapticsEnabled = hapticsEnabled
        }
    }
    @Published var spatialAudioEnabled: Bool {
        didSet {
            UserDefaults.standard.set(spatialAudioEnabled, forKey: Keys.spatial)
            feedback.spatialEnabled = spatialAudioEnabled
        }
    }
    @Published var sendVideo: Bool {
        didSet {
            UserDefaults.standard.set(sendVideo, forKey: Keys.video)
            pipeline.sendVideo.set(sendVideo)
        }
    }
    @Published var headTrackingEnabled: Bool {
        didSet {
            UserDefaults.standard.set(headTrackingEnabled, forKey: Keys.headTracking)
            if running { sensors.setHeadTracking(headTrackingEnabled) }
        }
    }

    // MARK: Status

    @Published private(set) var running = false
    @Published private(set) var wantsConnection = false
    @Published private(set) var connectionState: ReconnectingWebSocket.State = .idle
    @Published private(set) var connectionText = "Not connected"
    @Published private(set) var rttMs: Double?
    @Published private(set) var serverError: String?
    @Published private(set) var arEvent: String?
    @Published private(set) var arSupported = true
    @Published private(set) var lidarSupported = false
    @Published private(set) var pipelineState = PipelineUIState()
    @Published private(set) var sensorsState = SensorSnapshot()

    // MARK: The Mac's picture (responder_view)

    @Published private(set) var kin: RelativeKinematics?
    @Published private(set) var subjectState: String?
    @Published private(set) var subjectVerified = true
    @Published private(set) var serverMode: String?
    @Published private(set) var aiPaused = false
    @Published private(set) var serverSectors: [DepthSector] = []
    @Published private(set) var lastViewAt: Date?

    // MARK: Alerts

    @Published private(set) var currentAlert: WireAlert?
    @Published private(set) var currentAlertAcked = false
    @Published private(set) var localProximityText: String?
    @Published private(set) var transcript: [String] = []

    let feedback = AlertFeedback()
    let ar = ARSessionManager()
    let sensors: SensorHub
    let transport: CaptureTransport
    let pipeline: ObservationPipeline

    private var statusTimer: Timer?
    private var alertShownAt: Date?
    private var alertLifetimeS: TimeInterval = 6
    private var lastLocalProximityAt: Date?
    private var seenAlertIds: [String] = []
    private var lastSessionId: String?

    init() {
        let d = UserDefaults.standard
        host = d.string(forKey: Keys.host) ?? ""
        token = d.string(forKey: Keys.token) ?? ""
        speechEnabled = d.object(forKey: Keys.speech) as? Bool ?? true
        hapticsEnabled = d.object(forKey: Keys.haptics) as? Bool ?? true
        spatialAudioEnabled = d.object(forKey: Keys.spatial) as? Bool ?? true
        sendVideo = d.object(forKey: Keys.video) as? Bool ?? true
        headTrackingEnabled = d.object(forKey: Keys.headTracking) as? Bool ?? false
        let sensorHub = SensorHub()
        let socket = CaptureTransport(deviceName: UIDevice.current.name)
        sensors = sensorHub
        transport = socket
        pipeline = ObservationPipeline(transport: socket, sensors: sensorHub)
        wire()
    }

    private func wire() {
        feedback.speechEnabled = speechEnabled
        feedback.hapticsEnabled = hapticsEnabled
        feedback.spatialEnabled = spatialAudioEnabled
        pipeline.sendVideo.set(sendVideo)

        let pipeline = self.pipeline
        ar.canAcceptFrame = { !pipeline.isBusy }                    // AR delegate queue
        ar.onFrame = { frame in pipeline.submit(frame) }            // AR delegate queue
        ar.onEvent = { [weak self] text in self?.arEvent = text }  // main
        pipeline.onUIState = { [weak self] state in self?.pipelineState = state }  // main
        pipeline.notifier.onFire = { [weak self] distance in self?.localProximityFired(distanceM: distance) }  // main

        transport.onAlert = { [weak self] alert in
            DispatchQueue.main.async { self?.handleAlert(alert) }
        }
        transport.onResponderView = { [weak self] view in
            DispatchQueue.main.async { self?.handleView(view) }
        }
        transport.onTranscript = { [weak self] line in
            DispatchQueue.main.async { self?.appendTranscript(line) }
        }
        transport.onAck = { [weak self] alertId in
            DispatchQueue.main.async { self?.handleRemoteAck(alertId) }
        }
        transport.onServerError = { [weak self] detail in
            DispatchQueue.main.async { self?.serverError = detail }
        }
        transport.onOpen = { [weak self] in
            DispatchQueue.main.async {
                self?.serverError = nil
                self?.seenAlertIds.removeAll()  // alert ids restart at a-1 when the backend restarts
            }
        }
    }

    // MARK: - Lifecycle

    /// Starts ARKit + sensors (and the uplink if a host is set). Idempotent.
    func start() {
        guard !running else { return }
        running = true
        feedback.configure()
        sensors.start()
        if headTrackingEnabled { sensors.setHeadTracking(true) }
        arSupported = ARSessionManager.isSupported
        ar.start()
        lidarSupported = ar.lidarSupported
        pipeline.lidarSupported.set(ar.lidarSupported)
        UIApplication.shared.isIdleTimerDisabled = true
        if !host.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            connect()
        }
        statusTimer?.invalidate()
        statusTimer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            self?.refreshStatus()
        }
        refreshStatus()
    }

    func stop() {
        guard running else { return }
        running = false
        ar.pause()
        sensors.setHeadTracking(false)
        disconnect()
        statusTimer?.invalidate()
        statusTimer = nil
        feedback.stopRepeating()
        UIApplication.shared.isIdleTimerDisabled = false
    }

    func scenePhaseChanged(_ phase: ScenePhase) {
        if phase == .active && running {
            UIApplication.shared.isIdleTimerDisabled = true
        }
    }

    func connect() {
        serverError = nil
        if transport.connect(host: host, token: token) {
            wantsConnection = true
        } else {
            serverError = "Enter the Mac's address, e.g. 192.168.1.20 (port 8000 is the default)"
        }
        refreshStatus()
    }

    func disconnect() {
        transport.disconnect()
        wantsConnection = false
        refreshStatus()
    }

    func zeroHeadYaw() {
        pipeline.requestZeroHeadYaw()
    }

    // MARK: - Status polling (2 Hz)

    private func refreshStatus() {
        let snap = transport.snapshot
        if connectionState != snap.state { connectionState = snap.state }
        rttMs = snap.rttMs
        let text = Self.describe(snap, wanted: wantsConnection)
        if text != connectionText { connectionText = text }
        sensorsState = sensors.snapshot
        expireAlertIfNeeded()
        if let shown = lastLocalProximityAt, Date().timeIntervalSince(shown) > 3.0, localProximityText != nil {
            localProximityText = nil
        }
    }

    static func describe(_ s: ReconnectingWebSocket.Snapshot, wanted: Bool) -> String {
        switch s.state {
        case .connected:
            if let rtt = s.rttMs { return String(format: "Connected · %.0f ms RTT", rtt) }
            return "Connected"
        case .connecting:
            return "Connecting…"
        case .waiting:
            let retry = String(format: "Retrying in %.1f s", s.nextRetryInS ?? 0)
            if let err = s.lastError { return "\(retry) · \(err)" }
            return retry
        case .idle:
            return wanted ? "Offline" : "Not connected · local LiDAR alerts still active"
        }
    }

    // MARK: - Alerts

    private static func rank(_ severity: String) -> Int {
        switch severity {
        case "critical": return 0
        case "local_proximity": return 1
        case "warning": return 2
        case "sensor_fault": return 3
        case "attention": return 4
        default: return 5
        }
    }

    private func handleAlert(_ alert: WireAlert) {
        guard !seenAlertIds.contains(alert.alert_id) else { return }
        seenAlertIds.append(alert.alert_id)
        if seenAlertIds.count > 300 { seenAlertIds.removeFirst(100) }

        // The Mac echoes the phone's own LiDAR proximity as a LIDAR_PROXIMITY alert: banner yes, second buzz no.
        let echoOfLocal = alert.kind == "LIDAR_PROXIMITY"
            && (lastLocalProximityAt.map { Date().timeIntervalSince($0) < 4.0 } ?? false)

        // An un-acknowledged ack-required alert keeps the banner until acked; lower-severity alerts still sound.
        var replaceBanner = true
        if let current = currentAlert, current.requiresAck, !currentAlertAcked,
           Self.rank(alert.severity) > Self.rank(current.severity) {
            replaceBanner = false
        }
        if replaceBanner {
            show(alert)
        }
        feedback.deliver(alert, suppressFeedback: echoOfLocal)
    }

    private func show(_ alert: WireAlert) {
        currentAlert = alert
        currentAlertAcked = alert.acked ?? false
        alertShownAt = Date()
        if let ts = alert.ts_ms, let expires = alert.expires_ts_ms, expires > ts {
            alertLifetimeS = max(3.0, (expires - ts) / 1000.0)
        } else {
            alertLifetimeS = 6.0
        }
    }

    private func expireAlertIfNeeded() {
        guard let alert = currentAlert, let shown = alertShownAt else { return }
        let age = Date().timeIntervalSince(shown)
        if alert.requiresAck && !currentAlertAcked {
            if age > feedback.maxRepeatS { clearBanner() }
            return
        }
        if age > (currentAlertAcked ? 2.0 : alertLifetimeS) {
            clearBanner()
        }
    }

    private func clearBanner() {
        currentAlert = nil
        currentAlertAcked = false
        alertShownAt = nil
    }

    /// The big ACK button: `{"type":"ack","alert_id":...}` + stop the repeating haptic.
    func acknowledge() {
        guard let alert = currentAlert, !currentAlertAcked else { return }
        transport.sendAck(alertId: alert.alert_id)
        feedback.stopRepeating(alertId: alert.alert_id)
        currentAlertAcked = true
        alertShownAt = Date()  // keep "ACKED" on screen briefly
    }

    var canAcknowledge: Bool { currentAlert != nil && !currentAlertAcked }
    var ackUrgent: Bool { (currentAlert?.requiresAck ?? false) && !currentAlertAcked }

    private func handleRemoteAck(_ alertId: String) {
        feedback.stopRepeating(alertId: alertId)
        if currentAlert?.alert_id == alertId && !currentAlertAcked {
            currentAlertAcked = true
            alertShownAt = Date()
        }
    }

    private func handleView(_ view: ResponderView) {
        kin = view.kin
        subjectState = view.subject_state
        subjectVerified = view.verified ?? true
        serverMode = view.mode
        aiPaused = view.ai_paused ?? false
        serverSectors = view.sectors ?? []
        lastViewAt = Date()
        if let sid = view.session_id {
            if sid != lastSessionId {
                lastSessionId = sid
                seenAlertIds.removeAll()
            }
            pipeline.sessionId.set(sid)
        }
        // Connected after an alert went out: show the Mac's top active alert silently.
        if currentAlert == nil, let top = view.alerts?.first, !seenAlertIds.contains(top.alert_id),
           !(top.acked ?? false) {
            seenAlertIds.append(top.alert_id)
            show(top)
        }
    }

    private func appendTranscript(_ line: TranscriptLine) {
        guard let text = line.text, !text.isEmpty else { return }
        transcript.append(text)
        if transcript.count > 20 { transcript.removeFirst(transcript.count - 20) }
    }

    private func localProximityFired(distanceM: Double?) {
        lastLocalProximityAt = Date()
        if let d = distanceM {
            localProximityText = String(format: "Obstacle ahead · %.1f m (on-device LiDAR)", d)
        } else {
            localProximityText = "Obstacle ahead (on-device LiDAR)"
        }
        feedback.deliverLocalProximity(distanceM: distanceM)
    }

    // MARK: - Derived

    /// On-device sectors when LiDAR is present (fresher), otherwise whatever the Mac reports.
    var displaySectors: [DepthSector] {
        lidarSupported ? pipelineState.sectors : serverSectors
    }

    var macViewStale: Bool {
        guard let at = lastViewAt else { return true }
        return Date().timeIntervalSince(at) > 2.0
    }
}
