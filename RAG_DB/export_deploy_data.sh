#!/usr/bin/env bash
# Copies the demo course out of the local app stack into deploy_data/ for Dockerfile.render.
# Skips processed/media/temp (ingest scratch) and video originals over 50 MB.
set -euo pipefail
cd "$(dirname "$0")"
CONTAINER=${CONTAINER:-looper_multimodal-api-1}

rm -rf deploy_data.new
mkdir -p deploy_data.new/processed/media
docker cp "$CONTAINER:/app/metadata" deploy_data.new/metadata
docker cp "$CONTAINER:/app/processed/images" deploy_data.new/processed/images
for d in pages keyframes tts_cache; do
  docker cp "$CONTAINER:/app/processed/media/$d" "deploy_data.new/processed/media/$d"
done
docker cp "$CONTAINER:/app/raw" deploy_data.new/raw
find deploy_data.new/raw -type f -size +50M -print -delete
rm -rf deploy_data && mv deploy_data.new deploy_data
du -sh deploy_data
