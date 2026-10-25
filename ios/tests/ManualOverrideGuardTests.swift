import Foundation

@main
struct ManualOverrideGuardTests {
    static func main() {
        var guardrail = ManualOverrideGuard()
        func neutral(_ time: Double) {
            _ = guardrail.observe(sticks: [0, 0, 0, 0], wheel: 0, pause: false, home: false, mode: 1, at: time)
        }
        assert(guardrail.inhibited)
        assert(!guardrail.rearm(at: 0)) // no RC callback, no control
        neutral(1)
        assert(!guardrail.rearm(at: 1.1)) // neutral dwell required
        neutral(1.3)
        assert(guardrail.rearm(at: 1.3))
        assert(!guardrail.inhibited)
        let event = guardrail.observe(sticks: [0, 100, 0, 0], wheel: 0, pause: false, home: false, mode: 1, at: 1.4)
        assert(event != nil && guardrail.inhibited)
        neutral(1.5)
        neutral(1.8)
        assert(guardrail.inhibited) // releasing the sticks does not re-enable AI
        assert(guardrail.rearm(at: 1.8))
        assert(!guardrail.ready(at: 2.4)) // stale hardware callbacks fail closed
        assert(!guardrail.rearm(at: 2.4))
        neutral(2.5)
        assert(guardrail.observe(sticks: [0, 0, 0, 0], wheel: 0, pause: true, home: false, mode: 1, at: 2.6) != nil)
        assert(guardrail.inhibited)
        neutral(3)
        neutral(3.3)
        assert(guardrail.rearm(at: 3.3))
        assert(guardrail.observe(sticks: [0, 0, 0, 0], wheel: 0, pause: false, home: false, mode: 2, at: 3.4) != nil)
        assert(guardrail.inhibited)
        print("ManualOverrideGuard: initial gate, stick takeover, persistent latch, re-arm, stale RC, pause and mode switch passed")
    }
}
