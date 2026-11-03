#!/usr/bin/env bash
# Startskript im Nachbau-Container: /src klonen, Plugin aus dem Klon installieren und testen.
#   container-start.sh <src> <out>
# <src>: Git-Checkout des Plugins, schreibgeschützt gemountet.
set -eu
SRC=${1:-/src}
OUT=${2:-/out}
git config --global --add safe.directory '*'
git clone --quiet "$SRC" "$HOME/checkout"
COMMIT=$(git -C "$HOME/checkout" rev-parse HEAD)
export REPO_COMMIT=$COMMIT
exec bash /usr/local/bin/nachbau.sh "$HOME/checkout" "$OUT"
