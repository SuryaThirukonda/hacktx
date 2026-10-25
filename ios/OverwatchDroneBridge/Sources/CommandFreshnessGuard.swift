import Foundation

/// One sequence/age policy for every non-abort command, independent of DJI and exercised on the host.
/// Inhibition remains authoritative in VirtualStickController; re-arm rotates its control token.
struct CommandFreshnessGuard {
    private var generation: String?
    private(set) var lastSequence = -1.0

    static func isAbort(_ type: String, enable: Bool?) -> Bool {
        type == "estop" || type == "hover" || (type == "mode" && enable == false)
    }

    /// Stop is always accepted. Remember a well-formed stop sequence so an older re-arm cannot undo it.
    mutating func noteAbort(sequence: Double?, token: String) {
        if generation != token { generation = token; lastSequence = -1 }
        if let seq = sequence, seq.isFinite, seq >= 0, seq.rounded(.down) == seq, seq <= 9_007_199_254_740_991 {
            lastSequence = max(lastSequence, seq)
        }
    }

    /// Returns the remaining lease; callers must use it instead of refreshing a received setpoint's TTL.
    mutating func accept(token: String?, expectedToken: String, sequence: Double?, sentMs: Double?,
                         nowMs: Double, offsetMs: Double?, ttlMs: Double?) -> Double? {
        guard token == expectedToken, let seq = sequence, seq.isFinite, seq >= 0,
              seq.rounded(.down) == seq, seq <= 9_007_199_254_740_991,
              let sent = sentMs, sent.isFinite, let offset = offsetMs, offset.isFinite,
              nowMs.isFinite else { return nil }
        let requestedTTL = ttlMs ?? 300
        guard requestedTTL.isFinite, requestedTTL > 0 else { return nil }
        let ttl = min(300, requestedTTL)
        let age = nowMs - offset - sent
        guard ttl.isFinite, ttl > 0, age.isFinite, age >= -100, age < ttl else { return nil }
        if generation != expectedToken { generation = expectedToken; lastSequence = -1 }
        guard seq > lastSequence else { return nil }
        lastSequence = seq
        return (ttl - max(0, age)) / 1000
    }
}
