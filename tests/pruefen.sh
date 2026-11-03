#!/usr/bin/env bash
# Der einzige Prüfeinstieg: genau das, was die CI ausführt (.github/workflows/tests.yml ruft nur dieses Skript auf).
# Aufruf aus jedem Ordner: tests/pruefen.sh. Exit 0 nur, wenn alles grün ist; jeder Fehler und jedes fehlende
# Werkzeug gibt Exit ≠ 0. Beide Schritte laufen immer, damit ein Lauf alle Fehler zeigt.
set -uo pipefail

if ! command -v uv >/dev/null 2>&1; then
  echo "pruefen: uv fehlt im PATH (https://docs.astral.sh/uv/)" >&2
  exit 2
fi

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

fehler=0

echo "== unittest (alle Tests unter tests/)"
uv run --with pyyaml --with markdown python -m unittest || fehler=1

# ohne Docker; container_tests.py braucht Docker und läuft nicht hier
echo "== web-zugriff: test_pruefung.py"
uv run --with pyyaml --with markdown python -I skills/web-zugriff/scripts/tests/test_pruefung.py || fehler=1

if [ "$fehler" -ne 0 ]; then
  echo "pruefen: FEHLER" >&2
  exit 1
fi
echo "pruefen: alles grün"
