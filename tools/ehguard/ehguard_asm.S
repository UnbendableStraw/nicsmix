/* troubadour-ehguard: assembly half of the delegate exception guard.
 *
 * hook_entry_* : detour lands here (via absolute jump, x30 = AirPlay core's return addr).
 *   Saves all argument registers (x0-x7, v0-v7, x8) to a scratch frame, then calls the
 *   C++ troub_wrap() which runs the original under a try/catch. Returns the result (or the
 *   on-catch substitute) to the AirPlay core. x30 is preserved across the bl.
 *
 * troub_invoke(saved, tramp): restores the saved argument registers EXACTLY and calls the
 *   per-function trampoline (relocated prologue + absolute jump to original+16). It carries
 *   proper CFI so the unwinder can walk through it: when the original throws, unwinding goes
 *   original -> troub_invoke -> troub_wrap (whose compiler-generated LSDA catches).
 *
 * Scratch frame layout (0xE0 bytes), written by hook_entry, read by troub_invoke:
 *   0x00 x0,x1  0x10 x2,x3  0x20 x4,x5  0x30 x6,x7
 *   0x40 q0,q1  0x60 q2,q3  0x80 q4,q5  0xA0 q6,q7
 *   0xC0 x8     0xC8 x30(hook_entry's own return)
 */
    .text
    .macro HOOK_ENTRY name, trampglobal, retval
    .global \name
    .type   \name, %function
\name:
    sub     sp, sp, #0xE0
    str     x30, [sp, #0xC8]
    stp     x0, x1,  [sp, #0x00]
    stp     x2, x3,  [sp, #0x10]
    stp     x4, x5,  [sp, #0x20]
    stp     x6, x7,  [sp, #0x30]
    stp     q0, q1,  [sp, #0x40]
    stp     q2, q3,  [sp, #0x60]
    stp     q4, q5,  [sp, #0x80]
    stp     q6, q7,  [sp, #0xA0]
    str     x8, [sp, #0xC0]
    mov     x0, sp                      /* saved */
    adrp    x1, \trampglobal
    ldr     x1, [x1, #:lo12:\trampglobal]   /* tramp */
    mov     x2, #\retval                /* on-catch return value */
    bl      troub_wrap
    ldr     x30, [sp, #0xC8]
    add     sp, sp, #0xE0
    ret
    .size   \name, .-\name
    .endm

    HOOK_ENTRY hook_entry_init,  g_tramp_init,  0
    HOOK_ENTRY hook_entry_badge, g_tramp_badge, 0
    HOOK_ENTRY hook_entry_test,  g_tramp_test,  0

    .global troub_invoke
    .type   troub_invoke, %function
troub_invoke:                           /* x0 = saved, x1 = tramp */
    .cfi_startproc
    stp     x29, x30, [sp, #-16]!
    .cfi_def_cfa_offset 16
    .cfi_offset 30, -8
    .cfi_offset 29, -16
    mov     x29, sp
    mov     x9,  x0                     /* x9 = saved (survives arg restore) */
    mov     x16, x1                     /* x16 = tramp */
    ldp     q0, q1, [x9, #0x40]
    ldp     q2, q3, [x9, #0x60]
    ldp     q4, q5, [x9, #0x80]
    ldp     q6, q7, [x9, #0xA0]
    ldr     x8, [x9, #0xC0]
    ldp     x0, x1, [x9, #0x00]
    ldp     x2, x3, [x9, #0x10]
    ldp     x4, x5, [x9, #0x20]
    ldp     x6, x7, [x9, #0x30]
    blr     x16                         /* call trampoline; original returns here */
    ldp     x29, x30, [sp], #16
    .cfi_def_cfa_offset 0
    ret
    .cfi_endproc
    .size   troub_invoke, .-troub_invoke

    .section .note.GNU-stack,"",%progbits
