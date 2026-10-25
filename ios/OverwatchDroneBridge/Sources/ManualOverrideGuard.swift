import Foundation

/// SDK-independent policy. Only a local, neutral-controller re-arm can clear the latch.
struct ManualOverrideGuard {
    static let deadband = 20  // DJI sticks/wheel are -660...660; ignore small centering noise.
    static let freshnessS = 0.5
    private(set) var inhibited = true
    private(set) var reason = "Re-arm on this phone with the controller neutral"
    private var lastSampleS: Double?
    private var neutralSinceS: Double?
    private var lastMode: Int?

    func ready(at now: Double) -> Bool {
        guard let last = lastSampleS, let neutral = neutralSinceS else { return false }
        return now >= last && now - last <= Self.freshnessS && now - neutral >= 0.25
    }

    mutating func inhibit(_ why: String) {
        inhibited = true
        reason = why
    }

    mutating func rearm(at now: Double) -> Bool {
        guard ready(at: now) else { return false }
        inhibited = false
        reason = ""
        return true
    }

    /// Returns a new takeover event, including a mode switch, pause or RTH button.
    mutating func observe(sticks: [Int], wheel: Int, pause: Bool, home: Bool,
                          mode: Int, at now: Double) -> String? {
        let changedMode = lastMode.map { $0 != mode } ?? false
        if let last = lastSampleS, now - last > Self.freshnessS { neutralSinceS = nil }
        lastMode = mode
        lastSampleS = now
        let active = sticks.contains { abs($0) > Self.deadband } || abs(wheel) > Self.deadband
            || pause || home || changedMode
        if active {
            neutralSinceS = nil
            let why = "Physical controller input: PILOT IN CONTROL; re-arm on phone required"
            let changed = !inhibited
            inhibit(why)
            return changed ? why : nil
        }
        if neutralSinceS == nil { neutralSinceS = now }
        return nil
    }
}
