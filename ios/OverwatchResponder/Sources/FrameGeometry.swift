import CoreGraphics
import CoreImage
import CoreVideo
import Foundation
import ImageIO
import simd

/// ARKit world-from-camera transform in Double precision. Columns are the ARKit camera axes expressed in the ARKit
/// world (camera x right, y up, z back — in the sensor's native landscape orientation); `t` is the camera position.
struct RigidPose {
    let c0: SIMD3<Double>
    let c1: SIMD3<Double>
    let c2: SIMD3<Double>
    let t: SIMD3<Double>

    init(c0: SIMD3<Double>, c1: SIMD3<Double>, c2: SIMD3<Double>, t: SIMD3<Double>) {
        self.c0 = c0
        self.c1 = c1
        self.c2 = c2
        self.t = t
    }

    /// From `ARCamera.transform` (simd is column-major: `m.columns.j` is column j).
    init(_ m: simd_float4x4) {
        c0 = SIMD3<Double>(Double(m.columns.0.x), Double(m.columns.0.y), Double(m.columns.0.z))
        c1 = SIMD3<Double>(Double(m.columns.1.x), Double(m.columns.1.y), Double(m.columns.1.z))
        c2 = SIMD3<Double>(Double(m.columns.2.x), Double(m.columns.2.y), Double(m.columns.2.z))
        t = SIMD3<Double>(Double(m.columns.3.x), Double(m.columns.3.y), Double(m.columns.3.z))
    }

    /// R·p + t
    @inline(__always)
    func apply(_ p: SIMD3<Double>) -> SIMD3<Double> {
        c0 * p.x + c1 * p.y + c2 * p.z + t
    }

    /// Row-major 4x4 for `camera_pose_4x4`: row i = [R[i][0], R[i][1], R[i][2], t[i]].
    func rowMajor4x4(places: Int = 5) -> [[Double]] {
        func r(_ v: Double) -> Double { Wire.rounded(v, places) }
        return [
            [r(c0.x), r(c1.x), r(c2.x), r(t.x)],
            [r(c0.y), r(c1.y), r(c2.y), r(t.y)],
            [r(c0.z), r(c1.z), r(c2.z), r(t.z)],
            [0, 0, 0, 1],
        ]
    }

    /// Heading of the optical axis in the ARKit world (deg, clockwise from ARKit −z), exactly as
    /// `arkit_horizontal` in backend/overwatch/geometry/registration.py computes it.
    var yawCWDeg: Double {
        let fwd = -c2
        return atan2(fwd.x, -fwd.z) * 180.0 / .pi
    }
}

/// Pinhole intrinsics in pixels (OpenCV convention).
struct PinholeK {
    var fx: Double
    var fy: Double
    var cx: Double
    var cy: Double

    init(fx: Double, fy: Double, cx: Double, cy: Double) {
        self.fx = fx
        self.fy = fy
        self.cx = cx
        self.cy = cy
    }

    /// From `ARCamera.intrinsics` (column-major: column 2 holds (cx, cy, 1)). These are for `camera.imageResolution`.
    init(_ k: simd_float3x3) {
        fx = Double(k.columns.0.x)
        fy = Double(k.columns.1.y)
        cx = Double(k.columns.2.x)
        cy = Double(k.columns.2.y)
    }

    /// Rescale from the captured-image resolution to another resolution (e.g. the 256x192 depth map).
    func scaled(sx: Double, sy: Double) -> PinholeK {
        PinholeK(fx: fx * sx, fy: fy * sy, cx: cx * sx, cy: cy * sy)
    }

    func rowMajor3x3(places: Int = 4) -> [[Double]] {
        [[Wire.rounded(fx, places), 0, Wire.rounded(cx, places)],
         [0, Wire.rounded(fy, places), Wire.rounded(cy, places)],
         [0, 0, 1]]
    }
}

/// Which way the phone is held, derived from gravity in the camera pose (works with the UI locked to portrait).
/// The returned value is the `CGImagePropertyOrientation` that makes the sensor-native (landscape) captured image
/// upright, as used by Vision and for the uplinked JPEG.
struct ImageOrientationTracker {
    private(set) var orientation: CGImagePropertyOrientation = .right  // portrait

    mutating func update(_ pose: RigidPose) -> CGImagePropertyOrientation {
        // World-up (ARKit +y) components of the camera's x (image right) and y (image up) axes.
        let ux = pose.c0.y
        let uy = pose.c1.y
        let margin = 0.2  // hysteresis so a phone pointed at the floor does not flicker
        if abs(ux) > abs(uy) + margin {
            orientation = ux < 0 ? .right : .left   // portrait (image right points down) / upside-down portrait
        } else if abs(uy) > abs(ux) + margin {
            orientation = uy > 0 ? .up : .down      // native landscape (home side right) / the other landscape
        }
        return orientation
    }

    /// Maps a normalised top-left-origin point of the upright (oriented) image back to the sensor-native image.
    static func sensorPoint(_ p: CGPoint, from orientation: CGImagePropertyOrientation) -> CGPoint {
        switch orientation {
        case .right: return CGPoint(x: p.y, y: 1 - p.x)       // upright = sensor rotated 90° clockwise
        case .left: return CGPoint(x: 1 - p.y, y: p.x)        // upright = sensor rotated 90° counter-clockwise
        case .down: return CGPoint(x: 1 - p.x, y: 1 - p.y)    // 180°
        default: return p
        }
    }
}

/// `capturedImage` (YCbCr, sensor orientation) → upright JPEG ~640 px wide. One CIContext is reused.
final class JPEGEncoder {
    private let context = CIContext(options: [.cacheIntermediates: false])
    private let colorSpace = CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB()
    private let qualityKey = CIImageRepresentationOption(rawValue: kCGImageDestinationLossyCompressionQuality as String)

    func encode(_ pixelBuffer: CVPixelBuffer, orientation: CGImagePropertyOrientation, maxWidth: CGFloat,
                quality: CGFloat) -> Data? {
        var image = CIImage(cvPixelBuffer: pixelBuffer).oriented(orientation)
        image = image.transformed(by: CGAffineTransform(translationX: -image.extent.origin.x,
                                                        y: -image.extent.origin.y))
        let width = image.extent.width
        guard width > 0 else { return nil }
        if width > maxWidth {
            let s = maxWidth / width
            image = image.transformed(by: CGAffineTransform(scaleX: s, y: s))
        }
        return context.jpegRepresentation(of: image, colorSpace: colorSpace, options: [qualityKey: quality])
    }
}
