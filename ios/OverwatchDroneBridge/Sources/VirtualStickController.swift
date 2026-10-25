import DJISDK
import Foundation

/// Executes the Mac's virtual-stick setpoints on the aircraft with the bridge-side safety layer required by
/// docs/contracts.md (same semantics as backend/overwatch/sim/bridge_sim.py):
///
/// * a 20 Hz loop sends the LATCHED setpoint (`vstick` commands only latch; they are never forwarded directly);
/// * watchdog 1 — setpoint older than 300 ms (or its own shorter `ttl_ms`) → zero velocity (hover);
/// * watchdog 2 — no message of any kind from the Mac for 2 s → disable virtual stick, PILOT IN CONTROL;
/// * defence-in-depth caps: ≤ 6 m/s horizontal, ≤ 45 °/s yaw, ≤ 1.5 m/s vertical (the Mac clamps too);
/// * real-flight gate: virtual stick only while the DJI simulator runs, unless real flight is armed on this phone;
/// * pilot override: if the aircraft leaves virtual-stick mode (RC input / flight-mode switch) we stop sending;
/// * E-STOP (zero + disable), RTL (disable + go-home), operator DISABLE button (latches until re-armed).
///
/// Axis mapping (DJI MSDK v4, rollPitchControlMode = .velocity, rollPitchCoordinateSystem = .body):
/// **roll = forward m/s, pitch = right m/s**, yaw = deg/s, verticalThrottle = up m/s (backend/overwatch/autonomy/vstick.py).
///
/// Threading: every method must be called on `queue` (the bridge core queue). DJI completion blocks hop back to it.
final class VirtualStickController {
    enum Phase: String {
        case disabled = "DISABLED"
        case enabling = "ENABLING"
        case enabled = "ENABLED"
        case disabling = "DISABLING"
    }

    struct Setpoint: Equatable {
        var forward = 0.0     // m/s, body frame
        var right = 0.0       // m/s, body frame
        var up = 0.0          // m/s
        var yawRate = 0.0     // deg/s, clockwise positive
        var ttlS = 0.3
        var seq = 0
        var rxS = 0.0         // DeviceClock seconds when latched on the phone

        static func zero(at time: Double) -> Setpoint {
            Setpoint(rxS: time)
        }
    }

    struct Status {
        var phase: Phase = .disabled
        var pilotInControl = false
        var pilotReason: String?
        var inhibited = false
        var watchdogHover = false
        var setpoint: Setpoint?
        var setpointAgeMs: Double?
        var lastSent: Setpoint?
        var macLinkAgeMs: Double?
        var aircraftReportsEnabled: Bool?
        var rtlActive = false
        var sendErrors = 0
        var commandsLatched = 0
        var cameraOnly = true
    }

    static let maxHorizontalMps = 6.0
    static let maxYawRateDps = 45.0
    static let maxVerticalMps = 1.5
    static let commandTTLS = 0.3
    static let linkTimeoutS = 2.0
    static let loopHz = 20.0

    var audit: ((String) -> Void)?
    var onPilotOverride: ((String) -> Void)?
    var onPhaseChange: ((Phase) -> Void)?
    var isSimulatorActive: () -> Bool = { false }
    var isRealFlightArmed: () -> Bool = { false }
    var isTranslationArmed: () -> Bool = { false }
    var isFlying: () -> Bool = { false }

    private let queue: DispatchQueue
    private var timer: DispatchSourceTimer?
    private(set) var phase: Phase = .disabled
    private var phaseChangedS = 0.0
    private var setpoint: Setpoint?
    private var lastSent: Setpoint?
    private var lastMacMessageS = -1.0e9
    private var watchdogHover = false
    private var pilotInControl = false
    private var pilotReason: String?
    private var overrideGuard = ManualOverrideGuard()
    private(set) var controlToken = UUID().uuidString
    var inhibited: Bool { overrideGuard.inhibited }
    var controllerReady: Bool { overrideGuard.ready(at: DeviceClock.nowS()) }
    private var rtlActive = false
    private var rtlStartedS = -1.0e9
    private var goHomeAfterDisable = false
    private var tickCount = 0
    private var lastDisableCallS = -1.0e9
    private var lastDisableErrorLogS = -1.0e9
    private var aircraftReportsEnabled: Bool?
    private var sendErrors = 0
    private var lastSendErrorLogS = -1.0e9
    private var lastModeRepairLogS = -1.0e9
    private var commandsLatched = 0
    private var lastAcceptedSeq = -1
    private var requestedCameraOnly = true
    var cameraOnly: Bool { CameraOnlyLimit.required(requestedFull: !requestedCameraOnly, simulator: isSimulatorActive(),
                                                   realArmed: isRealFlightArmed(), translationArmed: isTranslationArmed()) }

    func configureProfile(_ profile: String?) {
        requestedCameraOnly = profile != "full"
        setpoint = nil
        audit?(cameraOnly ? "camera-only profile: translation disabled on phone" : "full-motion profile explicitly armed")
    }

    init(queue: DispatchQueue) {
        self.queue = queue
    }

    func start() {
        guard timer == nil else { return }
        let t = DispatchSource.makeTimerSource(queue: queue)
        t.schedule(deadline: .now() + 0.05, repeating: 1.0 / Self.loopHz, leeway: .milliseconds(5))
        t.setEventHandler { [weak self] in
            self?.tick()
        }
        t.resume()
        timer = t
    }

    var isEnabled: Bool { phase == .enabled }

    // MARK: - Inputs from the Mac (core queue)

    /// Any message from the Mac (text or binary, incl. sync and ping) refreshes the 2 s link watchdog.
    func noteMacMessage() {
        lastMacMessageS = DeviceClock.nowS()
    }

    /// `vstick`: latch as the current setpoint; the 20 Hz loop sends it while it is fresh.
    func latch(_ sp: Setpoint) {
        guard phase == .enabled, !inhibited, controllerReady else { return }
        guard sp.seq > lastAcceptedSeq, sp.ttlS.isFinite, sp.ttlS > 0 else { return }
        lastAcceptedSeq = sp.seq
        setpoint = sp
        commandsLatched += 1
    }

    /// `hover`: zero setpoint.
    func hover() {
        setpoint = .zero(at: DeviceClock.nowS())
    }

    /// `mode {vstick: true}`.
    func requestEnable(reason: String) {
        guard let fc = ProductLink.flightController else {
            audit?("virtual stick enable refused: no flight controller connected")
            return
        }
        if inhibited {
            audit?("virtual stick enable refused: disabled on the bridge phone (tap RE-ARM there first)")
            return
        }
        guard controllerReady else {
            pilotOverride("Controller input monitor stale or sticks not neutral")
            return
        }
        if !isSimulatorActive() && !isRealFlightArmed() {
            audit?("virtual stick enable refused: DJI simulator not running and real flight not armed on the bridge")
            return
        }
        if rtlActive {
            audit?("virtual stick enable refused during RTL")
            return
        }
        switch phase {
        case .enabled, .enabling:
            return
        case .disabling:
            audit?("virtual stick enable refused: still disabling, send mode again")
            return
        case .disabled:
            break
        }
        applyControlModes(fc)
        setpoint = nil
        lastAcceptedSeq = -1
        watchdogHover = false
        setPhase(.enabling, now: DeviceClock.nowS())
        fc.setVirtualStickModeEnabled(true, withCompletion: { [weak self] error in
            guard let self = self else { return }
            self.queue.async {
                if self.phase != .enabling {
                    // DJI may finish enabling after a concurrent pilot takeover already disabled it.
                    if error == nil {
                        self.aircraftReportsEnabled = true
                        self.beginDisable(reason: "late enable completion cancelled: PILOT IN CONTROL", pilot: true)
                        self.disableNowIfActive()
                    }
                    return
                }
                guard !self.inhibited, self.controllerReady else {
                    self.aircraftReportsEnabled = error == nil
                    self.pilotOverride("Controller takeover while virtual stick was enabling")
                    return
                }
                let now = DeviceClock.nowS()
                if let error = error {
                    self.setPhase(.disabled, now: now)
                    self.audit?("virtual stick enable failed: \(error.localizedDescription)")
                    return
                }
                if let fc = ProductLink.flightController {
                    self.applyControlModes(fc)  // re-apply once virtual stick is actually on
                }
                self.aircraftReportsEnabled = true
                self.setPhase(.enabled, now: now)
                self.pilotInControl = false
                self.pilotReason = nil
                self.audit?("virtual stick ENABLED (\(reason)); simulator=\(self.isSimulatorActive())")
            }
        })
    }

    /// `mode {vstick: false}`.
    func requestDisable(reason: String) {
        overrideGuard.inhibit(reason)
        beginDisable(reason: reason, pilot: true)
    }

    /// `estop`: zero setpoint + disable virtual stick.
    func estop() {
        controlToken = UUID().uuidString
        overrideGuard.inhibit("E-STOP: re-arm on phone required")
        beginDisable(reason: "E-STOP: zero velocity + virtual stick disabled, PILOT IN CONTROL", pilot: true)
        disableNowIfActive()
    }

    /// `rtl`: disable virtual stick, then start go-home.
    func rtl() {
        guard ProductLink.flightController != nil else {
            audit?("RTL refused: no flight controller connected")
            return
        }
        rtlActive = true
        rtlStartedS = DeviceClock.nowS()
        if phase == .disabled && aircraftReportsEnabled != true {
            audit?("RTL: starting go-home")
            startGoHome()
        } else {
            goHomeAfterDisable = true
            beginDisable(reason: "RTL: virtual stick disabled, go-home follows", pilot: false)
        }
    }

    // MARK: - Inputs from the bridge phone (core queue)

    /// The big red button. Latches: the Mac cannot re-enable virtual stick until the operator re-arms.
    func operatorDisable() {
        controlToken = UUID().uuidString
        overrideGuard.inhibit("Disabled on bridge phone")
        beginDisable(reason: "DISABLED ON THE BRIDGE by the operator: PILOT IN CONTROL (Mac control inhibited)",
                     pilot: true)
        disableNowIfActive()
    }

    func operatorRearm() {
        guard inhibited, phase == .disabled, aircraftReportsEnabled != true else { return }
        guard overrideGuard.rearm(at: DeviceClock.nowS()) else {
            audit?("re-arm refused: need fresh controller callbacks and neutral sticks for 250 ms")
            return
        }
        setpoint = nil
        controlToken = UUID().uuidString  // old buffered commands cannot survive local re-arm
        audit?("bridge re-armed by the operator: the Mac may enable virtual stick again")
    }

    func appWillBackground() {
        controlToken = UUID().uuidString
        overrideGuard.inhibit("Bridge app left foreground")
        guard phase != .disabled || aircraftReportsEnabled == true else { return }
        beginDisable(reason: "bridge app left the foreground: virtual stick disabled, PILOT IN CONTROL", pilot: true)
        disableNowIfActive()
    }

    /// Zero command + disable call right away instead of waiting up to 50 ms for the next tick.
    private func disableNowIfActive() {
        guard phase == .disabling, let fc = ProductLink.flightController else { return }
        send(.zero(at: DeviceClock.nowS()), fc: fc)
        lastDisableCallS = DeviceClock.nowS()
        callDisable(fc)
    }

    // MARK: - Connection events (core queue)

    /// After every aircraft (re)connect: virtual stick is off on the aircraft and the control modes may have been
    /// reset, so re-apply them.
    func productConnected() {
        controlToken = UUID().uuidString
        overrideGuard.inhibit("Aircraft connected: re-arm on phone required")
        aircraftReportsEnabled = nil
        if phase != .disabled {
            pilotInControl = true
            pilotReason = "aircraft link re-established: virtual stick reset"
            audit?("aircraft (re)connected while virtual stick was \(phase.rawValue): reset, PILOT IN CONTROL")
            setPhase(.disabled, now: DeviceClock.nowS())
        }
        setpoint = nil
        if let fc = ProductLink.flightController {
            applyControlModes(fc)
        }
    }

    /// A component (re)connected while the product stayed connected: only re-apply the control modes. If the
    /// flight controller itself bounced, the 2 Hz poll sees virtual stick off and hands control to the pilot.
    func componentsChanged() {
        if let fc = ProductLink.flightController {
            applyControlModes(fc)
        }
    }

    func productDisconnected() {
        controlToken = UUID().uuidString
        overrideGuard.inhibit("Aircraft disconnected")
        aircraftReportsEnabled = nil
        if phase != .disabled {
            pilotInControl = true
            pilotReason = "aircraft link lost"
            audit?("aircraft link lost while virtual stick was \(phase.rawValue): the aircraft's own failsafe applies")
            setPhase(.disabled, now: DeviceClock.nowS())
        }
        setpoint = nil
        rtlActive = false
        goHomeAfterDisable = false
    }

    /// Mac socket (re)opened: cheap insurance — re-apply the control modes.
    func macLinkOpened() {
        pilotOverride("Mac connection changed: re-arm on phone required")
        if let fc = ProductLink.flightController {
            applyControlModes(fc)
        }
    }

    func status() -> Status {
        let now = DeviceClock.nowS()
        var s = Status()
        s.phase = phase
        s.pilotInControl = pilotInControl
        s.pilotReason = pilotReason
        s.inhibited = inhibited
        s.watchdogHover = watchdogHover
        s.setpoint = setpoint
        s.setpointAgeMs = setpoint.map { (now - $0.rxS) * 1000.0 }
        s.lastSent = lastSent
        s.macLinkAgeMs = lastMacMessageS > 0 ? (now - lastMacMessageS) * 1000.0 : nil
        s.aircraftReportsEnabled = aircraftReportsEnabled
        s.rtlActive = rtlActive
        s.sendErrors = sendErrors
        s.commandsLatched = commandsLatched
        s.cameraOnly = cameraOnly
        return s
    }

    // MARK: - 20 Hz loop

    private func tick() {
        tickCount &+= 1
        let now = DeviceClock.nowS()
        let fc = ProductLink.flightController
        if (phase == .enabled || phase == .enabling) && !controllerReady {
            pilotOverride("Controller input monitor stale or controller not neutral")
        }
        if let fc = fc {
            if tickCount % 10 == 0 {
                pollAircraft(fc)                               // 2 Hz: detect pilot override
            }
            if tickCount % 20 == 5 && (phase == .enabled || phase == .enabling) {
                repairControlModes(fc, now: now)              // 1 Hz
            }
        }
        if rtlActive && now - rtlStartedS > 3.0 && !isFlying() {
            rtlActive = false
            audit?("RTL finished: aircraft on the ground")
        }

        switch phase {
        case .disabled:
            return
        case .disabling:
            guard let fc = fc else {
                setPhase(.disabled, now: now)
                return
            }
            send(.zero(at: now), fc: fc)                       // keep commanding zero until it is really off
            if now - lastDisableCallS >= 0.5 {
                lastDisableCallS = now
                callDisable(fc)
            }
            if goHomeAfterDisable && now - phaseChangedS > 3.0 {
                goHomeAfterDisable = false                     // disable keeps failing: try go-home anyway
                startGoHome()
            }
        case .enabling, .enabled:
            // Watchdog 2: the Mac link.
            if now - lastMacMessageS > Self.linkTimeoutS {
                overrideGuard.inhibit("Mac link timed out: re-arm on phone required")
                beginDisable(reason: String(format: "watchdog: no message from the Mac for %.1f s: virtual stick disabled, PILOT IN CONTROL",
                                            now - lastMacMessageS), pilot: true)
                return
            }
            // Real-flight gate (defence in depth; the Mac has its own allow_real_flight gate).
            if !isSimulatorActive() && !isRealFlightArmed() {
                beginDisable(reason: "DJI simulator stopped and real flight not armed on the bridge: virtual stick disabled",
                             pilot: true)
                return
            }
            guard phase == .enabled, let fc = fc else { return }
            // Watchdog 1: setpoint freshness.
            var command = Setpoint.zero(at: now)
            if let sp = setpoint, now - sp.rxS <= min(Self.commandTTLS, sp.ttlS) {
                command = sp
                if watchdogHover {
                    watchdogHover = false
                    audit?("watchdog: virtual-stick commands resumed")
                }
            } else if !watchdogHover {
                watchdogHover = true
                let age = setpoint.map { String(format: "%.0f ms", (now - $0.rxS) * 1000.0) } ?? "ever"
                audit?("watchdog hover: no vstick command for \(age) -> zero velocity")
            }
            var limited = Self.capped(command)
            if cameraOnly {
                (limited.forward, limited.right, limited.up, limited.yawRate) = CameraOnlyLimit.apply(
                    forward: limited.forward, right: limited.right, up: limited.up, yaw: limited.yawRate)
            } else if !isSimulatorActive() {
                (limited.forward, limited.right, limited.up, limited.yawRate) = RealMotionLimit.apply(
                    forward: limited.forward, right: limited.right, up: limited.up, yaw: limited.yawRate)
            }
            send(limited, fc: fc)
        }
    }

    /// Defence-in-depth limits; non-finite values become 0.
    static func capped(_ s: Setpoint) -> Setpoint {
        func finite(_ v: Double) -> Double { v.isFinite ? v : 0.0 }
        var out = s
        var forward = finite(s.forward)
        var right = finite(s.right)
        let horizontal = (forward * forward + right * right).squareRoot()
        if horizontal > maxHorizontalMps {
            let k = maxHorizontalMps / horizontal
            forward *= k
            right *= k
        }
        out.forward = forward
        out.right = right
        out.up = min(maxVerticalMps, max(-maxVerticalMps, finite(s.up)))
        out.yawRate = min(maxYawRateDps, max(-maxYawRateDps, finite(s.yawRate)))
        return out
    }

    // MARK: - DJI calls

    private func send(_ c: Setpoint, fc: DJIFlightController) {
        lastSent = c
        // Body-frame velocity mode: pitch = RIGHT m/s, roll = FORWARD m/s, yaw = deg/s, verticalThrottle = UP m/s.
        let data = DJIVirtualStickFlightControlData(pitch: Float(c.right), roll: Float(c.forward), yaw: Float(c.yawRate),
                                                    verticalThrottle: Float(c.up))
        // VERIFY: MSDK 4.16 imports -sendVirtualStickFlightControlData:withCompletion: as send(_:withCompletion:).
        fc.send(data, withCompletion: { [weak self] error in
            guard let self = self, let error = error else { return }
            self.queue.async {
                self.noteSendError(error)
            }
        })
    }

    private func callDisable(_ fc: DJIFlightController) {
        fc.setVirtualStickModeEnabled(false, withCompletion: { [weak self] error in
            guard let self = self else { return }
            self.queue.async {
                guard self.phase == .disabling else { return }
                let now = DeviceClock.nowS()
                if let error = error {
                    if now - self.lastDisableErrorLogS > 5.0 {
                        self.lastDisableErrorLogS = now
                        self.audit?("virtual stick disable failed (retrying): \(error.localizedDescription)")
                    }
                    return
                }
                self.aircraftReportsEnabled = false
                self.setPhase(.disabled, now: now)
                if self.goHomeAfterDisable {
                    self.goHomeAfterDisable = false
                    self.startGoHome()
                }
            }
        })
    }

    /// VERIFY: on the Mini 2 + RC-N1, confirm stick input (or the C/N/S switch / pause button) makes the aircraft
    /// leave virtual stick so this poll reports the pilot override; the DISABLE button works regardless.
    private func pollAircraft(_ fc: DJIFlightController) {
        fc.getVirtualStickModeEnabled { [weak self] enabled, error in
            guard let self = self else { return }
            self.queue.async {
                guard error == nil else { return }
                self.aircraftReportsEnabled = enabled
                let now = DeviceClock.nowS()
                guard now - self.phaseChangedS > 1.5 else { return }  // ignore polls racing a transition
                if !enabled && self.phase == .enabled {
                    self.pilotOverride("Aircraft left virtual-stick mode: PILOT IN CONTROL")
                } else if enabled && self.phase == .disabled {
                    self.setPhase(.disabling, now: now)
                    self.lastDisableCallS = -1.0e9
                    self.audit?("aircraft reports virtual stick ON while the bridge expects OFF: disabling")
                }
            }
        }
    }

    private func startGoHome() {
        guard let fc = ProductLink.flightController else {
            rtlActive = false
            audit?("go-home failed: no flight controller")
            return
        }
        fc.startGoHome { [weak self] error in
            guard let self = self else { return }
            self.queue.async {
                if let error = error {
                    self.rtlActive = false
                    self.audit?("go-home FAILED: \(error.localizedDescription) (pilot must fly home)")
                } else {
                    self.audit?("go-home started")
                }
            }
        }
    }

    private func applyControlModes(_ fc: DJIFlightController) {
        fc.rollPitchControlMode = .velocity
        fc.yawControlMode = .angularVelocity
        fc.verticalControlMode = .velocity
        fc.rollPitchCoordinateSystem = .body
    }

    private func controlModesCorrect(_ fc: DJIFlightController) -> Bool {
        fc.rollPitchControlMode == .velocity && fc.yawControlMode == .angularVelocity
            && fc.verticalControlMode == .velocity && fc.rollPitchCoordinateSystem == .body
    }

    private func repairControlModes(_ fc: DJIFlightController, now: Double) {
        guard !controlModesCorrect(fc) else { return }
        applyControlModes(fc)
        if now - lastModeRepairLogS > 5.0 {
            lastModeRepairLogS = now
            audit?("re-applied virtual-stick control modes (velocity / angular velocity / velocity / body)")
        }
    }

    private func beginDisable(reason: String, pilot: Bool) {
        overrideGuard.inhibit(reason)
        lastAcceptedSeq = -1
        setpoint = nil
        watchdogHover = false
        if pilot {
            pilotInControl = true
            pilotReason = reason
        }
        if phase != .disabled || aircraftReportsEnabled == true {
            setPhase(.disabling, now: DeviceClock.nowS())
            lastDisableCallS = -1.0e9  // call disable on the very next tick
        }
        audit?(reason)
    }

    /// Called directly by DJI RC hardware callbacks, independent of the model and Mac network.
    func observeController(sticks: [Int], wheel: Int, pause: Bool, home: Bool, mode: Int) {
        if let reason = overrideGuard.observe(sticks: sticks, wheel: wheel, pause: pause,
                                             home: home, mode: mode, at: DeviceClock.nowS()) {
            pilotOverride(reason)
        }
    }

    private func pilotOverride(_ reason: String) {
        controlToken = UUID().uuidString
        overrideGuard.inhibit(reason)
        rtlActive = false
        goHomeAfterDisable = false
        beginDisable(reason: reason, pilot: true)
        disableNowIfActive()
        onPilotOverride?(reason)
    }

    private func setPhase(_ p: Phase, now: Double) {
        guard p != phase else { return }
        phase = p
        phaseChangedS = now
        onPhaseChange?(p)
    }

    private func noteSendError(_ error: Error) {
        sendErrors += 1
        let now = DeviceClock.nowS()
        if now - lastSendErrorLogS > 5.0 {
            lastSendErrorLogS = now
            audit?("virtual-stick send error: \(error.localizedDescription)")
        }
    }
}
