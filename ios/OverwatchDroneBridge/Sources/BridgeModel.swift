import Foundation
import SwiftUI
import UIKit

/// Main-thread view model for the bridge UI. Settings persist in UserDefaults; the real work is in BridgeController.
final class BridgeModel: ObservableObject {
    private enum Keys {
        static let host = "ow.bridge.host"
        static let token = "ow.bridge.token"
        static let videoMode = "ow.bridge.videoMode"
        static let preview = "ow.bridge.preview"
    }

    @Published private(set) var ui = BridgeUISnapshot()
    @Published private(set) var wantsConnection = false
    @Published var connectError: String?

    @Published var host: String {
        didSet { UserDefaults.standard.set(host, forKey: Keys.host) }
    }
    @Published var token: String {
        didSet { UserDefaults.standard.set(token, forKey: Keys.token) }
    }
    @Published var videoMode: VideoForwarder.UplinkMode {
        didSet {
            UserDefaults.standard.set(videoMode.rawValue, forKey: Keys.videoMode)
            controller.setVideoMode(videoMode)
        }
    }
    @Published var previewEnabled: Bool {
        didSet {
            UserDefaults.standard.set(previewEnabled, forKey: Keys.preview)
            controller.video.setPreviewActive(previewEnabled)
        }
    }

    let controller = BridgeController()
    private var started = false

    init() {
        let d = UserDefaults.standard
        host = d.string(forKey: Keys.host) ?? ""
        token = d.string(forKey: Keys.token) ?? ""
        videoMode = VideoForwarder.UplinkMode(rawValue: d.string(forKey: Keys.videoMode) ?? "") ?? .h264
        previewEnabled = d.object(forKey: Keys.preview) as? Bool ?? true
    }

    /// Main thread; idempotent.
    func start() {
        guard !started else { return }
        started = true
        controller.onUI = { [weak self] snapshot in self?.ui = snapshot }
        controller.video.setPreviewActive(previewEnabled)
        controller.setVideoMode(videoMode)
        controller.start()
        UIApplication.shared.isIdleTimerDisabled = true
        if !host.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            connect()
        }
    }

    func connect() {
        connectError = nil
        if controller.connect(host: host, token: token) {
            wantsConnection = true
        } else {
            connectError = "Enter the Mac's address, e.g. 192.168.1.20 (port 8000 is the default)"
        }
    }

    func disconnect() {
        controller.disconnect()
        wantsConnection = false
    }

    func disableVirtualStick() {
        UINotificationFeedbackGenerator().notificationOccurred(.warning)
        controller.operatorDisable()
    }

    func rearm() {
        controller.operatorRearm()
    }

    func setRealFlightArmed(_ armed: Bool) {
        controller.setRealFlightArmed(armed)
    }

    func setTranslationArmed(_ armed: Bool) { controller.setTranslationArmed(armed) }

    func startSimulator() { controller.startSimulatorFromUI() }
    func stopSimulator() { controller.stopSimulatorFromUI() }
    func takeoffInSimulator() { controller.simTakeoff() }
    func landInSimulator() { controller.simLand() }
    func returnToHome() { controller.rtlFromUI() }

    func scenePhaseChanged(_ phase: ScenePhase) {
        switch phase {
        case .background:
            // The 20 Hz loop cannot be trusted once iOS suspends us: hand control back to the pilot now.
            var task = UIBackgroundTaskIdentifier.invalid
            let endTask = {
                if task != .invalid {
                    UIApplication.shared.endBackgroundTask(task)
                    task = .invalid
                }
            }
            task = UIApplication.shared.beginBackgroundTask(withName: "disable-virtual-stick") { endTask() }
            controller.appWillBackground()
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) { endTask() }
        case .active:
            UIApplication.shared.isIdleTimerDisabled = true
        default:
            break
        }
    }
}
