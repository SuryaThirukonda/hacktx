// Background adapter for the EXISTING DJI sample FPV screen. All operator controls stay on the website.
// Installed by integrate_sdk_sample.rb; preserves the sample UI, registration and preview ownership.
import UIKit
import DJISDK

final class DJIVideoRelay: NSObject {
    static let shared = DJIVideoRelay()
    private let bridge = BridgeController()
    private var poll: Timer?
    private var backgroundObserver: NSObjectProtocol?
    private var sending = false
    private var count = 0
    private var fpsSince = Date()
    private(set) var isStreaming = false
    var targetPort = 8765
    var onStatusChanged: ((String, UIColor) -> Void)?
    var onFPSUpdated: ((Double) -> Void)?
    var targetIP: String {
        get { UserDefaults.standard.string(forKey: "AerialCopilot_MacIP") ?? "" }
        set { UserDefaults.standard.set(newValue, forKey: "AerialCopilot_MacIP") }
    }

    override init() {
        super.init()
        bridge.onUI = { [weak self] snapshot in
            guard let self = self, self.isStreaming else { return }
            if snapshot.socket.state == .connected {
                self.onStatusChanged?(snapshot.vstick.inhibited ? "Streaming · website re-arm required" : "Streaming · controls ready", .systemGreen)
            } else { self.onStatusChanged?("Connecting to Eye in the Sky…", .systemYellow) }
        }
        bridge.start(externalSDK: true)
        poll = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            if self?.isStreaming == true { self?.bridge.refreshExternalSDK() }
        }
        backgroundObserver = NotificationCenter.default.addObserver(forName: UIApplication.willResignActiveNotification,
            object: nil, queue: .main) { [weak self] _ in self?.bridge.appWillBackground() }
    }

    func startStreaming(ip: String, port: Int = 8765) {
        guard !isStreaming else { return }
        targetIP = ip; targetPort = port
        bridge.refreshExternalSDK()
        let host = ip.contains(":") ? ip : "\(ip):\(port)"
        guard bridge.connect(host: host, token: UserDefaults.standard.string(forKey: "AerialCopilot_OverwatchToken") ?? "") else {
            onStatusChanged?("Invalid Mac address", .systemRed); return
        }
        isStreaming = true; sending = false; count = 0; fpsSince = Date()
        sendNextFrame()
    }

    func stopStreaming() {
        isStreaming = false; sending = false
        bridge.operatorDisable(); bridge.disconnect()
        onStatusChanged?("Disconnected · pilot control", .systemGray)
    }

    private func sendNextFrame() {
        guard isStreaming, !sending else { return }
        sending = true
        DJIVideoPreviewer.instance()?.snapshotPreview { [weak self] image in
            guard let self = self else { return }
            DispatchQueue.global(qos: .userInitiated).async {
                let jpeg = image?.jpegData(compressionQuality: 0.5)
                DispatchQueue.main.async {
                    self.sending = false
                    guard self.isStreaming else { return }
                    if let jpeg = jpeg {
                        self.bridge.sendExternalJPEG(jpeg)
                        self.count += 1
                        let elapsed = Date().timeIntervalSince(self.fpsSince)
                        if elapsed >= 1 { self.onFPSUpdated?(Double(self.count) / elapsed); self.count = 0; self.fpsSince = Date() }
                    }
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.06) { [weak self] in self?.sendNextFrame() }
                }
            }
        }
    }

    func sendSingleFrame(ip: String, port: Int = 8765, completion: @escaping (Bool, String) -> Void) {
        let host = ip.contains(":") ? ip : "\(ip):\(port)"
        guard let url = URL(string: "http://\(host)/frame") else { completion(false, "Invalid Mac address"); return }
        DJIVideoPreviewer.instance()?.snapshotPreview { image in
            guard let jpeg = image?.jpegData(compressionQuality: 0.5) else { completion(false, "No FPV image available"); return }
            var request = URLRequest(url: url); request.httpMethod = "POST"; request.timeoutInterval = 8
            request.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
            if let token = UserDefaults.standard.string(forKey: "AerialCopilot_OverwatchToken"), !token.isEmpty {
                request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            }
            URLSession.shared.uploadTask(with: request, from: jpeg) { data, response, error in
                let code = (response as? HTTPURLResponse)?.statusCode ?? 0
                let result = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
                let ok = (200..<300).contains(code) && error == nil
                let message = error?.localizedDescription ?? (result?[ok ? "note" : "detail"] as? String)
                    ?? (ok ? "Test OK: frame received" : "HTTP \(code)")
                DispatchQueue.main.async { completion(ok, message) }
            }.resume()
        }
    }
}
