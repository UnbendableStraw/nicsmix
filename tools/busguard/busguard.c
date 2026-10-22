/*
 * troubadour-busguard — sd-bus connection serialization shim for Syng Cell airplay2source
 *
 * Root cause (reverse-engineered; see docs/SYNG-AIRPLAY-FIXES.md section 1):
 *   airplay2source services ONE shared sd_bus connection from 11 threads. sd_bus is not
 *   thread-safe. When the event-loop thread is inside sd_bus_process() deserializing an
 *   incoming reply/signal, while another thread drives the SAME connection via
 *   sd_bus_call()/sd_bus_send(), the connection read cursor is corrupted. sdbus-c++ 1.1.0
 *   then throws sdbus::Error "Failed to enter/exit a variant" (ENXIO/EBUSY) from a handler
 *   with no catch; the exception unwinds through Apple's C AirPlay receiver core (no
 *   handlers) and hits std::terminate -> abort (status=6/ABRT).
 *
 * Fix: serialize every connection-level sd_bus entry point behind ONE process-wide
 *   recursive mutex, held for the whole duration of each call. Two threads can no longer
 *   pump the same connection at once, so the cursor is never corrupted and the throw never
 *   happens. Message-local reads (enter/exit_container on a singly-owned message) are left
 *   untouched. Fully reversible: drop the LD_PRELOAD and behavior is byte-for-byte stock.
 *
 * Interposes exactly the connection-pumping symbols libsdbus-c++.so.1.1.0 imports, at
 * their referenced version LIBSYSTEMD_221. Real implementations are reached via RTLD_NEXT.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <pthread.h>
#include <stdint.h>
#include <unistd.h>
#include <string.h>

/* Pin libc/pthread/dl refs to the aarch64 baseline GLIBC_2.17 (what the cell's glibc 2.30
   exports) so the toolchain here (glibc 2.35) does not emit @GLIBC_2.34 for merged
   pthread/dl symbols, which the cell lacks. */
__asm__(".symver pthread_mutex_lock,pthread_mutex_lock@GLIBC_2.17");
__asm__(".symver pthread_mutex_unlock,pthread_mutex_unlock@GLIBC_2.17");
__asm__(".symver pthread_mutex_init,pthread_mutex_init@GLIBC_2.17");
__asm__(".symver pthread_mutexattr_init,pthread_mutexattr_init@GLIBC_2.17");
__asm__(".symver pthread_mutexattr_settype,pthread_mutexattr_settype@GLIBC_2.17");
__asm__(".symver pthread_mutexattr_destroy,pthread_mutexattr_destroy@GLIBC_2.17");
__asm__(".symver dlsym,dlsym@GLIBC_2.17");
__asm__(".symver write,write@GLIBC_2.17");

static pthread_mutex_t g_lock;

__attribute__((constructor))
static void bg_init(void)
{
    pthread_mutexattr_t a;
    pthread_mutexattr_init(&a);
    pthread_mutexattr_settype(&a, PTHREAD_MUTEX_RECURSIVE);
    pthread_mutex_init(&g_lock, &a);
    pthread_mutexattr_destroy(&a);
    static const char m[] = "[troubadour-busguard] sd-bus connection serialization active\n";
    (void)write(2, m, sizeof(m) - 1);
}

typedef int (*fn_process)(void *, void **);
typedef int (*fn_call)(void *, void *, uint64_t, void *, void **);
typedef int (*fn_call_async)(void *, void **, void *, void *, void *, uint64_t);
typedef int (*fn_send)(void *, void *, uint64_t *);
typedef int (*fn_flush)(void *);

static fn_process    real_process;
static fn_call       real_call;
static fn_call_async real_call_async;
static fn_send       real_send;
static fn_flush      real_flush;

#define RESOLVE(v, n) do { if (!(v)) (v) = (void *)dlsym(RTLD_NEXT, (n)); } while (0)

__attribute__((visibility("default"))) int sd_bus_process(void *bus, void **ret)
{
    RESOLVE(real_process, "sd_bus_process");
    pthread_mutex_lock(&g_lock);
    int r = real_process(bus, ret);
    pthread_mutex_unlock(&g_lock);
    return r;
}

__attribute__((visibility("default"))) int sd_bus_call(void *bus, void *m, uint64_t usec, void *err, void **reply)
{
    RESOLVE(real_call, "sd_bus_call");
    pthread_mutex_lock(&g_lock);
    int r = real_call(bus, m, usec, err, reply);
    pthread_mutex_unlock(&g_lock);
    return r;
}

__attribute__((visibility("default"))) int sd_bus_call_async(void *bus, void **slot, void *m, void *cb, void *ud, uint64_t usec)
{
    RESOLVE(real_call_async, "sd_bus_call_async");
    pthread_mutex_lock(&g_lock);
    int r = real_call_async(bus, slot, m, cb, ud, usec);
    pthread_mutex_unlock(&g_lock);
    return r;
}

__attribute__((visibility("default"))) int sd_bus_send(void *bus, void *m, uint64_t *cookie)
{
    RESOLVE(real_send, "sd_bus_send");
    pthread_mutex_lock(&g_lock);
    int r = real_send(bus, m, cookie);
    pthread_mutex_unlock(&g_lock);
    return r;
}

__attribute__((visibility("default"))) int sd_bus_flush(void *bus)
{
    RESOLVE(real_flush, "sd_bus_flush");
    pthread_mutex_lock(&g_lock);
    int r = real_flush(bus);
    pthread_mutex_unlock(&g_lock);
    return r;
}
