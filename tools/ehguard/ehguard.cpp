/* troubadour-ehguard: LD_PRELOAD entry. Installs an exception guard around the two C++ AirPlay
 * delegates on the format-change crash path (initializeFormat @0x2546e0, stream-property/badge
 * updater @0x252f80), so a stray sdbus::Error can never unwind into Apple's C receiver core and
 * abort the process. Defense-in-depth behind busguard (which removes the race that throws).
 * No on-disk change to airplay2source; drop the LD_PRELOAD to revert. */
#define _GNU_SOURCE
#include <cstdint>
#include <dlfcn.h>
#include "ehguard_symver.h"

extern "C" {
    void* ehguard_install(void* func, void* hook_entry);
    void  ehguard_logs(const char* s);
    void  troub_wrap_init_env(void);
    extern __attribute__((visibility("hidden"))) void* g_tramp_init;
    extern __attribute__((visibility("hidden"))) void* g_tramp_badge;
    void  hook_entry_init(void);
    void  hook_entry_badge(void);
}

// File vaddrs in airplay2source (sha256 dbcfb3cf…), verified by RE.
static const uintptr_t VA_BADGE_ANCHOR = 0x218b80; // AirPlayReceiverSessionUpdateBadgingType (exported)
static const uintptr_t VA_INIT_FORMAT  = 0x2546e0; // syng::AirPlay2Source::initializeFormat
static const uintptr_t VA_BADGE_UPD    = 0x252f80; // stream-property/badge updater

__attribute__((constructor))
static void ehguard_init(void)
{
    troub_wrap_init_env();   // §1.4: read TROUBADOUR_EHGUARD_ONCATCH once
    void* anchor = dlsym(RTLD_DEFAULT, "AirPlayReceiverSessionUpdateBadgingType");
    if (!anchor) { ehguard_logs("[troubadour-ehguard] anchor symbol not found; not hooking\n"); return; }
    uintptr_t bias = (uintptr_t)anchor - VA_BADGE_ANCHOR;

    g_tramp_init  = ehguard_install((void*)(bias + VA_INIT_FORMAT), (void*)hook_entry_init);
    g_tramp_badge = ehguard_install((void*)(bias + VA_BADGE_UPD),   (void*)hook_entry_badge);

    if (g_tramp_init && g_tramp_badge)
        ehguard_logs("[troubadour-ehguard] delegate exception guard active (initializeFormat + badge)\n");
    else
        ehguard_logs("[troubadour-ehguard] WARNING: one or more hooks failed to install\n");
}
