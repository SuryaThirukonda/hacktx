import Foundation

@main
struct CommandFreshnessGuardTests {
    static func main() {
        for type in ["vstick", "gimbal", "mode", "rtl", "operator_rearm", "sim_start", "sim_stop"] {
            var gate = CommandFreshnessGuard()
            func accept(_ sequence: Double?, _ sent: Double?, token: String? = "one", ttl: Double? = nil,
                        offset: Double? = 0) -> Double? {
                gate.accept(token: token, expectedToken: "one", sequence: sequence, sentMs: sent,
                            nowMs: 1000, offsetMs: offset, ttlMs: ttl)
            }
            assert(!CommandFreshnessGuard.isAbort(type, enable: true))
            assert(accept(1, 699) == nil) // stale across every non-abort command
            assert(accept(1, 1101) == nil) // implausible future
            assert(accept(1, 950, token: "old") == nil)
            assert(accept(1, 950, offset: nil) == nil)
            assert(accept(.nan, 950) == nil && accept(1, .nan) == nil)
            assert(accept(1.5, 950) == nil && accept(1, 950, ttl: -1) == nil)
            assert(accept(1, 950, ttl: .nan) == nil && accept(1, 950, ttl: .infinity) == nil)
            assert(abs(accept(2, 950)! - 0.25) < 0.00001)
            assert(accept(2, 950) == nil && accept(1, 950) == nil) // replay/reorder
            gate.noteAbort(sequence: 5, token: "one")
            assert(accept(4, 950) == nil) // buffered re-arm before stop
            assert(accept(6, 950) != nil) // explicit later re-arm; native RC still gates it
            assert(gate.accept(token: "one", expectedToken: "two", sequence: 7, sentMs: 950,
                               nowMs: 1000, offsetMs: 0, ttlMs: 300) == nil)
            assert(gate.accept(token: "two", expectedToken: "two", sequence: 1, sentMs: 950,
                               nowMs: 1000, offsetMs: 0, ttlMs: 300) != nil)
        }
        for type in ["hover", "estop", "mode"] {
            assert(CommandFreshnessGuard.isAbort(type, enable: false)) // no token, sync or freshness required
        }
        print("CommandFreshnessGuard: all motion/setup types, TTL, sequence, sync, token, stop and re-arm generations passed")
    }
}
