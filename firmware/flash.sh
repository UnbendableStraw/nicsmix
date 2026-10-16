#!/usr/bin/env bash
# Flash a Troubadour image with NXP uuu. Run from a directory holding image.sdcard[.lzma], imx-boot and
# syng-cell-flash.uuu (tools/fw/build-troubadour.sh writes all of them into its out-dir).
# Requires: uuu (mfgtools). macOS: `brew install uuu`  or use the uuu bundled
# in "Syng Firmware Updater.app/Contents/MacOS/uuu".
set -euo pipefail
cd "${1:-$(dirname "$0")}"
[ -f imx-boot ] || { echo "imx-boot missing here — copy it from the stock firmware package (Syng Firmware Updater.app/Contents/Resources/Firmware/)"; exit 1; }
UUU="${UUU:-uuu}"
if [ ! -f image.sdcard ]; then
  echo "Decompressing image.sdcard.lzma (XZ) -> image.sdcard ..."
  xz -dc image.sdcard.lzma > image.sdcard
fi
echo "Put the Cell into USB serial-download mode and connect USB-C now."
echo "Running uuu (it will wait for the device)..."
exec "$UUU" syng-cell-flash.uuu
