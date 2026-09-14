#!/usr/bin/env bash
# Copy the JSON files that `vllm bench serve --save-result --result-dir /tmp/bench`
# left inside the head container into bench/results/ next to the console logs.
#
# Run on the head node (spark-927a), from anywhere:
#   bash fetch-results.sh [container] [dest]
# Defaults: container glm53-exl3-head, dest ./results
#
# The JSON carries every metric in the console output plus per-request
# latencies, so keep both: the .txt is what a human saw, the .json is
# what a script can plot.
set -euo pipefail

CONTAINER="${1:-glm53-exl3-head}"
DEST="${2:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/results}"

mkdir -p "$DEST"
docker cp "$CONTAINER:/tmp/bench/." "$DEST/"
echo "copied to $DEST:"
ls -1 "$DEST"/*.json
