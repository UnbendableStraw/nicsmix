import SwiftUI
import UIKit

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

/// The app is a native client for the agent that runs on every Troubadour cell (port 8080); on iPad it is laid
/// out for the iPad (PadViews.swift), not the phone layout stretched.
/// Bonjour discovery (no IPs), the sound field on Metal, every setup page native, Shortcuts, widgets, firmware
/// upload from Files; the cell's own web page stays one tap away for anything not surfaced here.
struct RootView: View {
    @EnvironmentObject var fleet: Fleet
    @Environment(\.horizontalSizeClass) private var hsize
    @State private var path: [Room] = []
    @State private var padSelection: Room.ID?
    @State private var openLastRoom = false
    var body: some View {
        Group {
            // a full-width iPad window gets the iPad app; iPhone (even a Max in landscape), Split View and Slide
            // Over get the phone layout
            if hsize == .regular && UIDevice.current.userInterfaceIdiom == .pad {
                PadRootView(selection: $padSelection)
            } else {
                NavigationStack(path: $path) { RoomsView() }
            }
        }
        .task { fleet.start() }
        // a widget tap (troubadour://room) opens the room the widget shows, once discovery has it
        .onOpenURL { _ in openLastRoom = true; pushLastRoom() }
        .onChange(of: fleet.rooms) { _ in pushLastRoom() }
    }
    private func pushLastRoom() {
        guard openLastRoom, let ip = Shared.agentIP,
              let r = fleet.rooms.first(where: { $0.answers(ip) }) else { return }
        openLastRoom = false; path = [r]; padSelection = r.id
    }
}
