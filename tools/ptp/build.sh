#!/bin/sh
# Builds overlay/usr/bin/syng-ptp4l and syng-pmc: linuxptp (GPLv2, https://github.com/richardcochran/linuxptp)
# as static aarch64 binaries, with one change (linuxptp-troubadour.patch): an interface that does not *advertise*
# software TX time stamping is tried anyway — the cells' USB NICs (r8152 on kernel 5.4) take the time stamps fine
# but stock ptp4l refuses them ("does not support requested timestamping mode"). Cross-compiled with zig
# (pip install ziglang), no toolchain needed.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
test -d linuxptp || git clone --depth 1 https://github.com/richardcochran/linuxptp.git
cd linuxptp
git apply "$HERE/linuxptp-troubadour.patch" 2>/dev/null || true
mkdir -p compat/sys; cp /usr/include/x86_64-linux-gnu/sys/queue.h compat/sys/ 2>/dev/null || cp /usr/include/sys/queue.h compat/sys/
cat > zigcc.sh <<'EOS'
#!/bin/sh
exec python3 -m ziglang cc -target aarch64-linux-musl "$@"
EOS
chmod +x zigcc.sh
make -j8 CC=$PWD/zigcc.sh EXTRA_CFLAGS="-O2 -I$PWD/compat -DHAVE_CLOCK_ADJTIME -DHAVE_POSIX_SPAWN" LDFLAGS="-static" ptp4l pmc
llvm-strip ptp4l pmc 2>/dev/null || strip ptp4l pmc
cp ptp4l "$HERE/../../overlay/usr/bin/syng-ptp4l"; cp pmc "$HERE/../../overlay/usr/bin/syng-pmc"
echo "built: $(./ptp4l -v 2>/dev/null || echo linuxptp) -> overlay/usr/bin/syng-ptp4l, syng-pmc"
