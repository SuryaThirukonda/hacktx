import DJISDK
#if canImport(DJIWidget)
import DJIWidget
#endif
import Foundation
import UIKit

/// Listens to `primaryVideoFeed` and
/// * pushes the raw stream into DJIWidget's `DJIVideoPreviewer` for the local FPV preview, and
/// * forwards it to the Mac as binary kind 0x02 (`[0x02][Float64 BE DeviceClock ms][H.264 Annex-B]`).
///
/// The Mac decodes with PyAV's parser, which copes with arbitrary chunking but needs SPS/PPS before it can decode.
/// After any drop (congestion) or (re)connect we therefore resume at the next SPS or IDR (re-sending cached SPS/PPS
/// in front of an IDR), falling back to cached parameter sets after 2 s for streams that use intra refresh.
/// Optional JPEG mode (kind 0x01) snapshots the decoded preview instead — a fallback if H.264 decoding on the Mac
/// misbehaves for a given aircraft.
final class VideoForwarder: NSObject, DJIVideoFeedListener {
    enum UplinkMode: String, CaseIterable, Identifiable {
        case h264
        case jpeg
        case off

        var id: String { rawValue }
        var label: String {
            switch self {
            case .h264: return "H.264"
            case .jpeg: return "JPEG"
            case .off: return "Off"
            }
        }
    }

    struct Stats {
        var chunksIn = 0
        var bytesIn = 0
        var lastChunkUptimeS: Double?
        var framesSent = 0
        var bytesSent = 0
        var dropped = 0
        var resyncs = 0
        var jpegsSent = 0
        var stripAUD = false
    }

    private struct Settings {
        var mode: UplinkMode = .h264
        var previewActive = false
        var stripAUD = false
    }

    /// Wired to `ReconnectingWebSocket.sendDroppable` (the closure is called when the frame was dropped).
    var sendFrame: ((Data, @escaping () -> Void) -> Void)?
    /// Cheap congestion pre-check.
    var canSend: () -> Bool = { false }

    private let videoQueue = DispatchQueue(label: "com.overwatch.bridge.video", qos: .userInitiated)
    private let encodeQueue = DispatchQueue(label: "com.overwatch.bridge.jpeg", qos: .utility)
    private let settings = Locked(Settings())
    private let statsBox = Locked(Stats())

    // videoQueue-confined
    private let audStripper = AUDStripper()
    private var needResync = true
    private var resyncSinceMs = DeviceClock.nowMs()
    private var sps: Data?
    private var pps: Data?

    // main-thread
    private var listening = false
    private var jpegTimer: Timer?
    private var jpegRequestedAt: Double?

    var stats: Stats { statsBox.current }

    // MARK: Settings (any thread)

    var mode: UplinkMode {
        get { settings.current.mode }
        set {
            settings.update { $0.mode = newValue }
            markNeedResync()
        }
    }

    func setPreviewActive(_ active: Bool) {
        settings.update { $0.previewActive = active }
    }

    /// DJIWidget strips these AUDs for the Mini 2 (H264EncoderType_DJIMini2); so do we for the uplink.
    /// VERIFY: the Mac's PyAV decoder shows clean Mini 2 frames with this on; if not, use the JPEG uplink mode.
    func setStripAUD(_ strip: Bool) {
        settings.update { $0.stripAUD = strip }
        statsBox.update { $0.stripAUD = strip }
    }

    func markNeedResync() {
        videoQueue.async {
            self.needResync = true
            self.resyncSinceMs = DeviceClock.nowMs()
            self.audStripper.reset()
            self.statsBox.update { $0.resyncs += 1 }
        }
    }

    // MARK: Feed attach (main thread)

    func attachToFeed() {
        guard let feeder = DJISDKManager.videoFeeder() else { return }
        if listening {
            feeder.primaryVideoFeed.remove(self)
        }
        // Callbacks on our own serial queue: keeps per-chunk work off the main thread and in order.
        feeder.primaryVideoFeed.add(self, with: videoQueue)
        listening = true
        markNeedResync()
    }

    func detachFromFeed() {
        guard listening else { return }
        DJISDKManager.videoFeeder()?.primaryVideoFeed.remove(self)
        listening = false
    }

    // MARK: DJIVideoFeedListener (videoQueue)

    func videoFeed(_ videoFeed: DJIVideoFeed, didUpdateVideoData videoData: Data) {
        let tsMs = DeviceClock.nowMs()
        let s = settings.current
        statsBox.update {
            $0.chunksIn += 1
            $0.bytesIn += videoData.count
            $0.lastChunkUptimeS = tsMs / 1000.0
        }
        if s.previewActive {
            Self.pushToPreviewer(videoData)
        }
        if s.mode == .h264 {
            forwardH264(videoData, tsMs: tsMs, stripAUD: s.stripAUD)
        }
    }

    private static func pushToPreviewer(_ data: Data) {
        guard let previewer = DJIVideoPreviewer.instance() else { return }
        var copy = data
        copy.withUnsafeMutableBytes { (raw: UnsafeMutableRawBufferPointer) in
            guard let base = raw.baseAddress else { return }
            previewer.push(base.assumingMemoryBound(to: UInt8.self), length: Int32(raw.count))
        }
    }

    private func forwardH264(_ chunk: Data, tsMs: Double, stripAUD: Bool) {
        let cleaned = stripAUD ? audStripper.process(chunk) : chunk
        guard !cleaned.isEmpty else { return }
        guard canSend() else {
            if !needResync {
                needResync = true
                resyncSinceMs = tsMs
            }
            statsBox.update { $0.dropped += 1 }
            return
        }
        let bytes = [UInt8](cleaned)
        let units = H264Scan.nalUnits(in: bytes)
        rememberParameterSets(bytes, units)
        var payload = cleaned
        if needResync {
            if let i = units.firstIndex(where: { $0.type == 7 || $0.type == 5 }) {
                var out = Data()
                if units[i].type == 5, let sps = sps, let pps = pps {
                    out.append(sps)
                    out.append(pps)
                }
                out.append(contentsOf: bytes[units[i].start...])
                payload = out
            } else if tsMs - resyncSinceMs > 2000, let sps = sps, let pps = pps {
                var out = Data()  // intra-refresh streams may never send an IDR
                out.append(sps)
                out.append(pps)
                out.append(cleaned)
                payload = out
            } else if tsMs - resyncSinceMs <= 4000 {
                statsBox.update { $0.dropped += 1 }
                return
            }
            needResync = false
        }
        let frame = Wire.binaryFrame(kind: .h264, captureTsMs: tsMs, payload: payload)
        statsBox.update {
            $0.framesSent += 1
            $0.bytesSent += frame.count
        }
        sendFrame?(frame, { [weak self] in
            self?.markNeedResync()
        })
    }

    /// Caches complete SPS/PPS units (only when the next start code is in the same chunk).
    private func rememberParameterSets(_ bytes: [UInt8], _ units: [H264Scan.NAL]) {
        for (k, unit) in units.enumerated() where unit.type == 7 || unit.type == 8 {
            guard k + 1 < units.count else { continue }
            let ps = Data(bytes[unit.start..<units[k + 1].start])
            if unit.type == 7 {
                sps = ps
            } else {
                pps = ps
            }
        }
    }

    // MARK: JPEG fallback (main thread)

    /// ~8 fps snapshots of the decoded preview (requires the preview to be on screen).
    func startJPEGTimer() {
        guard jpegTimer == nil else { return }
        jpegTimer = Timer.scheduledTimer(withTimeInterval: 1.0 / 8.0, repeats: true) { [weak self] _ in
            self?.jpegTick()
        }
    }

    func stopJPEGTimer() {
        jpegTimer?.invalidate()
        jpegTimer = nil
        jpegRequestedAt = nil
    }

    private func jpegTick() {
        let s = settings.current
        guard s.mode == .jpeg, s.previewActive, canSend(), let previewer = DJIVideoPreviewer.instance() else { return }
        let now = DeviceClock.nowMs()
        if let requested = jpegRequestedAt, now - requested < 1000 {
            return  // previous snapshot still pending (DJIWidget answers on the next rendered frame)
        }
        jpegRequestedAt = now
        previewer.snapshotPreview { [weak self] image in
            guard let self = self else { return }
            self.jpegRequestedAt = nil
            guard let image = image else { return }
            self.encodeQueue.async {
                guard let jpeg = Self.jpegData(image, maxWidth: 960, quality: 0.6) else { return }
                let frame = Wire.binaryFrame(kind: .jpeg, captureTsMs: now, payload: jpeg)
                self.statsBox.update { $0.jpegsSent += 1 }
                self.sendFrame?(frame, {})
            }
        }
    }

    private static func jpegData(_ image: UIImage, maxWidth: CGFloat, quality: CGFloat) -> Data? {
        let size = image.size
        guard size.width > 0, size.height > 0 else { return nil }
        let scale = min(1.0, maxWidth / size.width)
        let target = CGSize(width: (size.width * scale).rounded(), height: (size.height * scale).rounded())
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1
        let renderer = UIGraphicsImageRenderer(size: target, format: format)
        return renderer.jpegData(withCompressionQuality: quality) { _ in
            image.draw(in: CGRect(origin: .zero, size: target))
        }
    }
}
