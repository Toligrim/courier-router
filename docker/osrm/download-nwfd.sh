#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
mkdir -p "$ROOT/data/osm"
cd "$ROOT/data/osm"
URL="https://download.geofabrik.de/russia/northwestern-fed-district-latest.osm.pbf"
echo "Downloading $URL"
curl -fL --retry 5 -o nwfd-latest.osm.pbf "$URL"
ls -lh nwfd-latest.osm.pbf
