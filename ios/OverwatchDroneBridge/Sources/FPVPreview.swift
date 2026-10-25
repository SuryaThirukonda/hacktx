import DJISDK
#if canImport(DJIWidget)
import DJIWidget
#endif
import SwiftUI
import UIKit

/// Local FPV preview with DJIWidget's `DJIVideoPreviewer` (main thread).
/// Encoder-type mapping and the frame-control handler follow DJI's own samples (DJISDKSwiftDemo
/// `VideoPreviewerAdapter` / `CameraFPVViewController`): the Mini 2 camera needs `encoderType = ._DJIMini2` (which also
/// makes the previewer strip the Mini 2's extra AUDs) and a frame-control handler wired to the primary feed.
final class FPVPreviewController: NSObject, DJIVideoPreviewerFrameControlDelegate {
    private(set) var attached = false

    func attach(to view: UIView) {
        guard let previewer = DJIVideoPreviewer.instance() else { return }
        previewer.setView(view)
        previewer.frameControlHandler = self
        updateEncoderType()
        previewer.start()
        attached = true
    }

    func detach() {
        DJIVideoPreviewer.instance()?.unSetView()
        attached = false
    }

    /// Call whenever the product / camera changes.
    func updateEncoderType() {
        guard let previewer = DJIVideoPreviewer.instance() else { return }
        let camera = ProductLink.cameraDisplayName
        if camera == DJICameraDisplayNameDJIMini2Camera {
            previewer.encoderType = ._DJIMini2
        } else if camera == DJICameraDisplayNameMavicMiniCamera {
            previewer.encoderType = ._MavicMini
        } else {
            previewer.encoderType = ._unknown
        }
    }

    /// True when the uplink should strip the Mini 2's extra access-unit delimiters (same rule as DJIVideoPreviewer).
    static var needsAUDStrip: Bool {
        if ProductLink.cameraDisplayName == DJICameraDisplayNameDJIMini2Camera { return true }
        return ProductLink.isExpectedModel(ProductLink.modelName)
    }

    // MARK: DJIVideoPreviewerFrameControlDelegate (as in DJI's samples)

    func parseDecodingAssistInfo(withBuffer buffer: UnsafeMutablePointer<UInt8>!, length: Int32,
                                 assistInfo: UnsafeMutablePointer<DJIDecodingAssistInfo>!) -> Bool {
        DJISDKManager.videoFeeder()?.primaryVideoFeed.parseDecodingAssistInfo(withBuffer: buffer, length: length,
                                                                                assistInfo: assistInfo) ?? false
    }

    func isNeedFitFrameWidth() -> Bool {
        false
    }

    func syncDecoderStatus(_ isNormal: Bool) {
        DJISDKManager.videoFeeder()?.primaryVideoFeed.syncDecoderStatus(isNormal)
    }

    func decodingDidSucceed(withTimestamp timestamp: UInt32) {
        DJISDKManager.videoFeeder()?.primaryVideoFeed.decodingDidSucceed(withTimestamp: UInt(timestamp))
    }

    func decodingDidFail() {
        DJISDKManager.videoFeeder()?.primaryVideoFeed.decodingDidFail()
    }
}

/// Hosts the previewer's GL view. Attaches once the view has a real size (DJIVideoPreviewer sizes its GL view from
/// the host frame) and keeps it sized on layout.
final class FPVHostView: UIView {
    var onFirstLayout: ((UIView) -> Void)?
    private var didAttach = false

    override func layoutSubviews() {
        super.layoutSubviews()
        guard bounds.width > 1, bounds.height > 1 else { return }
        if !didAttach {
            didAttach = true
            onFirstLayout?(self)
        }
        DJIVideoPreviewer.instance()?.adjustViewSize()
    }
}

struct FPVPreviewView: UIViewRepresentable {
    let controller: FPVPreviewController

    func makeUIView(context: Context) -> FPVHostView {
        let view = FPVHostView()
        view.backgroundColor = .black
        let controller = self.controller
        view.onFirstLayout = { host in
            controller.attach(to: host)
        }
        return view
    }

    func updateUIView(_ uiView: FPVHostView, context: Context) {}

    static func dismantleUIView(_ uiView: FPVHostView, coordinator: ()) {
        DJIVideoPreviewer.instance()?.unSetView()
    }
}
