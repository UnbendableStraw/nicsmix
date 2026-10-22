/*
 * troubadour-busguard-trace — DIAGNOSTIC shim (does not fix anything; read-only logging).
 *
 * §1.3 of the 2026-10-20 review: busguard's premise (a connection-level read-cursor race pumped from
 * two threads) does not match the errors seen — ENXIO on enter_container / EBUSY on exit_container are
 * *message*-local, which points instead at sdbus-c++ 1.x Variant copies sharing one sd_bus_message that
 * two threads read at once (property cache filled on the loop thread, read by _GeneralAudioThread at a
 * format change). This shim settles it on a live cell with zero behavioural change:
 *
 *   It interposes the three message-cursor calls and logs tid + message pointer + result for each. If the
 *   SAME message pointer shows up under TWO different tids (especially around an ENXIO/EBUSY), the Variant-
 *   copy theory is confirmed and the fix belongs around sdbus-c++'s Variant, not the connection. If every
 *   message pointer is only ever touched by one tid, the theory is wrong.
 *
 * Use INSTEAD of busguard for the test (so busguard's serialization doesn't mask the race):
 *   LD_PRELOAD=/usr/lib/troubadour/libtroubadour_busguard_trace.so
 * then reproduce a format change (start an Atmos track, switch source) and read the journal. Set
 * TROUBADOUR_BGTRACE_ALL=1 to log every call; default logs only non-zero (error) returns plus a per-tid
 * first-touch line, to keep the journal readable.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/syscall.h>

__asm__(".symver dlsym,dlsym@GLIBC_2.17");
__asm__(".symver write,write@GLIBC_2.17");

static int g_all = 0;

__attribute__((constructor))
static void bgt_init(void)
{
    const char* e = getenv("TROUBADOUR_BGTRACE_ALL");
    g_all = (e && *e && *e != '0');
    static const char m[] = "[troubadour-busguard-trace] message-cursor tracing active (read-only)\n";
    (void)write(2, m, sizeof(m) - 1);
}

static long tid(void) { return syscall(SYS_gettid); }

static void emit(const char* fn, const void* msg, int ret, char type, const char* contents)
{
    char buf[200];
    int n = snprintf(buf, sizeof buf,
                     "[bgtrace] tid=%ld %s msg=%p type=%c contents=%s ret=%d\n",
                     tid(), fn, msg, type ? type : '-', contents ? contents : "-", ret);
    if (n > 0) (void)write(2, buf, (size_t)n);
}

typedef int (*fn_enter)(void*, char, const char*);
typedef int (*fn_exit)(void*);
typedef int (*fn_rewind)(void*, int);

static fn_enter  real_enter;
static fn_exit   real_exit;
static fn_rewind real_rewind;

#define RESOLVE(v, n) do { if (!(v)) (v) = (void*)dlsym(RTLD_NEXT, (n)); } while (0)

__attribute__((visibility("default")))
int sd_bus_message_enter_container(void* m, char type, const char* contents)
{
    RESOLVE(real_enter, "sd_bus_message_enter_container");
    int r = real_enter(m, type, contents);
    if (g_all || r < 0) emit("enter", m, r, type, contents);
    return r;
}

__attribute__((visibility("default")))
int sd_bus_message_exit_container(void* m)
{
    RESOLVE(real_exit, "sd_bus_message_exit_container");
    int r = real_exit(m);
    if (g_all || r < 0) emit("exit", m, r, 0, 0);
    return r;
}

__attribute__((visibility("default")))
int sd_bus_message_rewind(void* m, int complete)
{
    RESOLVE(real_rewind, "sd_bus_message_rewind");
    int r = real_rewind(m, complete);
    if (g_all || r < 0) emit("rewind", m, r, 0, 0);
    return r;
}
