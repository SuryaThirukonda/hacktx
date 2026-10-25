import ARKit
import Foundation
import ImageIO

/// What the UI shows about the on-device pipeline (published to the main queue at ~5 Hz).
struct PipelineUIState {
    var trackingState = "not_available"
    var trackingReason: String?
    var lidarSupported = false
    var depthValidFraction: Double?
    var sectors: [DepthSector] = DepthProcessor.unknownSectors()
    var corridorNearestM: Double?
    var localAlertActive = false
    var floorYM: Double?
    var personCount = 0
    var nearestPersonM: Double?
    var orientation = "portrait"
    var observationsSent = 0
    var jpegsSent = 0
    var observationRateHz = 0.0
    var processingMs = 0.0
    var headYawDeg: Double?
}

/// ARFrame (~10 Hz) → depth sectors + local proximity rule + Vision boxes (~5 Hz) + JPEG (~8 fps) → PhoneObservation.
/// Runs on its own serial queue; at most one frame is in flight (ARSessionManager skips frames while busy).
final class ObservationPipeline {
    var onUIState: ((PipelineUIState) -> Void)?   // delivered on the main queue

    let notifier = LocalProximityNotifier()

    // Settings written from the main thread.
    let sendVideo = Locked(true)
    let jpegFps = Locked(8.0)
    let lidarSupported = Locked(false)
    let sessionId = Locked<String?>(nil)
    private let zeroHeadRequest = Locked(false)

    private let transport: CaptureTransport
    private let sensors: SensorHub
    private let depth = DepthProcessor()
    private let vision = VisionBoxes()
    private let jpeg = JPEGEncoder()
    private let queue = DispatchQueue(label: "com.overwatch.pipeline", qos: .userInitiated)
    private let busy = Locked(false)

    // Pipeline-queue state.
    private var seq = 0
    private var orientation = ImageOrientationTracker()
    private var visionPhase = false
    private var lastVision: (boxes: [[Double]], ranges: [Double?], tsS: Double)?
    private var jpegCredit = 1.0
    private var lastFrameTsS: Double?
    private var lastUIPublishS = 0.0
    private var headCalibration: (head: Double, phone: Double)?
    private var ui = PipelineUIState()
    private var sendTimes: [Double] = []

    init(transport: CaptureTransport, sensors: SensorHub) {
        self.transport = transport
        self.sensors = sensors
    }

    /// Thread-safe; read by ARSessionManager on the AR delegate queue.
    var isBusy: Bool { busy.current }

    /// Called on the AR delegate queue. The ARFrame is retained only while it is being processed.
    func submit(_ frame: ARFrame) {
        busy.set(true)
        queue.async { [weak self] in
            guard let self = self else { return }
            let started = DeviceClock.nowS()
            self.process(frame)
            self.ui.processingMs = (DeviceClock.nowS() - started) * 1000.0
            self.busy.set(false)
        }
    }

    /// Next frame: treat the current head direction as "facing the same way as the phone".
    func requestZeroHeadYaw() {
        zeroHeadRequest.set(true)
    }

    func resetDepthState() {
        queue.async {
            self.depth.reset()
            self.notifier.reset()
        }
    }

    // MARK: - Per-frame work (pipeline queue)

    private func process(_ frame: ARFrame) {
        let tsS = frame.timestamp
        let tsMs = tsS * 1000.0                      // same timebase as DeviceClock / sync replies
        let camera = frame.camera
        let tracking = ARSessionManager.trackingInfo(camera.trackingState)
        let trackingNormal = tracking.state == "normal"
        let pose = RigidPose(camera.transform)
        let imageW = Double(camera.imageResolution.width)
        let imageH = Double(camera.imageResolution.height)
        let orient = orientation.update(pose)
        let hasLidar = lidarSupported.current

        // 1. LiDAR depth → sectors, obstacle cloud, corridor proximity (skipped only when ARKit has no pose at all).
        var depthData: DepthFrameData?
        var depthK: PinholeK?
        var result: DepthResult?
        if hasLidar, tracking.state != "not_available", imageW > 0, imageH > 0,
           let sceneDepth = frame.sceneDepth,
           let data = DepthFrameData.copy(depthMap: sceneDepth.depthMap, confidenceMap: sceneDepth.confidenceMap) {
            // ARKit intrinsics are for capturedImage; scale them to the depth-map resolution.
            let K = PinholeK(camera.intrinsics).scaled(sx: Double(data.width) / imageW, sy: Double(data.height) / imageH)
            depthData = data
            depthK = K
            result = depth.process(data, intrinsics: K, pose: pose)
        }

        // 2. Local proximity rule — independent of the Mac connection.
        notifier.update(alertActive: result?.proximityAlert ?? false, nearestM: result?.corridorNearestM, nowS: tsS)

        // 3. Apple Vision person boxes at ~5 Hz (every other frame); the previous result is reused for one frame.
        visionPhase.toggle()
        var boxes: [[Double]]?
        var ranges: [Double?]?
        if visionPhase {
            let rects = vision.detect(in: frame.capturedImage, orientation: orient)
            let b = rects.map { VisionBoxes.xyxy($0) }
            let r: [Double?] = rects.map { rect -> Double? in
                guard let d = depthData else { return nil }
                return VisionBoxes.medianRange(box: rect, orientation: orient, depth: d)
            }
            lastVision = (boxes: b, ranges: r, tsS: tsS)
            boxes = b
            ranges = r
        } else if let last = lastVision, tsS - last.tsS < 0.15 {
            boxes = last.boxes
            ranges = last.ranges
        }

        // 4. Camera JPEG (~8 fps, upright, ~640 px, q 0.6) — sent BEFORE the observation of the same frame.
        let dt = lastFrameTsS.map { min(0.5, max(0.0, tsS - $0)) } ?? 0.1
        lastFrameTsS = tsS
        jpegCredit = min(2.0, jpegCredit + dt * jpegFps.current)
        if sendVideo.current, jpegCredit >= 1.0, transport.canAcceptVideo {
            jpegCredit -= 1.0
            if let data = jpeg.encode(frame.capturedImage, orientation: orient, maxWidth: 640, quality: 0.6) {
                transport.sendJPEG(data, captureTsMs: tsMs)
                ui.jpegsSent += 1
            }
        }

        // 5. Compass / GPS / AirPods / battery.
        let s = sensors.snapshot
        let nowS = DeviceClock.nowS()
        var gps: GPSFix?
        if let fixTime = s.gpsUptimeS, nowS - fixTime < 30.0 {
            gps = s.gps
        }
        let headYaw = headYawRelativeToPhone(s, pose: pose, trackingNormal: trackingNormal, nowS: nowS)

        // 6. The observation.
        seq += 1
        let observation = PhoneObservation(
            session_id: sessionId.current,
            seq: seq,
            capture_ts_ms: tsMs,
            arkit_tracking_state: tracking.state,
            tracking_reason: tracking.reason,
            camera_pose_4x4: trackingNormal ? pose.rowMajor4x4() : nil,
            camera_intrinsics_3x3: depthK?.rowMajor3x3(),
            image_resolution: [Int(imageW), Int(imageH)],
            image_orientation: Self.wireName(orient),
            lidar_supported: hasLidar,
            depth_mode: result != nil ? "sceneDepth" : "none",
            depth_valid_fraction: result.map { Wire.rounded($0.validFraction, 3) },
            sectors: result?.sectors ?? [],
            obstacle_points: trackingNormal ? result?.obstaclePoints : nil,
            floor_y_m: Wire.finite(result?.floorY).map { Wire.rounded($0, 3) },
            heading_deg: Wire.finite(s.headingDeg).map { Wire.rounded($0, 1) },
            heading_accuracy_deg: Wire.finite(s.headingAccuracyDeg).map { Wire.rounded($0, 1) },
            head_yaw_deg: headYaw,
            gps: gps,
            battery_pct: s.batteryPct,
            vision_boxes: boxes,
            vision_ranges_m: ranges,
            local_alert_active: result?.proximityAlert ?? false)
        transport.sendObservation(observation)

        // 7. UI (~5 Hz).
        ui.observationsSent += 1
        sendTimes.append(nowS)
        sendTimes.removeAll { nowS - $0 > 2.0 }
        if nowS - lastUIPublishS >= 0.2 {
            lastUIPublishS = nowS
            ui.trackingState = tracking.state
            ui.trackingReason = tracking.reason
            ui.lidarSupported = hasLidar
            ui.depthValidFraction = result?.validFraction
            ui.sectors = result?.sectors ?? DepthProcessor.unknownSectors()
            ui.corridorNearestM = result?.corridorNearestM
            ui.localAlertActive = result?.proximityAlert ?? false
            ui.floorYM = result?.floorY
            ui.personCount = boxes?.count ?? 0
            ui.nearestPersonM = ranges?.compactMap { $0 }.min()
            ui.orientation = Self.describe(orient)
            ui.observationRateHz = Double(sendTimes.count) / 2.0
            ui.headYawDeg = headYaw
            let snapshot = ui
            DispatchQueue.main.async { [weak self] in
                self?.onUIState?(snapshot)
            }
        }
    }

    /// AirPods yaw relative to the phone (clockwise degrees), as the Mac expects (`head_yaw_deg` is added to the
    /// compass heading). Both yaws are relative to a calibration taken when the head faced the same way as the
    /// phone (automatically at the first sample, or on request), so neither reference frame needs to be absolute.
    private func headYawRelativeToPhone(_ s: SensorSnapshot, pose: RigidPose, trackingNormal: Bool,
                                        nowS: Double) -> Double? {
        guard let head = s.headYawCWDeg, let headTime = s.headUptimeS, nowS - headTime < 0.5 else {
            if s.headUptimeS == nil { headCalibration = nil }  // head tracking off: recalibrate when it returns
            return nil
        }
        guard trackingNormal else { return nil }
        let phone = pose.yawCWDeg
        if headCalibration == nil || zeroHeadRequest.current {
            headCalibration = (head: head, phone: phone)
            zeroHeadRequest.set(false)
        }
        guard let cal = headCalibration else { return nil }
        return Wire.rounded(Self.wrap180((head - cal.head) - (phone - cal.phone)), 1)
    }

    static func wrap180(_ angle: Double) -> Double {
        var a = (angle + 180.0).truncatingRemainder(dividingBy: 360.0)
        if a < 0 { a += 360.0 }
        a -= 180.0
        return a == -180.0 ? 180.0 : a
    }

    /// `image_orientation` on the wire (CGImagePropertyOrientation naming; the Mac derives the upright FOV from it).
    private static func wireName(_ o: CGImagePropertyOrientation) -> String? {
        switch o {
        case .up: return "up"
        case .down: return "down"
        case .left: return "left"
        case .right: return "right"
        default: return nil
        }
    }

    private static func describe(_ o: CGImagePropertyOrientation) -> String {
        switch o {
        case .right: return "portrait"
        case .left: return "portrait (upside down)"
        case .up: return "landscape"
        case .down: return "landscape (flipped)"
        default: return "other"
        }
    }
}
