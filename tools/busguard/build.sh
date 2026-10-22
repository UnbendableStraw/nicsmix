#!/bin/sh
# Build the troubadour-busguard sd-bus serialization shim for the Syng Cell (aarch64).
#
# Needs an aarch64 gcc (native on the cell's arch, or a cross toolchain). The source pins
# every libc/pthread/dl reference to GLIBC_2.17 via .symver, so the resulting .so only
# requires GLIBC_2.17 and loads on the cell's glibc 2.30 regardless of the builder's glibc.
#
# Usage:  CC=gcc ./build.sh        (native aarch64 builder)
#         CC=aarch64-linux-gnu-gcc ./build.sh   (cross)
set -e
CC=${CC:-gcc}
cd "$(dirname "$0")"
OUT=libtroubadour_busguard.so
"$CC" -O2 -fPIC -shared -fno-stack-protector -static-libgcc \
    busguard.c -o "$OUT" \
    -Wl,--version-script=busguard.map -Wl,--no-as-needed -ldl -lpthread
echo "built $OUT"
# Verify it needs nothing newer than the cell's glibc 2.30.
BAD=$(readelf -W --dyn-syms "$OUT" | grep -oE 'GLIBC_[0-9.]+' | sed 's/GLIBC_//' | sort -u \
      | awk -F. '{v=$1*1000+$2} v>2030 {print "GLIBC_"$0}')
if [ -n "$BAD" ]; then echo "ERROR: requires glibc newer than 2.30: $BAD" >&2; exit 1; fi
echo "glibc requirement OK (<=2.30):"; readelf -W --dyn-syms "$OUT" | grep -oE 'GLIBC_[0-9.]+' | sort -u
echo "exports:"; readelf -W --dyn-syms "$OUT" | grep -oE 'sd_bus_[a-z_]+@@LIBSYSTEMD_221' | sort -u
