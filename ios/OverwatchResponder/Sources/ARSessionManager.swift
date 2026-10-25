import ARKit
import Foundation

/// Owns the ARKit session: world tracking with `.sceneDepth` when the device has LiDAR (camera-only otherwise,
/// reported as `lidar_supported=false`, `depth_mode="none"`), and hands frames to the pipeline at ~10 Hz.
/// Frames arrive on a private delegate queue; frames are skipped (never queued) while the pipeline is busy, so ARKit
/// never has to retain a backlog of ARFrames.
final class ARSessionManager: NSObject, ARSessionDelegate {
    let session = ARSession()
    let scan = LocalScan()
    private(set) var meshSupported = false
    private(set) var lidarSupported = false
    var targetHz: Double = 10.0

    /// Called on the AR delegate queue with a frame the pipeline has agreed to take.
    var onFrame: ((ARFrame) -> Void)?
    /// Asked on the AR delegate queue before `onFrame`; return false while still processing the previous frame.
    var canAcceptFrame: () -> Bool = { true }
    /// Human-readable session events (interruptions, failures), delivered on the main queue.
    var onEvent: ((String) -> Void)?

    private let delegateQueue = DispatchQueue(label: "com.overwatch.arkit", qos: .userInteractive)
    private var lastEmitTs: TimeInterval = 0

    static var isSupported: Bool { ARWorldTrackingConfiguration.isSupported }

    /// Main thread.
    func start() {
        guard Self.isSupported else {
            emit("ARKit world tracking is not supported on this device")
            return
        }
        scan.reset()
        let config = ARWorldTrackingConfiguration()
        meshSupported = ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh)
        if meshSupported { config.sceneReconstruction = .mesh }
        config.worldAlignment = .gravity  // y up, arbitrary yaw; the Mac registers ARKit to its local map
        if ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) {
            config.frameSemantics.insert(.sceneDepth)
            lidarSupported = true
        } else {
            lidarSupported = false
        }
        if let format = Self.preferredVideoFormat() {
            config.videoFormat = format
        }
        session.delegate = self
        session.delegateQueue = delegateQueue
        session.run(config, options: [.resetTracking, .removeExistingAnchors])
    }

    func pause() {
        session.pause()
    }

    /// 1920x1440 at 30 fps when offered (half the default 60 fps: less heat over a long incident; we only use 10 Hz).
    static func preferredVideoFormat() -> ARConfiguration.VideoFormat? {
        ARWorldTrackingConfiguration.supportedVideoFormats.first { format in
            format.framesPerSecond == 30
                && Int(format.imageResolution.width) == 1920
                && Int(format.imageResolution.height) == 1440
        }
    }

    // MARK: ARSessionDelegate (delegate queue)

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        let ts = frame.timestamp
        guard ts - lastEmitTs >= 0.9 / max(1.0, targetHz) else { return }
        guard canAcceptFrame() else { return }
        lastEmitTs = ts
        onFrame?(frame)
    }

    func session(_ session: ARSession, didAdd anchors: [ARAnchor]) {
        scan.update(anchors.compactMap { $0 as? ARMeshAnchor }, timestamp: session.currentFrame?.timestamp ?? 0)
    }

    func session(_ session: ARSession, didUpdate anchors: [ARAnchor]) {
        scan.update(anchors.compactMap { $0 as? ARMeshAnchor }, timestamp: session.currentFrame?.timestamp ?? 0)
    }

    func session(_ session: ARSession, didRemove anchors: [ARAnchor]) {
        scan.remove(anchors.map { $0.identifier })
    }

    func session(_ session: ARSession, didFailWithError error: Error) {
        emit("ARKit session failed: \(error.localizedDescription)")
    }

    func sessionWasInterrupted(_ session: ARSession) {
        emit("ARKit interrupted (camera unavailable)")
    }

    func sessionInterruptionEnded(_ session: ARSession) {
        emit("ARKit resumed")
    }

    private func emit(_ text: String) {
        DispatchQueue.main.async { [weak self] in
            self?.onEvent?(text)
        }
    }

    /// `ARCamera.TrackingState` → (`arkit_tracking_state`, `tracking_reason`).
    static func trackingInfo(_ state: ARCamera.TrackingState) -> (state: String, reason: String?) {
        switch state {
        case .normal:
            return ("normal", nil)
        case .notAvailable:
            return ("not_available", nil)
        case .limited(let reason):
            switch reason {
            case .initializing: return ("limited", "initializing")
            case .excessiveMotion: return ("limited", "excessiveMotion")
            case .insufficientFeatures: return ("limited", "insufficientFeatures")
            case .relocalizing: return ("limited", "relocalizing")
            @unknown default: return ("limited", "unknown")
            }
        @unknown default:
            return ("not_available", "unknown")
        }
    }
}
