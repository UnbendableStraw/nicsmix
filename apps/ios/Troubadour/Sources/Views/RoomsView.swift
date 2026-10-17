import SwiftUI

struct RoomsView: View {
    @EnvironmentObject var fleet: Fleet
    @State private var manualIP = ""
    @State private var showManual = false

    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                if fleet.rooms.isEmpty {
                    Card {
                        HStack {
                            if fleet.scanning { ProgressView().tint(.white) }
                            Text(fleet.scanning ? "Looking for your Cells…" : (fleet.lastError ?? "No Cells found yet. Same Wi-Fi as the speakers? Or plug one in over USB-C and add 10.86.0.1."))
                                .font(.subheadline).foregroundStyle(Theme.muted)
                        }
                    }
                }
                ForEach(fleet.rooms) { room in
                    NavigationLink(value: room) {
                        Card(nil) {
                            HStack(alignment: .firstTextBaseline) {
                                Text(room.name).font(.title3.weight(.bold))
                                Text("\(room.cells.count) speaker\(room.cells.count == 1 ? "" : "s")").font(.subheadline).foregroundStyle(Theme.muted)
                                Spacer()
                                Image(systemName: "chevron.right").foregroundStyle(Theme.muted)
                            }
                            HStack(spacing: -8) {
                                ForEach(room.cells.prefix(8)) { c in Puck(size: 34, ring: c.role == "primary" ? .white : nil) }
                            }
                            Text(room.cells.map(\.title).joined(separator: " · ")).font(.caption).foregroundStyle(Theme.muted).lineLimit(2)
                        }
                    }
                    .buttonStyle(.plain)
                }
                if showManual {
                    Card("Add a speaker by address") {
                        HStack {
                            TextField("192.168.1.23 or 10.86.0.1", text: $manualIP).keyboardType(.decimalPad).textFieldStyle(.plain)
                                .padding(10).background(Theme.surface2, in: RoundedRectangle(cornerRadius: 10))
                            Button("Add") { fleet.addSeed(manualIP); manualIP = ""; showManual = false }.buttonStyle(Capsule_(filled: true)).frame(width: 90)
                        }
                    }
                }
            }
            .padding(16)
        }
        .background(Theme.bg.ignoresSafeArea())
        .navigationTitle("Rooms")
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                Menu {
                    Button { Task { await fleet.refresh(force: true) } } label: { Label("Rescan", systemImage: "arrow.clockwise") }
                    Button { showManual.toggle() } label: { Label("Add by address…", systemImage: "plus") }
                } label: { Image(systemName: "ellipsis.circle") }
            }
        }
        .navigationDestination(for: Room.self) { room in RoomView(room: room) }
        .refreshable { await fleet.refresh() }
    }
}
