#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DATA="$ROOT/data/osm"
IMAGE="ghcr.io/project-osrm/osrm-backend:26.8.0-debian"

test -f "$DATA/nwfd-latest.osm.pbf" || { echo "Missing $DATA/nwfd-latest.osm.pbf"; exit 1; }

echo "OSRM preprocessing can be memory/CPU intensive. Do not run Nominatim import in parallel."
docker run --rm -t -v "$DATA:/data" "$IMAGE" \
  osrm-extract -p /opt/car.lua /data/nwfd-latest.osm.pbf

docker run --rm -t -v "$DATA:/data" "$IMAGE" \
  osrm-partition /data/nwfd-latest.osrm

docker run --rm -t -v "$DATA:/data" "$IMAGE" \
  osrm-customize /data/nwfd-latest.osrm

echo "Done."
ls -lh "$DATA" | tail -n +1
