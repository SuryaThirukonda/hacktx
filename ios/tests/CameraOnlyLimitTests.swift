import Foundation

@main
struct CameraOnlyLimitTests {
    static func main() {
        let result = CameraOnlyLimit.apply(forward: 6, right: -6, up: 1.5, yaw: 45)
        assert(result.0 == 0 && result.1 == 0 && result.2 == 0 && result.3 == 15)
        assert(CameraOnlyLimit.apply(forward: .nan, right: .infinity, up: -.infinity, yaw: .nan).3 == 0)
        assert(CameraOnlyLimit.apply(forward: 1, right: 2, up: 3, yaw: -40).3 == -15)
        for sim in [false, true] {
            for real in [false, true] {
                for translation in [false, true] {
                    assert(CameraOnlyLimit.required(requestedFull: false, simulator: sim, realArmed: real, translationArmed: translation))
                    assert(CameraOnlyLimit.required(requestedFull: true, simulator: sim, realArmed: real, translationArmed: translation)
                           == !(sim || (real && translation)))
                }
            }
        }
        let motion = RealMotionLimit.apply(forward: 6, right: 8, up: 2, yaw: -90)
        assert(abs(hypot(motion.0, motion.1) - 1) < 0.0001 && motion.2 == 0.5 && motion.3 == -15)
        let invalid = RealMotionLimit.apply(forward: .nan, right: .infinity, up: .nan, yaw: .infinity)
        assert(invalid.0 == 0 && invalid.1 == 0 && invalid.2 == 0 && invalid.3 == 0)
        print("Camera-only phone limit: zero translation, yaw cap and non-finite input passed")
    }
}
