#!/bin/sh
# Build the v4 clock only; the historical v3 runtime is untouched.
set -eu
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
archive="$tmp/source.tar.gz"
mkdir "$tmp/out"
curl -fsSL 'https://codeload.github.com/wolfcw/libfaketime/tar.gz/86b37fde2fed7336ea2d0c17928e3015a55d9b4a' -o "$archive"
echo 'd98aa7beeb21344b3612f60cbfe08196541a2ef25525b3774b11871493a4726e  '"$archive" | sha256sum -c -
docker run --rm --network none --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$archive,dst=/source.tar.gz,readonly" \
  --mount "type=bind,src=$tmp/out,dst=/out" \
  gcc@sha256:3d3af69c72edf0652bb3a829965142e81891899c7827d849bc3c0dbb9edd216a \
  sh -c 'mkdir -p /tmp/source && tar -xzf /source.tar.gz -C /tmp/source --strip-components=1 && make -C /tmp/source/src && cp /tmp/source/src/libfaketime-time64.so.1 /out/'
echo 'd69f77e806fd01db81d8e26e5722704ad3951c3de2e68dfbcd9d32a2aff817fa  '"$tmp/out/libfaketime-time64.so.1" | sha256sum -c -
mkdir -p "$base/runtime-v4"
install -m 755 "$tmp/out/libfaketime-time64.so.1" "$base/runtime-v4/libfaketime.so.1"
