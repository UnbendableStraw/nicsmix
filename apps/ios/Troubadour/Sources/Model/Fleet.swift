import Foundation
import Network
import Combine

/// Finds the cells, three ways, cheapest first:
///  1. seeds we already know (last session's agents, typed addresses, the USB link 10.86.0.1) — a 2 s `whoami`,
///     then `/api/discover` on the first that answers enumerates the whole LAN;
///  2. Bonjour: celldirector advertises `_syng._tcp` on every cell (stock firmware included). Resolving is done by
///     opening a TCP connection to the service and reading the peer address — forced to IPv4, because iOS prefers
///     the cells' IPv6 link-local addresses and the agent is reached by IPv4;
///  3. a sweep of the phone's own subnet for port 8080 (what the agent itself does), for networks that eat mDNS.
@MainActor
final class Fleet: ObservableObject {
    static let shared = Fleet()
    @Published var rooms: [Room] = []
    @Published var seeds: [String] = []          // agent addresses we know
    @Published var scanning = false
    @Published var lastError: String?
    @Published var localNetworkDenied = false
    private var saved: [String] {
        get { (UserDefaults.standard.array(forKey: "seeds") as? [String]) ?? [] }
        set { UserDefaults.standard.set(newValue, forKey: "seeds") }
    }

    private var browser: NWBrowser?
    private var resolvers: [NWConnection] = []
    private var timer: Timer?
    private var sweptAt = Date.distantPast

    init() { seeds = saved }

    func start() {
        if browser == nil { browse() }
        Task { await refresh() }
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in Task { await self?.refresh() } }
    }

    func addSeed(_ ip: String) {
        let ip = ip.trimmingCharacters(in: .whitespaces)
        guard !ip.isEmpty, !seeds.contains(ip) else { return }
        seeds.insert(ip, at: 0); saved = Array(seeds.prefix(12))
        Task { await refresh(force: true) }
    }

    // MARK: Bonjour

    private func browse() {
        let b = NWBrowser(for: .bonjour(type: "_syng._tcp", domain: nil), using: .tcp)
        b.stateUpdateHandler = { [weak self] st in
            // -65570 = kDNSServiceErr_PolicyDenied: Local Network access is off for this app
            if case .waiting(let e) = st, case .dns(let code) = e, code == -65570 {
                Task { @MainActor in self?.localNetworkDenied = true }
            }
            if case .ready = st { Task { @MainActor in self?.localNetworkDenied = false } }
        }
        b.browseResultsChangedHandler = { [weak self] results, _ in
            Task { @MainActor in for r in results { self?.resolve(r.endpoint) } }
        }
        b.start(queue: .main); browser = b
    }
    private func resolve(_ endpoint: NWEndpoint) {
        let params = NWParameters.tcp
        if let ip = params.defaultProtocolStack.internetProtocol as? NWProtocolIP.Options { ip.version = .v4 }
        let c = NWConnection(to: endpoint, using: params)
        c.stateUpdateHandler = { [weak self, weak c] st in
            guard let c = c else { return }
            switch st {
            case .ready:
                if let path = c.currentPath, case let .hostPort(host, _)? = path.remoteEndpoint {
                    var ip = "\(host)"; if let i = ip.firstIndex(of: "%") { ip = String(ip[..<i]) }
                    if ip.contains(".") { Task { @MainActor in self?.addSeed(ip) } }
                }
                c.cancel()
            case .failed, .cancelled: c.cancel()
            default: break
            }
        }
        c.start(queue: .main); resolvers.append(c)
        if resolvers.count > 32 { resolvers.removeFirst(16) }
    }

    // MARK: refresh

    func refresh(force: Bool = false) async {
        guard !scanning else { return }
        scanning = true; defer { scanning = false }
        if await discover(via: seeds + ["10.86.0.1"]) { return }
        // nothing we knew answered: sweep the phone's subnet (at most every 30 s unless asked)
        guard force || Date().timeIntervalSince(sweptAt) > 30 else { return }
        sweptAt = Date()
        let (found, net) = await sweep()
        if !found.isEmpty {
            for ip in found where !seeds.contains(ip) { seeds.append(ip) }
            saved = Array(seeds.prefix(12))
            if await discover(via: found) { return }
        }
        lastError = localNetworkDenied
            ? "Local Network access is off for Troubadour — turn it on in Settings › Privacy & Security › Local Network."
            : "No Troubadour speakers answered\(net.map { " on \($0)" } ?? ""). Is the phone on the same Wi-Fi as the speakers?"
    }

    /// Try each address: a quick whoami (2 s) so a dead address doesn't hold things up, then the full LAN discovery.
    private func discover(via addresses: [String]) async -> Bool {
        for ip in addresses {
            do {
                _ = try await Agent(ip: ip).get("/api/whoami", timeout: 2)
                let d = try await Agent(ip: ip).get("/api/discover", timeout: 25)
                rooms = ((d["rooms"] as? [[String: Any]]) ?? []).compactMap(Room.init).sorted { $0.name < $1.name }
                lastError = nil
                for r in rooms { for c in r.cells where c.agent != nil && !seeds.contains(c.ip) && !c.ip.hasPrefix("10.86.") { seeds.append(c.ip) } }
                saved = Array(seeds.prefix(12))
                return true
            } catch { lastError = "\(ip): \(error.localizedDescription)" }
        }
        return false
    }

    // MARK: subnet sweep

    /// IPv4 address + netmask of the Wi-Fi / wired interface (en0 preferred; USB-Ethernet adapters are enN too).
    nonisolated private func localIPv4() -> (addr: UInt32, mask: UInt32, text: String)? {
        var ifaddr: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&ifaddr) == 0, let first = ifaddr else { return nil }
        defer { freeifaddrs(ifaddr) }
        var best: (String, UInt32, UInt32, String)?
        for p in sequence(first: first, next: { $0.pointee.ifa_next }) {
            let ifa = p.pointee
            guard let sa = ifa.ifa_addr, sa.pointee.sa_family == UInt8(AF_INET), let nm = ifa.ifa_netmask else { continue }
            let name = String(cString: ifa.ifa_name)
            guard name.hasPrefix("en") || name.hasPrefix("bridge") else { continue }
            var a = sockaddr_in(); memcpy(&a, sa, MemoryLayout<sockaddr_in>.size)
            var m = sockaddr_in(); memcpy(&m, nm, MemoryLayout<sockaddr_in>.size)
            let addr = UInt32(bigEndian: a.sin_addr.s_addr), mask = UInt32(bigEndian: m.sin_addr.s_addr)
            if addr >> 16 == 0xA9FE { continue }   // 169.254 self-assigned
            let text = String(cString: inet_ntoa(a.sin_addr)) + "/" + String(mask.nonzeroBitCount)
            if best == nil || name == "en0" { best = (name, addr, mask, text) }
        }
        return best.map { ($0.1, $0.2, $0.3) }
    }

    /// Every host on the local subnet (capped to the /22 around us) that answers `/api/whoami` on 8080.
    private func sweep() async -> ([String], String?) {
        guard let me = localIPv4() else { return ([], nil) }
        var mask = me.mask
        if mask.nonzeroBitCount < 22 { mask = 0xFFFFFC00 }
        let net = me.addr & mask, count = Int(~mask) - 1
        let hosts: [String] = (1...max(count, 1)).map { i in
            let h = net + UInt32(i)
            return "\((h >> 24) & 255).\((h >> 16) & 255).\((h >> 8) & 255).\(h & 255)"
        }.filter { $0 != me.text.split(separator: "/").first.map(String.init) }
        let found: [String] = await withTaskGroup(of: String?.self) { group in
            var out: [String] = []; var it = hosts.makeIterator(); var running = 0
            func spawn(_ ip: String) {
                group.addTask {
                    let cfg = URLSessionConfiguration.ephemeral; cfg.timeoutIntervalForRequest = 1.5; cfg.timeoutIntervalForResource = 1.5
                    let s = URLSession(configuration: cfg)
                    defer { s.invalidateAndCancel() }
                    guard let r = try? await s.data(from: Agent(ip: ip).base.appendingPathComponent("/api/whoami")),
                          let o = (try? JSONSerialization.jsonObject(with: r.0)) as? [String: Any], o["agent"] != nil else { return nil }
                    return ip
                }
            }
            while running < 48, let ip = it.next() { spawn(ip); running += 1 }
            for await r in group {
                if let ip = r { out.append(ip) }
                if let ip = it.next() { spawn(ip) }
            }
            return out
        }
        return (found, me.text)
    }
}
