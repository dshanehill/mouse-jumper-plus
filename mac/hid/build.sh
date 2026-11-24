#!/bin/bash
# Builds mjp-hid, the Mac-side receiver, against the Karabiner VirtualHIDDevice headers.
set -euo pipefail
cd "$(dirname "$0")"
KVHID="${KVHID:-vendor/Karabiner-DriverKit-VirtualHIDDevice}"
if [ ! -d "$KVHID" ]; then
  git clone --depth 1 --recurse-submodules \
    https://github.com/pqrs-org/Karabiner-DriverKit-VirtualHIDDevice.git "$KVHID"
fi
INCLUDES=()
while IFS= read -r d; do INCLUDES+=("-I$d"); done < <(find "$KVHID" -type d -name include -not -path '*/test*')
clang++ -std=c++2b -O2 "${INCLUDES[@]}" mjp_hid.cpp -o mjp-hid
echo "Built mac/hid/mjp-hid"
