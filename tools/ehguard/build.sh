#!/bin/sh
# Build the troubadour-ehguard delegate exception guard for the Syng Cell (aarch64).
# Needs an aarch64 g++. glibc refs are .symver-pinned to GLIBC_2.17; the C++ EH runtime
# symbols used (__cxa_begin_catch/end_catch, __gxx_personality_v0, _Unwind_Resume) are at
# CXXABI_1.3 / GCC_3.0, present in the cell's libstdc++.so.6 (6.0.27) and libgcc_s.so.1.
set -e
CXX=${CXX:-g++}
cd "$(dirname "$0")"
OUT=libtroubadour_ehguard.so
"$CXX" -O2 -fPIC -shared -fno-stack-protector \
    ehguard.cpp ehguard_engine.cpp ehguard_asm.S -o "$OUT" -ldl
echo "built $OUT"
BAD=$(readelf -W --dyn-syms "$OUT" | grep -oE 'GLIBC_[0-9.]+' | sed 's/GLIBC_//' | sort -u | awk -F. '{v=$1*1000+$2} v>2030{print "GLIBC_"$0}')
[ -n "$BAD" ] && { echo "ERROR: needs glibc >2.30: $BAD" >&2; exit 1; }
echo "version reqs:"; readelf -W --dyn-syms "$OUT" | grep -oE '(GLIBC|CXXABI|GCC)_[0-9.]+' | sort -u

# Optional: build + run the engine unit test (pass-through + catch) on an aarch64 builder.
if [ "$1" = "test" ]; then
  "$CXX" -O0 -g -fexceptions ehguard_test.cpp ehguard_engine.cpp ehguard_asm.S -o ehguard_test
  ./ehguard_test
fi
