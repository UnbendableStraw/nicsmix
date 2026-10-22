/* Unit test for the hook+EH engine, run on the device VM (same aarch64 ABI as the cell).
 * Validates: (1) pass-through — hooked function returns its real value and receives correct args;
 *            (2) catch — when the hooked function throws, the guard returns the substitute (0)
 *                instead of letting the exception escape / abort. */
#define _GNU_SOURCE
#include <cstdio>
#include <cstdint>
#include <stdexcept>

extern "C" {
    void* ehguard_install(void* func, void* hook_entry);
    extern void* g_tramp_test;
    void  hook_entry_test(void);
}

// A victim with register args and a normal, PC-independent prologue (built -O0).
extern "C" __attribute__((noinline)) uint64_t victim(uint64_t a, uint64_t b, uint64_t c, double d)
{
    if (a == 42) throw std::runtime_error("boom from victim");
    return a + b + c + (uint64_t)d;   // exercises x0,x1,x2 and d0
}

int main()
{
    // Sanity before hooking.
    printf("pre-hook victim(1,2,3,4.0) = %llu (expect 10)\n", (unsigned long long)victim(1,2,3,4.0));

    g_tramp_test = ehguard_install((void*)victim, (void*)hook_entry_test);
    if (!g_tramp_test) { printf("FAIL: install returned null\n"); return 2; }
    printf("hook installed; tramp=%p\n", g_tramp_test);

    uint64_t r1 = victim(1, 2, 3, 4.0);
    printf("post-hook passthrough victim(1,2,3,4.0) = %llu (expect 10)\n", (unsigned long long)r1);

    uint64_t r2 = victim(42, 100, 100, 100.0);   // throws -> guard must catch, return 0
    printf("post-hook throwing  victim(42,...)      = %llu (expect 0, no abort)\n", (unsigned long long)r2);

    int ok = (r1 == 10) && (r2 == 0);
    printf("%s\n", ok ? "ENGINE TEST PASS" : "ENGINE TEST FAIL");
    return ok ? 0 : 1;
}
