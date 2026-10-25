import SwiftUI

@main
struct OverwatchDroneBridgeApp: App {
    @StateObject private var model = BridgeModel()

    var body: some Scene {
        WindowGroup {
            BridgeContentView()
                .environmentObject(model)
                .onAppear { model.start() }
        }
    }
}
