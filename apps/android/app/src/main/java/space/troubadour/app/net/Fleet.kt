package space.troubadour.app.net

import android.content.Context
import android.net.ConnectivityManager
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import java.net.Inet4Address

/**
 * Finds the cells, cheapest first: (1) seeds we know (last session's agents, typed addresses, the USB link
 * 10.86.0.1) — a 2 s whoami then `/api/discover` on the first that answers enumerates the LAN; (2) NSD for
 * `_syng._tcp` (celldirector advertises it on every cell, stock or not) — IPv4 only, the agent is reached by IPv4;
 * (3) a sweep of the phone's own subnet for port 8080 (what the agent itself does), for networks that eat mDNS.
 */
object Fleet {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val _rooms = MutableStateFlow<List<Room>>(emptyList()); val rooms: StateFlow<List<Room>> = _rooms
    private val _status = MutableStateFlow<String?>(null); val status: StateFlow<String?> = _status
    private val _scanning = MutableStateFlow(false); val scanning: StateFlow<Boolean> = _scanning
    private var seeds = mutableListOf<String>()
    private var prefs: android.content.SharedPreferences? = null
    private var app: Context? = null
    private var nsd: NsdManager? = null
    private var lock: WifiManager.MulticastLock? = null
    private val refreshing = Mutex()
    private var sweptAt = 0L
    var lastRoomAgent: String?  // for the tile / shortcuts
        get() = prefs?.getString("lastAgent", null)
        set(v) { prefs?.edit()?.putString("lastAgent", v)?.apply() }

    fun start(ctx: Context) {
        if (prefs == null) {
            app = ctx.applicationContext
            prefs = ctx.getSharedPreferences("fleet", Context.MODE_PRIVATE)
            seeds = (prefs!!.getString("seeds", "") ?: "").split(",").filter { it.isNotBlank() }.toMutableList()
            val wifi = ctx.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
            lock = wifi.createMulticastLock("troubadour").apply { setReferenceCounted(false); acquire() }
            browse(ctx)
            scope.launch { while (true) { refresh(); delay(15_000) } }
        }
    }

    fun addSeed(ip: String, force: Boolean = true) {
        val v = ip.trim(); if (v.isEmpty() || seeds.contains(v)) return
        seeds.add(0, v); save(); scope.launch { refresh(force) }
    }
    private fun save() { prefs?.edit()?.putString("seeds", seeds.take(12).joinToString(","))?.apply() }

    private fun browse(ctx: Context) {
        val m = ctx.applicationContext.getSystemService(Context.NSD_SERVICE) as NsdManager; nsd = m
        m.discoverServices("_syng._tcp", NsdManager.PROTOCOL_DNS_SD, object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(t: String) {}
            override fun onDiscoveryStopped(t: String) {}
            override fun onStartDiscoveryFailed(t: String, e: Int) { _status.value = "mDNS browse failed ($e)" }
            override fun onStopDiscoveryFailed(t: String, e: Int) {}
            override fun onServiceLost(s: NsdServiceInfo) {}
            override fun onServiceFound(s: NsdServiceInfo) {
                @Suppress("DEPRECATION")
                m.resolveService(s, object : NsdManager.ResolveListener {
                    override fun onResolveFailed(i: NsdServiceInfo, e: Int) {}
                    override fun onServiceResolved(i: NsdServiceInfo) {
                        // the cells have IPv6 link-local addresses too; only an IPv4 is any use to us
                        val addrs = if (Build.VERSION.SDK_INT >= 34) i.hostAddresses else listOfNotNull(i.host)
                        val ip = addrs.filterIsInstance<Inet4Address>().firstOrNull()?.hostAddress ?: return
                        scope.launch { addSeed(ip) }
                    }
                })
            }
        })
    }

    suspend fun refresh(force: Boolean = false) {
        if (!refreshing.tryLock()) return
        _scanning.value = true
        try {
            if (discover(seeds + listOf("10.86.0.1"))) return
            // nothing we knew answered: sweep the phone's subnet (at most every 30 s unless asked)
            val now = System.currentTimeMillis()
            if (!force && now - sweptAt < 30_000) return
            sweptAt = now
            val (found, net) = sweep()
            if (found.isNotEmpty()) {
                for (ip in found) if (!seeds.contains(ip)) seeds.add(ip)
                save()
                if (discover(found)) return
            }
            _status.value = "No Troubadour speakers answered${if (net != null) " on $net" else ""}. Is the phone on the same Wi-Fi as the speakers?"
        } finally { _scanning.value = false; refreshing.unlock() }
    }

    /** Each address: a quick whoami (2 s) so a dead address doesn't hold things up, then the full LAN discovery. */
    private suspend fun discover(addresses: List<String>): Boolean {
        for (ip in addresses) {
            try {
                Agent(ip).get("/api/whoami", 2, connectSec = 2)
                val d = Agent(ip).get("/api/discover", 25)
                val arr = d.optJSONArray("rooms")
                val rooms = (0 until (arr?.length() ?: 0)).mapNotNull { Room.from(arr!!.getJSONObject(it)) }.sortedBy { it.name }
                _rooms.value = rooms; _status.value = null
                for (r in rooms) for (c in r.cells) if (c.agent != null && !seeds.contains(c.ip) && !c.ip.startsWith("10.86.")) seeds.add(c.ip)
                save(); return true
            } catch (e: Exception) { _status.value = "$ip: ${e.message}" }
        }
        return false
    }

    /** The active network's IPv4 address and prefix (Wi-Fi, or a USB-Ethernet adapter). */
    private fun localIPv4(): Pair<Inet4Address, Int>? {
        val cm = app?.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager ?: return null
        val nets = listOfNotNull(cm.activeNetwork) + cm.allNetworks.toList()
        for (n in nets) {
            val lp = cm.getLinkProperties(n) ?: continue
            val la = lp.linkAddresses.firstOrNull { it.address is Inet4Address && !it.address.isLinkLocalAddress && !it.address.isLoopbackAddress } ?: continue
            return (la.address as Inet4Address) to la.prefixLength
        }
        return null
    }

    /** Every host on the local subnet (capped to the /22 around us) that answers `/api/whoami` on 8080. */
    private suspend fun sweep(): Pair<List<String>, String?> = withContext(Dispatchers.IO) {
        val (addr, prefix0) = localIPv4() ?: return@withContext emptyList<String>() to null
        val prefix = maxOf(prefix0, 22)
        val b = addr.address; val me = ((b[0].toInt() and 255) shl 24) or ((b[1].toInt() and 255) shl 16) or ((b[2].toInt() and 255) shl 8) or (b[3].toInt() and 255)
        val mask = (-1 shl (32 - prefix)); val net = me and mask; val count = mask.inv() - 1
        val hosts = (1..maxOf(count, 1)).map { net + it }.filter { it != me }.map { "${(it ushr 24) and 255}.${(it ushr 16) and 255}.${(it ushr 8) and 255}.${it and 255}" }
        val sem = Semaphore(48)
        val found = coroutineScope {
            hosts.map { ip -> async { sem.withPermit {
                try { val o = Agent(ip).get("/api/whoami", 2, connectSec = 1); if (!o.isNull("agent")) ip else null } catch (_: Exception) { null }
            } } }.awaitAll().filterNotNull()
        }
        found to "${addr.hostAddress}/$prefix0"
    }
}
