import CoreGraphics
import CoreVideo
import Foundation
import ImageIO
import Vision

/// Apple Vision person boxes (~5 Hz) plus the on-device LiDAR range inside each box.
/// Boxes are normalised xyxy with a TOP-LEFT origin in the upright (oriented) image — the same frame as the JPEG that
/// is uplinked — converted from Vision's bottom-left-origin `boundingBox`.
final class VisionBoxes {
    private let request: VNDetectHumanRectanglesRequest = {
        let r = VNDetectHumanRectanglesRequest()
        r.upperBodyOnly = false  // full-body boxes (iOS 15+)
        return r
    }()
    var minConfidence: Float = 0.3
    var maxBoxes = 8

    /// Synchronous; call on the pipeline queue.
    func detect(in pixelBuffer: CVPixelBuffer, orientation: CGImagePropertyOrientation) -> [CGRect] {
        let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer, orientation: orientation, options: [:])
        do {
            try handler.perform([request])
        } catch {
            return []
        }
        let observations: [VNDetectedObjectObservation] = request.results ?? []
        var boxes: [CGRect] = []
        for obs in observations where obs.confidence >= minConfidence {
            let bb = obs.boundingBox  // normalised, origin bottom-left
            guard bb.height > 0.03, bb.width > 0.01 else { continue }
            boxes.append(CGRect(x: bb.minX, y: 1.0 - bb.maxY, width: bb.width, height: bb.height))
            if boxes.count >= maxBoxes { break }
        }
        return boxes
    }

    /// `[x0, y0, x1, y1]` normalised, top-left origin, clamped to [0, 1].
    static func xyxy(_ r: CGRect) -> [Double] {
        func c(_ v: CGFloat) -> Double { Wire.rounded(min(1.0, max(0.0, Double(v))), 4) }
        return [c(r.minX), c(r.minY), c(r.maxX), c(r.maxY)]
    }

    /// Median valid depth in the central torso region of a box (x: middle 40 %, y: 20–60 % from the top), the same
    /// region the simulator uses. Needs >= 4 valid (confidence >= medium) samples, else nil.
    static func medianRange(box b: CGRect, orientation: CGImagePropertyOrientation, depth: DepthFrameData) -> Double? {
        let ox0 = b.minX + 0.3 * b.width
        let ox1 = b.maxX - 0.3 * b.width
        let oy0 = b.minY + 0.2 * b.height
        let oy1 = b.minY + 0.6 * b.height
        let p0 = ImageOrientationTracker.sensorPoint(CGPoint(x: ox0, y: oy0), from: orientation)
        let p1 = ImageOrientationTracker.sensorPoint(CGPoint(x: ox1, y: oy1), from: orientation)
        let dw = depth.width
        let dh = depth.height
        let sx0 = Double(min(p0.x, p1.x)), sx1 = Double(max(p0.x, p1.x))
        let sy0 = Double(min(p0.y, p1.y)), sy1 = Double(max(p0.y, p1.y))
        guard sx0.isFinite, sx1.isFinite, sy0.isFinite, sy1.isFinite else { return nil }
        let x0 = max(0, min(dw - 1, Int(sx0 * Double(dw))))
        let x1 = max(x0 + 1, min(dw, Int(sx1 * Double(dw))))
        let y0 = max(0, min(dh - 1, Int(sy0 * Double(dh))))
        let y1 = max(y0 + 1, min(dh, Int(sy1 * Double(dh))))
        var values: [Double] = []
        values.reserveCapacity((x1 - x0) * (y1 - y0))
        for y in y0..<y1 {
            for x in x0..<x1 {
                let i = y * dw + x
                let d = depth.depth[i]
                if d.isFinite && d > 0 && depth.confidence[i] >= 1 {
                    values.append(Double(d))
                }
            }
        }
        guard values.count >= 4 else { return nil }
        values.sort()
        let m = values.count
        let median = m % 2 == 1 ? values[m / 2] : (values[m / 2 - 1] + values[m / 2]) / 2.0
        return Wire.rounded(median, 2)
    }
}
