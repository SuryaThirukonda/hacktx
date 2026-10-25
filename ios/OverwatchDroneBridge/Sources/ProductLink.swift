import DJISDK
import Foundation

/// DJI Mobile SDK v4 registration + product connection.
/// `registerApp` reads `DJISDKAppKey` from Info.plist and needs internet on first launch; on success the SDK is
/// asked to connect to the product over the remote controller's USB (External Accessory: com.dji.*).
/// DJI calls these delegate methods on the main queue.
final class ProductLink: NSObject, DJISDKManagerDelegate {
    var onRegistration: ((_ ok: Bool, _ message: String) -> Void)?
    var onProductChange: (() -> Void)?
    var onComponentChange: (() -> Void)?
    var onDatabaseProgress: ((Double) -> Void)?

    private(set) var registered = false

    static let appKeyInfoPlistKey = "DJISDKAppKey"

    func register() {
        let key = (Bundle.main.object(forInfoDictionaryKey: Self.appKeyInfoPlistKey) as? String) ?? ""
        let trimmed = key.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty || trimmed.hasPrefix("REPLACE") || trimmed.hasPrefix("$(") {
            onRegistration?(false, "DJISDKAppKey missing in Info.plist (register com.overwatch.dronebridge at developer.dji.com)")
            return
        }
        DJISDKManager.registerApp(with: self)
    }

    // MARK: Convenience accessors (any thread)

    static var product: DJIBaseProduct? { DJISDKManager.product() }
    static var aircraft: DJIAircraft? { DJISDKManager.product() as? DJIAircraft }
    static var flightController: DJIFlightController? { aircraft?.flightController }
    static var modelName: String? { DJISDKManager.product()?.model }
    static var cameraDisplayName: String? { aircraft?.camera?.displayName }
    static var sdkVersion: String { DJISDKManager.sdkVersion() }

    /// The bridge targets the DJI Mini 2 (MSDK 4.16+); other v4 aircraft probably work but are untested.
    static func isExpectedModel(_ model: String?) -> Bool {
        guard let m = model?.lowercased() else { return false }
        return m.contains("mini 2") || m.contains("mini2")
    }

    // MARK: DJISDKManagerDelegate

    func appRegisteredWithError(_ error: Error?) {
        if let error = error {
            registered = false
            onRegistration?(false, "DJI registration failed: \(error.localizedDescription) (needs internet on first launch and a valid App Key)")
            return
        }
        registered = true
        let started = DJISDKManager.startConnectionToProduct()
        onRegistration?(true, started ? "DJI SDK registered, connecting to product…" : "DJI SDK registered, but startConnectionToProduct failed")
    }

    func didUpdateDatabaseDownloadProgress(_ progress: Progress) {
        guard progress.totalUnitCount > 0 else { return }
        onDatabaseProgress?(Double(progress.completedUnitCount) / Double(progress.totalUnitCount))
    }

    func productConnected(_ product: DJIBaseProduct?) {
        onProductChange?()
    }

    func productDisconnected() {
        onProductChange?()
    }

    func componentConnected(withKey key: String?, andIndex index: Int) {
        onComponentChange?()
    }

    func componentDisconnected(withKey key: String?, andIndex index: Int) {
        onComponentChange?()
    }
}
