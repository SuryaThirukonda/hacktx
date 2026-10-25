import Foundation

/// `/ws/phone` client: observations (JSON text) + camera JPEGs (binary kind 0x01) up; alerts, responder_view,
/// transcript, ack and clock-sync pings down. Built on `ReconnectingWebSocket` (backoff reconnect, bounded queue).
///
/// Clock domain: capture timestamps are `ARFrame.timestamp * 1000` and sync replies use
/// `ProcessInfo.systemUptime * 1000` (`DeviceClock`) — the same timebase, so the Mac's offset estimate applies.
final class CaptureTransport {
    static let path = "/ws/phone"

    /// Up to ~600 KB may be in flight (≈ 10 JPEGs); observations coalesce above 200 KB and expire after 500 ms.
    let socket = ReconnectingWebSocket(label: "phone", maxInFlightBytes: 600 * 1024,
                                       stateHighWaterBytes: 200 * 1024, stateMaxAgeMs: 500)

    // Callbacks run on the socket queue; consumers hop to main.
    var onAlert: ((WireAlert) -> Void)?
    var onResponderView: ((ResponderView) -> Void)?
    var onTranscript: ((TranscriptLine) -> Void)?
    var onAck: ((String) -> Void)?
    var onServerError: ((String) -> Void)?
    var onOpen: (() -> Void)?
    var onClose: ((String?) -> Void)?

    private let deviceName: String

    init(deviceName: String) {
        self.deviceName = deviceName
        socket.onText = { [weak self] text, rxMs in
            self?.handle(text: text, rxMs: rxMs)
        }
        socket.onOpen = { [weak self] in
            guard let self = self else { return }
            let hello = HelloMessage(device: self.deviceName, source_id: "responder-iphone", app: "OverwatchResponder",
                                     text: "OverwatchResponder \(Self.appVersion) on \(self.deviceName)")
            if let s = WireJSON.encode(hello) {
                self.socket.sendControl(s)
            }
            self.onOpen?()
        }
        socket.onClose = { [weak self] reason in
            self?.onClose?(reason)
        }
    }

    static var appVersion: String {
        (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "1.0"
    }

    var snapshot: ReconnectingWebSocket.Snapshot { socket.snapshot }
    var isConnected: Bool { socket.snapshot.state == .connected }
    var canAcceptVideo: Bool { socket.canAcceptDroppable }

    @discardableResult
    func connect(host: String, token: String) -> Bool {
        guard let url = Wire.socketURL(host: host, path: Self.path, token: token) else { return false }
        socket.connect(to: url)
        return true
    }

    func disconnect() {
        socket.disconnect()
    }

    /// Periodic state: only the newest observation is kept when the link is congested.
    func sendObservation(_ observation: PhoneObservation) {
        guard let text = WireJSON.encode(observation) else { return }
        socket.sendLatest(text)
    }

    /// Camera frame for the observation that follows it (send BEFORE that observation).
    func sendJPEG(_ jpeg: Data, captureTsMs: Double) {
        socket.sendDroppable(Wire.binaryFrame(kind: .jpeg, captureTsMs: captureTsMs, payload: jpeg))
    }

    func sendAck(alertId: String) {
        guard let text = WireJSON.encode(AckMessage(alert_id: alertId)) else { return }
        socket.sendControl(text)
    }

    // MARK: - Downlink (socket queue)

    private func handle(text: String, rxMs: Double) {
        guard let probe = WireJSON.decode(WireProbe.self, from: text), let type = probe.type else { return }
        switch type {
        case "sync":
            guard let t0 = probe.t0 else { return }
            // t1 = receive time (taken in the socket callback), t2 = send time (taken at transmit).
            socket.sendControl(build: {
                WireJSON.encode(SyncReply(t0: t0, t1: rxMs, t2: DeviceClock.nowMs()))
            })
        case "alert":
            if let alert = WireJSON.decode(WireAlert.self, from: text) {
                onAlert?(alert)
            }
        case "responder_view":
            if let view = WireJSON.decode(ResponderView.self, from: text) {
                onResponderView?(view)
            }
        case "transcript":
            if let line = WireJSON.decode(TranscriptLine.self, from: text) {
                onTranscript?(line)
            }
        case "ack":
            if let id = probe.alert_id {
                onAck?(id)
            }
        case "error":
            onServerError?(probe.detail ?? text)
        default:
            break
        }
    }
}
