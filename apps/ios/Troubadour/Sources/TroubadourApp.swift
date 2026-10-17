import SwiftUI

@main
struct TroubadourApp: App {
    @StateObject private var fleet = Fleet.shared
    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(fleet)
                .preferredColorScheme(.dark)
                .tint(.white)
        }
    }
}

/// The app is a thin native shell around the agent that runs on every Troubadour cell (port 8080).
/// Native where the phone beats a browser tab: Bonjour discovery (no IPs), a now-playing card with haptics,
/// Shortcuts, firmware upload from Files. The sound field and the setup page are the agent's own pages,
/// embedded — one source of truth, already in the app's look.
struct RootView: View {
    @EnvironmentObject var fleet: Fleet
    @State private var path: [Room] = []
    @State private var openLastRoom = false
    var body: some View {
        NavigationStack(path: $path) {
            RoomsView()
        }
        .task { fleet.start() }
        // a widget tap (troubadour://room) opens the room the widget shows, once discovery has it
        .onOpenURL { _ in openLastRoom = true; pushLastRoom() }
        .onChange(of: fleet.rooms) { _ in pushLastRoom() }
    }
    private func pushLastRoom() {
        guard openLastRoom, let ip = Shared.agentIP,
              let r = fleet.rooms.first(where: { $0.agent?.ip == ip }) else { return }
        openLastRoom = false; path = [r]
    }
}
