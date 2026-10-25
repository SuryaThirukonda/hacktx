import SwiftUI

@main
struct OverwatchResponderApp: App {
    @StateObject private var model = ResponderModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
        }
    }
}
