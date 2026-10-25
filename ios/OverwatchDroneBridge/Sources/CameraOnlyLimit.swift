import Foundation

/// Defence in depth on the phone: network/model mistakes cannot introduce translation in this profile.
enum CameraOnlyLimit {
    static func required(requestedFull: Bool, simulator: Bool, realArmed: Bool, translationArmed: Bool) -> Bool {
        !requestedFull || (!simulator && !(realArmed && translationArmed))
    }
    static func apply(forward: Double, right: Double, up: Double, yaw: Double) -> (Double, Double, Double, Double) {
        let finiteYaw = yaw.isFinite ? yaw : 0
        return (0, 0, 0, min(15, max(-15, finiteYaw)))
    }
}

/// Conservative first physical translation test: vector speed cap, never a per-axis diagonal loophole.
enum RealMotionLimit {
    static func apply(forward: Double, right: Double, up: Double, yaw: Double) -> (Double, Double, Double, Double) {
        var f = forward.isFinite ? forward : 0
        var r = right.isFinite ? right : 0
        let speed = hypot(f, r)
        if speed > 1 { f /= speed; r /= speed }
        let u = up.isFinite ? min(0.5, max(-0.5, up)) : 0
        let y = yaw.isFinite ? min(15, max(-15, yaw)) : 0
        return (f, r, u, y)
    }
}
