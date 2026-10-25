import CoreVideo
import Foundation
import simd

// Swift port of backend/overwatch/geometry/phone_depth.py (the reference LiDAR depth -> obstacle-sector algorithm).
// The simulator runs the Python original, the phone runs this, and the Mac receives the same PhoneObservation
// summary either way. Keep the two in lock-step: same sector angles, thresholds, floor histogram, corridor,
// 10th-percentile range, persistence/hysteresis, 0.1 m voxel downsample to <= 400 points.

/// Thresholds — mirror `PhoneCfg` in backend/overwatch/config.py (`phone:` in config/default.yaml).
struct PhoneDepthConfig {
    var corridorHalfWidthM = 0.9
    var maxRangeM = 5.0
    var minObstacleHeightM = 0.15
    var maxObstacleHeightM = 2.0
    var minPoints = 25
    var persistenceFrames = 2
    var proximityAlertM = 1.2
}

/// CPU copy of one ARKit scene-depth frame at full depth-map resolution (row-major, no row padding).
struct DepthFrameData {
    let width: Int
    let height: Int
    let depth: [Float]        // metres along the optical axis; 0 / NaN = invalid
    let confidence: [UInt8]   // ARConfidenceLevel raw values: 0 low, 1 medium, 2 high

    /// Copies `depthMap` (DepthFloat32) and `confidenceMap` (OneComponent8) out of their pixel buffers, locking and
    /// unlocking each correctly and honouring bytes-per-row padding. A missing/odd confidence map counts as medium.
    static func copy(depthMap: CVPixelBuffer, confidenceMap: CVPixelBuffer?) -> DepthFrameData? {
        guard CVPixelBufferGetPixelFormatType(depthMap) == kCVPixelFormatType_DepthFloat32 else { return nil }
        let w = CVPixelBufferGetWidth(depthMap)
        let h = CVPixelBufferGetHeight(depthMap)
        guard w > 0, h > 0 else { return nil }

        var depth = [Float](repeating: 0, count: w * h)
        CVPixelBufferLockBaseAddress(depthMap, .readOnly)
        guard let dBase = CVPixelBufferGetBaseAddress(depthMap) else {
            CVPixelBufferUnlockBaseAddress(depthMap, .readOnly)
            return nil
        }
        let dRow = CVPixelBufferGetBytesPerRow(depthMap)
        depth.withUnsafeMutableBufferPointer { dst in
            guard let out = dst.baseAddress else { return }
            for y in 0..<h {
                memcpy(out + y * w, dBase + y * dRow, w * MemoryLayout<Float>.size)
            }
        }
        CVPixelBufferUnlockBaseAddress(depthMap, .readOnly)

        var conf = [UInt8](repeating: 1, count: w * h)
        if let cm = confidenceMap,
           CVPixelBufferGetPixelFormatType(cm) == kCVPixelFormatType_OneComponent8,
           CVPixelBufferGetWidth(cm) == w, CVPixelBufferGetHeight(cm) == h {
            CVPixelBufferLockBaseAddress(cm, .readOnly)
            if let cBase = CVPixelBufferGetBaseAddress(cm) {
                let cRow = CVPixelBufferGetBytesPerRow(cm)
                conf.withUnsafeMutableBufferPointer { dst in
                    guard let out = dst.baseAddress else { return }
                    for y in 0..<h {
                        memcpy(out + y * w, cBase + y * cRow, w)
                    }
                }
            }
            CVPixelBufferUnlockBaseAddress(cm, .readOnly)
        }
        return DepthFrameData(width: w, height: h, depth: depth, confidence: conf)
    }
}

struct DepthResult {
    var sectors: [DepthSector]
    var obstaclePoints: [[Double]]
    var floorY: Double?
    var floorSource: String          // measured | previous | assumed | none
    var validFraction: Double
    var corridorNearestM: Double?
    var proximityAlert: Bool
    var hfovDeg: Double
    var pointCount: Int
    var obstacleCount: Int
    var floorPointCount: Int
}

/// `PhoneDepthProcessor` in Swift. Not thread-safe: use from one queue (the observation pipeline's).
final class DepthProcessor {
    static let sectorDefs: [(name: String, a0: Double, a1: Double)] = [
        ("left", -75.0, -40.0),
        ("front_left", -40.0, -12.0),
        ("front", -12.0, 12.0),
        ("front_right", 12.0, 40.0),
        ("right", 40.0, 75.0),
    ]

    let cfg: PhoneDepthConfig
    let step: Int
    private(set) var floorY: Double?
    private var nearCount = 0
    private var clearCount = 0
    private(set) var alertActive = false

    init(cfg: PhoneDepthConfig = PhoneDepthConfig(), step: Int = 2) {
        self.cfg = cfg
        self.step = max(1, step)
    }

    func reset() {
        floorY = nil
        nearCount = 0
        clearCount = 0
        alertActive = false
    }

    static func unknownSectors() -> [DepthSector] {
        sectorDefs.map { DepthSector.unknown($0.name) }
    }

    /// - Parameters:
    ///   - frame: full-resolution depth + confidence.
    ///   - K: intrinsics at the depth map's full resolution (pixel coordinates, OpenCV convention).
    ///   - pose: ARKit world-from-camera (ARKit camera axes: x right, y up, z back).
    func process(_ frame: DepthFrameData, intrinsics K: PinholeK, pose: RigidPose) -> DepthResult {
        let s = step
        let W = frame.width
        let H = frame.height
        let hs = (H + s - 1) / s   // numpy depth[::s, ::s]
        let ws = (W + s - 1) / s
        let fx = K.fx / Double(s)  // K[0, :] /= step; K[1, :] /= step
        let fy = K.fy / Double(s)
        let cx = K.cx / Double(s)
        let cy = K.cy / Double(s)
        let hfov = 2.0 * atan((Double(W) / 2.0) / K.fx) * 180.0 / .pi
        let maxRange = cfg.maxRangeM

        // 1-2. Reject invalid / low-confidence / out-of-range depth, back-project to the ARKit world (y up).
        var points: [SIMD3<Double>] = []
        points.reserveCapacity(hs * ws)
        var hconf: [UInt8] = []
        hconf.reserveCapacity(hs * ws)
        var validCount = 0
        frame.depth.withUnsafeBufferPointer { dp in
            frame.confidence.withUnsafeBufferPointer { cp in
                for vi in 0..<hs {
                    let rowBase = vi * s * W
                    for ui in 0..<ws {
                        let idx = rowBase + ui * s
                        let z = Double(dp[idx])
                        let c = cp[idx]
                        guard z.isFinite, z > 0.1, z <= maxRange, c >= 1 else { continue }
                        validCount += 1
                        let x = (Double(ui) + 0.5 - cx) * z / fx
                        let y = (Double(vi) + 0.5 - cy) * z / fy
                        // (p_cv @ ARKIT_FROM_CV.T) @ R.T + t, with ARKIT_FROM_CV = diag(1, -1, -1)
                        points.append(pose.apply(SIMD3<Double>(x, -y, -z)))
                        hconf.append(c)
                    }
                }
            }
        }
        let total = hs * ws
        let validFraction = total > 0 ? Double(validCount) / Double(total) : 0.0

        if validCount < 10 {
            updateAlert(nil)
            let prevSource = (floorY ?? 0.0) != 0.0 ? "previous" : "none"  // Python truthiness of self.floor_y
            return DepthResult(sectors: Self.unknownSectors(), obstaclePoints: [], floorY: floorY,
                               floorSource: prevSource, validFraction: validFraction, corridorNearestM: nil,
                               proximityAlert: alertActive, hfovDeg: hfov, pointCount: 0, obstacleCount: 0,
                               floorPointCount: 0)
        }

        // 3. Floor: dominant mode of heights well below the camera (3 cm histogram), smoothed over frames.
        let camY = pose.t.y
        var floorSource = floorY != nil ? "previous" : "assumed"
        var lowYs: [Double] = []
        for p in points where p.y < camY - 0.6 {
            lowYs.append(p.y)
        }
        if Double(lowYs.count) > max(30.0, 0.05 * Double(points.count)),
           let mn = lowYs.min(), let mx = lowYs.max() {
            let binW = 0.03
            let start = mn - 0.03
            let stop = mx + 0.06
            let nEdges = Int(((stop - start) / binW).rounded(.up))   // len(np.arange(start, stop, 0.03))
            let nBins = nEdges - 1
            if nBins >= 1 {
                var hist = [Int](repeating: 0, count: nBins)
                for y in lowYs {
                    var k = Int(((y - start) / binW).rounded(.down))
                    if k < 0 { k = 0 }
                    if k >= nBins { k = nBins - 1 }
                    hist[k] += 1
                }
                var best = 0
                for i in 1..<nBins where hist[i] > hist[best] {
                    best = i
                }
                if Double(hist[best]) > 0.25 * Double(lowYs.count) {
                    let est = start + (Double(best) + 0.5) * binW   // (edges[k] + edges[k+1]) / 2
                    if let prev = floorY {
                        floorY = 0.7 * prev + 0.3 * est
                    } else {
                        floorY = est
                    }
                    floorSource = "measured"
                }
            }
        }
        let floor = floorY ?? (camY - 1.35)

        // 4. Obstacles = height band above the floor; azimuth relative to the phone's horizontal forward.
        let fwd3 = -pose.c2
        var f = SIMD2<Double>(fwd3.x, fwd3.z)
        let fn = (f.x * f.x + f.y * f.y).squareRoot()
        f = fn > 1e-6 ? f / fn : SIMD2<Double>(0.0, -1.0)
        let r = SIMD2<Double>(-f.y, f.x)
        let n = points.count
        var obst = [Bool](repeating: false, count: n)
        var fwdD = [Double](repeating: 0, count: n)
        var latD = [Double](repeating: 0, count: n)
        var az = [Double](repeating: 0, count: n)
        var rng = [Double](repeating: 0, count: n)
        var obstacleCount = 0
        var floorPointCount = 0
        for i in 0..<n {
            let p = points[i]
            let h = p.y - floor
            let isObstacle = h > cfg.minObstacleHeightM && h < cfg.maxObstacleHeightM
            obst[i] = isObstacle
            if isObstacle { obstacleCount += 1 }
            if abs(h) < 0.08 { floorPointCount += 1 }
            let rx = p.x - pose.t.x
            let rz = p.z - pose.t.z
            let fd = rx * f.x + rz * f.y
            let ld = rx * r.x + rz * r.y
            fwdD[i] = fd
            latD[i] = ld
            az[i] = atan2(ld, fd) * 180.0 / .pi
            rng[i] = (fd * fd + ld * ld).squareRoot()
        }

        // 5. Robust range per sector (10th percentile) and support counts -> confidence.
        var sectors: [DepthSector] = []
        let halfFov = hfov / 2.0
        for def in Self.sectorDefs {
            if def.a1 <= -halfFov || def.a0 >= halfFov {
                sectors.append(.unknown(def.name))  // outside the sensor's field of view
                continue
            }
            var ranges: [Double] = []
            var highConf = 0
            var seen = 0
            for i in 0..<n {
                let a = az[i]
                guard a >= def.a0 && a < def.a1 else { continue }
                seen += 1
                if obst[i] && fwdD[i] > 0.1 {
                    ranges.append(rng[i])
                    if hconf[i] >= 2 { highConf += 1 }
                }
            }
            let m = ranges.count
            if m < 5 {
                // Visible but empty: report "no obstacle" only when the floor there was actually observed.
                let c = seen > cfg.minPoints * 3 ? "high" : "unknown"
                sectors.append(DepthSector(sector: def.name, nearest_m: nil, confidence: c, num_valid_points: seen))
                continue
            }
            let nearest = Self.percentile(ranges, 10)
            let hi = Double(highConf) / Double(m)
            let c: String
            if m >= cfg.minPoints * 3 && hi > 0.6 {
                c = "high"
            } else if m >= cfg.minPoints {
                c = "medium"
            } else {
                c = "low"
            }
            sectors.append(DepthSector(sector: def.name, nearest_m: Wire.rounded(nearest, 3), confidence: c,
                                       num_valid_points: m))
        }

        // 6. 3D walking corridor for the local proximity rule, with persistence + hysteresis.
        var corridor: [Double] = []
        for i in 0..<n where obst[i] && abs(latD[i]) < cfg.corridorHalfWidthM && fwdD[i] > 0.2
            && fwdD[i] < cfg.maxRangeM {
            corridor.append(fwdD[i])
        }
        let corridorNearest: Double? = corridor.count >= cfg.minPoints ? Self.percentile(corridor, 10) : nil
        updateAlert(corridorNearest)

        // Voxel-downsampled obstacle cloud for the Mac's occupancy grid: first point of each 0.1 m voxel, in
        // original (row-major) order, at most 400 — same as np.unique(..., return_index=True) + sort + [:400].
        var cloud: [[Double]] = []
        var voxels = Set<SIMD3<Int>>()
        for i in 0..<n where obst[i] {
            let p = points[i]
            let key = SIMD3<Int>(Int((p.x / 0.1).rounded(.down)), Int((p.y / 0.1).rounded(.down)),
                                 Int((p.z / 0.1).rounded(.down)))
            if voxels.insert(key).inserted {
                cloud.append([Wire.rounded(p.x, 3), Wire.rounded(p.y, 3), Wire.rounded(p.z, 3)])
                if cloud.count >= 400 { break }
            }
        }

        return DepthResult(sectors: sectors, obstaclePoints: cloud, floorY: floorY, floorSource: floorSource,
                           validFraction: validFraction, corridorNearestM: corridorNearest,
                           proximityAlert: alertActive, hfovDeg: hfov, pointCount: n, obstacleCount: obstacleCount,
                           floorPointCount: floorPointCount)
    }

    /// Persistence (N near frames to raise) + hysteresis (2 clear frames and > threshold + 0.3 m to clear).
    private func updateAlert(_ nearest: Double?) {
        let thr = cfg.proximityAlertM
        if let d = nearest, d < thr {
            nearCount += 1
            clearCount = 0
        } else {
            clearCount += 1
            nearCount = 0
        }
        if !alertActive && nearCount >= cfg.persistenceFrames {
            alertActive = true
        } else if alertActive && clearCount >= 2 && (nearest == nil || nearest! > thr + 0.3) {
            alertActive = false
        }
    }

    /// numpy.percentile with the default 'linear' method.
    static func percentile(_ values: [Double], _ p: Double) -> Double {
        precondition(!values.isEmpty)
        let sorted = values.sorted()
        let pos = Double(sorted.count - 1) * p / 100.0
        let lo = Int(pos.rounded(.down))
        let hi = min(lo + 1, sorted.count - 1)
        let frac = pos - Double(lo)
        return sorted[lo] + (sorted[hi] - sorted[lo]) * frac
    }
}

/// Cloud-independent proximity rule: on the rising edge of the corridor alert it fires haptic + speech
/// ("Obstacle ahead, 0.8 meters") whether or not the Mac is reachable. `update` is called on the pipeline queue;
/// `onFire` is delivered on the main queue.
final class LocalProximityNotifier {
    var cooldownS = 2.5
    var onFire: ((_ distanceM: Double?) -> Void)?
    private var wasActive = false
    private var lastFireS = -1.0e9

    func update(alertActive: Bool, nearestM: Double?, nowS: Double) {
        defer { wasActive = alertActive }
        guard alertActive, !wasActive, nowS - lastFireS >= cooldownS else { return }
        lastFireS = nowS
        let distance = nearestM
        DispatchQueue.main.async { [weak self] in
            self?.onFire?(distance)
        }
    }

    func reset() {
        wasActive = false
    }
}
