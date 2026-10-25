import AVFoundation
import CoreHaptics
import Foundation
import UIKit

/// Body-delivered alerts: Core Haptics patterns (docs/contracts.md), AVSpeechSynthesizer for `spoken_text`, and a
/// spatialised earcon (AVAudioEngine + AVAudioEnvironmentNode, HRTF) placed at `bearing_rel_deg` just before speech.
/// Falls back to plain speech when the audio graph is unavailable. Main thread only.
final class AlertFeedback: NSObject {
    var speechEnabled = true
    var hapticsEnabled = true
    var spatialEnabled = true

    private var hapticEngine: CHHapticEngine?
    private var hapticsSupported = false
    private let synthesizer = AVSpeechSynthesizer()
    private let audioEngine = AVAudioEngine()
    private let environment = AVAudioEnvironmentNode()
    private let earconPlayer = AVAudioPlayerNode()
    private var earconBuffer: AVAudioPCMBuffer?
    private var audioGraphReady = false
    private var configured = false

    private var repeatTimer: Timer?
    private var repeatingAlertId: String?
    private var repeatStartedAt: Date?
    private var repeatPattern = "CLOSING_FAST"
    /// CLOSING_FAST repeats until acknowledged; this cap only stops a stuck buzz if the Mac vanished mid-alert.
    let maxRepeatS: TimeInterval = 30

    // MARK: - Setup

    func configure() {
        guard !configured else { return }
        configured = true
        configureAudioSession()
        setupHaptics()
        setupAudioGraph()
        let center = NotificationCenter.default
        center.addObserver(self, selector: #selector(audioInterrupted(_:)),
                           name: AVAudioSession.interruptionNotification, object: nil)
        center.addObserver(self, selector: #selector(engineConfigurationChanged(_:)),
                           name: .AVAudioEngineConfigurationChange, object: audioEngine)
        center.addObserver(self, selector: #selector(appBecameActive),
                           name: UIApplication.didBecomeActiveNotification, object: nil)
    }

    private func configureAudioSession() {
        let session = AVAudioSession.sharedInstance()
        do {
            // Playback so alerts are audible with the ring/silent switch on; mix so we never stop other audio.
            try session.setCategory(.playback, mode: .default, options: [.mixWithOthers])
            try session.setActive(true)
        } catch {
            NSLog("[AlertFeedback] audio session: %@", error.localizedDescription)
        }
    }

    private func setupHaptics() {
        hapticsSupported = CHHapticEngine.capabilitiesForHardware().supportsHaptics
        guard hapticsSupported else { return }
        do {
            let engine = try CHHapticEngine()
            engine.playsHapticsOnly = true
            engine.isAutoShutdownEnabled = false
            engine.resetHandler = { [weak self] in
                DispatchQueue.main.async {
                    try? self?.hapticEngine?.start()
                }
            }
            engine.stoppedHandler = { [weak self] _ in
                DispatchQueue.main.async {
                    try? self?.hapticEngine?.start()
                }
            }
            try engine.start()
            hapticEngine = engine
        } catch {
            NSLog("[AlertFeedback] haptics unavailable: %@", error.localizedDescription)
            hapticEngine = nil
        }
    }

    private func setupAudioGraph() {
        guard let mono = AVAudioFormat(standardFormatWithSampleRate: 44_100, channels: 1) else { return }
        audioEngine.attach(environment)
        audioEngine.attach(earconPlayer)
        // Only mono inputs to the environment node are spatialised.
        audioEngine.connect(earconPlayer, to: environment, format: mono)
        audioEngine.connect(environment, to: audioEngine.mainMixerNode, format: nil)
        earconPlayer.renderingAlgorithm = .HRTFHQ
        environment.listenerPosition = AVAudio3DPoint(x: 0, y: 0, z: 0)
        environment.listenerAngularOrientation = AVAudio3DAngularOrientation(yaw: 0, pitch: 0, roll: 0)
        environment.distanceAttenuationParameters.referenceDistance = 1.0
        environment.distanceAttenuationParameters.maximumDistance = 50.0
        earconBuffer = Self.makeEarcon(format: mono)
        audioEngine.prepare()
        do {
            try audioEngine.start()
            audioGraphReady = earconBuffer != nil
        } catch {
            NSLog("[AlertFeedback] audio engine: %@ (falling back to plain speech)", error.localizedDescription)
            audioGraphReady = false
        }
    }

    /// Short two-partial "ping" with a fast attack and exponential decay.
    private static func makeEarcon(format: AVAudioFormat) -> AVAudioPCMBuffer? {
        let duration = 0.18
        let frames = AVAudioFrameCount(format.sampleRate * duration)
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames),
              let samples = buffer.floatChannelData?[0] else { return nil }
        buffer.frameLength = frames
        let sr = format.sampleRate
        for i in 0..<Int(frames) {
            let t = Double(i) / sr
            let envelope = min(1.0, t / 0.008) * exp(-t * 16.0)
            let tone = sin(2.0 * .pi * 1046.5 * t) + 0.35 * sin(2.0 * .pi * 2093.0 * t)
            samples[i] = Float(0.55 * envelope * tone)
        }
        return buffer
    }

    @objc private func audioInterrupted(_ note: Notification) {
        guard let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
              let type = AVAudioSession.InterruptionType(rawValue: raw) else { return }
        if type == .ended {
            DispatchQueue.main.async { self.restartAudio() }
        }
    }

    @objc private func engineConfigurationChanged(_ note: Notification) {
        DispatchQueue.main.async { self.restartAudio() }  // e.g. AirPods connected / disconnected
    }

    @objc private func appBecameActive() {
        restartAudio()
        try? hapticEngine?.start()
    }

    private func restartAudio() {
        configureAudioSession()
        guard earconBuffer != nil, !audioEngine.isRunning else { return }
        do {
            try audioEngine.start()
            audioGraphReady = true
        } catch {
            audioGraphReady = false
        }
    }

    // MARK: - Alerts from the Mac

    /// Delivers one Mac alert according to its `delivery` list. `suppressFeedback` keeps the banner but skips
    /// sound/haptics (used to de-duplicate the Mac's echo of a proximity alert the phone already announced).
    func deliver(_ alert: WireAlert, suppressFeedback: Bool) {
        guard !suppressFeedback else { return }
        if alert.wantsHaptic, let pattern = alert.haptic {
            playHaptic(pattern, distanceM: alert.range_m)
        }
        if alert.wantsSpeech, let spoken = alert.spoken_text, !spoken.isEmpty {
            let urgent = alert.severity == "critical" || alert.severity == "local_proximity"
            announce(spoken, bearingRelDeg: alert.bearing_rel_deg, interrupt: urgent)
        }
        if alert.requiresAck {
            startRepeating(alertId: alert.alert_id, pattern: alert.haptic ?? "CLOSING_FAST")
        }
    }

    /// Local LiDAR proximity rule (works with no Mac connection).
    func deliverLocalProximity(distanceM: Double?) {
        playHaptic("PROXIMITY", distanceM: distanceM)
        let text: String
        if let d = distanceM {
            text = String(format: "Obstacle ahead, %.1f meters", d)
        } else {
            text = "Obstacle ahead"
        }
        announce(text, bearingRelDeg: 0, interrupt: true)
    }

    /// Earcon at the bearing first (if spatial audio is available), then the words.
    func announce(_ text: String, bearingRelDeg: Double?, interrupt: Bool) {
        guard speechEnabled else { return }
        if interrupt && synthesizer.isSpeaking {
            synthesizer.stopSpeaking(at: .immediate)
        }
        if spatialEnabled, let bearing = bearingRelDeg, playEarcon(bearingRelDeg: bearing) {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.22) { [weak self] in
                self?.speak(text)
            }
        } else {
            speak(text)
        }
    }

    private func speak(_ text: String) {
        let utterance = AVSpeechUtterance(string: text)
        utterance.rate = min(AVSpeechUtteranceMaximumSpeechRate, AVSpeechUtteranceDefaultSpeechRate * 1.08)
        utterance.voice = AVSpeechSynthesisVoice(language: "en-US")
        utterance.prefersAssistiveTechnologySettings = false
        synthesizer.speak(utterance)
    }

    /// Places the earcon on a 1.5 m circle around the listener: bearing 0 = straight ahead (−z), +90 = right (+x).
    /// VERIFY: AVAudioEnvironmentNode's default listener faces −z with +x to the right (OpenAL convention); with
    /// AirPods, a 3 o'clock alert must sound from the right ear.
    @discardableResult
    private func playEarcon(bearingRelDeg: Double) -> Bool {
        guard audioGraphReady, let buffer = earconBuffer else { return false }
        if !audioEngine.isRunning {
            do { try audioEngine.start() } catch { return false }
        }
        let b = bearingRelDeg * .pi / 180.0
        earconPlayer.position = AVAudio3DPoint(x: Float(sin(b) * 1.5), y: 0, z: Float(-cos(b) * 1.5))
        earconPlayer.scheduleBuffer(buffer, at: nil, options: [.interrupts], completionHandler: nil)
        if !earconPlayer.isPlaying {
            earconPlayer.play()
        }
        return true
    }

    // MARK: - Repeat-until-ack (CLOSING_FAST)

    func startRepeating(alertId: String, pattern: String) {
        stopRepeating()
        repeatingAlertId = alertId
        repeatPattern = pattern
        repeatStartedAt = Date()
        repeatTimer = Timer.scheduledTimer(withTimeInterval: 1.1, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            if let started = self.repeatStartedAt, Date().timeIntervalSince(started) > self.maxRepeatS {
                self.stopRepeating()
                return
            }
            self.playHaptic(self.repeatPattern, distanceM: nil)
        }
    }

    /// Stops the repetition (for a specific alert, or any when `alertId` is nil).
    func stopRepeating(alertId: String? = nil) {
        if let id = alertId, id != repeatingAlertId { return }
        repeatTimer?.invalidate()
        repeatTimer = nil
        repeatingAlertId = nil
        repeatStartedAt = nil
    }

    var isRepeating: Bool { repeatTimer != nil }

    // MARK: - Haptics

    func playHaptic(_ name: String, distanceM: Double?) {
        guard hapticsEnabled else { return }
        guard hapticsSupported, let engine = hapticEngine else {
            UINotificationFeedbackGenerator().notificationOccurred(name == "REACQUIRED" ? .success : .warning)
            return
        }
        do {
            let pattern = try Self.pattern(named: name, distanceM: distanceM)
            let player = try engine.makePlayer(with: pattern)
            try player.start(atTime: CHHapticTimeImmediate)
        } catch {
            do {
                try engine.start()
                let pattern = try Self.pattern(named: name, distanceM: distanceM)
                try engine.makePlayer(with: pattern).start(atTime: CHHapticTimeImmediate)
            } catch {
                NSLog("[AlertFeedback] haptic %@ failed: %@", name, error.localizedDescription)
            }
        }
    }

    /// docs/contracts.md: CLOSING_FAST = 3 strong transients 80 ms apart; TARGET_LOST = 2 long pulses at 0.6;
    /// REACQUIRED = single soft tap; PROXIMITY = rapid ticks scaled by distance; DIRECTION_LEFT / DIRECTION_RIGHT =
    /// rising / falling ramp.
    static func pattern(named name: String, distanceM: Double?) throws -> CHHapticPattern {
        func transient(_ time: TimeInterval, _ intensity: Float, _ sharpness: Float) -> CHHapticEvent {
            CHHapticEvent(eventType: .hapticTransient,
                          parameters: [CHHapticEventParameter(parameterID: .hapticIntensity, value: intensity),
                                       CHHapticEventParameter(parameterID: .hapticSharpness, value: sharpness)],
                          relativeTime: time)
        }
        func continuous(_ time: TimeInterval, _ duration: TimeInterval, _ intensity: Float,
                        _ sharpness: Float) -> CHHapticEvent {
            CHHapticEvent(eventType: .hapticContinuous,
                          parameters: [CHHapticEventParameter(parameterID: .hapticIntensity, value: intensity),
                                       CHHapticEventParameter(parameterID: .hapticSharpness, value: sharpness)],
                          relativeTime: time, duration: duration)
        }
        func ramp(from a: Float, to b: Float, duration: TimeInterval) -> CHHapticParameterCurve {
            CHHapticParameterCurve(parameterID: .hapticIntensityControl,
                                   controlPoints: [CHHapticParameterCurve.ControlPoint(relativeTime: 0, value: a),
                                                   CHHapticParameterCurve.ControlPoint(relativeTime: duration, value: b)],
                                   relativeTime: 0)
        }

        switch name {
        case "CLOSING_FAST":
            return try CHHapticPattern(events: [transient(0.0, 1.0, 1.0), transient(0.08, 1.0, 1.0),
                                                transient(0.16, 1.0, 1.0)], parameters: [])
        case "TARGET_LOST":
            return try CHHapticPattern(events: [continuous(0.0, 0.4, 0.6, 0.3), continuous(0.6, 0.4, 0.6, 0.3)],
                                       parameters: [])
        case "REACQUIRED":
            return try CHHapticPattern(events: [transient(0.0, 0.45, 0.25)], parameters: [])
        case "PROXIMITY":
            // Closer -> faster, stronger ticks. 0.3 m: 50 ms apart at full strength; 1.5 m+: 160 ms apart.
            let d = min(1.5, max(0.3, distanceM ?? 1.0))
            let frac = (d - 0.3) / 1.2
            let interval = 0.05 + 0.11 * frac
            let intensity = Float(1.0 - 0.45 * frac)
            let events = (0..<6).map { transient(Double($0) * interval, intensity, 0.9) }
            return try CHHapticPattern(events: events, parameters: [])
        case "DIRECTION_LEFT":
            return try CHHapticPattern(events: [continuous(0.0, 0.5, 1.0, 0.5)],
                                       parameterCurves: [ramp(from: 0.15, to: 1.0, duration: 0.5)])
        case "DIRECTION_RIGHT":
            return try CHHapticPattern(events: [continuous(0.0, 0.5, 1.0, 0.5)],
                                       parameterCurves: [ramp(from: 1.0, to: 0.15, duration: 0.5)])
        default:
            return try CHHapticPattern(events: [transient(0.0, 0.7, 0.5)], parameters: [])
        }
    }
}
