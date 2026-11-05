#!/bin/sh
set -eu
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl -fsSL 'https://deb.debian.org/debian/pool/main/f/faketime/libfaketime_0.9.10-2.1_amd64.deb' -o "$tmp/runtime.deb"
echo 'b09481e7690680966005330c3f907bba4b5eefc35e1faaea4783cc55655d1150  '"$tmp/runtime.deb" | sha256sum -c -
dpkg-deb -x "$tmp/runtime.deb" "$tmp/unpacked"
mkdir -p "$base/runtime"
install -m 755 "$tmp/unpacked/usr/lib/x86_64-linux-gnu/faketime/libfaketime.so.1" "$base/runtime/libfaketime.so.1"
echo '45c4822a7516e9c41ec1b5498b0f08665ba4877330e9e9573c1213d16d6629a0  '"$base/runtime/libfaketime.so.1" | sha256sum -c -
