#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Puts one component's pinned tarball in sources/:
#   download.sh <component>
# A copy already there is kept when its sha256 is the pinned one and
# removed otherwise. Then each of the component's urls in pins.json is
# tried in order, twice with a pause between, and the first file whose
# sha256 is the pinned one is kept; any other is deleted. The URL it came
# from is written beside it, as <tarball>.url, for the manifest. When no
# URL serves the pinned file, it fails naming each URL and what it served:
# a sha256 and size, or curl's error. A mirror serving other bytes for a
# moment then costs a retry, never a build.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME="${1:-}"
[ -n "$NAME" ] || { echo "usage: $0 <component>" >&2; exit 2; }
# Any \r a Windows Python writes is dropped (pins.py writes none itself).
pin() { python3 "$ROOT/build/pins.py" "$@" | tr -d '\r'; }

SOURCES="$ROOT/sources"
mkdir -p "$SOURCES"
tarball="$(pin tarball "$NAME")"
want="$(pin get "$NAME" sha256)"
dest="$SOURCES/$tarball"

if [ -f "$dest" ]; then
    if [ "$(pin sha256 "$dest")" = "$want" ]; then
        [ -f "$dest.url" ] || echo "a copy already in sources/" > "$dest.url"
        echo "$NAME: $tarball already in sources/, checked"
        exit 0
    fi
    echo "$NAME: the $tarball in sources/ is not the pinned file; removed"
    rm -f "$dest" "$dest.url"
fi

tried=()
exec 3< <(pin urls "$NAME")
while read -r url <&3; do
    for attempt in 1 2; do
        [ "$attempt" = 1 ] || sleep 10
        rm -f "$dest.part"
        if ! err="$(curl -fsSL --connect-timeout 30 --max-time 900 -o "$dest.part" "$url" 2>&1)"; then
            tried+=("$url, try $attempt: ${err:-curl failed}")
            continue
        fi
        got="$(pin sha256 "$dest.part")"
        if [ "$got" = "$want" ]; then
            mv "$dest.part" "$dest"
            printf '%s\n' "$url" > "$dest.url"
            echo "$NAME: $tarball from $url, checked"
            exit 0
        fi
        tried+=("$url, try $attempt: sha256 $got, $(wc -c < "$dest.part" | tr -d ' ') bytes")
        rm -f "$dest.part"
    done
done
exec 3<&-

{
    echo "$NAME: no URL served the pinned $tarball (sha256 $want):"
    printf '  %s\n' "${tried[@]}"
} >&2
exit 1
