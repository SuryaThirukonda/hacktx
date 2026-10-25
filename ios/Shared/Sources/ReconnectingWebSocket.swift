import Foundation
import os

/// `URLSessionWebSocketTask` wrapper used by both apps:
/// * exponential-backoff reconnect (0.5 s → 8 s, jittered) for as long as a connection is wanted;
/// * keepalive pings every 2 s with RTT measurement; a missing pong for 5 s counts as a dead link;
/// * a bounded send path so video and periodic state never queue up behind a slow Wi-Fi link:
///   - `sendControl`   small must-deliver-while-connected messages (acks, sync replies, hello, audit): sent at once;
///   - `sendLatest`    periodic state (observations, telemetry): when congested only the NEWEST is kept, and it is
///                     discarded if it got older than `stateMaxAgeMs` while waiting;
///   - `sendDroppable` video frames: dropped when congested (the caller is told, e.g. to resync H.264).
///
/// All mutable state is confined to a private serial queue. Callbacks (`onOpen`, `onClose`, `onText`, `onBinary`)
/// are invoked on that queue: keep them short and hop to your own queue for real work.
final class ReconnectingWebSocket: NSObject, URLSessionWebSocketDelegate {
    enum State: String {
        case idle = "Offline"
        case connecting = "Connecting"
        case connected = "Connected"
        case waiting = "Retrying"
    }

    struct Snapshot {
        var state: State = .idle
        var url: String = ""
        var rttMs: Double?
        var lastRxMs: Double?
        var connectedSinceMs: Double?
        var inFlightBytes = 0
        var sentMessages = 0
        var droppedVideo = 0
        var droppedState = 0
        var reconnects = 0
        var nextRetryInS: Double?
        var lastError: String?
    }

    var onOpen: (() -> Void)?
    var onClose: ((String?) -> Void)?
    /// (text, device receive time in DeviceClock ms — use it as t1 for sync replies)
    var onText: ((String, Double) -> Void)?
    var onBinary: ((Data, Double) -> Void)?

    let maxInFlightBytes: Int
    let stateHighWaterBytes: Int
    let stateMaxAgeMs: Double

    private let queue: DispatchQueue
    private let log: Logger
    private var session: URLSession?
    private var task: URLSessionWebSocketTask?
    private var url: URL?
    private var wantConnected = false
    private var isOpen = false
    private var generation = 0
    private var backoffS = 0.5
    private var inFlight = 0
    private var pendingState: (message: URLSessionWebSocketTask.Message, bytes: Int, createdMs: Double)?
    private var keepalive: DispatchSourceTimer?
    private var pingSentMs: Double?
    private var connectDeadline: DispatchWorkItem?
    private let snapLock = NSLock()
    private var snap = Snapshot()

    init(label: String, maxInFlightBytes: Int = 768 * 1024, stateHighWaterBytes: Int = 256 * 1024,
         stateMaxAgeMs: Double = 1000) {
        self.queue = DispatchQueue(label: "com.overwatch.ws.\(label)", qos: .userInitiated)
        self.log = Logger(subsystem: "com.overwatch", category: "ws.\(label)")
        self.maxInFlightBytes = maxInFlightBytes
        self.stateHighWaterBytes = stateHighWaterBytes
        self.stateMaxAgeMs = stateMaxAgeMs
        super.init()
    }

    // MARK: - Public API (any thread)

    var snapshot: Snapshot {
        snapLock.lock()
        defer { snapLock.unlock() }
        return snap
    }

    /// Cheap pre-check so producers can skip expensive encoding when a video frame would be dropped anyway.
    var canAcceptDroppable: Bool {
        let s = snapshot
        return s.state == .connected && s.inFlightBytes < maxInFlightBytes / 2
    }

    func connect(to url: URL) {
        queue.async {
            self.url = url
            self.wantConnected = true
            self.backoffS = 0.5
            self.openSocket()
        }
    }

    func disconnect() {
        queue.async {
            self.wantConnected = false
            self.teardown(reason: "disconnected by user", notify: true)
            self.updateSnap {
                $0.state = .idle
                $0.nextRetryInS = nil
            }
        }
    }

    func sendControl(_ text: String) {
        queue.async { self.transmit(.string(text), bytes: text.utf8.count) }
    }

    /// Builds the message at transmit time (e.g. a sync reply whose t2 must be the actual send time).
    func sendControl(build: @escaping () -> String?) {
        queue.async {
            guard self.isOpen, let text = build() else { return }
            self.transmit(.string(text), bytes: text.utf8.count)
        }
    }

    func sendLatest(_ text: String) {
        let created = DeviceClock.nowMs()
        queue.async {
            guard self.isOpen else { return }
            let bytes = text.utf8.count
            if self.pendingState != nil || self.inFlight > self.stateHighWaterBytes {
                if self.pendingState != nil {
                    self.updateSnap { $0.droppedState += 1 }  // the older pending one is superseded
                }
                self.pendingState = (message: URLSessionWebSocketTask.Message.string(text), bytes: bytes,
                                     createdMs: created)
                return
            }
            self.transmit(.string(text), bytes: bytes)
        }
    }

    /// `onDrop` runs on the socket queue when the frame was not sent.
    func sendDroppable(_ data: Data, onDrop: (() -> Void)? = nil) {
        queue.async {
            // A pending (older) state message must not be overtaken by a newer video frame: the Mac attaches the
            // last JPEG it received to the next observation.
            guard self.isOpen, self.pendingState == nil, self.inFlight + data.count <= self.maxInFlightBytes else {
                self.updateSnap { $0.droppedVideo += 1 }
                onDrop?()
                return
            }
            self.transmit(.data(data), bytes: data.count)
        }
    }

    /// Breaks the URLSession → delegate retain cycle. Only needed if the owner goes away.
    func invalidate() {
        queue.async {
            self.wantConnected = false
            self.teardown(reason: nil, notify: false)
            self.session?.invalidateAndCancel()
            self.session = nil
        }
    }

    // MARK: - Connection lifecycle (socket queue)

    private func makeSession() -> URLSession {
        if let s = session { return s }
        let cfg = URLSessionConfiguration.default
        cfg.waitsForConnectivity = false
        // URLSessionWebSocketTask may drop an idle socket after timeoutIntervalForRequest; liveness is handled by our
        // own keepalive instead, so keep this generous.
        cfg.timeoutIntervalForRequest = 120
        cfg.timeoutIntervalForResource = 7 * 24 * 3600
        let s = URLSession(configuration: cfg, delegate: self, delegateQueue: nil)
        session = s
        return s
    }

    private func openSocket() {
        guard wantConnected, let url = url else { return }
        teardown(reason: nil, notify: false)
        generation &+= 1
        let gen = generation
        let t = makeSession().webSocketTask(with: url)
        t.maximumMessageSize = 8 * 1024 * 1024
        task = t
        updateSnap {
            $0.state = .connecting
            $0.url = url.absoluteString
            $0.nextRetryInS = nil
        }
        t.resume()
        receive(on: t, gen: gen)
        let deadline = DispatchWorkItem { [weak self] in
            guard let self = self, gen == self.generation, !self.isOpen else { return }
            self.fail(gen: gen, reason: "connect timeout")
        }
        connectDeadline = deadline
        queue.asyncAfter(deadline: .now() + 5.0, execute: deadline)
    }

    private func teardown(reason: String?, notify: Bool) {
        let wasOpen = isOpen
        keepalive?.cancel()
        keepalive = nil
        connectDeadline?.cancel()
        connectDeadline = nil
        pingSentMs = nil
        isOpen = false
        inFlight = 0
        pendingState = nil
        if let t = task {
            t.cancel(with: .goingAway, reason: nil)
            task = nil
        }
        generation &+= 1  // late callbacks from the old task are ignored
        updateSnap {
            $0.inFlightBytes = 0
            $0.connectedSinceMs = nil
            $0.rttMs = nil
        }
        if notify && wasOpen {
            onClose?(reason)
        }
    }

    private func fail(gen: Int, reason: String) {
        guard gen == generation else { return }
        log.info("socket failure: \(reason, privacy: .public)")
        teardown(reason: reason, notify: true)
        guard wantConnected else {
            updateSnap { $0.state = .idle }
            return
        }
        let delay = backoffS * Double.random(in: 0.8...1.2)
        backoffS = min(backoffS * 2.0, 8.0)
        updateSnap {
            $0.state = .waiting
            $0.reconnects += 1
            $0.lastError = reason
            $0.nextRetryInS = delay
        }
        let scheduledGen = generation
        queue.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self = self, self.wantConnected, scheduledGen == self.generation, self.task == nil else { return }
            self.openSocket()
        }
    }

    private func didOpen(_ t: URLSessionWebSocketTask) {
        guard t === task, !isOpen else { return }
        isOpen = true
        connectDeadline?.cancel()
        connectDeadline = nil
        backoffS = 0.5
        inFlight = 0
        pendingState = nil
        let now = DeviceClock.nowMs()
        updateSnap {
            $0.state = .connected
            $0.connectedSinceMs = now
            $0.lastError = nil
            $0.inFlightBytes = 0
            $0.nextRetryInS = nil
        }
        startKeepalive(gen: generation)
        onOpen?()
    }

    private func receive(on t: URLSessionWebSocketTask, gen: Int) {
        t.receive { [weak self] result in
            let rx = DeviceClock.nowMs()  // as early as possible: this is t1 for clock sync
            guard let self = self else { return }
            self.queue.async {
                guard gen == self.generation else { return }
                switch result {
                case .failure(let error):
                    self.fail(gen: gen, reason: error.localizedDescription)
                case .success(let message):
                    if !self.isOpen {
                        self.didOpen(t)  // some OS versions deliver data before didOpenWithProtocol
                    }
                    self.updateSnap { $0.lastRxMs = rx }
                    switch message {
                    case .string(let text):
                        self.onText?(text, rx)
                    case .data(let data):
                        self.onBinary?(data, rx)
                    @unknown default:
                        break
                    }
                    if gen == self.generation {
                        self.receive(on: t, gen: gen)
                    }
                }
            }
        }
    }

    private func startKeepalive(gen: Int) {
        keepalive?.cancel()
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + 1.0, repeating: 2.0)
        timer.setEventHandler { [weak self] in self?.keepaliveTick(gen: gen) }
        timer.resume()
        keepalive = timer
    }

    private func keepaliveTick(gen: Int) {
        guard gen == generation, isOpen, let t = task else { return }
        let now = DeviceClock.nowMs()
        if let sent = pingSentMs {
            if now - sent > 5000 {
                fail(gen: gen, reason: "keepalive timeout (no pong for 5 s)")
            }
            return
        }
        pingSentMs = now
        t.sendPing { [weak self] error in
            let back = DeviceClock.nowMs()
            guard let self = self else { return }
            self.queue.async {
                guard gen == self.generation else { return }
                if let error = error {
                    self.fail(gen: gen, reason: "ping failed: \(error.localizedDescription)")
                    return
                }
                if let sent = self.pingSentMs {
                    self.updateSnap { $0.rttMs = back - sent }
                }
                self.pingSentMs = nil
            }
        }
    }

    // MARK: - Sending (socket queue)

    private func transmit(_ message: URLSessionWebSocketTask.Message, bytes: Int) {
        guard isOpen, let t = task else { return }
        let gen = generation
        inFlight += bytes
        let nowInFlight = inFlight
        updateSnap {
            $0.inFlightBytes = nowInFlight
            $0.sentMessages += 1
        }
        t.send(message) { [weak self] error in
            guard let self = self else { return }
            self.queue.async {
                guard gen == self.generation else { return }
                self.inFlight = max(0, self.inFlight - bytes)
                let remaining = self.inFlight
                self.updateSnap { $0.inFlightBytes = remaining }
                if let error = error {
                    self.fail(gen: gen, reason: "send failed: \(error.localizedDescription)")
                    return
                }
                self.flushPendingState()
            }
        }
    }

    private func flushPendingState() {
        guard let pending = pendingState, inFlight <= stateHighWaterBytes else { return }
        pendingState = nil
        if DeviceClock.nowMs() - pending.createdMs > stateMaxAgeMs {
            updateSnap { $0.droppedState += 1 }  // stale: the next periodic message will replace it
            return
        }
        transmit(pending.message, bytes: pending.bytes)
    }

    private func updateSnap(_ body: (inout Snapshot) -> Void) {
        snapLock.lock()
        body(&snap)
        snapLock.unlock()
    }

    // MARK: - URLSessionWebSocketDelegate (URLSession's own delegate queue → hop to ours)

    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask,
                    didOpenWithProtocol negotiatedProtocol: String?) {
        queue.async { self.didOpen(webSocketTask) }
    }

    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask,
                    didCloseWith closeCode: URLSessionWebSocketTask.CloseCode, reason: Data?) {
        queue.async {
            guard webSocketTask === self.task else { return }
            let why = reason.flatMap { String(data: $0, encoding: .utf8) } ?? ""
            self.fail(gen: self.generation, reason: "closed by server (code \(closeCode.rawValue)) \(why)")
        }
    }

    func urlSession(_ session: URLSession, task completedTask: URLSessionTask, didCompleteWithError error: Error?) {
        queue.async {
            guard completedTask === self.task else { return }
            self.fail(gen: self.generation, reason: error?.localizedDescription ?? "connection ended")
        }
    }
}
