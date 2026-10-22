/* troubadour-ehguard: C/C++ half — the hook installer, the C++ try/catch, globals, logging.
 * Shared by the LD_PRELOAD build (ehguard.cpp constructor) and the unit test (ehguard_test.cpp). */
#define _GNU_SOURCE
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <cstdio>
#include <unistd.h>
#include <sys/mman.h>
#include <atomic>
#include "ehguard_symver.h"

extern "C" {
    // asm
    uint64_t troub_invoke(void* saved, void* tramp);
    // per-target trampoline pointers, set by install_hook()
    __attribute__((visibility("hidden"))) void* g_tramp_init  = nullptr;
    __attribute__((visibility("hidden"))) void* g_tramp_badge = nullptr;
    __attribute__((visibility("hidden"))) void* g_tramp_test  = nullptr;
}

static void eh_log(const char* s) { (void)write(2, s, strlen(s)); }

// §1.4 verification + tuning. Two review findings:
//   (1) the doc disagreed with itself on whether the detours actually execute — so count every call and
//       every catch, and log the first hit of each, which settles it on a live cell with no guesswork.
//   (2) returning 0 (noErr) after a caught throw tells Apple's core the delegate succeeded, so it runs on
//       a half-initialised format/badge state instead of tearing the stream down. The right OSStatus is
//       device-testable, so it is a runtime knob (TROUBADOUR_EHGUARD_ONCATCH, a signed int) rather than a
//       baked-in guess; unset keeps the original behaviour (the per-hook value, 0), so nothing changes
//       until the user opts in. Try e.g. -6700 to have the core handle the failure.
static std::atomic<uint64_t> g_calls{0};
static std::atomic<uint64_t> g_catches{0};
static int   g_oncatch_override = 0;
static bool  g_oncatch_set = false;

// Hand-rolled decimal/hex parse: avoids libc strtol, which the cross-gcc emits as __isoc23_strtol@GLIBC_2.38
// (the cell only has glibc 2.30). Accepts an optional leading '-' and a 0x prefix.
static bool parse_int(const char* s, int* out)
{
    bool neg = false; long v = 0; bool any = false;
    if (*s == '-') { neg = true; ++s; } else if (*s == '+') ++s;
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) {
        s += 2;
        for (; *s; ++s) {
            int d; char c = *s;
            if (c >= '0' && c <= '9') d = c - '0';
            else if (c >= 'a' && c <= 'f') d = c - 'a' + 10;
            else if (c >= 'A' && c <= 'F') d = c - 'A' + 10;
            else return false;
            v = v * 16 + d; any = true;
        }
    } else {
        for (; *s; ++s) { if (*s < '0' || *s > '9') return false; v = v * 10 + (*s - '0'); any = true; }
    }
    if (!any) return false;
    *out = (int)(neg ? -v : v); return true;
}

extern "C" void troub_wrap_init_env(void)
{
    const char* e = getenv("TROUBADOUR_EHGUARD_ONCATCH");
    int v;
    if (e && *e && parse_int(e, &v)) { g_oncatch_override = v; g_oncatch_set = true; }
}

extern "C" uint64_t troub_wrap(void* saved, void* tramp, uint64_t on_catch)
{
    if (g_calls.fetch_add(1) == 0) eh_log("[troubadour-ehguard] first delegate call went through the guard\n");
    try {
        return troub_invoke(saved, tramp);
    } catch (...) {
        uint64_t rv = g_oncatch_set ? (uint64_t)(int64_t)g_oncatch_override : on_catch;
        char buf[160];
        snprintf(buf, sizeof buf,
                 "[troubadour-ehguard] caught exception at C++/C delegate boundary (catch #%llu); returning %lld\n",
                 (unsigned long long)(g_catches.fetch_add(1) + 1), (long long)(int64_t)rv);
        eh_log(buf);
        return rv;
    }
}

extern "C" void troub_wrap_stats(uint64_t* calls, uint64_t* catches)
{
    if (calls)   *calls   = g_calls.load();
    if (catches) *catches = g_catches.load();
}

/* Overwrite the first 16 bytes of `func` with an absolute jump to `hook_entry`, and return a
 * freshly built trampoline (relocated 16-byte prologue + absolute jump to func+16). The first
 * 4 instructions of both target functions are PC-independent, so the prologue copies verbatim.
 * Returns nullptr on failure (caller then leaves that function stock). */
extern "C" void* ehguard_install(void* func, void* hook_entry)
{
    const uint32_t LDR_X16_8 = 0x58000050;  // ldr x16, #8
    const uint32_t BR_X16    = 0xD61F0200;  // br  x16
    long ps = sysconf(_SC_PAGESIZE);
    if (ps <= 0) ps = 4096;

    // Trampoline in fresh RWX memory.
    void* tr = mmap(nullptr, (size_t)ps, PROT_READ|PROT_WRITE|PROT_EXEC,
                    MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    if (tr == MAP_FAILED) return nullptr;
    memcpy(tr, func, 16);                               // stolen 4 instrs
    uint32_t* tp = (uint32_t*)((char*)tr + 16);
    tp[0] = LDR_X16_8;
    tp[1] = BR_X16;
    *(uint64_t*)(tp + 2) = (uint64_t)func + 16;         // .quad func+16
    __builtin___clear_cache((char*)tr, (char*)tr + 32);

    // Detour the function entry.
    uintptr_t pg = (uintptr_t)func & ~((uintptr_t)ps - 1);
    size_t span = ((uintptr_t)func + 16 > pg + ps) ? (size_t)ps * 2 : (size_t)ps;
    if (mprotect((void*)pg, span, PROT_READ|PROT_WRITE|PROT_EXEC) != 0) {
        munmap(tr, (size_t)ps);
        return nullptr;
    }
    uint32_t* f = (uint32_t*)func;
    f[0] = LDR_X16_8;
    f[1] = BR_X16;
    *(uint64_t*)(f + 2) = (uint64_t)hook_entry;         // .quad hook_entry
    __builtin___clear_cache((char*)func, (char*)func + 16);
    mprotect((void*)pg, span, PROT_READ|PROT_EXEC);
    return tr;
}

extern "C" void ehguard_logs(const char* s) { eh_log(s); }
